# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Fault-injected peers are synthetic; no real SOOP outage is implied."""

import socket
import struct
import threading

import pytest

from conftest import media_body, synthetic_sps
from soop_grid.media import MediaSink, parse_media
from soop_grid.parent import Parent, receive_media
from soop_grid.protocol import Cancelled, GridError, parent_packet


class SyntheticSink:
    """Protocol-only sink; media bytes in these tests are intentionally fake."""

    class Mux:
        def __init__(self):
            self.video_frames = 0
            self.cc = {}

    def __init__(self, emit):
        self.emit = emit
        self.mux = self.Mux()
        self.stats = {"wire_bytes": 0, "media_packets": 0, "video_frames": 0, "audio_frames": 0}

    def add(self, frame, discard=False):
        chunk = parse_media(frame.body)
        self.stats["wire_bytes"] += len(frame.wire)
        self.stats["media_packets"] += 1
        if discard:
            return chunk
        if chunk.kind in ("I", "P", "B"):
            self.stats["video_frames"] += 1
            self.mux.video_frames += 1
            self.mux.cc[0x100] = (self.mux.cc.get(0x100, 0) + 1) & 15
        else:
            self.stats["audio_frames"] += 1
            self.mux.cc[0x101] = (self.mux.cc.get(0x101, 0) + 1) & 15
        self.emit(struct.pack(">Q", chunk.frame))
        return chunk

    def close(self):
        pass


def media(number, timestamp=None, payload=None, kind="I"):
    return parent_packet(
        0xC748,
        media_body(
            synthetic_sps() if payload is None else payload,
            kind,
            number,
            timestamp if timestamp is not None else 50_000_000 + number * 166667,
        ),
    )


def controls():
    return parent_packet(0xC746) + parent_packet(0xC747)


class SyntheticSocket:
    def __init__(self, wire, end, clock, stop):
        self.wire = bytearray(wire)
        self.end, self.clock, self.stop = end, clock, stop
        self.closed = False

    def settimeout(self, timeout):
        assert 0 < timeout <= 3

    def recv(self, n):
        if self.wire:
            # Exercise actual fragmented wire decoder.
            n = min(n, 7, len(self.wire))
            out = bytes(self.wire[:n])
            del self.wire[:n]
            return out
        if self.end == "deadline":
            self.clock[0] = 61
            raise socket.timeout()
        if self.end == "cancel":
            self.stop.set()
            raise OSError()
        if self.end == "timeout":
            raise socket.timeout()
        if self.end == "reset":
            raise ConnectionResetError()
        return b""

    def close(self):
        self.closed = True


class SyntheticNetwork:
    def __init__(self, scripts, clock):
        self.stop = threading.Event()
        self.scripts = list(scripts)
        self.clock = clock
        self.sockets = []
        self.writes = []

    def connect(self, _):
        wire, end = self.scripts.pop(0)
        if end == "connect":
            raise GridError("tcp_connect_failed")
        sock = SyntheticSocket(wire, end, self.clock, self.stop)
        self.sockets.append(sock)
        return sock

    def send(self, sock, data):
        self.writes.append(data)

    def release(self, sock):
        sock.close()


@pytest.fixture
def simulation(monkeypatch):
    import soop_grid.parent as parent

    clock = [0]
    monkeypatch.setattr(parent.time, "monotonic", lambda: clock[0])

    def run(scripts, rediscover=None, count=None, sink_class=SyntheticSink):
        network = SyntheticNetwork(scripts, clock)
        out, events = [], []
        sink = sink_class(out.append)
        candidates = [Parent(("8.8.8.8", 10000 + n), 1, 0) for n in range(count or len(scripts))]
        try:
            receive_media(network, candidates, 12345, 42, sink, events.append, 35, rediscover=rediscover)
        finally:
            sink.close()
            assert all(s.closed for s in network.sockets)
        return sink, out, events, network

    return run


def test_first_connect_failure_uses_next_candidate(simulation):
    sink, _, events, _ = simulation([(b"", "connect"), (controls() + media(1), "deadline")])
    assert sink.stats["video_frames"] == 1
    assert sink.stats["parent_connections"] == 1
    assert "parent_candidate_failed" in events
    assert "parent_failover_started" in events


