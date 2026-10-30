# SPDX-License-Identifier: AGPL-3.0-only
"""Independent policy regressions and bounded parser counterexamples.

Only the real-POC test claims an independently decoded complete bitstream.
Other vectors are deliberately bounded SPS/PPS/slice-header parser probes.
"""

import json
import os
import subprocess
from copy import deepcopy
from fractions import Fraction

import pytest

from conftest import media_body
from soop_grid.h264clock import H264Clock, Reader, inferred_reorder, parse_pps, parse_sei, parse_sps, rbsp
from soop_grid.media import MediaSink
from soop_grid.mpegts import TSMux, nals
from tools.legacy_h264_audit import LegacyH264Clock
from soop_grid.protocol import Frame, GridError


class Bits:
    def __init__(self):
        self.data = []

    def put(self, value, width=1):
        self.data.extend((value >> bit) & 1 for bit in range(width - 1, -1, -1))

    def ue(self, value):
        width = (value + 1).bit_length()
        self.data.extend([0] * (width - 1))
        self.put(value + 1, width)

    def se(self, value):
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def finish_rbsp(self):
        self.put(1)
        self.data.extend([0] * (-len(self.data) % 8))
        return bytes(sum(self.data[i + j] << (7 - j) for j in range(8)) for i in range(0, len(self.data), 8))

    def finish(self, header):
        return escape(header, self.finish_rbsp())


def escape(header, data):
    out, zeros = bytearray([header]), 0
    for value in data:
        if zeros >= 2 and value <= 3:
            out.append(3)
            zeros = 0
        out.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(out)


def annex(*units):
    return b"".join(b"\0\0\0\1" + unit for unit in units)


def sps(
    *,
    mode=0,
    fixed=True,
    frame_width=4,
    cycle=2,
    reorder=None,
    hrd=False,
    pic_struct=False,
    tick_num=1,
    tick_scale=60,
    initial_delay_bits=3,
    cpb_delay_bits=3,
    dpb_delay_bits=3,
    level=30,
    max_refs=2,
    constraints=0,
    profile=77,
    low_delay=False,
    nal_hrd=None,
    vcl_hrd=False,
):
    nal_hrd = hrd if nal_hrd is None else nal_hrd
    has_hrd = nal_hrd or vcl_hrd
    b = Bits()
    b.put(profile, 8)
    b.put(constraints, 8)
    b.put(level, 8)
    b.ue(0)  # seq_parameter_set_id
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135, 144):
        b.ue(1)  # 4:2:0 chroma_format_idc
        b.ue(0)  # bit_depth_luma_minus8
        b.ue(0)  # bit_depth_chroma_minus8
        b.put(0)  # qpprime_y_zero_transform_bypass_flag
        b.put(0)  # seq_scaling_matrix_present_flag
    b.ue(frame_width - 4)
    b.ue(mode)
    if mode == 0:
        b.ue(2)  # six-bit POC LSB
    elif mode == 1:
        b.put(1)  # delta_pic_order_always_zero_flag
        b.se(0)
        b.se(0)
        b.ue(1)
        b.se(cycle)
    b.ue(max_refs)
    b.put(0)
    b.ue(9)
    b.ue(5)
    b.put(1)
    b.put(1)
    b.put(0)
    vui = fixed or reorder is not None or has_hrd or pic_struct
    b.put(int(vui))
    if vui:
        for _ in range(4):
            b.put(0)
        b.put(int(fixed))  # timing_info_present_flag
        if fixed:
            b.put(tick_num, 32)
            b.put(tick_scale, 32)
            b.put(1)
        b.put(int(nal_hrd))  # NAL HRD
        if nal_hrd:
            b.ue(0)
            b.put(0, 8)
            b.ue(0)
            b.ue(0)
            b.put(1)
            for width in (initial_delay_bits, cpb_delay_bits, dpb_delay_bits, 0):
                b.put(width - 1 if width else 0, 5)
        b.put(int(vcl_hrd))  # VCL HRD
        if vcl_hrd:
            b.ue(0)
            b.put(0, 8)
            b.ue(0)
            b.ue(0)
            b.put(1)
            for width in (initial_delay_bits, cpb_delay_bits, dpb_delay_bits, 0):
                b.put(width - 1 if width else 0, 5)
        if has_hrd:
            b.put(int(low_delay))  # low_delay_hrd_flag
        b.put(int(pic_struct))
        b.put(int(reorder is not None))
        if reorder is not None:
            b.put(1)
            for value in (0, 0, 10, 10, reorder, max(2, reorder)):
                b.ue(value)
    return b.finish(0x67)


def pps(
    qp=0,
    parameter_id=0,
    weighted_bipred=0,
    chroma_qp=0,
    second_chroma_qp=None,
    transform_8x8=False,
    scaling_matrix=False,
    chroma_format=1,
):
    b = Bits()
    b.ue(parameter_id)
    b.ue(0)
    b.put(0)
    b.put(0)
    b.ue(0)
    b.ue(0)
    b.ue(0)
    b.put(0)
    b.put(weighted_bipred, 2)
    b.se(qp)
    b.se(0)
    b.se(chroma_qp)
    b.put(0)
    b.put(0)
    b.put(0)
    if second_chroma_qp is not None or scaling_matrix:
        b.put(int(transform_8x8))
        b.put(int(scaling_matrix))
        if scaling_matrix:
            list_count = 6 + (2 if chroma_format != 3 else 6) * transform_8x8
            for _ in range(list_count):
                b.put(0)  # pic_scaling_list_present_flag
        b.se(second_chroma_qp or 0)
    return b.finish(0x68)


@pytest.mark.parametrize(
    "unit,code",
    [
        (pps(qp=-27), "h264_pps_qp_offset_invalid"),
        (pps(chroma_qp=13), "h264_pps_chroma_qp_offset_invalid"),
        (pps(second_chroma_qp=13), "h264_pps_chroma_qp_offset_invalid"),
    ],
)
def test_pps_qp_offsets_are_range_checked_against_referenced_sps(unit, code):
    with pytest.raises(GridError, match=code):
        parse_pps(unit, {0: parse_sps(sps())})


