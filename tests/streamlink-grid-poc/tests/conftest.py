# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import json
import struct
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
