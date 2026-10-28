# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors

from fractions import Fraction
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from soop_grid.h264clock import H264Clock, parse_pps, parse_sps
from soop_grid.media import adts_info
from soop_grid.mpegts import TSMux, nals
from soop_grid.protocol import GridError


class BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, width):
        self.bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))

    def ue(self, value):
        code = value + 1
        width = code.bit_length()
        self.bits.extend([0] * (width - 1))
        self.put(code, width)

    def se(self, value):
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def rbsp(self):
        self.bits.append(1)
        self.bits.extend([0] * (-len(self.bits) % 8))
        output = bytearray()
        for offset in range(0, len(self.bits), 8):
            value = 0
            for bit in self.bits[offset : offset + 8]:
                value = (value << 1) | bit
            output.append(value)
        return bytes(output)


def test_b_picture_timestamps_come_from_poc_and_grid_clock(encoded_bframes):
    clock = H264Clock()
    timed = []
    for index, (access_unit, _grid_kind) in enumerate(encoded_bframes):
        timing = clock.inspect(access_unit, 50_000_000 + index * 333_333)
        assert timing is not None
        timed.append(timing)

    dts = [frame["dts"] for frame in timed]
    pts = [frame["pts"] for frame in timed]
    assert all(a < b for a, b in zip(dts, dts[1:], strict=False))
    assert all(p >= d for p, d in zip(pts, dts, strict=True))
    assert any(a > b for a, b in zip(pts, pts[1:], strict=False))
    assert any(frame["slice_type"] == "B" for frame in timed)
    display_pts = sorted(pts)
    assert all(b - a == 3000 for a, b in zip(display_pts, display_pts[1:], strict=False))
    assert Fraction(timed[0]["sps"]["fps_numerator"], timed[0]["sps"]["fps_denominator"]) == 30


def test_truncated_sps_is_rejected(encoded_bframes):
    sps = next(n for n in nals(encoded_bframes[0][0]) if n[0] & 31 == 7)
    with pytest.raises(GridError):
        parse_sps(sps[:-3])


@pytest.mark.parametrize(("unit_type", "parser"), [(7, parse_sps), (8, parse_pps)])
def test_parameter_set_missing_rbsp_stop_bit_is_rejected(encoded_bframes, unit_type, parser):
    unit = next(n for n in nals(encoded_bframes[0][0]) if n[0] & 31 == unit_type)
    malformed = bytearray(unit)
    for index in range(len(malformed) - 1, 0, -1):
        if malformed[index]:
            malformed[index] &= malformed[index] - 1  # Clear the final set bit: rbsp_stop_one_bit.
            break
    with pytest.raises(GridError, match="h264_parameter_set_trailing_bits_invalid"):
        parser(bytes(malformed))


def test_poc1_sps_reference_cycle_is_parsed():
    writer = BitWriter()
    writer.put(66, 8)  # Baseline profile
    writer.put(0, 8)  # constraint flags and reserved bits
    writer.put(30, 8)  # level
    writer.ue(0)  # seq_parameter_set_id
    writer.ue(0)  # log2_max_frame_num_minus4
    writer.ue(1)  # pic_order_cnt_type
    writer.put(0, 1)  # delta_pic_order_always_zero_flag
    writer.se(-1)  # offset_for_non_ref_pic
    writer.se(0)  # offset_for_top_to_bottom_field
    writer.ue(2)  # num_ref_frames_in_pic_order_cnt_cycle
    writer.se(2)
    writer.se(3)
    writer.ue(2)  # max_num_ref_frames
    writer.put(0, 1)  # gaps_in_frame_num_value_allowed_flag
    writer.ue(9)  # 160 pixels wide
    writer.ue(5)  # 96 pixels high
    writer.put(1, 1)  # frame_mbs_only_flag
    writer.put(1, 1)  # direct_8x8_inference_flag
    writer.put(0, 1)  # frame_cropping_flag
    writer.put(0, 1)  # vui_parameters_present_flag
    sps = parse_sps(b"\x67" + writer.rbsp())
    assert sps["poc_type"] == 1
    assert sps["offset_for_non_ref_pic"] == -1
    assert sps["offset_for_ref_frame"] == (2, 3)
    assert (sps["width"], sps["height"]) == (160, 96)


def test_invalid_emulation_prevention_byte_is_rejected():
    with pytest.raises(GridError, match="h264_emulation_prevention_invalid"):
        parse_sps(b"\x67\x00\x00\x03\x04")


