# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import errno
import importlib.util
import json
import os
from pathlib import Path
import sys
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
        (GridError("parent_access_denied"), "authentication_denied"),
        (OSError(errno.ENOSPC, "fixture-private-value"), "disk_write_error"),
        (ValueError("fixture-private-value"), "receiver_internal_error"),
    ],
)
def test_stop_classification_never_needs_exception_text(error, expected):
    assert stop_reason(error) == expected
    assert "fixture-private" not in safe_error(error)


def test_diagnostic_allowlist_atomic_summary_and_first_cause(tmp_path):
    path = tmp_path / "diagnostics"
    d = Diagnostics(
        path, lambda: {"payload_bytes": 4, "cookie": "fixture-private-value", "reader_state": "error"}
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
    assert not (path / "summary.tmp").exists() and not d.thread.is_alive()
    if os.name != "nt":
        assert (path / "summary.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(GridError, match="diagnostic_write_failed"):
        Diagnostics(path, lambda: {})


def test_diagnostic_storage_failure_does_not_expose_raw_os_error(tmp_path, caplog):
    d = Diagnostics(tmp_path / "diag", lambda: {})
    original = d.file

    class Full:
        def write(self, data):
            raise OSError(errno.ENOSPC, "fixture-private-path")

    d.file = Full()
    d.event(event="heartbeat")
    assert d.failed and "diagnostic_write_failed" in caplog.text
    assert "fixture-private" not in caplog.text
    d.file = original
    d.close()


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
    session = SimpleNamespace(center=None, bno=12345, clear=lambda: cleared.append("session"))
    credentials = SimpleNamespace(clear=lambda: cleared.append("credentials"))
    sink = SimpleNamespace(stats={}, close=lambda: (_ for _ in ()).throw(OSError(errno.ENOSPC, "secret")))
    monkeypatch.setattr(stream_module, "bootstrap", lambda *a, **k: session)
    monkeypatch.setattr(stream_module, "obtain_certificate", lambda *a: credentials)
    monkeypatch.setattr(stream_module, "discover_parent", lambda *a: ([], 1))
    monkeypatch.setattr(stream_module, "MediaSink", lambda *a: sink)
    monkeypatch.setattr(
        stream_module, "receive_media", lambda *a, **k: (_ for _ in ()).throw(GridError("media_header_short"))
    )
    with pytest.raises(GridError, match="media_header_short"):
        receive(Config("fixture_channel", "12345"), Network(), lambda x: None, lambda x: None, {})
    assert cleared == ["credentials", "session"]


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
