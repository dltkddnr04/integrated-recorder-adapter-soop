# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""First-party account login with a private, non-persistent Cookie Jar."""

import json
import re
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


class HTTP:
    def __init__(self, stop=None):
        self.stop = stop
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Origin": PLAY, "Referer": PLAY + "/"})

    def request(self, method, url, **kwargs):
        if self.stop is not None and self.stop.is_set():
            raise GridError("receiver_closed")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            raise GridError("http_destination_invalid")
        try:
            with self.session.request(
                method, url, timeout=(3, 8), allow_redirects=False, stream=True, **kwargs
            ) as resp:
                if resp.status_code != 200 or resp.url != url:
                    raise GridError("first_party_http_rejected")
                data = bytearray()
                for chunk in resp.iter_content(65536):
                    if self.stop is not None and self.stop.is_set():
                        raise GridError("receiver_closed")
                    data.extend(chunk)
                    if len(data) > 2 << 20:
                        raise GridError("http_response_limit")
                return bytes(data)
        except requests.RequestException:
            raise GridError("first_party_http_failed") from None

    def json(self, method, url, **kwargs):
        try:
            root = json.loads(self.request(method, url, **kwargs))
            if not isinstance(root, dict):
                raise ValueError
            return root
        except (ValueError, UnicodeError):
            raise GridError("first_party_json_invalid") from None

    def cookie(self, name, target=PLAY + "/"):
        prepared = self.session.prepare_request(requests.Request("GET", target))
        jar = SimpleCookie()
        try:
            jar.load(prepared.headers.get("Cookie", ""))
        except Exception:
            raise GridError("scoped_cookie_invalid") from None
        return jar[name].value if name in jar else ""

    def login(self, account, password):
        if not identifier(account) or not password or len(password) > 1024 or "\0" in password:
            raise GridError("account_input_invalid")
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
        if check.get("CHANNEL", {}).get("LOGIN_ID") != account:
            raise GridError("account_session_unconfirmed")

    def close(self):
        self.session.cookies.clear()
        self.session.close()
