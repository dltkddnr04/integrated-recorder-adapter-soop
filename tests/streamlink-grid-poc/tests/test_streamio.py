# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import socket
import threading
import time

import pytest
from streamlink import Streamlink
from streamlink.exceptions import StreamError

from soop_grid.bootstrap import Config
from soop_grid.stream import SoopGridReader, SoopGridStream


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -float("inf"), 0, -1, True])
def test_nonfinite_buffer_waits_are_rejected_before_threads_start(timeout):
    from soop_grid.stream import BoundedBuffer

    with pytest.raises(ValueError, match="buffer configuration invalid"):
        BoundedBuffer(188, timeout)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1])
def test_nonfinite_stream_timeout_is_rejected_during_construction(timeout):
    session = Streamlink()
    session.set_option("stream-timeout", timeout)
    with pytest.raises(StreamError, match="finite and positive"):
        SoopGridStream(session, Config("fixture_channel", "12345"))


def reader(runner, timeout=0.2, capacity=376):
    s = SoopGridStream(Streamlink(), Config("fixture_channel", "12345"))
    s.timeout = timeout
    s.buffer_size = capacity
    return SoopGridReader(s, runner=runner)


def test_open_partial_read_readinto_eof_and_double_close():
    def run(c, n, emit, report, stats):
        emit(b"abcdef")

    r = reader(run).open()
    assert r.read(0) == b"" and r.read(1) == b"a"
    buf = bytearray(2)
    assert r.readinto(buf) == 2 and buf == b"bc"
    assert r.read(20) == b"def" and r.read(1) == b""
    r.close()
    r.close()
    assert r.closed and not r.thread.is_alive() and r.read(1) == b""
    assert r.stats["stream_reader_bytes"] == len(b"abcdef")


def test_streamlink_runner_writes_every_reader_byte_through_eof(tmp_path):
    from streamlink_cli.output.file import FileOutput
    from streamlink_cli.streamrunner import StreamRunner

    payload = b"\x47" + b"x" * 187
    payload *= 100
    output_path = tmp_path / "stream.ts"

    def run(config, network, emit, report, stats):
        emit(payload)
        stats["stream_emit_bytes"] = len(payload)
        stats["stop_reason"] = "duration_limit"

    source = reader(run, timeout=2, capacity=376).open()
    prebuffer = source.read(8192)
    output = FileOutput(filename=output_path)
    output.open()
    try:
        StreamRunner(source, output).run(prebuffer)
    finally:
        output.close()
        source.close()

    assert output_path.read_bytes() == payload
    assert source.stats["stream_reader_bytes"] == len(payload)
    assert source.stats["stream_emit_bytes"] == source.stats["stream_buffer_accepted_bytes"]
    assert source.stats["stream_reader_bytes"] == source.stats["stream_buffer_accepted_bytes"]
    assert source.stats["stream_buffer_discarded_bytes"] == 0
    assert source.stats["stream_buffer_unaccepted_bytes"] == 0
    assert source.diagnostics.reason == "duration_limit"
    assert "stream_output_discarded_bytes" not in source.stats


def test_streamlink_output_write_failure_is_not_a_successful_partial_recording():
    from streamlink_cli.streamrunner import StreamRunner

    payload = (b"\x47" + b"x" * 187) * 4

    def run(config, network, emit, report, stats):
        emit(payload)
        stats["stream_emit_bytes"] = len(payload)
        stats["stop_reason"] = "duration_limit"

    class FailingOutput:
        def __init__(self):
            self.writes = 0
            self.bytes_written = 0

        def write(self, data):
            self.writes += 1
            if self.writes == 2:
                raise OSError("fixture output disk full")
            self.bytes_written += len(data)

    source = reader(run, timeout=1, capacity=len(payload)).open()
    output = FailingOutput()
    prebuffer = source.read(188)
    with pytest.raises(OSError, match="Error when writing to output"):
        StreamRunner(source, output).run(prebuffer)
    assert output.bytes_written < len(payload)
    assert source.closed and not source.thread.is_alive()
    # StreamIO delivery is complete even though the external writer failed;
    # the StreamRunner exception, not this reader counter, determines CLI success.
    assert source.stats["stream_reader_bytes"] == len(payload)
    assert source.stats["stream_emit_bytes"] == len(payload)


