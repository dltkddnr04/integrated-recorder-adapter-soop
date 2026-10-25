# Python validation

This report preserves evidence from several historical revisions. The current
user-installed filename and external ID are `soop_grid.py` / `soop_grid`;
`dist/soop.py` remains the generated artifact. Historical packaging descriptions
below refer to the revision under test at that time, not current installation steps.

Validation date: 2026-10-25 UTC. Host: isolated development checkout on Ubuntu x86-64, Python 3.12.3, official Streamlink 8.6.2, PyCryptodome 3.24.1. Go reference: commit `3518f21c8feeb7b046562945ade6702e2803b450`. FFmpeg/FFprobe 9.0 were used only after capture, outside the receiver.

## Actual public Native Grid capture

The official Streamlink CLI loaded `plugins/soop.py` from an unrelated `/tmp` working directory with `soop_grid` installed and no PYTHONPATH. It used a current public, password-free, non-subscription live selected from the normal live list. The same BNO was retained during the tests; its identifier and all endpoints remain in private local evidence, not in this repository.

The successful path was public Watch -> Gateway -> Center -> server-issued Parent -> actual `0xC748` -> Python H.264/AAC extraction -> Python TS muxer -> Streamlink output. There was no Go executable, external agent, HLS manifest/segment request, or FFmpeg receive process. Gateway and Center replies had to pass the normal parser and authentication state machine to reach Parent discovery. Credential responses were not dumped.

| Measurement | Final stdout capture |
| --- | --- |
| Start | 2026-10-25T02:27:40.489344Z |
| Configured receive duration / CLI wall time | 30 s / 32.59 s |
| Official CLI exit status | 0 |
| Received `0xC748` media packets | 1,980 |
| Media wire bytes / unchanged Payload bytes | 33,542,055 / 33,373,755 |
| Streamlink TS output | 34,338,200 bytes |
| Video | H.264, 1920×1080, 60/1 fps, 1,917 decoded frames |
| Video duration / overall TS duration | 31.949667 s / 32.420667 s |
| Audio | AAC, 48 kHz, stereo, 1,512 decoded ADTS frames |
| Grid audio chunks / audio duration | 63 / 31.765333 s |
| Frame sequence discontinuities | 0 |
| Whole-file video + audio decode | PASS, exit 0, no diagnostics |

Media timeline and wall time differ because cached data and initial audio/video timestamps are preserved. FFprobe starts were 1.000 s video and 0.529 s audio. These are measured timeline facts, not a subjective A/V synchronization claim.

Raw wire was independently reparsed after capture. Every stored packet was `0xC748` with a valid header; re-extracted Payload, H.264 and AAC hashes matched their separate files. Streamlink stdout was byte-identical to the Python muxer's `recording.ts`. A separate fresh CLI process was launched after exit and the saved recording hash remained unchanged. Full hashes and raw files are private local artifacts; public evidence contains only sanitized facts in [live-validation.json](live-validation.json).

A preceding `--output` capture also exited 0 and wrote 38,837,792 TS bytes; whole-file A/V decoding passed. An initial capture exposed an EOF bug: the deadline's read timeout was treated as failure. The implementation now treats that timeout as EOF only when the configured deadline has actually elapsed; an earlier timeout remains an error, with regression tests. No more live captures were needed after the final successful stdout test. File and stdout paths were exercised; interactive VLC/mpv was NOT_TESTED.

## Offline and installation checks

- Final check results: 91 Python tests passed; Ruff check/format, Python compilation, wheel build, official CLI sideload, Go PoC test/vet/build, and Adapter test/vet/build passed. Public-file secret/target-literal and artifact/large-file audits passed. Original adapter and Go PoC sources have no diff.
- The Python test suite covers framing/limits/checksums/truncation, synthetic issued credentials, normal/denied account sessions, Cookie Jar scope, exact bootstrap targets, password field placement, Center/Parent control ordering, SPS cropping, AAC metadata, TS CRC/continuity/PTS, partial reads, EOF, errors, timeouts, backpressure and concurrent close.
- Synthetic `tests/golden.json` was generated from a temporary copy of the Go module with test-only exporters. The repository's Go PoC was not edited. Inputs include Service 6 GUID `offline-fixture-guid` with timestamp `0x65a1b2c3`/counter `0x1234`; synthetic certificate timestamp 1700000000; GUID `0123456789ABCDEF0123456789ABCDEF`; BNO 12345; `log` value `offline`; synthetic ticket/password strings; and hand-written synthetic NAL/ADTS bytes. None is a live credential or captured broadcast.
- Go/Python results match byte-for-byte for the long-input native hash quirk, Service 6, P1 login, certificate request, Center authentication, ordinary/password JOIN and the deterministic TS sequence.
- Actual CLI URL recognition and JSON stream selection passed through official plugin sideloading. Editable and normal `py3-none-any` wheel installations were checked from `/tmp` without PYTHONPATH. The normal wheel imports `soop_grid` from site-packages. The external plugin remains in the source tree for `--plugin-dirs`.
- The test suite verifies official Streamlink Python source hashes against its installed wheel RECORD. No core modification was found. Ruff and bytecode compilation passed.
- Go PoC tests/build and existing Adapter tests/build passed after the Python addition. Their source files are unchanged.

