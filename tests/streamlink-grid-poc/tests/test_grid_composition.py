# SPDX-License-Identifier: AGPL-3.0-only
"""Grid timing input is independent of HRD; no live secrets or media fixtures."""

import struct
from fractions import Fraction

import pytest

from conftest import media_body
from test_h264_audit import annex, picture, pps, sps
from soop_grid.h264clock import H264Clock
from soop_grid.media import MediaSink, parse_media
from soop_grid.protocol import Frame, GridError
from soop_grid.recovery import Continuity


def video(timestamp=50_000_000, offset=0):
    payload = annex(sps(mode=2, fixed=False, hrd=False), pps(), picture(idr=True, mode=2))
    body = bytearray(media_body(payload, "I", 1, timestamp))
    struct.pack_into("<I", body, 57, offset)
    return body


def test_header_fields_preserved_and_auxiliary_not_end_time():
    body = video(offset=330000)
    struct.pack_into("<Q", body, 49, 123)
    chunk = parse_media(body)
    assert chunk.start_timestamp == 50_000_000
    assert chunk.auxiliary_time_raw == 123
    assert chunk.composition_offset_raw == 330000


@pytest.mark.parametrize("offset", [0x80000000, 0xFFFFFFFF])
def test_unobserved_negative_composition_is_not_reinterpreted(offset):
    with pytest.raises(GridError, match="grid_composition_offset_unsupported"):
        parse_media(video(offset=offset))


def test_raw_time_addition_overflow_is_rejected():
    with pytest.raises(GridError, match="media_timestamp_bounds"):
        parse_media(video((1 << 64) - 1, 1))


def test_grid_pts_rounds_raw_sum_once_without_hrd():
    clock = H264Clock()
    clock.observe_audio(49_999_950)
    chunk = parse_media(video(50_000_001, 50))
    timing = clock.inspect(chunk.payload, chunk.timestamp, chunk.composition_offset_raw)
    assert timing["pts"] == 90000 + round(Fraction(101 * 90000, 10000000))
    assert timing["dts"] == 90000 + round(Fraction(51 * 90000, 10000000))
    assert timing["timing_model"] == "grid_composition"


def test_cache_identity_includes_composition_but_not_auxiliary_field():
    continuity = Continuity({}, lambda _: None)
    first = parse_media(video(offset=330000))
    assert continuity.accept(first)
    other = video(offset=330000)
    struct.pack_into("<Q", other, 49, 999)
    assert not continuity.accept(parse_media(other))
    with pytest.raises(GridError, match="frame_identity_conflict"):
        continuity.accept(parse_media(video(offset=160000)))


def test_composition_is_required_after_an_accepted_picture():
    clock = H264Clock()
    chunk = parse_media(video())
    clock.inspect(chunk.payload, chunk.timestamp, 0)
    with pytest.raises(TypeError, match="composition_offset"):
        clock.inspect(chunk.payload, chunk.timestamp + 1_000_000)
    with pytest.raises(GridError, match="grid_composition_offset_unsupported"):
        clock.inspect(chunk.payload, chunk.timestamp + 1_000_000, None)


def test_startup_buffer_is_bounded_without_audio():
    sink = MediaSink(lambda _: None, synchronize_startup=True)
    frame = Frame(0xC748, video())
    for _ in range(600):
        sink.add(frame)
    with pytest.raises(GridError, match="media_startup_limit"):
        sink.add(frame)
    sink.close()
    assert sink.stats["video_output_frames"] == 0


@pytest.mark.parametrize("audio_first", [True, False])
@pytest.mark.parametrize("audio_start", [49_900_000, 50_100_000])
def test_production_startup_uses_common_accepted_origin(audio_first, audio_start):
    from test_audio_audit import header

    output = []
    sink = MediaSink(output.append, synchronize_startup=True)
    v = Frame(0xC748, video())
    a = Frame(0xC748, media_body(header() + b"\0", "A", 2, audio_start))
    frames = (a, v) if audio_first else (v, a)
    sink.add(frames[0])
    assert not output
    sink.add(frames[1])
    assert output
    assert sink.video_clock.origin_dts == min(audio_start, 50_000_000)
    assert sink.stats["audio_output_access_units"] == 1
    assert sink.stats["video_output_frames"] == 1
    sink.close()


