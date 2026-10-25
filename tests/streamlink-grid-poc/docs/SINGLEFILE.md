# Single-file distribution

`dist/soop.py` is readable static Python generated from canonical modular sources.
No project package, Go helper, SOOP agent, Streamlink core patch, import hook, runtime
`exec`/`eval`, archive loader, or code download is used. Only normal Streamlink
dependencies are required. Do not vendor those dependencies.

## Install

The distribution artifact is named `soop.py`; install it as **`soopgrid.py`** so the
custom plugin has its own Streamlink plugin ID and can coexist with the built-in
`soop` HLS plugin. Streamlink 8.6.2 / Python 3.12.3 was tested on Linux. Other OS
plugin directories follow Streamlink 8.6.2's official CLI constants but are not
runtime-tested here.

macOS:

```sh
brew install streamlink
mkdir -p "$HOME/Library/Application Support/streamlink/plugins"
curl -fL 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop.py' \
  -o "$HOME/Library/Application Support/streamlink/plugins/soopgrid.py"
```

Linux:

```sh
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins"
cp soop.py "${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins/soopgrid.py"
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\streamlink\plugins"
Copy-Item soop.py "$env:APPDATA\streamlink\plugins\soopgrid.py"
```

Check discovery:

```sh
streamlink --can-handle-url 'https://play.sooplive.com/CHANNEL_ID/BNO'
```

## Usage

```sh
# Grid only, unlimited until user stop / broadcast termination
streamlink --soop-grid-transport grid 'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts

# Official Streamlink HLS only
streamlink --soop-grid-transport hls 'https://play.sooplive.com/CHANNEL_ID/BNO' best -o recording.ts

# Grid, then official HLS only for an allowlisted pre-output transport failure
streamlink --soop-grid-transport auto 'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts

# Optional bounds
streamlink --soop-grid-duration 3600 --soop-grid-max-bytes 500000000 \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

HLS mode delegates to the Streamlink built-in SOOP plugin; that plugin is not copied
into this artifact. `auto` is one-way fallback before the Grid stream emits bytes; it
never combines separate timelines. Broadcast access/password/account rules are
documented in the main README. Use only streams you are authorized to watch and
record.

To update, replace the installed `soopgrid.py` with the new `dist/soop.py` contents.
To uninstall, remove only that file; Streamlink's built-in SOOP HLS plugin remains.

## Build and parity verification

```sh
python tools/build_singlefile.py
python tools/build_singlefile.py --check
python -m pytest -q
python tools/test_bundle.py -q
```

The generated `dist/SHA256SUMS` records the artifact digest; after downloading the
file, compare it with the published checksum for the same commit/release.

The specialized AST/symbol-table bundler resolves the known internal module graph,
prefixes internal globals and preserves dataclass identity and lazy imports. Unsupported
cycles or constructs fail at build time. `dist/soop.py` is generated, never edited by
hand. The isolated CLI test copies only this artifact to a temporary `soopgrid.py`,
uses a fresh venv containing Streamlink 8.6.2 and official dependencies only, removes
PYTHONPATH, and runs from an unrelated directory.

## License

SPDX `AGPL-3.0-only`, Copyright (c) 2026 source.soop contributors. The bundled code
is derived from this repository's AGPL Go/Python work. Streamlink and its dependencies
remain separate packages under their own licenses and are not vendored or relabeled.
