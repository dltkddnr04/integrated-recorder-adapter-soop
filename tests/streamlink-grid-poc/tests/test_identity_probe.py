# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import hashlib
import socket
import threading
from types import SimpleNamespace

import pytest

from conftest import media_body, synthetic_sps
from soop_grid.bootstrap import Config
from soop_grid.media import MediaSink, layout_facts, parse_media
from soop_grid.parent import Parent
from soop_grid.protocol import Frame, GridError, Network, parent_packet, read_frame
from tools.probe_parent_identity import compare
import tools.probe_parent_identity as probe_module
from tools.cache_request_experiment import run_trial
import tools.cache_request_experiment as cache_probe


def identity(number, arrival=0):
    return (
        "A" if number % 5 == 0 else "I",
        number * 100,
        8,
        hashlib.sha256(str(number).encode()).hexdigest(),
        arrival,
    )


def test_frame_comparison_distinguishes_payload_conflicts_and_identity_mismatch():
    a = {i: identity(i) for i in range(1, 206)}
    b = {i: identity(i, 0.01) for i in range(1, 206)}
    r = compare(a, b)
    assert r["matched_frames"] == r["longest_consecutive_matches"] == 205
    assert r["audio_matches"] == 41 and r["video_matches"] == 164
    assert r["absolute_arrival_delay_ms_median"] == 10
    b[10] = (*b[10][:3], "different-payload", 0.01)
    b[11] = ("P", *b[11][1:])
    del b[12]
    r = compare(a, b)
    assert r["conflicting_frames"] == 2 and r["incomparable_frames"] == 0
    assert r["payload_hash_conflicts"] == 1 and r["kind_conflicts"] == 1
    assert r["missing_on_a_within_shared_number_span"] == 0
    assert r["missing_on_b_within_shared_number_span"] == 1
    assert compare({1: identity(1)}, {1000: identity(1000)})["missing_on_a_within_shared_number_span"] is None


def test_rejected_layout_has_numeric_facts_and_opt_in_private_wire(tmp_path):
    body = bytearray(media_body(synthetic_sps()))
    body[4:8] = (1).to_bytes(4, "little")
    with pytest.raises(GridError, match="media_layout_unconfirmed"):
        parse_media(body)
    facts = layout_facts(body)
    assert facts["rejected_extension_bytes"] == 1
    assert all(type(x) is int for x in facts.values())
    sink = MediaSink(lambda b: None, tmp_path / "private")
    wire = parent_packet(0xC748, body)
    sink.reject(Frame(0xC748, bytes(body), wire=wire))
    sink.close()
    assert (tmp_path / "private" / "rejected-grid-wire.bin").read_bytes() == wire


@pytest.mark.parametrize("extension_size", [0, 1, 50, 4096])
def test_opaque_media_extension_precedes_unchanged_codec_payload(extension_size):
    payload = synthetic_sps()
    normal = media_body(payload)
    body = bytearray(normal[:69] + b"x" * extension_size + payload)
    body[4:8] = extension_size.to_bytes(4, "little")
    parsed = parse_media(body)
    assert parsed.payload == payload and parsed.extension_bytes == extension_size
    assert parsed.frame == 1 and parsed.kind == "I"
    with pytest.raises(GridError):
        parse_media(body + b"trailing")
    with pytest.raises(GridError):
        parse_media(body[:-1])


def test_extension_bound_is_not_relaxed_for_valid_payload_prefix():
    payload = synthetic_sps()
    body = bytearray(media_body(payload)[:69] + b"x" * 4097 + payload)
    body[4:8] = (4097).to_bytes(4, "little")
    with pytest.raises(GridError, match="media_layout_unconfirmed"):
        parse_media(body)


