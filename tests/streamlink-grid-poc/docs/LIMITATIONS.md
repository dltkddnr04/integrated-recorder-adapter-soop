# Limitations and licensing

- Experimental, bounded Native TCP Grid sessions only: 1–60 seconds, 512 MiB media wire ceiling, no long-duration stability or reconnect guarantee.
- Public anonymous live capture was verified in Python. Native account TK/FTK, adult confirmation, and password Center JOIN were ported and tested offline; those protected Python live paths remain NOT_TESTED. Historical Go success does not prove Python live success.
- No subscriber-only or paid access support is asserted. Server denials are not bypassed.
- The public Watch route uses direct exact-target Live/Watch validation without a listing. Live metadata does not expose every listing policy; explicit Watch permission is required. Channel-only URLs require the ordinary page's live BNO marker. URL enumeration is not a media-access check.
- Only the observed Annex-B H.264 plus ADTS AAC layout with a zero extension is accepted. HEVC and other codecs are not supported. Quality codes do not prove resolution.
- TS starts at a received SPS/IDR. Earlier data remains in raw files. PTS-only PES matches the Go PoC; independent DTS/reordering handling is not implemented. Arbitrary B-frame streams are not a general compatibility claim.
- HTTP bootstrap close is limited by underlying HTTP/DNS timeouts; active binary TCP sockets are explicitly shut down. The worker is daemonized and close waits up to 12 seconds. This does not claim instant interruption of every OS resolver failure.
- Interactive VLC/mpv playback and protected-stream live tests were not performed. File/stdout plus whole-file decoding are the demonstrated playback evidence.
- The prior Go password test's one-frame MP4 decode-count difference remains unexplained. This Python validation uses original TS as its reference; stream-copy remux is optional and not an encoding step.
- Python immutable strings/bytes cannot provide guaranteed memory erasure. Secrets are kept in memory, references are dropped, secret environment entries are removed, and cookies are never written to disk. Debugging tools outside this plugin can still inspect process memory.

## License / future upstream work

The Python implementation and synthetic tests are derived from this project's Go PoC and are distributed under **AGPL-3.0-only**, with the repository license copied into this standalone package. New source files identify source.soop contributors. The referenced public protocol studies are listed in the Go PoC; no proprietary SOOP binaries, decompilation dumps, or third-party source files are bundled.

Streamlink is used through its normal package and public plugin interfaces under its own BSD license. PyCryptodome is Streamlink's normal AES dependency and retains its own license. No dependency code is relabeled as AGPL or vendored here. The plugin design consulted the official SOOP/TwitCasting/Stream classes; it does not copy the complete built-in SOOP plugin or implement its HLS features.

A future Streamlink upstream contribution cannot present this AGPL-derived code as BSD. It would require permission/relicensing from all relevant rights holders, including the Go implementation's contributors, or a separately reviewed implementation under suitable terms. No upstream PR is part of this work.

Bounded Parent recovery is synthetic/loopback-tested, not live-failover verified.
It requires exact cached overlap and consecutive frames; unprovable gaps terminate.
Automatic certificate refresh and TS discontinuity restart are not implemented.
See [RELIABILITY.md](RELIABILITY.md).
