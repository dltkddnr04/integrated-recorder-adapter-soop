# Exact-current-bundle 30-minute Native Grid validation — 2026-10-28

**Result: PASS for this recording's duration, structural, timestamp, and full A/V decode gates.**
This is one controlled Grid-only recording, not a universal stability or
perceptual synchronization claim. This broadcast contained no H.264 B slices.

## Run identity

- Repository revision: `8366328d0968c16942045f1ccb0c65c98172cbbc` plus the
  uncommitted working-tree changes described by the local run manifest.
- Standalone artifact: `dist/soop_grid.py`, SHA-256
  `400f028ed99467ad644d61525f8a59f85477221dbba28708710ff06d77682cd0`.
- Linux x86_64, Python 3.12, Streamlink 8.6.2, FFmpeg/ffprobe 9.0.2.
- Explicit `--soop-grid-transport grid`; no HLS fallback.
- Started `2026-10-28T10:08:49.356468Z`; finished
  `2026-10-28T10:38:52.187873Z`; elapsed `1802.831` seconds.
- Process exit 0, stop reason `duration_limit`, no receiver error, signal,
  supervisor timeout, or output-drain loss.

The recording, private raw Grid captures, and process/receiver summaries are
preserved in the authorized private validation archive. Raw media, broadcast
identifiers, endpoints, credentials, and private filesystem paths are excluded
from this repository.

## Receiver and resource evidence

- Output: 1,896,267,076 bytes; SHA-256
  `bd7d77237ba9d698bd655f90347790bcb4b63546559377cfefca9ddcc1e09ea6`.
- Grid media: 111,610 messages; 108,010 video frames and 3,600 audio messages.
- Receiver output: 108,010 video frames; 84,375 AAC access units and 86,400,000
  decoded core samples per channel at 48 kHz.
- Receiver reported zero missing frames, frame-identity conflicts, duplicate
  exclusions, or sequence discontinuities. This session had no Parent recovery.
- Peak RSS 48,173,056 bytes; peak bounded buffer 1,160,496 bytes; 3 threads and
  13 file descriptors at peak. At shutdown, active sockets and buffer occupancy
  were zero; output drain completed with zero discarded bytes.

## Independent full-file verification

The first verification attempt had a 600-second total wall-clock limit and
timed out before reaching EOF; it is not treated as a media failure or pass.
A second read-only verification with a longer wall-clock allowance completed
with exit 0 and `verdict=pass`. Its scope was structural, decode, and timing
validation; it did not independently hash-match source Grid payloads to every
output PES. The verifier's path-free aggregate JSON is available as
[`LIVE_VALIDATION_30M_20261028.json`](LIVE_VALIDATION_30M_20261028.json).

- Decoded H.264: 108,010 pictures over 1,800.093667 seconds.
- Decoded AAC: 84,375 access units, no trailing bytes; 86,400,000 PCM samples
  per channel at 48 kHz, exactly 1,800.000 seconds.
- TS: 10,086,527 packets; sync, continuity, transport, adaptation, PSI CRC and
  PSI structure errors all zero. PAT and PMT observed 1,801 times each.
- PCR: 108,010 samples, zero regressions; largest value step 25 ms.
- Video and audio packet timestamps were present; DTS regressions, PTS-before-DTS,
  packet timestamp overlaps, and malformed packet rows were zero.
- FFmpeg full decode and AAC access-unit scan completed without diagnostics.
- TS SHA-256: `bd7d77237ba9d698bd655f90347790bcb4b63546559377cfefca9ddcc1e09ea6`.

The independent verifier reported an audio/video coverage-offset change of
`-93.667 ms` (start offset `-130 ms`, end offset approximately `-223.667 ms`).
These are decoded track-coverage boundary comparisons, not a source-clock drift
measurement or perceptual lip-sync test.

## H.264 scope and remaining limits

The stream used POC type 2 with fixed VUI timing and HRD/SEI. It contained 901
I slices and 107,109 P slices, with **zero B slices** and zero FFprobe PTS
reorder steps. Therefore this exact recording verifies a 30-minute current
artifact run but does not itself exercise live B-frame reordering.

A separate earlier 30-minute run with bundle
`03b31e3712160cc5bdcc398e63aeb602c510994d144d56c079b8f8998f9d8ff1` contained
80,179 B slices and passed full-file verification. That is distinct-artifact
evidence; the two runs are not represented as one exact-build B-frame test.
The receiver still fails closed on a separate observed HRD/POC ordering conflict.
Official SOOP clock semantics and perceptual A/V sync remain unproven.

## Final status

The exact `400f…` bundle completed one full 30-minute Grid-only recording and
passed its independent structural, timing, and full-decode gates. This is
meaningful long-run evidence, but not proof of every supported H.264 timing
variant, B-frame live behavior on this exact run, natural-failure recovery over
30 minutes, or universal zero-loss operation. Overall readiness remains
**PARTIAL / Experimental-Beta**.