def test_pps_before_sps_qp_range_is_checked_when_sps_arrives():
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_pps_qp_offset_invalid"):
        clock.inspect(annex(pps(qp=-27), sps(), picture(idr=True)), 50_000_000, 0)


def test_pps_scaling_extension_with_six_lists_does_not_require_sps():
    parsed = parse_pps(pps(scaling_matrix=True))
    assert parsed["scaling_matrix"] and parsed["scaling_lists"] == 6


def test_pps_eight_by_eight_scaling_layout_can_precede_sps():
    clock = H264Clock()
    payload = annex(
        pps(transform_8x8=True, scaling_matrix=True, chroma_format=1),
        sps(profile=100),
    )
    clock.inspect(payload, 50_000_000, 0)
    assert clock.sps[0]["chroma_format"] == 1
    assert clock.pps[0]["scaling_lists"] == 8


def picture(frame=0, poc=0, *, mode=0, idr=False, idr_id=0, mmco=False, kind=0, first_mb=0, pps_id=0):
    b = Bits()
    b.ue(first_mb)
    b.ue(2 if idr else kind)
    b.ue(pps_id)
    b.put(frame, 4)
    if idr:
        b.ue(idr_id)
    if mode == 0:
        b.put(poc, 6)
    if not idr:
        if kind == 1:
            b.put(0)  # direct_spatial_mv_pred_flag
        b.put(0)  # reference count override
        b.put(0)  # ref_pic_list_modification_flag_l0
        if kind == 1:
            b.put(0)  # ref_pic_list_modification_flag_l1
    if idr:
        b.put(0)
        b.put(0)
    else:
        b.put(int(mmco))
        if mmco:
            b.ue(5)
            b.ue(0)
    return b.finish(0x65 if idr else 0x41)


@pytest.mark.parametrize("mode,cycle", [(0, 2), (1, 4)])
def test_fixed_vui_and_poc_order_alone_do_not_time_reordered_pictures(mode, cycle):
    clock = LegacyH264Clock()
    with pytest.raises(GridError, match="h264_reorder_timing_unconfirmed"):
        clock.inspect(annex(sps(mode=mode, cycle=cycle), pps(), picture(mode=mode, idr=True)), 50_000_000)
    assert clock.origin_dts is None


def test_type2_nonreference_picture_has_a_whole_output_interval():
    clock = LegacyH264Clock()
    first = clock.inspect(annex(sps(mode=2, reorder=0), pps(), picture(mode=2, idr=True)), 50_000_000)
    unit = picture(1, mode=2)
    second = clock.inspect(annex(bytes([0x01]) + unit[1:]), 50_333_333)
    # POC 0 then 1 are consecutive output pictures, not half-frame samples.
    assert second["pts"] - first["pts"] == 3000


def test_poc2_requires_explicit_zero_reorder_without_hrd():
    clock = LegacyH264Clock()
    first = clock.inspect(
        annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)),
        50_000_000,
    )
    second = clock.inspect(annex(picture(frame=1, mode=2)), 50_333_333)
    assert first["pts"] == first["dts"]
    assert second["pts"] == second["dts"]


def test_poc2_without_reorder_constraint_or_hrd_fails_before_output():
    sequence = sps(mode=2, fixed=False)
    parsed = parse_sps(sequence)
    assert parsed["poc_type"] == 2
    assert parsed["max_reorder"] > 0
    assert not parsed["max_reorder_signaled"]

    clock = LegacyH264Clock()
    with pytest.raises(GridError, match="h264_reorder_timing_unconfirmed"):
        clock.inspect(annex(sequence, pps(), picture(mode=2, idr=True)), 50_000_000)
    assert clock.origin_dts is None and clock.last_dts is None


def test_poc2_accepts_annex_a_inferred_zero_reorder():
    # H.264 Annex A infers zero for these profile/constraint combinations when
    # the VUI restriction is absent. Exercise that inference independently of
    # the VUI-signaled clock branch below.
    assert inferred_reorder(100, 30, 0x10, 10, 6) == 0
    sequence = sps(mode=2, fixed=False, profile=100, constraints=0x10)
    parsed = parse_sps(sequence)
    assert parsed["max_reorder"] == 0
    assert not parsed["max_reorder_signaled"]
    assert parsed["max_reorder_zero_proven"]
    clock = LegacyH264Clock()
    first = clock.inspect(annex(sequence, pps(), picture(mode=2, idr=True)), 50_000_000)
    second = clock.inspect(annex(picture(1, mode=2)), 50_333_333)
    assert first["pts"] == first["dts"] and second["pts"] == second["dts"]


def test_sps_rejects_reference_count_over_level_dpb_bound():
    with pytest.raises(GridError, match="h264_reference_count_invalid"):
        parse_sps(sps(mode=2, fixed=False, level=10, max_refs=7))


def test_sps_uses_level_1b_dpb_ceiling():
    # 60 macroblocks per picture: level 1b allows 6 DPB frames (396 Mbs),
    # while level 1.1 allows 15 (900 Mbs).
    with pytest.raises(GridError, match="h264_reference_count_invalid"):
        parse_sps(sps(mode=2, fixed=False, level=11, constraints=0x10, max_refs=7))
    assert parse_sps(sps(mode=2, fixed=False, level=11, max_refs=7))["max_num_ref_frames"] == 7


def test_sps_rejects_unimplemented_profiles_explicitly():
    with pytest.raises(GridError, match="h264_profile_unsupported"):
        parse_sps(sps(mode=2, fixed=False, profile=244))


def test_pps_rejects_reserved_weighted_bipred_value():
    with pytest.raises(GridError, match="h264_weighted_bipred_reserved"):
        parse_pps(pps(weighted_bipred=3))


def test_hrd_cpb_clock_is_not_assumed_to_equal_grid_decode_clock():
    clock = LegacyH264Clock()
    first = clock.inspect(
        annex(
            sps(mode=2, hrd=True),
            pps(),
            buffering_period_sei(),
            timing_sei(cpb=0, dpb=0),
            picture(idr=True, mode=2),
        ),
        50_000_000,
    )
    # Grid and CPB clocks are not required to agree frame by frame. Grid remains
    # the decode-time candidate; the HRD DPB delay is a relative PTS offset.
    next_picture = clock.inspect(
        annex(timing_sei(cpb=1, dpb=0), picture(frame=1, poc=2, mode=2)),
        50_082_000,
    )
    assert next_picture["dts"] - first["dts"] == H264Clock._rescale(82_000)
    assert next_picture["pts"] - first["pts"] == H264Clock._rescale(82_000)
    assert clock.last_observation["h264_cpb_removal_delay"] == 1


