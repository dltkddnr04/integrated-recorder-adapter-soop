# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from streamlink import Streamlink

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle():
    return load(ROOT / "dist" / "soop_grid.py", "soop_singlefile_unit")


def test_reproducible_build_and_no_runtime_loader(tmp_path):
    builder = load(ROOT / "tools" / "build_singlefile.py", "soop_builder_unit")
    first = builder.build().encode("utf-8")
    second = builder.build().encode("utf-8")
    assert first == second == (ROOT / "dist" / "soop_grid.py").read_bytes()
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()
    tree = ast.parse(first, feature_version=(3, 10))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not node.level and not (node.module or "").startswith("soop_grid")
        if isinstance(node, ast.Import):
            assert all(not a.name.startswith("soop_grid") for a in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("exec", "eval", "__import__")
    assert b"sys.path" not in first and b"zipimport" not in first
    assert b"SPDX-License-Identifier: AGPL-3.0-only" in first


def test_builder_canonicalizes_source_newlines_and_utf8(tmp_path, monkeypatch):
    builder = load(ROOT / "tools" / "build_singlefile.py", "soop_builder_newlines")
    outputs = []
    for index, newline in enumerate((b"\n", b"\r\n", b"\r")):
        root = tmp_path / str(index)
        (root / "soop_grid").mkdir(parents=True)
        (root / "plugins").mkdir()
        (root / "soop_grid" / "base.py").write_bytes(
            "LABEL = 'left ↔ right'\n".replace("\n", "\n").encode("utf-8").replace(b"\n", newline)
        )
        (root / "plugins" / "soop_grid.py").write_bytes(
            b"from soop_grid.base import LABEL\n__plugin__ = LABEL\n".replace(b"\n", newline)
        )
        monkeypatch.setattr(builder, "ROOT", root)
        outputs.append(builder.build().encode("utf-8"))
    assert outputs[0] == outputs[1] == outputs[2]


def test_bundle_identity_dataclasses_and_golden_packets(bundle, golden):
    def get(module, name):
        return getattr(bundle, bundle.__grid_names__[module][name])

    from soop_grid.gateway import login_packet, certificate_packet
    from soop_grid.crypto import service6
    from soop_grid.center import Credentials, join_body
    from soop_grid.mpegts import TSMux

    assert get("protocol", "GridError") is get("parent", "GridError")
    assert get("protocol", "GridError") is get("center", "GridError")
    args = ("00112233-4455-6677-8899-aabbccddeeff", 123456, 1)
    assert get("crypto", "service6")(*args) == service6(*args)
    args = ("00112233445566778899aabbccddeeff", "fixture-cookie", "fixture_channel")
    assert get("gateway", "login_packet")(*args) == login_packet(*args)
    args = (12345, "fixture_channel", "123", b"fixture-ticket")
    assert get("gateway", "certificate_packet")(*args) == certificate_packet(*args)
    args = (b"fixture-ticket", b"", "fixture-guid", 12345, b"fixture-only")
    assert get("center", "join_body")(get("center", "Credentials")(*args)) == join_body(Credentials(*args))
    original, single = TSMux(), get("mpegts", "TSMux")()
    g = golden["ts"]
    for kind, timestamp, payload in (
        ("I", 50_000_000, bytes.fromhex(g["video"])),
        ("A", 50_010_000, bytes.fromhex(g["audio"])),
    ):
        assert single.add(kind, timestamp, payload) == original.add(kind, timestamp, payload)


def test_singlefile_plugin_coexists_with_builtin_without_package_install(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "soop_grid.py").write_bytes((ROOT / "dist" / "soop_grid.py").read_bytes())
    python = os.environ.get("SOOP_GRID_CLEAN_PYTHON", sys.executable)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    base = [python, "-m", "streamlink_cli", "--plugin-dir", str(plugin_dir)]
    target = "https://play.sooplive.com/fixture_channel/12345"
    result = subprocess.run(
        base + ["--can-handle-url", target], env=env, cwd=tmp_path, capture_output=True, timeout=10
    )
    assert result.returncode == 0
    result = subprocess.run(
        base + ["--json", target],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["plugin"] == "soop_grid"
    assert data["streams"]["original"]["type"] == "auto"
    result = subprocess.run(
        base + ["--json", "--soop-grid-transport", "grid", "--soop-grid-duration", "3600", target],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["plugin"] == "soop_grid"
    assert data["streams"]["original"]["type"] == "grid"
    if "SOOP_GRID_CLEAN_PYTHON" in os.environ:
        probe = subprocess.run(
            [python, "-c", "import importlib.util; assert importlib.util.find_spec('soop_grid') is None"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert probe.returncode == 0, probe.stderr
    # Same enumeration through official API, not merely successful import.
    session = Streamlink()
    session.plugins.load_path(str(plugin_dir))
    _, cls, url = session.resolve_url(target)
    assert cls.__module__.endswith("soop_grid")
    session.plugins.load_builtin()
    assert session.plugins.get_loaded()["soop"].__name__ == "Soop"
    assert session.plugins.get_loaded()["soop_grid"] is cls
    assert cls.__name__ == "_sg_plugin_SoopGridPlugin"
    assert cls(session, url).streams()["original"].grid.config.bno == "12345"


def test_singlefile_cli_secrets_do_not_leak(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(
        SOOP_ACCOUNT_ID="fixture_account",
        SOOP_ACCOUNT_PASSWORD="fixture-private-password",
        SOOP_STREAM_PASSWORD="fixture-only",
    )
    python = os.environ.get("SOOP_GRID_CLEAN_PYTHON", sys.executable)
    p = subprocess.run(
        [
            python,
            "-m",
            "streamlink_cli",
            "--plugin-dir",
            str(ROOT / "dist"),
            "--loglevel",
            "debug",
            "--json",
            "https://play.sooplive.com/fixture_channel/12345",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert p.returncode == 0
    assert b"fixture-private-password" not in p.stdout + p.stderr
    assert b"fixture-only" not in p.stdout + p.stderr


def test_clean_cli_accepts_external_auth_options_without_module_install(tmp_path):
    python = os.environ.get("SOOP_GRID_CLEAN_PYTHON", sys.executable)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "soop_grid.py").write_bytes((ROOT / "dist" / "soop_grid.py").read_bytes())
    result = subprocess.run(
        [
            python,
            "-m",
            "streamlink_cli",
            "--plugin-dir",
            str(plugin_dir),
            "--json",
            "--soop-grid-username",
            "fixture_user",
            "--soop-grid-password",
            "fixture-password",
            "--soop-grid-stream-password",
            "fixture-pass",
            "https://play.sooplive.com/fixture_channel/12345",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["plugin"] == "soop_grid"
    assert "fixture-password" not in result.stdout + result.stderr
    assert "fixture-pass" not in result.stdout + result.stderr


def test_linker_preserves_local_shadowing_closures_and_lazy_imports(tmp_path, monkeypatch):
    builder = load(ROOT / "tools" / "build_singlefile.py", "soop_builder_scopes")
    (tmp_path / "soop_grid").mkdir()
    (tmp_path / "plugins").mkdir()
    (tmp_path / "soop_grid" / "base.py").write_text("""from dataclasses import dataclass
VALUE = 7
@dataclass
class Item:
    value: int
def factory(VALUE):
    def closure():
        return VALUE
    return closure
def comp():
    return [VALUE for VALUE in range(3)], VALUE
""")
    (tmp_path / "plugins" / "soop_grid.py").write_text("""from soop_grid.base import Item, factory, comp

def lazy():
    from soop_grid.base import VALUE
    return VALUE
__plugin__ = (Item(3).value, factory(5)(), comp(), lazy())
""")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    path = tmp_path / "result.py"
    path.write_text(builder.build())
    linked = load(path, "scope_fixture")
    assert linked.__plugin__ == (3, 5, ([0, 1, 2], 7), 7)


def test_old_and_new_install_names_both_match_and_require_manual_migration(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    artifact = (ROOT / "dist" / "soop_grid.py").read_bytes()
    (plugin_dir / "soopgrid.py").write_bytes(artifact)
    (plugin_dir / "soop_grid.py").write_bytes(artifact)

    session = Streamlink()
    session.plugins.load_path(str(plugin_dir))
    loaded = session.plugins.get_loaded()
    assert {"soopgrid", "soop_grid"} <= loaded.keys()

    url = "https://play.sooplive.com/fixture_channel/12345"
    matching_ids = {
        plugin_id
        for plugin_id in ("soopgrid", "soop_grid")
        if any(matcher.pattern.match(url) for matcher in loaded[plugin_id].matchers)
    }
    assert matching_ids == {"soopgrid", "soop_grid"}
    assert session.resolve_url(url)[0] in matching_ids


def test_old_new_plugin_collision_selection_follows_load_order(tmp_path):
    artifact = (ROOT / "dist" / "soop_grid.py").read_bytes()
    url = "https://play.sooplive.com/fixture_channel/12345"
    observed = []
    for index, names in enumerate((("soopgrid.py", "soop_grid.py"), ("soop_grid.py", "soopgrid.py"))):
        session = Streamlink()
        for position, filename in enumerate(names):
            directory = tmp_path / str(index) / str(position)
            directory.mkdir(parents=True)
            (directory / filename).write_bytes(artifact)
            session.plugins.load_path(str(directory))
        observed.append(session.resolve_url(url)[0])
    assert observed == ["soopgrid", "soop_grid"]
