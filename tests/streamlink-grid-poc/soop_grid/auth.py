# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""First-party account login with a private, non-persistent Cookie Jar."""

import json
import re
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import requests

from .protocol import GridError

PLAY = "https://play.sooplive.com"
LIVE = "https://live.sooplive.com/afreeca/player_live_api.php"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
ALLOWED = {
    "play.sooplive.com",
    "live.sooplive.com",
    "login.sooplive.com",
    "afevent2.sooplive.com",
    "api.m.sooplive.co.kr",
}


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


def destination(url):
    try:
        if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise GridError("http_destination_invalid") from None


class HTTP:
    def __init__(self, stop=None):
        self.stop = stop
        self.session = requests.Session()
        # Do not load ambient netrc credentials or proxy settings for login traffic.
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": UA, "Origin": PLAY, "Referer": PLAY + "/"})

    def request(self, method, url, **kwargs):
        if self.stop is not None and self.stop.is_set():
            raise GridError("receiver_closed")
        destination(url)
        deadline = time.monotonic() + 30

        def check():
            if self.stop is not None and self.stop.is_set():
                raise GridError("receiver_closed")
            if time.monotonic() >= deadline:
                raise GridError("http_deadline_reached")

        try:
            with self.session.request(
                method, url, timeout=(3, 8), allow_redirects=False, stream=True, **kwargs
            ) as resp:
                check()
                if resp.status_code != 200 or resp.url != url:
                    raise GridError("first_party_http_rejected")
                data = bytearray()
                for chunk in resp.iter_content(1024):
                    check()
                    data.extend(chunk)
                    if len(data) > 2 << 20:
                        raise GridError("http_response_limit")
                check()
                return bytes(data)
        except requests.RequestException:
            raise GridError("first_party_http_failed") from None

    def json(self, method, url, **kwargs):
        def reject_constant(value):
            raise ValueError

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        try:
            root = json.loads(
                self.request(method, url, **kwargs),
                parse_constant=reject_constant,
                object_pairs_hook=unique_object,
            )
            if not isinstance(root, dict):
                raise ValueError
            return root
        except (ValueError, UnicodeError, RecursionError):
            raise GridError("first_party_json_invalid") from None

    def cookie(self, name, target=PLAY + "/"):
        destination(target)
        prepared = self.session.prepare_request(requests.Request("GET", target))
        jar = SimpleCookie()
        try:
            jar.load(prepared.headers.get("Cookie", ""))
        except Exception:
            raise GridError("scoped_cookie_invalid") from None
        return jar[name].value if name in jar else ""

    def login(self, account, password, referer=None):
        if (
            not identifier(account)
            or not isinstance(password, str)
            or not password
            or len(password) > 1024
            or "\0" in password
        ):
            raise GridError("account_input_invalid")
        if referer is not None:
            if destination(referer).hostname != "play.sooplive.com":
                raise GridError("http_destination_invalid")
            self.session.headers["Referer"] = referer
        result = self.json(
            "POST",
            "https://login.sooplive.com/app/LoginAction.php",
            data={
                "szWork": "login",
                "szType": "json",
                "szUid": account,
                "szPassword": password,
                "isSaveId": "true",
                "isSavePw": "false",
                "isSaveJoin": "false",
                "isLoginRetain": "Y",
            },
        )
        if str(result.get("RESULT")) != "1":
            raise GridError("account_login_denied_or_verification_required")
        check = self.json("GET", "https://afevent2.sooplive.com/api/get_private_info.php")
        channel = check.get("CHANNEL")
        if not isinstance(channel, dict) or channel.get("LOGIN_ID") != account:
            raise GridError("account_session_unconfirmed")

    def close(self):
        self.session.cookies.clear()
        self.session.close()
