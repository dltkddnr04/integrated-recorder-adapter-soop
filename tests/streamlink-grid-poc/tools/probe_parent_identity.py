#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""At most two server-issued Parent connections; hashes raw chunks, never writes media."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import struct
import threading
import time

from soop_grid.bootstrap import Config, bootstrap
from soop_grid.center import discover_parent
from soop_grid.diagnostics import safe_error
from soop_grid.gateway import obtain_certificate
from soop_grid.media import parse_media
from soop_grid.protocol import Network, parent_packet, read_frame
from tools.field_validate import atomic_json, public_target


def compare(a, b):
    common = sorted(a.keys() & b.keys())
    match = conflict = incomparable = 0
    kind_conflicts = timestamp_conflicts = length_conflicts = hash_conflicts = 0
    delays = []
    audio_matches = video_matches = 0
    streak = longest = 0
    previous = None
    for number in common:
        left, right = a[number], b[number]
        # Both maps are collected only after equal BNO and Quality were checked.
        # A same-number frame with any differing identity/content field is a conflict,
        # not an incomparable sample. Incomparability is reserved for mismatched
        # session/quality metadata, which this probe rejects before comparison.
        if left[:4] != right[:4]:
            conflict += 1
            kind_conflicts += left[0] != right[0]
            timestamp_conflicts += left[1] != right[1]
            length_conflicts += left[2] != right[2]
            hash_conflicts += left[3] != right[3]
            streak = 0
        else:
            match += 1
            audio_matches += left[0] == "A"
            video_matches += left[0] != "A"
            delays.append(abs(left[4] - right[4]) * 1000)
            streak = streak + 1 if previous is not None and number == previous + 1 else 1
            longest = max(longest, streak)
        previous = number
    missing_a = missing_b = None
    if a and b:
        low, high = max(min(a), min(b)), min(max(a), max(b))
        missing_a = sum(low <= x <= high for x in b.keys() - a.keys()) if low <= high else None
        missing_b = sum(low <= x <= high for x in a.keys() - b.keys()) if low <= high else None
    ordered = sorted(delays)
    return {
        "common_frame_numbers": len(common),
        "matched_frames": match,
        "conflicting_frames": conflict,
        "incomparable_frames": incomparable,
        "kind_conflicts": kind_conflicts,
        "timestamp_conflicts": timestamp_conflicts,
        "payload_length_conflicts": length_conflicts,
        "payload_hash_conflicts": hash_conflicts,
        "missing_on_a_within_shared_number_span": missing_a,
        "missing_on_b_within_shared_number_span": missing_b,
        "longest_consecutive_matches": longest,
        "audio_matches": audio_matches,
        "video_matches": video_matches,
        "absolute_arrival_delay_ms_median": statistics.median(delays) if delays else None,
        "absolute_arrival_delay_ms_p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        if delays
        else None,
        "absolute_arrival_delay_ms_max": max(delays) if delays else None,
    }


