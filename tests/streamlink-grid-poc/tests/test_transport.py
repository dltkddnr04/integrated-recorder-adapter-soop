# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors

import sys
from pathlib import Path

import pytest
from streamlink import Streamlink
from streamlink.exceptions import StreamError
from streamlink.stream.stream import Stream

from soop_grid.stream import OfficialHLSFallback, SoopAutoStream


@pytest.fixture(autouse=True)
def block_http(monkeypatch):
    from streamlink.session.http import HTTPSession

    def unexpected_request(*args, **kwargs):
        raise AssertionError("offline transport tests must not issue HTTP requests")

    monkeypatch.setattr(HTTPSession, "request", unexpected_request)


def loaded_official(session, monkeypatch):
    # Loading built-ins recreates their classes; patch the actual delegated class.
    session.plugins.load_builtin()
    official = session.plugins.get_loaded()["soop"]
    monkeypatch.setattr(session.plugins, "load_builtin", lambda: True)
    return official


class FakeStream:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.opened, self.cleared = result, error, 0, False

    def open(self):
        self.opened += 1
        if self.error:
            raise self.error
        return self.result

    def clear(self):
        self.cleared = True


@pytest.mark.parametrize("code", ["tcp_connect_failed", "network_read_failed", "parent_media_stalled"])
def test_auto_falls_back_only_for_initial_transport_failure(code):
    error = StreamError("SOOP Grid: " + code)
    error.grid_error_code = code
    grid, hls = FakeStream(error=error), FakeStream(result="hls-reader")
    auto = SoopAutoStream(Streamlink(), grid, hls)
    assert auto.open() == "hls-reader"
    assert grid.opened == hls.opened == 1 and hls.cleared


@pytest.mark.parametrize(
    "code", ["public_watch_access_denied", "center_access_denied", "h264_timing_unconfirmed"]
)
def test_auto_never_falls_back_for_auth_or_media_rejection(code):
    error = StreamError("SOOP Grid: " + code)
    error.grid_error_code = code
    grid, hls = FakeStream(error=error), FakeStream(result="must-not-open")
    with pytest.raises(StreamError):
        SoopAutoStream(Streamlink(), grid, hls).open()
    assert grid.opened == 1 and hls.opened == 0 and hls.cleared


def test_auto_success_clears_deferred_credentials_without_hls_attempt():
    fallback = FakeStream(result="not-used")
    assert SoopAutoStream(Streamlink(), FakeStream(result="grid-reader"), fallback).open() == "grid-reader"
    assert fallback.opened == 0 and fallback.cleared


def test_auto_never_splices_hls_after_grid_output_and_read_failure():
    from soop_grid.bootstrap import Config
    from soop_grid.protocol import GridError
    from soop_grid.stream import SoopGridReader, SoopGridStream

    session = Streamlink()

    def runner(config, network, emit, report, stats):
        emit(b"G" * 188)
        raise GridError("network_read_timeout")

    reader = SoopGridReader(SoopGridStream(session, Config("fixture_channel", "12345")), runner).open()
    fallback = FakeStream(result="must-not-open")
    output = SoopAutoStream(session, FakeStream(result=reader), fallback).open()
    assert output.read(188) == b"G" * 188
    with pytest.raises(OSError, match="network_read_timeout"):
        output.read(1)
    assert fallback.opened == 0 and fallback.cleared
    assert not reader.thread.is_alive()


def test_hls_mode_delegates_to_official_plugin_and_auto_is_lazy(monkeypatch):
    session = Streamlink()
    session.plugins.load_path(str(Path(__file__).resolve().parents[1] / "plugins"))
    plugin_class = session.plugins.get_loaded()["soop_grid"]
    module = sys.modules[plugin_class.__module__]
    calls = []

    class HLS(Stream):
        __shortname__ = "hls"

    monkeypatch.setattr(
        module, "official_hls_streams", lambda *args: calls.append(args) or {"original": HLS(session)}
    )
    url = "https://play.sooplive.com/fixture_channel/12345"
    hls = plugin_class(
        session,
        url,
        options={
            "transport": "hls",
            "grid-username": "fixture_user",
            "grid-password": "fixture-password",
            "grid-stream-password": "fixture-pass",
            "grid-purge-credentials": True,
        },
    ).streams()
    assert hls["original"].shortname() == "hls" and len(calls) == 1
    assert calls[0][2:] == ("fixture_user", "fixture-password", "fixture-pass", True)
    auto = plugin_class(session, url, options={"transport": "auto"}).streams()
    assert auto["original"].shortname() == "auto" and len(calls) == 1


