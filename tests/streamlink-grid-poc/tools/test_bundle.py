#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Test-only facades run existing offline tests against static bundled objects.

This harness is never distributed inside soop.py. It does not load modular source.
It redirects test imports/monkeypatches to bundled globals in this process only.
"""

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = importlib.util.spec_from_file_location("soop_static_parity", ROOT / "dist" / "soop.py")
    bundle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bundle
    spec.loader.exec_module(bundle)
    package = types.ModuleType("soop_grid")
    package.__path__ = []
    sys.modules["soop_grid"] = package

    class Facade(types.ModuleType):
        def __getattr__(self, name):
            mapping = self.__dict__["mapping"]
            if name in mapping:
                return bundle.__dict__[mapping[name]]
            raise AttributeError(name)

        def __setattr__(self, name, value):
            mapping = self.__dict__.get("mapping", {})
            if name in mapping:
                bundle.__dict__[mapping[name]] = value
            else:
                super().__setattr__(name, value)

    for name, mapping in bundle.__grid_names__.items():
        if name == "plugin":
            continue
        facade = Facade("soop_grid." + name)
        facade.mapping = mapping
        facade.__file__ = str(ROOT / "dist" / "soop.py")
        sys.modules[facade.__name__] = facade
        setattr(package, name, facade)
    import pytest

    # Single-file construction tests run separately to avoid recursively testing
    # this test harness. Every pre-bundle regression/fault test runs unchanged.
    args = [str(ROOT / "tests"), "--ignore=" + str(ROOT / "tests" / "test_singlefile.py"), *sys.argv[1:]]
    raise SystemExit(pytest.main(args))


if __name__ == "__main__":
    main()
