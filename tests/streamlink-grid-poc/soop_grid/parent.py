# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Receive-only Parent control flow matching Go iss/parent.go."""

import json
import struct
import time
from dataclasses import dataclass

from .media import layout_facts, parse_media
from .recovery import Continuity
from .protocol import Cancelled, GridError, endpoint, parent_packet, read_frame


MEDIA_STALL_TIMEOUT = 3.0
FAILOVER_BACKOFF_SECONDS = 0.0
MAX_PARENT_ATTEMPTS_PER_OUTAGE = 8
MAX_CENTER_REDISCOVERIES_PER_OUTAGE = 1


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
        seen = set()
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
            identity = (addr, quality)
            if identity not in seen:
                seen.add(identity)
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
    recovery_started_at = None
    recovery_failure_at = None
    candidate_selection_started = None
    last_media_at = None
    stats.setdefault("parent_wire_bytes", 0)
    stats.setdefault("recovery_wire_bytes", 0)
    stats.setdefault("recovery_media_bytes", 0)
    stats.setdefault("cache_frames_received", 0)
    stats.setdefault("cache_frames_replayed", 0)
    stats.setdefault("recovery_success", 0)
    stats.setdefault("recovery_failures", 0)
    stats.setdefault("catchup_state", "not_started")
    while not network.stop.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            if continuity.recovering:
                continuity.reject("recovery_deadline_exhausted")
            stats["stop_reason"] = "duration_limit"
            report("grid_duration_limit_reached")
            return
        if not queue:
            if (
                rediscover is None
                or rediscoveries_this_outage >= MAX_CENTER_REDISCOVERIES_PER_OUTAGE
                or attempts >= MAX_PARENT_ATTEMPTS_PER_OUTAGE
            ):
                report("parent_failover_exhausted")
                if recovery_started_at is not None:
                    stats["recovery_failures"] += 1
                    stats["recovery_failure_code"] = "recovery_candidates_exhausted"
                    report("recovery_failed")
                if continuity.recovering:
                    continuity.reject("recovery_candidates_exhausted")
                raise GridError(last_error)
            stats["rediscoveries"] += 1
            rediscoveries_this_outage += 1
            rediscovery_start = time.monotonic()
            report("parent_rediscovery_started")
            # This is a NEW Center connection; use its newly issued node key.
            # Expired/denied certificates are terminal, never forged/refreshed implicitly.
            try:
                queue, node_key = rediscover()
            except GridError:
                stats["center_rediscovery_failed"] = 1
                stats["center_rediscovery_ms"] = round((time.monotonic() - rediscovery_start) * 1000, 3)
                stats["recovery_failure_code"] = "center_rediscovery_failed"
                if recovery_started_at is not None:
                    stats["recovery_failures"] += 1
                report("center_rediscovery_failed")
                report("recovery_failed")
                raise
            stats["center_rediscovery_ms"] = round((time.monotonic() - rediscovery_start) * 1000, 3)
            report("center_rediscovery_completed")
            report("new_node_key_obtained")
            queue = list(queue)
            if not queue:
                stats["recovery_failure_code"] = "parent_rediscovery_empty"
                if recovery_started_at is not None:
                    stats["recovery_failures"] += 1
                report("recovery_failed")
                raise GridError("parent_rediscovery_empty")
            candidate_selection_started = time.monotonic()
        if attempts >= MAX_PARENT_ATTEMPTS_PER_OUTAGE:
            report("parent_failover_exhausted")
            stats["recovery_failure_code"] = "parent_retry_limit"
            if recovery_started_at is not None:
                stats["recovery_failures"] += 1
            report("recovery_failed")
            raise GridError("parent_retry_limit")
        parent = queue.pop(0)
        if candidate_selection_started is not None:
            stats["candidate_selection_ms"] = round(
                (time.monotonic() - candidate_selection_started) * 1000, 3
            )
            candidate_selection_started = None
        elif recovery_failure_at is not None:
            stats["candidate_selection_ms"] = round((time.monotonic() - recovery_failure_at) * 1000, 3)
        report("parent_candidate_selected")
        if quality is not None and parent.quality != quality:
            last_error = "parent_quality_changed"
            report("parent_candidate_failed")
            continue
        if attempts or continuity.recovering:
            stats["failover_attempts"] += 1
            report("parent_failover_started")
            # Issued candidates are tried immediately. The zero default removes
            # the former fixed 100 ms delay; a nonzero value remains injectable
            # for deterministic comparative benchmarks only.
            if FAILOVER_BACKOFF_SECONDS > 0:
                wait = FAILOVER_BACKOFF_SECONDS
                if deadline is not None:
                    wait = min(wait, max(0.0, deadline - time.monotonic()))
                if network.stop.wait(wait):
                    raise Cancelled("receiver_closed")
        attempts += 1
        stats["parent_connection_attempts"] = stats.get("parent_connection_attempts", 0) + 1
        sock = None
        try:
            connect_started = time.monotonic()
            report("parent_tcp_connect_started")
            sock = network.connect(parent.address)
            stats["parent_tcp_connect_ms"] = round((time.monotonic() - connect_started) * 1000, 3)
            stats["parent_connections"] += 1
            stats["active_parents"] = 1
            report("parent_connected")
            report("parent_tcp_connected")
            continuity.begin()
            last_frame = continuity.last if continuity.last is not None else parent.frame
            # Recover only if the new cache can prove an exact overlap with our last
            # accepted frame. First-session request remains the Go-proven +1 rule.
            cache_start = last_frame if continuity.recovering else last_frame + 1
            if not 0 <= cache_start < (1 << 64):
                raise GridError("cache_frame_bounds")
            live_started = False
            connection_requests = 0
            last_media = time.monotonic()
            handshake_started = connect_started
            cache_request_at = None
            handshake_completed = False

            def send(cmd, body=b"", connection=sock):
                data = parent_packet(cmd, body)
                network.send(connection, data)
                stats["parent_wire_bytes"] += len(data)
                if continuity.recovering:
                    stats["recovery_wire_bytes"] += len(data)

            send(0xCB2E, struct.pack("<III", bno, node_key, parent.quality))
            while not network.stop.is_set():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    if continuity.recovering:
                        continuity.reject("recovery_deadline_exhausted")
                    stats["stop_reason"] = "duration_limit"
                    report("grid_duration_limit_reached")
                    return
                media_age = max(0.0, time.monotonic() - last_media)
                stall_remaining = MEDIA_STALL_TIMEOUT - media_age
                if stall_remaining <= 0:
                    raise GridError("parent_media_stalled")
                read_timeout = min(stall_remaining, remaining) if remaining is not None else stall_remaining
                try:
                    frame = read_frame(sock, "parent", read_timeout)
                except GridError as read_error:
                    if str(read_error) == "capture_deadline_reached" or (
                        deadline is not None and time.monotonic() >= deadline
                    ):
                        raise GridError("capture_deadline_reached") from None
                    if str(read_error) == "network_read_timeout" and time.monotonic() - last_media >= MEDIA_STALL_TIMEOUT:
                        raise GridError("parent_media_stalled") from None
                    raise
                stats["parent_packets"] = stats.get("parent_packets", 0) + 1
                stats["parent_wire_bytes"] += len(frame.wire)
                if continuity.recovering:
                    stats["recovery_wire_bytes"] += len(frame.wire)
                if frame.command != 0xC748:
                    stats["last_control_command"] = frame.command
                packets_without_media += 1
                if packets_without_media > 8192:
                    raise GridError("parent_packet_limit")
                if frame.result < 0:
                    raise GridError("parent_access_denied")
                if frame.command in (0xC746, 0x1BC7):
                    send(0xCB2F, struct.pack("<I", parent.quality))
                elif frame.command == 0xC747:
                    if not handshake_completed:
                        stats["parent_handshake_ms"] = round((time.monotonic() - handshake_started) * 1000, 3)
                        handshake_completed = True
                        report("parent_handshake_completed")
                    requests_without_media += 1
                    connection_requests += 1
                    stats["cache_request_count"] = stats.get("cache_request_count", 0) + 1
                    stats["last_cache_request_start"] = cache_start
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
                    cache_request_at = time.monotonic()
                    report("cache_request_sent")
                elif frame.command == 0xC742:
                    if not live_started:
                        send(0xCB2B)
                        live_started = True
                        if continuity.recovering:
                            stats["catchup_state"] = "unverified_live_boundary"
                        report("live_push_transition_requested")
                elif frame.command == 0xC748:
                    if cache_request_at is not None and stats.get("cache_first_byte_ms") is None:
                        stats["cache_first_byte_ms"] = round((time.monotonic() - cache_request_at) * 1000, 3)
                        report("cache_first_media_received")
                    if continuity.recovering:
                        stats["cache_frames_received"] += 1
                        stats["recovery_media_bytes"] += len(frame.body)
                    if max_bytes is not None and stats["wire_bytes"] + len(frame.wire) > max_bytes:
                        stats["stop_reason"] = "byte_limit"
                        report("grid_media_byte_limit_reached")
                        return
                    try:
                        chunk = parse_media(frame.body)
                    except GridError:
                        stats.update(layout_facts(frame.body))
                        if hasattr(sink, "reject"):
                            sink.reject(frame)
                        report("media_packet_rejected")
                        raise
                    accepted = continuity.accept(chunk)
                    if continuity.recovering and not accepted and chunk.frame == continuity.last:
                        overlap_started = cache_request_at if cache_request_at is not None else time.monotonic()
                        stats["overlap_verification_ms"] = round(
                            (time.monotonic() - overlap_started) * 1000, 3
                        )
                        report("overlap_frame_verified")
                    sink.add(frame, discard=not accepted) if not accepted else sink.add(frame)
                    last_media_at = time.monotonic()
                    stats["last_media_monotonic"] = last_media_at
                    report("parent_last_media_received")
                    if accepted:
                        was_recovering = continuity.recovering
                        continuity.committed()
                        stats["last_media_monotonic"] = time.monotonic()
                        last_media_at = stats["last_media_monotonic"]
                        packets_without_media = 0
                        requests_without_media = 0
                        attempts = 0
                        rediscoveries_this_outage = 0
                        if was_recovering:
                            stats["cache_frames_replayed"] += 1
                            if stats.get("first_new_frame_ms") is None:
                                recovery_origin = (
                                    recovery_started_at if recovery_started_at is not None else time.monotonic()
                                )
                                elapsed = (time.monotonic() - recovery_origin) * 1000
                                stats["first_new_frame_ms"] = round(elapsed, 3)
                            if recovery_started_at is not None:
                                stats["total_recovery_ms"] = round((time.monotonic() - recovery_started_at) * 1000, 3)
                            stats["recovery_success"] += 1
                            report("first_new_frame_accepted")
                            report("recovery_completed")
                            stats.pop("recovery_failure_code", None)
                            stats.pop("center_rediscovery_failed", None)
                            recovery_started_at = recovery_failure_at = None
                            stats["catchup_state"] = "unverified_live_boundary"
                        quality = parent.quality
                        cache_start = chunk.frame + 1
                    last_media = time.monotonic()
                    if stats["media_packets"] == 1:
                        report("parent_c748_media_received")
                if time.monotonic() - last_media >= MEDIA_STALL_TIMEOUT:
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
                stats["stop_reason"] = "duration_limit"
                report("grid_duration_limit_reached")
                return
            last_error = str(exc)
            if last_error not in TRANSIENT:
                if continuity.recovering:
                    stats["recovery_failures"] += 1
                    stats["recovery_failure_code"] = last_error
                    report("recovery_failed")
                raise
            failure_now = time.monotonic()
            if recovery_started_at is None and continuity.last is not None:
                if last_media_at is not None:
                    stats["failure_detection_ms"] = round(max(0.0, failure_now - last_media_at) * 1000, 3)
                recovery_started_at = failure_now
                stats["recovery_started_monotonic"] = recovery_started_at
                stats.pop("recovery_failure_code", None)
                stats.pop("center_rediscovery_failed", None)
                stats["first_new_frame_ms"] = None
                stats["cache_first_byte_ms"] = None
                stats["overlap_verification_ms"] = None
                stats["catchup_state"] = "not_started"
                report("recovery_started")
            recovery_failure_at = failure_now
            stats["recovery_failure_attempts"] = stats.get("recovery_failure_attempts", 0) + 1
            report("parent_failure_detected")
            report(last_error)
            report("parent_connection_lost" if sock is not None else "parent_candidate_failed")
            continuity.begin()
        finally:
            stats["active_parents"] = 0
            if sock is not None:
                network.release(sock)