@pytest.mark.parametrize("same_endpoint,close_after", [(False, None), (True, None), (True, 0.2)])
def test_two_loopback_connections_compare_real_framed_chunks(monkeypatch, same_endpoint, close_after):
    done = threading.Event()
    listeners = []
    sockets = []
    workers = []
    count = 1 if same_endpoint else 2
    for _ in range(count):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(3)
        listeners.append(listener)

    def serve_connection(sock):
        try:
            assert read_frame(sock, "parent").command == 0xCB2E
            sock.sendall(parent_packet(0xC746) + parent_packet(0xC747))
            assert read_frame(sock, "parent").command == 0xCB2F
            assert read_frame(sock, "parent").command == 0xCB30
            wire = b"".join(
                parent_packet(0xC748, media_body(synthetic_sps(), frame=i, timestamp=50_000_000 + i * 166667))
                for i in range(1, 206)
            )
            sock.sendall(wire)
            if close_after:
                done.wait(0.4)
                more = b"".join(
                    parent_packet(
                        0xC748, media_body(synthetic_sps(), frame=i, timestamp=50_000_000 + i * 166667)
                    )
                    for i in range(206, 411)
                )
                try:
                    sock.sendall(more)
                except OSError:
                    pass  # This connection was intentionally closed by the synthetic client.
            done.wait(3)
        finally:
            sock.close()

    def accept(listener, clients):
        for _ in range(clients):
            sock, _ = listener.accept()
            sockets.append(sock)
            t = threading.Thread(target=serve_connection, args=(sock,))
            workers.append(t)
            t.start()

    acceptors = [threading.Thread(target=accept, args=(s, 2 if same_endpoint else 1)) for s in listeners]
    for t in acceptors:
        t.start()

    class LocalNetwork(Network):
        def connect(self, address):
            sock = socket.create_connection(address, timeout=2)
            with self._lock:
                self._sockets.add(sock)
            return sock

    parents = [Parent(s.getsockname(), 1, 0) for s in listeners]
    monkeypatch.setattr(probe_module, "Network", LocalNetwork)
    monkeypatch.setattr(
        probe_module, "bootstrap", lambda *a: SimpleNamespace(bno=12345, center=None, clear=lambda: None)
    )
    monkeypatch.setattr(probe_module, "obtain_certificate", lambda *a: SimpleNamespace(clear=lambda: None))
    monkeypatch.setattr(probe_module, "discover_parent", lambda *a: (parents, 42))
    try:
        result, frames = probe_module.probe(Config("fixture_channel", "12345"), 1, close_after)
        assert result["threads_stopped"]
        assert result["same_endpoint_two_connections"] == same_endpoint
        assert result["center_session_shared"] and result["node_key_generation_shared"]
        assert result["matched_frames"] == 205
        assert result["conflicting_frames"] == result["incomparable_frames"] == 0
        assert all(c["connected"] for c in result["connections"])
        assert result["connections"][0]["media_packets"] == 205
        assert result["connections"][1]["media_packets"] == (410 if close_after else 205)
        if close_after:
            assert result["connections"][1]["packets_after_other_client_close"] == 205
        assert len(frames) == 2
    finally:
        done.set()
        for t in acceptors + workers:
            t.join(4)
        for s in listeners:
            s.close()
    assert all(s.fileno() == -1 for s in sockets)


def test_cache_request_probe_counts_wire_and_never_starts_live_push(monkeypatch):
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    peer = Parent(listener.getsockname(), 7, 100)
    observed = {}

    def serve():
        sock, _ = listener.accept()
        with sock:
            observed["stream"] = read_frame(sock, "parent")
            sock.sendall(parent_packet(0xC746))
            observed["followup"] = read_frame(sock, "parent")
            sock.sendall(parent_packet(0xC747))
            observed["request"] = read_frame(sock, "parent")
            requested = int.from_bytes(observed["request"].body[4:12], "little")
            sock.sendall(b"".join(parent_packet(
                0xC748, media_body(synthetic_sps(), frame=requested+i,
                                   timestamp=50_000_000 + (requested+i)*166667)
            ) for i in range(3)))
            sock.sendall(parent_packet(0xC747))
            observed["request2"] = read_frame(sock, "parent")
            requested2 = int.from_bytes(observed["request2"].body[4:12], "little")
            sock.sendall(b"".join(parent_packet(
                0xC748, media_body(synthetic_sps(), frame=requested2+i,
                                   timestamp=50_000_000 + (requested2+i)*166667)
            ) for i in range(2)))
            time.sleep(0.3)

    worker = threading.Thread(target=serve)
    worker.start()

    class LocalNetwork(Network):
        def connect(self, address):
            sock = socket.create_connection(address, timeout=2)
            with self._lock:
                self._sockets.add(sock)
            return sock

    monkeypatch.setattr(cache_probe, "Network", LocalNetwork)
    session = SimpleNamespace(bno=12345)
    network = LocalNetwork()
    try:
        result, hashes = run_trial(network, session, 42, peer, offset=5, seconds=0.1, repeat_once=True)
    finally:
        network.close()
        worker.join(2)
        listener.close()
    assert observed["stream"].command == 0xCB2E
    assert observed["followup"].command == 0xCB2F
    assert observed["request"].command == 0xCB30
    assert observed["request2"].command == 0xCB30
    assert int.from_bytes(observed["request"].body[4:12], "little") == 106
    assert observed["request"].body[-4:] == b"\xff\xff\xff\xff"
    assert int.from_bytes(observed["request2"].body[4:12], "little") == 109
    assert observed["request2"].body[-4:] == (6).to_bytes(4, "little")
    assert result["media_packets"] == 5
    assert result["first_frame"] == 106 and result["last_frame"] == 110
    assert result["received_application_wire_bytes"] > result["payload_bytes"]
    assert result["sent_application_wire_bytes"] > 0
    assert len(hashes) == 5 and result["live_push_request_sent"] is False
