# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Streamlink public Stream/StreamIO integration and bounded backpressure."""

import threading
import time
import logging
from collections import deque

from streamlink.exceptions import StreamError
from streamlink.stream.stream import Stream, StreamIO

from .auth import HTTP
from .bootstrap import bootstrap
from .center import discover_parent
from .gateway import obtain_certificate
from .diagnostics import Diagnostics, safe_error, stop_reason
from .media import MediaSink
from .parent import receive_media
from .protocol import Cancelled, GridError, Network


_log = logging.getLogger("streamlink.plugins.soop_grid")
_INITIAL_TRANSPORT_FAILURES = {
    "tcp_connect_failed",
    "peer_closed_or_truncated",
    "network_read_failed",
    "network_read_timeout",
    "network_write_failed",
    "parent_media_stalled",
    "parent_retry_limit",
    "parent_rediscovery_empty",
    "no_usable_server_parent",
}


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
            if self.error is None:
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
    primary = None
    try:
        session = bootstrap(config, report, http_factory=lambda: HTTP(network.stop))
        credentials = obtain_certificate(network, session, report)
        parents, node_key = discover_parent(network, session.center, credentials, report)
        sink = MediaSink(emit, config.capture_dir)
        stats.update(sink.stats)
        sink.stats = stats  # Heartbeats can observe counters while reception is running.
        receive_media(
            network,
            parents,
            session.bno,
            node_key,
            sink,
            report,
            config.duration,
            config.max_bytes,
            rediscover=lambda: discover_parent(network, session.center, credentials, report),
        )
        if not sink.mux.video_frames:
            raise GridError("no_playable_grid_video")
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if sink:
                try:
                    sink.close()
                except Exception:
                    if primary is None:
                        raise
                    report("cleanup_failed_after_primary_error")
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
        self.stages = deque(maxlen=128)
        self._close_lock = threading.Lock()
        self._closing = False
        self.failure_code = None
        self.reader_state = "initializing"
        self.diagnostics = Diagnostics(stream.config.diagnostics_dir, self._snapshot)

    def _snapshot(self):
        facts = dict(self.stats)
        with self.buffer.condition:
            facts.update(buffer_used_bytes=len(self.buffer.data), buffer_peak_bytes=self.buffer.peak_bytes)
        with self.network._lock:
            facts["active_sockets"] = len(self.network._sockets)
        facts["reader_state"] = self.reader_state
        return facts

    def _report(self, event):
        self.stages.append(event)
        stage = next(
            (x for x in ("bootstrap", "gateway", "center", "parent") if event.startswith(x)), "receiver"
        )
        self.diagnostics.event(stage, event)

    def open(self):
        if self.thread is not None or self.closed:
            raise StreamError("Grid reader already opened or closed")
        self.diagnostics.start()
        self.thread = threading.Thread(target=self._run, name="soop-grid-receiver", daemon=True)
        self.thread.start()
        try:
            self.buffer.wait_ready()
        except Exception as error:
            if not self.failure_code:
                self.diagnostics.fail(GridError("reader_timeout"))
            self.close()
            if self.failure_code and error is not None:
                error.grid_error_code = self.failure_code
            raise
        return self

    def _run(self):
        error = None
        try:
            self.runner(self.stream.config, self.network, self.buffer.put, self._report, self.stats)
            self.diagnostics.complete(self.stats.get("stop_reason", "receiver_completed_unknown"))
        except BaseException as exc:
            # Never expose raw HTTP/socket exceptions or arbitrary worker text.
            code = safe_error(exc)
            self.failure_code = code
            reason = (
                "center_rediscovery_failed"
                if self.stats.get("center_rediscovery_failed") and stop_reason(exc) != "authentication_denied"
                else None
            )
            self.diagnostics.fail(exc, reason)
            if not self._closing:
                error = StreamError("SOOP Grid: " + code)
                _log.error("Native Grid receiver stopped: %s (%s)", code, self.diagnostics.reason)
        finally:
            self.network.close()
            self.buffer.finish(error)
            self.diagnostics.event(event="worker_finished")
            self.diagnostics.summary()

    def readable(self):
        return not self.closed

    def read(self, size=-1):
        if self.closed or self._closing:
            return b""
        try:
            result = self.buffer.read(size)
            if result:
                self.reader_state = "streaming"
            elif size != 0:
                self.reader_state = "eof"
                self.diagnostics.event("reader", "reader_eof")
            return result
        except StreamError as exc:
            self.reader_state = "error"
            if not self.failure_code:
                self.diagnostics.fail(GridError("reader_timeout"))
            self.diagnostics.event("reader", "reader_error")
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
            if self.thread and self.thread.is_alive() and self.diagnostics.reason is None:
                self.diagnostics.complete("user_cancelled")
            self._closing = True
            self.network.close()
            self.buffer.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(12)
        self.stream.config.clear()
        self.reader_state = "closed"
        self.diagnostics.event("reader", "reader_closed")
        self.diagnostics.close()
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
        try:
            return SoopGridReader(self).open()
        except GridError as exc:
            raise StreamError("SOOP Grid: " + safe_error(exc)) from None


class OfficialHLSFallback:
    """Lazy, one-shot delegation to Streamlink's maintained SOOP plugin."""

    def __init__(self, session, url, account, password, stream_password, purge_credentials=False):
        self.session, self.url, self.account = session, url, account
        self.password, self.stream_password = password, stream_password
        self.purge_credentials = purge_credentials
        self.used = False

    def __repr__(self):
        return "<OfficialHLSFallback credentials=redacted>"

    def clear(self):
        self.password = self.stream_password = self.account = ""

    def open(self):
        if self.used:
            raise StreamError("Official SOOP HLS fallback is single-use")
        self.used = True
        try:
            self.session.plugins.load_builtin()
            plugin_class = self.session.plugins.get_loaded().get("soop")
            if plugin_class is None:
                raise StreamError("Streamlink built-in SOOP plugin is unavailable")
            plugin = plugin_class(
                self.session,
                self.url,
                options={
                    "username": self.account,
                    "password": self.password,
                    "stream-password": self.stream_password,
                    "purge-credentials": self.purge_credentials,
                },
            )
            streams = plugin.streams()
            selected = streams.get("original") or next(iter(streams.values()), None)
            if selected is None:
                raise StreamError("Official SOOP HLS plugin returned no playable stream")
            return selected.open()
        finally:
            self.clear()


class SoopAutoStream(Stream):
    """Grid-first stream; only pre-output transport failures can select HLS."""

    __shortname__ = "auto"

    def __init__(self, session, grid, fallback):
        super().__init__(session)
        self.grid = grid
        self.fallback = fallback
        self._opened = False

    def open(self):
        if self._opened:
            raise StreamError("SOOP auto streams are single-session; enumerate again to reopen")
        self._opened = True
        try:
            reader = self.grid.open()
            if self.fallback:
                self.fallback.clear()
            return reader
        except StreamError as exc:
            if getattr(exc, "grid_error_code", None) not in _INITIAL_TRANSPORT_FAILURES:
                if self.fallback:
                    self.fallback.clear()
                raise
            if self.fallback is None:
                raise
            _log.warning("Native Grid unavailable before media output; starting the official SOOP HLS stream")
            try:
                return self.fallback.open()
            finally:
                self.fallback.clear()
