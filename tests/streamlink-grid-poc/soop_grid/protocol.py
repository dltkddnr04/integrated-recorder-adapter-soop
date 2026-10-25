# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Bounded framing for three distinct TCP protocols, ported from the Go PoC."""

import ipaddress
import socket
import struct
import threading
import time
from dataclasses import dataclass, field


class GridError(Exception):
    """Only fixed, non-secret stage codes may be exposed to the caller."""


class Cancelled(GridError):
    pass


def endpoint(host, port):
    try:
        ip = ipaddress.ip_address(host)
        port = int(port)
        if not ip.is_global or ip.is_multicast or not 0 < port < 65536:
            raise ValueError
    except (ValueError, TypeError):
        raise GridError("invalid_server_endpoint") from None
    return str(ip), port


@dataclass(repr=False)
class Frame:
    command: int
    body: bytes = field(repr=False)
    result: int = 0
    wire: bytes = field(default=b"", repr=False)


def gateway_packet(command, body=b""):
    if len(body) > 1 << 20:
        raise GridError("gateway_body_limit")
    return struct.pack("<IIII", command, 0, len(body), command ^ len(body)) + body


def center_packet(command, body=b""):
    if len(body) > 1 << 20:
        raise GridError("center_body_limit")
    return struct.pack("<IIIII", command, 0, len(body), 0, command ^ len(body)) + body


def parent_packet(command, body=b"", unknown=0):
    if len(body) > 4 << 20:
        raise GridError("parent_body_limit")
    return struct.pack("<HHIII", 0x202, command, len(body), 0x202 ^ command ^ len(body), unknown) + body


def read_exact(sock, size, deadline):
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GridError("network_read_timeout")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(size - len(data))
        except socket.timeout:
            raise GridError("network_read_timeout") from None
        except OSError:
            raise GridError("network_read_failed") from None
        if not chunk:
            raise GridError("peer_closed_or_truncated")
        data.extend(chunk)
    return bytes(data)


def read_frame(sock, family, timeout=4):
    deadline = time.monotonic() + timeout
    header = read_exact(sock, 20 if family == "center" else 16, deadline)
    if family == "parent":
        version, command, size, check, _ = struct.unpack("<HHIII", header)
        if version != 0x202 or check != version ^ command ^ size:
            raise GridError("parent_header_invalid")
        result, limit = 0, 4 << 20
    else:
        command, result, size = struct.unpack_from("<IiI", header)
        check = struct.unpack_from("<I", header, len(header) - 4)[0]
        if check != command ^ size:
            if family == "gateway" and result == -1 and check == ((command ^ size) ^ 0xFFFFFFFF):
                raise GridError("gateway_negative_reply")
            raise GridError("control_checksum_invalid")
        limit = (64 << 10) if family == "gateway" else (1 << 20)
    if size > limit:
        raise GridError("incoming_body_limit")
    body = read_exact(sock, size, deadline)
    return Frame(command, body, result, header + body)


class Network:
    """Tracks active sockets so StreamIO.close interrupts blocked network work."""

    def __init__(self):
        self.stop = threading.Event()
        self.deadline = None
        self._lock = threading.Lock()
        self._sockets = set()

    def remaining(self, maximum):
        if self.stop.is_set():
            raise Cancelled("receiver_closed")
        if self.deadline is None:
            return maximum
        remaining = min(maximum, self.deadline - time.monotonic())
        if remaining <= 0:
            raise GridError("capture_deadline_reached")
        return remaining

    def connect(self, address):
        address = endpoint(*address)
        timeout = self.remaining(3)
        with self._lock:
            if self.stop.is_set():
                raise Cancelled("receiver_closed")
            sock = socket.socket(socket.AF_INET6 if ":" in address[0] else socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            self._sockets.add(sock)
        try:
            sock.connect(address)
        except OSError:
            self.release(sock)
            raise GridError("tcp_connect_failed") from None
        if self.stop.is_set():
            self.release(sock)
            raise Cancelled("receiver_closed")
        return sock

    def send(self, sock, data):
        if self.stop.is_set():
            raise Cancelled("receiver_closed")
        try:
            sock.settimeout(self.remaining(2))
            sock.sendall(data)
        except OSError:
            raise GridError("network_write_failed") from None

    def release(self, sock):
        with self._lock:
            self._sockets.discard(sock)
        sock.close()

    def close(self):
        self.stop.set()
        with self._lock:
            sockets = list(self._sockets)
            self._sockets.clear()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
