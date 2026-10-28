# Comprehensive hardening and field validation — 2026-10-26

**Status: field validation recorded; overall goal remains PARTIAL, not a production-ready declaration.** This report
concerns the current uncommitted working tree above
`8366328d0968c16942045f1ccb0c65c98172cbbc`, not a published release. Existing local
changes were preserved. No commits, pushes, tags or releases were made. The Go
Adapter, Go Grid PoC and Integrated Recorder Core were not modified.

This is a chronological field log. Any earlier section calling a prior artifact
“current” describes the worktree at that point in time; the latest artifact and
retest results are in the final dated addenda. The current generated bundle is
`bf42ec535870c72f89ee9fba32bfc58f36212cd7c115c53442c1b3afde0190ad`. The
retained 30-minute capture used the earlier `af82...` bundle; the exact current
bundle has a 12-second Grid-only smoke, not a 30-minute run. The earlier bundle
was recovered from its run directory and its SHA-256 matches the 30-minute run
manifest. A byte diff against the current bundle shows changes only in rejected
packet capture/exception reporting: valid accepted media, timestamp calculation,
and TS muxing code are unchanged. That supports applying the 30-minute result to
the current successful-media path, but is not a new 30-minute execution of the
exact current artifact; the distinction remains explicit.

**Latest superseding verification (2026-10-28):** the exact current bundle is
`400f028ed99467ad644d61525f8a59f85477221dbba28708710ff06d77682cd0`. It passed
576 modular tests, 568 bundled regressions, Ruff, format, compileall, deterministic
bundle check, and a 30-minute Grid-only capture. That exact run exited 0 at the
duration limit after 1,802.831 seconds; the complete 1.8-GB TS passed the
independent full-file verifier, including complete H.264/AAC decode, 10,086,527
TS packets, zero sync/continuity/PCR/DTS/PSI errors, and 1,800 seconds of decoded
audio. The run had no B slices, so it does not prove B-frame behavior for this
exact capture. A separate 30-minute run of bundle
`03b31e3712160cc5bdcc398e63aeb602c510994d144d56c079b8f8998f9d8ff1` contained
HRD/SEI and 80,179 B-slices and passed independent full-file checks; it remains
separate-artifact B-frame evidence. The intervening change to `400f…` adds
StreamIO output-drain/accounting diagnostics only, but the distinction between
those two live runs is retained. The 1-ms Grid/HRD equality gate was an
unsupported receiver assumption and has been removed. See
[the 2026-10-28 timeline-model evidence](TIMELINE_MODEL_FIELD_VALIDATION_20261028.md)
and [the exact-current 30-minute record](LIVE_VALIDATION_30M_20261028.md) for
clock-model limits, AAC timing and validation detail. Overall status
remains **PARTIAL** because Grid clock semantics, one distinct HRD/POC conflict,
perceptual A/V synchronization, and exact-artifact live B-frame behavior remain
unresolved.

The active user goal requires real Native Grid recording and independent checks,
not merely an audit checklist. Findings below distinguish demonstrated code defects,
fixed regressions, restricted support, and unresolved transport semantics.

## Investigation and evidence chain

The normal path is the actual standalone `soop_grid.py` loaded by Streamlink 8.6.2:
HTTP exact-target/access validation → authenticated Gateway → Center JOIN/discovery
→ one Parent → validated Grid media → H.264/AAC → shared TS muxer → bounded
StreamIO → Streamlink file output. HLS was explicitly disabled for Native Grid
field tests. A failed media gate was never converted to an HLS success.

The current investigation preserves prior captures privately, retains only safe
aggregate results in reports, and adds synthetic/loopback regressions for failures.
The persistent user credential file was sourced only for authenticated tests and
was neither modified nor deleted. Anonymous public tests removed credentials from
the child environment. Account/stream passwords remain separate.

## Demonstrated defects and corrections

| Severity | Area / source | Demonstrated issue | Correction / evidence |
| --- | --- | --- | --- |
| MAJOR | `h264clock.py` | POC distance treated as elapsed presentation time; legal scaled POC changed timestamps while decoded pictures stayed identical | Removed POC-to-duration mapping; independently decoded scaled-POC counterexample and POC 0/1/2 regressions |
| MAJOR | `h264clock.py` | Fixed VUI FPS mistaken for sufficient timing of arbitrary reordered pictures | Parse HRD and Picture Timing SEI; validate DPB output delay and POC/PTS ordering; uncertain combinations fail closed |
| MAJOR | `h264clock.py`, `media.py` | Joining a valid stream between IDRs rejected missing startup parameters or lost early audio | Fixed byte/frame/time startup bounds, active SPS/PPS/IDR initialization and exact retained parameter prefix; bounded early-audio queue |
| MAJOR | `h264clock.py` | MMCO 5/IDR epoch and reference-state corner cases could produce wrong order or repeated presentation times | Correct previous-reference POC state; output epoch floor; reference `frame_num` and POC-2 non-reference validation |
| MAJOR | `h264clock.py` | Partial parameter comparison missed PPS changes; multiple-picture and active-ID ambiguities | Full bounded raw SPS/PPS definitions, active-ID prefix check and one-picture validation; independent counterexamples |
| MINOR | `h264clock.py` | Legal Annex-B trailing zeros, terminal CABAC prevention and SEI alignment rejected | Bounded RBSP/SEI parsing and exact regression vectors; malformed variants still rejected |
| MAJOR | `h264clock.py` | Access-unit parser applied SEI without enforcing NAL order; timing metadata after VCL/non-leading buffering-period could be accepted. Rejected access units could also partially commit in-band parameter sets | Preflight AU/SEI ordering and frame syntax against bounded candidate SPS/PPS state; commit only after accepted syntax; reject invalid or parameter-only post-start AUs without mutating active codec state; regressions cover all cases |
| MAJOR | `h264clock.py` | Grid DTS/POC/HRD validation happened after parameter sets and picture-order state had begun mutating; a caller reusing the clock after rejection could inherit state from the rejected AU | Wrap `inspect()` transactionally; restore parameter maps, epoch/POC fields, timing state and bounded picture history on any exception; late-DTS rejection followed by valid retry is tested |
| MAJOR | `media.py`, `mpegts.py` | Arbitrary reorder lead shifted audio relative to independently timestamped reference | One common affine origin; audio has no invented reorder delay; FFmpeg reference comparisons |
| MAJOR | `aac.py`, `media.py` | Grid audio message count confused with AAC frame count; late ADTS corruption/config changes not validated | Validate every ADTS boundary and configuration; separate messages, ADTS frames, AUs, core samples and emitted samples |
| MAJOR | `mpegts.py` | Large valid audio chunks exceeded PES length; naive grouped timing could accumulate rounding error | Split only at ADTS boundaries into bounded PES; absolute rational sample timestamps; 44.1-kHz full PCM/payload comparison |
| MAJOR | `media.py` | AAC AU start regressions and configuration changes were not consistently rejected; chunk timestamps also show bounded batch-clock residuals | Validate strictly increasing AU starts and stable AAC-LC/single-raw-block configuration; expose per-chunk clock residual/overlap metrics; retain measured sub-AU batch residuals instead of treating them as duplicate AUs |
| MINOR | `media.py`, `mpegts.py` | Failed output could increment emitted counts/hashes or mutate mux state prematurely | Validate before serialization; commit emitted counters/hashes only after successful output |
| MAJOR | `auth.py`, `bootstrap.py` | Validation/factory failure retained secret-bearing objects; ambient HTTP settings could affect credential routing | Clear ephemeral credentials on failure; bounded exact-target responses and isolated HTTP settings; synthetic secret canaries |
| MAJOR | `gateway.py`, `center.py` | Identity truncation, delimiter injection, loose response typing and JOIN/discovery ordering | Explicit bounded fields and strict types; reject injection/truncation; validate state ordering with regressions |
| MAJOR | `protocol.py` | Socket creation/setup/close failures could leak or mask other cleanup | Sanitized errors and per-resource cleanup; cancellation through connect/write/rediscovery; loopback regressions |
| MAJOR | `parent.py` | Duplicate replay counted as forward media progress and could conceal stall | Only committed new media resets stall progress; exact-overlap and finite retry budgets retained |
| MAJOR | `stream.py`, plugin | Invalid `readinto` buffers consumed data; HLS errors/failed readers could violate error/cleanup contract | Validate writable byte view before reading; sanitized official-delegation errors and cleanup; actual resolver/CLI regressions |
| MINOR | plugin / HLS | Delegation could select wrong quality or truncate an unsupported channel match | Preserve official available quality selection; reject unsupported exact-target delegation rather than select another channel |
| MINOR | `parent.py` | Deadline expiry and stop-flag cancellation during Center rediscovery mislabeled as Center failure | Cancellation takes precedence; match Parent-read deadline policy: duration-limit before progress, explicit unrecovered-gap failure during recovery; six regression cases and independent real-read probes |
| MINOR | verifier platform scope | Selectable subprocess pipes and procfs checks incorrectly implied Windows/macOS test portability | Explicit POSIX tool guard, Linux-only procfs observation, portable no-spawn rejection regression; plugin runtime/media CI tests remain enabled |
| MAJOR | verifier tool | Decode/probe success alone, missing timestamps, PSI corruption, truncated PES or unbounded subprocess output could mislead validation | Streaming TS/PES/CRC analysis, finite output/time limits, process-group cleanup, nonzero failure status, independent PCM/payload counts and changing-input rejection |

Tests are in `test_h264_audit.py`, `test_h264clock.py`, `test_audio_audit.py`,
`test_auth.py`, `test_gateway.py`, `test_center.py`, `test_protocol.py`,
`test_recovery.py`, `test_streamio.py`, `test_transport.py`, `test_singlefile.py`,
`test_diagnostics.py` and `test_recording_verifier.py`. Some vectors intentionally
exercise bounded header parsing; they are not represented as decodable full video.

## H.264 timing support and unresolved adult-stream case

POC determines picture order, not time. In the supported model the wrapper's
observed 100-ns clock supplies DTS, and per-picture HRD/SEI DPB output delay supplies
PTS offset using the declared VUI clock tick. Without HRD, only constrained POC-2
or zero-reorder input may use PTS=DTS. Fixed VUI FPS alone does not establish
reordered presentation times. No arbitrary FPS, frame synthesis or relaxed DTS
validation is used. See [H264_TIMING_DIAGNOSIS.md](H264_TIMING_DIAGNOSIS.md).

