# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import ast
import errno
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest
from streamlink import Streamlink

from soop_grid.bootstrap import Config
from soop_grid.diagnostics import Diagnostics, safe_error, stop_reason
from soop_grid.protocol import GridError, Network
from soop_grid.stream import SoopGridReader, SoopGridStream, receive
import soop_grid.stream as stream_module


def tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "field_validate.py"
    spec = importlib.util.spec_from_file_location("field_validation_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "error,expected",
    [
        (GridError("network_read_timeout"), "network_timeout"),
        (GridError("peer_closed_or_truncated"), "network_eof"),
        (GridError("recovery_overlap_unavailable"), "cache_overlap_unavailable"),
        (GridError("h264_timing_changed"), "media_timestamp_invalid"),
        (GridError("h264_hrd_grid_clock_conflict"), "media_timing_unconfirmed"),
        (GridError("h264_hrd_low_delay_timing_unsupported"), "media_timing_unconfirmed"),
        (GridError("h264_picture_order_conflict"), "media_timestamp_invalid"),
        (GridError("audio_timestamp_cumulative_drift"), "media_timestamp_invalid"),
        (GridError("audio_chunk_clock_mismatch"), "media_timestamp_invalid"),
        (GridError("duration_limit_output_incomplete"), "duration_limit_output_incomplete"),
        (GridError("parent_access_denied"), "authentication_denied"),
        (GridError("live_metadata_denied_or_offline"), "receiver_error"),
        (OSError(errno.ENOSPC, "fixture-private-value"), "disk_write_error"),
        (ValueError("fixture-private-value"), "receiver_internal_error"),
    ],
)
def test_stop_classification_never_needs_exception_text(error, expected):
    assert stop_reason(error) == expected
    assert "fixture-private" not in safe_error(error)


def test_h264_reorder_timing_failure_uses_fixed_safe_code():
    error = GridError("h264_reorder_timing_unconfirmed")
    assert safe_error(error) == "h264_reorder_timing_unconfirmed"


def test_all_literal_grid_errors_have_safe_diagnostic_codes():
    package = Path(__file__).resolve().parents[1] / "soop_grid"
    missing = set()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            if not (
                isinstance(function, ast.Name)
                and function.id == "GridError"
                or isinstance(function, ast.Attribute)
                and function.attr == "GridError"
            ):
                continue
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if safe_error(GridError(node.args[0].value)) != node.args[0].value:
                    missing.add((node.args[0].value, path.name, node.lineno))
    assert not missing


def test_protocol_header_facts_are_allowlisted_without_raw_values(tmp_path):
    path = tmp_path / "wire-facts"
    d = Diagnostics(path, lambda: {})
    d.fail(
        GridError(
            "control_checksum_invalid",
            safe_facts={
                "family": "center",
                "command": 0x69,
                "result": 0,
                "declared_body_bytes": 12,
                "checksum_received": 1,
                "checksum_expected": 2,
                "cookie": "fixture-secret",
                "raw_packet": b"private-wire",
            },
        )
    )
    d.close()
    summary = json.loads((path / "summary.json").read_text())
    assert summary["protocol_failure"] == {
        "family": "center",
        "command": 0x69,
        "result": 0,
        "declared_body_bytes": 12,
        "checksum_received": 1,
        "checksum_expected": 2,
    }
    text = (path / "summary.json").read_text()
    assert "fixture-secret" not in text and "private-wire" not in text


