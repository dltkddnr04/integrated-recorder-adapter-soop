# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import socket
import struct

import pytest

from soop_grid.protocol import (
    GridError,
    Network,
    center_packet,
    endpoint,
    gateway_packet,
    parent_packet,
    read_frame,
)


@pytest.mark.parametrize(
    "family,encoder", [("gateway", gateway_packet), ("center", center_packet), ("parent", parent_packet)]
)
def test_framing_fragmentation_and_disconnect(family, encoder):
    left, right = socket.socketpair()
    try:
        wire = encoder(0x123, b"synthetic")
        right.sendall(wire[:7])
        right.sendall(wire[7:])
        f = read_frame(left, family)
        assert f.command == 0x123 and f.body == b"synthetic" and f.wire == wire
        right.sendall(wire[:-1])
        right.close()
        with pytest.raises(GridError, match="truncated"):
            read_frame(left, family)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    "family,header",
    [
        ("parent", struct.pack("<HHIII", 0x202, 1, 0xFFFFFFFF, 0x202 ^ 1 ^ 0xFFFFFFFF, 0)),
        ("gateway", struct.pack("<IIII", 1, 0, 0xFFFFFFFF, 1 ^ 0xFFFFFFFF)),
        ("center", struct.pack("<IIIII", 1, 0, 0xFFFFFFFF, 0, 1 ^ 0xFFFFFFFF)),
        ("parent", struct.pack("<HHIII", 0x202, 1, 0, 0, 0)),
    ],
)
def test_invalid_lengths_and_checksums_rejected_before_allocation(family, header):
    left, right = socket.socketpair()
    try:
        right.sendall(header)
        with pytest.raises(GridError):
            read_frame(left, family, 0.1)
    finally:
        left.close()
        right.close()


def test_negative_gateway_frame():
    left, right = socket.socketpair()
    try:
        right.sendall(struct.pack("<IIII", 0x3E2, 0xFFFFFFFF, 256, (0x3E2 ^ 256) ^ 0xFFFFFFFF))
        with pytest.raises(GridError, match="negative_reply"):
            read_frame(left, "gateway")
    finally:
        left.close()
        right.close()


def test_control_checksum_error_has_only_safe_header_facts():
    left, right = socket.socketpair()
    try:
        command, size = 0x69, 12
        right.sendall(struct.pack("<IIIII", command, 0, size, 0, command ^ size ^ 1))
        with pytest.raises(GridError, match="control_checksum_invalid") as caught:
            read_frame(left, "center")
        assert caught.value.safe_facts == {
            "family": "center",
            "command": command,
            "result": 0,
            "declared_body_bytes": size,
            "checksum_received": command ^ size ^ 1,
            "checksum_expected": command ^ size,
        }
    finally:
        left.close()
        right.close()


def test_read_frame_closed_socket_is_transient_network_error():
    left, right = socket.socketpair()
    right.close()
    left.close()
    with pytest.raises(GridError, match="network_read_failed"):
        read_frame(left, "parent")


@pytest.mark.parametrize(
    "host,port",
    [
        ("127.0.0.1", 1),
        ("192.168.1.2", 1),
        ("224.0.0.1", 1),
        ("not-an-ip", 3),
        ("8.8.8.8", 0),
        ("8.8.8.8", 65536),
    ],
)
def test_only_public_server_endpoints(host, port):
    with pytest.raises(GridError):
        endpoint(host, port)


def test_network_close_shuts_down_tracked_sockets():
    left, right = socket.socketpair()
    n = Network()
    n._sockets.add(left)
    n.close()
    n.close()
    assert left.fileno() == -1
    assert right.recv(1) == b""
    right.close()


def test_parent_published_header_encoding():
    assert parent_packet(0xCB2E, struct.pack("<III", 12345, 42, 1))[:12].hex() == "02022ecb0c00000020c90000"