def test_data_partitioned_vcl_is_rejected_explicitly():
    with pytest.raises(GridError, match="h264_vcl_partition_or_extension_unsupported"):
        H264Clock().inspect(b"\0\0\1\x42\x80", 50_000_000)


def test_poc1_reference_cycle_and_frame_num_wrap_are_bounded():
    clock = H264Clock()
    sps = {
        "poc_type": 1,
        "max_frame_num": 16,
        "offset_for_ref_frame": (2, 3),
        "offset_for_non_ref_pic": -1,
        "offset_for_top_to_bottom_field": 0,
    }

    def header(frame_num, ref=1, mmco5=False):
        return SimpleNamespace(
            frame_num=frame_num,
            idr=False,
            nal_ref_idc=ref,
            delta_poc0=0,
            delta_poc1=0,
            mmco5=mmco5,
        )

    values = [clock._picture_order(header(n), sps) for n in (14, 15, 0, 1)]
    assert values == [35, 37, 40, 42]
    assert clock.prev_frame_num_offset == 16


def test_poc2_non_reference_frame_and_idr_epoch():
    clock = H264Clock()
    sps = {"poc_type": 2, "max_frame_num": 16}

    def header(frame_num, *, idr=False, ref=1):
        return SimpleNamespace(frame_num=frame_num, idr=idr, nal_ref_idc=ref, mmco5=False)

    assert clock._picture_order(header(0, idr=True), sps) == 0
    assert clock._picture_order(header(1), sps) == 2
    assert clock._picture_order(header(2, ref=0), sps) == 3
    assert clock._picture_order(header(3), sps) == 6
    assert clock._picture_order(header(0, idr=True), sps) == 0


def test_poc0_lsb_wrap_uses_previous_reference_epoch():
    clock = H264Clock()
    sps = {"poc_type": 0, "log2_poc_lsb": 4}

    def header(lsb, *, idr=False, ref=1, mmco5=False):
        return SimpleNamespace(
            frame_num=0,
            idr=idr,
            nal_ref_idc=ref,
            poc_lsb=lsb,
            delta_bottom=0,
            mmco5=mmco5,
        )

    # Seed the previous reference state reached by POC values 0..14.
    clock.prev_msb, clock.prev_lsb = 0, 14
    assert clock._picture_order(header(0), sps) == 16
    assert clock.prev_msb == 16
    assert clock._picture_order(header(2), sps) == 18
    assert clock._picture_order(header(0, idr=True), sps) == 0
    assert clock.prev_msb == 0


def test_poc0_mmco5_keeps_current_top_order_count_as_previous_lsb():
    clock = H264Clock()
    sps = {"poc_type": 0, "log2_poc_lsb": 4}

    def header(lsb, mmco5=False):
        return SimpleNamespace(
            frame_num=0,
            idr=False,
            nal_ref_idc=1,
            poc_lsb=lsb,
            delta_bottom=0,
            mmco5=mmco5,
        )

    clock.prev_msb, clock.prev_lsb = 0, 14
    assert clock._picture_order(header(0, mmco5=True), sps) == 16
    assert (clock.prev_msb, clock.prev_lsb) == (0, 16)
    assert clock._picture_order(header(2), sps) == 18


def test_mmco5_resets_frame_num_offset():
    clock = H264Clock()
    sps = {"poc_type": 2, "max_frame_num": 16}

    def header(frame_num, mmco5=False):
        return SimpleNamespace(frame_num=frame_num, idr=False, nal_ref_idc=1, mmco5=mmco5)

    assert clock._picture_order(header(15), sps) == 30
    assert clock._picture_order(header(0, mmco5=True), sps) == 32
    assert clock.prev_frame_num_offset == 0
    assert clock._picture_order(header(1), sps) == 2