def test_diagnostic_allowlist_atomic_summary_and_first_cause(tmp_path):
    path = tmp_path / "diagnostics"
    d = Diagnostics(
        path,
        lambda: {
            "payload_bytes": 4,
            "audio_chunk_overlap_100ns": 120000,
            "audio_max_chunk_overlap_100ns": 120000,
            "cookie": "fixture-private-value",
            "reader_state": "error",
            "h264_poc_type": 2,
            "h264_fixed_vui_timing": 0,
            "last_video_poc": 18,
            "h264_rejected_cpb_removal_delay": 18,
            "h264_previous_cpb_removal_delay": 16,
            "h264_rejected_cpb_delta": 2,
            "h264_rejected_grid_delta_100ns": 160000,
            "h264_rejected_cpb_delta_100ns": 166667,
            "h264_rejected_cpb_delay_width": 16,
            "h264_rejected_clock_tolerance_100ns": 10000,
            "h264_rejected_cpb_epoch_ticks": 2,
            "h264_rejected_cpb_epoch_delta_100ns": 166667,
            "h264_rejected_grid_epoch_delta_100ns": 180000,
            "h264_current_dts_90k": 91530,
            "h264_current_candidate_pts_90k": 93030,
            "h264_conflicting_reference_poc": 0,
            "h264_conflicting_reference_pts_90k": 103500,
            "h264_clock_tick_100ns_numerator": 250000,
            "h264_clock_tick_100ns_denominator": 3,
            "last_video_slice_type": "P",
            "unexpected_media": b"private-payload",
        },
    )
    d.start()
    d.event("fixture-private-value", "fixture-private-value")
    d.fail(GridError("network_read_timeout"))
    d.complete("user_cancelled")
    d.close()
    text = "".join(p.read_text() for p in path.iterdir())
    assert "fixture-private" not in text and "cookie" not in text
    summary = json.loads((path / "summary.json").read_text())
    assert summary["stop_reason"] == "network_timeout" and summary["payload_bytes"] == 4
    assert summary["audio_chunk_overlap_100ns"] == 120000
    assert summary["audio_max_chunk_overlap_100ns"] == 120000
    assert summary["h264_poc_type"] == 2
    assert summary["h264_fixed_vui_timing"] == 0 and summary["last_video_poc"] == 18
    assert summary["h264_rejected_cpb_removal_delay"] == 18
    assert summary["h264_rejected_cpb_delta"] == 2
    assert summary["h264_rejected_grid_delta_100ns"] == 160000
    assert summary["h264_rejected_grid_epoch_delta_100ns"] == 180000
    assert summary["h264_current_candidate_pts_90k"] == 93030
    assert summary["h264_conflicting_reference_poc"] == 0
    assert summary["h264_conflicting_reference_pts_90k"] == 103500
    assert summary["h264_clock_tick_100ns_numerator"] == 250000
    assert summary["h264_clock_tick_100ns_denominator"] == 3
    assert summary["last_video_slice_type"] == "P"
    assert "unexpected_media" not in summary and "private-payload" not in text
    assert not (path / "summary.tmp").exists() and not d.thread.is_alive()
    if os.name != "nt":
        assert (path / "summary.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(GridError, match="diagnostic_write_failed"):
        Diagnostics(path, lambda: {})


def test_diagnostics_do_not_export_media_content_fingerprints(tmp_path):
    fingerprints = {
        "video_output_sha256": "a" * 64,
        "audio_output_sha256": "b" * 64,
        "payload_sha256": "c" * 64,
    }
    d = Diagnostics(tmp_path / "diagnostics", lambda: fingerprints)
    d.complete("duration_limit")
    d.close()
    text = "".join(p.read_text() for p in (tmp_path / "diagnostics").iterdir())
    assert not any(key in text for key in fingerprints)
    assert not any(value in text for value in fingerprints.values())


def test_diagnostic_storage_failure_does_not_expose_raw_os_error(tmp_path, caplog):
    d = Diagnostics(tmp_path / "diag", lambda: {})
    original = d.file

    class Full:
        def tell(self):
            return 0

        def write(self, data):
            raise OSError(errno.ENOSPC, "fixture-private-path")

    d.file = Full()
    d.event(event="heartbeat")
    assert d.failed and "diagnostic_write_failed" in caplog.text
    assert "fixture-private" not in caplog.text
    d.file = original
    d.close()


def test_diagnostic_event_log_has_a_hard_byte_limit_and_atomic_summary(tmp_path, monkeypatch):
    import soop_grid.diagnostics as diagnostics_module

    monkeypatch.setattr(diagnostics_module, "MAX_EVENT_LOG_BYTES", 2048)
    d = Diagnostics(tmp_path / "bounded", lambda: {"payload_bytes": 12})
    for _ in range(1000):
        d.event("parent", "heartbeat")
    d.fail(GridError("network_read_timeout"))
    d.close()
    path = tmp_path / "bounded"
    event_data = (path / "events.jsonl").read_bytes()
    summary = json.loads((path / "summary.json").read_text())
    assert len(event_data) <= 2048
    assert summary["diagnostic_events_truncated"] is True
    assert b"diagnostic_event_log_limit_reached" in event_data
    assert summary["stop_reason"] == "network_timeout"


def test_per_frame_progress_events_are_rate_limited_but_heartbeats_remain(tmp_path, monkeypatch):
    import soop_grid.diagnostics as diagnostics_module

    now = [100.0]
    monkeypatch.setattr(diagnostics_module.time, "monotonic", lambda: now[0])
    d = Diagnostics(tmp_path / "rate-limited", lambda: {"video_frames": 1})
    d.event("parent", "parent_last_media_received")
    for _ in range(1000):
        d.event("parent", "parent_last_media_received")
    now[0] += 9.9
    d.event("parent", "parent_last_media_received")
    now[0] += 0.1
    d.event("parent", "parent_last_media_received")
    d.event("parent", "parent_connection_lost")
    d.close()

    rows = [
        json.loads(line) for line in (tmp_path / "rate-limited" / "events.jsonl").read_text().splitlines()
    ]
    assert [row["event"] for row in rows].count("parent_last_media_received") == 2
    assert rows[-1]["event"] == "parent_connection_lost"
    summary = json.loads((tmp_path / "rate-limited" / "summary.json").read_text())
    assert summary["diagnostic_events_truncated"] is False


def test_heartbeat_thread_continues_during_per_frame_event_storm(tmp_path, monkeypatch):
    import time

    import soop_grid.diagnostics as diagnostics_module

    monkeypatch.setattr(diagnostics_module, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    d = Diagnostics(tmp_path / "heartbeat-under-load", lambda: {"video_frames": 1000})
    stop = threading.Event()
    two_heartbeats = threading.Event()
    heartbeat_media_counts = []
    media_event_count = 0
    event_count_lock = threading.Lock()
    original_event = d.event

    def observed_event(stage=None, event="heartbeat"):
        nonlocal media_event_count
        if event == "parent_last_media_received":
            with event_count_lock:
                media_event_count += 1
        original_event(stage, event)
        if event == "heartbeat":
            with event_count_lock:
                heartbeat_media_counts.append(media_event_count)
                if len(heartbeat_media_counts) >= 2:
                    two_heartbeats.set()

    monkeypatch.setattr(d, "event", observed_event)
    d.start()

    def report_media_progress():
        while not stop.is_set():
            d.event("parent", "parent_last_media_received")
            time.sleep(0)  # Yield scheduling fairly while retaining event pressure.

    producer = threading.Thread(target=report_media_progress)
    producer.start()
    assert two_heartbeats.wait(2), "heartbeat worker did not emit periodically under media event load"
    assert producer.is_alive()
    stop.set()
    producer.join(1)
    assert not producer.is_alive()
    d.close()

    rows = [
        json.loads(line)
        for line in (tmp_path / "heartbeat-under-load" / "events.jsonl").read_text().splitlines()
    ]
    assert [row["event"] for row in rows].count("parent_last_media_received") == 1
    assert [row["event"] for row in rows].count("heartbeat") >= 2
    assert all(count > 0 for count in heartbeat_media_counts[:2])
    assert (
        json.loads((tmp_path / "heartbeat-under-load" / "summary.json").read_text())[
            "diagnostic_events_truncated"
        ]
        is False
    )


def test_recovery_events_and_last_transient_error_are_not_dropped(tmp_path):
    d = Diagnostics(tmp_path / "diag", lambda: {})
    events = [
        "peer_closed_or_truncated",
        "parent_connection_lost",
        "grid_frame_gap_detected",
        "parent_rediscovery_started",
        "parent_failover_started",
        "grid_frame_gap_recovered",
        "parent_failover_succeeded",
        "grid_live_resumed",
    ]
    for event in events:
        d.event("parent", event)
    d.complete("duration_limit")
    d.close()
    rows = [json.loads(line) for line in (tmp_path / "diag" / "events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == events
    assert (
        json.loads((tmp_path / "diag" / "summary.json").read_text())["last_transport_error"]
        == "peer_closed_or_truncated"
    )


def test_pending_bytes_then_worker_failure_has_same_final_cause(tmp_path):
    config = Config("fixture_channel", "12345", diagnostics_dir=str(tmp_path / "diag"))
    s = SoopGridStream(Streamlink(), config)

    def runner(c, n, emit, report, stats):
        stats["payload_bytes"] = 4
        emit(b"safe")
        raise GridError("h264_timing_changed")

    r = SoopGridReader(s, runner).open()
    assert r.read(4) == b"safe"
    with pytest.raises(OSError, match="h264_timing_changed"):
        r.read(1)
    summary = json.loads((tmp_path / "diag" / "summary.json").read_text())
    assert summary["stop_reason"] == "media_timestamp_invalid"
    assert r.closed and not r.thread.is_alive() and not r.diagnostics.thread.is_alive()


@pytest.mark.parametrize(
    "exception", [ValueError("fixture-private-value"), SystemExit("fixture-private-value")]
)
def test_unexpected_worker_exit_is_not_normal_eof(tmp_path, exception):
    config = Config("fixture_channel", "12345", diagnostics_dir=str(tmp_path / "diag"))

    def runner(*args):
        raise exception

    r = SoopGridReader(SoopGridStream(Streamlink(), config), runner)
    with pytest.raises(Exception, match="receiver_internal_failure"):
        r.open()
    assert r.diagnostics.reason == "receiver_internal_error"
    assert "fixture-private" not in (tmp_path / "diag" / "summary.json").read_text()


def test_cleanup_failure_does_not_replace_parser_failure(monkeypatch):
    cleared = []
    session = SimpleNamespace(
        center=None,
        bno=12345,
        clear_gateway_auth=lambda: cleared.append("gateway_auth"),
        clear=lambda: cleared.append("session"),
    )
    credentials = SimpleNamespace(clear=lambda: cleared.append("credentials"))
    sink = SimpleNamespace(stats={}, close=lambda: (_ for _ in ()).throw(OSError(errno.ENOSPC, "secret")))
    monkeypatch.setattr(stream_module, "bootstrap", lambda *a, **k: session)
    monkeypatch.setattr(stream_module, "obtain_certificate", lambda *a: credentials)
    monkeypatch.setattr(stream_module, "discover_parent", lambda *a: ([], 1))
    monkeypatch.setattr(stream_module, "MediaSink", lambda *a, **k: sink)
    monkeypatch.setattr(
        stream_module, "receive_media", lambda *a, **k: (_ for _ in ()).throw(GridError("media_header_short"))
    )
    with pytest.raises(GridError, match="media_header_short"):
        receive(Config("fixture_channel", "12345"), Network(), lambda x: None, lambda x: None, {})
    assert cleared == ["gateway_auth", "credentials", "session"]


def test_process_supervisor_reads_stdout_and_stderr_without_secrets(tmp_path):
    m = tool()
    result = m.supervise(
        [
            sys.executable,
            "-c",
            "import sys; print('SOOP Grid: h264_timing_changed secret-cookie'); print('secret-password',file=sys.stderr); sys.exit(7)",
        ],
        tmp_path / "run",
        5,
    )
    assert result["process_exit_code"] == 7
    assert result["cli_log_counts"]["stdout"] and result["cli_log_counts"]["stderr"]
    assert "h264_timing_changed" in result["cli_codes"]
    text = "".join(p.read_text() for p in (tmp_path / "run").iterdir())
    assert "secret-cookie" not in text and "secret-password" not in text
    assert m.log_code("No space left on device private-path") == "disk_write_error"
    assert m.log_code("Error when writing to output: private-path") == "streamlink_output_error"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal semantics")
def test_process_signal_is_not_receiver_eof(tmp_path):
    result = tool().supervise(
        [sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"],
        tmp_path / "signal",
        5,
    )
    assert result["termination_signal"] == 15 and result["stop_reason"] == "process_signal"


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="requires Linux ENOSPC device")
def test_real_streamlink_output_enospc_is_external_to_receiver(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "outputfixture.py").write_text("""
import io
import re
from streamlink.plugin import Plugin, pluginmatcher
from streamlink.stream.stream import Stream
class FixtureStream(Stream):
    __shortname__ = "fixture"
    def open(self):
        return io.BytesIO(b"x" * 65536)
@pluginmatcher(re.compile(r"https://fixture.invalid/output"))
class OutputFixture(Plugin):
    def _get_streams(self):
        yield "original", FixtureStream(self.session)
__plugin__ = OutputFixture
""")
    python = os.environ.get("SOOP_GRID_CLEAN_PYTHON", sys.executable)
    result = tool().supervise(
        [
            python,
            "-m",
            "streamlink_cli",
            "--plugin-dir",
            str(plugins),
            "--force",
            "https://fixture.invalid/output",
            "original",
            "-o",
            "/dev/full",
        ],
        tmp_path / "disk-error",
        10,
        {k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert result["process_exit_code"] != 0
    assert result["stop_reason"] == "disk_write_error"
