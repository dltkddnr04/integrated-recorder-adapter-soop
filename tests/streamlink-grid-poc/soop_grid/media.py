# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import hashlib
import json
import os
import struct
from collections import deque as deque
from dataclasses import dataclass as dataclass, field as field
from fractions import Fraction as Fraction
from pathlib import Path as Path
from .h264clock import H264Clock
from .aac import parse_adts
from .mpegts import TSMux
from .mpegts import nals
from .protocol import GridError


@dataclass(repr=False)
class Chunk:
    frame: int
    kind: str
    timestamp: int
    payload: bytes = field(repr=False)
    extension_bytes: int = 0
    auxiliary_time_raw: int = 0
    composition_offset_raw: int = 0

    @property
    def start_timestamp(self):
        return self.timestamp


def layout_facts(body):
    facts = {"rejected_body_bytes": len(body)}
    if len(body) >= 12:
        facts.update(
            rejected_header_bytes=struct.unpack_from("<H", body, 2)[0],
            rejected_extension_bytes=struct.unpack_from("<I", body, 4)[0],
            rejected_payload_bytes=struct.unpack_from("<I", body, 8)[0],
        )
    return facts


def parse_media(body):
    if len(body) < 69:
        raise GridError("media_header_short")
    header = struct.unpack_from("<H", body, 2)[0]
    extension, size = struct.unpack_from("<II", body, 4)
    start = header + extension
    if (
        struct.unpack_from("<H", body, 0)[0] != 1
        or header != 69
        or extension > 4096
        or size < 1
        or start + size != len(body)
    ):
        raise GridError("media_layout_unconfirmed")
    kind = chr(body[40])
    if kind not in ("I", "P", "B", "A"):
        raise GridError("media_kind_unconfirmed")
    timestamp = struct.unpack_from("<Q", body, 41)[0]
    composition = struct.unpack_from("<I", body, 57)[0]
    if composition >= 1 << 31:
        raise GridError("grid_composition_offset_unsupported")
    if timestamp + composition >= 1 << 64:
        raise GridError("media_timestamp_bounds")
    payload = body[start:]
    if kind in ("I", "P", "B") and (len(payload) < 4 or payload[:2] != b"\x00\x00"):
        raise GridError("video_prefix_unconfirmed")
    if kind == "A" and (len(payload) < 7 or payload[0] != 255 or payload[1] & 240 != 240):
        raise GridError("audio_prefix_unconfirmed")
    return Chunk(
        struct.unpack_from("<Q", body, 12)[0],
        kind,
        timestamp,
        payload,
        extension,
        struct.unpack_from("<Q", body, 49)[0],
        composition,
    )


