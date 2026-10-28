# SOOP Native Grid — Streamlink Plugin

[한국어](README.md) | **English**

An external Python plugin that receives SOOP Native Grid broadcasts through Streamlink. It uses Streamlink's public plugin API and outputs H.264/AAC as MPEG-TS without re-encoding. It does not require a SOOP Grid Agent or a Go executable.

## Status

This is an **Experimental/Beta** project preparing for its first public release. Single-file loading and real Grid reception were verified on Linux with Python 3.12.3 and Streamlink 8.6.2. Results from bounded live captures and controlled Parent socket-close recovery are in the [validation report](docs/LIVE_FAILOVER_VALIDATION.md). The plugin does not claim stable or universally lossless operation.

## Features

- Native TCP Grid reception without the Windows Grid Agent
- Streamlink output to files and other supported Streamlink targets
- H.264/AAC preserved and muxed into MPEG-TS without transcoding
- Grid-first `auto` by default, Grid-only `grid`, and built-in SOOP HLS delegation with `hls`
- Restricted HLS fallback for allowlisted initial transport failures
- Single-Parent reception with bounded Center rediscovery and Cache-overlap recovery
- One-file installation without installing a project Python package

## Requirements

- Streamlink 8.6.2 recommended (tested on Linux with Python 3.12.3)
- Streamlink's normal dependencies
- Enough disk space for the recording

No project package, Go, FFmpeg receive process, SOOP Agent, or VPN is required. FFmpeg is optional for post-recording inspection only. Installation paths are documented for Windows and macOS, but runtime operation has not been verified on those systems.

## Install

Save the repository artifact `dist/soop_grid.py` in Streamlink's user plugin directory as **`soop_grid.py`**. Streamlink loads it with the external plugin ID `soop_grid`; the built-in `soop` plugin remains available.

See the [Korean user guide](docs/COMMUNITY_GUIDE_KO.md) for platform-specific paths, discovery checks, updates, and removal. For a reproducible installation, use a download URL pinned to the published release commit.

## Record a stream

After installation, and only for a broadcast you are authorized to watch and record:

```sh
streamlink 'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

When omitted, the transport option defaults to `auto`, which tries Native Grid first.

## Grid / HLS / Auto

| Mode | Behavior |
| --- | --- |
| Default or `auto` | Grid first; try official HLS only for allowlisted initial transport failures before media output |
| `grid` | Native Grid only; no HLS transition |
| `hls` | Delegate to Streamlink's built-in `soop` plugin |

`auto` does not fall back on authorization denial, malformed protocol/media, or frame-continuity errors. After Grid output begins, it does not append HLS bytes or mix the two timelines. See the [user guide](docs/COMMUNITY_GUIDE_KO.md) for commands and details.

## Key limitations

- Experimental/Beta. The latest bundle, `400f028e…`, completed a 30-minute Grid-only recording and passed full H.264/AAC decode plus TS/timestamp checks; that run contained no B pictures. A separate 30-minute run using an earlier bundle contained B pictures and passed full-file checks, but it is not represented as a same-build test. A separate HRD/POC presentation-order conflict remains fail-closed. Perceptual A/V sync, all codec/broadcast combinations, and repeated long-duration natural failures remain unverified. See the [latest 30-minute report](docs/LIVE_VALIDATION_30M_20261028.md) and [timeline analysis](docs/TIMELINE_MODEL_FIELD_VALIDATION_20261028.md).
- A roughly 600-second recording and three Cache-assisted recoveries after client-side Parent socket closes in one session were verified in controlled experiments. This does not guarantee lossless recovery from every failure.
- Automatic account reauthentication, reliable classification of broadcast end versus network failure, and automatic broadcast-number changes are not supported.
- Support is limited to validated subsets of H.264 timing/POC structures and ADTS AAC. Multi-Parent simultaneous reception is not supported.
- Native Grid exposes only the verified `original` stream. HLS uses the quality choices provided by Streamlink's built-in `soop` plugin.
- If Cache overlap and continuity cannot be verified, reception fails rather than inventing data. Three seconds without media may trigger recovery.
- Optional raw diagnostic capture can consume substantial disk space and synchronous I/O. It is disabled by default.
- Windows/macOS runtime, all broadcast conditions, and long-run A/V drift have not been verified.
- For authenticated broadcasts, use `--soop-grid-username` and `--soop-grid-password`, or `SOOP_ACCOUNT_ID` / `SOOP_ACCOUNT_PASSWORD`. Streamlink's built-in `--soop-username` / `--soop-password` options are not passed to the external plugin. The broadcast password is separate: `--soop-grid-stream-password` or `SOOP_STREAM_PASSWORD`.

See [limitations](docs/LIMITATIONS.md), the [pre-release static audit](docs/PRE_RELEASE_STATIC_AUDIT.md), [live failover validation](docs/LIVE_FAILOVER_VALIDATION.md), and [comprehensive hardening and field validation](docs/COMPREHENSIVE_HARDENING.md) for evidence and boundaries.

## Documentation and source

- [Korean installation and user guide](docs/COMMUNITY_GUIDE_KO.md)
- [Single-file installation and build](docs/SINGLEFILE.md)
- [Authentication option compatibility and limitations](docs/AUTH_COMPATIBILITY.md)
- [Architecture and protocol](docs/ARCHITECTURE.md)
- [H.264 timing diagnosis and supported subset](docs/H264_TIMING_DIAGNOSIS.md)
- [Recovery policy](docs/RELIABILITY.md)
- [Validation history and scope](docs/VALIDATION.md)
- [Research reports](docs/): failover, Multi-Parent, Cache-range analysis, and more
- [Canonical Python modules](soop_grid/), [Streamlink entrypoint](plugins/soop_grid.py)

This Streamlink plugin is separate from the Go-based Integrated Recorder Source Adapter in the same repository. It is not integrated into the production Adapter or Core. The repository root README describes the Go Adapter.

## License

The plugin and generated distribution artifact are licensed **AGPL-3.0-only**. See [LICENSE](LICENSE). Users are responsible for respecting broadcast access permissions, recording rights, and SOOP's terms.
