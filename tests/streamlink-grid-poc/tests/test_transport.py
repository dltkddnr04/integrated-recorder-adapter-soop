# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors

import sys
from pathlib import Path

import pytest
from streamlink import Streamlink
from streamlink.exceptions import StreamError
from streamlink.stream.stream import Stream

from soop_grid.stream import OfficialHLSFallback, SoopAutoStream


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
        session, "fixture-url", "fixture_user", "fixture-password", "fixture-pass", True
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
