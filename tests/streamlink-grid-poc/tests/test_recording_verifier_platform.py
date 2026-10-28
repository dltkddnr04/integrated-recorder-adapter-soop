# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Portable guard coverage; plugin/runtime tests remain enabled on every OS."""

import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[1] / "tools" / "verify_recording.py"
    spec = importlib.util.spec_from_file_location("recording_verifier_platform", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nonposix_verification_never_spawns_or_claims_success(verifier, monkeypatch, tmp_path):
    monkeypatch.setattr(verifier, "os", SimpleNamespace(name="nt"))

    def unexpected_spawn(*args, **kwargs):
        pytest.fail("unsupported platform spawned a subprocess")

    monkeypatch.setattr(verifier.subprocess, "Popen", unexpected_spawn)
    with pytest.raises(verifier.ValidationError, match="^validation_platform_unsupported$"):
        verifier.run_process([], lambda chunk: None, time.monotonic() + 1)
    result = verifier.verify(tmp_path / "missing.ts")
    assert result["verdict"] == "fail" and not result["passed"]
    assert result["failures"] == ["validation_platform_unsupported"]