def test_hrd_counter_wrap_does_not_change_grid_derived_dts():
    clock = LegacyH264Clock()
    first = clock.inspect(
        annex(
            sps(mode=2, hrd=True),
            pps(),
            buffering_period_sei(),
            timing_sei(cpb=7, dpb=0),
            picture(idr=True, mode=2),
        ),
        50_000_000,
    )
    wrapped = clock.inspect(
        annex(timing_sei(cpb=0, dpb=0), picture(frame=1, poc=2, mode=2)),
        50_166_667,
    )
    assert wrapped["dts"] > 90_000
    assert wrapped["pts"] - first["pts"] == H264Clock._rescale(166_667)


def test_hrd_buffering_period_reanchors_hrd_without_grid_clock_equality():
    seq = sps(mode=2, hrd=True, cpb_delay_bits=8)

    def buffering_period(cpb):
        period = Bits()
        period.ue(0)  # seq_parameter_set_id
        period.put(5, 3)  # initial_cpb_removal_delay
        period.put(2, 3)  # initial_cpb_removal_delay_offset
        return sei_message(0, period.finish_rbsp()), timing_sei(cpb=cpb, dpb=0, cpb_bits=8)

    clock = LegacyH264Clock()
    first_bp, first_timing = buffering_period(240)
    first = clock.inspect(annex(seq, pps(), first_bp, first_timing, picture(idr=True, mode=2)), 50_000_000)
    second = clock.inspect(
        annex(timing_sei(cpb=2, dpb=0, cpb_bits=8), picture(1, mode=2)),
        50_082_000,
    )
    # A new buffering_period does not reset the wrapper timestamp timeline.
    # This test checks that Grid DTS progresses independently of CPB values.
    next_bp, next_timing = buffering_period(12)
    third = clock.inspect(
        annex(next_bp, next_timing, picture(2, mode=2)),
        50_090_000,
    )
    fourth = clock.inspect(
        annex(timing_sei(cpb=2, dpb=0, cpb_bits=8), picture(3, mode=2)),
        50_172_000,
    )
    assert first["dts"] < second["dts"] < third["dts"] < fourth["dts"]
    assert [
        b["pts"] - a["pts"] for a, b in zip([first, second, third], [second, third, fourth], strict=True)
    ] == [
        H264Clock._rescale(82_000),
        H264Clock._rescale(8_000),
        H264Clock._rescale(82_000),
    ]


@pytest.mark.parametrize("mode", [1, 2])
def test_mmco5_without_an_independent_timing_source_fails_closed(mode):
    clock = LegacyH264Clock()
    clock.inspect(annex(sps(mode=mode, reorder=0), pps(), picture(mode=mode, idr=True)), 50_000_000)
    clock.inspect(annex(picture(1, mode=mode)), 50_333_333)
    with pytest.raises(GridError, match="h264_mmco5_timing_unsupported"):
        clock.inspect(annex(picture(2, mode=mode, mmco=True)), 50_666_666)


def test_different_idr_pic_ids_are_two_pictures():
    with pytest.raises(GridError, match="h264_multiple_pictures_per_grid_chunk"):
        H264Clock().inspect(
            annex(sps(), pps(), picture(idr=True, idr_id=1), picture(idr=True, idr_id=2, first_mb=1)),
            50_000_000,
            0,
        )


def test_annexb_trailing_zeros_do_not_corrupt_sps():
    clock = H264Clock()
    assert clock.inspect(annex(sps()) + b"\0\0\0", 50_000_000, 0) is None
    assert clock.sps[0]["width"] == 160
    assert clock.preroll_frames == 1


def test_frame_num_width_redefinition_is_rejected():
    clock = H264Clock()
    clock.inspect(annex(sps(frame_width=4, reorder=0), pps(), picture(idr=True)), 50_000_000, 0)
    with pytest.raises(GridError, match="h264_codec_configuration_changed"):
        clock.inspect(annex(sps(frame_width=5, reorder=0)), 50_333_333, 0)


def test_qp_redefinition_is_not_invisible_to_configuration_policy():
    assert pps(0) != pps(1)
    clock = H264Clock()
    clock.inspect(annex(sps(reorder=0), pps(0), picture(idr=True)), 50_000_000, 0)
    with pytest.raises(GridError, match="h264_codec_configuration_changed"):
        clock.inspect(annex(pps(1)), 50_333_333, 0)


def test_reference_b_with_increasing_poc_is_not_proof_of_reordering():
    """Explicit zero reorder permits B prediction without POC reordering."""
    clock = H264Clock()
    clock.inspect(annex(sps(fixed=False, reorder=0), pps(), picture(idr=True)), 50_000_000, 0)
    timed = clock.inspect(annex(picture(1, 2, kind=1)), 50_333_333, 0)
    assert timed["pts"] == timed["dts"]


