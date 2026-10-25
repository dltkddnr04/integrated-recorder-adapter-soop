# Integrated Recorder SOOP Source Plugin

[한국어](README.md) | **English**

A first-party Source Plugin that connects SOOP live streams through Adapter Protocol v1.

> [!WARNING]
> **Status: Development / Experimental. Stable Plugin Registry distribution: No.**
>
> High-quality stream selection is not yet reliable. Depending on the AID issuance path and egress conditions, the adapter may receive a lower-resolution stream assignment than the browser. No general fix or stable distribution is claimed.

## Role and current capabilities

This standalone executable handles each channel check as a request from Core; it does not run a recorder or polling loop.

- `watch`: checks whether a channel is offline or live and returns a broadcast-specific session reference and HLS source.
- `resolve`: resolves a live channel to HLS media.
- `metadata`: reports the current title while the broadcast number still matches.
- `refresh`: requests a new AID and manifest after HTTP 401/403.

Core controls the watch schedule and owns recording, segment storage, the metadata timeline, VOD, and archive integrity. This plugin does not replace those responsibilities.

## High-quality stream selection limitation

Even when the browser player can select a higher quality, the adapter may receive a lower-quality manifest assignment depending on the AID issuance route and egress conditions. A resolution observed on one broadcast is not presented as a general limit.

An experiment using a different egress for AID issuance returned a 1920×1080 stream. This shows a possible route, not a general fix. Work is underway to investigate separating AID-issuance egress. Proxying every media segment is not an established solution. Until quality selection is resolved, this plugin is not considered ready for stable official Registry distribution.

## Inputs and settings

| Field | Location | Description |
| --- | --- | --- |
| `channel` | Required input | SOOP login ID or a `play.sooplive.com`, `play.sooplive.co.kr`, or `play.afreecatv.com` URL. A URL may include a broadcast number as its second path segment. |
| `stream_password` | Secret input | Optional password for a protected broadcast. |
| `quality` | Setting | Defaults to `best`; supports `worst` or an exact SOOP preset name/label. `auto` is not selectable. |
| `account_username` | Secret setting | Optional SOOP account username. Supply it with `account_password`. |
| `account_password` | Secret setting | Optional SOOP account password. Supply it with `account_username`. |

If an account is required (`RESULT=-6`), the adapter logs in and retries. Stream and account secrets are stored through Protocol v1 secret state mutations, namespaced by channel and broadcast so refresh can recover them after the adapter process restarts. They are not added to media metadata or error messages. Signed manifest URLs are marked sensitive.

Platform failures, login requirements, and malformed responses are errors, not offline status.

## Platform flow and security

The adapter queries SOOP's live API for status, broadcast identity, title, CDN, and quality presets. It requests an AID using the broadcast number, selected quality, and optional stream password, then obtains the HLS manifest from the returned RMD host's `broad_stream_assign.html`. Dynamic platform URLs must use HTTPS and a SOOP/Afreeca domain.

The executable reads Protocol v1 newline-delimited JSON from stdin and writes protocol frames only to stdout; diagnostics go to stderr. The plugin is a native executable and is not sandboxed.

## Development and verification

```sh
GOWORK=off go test -race -count=1 ./...
GOWORK=off go vet ./...
GOWORK=off go build -o integrated-recorder-adapter-soop ./cmd/integrated-recorder-adapter-soop
```

This repository uses the [Adapter SDK for Go](https://github.com/integrated-recorder/adapter-sdk-go). See the [Plugin Registry](https://github.com/integrated-recorder/plugin-registry) for distribution status and releases. There is no stable official Registry distribution at this time.

## Experimental/Beta Streamlink Native Grid plugin

The independent Go Grid receiver research and synthetic fixture tests are in [tests/grid-poc](tests/grid-poc/README.md). This experimental code is separate from the Adapter Protocol v1 plugin and is not integrated into the production adapter.

The separate Python Streamlink Native Grid plugin has passed bounded live reception and controlled Parent-failure recovery tests; it remains Experimental/Beta. See the [Korean quick start](tests/streamlink-grid-poc/README.md) and [English README](tests/streamlink-grid-poc/README.en.md) for installation and use. It is not integrated into the Go Adapter or Core.

## Ecosystem and license

- [Integrated Recorder Core](https://github.com/integrated-recorder/core) — recording and canonical archives
- [Adapter SDK for Go](https://github.com/integrated-recorder/adapter-sdk-go) — Source Plugin authoring API
- [Plugin Registry](https://github.com/integrated-recorder/plugin-registry) — approved release artifact index
- [source.owncast](https://github.com/integrated-recorder/source.owncast) — a separate first-party integration

See [LICENSE](LICENSE) for the license.
