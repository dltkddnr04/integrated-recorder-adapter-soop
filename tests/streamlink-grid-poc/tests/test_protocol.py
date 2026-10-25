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