def test_real_scaled_poc_cannot_change_presentation_timing(encoded_bframes, tmp_path):
    """Complete first GOP: monotonic POC scaling preserves decoded pixels/order.

    HRD timing must stay unchanged after scaling POC. Without HRD, reject at
    the first IDR instead of emitting an invented presentation clock.
    """
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG")
    if not ffmpeg:
        pytest.skip("set SOOP_TEST_FFMPEG for independent decoding")
    original = [unit for unit, _ in encoded_bframes[:15]]
    active_sps, active_pps, scaled = {}, {}, []
    for unit in original:
        output = []
        for nal in nals(unit):
            kind = nal[0] & 31
            if kind == 7:
                info = parse_sps(nal)
                active_sps[info["id"]] = info
            if kind == 8:
                info = parse_pps(nal)
                active_pps[info["id"]] = info
            if kind in (1, 5):
                body = bytearray(rbsp(nal))
                reader = Reader(body)
                reader.ue()
                reader.ue()
                ps = active_pps[reader.ue()]
                seq = active_sps[ps["sps_id"]]
                assert seq["poc_type"] == 0 and not ps["bottom_poc"]
                reader.bits(seq["log2_frame_num"])
                if kind == 5:
                    reader.ue()
                start, width = reader.pos, seq["log2_poc_lsb"]
                value = reader.bits(width) * 2
                assert value < 1 << width
                for offset in range(width):
                    pos = start + offset
                    mask = 1 << (7 - pos % 8)
                    body[pos // 8] = (body[pos // 8] & ~mask) | (((value >> (width - offset - 1)) & 1) * mask)
                nal = escape(nal[0], body)
            output.append(nal)
        scaled.append(annex(*output))
    decoded = []
    for name, units in [("original", original), ("scaled", scaled)]:
        path = tmp_path / (name + ".h264")
        path.write_bytes(b"".join(units))
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-nostdin",
                "-i",
                str(path),
                "-fps_mode",
                "passthrough",
                "-f",
                "framemd5",
                "-",
            ],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode()
        decoded.append([line for line in result.stdout.splitlines() if not line.startswith(b"#")])
    assert len(decoded[0]) == len(decoded[1]) == 15
    assert decoded[0] == decoded[1]
    for units in [original, scaled]:
        clock = LegacyH264Clock()
        seq = next(iter(active_sps.values()))
        if seq["nal_hrd"] or seq["vcl_hrd"]:
            first = clock.inspect(units[0], 50_000_000)
            second = clock.inspect(units[1], 50_333_333)
            assert second["pts"] - first["pts"] == 9000
        else:
            with pytest.raises(GridError, match="h264_reorder_timing_unconfirmed"):
                clock.inspect(units[0], 50_000_000)