@pytest.mark.parametrize("failure", ["eof", "reset", "timeout"])
def test_cache_overlap_recovers_without_resetting_mux(simulation, failure):
    baseline = SyntheticSink(lambda _: None)
    from soop_grid.protocol import Frame

    for n in (1, 2, 3):
        wire = media(n)
        baseline.add(Frame(0xC748, wire[16:], wire=wire))
    sink, out, events, net = simulation(
        [
            (controls() + media(1) + media(2), failure),
            (controls() + media(1) + media(2) + media(3), "deadline"),
        ]
    )
    assert sink.stats["video_frames"] == 3
    assert sink.stats["media_packets"] == 5  # Raw duplicates remain in evidence.
    assert sink.stats["duplicate_frames_excluded"] == 2
    assert sink.stats["failover_successes"] == 1
    assert sink.stats["recovered_ranges"] == 1
    assert sink.mux.cc == baseline.mux.cc
    expected = []
    baseline = SyntheticSink(expected.append)
    for n in (1, 2, 3):
        wire = media(n)
        baseline.add(Frame(0xC748, wire[16:], wire=wire))
    assert b"".join(out) == b"".join(expected)
    starts = [
        struct.unpack_from("<IQI", w, 16)[1]
        for w in net.writes
        if struct.unpack_from("<H", w, 2)[0] == 0xCB30
    ]
    assert starts == [1, 2]
    assert events.count("parent_failover_succeeded") == 1


@pytest.mark.parametrize(
    "second,code",
    [
        (media(3), "recovery_overlap_unavailable"),
        (media(2) + media(4), "frame_sequence_gap_or_reset"),
        (media(2) + media(3, timestamp=1), "frame_timestamp_regression"),
        (media(2, payload=synthetic_sps(1280, 720)), "frame_identity_conflict"),
        (media(2) + media(0), "frame_sequence_gap_or_reset"),
    ],
)
def test_missing_cache_reset_and_regression_are_explicit_failures(simulation, second, code):
    with pytest.raises(GridError, match=code):
        simulation([(controls() + media(1) + media(2), "eof"), (controls() + second, "deadline")])


def test_all_candidates_and_rediscovery_fail_without_unlimited_retry(simulation):
    calls = []

    def rediscover():
        calls.append(1)
        raise GridError("center_access_denied")

    with pytest.raises(GridError, match="center_access_denied"):
        simulation([(b"", "connect"), (b"", "connect")], rediscover)
    assert calls == [1]


def test_rediscovery_uses_new_session_node_key(simulation):
    def rediscover():
        return [Parent(("1.1.1.1", 10000), 1, 0)], 123

    sink, _, _, network = simulation(
        [
            (controls() + media(1), "eof"),
            (controls() + media(1) + media(2), "deadline"),
        ],
        rediscover,
        count=1,
    )
    keys = [
        struct.unpack_from("<III", wire, 16)[1]
        for wire in network.writes
        if struct.unpack_from("<H", wire, 2)[0] == 0xCB2E
    ]
    assert keys == [42, 123] and sink.stats["rediscoveries"] == 1


def test_repeated_timeouts_exhaust_candidates(simulation):
    with pytest.raises(GridError, match="network_read_timeout"):
        simulation([(b"", "timeout")] * 3)


def test_cancel_during_recovery_releases_socket(simulation):
    with pytest.raises(Cancelled):
        simulation([(controls() + media(1), "eof"), (b"", "cancel")])


def test_global_deadline_does_not_restart_on_failover(simulation):
    with pytest.raises(GridError, match="recovery_deadline_exhausted"):
        simulation([(controls() + media(1), "eof"), (b"", "deadline")])


def test_unbounded_session_packet_window_resets_after_accepted_media(monkeypatch):
    import soop_grid.parent as parent
    from soop_grid.protocol import Frame

    stop = threading.Event()
    calls = [0]

    class Net:
        def __init__(self):
            self.stop = stop

        def connect(self, address):
            return object()

        def send(self, *args):
            pass

        def release(self, *args):
            pass

    def next_packet(*_args):
        calls[0] += 1
        if calls[0] > 8192:
            stop.set()
        packet = media(calls[0])
        return Frame(0xC748, packet[16:], wire=packet)

    monkeypatch.setattr(parent, "read_frame", next_packet)
    sink = SyntheticSink(lambda _: None)
    receive_media(
        Net(),
        [Parent(("8.8.8.8", 10000), 1, 0)],
        12345,
        42,
        sink,
        lambda _: None,
        duration=None,
        max_bytes=None,
    )
    assert calls[0] == 8193
    assert sink.stats["parent_packets"] == 8193
    assert sink.stats["video_frames"] == 8193


@pytest.mark.parametrize(
    "wire,code",
    [
        (b"\0" * 16, "parent_header_invalid"),
        (parent_packet(0xC748, b"invalid"), "media_header_short"),
    ],
)
def test_protocol_failures_never_rotate_to_another_parent(simulation, wire, code):
    with pytest.raises(GridError, match=code):
        simulation([(wire, "eof"), (controls() + media(1), "deadline")])


def test_access_denial_is_terminal(monkeypatch, simulation):
    from soop_grid.protocol import Frame
    import soop_grid.parent as parent

    monkeypatch.setattr(parent, "read_frame", lambda *args: Frame(0xC746, b"", result=-1))
    with pytest.raises(GridError, match="parent_access_denied"):
        simulation([(b"", "eof"), (b"", "eof")])


