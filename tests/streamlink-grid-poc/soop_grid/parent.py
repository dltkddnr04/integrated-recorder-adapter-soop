# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Receive-only Parent control flow matching Go iss/parent.go."""

import json
import struct
import time
from dataclasses import dataclass

from .media import parse_media
from .recovery import Continuity
from .protocol import Cancelled, GridError, endpoint, parent_packet, read_frame


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


# Only transport failures are candidates for retry. Malformed/auth replies terminate.
TRANSIENT = {
    "tcp_connect_failed",
    "peer_closed_or_truncated",
    "network_read_failed",
    "network_read_timeout",
    "network_write_failed",
    "parent_media_stalled",
}


def receive_media(
    network, parents, bno, node_key, sink, report, duration=None, max_bytes=None, rediscover=None
):
    if duration is not None and (type(duration) not in (int, float) or duration <= 0):
        raise GridError("capture_limits_invalid")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise GridError("capture_limits_invalid")
    # One capture budget, including connect/backoff/rediscovery. Never reset on failover.
    deadline = time.monotonic() + duration if duration is not None else None
    if hasattr(network, "deadline"):
        network.deadline = deadline
    stats = sink.stats if sink is not None else {}
    continuity = Continuity(stats, report)
    queue = list(parents)
    attempts = packets_without_media = requests_without_media = 0
    rediscoveries_this_outage = 0
    quality = None
    last_error = "no_usable_server_parent"
    while not network.stop.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            if continuity.recovering:
                continuity.reject("recovery_deadline_exhausted")
            stats["stop_reason"] = "duration_limit"
            report("grid_duration_limit_reached")
            return
        if not queue:
            if rediscover is None or rediscoveries_this_outage >= 1 or attempts >= 8:
                report("parent_failover_exhausted")
                if continuity.recovering:
                    continuity.reject("recovery_candidates_exhausted")
                raise GridError(last_error)
            stats["rediscoveries"] += 1
            rediscoveries_this_outage += 1
            report("parent_rediscovery_started")
            # This is a NEW Center connection; use its newly issued node key.
            # Expired/denied certificates are terminal, never forged/refreshed implicitly.
            queue, node_key = rediscover()
            queue = list(queue)
            if not queue:
                raise GridError("parent_rediscovery_empty")
        if attempts >= 8:
            report("parent_failover_exhausted")
            raise GridError("parent_retry_limit")
        parent = queue.pop(0)
        if quality is not None and parent.quality != quality:
            last_error = "parent_quality_changed"
            report("parent_candidate_failed")
            continue
        if attempts:
            stats["failover_attempts"] += 1
            report("parent_failover_started")
            backoff = 0.1 if deadline is None else min(0.1, max(0, deadline - time.monotonic()))
            if network.stop.wait(backoff):
                raise Cancelled("receiver_closed")
        attempts += 1
        stats["parent_connection_attempts"] = stats.get("parent_connection_attempts", 0) + 1
        sock = None
        try:
            sock = network.connect(parent.address)
            stats["parent_connections"] += 1
            report("parent_connected")
            continuity.begin()
            resume_live_pending = continuity.recovering
            last_frame = continuity.last if continuity.last is not None else parent.frame
            # Recover only if the new cache can prove an exact overlap with our last
            # accepted frame. First-session request remains the Go-proven +1 rule.
            cache_start = last_frame if continuity.recovering else last_frame + 1
            if not 0 <= cache_start < (1 << 64):
                raise GridError("cache_frame_bounds")
            live_started = False
            connection_requests = 0
            last_media = time.monotonic()

            def send(cmd, body=b"", connection=sock):
                network.send(connection, parent_packet(cmd, body))

            send(0xCB2E, struct.pack("<III", bno, node_key, parent.quality))
            while not network.stop.is_set():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    if continuity.recovering:
                        continuity.reject("recovery_deadline_exhausted")
                    stats["stop_reason"] = "duration_limit"
                    report("grid_duration_limit_reached")
                    return
                frame = read_frame(sock, "parent", 3 if remaining is None else min(3, remaining))
                stats["parent_packets"] = stats.get("parent_packets", 0) + 1
                packets_without_media += 1
                if packets_without_media > 8192:
                    raise GridError("parent_packet_limit")
                if frame.result < 0:
                    raise GridError("parent_access_denied")
                if frame.command in (0xC746, 0x1BC7):
                    send(0xCB2F, struct.pack("<I", parent.quality))
                elif frame.command == 0xC747:
                    requests_without_media += 1
                    connection_requests += 1
                    if requests_without_media > 45:
                        raise GridError("cache_request_limit")
                    if not 0 <= cache_start < (1 << 64):
                        raise GridError("cache_frame_bounds")
                    send(
                        0xCB30,
                        struct.pack(
                            "<IQI", parent.quality, cache_start, 0xFFFFFFFF if connection_requests == 1 else 6
                        ),
                    )
                elif frame.command == 0xC742:
                    if not live_started:
                        send(0xCB2B)
                        live_started = True
                elif frame.command == 0xC748:
                    if max_bytes is not None and stats["wire_bytes"] + len(frame.wire) > max_bytes:
                        stats["stop_reason"] = "byte_limit"
                        report("grid_media_byte_limit_reached")
                        return
                    chunk = parse_media(frame.body)
                    accepted = continuity.accept(chunk)
                    sink.add(frame, discard=not accepted) if not accepted else sink.add(frame)
                    if accepted:
                        continuity.committed()
                        packets_without_media = 0
                        requests_without_media = 0
                        attempts = 0
                        rediscoveries_this_outage = 0
                        if live_started and resume_live_pending:
                            report("grid_live_resumed")
                            resume_live_pending = False
                        quality = parent.quality
                        cache_start = chunk.frame + 1
                    last_media = time.monotonic()
                    if stats["media_packets"] == 1:
                        report("parent_c748_media_received")
                if time.monotonic() - last_media > 6:
                    raise GridError("parent_media_stalled")
        except Cancelled:
            raise
        except GridError as exc:
            if network.stop.is_set():
                raise Cancelled("receiver_closed") from None
            if (
                str(exc) in ("network_read_timeout", "capture_deadline_reached")
                and deadline is not None
                and time.monotonic() >= deadline
            ):
                if continuity.recovering:
                    continuity.reject("recovery_deadline_exhausted")
                return
            last_error = str(exc)
            if last_error not in TRANSIENT:
                raise
            report("parent_connection_lost" if sock is not None else "parent_candidate_failed")
            continuity.begin()
        finally:
            if sock is not None:
                network.release(sock)
