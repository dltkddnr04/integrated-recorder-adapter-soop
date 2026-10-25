# Python SOOP Native Grid Streamlink PoC

This standalone experimental Streamlink sideload plugin ports the native TCP Go receiver in [../grid-poc](../grid-poc/README.md). It implements account/public bootstrap, Gateway, Center, Parent, H.264/AAC extraction and MPEG-TS packetization in Python. It supplies TS bytes through Streamlink's public `Stream`/`StreamIO` interface. There is no Go subprocess, SOOP agent, or HLS delivery in this receiver. AES uses PyCryptodome, already a normal Streamlink dependency.

The plugin is named `soop.py` and **replaces the built-in SOOP HLS plugin for the invocation that loads it**. Only Native Grid is provided. Omit `--plugin-dirs` to use the ordinary built-in SOOP plugin. The production source adapter and Go PoC are separate and unchanged.

## Install

Python 3.10+ and official Streamlink 8.6.2 are the tested API baseline. From this directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'streamlink==8.6.2' '.[test]'
```

Install the package before sideloading `plugins/soop.py`; the plugin imports the installed `soop_grid` package and does not modify `sys.path` or Streamlink files. Both editable and ordinary wheel installations were checked from an unrelated working directory.

## Public stream: file, stdout, or player

Use a current stream you are allowed to watch and record. Replace `CHANNEL_ID` and `BNO` with the actual target. The anonymous path validates the exact target directly with Live metadata and requires a normally issued Watch Ticket. No live-list page is required. Explicit restrictions and server denial fail closed; see [RELIABILITY.md](docs/RELIABILITY.md) for policy coverage and bounded Parent recovery.

```sh
streamlink --plugin-dirs "$PWD/plugins" --stream-types grid \
  --retry-open 1 --soop-grid-duration 35 \
  https://play.sooplive.com/CHANNEL_ID/BNO original -o recording.ts

streamlink --plugin-dirs "$PWD/plugins" --stream-types grid \
  --soop-grid-duration 35 \
  https://play.sooplive.com/CHANNEL_ID/BNO original --stdout > recording-stdout.ts

streamlink --plugin-dirs "$PWD/plugins" --stream-types grid \
  --soop-grid-duration 35 \
  https://play.sooplive.com/CHANNEL_ID/BNO original --player mpv
```

File output and stdout were live-tested. Player output uses the same `StreamIO`; an interactive mpv/VLC session was not tested. Channel-only URLs are also recognized: the plugin resolves the current BNO from the normal first-party player page and fixes that BNO for the session.

The stream is called `original`, with stream type `grid`. This is a requested quality, not proof of a resolution; check the actual codec metadata. Sessions default to 35 seconds and are limited to 1–60 seconds and 512 MiB of incoming media wire bytes. This is a bounded PoC, not an indefinite recorder.

## Native account and protected streams

Use an authorized account. Secrets are read from the process environment, never from password CLI flags, and are not persisted. In Bash:

```sh
export SOOP_ACCOUNT_ID=ACCOUNT_ID
read -r -s -p 'SOOP account password: ' SOOP_ACCOUNT_PASSWORD
printf '\n'
export SOOP_ACCOUNT_PASSWORD
streamlink --plugin-dirs "$PWD/plugins" --stream-types grid \
  --retry-open 1 --soop-grid-require-adult --soop-grid-duration 35 \
  https://play.sooplive.com/CHANNEL_ID/BNO original -o authenticated.ts
unset SOOP_ACCOUNT_ID SOOP_ACCOUNT_PASSWORD
```

Omit `--soop-grid-require-adult` for an ordinary authenticated stream. It requires `ACHK=1` in the authenticated live response; it does not establish the broadcast grade on its own. The server remains responsible for authorization. No CAPTCHA or identity-verification automation is provided.

For a password-protected target, also set `SOOP_STREAM_PASSWORD` with a silent prompt before the command. It is restricted to printable ASCII, 1–12 bytes, and is placed only in native Center JOIN Service 70 `passwd`. It is never sent as HTTP metadata `pwd` or in the Gateway login. The target's `BPWD` state must agree with whether a stream password was supplied. Account-based adult and password paths have synthetic tests; Python live validation of those protected paths is **NOT_TESTED**.

Implemented plugin options are `--soop-grid-account` (alternative to `SOOP_ACCOUNT_ID`, marked sensitive), `--soop-grid-require-adult`, and `--soop-grid-duration`. Normal Streamlink options such as `--stream-timeout`, `--ringbuffer-size`, `--output` and `--stdout` apply. Enumeration (`--json`) is deferred and does not authenticate or prove media access. Enumerate again in a fresh process to open a new session; Stream objects are single-use.

## Optional private raw evidence

Set `SOOP_GRID_CAPTURE_DIR` to a **new, nonexistent** directory under an existing private parent. It creates mode-0700 directories and mode-0600 raw files:

```sh
mkdir -m 700 -p captures
export SOOP_GRID_CAPTURE_DIR="$PWD/captures/private-run-001"
# Run one of the commands above, using a new recording output filename.
unset SOOP_GRID_CAPTURE_DIR
```

Outputs are `grid-wire.bin` (received media packets only), `grid-payload.bin`, `video.h264`, `audio.aac`, `recording.ts`, `frames.jsonl`, and `grid-analysis.json`. Keep raw evidence private; none is included in Git. The summary contains fixed stage facts, counts, media metadata and hashes, with no account, target, cookie, ticket, or endpoint values. `audio_frames` counts Grid audio chunks, which can each contain multiple ADTS frames.

Optional post-capture verification/remux:

```sh
ffprobe -v error -show_streams -show_format -of json recording.ts
ffmpeg -v error -xerror -i recording.ts -map 0:v:0 -map 0:a:0 -f null -
ffmpeg -i recording.ts -map 0:v:0 -map 0:a:0 -c copy playable.mp4
```

FFmpeg is not invoked by the plugin.

## Offline checks and evidence

```sh
python -m pytest -q
ruff check .
ruff format --check .
python -m compileall -q soop_grid plugins
streamlink --plugin-dirs "$PWD/plugins" --can-handle-url https://play.sooplive.com/CHANNEL_ID/BNO
```

Tests use synthetic credentials and packet data only; they do not contact SOOP. Local socketpair tests require local socket I/O permission. The Go-generated golden vectors cover the native hash quirk, Service 6, Gateway login/certificate, Center auth/JOIN and byte-identical TS output. See [VALIDATION.md](docs/VALIDATION.md), [architecture](docs/ARCHITECTURE.md), and [limitations/license notes](docs/LIMITATIONS.md).
