# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Receive-only Parent control flow matching Go iss/parent.go."""

import json
import struct
import time
from dataclasses import dataclass

from .protocol import GridError, endpoint, parent_packet, read_frame


@dataclass(repr=False)
class Parent:
    address: tuple
    quality: int
    frame: int


def parse_parents(body):
    if len(body) < 4:
        raise GridError("parent_list_short")
    size = struct.unpack_from("<I", body)[0]
    if size > len(body) - 4:
        raise GridError("parent_list_length")
    try:
        data = json.loads(body[4 : 4 + size].rstrip(b"\0"))
        items = data["list"]
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            raise ValueError
        out = []
        for item in items:
            ip, port, quality, frame = (
                item.get(k, 0) for k in ("parent_ip", "parent_port", "quality", "cur_frame_no")
            )
            if (
                any(type(v) is not int for v in (ip, port, quality, frame))
                or not 0 <= ip <= 0xFFFFFFFF
                or not 0 <= port <= 65535
            ):
                raise ValueError
            if not 0 <= quality <= 0xFFFFFFFF or not 0 <= frame < 1 << 64:
                raise ValueError
            try:
                addr = endpoint(
                    ".".join(str(b) for b in struct.pack("<I", ip)), ((port & 255) << 8) | ((port >> 8) & 255)
                )
            except GridError:
                continue
            out.append(Parent(addr, quality, frame))
        if not out:
            raise GridError("no_usable_server_parent")
        return out
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise GridError("parent_list_invalid") from None


def receive_media(network, parents, bno, node_key, sink, report, duration=35, max_bytes=512 << 20):
    parent = parents[0]  # Only a normally issued endpoint, no peer scanning.
    sock = network.connect(parent.address)
    try:
        report("parent_connected")

        def send(cmd, body=b""):
            network.send(sock, parent_packet(cmd, body))

        send(0xCB2E, struct.pack("<III", bno, node_key, parent.quality))
        deadline = time.monotonic() + duration
        last_frame = parent.frame
        requests = 0
        live_started = False
        for _ in range(8192):
            if network.stop.is_set() or time.monotonic() >= deadline:
                return
            try:
                frame = read_frame(sock, "parent", min(3, max(0.001, deadline - time.monotonic())))
            except GridError as exc:
                if str(exc) == "network_read_timeout" and time.monotonic() >= deadline:
                    return
                raise
            if frame.command in (0xC746, 0x1BC7):
                send(0xCB2F, struct.pack("<I", parent.quality))
            elif frame.command == 0xC747:
                requests += 1
                if requests > 45 or last_frame == (1 << 64) - 1:
                    raise GridError("cache_request_limit")
                send(
                    0xCB30,
                    struct.pack("<IQI", parent.quality, last_frame + 1, 0xFFFFFFFF if requests == 1 else 6),
                )
            elif frame.command == 0xC742:
                if not live_started:
                    send(0xCB2B)
                    live_started = True
            elif frame.command == 0xC748:
                if sink.stats["wire_bytes"] + len(frame.wire) > max_bytes:
                    return
                chunk = sink.add(frame)
                last_frame = chunk.frame
                if sink.stats["media_packets"] == 1:
                    report("parent_c748_media_received")
        raise GridError("parent_packet_limit")
    finally:
        network.release(sock)