## Scope of claims

| Path | Python status |
| --- | --- |
| Public Native Grid, actual Parent media, TS file/stdout and full decoding | VERIFIED |
| Account TK/FTK, adult confirmation, password Center JOIN | IMPLEMENTED, synthetic tests passed; live NOT_TESTED |
| Subscriber-only / paid access | NOT_VERIFIED, no support claim |
| HLS or Colony/WebRTC | Not part of this transport |

Only the Python live result above establishes this port's capture success. Historical Go adult/password success is protocol provenance, not Python live evidence.

## Reliability revision

Before bundling: 129 modular tests passed (including Go Golden Vectors, local TCP
failover, cancellation and full FFmpeg decode of generated recovered H.264/AAC TS).
Synthetic tests do not imply SOOP live cache replay support.
See [RELIABILITY.md](RELIABILITY.md) for verified scope and failure policies.

## Final single-file revision

145 modular/construction tests and 140 identical bundled-object regressions passed.
The final one-file plugin ran in a new Streamlink-only venv and received an exact
public target absent from the first list page. Its 188 C748 packets yielded
1920×1080/30fps H.264 + AAC. Full ES/TS decoding passed, but B-frame DTS timing
warnings remain in timestamped-output validation. See
[RELIABILITY_RESULT.md](RELIABILITY_RESULT.md) for measurements and distinctions.
This does not supersede previous captures' own measurements or imply live failover.

## Production-readiness revision — 2026-10-25

The later source revision adds supported-subset H.264 DTS/PTS reconstruction,
continuous default capture with optional time/byte bounds, exact-overlap single-Parent
failover, and built-in HLS delegation through a distinct external plugin ID. The
previous live capture above used the earlier PTS-only revision; it is historical
evidence and was not captured again with this revision.

An already preserved private Grid capture was reparsed through the new H.264 clock
and TS muxer: 188 media chunks, H.264 1920×1080 at 30 fps, AAC 44.1 kHz stereo, and a
3,023,792-byte TS. The video PTS reorders according to H.264 POC while DTS increases
in Grid decode order. FFmpeg 9 full video+audio decode with `-xerror` exited 0 with no
diagnostics, including no nonmonotonic-DTS warning. This is a reprocessing of a
historical private input, not a new live capture. The Grid timestamp-to-clock model
is supported by this sample and synthetic fixtures; broad source compatibility and
perceptual A/V sync are not established.

Current offline suite at that historical revision: 159 tests pass when the isolated loopback socket tests are
allowed. In the default sandbox those local sockets are denied with EPERM; the same
subset passes when run with local socket permission. A clean virtual environment with
Streamlink 8.6.2 only (plus its own dependencies), no project package, no PYTHONPATH,
and a copied `dist/soop.py` installed under the then-current external-plugin
filename passes plugin discovery and JSON stream enumeration. The bundle is
reproducible and source/bundle regression tests pass. Linux live tests were not run
for that revision.

Current status: HLS delegation is verified with an offline mocked official plugin,
but fresh network HLS behavior is not revalidated. Parent failover/cache overlap is
synthetic and local-loopback verified only. Simultaneous multi-Parent reception,
30-minute/3-hour/6-hour recording, automatic reauthentication, and Windows/macOS
runtime loading are not verified. See [PRODUCTION_READINESS_RESULT.md](PRODUCTION_READINESS_RESULT.md).

## Subsequent controlled-live recovery validation

The later [LIVE_FAILOVER_VALIDATION.md](LIVE_FAILOVER_VALIDATION.md) records a
150-second actual-public-broadcast run with three sequential client-owned Parent TCP
closes. Each recovery performed Center rediscovery, used a new Node Key, reconnected
to a different observed endpoint identity, verified one exact server Cache-overlap
frame, discarded the duplicate, and accepted subsequent frames. The output reported
no gaps, duplicate output, or identity conflicts; the final TS passed full H.264/AAC
decode and timing/continuity checks. This is controlled client-side failure injection,
not a natural remote Parent failure or proof of independent physical failure domains.
It does not establish multi-hour endurance.
