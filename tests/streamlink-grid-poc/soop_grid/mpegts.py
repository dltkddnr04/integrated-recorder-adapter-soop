# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Deterministic, lossless H.264/ADTS -> MPEG-TS port of Go media/ts.go."""

import struct
from fractions import Fraction

from .aac import parse_adts
from .protocol import GridError

PCR_INTERVAL_90K = 9_000  # 100 ms, expressed in the 90 kHz media clock.


def nals(data, max_units=None):
    import re

    starts = []
    for match in re.finditer(b"\x00\x00(?:\x00)?\x01", data):
        starts.append(match)
        if max_units is not None and len(starts) > max_units:
            raise GridError("h264_nal_count_limit")
    return [
        data[m.end() : starts[i + 1].start() if i + 1 < len(starts) else len(data)]
        for i, m in enumerate(starts)
        if m.end() < len(data)
    ]


def has_nal(data, kind):
    return any(nal[0] & 31 == kind for nal in nals(data))


def crc_mpeg(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
    return crc


def pts_bytes(value, prefix=2):
    v = value & ((1 << 33) - 1)
    return bytes(
        (
            ((prefix & 15) << 4) | (((v >> 30) & 7) << 1) | 1,
            (v >> 22) & 255,
            ((v >> 14) & 254) | 1,
            (v >> 7) & 255,
            ((v << 1) & 255) | 1,
        )
    )


class TSMux:
    def __init__(self):
        self.cc = {}
        self.started = False
        self.base = 0
        self.video_frames = 0
        self.audio_chunks = 0
        self.preroll_video = 0
        self.first_pts = None
        self.last_pts = None
        self.min_pts = None
        self.max_pts = None
        self.last_dts = None
        self.last_pcr_dts = None
        self.last_audio_pts = None
        self.audio_config = None

    def packetize(self, pid, data, start=True, pcr=None):
        data = memoryview(data)
        packets = []
        while data:
            b = bytearray(b"\xff" * 188)
            b[:4] = bytes(
                (0x47, ((pid >> 8) & 31) | (0x40 if start else 0), pid & 255, 0x10 | self.cc.get(pid, 0))
            )
            self.cc[pid] = (self.cc.get(pid, 0) + 1) & 15
            capacity = 176 if pcr is not None else 184
            adapt = 8 if pcr is not None else 0
            n = min(len(data), capacity)
            if n < capacity:
                adapt = 184 - n
            pos = 4
            if adapt:
                b[3] |= 0x20
                b[pos] = adapt - 1
                if adapt >= 2:
                    b[pos + 1] = 0
                if pcr is not None:
                    v = pcr & ((1 << 33) - 1)
                    b[pos + 1 : pos + 8] = bytes(
                        (
                            0x10,
                            (v >> 25) & 255,
                            (v >> 17) & 255,
                            (v >> 9) & 255,
                            (v >> 1) & 255,
                            ((v << 7) & 255) | 0x7E,
                            0,
                        )
                    )
                pos += adapt
            b[pos : pos + n] = data[:n]
            packets.append(bytes(b))
            data = data[n:]
            start = False
            pcr = None
        return b"".join(packets)

    def _validate_pcr_time(self, dts):
        if self.last_pcr_dts is None:
            return
        delta = dts - self.last_pcr_dts
        if delta <= 0:
            raise GridError("media_pcr_not_increasing")
        # The muxer has no wall-clock-paced scheduler. It cannot safely invent
        # PCR samples for an interval that elapsed before this PES was produced.
        # Fail closed rather than emit a burst of backdated PCR-only packets.
        if delta > PCR_INTERVAL_90K:
            raise GridError("media_pcr_gap_limit")

    def section(self, pid, data):
        return self.packetize(pid, b"\0" + data + struct.pack(">I", crc_mpeg(data)))

    def tables(self):
        pat = bytes.fromhex("00b00d0001c100000001f000")
        pmt = bytes.fromhex("02b0170001c10000e100f0001be100f0000fe101f000")
        return self.section(0, pat) + self.section(0x1000, pmt)

    def _pes(self, video, pts, payload, dts=None, pcr=None):
        out = b""
        if not self.started:
            return b""
        if type(pts) is not int or pts < 0:
            raise GridError("media_pts_bounds")
        if dts is not None and (type(dts) is not int or dts < 0):
            raise GridError("media_dts_bounds")
        if dts is not None and (pts < dts or pts - dts >= 1 << 32):
            raise GridError("media_pts_dts_invalid")
        if video and dts is not None and self.last_dts is not None and dts <= self.last_dts:
            raise GridError("media_dts_not_monotonic")
        if video and dts is not None:
            self._validate_pcr_time(dts)
        header_len = 10 if video and dts is not None else 5
        length = 0 if video else len(payload) + 3 + header_len
        if length > 65535:
            raise GridError("aac_pes_limit")
        out = b""
        if video:
            self.last_dts = dts
            if self.video_frames and self.video_frames % 60 == 0:
                out += self.tables()
            self.video_frames += 1
            if self.first_pts is None:
                self.first_pts = pts
            self.last_pts = pts
            self.min_pts = pts if self.min_pts is None else min(self.min_pts, pts)
            self.max_pts = pts if self.max_pts is None else max(self.max_pts, pts)
        else:
            self.audio_chunks += 1
        if video and dts is not None:
            flags, header_len = 0xC0, 10
            timestamps = pts_bytes(pts, 3) + pts_bytes(dts, 1)
        else:
            flags, header_len = 0x80, 5
            timestamps = pts_bytes(pts, 2)
        length = 0 if video else len(payload) + 3 + header_len
        if length > 65535:
            raise GridError("aac_pes_limit")
        pes = (
            b"\0\0\1"
            + bytes((0xE0 if video else 0xC0,))
            + struct.pack(">H", length)
            + bytes((0x80, flags, header_len))
            + timestamps
            + payload
        )
        pcr_value = (dts if dts is not None else pcr) if video else None
        if video and dts is not None:
            self.last_pcr_dts = dts
        return out + self.packetize(0x100 if video else 0x101, pes, pcr=pcr_value)

    def add_video(self, dts, pts, payload, *, idr=False):
        if not self.started:
            if not idr or not has_nal(payload, 7) or not has_nal(payload, 8) or not has_nal(payload, 5):
                self.preroll_video += 1
                return b""
            self.started = True
            out = self.tables()
        else:
            out = b""
        return out + self._pes(True, pts, payload, dts)

    def add_audio(self, pts, payload):
        if not self.started or pts is None:
            return b""
        parsed = parse_adts(payload)
        if parsed.config_changed:
            raise GridError("aac_configuration_changed")
        if parsed.config.audio_object_type != 2:
            raise GridError("aac_profile_unsupported")
        if any(frame.raw_data_blocks != 1 for frame in parsed.frames):
            raise GridError("aac_raw_data_blocks_unsupported")
        if type(pts) is not int or pts < 0:
            raise GridError("media_pts_bounds")
        if self.audio_config is not None and self.audio_config != parsed.config:
            raise GridError("aac_configuration_changed")
        if self.last_audio_pts is not None and pts <= self.last_audio_pts:
            raise GridError("audio_timestamp_regression")
        output = bytearray()
        group = bytearray()
        samples = group_start = 0
        for frame in parsed.frames:
            if len(group) + len(frame.data) > 65527:
                group_pts = pts + round(Fraction(group_start * 90000, parsed.config.sample_rate))
                output.extend(self._pes(False, group_pts, bytes(group)))
                group.clear()
                group_start = samples
            group.extend(frame.data)
            samples += frame.core_samples
        if group:
            group_pts = pts + round(Fraction(group_start * 90000, parsed.config.sample_rate))
            output.extend(self._pes(False, group_pts, bytes(group)))
        self.audio_config = parsed.config
        self.last_audio_pts = pts + round(Fraction((samples - 1024) * 90000, parsed.config.sample_rate))
        return bytes(output)

    def add(self, kind, timestamp, payload):
        """Legacy PTS-only serializer retained for Go golden-vector tests.

        The receiver never calls this method: production media must use
        ``add_video`` with independently derived PTS/DTS.
        """
        video = kind in ("I", "P", "B")
        if not video and kind != "A":
            return b""
        if not self.started:
            if not video:
                return b""
            if kind != "I" or not has_nal(payload, 7) or not has_nal(payload, 5):
                self.preroll_video += 1
                return b""
            self.started = True
            self.base = timestamp
            out = self.tables()
        else:
            out = b""
        if timestamp > (1 << 63) - 1 - 10_000_000 or timestamp + 10_000_000 < self.base:
            raise GridError("media_timestamp_bounds")
        pts = (timestamp - self.base + 10_000_000) * 9 // 1000
        return out + self._pes(video, pts, payload, pcr=pts if video else None)
