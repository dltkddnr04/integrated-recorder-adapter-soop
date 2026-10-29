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

The retained [`LIVE_VALIDATION_30M_20261028.json`](LIVE_VALIDATION_30M_20261028.json)
is **not a full-file decode pass**: its single 1,800-second budget was consumed
before FFmpeg and the AAC validator reached EOF. It reports `timed_out=true`,
partial decoded coverage of 1,637.5 seconds, and `verdict=fail` for validation
timeout/minimum-duration gates. Those results are incomplete evidence, not a
codec-failure finding. On 2026-10-28, a separate direct FFmpeg run decoded the
entire video elementary output and audio output from the retained TS using raw
outputs (avoiding the null-muxer's timestamp-quantization warnings): video
exit 0 in 505.232 seconds, audio exit 0 in 5.125 seconds, and zero FFmpeg
diagnostic bytes for either run.

An independent PES extraction compared the full source elementary streams
against the final TS. H.264 source and PES were both 1,798,277,036 bytes with
108,010 access units and SHA-256
`6b56f092188e559c949cb4ca5754e867ab7442982e4839e9703ef7f013f06fbd`. AAC
source and PES were both 43,790,621 bytes with SHA-256
`654301291fc3fa34e016fe4b769fb7199e4e7fa5e19ce6a1bc30b95bd3e7506e`; parsing
found 84,375 AAC access units, 86,400,000 samples/channel, and no trailing
bytes. The generated decoder prefix was 0 bytes in this capture. The path-free
PES/TS scan reports zero truncated/extra/orphan PES payload bytes, zero TS sync,
continuity, PCR, DTS, PSI CRC/structure errors, and 10,086,527 TS packets.
This PES identity result is separate from the timed-out aggregate verifier.

The user output `recording.ts` and `private-capture/recording.ts` were compared
read-only: `cmp` matched and both SHA-256 values are
`bd7d77237ba9d698bd655f90347790bcb4b63546559377cfefca9ddcc1e09ea6`. Receiver
emit, StreamIO accepted and consumed byte counts were all 1,896,267,076, with
zero discarded/unaccepted bytes and the final process summary showing drain
complete. The capture-time `grid-analysis.json` has an older
`stream_output_drain_complete=0`; it was serialized when MediaSink closed,
before the reader's final drain. The new code records that field as pending in
the per-sink capture file and reserves the final drain result for the reader
summary.

- Decoded H.264: 108,010 pictures over 1,800.093667 seconds.
- Decoded AAC: 84,375 access units, no trailing bytes; 86,400,000 PCM samples
  per channel at 48 kHz, exactly 1,800.000 seconds.
- TS: 10,086,527 packets; sync, continuity, transport, adaptation, PSI CRC and
  PSI structure errors all zero. PAT and PMT observed 1,801 times each.
- PCR: 108,010 samples, zero regressions; largest value step 25 ms.
- Video and audio packet timestamps were present; DTS regressions, PTS-before-DTS,
  packet timestamp overlaps, and malformed packet rows were zero.
- Independent full FFmpeg video and audio decode completed without diagnostics;
  decoded audio PCM count was independently measured as 345,600,000 bytes
  (86,400,000 samples/channel, 1,800.000 seconds).
- TS SHA-256: `bd7d77237ba9d698bd655f90347790bcb4b63546559377cfefca9ddcc1e09ea6`.

The retained aggregate verifier reported an audio/video coverage-offset change of
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

## Post-audit replay on 2026-10-29

The source and generated bundle under test at the time of the 2026-10-29
timeline-audit replay (`6d456f0e94c7ae8beb4eaa0bb5cee47b860c918e37ad5a967c7a6dd863751ed2`,
before the later authentication-interface edits) were replayed against both retained 30-minute raw Grid captures. NFS originals
were read-only; replay output was compared in memory with the archived final TS.

- For `400f…`, the replay emitted 1,896,267,076 TS bytes with SHA-256
  `bd7d77237ba9d698bd655f90347790bcb4b63546559377cfefca9ddcc1e09ea6`,
  byte-for-byte matching the archived TS. The 108,010-frame H.264 and 84,375-AU
  AAC payload hashes matched their retained elementary-stream hashes; the new
  track-span gate reported `consistent`.
- For the B-frame `03b31e…` capture, the replay emitted 1,662,461,064 TS bytes
  with SHA-256
  `5c275e5d1f307d53431b891da4c0f714467d43c37d92e848b9b7cdb5abbbba82`,
  byte-for-byte matching the archived TS. The 108,107-frame H.264 and 84,456-AU
  AAC payload hashes matched the retained elementary-stream hashes; the new
  track-span gate reported `consistent`. This is an offline replay of retained
  live Grid bytes, not a new 30-minute recording with the current bundle.

A separate bounded Grid-only live smoke test used the current bundle, a
12-second duration limit, and no HLS fallback. It produced a 12,132,392-byte
TS with H.264 1920x1080 at 60 fps and AAC 48 kHz stereo. The independent
verifier passed: 64,534 TS packets, zero sync/continuity/PSI/PCR/DTS errors,
zero PTS-before-DTS, 790 decoded video frames, 624 AAC access units, and
638,976 decoded audio samples per channel. Decoded track coverage was about
13.2 seconds; its approximately 95 ms boundary-offset change is not evidence
of perceptual synchronization. The sandboxed attempt failed before media
access; the single network-enabled retry succeeded. This short capture does
not constitute a long-duration test.
