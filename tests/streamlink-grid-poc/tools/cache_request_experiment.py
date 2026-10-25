#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Bounded, receive-only probes for the observed Parent cache request flow."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

from soop_grid.bootstrap import Config, bootstrap
from soop_grid.center import discover_parent
from soop_grid.gateway import obtain_certificate
from soop_grid.media import parse_media
from soop_grid.protocol import GridError, Network, parent_packet, read_frame
from tools.field_validate import atomic_json, public_target


MAX_TRIAL_SECONDS = 12
MAX_TRIAL_WIRE = 32 << 20


def run_trial(network, session, node_key, parent, offset, seconds, repeat_once=False, absolute_start=None):
    """Issue the normal CB30 request with only its observed 64-bit start varied."""
    started = time.monotonic()
    deadline = started + seconds
    network.deadline = deadline
    sock = network.connect(parent.address)
    trial = {
        "offset_from_center_frame_plus_one": offset,
        "start_frame_source": "center_entry_plus_offset"
        if absolute_start is None
        else "previous_trial_observed_frame_plus_offset",
        "connected": True,
        "requests_sent": [],
        "received_command_counts": {},
        "media_packets": 0,
        "payload_bytes": 0,
        "received_application_wire_bytes": 0,
        "sent_application_wire_bytes": 0,
        "first_frame": None,
        "first_media_after_request_seconds": None,
        "last_frame": None,
        "frames_before_requested_start": 0,
        "frame_gaps": 0,
        "live_push_request_sent": False,
        "stop_code": None,
        "elapsed_seconds": 0,
    }
    hashes = {}
    requested_start = parent.frame + 1 + offset if absolute_start is None else absolute_start
    if requested_start >= 1 << 64:
        network.release(sock)
        raise GridError("cache_frame_bounds")
    last_frame = None
    cache_requests = 0
    setup_complete = False
    request_sent_monotonic = None

    def send(command, body=b""):
        wire = parent_packet(command, body)
        network.send(sock, wire)
        trial["sent_application_wire_bytes"] += len(wire)

    try:
        send(0xCB2E, struct.pack("<III", session.bno, node_key, parent.quality))
        while not network.stop.is_set() and time.monotonic() < deadline:
            frame = read_frame(sock, "parent", min(2.0, max(0.001, deadline - time.monotonic())))
            trial["received_application_wire_bytes"] += len(frame.wire)
            if (
                trial["received_application_wire_bytes"] + trial["sent_application_wire_bytes"]
                > MAX_TRIAL_WIRE
            ):
                trial["stop_code"] = "application_wire_budget"
                break
            if frame.result < 0:
                trial["stop_code"] = "parent_access_denied"
                break
            command = f"0x{frame.command:04x}"
            trial["received_command_counts"][command] = trial["received_command_counts"].get(command, 0) + 1
            if frame.command in (0xC746, 0x1BC7):
                send(0xCB2F, struct.pack("<I", parent.quality))
                setup_complete = True
            elif frame.command == 0xC747:
                if not setup_complete:
                    trial["stop_code"] = "cache_header_before_stream_setup"
                    break
                if cache_requests == 0:
                    start = requested_start
                    connect_status = 0xFFFFFFFF
                elif repeat_once and cache_requests == 1:
                    start = (last_frame + 1) if last_frame is not None else requested_start
                    connect_status = 6  # Existing client behavior; opaque, not reinterpreted.
                else:
                    trial["stop_code"] = "cache_header_after_request_budget"
                    break
                cache_requests += 1
                trial["requests_sent"].append(
                    {
                        "command": "0xCB30",
                        "start_frame": start,
                        "opaque_final_word": connect_status,
                    }
                )
                send(0xCB30, struct.pack("<IQI", parent.quality, start, connect_status))
                request_sent_monotonic = time.monotonic()
            elif frame.command == 0xC742:
                # Deliberately do not send CB2B: this measures the cache path before
                # explicit Live Push activation.
                continue
            elif frame.command == 0xC748:
                chunk = parse_media(frame.body)
                trial["media_packets"] += 1
                trial["payload_bytes"] += len(chunk.payload)
                if trial["payload_bytes"] > MAX_TRIAL_WIRE:
                    trial["stop_code"] = "payload_budget"
                    break
                if trial["first_frame"] is None:
                    trial["first_frame"] = chunk.frame
                    if request_sent_monotonic is not None:
                        trial["first_media_after_request_seconds"] = round(
                            time.monotonic() - request_sent_monotonic, 6
                        )
                if last_frame is not None and chunk.frame != last_frame + 1:
                    trial["frame_gaps"] += 1
                if chunk.frame < requested_start:
                    trial["frames_before_requested_start"] += 1
                trial["last_frame"] = last_frame = chunk.frame
                hashes[str(chunk.frame)] = {
                    "kind": chunk.kind,
                    "raw_timestamp": chunk.timestamp,
                    "payload_length": len(chunk.payload),
                    "payload_sha256": hashlib.sha256(chunk.payload).hexdigest(),
                }
        if trial["stop_code"] is None:
            trial["stop_code"] = "time_budget"
    except GridError as exc:
        trial["stop_code"] = (
            str(exc)
            if str(exc)
            in {
                "parent_access_denied",
                "peer_closed_or_truncated",
                "network_read_failed",
                "network_read_timeout",
                "parent_header_invalid",
                "media_layout_unconfirmed",
                "media_kind_unconfirmed",
                "media_timestamp_bounds",
                "capture_deadline_reached",
            }
            else "probe_protocol_error"
        )
    finally:
        trial["elapsed_seconds"] = round(time.monotonic() - started, 3)
        network.release(sock)
    return trial, hashes