def test_real_local_tcp_parent_disconnect_and_overlap_recovery():
    """Loopback servers are test-only; production endpoint validation is unchanged."""
    from soop_grid.protocol import Network, read_frame

    servers, threads, failures = [], [], []

    def serve(listener, frames, cache_start):
        try:
            listener.settimeout(3)
            conn, _ = listener.accept()
            with conn:
                assert read_frame(conn, "parent").command == 0xCB2E
                conn.sendall(controls())
                assert read_frame(conn, "parent").command == 0xCB2F
                request = read_frame(conn, "parent")
                assert request.command == 0xCB30
                assert struct.unpack_from("<Q", request.body, 4)[0] == cache_start
                conn.sendall(frames)
                if cache_start == 2:
                    # Hold final live peer until the single global 1s budget expires.
                    conn.settimeout(2)
                    try:
                        conn.recv(1)
                    except socket.timeout:
                        pass
        except Exception as exc:
            failures.append(exc)
        finally:
            listener.close()

    for frames, start in ((media(1) + media(2), 1), (media(2) + media(3), 2)):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        servers.append(listener.getsockname())
        t = threading.Thread(target=serve, args=(listener, frames, start))
        t.start()
        threads.append(t)

    class LocalNetwork(Network):
        def connect(self, address):
            # Only the two loopback endpoints allocated by this test.
            assert address in servers
            sock = socket.create_connection(address, timeout=1)
            self._sockets.add(sock)
            return sock

    network = LocalNetwork()
    sink = SyntheticSink(lambda _: None)
    try:
        receive_media(
            network, [Parent(x, 1, 0) for x in servers], 12345, 42, sink, lambda _: None, duration=1
        )
    finally:
        network.close()
        for t in threads:
            t.join(3)
    assert not failures and not any(t.is_alive() for t in threads)
    assert sink.stats["failover_successes"] == 1 and sink.stats["video_frames"] == 3
    assert not network._sockets


def test_recovered_ts_decodes_in_full(simulation, tmp_path, encoded_bframes):
    """Generated B-frame/AAC input verifies DTS, PTS, and complete decoding."""
    import os
    import shutil
    import subprocess

    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("optional FFmpeg post-validation tool unavailable")
    audio = tmp_path / "generated.aac"

    def run(args):
        p = subprocess.run([ffmpeg, "-v", "error", "-nostdin", *args], capture_output=True, timeout=20)
        assert p.returncode == 0, p.stderr.decode()

    run(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "1",
            "-c:a",
            "aac",
            "-f",
            "adts",
            str(audio),
        ]
    )
    samples = [(i * 333_333, payload, kind) for i, (payload, kind) in enumerate(encoded_bframes)]
    data = audio.read_bytes()
    offset = index = 0
    while offset < len(data):
        length = ((data[offset + 3] & 3) << 11) | (data[offset + 4] << 3) | (data[offset + 5] >> 5)
        assert length >= 7
        samples.append((index * 1024 * 10_000_000 // 48000, data[offset : offset + length], "A"))
        offset += length
        index += 1
    samples.sort(key=lambda x: x[0])
    wires = [
        parent_packet(0xC748, media_body(payload, kind, n + 1, 50_000_000 + timestamp))
        for n, (timestamp, payload, kind) in enumerate(samples)
    ]
    sink, out, _, _ = simulation(
        [
            (controls() + b"".join(wires[:20]), "eof"),
            (controls() + b"".join(wires[17:]), "deadline"),
        ],
        sink_class=MediaSink,
    )
    assert sink.stats["failover_successes"] == 1
    assert sink.stats["duplicate_frames_excluded"] == 3
    ts = tmp_path / "recovered.ts"
    ts_bytes = b"".join(out)
    ts.write_bytes(ts_bytes)
    pcr_values = []
    for offset in range(0, len(ts_bytes), 188):
        packet = ts_bytes[offset : offset + 188]
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        # PCR_flag is meaningful only when the adaptation field actually has
        # enough bytes for flags plus the six-byte PCR value.
        if pid == 0x100 and packet[3] & 0x20 and packet[4] >= 7 and packet[5] & 0x10:
            pcr_values.append(
                (packet[6] << 25)
                | (packet[7] << 17)
                | (packet[8] << 9)
                | (packet[9] << 1)
                | (packet[10] >> 7)
            )
    assert pcr_values and all(a < b for a, b in zip(pcr_values, pcr_values[1:], strict=False))
    decoded = subprocess.run(
        [
            ffmpeg,
            "-v",
            "warning",
            "-xerror",
            "-nostdin",
            "-i",
            str(ts),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=20,
    )
    assert decoded.returncode == 0, decoded.stderr.decode("utf-8", "replace")
    assert b"non-monoton" not in decoded.stderr.lower()
    ffprobe = os.environ.get("SOOP_TEST_FFPROBE") or shutil.which("ffprobe")
    if ffprobe:
        packets = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "packet=pts,dts",
                "-of",
                "json",
                str(ts),
            ],
            capture_output=True,
            timeout=20,
        )
        assert packets.returncode == 0
        values = __import__("json").loads(packets.stdout)["packets"]
        dts = [int(p["dts"]) for p in values]
        pts = [int(p["pts"]) for p in values]
        assert all(a < b for a, b in zip(dts, dts[1:], strict=False))
        assert all(p >= d for p, d in zip(pts, dts, strict=True))
        assert any(a > b for a, b in zip(pts, pts[1:], strict=False))
        timeline = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,start_time",
                "-of",
                "json",
                str(ts),
            ],
            capture_output=True,
            timeout=20,
        )
        assert timeline.returncode == 0
        streams = __import__("json").loads(timeline.stdout)["streams"]
        starts = {stream["codec_type"]: float(stream["start_time"]) for stream in streams}
        assert abs(starts["video"] - starts["audio"]) < 1 / 30


