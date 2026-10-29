# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Streamlink public Stream/StreamIO integration and bounded backpressure."""

import threading
import time
import logging
import math
import re
from collections import deque
from urllib.parse import urlsplit

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


def validate_media_output(stats):
    """Reject a nominally successful stop when output lacks A/V coverage."""
    if not stats.get("video_output_frames"):
        raise GridError("no_playable_grid_video")
    if not stats.get("audio_output_access_units"):
        raise GridError("no_playable_grid_audio")
    span_status = stats.get("av_track_span_status")
    if span_status == "incomplete":
        raise GridError("media_track_coverage_incomplete")
    if span_status != "consistent":
        raise GridError("media_track_coverage_unverified")


class BoundedBuffer:
    def __init__(self, capacity, timeout):
        if (
            type(capacity) is not int
            or not 188 <= capacity <= 16 << 20
            or type(timeout) not in (int, float)
            or not 0 < timeout < math.inf
        ):
            raise ValueError("buffer configuration invalid")
        self.capacity = capacity
        self.timeout = timeout
        self.data = bytearray()
        self.condition = threading.Condition()
        self.eof = False
        self.cancelled = False
        self.error = None
        self.peak_bytes = 0
        self.last_progress = time.monotonic()
        self.accepted_bytes = 0
        self.discarded_bytes = 0
        self.unaccepted_bytes = 0

    def put(self, data, *, deadline=None, atomic_unit=1):
        view = memoryview(data)
        if type(atomic_unit) is not int or atomic_unit < 1 or len(view) % atomic_unit:
            raise GridError("buffer_output_unit_invalid")
        accepted_any = False
        while view:
            with self.condition:
                while not self.cancelled:
                    now = time.monotonic()
                    if deadline is not None and now >= deadline:
                        self.unaccepted_bytes += len(view)
                        raise GridError(
                            "capture_deadline_reached"
                            if not accepted_any
                            else "duration_limit_output_incomplete"
                        )
                    available = self.capacity - len(self.data)
                    if available >= atomic_unit:
                        break
                    wait_deadline = self.last_progress + self.timeout
                    if deadline is not None:
                        wait_deadline = min(wait_deadline, deadline)
                    remaining = wait_deadline - now
                    if remaining <= 0:
                        if deadline is not None and now >= deadline:
                            self.unaccepted_bytes += len(view)
                            raise GridError(
                                "capture_deadline_reached"
                                if not accepted_any
                                else "duration_limit_output_incomplete"
                            )
                        self.unaccepted_bytes += len(view)
                        raise GridError("buffer_backpressure_timeout")
                    self.condition.wait(remaining)
                if self.cancelled:
                    self.unaccepted_bytes += len(view)
                    raise Cancelled("reader_closed")
                n = min(len(view), self.capacity - len(self.data))
                n -= n % atomic_unit
                if n == 0:
                    continue
                self.data.extend(view[:n])
                accepted_any = True
                self.accepted_bytes += n
                self.peak_bytes = max(self.peak_bytes, len(self.data))
                view = view[n:]
                self.last_progress = time.monotonic()
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
            if n:
                self.last_progress = time.monotonic()
            self.condition.notify_all()
            return data

    def close(self):
        with self.condition:
            self.cancelled = True
            discarded = len(self.data)
            self.data.clear()
            self.discarded_bytes += discarded
            self.condition.notify_all()
            return discarded


def receive(config, network, emit, report, stats):
    session = credentials = sink = None
    primary = None
    try:
        session = bootstrap(config, report, http_factory=lambda: HTTP(network.stop))
        credentials = obtain_certificate(network, session, report)
        session.clear_gateway_auth()
        parents, node_key = discover_parent(network, session.center, credentials, report)
        sink = MediaSink(emit, config.capture_dir, synchronize_startup=True)
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
    # MediaSink.close() flushes the final held IDR and pending output. Only
    # evaluate playable/track completeness after that drain has succeeded.
    if primary is None and sink is not None:
        validate_media_output(sink.stats)


