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


def test_error_after_pending_bytes_and_no_secret_exception_text():
    def run(c, n, emit, report, stats):
        emit(b"safe")
        raise ValueError("fixture-private-password")

    r = reader(run).open()
    assert r.read(4) == b"safe"
    with pytest.raises(OSError, match="receiver_internal_failure") as err:
        r.read(1)
    assert "fixture-private" not in str(err.value) and r.closed


def test_open_waits_for_actual_output_and_timeout_cleans_worker():
    def run(c, n, emit, report, stats):
        n.stop.wait(2)

    r = reader(run, timeout=0.03)
    with pytest.raises(StreamError, match="timed out"):
        r.open()
    assert r.closed and not r.thread.is_alive()


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