def run(config, offsets=(0, 5), seconds=8, repeat_once=False):
    if (
        not 1 <= seconds <= MAX_TRIAL_SECONDS
        or len(offsets) != 2
        or offsets[0] != 0
        or not 1 <= offsets[1] <= 32
    ):
        raise ValueError("invalid bounded cache experiment parameters")
    stages = []
    session = bootstrap(config, stages.append)
    network = Network()
    credentials = None
    try:
        credentials = obtain_certificate(network, session, stages.append)
        parents, node_key = discover_parent(network, session.center, credentials, stages.append)
        unique = []
        for candidate in parents:
            if not any(
                candidate.address == existing.address and candidate.quality == existing.quality
                for existing in unique
            ):
                unique.append(candidate)
        if not unique:
            raise GridError("no_usable_server_parent")
        parent = unique[0]
        trials, private_hashes = [], []
        # Separate sequential sockets share this one legitimately issued Center
        # session/Node Key; no server endpoint is guessed or scanned.
        for index, offset in enumerate(offsets):
            first_frame = trials[0]["first_frame"] if trials else None
            if index == 1 and first_frame is None:
                break
            start = first_frame + offset if index == 1 else None
            # Keep the baseline short so the second request targets a recently
            # observed frame rather than a stale historical range.
            trial_seconds = min(2, seconds) if index == 0 else seconds
            trial, hashes = run_trial(
                network,
                session,
                node_key,
                parent,
                offset,
                trial_seconds,
                repeat_once if index == 0 else False,
                start,
            )
            trials.append(trial)
            private_hashes.append(hashes)
            if trial["stop_code"] == "parent_access_denied":
                break
        return {
            "server_parent_entries": len(parents),
            "unique_endpoint_quality_pairs": len(unique),
            "trials": trials,
            "cache_only_observation": "no_CB2B_sent",
            "opaque_final_word_interpretation": "unknown",
            "tcp_ip_overhead_included": False,
        }, private_hashes
    finally:
        network.close()
        if credentials:
            credentials.clear()
        session.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=8)
    parser.add_argument("--offset", type=int, default=5)
    parser.add_argument("--repeat-cache-once", action="store_true")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url")
    target.add_argument("--discover-public", action="store_true")
    args = parser.parse_args()
    url = public_target() if args.discover_public else args.url
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "play.sooplive.com":
        parser.error("only official SOOP URLs are allowed")
    path = parts.path.strip("/").split("/")
    if len(path) != 2:
        parser.error("exact channel/BNO required")
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    summary, hashes = run(Config(path[0], path[1]), (0, args.offset), args.seconds, args.repeat_cache_once)
    atomic_json(args.directory / "summary.json", summary)
    atomic_json(args.directory / "frame-payload-hashes-private.json", hashes)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