class SoopGridReader(StreamIO):
    def __init__(self, stream, runner=receive):
        super().__init__()
        self.stream = stream
        self.network = Network()
        self.buffer = BoundedBuffer(stream.buffer_size, stream.timeout)
        self.runner = runner
        self.thread = None
        self.stats = {"stream_output_drain_complete": 0}
        self.stages = deque(maxlen=128)
        self._close_lock = threading.Lock()
        self._closing = False
        self.failure_code = None
        self.reader_state = "initializing"
        self.diagnostics = Diagnostics(stream.config.diagnostics_dir, self._snapshot)

    def _snapshot(self):
        facts = dict(self.stats)
        with self.buffer.condition:
            facts.update(
                buffer_used_bytes=len(self.buffer.data),
                buffer_peak_bytes=self.buffer.peak_bytes,
                stream_buffer_accepted_bytes=self.buffer.accepted_bytes,
                stream_buffer_discarded_bytes=self.buffer.discarded_bytes,
                stream_buffer_unaccepted_bytes=self.buffer.unaccepted_bytes,
                stream_output_discarded_bytes=(self.buffer.discarded_bytes + self.buffer.unaccepted_bytes),
            )
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

    def _emit(self, data):
        # The duration deadline limits media acquisition, not delivery of a
        # complete TS block already produced from an accepted Grid frame.
        # Applying it here could drop the whole final block and then have the
        # network read timeout misreport that as a normal duration EOF.
        atomic_unit = 188 if len(data) % 188 == 0 else 1
        self.buffer.put(data, atomic_unit=atomic_unit)

    def open(self):
        thread = threading.Thread(target=self._run, name="soop-grid-receiver", daemon=True)
        start_error = None
        phase = "diagnostics"
        with self._close_lock:
            if self.thread is not None or self.closed:
                start_error = StreamError("Grid reader already opened or closed")
            else:
                try:
                    self.diagnostics.start()
                    phase = "receiver"
                    thread.start()
                except BaseException as error:
                    start_error = (phase, error)
                else:
                    # Publish the started worker before close() can pass the same
                    # lock, so shutdown always joins a worker created by open().
                    self.thread = thread
        if start_error is not None:
            if isinstance(start_error, tuple):
                phase, error = start_error
            else:
                error = start_error
            if phase == "diagnostics":
                self.close()
                raise error
            if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                self.failure_code = "receiver_thread_start_failed"
                self.diagnostics.fail(GridError(self.failure_code))
            self.close()
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, StreamError):
                raise error
            raise StreamError("SOOP Grid: receiver_thread_start_failed") from None
        try:
            self.buffer.wait_ready()
            # close() may race with startup; BoundedBuffer wakes cancelled
            # waiters without raising, so never return an already-closed reader.
            if self._closing or self.closed:
                raise StreamError("Grid reader closed while opening")
        except BaseException as error:
            cancelled = isinstance(error, KeyboardInterrupt) or self._closing or self.closed
            if not cancelled and not self.failure_code:
                self.diagnostics.fail(GridError("reader_timeout"))
            self.close()
            if self.failure_code and isinstance(error, Exception):
                error.grid_error_code = self.failure_code
            raise
        return self

    def _run(self):
        error = None
        try:
            self.runner(self.stream.config, self.network, self._emit, self._report, self.stats)
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
                self.stats["stream_reader_bytes"] = self.stats.get("stream_reader_bytes", 0) + len(result)
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
        with memoryview(b) as view:
            if view.readonly:
                raise TypeError("readinto() requires a writable buffer")
            with view.cast("B") as target:
                data = self.read(len(target))
                target[: len(data)] = data
                return len(data)

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            if self.thread and self.thread.is_alive() and self.diagnostics.reason is None:
                self.diagnostics.complete("user_cancelled")
            self._closing = True
            self.network.close()
            with self.buffer.condition:
                self.buffer.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(12)
            if self.thread.is_alive():
                error = GridError("reader_shutdown_timeout")
                self.failure_code = "reader_shutdown_timeout"
                self.reader_state = "shutdown_timeout"
                self.diagnostics.fail(error, "receiver_internal_error")
                self.diagnostics.event("reader", "reader_shutdown_timeout")
                _log.error("Native Grid receiver did not stop within the shutdown deadline")
        with self.buffer.condition:
            self.stats["stream_buffer_accepted_bytes"] = self.buffer.accepted_bytes
            self.stats["stream_buffer_discarded_bytes"] = self.buffer.discarded_bytes
            self.stats["stream_buffer_unaccepted_bytes"] = self.buffer.unaccepted_bytes
            discarded_bytes = self.buffer.discarded_bytes + self.buffer.unaccepted_bytes
        if discarded_bytes:
            self.stats["stream_output_discarded_bytes"] = discarded_bytes
            self.diagnostics.event("reader", "stream_output_discarded")
        if discarded_bytes and self.diagnostics.reason in ("duration_limit", "byte_limit"):
            self.failure_code = "stream_output_incomplete"
            self.reader_state = "error"
            self.diagnostics.mark_output_incomplete()
            self.diagnostics.event("reader", "stream_output_incomplete")
            _log.error(
                "Native Grid output closed before queued data was consumed (%s bytes)",
                discarded_bytes,
            )
        self.stats["stream_output_drain_complete"] = int(
            self.diagnostics.reason in ("duration_limit", "byte_limit")
            and not discarded_bytes
            and self.stats.get("stream_reader_bytes", 0) == self.stats.get("stream_emit_bytes", 0)
            and self.stats.get("stream_emit_bytes", 0) == self.buffer.accepted_bytes
        )
        self.stream.config.clear()
        if self.reader_state != "shutdown_timeout":
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
        if not 0 < self.timeout < math.inf:
            raise StreamError("Grid stream timeout must be finite and positive")
        self._opened = False

    def open(self):
        if self._opened:
            raise StreamError("Grid streams are single-session; enumerate again to reopen")
        self._opened = True
        try:
            return SoopGridReader(self).open()
        except GridError as exc:
            self.config.clear()
            raise StreamError("SOOP Grid: " + safe_error(exc)) from None
        except BaseException:
            self.config.clear()
            raise


