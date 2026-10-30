# Legacy HRD retirement validation

Experimental/Beta; baseline `3bfbaa2df85a21bd048d064a9fe76be8d1fdef00`.

Production requires explicit Grid composition, with one rescale of raw start plus composition. HRD/no-reorder timestamp generation and HRD-only conflict facts now live in [LegacyH264Clock](LEGACY_HRD_AUDIT.md), outside the bundle. SPS/VUI/SEI syntax, POC reconstruction, transactional rollback and POC–PTS checks stay in production. Removed unused clock state: epoch_pts, reorder_lead, duration and reorder. Removed unreachable initial HRD-IDR staging and EOF flush from MediaSink. The audio sample clock, origin, muxer, recovery and wire parser are unchanged.

## Offline results

Python 3.12.3 / Streamlink 8.6.2 / FFmpeg and FFprobe N-128155-g8e362b7b51-20261028:

- Modular suite: 627 passed, 0 skipped; bundle suite: 618 passed, 0 skipped. Historical HRD tests run through the external audit API; syntax/production timing tests use the production class.
- Ruff check/format, compileall, deterministic bundle, checksum manifest, CLI loading and diff checks passed.
- Actual A-capture composition mutation 830000 to 0: h264_picture_order_conflict after one valid output picture. This is fail-closed partial output, not rejection before all output.
- A/B/C/D output TS hashes, H.264/AAC payload hashes, origin, frame/AU counts and completion status are identical before/after. Identical full TS bytes also preserve every serialized DTS/PTS/PCR, PSI and continuity counter.

| Capture | Pictures | AAC AUs | Unchanged TS SHA-256 |
|---|---:|---:|---|
| A | 2998 | 2352 | fac89c318ec928e2a89c445590373bc262134e5a9353e2f4dc6e3d76711322ba |
| B | 2161 | 1687 | 7aa24876357878d708e10497abd2e773357755fb83ec1c36fe15856c5c90c4f9 |
| C | 2209 | 1704 | 624999bd183d2f2bbe0689c8604960cc3ec00c1bb0477b9fdca0e0ea0e610542 |
| D | 2668 | 2109 | 74afe4143435a217197dea63a897c8fe4b10067a559426c0312cf7770c42ad0e |

All four final fixed-file outputs passed full FFmpeg H.264/AAC decoding and independent TS sync/continuity, PSI CRC, PCR, DTS/PTS and AAC checks. An earlier verifier run overlapped benchmark output rewrites and correctly reported input_changed_during_validation; that invalid test attempt was preserved separately and repeated on fixed files.

D retains exclusion of its 23 pre-IDR pictures. Historical independent decoded B-picture counts remain A=2229, C=1209; byte identity is regression evidence, not a new live B-frame result.

## Size and performance

Compared with baseline HEAD, h264clock.py: 1298 lines/51817 bytes to 1259/49366; MediaSink module: 653/28385 to 587/25234; bundle: 215075 to 210740 bytes. The offline audit module is not imported by production or linked into the standalone artifact.

Before measurements used the preserved partially cleaned bundle `3f6f8dee…`; after measurements used `27aff828…`. Three complete four-capture replay measurements had median user CPU 36.81s before / 37.45s after, peak RSS 247520/247692 KiB and wall time 38.89/42.57s. Other validation ran concurrently, so these are noisy observations and do not establish a performance gain or regression. No speed or memory improvement is claimed.

New bundle SHA-256: `27aff828ad815e7e6ba4265b13d7ae4b424a49dff07c50f99bc461ba253bc3c6`.

## Previous live evidence remains LIVE PARTIAL

The previous baseline bundle 9e08c19e… received Grid-only for 600.447 seconds: 18057 pictures, no B pictures, 25905 AAC AUs at 44100Hz, complete output drain, full TS decode and structure checks. It did not retain raw Grid wire, so its source-to-PES identity is unverified. Perceptual synchronization was not independently measured. The private recording and reports are preserved outside Git. This refactor opens no new SOOP session and does not replay that TS as Native Grid input.

POC type 1, Grid MMCO5, interlace, low-delay HRD, nonzero pic_struct and unobserved signed composition retain their existing restrictions. Parser sharing limits the audit tool's independence; external decoder and payload comparisons remain necessary.
