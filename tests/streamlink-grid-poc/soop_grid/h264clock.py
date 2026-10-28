# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Bounded H.264 picture-order parser and provisional Grid-clock mapper.

POC 0/1/2 validates picture order; POC distance is not elapsed time. The
current output model treats the empirically 100-ns Grid value as decode time
and uses HRD Picture Timing SEI only for relative presentation delay. No
protocol evidence proves Grid↔HRD clock equality. Grid DTS monotonicity,
HRD-derived PTS/POC ordering and AAC sample-clock continuity are separate
checks. Missing or contradictory timing evidence fails closed without guessed
FPS. This mapping is an implementation model, not an official SOOP guarantee.
"""

from collections import deque
from dataclasses import dataclass
from fractions import Fraction

from .mpegts import nals
from .protocol import GridError


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def bit(self):
        if self.pos >= len(self.data) * 8:
            raise GridError("h264_header_truncated")
        b = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return b

    def bits(self, n):
        if not 0 <= n <= 32:
            raise GridError("h264_field_bounds")
        value = 0
        for _ in range(n):
            value = (value << 1) | self.bit()
        return value

    def ue(self):
        zeroes = 0
        while self.bit() == 0:
            zeroes += 1
            if zeroes > 31:
                raise GridError("h264_golomb_limit")
        return (1 << zeroes) - 1 + self.bits(zeroes)

    def se(self):
        value = self.ue()
        return (value + 1) // 2 if value & 1 else -(value // 2)

    def more_rbsp_data(self):
        """Whether syntax remains before the mandatory rbsp_trailing_bits."""
        total = len(self.data) * 8
        if self.pos >= total:
            return False
        # rbsp_trailing_bits is a single one bit followed only by zero alignment.
        if (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1 == 0:
            return True
        for pos in range(self.pos + 1, total):
            if (self.data[pos >> 3] >> (7 - (pos & 7))) & 1:
                return True
        return False

    def rbsp_trailing_bits(self):
        if self.pos >= len(self.data) * 8 or self.bit() != 1:
            raise GridError("h264_parameter_set_trailing_bits_invalid")
        while self.pos & 7:
            if self.bit() != 0:
                raise GridError("h264_parameter_set_trailing_bits_invalid")
        if self.pos != len(self.data) * 8:
            raise GridError("h264_parameter_set_trailing_bits_invalid")


def rbsp(nal):
    # Annex B trailing_zero_8bits and CABAC zero words are outside the header
    # syntax consumed here; encoded output still preserves the original payload.
    nal = nal.rstrip(b"\0")
    if len(nal) < 2 or nal[0] & 0x80 or nal[0] & 31 == 0:
        raise GridError("h264_nal_header_invalid")
    out, zeros = bytearray(), 0
    for index, value in enumerate(nal[1:], 1):
        if zeros >= 2 and value == 3:
            terminal_cabac = index + 1 == len(nal) and nal[0] & 31 in (1, 5)
            if not terminal_cabac and (index + 1 >= len(nal) or nal[index + 1] > 3):
                raise GridError("h264_emulation_prevention_invalid")
            zeros = 0
            continue
        if zeros >= 2 and value <= 2:
            raise GridError("h264_emulation_prevention_invalid")
        out.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(out)


def skip_scaling_list(reader, size):
    last = nxt = 8
    for _ in range(size):
        if nxt:
            nxt = (last + reader.se() + 256) % 256
        if nxt:
            last = nxt


def skip_hrd(reader):
    count = reader.ue()
    if count > 31:
        raise GridError("h264_hrd_limit")
    reader.bits(4)
    reader.bits(4)
    for _ in range(count + 1):
        reader.ue()
        reader.ue()
        reader.bit()
    return {
        "cpb_count": count + 1,
        "initial_delay_bits": reader.bits(5) + 1,
        "removal_delay_bits": reader.bits(5) + 1,
        "output_delay_bits": reader.bits(5) + 1,
        "time_offset_bits": reader.bits(5),
    }


_MAX_DPB_MBS = {
    10: 396,
    11: 900,
    12: 2376,
    13: 2376,
    20: 2376,
    21: 4752,
    22: 8100,
    30: 8100,
    31: 18000,
    32: 20480,
    40: 32768,
    41: 32768,
    42: 34816,
    50: 110400,
    51: 184320,
    52: 184320,
    60: 696320,
    61: 696320,
    62: 696320,
}

_SUPPORTED_PROFILES = (66, 77, 88, 100)
_HIGH_PROFILES = (100,)


def max_dpb_frames(level, width_mbs, height_map, profile=None, constraints=0):
    if level not in _MAX_DPB_MBS or not width_mbs or not height_map:
        raise GridError("h264_reorder_bound_unconfirmed")
    max_dpb_mbs = _MAX_DPB_MBS[level]
    # Level 1b is signaled as level_idc 11 plus constraint_set3_flag for
    # Baseline, Main and Extended profiles. Its MaxDpbMbs is 396, not level 1.1's 900.
    if level == 11 and profile in (66, 77, 88) and constraints & 0x10:
        max_dpb_mbs = 396
    picture_mbs = width_mbs * height_map
    frames = min(16, max_dpb_mbs // picture_mbs)
    if frames < 1:
        raise GridError("h264_level_dpb_invalid")
    return frames


def inferred_reorder(profile, level, constraints, width_mbs, height_map):
    """Return the Annex A DPB reordering upper bound when VUI omits it."""
    max_frames = max_dpb_frames(level, width_mbs, height_map, profile, constraints)
    if profile in (44, 86, 100, 110, 122, 244) and constraints & 0x10:
        return 0
    return max_frames


def parse_sps(nal):
    if len(nal) > 4096:
        raise GridError("h264_parameter_set_limit")
    reader = Reader(rbsp(nal))
    profile = reader.bits(8)
    constraints = reader.bits(8)
    level = reader.bits(8)
    if profile not in _SUPPORTED_PROFILES:
        raise GridError("h264_profile_unsupported")
    if constraints & 3:
        raise GridError("h264_reserved_sps_bits_nonzero")
    sps_id = reader.ue()
    if sps_id > 31:
        raise GridError("h264_sps_id_limit")

    chroma, separate, bit_depth_luma, bit_depth_chroma = 1, 0, 8, 8
    if profile in _HIGH_PROFILES:
        chroma = reader.ue()
        if chroma > 3:
            raise GridError("h264_chroma_unsupported")
        if chroma == 3:
            separate = reader.bit()
            if separate:
                raise GridError("h264_separate_colour_plane_unsupported")
        bit_depth_luma = reader.ue() + 8
        bit_depth_chroma = reader.ue() + 8
        if bit_depth_luma > 14 or bit_depth_chroma > 14:
            raise GridError("h264_bit_depth_unsupported")
        reader.bit()
        if reader.bit():
            for index in range(12 if chroma == 3 else 8):
                if reader.bit():
                    skip_scaling_list(reader, 16 if index < 6 else 64)

    log2_frame = reader.ue() + 4
    poc_type = reader.ue()
    if not 4 <= log2_frame <= 16 or poc_type not in (0, 1, 2):
        raise GridError("h264_poc_mode_unsupported")

    log2_poc = None
    delta_always_zero = False
    offset_non_ref = offset_top_bottom = 0
    offsets = ()
    if poc_type == 0:
        log2_poc = reader.ue() + 4
        if not 4 <= log2_poc <= 16:
            raise GridError("h264_poc_width_invalid")
    elif poc_type == 1:
        delta_always_zero = bool(reader.bit())
        offset_non_ref = reader.se()
        offset_top_bottom = reader.se()
        cycle = reader.ue()
        if cycle > 255:
            raise GridError("h264_poc_cycle_limit")
        offsets = tuple(reader.se() for _ in range(cycle))

    max_refs = reader.ue()
    if max_refs > 16:
        raise GridError("h264_reference_count_limit")
    gaps_allowed = bool(reader.bit())
    width_mbs, height_map = reader.ue() + 1, reader.ue() + 1
    if width_mbs > 1024 or height_map > 1024:
        raise GridError("h264_dimensions_invalid")
    frame_only = bool(reader.bit())
    if not frame_only:
        raise GridError("h264_interlaced_unsupported")
    reader.bit()  # direct_8x8_inference_flag
    crop = (0, 0, 0, 0)
    if reader.bit():
        crop = tuple(reader.ue() for _ in range(4))

    vui = parse_vui(reader) if reader.bit() else None
    reader.rbsp_trailing_bits()
    if vui is None:
        vui = {
            "fixed": False,
            "frame_duration": None,
            "max_reorder": None,
            "reorder": False,
            "max_buffer": None,
            "nal_hrd": None,
            "vcl_hrd": None,
            "low_delay_hrd": False,
            "pic_struct_present": False,
        }
    max_reorder = vui["max_reorder"]
    dpb_frames = max_dpb_frames(level, width_mbs, height_map, profile, constraints)
    if max_refs > dpb_frames:
        raise GridError("h264_reference_count_invalid")
    if vui["max_buffer"] is not None and (vui["max_buffer"] > dpb_frames or max_refs > vui["max_buffer"]):
        raise GridError("h264_dpb_bound_invalid")
    if not vui["reorder"]:
        max_reorder = inferred_reorder(profile, level, constraints, width_mbs, height_map)
    if max_reorder > 16:
        raise GridError("h264_reorder_limit")

    subw, subh = (1, 1) if separate or chroma in (0, 3) else ((2, 2) if chroma == 1 else (2, 1))
    left, right, top, bottom = crop
    width = width_mbs * 16 - subw * (left + right)
    height = height_map * 16 - subh * (top + bottom)
    if not 0 < width <= 16384 or not 0 < height <= 16384:
        raise GridError("h264_dimensions_invalid")

    frame_duration = vui["frame_duration"]
    return {
        "id": sps_id,
        "profile": profile,
        "constraints": constraints,
        "level": level,
        "chroma_format": chroma,
        "chroma_array_type": 0 if separate else chroma,
        "bit_depth_luma": bit_depth_luma,
        "bit_depth_chroma": bit_depth_chroma,
        "log2_frame_num": log2_frame,
        "max_frame_num": 1 << log2_frame,
        "poc_type": poc_type,
        "log2_poc_lsb": log2_poc,
        "delta_pic_order_always_zero": delta_always_zero,
        "offset_for_non_ref_pic": offset_non_ref,
        "offset_for_top_to_bottom_field": offset_top_bottom,
        "offset_for_ref_frame": offsets,
        "max_num_ref_frames": max_refs,
        "gaps_allowed": gaps_allowed,
        "width_mbs": width_mbs,
        "height_map": height_map,
        "width": width,
        "height": height,
        "frame_duration_100ns": frame_duration,
        "timing_fixed": bool(vui["fixed"]),
        "clock_tick_100ns": (Fraction(10_000_000 * vui["units"], vui["scale"]) if vui.get("units") else None),
        "nal_hrd": vui["nal_hrd"],
        "vcl_hrd": vui["vcl_hrd"],
        "low_delay_hrd": vui["low_delay_hrd"],
        "pic_struct_present": vui["pic_struct_present"],
        "max_reorder": max_reorder,
        "max_reorder_signaled": bool(vui["reorder"]),
        # max_num_reorder_frames may be established either by VUI or by the
        # Annex A profile/constraint inference above. Only a proven zero allows
        # timestamps without picture-reordering timing information.
        "max_reorder_zero_proven": max_reorder == 0,
        "fps_numerator": vui.get("scale"),
        "fps_denominator": 2 * vui["units"] if vui.get("units") else None,
    }


def parse_vui(reader):
    if reader.bit():
        aspect = reader.bits(8)
        if aspect == 255:
            reader.bits(16)
            reader.bits(16)
    if reader.bit():
        reader.bit()
    if reader.bit():
        reader.bits(3)
        reader.bit()
        if reader.bit():
            reader.bits(8)
            reader.bits(8)
            reader.bits(8)
    if reader.bit():
        reader.ue()
        reader.ue()

    timing = None
    if reader.bit():
        units, scale, fixed = reader.bits(32), reader.bits(32), bool(reader.bit())
        if not units or not scale:
            raise GridError("h264_vui_timing_invalid")
        timing = {"units": units, "scale": scale, "fixed": fixed}
    nal_hrd = skip_hrd(reader) if reader.bit() else None
    vcl_hrd = skip_hrd(reader) if reader.bit() else None
    low_delay_hrd = bool(reader.bit()) if nal_hrd or vcl_hrd else False
    pic_struct_present = bool(reader.bit())

    restriction = False
    max_reorder = None
    max_buffer = None
    if reader.bit():
        restriction = True
        reader.bit()
        for _ in range(4):
            reader.ue()
        max_reorder = reader.ue()
        max_buffer = reader.ue()
        if max_reorder > max_buffer:
            raise GridError("h264_reorder_bound_invalid")
    duration = None
    if timing and timing["fixed"]:
        duration = Fraction(20_000_000 * timing["units"], timing["scale"])
    return {
        "fixed": bool(timing and timing["fixed"]),
        "frame_duration": duration,
        "units": timing["units"] if timing else None,
        "scale": timing["scale"] if timing else None,
        "reorder": restriction,
        "max_reorder": max_reorder,
        "max_buffer": max_buffer,
        "nal_hrd": nal_hrd,
        "vcl_hrd": vcl_hrd,
        "low_delay_hrd": low_delay_hrd,
        "pic_struct_present": pic_struct_present,
    }


def parse_sei(nal, sps, *, first_sei_nal=True):
    """Read bounded, byte-framed SEI timing metadata; never retain user data."""
    data = rbsp(nal.rstrip(b"\0"))
    offset, messages = 0, 0
    result = {}
    while offset < len(data):
        if offset == len(data) - 1 and data[offset] == 0x80:
            return result
        messages += 1
        if messages > 256:
            raise GridError("h264_sei_message_limit")
        values = []
        for _ in range(2):
            value = 0
            while offset < len(data) and data[offset] == 255:
                value += 255
                offset += 1
            if offset >= len(data):
                raise GridError("h264_sei_truncated")
            value += data[offset]
            offset += 1
            values.append(value)
        kind, size = values
        if size > len(data) - offset:
            raise GridError("h264_sei_truncated")
        if kind not in (0, 1, 6):
            offset += size
            continue
        if size > 4096:
            raise GridError("h264_sei_timing_limit")
        reader = Reader(data[offset : offset + size])
        offset += size
        if kind == 0:
            if not first_sei_nal or messages != 1:
                raise GridError("h264_sei_buffering_period_order_invalid")
            if "buffering_period" in result:
                raise GridError("h264_sei_parameter_set_mismatch")
            if not (sps["nal_hrd"] or sps["vcl_hrd"]):
                raise GridError("h264_sei_buffering_period_unexpected")
            sps_id = reader.ue()
            if sps_id != sps["id"]:
                raise GridError("h264_sei_parameter_set_mismatch")
            buffering_period = {"sps_id": sps_id}
            for name, hrd in (("nal_hrd", sps["nal_hrd"]), ("vcl_hrd", sps["vcl_hrd"])):
                if hrd:
                    entries = []
                    for _ in range(hrd["cpb_count"]):
                        initial_delay = reader.bits(hrd["initial_delay_bits"])
                        initial_offset = reader.bits(hrd["initial_delay_bits"])
                        if initial_delay == 0:
                            raise GridError("h264_hrd_initial_delay_invalid")
                        entries.append(
                            {
                                "initial_cpb_removal_delay": initial_delay,
                                "initial_cpb_removal_delay_offset": initial_offset,
                            }
                        )
                    buffering_period[name] = entries
            result["buffering_period"] = buffering_period
            _sei_alignment(reader)
            continue
        if kind == 6:
            recovery_point = {
                "recovery_frame_cnt": reader.ue(),
                "exact_match_flag": reader.bit(),
                "broken_link_flag": reader.bit(),
                "changing_slice_group_idc": reader.bits(2),
            }
            _sei_alignment(reader)
            result["recovery_point"] = recovery_point
            continue
        if "picture_timing" in result:
            raise GridError("h264_sei_timing_duplicate")
        hrd = sps["nal_hrd"] or sps["vcl_hrd"]
        if sps["nal_hrd"] and sps["vcl_hrd"]:
            for key in ("removal_delay_bits", "output_delay_bits", "time_offset_bits"):
                if sps["nal_hrd"][key] != sps["vcl_hrd"][key]:
                    raise GridError("h264_hrd_parameters_ambiguous")
        timing = {}
        if hrd:
            timing["cpb_removal_delay"] = reader.bits(hrd["removal_delay_bits"])
            timing["dpb_output_delay"] = reader.bits(hrd["output_delay_bits"])
        if sps["pic_struct_present"]:
            pic_struct = reader.bits(4)
            if pic_struct != 0:
                raise GridError("h264_pic_struct_unsupported")
            if reader.bit():  # one clock_timestamp_flag for frame pictures
                reader.bits(2)
                reader.bit()
                reader.bits(5)
                full = reader.bit()
                reader.bit()
                reader.bit()
                reader.bits(8)
                if full:
                    reader.bits(6)
                    reader.bits(6)
                    reader.bits(5)
                elif reader.bit():
                    reader.bits(6)
                    if reader.bit():
                        reader.bits(6)
                        if reader.bit():
                            reader.bits(5)
                offset_bits = hrd["time_offset_bits"] if hrd else 24
                if offset_bits:
                    reader.bits(offset_bits)
        _sei_alignment(reader)
        result["picture_timing"] = timing
    raise GridError("h264_sei_trailing_bits_invalid")


def _validate_sei_buffering_period_order(nal, *, first_sei_nal):
    """Validate SEI framing and the BP position without mutating SPS state."""
    data = rbsp(nal)
    offset, messages = 0, 0
    while offset < len(data):
        if offset == len(data) - 1 and data[offset] == 0x80:
            return
        messages += 1
        if messages > 256:
            raise GridError("h264_sei_message_limit")
        values = []
        for _ in range(2):
            value = 0
            while offset < len(data) and data[offset] == 255:
                value += 255
                offset += 1
            if offset >= len(data):
                raise GridError("h264_sei_truncated")
            value += data[offset]
            offset += 1
            values.append(value)
        kind, size = values
        if size > len(data) - offset:
            raise GridError("h264_sei_truncated")
        if kind == 0 and (not first_sei_nal or messages != 1):
            raise GridError("h264_sei_buffering_period_order_invalid")
        offset += size
    raise GridError("h264_sei_trailing_bits_invalid")


def _sei_alignment(reader):
    remaining = len(reader.data) * 8 - reader.pos
    if remaining:
        if remaining > 7 or reader.bit() != 1 or any(reader.bit() for _ in range(remaining - 1)):
            raise GridError("h264_sei_payload_invalid")


def _validate_pps_qp_offsets(pps, sps):
    qp_bd_offset = 6 * (sps["bit_depth_luma"] - 8)
    if (
        not -(26 + qp_bd_offset) <= pps["pic_init_qp_minus26"] <= 25
        or not -26 <= pps["pic_init_qs_minus26"] <= 25
    ):
        raise GridError("h264_pps_qp_offset_invalid")
    if not -12 <= pps["chroma_qp_index_offset"] <= 12 or (
        pps["second_chroma_qp_index_offset"] is not None
        and not -12 <= pps["second_chroma_qp_index_offset"] <= 12
    ):
        raise GridError("h264_pps_chroma_qp_offset_invalid")


def _parse_pps_scaling_candidate(data, pos, count):
    reader = Reader(data)
    reader.pos = pos
    for index in range(count):
        if reader.bit():
            skip_scaling_list(reader, 16 if index < 6 else 64)
    second_chroma_qp = reader.se()
    syntax_end = reader.pos
    reader.rbsp_trailing_bits()
    return syntax_end, second_chroma_qp


def parse_pps(nal, sps_by_id=None):
    if len(nal) > 4096:
        raise GridError("h264_parameter_set_limit")
    reader = Reader(rbsp(nal))
    pps_id, sps_id = reader.ue(), reader.ue()
    if pps_id > 255 or sps_id > 31:
        raise GridError("h264_pps_id_limit")
    entropy = bool(reader.bit())
    bottom = bool(reader.bit())
    if reader.ue() != 0:
        raise GridError("h264_slice_groups_unsupported")
    ref_l0, ref_l1 = reader.ue(), reader.ue()
    if ref_l0 > 31 or ref_l1 > 31:
        raise GridError("h264_reference_count_limit")
    weighted_pred = bool(reader.bit())
    weighted_bipred = reader.bits(2)
    if weighted_bipred == 3:
        raise GridError("h264_weighted_bipred_reserved")
    pic_init_qp = reader.se()
    pic_init_qs = reader.se()
    chroma_qp = reader.se()
    deblocking = bool(reader.bit())
    reader.bit()
    redundant = bool(reader.bit())
    transform_8x8 = scaling_matrix = False
    scaling_lists = 0
    second_chroma_qp = None
    if reader.more_rbsp_data():
        transform_8x8 = bool(reader.bit())
        scaling_matrix = bool(reader.bit())
        if scaling_matrix:
            sps = (sps_by_id or {}).get(sps_id)
            if sps is not None:
                chroma = sps["chroma_format"]
                scaling_lists = 6 + (2 if chroma != 3 else 6) * transform_8x8
            elif not transform_8x8:
                # With 8x8 transforms disabled, all chroma formats have six
                # scaling lists, so PPS-before-SPS remains fully parseable.
                scaling_lists = 6
            else:
                # For 8x8 transforms the list count depends on chroma_format_idc.
                # Try both legal layouts and require exactly one complete RBSP parse;
                # the SPS, when it arrives, confirms the selected layout below.
                candidates = []
                for count in (8, 12):
                    try:
                        candidates.append(
                            (count, *_parse_pps_scaling_candidate(reader.data, reader.pos, count))
                        )
                    except GridError:
                        pass
                if len(candidates) != 1:
                    raise GridError("h264_pps_scaling_layout_unconfirmed")
                scaling_lists, reader.pos, second_chroma_qp = candidates[0]
            if sps is not None or not transform_8x8:
                for index in range(scaling_lists):
                    if reader.bit():
                        skip_scaling_list(reader, 16 if index < 6 else 64)
                second_chroma_qp = reader.se()
        else:
            second_chroma_qp = reader.se()
    reader.rbsp_trailing_bits()
    parsed = {
        "id": pps_id,
        "sps_id": sps_id,
        "entropy_coding": entropy,
        "bottom_poc": bottom,
        "ref_l0": ref_l0,
        "ref_l1": ref_l1,
        "weighted_pred": weighted_pred,
        "weighted_bipred": weighted_bipred,
        "deblocking_filter_control": deblocking,
        "redundant": redundant,
        "pic_init_qp_minus26": pic_init_qp,
        "pic_init_qs_minus26": pic_init_qs,
        "chroma_qp_index_offset": chroma_qp,
        "transform_8x8": transform_8x8,
        "scaling_matrix": scaling_matrix,
        "scaling_lists": scaling_lists,
        "second_chroma_qp_index_offset": second_chroma_qp,
    }
    sps = (sps_by_id or {}).get(sps_id)
    if sps is not None:
        _validate_pps_qp_offsets(parsed, sps)
    elif not -62 <= pic_init_qp <= 25 or not -26 <= pic_init_qs <= 25:
        # Until the referenced SPS arrives, the largest supported luma bit
        # depth (14) is the only safe bound available for pic_init_qp_minus26.
        raise GridError("h264_pps_qp_offset_invalid")
    elif not -12 <= chroma_qp <= 12 or (second_chroma_qp is not None and not -12 <= second_chroma_qp <= 12):
        raise GridError("h264_pps_chroma_qp_offset_invalid")
    return parsed


def _skip_ref_pic_list_modification(reader, slice_kind):
    lists = (0, 1) if slice_kind == "B" else (0,)
    for _ in lists:
        if not reader.bit():
            continue
        for _ in range(64):
            operation = reader.ue()
            if operation == 3:
                break
            if operation in (0, 1):
                reader.ue()
            elif operation == 2:
                reader.ue()
            else:
                raise GridError("h264_ref_list_modification_invalid")
        else:
            raise GridError("h264_ref_list_modification_limit")


def _skip_weight_list(reader, count, chroma_array_type):
    for _ in range(count):
        if reader.bit():
            reader.se()
            reader.se()
        if chroma_array_type and reader.bit():
            for _ in range(2):
                reader.se()
                reader.se()


def _skip_pred_weight_table(reader, sps, refs_l0, refs_l1, slice_kind):
    reader.ue()
    if sps["chroma_array_type"]:
        reader.ue()
    _skip_weight_list(reader, refs_l0, sps["chroma_array_type"])
    if slice_kind == "B":
        _skip_weight_list(reader, refs_l1, sps["chroma_array_type"])


def _parse_dec_ref_pic_marking(reader, nal_type, nal_ref_idc):
    if nal_type == 5:
        reader.bit()
        reader.bit()
        return False
    if not nal_ref_idc:
        return False
    if not reader.bit():
        return False
    for _ in range(64):
        operation = reader.ue()
        if operation == 0:
            return False
        if operation in (1, 3):
            reader.ue()
        if operation == 2:
            reader.ue()
        if operation in (3, 6):
            reader.ue()
        if operation == 4:
            reader.ue()
        if operation == 5:
            return True
        if operation > 6:
            raise GridError("h264_memory_management_operation_invalid")
    raise GridError("h264_memory_management_operation_limit")


@dataclass(frozen=True)
class SliceHeader:
    first_mb: int
    pps_id: int
    frame_num: int
    poc_lsb: int | None
    delta_bottom: int
    delta_poc0: int
    delta_poc1: int
    slice_kind: str
    idr: bool
    nal_ref_idc: int
    mmco5: bool
    idr_pic_id: int | None


def parse_slice(nal, sps, pps):
    nal_type = nal[0] & 31
    if nal_type not in (1, 5):
        raise GridError("h264_slice_nal_type_invalid")
    nal_ref_idc = (nal[0] >> 5) & 3
    if nal_type == 5 and nal_ref_idc == 0:
        raise GridError("h264_idr_reference_invalid")
    reader = Reader(rbsp(nal))
    first_mb = reader.ue()
    slice_type = reader.ue()
    if slice_type > 9:
        raise GridError("h264_slice_type_invalid")
    slice_type %= 5
    pps_id = reader.ue()
    if pps_id != pps["id"]:
        raise GridError("h264_slice_parameter_set_mismatch")
    frame_num = reader.bits(sps["log2_frame_num"])
    idr = nal_type == 5
    idr_pic_id = None
    if idr:
        idr_pic_id = reader.ue()
        if idr_pic_id > 65535:
            raise GridError("h264_idr_picture_id_limit")

    poc_lsb, delta_bottom, delta0, delta1 = None, 0, 0, 0
    if sps["poc_type"] == 0:
        poc_lsb = reader.bits(sps["log2_poc_lsb"])
        if pps["bottom_poc"]:
            delta_bottom = reader.se()
    elif sps["poc_type"] == 1 and not sps["delta_pic_order_always_zero"]:
        delta0 = reader.se()
        if pps["bottom_poc"]:
            delta1 = reader.se()
    if pps["redundant"]:
        reader.ue()

    slice_kind = ("P", "B", "I", "SP", "SI")[slice_type]
    if slice_kind in ("SP", "SI"):
        raise GridError("h264_switching_slice_unsupported")
    if slice_kind == "B":
        reader.bit()  # direct_spatial_mv_pred_flag

    refs_l0 = pps["ref_l0"] + 1
    refs_l1 = pps["ref_l1"] + 1
    if slice_kind in ("P", "B") and reader.bit():
        refs_l0 = reader.ue() + 1
        if slice_kind == "B":
            refs_l1 = reader.ue() + 1
        if refs_l0 > 32 or refs_l1 > 32:
            raise GridError("h264_reference_count_limit")

    if slice_kind not in ("I",):
        _skip_ref_pic_list_modification(reader, slice_kind)
    if (pps["weighted_pred"] and slice_kind == "P") or (pps["weighted_bipred"] == 1 and slice_kind == "B"):
        _skip_pred_weight_table(reader, sps, refs_l0, refs_l1, slice_kind)

    mmco5 = _parse_dec_ref_pic_marking(reader, nal_type, nal_ref_idc)
    return SliceHeader(
        first_mb,
        pps_id,
        frame_num,
        poc_lsb,
        delta_bottom,
        delta0,
        delta1,
        slice_kind,
        idr,
        nal_ref_idc,
        mmco5,
        idr_pic_id,
    )


def _slice_pps_id(nal):
    reader = Reader(rbsp(nal))
    reader.ue()
    reader.ue()
    return reader.ue()


class H264Clock:
    def __init__(self):
        self.sps = {}
        self.pps = {}
        self.prev_msb = 0
        self.prev_lsb = 0
        self.prev_frame_num = 0
        self.prev_frame_num_offset = 0
        self.epoch_poc = None
        self.epoch_pts = None
        self.origin_dts = None
        self.reorder_lead = None
        self.last_dts = None
        self.last_dts_out = None
        self.last_pts = None
        self.last_poc = None
        self.max_pts = None
        self.duration = None
        self.reorder = None
        self.sps_signature = None
        self.parameter_definitions = {}
        self.parameter_nals = {}
        self.timing_model = None
        self.picture_times = deque(maxlen=64)
        self.reset_picture_order = False
        self.last_observation = {}
        self.preroll_frames = 0
        self.preroll_bytes = 0
        self.preroll_timestamp = None
        self.previous_reference_frame_num = None
        self.previous_nonreference = False
        self.output_epoch_floor = None
        self.media_origin_hint = None
        self.last_picture_idr = False
        self.last_slice_kind = None

    def observe_audio(self, timestamp):
        if type(timestamp) is not int or not 0 <= timestamp < 1 << 64:
            raise GridError("media_timestamp_bounds")
        if self.origin_dts is None:
            self.media_origin_hint = (
                timestamp if self.media_origin_hint is None else min(self.media_origin_hint, timestamp)
            )

    def _preroll(self, timestamp, size):
        self.preroll_frames += 1
        self.preroll_bytes += size
        if self.preroll_timestamp is None:
            self.preroll_timestamp = timestamp
        if (
            self.preroll_frames > 600
            or self.preroll_bytes > 32 << 20
            or abs(timestamp - self.preroll_timestamp) > 100_000_000
        ):
            raise GridError("h264_start_boundary_unavailable")
        return None

    @staticmethod
    def _sps_signature(info):
        return tuple(sorted(info.items()))

    def _picture_order(self, header, sps):
        frame_num = header.frame_num
        if header.idr:
            self.prev_msb = self.prev_lsb = 0
            self.prev_frame_num = self.prev_frame_num_offset = 0

        if sps["poc_type"] == 0:
            max_lsb = 1 << sps["log2_poc_lsb"]
            lsb = header.poc_lsb
            if lsb is None:
                raise GridError("h264_poc_fields_missing")
            if lsb < self.prev_lsb and self.prev_lsb - lsb >= max_lsb // 2:
                msb = self.prev_msb + max_lsb
            elif lsb > self.prev_lsb and lsb - self.prev_lsb > max_lsb // 2:
                msb = self.prev_msb - max_lsb
            else:
                msb = self.prev_msb
            top = msb + lsb
            bottom = top + header.delta_bottom
            poc = min(top, bottom)
            if header.nal_ref_idc:
                if header.mmco5:
                    # H.264 8.2.1.1: after MMCO 5 the previous MSB is zero,
                    # while previous LSB is the current TopFieldOrderCnt.
                    self.prev_msb, self.prev_lsb = 0, top
                else:
                    self.prev_msb, self.prev_lsb = msb, lsb
            return poc

        if header.idr:
            frame_num_offset = 0
        else:
            frame_num_offset = self.prev_frame_num_offset
            if self.prev_frame_num > frame_num:
                frame_num_offset += sps["max_frame_num"]

        if sps["poc_type"] == 1:
            cycle = sps["offset_for_ref_frame"]
            abs_frame_num = frame_num_offset + frame_num if cycle else 0
            if not header.nal_ref_idc and abs_frame_num > 0:
                abs_frame_num -= 1
            expected = 0
            if abs_frame_num > 0 and cycle:
                cycle_count = (abs_frame_num - 1) // len(cycle)
                frame_in_cycle = (abs_frame_num - 1) % len(cycle)
                expected = cycle_count * sum(cycle) + sum(cycle[: frame_in_cycle + 1])
            if not header.nal_ref_idc:
                expected += sps["offset_for_non_ref_pic"]
            top = expected + header.delta_poc0
            bottom = top + sps["offset_for_top_to_bottom_field"] + header.delta_poc1
            poc = min(top, bottom)
        else:
            poc = 0 if header.idr else 2 * (frame_num_offset + frame_num) - (not bool(header.nal_ref_idc))

        if header.mmco5:
            self.prev_frame_num = self.prev_frame_num_offset = 0
        else:
            self.prev_frame_num = frame_num
            self.prev_frame_num_offset = frame_num_offset
        return int(poc)

    def inspect(self, payload, grid_timestamp):
        # Parsing and timeline validation mutate POC, epoch, and parameter-set
        # state. A rejected AU must leave the prior accepted reference state
        # intact, including failures discovered after syntax parsing.
        state = self.__dict__.copy()
        state["picture_times"] = deque(self.picture_times, maxlen=self.picture_times.maxlen)
        try:
            return self._inspect(payload, grid_timestamp)
        except BaseException as error:
            safe_facts = error.safe_facts if isinstance(error, GridError) else None
            self.__dict__.clear()
            self.__dict__.update(state)
            if safe_facts:
                self.last_observation = {**self.last_observation, **safe_facts}
            raise

    def _inspect(self, payload, grid_timestamp):
        if type(grid_timestamp) is not int or not 0 <= grid_timestamp < (1 << 64):
            raise GridError("media_timestamp_bounds")
        if self.origin_dts is None and self.preroll_timestamp is not None:
            if (
                self.preroll_frames + 1 > 600
                or self.preroll_bytes + len(payload) > 32 << 20
                or abs(grid_timestamp - self.preroll_timestamp) > 100_000_000
            ):
                raise GridError("h264_start_boundary_unavailable")
        units = nals(payload, max_units=256)
        # Validate AU ordering before parsing parameter sets, because parsing
        # them mutates the clock's configuration state. A malformed AU must
        # not partially update state before it is rejected.
        seen_primary_picture = False
        sei_nal_count = 0
        for nal in units:
            kind = nal[0] & 31
            if kind in (1, 5):
                seen_primary_picture = True
            elif kind == 6:
                if seen_primary_picture:
                    raise GridError("h264_sei_after_vcl")
                _validate_sei_buffering_period_order(nal, first_sei_nal=sei_nal_count == 0)
                sei_nal_count += 1
            elif seen_primary_picture and kind in (7, 8):
                raise GridError("h264_access_unit_boundary_invalid")
        vcl = []
        sei_units = []
        in_band_parameter_keys = set()
        candidate_sps = self.sps.copy()
        candidate_pps = self.pps.copy()
        candidate_definitions = self.parameter_definitions.copy()
        candidate_nals = self.parameter_nals.copy()
        candidate_signature = self.sps_signature

        def commit_parameter_sets():
            self.sps = candidate_sps
            self.pps = candidate_pps
            self.parameter_definitions = candidate_definitions
            self.parameter_nals = candidate_nals
            self.sps_signature = candidate_signature

        for nal in units:
            kind = nal[0] & 31
            if kind == 7:
                info = parse_sps(nal)
                for existing_pps in candidate_pps.values():
                    if existing_pps["sps_id"] == info["id"]:
                        _validate_pps_qp_offsets(existing_pps, info)
                        if existing_pps["scaling_matrix"]:
                            expected_lists = (
                                6 + (2 if info["chroma_format"] != 3 else 6) * existing_pps["transform_8x8"]
                            )
                            if existing_pps["scaling_lists"] != expected_lists:
                                raise GridError("h264_pps_scaling_layout_mismatch")
                key = (7, info["id"])
                in_band_parameter_keys.add(key)
                definition = rbsp(nal)
                if key in candidate_definitions and candidate_definitions[key] != definition:
                    raise GridError("h264_codec_configuration_changed")
                candidate_definitions[key] = definition
                candidate_nals[key] = nal
                signature = self._sps_signature(info)
                if candidate_signature is not None and signature != candidate_signature:
                    raise GridError("h264_codec_configuration_changed")
                candidate_signature = signature
                candidate_sps[info["id"]] = info
            elif kind == 8:
                pps = parse_pps(nal, candidate_sps)
                key = (8, pps["id"])
                in_band_parameter_keys.add(key)
                definition = rbsp(nal)
                if key in candidate_definitions and candidate_definitions[key] != definition:
                    raise GridError("h264_codec_configuration_changed")
                candidate_definitions[key] = definition
                candidate_nals[key] = nal
                previous = candidate_pps.get(pps["id"])
                if previous is not None and previous != pps:
                    raise GridError("h264_codec_configuration_changed")
                candidate_pps[pps["id"]] = pps
            elif kind in (2, 3, 4, 19, 20, 21):
                raise GridError("h264_vcl_partition_or_extension_unsupported")
            elif kind in (1, 5):
                vcl.append(nal)
            elif kind == 6:
                sei_units.append(nal)
        if not vcl:
            if self.origin_dts is None:
                result = self._preroll(grid_timestamp, len(payload))
                commit_parameter_sets()
                return result
            raise GridError("h264_vcl_missing")
        if len(vcl) > 8:
            raise GridError("h264_slice_count_limit")

        parsed = []
        for nal in vcl:
            pps_id = _slice_pps_id(nal)
            pps = candidate_pps.get(pps_id)
            sps = candidate_sps.get(pps["sps_id"]) if pps else None
            if not sps:
                if self.origin_dts is None:
                    result = self._preroll(grid_timestamp, len(payload))
                    commit_parameter_sets()
                    return result
                raise GridError("h264_parameter_set_missing")
            parsed.append((parse_slice(nal, sps, pps), sps, pps))
        header, sps, pps = parsed[0]
        if (sps["nal_hrd"] or sps["vcl_hrd"]) and sps["low_delay_hrd"]:
            # Annex C.1.2 C-11 can shift actual CPB removal beyond nominal
            # cpb_removal_delay when the AU arrives too late. We do not know
            # the HRD arrival-time schedule from Grid's opaque timestamp, so
            # this subset cannot safely map that mode onto DTS.
            raise GridError("h264_hrd_low_delay_timing_unsupported", safe_facts={"h264_low_delay_hrd": 1})
        if sum(item.first_mb == 0 for item, _, _ in parsed) != 1:
            raise GridError("h264_access_unit_boundary_invalid")
        sei = {}
        for index, nal in enumerate(sei_units):
            metadata = parse_sei(nal, sps, first_sei_nal=index == 0)
            if metadata.keys() & sei.keys():
                raise GridError("h264_sei_timing_duplicate")
            sei.update(metadata)
        hrd_present = bool(sps["nal_hrd"] or sps["vcl_hrd"])
        if hrd_present and (header.idr or "recovery_point" in sei) and "buffering_period" not in sei:
            # H.264 V15 Annex D.2.2 requires buffering_period for IDR and
            # recovery-point access units when either HRD is present. Do not
            # infer an HRD epoch from picture_timing alone.
            raise GridError("h264_sei_buffering_period_missing")
        picture_identity = (
            header.pps_id,
            header.frame_num,
            header.poc_lsb,
            header.delta_bottom,
            header.delta_poc0,
            header.delta_poc1,
            header.slice_kind,
            header.idr,
            header.nal_ref_idc,
            header.mmco5,
            header.idr_pic_id,
        )
        if any(
            (
                other.pps_id,
                other.frame_num,
                other.poc_lsb,
                other.delta_bottom,
                other.delta_poc0,
                other.delta_poc1,
                other.slice_kind,
                other.idr,
                other.nal_ref_idc,
                other.mmco5,
                other.idr_pic_id,
            )
            != picture_identity
            or other_sps != sps
            or other_pps != pps
            for other, other_sps, other_pps in parsed[1:]
        ):
            raise GridError("h264_multiple_pictures_per_grid_chunk")

        # Commit in-band codec configuration only after slice and SEI syntax
        # (including buffering-period identity) has been fully validated.
        commit_parameter_sets()

        self.last_observation = {
            "h264_current_poc_type": sps["poc_type"],
            "h264_current_fixed_vui": int(sps["timing_fixed"]),
            "h264_current_idr": int(header.idr),
            "h264_current_mmco5": int(header.mmco5),
            "h264_current_slice_type": header.slice_kind,
            "h264_hrd_present": int(bool(sps["nal_hrd"] or sps["vcl_hrd"])),
            "h264_sei_timing_present": int("picture_timing" in sei),
        }
        if self.last_dts is not None:
            self.last_observation["h264_current_grid_delta_100ns"] = grid_timestamp - self.last_dts
        if self.epoch_poc is None and not header.idr:
            return self._preroll(grid_timestamp, len(payload))
        if header.idr and header.frame_num != 0:
            raise GridError("h264_frame_num_invalid")
        if not header.idr and self.previous_reference_frame_num is not None:
            expected_frame_num = (self.previous_reference_frame_num + 1) % sps["max_frame_num"]
            if not sps["gaps_allowed"] and header.frame_num != expected_frame_num:
                raise GridError("h264_frame_num_gap")
            if sps["poc_type"] == 2 and not header.nal_ref_idc and self.previous_nonreference:
                raise GridError("h264_poc2_nonreference_sequence_invalid")
        poc = self._picture_order(header, sps)
        self.last_observation["h264_current_poc"] = poc

        first_picture = self.origin_dts is None
        if header.idr:
            if self.origin_dts is None:
                self.origin_dts = (
                    min(grid_timestamp, self.media_origin_hint)
                    if self.media_origin_hint is not None
                    else grid_timestamp
                )
                self.reorder_lead = 0
            self.epoch_poc = poc
            self.output_epoch_floor = self.max_pts
            self.picture_times.clear()
        elif self.reset_picture_order:
            self.output_epoch_floor = self.max_pts
            self.picture_times.clear()
            self.epoch_poc = poc
        self.reset_picture_order = header.mmco5

        if self.last_dts is not None and grid_timestamp <= self.last_dts:
            raise GridError("grid_video_dts_not_increasing")
        dts = self._rescale(grid_timestamp - self.origin_dts) + 90_000
        if self.last_dts_out is not None and dts <= self.last_dts_out:
            raise GridError("grid_video_dts_not_increasing")

        picture_timing = sei.get("picture_timing", {})
        for key in ("cpb_removal_delay", "dpb_output_delay"):
            if key in picture_timing:
                self.last_observation["h264_" + key] = picture_timing[key]
        if sps["nal_hrd"] or sps["vcl_hrd"]:
            if "dpb_output_delay" not in picture_timing:
                raise GridError("h264_hrd_timing_missing")
            if sps["clock_tick_100ns"] is None:
                raise GridError("h264_hrd_clock_unconfirmed")
            cpb_delay = picture_timing.get("cpb_removal_delay")
            if cpb_delay is None:
                raise GridError("h264_hrd_timing_missing")
            # Grid supplies only a DTS candidate; HRD's DPB output delay
            # supplies relative PTS. Contradictory ordering fails closed.
            model = "hrd_sei"
            pts = dts + self._rescale(picture_timing["dpb_output_delay"] * sps["clock_tick_100ns"])
        elif sps["max_reorder_zero_proven"]:
            model = "grid_no_reorder"
            if header.mmco5:
                raise GridError("h264_mmco5_timing_unsupported")
            if not header.idr and self.last_poc is not None and poc <= self.last_poc:
                raise GridError("h264_picture_order_conflict")
            pts = dts
        else:
            raise GridError("h264_reorder_timing_unconfirmed")
        if self.timing_model is not None and self.timing_model != model:
            raise GridError("h264_timing_changed")
        self.timing_model = model
        if pts - dts >= 1 << 32:
            raise GridError("h264_hrd_delay_limit")
        if self.output_epoch_floor is not None and pts <= self.output_epoch_floor:
            facts = {
                "h264_current_poc_type": sps["poc_type"],
                "h264_current_poc": poc,
                "h264_current_dts_90k": dts,
                "h264_current_candidate_pts_90k": pts,
                "h264_previous_output_epoch_floor_90k": self.output_epoch_floor,
                "h264_current_grid_delta_100ns": grid_timestamp - self.last_dts,
            }
            if model == "hrd_sei":
                facts["h264_hrd_poc_order_conflicts"] = 1
                facts["h264_cpb_removal_delay"] = picture_timing["cpb_removal_delay"]
                facts["h264_dpb_output_delay"] = picture_timing["dpb_output_delay"]
                tick = sps["clock_tick_100ns"]
                facts["h264_clock_tick_100ns_numerator"] = tick.numerator
                facts["h264_clock_tick_100ns_denominator"] = tick.denominator
            raise GridError("h264_picture_order_conflict", safe_facts=facts)
        if sum(pts < old_pts for _, old_pts in self.picture_times) > sps["max_reorder"]:
            raise GridError("h264_reorder_bound_invalid")
        for old_poc, old_pts in self.picture_times:
            if (poc < old_poc and pts >= old_pts) or (poc > old_poc and pts <= old_pts):
                # Preserve observations from the rejected AU itself. inspect()
                # rolls parser/timeline state back on failure, so without these
                # facts diagnostics would misleadingly describe the previous
                # accepted picture as the failing one.
                facts = {
                    "h264_current_poc_type": sps["poc_type"],
                    "h264_current_fixed_vui": int(sps["timing_fixed"]),
                    "h264_current_idr": int(header.idr),
                    "h264_current_mmco5": int(header.mmco5),
                    "h264_current_slice_type": header.slice_kind,
                    "h264_current_poc": poc,
                    "h264_current_dts_90k": dts,
                    "h264_current_candidate_pts_90k": pts,
                    "h264_conflicting_reference_poc": old_poc,
                    "h264_conflicting_reference_pts_90k": old_pts,
                    "h264_current_grid_delta_100ns": grid_timestamp - self.last_dts,
                    "h264_hrd_present": int(bool(sps["nal_hrd"] or sps["vcl_hrd"])),
                    "h264_sei_timing_present": int("picture_timing" in sei),
                }
                if model == "hrd_sei":
                    facts["h264_hrd_poc_order_conflicts"] = 1
                    facts["h264_cpb_removal_delay"] = picture_timing["cpb_removal_delay"]
                    facts["h264_dpb_output_delay"] = picture_timing["dpb_output_delay"]
                    tick = sps["clock_tick_100ns"]
                    facts["h264_clock_tick_100ns_numerator"] = tick.numerator
                    facts["h264_clock_tick_100ns_denominator"] = tick.denominator
                raise GridError("h264_picture_order_conflict", safe_facts=facts)
        self.picture_times.append((poc, pts))
        self.previous_nonreference = not bool(header.nal_ref_idc)
        if header.nal_ref_idc:
            self.previous_reference_frame_num = 0 if header.mmco5 else header.frame_num

        self.last_dts, self.last_dts_out, self.last_pts, self.last_poc = grid_timestamp, dts, pts, poc
        self.last_picture_idr = header.idr
        self.last_slice_kind = header.slice_kind
        self.max_pts = pts if self.max_pts is None else max(self.max_pts, pts)
        return {
            "dts": dts,
            # None marks a PTS that must be resolved by MediaSink's bounded
            # POC/VUI scheduler; the internal dts placeholder is only used
            # for transactional state and is never serialized as a PTS.
            "pts": pts,
            "poc": poc,
            "slice_type": header.slice_kind,
            "idr": header.idr,
            "sps": sps,
            "sei": sei,
            "timing_model": model,
            "decoder_prefix": (
                b"".join(
                    b"\0\0\0\1" + self.parameter_nals[(kind, parameter_id)]
                    for kind, parameter_id in ((7, sps["id"]), (8, pps["id"]))
                    if (kind, parameter_id) not in in_band_parameter_keys
                )
                if first_picture
                else b""
            ),
            "units": units,
        }

    @staticmethod
    def _rescale(timestamp_100ns):
        return round(Fraction(timestamp_100ns * 90_000, 10_000_000))

    def audio_pts(self, timestamp_100ns):
        if self.origin_dts is None:
            return None
        return self._rescale(timestamp_100ns - self.origin_dts) + 90_000
