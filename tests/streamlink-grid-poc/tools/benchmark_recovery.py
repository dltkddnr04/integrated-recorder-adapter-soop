#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Compare the prior fixed failover pause with immediate issued-candidate reuse."""

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from conftest import media_body, synthetic_sps  # noqa: E402
from soop_grid.media import parse_media  # noqa: E402
from soop_grid.parent import Parent, receive_media  # noqa: E402
from soop_grid.protocol import Frame, GridError, parent_packet  # noqa: E402


def packet(command, body=b""):
    wire = parent_packet(command, body)
    return Frame(command, wire[16:], wire=wire)


def media(number):
    return packet(
        0xC748,
        media_body(synthetic_sps(), "I", number, 50_000_000 + number * 166667),
    )


class Sink:
    def __init__(self):
        self.stats = {"wire_bytes": 0, "media_packets": 0, "video_frames": 0, "audio_frames": 0}
        self.mux = type("Mux", (), {"video_frames": 0})()

    def add(self, frame, discard=False):
        parse_media(frame.body)
        self.stats["wire_bytes"] += len(frame.wire)
        self.stats["media_packets"] += 1
        if not discard:
            self.stats["video_frames"] += 1
            self.mux.video_frames += 1


class Socket:
    def __init__(self, sequence):
        self.sequence = iter(sequence)


class Network:
    def __init__(self):
        import threading

        self.stop = threading.Event()
        self.connections = 0

    def connect(self, _address):
        index = self.connections
        self.connections += 1
        if index == 0:
            return Socket([packet(0xC746), packet(0xC747), media(1), GridError("peer_closed_or_truncated")])
        return Socket([packet(0xC746), packet(0xC747), media(1), media(2)])

    def send(self, _sock, _data):
        pass

    def release(self, _sock):
        pass


def run_one(backoff):
    import soop_grid.parent as parent_module

    net, sink = Network(), Sink()
    previous_reader = parent_module.read_frame
    previous_backoff = parent_module.FAILOVER_BACKOFF_SECONDS

    def read_frame(sock, _family, _timeout=4):
        item = next(sock.sequence)
        if isinstance(item, GridError):
            raise item
        if item.command == 0xC748 and parse_media(item.body).frame == 2:
            net.stop.set()
        return item

    parent_module.read_frame = read_frame
    parent_module.FAILOVER_BACKOFF_SECONDS = backoff
    started = time.monotonic()
    try:
        receive_media(
            net,
            [Parent(("8.8.8.8", 10000), 1, 0), Parent(("1.1.1.1", 10001), 1, 0)],
            1,
            1,
            sink,
            lambda _event: None,
        )
    finally:
        parent_module.read_frame = previous_reader
        parent_module.FAILOVER_BACKOFF_SECONDS = previous_backoff
    return {
        "wall_ms": (time.monotonic() - started) * 1000,
        "total_recovery_ms": sink.stats.get("total_recovery_ms"),
        "frames_written": sink.stats["video_frames"],
        "missing_frames": sink.stats.get("missing_frames", 0),
        "overlap_duplicates_discarded": sink.stats.get("overlap_duplicates_discarded", 0),
        "recovery_success": sink.stats.get("recovery_success", 0),
    }


def summarize(values):
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 3),
        "max_ms": round(max(ordered), 3),
        "samples": len(ordered),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.samples <= 500:
        parser.error("--samples must be between 5 and 500")
    baseline = [run_one(0.1) for _ in range(args.samples)]
    optimized = [run_one(0.0) for _ in range(args.samples)]
    result = {
        "environment": "synthetic in-process Parent responses; no sockets or SOOP requests",
        "baseline_policy": "pre-change fixed 100 ms wait before failover candidate",
        "optimized_policy": "immediate try of already-issued candidate; rediscovery/auth rules unchanged",
        "baseline_total_recovery": summarize([r["total_recovery_ms"] for r in baseline]),
        "optimized_total_recovery": summarize([r["total_recovery_ms"] for r in optimized]),
        "baseline_wall": summarize([r["wall_ms"] for r in baseline]),
        "optimized_wall": summarize([r["wall_ms"] for r in optimized]),
        "baseline_all_recovered_without_gap": all(
            r["recovery_success"] == 1 and r["missing_frames"] == 0 and r["frames_written"] == 2
            for r in baseline
        ),
        "optimized_all_recovered_without_gap": all(
            r["recovery_success"] == 1 and r["missing_frames"] == 0 and r["frames_written"] == 2
            for r in optimized
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
