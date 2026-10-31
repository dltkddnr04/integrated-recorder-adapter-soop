# Native Grid Stable Candidate validation

## Result and scope

The Native Grid standalone bundle `30ea7bd28ee81fbd928307267b9c23c9130fb1a24ac7f4ae5a5d9ff0e5625283` completed the recorded validation gates below on Linux, Python 3.12, and Streamlink 8.6.2. This is a **Stable Candidate validation**, not a Stable release or a guarantee for every broadcast, network, or codec configuration. The plugin remains Experimental/Beta.

## Media and long-run evidence

- A 600-second source-matched A/V window passed independent picture/sample continuity, timestamp, decode, and Grid-to-PES payload identity checks.
- A separate 7,200.986-second Grid-only run passed full H.264/AAC decode, POC–PTS and DTS checks, TS structure/PSI/PCR checks, source-to-PES payload identity, and normal duration-limit completion, consumer EOF, and StreamIO drain.
- The long-run output contains 432,159 video pictures and 337,680 AAC access units (48 kHz, 345,784,320 decoded samples per channel). Its 122,345,700-byte TS contains exactly 650,775 complete 188-byte packets: `122345700 / 188 = 650775`. The independent report counted 650,775 packets in both capture and output TS and recorded the same TS SHA-256 for both.
- The 600-second event comparison passed with 239 matched events, maximum absolute added A/V offset 0.667 ms, and estimated 600-second drift +0.137 ms. The separate two-hour run matched 721 events with maximum absolute added offset 0.667 ms. These are source-relative measurements for the generated test signal; they do not establish perceptual synchronization for arbitrary broadcasts.

## Parent recovery and actual StreamIO drain

A controlled same-session test closed only the client-owned active Parent TCP socket three times. The receiver rediscovered Center, reconnected, validated an exact Cache overlap each time, excluded duplicate output, and resumed media. Recovery times were 206.503, 202.449, and 193.955 ms. A joined 60.494-second run exercised the standalone bundle's actual Reader → bounded buffer → consumer read → TS file → EOF/close path in the same session as the three faults. All three recoveries and the independent media/A/V gates passed. Accepted, emitted, and consumed bytes were each 19,650,324; discarded and unaccepted bytes were zero; natural consumer EOF, drain, worker shutdown, and socket closure were observed. Twenty-three A/V events covered the fault segments and met the configured offset limits.

The controlled test proves client-side socket-close recovery under the tested session. It does not establish recovery from a naturally failing remote Parent or a complete Internet outage.

An earlier 121.072-second wall-clock Pilot B remains a receiver-completion failure (`media_track_coverage_incomplete`). Its received media and emitted PES were independently byte-matched and 47 source-relative A/V events passed, but its roughly 517 ms final audio/video boundary difference has no recorded packet-arrival timestamps to prove whether another AAC bundle was due before the deadline. That run is not reclassified as a completion pass by the later 600-second and two-hour evidence.

## Boundaries

- Evidence is limited to Linux, Python 3.12, Streamlink 8.6.2, the tested standalone bundle, and the H.264/AAC structures present in the retained test streams. Unsupported H.264 structures and malformed/ambiguous media remain fail-closed.
- Natural server-side failures, total network loss, other operating systems, all broadcast variants, and perceptual lip-sync on arbitrary program content were not verified.
- Cyneti co-viewing compatibility was not tested.
- The final source and bundle suites each collect two tests that require a generated conformant POC type 2 stream: `test_poc2_without_fixed_clock_but_zero_reorder_constraint_decodes` and `test_poc2_b_slice_syntax_does_not_prove_zero_reordering`. They are `ENVIRONMENT_SKIP` because the retained FFmpeg 6.1.1 build has no `libopenh264` encoder. The current POC parsing/state unit tests and the recorded live H.264 runs passed, but those runs do not substitute for an independently encoded POC type 2 stream; no broader POC type 2 support claim is made here.
- Historical reports retain their original revision-specific results. Private source recordings and detailed manifests are held outside Git and are not part of this repository.

## Reproduction record

The measurements above refer to preserved private run manifests and independent reports. The run artifacts contain broadcast/source data and are intentionally excluded from the repository. The standalone artifact used for these checks is `dist/soop_grid.py`; its digest is recorded in `dist/SHA256SUMS`.