def test_poc2_without_fixed_clock_but_zero_reorder_constraint_decodes(encoded_poc2_no_vui, tmp_path):
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("SOOP_TEST_FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and ffprobe required to independently validate the POC type 2 output")
    raw = tmp_path / "poc2.h264"
    raw.write_bytes(b"".join(encoded_poc2_no_vui))
    decoded_raw = subprocess.run(
        [ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", str(raw), "-f", "null", "-"],
        capture_output=True,
        timeout=30,
    )
    assert decoded_raw.returncode == 0, decoded_raw.stderr.decode("utf-8", "replace")
    parameter_set = next(nal for nal in nals(encoded_poc2_no_vui[0]) if nal[0] & 31 == 7)
    parsed_sps = parse_sps(parameter_set)
    assert parsed_sps["poc_type"] == 2
    assert parsed_sps["max_reorder"] == 0
    assert parsed_sps["max_reorder_signaled"]

    clock, mux = H264Clock(), TSMux()
    video = []
    base = 1_000_000_000
    for index, access_unit in enumerate(encoded_poc2_no_vui):
        timestamp = base + index * 333_333
        timing = clock.inspect(access_unit, timestamp)
        assert timing is not None and timing["pts"] == timing["dts"]
        video.append((timestamp, timing, access_unit))

    audio_path = tmp_path / "audio.aac"
    generated = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "2",
            "-c:a",
            "aac",
            "-f",
            "adts",
            str(audio_path),
        ],
        capture_output=True,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr.decode("utf-8", "replace")
    audio = []
    data, offset, sample = audio_path.read_bytes(), 0, 0
    while offset < len(data):
        length = ((data[offset + 3] & 3) << 11) | (data[offset + 4] << 3) | (data[offset + 5] >> 5)
        frame = data[offset : offset + length]
        assert len(frame) == length and adts_info(frame)
        audio.append((base + sample * 1024 * 10_000_000 // 48000, frame))
        offset += length
        sample += 1

    events = [(timestamp, "video", timing, payload) for timestamp, timing, payload in video]
    events.extend((timestamp, "audio", None, payload) for timestamp, payload in audio)
    output = bytearray()
    for timestamp, kind, timing, payload in sorted(events, key=lambda event: event[0]):
        if kind == "video":
            output.extend(mux.add_video(timing["dts"], timing["pts"], payload, idr=timing["idr"]))
        else:
            output.extend(mux.add_audio(clock.audio_pts(timestamp), payload))
    ts = tmp_path / "poc2.ts"
    ts.write_bytes(output)
    decoded = subprocess.run(
        [
            ffmpeg,
            "-v",
            "warning",
            "-xerror",
            "-nostdin",
            "-i",
            str(ts),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=30,
    )
    assert decoded.returncode == 0, decoded.stderr.decode("utf-8", "replace")
    assert b"non-monoton" not in decoded.stderr.lower()
    streams = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,start_time", "-of", "json", str(ts)],
        capture_output=True,
        timeout=30,
    )
    assert streams.returncode == 0, streams.stderr.decode("utf-8", "replace")
    starts = {
        stream["codec_type"]: float(stream["start_time"]) for stream in json.loads(streams.stdout)["streams"]
    }
    assert abs(starts["video"] - starts["audio"]) < 1 / 30
    streams = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,start_time",
            "-of",
            "json",
            str(ts),
        ],
        capture_output=True,
        timeout=30,
    )
    assert streams.returncode == 0, streams.stderr.decode("utf-8", "replace")
    starts = {
        stream["codec_type"]: float(stream["start_time"]) for stream in json.loads(streams.stdout)["streams"]
    }
    assert abs(starts["video"] - starts["audio"]) < 1 / 30


def test_poc2_b_slice_syntax_does_not_prove_zero_reordering(encoded_poc2_no_vui):
    from soop_grid.h264clock import parse_pps, parse_slice

    parameter_sets = nals(encoded_poc2_no_vui[0])
    sps_nal = next(nal for nal in parameter_sets if nal[0] & 31 == 7)
    pps_nal = next(nal for nal in parameter_sets if nal[0] & 31 == 8)
    sps = parse_sps(sps_nal)
    pps = parse_pps(pps_nal)
    idr = encoded_poc2_no_vui[0]
    assert sps["poc_type"] == 2 and sps["max_reorder"] == 0 and sps["max_reorder_signaled"]

    # Build the bounded B-slice header prefix using the fixture's valid SPS/PPS.
    writer = BitWriter()
    writer.ue(0)  # first_mb_in_slice
    writer.ue(1)  # B slice
    writer.ue(pps["id"])
    writer.put(1, sps["log2_frame_num"])
    writer.put(0, 1)  # direct_spatial_mv_pred_flag
    writer.put(0, 1)  # num_ref_idx_active_override_flag
    writer.put(0, 1)  # ref_pic_list_modification_flag_l0
    writer.put(0, 1)  # ref_pic_list_modification_flag_l1
    b_slice = b"\x01" + writer.rbsp()
    parsed = parse_slice(b_slice, sps, pps)
    assert parsed.slice_kind == "B"
    clock = H264Clock()
    assert clock.inspect(idr, 2_000_000_000) is not None
    timing = clock.inspect(b"\0\0\1" + b_slice, 2_000_333_333)
    assert timing["poc"] == 1 and timing["pts"] == timing["dts"]
    assert timing["pts"] == timing["dts"]
