# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_live_credentials(monkeypatch):
    for name in ("SOOP_ACCOUNT_ID", "SOOP_ACCOUNT_PASSWORD", "SOOP_STREAM_PASSWORD", "SOOP_GRID_CAPTURE_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def golden():
    return json.loads(Path(__file__).with_name("golden.json").read_text())


def synthetic_sps(width=1920, height=1080):
    bits = []

    def put(value, n):
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    def ue(value):
        n = (value + 1).bit_length()
        bits.extend([0] * (n - 1))
        put(value + 1, n)

    put(66, 8)
    put(0, 8)
    put(40, 8)
    ue(0)
    ue(0)
    ue(0)
    ue(0)
    ue(1)
    put(0, 1)
    ue(width // 16 - 1)
    ue((height + 15) // 16 - 1)
    put(1, 1)
    put(1, 1)
    put(1, 1)
    ue(0)
    ue(0)
    ue(0)
    ue(((height + 15) // 16 * 16 - height) // 2)
    put(0, 1)
    put(1, 1)
    while len(bits) % 8:
        bits.append(0)
    data = bytes(sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8))
    return b"\0\0\0\1\x67" + data + b"\0\0\1\x68\xce\0\0\1\x65\x88"


def media_body(payload, kind="I", frame=1, timestamp=50_000_000):
    b = bytearray(69 + len(payload))
    struct.pack_into("<HHIIQ", b, 0, 1, 69, 0, len(payload), frame)
    b[40] = ord(kind)
    struct.pack_into("<Q", b, 41, timestamp)
    b[69:] = payload
    return bytes(b)


@pytest.fixture(scope="session")
def encoded_bframes(tmp_path_factory):
    """Synthetic Annex-B access units with real SPS/PPS and two B pictures."""
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required to synthesize standards-conformant B-frame fixture")
    path = tmp_path_factory.mktemp("h264-bframes") / "video.h264"
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
            "-frames:v",
            "30",
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
            "800k",
            "-x264-params",
            "bframes=2:keyint=15:min-keyint=15:scenecut=0:aud=1:repeat-headers=1:force-cfr=1:nal-hrd=cbr",
            "-f",
            "h264",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    data = path.read_bytes()
    starts = list(re.finditer(b"\x00\x00(?:\x00)?\x01\x09", data))
    assert len(starts) == 30
    from soop_grid.mpegts import nals

    units = [
        data[m.start() : starts[i + 1].start() if i + 1 < len(starts) else len(data)]
        for i, m in enumerate(starts)
    ]
    result = [(unit, "I" if any(n[0] & 31 == 5 for n in nals(unit)) else "P") for unit in units]
    assert any(n[0] & 31 == 1 for unit, _ in result[1:] for n in nals(unit))
    return result


@pytest.fixture(scope="session")
def encoded_poc2_no_vui(tmp_path_factory):
    """Real POC type 2, no fixed VUI clock, progressive no-B test stream."""
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required to synthesize a POC type 2 fixture")
    encoders = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, timeout=15, check=True
    ).stdout
    if b"libopenh264" not in encoders:
        pytest.skip("FFmpeg libopenh264 encoder required for a real POC type 2 fixture")
    path = tmp_path_factory.mktemp("h264-poc2") / "video.h264"
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
            "-frames:v",
            "60",
            "-an",
            "-c:v",
            "libopenh264",
            "-b:v",
            "300k",
            "-g",
            "30",
            "-f",
            "h264",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")

    from soop_grid.h264clock import _slice_pps_id, parse_pps, parse_slice, parse_sps
    from soop_grid.mpegts import nals

    units = nals(path.read_bytes())
    sps, pps, pending, current, access_units = {}, {}, [], [], []
    for nal in units:
        kind = nal[0] & 31
        if kind == 7:
            info = parse_sps(nal)
            sps[info["id"]] = info
        elif kind == 8:
            info = parse_pps(nal)
            pps[info["id"]] = info
        if kind in (1, 5):
            pps_id = _slice_pps_id(nal)
            header = parse_slice(nal, sps[pps[pps_id]["sps_id"]], pps[pps_id])
            if header.first_mb == 0 and current:
                access_units.append(current)
                current = []
            if not current:
                current.extend(pending)
                pending = []
            current.append(nal)
        elif current:
            pending.append(nal)
        else:
            pending.append(nal)
    if current:
        access_units.append(current)
    result_units = [b"".join(b"\0\0\0\1" + nal for nal in au) for au in access_units]
    assert len(result_units) == 60
    assert sps and {info["poc_type"] for info in sps.values()} == {2}
    assert all(not info["timing_fixed"] for info in sps.values())
    for access_unit in result_units:
        for nal in nals(access_unit):
            if nal[0] & 31 in (1, 5):
                pps_id = _slice_pps_id(nal)
                header = parse_slice(nal, sps[pps[pps_id]["sps_id"]], pps[pps_id])
                assert header.slice_kind != "B"
    return result_units