def test_auto_fallback_preserves_grid_credentials_and_forwards_to_builtin():
    from soop_grid.stream import OfficialHLSFallback

    received = {}

    class Plugins:
        def load_builtin(self):
            pass

        def get_loaded(self):
            return {"soop": Soop}

    class Session:
        plugins = Plugins()

    class Soop:
        def __init__(self, _session, _url, options):
            received.update(options)

        def streams(self):
            return {"original": FakeStream(result="reader")}

    session = Session()
    fallback = OfficialHLSFallback(
        session,
        "https://play.sooplive.com/fixture_channel/12345",
        "fixture_user",
        "fixture-password",
        "fixture-pass",
        True,
    )
    assert fallback.open() == "reader"
    assert received == {
        "username": "fixture_user",
        "password": "fixture-password",
        "stream-password": "fixture-pass",
        "purge-credentials": True,
    }
    assert fallback.account == fallback.password == fallback.stream_password == ""


def test_official_hls_fallback_redacts_and_clears_credentials():
    fallback = OfficialHLSFallback(Streamlink(), "fixture-url", "account", "secret", "stream-secret")
    assert "secret" not in repr(fallback) and "stream-secret" not in repr(fallback)
    fallback.clear()
    assert fallback.password == fallback.stream_password == fallback.account == ""


def test_real_official_fallback_uses_best_quality_without_original(monkeypatch):
    import io

    session = Streamlink()
    session.set_option("no-plugin-cache", True)
    Soop = loaded_official(session, monkeypatch)
    opened = []

    class HLS(Stream):
        __shortname__ = "hls"

        def __init__(self, quality):
            super().__init__(session)
            self.quality = quality

        def open(self):
            opened.append(self.quality)
            return io.BytesIO(self.quality.encode())

    monkeypatch.setattr(Soop, "_get_streams", lambda self: {"360p": HLS("360p"), "1080p": HLS("1080p")})
    fallback = OfficialHLSFallback(session, "https://play.sooplive.com/fixture_channel/12345", "", "", "")
    with fallback.open() as output:
        assert output.read() == b"1080p"
    assert opened == ["1080p"]


@pytest.mark.parametrize("stage", ["enumerate", "open"])
def test_real_cli_fallback_errors_are_stream_errors_without_private_text(monkeypatch, stage):
    from streamlink.exceptions import PluginError
    from streamlink_cli.main import open_stream

    session = Streamlink()
    session.set_option("no-plugin-cache", True)
    Soop = loaded_official(session, monkeypatch)

    class BrokenHLS(Stream):
        __shortname__ = "hls"

        def open(self):
            raise StreamError("fixture-private-hls-url-token")

    def enumerate_streams(self):
        if stage == "enumerate":
            raise PluginError("fixture-private-account-password")
        return {"original": BrokenHLS(session)}

    monkeypatch.setattr(Soop, "_get_streams", enumerate_streams)
    fallback = OfficialHLSFallback(
        session, "https://play.sooplive.com/fixture_channel/12345", "fixture_user", "secret", ""
    )
    with pytest.raises(StreamError) as caught:
        open_stream(fallback)
    assert "fixture-private" not in str(caught.value)
    assert fallback.account == fallback.password == fallback.stream_password == ""
    with pytest.raises(StreamError, match="single-use"):
        fallback.open()


@pytest.mark.parametrize("transport", ["hls", "auto"])
def test_official_delegation_never_changes_a_hyphenated_target(monkeypatch, transport):
    from streamlink.exceptions import PluginError

    session = Streamlink()
    session.set_option("no-plugin-cache", True)
    Soop = loaded_official(session, monkeypatch)
    session.plugins.load_path(str(Path(__file__).resolve().parents[1] / "plugins"))
    url = "https://play.sooplive.com/fixture-channel/12345"
    plugin_class = session.plugins.get_loaded()["soop_grid"]
    visited = []

    def enumerate_streams(self):
        visited.append(self.match.groupdict())
        return {}

    monkeypatch.setattr(Soop, "_get_streams", enumerate_streams)
    if transport == "hls":
        with pytest.raises(PluginError):
            plugin_class(session, url, options={"transport": "hls"}).streams()
    else:
        stream = plugin_class(session, url).streams()["original"]
        error = StreamError("Grid unavailable")
        error.grid_error_code = "tcp_connect_failed"
        stream.grid = FakeStream(error=error)
        with pytest.raises(StreamError):
            stream.open()
    assert visited == [], "official matcher must not silently truncate the requested channel"


