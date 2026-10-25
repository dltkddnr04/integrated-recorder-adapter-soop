# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Strict progressive H.264 POC parser for Grid decode-order chunks.

Supported subset: Annex-B, frame-only pictures, pic_order_cnt_type 0, one
constant-frame-rate VUI, level-derived reorder bound when VUI omits it, and
non-FMO PPS. Unsupported/missing timing metadata is rejected; frame rate is
never guessed.
"""

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


def rbsp(nal):
    if len(nal) < 2 or nal[0] & 0x80:
        raise GridError("h264_nal_header_invalid")
    out, zeros = bytearray(), 0
    for b in nal[1:]:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def skip_scaling_list(r, size):
    last = nxt = 8
    for _ in range(size):
        if nxt:
            nxt = (last + r.se() + 256) % 256
        if nxt:
            last = nxt


def skip_hrd(r):
    count = r.ue()
    if count > 31:
        raise GridError("h264_hrd_limit")
    r.bits(4)
    r.bits(4)
    for _ in range(count + 1):
        r.ue()
        r.ue()
        r.bit()
    r.bits(5)
    r.bits(5)
    r.bits(5)
    r.bits(5)


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


def inferred_reorder(profile, level, constraints, width_mbs, height_map):
    """Infer max_num_reorder_frames per H.264 8.2.5.2 when absent in VUI."""
    if level not in _MAX_DPB_MBS or not width_mbs or not height_map:
        raise GridError("h264_reorder_bound_unconfirmed")
    # constraint_set3_flag applies to Baseline/Main/Extended and the listed
    # profiles. Annex A specifies zero reordering for this constrained case.
    if profile in (44, 86, 100, 110, 122, 244) and constraints & 0x10:
        return 0
    picture_mbs = width_mbs * height_map
    max_dpb_frames = min(16, _MAX_DPB_MBS[level] // picture_mbs)
    if max_dpb_frames < 1:
        raise GridError("h264_level_dpb_invalid")
    return max_dpb_frames


def parse_sps(nal):
    r = Reader(rbsp(nal))
    profile = r.bits(8)
    constraints = r.bits(8)
    level = r.bits(8)
    sps_id = r.ue()
    if sps_id > 31:
        raise GridError("h264_sps_id_limit")
    chroma, separate = 1, 0
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma > 3:
            raise GridError("h264_chroma_unsupported")
        if chroma == 3:
            separate = r.bit()
            if separate:
                raise GridError("h264_separate_colour_plane_unsupported")
        r.ue()
        r.ue()
        r.bit()
        if r.bit():
            for i in range(12 if chroma == 3 else 8):
                if r.bit():
                    skip_scaling_list(r, 16 if i < 6 else 64)
    log2_frame = r.ue() + 4
    poc_type = r.ue()
    if not 4 <= log2_frame <= 16 or poc_type != 0:
        raise GridError("h264_poc_mode_unsupported")
    log2_poc = r.ue() + 4
    if not 4 <= log2_poc <= 16:
        raise GridError("h264_poc_width_invalid")
    r.ue()  # max_num_ref_frames
    r.bit()  # gaps_in_frame_num_value_allowed_flag
    width_mbs, height_map = r.ue() + 1, r.ue() + 1
    frame_only = r.bit()
    if not frame_only:
        raise GridError("h264_interlaced_unsupported")
    r.bit()  # direct_8x8_inference_flag
    crop = (0, 0, 0, 0)
    if r.bit():
        crop = tuple(r.ue() for _ in range(4))
    vui = None
    if r.bit():
        vui = parse_vui(r)
    if not vui or not vui["fixed"]:
        raise GridError("h264_timing_unconfirmed")
    max_reorder = vui["max_reorder"]
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
    return {
        "id": sps_id,
        "profile": profile,
        "constraints": constraints,
        "level": level,
        "log2_frame_num": log2_frame,
        "log2_poc_lsb": log2_poc,
        "width": width,
        "height": height,
        "frame_duration_100ns": Fraction(20_000_000 * vui["units"], vui["scale"]),
        "max_reorder": max_reorder,
        "fps_numerator": vui["scale"],
        "fps_denominator": 2 * vui["units"],
    }


def parse_vui(r):
    if r.bit():
        aspect = r.bits(8)
        if aspect == 255:
            r.bits(16)
            r.bits(16)
    if r.bit():
        r.bit()
    if r.bit():
        r.bits(3)
        r.bit()
        if r.bit():
            r.bits(8)
            r.bits(8)
            r.bits(8)
    if r.bit():
        r.ue()
        r.ue()
    timing = None
    if r.bit():
        units, scale, fixed = r.bits(32), r.bits(32), r.bit()
        if not units or not scale:
            raise GridError("h264_vui_timing_invalid")
        timing = {"units": units, "scale": scale, "fixed": bool(fixed)}
    nal_hrd = r.bit()
    if nal_hrd:
        skip_hrd(r)
    vcl_hrd = r.bit()
    if vcl_hrd:
        skip_hrd(r)
    if nal_hrd or vcl_hrd:
        r.bit()
    r.bit()  # pic_struct_present_flag
    restriction = False
    max_reorder = 0
    if r.bit():
        restriction = True
        r.bit()
        for _ in range(4):
            r.ue()
        max_reorder = r.ue()
        max_buffer = r.ue()
        if max_reorder > max_buffer:
            raise GridError("h264_reorder_bound_invalid")
    if not timing:
        return None
    return {**timing, "reorder": restriction, "max_reorder": max_reorder}


def parse_pps(nal):
    r = Reader(rbsp(nal))
    pps_id, sps_id = r.ue(), r.ue()
    if pps_id > 255 or sps_id > 31:
        raise GridError("h264_pps_id_limit")
    r.bit()  # entropy_coding_mode_flag
    bottom = r.bit()
    if r.ue() != 0:
        raise GridError("h264_slice_groups_unsupported")
    r.ue()
    r.ue()
    r.bit()
    r.bits(2)
    r.se()
    r.se()
    r.se()
    r.bit()
    r.bit()
    redundant = r.bit()
    return {"id": pps_id, "sps_id": sps_id, "bottom_poc": bool(bottom), "redundant": bool(redundant)}


@dataclass(frozen=True)
class Picture:
    sps: dict
    pps: dict
    frame_num: int
    poc: int
    slice_kind: str
    idr: bool
    nal_ref_idc: int


def parse_slice(nal, sps, pps):
    r = Reader(rbsp(nal))
    r.ue()  # first_mb_in_slice
    slice_type = r.ue()
    if slice_type > 9:
        raise GridError("h264_slice_type_invalid")
    slice_type %= 5
    r.ue()  # pic_parameter_set_id
    if sps["profile"] in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        # separate_colour_plane_flag is parsed from SPS below via supported profile/chroma
        pass
    frame_num = r.bits(sps["log2_frame_num"])
    # SPS parser rejects interlaced, so field_pic_flag is absent.
    nal_type = nal[0] & 31
    idr = nal_type == 5
    if idr:
        r.ue()  # idr_pic_id
    poc_lsb = r.bits(sps["log2_poc_lsb"])
    delta = r.se() if pps["bottom_poc"] else 0
    kind = ("P", "B", "I", "SP", "SI")[slice_type]
    if kind in ("SP", "SI"):
        raise GridError("h264_switching_slice_unsupported")
    return frame_num, poc_lsb, delta, kind, idr, (nal[0] >> 5) & 3


class H264Clock:
    def __init__(self):
        self.sps = {}
        self.pps = {}
        self.prev_msb = 0
        self.prev_lsb = 0
        self.epoch_poc = None
        self.epoch_dts = None
        self.epoch_pts = None
        self.origin_dts = None
        self.reorder_lead = None
        self.last_dts = None
        self.last_pts = None
        self.last_poc = None
        self.max_pts = None
        self.duration = None
        self.reorder = None
        self.sps_signature = None

    def inspect(self, payload, grid_timestamp):
        units = nals(payload)
        vcl = []
        for nal in units:
            kind = nal[0] & 31
            if kind == 7:
                info = parse_sps(nal)
                signature = (
                    info["id"],
                    info["profile"],
                    info["level"],
                    info["width"],
                    info["height"],
                    info["frame_duration_100ns"],
                    info["max_reorder"],
                )
                if self.sps_signature is not None and signature != self.sps_signature:
                    raise GridError("h264_codec_configuration_changed")
                self.sps_signature = signature
                self.sps[info["id"]] = info
            elif kind == 8:
                pps = parse_pps(nal)
                previous = self.pps.get(pps["id"])
                if previous is not None and previous != pps:
                    raise GridError("h264_codec_configuration_changed")
                self.pps[pps["id"]] = pps
            elif kind in (2, 3, 4, 19, 20, 21):
                raise GridError("h264_vcl_partition_or_extension_unsupported")
            elif kind in (1, 5):
                vcl.append(nal)
        if not vcl:
            return None
        if len(vcl) > 8:
            raise GridError("h264_slice_count_limit")
        parsed = []
        for nal in vcl:
            r = Reader(rbsp(nal))
            r.ue()
            r.ue()
            pps_id = r.ue()
            pps = self.pps.get(pps_id)
            sps = self.sps.get(pps["sps_id"]) if pps else None
            if not sps:
                raise GridError("h264_parameter_set_missing")
            parsed.append((parse_slice(nal, sps, pps), sps, pps))
        first, sps, pps = parsed[0]
        if any(item[0] != first or item[1] != sps or item[2] != pps for item in parsed[1:]):
            raise GridError("h264_multiple_pictures_per_grid_chunk")
        frame_num, poc_lsb, delta_bottom, slice_kind, idr, ref_idc = first
        max_lsb = 1 << sps["log2_poc_lsb"]
        if idr:
            self.prev_msb = self.prev_lsb = 0
        if poc_lsb < self.prev_lsb and self.prev_lsb - poc_lsb >= max_lsb // 2:
            msb = self.prev_msb + max_lsb
        elif poc_lsb > self.prev_lsb and poc_lsb - self.prev_lsb > max_lsb // 2:
            msb = self.prev_msb - max_lsb
        else:
            msb = self.prev_msb
        poc = msb + poc_lsb + min(0, delta_bottom)
        if idr:
            if self.epoch_poc is not None and self.last_pts is not None:
                self.epoch_pts = self.max_pts + self._rescale(sps["frame_duration_100ns"])
            else:
                self.origin_dts = grid_timestamp
                self.reorder_lead = self._rescale(sps["max_reorder"] * sps["frame_duration_100ns"])
                self.epoch_pts = self.reorder_lead + 90_000
            self.epoch_poc, self.epoch_dts = poc, grid_timestamp
        if self.epoch_poc is None:
            # Decode-order timestamps before the first IDR cannot safely be
            # muxed. They remain available in the private raw capture only.
            return None
        duration = sps["frame_duration_100ns"]
        if self.duration is None:
            self.duration, self.reorder = duration, sps["max_reorder"]
        elif self.duration != duration or self.reorder != sps["max_reorder"]:
            raise GridError("h264_timing_changed")
        if self.last_dts is not None and grid_timestamp <= self.last_dts:
            raise GridError("grid_video_dts_not_increasing")
        # Delay presentation by the SPS DPB bound, while decode time follows
        # the observed Grid arrival/decode timeline. This keeps DTS monotonic
        # and PTS >= DTS as expected by MPEG-TS decoders.
        dts = self._rescale(grid_timestamp - self.origin_dts) + 90_000
        poc_offset = self._rescale(Fraction((poc - self.epoch_poc), 2) * sps["frame_duration_100ns"])
        pts = self.epoch_pts + poc_offset
        # B pictures are decoded after their future reference picture and can
        # legitimately have PTS < DTS. DTS follows Grid decode order; PTS comes
        # from H.264 POC. Reject only a regression beyond the SPS DPB bound.
        reorder_bound = self._rescale(self.reorder * self.duration)
        if pts < dts:
            raise GridError("h264_pts_before_dts")
        if self.last_pts is not None and pts < self.last_pts - self.reorder_lead:
            raise GridError("h264_poc_regression_out_of_bound")
        if self.max_pts is not None and pts < self.max_pts - reorder_bound:
            raise GridError("h264_poc_regression_out_of_bound")
        if ref_idc:
            self.prev_msb, self.prev_lsb = msb, poc_lsb
        self.last_dts, self.last_dts_out, self.last_pts, self.last_poc = grid_timestamp, dts, pts, poc
        self.max_pts = pts if self.max_pts is None else max(self.max_pts, pts)
        return {
            "dts": dts,
            "pts": pts,
            "poc": poc,
            "slice_type": slice_kind,
            "idr": idr,
            "sps": sps,
            "units": units,
        }

    @staticmethod
    def _rescale(timestamp_100ns):
        return int(Fraction(timestamp_100ns * 90_000, 10_000_000))

    def audio_pts(self, timestamp_100ns):
        if self.origin_dts is None:
            return None
        return self._rescale(timestamp_100ns - self.origin_dts) + self.reorder_lead + 90_000
