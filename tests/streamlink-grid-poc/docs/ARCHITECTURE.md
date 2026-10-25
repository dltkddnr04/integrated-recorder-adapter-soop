# Architecture and protocol provenance

The modular Python sources are canonical. They port the separately maintained Go
Grid PoC and use Streamlink's public `Plugin`, `Stream`, and `StreamIO` interfaces.
The plugin has a distinct `soopgrid` ID and matcher priority so the built-in `soop`
plugin remains available for HLS delegation.

```text
Streamlink
  └─ soopgrid plugin
      ├─ grid: first-party HTTP bootstrap -> Gateway -> Center -> Parent
      │                                  -> 0xC748 -> H.264/AAC -> one TS mux
      ├─ hls: delegates to Streamlink's built-in SOOP plugin
      └─ auto: Grid; initial transport-only failure may select built-in HLS
```

The receive path is download-only. It does not listen for peers, upload media,
modify Streamlink, or invoke a Go process or official SOOP agent.

## Protocol and session safety

`protocol.py` keeps separate bounded framing for Gateway, Center, and Parent. A
normal server-issued Gateway certificate is checked before use. Center rediscovery
uses the new Center-issued node key. Credentials and tickets are kept in memory and
cleared after bootstrap; silent account reauthentication is not implemented.
Broadcast passwords are sent only in Center JOIN. Explicit server denial is terminal.

Anonymous public bootstrap does not enumerate live-list pages. It validates the
requested channel and optional BNO, directly checks Live metadata, verifies BJID and
BNO, checks password and any explicit restriction fields, and submits the exact BNO
to the normal Watch API. Only a positive server result and valid returned endpoint
and ticket proceeds. The Live schema does not expose every policy flag; absence of a
restriction field is not inferred as permission, so the Watch response is
authoritative. Automatic BNO switching is never done.

`parent.py` uses one active Parent. Transport EOF, reset, timeout, or stall can move
to another Center-issued candidate and then perform at most one fresh Center
rediscovery per outage. Explicit denials and malformed protocol data do not retry.
The capture budget, if configured, is a single deadline for Parent connections,
handshakes, backoff and rediscovery; bootstrap occurs before this budget and has
its own bounded HTTP/network operation timeouts.

## Recovery limits

On reconnect, the receiver asks for the last accepted frame as an overlap anchor.
It accepts recovery only when frame number, kind, raw timestamp and SHA-256 payload
match, then requires exact next-frame numbering and nondecreasing per-stream time.
The muxer and timestamp origin are retained. Missing overlap, sequence reset, changed
quality/identity, timestamp regression, or conflicting data terminates output and
records a gap. No frame is fabricated. This behavior is offline/loopback-tested;
live SOOP cache overlap and natural Parent failure have not been verified.

Active-active Parent reception and cross-Parent aggregation are deliberately absent.
The protocol's simultaneous-session limit, node-key scope and shared sequence space
remain unverified. Multiple Parent connections will not be attempted speculatively.

## H.264 timeline and MPEG-TS

`media.py` validates the observed Grid chunk header and preserves original payload
bytes. `h264clock.py` currently supports progressive Annex-B H.264, POC type 0,
non-FMO PPS, one picture per Grid video chunk and fixed-frame-rate VUI timing. It
derives display order from POC and decode order from the observed Grid 100-ns
timestamp. It rejects absent/unsupported timing, interlacing, unsupported POC mode,
codec-configuration changes, nonmonotonic Grid video time, and PTS-before-DTS rather
than guessing from a nominal FPS. SPS DPB/reorder bounds constrain POC movement.

`mpegts.py` emits PTS+DTS PES headers for video, PTS for AAC, and PCR from video DTS;
timestamps are serialized in the standard 33-bit clock domain. It retains PAT/PMT,
PIDs and continuity counters. The output begins only when SPS/PPS/IDR are available.
Raw H.264 and AAC payloads are never transcoded. The legacy PTS-only `TSMux.add()`
exists only for old Go golden-vector parity tests and is not called by live receive.

An offline synthetic B-frame/AAC stream and a historical private Grid capture were
fully decoded without nonmonotonic-DTS diagnostics after the timeline change. This
does not establish all H.264 profiles, arbitrary Grid timestamp semantics, subjective
A/V sync, or long-session clock-wrap behavior. Unsupported structures fail closed.

## Bounded resources and output

`StreamIO` uses a bounded ring-buffer-sized byte queue (maximum 16 MiB), bounded
network frame sizes, bounded packet/control loops between media progress, one
rediscovery per outage, and cancellable backoff. The cumulative lifetime media byte
cap is optional; the duration cap is optional. Neither disables finite queues or
per-operation timeouts. Closing the reader cancels sockets and backpressure waits.
The receiver does not retain all recorded media in memory.

Streamlink HLS uses its maintained built-in plugin. `auto` only switches before any
Grid TS output and only on an allowlisted initial transport error; it does not mask
authorization, protocol, codec, or media-continuity failures. It never appends HLS
bytes to an already-started Grid TS timeline.

API references inspected from Streamlink 8.6.2 include its official Stream/StreamIO,
SOOP, and TwitCasting plugin implementations. No Streamlink source is patched or
vendored.
