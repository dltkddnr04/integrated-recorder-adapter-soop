# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import pytest
import requests

from soop_grid.auth import HTTP, LIVE
from soop_grid.bootstrap import Config, Session, bootstrap
from soop_grid.protocol import GridError


def test_session_discards_bootstrap_secrets_after_certificate_issue():
    session = Session(
        "fixture_account",
        12345,
        "1",
        ("8.8.8.8", 1),
        ("8.8.4.4", 2),
        "fixture-cookie",
        b"fixture-ticket",
        b"fixture-stream-password",
    )
    session.clear_gateway_auth()
    assert session.cookie == "" and session.ticket == b"" and session.stream_password == b""
    assert session.center == ("8.8.4.4", 2) and session.bno == 12345


def test_account_login_form_and_session_confirmation(monkeypatch):
    http = HTTP()
    calls = []

    def response(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"RESULT": 1} if len(calls) == 1 else {"CHANNEL": {"LOGIN_ID": "fixture_account"}}

    monkeypatch.setattr(http, "json", response)
    http.login("fixture_account", "fixture-password", "https://play.sooplive.com/fixture_channel/12345")
    assert calls[0][2]["data"]["isSavePw"] == "false"
    assert calls[0][2]["data"]["szPassword"] == "fixture-password"
    assert calls[0][2]["data"] == {
        "szWork": "login",
        "szType": "json",
        "szUid": "fixture_account",
        "szPassword": "fixture-password",
        "isSaveId": "true",
        "isSavePw": "false",
        "isSaveJoin": "false",
        "isLoginRetain": "Y",
    }
    assert http.session.headers["Origin"] == "https://play.sooplive.com"
    assert http.session.headers["Referer"] == "https://play.sooplive.com/fixture_channel/12345"
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


def test_exact_public_target_never_requests_a_listing():
    class Unlisted(FakeHTTP):
        def json(self, method, url, **kwargs):
            assert "main_broad_list" not in url
            return super().json(method, url, **kwargs)

    session = bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=Unlisted)
    assert session.bno == 12345 and session.ticket


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"BJID": "different"}, "live_target_changed"),
        ({"BNO": "12346"}, "live_target_changed"),
        ({"RESULT": -1}, "metadata_denied_or_offline"),
        ({"BPWD": "Y"}, "password_state_mismatch"),
        ({"BPWD": None}, "password_state_unknown"),
        ({"BPWD": "unconfirmed"}, "password_state_unknown"),
        ({"SUBSCRIPTION_ONLY": "1"}, "public_policy"),
        ({"PAID": "Y"}, "public_policy"),
        ({"BNO": None}, "live_target_changed"),
    ],
)
def test_public_metadata_fails_closed(changes, code):
    class Metadata(FakeHTTP):
        def json(self, method, url, **kwargs):
            result = super().json(method, url, **kwargs)
            if url == LIVE:
                result["CHANNEL"].update(changes)
            else:
                pytest.fail("Watch must not be requested after invalid metadata")
            return result

    with pytest.raises(GridError, match=code):
        bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=Metadata)


@pytest.mark.parametrize("channel", [None, [], "invalid", {}])
def test_invalid_channel_schema(channel):
    class Bad(FakeHTTP):
        def json(self, *args, **kwargs):
            return {"CHANNEL": channel}

    with pytest.raises(GridError):
        bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=Bad)


@pytest.mark.parametrize("update", [{"fan_ticket": ""}, {"broad_no": "999"}, {"bj_id": "other"}])
def test_watch_must_issue_ticket_for_exact_target(update):
    class Bad(FakeHTTP):
        def json(self, method, url, **kwargs):
            root = super().json(method, url, **kwargs)
            if url != LIVE:
                root["data"].update(update)
            return root

    with pytest.raises(GridError):
        bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=Bad)


def test_public_target_normalization():
    session = bootstrap(Config("FIXTURE_CHANNEL", "0000012345"), lambda _: None, http_factory=FakeHTTP)
    assert session.broadcaster == "fixture_channel" and session.bno == 12345


@pytest.mark.parametrize("bno", [0, 12345, "0", "4294967296", "-1", "123/45"])
def test_invalid_bno_rejected_before_http(bno):
    def no_http():
        pytest.fail("invalid target must not create an HTTP session")

    with pytest.raises(GridError):
        bootstrap(Config("fixture_channel", bno), lambda _: None, http_factory=no_http)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -float("inf")])
def test_duration_must_be_finite(duration):
    with pytest.raises(GridError, match="duration"):
        Config("fixture_channel", duration=duration).validate()


def test_bootstrap_clears_secrets_on_validation_and_factory_failure():
    for broadcaster in ("invalid/channel", "fixture_channel"):
        config = Config(broadcaster, account="fixture_account", account_password="fixture-secret")

        def fail():
            raise GridError("factory_failed")

        with pytest.raises(GridError):
            bootstrap(config, lambda _: None, http_factory=fail)
        assert not config.account_password and not config.account


