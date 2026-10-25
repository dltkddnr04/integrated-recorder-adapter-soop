# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors

from fractions import Fraction

import pytest

from soop_grid.h264clock import H264Clock, parse_sps
from soop_grid.mpegts import nals
from soop_grid.protocol import GridError


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


def test_data_partitioned_vcl_is_rejected_explicitly():
    with pytest.raises(GridError, match="h264_vcl_partition_or_extension_unsupported"):
        H264Clock().inspect(b"\0\0\1\x42\x80", 50_000_000)