def test_error_after_pending_bytes_and_no_secret_exception_text():
    def run(c, n, emit, report, stats):
        emit(b"safe")
        stats["stream_emit_bytes"] = 4
        raise ValueError("fixture-private-password")

    r = reader(run).open()
    assert r.read(4) == b"safe"
    with pytest.raises(OSError, match="receiver_internal_failure") as err:
        r.read(1)
    assert "fixture-private" not in str(err.value) and r.closed
    assert r.stats["stream_reader_bytes"] == r.stats["stream_emit_bytes"] == 4
    assert r.stats["stream_buffer_accepted_bytes"] == 4
    assert r.stats["stream_buffer_discarded_bytes"] == r.stats["stream_buffer_unaccepted_bytes"] == 0


def test_open_waits_for_actual_output_and_timeout_cleans_worker():
    def run(c, n, emit, report, stats):
        n.stop.wait(2)

    r = reader(run, timeout=0.03)
    with pytest.raises(StreamError, match="timed out"):
        r.open()
    assert r.closed and not r.thread.is_alive()


def test_keyboard_interrupt_during_open_cleans_worker_and_credentials(monkeypatch):
    config = Config("fixture_channel", "12345", account="fixture_user", account_password="fixture-pass")

    def run(c, n, emit, report, stats):
        n.stop.wait(2)

    stream = SoopGridStream(Streamlink(), config)
    r = SoopGridReader(stream, runner=run)
    monkeypatch.setattr(r.buffer, "wait_ready", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        r.open()

    assert r.closed and not r.thread.is_alive()
    assert r.network.stop.is_set()
    assert config.account == config.account_password == config.stream_password == ""
    assert r.diagnostics.reason == "user_cancelled"


def test_close_during_open_does_not_return_closed_reader():
    started = threading.Event()
    opened = []

    def run(c, n, emit, report, stats):
        started.set()
        n.stop.wait(2)

    r = reader(run, timeout=1)

    def opening():
        try:
            r.open()
            opened.append("returned")
        except BaseException as exc:
            opened.append(exc)

    waiter = threading.Thread(target=opening)
    waiter.start()
    assert started.wait(1)
    r.close()
    waiter.join(2)

    assert not waiter.is_alive()
    assert len(opened) == 1 and isinstance(opened[0], StreamError)
    assert "closed while opening" in str(opened[0])
    assert r.closed and not r.thread.is_alive()
    assert r.diagnostics.reason == "user_cancelled"


def test_close_cannot_miss_receiver_during_thread_start(monkeypatch):
    starting = threading.Event()
    allow_start = threading.Event()
    close_done = threading.Event()
    real_start = threading.Thread.start

    def delayed_start(thread):
        if thread.name == "soop-grid-receiver":
            starting.set()
            assert allow_start.wait(2)
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", delayed_start)
    r = reader(lambda c, n, emit, report, stats: n.stop.wait(2))
    opened = []

    def opening():
        try:
            r.open()
            opened.append("returned")
        except BaseException as error:
            opened.append(error)

    opener = threading.Thread(target=opening)
    opener.start()
    assert starting.wait(1)

    closer = threading.Thread(target=lambda: (r.close(), close_done.set()))
    closer.start()
    assert not close_done.wait(0.05)
    allow_start.set()

    opener.join(3)
    closer.join(3)
    assert not opener.is_alive() and not closer.is_alive()
    assert len(opened) == 1 and isinstance(opened[0], StreamError)
    assert r.thread is not None and not r.thread.is_alive()
    assert close_done.is_set()


def test_close_reports_worker_that_outlives_bounded_join(monkeypatch):
    release = threading.Event()

    def run(c, n, emit, report, stats):
        emit(b"ready")
        release.wait(2)

    r = reader(run).open()
    worker = r.thread
    real_join = worker.join
    monkeypatch.setattr(worker, "join", lambda timeout=None: None)
    r.close()
    assert worker.is_alive()
    assert r.failure_code == "reader_shutdown_timeout"
    assert r.reader_state == "shutdown_timeout"
    assert r.diagnostics.reason == "receiver_internal_error"
    release.set()
    real_join(2)
    assert not worker.is_alive()


def test_read_timeout_closes_running_worker():
    def run(c, n, emit, report, stats):
        emit(b"a")
        n.stop.wait(2)

    r = reader(run, timeout=0.03).open()
    assert r.read(1) == b"a"
    with pytest.raises(OSError, match="timed out"):
        r.read(1)
    assert r.closed and not r.thread.is_alive()


def test_backpressure_is_bounded_and_error_is_propagated():
    def run(c, n, emit, report, stats):
        emit(b"x" * 1024)

    r = reader(run, timeout=0.03, capacity=188).open()
    time.sleep(0.08)
    assert r.buffer.peak_bytes == 188
    assert len(r.read(1000)) == 188
    with pytest.raises(OSError, match="backpressure_timeout"):
        r.read(1)
    assert not r.thread.is_alive()


def test_backpressure_timeout_tracks_stall_not_total_large_emit_time(monkeypatch):
    from soop_grid.stream import BoundedBuffer
    from types import SimpleNamespace
    import soop_grid.stream as stream_module

    # Advance the producer's monotonic clock only as the consumer makes
    # progress. Condition handoffs, rather than scheduler-sensitive sleeps,
    # keep this regression deterministic while total simulated time exceeds
    # the per-stall budget.
    clock = [100.0]
    monkeypatch.setattr(stream_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    output = BoundedBuffer(188, timeout=0.06)
    payload = b"x" * (188 * 6)
    errors = []

    def producer():
        try:
            output.put(payload, atomic_unit=188)
        except BaseException as exc:
            errors.append(exc)
        finally:
            output.finish(errors[0] if errors else None)

    thread = threading.Thread(target=producer)
    thread.start()
    received = bytearray()
    while len(received) < len(payload):
        with output.condition:
            assert output.condition.wait_for(lambda: len(output.data) >= 188 or output.eof, timeout=1)
            assert len(output.data) >= 188
            received.extend(output.read(94))
            clock[0] += 0.04
            received.extend(output.read(94))
    thread.join(1)
    assert not thread.is_alive()
    assert not errors
    assert received == payload
    assert clock[0] - 100.0 > output.timeout


def test_concurrent_close_unblocks_read_and_closes_socket():
    sockets = []

    def run(c, n, emit, report, stats):
        left, right = socket.socketpair()
        sockets.extend((left, right))
        n._sockets.add(left)
        emit(b"ready")
        n.stop.wait(2)
        right.close()

    r = reader(run, timeout=1).open()
    assert r.read(5) == b"ready"
    output = []
    waiter = threading.Thread(target=lambda: output.append(r.read(1)))
    waiter.start()
    threads = [threading.Thread(target=r.close) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2)
    waiter.join(2)
    assert output == [b""] and not r.thread.is_alive()
    assert all(s.fileno() == -1 for s in sockets)


def test_empty_normal_eof_and_single_session_stream():
    r = reader(lambda *a: None).open()
    assert r.read(1) == b""
    r.close()


def test_readinto_accepts_typed_writable_buffers():
    from array import array

    with reader(lambda c, n, emit, report, stats: emit(b"abcdefgh")).open() as output:
        target = array("I", [0, 0])
        assert output.readinto(target) == 8
        assert target.tobytes() == b"abcdefgh"


def test_invalid_readinto_does_not_consume_stream_bytes():
    with reader(lambda c, n, emit, report, stats: emit(b"safe")).open() as output:
        with pytest.raises(TypeError):
            output.readinto(b"xxxx")
        assert output.read(4) == b"safe"


def test_failed_reader_construction_clears_grid_credentials(tmp_path):
    config = Config("fixture_channel", "12345", account="fixture_user", account_password="fixture-pass")
    config.diagnostics_dir = str(tmp_path)  # Existing paths must fail closed, before any worker starts.
    stream = SoopGridStream(Streamlink(), config)
    with pytest.raises(StreamError, match="diagnostic_write_failed"):
        stream.open()
    assert config.account == config.account_password == config.stream_password == ""


def test_receiver_thread_start_failure_closes_diagnostics_and_buffer(monkeypatch, tmp_path):
    stream = SoopGridStream(Streamlink(), Config("fixture_channel", "12345"))
    stream.timeout = 0.2
    stream.buffer_size = 376
    stream.config.diagnostics_dir = str(tmp_path / "diagnostics")
    r = SoopGridReader(stream, runner=lambda *args: None)
    real_start = threading.Thread.start

    def start(thread):
        if thread.name == "soop-grid-receiver":
            raise RuntimeError("injected thread start failure")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(StreamError, match="receiver_thread_start_failed"):
        r.open()
    assert r.closed
    assert r.buffer.cancelled
    assert r.diagnostics.file is None
    assert r.diagnostics.thread is not None and not r.diagnostics.thread.is_alive()


def test_diagnostics_thread_start_failure_closes_file_and_config(monkeypatch, tmp_path):
    from soop_grid.bootstrap import Config

    config = Config("fixture_channel", "12345", account="fixture_user", account_password="fixture-pass")
    config.diagnostics_dir = str(tmp_path / "diagnostics")
    stream = SoopGridStream(Streamlink(), config)
    real_start = threading.Thread.start

    def start(thread):
        if thread.name == "soop-grid-diagnostics":
            raise RuntimeError("injected diagnostics thread start failure")
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(StreamError, match="diagnostic_thread_start_failed"):
        stream.open()
    assert config.account == config.account_password == ""


def test_backpressure_preserves_all_bytes_before_deferred_error():
    from soop_grid.protocol import GridError

    payload = bytes(range(256)) * 23

    def run(config, network, emit, report, stats):
        emit(payload)
        raise GridError("network_read_failed")

    with reader(run, timeout=2, capacity=188).open() as output:
        received = bytearray()
        sizes = (1, 187, 3, 512, 67)
        while len(received) < len(payload):
            chunk = output.read(sizes[len(received) % len(sizes)])
            assert chunk
            received.extend(chunk)
        assert received == payload
        with pytest.raises(OSError, match="network_read_failed"):
            output.read(1)
        assert output.buffer.peak_bytes <= 188
        assert not output.thread.is_alive()


def test_close_releases_producer_blocked_by_full_buffer():
    producing = threading.Event()

    def run(config, network, emit, report, stats):
        producing.set()
        emit(b"x" * 1024)

    output = reader(run, timeout=2, capacity=188).open()
    assert producing.wait(1)
    output.close()
    assert output.closed and not output.thread.is_alive()
    assert output.buffer.data == b""
    assert output.stats["stream_buffer_accepted_bytes"] == 188
    assert output.stats["stream_buffer_discarded_bytes"] == 188
    assert output.stats["stream_buffer_unaccepted_bytes"] == 1024 - 188
    assert output.stats["stream_output_discarded_bytes"] == 1024
    assert output.diagnostics.reason == "user_cancelled"


def test_duration_deadline_does_not_truncate_a_complete_output_block():
    payload = b"".join(bytes((0x47,)) + bytes((i % 256,)) * 187 for i in range(4))

    def run(config, network, emit, report, stats):
        network.deadline = time.monotonic() - 0.01
        emit(payload)

    output = reader(run, timeout=1, capacity=376).open()
    received = bytearray()
    while chunk := output.read(188):
        received.extend(chunk)
    assert received == payload
    assert len(received) % 188 == 0
    assert not output.thread.is_alive()


def test_duration_output_remains_backpressured_and_bounded_after_deadline():
    payload = (b"\x47" + b"x" * 187) * 20

    def run(config, network, emit, report, stats):
        network.deadline = time.monotonic() - 0.01
        emit(payload)

    output = reader(run, timeout=1, capacity=376).open()
    received = bytearray()
    while chunk := output.read(188):
        received.extend(chunk)
    assert received == payload
    assert output.buffer.peak_bytes <= output.buffer.capacity
    assert not output.thread.is_alive()


def test_close_with_undrained_duration_output_is_not_reported_as_success():
    payload = b"\x47" + b"x" * 187

    def run(config, network, emit, report, stats):
        emit(payload)
        stats["stream_emit_bytes"] = len(payload)
        stats["stop_reason"] = "duration_limit"

    output = reader(run, timeout=1, capacity=376).open()
    output.thread.join(1)
    assert not output.thread.is_alive()
    assert output.diagnostics.reason == "duration_limit"
    assert len(output.buffer.data) == len(payload)

    output.close()

    assert output.diagnostics.reason == "stream_output_incomplete"
    assert output.diagnostics.error_code == "stream_output_incomplete"
    assert output.stats["stream_output_discarded_bytes"] == len(payload)
    assert output.stats["stream_buffer_discarded_bytes"] == len(payload)
    assert output.stats["stream_buffer_unaccepted_bytes"] == 0
    assert output.stats["stream_output_drain_complete"] == 0
    assert output.stats["stream_emit_bytes"] == output.stats["stream_buffer_accepted_bytes"] == len(payload)
    assert output.stats.get("stream_reader_bytes", 0) == 0
    assert output.buffer.data == b""


def test_diagnostics_summary_records_undrained_output_after_close(tmp_path):
    import json

    payload = b"\x47" + b"x" * 187
    config = Config("fixture_channel", "12345", diagnostics_dir=str(tmp_path / "diagnostics"))
    stream = SoopGridStream(Streamlink(), config)
    stream.timeout = 1
    stream.buffer_size = 376

    def run(c, network, emit, report, stats):
        emit(payload)
        stats["stream_emit_bytes"] = len(payload)
        stats["stop_reason"] = "duration_limit"

    output = SoopGridReader(stream, runner=run).open()
    output.thread.join(1)
    assert not output.thread.is_alive()
    output.close()

    summary = json.loads((tmp_path / "diagnostics" / "summary.json").read_text())
    assert summary["stop_reason"] == "stream_output_incomplete"
    assert summary["error_code"] == "stream_output_incomplete"
    assert summary["stream_output_discarded_bytes"] == len(payload)
    assert summary["stream_buffer_accepted_bytes"] == len(payload)
    assert summary["stream_buffer_discarded_bytes"] == len(payload)
    assert summary["stream_output_drain_complete"] == 0


def test_fully_drained_duration_output_remains_successful():
    payload = b"\x47" + b"x" * 187

    def run(config, network, emit, report, stats):
        emit(payload)
        stats["stream_emit_bytes"] = len(payload)
        stats["stop_reason"] = "duration_limit"

    output = reader(run, timeout=1, capacity=376).open()
    assert output.read(len(payload)) == payload
    assert output.read(1) == b""
    output.close()

    assert output.diagnostics.reason == "duration_limit"
    assert "stream_output_discarded_bytes" not in output.stats
    assert output.stats["stream_output_drain_complete"] == 1
    assert output.stats["stream_emit_bytes"] == output.stats["stream_reader_bytes"]
    assert output.stats["stream_reader_bytes"] == output.stats["stream_buffer_accepted_bytes"]


def test_user_cancel_preserves_cancellation_reason_with_queued_output():
    payload = b"\x47" + b"x" * 187

    def run(config, network, emit, report, stats):
        emit(payload)
        network.stop.wait(1)

    output = reader(run, timeout=1, capacity=376).open()
    output.close()
    assert not output.thread.is_alive()
    assert output.diagnostics.reason == "user_cancelled"
    assert output.stats["stream_output_discarded_bytes"] == len(payload)
    assert output.stats["stream_output_drain_complete"] == 0
    output.close()
    assert output.stats["stream_output_discarded_bytes"] == len(payload)
