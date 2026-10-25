# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Exact-target public Watch or authenticated desktop TK/FTK bootstrap."""

import re
from dataclasses import dataclass, field

from .auth import HTTP, LIVE, PLAY, identifier
from .protocol import GridError, endpoint


@dataclass(repr=False)
class Config:
    broadcaster: str
    bno: str | None = None
    account: str = field(default="", repr=False)
    account_password: str = field(default="", repr=False)
    stream_password: str = field(default="", repr=False)
    require_adult: bool = False
    duration: float | None = None
    max_bytes: int | None = None
    capture_dir: str | None = field(default=None, repr=False)
    diagnostics_dir: str | None = field(default=None, repr=False)

    def validate(self):
        if not identifier(self.broadcaster) or (
            self.bno is not None
            and (not isinstance(self.bno, str) or not re.fullmatch(r"[0-9]{1,10}", self.bno))
        ):
            raise GridError("broadcast_target_invalid")
        self.broadcaster = self.broadcaster.lower()
        if self.bno is not None:
            if not 0 < int(self.bno) <= 0xFFFFFFFF:
                raise GridError("broadcast_number_bounds")
            self.bno = str(int(self.bno))
        if self.duration is not None and (type(self.duration) not in (int, float) or self.duration <= 0):
            raise GridError("capture_duration_bounds")
        if self.max_bytes is not None and (type(self.max_bytes) is not int or self.max_bytes <= 0):
            raise GridError("capture_byte_limit_bounds")
        if (
            bool(self.account) != bool(self.account_password)
            or (self.require_adult or self.stream_password)
            and not self.account
        ):
            raise GridError("account_credentials_required")
        if self.stream_password and (
            len(self.stream_password) > 12 or any(ord(c) < 32 or ord(c) > 126 for c in self.stream_password)
        ):
            raise GridError("stream_password_encoding_invalid")

    def clear(self):
        self.account_password = self.stream_password = ""


@dataclass(repr=False)
class Session:
    broadcaster: str
    bno: int
    category: str
    gateway: tuple = field(repr=False)
    center: tuple = field(repr=False)
    cookie: str = field(repr=False)
    ticket: bytes = field(repr=False)
    stream_password: bytes = field(default=b"", repr=False)

    def clear(self):
        self.cookie = ""
        self.ticket = self.stream_password = b""


def affirmative_no(value):
    return str(value).upper() in ("N", "0", "FALSE")


def bootstrap(config, report, http_factory=HTTP):
    config.validate()
    http = http_factory()
    try:
        if config.account:
            http.login(config.account, config.account_password)
            report("account_session_confirmed")
        bno = config.bno
        if not bno:
            page = http.request("GET", PLAY + "/" + config.broadcaster).decode("utf-8", "replace")
            match = re.search(r"window\.nBroadNo\s*=\s*([0-9]+);", page)
            if not match:
                raise GridError("channel_live_number_unavailable")
            bno = match[1]
        if not 0 < int(bno) <= 0xFFFFFFFF:
            raise GridError("broadcast_number_bounds")
        channel = http.json(
            "POST",
            LIVE,
            data={
                "from_api": "0",
                "mode": "landing",
                "player_type": "html5",
                "stream_type": "common",
                "type": "live",
                "bid": config.broadcaster,
                "bno": bno,
                "pwd": "",
            },
        ).get("CHANNEL", {})
        if not isinstance(channel, dict) or str(channel.get("RESULT")) != "1":
            raise GridError("live_metadata_denied_or_offline")
        if (
            str(channel.get("BNO")) != bno
            or str(channel.get("BJID", "")).lower() != config.broadcaster.lower()
        ):
            raise GridError("live_target_changed")
        if str(channel.get("BPWD")).upper() not in ("N", "Y", "0", "1", "FALSE", "TRUE"):
            raise GridError("password_state_unknown")
        protected = not affirmative_no(channel["BPWD"])
        if protected != bool(config.stream_password):
            raise GridError("stream_password_state_mismatch")
        if config.require_adult and str(channel.get("ACHK")) != "1":
            raise GridError("adult_check_unconfirmed")
        if not config.account:
            # Live has no verified equivalent of listing.subscription_only.
            # Refuse explicit restrictions; exact-target Watch is authoritative
            # for policies not exposed here. Never infer permission from absence.
            for name in ("subscription_only", "SUBSCRIPTION_ONLY", "ISPAID", "PAID"):
                if name in channel and not affirmative_no(channel[name]):
                    raise GridError("public_policy_restricted_or_unknown")
        gate = endpoint(channel.get("GWIP"), channel.get("GWPT"))
        center = endpoint(channel.get("CTIP"), channel.get("CTPT"))
        if config.account:
            cookie = channel.get("TK") or http.cookie("AuthTicket")
            ticket = channel.get("FTK")
            report("native_tk_ftk_metadata_received")
        else:
            http.request("GET", PLAY + "/" + config.broadcaster)
            cookie = http.cookie("_au")
            headers = {"Cookie": "_au=" + cookie} if cookie else {}
            root = http.json(
                "POST",
                "https://api.m.sooplive.co.kr/broad/a/watch",
                headers=headers,
                data={"bj_id": config.broadcaster, "broad_no": bno, "player_type": "html5"},
            )
            data = root.get("data", {})
            if str(root.get("result")) != "1":
                raise GridError("public_watch_access_denied")
            if not isinstance(data, dict):
                raise GridError("public_watch_schema_invalid")
            for name, expected in (("broad_no", bno), ("bj_id", config.broadcaster)):
                if name in data and str(data[name]).lower() != expected.lower():
                    raise GridError("watch_target_changed")
            ticket = data.get("fan_ticket")
            gate = endpoint(data.get("gateway_ip"), data.get("gateway_port"))
            center = endpoint(data.get("relay_ip"), data.get("relay_port"))
            report("public_watch_ticket_received")
        if (
            not isinstance(cookie, str)
            or not 0 < len(cookie) <= 4096
            or any(c in cookie for c in "\0\x11\x12\r\n")
        ):
            raise GridError("gateway_cookie_missing_or_invalid")
        if not isinstance(ticket, str) or not 0 < len(ticket.encode()) <= 4096:
            raise GridError("gateway_ticket_missing_or_invalid")
        return Session(
            config.broadcaster,
            int(bno),
            str(channel.get("CATE", "")),
            gate,
            center,
            cookie,
            ticket.encode(),
            config.stream_password.encode("ascii"),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise GridError("bootstrap_schema_invalid") from None
    finally:
        config.clear()
        http.close()
