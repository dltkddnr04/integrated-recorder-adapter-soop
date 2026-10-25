#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Post-capture media checks. Output has numeric facts and codec names, no file paths."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def scan_ts(path):
    counts = {}
    result = dict(
        packets=0,
        sync_errors=0,
        continuity_errors=0,
        pat=0,
        pmt=0,
        pcr_samples=0,
        pcr_regressions=0,
        explicit_discontinuities=0,
    )
    last_pcr = {}
    with Path(path).open("rb") as stream:
        while True:
            packet = stream.read(188)
            if not packet:
                break
            if len(packet) != 188 or packet[0] != 0x47:
                result["sync_errors"] += 1
                break
            result["packets"] += 1
            pid = ((packet[1] & 31) << 8) | packet[2]
            afc = (packet[3] >> 4) & 3
            cc = packet[3] & 15
            if pid == 0:
                result["pat"] += 1
            if pid == 0x1000:
                result["pmt"] += 1
            if afc in (2, 3) and packet[4] and packet[5] & 128:
                result["explicit_discontinuities"] += 1
            if afc in (1, 3):
                if pid in counts and cc != ((counts[pid] + 1) & 15):
                    result["continuity_errors"] += 1
                counts[pid] = cc
            if afc in (2, 3) and packet[4] >= 7 and packet[5] & 16:
                b = packet[6:12]
                pcr = (int.from_bytes(b[:4], "big") << 1) | (b[4] >> 7)
                if pid in last_pcr:
                    delta = (pcr - last_pcr[pid]) % (1 << 33)
                    if delta >= (1 << 32):
                        result["pcr_regressions"] += 1
                last_pcr[pid] = pcr
                result["pcr_samples"] += 1
    return result


def verify(path, ffprobe, ffmpeg):
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,start_time,duration,sample_rate,channels:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = {"ffprobe_exit": probe.returncode, "ts": scan_ts(path)}
    if probe.returncode:
        return result
    result["media"] = json.loads(probe.stdout)
    packets = subprocess.Popen(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_entries",
            "packet=pts,dts",
            "-of",
            "csv=p=0",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    previous_dts = previous_pts = None
    count = backwards_dts = backwards_pts = pts_before_dts = 0
    for line in packets.stdout:
        values = line.strip().split(",")
        try:
            pts, dts = int(values[0]), int(values[1])
        except (ValueError, IndexError):
            continue
        count += 1
        if previous_dts is not None and dts <= previous_dts:
            backwards_dts += 1
        if previous_pts is not None and pts < previous_pts:
            backwards_pts += 1
        if pts < dts:
            pts_before_dts += 1
        previous_dts, previous_pts = dts, pts
    result["packets"] = dict(
        ffprobe_exit=packets.wait(),
        video_packets=count,
        nonmonotonic_dts=backwards_dts,
        pts_reorder_steps=backwards_pts,
        pts_before_dts=pts_before_dts,
    )
    decoded = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "warning",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=1800,
    )
    result["decode"] = {
        "exit_code": decoded.returncode,
        "diagnostic_lines": len(decoded.stderr.splitlines()),
        "dts_diagnostic": b"non-monoton" in decoded.stderr.lower(),
    }
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    result["ts_sha256"] = digest.hexdigest()
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path)
    p.add_argument("--ffprobe", default="ffprobe")
    p.add_argument("--ffmpeg", default="ffmpeg")
    args = p.parse_args()
    print(json.dumps(verify(args.path, args.ffprobe, args.ffmpeg), indent=2))


if __name__ == "__main__":
    main()
