# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from streamlink import Streamlink
from streamlink.exceptions import PluginError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "url",
    [
        "https://play.sooplive.com/fixture_channel",
        "https://play.sooplive.com/fixture_channel/12345",
        "https://play.sooplive.co.kr/fixture_channel/12345",
        "https://play.afreecatv.com/fixture_channel/12345",
    ],
)
def test_actual_plugin_discovery_and_grid_stream_enumeration(url):
    session = Streamlink()
    assert session.plugins.load_path(str(ROOT / "plugins"))
    name, cls, resolved = session.resolve_url(url)
    assert name == "soopgrid" and cls.__name__ == "SoopGridPlugin"
    plugin = cls(session, resolved)
    streams = plugin.streams()
    assert streams["original"].shortname() == "auto"
    assert streams["original"].grid.config.broadcaster == "fixture_channel"
    assert cls(session, resolved, options={"transport": "grid"}).streams()["original"].shortname() == "grid"
    session.plugins.load_builtin()
    assert session.plugins.get_loaded()["soop"].__name__ == "Soop"
    assert session.plugins.get_loaded()["soopgrid"] is cls


def test_modular_cli_sideload_from_unrelated_directory(tmp_path):
    env = os.environ.copy()
    for k in (
        "PYTHONPATH",
        "SOOP_ACCOUNT_ID",
        "SOOP_ACCOUNT_PASSWORD",
        "SOOP_STREAM_PASSWORD",
        "SOOP_GRID_CAPTURE_DIR",
    ):
        env.pop(k, None)
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlink_cli",
            "--plugin-dir",
            str(ROOT / "plugins"),
            "--json",
            "https://play.sooplive.com/fixture_channel/12345",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert p.returncode == 0, p.stderr
    root = json.loads(p.stdout)
    assert root["streams"]["original"]["type"] == "auto"


def test_cli_debug_enumeration_does_not_log_secret_environment_values(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env.update(
        SOOP_ACCOUNT_ID="fixture_account",
        SOOP_ACCOUNT_PASSWORD="fixture-private-password",
        SOOP_STREAM_PASSWORD="fixture-only",
    )
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlink_cli",
            "--plugin-dirs",
            str(ROOT / "plugins"),
            "--loglevel",
            "debug",
            "--json",
            "https://play.sooplive.com/fixture_channel/12345",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert p.returncode == 0
    output = p.stdout + p.stderr
    assert "fixture-private-password" not in output and "fixture-only" not in output


def test_secrets_are_not_serialized_and_consumed_from_environment(monkeypatch):
    monkeypatch.setenv("SOOP_ACCOUNT_ID", "fixture_account")
    monkeypatch.setenv("SOOP_ACCOUNT_PASSWORD", "fixture-private-password")
    monkeypatch.setenv("SOOP_STREAM_PASSWORD", "fixture-only")
    session = Streamlink()
    session.plugins.load_path(str(ROOT / "plugins"))
    _, cls, url = session.resolve_url("https://play.sooplive.com/fixture_channel/12345")
    stream = cls(session, url).streams()["original"]
    assert stream.grid.config.account_password == "fixture-private-password"
    assert "SOOP_ACCOUNT_PASSWORD" not in os.environ and "SOOP_STREAM_PASSWORD" not in os.environ
    assert "fixture" not in repr(stream) and "fixture" not in stream.json
    stream.grid.config.clear()


@pytest.mark.parametrize("duration", [0, -1])
def test_cli_duration_rejects_nonpositive_limits(duration):
    session = Streamlink()
    session.plugins.load_path(str(ROOT / "plugins"))
    _, cls, url = session.resolve_url("https://play.sooplive.com/fixture_channel/12345")
    with pytest.raises(PluginError, match="capture_duration_bounds"):
        cls(session, url, options={"grid-duration": duration}).streams()


def test_cli_duration_default_is_unlimited_and_explicit_values_are_not_capped():
    session = Streamlink()
    session.plugins.load_path(str(ROOT / "plugins"))
    url = "https://play.sooplive.com/fixture_channel/12345"
    cls = session.resolve_url(url)[1]
    assert cls(session, url).streams()["original"].grid.config.duration is None
    assert (
        cls(session, url, options={"grid-duration": 3600}).streams()["original"].grid.config.duration == 3600
    )


def test_official_streamlink_python_sources_match_wheel_record():
    distribution = importlib.metadata.distribution("streamlink")
    verified = 0
    for f in distribution.files:
        if str(f).endswith(".py") and str(f).startswith(("streamlink/", "streamlink_cli/")) and f.hash:
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(distribution.locate_file(f).read_bytes()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert actual == f.hash.value, str(f)
            verified += 1
    assert verified > 100
