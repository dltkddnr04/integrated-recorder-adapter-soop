# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Protocol RIPEMD-128 quirk and AES-CTR; ports of the repository Go PoC.

AES uses PyCryptodome, an existing official Streamlink dependency.
"""

import struct
from Crypto.Cipher import AES

MASK = 0xFFFFFFFF
leftWords = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    7,
    4,
    13,
    1,
    10,
    6,
    15,
    3,
    12,
    0,
    9,
    5,
    2,
    14,
    11,
    8,
    3,
    10,
    14,
    4,
    9,
    15,
    8,
    1,
    2,
    7,
    0,
    6,
    13,
    11,
    5,
    12,
    1,
    9,
    11,
    10,
    0,
    8,
    12,
    4,
    13,
    3,
    7,
    15,
    14,
    5,
    6,
    2,
)
rightWords = (
    5,
    14,
    7,
    0,
    9,
    2,
    11,
    4,
    13,
    6,
    15,
    8,
    1,
    10,
    3,
    12,
    6,
    11,
    3,
    7,
    0,
    13,
    5,
    10,
    14,
    15,
    8,
    12,
    4,
    9,
    1,
    2,
    15,
    5,
    1,
    3,
    7,
    14,
    6,
    9,
    11,
    8,
    12,
    2,
    10,
    0,
    4,
    13,
    8,
    6,
    4,
    1,
    3,
    11,
    15,
    0,
    5,
    12,
    2,
    13,
    9,
    7,
    10,
    14,
)
leftShifts = (
    11,
    14,
    15,
    12,
    5,
    8,
    7,
    9,
    11,
    13,
    14,
    15,
    6,
    7,
    9,
    8,
    7,
    6,
    8,
    13,
    11,
    9,
    7,
    15,
    7,
    12,
    15,
    9,
    11,
    7,
    13,
    12,
    11,
    13,
    6,
    7,
    14,
    9,
    13,
    15,
    14,
    8,
    13,
    6,
    5,
    12,
    7,
    5,
    11,
    12,
    14,
    15,
    14,
    15,
    9,
    8,
    9,
    14,
    5,
    6,
    8,
    6,
    5,
    12,
)
rightShifts = (
    8,
    9,
    9,
    11,
    13,
    15,
    15,
    5,
    7,
    7,
    8,
    11,
    14,
    14,
    12,
    6,
    9,
    13,
    15,
    7,
    12,
    8,
    9,
    11,
    7,
    7,
    12,
    7,
    6,
    15,
    13,
    11,
    9,
    7,
    15,
    11,
    8,
    6,
    6,
    14,
    12,
    13,
    5,
    14,
    13,
    13,
    7,
    5,
    15,
    5,
    8,
    11,
    14,
    14,
    6,
    14,
    6,
    9,
    12,
    9,
    12,
    5,
    15,
    8,
)


def _boolean(r, x, y, z):
    return (x ^ y ^ z, (x & y) | (~x & z), (x | ~y) ^ z, (x & z) | (y & ~z))[r]


def _block(h, block):
    words = struct.unpack("<16I", block)
    a, b, c, d = h
    aa, bb, cc, dd = h
    kl = (0, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC)
    kr = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0)

    def rotate(v, n):
        v &= MASK
        return ((v << n) | (v >> (32 - n))) & MASK

    for j in range(64):
        r = j // 16
        t = rotate(a + _boolean(r, b, c, d) + words[leftWords[j]] + kl[r], leftShifts[j])
        a, b, c, d = d, t, b, c
        t = rotate(aa + _boolean(3 - r, bb, cc, dd) + words[rightWords[j]] + kr[r], rightShifts[j])
        aa, bb, cc, dd = dd, t, bb, cc
    return [(h[1] + c + dd) & MASK, (h[2] + d + aa) & MASK, (h[3] + a + bb) & MASK, (h[0] + b + cc) & MASK]


def wire_hash(data):
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    for i in range(0, len(data) - 63, 64):
        h = _block(h, data[i : i + 64])
    # The native finalizer reuses the FIRST remainder bytes, not the tail.
    tail = data[: len(data) % 64] + b"\x80"
    tail += bytes((56 - len(tail)) % 64)
    tail += struct.pack("<Q", len(data) * 8)
    for i in range(0, len(tail), 64):
        h = _block(h, tail[i : i + 64])
    return struct.pack("<4I", *h)


def pad16(data):
    return data + bytes((-len(data)) % 16)


def ctr(key, iv, data):
    return AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=int.from_bytes(iv, "big")).encrypt(pad16(data))


PREFIX = bytes.fromhex("756a5e747666666761343337")
SALT = bytes.fromhex("756f6e6b74737172606872716765692f2e34")
SERVICE_KEY = bytes.fromhex("45cb101d263d47515b64757b858f999f")
SERVICE_IV = bytes.fromhex("b6c3cadde7f1fb040e182228323c464c")
AUTH_KEY = bytes.fromhex("e2081129c4d0afbe55379fcde1755413")
AUTH_IV = bytes.fromhex("22655d8796eeca33c7a8221dffcb8271")


def mixed_header(timestamp, mix, counter, length):
    words = [timestamp, mix, counter, length]
    lanes = [
        (0, 0),
        (1, 3),
        (2, 2),
        (3, 1),
        (0, 2),
        (1, 1),
        (2, 0),
        (3, 0),
        (0, 3),
        (1, 2),
        (2, 1),
        (3, 3),
        (0, 1),
        (1, 0),
        (2, 3),
        (3, 2),
    ]
    return bytes((words[w] >> (8 * n)) & 255 for w, n in lanes)


def service6(guid, timestamp, counter):
    from .protocol import GridError

    if not guid or len(guid) > 256 or "\0" in guid:
        raise GridError("guid_invalid")
    data = struct.pack("<IQ", 1, 42) + wire_hash(guid.encode()).hex().upper().encode() + b"\0"
    mix = len(data) | 0x06000000
    for i, b in enumerate(data):
        v = b << (i % 4 * 8)
        mix = mix ^ v if i & 1 else mix | v
    first = ctr(SERVICE_KEY, SERVICE_IV, mixed_header(timestamp, mix, counter, len(data)) + PREFIX + data)
    second = SALT + first
    return wire_hash(pad16(second)) + ctr(SERVICE_KEY, SERVICE_IV, second)