class Bits:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def get(self, n):
        if not 0 <= n <= 32 or self.offset + n > len(self.data) * 8:
            raise GridError("sps_truncated")
        value = 0
        for _ in range(n):
            value = value << 1 | self.data[self.offset // 8] >> 7 - self.offset % 8 & 1
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
    chroma, separate = (1, 0)
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
    w, h = (r.ue() + 1, r.ue() + 1)
    only = r.get(1)
    if not only:
        r.get(1)
    r.get(1)
    left = right = top = bottom = 0
    if r.get(1):
        left, right, top, bottom = (r.ue() for _ in range(4))
    subw, subh = (1, 1) if separate or chroma in (0, 3) else (2, 2) if chroma == 1 else (2, 1)
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
            units, scale = (r.get(32), r.get(32))
            r.get(1)
            if 0 < units < 1 << 30:
                info.update(fps_numerator=scale, fps_denominator=2 * units)
    return info


def adts_info(payload):
    parsed = parse_adts(payload)
    if parsed.config_changed:
        raise GridError("aac_configuration_changed")
    return {"codec": "aac", "sample_rate": parsed.config.sample_rate, "channels": parsed.config.channels}


class MediaSink:
    def __init__(self, emit, capture_dir=None, *, synchronize_startup=False):
        self.synchronize_startup = synchronize_startup
        self.startup_frames = []
        self.startup_bytes = 0
        self.startup_audio = None
        self.startup_video = False
        self.emit = emit
        self.mux = TSMux()
        self.video_clock = H264Clock()
        self.audio_config = None
        self.last_audio_timestamp = None
        self.last_audio_samples = 0
        self.audio_origin_timestamp = None
        self.pending_audio = deque()
        self.pending_audio_bytes = 0
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
            "audio_adts_frames": 0,
            "audio_access_units": 0,
            "audio_core_samples": 0,
            "audio_output_access_units": 0,
            "audio_output_core_samples": 0,
            "video_output_frames": 0,
            "sequence_discontinuities": 0,
            "extended_media_packets": 0,
            "media_extension_bytes": 0,
        }
        self.payload_hash = hashlib.sha256()
        self.video_output_hash = hashlib.sha256()
        self.audio_output_hash = hashlib.sha256()
        self.video_output_pts_window = deque(maxlen=32)
        self.audio_output_group_duration_window = deque(maxlen=32)
        self.last_frame = None
        try:
            if self.path:
                self.path.mkdir(mode=448, parents=False, exist_ok=False)
                self.owns_directory = True
                for name in (
                    "grid-wire.bin",
                    "grid-payload.bin",
                    "video.h264",
                    "audio.aac",
                    "recording.ts",
                    "frames.jsonl",
                    "rejected-frames.jsonl",
                ):
                    fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
                    self.files[name] = os.fdopen(fd, "wb")
        except OSError:
            self.close()
            raise GridError("private_capture_create_failed") from None

    def write(self, name, data):
        if name in self.files:
            self.files[name].write(data)

    def _record_output(self, data, *, video=None, audio=()):
        if not data:
            return
        self.stats["mux_output_bytes"] = self.stats.get("mux_output_bytes", 0) + len(data)
        self.write("recording.ts", data)
        self.emit(data)
        self.stats["stream_emit_bytes"] = self.stats.get("stream_emit_bytes", 0) + len(data)
        if video is not None:
            payload, timing = video
            self.video_output_hash.update(payload)
            self.stats["video_output_frames"] += 1
            pts = timing["pts"]
            self.video_output_pts_window.append(pts)
            self.stats["video_output_first_pts_90k"] = min(
                pts, self.stats.get("video_output_first_pts_90k", pts)
            )
            self.stats["video_output_end_pts_90k"] = max(pts, self.stats.get("video_output_end_pts_90k", pts))
            self.stats["video_parameter_prefix_bytes"] = self.stats.get(
                "video_parameter_prefix_bytes", 0
            ) + len(timing["decoder_prefix"])
        for payload, units, samples, pts in audio:
            self.audio_output_hash.update(payload)
            self.stats["audio_output_access_units"] += units
            self.stats["audio_output_core_samples"] += samples
            start = int(pts)
            end = pts + Fraction(samples * 90000, self.audio_config.sample_rate)
            end = (end.numerator + end.denominator - 1) // end.denominator
            self.stats["audio_output_first_pts_90k"] = min(
                start, self.stats.get("audio_output_first_pts_90k", start)
            )
            self.stats["audio_output_end_pts_90k"] = max(end, self.stats.get("audio_output_end_pts_90k", end))
            self.stats["audio_output_last_packet_duration_90k"] = max(0, end - start)
            self.audio_output_group_duration_window.append(max(0, end - start))

    def reject(self, frame, rejection_code="media_packet_rejected", *, wire_already_captured=False):
        if self.owns_directory:
            try:
                if not wire_already_captured:
                    fd = os.open(
                        self.path / "rejected-grid-wire.bin", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384
                    )
                    with os.fdopen(fd, "wb") as out:
                        out.write(frame.wire)
                try:
                    chunk = parse_media(frame.body)
                    identity = {
                        "frame_number": chunk.frame,
                        "frame_kind": chunk.kind,
                        "timestamp_raw": chunk.timestamp,
                        "payload_bytes": len(chunk.payload),
                    }
                except GridError:
                    identity = layout_facts(frame.body)
                code = str(rejection_code)
                if (
                    not code
                    or len(code) > 64
                    or (not all((c.islower() or c.isdigit() or c == "_" for c in code)))
                ):
                    code = "media_packet_rejected"
                row = {"event": "media_packet_rejected", "command": frame.command, "code": code, **identity}
                self.write("rejected-frames.jsonl", (json.dumps(row, sort_keys=True) + "\n").encode("ascii"))
            except OSError:
                self.stats["rejected_packet_preservation_failed"] = 1

    def add(self, frame, discard=False):
        if not self.synchronize_startup or discard:
            return self._add(frame, discard)
        chunk = parse_media(frame.body)
        self.startup_frames.append(frame)
        self.startup_bytes += len(frame.body)
        if chunk.kind == "A" and self.startup_audio is None:
            self.startup_audio = chunk.timestamp
        elif chunk.kind in ("I", "P", "B"):
            self.startup_video = True
        if self.startup_bytes > 32 << 20 or len(self.startup_frames) > 600:
            raise GridError("media_startup_limit")
        if self.startup_audio is not None and self.startup_video:
            self.video_clock.observe_audio(self.startup_audio)
            pending, self.startup_frames = self.startup_frames, []
            self.startup_bytes = 0
            self.synchronize_startup = False
            for item in pending:
                self._add(item)
        return chunk

    def _add(self, frame, discard=False):
        self.write("grid-wire.bin", frame.wire)
        chunk = parse_media(frame.body)
        self.write("grid-payload.bin", chunk.payload)
        self.payload_hash.update(chunk.payload)
        self.stats["media_packets"] += 1
        self.stats["payload_bytes"] += len(chunk.payload)
        self.stats["wire_bytes"] += len(frame.wire)
        if chunk.extension_bytes:
            self.stats["extended_media_packets"] += 1
            self.stats["media_extension_bytes"] += chunk.extension_bytes
        if discard:
            return chunk
        if self.last_frame is not None and chunk.frame != self.last_frame + 1:
            self.stats["sequence_discontinuities"] += 1
        self.last_frame = chunk.frame
        if chunk.kind in ("I", "P", "B"):
            self.write("video.h264", chunk.payload)
            self.stats["video_frames"] += 1
            try:
                timing = self.video_clock.inspect(
                    chunk.payload, chunk.timestamp, chunk.composition_offset_raw
                )
            finally:
                self.stats.update(self.video_clock.last_observation)
                self.stats["video_preroll_frames"] = self.video_clock.preroll_frames
                self.stats["video_preroll_bytes"] = self.video_clock.preroll_bytes
            if "video" not in self.stats:
                info = sps_info(chunk.payload)
                if info:
                    self.stats["video"] = info
        elif chunk.kind == "A":
            audio = parse_adts(chunk.payload)
            if audio.config is not None and audio.config.audio_object_type != 2:
                raise GridError("aac_profile_unsupported")
            if audio.config_changed or (self.audio_config is not None and self.audio_config != audio.config):
                raise GridError("aac_configuration_changed")
            if any((frame.raw_data_blocks != 1 for frame in audio.frames)):
                raise GridError("aac_raw_data_blocks_unsupported")
            self.audio_config = audio.config
            self.video_clock.observe_audio(chunk.timestamp)
            if self.last_audio_timestamp is not None:
                if chunk.timestamp <= self.last_audio_timestamp:
                    raise GridError("audio_timestamp_regression")
                last_au_start = self.last_audio_timestamp + Fraction(
                    (self.last_audio_samples - 1024) * 10000000, audio.config.sample_rate
                )
                if chunk.timestamp <= last_au_start:
                    raise GridError("audio_timestamp_regression")
                expected = self.last_audio_timestamp + Fraction(
                    self.last_audio_samples * 10000000, audio.config.sample_rate
                )
                delta = round(chunk.timestamp - expected)
                one_au = Fraction(1024 * 10000000, audio.config.sample_rate)
                if abs(chunk.timestamp - expected) >= one_au:
                    raise GridError("audio_chunk_clock_mismatch")
                self.stats["audio_chunk_clock_delta_100ns"] = delta
                self.stats["audio_max_chunk_clock_delta_100ns"] = max(
                    self.stats.get("audio_max_chunk_clock_delta_100ns", 0), abs(delta)
                )
                overlap = max(0, round(expected - chunk.timestamp))
                self.stats["audio_chunk_overlap_100ns"] = overlap
                self.stats["audio_max_chunk_overlap_100ns"] = max(
                    self.stats.get("audio_max_chunk_overlap_100ns", 0), overlap
                )
            if self.audio_origin_timestamp is None:
                self.audio_origin_timestamp = chunk.timestamp
            one_au = Fraction(1024 * 10000000, audio.config.sample_rate)
            sample_clock_timestamp = self.audio_origin_timestamp + Fraction(
                self.stats["audio_core_samples"] * 10000000, audio.config.sample_rate
            )
            audio_clock_drift = (
                chunk.timestamp
                - self.audio_origin_timestamp
                - Fraction(self.stats["audio_core_samples"] * 10000000, audio.config.sample_rate)
            )
            if abs(audio_clock_drift) >= one_au:
                raise GridError("audio_timestamp_cumulative_drift")
            self.stats["audio_clock_drift_100ns"] = round(audio_clock_drift)
            self.last_audio_timestamp = chunk.timestamp
            self.last_audio_samples = audio.core_samples
            self.write("audio.aac", chunk.payload)
            self.stats["audio_frames"] += 1
            self.stats["audio_adts_frames"] += len(audio.frames)
            self.stats["audio_access_units"] += audio.access_unit_count
            self.stats["audio_core_samples"] += audio.core_samples
            if "audio" not in self.stats:
                self.stats["audio"] = adts_info(chunk.payload)
        ts = b""
        video_for_emit = None
        audio_for_emit = []
        if chunk.kind in ("I", "P", "B"):
            if timing is not None:
                mux_payload = timing["decoder_prefix"] + chunk.payload
                ts = self.mux.add_video(timing["dts"], timing["pts"], mux_payload, idr=timing["idr"])
                if ts:
                    video_for_emit = mux_payload
                self.stats["last_video_dts_90k"] = timing["dts"]
                self.stats["last_video_pts_90k"] = timing["pts"]
                self.stats["last_video_slice_type"] = timing["slice_type"]
                self.stats["last_video_poc"] = timing["poc"]
                self.stats["h264_poc_type"] = timing["sps"]["poc_type"]
                self.stats["h264_fixed_vui_timing"] = int(timing["sps"]["timing_fixed"])
                self.stats["video_timing_model"] = timing["timing_model"]
                if ts and self.pending_audio:
                    for timestamp, payload, units, samples in self.pending_audio:
                        audio_pts = self.video_clock.audio_pts(timestamp)
                        ts += self.mux.add_audio(audio_pts, payload)
                        audio_for_emit.append((payload, units, samples, audio_pts))
        elif not self.mux.started:
            if self.pending_audio_bytes + len(chunk.payload) > 1 << 20 or len(self.pending_audio) >= 64:
                raise GridError("audio_preroll_limit")
            self.pending_audio.append(
                (sample_clock_timestamp, chunk.payload, audio.access_unit_count, audio.core_samples)
            )
            self.pending_audio_bytes += len(chunk.payload)
        else:
            audio_pts = self.video_clock.audio_pts(sample_clock_timestamp)
            ts = self.mux.add_audio(audio_pts, chunk.payload)
            audio_for_emit.append((chunk.payload, audio.access_unit_count, audio.core_samples, audio_pts))
        if ts:
            self._record_output(
                ts,
                video=(video_for_emit, timing) if video_for_emit is not None else None,
                audio=audio_for_emit,
            )
            if self.pending_audio and audio_for_emit:
                self.pending_audio.clear()
                self.pending_audio_bytes = 0
        record = {
            "frame": chunk.frame,
            "type": chunk.kind,
            "timestamp_raw": chunk.timestamp,
            "payload_bytes": len(chunk.payload),
            **(
                {
                    "pts_90k": timing["pts"],
                    "dts_90k": timing["dts"],
                    "poc": timing["poc"],
                    "slice_type": timing["slice_type"],
                    "timing_model": timing["timing_model"],
                }
                if chunk.kind in ("I", "P", "B") and timing is not None
                else {}
            ),
        }
        self.write("frames.jsonl", (json.dumps(record, sort_keys=True) + "\n").encode())
        return chunk

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stats["startup_unoutput_frames"] = len(self.startup_frames)
        self.startup_frames.clear()
        self.startup_bytes = 0
        failed = False
        for f in self.files.values():
            try:
                f.close()
            except OSError:
                failed = True
        self.files.clear()
        self.stats["audio_preroll_unoutput_access_units"] = sum((item[2] for item in self.pending_audio))
        self.pending_audio.clear()
        self.pending_audio_bytes = 0
        self.stats["payload_sha256"] = self.payload_hash.hexdigest()
        self.stats["video_output_sha256"] = self.video_output_hash.hexdigest()
        self.stats["audio_output_sha256"] = self.audio_output_hash.hexdigest()
        self.stats["playable_video_frames"] = self.stats["video_output_frames"]
        video_pts = tuple(sorted(set(self.video_output_pts_window)))
        video_steps = sorted(
            right - left for left, right in zip(video_pts, video_pts[1:], strict=False) if right > left
        )
        video_step = video_steps[len(video_steps) // 2] if video_steps else 0
        audio_group_steps = sorted(self.audio_output_group_duration_window)
        audio_group_step = audio_group_steps[len(audio_group_steps) // 2] if audio_group_steps else 0
        video_duration = (
            self.stats.get("video_output_end_pts_90k", 0)
            - self.stats.get("video_output_first_pts_90k", 0)
            + video_step
        )
        audio_duration = self.stats.get("audio_output_end_pts_90k", 0) - self.stats.get(
            "audio_output_first_pts_90k", 0
        )
        if not self.stats["video_output_frames"] or not self.stats["audio_output_access_units"]:
            span_status = "incomplete"
        elif not video_step or not self.stats.get("audio_output_last_packet_duration_90k"):
            span_status = "unverified"
        else:
            video_start = self.stats.get("video_output_first_pts_90k", 0)
            video_end = self.stats.get("video_output_end_pts_90k", 0)
            audio_start = self.stats.get("audio_output_first_pts_90k", 0)
            audio_end = self.stats.get("audio_output_end_pts_90k", 0)
            # The presentation interval of the final video picture includes
            # its observed cadence. Use the same end model for overlap and
            # duration checks.
            video_window_end = video_end + video_step
            windows_overlap = video_start <= audio_end and audio_start <= video_window_end
            # This is a coarse track-span consistency check, not an A/V sync
            # proof. Grid/HRD epoch semantics and external capture boundaries
            # are not established by these counters.
            # A single unusually large AAC bundle must not mask a stalled
            # track. Bound endpoint granularity by the median of recent output
            # bundles rather than the last (or largest-ever) bundle.
            edge = max(video_step, audio_group_step)
            delta = video_duration - audio_duration
            self.stats["av_output_coverage_delta_90k"] = delta
            self.stats["av_output_coverage_edge_90k"] = edge
            self.stats["av_output_start_offset_90k"] = audio_start - video_start
            self.stats["av_output_end_offset_90k"] = audio_end - video_end
            span_status = "incomplete" if not windows_overlap or abs(delta) > edge else "consistent"
        self.stats["av_track_span_status"] = span_status
        if self.mux.first_pts is not None:
            self.stats["media_timeline_seconds"] = (self.mux.max_pts - self.mux.min_pts) / 90000
        if self.owns_directory:
            path = self.path / "grid-analysis.json"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
                capture_stats = dict(self.stats)
                # StreamIO drain is finalized only after the consumer reads
                # EOF; this per-sink file cannot truthfully record that value.
                capture_stats.pop("stream_output_drain_complete", None)
                capture_stats["stream_output_drain_status"] = "pending_at_media_close"
                with os.fdopen(fd, "w") as f:
                    json.dump(capture_stats, f, indent=2)
            except FileExistsError:
                pass
            except OSError:
                failed = True
        if failed:
            raise GridError("capture_close_failed")