def test_retry_and_rediscovery_counts_are_globally_bounded(simulation):
    calls = []

    def rediscover():
        calls.append(1)
        return [Parent(("1.1.1.1", 10000), 1, 0)] * 8, 9

    with pytest.raises(GridError, match="parent_retry_limit"):
        simulation([(b"", "connect")] * 10, rediscover, count=1)
    assert calls == [1]


def test_streamio_close_cancels_active_failover(monkeypatch):
    from soop_grid.bootstrap import Config
    from soop_grid.stream import SoopGridReader
    from streamlink import Streamlink
    from soop_grid.stream import SoopGridStream
    import time

    opened = threading.Event()
    failures = []

    def runner(config, network, emit, report, stats):
        # Real recovery loop: both normally issued synthetic endpoints fail.
        def connect(_):
            opened.set()
            raise GridError("tcp_connect_failed")

        monkeypatch.setattr(network, "connect", connect)
        emit(b"G" * 188)
        sink = MediaSink(emit)
        try:
            receive_media(network, [Parent(("8.8.8.8", 1), 1, 0)] * 8, 12345, 42, sink, report, 35)
        except Cancelled:
            failures.append("cancelled")
        finally:
            sink.close()

    stream = SoopGridStream(Streamlink(), Config("fixture_channel", "12345"))
    reader = SoopGridReader(stream, runner=runner).open()
    assert opened.wait(1)
    start = time.monotonic()
    reader.close()
    reader.close()
    assert time.monotonic() - start < 1
    assert not reader.thread.is_alive() and not reader.network._sockets
    assert failures == ["cancelled"]


def test_first_cache_request_is_initial_only_once_per_connection(monkeypatch, simulation):
    sink, _, _, net = simulation([(controls() + parent_packet(0xC747) + media(1), "deadline")])
    requests = [
        struct.unpack_from("<IQI", w, 16) for w in net.writes if struct.unpack_from("<H", w, 2)[0] == 0xCB30
    ]
    assert [r[2] for r in requests] == [0xFFFFFFFF, 6]
    assert sink.stats["video_frames"] == 1


def test_control_traffic_cannot_hide_media_stall(monkeypatch):
    import soop_grid.parent as parent
    from soop_grid.protocol import Frame

    clock = [0]
    monkeypatch.setattr(parent.time, "monotonic", lambda: clock[0])
    reads = [0]

    def controls_only(*args):
        reads[0] += 1
        clock[0] += 1
        return Frame(0xC746, b"")

    monkeypatch.setattr(parent, "read_frame", controls_only)
    net = SyntheticNetwork([(b"", "eof")], clock)
    with pytest.raises(GridError, match="parent_media_stalled"):
        receive_media(
            net, [Parent(("8.8.8.8", 1), 1, 0)], 12345, 42, MediaSink(lambda _: None), lambda _: None
        )
    assert reads == [7] and all(s.closed for s in net.sockets)


def test_live_resumed_requires_server_push_transition_and_new_media(simulation):
    _, _, events, _ = simulation(
        [
            (controls() + media(1), "eof"),
            (controls() + media(1) + media(2) + parent_packet(0xC742) + media(3), "deadline"),
        ]
    )
    assert events.count("grid_live_resumed") == 1
    assert events.index("parent_failover_succeeded") < events.index("grid_live_resumed")


def test_cache_only_recovery_does_not_claim_live_push_resumed(simulation):
    _, _, events, _ = simulation(
        [(controls() + media(1), "eof"), (controls() + media(1) + media(2), "deadline")]
    )
    assert "parent_failover_succeeded" in events
    assert "grid_live_resumed" not in events
