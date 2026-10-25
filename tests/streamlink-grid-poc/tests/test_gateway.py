# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import struct

import pytest

from soop_grid.crypto import service6, wire_hash
from soop_grid.gateway import certificate_packet, login_packet, validate_login
from soop_grid.protocol import Frame, GridError, gateway_packet


def test_go_gateway_golden_vectors(golden):
    g = golden["gateway"]
    payload = service6("offline-fixture-guid", 0x65A1B2C3, 0x1234)
    assert gateway_packet(0x3DF, struct.pack("<II", 6, 128) + payload).hex() == g["service6"]
    assert (
        login_packet("0123456789ABCDEF0123456789ABCDEF", "fixture-cookie", "fixture_channel").hex()
        == g["login"]
    )
    assert (
        certificate_packet(12345, "fixture_channel", "123", b"fixture-ticket").hex()
        == g["certificate_request"]
    )
    assert wire_hash(bytes(112)).hex() == g["hash_long_zero"]


@pytest.mark.parametrize(
    "data,want", [(b"", "cdf26213a150dc3ecb610f18f6b38b46"), (b"abc", "c14a12199c66e4ba84636b0f69144c77")]
)
def test_independent_ripemd_vectors(data, want):
    assert wire_hash(data).hex() == want


@pytest.mark.parametrize(
    "command,result,body",
    [(1, 0, bytes(111)), (0x3E2, 0, b"short"), (0x3E2, -1, bytes(111)), (0x3E2, 0, b"x" * 111)],
)
def test_login_denied_unknown_truncated_or_unterminated(command, result, body):
    with pytest.raises(GridError):
        validate_login(Frame(command, body, result))


def test_valid_login_and_delimiter_injection():
    validate_login(Frame(0x3E2, bytes(111)))
    with pytest.raises(GridError):
        login_packet("0" * 32, "fixture\x12cookie", "fixture_channel")
