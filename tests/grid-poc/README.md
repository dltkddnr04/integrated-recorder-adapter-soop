# SOOP Native Grid Receiver PoC

This directory preserves an experimental, standalone Go receiver for SOOP's native TCP Grid path. It is isolated from the production Adapter Protocol v1 plugin in the parent module.

## Status

The research implementation has received and decoded actual `0xC748` media from a public stream, a verified grade-19 stream, and an owner-operated password-protected test stream. Historical validation details are in [VALIDATION.md](docs/VALIDATION.md). The packaged CLI and bootstrap cleanup in this repository have **not** been live-revalidated after curation; only offline tests have run on this packaged version.

This PoC does not implement Colony/WebRTC or HLS delivery. It does not advertise subscriber-only support. Use only streams that the account is authorized to watch and record, and follow SOOP's terms and applicable copyright rules.

## Architecture

```text
SOOP account / public Watch bootstrap
                ↓
        TK / FTK or Watch ticket
                ↓
             Gateway
                ↓
              Center
                ↓
              Parent
                ↓
      Grid media packets (0xC748)
                ↓
        H.264 / AAC payloads
                ↓
          MPEG-TS output
```

The native mode obtains session fields directly in Go. The watch mode handles one exact public, password-free target. No mode searches for or switches to another broadcast.

## Build and offline checks

Run from this directory with Go 1.23 or newer:

```sh
GOWORK=off go test ./...
GOWORK=off go test -race -count=1 ./...
GOWORK=off go vet ./...
GOWORK=off go build ./...
```

The Go module uses only the standard library. `ffmpeg` and `ffprobe` are optional runtime tools for lossless MP4 remux and full decode verification; the Grid receiver and MPEG-TS writer are Go code.

## Usage

The target must be explicit. `-output` must name a new directory. Captures are created with private directory/file permissions; keep the output directory private because it contains broadcast media.

### Public, password-free stream

```sh
mkdir -m 700 -p captures
go run ./cmd/grid-poc \
  -mode watch \
  -broadcaster CHANNEL_ID \
  -broadcast BNO \
  -duration 35s \
  -output captures/grid-run-001
```

### Authenticated native session

Supply the account password through a non-echoing prompt or a secret manager, not a command-line flag:

```sh
read -r -s -p 'SOOP account password: ' SOOP_ACCOUNT_PASSWORD
printf '\n'
export SOOP_ACCOUNT_PASSWORD
go run ./cmd/grid-poc \
  -mode native \
  -account-id ACCOUNT_ID \
  -broadcaster CHANNEL_ID \
  -broadcast BNO \
  -require-adult \
  -duration 35s \
  -output captures/grid-run-002
unset SOOP_ACCOUNT_PASSWORD
```

`-require-adult` requires the authenticated live API to report its adult check as confirmed. For a normal non-adult target, omit the flag.

### Password-protected native session

Use this only with a stream password supplied by the broadcaster or another authorized source. The PoC passes it to Center JOIN Service 70 and does not add it to the live metadata request.

```sh
read -r -s -p 'SOOP account password: ' SOOP_ACCOUNT_PASSWORD
printf '\n'
read -r -s -p 'Broadcast password: ' SOOP_STREAM_PASSWORD
printf '\n'
export SOOP_ACCOUNT_PASSWORD SOOP_STREAM_PASSWORD
go run ./cmd/grid-poc \
  -mode native \
  -password-protected \
  -account-id ACCOUNT_ID \
  -broadcaster CHANNEL_ID \
  -broadcast BNO \
  -duration 35s \
  -output captures/grid-run-003
unset SOOP_ACCOUNT_PASSWORD SOOP_STREAM_PASSWORD
```

The current native stream-password encoder supports printable ASCII passwords of 1–12 bytes. It rejects other values rather than truncating or guessing an encoding.

The receiver writes `grid-wire.bin`, `grid-payload.bin`, `video.h264`, `audio.aac`, `frames.jsonl`, `grid-analysis.json`, and `recording.ts`. When `ffmpeg` and `ffprobe` are available, it also writes `playable.mp4` and reports the measured streams, dimensions, duration, decode result, and SHA-256 values. The optional `-result PATH` writes sanitized run JSON with mode `0600`; standard output is also sanitized JSON.

## Validation and limits

See [protocol notes](docs/PROTOCOL.md), [historical validation](docs/VALIDATION.md), and [limitations](docs/LIMITATIONS.md). Historical live results came from the research checkout before this repository packaging. No stream media, packet capture, account value, ticket, cookie, broadcast password, SOOP binary, or live session response is included here.
