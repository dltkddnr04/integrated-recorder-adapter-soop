# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Normal Gateway Service 6 -> P1 -> server certificate sequence."""

import secrets
import re
import struct
import time

from .crypto import service6
from .auth import identifier
from .protocol import GridError, gateway_packet, read_frame


def login_packet(guid, cookie, broadcaster):
    try:
        if not isinstance(guid, str) or not re.fullmatch(r"[0-9A-Fa-f]{32}", guid):
            raise ValueError
        if not identifier(broadcaster) or len(broadcaster) > 25 or not isinstance(cookie, str):
            raise ValueError
        bj = broadcaster.encode("ascii")
        ck = cookie.encode("utf-8")
        if not 0 < len(ck) <= 4096 or any(x < 32 or x == 127 for x in bj + ck):
            raise ValueError
    except (ValueError, UnicodeError):
        raise GridError("gateway_login_fields_invalid") from None
    info = b"cookie\x11" + ck + b"\x12bj\x11" + bj + b"\x12"
    return gateway_packet(
        0x3E2, struct.pack("<I", 0x2002A) + guid.encode() + b"\0" + struct.pack("<I", len(info)) + info
    )


def certificate_packet(bno, broadcaster, category, ticket):
    try:
        if not (
            type(category) is int or isinstance(category, str) and re.fullmatch(r"[0-9]{1,10}", category)
        ):
            raise ValueError
        category = int(category)
        if not identifier(broadcaster) or len(broadcaster) > 25:
            raise ValueError
        bj = broadcaster.encode("ascii")
        if (
            type(bno) is not int
            or not 0 < bno <= 0xFFFFFFFF
            or not 0 <= category <= 0xFFFFFFFF
            or not bj
            or b"\0" in bj
            or not isinstance(ticket, bytes)
            or not 0 < len(ticket) <= 4096
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise GridError("gateway_certificate_fields_invalid") from None
    body = bytearray(0x47 + len(ticket))
    struct.pack_into("<II", body, 0, bno, 1)
    body[0x21 : 0x21 + len(bj)] = bj
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
    login = login_packet(guid, session.cookie, session.broadcaster)
    certificate = certificate_packet(session.bno, session.broadcaster, session.category, session.ticket)
    sock = network.connect(session.gateway)

    def receive():
        return read_frame(sock, "gateway", network.remaining(4) if hasattr(network, "remaining") else 4)

    try:
        payload = service6(guid, now, counter)
        network.send(sock, gateway_packet(0x3DF, struct.pack("<II", 6, len(payload)) + payload))
        network.send(sock, login)
        validate_login(receive())
        report("gateway_login_accepted")
        network.send(sock, certificate)
        reply = receive()
        if reply.command != 0x415 or reply.result < 0:
            raise GridError("gateway_certificate_denied")
        from .center import Credentials

        credentials = Credentials.parse(reply.body, guid, session.bno, session.stream_password)
        report("gateway_certificate_received")
        return credentials
    finally:
        network.release(sock)
