# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import pytest
import requests

from soop_grid.auth import HTTP, LIVE
from soop_grid.bootstrap import Config, bootstrap
from soop_grid.protocol import GridError


def test_account_login_form_and_session_confirmation(monkeypatch):
    http = HTTP()
    calls = []

    def response(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"RESULT": 1} if len(calls) == 1 else {"CHANNEL": {"LOGIN_ID": "fixture_account"}}

    monkeypatch.setattr(http, "json", response)
    http.login("fixture_account", "fixture-password")
    assert calls[0][2]["data"]["isSavePw"] == "false"
    assert calls[0][2]["data"]["szPassword"] == "fixture-password"
    assert calls[1][1] == "https://afevent2.sooplive.com/api/get_private_info.php"
    http.close()


@pytest.mark.parametrize(
    "responses",
    [
        [{"RESULT": -1}],
        [{"RESULT": 1}, {"CHANNEL": {"LOGIN_ID": ""}}],
    ],
)
def test_account_denial_or_expired_session_requires_normal_reauthentication(monkeypatch, responses):
    http = HTTP()
    replies = iter(responses)
    monkeypatch.setattr(http, "json", lambda *args, **kwargs: next(replies))
    with pytest.raises(GridError):
        http.login("fixture_account", "fixture-password")
    http.close()


def test_cookie_jar_does_not_copy_account_cookies_to_other_domain():
    http = HTTP()
    http.session.cookies.set("AuthTicket", "fixture-cookie", domain=".sooplive.com", path="/", secure=True)
    assert http.cookie("AuthTicket") == "fixture-cookie"
    assert http.cookie("AuthTicket", "https://api.m.sooplive.co.kr/") == ""
    http.close()


def test_http_raw_exception_and_redirect_do_not_leak(monkeypatch):
    http = HTTP()

    def fail(*args, **kwargs):
        raise requests.ConnectionError("fixture-private-token")

    monkeypatch.setattr(http.session, "request", fail)
    with pytest.raises(GridError) as err:
        http.request("GET", LIVE)
    assert "fixture-private-token" not in str(err.value)

    class Response:
        status_code = 302
        url = LIVE

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(http.session, "request", lambda *a, **kw: Response())
    with pytest.raises(GridError, match="http_rejected"):
        http.request("GET", LIVE)
    with pytest.raises(GridError, match="destination"):
        http.request("POST", "https://example.org/", data={"secret": "fixture"})
    http.close()


class FakeHTTP:
    def __init__(self, protected=False, adult=True, watch_result=1):
        self.calls = []
        self.closed = False
        self.protected = protected
        self.adult = adult
        self.watch_result = watch_result

    def login(self, *args):
        self.calls.append(("login",))

    def cookie(self, name):
        return "fixture-cookie"

    def request(self, method, url, **kwargs):
        return b"window.nBroadNo = 12345;"

    def close(self):
        self.closed = True

    def json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "main_broad_list" in url:
            return {
                "broad": [
                    {
                        "user_id": "fixture_channel",
                        "broad_no": "12345",
                        "is_password": "N",
                        "subscription_only": "0",
                    }
                ]
            }
        if url == LIVE:
            return {
                "CHANNEL": {
                    "RESULT": 1,
                    "BNO": "12345",
                    "BJID": "fixture_channel",
                    "BPWD": "Y" if self.protected else "N",
                    "ACHK": 1 if self.adult else 0,
                    "TK": "fixture-tk",
                    "FTK": "fixture-ftk",
                    "CATE": "123",
                    "GWIP": "8.8.8.8",
                    "GWPT": 10000,
                    "CTIP": "1.1.1.1",
                    "CTPT": 19000,
                }
            }
        return {
            "result": self.watch_result,
            "data": {
                "fan_ticket": "fixture-watch",
                "gateway_ip": "8.8.8.8",
                "gateway_port": 10000,
                "relay_ip": "1.1.1.1",
                "relay_port": 19000,
            },
        }


def test_native_password_only_in_center_session_and_no_secret_repr():
    http = FakeHTTP(protected=True)
    config = Config(
        "fixture_channel",
        "12345",
        account="fixture_account",
        account_password="fixture-password",
        stream_password="fixture-only",
    )
    session = bootstrap(config, lambda _: None, http_factory=lambda: http)
    assert session.stream_password == b"fixture-only" and session.cookie == "fixture-tk"
    live = next(x for x in http.calls if len(x) == 3 and x[1] == LIVE)
    assert live[2]["data"]["pwd"] == ""
    assert config.account_password == config.stream_password == "" and http.closed
    assert "fixture" not in repr(session)
    session.clear()
    assert not session.ticket and not session.cookie


def test_public_watch_and_channel_url_resolution():
    http = FakeHTTP()
    session = bootstrap(Config("fixture_channel"), lambda _: None, http_factory=lambda: http)
    assert session.bno == 12345 and session.ticket == b"fixture-watch"
    watch = next(x for x in http.calls if len(x) == 3 and "broad/a/watch" in x[1])
    assert watch[2]["headers"] == {"Cookie": "_au=fixture-cookie"}
    assert "pwd" not in watch[2]["data"]


def test_adult_denial_and_watch_denial_never_fall_through():
    http = FakeHTTP(adult=False)
    config = Config(
        "fixture_channel",
        "12345",
        account="fixture_account",
        account_password="fixture-password",
        require_adult=True,
    )
    with pytest.raises(GridError, match="adult_check"):
        bootstrap(config, lambda _: None, http_factory=lambda: http)
    assert http.closed and not config.account_password
    http = FakeHTTP(watch_result=-1)
    with pytest.raises(GridError, match="watch_access_denied"):
        bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=lambda: http)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stream_password": "fixture-only"},
        {"require_adult": True},
        {"account": "fixture_account"},
        {"account": "fixture_account", "account_password": "fixture", "stream_password": "bad\x11"},
    ],
)
def test_invalid_secret_options(kwargs):
    with pytest.raises(GridError):
        Config("fixture_channel", "12345", **kwargs).validate()
