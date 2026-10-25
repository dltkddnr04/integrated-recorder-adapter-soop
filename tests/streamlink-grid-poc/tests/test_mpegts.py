# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import pytest

from conftest import synthetic_sps
from soop_grid.media import adts_info, sps_info
from soop_grid.mpegts import TSMux, crc_mpeg
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
