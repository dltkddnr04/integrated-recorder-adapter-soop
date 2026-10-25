# SOOP Native Grid Streamlink Plugin

An experimental, single-file Streamlink plugin for the native SOOP TCP Grid
transport. The canonical implementation is the Python package in this directory;
`dist/soop.py` is generated from it. It preserves H.264/AAC without transcoding and
emits MPEG-TS through Streamlink's `StreamIO`.

## Install one file

Install Streamlink normally, then copy `dist/soop.py` as **`soopgrid.py`** into the
user plugin directory. The filename is intentional: it lets this plugin coexist
with Streamlink's maintained built-in `soop` HLS plugin. Installation paths,
update/removal commands, and clean-environment evidence are in
[docs/SINGLEFILE.md](docs/SINGLEFILE.md).

The tested baseline is Streamlink 8.6.2 on Linux/Python 3.12.3. The source is
AGPL-3.0-only. No project package, Go subprocess, Grid Agent, FFmpeg receive process,
or Streamlink core modification is required. Streamlink's ordinary dependencies
remain required.

## Transport modes

```sh
# Grid only
streamlink --plugin-dir ./plugins --soop-grid-transport grid \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts

# Maintained Streamlink SOOP HLS plugin only
streamlink --plugin-dir ./plugins --soop-grid-transport hls \
  'https://play.sooplive.com/CHANNEL_ID/BNO' best -o recording.ts

# Grid first; use official HLS only if Grid fails before emitting media
streamlink --plugin-dir ./plugins --soop-grid-transport auto \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

`auto` falls back only for a bounded set of initial transport failures. It does not
fall back after authorization denial, malformed protocol/media, unsupported timing,
or after Grid bytes have been returned. It never mixes independent HLS and Grid
timelines in one output.

Grid records until the broadcast ends, the user stops Streamlink, or an optional
limit is reached. `--soop-grid-duration SECONDS` sets a duration budget beginning at
Parent reception; omission means no duration limit. `--soop-grid-max-bytes BYTES`
optionally caps received Grid wire bytes. Streamlink's own `--stream-timeout` controls
reader/backpressure waits; `--ringbuffer-size` controls the bounded output buffer.

Use only a broadcast you are authorized to watch and record. Public bootstrap checks
the exact channel and BNO using live metadata and requires a server-issued Watch
ticket. Account credentials are read from `SOOP_ACCOUNT_ID` and
`SOOP_ACCOUNT_PASSWORD`; a broadcast password may be supplied through
`SOOP_STREAM_PASSWORD`. The broadcast password is sent only through the normal
Center JOIN field. Do not put secrets in shell history or share logs containing
private capture data.

Optional arguments:

- `--soop-grid-account`: account ID instead of `SOOP_ACCOUNT_ID`
- `--soop-grid-require-adult`: require the authenticated API's adult-check marker
- `--soop-grid-stream-password`: broadcast password (environment input is safer)
- `--soop-grid-duration`: optional Grid receive duration
- `--soop-grid-max-bytes`: optional Grid wire-byte limit

The requested quality label `original` is not proof of output resolution. Inspect
actual SPS/codec metadata.

## Current reliability boundary

The plugin uses one active Parent and bounded failover to other server-issued
candidates, with at most one Center rediscovery per outage. Recovery accepts only an
exact cached overlap and consecutive next frames; it reports and terminates on an
unprovable gap. It does not claim seamless or lossless recovery from every outage.
Multi-Parent active-active reception is not implemented because simultaneous
connections and shared frame numbering have not been verified against SOOP.

H.264 TS output now derives DTS from the observed Grid decode-order clock and PTS
from H.264 POC plus fixed VUI frame timing. Unsupported timing/POC structures are
rejected rather than assigned guessed timestamps. Synthetic B-frame/AAC TS passes
full FFmpeg decoding and monotonic DTS checks. An existing private 1080p30 Grid
capture also passes the new full decode check; this is not a new live capture or a
long-duration test. A/V offsets are preserved from the common observed Grid clock;
independent perceptual sync was not measured.

Default recording is continuous, with bounded in-memory queues. Automatic credential
refresh, broadcast rollover, crash-resume, and multi-hour live stability have not
been verified. See [docs/PRODUCTION_READINESS_RESULT.md](docs/PRODUCTION_READINESS_RESULT.md),
[architecture](docs/ARCHITECTURE.md), [reliability policy](docs/RELIABILITY.md), and
[limitations](docs/LIMITATIONS.md).

## Development checks

```sh
python tools/build_singlefile.py
python tools/build_singlefile.py --check
python -m pytest -q
ruff check .
ruff format --check .
```

The local TCP fault-injection tests use loopback sockets. See
[docs/VALIDATION.md](docs/VALIDATION.md) for the distinction between offline,
historical-live, and current-live evidence.
