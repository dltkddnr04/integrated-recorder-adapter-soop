# Controlled Live Failover Validation

Date: 2026-10-25

Scope: bounded public live capture; client-owned Parent socket closure only. No 30-minute endurance test was run.

## 1. Environment

| Component | Observed environment |
|---|---|
| OS | Linux x86_64 |
| Python | 3.12.3, isolated temporary virtual environment |
| Streamlink | 8.6.2 |
| PyCryptodome | 3.24.1 |
| FFmpeg / ffprobe | n9.0.2-35-g9a9b70bb8c-20261023, temporary user-scoped binaries; no system-wide install |
| Starting revision | `5355b4401e2060aa54d69693b67749feee5d6346` |
| Git policy | Existing modified and untracked files were preserved. No commit or push was made. |

The first complete test run used the restored FFmpeg tools but lacked local socket permissions in the sandbox. It reported 13 socket-related failures and 178 passes/skips; rerunning the same suite with loopback permission produced 191 passes. The controlled live experiment then exposed a genuine edge case: closing the local Parent socket caused `settimeout()` in `read_exact()` to raise an unclassified `OSError`, bypassing the receiver's transient-network recovery loop. `settimeout()` is now covered by the same fixed `network_read_failed` mapping as `recv()`, with a closed-socket regression test. The updated full suite passes 193 tests.

## 2. Fault Injection Implementation

Added the research-only `tools/live_fault_inject.py` harness and an offline isolation test. It is not part of the general plugin interface and is not bundled into `dist/soop_grid.py`.

The harness selects one candidate from a limited public live-list sample, then relies on the normal exact BJID/BNO, Live metadata, public Watch ticket and Grid bootstrap checks. It injects only after 100 accepted media packets and only closes the socket that sent the Parent `0xCB2E` handshake. Gateway and Center sockets remain untouched. It injects at most one or three sequential faults, requires at least 100 accepted packets between recoveries, and applies a single duration deadline and a 256/300 MiB wire budget. Per-run endpoint IDs are HMACs using an ephemeral key; the key and raw endpoint are not written. Output directories and diagnostics use owner-only permissions.

`INJECT_LOCAL_CLOSE` is a controlled client-side close. It is not described as a remote TCP reset or a naturally occurring server failure. `INJECT_LOCAL_READ_FAILURE` and silent-stall modes were not used in the live run.

## 3. Live Experiment Results

The first instrumented attempt stopped after the local close because of the `settimeout()` error described above. It did not recover and is a failure, not a successful trial. After the fix, one bounded trial and then one three-fault trial were run against a currently public live selected through the limited listing sample. Both used the Native Grid path directly; no HLS fallback was involved.

| Run | Capture limit / elapsed | Injections | Parent connections | Center rediscovery | Result |
|---|---:|---:|---:|---:|---|
| Single-fault confirmation | 60 s / 60.534 s | 1 | 2 | 1 | duration limit, exit 0; recovery and full media verification passed |
| Repeated same-session run | 150 s / 150.546 s | 3 | 4 | 3 | duration limit, exit 0; all three recoveries and full media verification passed |

The repeated run recovered through a fresh Center discovery and newly issued Node Key at each fault. Within-run HMAC endpoint IDs changed after each reconnection, so all three were **different endpoint reconnects** according to the observed IP:Port identity. This establishes endpoint change for these runs, not a claim that the endpoints are independent failure domains.

All three overlap frames matched the pre-fault accepted frame on frame number, kind, raw timestamp, payload length and SHA-256. One overlap duplicate per recovery was excluded from output. Each recovery accepted new frames after the overlap. No missing frame, identity conflict or unrecoverable range was reported. The harness observed no cache/live boundary signal sufficient to claim catch-up completion; live-boundary/catch-up remains unverified.

### Per-recovery timings (three-fault run)

Milliseconds are measured with a monotonic clock. “Failure detection” is local socket-close to the receiver's failure-detected event. The Center interval is rediscovery start to completion; connection and handshake are separate.

| Fault | Failure detection | Center rediscovery | Parent TCP connect | Parent handshake | Cache request to first media | Cache request to overlap verification | Close to first new frame | Close to recovery complete |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.324 | 83.620 | 13.363 | 26.387 | 24.998 | 25.176 | 151.100 | 151.170 |
| 2 | 0.337 | 80.526 | 4.557 | 9.628 | 28.152 | 28.473 | 125.977 | 126.043 |
| 3 | 0.387 | 94.311 | 6.120 | 10.925 | 16.404 | 16.558 | 130.612 | 130.698 |

These are three observations, not a statistical latency distribution. No baseline live failure timing exists, so no live percentage improvement is claimed. The prior synthetic benchmark is not used as a live-server comparison.

## 4. Media Integrity

The three-fault capture was 150.546 seconds of receiver runtime and 151.495 seconds in the muxed TS timeline. It contained 9,060 video and 295 audio output records. The accepted frame log had 9,355 records, zero duplicate frame numbers and zero non-consecutive frame-number steps. Receiver statistics reported 3 detected/recovered outage ranges, 3 overlap duplicates excluded, 0 missing frames, 0 identity conflicts and 0 unrecoverable ranges.

