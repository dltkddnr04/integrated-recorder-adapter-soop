# Standalone distribution

`dist/soop.py` is a readable, static Python file generated from the canonical modular
sources and `plugins/soop.py`. No separate `soop_grid` package, pip installation of
this project, Go process, SOOP agent, FFmpeg receive process, encoded source archive,
`exec`/`eval`, import hook, source-path search or runtime code download is used.
Streamlink's normal dependencies (including requests and PyCryptodome) remain needed.
Do not vendor those dependencies.

## Install one file

Streamlink 8.6.2 and Python 3.12.3 were runtime-tested on Linux. Python 3.10+ syntax is
checked; other Python versions and macOS/Windows runtime are not verified. Homebrew's
current version can differ from the tested Streamlink version.

macOS:

```sh
brew install streamlink
mkdir -p "$HOME/Library/Application Support/streamlink/plugins"
curl -fL 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop.py' \
  -o "$HOME/Library/Application Support/streamlink/plugins/soop.py"
```

If you already downloaded `soop.py`, use:

```sh
cp soop.py "$HOME/Library/Application Support/streamlink/plugins/soop.py"
```

Linux (Streamlink already installed):

```sh
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins"
cp soop.py "${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins/soop.py"
```

Windows PowerShell (Streamlink already installed):

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\streamlink\plugins"
Copy-Item soop.py "$env:APPDATA\streamlink\plugins\soop.py"
```

These paths follow the installed official Streamlink 8.6.2 `streamlink_cli/constants.py`.
An explicit directory works independently of OS discovery:

```sh
streamlink --plugin-dir /path/to/plugins --can-handle-url \
  'https://play.sooplive.com/CHANNEL_ID/BNO'
streamlink --plugin-dir /path/to/plugins --stream-types grid \
  --soop-grid-duration 20 'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

Use only a target you may access and record. URL/JSON discovery is offline enumeration,
not proof of media access. Anonymous opens directly validate exact Live metadata and
normal Watch authorization. Account/password/adult environment settings and bounded
capture options are the same as the modular plugin; see the main PoC README.

Installing this file overrides Streamlink's built-in `soop` plugin with Grid-only
behavior. It does not add an HLS fallback. No core file is changed. To update, replace
this one file with a newly generated/released copy. To uninstall, remove only the
installed `soop.py`; the built-in SOOP HLS plugin becomes available again. Do not
remove files from Streamlink's own installation.

## Rebuild and verify

From this directory, using the developer/test environment:

```sh
python tools/build_singlefile.py
python tools/build_singlefile.py --check
python -m pytest -q
python tools/test_bundle.py -q
```

The builder is specialized to this source tree. It uses AST + Python symbol tables,
resolves top-level and delayed internal imports into ordinary assignments, prefixes
module globals, preserves function locals/closures/comprehensions/class attributes,
and topologically orders dependencies. Exceptions retain object identity across
import aliases. Dataclass annotations and Streamlink decorators are normal Python.
Unsupported import cycles/constructs fail instead of using a runtime loader. Source
module hashes are embedded as comments. Generation is deterministic on the tested
Python 3.12 toolchain; AST formatting across Python toolchain versions is not promised.
Never hand-format/edit `dist/soop.py`; edit modular source and regenerate instead.

`tools/test_bundle.py` is a **test-only** adapter that exposes bundled objects under
module names expected by the existing tests. It is not shipped in the distribution
file and installs no runtime import hook. All original regression/fault cases execute
against those objects. Additional tests run the real CLI with a copied single file.

## Clean installation validation

A new `/tmp/soop-grid-clean-venv` was provisioned with `streamlink==8.6.2` and official
transitive dependencies only. The host lacks system ensurepip, so an official pip
wheel was used as an external installer for the new venv; system Python was unchanged.
Neither this project nor `soop_grid` was installed. With PYTHONPATH unset, from an
unrelated directory, a copied `soop.py` passed URL discovery, JSON Grid enumeration,
CLI options, built-in override, secret-log tests and public live Grid capture.
Use `SOOP_GRID_CLEAN_PYTHON=/path/to/clean/python` with pytest to repeat CLI checks.

## License

SPDX: AGPL-3.0-only. Copyright (c) 2026 source.soop contributors.
The generated file identifies the original repository and license URL. All protocol
implementation is derived from the same AGPL research source; generation does not
relicense it. Streamlink and PyCryptodome are imported, not copied or relicensed.