@pytest.mark.parametrize("channel", [None, [], "fixture-secret"])
def test_login_session_schema_is_fixed_error(monkeypatch, channel):
    http = HTTP()
    replies = iter([{"RESULT": 1}, {"CHANNEL": channel}])
    monkeypatch.setattr(http, "json", lambda *a, **k: next(replies))
    try:
        with pytest.raises(GridError, match="session_unconfirmed"):
            http.login("fixture_account", "fixture-password")
    finally:
        http.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://live.sooplive.com:secret/",
        "https://[invalid/",
        "https://@live.sooplive.com/",
        "https://live.sooplive.com/#fragment",
    ],
)
def test_malformed_http_destinations_never_reach_transport(monkeypatch, url):
    http = HTTP()
    monkeypatch.setattr(http.session, "request", lambda *a, **k: pytest.fail("transport reached"))
    try:
        with pytest.raises(GridError, match="destination_invalid"):
            http.request("GET", url)
    finally:
        http.close()


def test_http_elapsed_budget_and_empty_body_cancellation(monkeypatch):
    import threading
    import soop_grid.auth as auth

    stop = threading.Event()
    http = HTTP(stop)

    class Response:
        status_code = 200
        url = LIVE
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def iter_content(self, size):
            yield b"a"
            yield b"b"

    response = Response()
    monkeypatch.setattr(http.session, "request", lambda *a, **k: response)
    ticks = iter([0, 0, 31, 31, 31])
    monkeypatch.setattr(auth.time, "monotonic", lambda: next(ticks))
    with pytest.raises(GridError, match="http_deadline"):
        http.request("GET", LIVE)
    assert response.closed

    monkeypatch.setattr(auth.time, "monotonic", lambda: 0)

    def empty(size):
        stop.set()
        return iter(())

    monkeypatch.setattr(response, "iter_content", empty)
    with pytest.raises(GridError, match="receiver_closed"):
        http.request("GET", LIVE)
    http.close()


@pytest.mark.parametrize("field,value", [("GWIP", 134744072), ("GWPT", True), ("CTPT", 19000.5)])
def test_bootstrap_endpoint_schema_rejects_coercion(field, value):
    class Bad(FakeHTTP):
        def json(self, method, url, **kwargs):
            root = super().json(method, url, **kwargs)
            if url == LIVE:
                root["CHANNEL"][field] = value
            return root

    with pytest.raises(GridError):
        bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=Bad)


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"result":1,"result":-1}',
        b'{"result":NaN}',
        b'{"result":Infinity}',
        b"[" * 2000 + b"]" * 2000,
        b"\xff",
    ],
)
def test_http_json_rejects_ambiguous_or_invalid_schema(monkeypatch, body):
    http = HTTP()
    monkeypatch.setattr(http, "request", lambda *a, **k: body)
    try:
        with pytest.raises(GridError, match="json_invalid"):
            http.json("GET", LIVE)
    finally:
        http.close()


def test_http_transport_bounds_and_cleanup(monkeypatch):
    import threading

    stop = threading.Event()
    http = HTTP(stop)
    assert http.session.trust_env is False
    calls = []

    class Response:
        status_code = 200
        url = LIVE
        closed = False
        chunks = [b"x" * 65536] * 33

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def iter_content(self, size):
            yield from self.chunks

    response = Response()

    def request(*a, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(http.session, "request", request)
    with pytest.raises(GridError, match="response_limit"):
        http.request("GET", LIVE)
    assert response.closed
    assert calls[0]["timeout"] == (3, 8)
    assert calls[0]["allow_redirects"] is False and calls[0]["stream"] is True
    response.chunks = [b"x" * (2 << 20)]
    assert len(http.request("GET", LIVE)) == 2 << 20
    stop.set()
    with pytest.raises(GridError, match="receiver_closed"):
        http.request("GET", LIVE)
    assert len(calls) == 2
    http.close()


@pytest.mark.parametrize("cookie", ["bad\r\nCookie: secret", "bad;other=secret", "bad\x11value"])
def test_public_cookie_is_validated_before_watch_request(cookie):
    class Bad(FakeHTTP):
        def cookie(self, name):
            return cookie

        def json(self, method, url, **kwargs):
            assert url == LIVE, "invalid cookie reached Watch headers"
            return super().json(method, url, **kwargs)

    with pytest.raises(GridError, match="cookie_missing_or_invalid"):
        bootstrap(Config("fixture_channel", "12345"), lambda _: None, http_factory=Bad)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"account": True, "account_password": 123},
        {"stream_password": 123},
        {"require_adult": "false"},
        {"user_agent": "x\r\nSecret: value"},
        {"referer": "https://example.org/"},
    ],
)
def test_config_types_and_headers_fail_before_http(kwargs):
    with pytest.raises(GridError):
        bootstrap(
            Config("fixture_channel", "12345", **kwargs),
            lambda _: None,
            http_factory=lambda: pytest.fail("invalid config reached HTTP"),
        )