Authentication and media reception succeeded for an adult-rated public stream,
but one sampled sequence remains **unsupported**: IDR POC 0 with DPB delay 18 ticks,
then POC 2 with DPB delay 2 and 15 ms Grid advance, at a declared 1/120-second tick.
That mapping yields PTS opposite to POC order. FFmpeg `trace_headers` independently
confirmed the fields. The current gate correctly rejects the contradiction with
`h264_picture_order_conflict`; a decoded partial prefix does not resolve it.
The transport clock/HRD relationship needs further evidence. Adult authentication
success is therefore not claimed as complete adult-stream recording support.

Two final-artifact, authenticated, 15-second-bounded retests on the same previously
selected public adult target reproduced the fail-closed result in about 2 receiver
seconds. One stopped after 117 video messages and 94 AAC AUs; a private raw-trace
run stopped on its second video access unit, before outputting audio. Both exited 1
with `h264_picture_order_conflict`; all Parent sockets closed. In the private trace,
the IDR (POC 0) had CPB/DPB delays 240/18 and a buffering-period SEI; the next P
picture (POC 2) had delays 2/2, no new buffering-period SEI, and a 15-ms Grid clock
advance. FFmpeg 9.0 `trace_headers` independently confirmed the CPB delay width is
16 bits, DPB delay width is 6 bits, and the observed fields; the buffering-period
initial delay is 90000 with a 24-bit width and zero offset. With the current Grid
DTS interpretation, DPB offsets place POC 2 about 118.3 ms before POC 0, so the
existing POC/PTS guard rejects it. This demonstrates contradictory presentation
timing in this sample, but does not prove whether the encoder HRD or proprietary Grid
clock semantics are at fault. No HLS fallback or timing relaxation was used; neither
partial output counts as a successful recording. Synthetic regressions preserve
the fail-closed condition, including the observed HRD field widths and delay values,
without committing the private payload.

The observed CPB-removal delay was checked against H.264 Annex C.1.2 as another
possible decode clock. Relative to the buffering-period anchor, CPB delay 2 plus
the DPB change 18→2 gives a nominal presentation-time change of −14 ticks (−116.7 ms),
still opposite to increasing POC. FFmpeg 9.0.2 decoded both access units from the
bounded raw capture, confirming those two pictures are decodable; this does not
validate the timing or a complete recording. The current ITU record lists the
06/2026 edition in force; its full PDF was not inspected here.

The historical no-fixed-VUI capture contained no usable timing source in the
inspected VUI/SEI fields. Output order plus a decode-order timestamp does not
uniquely determine every presentation time. Absence of that information is a
support limitation, not permission to invent it.

A bounded REA/Ghidra query for native timestamp-consumer cross-references timed out
after 300 seconds; no fresh decompilation/data-flow proof was obtained. Static
strings alone were not treated as clock-semantic proof. The analysis session was
closed. No proprietary binary or decompilation dump is included in this repository.

## Audio, synchronization and payload preservation

One Grid audio message can contain many ADTS frames. Independent historical checks
found 72 messages containing 1,687 ADTS frames / 1,727,488 core samples per channel,
and 98 messages containing 2,352 / 2,408,448. These are different private samples
from the older 87-message failed run and must not be conflated.

A historical stream has ordinary batch-clock residuals of about +9.333/-12 ms,
with cumulative residual within 0–20 ms. A speculative 2-ms discontinuity threshold
would falsely reject it. The receiver measures residuals and rejects actual
backward/equal AU starts. A negative difference between one Grid message timestamp
and the previous chunk's nominal sample-count end can occur while all individual
AU starts remain strictly increasing; the receiver reports this batch-clock overlap
separately and does not label it duplicated audio without evidence.
ADTS core configuration alone does not establish implicit SBR/PS output format;
actual decoded rate/channel/sample counts are reported independently.

For current files the verifier demuxes TS PES independently and reports SHA-256
of elementary video/audio bytes. A separate verification cross-check compares
these facts with receiver output statistics, including committed AU/core counts
and independently decoded PCM samples.
The first IDR may include exact retained parameter sets, which are accounted for
in the emitted video hash. No encoding or transcoding is performed.

A/V start/end **coverage** offsets are not source-clock drift measurements. Different
coverage lengths, preroll and terminal batching can change these offsets with a
sample-continuous audio clock. The verifier labels this explicitly. Without an
independent content reference, perceptual/lip-sync correctness remains unproven.

## Bounded resources and lifecycle

- StreamIO queue has a finite byte bound; backpressure/cancellation are tested.
- Network frame/control limits and per-operation timeouts remain finite.
- Frame identity history has at most 128 entries; reorder history at most 64.
- SPS/PPS definitions, NALs, SEI, AAC messages and startup retention have fixed bounds.
- Only accepted forward media refreshes progress/retry state. One Center rediscovery
  and eight Parent attempts per outage remain bounded; global duration is preserved.
- Exact overlap kind/timestamp/payload SHA-256 is mandatory. Missing overlap,
  frame gap, conflict, invalid configuration or timing terminates with an error.
- Output errors drain existing buffered data then propagate rather than becoming
  clean EOF. Partial files are preserved; filesystem crash durability is not promised.
- Diagnostics use fixed classifications and bounded fields; optional raw capture
  remains private/opt-in and can consume disk and synchronous I/O.
- Diagnostic JSONL is capped at 16 MiB and uses batched flushes; the atomic summary
  records truncation. Header-checksum failures can include only allowlisted numeric
  fields for Gateway/Center framing, never raw header bytes.
- Native Gateway/Center/Parent sockets use SOOP plain TCP framing; the plugin does
  not add TLS and some authentication-bearing values are carried in packet bodies.
  Do not use this transport on untrusted/shared networks; an on-path observer is
  outside the confidentiality protection provided by HTTPS Bootstrap.
- Password environment variables are read without removing them, so a second
  plugin instance in the same process can reuse them. Cookie and bootstrap ticket
  references are cleared from the transient Session after certificate issuance;
  immutable Python objects cannot be guaranteed zeroized.
- Python immutable strings cannot guarantee memory zeroization. Persistent user
  credentials are intentionally outside plugin-owned ephemeral cleanup.

## Independent reviewers actually executed

The main integrator used available subagents under Luna roles; these are actual
runs, not hypothetical assignments. Their concrete counterexamples were reviewed,
reproduced and converted to regressions before acceptance.

| Role | Runtime nickname | Scope and independent result |
| --- | --- | --- |
| A | Ramanujan | Authentication, Bootstrap, Gateway/Center field/state validation; focused tests; later requested cross-review hit usage limits |
| B/F | Einstein | Independent H.264 scaled-POC and nine further boundary counterexamples; final 89 focused tests passed, no remaining demonstrated blocker in that scope |
| C | Newton | ADTS/PCM counts, sample timing, malformed/config/PES cases; final 117 focused tests passed; no confirmed integration blocker |
| D/F | Halley | Streamlink contract and verifier adversarial tests; final media integration review; later auth/protocol review found the rediscovery deadline classification defect |
| E | Bacon | Protocol sockets, cancellation, duplicate-progress stall/retry audit and tests; later requested auth cross-review hit usage limits |

D independently rechecked the final rediscovery classification patch: 14 focused
tests and real-read cancellation probes passed; authentication denial remained
terminal even at the deadline.

A/E unavailable cross-reviews were not counted as completed; D performed the
independent authentication/protocol review instead. Nine socket tests were denied
in D's sandbox; unrestricted main full-suite execution runs them normally. Reviews
are bounded evidence, not proof that no unknown bugs remain.

## Automated validation and field runs