def test_hrd_av_clock_matches_independent_ffmpeg_reference(tmp_path):
    """Reference DTS/PTS, not assumed first-I/audio alignment, define Grid inputs."""
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG")
    ffprobe = os.environ.get("SOOP_TEST_FFPROBE")
    if not ffmpeg or not ffprobe:
        pytest.skip("set SOOP_TEST_FFMPEG and SOOP_TEST_FFPROBE")
    reference = tmp_path / "reference.ts"
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x96:rate=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-b:v",
            "400k",
            "-minrate",
            "400k",
            "-maxrate",
            "400k",
            "-bufsize",
            "400k",
            "-x264-params",
            "nal-hrd=cbr:bframes=2:b-adapt=0:keyint=30:min-keyint=30:scenecut=0:aud=1:repeat-headers=1:force-cfr=1",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            str(reference),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()

    def probe(path, data=False):
        args = [ffprobe, "-v", "error", "-show_packets", "-of", "json"]
        if data:
            args.append("-show_data")
        result = subprocess.run(args + [str(path)], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode()
        return json.loads(result.stdout)["packets"]

    packets = probe(reference, data=True)
    assert sum(p["codec_type"] == "video" for p in packets) == 30
    assert any(int(p["pts"]) != int(p["dts"]) for p in packets if p["codec_type"] == "video")

    def payload(packet):
        return bytes.fromhex(
            "".join(
                line.split(":", 1)[1].strip().split("  ", 1)[0].replace(" ", "")
                for line in packet["data"].splitlines()
                if ":" in line
            )
        )

    origin = min(int(p["dts"]) for p in packets if p["codec_type"] == "video")
    clock, mux, output, expected = LegacyH264Clock(), TSMux(), bytearray(), {"video": [], "audio": []}
    # The synthetic transport timestamps are deliberately sourced from the
    # independently probed DTS (video) and PTS (audio) of the reference file.
    for packet in sorted(packets, key=lambda p: int(p["dts"])):
        kind = packet["codec_type"]
        raw = int(packet["dts"] if kind == "video" else packet["pts"])
        grid = 50_000_000 + (raw - origin) * 10_000_000 // 90_000
        if kind == "video":
            timed = clock.inspect(payload(packet), grid)
            assert timed is not None
            # This validates HRD DPB-offset parsing when Grid DTS is known by
            # construction. It does not prove the proprietary Grid clock's
            # meaning on a live SOOP connection.
            expected_pts = int(packet["pts"]) - origin + 90_000
            assert abs(timed["pts"] - expected_pts) <= 1
            assert abs(timed["dts"] - (int(packet["dts"]) - origin + 90_000)) <= 1
            output.extend(mux.add_video(timed["dts"], timed["pts"], payload(packet), idr=timed["idr"]))
        else:
            assert clock.origin_dts is not None  # Source has video decode preroll.
            output.extend(mux.add_audio(clock.audio_pts(grid), payload(packet)))
        expected[kind].append((int(packet["pts"]) - origin + 90_000, int(packet["dts"]) - origin + 90_000))
    remuxed = tmp_path / "remuxed.ts"
    remuxed.write_bytes(output)
    actual = {"video": [], "audio": []}
    for packet in probe(remuxed):
        actual[packet["codec_type"]].append((int(packet["pts"]), int(packet["dts"])))
    # A single output epoch for A/V. Allow one tick for integer Grid rescaling.
    for kind in ("video", "audio"):
        assert len(actual[kind]) == len(expected[kind])
        for observed, wanted in zip(actual[kind], expected[kind], strict=True):
            assert all(abs(a - b) <= 1 for a, b in zip(observed, wanted, strict=True)), (
                kind,
                observed,
                wanted,
            )


def sei_message(kind, payload):
    assert len(payload) < 255
    return escape(0x06, bytes((kind, len(payload))) + payload + b"\x80")


def sei_nal(*messages):
    assert all(len(payload) < 255 for _, payload in messages)
    body = b"".join(bytes((kind, len(payload))) + payload for kind, payload in messages) + b"\x80"
    return escape(0x06, body)


def timing_sei(cpb=0, dpb=0, pic_struct=None, cpb_bits=3, dpb_bits=3):
    b = Bits()
    b.put(cpb, cpb_bits)
    b.put(dpb, dpb_bits)
    if pic_struct is not None:
        b.put(pic_struct, 4)
        b.put(0)  # clock_timestamp_flag
    return sei_message(1, rbsp(b.finish(0x06)))


def buffering_period_sei(initial_delay=1, initial_offset=0, delay_bits=3, sps_id=0):
    b = Bits()
    b.ue(sps_id)
    b.put(initial_delay, delay_bits)
    b.put(initial_offset, delay_bits)
    return sei_message(0, b.finish_rbsp())


@pytest.mark.parametrize("pic_struct", [False, True])
def test_hrd_frame_picture_timing_and_required_per_au_metadata(pic_struct):
    seq = sps(hrd=True, pic_struct=pic_struct)
    clock = LegacyH264Clock()
    first = clock.inspect(
        annex(
            seq,
            pps(),
            buffering_period_sei(),
            timing_sei(dpb=2, pic_struct=0 if pic_struct else None),
            picture(idr=True),
        ),
        50_000_000,
    )
    assert first["pts"] - first["dts"] == 3000
    with pytest.raises(GridError, match="h264_hrd_timing_missing"):
        clock.inspect(annex(picture(1, 2)), 50_333_333)


def test_hrd_presentation_order_conflict_fails_closed():
    # A POC/VUI schedule is not substituted for contradictory per-picture HRD.
    # Without a proven alternate clock, reject rather than choose one signal.
    clock = LegacyH264Clock()
    seq = sps(mode=2, hrd=True)
    buffering_period = buffering_period_sei()
    first = clock.inspect(
        annex(seq, pps(), buffering_period, timing_sei(cpb=0, dpb=7), picture(mode=2, idr=True)),
        50_000_000,
    )
    assert first["poc"] == 0
    assert first["pts"] - first["dts"] == 10_500
    prior_state = deepcopy(clock.__dict__)
    with pytest.raises(GridError, match="h264_picture_order_conflict"):
        clock.inspect(annex(timing_sei(cpb=1, dpb=0), picture(1, mode=2)), 50_166_667)
    for key, value in prior_state.items():
        if key != "last_observation":
            assert clock.__dict__[key] == value
    assert clock.last_observation["h264_hrd_poc_order_conflicts"] == 1


@pytest.mark.parametrize("reorder", [None, 0, 1])
def test_grid_composition_does_not_inherit_hrd_poc_conflict(reorder):
    # Grid's explicit zero composition is independent of HRD DPB delay.
    seq = sps(mode=2, fixed=True, reorder=reorder, hrd=True)
    first = annex(
        seq,
        pps(),
        buffering_period_sei(),
        timing_sei(cpb=0, dpb=7),
        picture(idr=True, mode=2),
    )
    second = annex(
        timing_sei(cpb=1, dpb=0),
        picture(1, 2, mode=2),
    )
    output = []
    sink = MediaSink(output.append)
    sink.add(Frame(0xC748, media_body(first, "I", 1, 50_000_000)))

    sink.add(Frame(0xC748, media_body(second, "P", 2, 50_166_667)))
    sink.close()
    assert output
    assert sink.video_clock.timing_model == "grid_composition"
    assert sink.stats["video_output_frames"] == 2
    assert sink.stats["last_video_pts_90k"] == sink.stats["last_video_dts_90k"]


def test_single_grid_composition_idr_is_emitted_at_eof(tmp_path):
    seq = sps(mode=2, fixed=False, hrd=False)
    idr = annex(seq, pps(), picture(idr=True, mode=2))
    capture_dir = tmp_path / "grid-idr-eof"
    output = []
    sink = MediaSink(output.append, capture_dir=capture_dir)
    sink.add(Frame(0xC748, media_body(idr, "I", 1, 50_000_000)))

    sink.close()
    assert output
    assert sink.stats["video_output_frames"] == 1

    rows = [json.loads(line) for line in (capture_dir / "frames.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["frame"] == 1
    assert rows[0]["timing_model"] == "grid_composition"
    assert rows[0]["pts_90k"] >= rows[0]["dts_90k"]
    facts = json.loads((capture_dir / "grid-analysis.json").read_text())
    assert facts["video_output_frames"] == 1
    assert facts["stream_output_drain_status"] == "pending_at_media_close"
    assert "stream_output_drain_complete" not in facts


@pytest.mark.parametrize("nal_hrd,vcl_hrd", [(True, False), (False, True)])
def test_hrd_idr_without_required_buffering_period_is_rejected_transactionally(nal_hrd, vcl_hrd):
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_sei_buffering_period_missing"):
        clock.inspect(
            annex(sps(nal_hrd=nal_hrd, vcl_hrd=vcl_hrd), pps(), timing_sei(), picture(idr=True)),
            50_000_000,
            0,
        )
    assert clock.sps == {}
    assert clock.pps == {}


def test_buffering_period_is_rejected_when_sps_has_no_hrd():
    with pytest.raises(GridError, match="h264_sei_buffering_period_unexpected"):
        H264Clock().inspect(
            annex(sps(hrd=False), pps(), buffering_period_sei(), picture(idr=True)), 50_000_000, 0
        )


def test_buffering_period_rejects_zero_initial_cpb_delay_transactionally():
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_hrd_initial_delay_invalid"):
        clock.inspect(
            annex(
                sps(hrd=True),
                pps(),
                buffering_period_sei(initial_delay=0),
                timing_sei(),
                picture(idr=True),
            ),
            50_000_000,
            0,
        )
    assert clock.sps == {}
    assert clock.pps == {}


def test_hrd_recovery_point_requires_buffering_period():
    clock = H264Clock()
    seq = sps(mode=2, hrd=True)
    clock.inspect(
        annex(seq, pps(), buffering_period_sei(), timing_sei(cpb=0), picture(idr=True, mode=2)),
        50_000_000,
        0,
    )
    recovery = Bits()
    recovery.ue(0)  # recovery_frame_cnt
    recovery.put(1)  # exact_match_flag
    recovery.put(0)  # broken_link_flag
    recovery.put(0, 2)  # changing_slice_group_idc
    recovery_sei = sei_message(6, recovery.finish_rbsp())
    with pytest.raises(GridError, match="h264_sei_buffering_period_missing"):
        clock.inspect(annex(recovery_sei, timing_sei(cpb=1), picture(frame=1, poc=2, mode=2)), 50_082_000, 0)
    accepted = clock.inspect(
        annex(buffering_period_sei(), recovery_sei, timing_sei(cpb=1), picture(frame=1, poc=2, mode=2)),
        50_082_000,
        0,
    )
    assert accepted["pts"] == accepted["dts"]


def test_adult_capture_hrd_field_widths_and_delays_fail_closed():
    # Reproduce the observed syntax widths and delays without retaining live media.
    seq = sps(
        mode=2,
        hrd=True,
        tick_num=1000,
        tick_scale=120000,
        initial_delay_bits=24,
        cpb_delay_bits=16,
        dpb_delay_bits=6,
    )
    bp = Bits()
    bp.ue(0)  # seq_parameter_set_id
    bp.put(90000, 24)  # initial_cpb_removal_delay
    bp.put(0, 24)  # initial_cpb_removal_delay_offset
    period = sei_message(0, bp.finish_rbsp())
    first = timing_sei(cpb=240, dpb=18, cpb_bits=16, dpb_bits=6)
    second = timing_sei(cpb=2, dpb=2, cpb_bits=16, dpb_bits=6)
    clock = LegacyH264Clock()
    observed = clock.inspect(annex(seq, pps(), period, first, picture(mode=2, idr=True)), 50_000_000)
    assert observed["sps"]["clock_tick_100ns"] == Fraction(250_000, 3)
    assert observed["sei"]["buffering_period"] == {
        "sps_id": 0,
        "nal_hrd": [
            {
                "initial_cpb_removal_delay": 90000,
                "initial_cpb_removal_delay_offset": 0,
            }
        ],
    }
    assert observed["sei"]["picture_timing"] == {
        "cpb_removal_delay": 240,
        "dpb_output_delay": 18,
    }
    # The observed values are retained as a regression vector, but the candidate
    # output timing contradicts POC order and must not be emitted.
    tick = observed["sps"]["clock_tick_100ns"]
    next_timing = parse_sei(second, observed["sps"])["picture_timing"]
    assert next_timing == {"cpb_removal_delay": 2, "dpb_output_delay": 2}
    relative_presentation = (
        next_timing["cpb_removal_delay"]
        + next_timing["dpb_output_delay"]
        - observed["sei"]["picture_timing"]["dpb_output_delay"]
    ) * tick
    assert relative_presentation == -14 * tick
    with pytest.raises(GridError, match="h264_picture_order_conflict") as raised:
        clock.inspect(annex(second, picture(1, mode=2)), 50_170_000)
    facts = raised.value.safe_facts
    assert facts["h264_current_poc_type"] == 2
    assert facts["h264_current_poc"] == 2
    assert facts["h264_current_dts_90k"] == 91_530
    assert facts["h264_current_candidate_pts_90k"] == 93_030
    assert facts["h264_conflicting_reference_poc"] == 0
    assert facts["h264_conflicting_reference_pts_90k"] == 103_500
    assert facts["h264_current_grid_delta_100ns"] == 170_000
    assert facts["h264_hrd_poc_order_conflicts"] == 1
    assert facts["h264_cpb_removal_delay"] == 2
    assert facts["h264_dpb_output_delay"] == 2
    assert facts["h264_clock_tick_100ns_numerator"] == 250_000
    assert facts["h264_clock_tick_100ns_denominator"] == 3
    assert clock.last_dts == 50_000_000
    # The failed AU's diagnostic observation must not retain the preceding
    # IDR's slice type, POC, or timing fields after transactional rollback.
    assert clock.last_observation["h264_current_poc"] == 2
    assert clock.last_observation["h264_current_idr"] == 0
    assert clock.last_observation["h264_current_slice_type"] == "P"
    assert clock.last_observation["h264_cpb_removal_delay"] == 2
    assert clock.last_observation["h264_dpb_output_delay"] == 2
    assert clock.last_observation["h264_current_grid_delta_100ns"] == 170_000
    assert clock.last_observation["h264_conflicting_reference_pts_90k"] == 103_500


def test_picture_timing_sei_after_vcl_is_rejected_before_timing_application():
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_sei_after_vcl"):
        clock.inspect(annex(sps(hrd=True), pps(), picture(idr=True), timing_sei(dpb=2)), 50_000_000, 0)
    assert clock.sps == {}  # Rejected AU did not partially commit its SPS.


@pytest.mark.parametrize("later_sei_nal", [False, True])
def test_buffering_period_must_be_first_sei_payload_of_first_sei_nal(later_sei_nal):
    bp = Bits()
    bp.ue(0)  # seq_parameter_set_id
    bp.put(1, 3)  # nonzero initial_cpb_removal_delay
    bp.put(0, 3)  # initial_cpb_removal_delay_offset
    period = bp.finish_rbsp()
    if later_sei_nal:
        units = [sei_message(5, b"\x00"), sei_message(0, period)]
    else:
        units = [sei_nal((5, b"\x00"), (0, period))]

    clock = H264Clock()
    with pytest.raises(GridError, match="h264_sei_buffering_period_order_invalid"):
        clock.inspect(annex(sps(hrd=True), pps(), *units, picture(idr=True)), 50_000_000, 0)
    assert clock.sps == {}
    assert clock.pps == {}
    assert clock.parameter_definitions == {}


def test_buffering_period_wrong_sps_id_does_not_commit_parameter_sets():
    bp = Bits()
    bp.ue(1)  # SPS 1 does not exist in this access unit (which carries SPS 0).
    bp.put(1, 3)
    bp.put(0, 3)
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_sei_parameter_set_mismatch"):
        clock.inspect(
            annex(sps(hrd=True), pps(), sei_message(0, bp.finish_rbsp()), picture(idr=True)),
            50_000_000,
            0,
        )
    assert clock.sps == {}
    assert clock.pps == {}
    assert clock.parameter_definitions == {}


def test_parameter_only_access_unit_after_start_does_not_commit_candidate_pps():
    clock = H264Clock()
    clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 50_000_000, 0)
    prior_pps = clock.pps.copy()
    prior_definitions = clock.parameter_definitions.copy()
    prior_nals = clock.parameter_nals.copy()

    with pytest.raises(GridError, match="h264_vcl_missing"):
        clock.inspect(annex(pps(parameter_id=1)), 50_033_333, 0)

    assert clock.pps == prior_pps
    assert clock.parameter_definitions == prior_definitions
    assert clock.parameter_nals == prior_nals


def test_late_timing_rejection_rolls_back_codec_and_picture_state():
    clock = H264Clock()
    clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 50_000_000, 0)
    previous_state = deepcopy(clock.__dict__)

    with pytest.raises(GridError, match="grid_video_dts_not_increasing"):
        clock.inspect(annex(pps(parameter_id=1), picture(frame=1, mode=2, pps_id=1)), 50_000_000, 0)

    assert clock.__dict__ == previous_state
    assert clock.inspect(annex(pps(parameter_id=1), picture(frame=1, mode=2, pps_id=1)), 50_033_333, 0)


def test_valid_sps_is_retained_when_startup_picture_is_missing_its_pps():
    clock = H264Clock()
    assert (
        clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), picture(mode=2, pps_id=1)), 50_000_000, 0)
        is None
    )
    assert 0 in clock.sps
    assert not clock.pps

    observed = clock.inspect(
        annex(pps(parameter_id=1), picture(mode=2, pps_id=1, idr=True)),
        50_033_333,
        0,
    )
    assert observed["idr"]