def official_hls_plugin(session, url, account, password, stream_password, purge_credentials=False):
    # Streamlink 8.6.2's SOOP matcher is not anchored and truncates hyphenated
    # channels. Never delegate a different channel or lose an explicit BNO.
    if not re.fullmatch(r"/[A-Za-z0-9_]+(?:/[0-9]+)?/?", urlsplit(url).path):
        raise StreamError("Official SOOP HLS plugin cannot represent this target")
    session.plugins.load_builtin()
    plugin_class = session.plugins.get_loaded().get("soop")
    if plugin_class is None:
        raise StreamError("Streamlink built-in SOOP plugin is unavailable")
    return plugin_class(
        session,
        url,
        options={
            "username": account,
            "password": password,
            "stream-password": stream_password,
            "purge-credentials": purge_credentials,
        },
    )


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
            plugin = official_hls_plugin(
                self.session,
                self.url,
                self.account,
                self.password,
                self.stream_password,
                self.purge_credentials,
            )
            streams = plugin.streams()
            selected = streams.get("original") or streams.get("best") or next(iter(streams.values()), None)
            if selected is None:
                raise StreamError("Official SOOP HLS plugin returned no playable stream")
            return selected.open()
        except Exception:
            # Enumeration raises PluginError, while Stream.open() must report
            # StreamError. Official errors can include signed URLs or tokens.
            raise StreamError("Official SOOP HLS fallback failed") from None
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