def probe(config, seconds=20, close_first_after=None):
    if not 1 <= seconds <= 30:
        raise ValueError("probe duration must be 1..30 seconds")
    if close_first_after is not None and not 0 < close_first_after < seconds:
        raise ValueError("client close must be within the probe interval")
    stages = []
    session = bootstrap(config, stages.append)
    network = Network()
    credentials = None
    try:
        credentials = obtain_certificate(network, session, stages.append)
        parents, node_key = discover_parent(network, session.center, credentials, stages.append)
        unique = []
        for parent in parents:
            if not any(parent.address == p.address and parent.quality == p.quality for p in unique):
                unique.append(parent)
        first = unique[0]
        second = next((p for p in unique if p.address != first.address and p.quality == first.quality), first)
        selected = [first, second]
        started = time.monotonic()
        deadline = started + seconds
        network.deadline = deadline
        results = [
            dict(connected=False, media_packets=0, payload_bytes=0, wire_bytes=0, sent_wire_bytes=0,
                 first_media_seconds=None, last_media_seconds=None, sequence_gaps=0, error=None, frames={})
            for _ in range(2)
        ]
        sockets = [None, None]
        intentional_close = threading.Event()
        closed_at = [None]

        def worker(index):
            parent = selected[index]
            result = results[index]
            sock = None
            cache_start = parent.frame + 1
            cache_requests = 0
            live = False
            previous = None
            try:
                sock = network.connect(parent.address)
                sockets[index] = sock
                result["connected"] = True

                def send(command, body=b""):
                    wire = parent_packet(command, body)
                    network.send(sock, wire)
                    result["sent_wire_bytes"] += len(wire)

                send(0xCB2E, struct.pack("<III", session.bno, node_key, parent.quality))
                while not network.stop.is_set() and time.monotonic() < deadline:
                    frame = read_frame(sock, "parent", min(3, max(0.001, deadline - time.monotonic())))
                    if frame.result < 0:
                        result["error"] = "parent_access_denied"
                        network.stop.set()
                        break
                    result["wire_bytes"] += len(frame.wire)
                    if frame.command in (0xC746, 0x1BC7):
                        send(0xCB2F, struct.pack("<I", parent.quality))
                    elif frame.command == 0xC747:
                        cache_requests += 1
                        if cache_requests > 45:
                            result["error"] = "cache_request_limit"
                            network.stop.set()
                            break
                        send(
                            0xCB30,
                            struct.pack(
                                "<IQI", parent.quality, cache_start, 0xFFFFFFFF if cache_requests == 1 else 6
                            ),
                        )
                    elif frame.command == 0xC742 and not live:
                        send(0xCB2B)
                        live = True
                    elif frame.command == 0xC748:
                        chunk = parse_media(frame.body)
                        result["media_packets"] += 1
                        if index == 1 and closed_at[0] is not None:
                            result["packets_after_other_client_close"] = (
                                result.get("packets_after_other_client_close", 0) + 1
                            )
                        result["payload_bytes"] += len(chunk.payload)
                        result["first_media_seconds"] = (
                            result["first_media_seconds"]
                            if result["first_media_seconds"] is not None
                            else round(time.monotonic() - started, 6)
                        )
                        result["last_media_seconds"] = round(time.monotonic() - started, 6)
                        if result["payload_bytes"] > 64 << 20:
                            result["error"] = "probe_byte_budget"
                            network.stop.set()
                            break
                        if previous is not None and chunk.frame != previous + 1:
                            result["sequence_gaps"] += 1
                        previous = chunk.frame
                        cache_start = chunk.frame + 1
                        cache_requests = 0
                        result["frames"][chunk.frame] = (
                            chunk.kind,
                            chunk.timestamp,
                            len(chunk.payload),
                            hashlib.sha256(chunk.payload).hexdigest(),
                            time.monotonic() - started,
                        )
                        if len(result["frames"]) > 4096:
                            del result["frames"][next(iter(result["frames"]))]
            except Exception as exc:
                if index == 0 and intentional_close.is_set():
                    result["intentional_client_close"] = True
                elif time.monotonic() < deadline and not network.stop.is_set():
                    result["error"] = safe_error(exc)
                    network.stop.set()  # A rejection/failure ends BOTH connections immediately.
            finally:
                if sock is not None:
                    network.release(sock)

        threads = [threading.Thread(target=worker, args=(i,), name=f"parent-probe-{i}") for i in range(2)]
        for thread in threads:
            thread.start()
        while time.monotonic() < deadline and not network.stop.wait(0.05):
            if (
                close_first_after is not None
                and closed_at[0] is None
                and time.monotonic() - started >= close_first_after
            ):
                overlap = compare(results[0]["frames"].copy(), results[1]["frames"].copy())
                if (
                    overlap["matched_frames"] >= 200
                    and not overlap["conflicting_frames"]
                    and not overlap["incomparable_frames"]
                    and sockets[0] is not None
                ):
                    intentional_close.set()
                    closed_at[0] = time.monotonic() - started
                    # Close this client's socket only; never disturb the server or other peer.
                    try:
                        sockets[0].shutdown(2)
                    except OSError:
                        pass
                    network.release(sockets[0])
            if not any(t.is_alive() for t in threads):
                break
        network.close()
        for thread in threads:
            thread.join(5)
        comparison = compare(results[0]["frames"], results[1]["frames"])
        summary = {
            "server_parent_entries": len(parents),
            "unique_endpoint_quality_pairs": len(unique),
            "distinct_endpoints": len({p.address for p in unique}),
            "distinct_same_quality_endpoints": len({p.address for p in unique if p.quality == first.quality}),
            "same_endpoint_two_connections": first.address == second.address,
            "connection_topology": "SAME_ENDPOINT_TWO_CONNECTIONS" if first.address == second.address else "DISTINCT_PARENT_ENDPOINTS",
            "center_session_shared": True,
            "node_key_generation_shared": True,
            "quality_identical": first.quality == second.quality,
            "intentional_client_close_at_seconds": closed_at[0],
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "connections": [{k: v for k, v in r.items() if k != "frames"} for r in results],
            "threads_stopped": all(not t.is_alive() for t in threads),
            **comparison,
        }
        return summary, [{str(n): list(identity) for n, identity in r["frames"].items()} for r in results]
    finally:
        network.close()
        if credentials:
            credentials.clear()
        session.clear()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--close-first-after", type=float)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--url")
    target.add_argument("--discover-public", action="store_true")
    args = p.parse_args()
    url = public_target() if args.discover_public else args.url
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "play.sooplive.com":
        p.error("only official SOOP URLs are allowed")
    path = parts.path.strip("/").split("/")
    if len(path) != 2:
        p.error("exact channel/BNO required")
    args.directory.mkdir(mode=0o700, exist_ok=False)
    result, identities = probe(Config(path[0], path[1]), args.seconds, args.close_first_after)
    atomic_json(args.directory / "summary.json", result)
    atomic_json(args.directory / "frame-identities-private.json", identities)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
