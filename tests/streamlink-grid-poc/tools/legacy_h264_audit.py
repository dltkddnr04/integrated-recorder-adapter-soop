# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Offline historical HRD/Grid timing comparisons; never imported by production.

LegacyH264Clock retains the retired HRD-delay and proven-zero-reorder models
for regression audits. Its timestamps are candidates, not a production clock.
Syntax, parameter-set transactions, preroll and POC validation are shared with
H264Clock so historical probes retain the same bounded parser protections.
"""

from soop_grid.h264clock import H264Clock
from soop_grid.protocol import GridError


class LegacyH264Clock(H264Clock):
    def inspect(self, payload, grid_timestamp):
        return super().inspect(payload, grid_timestamp, None)

    @staticmethod
    def _validate_composition(grid_timestamp, composition_offset):
        # This offline API intentionally has no Grid composition input.
        pass

    @staticmethod
    def _validate_timing_subset(header, sps):
        # Historical audits include POC type 1 and MMCO5 timing probes.
        pass

    def _presentation_time(self, header, sps, sei, poc, dts, grid_timestamp, composition_offset):
        picture_timing = sei.get("picture_timing", {})
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
        if pts - dts >= 1 << 32:
            raise GridError("h264_hrd_delay_limit")
        if self.timing_model is not None and self.timing_model != model:
            raise GridError("h264_timing_changed")
        return model, pts

    def _raise_order_conflict(self, facts, sps, sei):
        if self.timing_model == "hrd_sei":
            timing = sei["picture_timing"]
            tick = sps["clock_tick_100ns"]
            facts.update(
                {
                    "h264_hrd_poc_order_conflicts": 1,
                    "h264_cpb_removal_delay": timing["cpb_removal_delay"],
                    "h264_dpb_output_delay": timing["dpb_output_delay"],
                    "h264_clock_tick_100ns_numerator": tick.numerator,
                    "h264_clock_tick_100ns_denominator": tick.denominator,
                }
            )
        super()._raise_order_conflict(facts, sps, sei)