@pytest.mark.parametrize("across_nals", [False, True])
def test_duplicate_picture_timing_is_rejected(across_nals):
    seq = sps(hrd=True)
    timing = timing_sei(dpb=2)
    units = [timing, timing] if across_nals else [escape(6, rbsp(timing)[:-1] * 2 + b"\x80")]
    with pytest.raises(GridError, match="h264_sei_timing_duplicate"):
        H264Clock().inspect(annex(seq, pps(), *units, picture(idr=True)), 50_000_000, 0)


@pytest.mark.parametrize("across_nals", [False, True])
def test_duplicate_buffering_period_is_rejected(across_nals):
    # SPS id 0, nonzero three-bit initial delay, zero offset, and valid payload alignment.
    period = buffering_period_sei()
    units = [period, period] if across_nals else [escape(6, rbsp(period)[:-1] * 2 + b"\x80")]
    with pytest.raises(GridError, match="h264_sei_buffering_period_order_invalid"):
        H264Clock().inspect(
            annex(sps(hrd=True), pps(), *units, timing_sei(), picture(idr=True)), 50_000_000, 0
        )


@pytest.mark.parametrize("pic_struct", list(range(1, 16)))
def test_nonzero_pic_struct_is_outside_supported_timing_subset(pic_struct):
    # Values 7 and 8 are valid frame-repeat structures; all nonzero values
    # remain unsupported because the muxer models one output picture per AU.
    with pytest.raises(GridError, match="h264_pic_struct_unsupported"):
        parse_sei(timing_sei(dpb=2, pic_struct=pic_struct), parse_sps(sps(hrd=True, pic_struct=True)))


