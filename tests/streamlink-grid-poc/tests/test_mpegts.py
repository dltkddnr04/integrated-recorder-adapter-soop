# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import pytest

from conftest import synthetic_sps
from soop_grid.media import adts_info, sps_info
from soop_grid.mpegts import TSMux, crc_mpeg, pts_bytes
from soop_grid.protocol import GridError


def test_go_ts_golden_and_preroll(golden):
    g = golden["ts"]
    m = TSMux()
    assert m.add("P", 40_000_000, b"\0\0\1\x41") == b""
    assert m.add("A", 40_000_000, bytes.fromhex(g["audio"])) == b""
    ts = m.add("I", 50_000_000, bytes.fromhex(g["video"]))
    ts += m.add("A", 50_010_000, bytes.fromhex(g["audio"]))
    ts += m.add("P", 50_333_333, b"\0\0\1\x41\x88")
    assert ts.hex() == g["ts"] and m.preroll_video == 1


@pytest.mark.parametrize("n", [1, 175, 176, 177, 183, 184, 185, 1000])
@pytest.mark.parametrize("pcr", [None, 123456789])
def test_ts_boundaries_and_original_payload(n, pcr):
    data = bytes([37]) * n
    out = TSMux().packetize(256, data, pcr=pcr)
    restored = bytearray()
    for i in range(0, len(out), 188):
        packet = out[i : i + 188]
        assert len(packet) == 188 and packet[0] == 0x47 and packet[3] & 15 == i // 188 % 16
        pos = 4 + ((packet[4] + 1) if packet[3] & 32 else 0)
        restored.extend(packet[pos:])
    assert restored == data


def test_pat_pmt_crc_and_timestamp_bounds():
    out = TSMux().tables()
    for offset in (0, 188):
        packet = out[offset : offset + 188]
        pos = 4 + packet[4] + 1 + 1
        section = packet[pos:]
        assert crc_mpeg(section) == 0
    m = TSMux()
    m.add("I", 50_000_000, synthetic_sps())
    with pytest.raises(GridError):
        m.add("P", 1, b"\0\0\1\x41")


def test_pts_dts_timestamp_serialization_wraps_at_33_bits():
    wrap = 1 << 33
    assert pts_bytes(wrap + 12345) == pts_bytes(12345)


def test_pcr_encoding_wraps_33_bit_base_without_reversing_unwrapped_clock():
    wrap = 1 << 33
    mux = TSMux()
    mux.started = True
    first_dts = wrap - 4_500
    data = mux.add_video(first_dts, first_dts, b"first")
    data += mux.add_video(wrap + 4_500, wrap + 4_500, b"second")
    pcrs = []
    for offset in range(0, len(data), 188):
        packet = data[offset : offset + 188]
        control = (packet[3] >> 4) & 3
        if ((packet[1] & 31) << 8 | packet[2]) == 0x100 and control in (2, 3) and packet[5] & 0x10:
            base = (
                (packet[6] << 25)
                | (packet[7] << 17)
                | (packet[8] << 9)
                | (packet[9] << 1)
                | (packet[10] >> 7)
            )
            extension = ((packet[10] & 1) << 8) | packet[11]
            pcrs.append(base * 300 + extension)
    assert pcrs == [(wrap - 4_500) * 300, 4_500 * 300]


def test_video_pcr_interval_at_100ms_boundary_is_encoded_correctly():
    mux = TSMux()
    idr = b"\0\0\1\x67\x42\0\0\1\x68\xce\0\0\1\x65\x88"
    data = mux.add_video(0, 0, idr, idr=True)
    data += mux.add_video(9_000, 9_000, b"\0\0\1\x41\x88")
    packets = [data[i : i + 188] for i in range(0, len(data), 188)]
    pcrs = []
    for packet in packets:
        pid = ((packet[1] & 31) << 8) | packet[2]
        control = (packet[3] >> 4) & 3
        if pid != 0x100 or control not in (2, 3) or packet[4] < 7 or not packet[5] & 0x10:
            continue
        base = (packet[6] << 25) | (packet[7] << 17) | (packet[8] << 9) | (packet[9] << 1) | (packet[10] >> 7)
        extension = ((packet[10] & 1) << 8) | packet[11]
        pcrs.append(base * 300 + extension)
    assert len(pcrs) == 2
    assert pcrs[1] - pcrs[0] == 9_000 * 300


def test_mux_rejects_pcr_gap_over_100ms_before_mutating_state():
    mux = TSMux()
    mux.started = True
    mux.add_video(0, 0, b"first")
    before = (mux.last_dts, mux.last_pcr_dts, mux.video_frames, dict(mux.cc))
    with pytest.raises(GridError, match="media_pcr_gap_limit"):
        mux.add_video(9_001, 9_001, b"second")
    assert (mux.last_dts, mux.last_pcr_dts, mux.video_frames, dict(mux.cc)) == before


def test_mux_tracks_presentation_extent_across_b_frame_reordering():
    mux = TSMux()
    mux.started = True
    mux.add_video(0, 300, b"first")
    mux.add_video(100, 400, b"second")
    mux.add_video(200, 250, b"third")
    assert mux.last_pts == 250
    assert mux.min_pts == 250
    assert mux.max_pts == 400


def test_sps_cropping_and_adts_metadata(golden):
    info = sps_info(synthetic_sps())
    assert (info["width"], info["height"]) == (1920, 1080)
    assert sps_info(b"\0\0\1\x41") is None
    with pytest.raises(GridError):
        sps_info(b"\0\0\1\x67\0")
    assert adts_info(bytes.fromhex(golden["ts"]["audio"])) == {
        "codec": "aac",
        "sample_rate": 44100,
        "channels": 2,
    }
