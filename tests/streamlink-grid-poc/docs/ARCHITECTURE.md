# Architecture and protocol provenance

The baseline is source.soop commit `3518f21c8feeb7b046562945ade6702e2803b450`, `tests/grid-poc/`. See its protocol and historical-validation documents. This port has no HLS fallback and advertises no peer listening port or media upload capability.

```text
official Streamlink CLI -> sideload plugins/soop.py -> SoopGridStream.open()
    -> private HTTP Cookie Jar / public Watch or authenticated TK + FTK
    -> Gateway 0x03DF / 0x03E2 / 0x0415
    -> Center 2 / 3 / 0x3E / 0x14 / 0x2A / 0x46
    -> handle / Parent discovery 0x50 / 0x69
    -> Parent 0xCB2E / 0xCB2F / 0xCB30; 0xC742 -> 0xCB2B
    -> actual 0xC748 -> unchanged H.264 / AAC -> Python TS muxer
    -> bounded buffer -> StreamIO.read() -> Streamlink file/stdout/player output
```

`protocol.py` bounds headers and bodies independently for Gateway (16-byte LE32 header), Center (20-byte LE32 header), and Parent (16-byte LE16/LE32 header, version 0x0202). Socket reads use whole-frame deadlines. Only globally routable addresses supplied through the normal session are connected to.

`crypto.py` ports the native RIPEMD-128 finalizer quirk (first remainder bytes reused after complete blocks), not a replacement standard hash. Service 6 and Center auth reproduce the Go mixers and AES-CTR zero-padding. A certificate must contain a server-issued seed whose digest validates; the runtime never creates a substitute server credential. Test seeds are synthetic and never used for live connections.

The authenticated path uses private first-party requests and normal Cookie Jar scoping, with no credential cache. Live metadata `TK` (or scoped `AuthTicket`) and `FTK` are distinct from the anonymous Watch ticket. The password is only part of Center JOIN. Denial ends the operation.

`media.py` validates the observed 69-byte Grid body header, zero extension, exact payload length, frame kind and raw 100-ns timestamp. It preserves received bytes and optionally extracts SPS dimensions/cropping and ADTS metadata. `mpegts.py` ports PAT/PMT/CRC, PID 0x100 video / 0x101 audio, PES, PTS/PCR and continuity counters. Playable TS starts at SPS plus IDR; all preroll remains in raw/elementary files. There is no independent DTS reconstruction beyond the Go baseline's PTS-only PES behavior.

`SoopGridReader` owns one worker and tracked sockets. The bounded byte buffer splits oversized writes, waits for consumers and fails on sustained backpressure. Open waits for actual TS output. Partial reads drain pending bytes before EOF or a stored error. Open failures use StreamError; read failures use OSError, as required by Streamlink 8.6.2's output runner. Close cancels producers, wakes readers, shuts down sockets, and joins the worker (HTTP request operations have bounded timeouts). `read(-1)` drains at most the configured buffer capacity; Streamlink's runner uses positive read sizes.

API references inspected from the official installed Streamlink 8.6.2 package:

- [Stream and StreamIO](https://github.com/streamlink/streamlink/blob/8.6.2/src/streamlink/stream/stream.py)
- [TwitCasting custom reader](https://github.com/streamlink/streamlink/blob/8.6.2/src/streamlink/plugins/twitcasting.py)
- [SOOP first-party authentication](https://github.com/streamlink/streamlink/blob/8.6.2/src/streamlink/plugins/soop.py)
- [CLI output runner](https://github.com/streamlink/streamlink/blob/8.6.2/src/streamlink_cli/streamrunner.py)

The official installed package's Python source hashes are checked against its wheel RECORD by the test suite. No Streamlink core source is patched or distributed here.