@pytest.mark.parametrize("data", [b"\x01", b"\x01\xff", b"\x01\x02\x08\x80", b"\x01\x01\x0a"])
def test_truncated_sei_envelope_or_missing_rbsp_trailer_is_rejected(data):
    with pytest.raises(GridError):
        parse_sei(escape(6, data), parse_sps(sps(hrd=True)))


def test_malformed_picture_timing_alignment_cannot_supply_pts():
    # 000 010 is CPB=0, DPB=2; remaining 00 lacks payload_bit_equal_to_one.
    seq = sps(hrd=True)
    with pytest.raises(GridError):
        H264Clock().inspect(annex(seq, pps(), sei_message(1, b"\x08"), picture(idr=True)), 50_000_000, 0)


def test_poc2_normatively_forbidden_consecutive_nonreference_pictures():
    clock = H264Clock()
    clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 50_000_000, 0)
    for frame in (1, 2):
        unit = picture(frame, mode=2)
        payload = annex(bytes((0x01,)) + unit[1:])
        if frame == 1:
            clock.inspect(payload, 50_333_333, 0)
        else:
            with pytest.raises(GridError):
                clock.inspect(payload, 50_666_666, 0)


def test_poc2_reference_frame_num_gap_is_not_silently_accepted():
    clock = H264Clock()
    clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 50_000_000, 0)
    with pytest.raises(GridError):
        clock.inspect(annex(picture(2, mode=2)), 50_333_333, 0)


def test_hrd_new_idr_cannot_output_before_prior_sequence_pictures():
    clock = LegacyH264Clock()
    first = clock.inspect(
        annex(sps(hrd=True), pps(), buffering_period_sei(), timing_sei(cpb=0, dpb=6), picture(idr=True)),
        50_000_000,
    )
    assert first["pts"] == 99000
    # IDR resets picture order, not the transport or presentation clock.
    with pytest.raises(GridError, match="h264_picture_order_conflict"):
        clock.inspect(
            annex(buffering_period_sei(), timing_sei(cpb=2, dpb=0), picture(idr=True, idr_id=1)),
            50_333_333,
        )
    assert clock.last_pts == first["pts"]


def test_hrd_mmco5_cannot_restart_output_before_its_reset_picture():
    clock = LegacyH264Clock()
    clock.inspect(
        annex(sps(hrd=True), pps(), buffering_period_sei(), timing_sei(cpb=0), picture(idr=True)), 50_000_000
    )
    reset = clock.inspect(annex(timing_sei(cpb=2, dpb=6), picture(1, 4, mmco=True)), 50_333_333)
    assert reset["pts"] == 102000
    with pytest.raises(GridError, match="h264_picture_order_conflict"):
        clock.inspect(annex(timing_sei(cpb=4), picture(1, 2)), 50_666_666)


def test_hrd_timing_cannot_override_zero_reorder_conformance():
    clock = LegacyH264Clock()
    clock.inspect(
        annex(
            sps(hrd=True, reorder=0),
            pps(),
            buffering_period_sei(),
            timing_sei(cpb=0, dpb=2),
            picture(idr=True),
        ),
        50_000_000,
    )
    clock.inspect(annex(timing_sei(cpb=2, dpb=4), picture(1, 4)), 50_333_333)
    # PTS 96000 lies before the prior reference PTS 99000: one inversion, bound 0.
    with pytest.raises(GridError, match="h264_reorder_bound_invalid"):
        clock.inspect(annex(timing_sei(cpb=4), picture(2, 2, kind=1)), 50_666_666)


