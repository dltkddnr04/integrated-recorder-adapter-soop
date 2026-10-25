# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import struct

import pytest

from soop_grid.center import Credentials, auth_body, discover_parent, join_body, json_body
from soop_grid.protocol import Frame, GridError


def credentials(golden):
    c = Credentials.parse(
        bytes.fromhex(golden["center"]["certificate"]), "0123456789ABCDEF0123456789ABCDEF", 12345
    )
    c.joinlog = b"log\x11offline\x12"
    return c


def test_go_center_auth_and_password_golden(golden):
    c = credentials(golden)
    assert auth_body(c).hex() == golden["center"]["center_auth"]
    assert join_body(c).hex() == golden["center"]["center_join"]
    c.password = b"fixture-only"
    assert join_body(c).hex() == golden["center"]["center_join_password"]
    assert "fixture" not in repr(c)
    c.clear()
    with pytest.raises(GridError):
        auth_body(c)


@pytest.mark.parametrize("kind", ["short", "length", "seed", "unterminated", "password"])
def test_issued_certificate_rejects_corruption(golden, kind):
    b = bytearray.fromhex(golden["center"]["certificate"])
    password = b""
    if kind == "short":
        b = b[:20]
    if kind == "length":
        b[12] ^= 1
    if kind == "seed":
        b[-20] ^= 1
    if kind == "unterminated":
        b[16:24] = b"x" * 8
    if kind == "password":
        password = b"bad\x11field"
    with pytest.raises(GridError):
        Credentials.parse(bytes(b), "0" * 32, 12345, password)


def test_center_normal_sequence_and_server_denial(golden, monkeypatch):
    import soop_grid.center as center

    class Net:
        def __init__(self):
            self.wires = []
            self.released = False

        def connect(self, address):
            return object()

        def send(self, sock, wire):
            self.wires.append(wire)

        def release(self, sock):
            self.released = True

    replies = iter(
        [
            Frame(2, bytes(8)),
            Frame(3, struct.pack("<II", 0, 42)),
            Frame(0x4A, bytes(52)),
            Frame(0x46, b""),
            Frame(0x50, b""),
            Frame(
                0x69,
                json_body(
                    {
                        "list": [
                            {"parent_ip": 0x08080808, "parent_port": 0x1027, "quality": 1, "cur_frame_no": 99}
                        ]
                    }
                ),
            ),
        ]
    )
    monkeypatch.setattr(center, "read_frame", lambda *a: next(replies))
    n = Net()
    events = []
    parents, key = discover_parent(n, ("8.8.8.8", 1), credentials(golden), events.append)
    assert key == 42 and parents[0].address == ("8.8.8.8", 10000)
    assert [struct.unpack_from("<I", x)[0] for x in n.wires] == [
        2,
        3,
        0x3E,
        0x14,
        0x2A,
        0x46,
        0x71,
        0x50,
        0x69,
    ]
    assert n.released
    monkeypatch.setattr(center, "read_frame", lambda *a: Frame(2, b"", -1))
    with pytest.raises(GridError, match="access_denied"):
        discover_parent(Net(), ("8.8.8.8", 1), credentials(golden), events.append)