def test_missing_composition_on_real_b_pictures_still_fails_closed(encoded_bframes):
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_picture_order_conflict"):
        for index, (payload, _) in enumerate(encoded_bframes):
            clock.inspect(payload, 50_000_000 + index * 333333, 0)


def test_extension_changes_payload_start_not_timing_offsets():
    body = video(offset=160000)
    extended = bytearray(body[:69] + bytes(50) + body[69:])
    struct.pack_into("<I", extended, 4, 50)
    chunk = parse_media(extended)
    assert chunk.payload == parse_media(body).payload
    assert chunk.composition_offset_raw == 160000
    assert chunk.extension_bytes == 50
    with pytest.raises(GridError, match="media_layout_unconfirmed"):
        parse_media(extended[:-1])


def test_unverified_media_version_cannot_reuse_composition_layout():
    body = video()
    struct.pack_into("<H", body, 0, 2)
    with pytest.raises(GridError, match="media_layout_unconfirmed"):
        parse_media(body)


@pytest.mark.parametrize("offset", [None, False, True, -1, 1 << 31, 1.0, "0"])
def test_clock_rejects_invalid_composition_before_parsing_or_preroll(offset):
    clock = H264Clock()
    before = clock.__dict__.copy()
    with pytest.raises(GridError, match="grid_composition_offset_unsupported"):
        clock.inspect(b"invalid syntax", 50_000_000, offset)
    assert clock.__dict__ == before


def test_clock_requires_composition_even_before_start():
    clock = H264Clock()
    with pytest.raises(TypeError, match="composition_offset"):
        clock.inspect(b"invalid syntax", 50_000_000)
    assert not clock.sps and clock.preroll_frames == 0


def test_clock_rejects_composition_overflow_before_parsing():
    with pytest.raises(GridError, match="media_timestamp_bounds"):
        H264Clock().inspect(b"invalid syntax", (1 << 64) - 1, 1)


def test_grid_composition_needs_neither_hrd_picture_timing_nor_fixed_vui():
    from test_h264_audit import buffering_period_sei

    clock = H264Clock()
    first = annex(
        sps(mode=2, hrd=True, fixed=False), pps(), buffering_period_sei(), picture(mode=2, idr=True)
    )
    timed = clock.inspect(first, 50_000_000, 333_333)
    assert timed["pts"] - timed["dts"] == 3000
    assert "picture_timing" not in timed["sei"]
    second = clock.inspect(annex(picture(1, mode=2)), 50_333_333, 333_333)
    assert second["timing_model"] == "grid_composition"
    assert second["pts"] == 96_000


@pytest.mark.parametrize(
    "mode,mmco,code",
    [
        (1, False, "h264_grid_poc_type_unverified"),
        (0, True, "h264_grid_mmco5_unverified"),
    ],
)
def test_production_preserves_unverified_poc_and_mmco_restrictions(mode, mmco, code):
    clock = H264Clock()
    first = annex(sps(mode=mode, reorder=0), pps(), picture(mode=mode, idr=True))
    if mode == 1:
        with pytest.raises(GridError, match=code):
            clock.inspect(first, 50_000_000, 0)
        assert clock.origin_dts is None
    else:
        clock.inspect(first, 50_000_000, 0)
        with pytest.raises(GridError, match=code):
            clock.inspect(annex(picture(1, 2, mode=mode, mmco=mmco)), 50_333_333, 0)
        assert clock.last_poc == 0


def test_production_preserves_low_delay_hrd_restriction():
    from test_h264_audit import buffering_period_sei

    payload = annex(
        sps(mode=2, hrd=True, low_delay=True), pps(), buffering_period_sei(), picture(mode=2, idr=True)
    )
    clock = H264Clock()
    with pytest.raises(GridError, match="h264_hrd_low_delay_timing_unsupported"):
        clock.inspect(payload, 50_000_000, 0)
    assert clock.origin_dts is None and not clock.sps