def test_escaped_cabac_zero_words_do_not_invalidate_header_rbsp():
    # Header-prefix probe followed by two cabac_zero_word values, correctly escaped.
    # H.264 7.4.1/9.3.4.6 represents each zero word as 00 00 03, including at EOF.
    nal = b"\x61\x80" + b"\0\0\x03" * 2
    assert rbsp(nal).startswith(b"\x80")


def test_parameterless_mid_gop_preroll_recovers_only_at_complete_idr():
    clock, mux = H264Clock(), TSMux()
    seq, picset = sps(mode=2, fixed=False, reorder=0), pps()
    discarded = [annex(picture(frame, mode=2)) for frame in (7, 8, 9)]
    discarded.extend([annex(seq), annex(picset), annex(picture(10, mode=2))])
    for index, payload in enumerate(discarded):
        assert clock.inspect(payload, 50_000_000 + index * 333_333, 0) is None
        assert clock.origin_dts is None
        assert clock.last_dts is None
        assert not clock.picture_times
        assert not mux.started
    assert clock.preroll_frames == len(discarded)
    assert clock.preroll_bytes == sum(map(len, discarded))
    assert clock.sps[0]["poc_type"] == 2
    assert clock.pps[0]["sps_id"] == 0
    payload = annex(seq, picset, picture(mode=2, idr=True))
    first = clock.inspect(payload, 52_000_000, 0)
    assert first["pts"] == first["dts"] == 90_000
    assert first["poc"] == 0
    assert mux.add_video(first["dts"], first["pts"], payload, idr=True)
    next_frame = clock.inspect(annex(picture(1, mode=2)), 52_333_333, 0)
    assert next_frame["dts"] == next_frame["pts"] == 93_000
    assert clock.preroll_frames == len(discarded)


def test_parameterless_idr_does_not_establish_a_start_boundary():
    clock = H264Clock()
    assert clock.inspect(annex(picture(mode=2, idr=True)), 50_000_000, 0) is None
    assert clock.origin_dts is None
    assert clock.preroll_frames == 1


def test_preroll_frame_limit_has_exact_bounded_exhaustion():
    clock = H264Clock()
    payload = annex(picture(7, mode=2))
    for index in range(600):
        assert clock.inspect(payload, 50_000_000 + index, 0) is None
    assert clock.preroll_frames == 600
    with pytest.raises(GridError, match="h264_start_boundary_unavailable"):
        clock.inspect(payload, 50_000_600, 0)
    assert clock.preroll_frames == 600
    assert clock.origin_dts is None


def test_preroll_byte_limit_has_exact_bounded_exhaustion():
    clock = H264Clock()
    # One reusable MiB of filler, not buffered by the clock, exercises actual inspect accounting.
    payload = b"\0\0\1\x0c" + b"\xff" * ((1 << 20) - 4)
    for index in range(32):
        assert clock.inspect(payload, 50_000_000 + index, 0) is None
    assert clock.preroll_bytes == 32 << 20
    with pytest.raises(GridError, match="h264_start_boundary_unavailable"):
        clock.inspect(b"\0", 50_000_032, 0)
    assert clock.preroll_bytes == 32 << 20
    assert clock.origin_dts is None


@pytest.mark.parametrize("direction", [-1, 1])
def test_preroll_span_limit_handles_forward_and_backward_raw_clock(direction):
    clock = H264Clock()
    payload = annex(picture(7, mode=2))
    base = 200_000_000
    assert clock.inspect(payload, base, 0) is None
    assert clock.inspect(payload, base + direction * 100_000_000, 0) is None
    with pytest.raises(GridError, match="h264_start_boundary_unavailable"):
        clock.inspect(payload, base + direction * 100_000_001, 0)
    assert clock.origin_dts is None


@pytest.mark.parametrize(
    "payload", [annex(sps(mode=2, fixed=False, reorder=0)), annex(pps()), annex(b"\x09\xf0")]
)
def test_header_only_chunk_is_preroll_before_start_but_fatal_after_start(payload):
    clock = H264Clock()
    assert clock.inspect(payload, 50_000_000, 0) is None
    clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 50_333_333, 0)
    count = clock.preroll_frames
    with pytest.raises(GridError, match="h264_vcl_missing"):
        clock.inspect(payload, 50_666_666, 0)
    assert clock.preroll_frames == count


def test_missing_parameter_set_is_fatal_after_start_not_more_preroll():
    clock = H264Clock()
    clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 50_000_000, 0)
    with pytest.raises(GridError, match="h264_parameter_set_missing"):
        clock.inspect(annex(picture(1, mode=2, pps_id=1)), 50_333_333, 0)
    assert clock.preroll_frames == 0


def test_idr_after_startup_deadline_does_not_bypass_preroll_limit():
    clock = H264Clock()
    assert clock.inspect(annex(picture(7, mode=2)), 50_000_000, 0) is None
    with pytest.raises(GridError, match="h264_start_boundary_unavailable"):
        clock.inspect(
            annex(sps(mode=2, fixed=False, reorder=0), pps(), picture(mode=2, idr=True)), 150_000_001, 0
        )


def test_cached_parameters_do_not_end_clock_preroll_before_mux_can_start():
    clock, mux = H264Clock(), TSMux()
    assert clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), pps()), 50_000_000, 0) is None
    payload = annex(picture(mode=2, idr=True))
    timing = clock.inspect(payload, 50_333_333, 0)
    if timing is not None:
        # Any accepted start must be outputtable through the unchanged production mux path.
        mux_payload = timing["decoder_prefix"] + payload
        assert mux.add_video(timing["dts"], timing["pts"], mux_payload, idr=timing["idr"])
    else:
        assert clock.origin_dts is None


def test_startup_prefix_contains_active_pps_even_when_another_pps_is_in_band():
    clock = H264Clock()
    active_pps = pps()
    assert clock.inspect(annex(sps(mode=2, fixed=False, reorder=0), active_pps), 50_000_000, 0) is None
    payload = annex(pps(parameter_id=1), picture(mode=2, idr=True))
    timed = clock.inspect(payload, 50_333_333, 0)
    output_units = nals(timed["decoder_prefix"] + payload)
    assert any(unit[0] & 31 == 8 and rbsp(unit) == rbsp(active_pps) for unit in output_units)
