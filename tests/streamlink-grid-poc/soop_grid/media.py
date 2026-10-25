# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Grid chunk boundaries, codec metadata, and optional private raw preservation."""

import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .mpegts import TSMux, nals
from .protocol import GridError


@dataclass(repr=False)
class Chunk:
    frame: int
    kind: str
    timestamp: int
    payload: bytes = field(repr=False)


def parse_media(body):
    if len(body) < 69:
        raise GridError("media_header_short")
    header = struct.unpack_from("<H", body, 2)[0]
    extension, size = struct.unpack_from("<II", body, 4)
    if not 69 <= header <= 4096 or extension or size < 1 or header + size != len(body):
        raise GridError("media_layout_unconfirmed")
    kind = chr(body[40])
    payload = body[header:]
    if kind in ("I", "P", "B") and (len(payload) < 4 or payload[:2] != b"\0\0"):
        raise GridError("video_prefix_unconfirmed")
    if kind == "A" and (len(payload) < 7 or payload[0] != 255 or payload[1] & 240 != 240):
        raise GridError("audio_prefix_unconfirmed")
    return Chunk(struct.unpack_from("<Q", body, 12)[0], kind, struct.unpack_from("<Q", body, 41)[0], payload)


class Bits:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def get(self, n):
        if not 0 <= n <= 32 or self.offset + n > len(self.data) * 8:
            raise GridError("sps_truncated")
        value = 0
        for _ in range(n):
            value = (value << 1) | ((self.data[self.offset // 8] >> (7 - self.offset % 8)) & 1)
            self.offset += 1
        return value

    def ue(self):
        zeros = 0
        while self.get(1) == 0:
            zeros += 1
            if zeros > 30:
                raise GridError("sps_golomb_limit")
        return (1 << zeros) - 1 + self.get(zeros)

    def se(self):
        n = self.ue()
        return (n + 1) // 2 if n & 1 else -n // 2


def sps_info(payload):
    nal = next((n for n in nals(payload) if n[0] & 31 == 7), None)
    if nal is None:
        return None
    rbsp = bytearray()
    zeros = 0
    for b in nal[1:]:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        rbsp.append(b)
        zeros = zeros + 1 if b == 0 else 0
    r = Bits(rbsp)
    profile = r.get(8)
    r.get(16)
    r.ue()
    chroma, separate = 1, 0
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma > 3:
            raise GridError("sps_chroma")
        if chroma == 3:
            separate = r.get(1)
        r.ue()
        r.ue()
        r.get(1)
        if r.get(1):
            for i in range(12 if chroma == 3 else 8):
                if r.get(1):
                    last = next_value = 8
                    for _ in range(16 if i < 6 else 64):
                        if next_value:
                            next_value = (last + r.se() + 256) % 256
                        if next_value:
                            last = next_value
    r.ue()
    poc = r.ue()
    if poc == 0:
        r.ue()
    elif poc == 1:
        r.get(1)
        r.se()
        r.se()
        count = r.ue()
        if count > 256:
            raise GridError("sps_cycle_limit")
        for _ in range(count):
            r.se()
    elif poc > 2:
        raise GridError("sps_poc_invalid")
    r.ue()
    r.get(1)
    w, h = r.ue() + 1, r.ue() + 1
    only = r.get(1)
    if not only:
        r.get(1)
    r.get(1)
    left = right = top = bottom = 0
    if r.get(1):
        left, right, top, bottom = (r.ue() for _ in range(4))
    subw, subh = (1, 1) if separate or chroma in (0, 3) else ((2, 2) if chroma == 1 else (2, 1))
    width = w * 16 - subw * (left + right)
    height = h * 16 * (2 - only) - subh * (2 - only) * (top + bottom)
    if not 0 < width <= 16384 or not 0 < height <= 16384:
        raise GridError("sps_dimensions_invalid")
    info = {"codec": "h264", "width": width, "height": height}
    if r.get(1):
        if r.get(1) and r.get(8) == 255:
            r.get(16)
            r.get(16)
        if r.get(1):
            r.get(1)
        if r.get(1):
            r.get(3)
            r.get(1)
            if r.get(1):
                r.get(24)
        if r.get(1):
            r.ue()
            r.ue()
        if r.get(1):
            units, scale = r.get(32), r.get(32)
            r.get(1)
            if 0 < units < 1 << 30:
                info.update(fps_numerator=scale, fps_denominator=2 * units)
    return info


def adts_info(payload):
    if len(payload) < 7 or payload[0] != 255 or payload[1] & 0xF6 != 0xF0:
        raise GridError("adts_header_invalid")
    index = (payload[2] >> 2) & 15
    rates = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)
    if index >= len(rates):
        raise GridError("adts_sample_rate_invalid")
    return {
        "codec": "aac",
        "sample_rate": rates[index],
        "channels": ((payload[2] & 1) << 2) | (payload[3] >> 6),
    }


class MediaSink:
    def __init__(self, emit, capture_dir=None):
        self.emit = emit
        self.mux = TSMux()
        self.files = {}
        self.path = Path(capture_dir) if capture_dir else None
        self.owns_directory = False
        self.closed = False
        self.stats = {
            "media_packets": 0,
            "payload_bytes": 0,
            "wire_bytes": 0,
            "video_frames": 0,
            "audio_frames": 0,
            "sequence_discontinuities": 0,
        }
        self.payload_hash = hashlib.sha256()
        self.last_frame = None
        try:
            if self.path:
                self.path.mkdir(mode=0o700, parents=False, exist_ok=False)
                self.owns_directory = True
                for name in (
                    "grid-wire.bin",
                    "grid-payload.bin",
                    "video.h264",
                    "audio.aac",
                    "recording.ts",
                    "frames.jsonl",
                ):
                    fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    self.files[name] = os.fdopen(fd, "wb")
        except OSError:
            self.close()
            raise GridError("private_capture_create_failed") from None

    def write(self, name, data):
        if name in self.files:
            self.files[name].write(data)

    def add(self, frame):
        chunk = parse_media(frame.body)
        self.write("grid-wire.bin", frame.wire)
        self.write("grid-payload.bin", chunk.payload)
        self.payload_hash.update(chunk.payload)
        self.stats["media_packets"] += 1
        self.stats["payload_bytes"] += len(chunk.payload)
        self.stats["wire_bytes"] += len(frame.wire)
        if self.last_frame is not None and chunk.frame != self.last_frame + 1:
            self.stats["sequence_discontinuities"] += 1
        self.last_frame = chunk.frame
        if chunk.kind in ("I", "P", "B"):
            self.write("video.h264", chunk.payload)
            self.stats["video_frames"] += 1
            if "video" not in self.stats:
                info = sps_info(chunk.payload)
                if info:
                    self.stats["video"] = info
        elif chunk.kind == "A":
            self.write("audio.aac", chunk.payload)
            self.stats["audio_frames"] += 1
            if "audio" not in self.stats:
                self.stats["audio"] = adts_info(chunk.payload)
        ts = self.mux.add(chunk.kind, chunk.timestamp, chunk.payload)
        if ts:
            self.write("recording.ts", ts)
            self.emit(ts)
        self.write(
            "frames.jsonl",
            (
                json.dumps(
                    {
                        "frame": chunk.frame,
                        "type": chunk.kind,
                        "timestamp_raw": chunk.timestamp,
                        "payload_bytes": len(chunk.payload),
                    }
                )
                + "\n"
            ).encode(),
        )
        return chunk

    def close(self):
        if self.closed:
            return
        self.closed = True
        failed = False
        for f in self.files.values():
            try:
                f.close()
            except OSError:
                failed = True
        self.files.clear()
        self.stats["payload_sha256"] = self.payload_hash.hexdigest()
        self.stats["playable_video_frames"] = self.mux.video_frames
        if self.mux.first_pts is not None:
            self.stats["media_timeline_seconds"] = (self.mux.last_pts - self.mux.first_pts) / 90000
        if self.owns_directory:
            # Fixed facts only: no session, endpoint, account, or target values.
            path = self.path / "grid-analysis.json"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(self.stats, f, indent=2)
            except FileExistsError:
                pass
            except OSError:
                failed = True
        if failed:
            raise GridError("capture_close_failed")
