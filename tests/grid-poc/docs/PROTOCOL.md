# Native TCP Grid protocol notes

This document describes only the path implemented in this PoC. It is an observed, version-sensitive protocol summary, not an official SOOP specification.

## Bootstrap modes

### `watch`

1. Read the live list and require the exact broadcaster/BNO to be present as password-free and not subscription-only.
2. Request first-party `type=live` metadata for that exact target.
3. Obtain the public viewer's Watch ticket and Gateway/Center endpoints from `/broad/a/watch`.
4. Use the session values in memory for the Gateway and Center exchange.

### `native`

1. POST the account login to SOOP's first-party login endpoint using an in-memory Cookie Jar.
2. Check the account session and request authenticated `type=live` metadata for the exact target.
3. Use `CHANNEL.TK` (or a naturally scoped `AuthTicket`) and `CHANNEL.FTK` for native Gateway setup.
4. When `-require-adult` is selected, require `ACHK=1` from the authenticated live response.
5. For an explicitly password-protected target, keep the stream password out of the HTTP metadata request and add it to Center JOIN Service 70.

Both paths use normal server-issued values. The command rejects missing endpoints or ticket fields and does not fabricate credentials.

## Gateway

The current encoder sends the Service 6 prelude (`0x03DF`) followed by the P1 login (`0x03E2`), reads and parses the bounded login reply, then requests the certificate (`0x0415`) on the same connection. The issued certificate is consumed in memory and passed to the Center client; raw credential replies are not written to the capture.

The Gateway packet format and the native login body are implemented in `internal/gateway`. The 2023 public articles include packet examples; the P1 and Service 6 details here also use the later independent REA analysis and successful server exchange. Field meanings that are not needed by this receiver are not asserted.

## Center and Parent

The Center exchange uses the native framed stream and current session state. In the successful native path, the observed service order is:

```text
2 → 3 → 0x3E → 0x14 → 0x2A → 0x46
```

Service `0x46` is the channel JOIN. Its body carries the issued ticket and requested quality; only the explicit password mode fills `passwd`. The client advertises zero upload bandwidth and no peer listening port.

After JOIN, the client discovers a Parent from the Center's response, validates the returned endpoint, and connects only to that server-provided Parent. It does not enumerate IP ranges or scan peers.

## Parent media exchange

The 16-byte Parent packet header uses the version, command, body size, checksum, and reserved/unknown words in `internal/protocol`. Bodies are size-checked before parsing. The supported flow includes:

1. `0xCB2E` stream request.
2. `0xC746` or `0x1BC7` stream response, followed by `0xCB2F`.
3. `0xC747` cache header, followed by `0xCB30` cache-data request.
4. `0xC748` cache media response.
5. `0xC742` push frames; the receiver switches to the live push flow with `0xCB2B`.

The Parent client writes protocol control requests and receives media. It contains no media re-upload feature. Some control fields reflect observed native behavior and may change with the service.

## Media storage

Each accepted `0xC748` packet is retained as wire bytes and separated payload bytes. Codec-recognized H.264 and AAC chunks are copied byte-for-byte to elementary-stream files and added to an MPEG-TS muxer. No re-encoding occurs. `playable.mp4` is an optional stream-copy remux for playback and verification.

## Public references

- [Blackcon: network initialization](https://blackcon.github.io/posts/afreecatv-analysis-1-network-init/)
- [Blackcon: Grid network](https://blackcon.github.io/posts/afreecatv-analysis-2-grid-network/)
- [Blackcon: P2P packet analysis](https://blackcon.github.io/posts/afreecatv-analysis-3-p2p-packet/)
- [famomatic/ytv1 SOOP P2P Grid analysis](https://github.com/famomatic/ytv1/blob/main/docs/SOOP_P2P_GRID_ANALYSIS.md)

The above projects are cited as protocol references. This PoC has no runtime dependency on them and does not bundle their binaries or private data.