@pytest.mark.parametrize("stage", ["create", "settimeout", "connect", "connect_close"])
def test_connect_oserror_is_sanitized_and_socket_released(monkeypatch, stage):
    import soop_grid.protocol as protocol

    class Socket:
        closed = False

        def settimeout(self, _):
            if stage == "settimeout":
                raise OSError("synthetic-private-detail")

        def connect(self, _):
            if stage in ("connect", "connect_close"):
                raise OSError("synthetic-private-detail")

        def close(self):
            self.closed = True
            if stage == "connect_close":
                raise OSError("synthetic-private-close-detail")

    sock = Socket()

    def create(*_):
        if stage == "create":
            raise OSError("synthetic-private-detail")
        return sock

    monkeypatch.setattr(protocol.socket, "socket", create)
    network = Network()
    with pytest.raises(GridError, match="^tcp_connect_failed$"):
        network.connect(("8.8.8.8", 1234))
    assert not network._sockets
    assert stage == "create" or sock.closed


def test_close_attempts_all_sockets_despite_close_oserror():
    closed = []

    class Socket:
        def shutdown(self, _):
            raise OSError("synthetic-shutdown")

        def close(self):
            closed.append(self)
            raise OSError("synthetic-close")

    network = Network()
    sockets = {Socket(), Socket()}
    network._sockets.update(sockets)
    network.close()
    network.close()
    assert set(closed) == sockets and not network._sockets


@pytest.mark.parametrize("operation", ["connect", "send"])
def test_transport_cancellation_is_not_a_retryable_failure(monkeypatch, operation):
    import soop_grid.protocol as protocol
    from soop_grid.protocol import Cancelled

    network = Network()

    class Socket:
        def settimeout(self, _):
            pass

        def connect(self, _):
            network.stop.set()
            raise OSError("synthetic-cancel")

        sendall = connect

        def close(self):
            pass

    sock = Socket()
    monkeypatch.setattr(protocol.socket, "socket", lambda *_: sock)
    with pytest.raises(Cancelled, match="receiver_closed"):
        if operation == "connect":
            network.connect(("8.8.8.8", 1234))
        else:
            network.send(sock, b"synthetic")
    assert not network._sockets


@pytest.mark.parametrize(
    "family,encoder", [("gateway", gateway_packet), ("center", center_packet), ("parent", parent_packet)]
)
@pytest.mark.parametrize("timeout", [1.2, 4])
def test_synthetic_partial_reads_share_header_and_body_deadline(monkeypatch, family, encoder, timeout):
    import soop_grid.protocol as protocol

    clock = [0.0]
    monkeypatch.setattr(protocol.time, "monotonic", lambda: clock[0])

    class Socket:
        def __init__(self):
            self.wire = bytearray(encoder(0x123, b"synthetic"))
            self.timeouts = []

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def recv(self, size):
            clock[0] += 0.1
            chunk = self.wire[: min(size, 2)]
            del self.wire[: len(chunk)]
            return bytes(chunk)

    sock = Socket()
    if timeout == 1.2:
        with pytest.raises(GridError, match="network_read_timeout"):
            read_frame(sock, family, timeout=timeout)
        assert clock[0] < 1.4
        assert sock.wire  # A drip-fed body must not get a fresh timeout.
    else:
        frame = read_frame(sock, family, timeout=timeout)
        assert frame.command == 0x123 and frame.body == b"synthetic"
        assert frame.wire == encoder(0x123, b"synthetic")
        assert not sock.wire
    assert all(a > b for a, b in zip(sock.timeouts, sock.timeouts[1:], strict=False))


@pytest.mark.parametrize("failure", [socket.timeout, BrokenPipeError])
def test_partial_sendall_failure_is_sanitized_without_resending(failure):
    class Socket:
        def __init__(self):
            self.prefixes = []

        def settimeout(self, timeout):
            assert 0 < timeout <= 2

        def sendall(self, data):
            self.prefixes.append(data[:3])
            raise failure("synthetic-private-detail")

    sock = Socket()
    with pytest.raises(GridError, match="^network_write_failed$"):
        Network().send(sock, b"synthetic")
    assert sock.prefixes == [b"syn"]