Environment: Linux x86-64, Python 3.12.3, Streamlink 8.6.2, FFmpeg/ffprobe 9.0.2.
The development verifier uses POSIX selectable pipes and is verified on Linux only.
[Python's selector documentation](https://docs.python.org/3.12/library/selectors.html#introduction)
excludes Windows pipes; unsupported platforms return a failed fixed-code verdict
before opening files/spawning tools. Only this verifier's POSIX test module is
conditionally excluded on Windows; the separate platform-guard test and plugin,
media, Streamlink and recovery tests remain in the OS CI matrix. The procfs
process-state test is Linux-only. No Windows/macOS runtime success is inferred.
A separate clean Streamlink virtual environment loads only the copied standalone
plugin, without `soop_grid` package installation or child `PYTHONPATH`.

Final offline regression on the final source/bundle: **494 source tests passed**,
**486 bundled regression tests passed with no skips**, and clean
standalone Streamlink CLI tests: **8 passed**. Ruff check/format, compileall,
deterministic bundle check, plugin
discovery/transport parity, Markdown local-link checks and `git diff --check`
passed. The same run also exercised the FFmpeg-dependent synthetic media fixtures.
Go Grid PoC and Source Adapter module tests passed; neither Go tree was modified.
The current generated artifact SHA-256 is
`149f40f541066d3b07a1ff7e9b840ce9764fbab3ec47fb23eaf35b786bdd339f`.

A 30-minute **synthetic** verifier stress stream decoded 45,000 video frames and
84,376 AAC AUs / 86,401,024 PCM samples; verifier peak RSS was about 18.1 MiB and its
child about 41.4 MiB. This is not a 30-minute live test. Six-hour arithmetic/audio
fixtures likewise do not establish six-hour real operation.

The initial public target went offline between stages; the receiver refused the old
BNO. A new target was selected explicitly and held fixed for the final 10-second,
60-second and first 1,800-second attempt. The short stages completed with exit 0,
full decode, matched payload hashes and matched emitted/decoded counts.

### First sustained attempt: failed recovery, not a 30-minute success

The 1,800-second attempt ended after **1,108.467 process seconds**, exit 1,
`parent_retry_exhausted` / `recovery_candidates_exhausted`. Last accepted media was
at 1,106.819 receiver seconds; EOF was detected at 1,106.899. Center rediscovery took
539.637 ms and returned a new node key. Parent TCP connected in 8.131 ms, but the
new connection closed before a complete handshake response or cache request.
The 28 counted recovery wire bytes are the **outgoing CB2E request**, not evidence
of a received error packet. Complete-frame accounting cannot exclude partially
received bytes; no response reason was captured.

Before termination, output contained 66,435 video pictures and 51,912 AAC AUs /
53,157,888 core samples per channel. Observed missing-frame and identity-conflict
counters were zero, but one **unrecovered range** was recorded. That is not a claim
of zero loss over the requested recording interval. Partial TS is preserved. Its whole-file verifier subsequently passed: 5,434,142 TS
packets, 0 sync/continuity/PSI-CRC/PCR errors, 0 video DTS regressions or PTS-before-DTS
cases, 16,609 observed B-picture PTS reorder steps, all 66,435 video pictures decoded,
and all 53,157,888 PCM samples per channel decoded. Both elementary payload hashes
and committed media counts matched. Audio timestamp span minus PCM duration was
0.0 seconds. This certifies the **partial prefix**, not the failed recovery or the
missing remainder of the requested interval.

An official exact-target metadata check after exit still returned RESULT 1 for the
same BNO. A new complete authenticated transport session (anonymous public Watch,
new Gateway/Center) then completed 10 seconds normally. It now carried POC type 2
instead of type 0, and its observed frame numbers were below the previous accepted
range. This supports a changed stream generation/configuration as a possibility;
it does not establish why the earlier Parent closed, prove certificate expiry, or
prove a specific server restart. No data was joined across the sessions.

Independent review confirmed credentials were not cleared during rediscovery, the
new key reaches CB2E, and cache frame state is sent only after C747. No concrete
client-code defect explains the pre-handshake EOF from available evidence. A
POC-0→POC-2 transition on an existing parser and a reset without overlap correctly
fail closed in offline probes. Increasing retries or inventing refresh/control
semantics was not used to hide this failure.

A separate post-transition 60-second final-artifact recording completed and passed
full-media verification: 3,648 video pictures and 2,924,544 PCM samples/channel;
matched emitted/decoded counts and payload hashes; no TS/timestamp error. The
bounded second sustained attempt below used the artifact recorded in its provenance;
it predates the later buffering-period parse-detail change.

A deterministic malformed-input probe exercised 28,000 bounded byte strings across
SPS, PPS, SEI, slice-header, Grid-media, Parent-list and ADTS parsers (seed 2601026,
maximum 513 input bytes), with no unexpected exception. Header-parser acceptance
is not proof of a decodable or fully conformant stream; this is bounded exploratory
coverage, not exhaustive fuzzing. A separate compressed counter test accepted
5,356,800 synthetic chunks (24 hours at 62 chunks/second) in 12.291 seconds:
identity history remained 128 entries, timestamp-kind state two entries, and sampled
peak RSS stayed 17,536 KiB. It exercises counter/state bounds only, not codec,
network, session lifetime, wall-clock endurance or actual 24-hour recording.

## Artifact provenance

The first 10-second/60-second stages and the failed first 1,800-second attempt used frozen artifact
`a9448bba907ab45ac5047f21e5cf1d6c5604087d525097bc42a742911638331c`.
A later module-docstring correction changes only generated section-checksum comments
(executable bundle AST identical). The subsequently reviewed rediscovery deadline
fix changes only that error/termination branch, not media parsing or muxing. The
post-transition attempt loaded artifact
`df8336dc063d6ec03b1e0d845289fe6260938eed2de0020ba02f0b172f00a973`
directly; its 10-second, 60-second and 30-minute gates used that same file. Since
then, the parser began retaining buffering-period initial-delay values in the
transient parse result so the H.264 regression can assert parsed BP data. The values
are not used in timestamp selection, muxing or diagnostic files. The current rebuilt
artifact `149f40f541066d3b07a1ff7e9b840ce9764fbab3ec47fb23eaf35b786bdd339f` has
passed offline regressions but has not been re-run live. The first attempt is not
described as loading bytes produced afterward.

### Second sustained attempt: 30-minute public recording passed structural/decode gates

A second bounded run used the artifact in its provenance above and the same explicitly selected public BNO as the 10-second and 60-second post-transition stages. Receiver duration was 1,800.556 s; supervisor wall duration 1,801.968 s; normal `duration_limit`, process exit 0, no signal or supervisor timeout. The 1,929,412,604-byte TS contains 10,262,833 packets; PAT and PMT each appeared 1,802 times.

The receiver counted 108,107 1920×1080 H.264 pictures and 84,456 ADTS AAC access units in 3,519 Grid audio messages. Independent full-file verification decoded all video pictures and 86,482,944 PCM samples/channel at 48 kHz stereo. Receiver and independently extracted PES SHA-256 values matched for both elementary streams; receiver, ffprobe, ADTS parser and decoder counts matched. TS sync, continuity, PSI CRC/structure, transport, adaptation, PCR regressions, H.264 DTS regressions, PTS-before-DTS and missing packet timestamp counts were all zero. There were 108,107 PCR samples, maximum PCR gap 17 ms, and no flagged PTS reorder steps in this run; this 30-minute sample therefore adds no live B-picture reorder evidence. Earlier B-picture capture and synthetic vectors remain separate evidence. Audio timeline minus decoded PCM duration was effectively zero.

A/V coverage boundaries were audio-minus-video −28.667 ms at start and −83.334 ms at end (−54.667 ms change; audio coverage ended earlier). These are coverage offsets, **not** a measured source-clock drift or perceptual sync test. An independent 12-second built-in HLS reference produced 562 identical AAC packet hashes in the overlapping Grid sample. The −723.000989 s audio offset compares the **output TS packet PTS** for identical AAC payloads, not raw Grid message timestamps. An initial 96×54/100-frame image correlation was inconclusive. A later 160×90 correlation across 305 motion-selected frames found its best Grid-minus-HLS offset at −722.984322 s, with a far-offset pixel-error ratio of 1.1053. The 16.667 ms difference from audio equals the 1/60-second video search step, so this does not establish a true A/V offset or perceptual sync. It is cross-transport content evidence only.

The 30-minute run had one Parent connection and no reconnection, so it does not re-verify failover. Receiver reported zero gaps, missing frames, duplicates, identity conflicts, sequence discontinuities and unrecoverable ranges. Peak receiver RSS was 44,490,752 bytes (about 42.4 MiB); median sampled RSS was about 40.9 MiB and peak sampled RSS 44,322,816 bytes; process CPU time was 455.64 s; peak 3 threads / 6 open file descriptors; maximum StreamIO buffer 233,684 bytes; sockets were closed at exit. This is one successful supported-stream sample, not repeated or multi-hour endurance.

The exact final-file verifier report is private under `temporary local validation output/media-validation.json`; no media or broadcast identifiers are included in the repository. Raw HLS/Grid media remain private on the authorized NFS. Numeric-only refined video comparison is preserved at `private validation archive/video-reference-comparison-refined-20261027.json`; its hash is recorded in `H264_TIMING_DIAGNOSIS.md`.

## Remaining scope and final decision

Overall goal status: **PARTIAL**. One complete 30-minute supported public capture has passed structural, payload-preservation and decode gates, but the first sustained attempt failed recovery after a pre-handshake Parent EOF, and the authenticated adult-stream HRD/POC contradiction remains fail-closed and unsupported. A/V coverage and limited HLS content correlation do not establish perceptual synchronization. Natural remote-failure repetition, multi-hour endurance, automatic reauthentication, authoritative broadcast-end detection, arbitrary encoder clocks, midstream codec changes, multi-Parent reception, physical failure-domain independence and Windows/macOS runtime are not established. The plugin remains Experimental/Beta; no production-ready or universal lossless claim is justified.

### Latest follow-up: bounded live attempt failed during Center rediscovery

On 2026-10-26, artifact
`149f40f541066d3b07a1ff7e9b840ce9764fbab3ec47fb23eaf35b786bdd339f` was run
against one server-selected, password-free public stream with a 1,800-second
duration cap. It lasted 578.565 receiver seconds and exited 1. A Parent TCP EOF
was detected at 577.849 s; one Center rediscovery attempt failed with
`control_checksum_invalid` after about 713 ms. No replacement Parent connection
or Cache Overlap occurred. That run did not capture safe checksum-header fields,
so this identifies the rejecting code path but does not establish whether the
Center sent a malformed header or the client parsed a valid variant incorrectly.
It is not a successful 30-minute recording.

The retained 373,994,980-byte partial TS independently passed structural parsing
and complete H.264/AAC decoding: 3,168,759 TS packets; zero sync, continuity, PSI,
PCR-regression or DTS-regression errors; 34,662 decoded 1920×1080 H.264 frames;
27,070 AAC access units and 27,719,680 decoded PCM samples per channel at 48 kHz
stereo. Receiver and independently extracted PES payload hashes matched. No
accepted-frame sequence gap occurred before the receiver stopped. A/V coverage
offsets were −170 ms at start and −357.001 ms at end (−187.001 ms boundary change).
These are coverage measurements, not source-clock drift or perceptual sync. Peak
receiver RSS was about 44 MiB; all sockets were closed at failure.

The old artifact's opt-in diagnostic JSONL reached 51,774,516 bytes in under ten
minutes. The current source now caps that event log at 16 MiB, batches flushes,
marks truncation in the final summary, and allowlists numeric Gateway/Center
header facts on checksum failure (never raw headers or packet payload). New tests
cover the byte cap and redaction. One bounded live revalidation with the rebuilt
artifact is needed to capture the safe header tuple; it has not yet run.

### Current targeted corrections

The plugin previously consumed `SOOP_ACCOUNT_PASSWORD` and
`SOOP_STREAM_PASSWORD` with `os.environ.pop()`, so repeated plugin enumeration in
one process could lose those values. It now reads without mutating the environment;
regression tests cover two plugin instances. After Gateway certificate issuance,
the transient Session clears its Cookie and ticket. Bootstrap clears account inputs
and closes its private HTTP Cookie Jar; issued credentials are cleared on reader
shutdown. Python immutable strings/bytes cannot be guaranteed zeroized in memory.

The mux now tracks minimum and maximum unwrapped PTS for the media timeline extent;
the old statistic used only the last decode-order PTS and understated/reported the
wrong span for reordered pictures. This affects summary metadata, not serialized
timestamps.

### Follow-up after the first sustained pass: latest parser/verifier changes

An adversarial H.264 review found that POC type 2 alone must not be treated as
proof of zero reordering. The no-HRD `PTS=DTS` path now requires `max_reorder == 0`
to be established either by the VUI restriction or by the applicable H.264 Annex A
profile/constraint inference. POC2 without such evidence still fails before emitting
an IDR prefix. SPS parsing also rejects reference counts or signaled DPB bounds
that exceed the level-derived DPB capacity. These are parser/timing acceptance
changes, not an invented clock. The POC2/B-frame reordered stream without HRD still
has no independently decodable end-to-end fixture; it remains unsupported.

An independent verifier review found that the previous PCR-gap metric was
mislabeled: an offline TS file does not provide packet arrival times or a transport
bitrate, so this scanner cannot establish the actual PCR wire-repetition interval.
The verifier now reports `max_pcr_value_delta_seconds`, requires at least two PCR
samples, applies a 100-ms local media-clock continuity heuristic, and rejects
non-H.264/non-AAC streams. This heuristic is not PCR repetition-conformance proof.
The verifier counts decoded frames rather than comparing every decoded-picture
checksum; accepted Grid frame identities and TS/PES payload hashes remain separate
evidence.

The latest source and generated artifact
(`ca5380ffbd4d918a3726c7cef4f117f2b3e526096cd4db4d92d3ab6d3ba2a7f7`) passed a
10-second public Grid-only live smoke: 15.016 seconds wall time, normal duration
limit exit 0, 23,667,508 output bytes, 679 decoded 2560×1440 H.264 pictures, 516
AAC frames and 528,384 decoded PCM samples per channel. The current independent
verifier passed: 125,891 TS packets, zero sync/continuity/PCR-regression or PSI CRC
errors, 679 PCR samples, maximum PCR value delta 20 ms, and full H.264/AAC decode.
This confirms the latest parser on the observed HRD/SEI live path; it is not a
current-artifact 30-minute test.

A separate 1,800-second public Grid-only capture on artifact
`2e44c8031b924cb7eea1b03631a10aac6f717cdb69c5dfa801f58ecb2cf6cfc6` completed in
1,801.505 seconds with `duration_limit`, exit 0, no signal or supervisor timeout.
It wrote 3,745,639,996 bytes, counted 108,108 POC2/HRD H.264 frames and 84,469 AAC
access units / 86,496,256 core samples per channel, and reached 50,003,968-byte
peak RSS, three threads, six FDs and a 724,364-byte StreamIO buffer. It used one
Parent connection with no failover, no sequence/gap/conflict counters and sockets
closed at exit. The event JSONL reached its bounded 16-MiB limit and recorded
truncation; diagnostic I/O did not fail. This run used an earlier artifact; later
changes concern cancellation cleanup and no-HRD/DPB validation. The latest bundle
has only the short live smoke. Whole-file verification of this capture is pending
and is not counted as passed. The earlier 30-minute verified run above is separate
evidence.

The latest full offline suite passed **511 tests** with local loopback permission
and FFmpeg/ffprobe configured. Ruff check, Ruff format check, compileall,
deterministic bundle check and `git diff --check` passed. The local generated bundle
SHA-256 is
`ca5380ffbd4d918a3726c7cef4f117f2b3e526096cd4db4d92d3ab6d3ba2a7f7`. No files
were committed or pushed.

### Final verification follow-up — 2026-10-26

An independent adversarial review found a weakness in the verifier's prior PCR
gate: two samples on one PID could pass even if the values were equal, or if an
explicit discontinuity separated them. The gate now requires at least one
strictly positive, non-regressing PCR value delta between consecutive samples on
the same PID; the parser resets PCR history at signaled discontinuity. It reports
`pcr_progression_pair_counts`, not a sample-count proxy. Seven focused PCR tests
pass, including equal-value, split-PID, discontinuity, and positive-pair cases.
This remains an offline PCR-value progression check: a file does not preserve
packet arrival times, so it cannot certify wire PCR repetition intervals.

The complete suite after this correction passed **517 tests** with FFmpeg/ffprobe
configured and local loopback socket permission. The latest standalone bundle
`df3f4f1f1c2907985fd59e97bfbbdeff611ea82ed13957df20b057d0f7074ec1` was tested in
a 13.013-second public Grid-only capture (duration limit, exit 0): 679 video
frames, 516 AAC access units / 528,384 decoded PCM samples per channel. The
current independent verifier passed on that file: 125,207 TS packets, 12 PAT/PMT
instances each, 679 PCR samples, 678 positive same-PID progression pairs, maximum
PCR value delta 19 ms, and zero sync, continuity, PSI CRC/structure, PCR
regression, or full H.264/AAC decode failures. This short smoke does not substitute
for a sustained run on this exact artifact.

The independent whole-file verification of the 30-minute recording on artifact
`2e44c8031b924cb7eea1b03631a10aac6f717cdb69c5dfa801f58ecb2cf6cfc6` also passed:
19,923,617 TS packets, 108,108 decoded video frames, 84,469 AAC access units and
86,496,256 decoded PCM samples per channel; sync, continuity, PSI CRC/structure,
PCR regression, DTS regression and timestamp-order failures were zero. A fresh
scan with the stricter PCR progression parser found 108,107 positive same-PID
PCR progression pairs, zero PCR regressions, zero explicit discontinuities, and
a maximum PCR value delta of 22 ms. PMT signaling and PCR PID matched. This
30-minute artifact predates later no-HRD reorder-proof and SPS DPB validation
changes; only the short smoke above used the current bundle. The environment had
about 3.5 GiB free while the retained 30-minute TS occupied 3.6 GiB, so another
30-minute output was not run; existing captures were preserved.

The 30-minute run's A/V boundary offsets are coverage observations, not proof of
perceptual synchronization. At this point in the log, the authenticated adult
sample remained fail-closed and no eligible replacement candidate had been found.
Later dated addenda record a further bounded authenticated adult retest; that
retest also failed closed on a timing conflict. No credentials were changed or
emitted. Overall status remains **PARTIAL**.

The latest review found two additional correctness gaps and they are fixed in
the current source: AAC output PTS now follows the exact sample-count/sample-rate
clock anchored to the first Grid audio timestamp, while subsequent Grid batch
timestamps are checked as observations and deviations of one AAC access-unit or
more fail closed. This prevents sub-AU batch-clock residuals from creating
overlapping output packet intervals while retaining measured residual metrics.
The independent verifier now checks packet PTS against the previous packet's
duration, not just PTS monotonicity. In the earlier 30-minute artifact, ffprobe
showed 1,577 audio packet intervals with up to 12 ms overlap; this motivates the
change but is not a claim that its samples were duplicated. The current bundle
must pass the short live smoke and synthetic packet-overlap gate after rebuild.

The verifier also now parses PMT `PCR_PID` and requires observed positive PCR
progression on a declared PID. SPS parsing is explicitly limited to the
implemented Baseline/Main/Extended/High profiles (66/77/88/100), and the reserved
PPS `weighted_bipred_idc=3` is rejected. HRD timing acceptance now checks modular
CPB-delay progression against the Grid decode-clock delta within 1 ms, resetting
the comparison anchor at buffering-period AUs; this is a guard on the existing
Grid-derived DTS model, not a new timestamp source. The contradictory adult sample
remains unsupported, and new candidate selection was limited to the original
small sample rather than broader enumeration.

### Current worktree verification — 2026-10-26, supersedes earlier “latest” labels

The current generated plugin artifact is
`4228693e94bd51b11a18074561adb9a779114258e4719df8b23bf4aff49acbe0`. A fresh
offline run passed **532 modular tests** and **524 bundled tests** with the local
loopback permission and FFmpeg/ffprobe configured; neither run skipped tests.
Ruff check, Ruff format check, Python compileall, deterministic bundle generation
and `git diff --check` passed. The generated SHA256SUMS matches the artifact.
An independent privacy review found that opt-in diagnostic summaries exposed
output media SHA-256 fingerprints. Those fields were removed from diagnostic JSON
while internal integrity calculations remain; a regression test covers omission.

One bounded anonymous public Grid-only smoke used the immediately preceding
artifact (`9aee91fc32fcad45b5297084a8c325efa50093da6e1bda4dc8a73d3d42ba58ab`) for a
10-second/200-MB-capped run. It stopped after 3.004 seconds, exit 1, with
`h264_hrd_grid_clock_conflict` (mapped to `media_timestamp_invalid`), no process
signal and no supervisor timeout. The receiver had accepted 12 video pictures;
the 13th was rejected before output. At the buffering-period epoch, 24 signaled
CPB ticks represented 200 ms while the Grid clock advanced 198 ms; cumulative
residual was 2 ms, beyond the bounded 1-ms quantization guard. This confirms the
failure is an explicit timing-integrity rejection, not the earlier generic
`receiver_internal_failure` diagnostic. It does not establish that the source
bitstream violates H.264: the proprietary Grid timestamp clock domain is not
specified. The receiver did not guess a timestamp or fall back to HLS.

The emitted prefix was independently inspected: FFprobe found 12 H.264 pictures
(2560×1440) and an AAC PMT entry but no accepted AAC access units; FFmpeg 9.0.2
decoded the video prefix with `-xerror`. The TS scan found 2,543 packets, 12 PCR
samples / 11 positive pairs on the declared PID, and zero sync, continuity, PSI
CRC/structure or PCR regression errors. This is only a decodable partial video
prefix, not a successful A/V recording.

Two retained private earlier wire captures were analyzed locally with payloads and
identifiers omitted from output. Their 4,415 HRD-timed pictures yielded 4,376
adjacent comparisons across 39 buffering periods; all CPB increments were 2
ticks, Grid deltas were 16–17 ms, and cumulative epoch residual stayed within
0.667 ms. These independent historical samples support the constrained mapping
for those broadcasts. They do not prove universal Grid clock semantics or explain
the current sample’s 198/200-ms divergence.

The source now also prevents cumulative sub-AAC-access-unit timestamp drift from
being silently normalized: cumulative Grid-vs-sample-clock displacement reaching
one AAC AU fails closed. An adversarial verifier finding was fixed so PCR values
on null PID `0x1FFF` cannot satisfy the PMT-signaled PCR gate. Literal GridError
codes are checked against the diagnostic allowlist, and HRD clock conflicts retain
only numeric timing facts. The incomplete smoke was not treated as success. The
subsequently generated `4228693e…` artifact differs in diagnostic fingerprint
serialization only; its H.264 timing path is unchanged.

A later bounded Grid-only smoke used the exact current artifact hash
`4228693e…` against the second explicitly public, non-adult candidate in the same
first-page sample. It ended after 2.004 seconds with exit 1 and the same
`h264_hrd_grid_clock_conflict`: 6 video inputs and 1 audio message were seen; no
HLS fallback or process timeout occurred. The buffering-period epoch compared 10
CPB ticks (833.333 ms) with 820 ms of Grid time, a 13.333-ms difference beyond
the implementation's 1-ms guard. No complete recording was produced. The first
eight listing entries contained no password-free, non-subscription adult candidate;
the listing was not expanded. The credentials file was left untouched.

### Current-source replay of retained field captures

Two private earlier 60-second Grid wire captures were replayed through the current
modular `MediaSink` without exposing payload or broadcaster identifiers. The
current source accepted 3,792 and 3,821 media packets respectively; the second
produced 3,700 video pictures, 2,904 AAC access units and 2,973,696 audio samples
at 48 kHz. A current-source remux of that second capture produced a 65.8-MB TS;
the current independent verifier passed the entire file, including H.264/AAC
`-xerror` decode, audio packet interval checks, TS continuity/PSI and PMT-declared
PCR progression (3,700 PCR samples, no transport/timestamp failures). Its A/V
coverage-boundary change was about 285 ms and remains **not** a source-clock-drift
or perceptual-sync measurement. These are offline replays of private captured
traffic, not current-build live recordings or endurance evidence.

The earlier large TS was also rerun through the current verifier. The verifier
reported 1,577 audio packet-interval overlaps among 84,469 audio packets (maximum
about 12 ms) and its full decode/AAC-validation subprocesses exceeded the
900-second total budget. It decoded a substantial prefix, but the result is
incomplete and fails the current strict gate. This is an older artifact, so it
does not demonstrate an overlap defect in the current source; the current-source
60-second remux above passed the same interval gate. The underlying 30-minute
capture has therefore not been revalidated end to end by the current verifier.

At this point in the log, no full 10-second, one-minute or 30-minute capture of
the then-current artifact had completed; the short runs failed their timing gate.
The later 30-minute recording described in the final dated addendum is still a
different artifact and does not validate the latest bundle. The authenticated
adult sample also remains unresolved on its separate POC/HRD/Grid conflict.
Overall status is **PARTIAL**, and production readiness is not established. No
source credentials were modified, printed, or committed; no commit or push was
performed.

An independent Annex C review found that the previous implementation reset the
comparison anchor at each buffering period before validating the transition AU.
The current source now checks later-period C-8 timing against the previous
explicit period, then establishes the new C-9 anchor. Regressions cover a nonzero
initial buffering-period CPB value, a valid subsequent boundary and C-9 interval,
and conflicting/zero-delta boundaries. The 1 ms bound is an empirical
implementation threshold, not a standard tolerance.

The retained 55,041,285-byte private Grid wire capture was replayed through this
source after the C-8 correction. The replay accepted 3,792 C748 packets (3,673
video pictures and 119 audio messages / 2,856 AAC AUs), produced a 56,387,028-byte
TS, and passed the independent verifier with FFmpeg 9.0.2: full H.264 and AAC
decode, 299,931 TS packets, zero sync/continuity/PSI/PCR regressions, monotonic
DTS, and no audio packet interval overlaps. This is offline replay evidence from
a retained capture, not a current-build live recording or a long-duration test.

An additional independent lifecycle review found that StreamIO backpressure could
delay observation of the global media-duration deadline. The buffer now checks
the capture deadline during blocked writes, keeps deadline-aware output aligned
to complete 188-byte TS packets, and uses a distinct
`duration_limit_output_incomplete` failure if the deadline interrupts an emission
after part of it entered the output queue. A deadline before an emission is
accepted maps to the existing normal duration handling. Regressions exercise both
paths. This does not promise that a partial file after a backpressure failure is
decodable; it prevents such an interruption from being mislabeled as a clean
duration-limit completion. Completed full runs after this change passed **537
modular tests** and **529 bundled regression tests**, with no skips. Ruff check/
format and compileall also passed; deterministic generation and final diff checks
are being repeated after this report update.

### Current artifact live validation after the C-8 correction — 2026-10-26

The generated standalone artifact `109148f4132b0fd444c68a2e013ed3176fd5fa0ce713c4d0f5065d13e25514e1`
first completed a 10-second anonymous Grid-only smoke. The resulting 11.5-MB
1920×1080 H.264 / 48-kHz stereo AAC TS passed the independent verifier and full
`-xerror` A/V decode. This stream used POC type 2 with no HRD or fixed VUI timing;
it did not exercise live B-frame reordering.

The same artifact then completed a **30-minute** anonymous Grid-only recording.
The receiver elapsed time was 1,800.787 s, the supervisor elapsed time was
1,801.913 s, exit code was 0, and the receiver stop reason was `duration_limit`.
The 1.833-GB TS independently passed the full verifier and complete H.264/AAC
decode: 9,751,445 TS packets; 108,103 emitted video pictures; 84,492 AAC access
units / 86,519,808 decoded PCM samples per channel at 48 kHz; zero TS sync,
continuity, PSI, PCR regression, DTS regression, PTS-before-DTS, truncated PES,
or audio packet-interval overlap failures. The run used POC type 2 without HRD
or fixed VUI timing and therefore does not establish live B-frame support.

One natural Parent TCP EOF occurred during this run. The receiver performed one
Center rediscovery, opened a second Parent connection, received two cache media
frames, verified and discarded one exact overlap duplicate, then accepted new
media. Summary counters report one recovered range, zero missing frames, zero
identity conflicts, zero sequence discontinuities and zero unrecoverable ranges.
Measured recovery from failure detection to first new frame was 156.962 ms; Center
rediscovery took 115.561 ms and the new TCP connect took 5.949 ms. The Center
rediscovery path issued a fresh Node Key. Endpoint identity was not retained, so
this run does not prove that the two connections were to physically distinct
Parent machines. It is natural live EOF/recovery evidence, not injected outage
or repeated-failure evidence.

The verifier reports a 0.852333-s change between audio/video packet-coverage
boundary offsets across the recording. Its semantics are explicitly coverage
boundaries only; there is no independent content reference to establish
perceptual synchronization or to assign that change to source-clock drift.
Treat long-run A/V synchronization as unresolved. The retained 30-minute video
is available locally in `temporary local validation output/recording.ts`; no
media or identifying URL was added to the repository.

After this POC-2 recording, the corrected artifact was also tested with the
user-provided credentials against a publicly listed adult-rated broadcast,
Native Grid only, with adult access explicitly required and bounded duration and
wire bytes. Authentication reached actual Grid media, but the recording failed
closed after three video pictures on `h264_hrd_grid_clock_conflict`; no HLS
fallback occurred and no audio access unit was accepted. The rejected interval
had four cumulative CPB ticks (33.333 ms) against 32 ms of Grid time, a 1.333 ms
epoch difference beyond the implementation's empirical 1 ms guard. The partial
TS was structurally clean and its two complete video PES decoded, but the full
A/V verifier correctly rejected it because it contained no accepted audio.
This is a current-artifact authenticated adult-stream timing failure, not a
successful recording and not evidence that all adult streams fail. Earlier
adult-stream `h264_picture_order_conflict` evidence from a separate capture is
also retained above; the two failure modes must not be conflated. Current status
remains **PARTIAL**, not production-ready: fresh-live B-frame timing with the
corrected artifact, adult-stream A/V recording, perceptual A/V sync, independent
Parent identity, multi-hour endurance, reauthentication, and Windows/macOS
runtime remain unverified or unsupported by current evidence.

### Current H.264 clock and MediaSink replay against retained B-frame Grid capture

To test the corrected implementation beyond the POC-2 live stream, the current
`H264Clock` and complete `MediaSink`/TS path were replayed offline against a
retained 380-second actual Grid capture. First, all 23,542 retained Parent wire
packets were parsed again. The 23,542 `0xC748` packet records matched the saved
frame index for Frame ID, kind, raw timestamp and payload length; their video and
audio payloads matched the saved elementary streams byte-for-byte. The original
`0xC748` packet bodies were then fed directly to the current `MediaSink`, without
reconstructing their media envelope. No live request was made and no capture was
added to the repository.

The current `H264Clock` accepted all 22,800 video access units: 16,910 parsed as
B-slice pictures. The stream used POC type 0, fixed VUI timing and HRD SEI; all
access units selected the `hrd_sei` timing model. Decoder timestamps were strictly
increasing, 5,890 pictures had PTS greater than DTS, and the observed PTS sequence
contained 5,700 reorder steps. This is offline replay evidence for a real captured
B-frame bitstream, not a new live run of the current artifact and not evidence
that the separate adult-stream Grid/HRD clock conflict is resolved.

The retained sidecar PTS values came from an earlier timing implementation and
are not an independent oracle: current PTS differed on all 22,800 pictures, while
POC, slice type and DTS matched on all 22,800. FFmpeg 9.0.2 `trace_headers`
independently parsed every picture-timing SEI. Its CPB-removal and DPB-output
delay values matched the current parser on all 22,800 AUs (zero mismatches). The
capture contained 190 buffering-period AUs; the current clock accepted the first
anchor and passed 189 subsequent C-8 boundary checks before using the new C-9
epochs. On the first 200 pictures, including two buffering-period AUs, current
`PTS-DTS` also matched independently parsed `dpb_output_delay` rescaled by the SPS
clock tick on 200/200 pictures; the historical sidecar matched 0/200. This
cross-checks SEI parsing, AU association and the sampled relative composition
offset calculation. It does not independently validate the C-8/C-9 interpretation
for every stream, Grid timestamp field semantics, the absolute A/V origin, the
empirical Grid/HRD clock tolerance, or perceptual synchronization. The old-vs-
current PTS difference is therefore a historical implementation difference, not
by itself a correctness failure or proof of absolute timing.

Replaying those original wire packets through the current `MediaSink` created a
380-second TS. The independent verifier passed full decode and structural
checks: 22,800 decoded H.264 pictures; 17,808 AAC access units and 18,235,392 PCM
samples per channel at 48 kHz; 1,865,099 TS packets; zero sync, continuity, PSI,
PCR regression, DTS regression, PTS-before-DTS, transport, truncated-PES or audio
packet-overlap errors. It measured 5,700 video PTS reorder steps and zero AAC PTS
overlaps. The current MediaSink's elementary payload hashes matched the bytes
extracted independently from the generated TS. This direct-wire replay produced
the same 350,638,612-byte TS and SHA-256 as the separately reconstructed-envelope
replay that was passed to the verifier, so that full verifier result applies
byte-for-byte to this output. PCR progression was monotonic with
a maximum adjacent PCR-value delta of 17 ms; the offline file does not provide
transport arrival times to assess PCR repetition intervals.

Audio/video packet coverage began 270.667 ms apart and ended 366.667 ms apart,
with a 96 ms change in the coverage boundary offset. These are per-track extent
measurements, not paired content timestamps, source-clock drift, or perceptual
lip-sync. Long-run A/V synchronization therefore remains unverified. The earlier
30-minute live capture remains the endurance evidence; this 380-second private
capture replay adds real B-frame/HRD timing and current full-pipeline evidence only.

### Post-audit StreamIO correction — 2026-10-26

Independent lifecycle/backpressure reviews found that a single large `put()`
used one deadline for the entire emission, even when the consumer kept draining
the bounded queue. The buffer now tracks last forward progress on both enqueue
and read, including partial reads below the atomic TS-packet size. A loopback
regression slowly drains six capacities over multiple timeout intervals while
making progress; the stalled-reader case still times out. `close()` now records
`reader_shutdown_timeout` if a worker survives the bounded join. A synthetic
blocked-worker test confirms the diagnostic; real network I/O remains separately
bounded and socket cancellation is exercised in loopback tests. The full current
offline suite passed **546 modular tests** and **538 bundled tests**, with
FFmpeg/ffprobe fixtures and local loopback permission enabled. Ruff check/format,
compileall, bundle check, and `git diff --check` also passed. The rebuilt artifact
SHA-256 is `c5aab8199d77a7189fd381fe5b2c5ded53b7fdeb4eefb21bc5a8c3a64ad7f824`.

The 30-minute file at `temporary local validation output/recording.ts` was
rechecked with the bounded independent verifier: 9,751,445 TS packets, 108,103
video frames, 84,492 AAC AUs / 86,519,808 decoded PCM samples per channel, and
zero structural, continuity, PCR/DTS, packet overlap, or full-decode failures.
Its recording artifact was `109148f…`, not the latest rebuilt `c5aab8…`; this is
prior-build evidence and does not satisfy a fresh 30-minute run of the latest
artifact. The authenticated adult sample still fails closed on an unresolved
Grid/HRD/POC timing contradiction. No new live request was made in this addendum.
Overall status remains **PARTIAL**.

### Current-artifact public Grid smoke — 2026-10-26

The exact final bundle (`c5aab8199d77a7189fd381fe5b2c5ded53b7fdeb4eefb21bc5a8c3a64ad7f824`)
completed a 20-second, Grid-only recording from one explicitly public candidate
with credentials removed from the child process. It exited 0 on the requested
`duration_limit`; the receiver reported 1,229 playable 1080p H.264 pictures and
961 AAC access units, zero sequence gaps/conflicts, and zero active sockets at
shutdown. The 21.36 MB TS passed the independent full verifier: 113,602 TS
packets, zero sync/continuity/PSI/PCR/DTS/PTS-overlap/truncation errors, and full
H.264/AAC decode (984,064 PCM samples per channel at 48 kHz). This sample used
POC type 2 without HRD and does not test B-frame timing, adult-stream timing, or
long-run durability. A/V coverage-boundary offset changed by 18.666 ms; this is
not source-clock drift or perceptual-sync proof. The file is retained at
`temporary local validation output/recording.ts`.

A new 30-minute file on the current artifact was not attempted: the workspace
filesystem had only 965 MB free, below the prior 1.83 GB output size. Existing
captures were preserved rather than filling the filesystem. The prior 30-minute
recording is still fully verifier-passing evidence for artifact `109148f…`, not
the current bundle.

### Final-bundle 30-minute public recording and natural recovery — 2026-10-26

The current generated bundle (`8265228dc4e5f6238a60880d1f1e9e30ff7d94199c02c511760fd22bc8a260f1`)
was installed in a clean Streamlink 8.6.2 environment as `soop_grid.py`. A
single explicitly public, non-password, non-subscription listing candidate was
used; account credentials were removed from the child environment. The 30-minute
Grid-only recording ran for 1,801.318 seconds, ended on `duration_limit`, and
exited 0. The 1,921,903,132-byte TS was saved temporarily at
the VM's volatile shared-memory filesystem and was not added to Git.

One **natural** Parent connection EOF occurred at 1,628.197 seconds; no client
fault was injected. The receiver detected `peer_closed_or_truncated`, rediscovered
Center once, obtained a new Node Key, and established a second Parent connection.
Failure detection was 8.746 ms; Center rediscovery took 122.699 ms; the exact Cache
overlap was verified; one duplicate overlap frame was excluded; the first new frame
was accepted 197.103 ms after recovery began (total recovery 197.106 ms). The
receiver recorded one recovered range, zero unrecoverable ranges, zero missing
frames, zero sequence discontinuities, zero identity conflicts, and zero active
sockets at shutdown. Endpoint identities were not preserved, so whether the
reconnected socket reached a physically distinct Parent remains unknown. This is
one natural live recovery, not a repeated-natural-failure reliability estimate.

The receiver observed 108,091 H.264 pictures and 84,432 AAC access units
(86,458,368 decoded PCM samples per channel at 48 kHz). Timing used POC type 2
with HRD/Picture Timing SEI (`hrd_sei`); ffprobe observed no PTS reorder steps in
this sample, so this run does not verify reordered B-frame output. The bounded
receiver buffer peaked at 350,244 bytes. The process RSS peak was 57,151,488
bytes; the supervisor observed at most three threads and six open descriptors.

The independent verifier passed (`structural_decode_and_timing_only`): FFprobe,
packet scan, full H.264/AAC decode, and AAC extraction all exited 0 with no
diagnostic lines. It parsed 10,222,889 TS packets with zero sync, continuity,
PSI CRC/structure, PCR regression, transport, scrambling, adaptation, duplicate
packet, or truncated-PES errors. PCR progression had 108,091 samples and maximum
adjacent value delta 17 ms. All 108,091 video packets had monotonic DTS, PTS not
before DTS, and zero missing timestamps/durations. The verifier's packet-
interval overlap gate applies to audio packets; it does not calculate video
packet-interval overlap. This recording also had no observed video PTS reorder
steps. All
84,432 AAC packets decoded; the extracted ADTS had zero trailing bytes. The
recording SHA-256 was
`a6be481f1609d841363d60a43b99146bf82837d491c9340696054daf6d29b61a`.

Audio coverage began 139.667 ms before video and ended 440.334 ms before video;
the 300.667 ms change is a **track-coverage boundary** measurement, not source
clock drift or perceptual lip-sync. Long-run perceptual A/V synchronization is
still unverified. The verifier checks whole-file structure, timestamps and decode;
frame-gap evidence comes from the receiver's Grid sequence and recovery counters,
not from FFmpeg decode alone.

This run also exposed an opt-in diagnostics issue: per-frame
`parent_last_media_received` JSONL rows filled the 16 MiB log cap in roughly two
minutes, preventing later event history. That repetitive event is now limited to
one row per ten seconds; the separate two-second heartbeat, fixed recovery events,
final counters and hard byte cap remain. A regression starts the real heartbeat
thread under an event storm. In this run the 2.52 MB diagnostics file was not
truncated, and the natural recovery events were retained. The regenerated bundle
hash above includes this diagnostics change.

The current artifact now has one complete 30-minute saved public Grid recording
and one natural Parent EOF recovery validated end to end. This does not resolve
the separate authenticated adult-stream H.264 Grid/HRD timing conflict, establish
perceptual A/V sync, demonstrate B-frame reordering on this recording, prove
physically distinct Parent failover, or validate multi-hour operation. Overall
goal status remains **PARTIAL**.

The final offline regression run after this diagnostics change completed **548
tests with no skips**. Ruff check and format, Python compileall, deterministic
single-file regeneration/check, clean Streamlink plug-in tests, and `git diff
--check` passed. The exact generated plugin hash is the one stated above. No
commit or push was performed.

### Recording artifact retention recheck — 2026-10-27

The 30-minute recording above was written to volatile `/dev/shm`. A follow-up
check in the current execution environment found that file no longer present.
The run metrics and verifier output recorded above remain as historical evidence,
but the exact `8265228…` media file cannot now be reopened or independently
rechecked. A separate earlier 30-minute recording from artifact `109148f…` is
still available locally; it is not a substitute for preserving the latest
artifact's recording bytes. No broadcast media has been copied into the repository.

The same follow-up first reran the available older file through the current
verifier with a 600-second total budget. Structural and packet timestamp checks
passed, but full decode timed out before completion. A second run with the
verifier's 1,800-second budget passed completely: 9,751,445 TS packets; 108,103
decoded H.264 frames; 84,492 decoded AAC access units and 86,519,808 PCM samples
per channel; zero sync, continuity, PSI, PCR/DTS, timestamp-overlap or decode
errors. Packet and decoded A/V coverage-boundary changes were 0.852333 seconds;
this remains a boundary-coverage measure, not source drift or perceptual sync.
This is a fresh verifier run on the older `109148f…` artifact, not the vanished
`8265228…` recording.

The offline suite with FFmpeg/ffprobe configured passed 534 tests while excluding
14 local loopback-socket cases because this sandbox denies socket creation. A
prior full run with loopback permission recorded 548 passed; that run was not
reproducible under the current sandbox because local socket creation is denied.
Ruff, format, compileall, deterministic bundle check and `git diff --check`
passed again.

### Verifier scope review — 2026-10-27

An independent code review confirmed that `verify_recording.py` is a bounded
structural, timestamp, and full-decode checker, not a source-to-file completeness
proof. Without an explicit duration request, it does not know the intended run
length; a valid truncated prefix can therefore pass its structural checks. The
optional `--minimum-duration` gate now checks both decoded video coverage and
decoded audio sample duration against an operator-specified minimum. It still
does not know the source's expected frame/sample count and cannot independently
detect source-frame/AAC loss or duplication without receiver-side
sequence/counter evidence. Its packet-interval overlap
gate checks audio only, not video, and its PCR check measures file-order value
progression rather than wire-arrival cadence or PCR-to-DTS conformance. The
30-minute report's zero receiver frame gaps and zero counted duplicate TS
packets remain recorded observations, but those are separate facts from the
verifier's decode pass. Preserve this scope when interpreting historical
`passed` results; no claim of perceptual A/V synchronization or source-complete
capture follows from the verifier alone.

The current-source full offline run on 2026-10-27 passed **558 tests** with
FFmpeg/ffprobe configured and local loopback sockets enabled. Ruff check/format,
compileall, deterministic bundle `--check`, SHA256SUMS consistency, and
`git diff --check` also passed. A bounded read-only check of the first eight
official public-listing entries found no eligible public adult-rated test
broadcast, so no adult live test was started and discovery was not broadened.
The authenticated retained-capture replay above remains offline evidence, not a
current adult live recapture. A separate single-candidate public probe was
attempted later; its result is documented below.

The parser hardening added after this 30-minute artifact rebuilt the standalone
bundle first as `ef3a67c0386b513da6818988fa9a8ccc8137f0cfeef86510f399fd3f00994872`.
The 30-minute recording evidence above belongs to the preceding
`8265228…` bundle and must not be presented as a 30-minute run of this newly
rebuilt artifact. The two short public timing probes below used the intermediate
`ef3a67c…` artifact, before PPS QP-range hardening. The final regenerated bundle
and checksum are recorded at the end of this section; its live duration remains
unverified.

### Current-bundle public live timing probe — 2026-10-27

One bounded official-listing candidate was opened once using the clean
Streamlink 8.6.2 environment and the then-current `ef3a67c…` bundle, Grid-only, with a 1,800-second
duration ceiling and 2.5-GB wire ceiling. The receiver stopped after 2.003
seconds with exit code 1, high-level `media_timing_unconfirmed`, and safe code
`h264_hrd_grid_clock_conflict`. It accepted one video output frame and zero AAC
access units; 196,272 bytes of partial TS were emitted. The independent verifier
failed with missing A/V metadata and unverified PCR progression (only one PCR
sample). The partial output is not a completed or decodable recording. No HLS
fallback occurred and no further live retry was made. The requested 30-minute
recording was not achieved on this artifact.

A second distinct candidate from the same eight-entry listing was tried once
with the `ef3a67c…` bundle and a 10-second cap. It stopped at 2.003 seconds with the same HRD/Grid clock
conflict at POC 8: two HRD ticks represented 8.333 ms while Grid elapsed was
8.200 ms (0.133 ms residual, beyond the empirical 1 ms guard). Five video
pictures and zero AAC access units had been accepted. Its partial TS is also
not a completed recording. Because two distinct public candidates reached the
same unsupported timing class, a third attempt was deferred until the final
bundle and a specifically bounded probe could be used.

The subsequent PPS QP-range and PPS-before-SPS scaling-layout hardening, plus
the duration-verifier gate, rebuilt the final standalone artifact as
`69d8e4679cf75494062b87f1cf74b06def25410179afa96f0da1e01563ac1126`. The full
modular suite passed **558 tests**; the same offline regressions through the
static bundle parity harness passed **550 tests**. The clean Streamlink 8.6.2
environment listed both `soop` and `soop_grid`. Ruff, Ruff format, compileall,
deterministic bundle/checksum verification, and `git diff --check` passed. No
live duration test was run on this final hash.

### Final-bundle public timing probe — 2026-10-27

One additional, bounded anonymous Grid-only probe used the final
`69d8e467…` artifact with a 10-second duration ceiling and a 300-MB wire-byte
ceiling. It selected the third eligible candidate within the same fixed
first-page public listing sample; no credentials or HLS fallback were used.
The process stopped after 2.011 seconds with exit code 1 and
`h264_hrd_grid_clock_conflict`. The receiver observed 16 video messages
(14 startup-preroll pictures), emitted one playable video frame, and accepted
zero AAC access units. The output was a 269,780-byte partial TS, not a complete
or A/V-decodable recording. The failure was detected during the first
HRD-timed output transition: the CPB epoch represented 166,667 100-ns units,
while Grid elapsed time was 180,000 units, exceeding the implementation's
10,000-unit (1-ms) empirical tolerance. Peak receiver RSS was 43,859,968 bytes;
the Parent socket was closed on exit. This is a third short observation of the
same incompatibility class, not a 30-minute test and not proof that every
public stream is incompatible.

This final-hash probe confirms that the current bundle is correctly exercising
the fail-closed timing guard on this stream. It does not reveal whether the
proprietary Grid clock is paced by another clock domain, whether the encoder's
HRD signaling is internally inconsistent, or whether a protocol-defined
conversion exists. No tolerance was changed and no synthetic timestamp was
introduced. A complete current-hash 30-minute recording remains unverified.

### Cross-clock gate reassessment — 2026-10-27

The preceding `69d8e467…` probe is historical. Its `h264_hrd_grid_clock_conflict`
was produced by the receiver's own 1-ms Grid↔HRD equality policy, not by a
SOOP or H.264 conformance rule. The policy has now been removed from the
working-tree source and its replacement is independent checks: strictly
increasing Grid-derived DTS, HRD DPB delay bounded by parser/model limits,
POC/PTS ordering and reorder bounds, and AAC Grid-chunk/sample-clock continuity.
This keeps actual bitstream/order errors fail-closed without treating two
unproven clock domains as identical. Grid-as-DTS remains an empirically
supported implementation choice, not a proven official-player semantic.

An offline replay of retained actual Grid media accepted all 2,161 H.264
pictures and 1,687 AAC access units and produced a 36.016-s TS. An independent
FFmpeg 9.0.2 check decoded 2,161 video frames and 1,727,488 PCM samples/channel;
TS sync, continuity, PSI, PCR progression, DTS and packet-overlap checks passed.
The capture had no B pictures. Packet-coverage boundary offset changed by
−26.333 ms; that is not a source-clock-drift or perceptual-sync measurement.
The TS and machine-readable verification are retained outside the Git
repository at `validation-artifacts-grid-clock-20261027/` with owner-only
permissions. The raw Grid source remains in the existing private capture area.

This is offline evidence only. The final bundle was regenerated after the
source change but has **not** yet passed a live short recording, a current-build
30-minute recording, or independent perceptual A/V synchronization. The goal
therefore remains PARTIAL and the plugin remains Experimental/Beta.

### Timeline semantics correction — 2026-10-27

The 1-ms Grid/HRD equality check described in older run entries was imposed by
this receiver, not identified in SOOP protocol evidence or H.264. It has been
removed; those historical rejection codes do not establish media corruption.
The related interval arithmetic is corrected here: 8.333333 ms versus 8.200 ms
is a 0.133333-ms difference (1,333.333 units on a 100-ns clock), not 1.333 ms.
The separate 166.667-ms versus 180-ms and 833.333-ms versus 820-ms observations
are 13.333 ms apart; the 33.333-ms versus 32-ms observation is 1.333 ms apart.
Prior entries with different arithmetic are superseded by this correction.

The current receiver does not map POC/2 to time. A reviewer confirmed that POC
establishes order, not a general frame-duration scale; fixed VUI plus a numeric
POC formula would fail on legal type-0/type-1 patterns. The current candidate
remains Grid-derived DTS plus the per-picture HRD DPB offset, with independent
POC/PTS ordering checks. This model passed offline replay of 2,161 I/P pictures
and 1,687 AAC AUs from a retained 36-s actual capture, and a distinct 380-s
B-frame capture; it still fails the bounded live sample where HRD-derived PTS
orders POC 2 before POC 0. That failure is a contradiction among observed
bitstream timing signals under the candidate model; evidence does not identify
which source is wrong, so no timestamp substitution is justified.

The latest working source has not passed a fresh final-bundle live short test.
The recorded bounded live failure remains `h264_picture_order_conflict`; a
current-build 30-minute recording was therefore not attempted. The 36-s TS and
independent verification JSON in `validation-artifacts-grid-clock-20261027/`
are offline replay outputs, not new live success. A/V coverage offsets remain
distinct from perceptual synchronization.

**Superseding field update — 2026-10-27:** the local bundle was regenerated to
SHA-256 `b3ed4729795636942a1636a03ee2ea4921b7590c18d9fae24a4810347ac4e505`
and completed a 60-second Grid-only recording of a different listed public
adult-rated broadcast. It ended at the configured duration with exit 0. The
independent verifier passed 341,905 TS packets, PAT/PMT, 3,658 PCR samples,
3,658 H.264 pictures, and 2,860 AAC-LC AUs / 2,928,640 decoded samples per
channel; TS/PSI/PCR/DTS/decode errors were zero. The sample was POC type 2 and
contained no B pictures, so it does not resolve the earlier POC/HRD conflict.
Its Grid/HRD fits reset at 31 buffering-periods and are only local (median slope
1.000002 over periods no longer than about two seconds); no long-term clock
mapping or perceptual A/V sync is established. A 30-minute current-bundle run
was blocked before start because persistent storage had about 485 MB free, less
than the projected recording size; `/dev/shm` is volatile and was not used as a
substitute for the required persistent evidence. The newly verified TS, private
raw capture, process summary, timeline comparison and verifier JSON remain in
the local, untracked `validation-artifacts-grid-clock-20261027/live-short-final-b3ed/`
directory. This update supersedes the preceding paragraph's “has not passed a
fresh ... short test” statement only; it does not supersede historical results
or claim a 30-minute, B-frame or perceptual-sync pass.

**Superseding storage and current-artifact update — 2026-10-27:** the user
authorized temporary NFS use. A create/delete write probe on the existing
export succeeded; no VM partition or filesystem was changed. The final current
bundle was regenerated with SHA-256
`af82bfaca211488c3a162f5f1f90d20932fd9828c375dbb37f3c74f0e3eb2cd0` and passed
a 12-second public Grid-only smoke in clean Streamlink 8.6.2 / Python 3.12.3.
It accepted 724 video frames and 552 AAC-LC AUs, then exited 0 at the duration
limit with no HLS fallback. FFmpeg/FFprobe 9.0.2 decoded 12.0667 seconds of
video and 11.776 seconds of audio; source H.264/AAC hashes matched the complete
TS PES payloads, with zero TS sync, continuity, PAT/PMT, PSI CRC, PCR or DTS
errors. This sample was POC type 2 with no HRD or B-slices and does not resolve
the HRD/POC conflict.

The current source was also replayed offline against a retained 36.8-second
Grid-derived B-frame elementary-stream sample. It emitted 2,209 pictures
(1,209 B-slices) and 1,704 AAC AUs; complete FFmpeg decode, source-to-PES hashes,
PTS reorder and TS structural checks passed. The older sample has no retained
Grid wire file, so it does not independently validate packet parsing or a
fresh live B-frame path. These artifacts and verifier JSON are retained outside
Git in the private NFS validation directory.

A current-bundle 30-minute Grid-only public recording is now running with a
4-GB wire cap. At this report update it had not completed; no endurance verdict
is claimed. Its live files are being written to the authorized NFS path
`live-30m-af82-20261027/`. The prior `live-30m-b3ed-20261027/` item remains an
early failure (3.096 seconds, one playable video frame, no audio), not a
30-minute test.

**Superseding completion update — 2026-10-27:** the 30-minute run completed at
1,805.568 seconds with exit code 0 and `duration_limit`. It received 108,044
video frames and 84,408 AAC-LC access units / 86,433,792 samples per channel;
the independent full-file verifier passed all gates. The TS had zero sync,
continuity, PSI/PAT/PMT, PCR or DTS errors, and the private raw H.264/AAC hashes
matched the complete TS PES payload hashes. This broadcast used POC type 2,
fixed VUI, no HRD and no B-slices, so it verifies a 30-minute no-reorder path
only. A/V decoded coverage changed by 29.667 ms at the track boundaries, which
does not establish perceptual synchronization or source-clock drift. Evidence
and hashes are stored in private NFS run directory
`live-30m-af82-20261027/`.
Capture bundle SHA-256 was `af82bfaca211488c3a162f5f1f90d20932fd9828c375dbb37f3c74f0e3eb2cd0`;
the later `bf42ec535870c72f89ee9fba32bfc58f36212cd7c115c53442c1b3afde0190ad`
rebuild only changes the opt-in rejected-packet diagnostics/capture path, not
normal timing or mux behavior. The previous “running” statement is historical.

**Latest offline regression — 2026-10-27:** with FFmpeg/ffprobe 9.0.2 explicitly
configured and loopback tests allowed, the full suite completed **554 passed,
0 skipped**. Ruff check/format, compileall, `git diff --check`, deterministic
bundle regeneration and `dist/SHA256SUMS` comparison passed. The rebuilt bundle
SHA-256 remains
`bf42ec535870c72f89ee9fba32bfc58f36212cd7c115c53442c1b3afde0190ad`.

The exact-bundle 12-second smoke's saved raw capture contains one final Grid
video message at the duration boundary that was not committed to the output.
The run is therefore not claimed to flush every packet observed at shutdown.
The emitted H.264 PES hash matches the 755 output/decoded pictures and the AAC
PES hash matches all 600 accepted AUs. This does not resolve the live HRD/POC
conflict or establish current-live B-frame timing. The separate 30-minute proof
is on the earlier `af82...` bundle without HRD or B-slices; its intervening
change to `bf42...` is confined to opt-in rejected-packet diagnostics and does
not alter the normal timestamp/mux path. The 30-minute verifier JSON and media
remain on the authorized NFS evidence path. A subsequent independent full reread
completed successfully with FFmpeg/ffprobe 9.0.2; details and its exact result hash
are recorded in `H264_TIMING_DIAGNOSIS.md`. The earlier interrupted rescan used a
missing-library ffprobe path and is superseded by that completed pass.

An independent adversarial review found no safe alternative timing transform
in the available evidence. The POC/PTS order rejection is appropriate for the
chosen candidate output model, but it does not certify that accepted Grid DTS
intervals have the correct rate relative to HRD CPB time. AAC AU/sample
continuity likewise does not identify whether a Grid timestamp labels the start
or end of a batch or prove an absolute common A/V epoch. Those clock semantics
remain unresolved; see `H264_TIMING_DIAGNOSIS.md`. The 30-minute pass is limited
to the observed no-HRD/no-B stream. A separate early verifier attempt failed
because its ffprobe binary could not load shared libraries; the later full reread
with working FFmpeg/ffprobe 9.0.2 completed successfully.


### HRD SEI conformance checks — 2026-10-27

A standards audit found that synthetic HRD IDR vectors omitted the mandatory `buffering_period` SEI, and the parser accepted this malformed combination. The implementation now requires it for HRD IDRs and recovery-point access units, parses bounded recovery-point SEI syntax, and rejects buffering-period SEI when the active SPS has no HRD. The focused H.264 test module passes 85 tests with two FFmpeg-dependent skips when fixture variables are absent. With FFmpeg/ffprobe configured, the full modular suite passes 559 tests with no skips. Ruff check/format, compileall, deterministic bundle verification and `git diff --check` pass. The final bundled regression passes 551 tests. Current bundle SHA-256: `cd1d390bd980c58f83a0d19284dac2e2eff7bffb50b433d926260e3dbdd076d5`.

This is an input-conformance correction only. It does not solve the retained live HRD/POC ordering contradiction. The 30-minute capture and its successful independent rescan use an earlier bundle and a POC-2 stream without HRD or B-slices. They do not validate this HRD path, the exact current bundle for 30 minutes, or perceptual A/V synchronization.

### Exact final artifact: 30-minute run and full-file verification — 2026-10-27

The preceding statement is superseded for the exact-bundle endurance gate. The
final `cd1d390b…` single-file artifact was run directly in a clean Streamlink
8.6.2 / Python 3.12.3 environment in Grid-only mode. It completed 1,806.318 s
with `duration_limit`, exit code 0, and no HLS fallback. The received stream was
POC type 2, fixed VUI, without HRD or B-slices. Receiver counters reported
108,098 output video pictures, 84,456 AAC-LC access units / 86,482,944
samples per channel, zero missing frames, identity conflicts, or duplicate
outputs, one Parent connection, and no failover. Peak RSS was 48,697,344
bytes, peak media buffer 1,768,640 bytes, three threads, and 13 open FDs.

The independent FFmpeg/ffprobe 9.0.2 full-file verifier passed. It decoded
108,098 H.264 pictures over 1,801.633667 s and all AAC samples over 1,801.728
s. The 10,062,565-packet TS had zero sync/continuity/transport/adaptation/
scrambling/duplicate-packet/PSI-CRC/PSI-structure errors and zero PCR
regressions. ffprobe found no missing packet timestamps or durations,
nonmonotonic DTS, PTS-before-DTS, or audio packet overlap. The decoded A/V
coverage-boundary offset changed by 94.333 ms; the verifier explicitly does
not interpret this as source clock drift or perceptual synchronization.

The raw AAC stream hash equals the complete TS AAC PES payload hash. The TS
video PES is an exact byte-for-byte prefix of the raw Grid-derived H.264
capture; 6,578 trailing bytes (one NAL type 1 P-slice) were captured at the
duration boundary but not written to the limited output. No bytes within the
written video prefix differ. TS SHA-256:
`c60244efe265c38d847344ed6b3eb3ababa3158b859f0f61003b4df2f75859df`.
Recording, raw capture, reports, telemetry, and a redacted manifest are
preserved with owner-restricted permissions in private NFS run directory
`live-30m-final-cd1d-20261027` (machine-specific mount path omitted). The exact
final artifact also passed a 15-second live Grid-only smoke and full decode/TS
verification; its evidence is in private NFS directory
`live-final-cd1d-smoke-20261027`.

This completes the 30-minute gate only for the observed no-HRD/no-B-frame
path. It does not validate B-frame/HRD timing, parent failover, natural remote
failure recovery, or perceptual A/V sync. In particular the retained actual
HRD sequence still makes the provisional Grid-as-DTS plus HRD-DPB-offset PTS
candidate contradict POC order; the stream remains fail-closed. The original
1-ms cross-clock equality gate was invalid and removed, but removing that gate
does not resolve the separate mapping ambiguity. See
`H264_TIMING_DIAGNOSIS.md` for the frame-level counterexample and candidate
clock-model evidence.

### Current exact worktree artifact follow-up — 2026-10-27

The `cd1d390b…` run above belongs to a previous bundle. A new run used the
current worktree bundle `e35dc98aced1149bb63325863d1ee92dd353fe3777fc6fb897d626082d03c702`
and completed 1,811.624 seconds in explicit Grid-only mode, with `duration_limit`
and process exit 0. Its full-file verifier also passed. This new stream had no
HRD/SEI and no B pictures, so it adds a second no-reorder endurance sample but
does not resolve the previously observed HRD/POC conflict. Its 111,532 Grid
media-message frame numbers were contiguous; the TS contained 10,058,300
packets with zero sync/continuity/PSI/PCR/DTS errors. All 108,016 H.264 frames
and 84,384 AAC access units decoded; 86,409,216 PCM samples/channel covered
1,800.192 seconds. Raw elementary H.264/AAC SHA-256 values exactly matched the
extracted TS PES payload hashes. Receiver peak RSS was 48,478,160 bytes, peak
buffer 1,012,240 bytes, three threads and 13 open FDs.

This run had one Parent connection and no recovery. Video contained 901 I and
107,115 P pictures, no B pictures. Audio coverage began 22 ms before video and
the track-boundary offset changed by −74.7 ms; this remains a coverage-boundary
observation, not perceptual sync or source-clock drift. Detailed per-file hashes
and the independent verifier result are in
`LIVE_VALIDATION_30M_20261027.md`; raw media and its manifest remain private on
NFS. The run validates neither live B-frame timing nor the adult-stream
HRD/POC conflict. Overall goal remains **PARTIAL**.


### Current source and bundle follow-up — 2026-10-28

After adversarial review, the speculative `grid_no_reorder_observed` branch was
removed. A finite I/P prefix cannot certify that later frames will not reorder;
HRD/POC conflicts now remain fail-closed. Regression tests assert the expected
error for the conflicting initial pair under SPS reorder settings `None`, `0`,
and `1`, and confirm that cleanup may emit only the already-validated IDR as a
partial output while the original exception remains primary.

The regenerated standalone bundle is `dist/soop_grid.py`, SHA-256
`8650ef85363d5c24b2f61cdfc874ac98fa36c1c16b32debc4db1c0ca4bd4c019`; its
checksum manifest matches. Final offline tests: 564 module tests passed and 556
bundle regressions passed, with FFmpeg 9.0.2 enabled and no skips. Ruff check,
Ruff format check, compileall and deterministic bundle verification passed.

A clean Streamlink 8.6.2 run loaded and selected `soop_grid`. One bounded
20-second public Grid-only attempt ended before Parent connection with
`channel_live_number_unavailable`; it produced no media file and did not use
HLS. The sanitized log and run summary are preserved under a unique private NFS
run directory; the log scan found no credential values. This does not count as
live Grid validation. The successful 30-minute capture remains the earlier
`e35dc98…` no-HRD/no-B-frame artifact. No exact-current-bundle 30-minute run or
live B-frame run was possible in this attempt. The overall goal remains
**PARTIAL**.

#### NFS-backed preservation and second bounded Grid probe — 2026-10-28

Temporary NFS use was authorized after the guest workspace showed low free
space. The existing 30-minute `e35dc98…` run and its manifest were retained;
`sha256sum -c SHA256SUMS` verified every listed artifact, including the 1.89 GB
TS. The NFS export reports 1.8 TiB available. No local partition, filesystem,
mount point, or prior test data was changed or removed.

A second short probe used the exact current `8650ef…` plugin copy, the clean
Streamlink 8.6.2 environment, and explicit Grid-only transport against the
public SOOPTV channel. It exited 1 with `channel_live_number_unavailable`
before Parent TCP connection; the TS output is zero bytes and HLS was not used.
The sanitized run summary and log are retained under
`private validation archive/live-updated-8650ef-official-smoke-20261028`.
Credential-value scanning found no match in the log or changed repository files.
NFS space removes the storage blocker, but no active broadcast number was
available through either attempted public channel at test time. Consequently,
the current bundle's 30-minute live recording remains unverified; prior-bundle
30-minute evidence is not substituted for it.
