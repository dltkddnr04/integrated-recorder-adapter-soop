#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Test-only facades run existing offline tests against static bundled objects.

This harness is never distributed inside soop_grid.py. It does not load modular source.
It redirects test imports/monkeypatches to bundled globals in this process only.
"""

import importlib
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = importlib.util.spec_from_file_location("soop_static_parity", ROOT / "dist" / "soop_grid.py")
    bundle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bundle
    spec.loader.exec_module(bundle)
    package = types.ModuleType("soop_grid")
    package.__path__ = []
    sys.modules["soop_grid"] = package

    class Facade(types.ModuleType):
        def __getattr__(self, name):
            mapping = self.__dict__["mapping"]
            target = mapping.get(name)
            if target in bundle.__dict__:
                return bundle.__dict__[target]
            raise AttributeError(name)

        def __setattr__(self, name, value):
            mapping = self.__dict__.get("mapping", {})
            target = mapping.get(name)
            if target in bundle.__dict__:
                bundle.__dict__[target] = value
            super().__setattr__(name, value)

        def __delattr__(self, name):
            mapping = self.__dict__.get("mapping", {})
            target = mapping.get(name)
            if target in bundle.__dict__:
                del bundle.__dict__[target]
            super().__delattr__(name)

    for name, mapping in bundle.__grid_names__.items():
        if name == "plugin":
            continue
        facade = Facade("soop_grid." + name)
        facade.mapping = mapping
        for alias, target in mapping.items():
            if target in bundle.__dict__:
                types.ModuleType.__setattr__(facade, alias, bundle.__dict__[target])
        facade.__file__ = str(ROOT / "dist" / "soop_grid.py")
        sys.modules[facade.__name__] = facade
        setattr(package, name, facade)
    # Offline audit policy stays in tools. Import it only after installing the
    # facades so its shared parser/POC base is the bundled production class.
    sys.path.insert(0, str(ROOT))
    audit = importlib.import_module("tools.legacy_h264_audit")
    assert audit.LegacyH264Clock.__bases__ == (sys.modules["soop_grid.h264clock"].H264Clock,)
    assert "legacy_h264_audit" not in bundle.__grid_names__
    import pytest

    # Single-file construction tests run separately to avoid recursively testing
    # this test harness. Regression/fault tests include the external offline audit policy.
    args = [str(ROOT / "tests"), "--ignore=" + str(ROOT / "tests" / "test_singlefile.py"), *sys.argv[1:]]
    raise SystemExit(pytest.main(args))


if __name__ == "__main__":
    main()