| Check | Result |
|---|---|
| ffprobe | exit 0; H.264 1920×1080 at 60 fps and AAC 48 kHz stereo identified |
| TS packet scan | 741,566 packets; sync errors 0; continuity errors 0; PAT/PMT each 151; PCR regressions 0 |
| Video packet timeline | 9,060 packets; non-monotonic DTS 0; PTS-before-DTS 0; PTS reorder steps 2,265 |
| Full decode | FFmpeg `-xerror`, mapped video and audio; exit 0; diagnostic lines 0 |
| File SHA-256 | `7a3c025b9d15a8814fa2348a000d9caea55c8d7c763a6915721e98c406fdb71e` |

The start times reported by ffprobe were video 1.066667 s and audio 0.588667 s. Both streams decode fully; this test did not include subjective audiovisual sync review, so it does not assert perceptual sync.

The single-fault capture also passed full decode: 1080p60 H.264/AAC, 302,787 TS packets, zero TS continuity errors/PCR regressions/DTS regressions, FFmpeg exit 0 with no diagnostics. Its TS SHA-256 was `7ef4e2cd55248d2b3fa1a24210e847c07f93bf1a298221b0ecd6cdbe44cdfb26`.

## 5. Changes and Regression Results

The only production-path change made for this task is to classify an OS error from either `socket.settimeout()` or `socket.recv()` as the existing transient `network_read_failed` error. This allowed the existing recovery loop to run without weakening overlap, continuity, authentication or retry checks. A regression test covers reading from a closed socket. A separate offline test confirms that the test injector recognizes and closes only the Parent handshake socket.

| Validation | Result |
|---|---|
| Python tests, including FFmpeg-dependent tests | 193 passed |
| Ruff | passed |
| Static single-file regeneration check | passed; SHA-256 `4752f2b4ad99c0f647adb9c992beaa0a9e4f635b3ca3ce5fa30e2edbcaea52e7` |
| Streamlink version check | 8.6.2 |
| `git diff --check` | passed |
| 30-minute endurance | not run, as required |
| HLS fallback | not used |

Private live captures and detailed JSONL remain in temporary directories outside the repository; no capture, target identifier, credential, ticket, raw endpoint or payload was added to the repository.

## 6. Final Status Matrix

| Item | Status | Evidence / limit |
|---|---|---|
| `CONTROLLED_LIVE_TCP_CLOSE` | VERIFIED | Parent-owned local socket closed after 100 accepted media packets; three sequential injections in one run |
| `SINGLE_LIVE_RECOVERY` | VERIFIED | Separate one-fault, 60-second run recovered and ended at duration limit, exit 0 |
| `THREE_CONSECUTIVE_LIVE_RECOVERIES` | VERIFIED | Three recoveries in one 150-second live session, each followed by progress |
| `CENTER_REDISCOVERY` | VERIFIED | One rediscovery per injected fault, total 3 |
| `NEW_NODE_KEY` | VERIFIED | Each rediscovery reported a newly obtained key; key material was not logged |
| `SAME_ENDPOINT_RECONNECT` | NOT_TESTED | No same-endpoint reconnect in the successful runs |
| `DISTINCT_ENDPOINT_RECONNECT` | VERIFIED | Run-scoped HMAC of endpoint changed on all three reconnects |
| `CACHE_OVERLAP` | VERIFIED | Exact frame identity and payload hash matched once per fault |
| `ZERO_UNVERIFIED_FRAME_GAPS` | VERIFIED | Zero missing/unrecoverable frames; accepted frame log consecutive |
| `ZERO_DUPLICATE_OUTPUT` | VERIFIED | Three cache overlaps excluded; output frame IDs unique |
| `CONTINUOUS_TS_TIMELINE` | VERIFIED | TS continuity and PCR checks clean; DTS monotonic; full decode clean |
| `FULL_VIDEO_DECODE` | VERIFIED | FFmpeg `-xerror`, H.264 decode exit 0 |
| `FULL_AUDIO_DECODE` | VERIFIED | FFmpeg `-xerror`, AAC decode exit 0 |
| `DURATION_LIMIT_EXIT_0` | VERIFIED | Both successful runs ended at configured duration, receiver exit 0 |
| `RECOVERY_TELEMETRY` | VERIFIED | Monotonic per-event logs and per-injection summaries captured |
| `RESOURCE_CLEANUP` | VERIFIED | Normal bounded completion, sockets/diagnostic worker closed by `finally`; no lingering harness process observed |
| `SINGLEFILE_REGRESSION` | VERIFIED | Reproducible generated bundle and full tests passed |

## 7. Final Verdict

**PASS** for the stated controlled-live acceptance condition: three sequential client-side Parent TCP closes were recovered from server Cache in the same live recording session, with exact overlap identity, no unverified frame gaps or duplicate output, normal duration-limit exit, and full A/V decode.

This does not establish natural remote Parent failure behavior, independent network failure domains, long-duration reliability, perceptual A/V sync, or a 30-minute endurance result. No commit or push was performed.
