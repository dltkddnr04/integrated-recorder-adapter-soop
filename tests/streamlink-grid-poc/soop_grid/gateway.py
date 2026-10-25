# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Normal Gateway Service 6 -> P1 -> server certificate sequence."""

import secrets
import struct
import time

from .crypto import service6
from .protocol import GridError, gateway_packet, read_frame


def login_packet(guid, cookie, broadcaster):
    try:
        if len(guid) != 32:
            raise ValueError
        bytes.fromhex(guid)
        bj = broadcaster.encode("ascii")
        ck = cookie.encode("utf-8")
        if not 0 < len(bj) <= 128 or len(ck) > 4096 or any(x in bj + ck for x in (0, 17, 18)):
            raise ValueError
    except (ValueError, UnicodeError):
        raise GridError("gateway_login_fields_invalid") from None
    info = b"cookie\x11" + ck + b"\x12bj\x11" + bj + b"\x12"
    return gateway_packet(
        0x3E2, struct.pack("<I", 0x2002A) + guid.encode() + b"\0" + struct.pack("<I", len(info)) + info
    )


def certificate_packet(bno, broadcaster, category, ticket):
    try:
        category = int(category)
        bj = broadcaster.encode("ascii")
        if (
            not 0 < bno <= 0xFFFFFFFF
            or not 0 <= category <= 0xFFFFFFFF
            or not bj
            or b"\0" in bj
            or len(ticket) > 4096
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise GridError("gateway_certificate_fields_invalid") from None
    body = bytearray(0x47 + len(ticket))
    struct.pack_into("<II", body, 0, bno, 1)
    body[0x21 : 0x21 + min(len(bj), 25)] = bj[:25]
    struct.pack_into("<III", body, 0x3B, 42, category, len(ticket))
    body[0x47:] = ticket
    return gateway_packet(0x415, bytes(body))


def validate_login(frame):
    if frame.command != 0x3E2 or len(frame.body) < 0x6F:
        raise GridError("gateway_login_response_layout")
    if b"\0" not in frame.body[:0x1A] or b"\0" not in frame.body[0x2E:]:
        raise GridError("gateway_login_unterminated_field")
    if frame.result != 0:
        raise GridError("gateway_login_denied")


def obtain_certificate(network, session, report):
    guid = secrets.token_hex(16).upper()
    now = int(time.time()) & 0xFFFFFFFF
    counter = (((now * 214013 + 2531011) & 0xFFFFFFFF) >> 16) & 0x7FFF
    sock = network.connect(session.gateway)
    try:
        payload = service6(guid, now, counter)
        network.send(sock, gateway_packet(0x3DF, struct.pack("<II", 6, len(payload)) + payload))
        network.send(sock, login_packet(guid, session.cookie, session.broadcaster))
        validate_login(read_frame(sock, "gateway"))
        report("gateway_login_accepted")
        network.send(
            sock, certificate_packet(session.bno, session.broadcaster, session.category, session.ticket)
        )
        reply = read_frame(sock, "gateway")
        if reply.command != 0x415 or reply.result < 0:
            raise GridError("gateway_certificate_denied")
        from .center import Credentials

        credentials = Credentials.parse(reply.body, guid, session.bno, session.stream_password)
        report("gateway_certificate_received")
        return credentials
    finally:
        network.release(sock)