@pytest.mark.parametrize(
    ("code", "allowed"),
    [
        ("tcp_connect_failed", True),
        ("network_read_timeout", True),
        ("parent_access_denied", False),
        ("h264_timing_unconfirmed", False),
    ],
)
def test_real_grid_open_error_controls_cli_fallback(monkeypatch, code, allowed):
    import io
    import soop_grid.stream as integration
    from soop_grid.bootstrap import Config
    from soop_grid.protocol import GridError
    from streamlink_cli.main import open_stream

    session = Streamlink()
    session.set_option("no-plugin-cache", True)
    official = loaded_official(session, monkeypatch)
    calls = []

    class HLS(Stream):
        __shortname__ = "hls"

        def open(self):
            calls.append("open")
            return io.BytesIO(b"offline-hls")

    def enumerate_streams(self):
        calls.append("enumerate")
        return {"original": HLS(session)}

    monkeypatch.setattr(official, "_get_streams", enumerate_streams)
    reader_class = integration.SoopGridReader

    def run(config, network, emit, report, stats):
        raise GridError(code)

    monkeypatch.setattr(integration, "SoopGridReader", lambda stream: reader_class(stream, runner=run))
    config = Config("fixture_channel", "12345", account="fixture_user", account_password="fixture-pass")
    grid = integration.SoopGridStream(session, config)
    fallback = OfficialHLSFallback(
        session, "https://play.sooplive.com/fixture_channel/12345", "fixture_user", "fixture-pass", ""
    )
    auto = SoopAutoStream(session, grid, fallback)
    if allowed:
        output, prebuffer = open_stream(auto)
        assert prebuffer == b"offline-hls"
        output.close()
        assert calls == ["enumerate", "open"]
    else:
        with pytest.raises(StreamError, match=code):
            open_stream(auto)
        assert calls == []
    assert config.account == config.account_password == ""
    assert fallback.account == fallback.password == ""
    with pytest.raises(StreamError, match="single-session"):
        auto.open()


@pytest.mark.parametrize("transport", ["hls", "auto"])
def test_real_official_constructor_receives_credentials_and_purge(monkeypatch, transport):
    import io

    session = Streamlink()
    session.set_option("no-plugin-cache", True)
    official = loaded_official(session, monkeypatch)
    purged, received = [], []
    monkeypatch.setattr(official, "clear_cookies", lambda self: purged.append(True))

    class HLS(Stream):
        __shortname__ = "hls"

        def open(self):
            return io.BytesIO(b"offline-hls")

    def enumerate_streams(self):
        received.append(
            (
                self.match.groupdict(),
                tuple(
                    self.get_option(k)
                    for k in ("username", "password", "stream-password", "purge-credentials")
                ),
            )
        )
        return {"original": HLS(session)}

    monkeypatch.setattr(official, "_get_streams", enumerate_streams)
    session.plugins.load_path(str(Path(__file__).resolve().parents[1] / "plugins"))
    cls = session.plugins.get_loaded()["soop_grid"]
    url = "https://play.afreecatv.com/fixture_channel/12345?player=1#live"
    stream = cls(
        session,
        url,
        options={
            "transport": transport,
            "grid-username": "fixture_user",
            "grid-password": "fixture-pass",
            "grid-stream-password": "fixture-only",
            "grid-purge-credentials": True,
        },
    ).streams()["original"]
    if transport == "auto":
        assert not received and not purged
        error = StreamError("Grid unavailable")
        error.grid_error_code = "tcp_connect_failed"
        stream.grid = FakeStream(error=error)
    with stream.open() as output:
        assert output.read() == b"offline-hls"
    assert purged == [True]
    assert received == [
        (
            {"channel": "fixture_channel", "bno": "12345"},
            ("fixture_user", "fixture-pass", "fixture-only", True),
        )
    ]
