# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Streamlink public Stream/StreamIO integration and bounded backpressure."""

import threading
import time

from streamlink.exceptions import StreamError
from streamlink.stream.stream import Stream, StreamIO

from .auth import HTTP
from .bootstrap import bootstrap
from .center import discover_parent
from .gateway import obtain_certificate
from .media import MediaSink
from .parent import receive_media
from .protocol import Cancelled, GridError, Network


class BoundedBuffer:
    def __init__(self, capacity, timeout):
        if not 188 <= capacity <= 16 << 20 or timeout <= 0:
            raise ValueError("buffer configuration invalid")
        self.capacity = capacity
        self.timeout = timeout
        self.data = bytearray()
        self.condition = threading.Condition()
        self.eof = False
        self.cancelled = False
        self.error = None
        self.peak_bytes = 0

    def put(self, data):
        view = memoryview(data)
        while view:
            deadline = time.monotonic() + self.timeout
            with self.condition:
                while len(self.data) >= self.capacity and not self.cancelled:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise GridError("buffer_backpressure_timeout")
                    self.condition.wait(remaining)
                if self.cancelled:
                    raise Cancelled("reader_closed")
                n = min(len(view), self.capacity - len(self.data))
                self.data.extend(view[:n])
                self.peak_bytes = max(self.peak_bytes, len(self.data))
                view = view[n:]
                self.condition.notify_all()

    def finish(self, error=None):
        with self.condition:
            self.error = error
            self.eof = True
            self.condition.notify_all()

    def wait_ready(self):
        with self.condition:
            self._wait()

    def _wait(self):
        deadline = time.monotonic() + self.timeout
        while not self.data and not self.eof and not self.cancelled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StreamError("Grid read timed out")
            self.condition.wait(remaining)
        if not self.data and self.error and not self.cancelled:
            raise self.error

    def read(self, size):
        if size == 0:
            return b""
        with self.condition:
            self._wait()
            n = min(size if size >= 0 else self.capacity, len(self.data))
            data = bytes(self.data[:n])
            del self.data[:n]
            self.condition.notify_all()
            return data

    def close(self):
        with self.condition:
            self.cancelled = True
            self.data.clear()
            self.condition.notify_all()


def receive(config, network, emit, report, stats):
    session = credentials = sink = None
    try:
        session = bootstrap(config, report, http_factory=lambda: HTTP(network.stop))
        credentials = obtain_certificate(network, session, report)
        parents, node_key = discover_parent(network, session.center, credentials, report)
        sink = MediaSink(emit, config.capture_dir)
        receive_media(
            network,
            parents,
            session.bno,
            node_key,
            sink,
            report,
            config.duration,
            rediscover=lambda: discover_parent(network, session.center, credentials, report),
        )
        if not sink.mux.video_frames:
            raise GridError("no_playable_grid_video")
    finally:
        try:
            if sink:
                sink.close()
                stats.update(sink.stats)
        finally:
            if credentials:
                credentials.clear()
            if session:
                session.clear()
            config.clear()
            network.close()


class SoopGridReader(StreamIO):
    def __init__(self, stream, runner=receive):
        super().__init__()
        self.stream = stream
        self.network = Network()
        self.buffer = BoundedBuffer(stream.buffer_size, stream.timeout)
        self.runner = runner
        self.thread = None
        self.stats = {}
        self.stages = []
        self._close_lock = threading.Lock()
        self._closing = False

    def open(self):
        if self.thread is not None or self.closed:
            raise StreamError("Grid reader already opened or closed")
        self.thread = threading.Thread(target=self._run, name="soop-grid-receiver", daemon=True)
        self.thread.start()
        try:
            self.buffer.wait_ready()
        except Exception:
            self.close()
            raise
        return self

    def _run(self):
        error = None
        try:
            self.runner(self.stream.config, self.network, self.buffer.put, self.stages.append, self.stats)
        except Exception as exc:
            # Never expose raw HTTP/socket exceptions or arbitrary worker text.
            code = str(exc) if isinstance(exc, GridError) else "receiver_internal_failure"
            if not self._closing:
                error = StreamError("SOOP Grid: " + code)
        finally:
            self.network.close()
            self.buffer.finish(error)

    def readable(self):
        return not self.closed

    def read(self, size=-1):
        if self.closed or self._closing:
            return b""
        try:
            return self.buffer.read(size)
        except StreamError as exc:
            self.close()
            # Streamlink's output runner expects OSError from read(), while
            # Stream.open() reports StreamError. Keep both interfaces correct.
            raise OSError(str(exc)) from None

    def readinto(self, b):
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            self._closing = True
            self.network.close()
            self.buffer.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(12)
        self.stream.config.clear()
        super().close()


class SoopGridStream(Stream):
    __shortname__ = "grid"

    def __init__(self, session, config):
        super().__init__(session)
        self.config = config
        self.buffer_size = min(max(int(session.get_option("ringbuffer-size") or 8 << 20), 188), 16 << 20)
        self.timeout = float(session.get_option("stream-timeout") or 60)
        self._opened = False

    def open(self):
        if self._opened:
            raise StreamError("Grid streams are single-session; enumerate again to reopen")
        self._opened = True
        return SoopGridReader(self).open()
