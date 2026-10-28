# H.264 timing: evidence and supported behavior

**Current status update — 2026-10-28:** the earlier `h264_hrd_grid_clock_conflict`
was caused by a receiver-imposed 1 ms equality test between Grid time and HRD
CPB elapsed time. That equality is neither an H.264 requirement nor a verified
SOOP-player requirement; it was removed rather than relaxed. The independent
`h264_picture_order_conflict` from a particular HRD/POC sample remains
fail-closed and unresolved. Separately, a later real 30-minute Grid-only run
contained HRD/SEI and 80,179 B slices and passed full TS, H.264/AAC, DTS/PTS/PCR
and payload-preservation checks using the provisional Grid-DTS plus HRD-DPB
relative-PTS model. The exact latest bundle also completed a 30-minute Grid-only
recording and full structural/decode verification, but that specific recording
contained no B slices. The earlier B-slice run used a distinct bundle hash.
Neither result proves the official client's clock semantics or perceptual A/V
synchronization. Full metrics and the exact distinction between the 30-minute
artifacts are in
[TIMELINE_MODEL_FIELD_VALIDATION_20261028.md](TIMELINE_MODEL_FIELD_VALIDATION_20261028.md).

Working-tree investigation, updated 2026-10-28. This supersedes the earlier
POC-distance timing interpretation; historical measurements below remain separate
from current-artifact validation. No account, target, endpoint or payload is published.

**Current-source update (2026-10-28):** the speculative
`grid_no_reorder_observed` escape after an HRD/POC conflict has been removed.
The 1-ms Grid↔HRD equality gate is also absent. HRD/POC presentation-order
contradictions still fail closed. The retained 30-minute recording used an
older bundle and a no-B/no-HRD stream; it is not evidence for the currently
unsupported HRD-conflict stream or for live B-frame behavior on the updated
bundle. The current deterministic bundle is `8650ef85363d5c24b2f61cdfc874ac98fa36c1c16b32debc4db1c0ca4bd4c019`. The final offline run passed 564 module tests and 556 bundle regressions with FFmpeg 9.0.2; Ruff, format, compileall and deterministic regeneration passed. A clean Streamlink 8.6.2 run selected `soop_grid`. A single 20-second Grid-only live probe stopped before Parent connection with `channel_live_number_unavailable`, so there is no new live media result for this bundle. The retained 30-minute no-HRD/no-B-frame capture belongs to the earlier `e35dc98…` bundle.

## Exact earlier rejection paths

The original SPS parser rejected `pic_order_cnt_type != 0` with
`h264_poc_mode_unsupported`; absent/non-fixed VUI caused
`h264_timing_unconfirmed` before inspecting subsequent access units. The next
revision parsed POC 0/1/2, but incorrectly treated POC distance as elapsed time.
An independently decoded stream with scaled legal POC values preserved identical
pictures while changing that algorithm's timestamps. Fixed VUI timing alone does
not make arbitrary POC increments a frame-period counter.

The historical 43.45-second failure reported 2,641 video messages and 87 audio
messages with `h264_reorder_timing_unconfirmed`. That error also covered order
regressions, and the offending access unit was not retained. The previous statement
that this proved a B picture arrived at that instant was too strong. The partial TS
decoded, but that proves neither a complete recording nor perceptual A/V sync.

## Current model

POC 0/1/2 is parsed to validate picture order, reference state, wraparound,
IDR boundaries and MMCO 5. It is never converted directly to milliseconds.

* The current implementation uses the observed Grid timestamp as a provisional
  decode-time candidate and `dpb_output_delay * num_units_in_tick / time_scale`
  as the relative presentation offset. It no longer requires Grid elapsed time
  to equal HRD CPB elapsed time: no protocol mapping or tolerance is established.
  `cpb_removal_delay` and buffering-period syntax are parsed, but the current
  clock does not validate a full CPB-removal-delay sequence/epoch progression;
  CPB time is not substituted for Grid DTS or treated as an AAC epoch. The VUI clock tick remains
  usable when `fixed_frame_rate_flag` is false; fixed FPS is not invented.
* Without HRD, only the constrained POC-2 or explicitly zero-reorder case is
  accepted with PTS=DTS, subject to reference and order validation. A potentially
  reordered stream without sufficient timing fails at initialization, rather than
  emitting an apparently valid prefix and waiting for its first B picture.
* HRD presence requires a valid Picture Timing SEI on each accepted access unit.
  Missing/contradictory timing, POC/PTS order conflicts, unsupported structures
  and parameter changes fail closed.
  Streams with `low_delay_hrd_flag=1` are now rejected with
  `h264_hrd_low_delay_timing_unsupported`: H.264 Annex C.1.2 equation C-11 can
  shift actual removal beyond nominal CPB time based on AU arrival time and AU
  size/bitrate, while the receiver has no trusted Grid-side HRD arrival schedule.
  This avoids silently applying the nominal clock model to an unmodeled case.
  The parser does not treat `fixed_frame_rate_flag=1` with
  `low_delay_hrd_flag=1` as malformed. That combination remains unsupported by
  the current timestamp model and is rejected as
  `h264_hrd_low_delay_timing_unsupported`.
* Audio access units use the first observed Grid audio timestamp plus an exact
  sample-count clock. Chunk starts are checked against that sample timeline with
  a bounded sub-AU allowance. The same affine origin is applied to video and
  audio. This is empirically supported by retained captures but the Grid epoch's
  official meaning and universal audio/video epoch identity are unverified.
  The previous arbitrary video reorder lead is not added to audio; early audio
  is retained within fixed limits.
* Internal timestamps are unwrapped Python integers; PES/PCR fields serialize in
  their specified 33-bit domains. Absolute rational rescaling avoids accumulated
  44.1-kHz rounding drift.

The Grid timestamp unit and decode-clock mapping are an **empirically constrained
implementation model**, not protocol-proven semantics or proof that every SOOP
encoder uses that clock. The historical 1-ms cross-clock guard is removed in the
2026-10-27 working tree; the prior failures under that guard remain recorded as
historical observations, not current behavior. The final
artifact at that earlier revision was tested twice against one authenticated adult-rated public stream; both
runs rejected the sequence. A private bounded packet capture from the second run
showed an IDR (POC 0, CPB/DPB delays 240/18) carrying a buffering-period SEI, then
the next P picture (POC 2, delays 2/2) without a new buffering-period SEI. The raw
Grid clock advanced 15 ms; the VUI clock tick was 1/120 s. FFmpeg 9.0 `trace_headers`
independently confirmed the 16-bit CPB-removal-delay field, 6-bit DPB-output-delay
field and per-picture values. The buffering-period initial delay is 90000 in a
24-bit field and its offset is zero. Using the observed Grid timestamp as DTS and the
signaled DPB delay yields the second, higher-POC picture with a PTS about 118.3 ms
earlier than the IDR, exactly the inversion rejected by the POC/PTS guard. Synthetic
regressions `test_hrd_output_delay_that_reverses_poc_order_fails_closed` and
`test_adult_capture_hrd_field_widths_and_delays_fail_closed` exercise the ordering
conflict and observed HRD field widths, initial delay, offset and Picture Timing
values without storing the private payload.
The receiver exits
with `h264_picture_order_conflict`; no HLS fallback or timing relaxation is used.
The evidence establishes contradictory POC/HRD presentation timing for this sample,
but cannot determine whether the encoder's HRD signaling or the proprietary Grid
clock's decode-time interpretation is wrong. No safe timestamp remapping has been
proven, so this stream remains unsupported.

The parsed CPB-removal delay was also checked as an independent timing candidate.
H.264 Annex C.1.2 defines subsequent access-unit removal relative to the current
buffering-period anchor. With the observed next-AU CPB delay 2 and DPB delays 18→2,
that model places the next nominal output `2 + 2 - 18 = -14` ticks (about −116.7 ms) before
the anchor picture. It therefore does not repair the POC/output-time contradiction;
it remains a reason to reject this sample, not an alternate clock adopted by the
receiver. FFmpeg 9.0.2 independently decoded both pictures from the bounded raw
H.264 capture, but a two-picture decode verifies neither a complete recording nor
the correctness of the signaled timing.

## Why more pictures cannot always establish timing

A finite prefix can establish parameter identity and observed picture order, but
cannot turn POC increments into a normative duration. Without an applicable time
base or per-picture timing, several increasing presentation schedules can satisfy
the same picture-order inequalities and decode-order timestamps. Observing more
pictures does not make one schedule uniquely correct. The independent legal
scaled-POC fixture demonstrates why inferring duration from numeric POC distance
is unsafe even with fixed VUI FPS.

Bounded waiting is useful when active parameter sets or an IDR have not yet arrived;
it is not a substitute for absent timing semantics. Picture Timing SEI clock-
timestamp fields are consumed with bounded syntax parsing but are not implemented
as an alternative time-code synchronization source. No such alternative clock was
established for the historical no-timing sample.

## Bounded initialization and parsing

A connection may start between IDRs. Parameter sets are retained; undecodable
pre-IDR pictures are skipped for at most 600 pictures, 32 MiB of consumed video,
and 10 seconds of observed media time. Early audio retention is at most 64 messages
and 1 MiB. A valid IDR arriving after the bound is also rejected. Missing active
SPS/PPS after initialization is an error. When needed, the exact retained SPS/PPS
are prefixed to the first IDR without re-encoding.

Parameter sets are bounded to 4 KiB each; NAL counts, SEI messages, Golomb values,
reference lists and reorder history have finite bounds. Interlace, FMO, unsupported
slice layouts, multiple pictures in one Grid chunk, and codec changes are rejected.
Raw SPS/PPS definitions, including fields outside timing calculations, participate
in change detection. Legal Annex-B trailing zeros and terminal CABAC emulation
prevention are handled. No unbounded speculative video queue is introduced.

## Independent checks

Regression tests include independently decoded scaled-POC bitstreams, OpenH264
POC-2 video, x264 HRD B-frame streams, all three POC parsers, wrap/MMCO/IDR cases,
SEI alignment, active parameter IDs, reordering bounds and payload preservation.
Access-unit ordering is checked before parameter-set state is changed: SEI after
VCL is rejected, and a buffering-period message is accepted only as the first
payload in the first SEI NAL of the access unit. In-band SPS/PPS are staged and
committed only after slice/SEI validation; rejected AUs leave active codec state
unchanged. Parameter-only units are accepted only during bounded startup preroll.
AAC checks distinguish Grid messages, ADTS frames, access units and core samples;
large valid chunks are split only at ADTS boundaries into bounded PES packets.

Current artifact/live results and unresolved issues are recorded in
[COMPREHENSIVE_HARDENING.md](COMPREHENSIVE_HARDENING.md). Full decode, transport
continuity, and timestamp consistency are separate gates; none proves perceptual
A/V synchronization without an independent content reference.

## Standards reference

The publicly posted [ITU-T H.264 (08/2024), V15](https://www.itu.int/rec/T-REC-H.264-202408-S)
text was checked for VUI flag constraints and Annex C.1.2 equations C-8 through
C-11. It distinguishes nominal CPB removal from the actual low-delay removal
time, which may depend on AU arrival. The ITU lists [H.264 (06/2026), V16](https://www.itu.int/rec/T-REC-H.264-202606-I)
as currently in force; its 15.5 MB PDF was not independently inspected here.
Neither edition defines the proprietary Grid wrapper timestamp or establishes
its clock domain.

## Latest bounded live check — 2026-10-26

The final generated artifact in this worktree
(`9aee91fc32fcad45b5297084a8c325efa50093da6e1bda4dc8a73d3d42ba58ab`) was loaded
from a clean Streamlink 8.6.2 plugin directory and used for one
10-second/200-MB-capped, anonymous Grid-only smoke against one item from the
existing first-page public sample. The run ended after 3.004 seconds with exit 1
and the now-specific error `h264_hrd_grid_clock_conflict`; it was not identified
as an adult-rated stream. The structured error facts showed a buffering-period
anchor followed by 24 CPB ticks (200 ms from the signaled 1/120-second tick),
while the Grid timestamp advanced only 1,980,000 units (198 ms). The final AU
delta alone was 170,000 units against 166,667 expected, but the accumulated
2-ms epoch discrepancy exceeded the 1-ms bound. This demonstrates why only
checking each adjacent AU would permit clock drift to accumulate.

The partial TS contains 12 decodable 2560×1440 H.264 pictures and no accepted AAC
access units. FFmpeg 9.0.2 decoded those 12 video pictures with `-xerror`. The
independent TS scan found 2,543 packets, 12 PCR samples and 11 positive progression
pairs on PMT-declared PID `0x100`, with zero sync, continuity, PSI CRC/structure or
PCR regression errors. These results verify only the emitted prefix; they do not
turn the failed recording into a successful A/V capture.

As a comparison, two retained, private, earlier Grid captures were re-read without
exporting payload or identifiers. Across 4,415 HRD-timed pictures and 4,376
adjacent comparisons (39 buffering periods), the CPB tick was 2 per picture, Grid
increments were 16–17 ms, and epoch cumulative residual stayed within 0.667 ms.
Those samples support the constrained check for those earlier sources but do not
prove the proprietary Grid timestamp semantics universally. The new 198-ms/200-ms
sample is therefore **incompatible with the currently validated Grid↔HRD clock
mapping**; it is not sufficient evidence that the H.264 bitstream itself violates
the standard. Without proof of the Grid clock domain or a validated common
audio/video rescaling, changing the tolerance or substituting a guessed clock
would be unsafe. The receiver remains fail-closed for this sample.

The authenticated adult-stream tests documented above remain separate evidence:
they were rejected for POC/HRD/Grid timing conflict and did not produce a complete
recording. A bounded first-eight listing query on the corrected artifact found no
eligible adult-rated, non-password, non-subscription target; no broader search or
new authenticated adult capture was performed.

### Follow-up after Annex C buffering-period correction — 2026-10-26

The current source validates a later buffering-period transition access unit
against the preceding explicit anchor using Annex C.1.2 equation C-8 before
resetting the anchor for C-9. This fixes a state-boundary omission; it does not
change the Grid clock interpretation or relax the empirical 1 ms residual gate.
A retained private Grid capture replayed through the corrected source passed full
H.264/AAC decode and the independent TS, PCR, DTS and audio packet-interval
checks. The corrected artifact later passed a 10-second live smoke and a
30-minute live recording on a POC-type-2 stream without HRD or fixed VUI timing.
The recording passed complete H.264/AAC decoding and TS/timestamp checks, and one
natural TCP EOF recovered through Center rediscovery and Cache Overlap. This does
not exercise live B-frame reordering, establish a physically distinct Parent, or
resolve the earlier adult-stream timing conflict. The verifier measured a
0.852333-second change in audio/video packet-coverage boundary offsets; without an
independent content reference this is not classified as perceptual sync or
definitive source-clock drift. A/V synchronization remains unresolved and the
overall validation status is **PARTIAL**.

### Offline replay of retained real B-frame Grid capture — 2026-10-26

The current H.264 clock was separately replayed against a retained 380-second
actual Grid capture. Re-parsing the retained Parent wire found 23,542 `0xC748`
packets whose Frame ID, kind, raw timestamp and payload length matched the saved
index; payload bytes matched the saved H.264/AAC elementary streams. The original
packet bodies were fed through the current `MediaSink` and TS muxer. It accepted
all 22,800 pictures, including 16,910 B-slice pictures, using POC type 0 and HRD
SEI timing. DTS remained strictly increasing; 5,700 PTS reorder steps were
observed. The resulting TS passed
independent full H.264/AAC decode, with 22,800 video pictures, 17,808 AAC access
units, zero TS continuity/PSI/PCR/DTS errors, and zero PTS-before-DTS errors.

This validates the current parser and output path on a retained real B-frame
sample by offline replay; it is not a fresh live run of the corrected artifact.
It also does not resolve the separately observed authenticated adult-stream
Grid/HRD clock contradiction. The 96 ms change in audio/video packet-coverage
boundary offset is not a clock-drift or perceptual-sync measurement. Long-run
A/V synchronization and the adult-stream case remain unresolved.

The original frame sidecar was generated by an earlier timing implementation and
is not an independent PTS reference: all 22,800 current PTS values differed from
it, while current POC, slice type and DTS matched all sidecar entries. FFmpeg 9.0.2
`trace_headers` independently parsed CPB-removal and DPB-output delay values for
all 22,800 pictures; both fields matched the current parser on every AU. The
capture contained 190 buffering-period AUs, and the current state machine passed
189 later C-8 boundary checks. On the first 200 pictures (including two
buffering-period AUs), the current `PTS-DTS` value matched FFmpeg's
`dpb_output_delay` rescaled by the SPS clock tick on 200/200 pictures; the
historical sidecar matched 0/200. This cross-checks field parsing and AU
association, plus the sampled relative composition-offset calculation. It does
not independently establish the general correctness of every C-8/C-9 transition,
Grid timestamp semantics, absolute A/V origin, or perceptual synchronization.

### Authenticated adult-stream check on artifact `109148f…` (current at test time) — 2026-10-26

The current `109148f4…` standalone artifact was tested using the user's existing
credentials against a publicly listed adult-rated broadcast. The test required
adult authorization, selected Native Grid only, and had a short duration and
wire-byte cap. It reached actual Grid video media, confirming that this attempt
passed far enough through authentication and Grid setup to exercise the current
H.264 timing path. It then failed closed after three video pictures with
`h264_hrd_grid_clock_conflict`; there was no HLS fallback and no AAC access unit
was accepted.

Safe diagnostic facts for the rejected AU: the IDR anchor carried CPB/DPB values
240/2; the next P picture carried CPB delay 2 with a 17 ms Grid delta; the next
rejected P picture carried CPB delay 4. Across the explicit epoch, four HRD ticks
at 1/120 second imply 33.333 ms, while the Grid timestamp advanced 32 ms. The
1.333 ms difference exceeded the implementation's empirical 1 ms comparison
guard. These facts explain the code path but do not determine whether the Grid
clock is quantized/jittered, the Grid and HRD clock domains differ, or the
bitstream's HRD signaling is inconsistent with the actual transmission clock.
The guard is an implementation policy, not a tolerance defined by H.264.

The partial TS had no sync, continuity, PSI, or PCR regression findings, and its
two complete video PES decoded. The independent full-recording verifier failed
because there was no accepted audio stream; this is a failed partial capture,
not a successful A/V result. No tolerance was widened and no guessed timestamp
was emitted. The current implementation does not support this observed timing
combination; support for adult-rated streams generally is not established by
this one sample.

The later bundle `8265228dc4e5f6238a60880d1f1e9e30ff7d94199c02c511760fd22bc8a260f1`
completed a 30-minute public POC-2/HRD capture with no observed PTS reordering.
That validates a different timing case and does not resolve the adult-stream
Grid/HRD conflict above. The adult-rated case was not re-run against that exact
bundle; its H.264 timing code is unchanged, and the intervening source change
only rate-limits repetitive diagnostic events.

### 2026-10-27 offline timing-threshold investigation

A retained 36.05-second authenticated adult Grid capture was replayed through the
current `H264Clock` and complete `MediaSink`/TS path without exporting its
payloads or identifiers. It contains 2,161 POC-type-2 video pictures (I/P only,
no B pictures) and 72 audio Grid messages. With the production 1 ms
Grid/HRD-epoch guard enabled in that historical build, current-source `H264Clock` rejects video picture
9: 16 HRD ticks represent 133.333 ms while the Grid timestamp has advanced
135 ms, a +1.667 ms residual. This is `h264_hrd_grid_clock_conflict`; it is not
`h264_poc_mode_unsupported`, and the current parser no longer raises the old
generic `h264_timing_unconfirmed` code for the no-HRD/reorder case. The latter
case now uses the specific `h264_reorder_timing_unconfirmed` error.

For sensitivity analysis only, the in-memory clock tolerance was set to 5 ms;
no source or bundle file was changed. All 2,161 pictures then passed the current
parser, and the current MediaSink emitted all 2,161 pictures and 1,687 AAC access
units (1,727,488 core samples per channel) into a 38,152,720-byte TS. FFprobe
recognized 1920×1080 H.264 and 48 kHz stereo AAC. FFmpeg 9.0.2 decoded the full
audio/video output with `-xerror`, no diagnostics, and no trailing non-TS bytes.
This replay had no B pictures, so it did not exercise reordered output. Full
decoding demonstrates bitstream/container decodability only; it does not prove
that a 5 ms Grid/HRD tolerance gives the correct source timeline or perceptual
A/V synchronization.

Two additional retained capture indexes were replayed through `H264Clock`.
One distinct POC-2/HRD sample also crosses the production 1 ms guard at picture
104 (13.350 ms Grid epoch elapsed versus 13.333 ms HRD elapsed, +1.667 ms) but
parses all 2,691 pictures with a 5 ms **analysis-only** tolerance; its measured
epoch residual ranged from −3.000 to +4.667 ms and ended at −1.000 ms. A separate
1440p sample passed the production guard, with residual no greater than 0.667 ms.
These captures show that the current threshold distinguishes observed sources;
they do not identify which clock is authoritative or establish a normative
tolerance.

An independent adversarial review concluded that waiting for a finite prefix
cannot prove that future Grid timestamps will remain compatible with HRD. A
second review found that 5 ms can be described as an empirical accommodation for
these samples, but not as a demonstrated safe timing bound: timestamp semantics,
the effect on actual A/V error, and later clock behavior lack an independent
reference. A different adult sample also has a POC/PTS ordering contradiction;
increasing this clock-comparison threshold would not resolve that condition.

The former 1 ms cross-clock guard was an implementation assumption, not a
documented SOOP protocol invariant, and has since been removed. POC/PTS ordering
checks and fail-closed handling of internally contradictory timing remain. The
retained adult sample is still unsupported by the current plugin. No
bounded-start buffering scheme can repair an internally contradictory HRD/POC
mapping or certify a future timeline. Resolving the remaining semantic question
requires an independent definition or reference for the Grid timestamp clock,
or another validated common video/audio timebase.

### Parameter-set parser review — 2026-10-27

Independent review found that SPS/PPS parsing stopped after the fields used by
the clock without validating the mandatory `rbsp_trailing_bits`. A malformed
SPS and PPS with the `rbsp_stop_one_bit` cleared were accepted. The parsers now
validate the stop bit, zero alignment, and end-of-RBSP boundary. PPS extension
syntax is parsed, including scaling-list syntax when its referenced SPS is
available; when the SPS has not arrived, the parser accepts only a uniquely
parseable legal scaling-list layout and later checks that list count against
the SPS. New tests mutate the stop bit on FFmpeg-generated parameter sets and
verify rejection, and cover PPS-before-SPS scaling extensions.

The review also showed that `fixed_frame_rate_flag=1` and
`low_delay_hrd_flag=1` are not inherently contradictory. The parser no longer
labels that combination malformed. The current timestamp model still rejects
low-delay HRD explicitly as `h264_hrd_low_delay_timing_unsupported`; support was
not added. The H.264/Grid clock mapping test remains an HRD conversion test and
does not independently prove that real Grid timestamps are decode-clock DTS.

A follow-up adversarial pass found that PPS `pic_init_qp_minus26`,
`pic_init_qs_minus26`, and chroma QP offsets were parsed but not range-checked.
These values are now validated against H.264 bounds, including the referenced
SPS bit-depth offset; if PPS arrives before SPS, the deferred QP check runs when
the SPS appears. Chroma offsets outside −12…12 and an 8-bit QP offset of −27
are rejected in regression cases.

### Clock-semantics review — 2026-10-27

The `h264_hrd_grid_clock_conflict` name describes a guard added by this
receiver; it is not a SOOP protocol error or a condition required by the H.264
standard. At the revision described in this dated section, the receiver mapped the
Grid media-header timestamp directly to DTS and additionally required elapsed
Grid time to agree with the HRD CPB removal schedule within 10,000 units of the
assumed 100-ns Grid timebase (1 ms). That local gate was later removed.
No SOOP protocol document, official-client dataflow, or independent shared-clock
reference has established that equality or tolerance. Treating it as an
official-player requirement would therefore be incorrect.

ITU-T H.264 defines `cpb_removal_delay` relative to an access unit associated
with a preceding buffering-period SEI and defines `dpb_output_delay` as the
number of HRD clock ticks between CPB removal and DPB output. The VUI/HRD clock
defines timing within the coded-video HRD model; the specification does not
define a mapping to SOOP's proprietary Grid media-header timestamp. The Grid
field's unit, clock domain, and whether it represents decode time, presentation
time, or distribution scheduling time remain unconfirmed. Consequently, the
1-ms comparison is an implementation policy, not a normative conformance test.
See [ITU-T H.264, Annex C and Picture Timing SEI](https://www.itu.int/rec/ITU-T-H.264/en/en).

The latest final-artifact observation (`69d8e467…`) reproduces the gate exactly:
the rejected transition had a 166,667-unit HRD delta and a 180,000-unit Grid
delta, a 13,333-unit (1.3333-ms) difference. An earlier offline replay of a
retained real capture found 133.333 ms HRD versus 135 ms Grid (1.667 ms). These
are direct observations of differing values, not proof of which clock is the
media presentation timeline. Another retained sample contains a POC/DPB output
ordering contradiction; simply widening this comparison would not correct
that separate contradiction.

The retained native analysis is for `SOOPStreamer.exe`, SHA-256
`98a1741b2368be33e25958a46443760663ff8da225c742aabac16d625297709b` (x86-64,
Ghidra 12.1.4). The saved Ghidra decompilation of
`FeatureSocketHandler::handleBroadcastStreamVer2` at `0x1402f2d60` shows the
stream-response path constructing/sending the `0xCB2F` follow-up. The saved
`handlePushFrame` decompilation at `0x1402f54f0` shows socket-event/message
handling. In those retained function excerpts, no dataflow from the Grid media
timestamp to a decoder DTS/PTS or to the receiver's 1-ms HRD comparison was
established. This is limited negative evidence, not proof that the complete
official client never consumes the timestamp elsewhere. A fresh REA/Ghidra
import of the same binary exceeded the configured 330-second startup deadline;
no new native-client timing claim is made from that failed import.

The older `0x1BC7` response has a 24-byte structure in the Go research parser
with fields named `LastPTSNew` and `LastPTSBefore`, based on historical packet
analysis. That does not establish field semantics for the current `0xC746`
response path. The current Python receiver accepts the command as a handshake
response but discards its body, and current retained live captures contain only
`0xC748` media packets, not a correlated `0x1BC7` control transcript. These
fields are not used as a timestamp anchor.

Therefore the present evidence supports neither “Grid timestamp is DTS” nor
“Grid timestamp is PTS” as a protocol fact. A distribution-time/scheduling
timestamp or a separate clock with an unknown transform also remains possible.
At the revision described here, rejection was a locally imposed fail-closed
compatibility gate, not an identified official-player requirement. The later
model review removed the cross-clock equality check but retained the independent
POC/PTS ordering rejection. The observed HRD-conflict stream remains unsupported;
no official-client mapping or independent synchronized reference has established
a safe alternative.

### Timeline-model comparison and removal of the empirical cross-clock gate — 2026-10-27

The preceding paragraphs describe the old implementation. They must not be
read as claiming that a 1-ms Grid↔HRD agreement is an H.264 or SOOP requirement.
That test was introduced locally because earlier captures happened to agree
closely and cumulative drift should not be hidden by a per-picture allowance.
It was never derived from an official player requirement. A new review of the
model and retained media found that a cross-domain equality check is not a valid
conformance gate without a defined mapping between the clocks. The code no
longer compares Grid elapsed time to HRD CPB elapsed time. It still rejects
non-increasing Grid DTS candidates, missing or malformed HRD timing, unsupported
low-delay HRD, contradictory POC/PTS order, unbounded reorder, and invalid AAC
sample-clock progression.

The exact previously reported `8.333 ms / 8.200 ms` observation was two HRD
ticks of `1/240 s` each (`8.333333 ms` total) versus a Grid timestamp delta of 82,000
100-ns units (`8.200 ms`), a local residual of `0.133333 ms` at POC 8. The
final `69d8e467…` probe was a different signaled clock: two ticks at
`1/120 s` each represented `166,667` 100-ns units (`16.6667 ms`), while Grid
advanced `180,000` units (`18.000 ms`), again a `1.3333 ms` residual. These
are interval observations, not measurements of A/V error. The first comparison
was not a constant offset test; the later buffering-period anchored comparison
was an accumulated residual within that HRD epoch.

Four candidate models were compared against retained Grid input:

| Model | Evidence in retained input | Result and limitation |
|---|---|---|
| A. Grid timestamp is DTS; HRD DPB delay gives PTS−DTS | Grid video timestamps are increasing in receive/decode order; on the retained real B-picture capture, the existing model produced strictly increasing DTS, POC-consistent PTS reorder, and complete decoded output. On the 36.05-s sample below, the same model now emits all pictures and AAC samples. | Best-supported implementation model so far, but not proven as an official protocol semantic. The 36.05-s sample has no B pictures. |
| B. HRD alone supplies DTS/PTS | `cpb_removal_delay` and `dpb_output_delay` parse and are independently cross-checked against FFmpeg `trace_headers` on the retained B-picture capture. In the 36.05-s sample, C-8/C-9 segment accounting yields about 36.000 s HRD versus 35.999 s Grid span. | HRD provides relative coded-video timing, but no verified absolute transform to AAC/Grid epoch. It cannot independently anchor a combined A/V TS. |
| C. Affine Grid↔HRD transform | In the 36.05-s sample, 18 within-buffering-period fits had slopes 0.999777–1.000169 (median 0.999961); residuals after each period-local anchor ranged −3.000 to +4.333 ms. Eighteen C-8 buffering-period intervals differed by −1 to +1 ms; accumulated HRD span was 36.000 s versus Grid 35.999 s. | Evidence is consistent with closely related rates plus quantization/jitter for this sample. Per-period re-anchoring removes information about long-term offset/drift; a 36-s sample cannot establish a universal affine mapping. |
| D. Grid is transport/scheduling time and media needs another clock | No per-packet monotonic socket-arrival trace tied to these frames is retained. The native-client analysis did not trace the Grid timestamp into a decoder timestamp setter. | Not testable from current evidence; remains possible. |

The audio in this retained sample contains 72 Grid audio messages, 1,687
AAC-LC access units and 1,727,488 core samples/channel at 48 kHz (35.989333 s).
Each ADTS access unit contributes 1,024 samples; messages contain multiple
access units. The 71 inter-message intervals differed from the preceding
message's sample duration by −12.000 to +9.333 ms, with +1.333 ms accumulated
over the 71 intervals. The raw audio Grid timestamp span was 35.500 s; adding
the last chunk's sample duration gives 35.989333 s, within that accumulated
1.333-ms residual. This supports interpreting the chunk timestamp near its
first AAC sample for this capture. It does not establish that audio and video
Grid timestamps share an epoch for all streams, nor that network arrival time
is media time.

With the empirical cross-clock rejection removed, the retained 36.05-s actual
Grid capture was replayed through the unmodified media payloads and current
`MediaSink`/TS path. All 2,161 video pictures and all 1,687 AAC access units
were emitted. The locally retained artifact is
`validation-artifacts-grid-clock-20261027/recording.ts` (outside the Git
repository, owner-only permissions), SHA-256
`07a1e07313f9b7bce5d935b193efa92a6c2521b5a6e578bb61c83c156f81b112`;
`verification.json` records the independent check. FFmpeg 9.0.2 decoded all
2,161 H.264 pictures and 1,727,488 PCM samples/channel with `-xerror`; TS sync,
continuity, PAT/PMT CRC/structure, PCR progression, DTS monotonicity, audio
packet overlap and trailing-byte checks passed. Packet-coverage start/end offset
changed by −26.333 ms; this remains a coverage statistic, not source-clock
drift or perceptual sync. This sample is POC type 2 with no B pictures, so it
does not revalidate reordered output.

**Current rule:** Grid's measured 100-ns value is used as a provisional DTS
candidate; when HRD timing exists, `dpb_output_delay × clock_tick` supplies the
relative PTS offset. POC/PTS ordering is checked independently. AAC output uses
its sample count at the first observed Grid audio timestamp and checks later
chunk starts against that sample clock. This removes a demonstrated false
rejection without asserting the Grid timestamp's official meaning. The retained
real B-picture replay independently validates relative reordering and decode,
while the short POC-2 sample validates the newly accepted cross-clock-jitter
case. Neither proves perceptual A/V synchronization or behavior of the latest
single-file artifact on a live adult-rated broadcast.

The latest 30-minute verification attempt in this environment targeted an
older artifact's retained file, not the current bundle. Structural packet
inspection found no TS sync/continuity, PSI, PCR or DTS regressions, but the
verifier timed out during full decode and reported audio packet overlaps; it is
therefore **not a passing 30-minute result**. No 30-minute live recording has
been made with the artifact built from this timeline change. The next required
gates are a bounded live Grid capture on the regenerated artifact, followed by
30 minutes only if the short capture passes full A/V and timeline validation.

### Timestamp-model audit addendum — 2026-10-27

The former 1-ms Grid/HRD rule was a receiver-authored empirical acceptance
criterion. Neither the inspected H.264 text nor any recovered SOOP client path
requires Grid timestamps and HRD CPB time to agree within 1 ms. The historical
`h264_hrd_grid_clock_conflict` therefore identified failure of that local
assumption, not proof of corrupt media. That equality check is absent from the
current `H264Clock`; it was not merely widened.

The reported 8.333-ms versus 8.200-ms pair is an **interval** comparison:
one HRD tick at 1/120 s is 83,333.333 units of the Grid 100-ns scale, while the
observed Grid delta was 82,000 units, a 1,333.333-unit (0.133333-ms) difference
in the raw 100-ns domain. It is not a fixed
epoch offset. A separate 10-tick/198-ms observation accumulated a 2-ms
within-epoch residual. These short observations alone cannot distinguish
timestamp quantization/jitter from a rate mismatch. In the 36.05-s retained
capture, 18 period-local slopes were 0.999777–1.000169 (median 0.999961),
local residuals −3.000 to +4.333 ms, and complete video spans were 35.999 s
(Grid) versus about 36.000 s (HRD). That longer sample is consistent with close
rates and local quantization; it does not prove a universal clock transform.

Candidate-model results:

| Model | Evidence/result | Limit |
|---|---|---|
| A. Grid as DTS, HRD DPB delay as relative PTS | The current source replay of the retained 36.05-s capture emitted 2,161/2,161 video pictures and 1,687/1,687 AAC AUs. FFmpeg decoded all video and 1,727,488 PCM samples/channel; TS, PSI, PCR, continuity and DTS checks passed. The separate 380-s B-frame replay also passed with 22,800 pictures and 16,910 B slices. | This is the best-supported current implementation model for those captures. In the bounded adult live sample, Grid+DPB candidate PTS put POC 2 before POC 0; the receiver correctly fails closed under this model. Grid-as-DTS is not a SOOP protocol fact. |
| B. HRD CPB removal and DPB output as the complete video clock | In the failing live sample, the first POC-0 picture had DPB delay 18; the next POC-2 picture had CPB delay 2 and DPB delay 2, with no intervening buffering-period message. Relative to the first picture's CPB removal anchor, H.264 timing yields `2 + 2 - 18 = -14` HRD ticks (−116.667 ms at 1/120 s), contrary to POC output order. FFmpeg `trace_headers` independently confirmed parsed widths/values. | The stream's HRD timing and POC order are contradictory under the standard timing interpretation, or the association/stream semantics are otherwise unsupported. HRD alone also supplies no validated AAC/Grid epoch. |
| C. Fixed offset or affine Grid↔HRD conversion | The 36-s sample is consistent with close rates and period-local fits; prior captures also showed close spans. | An affine transform near unit slope cannot reverse the failing sample's −14-tick HRD/POC ordering. Fitting each short period would hide long-run drift and is not a validated conversion. |
| D. Grid is delivery/scheduling time; another presentation clock is needed | Possible; no official player timestamp-consumer path was recovered. | No independent decoder-output or common A/V clock has been retained to identify that clock. |

POC is used only for order, reference-state and reorder validation. A proposed
`ceil(POC/2) × fixed VUI frame duration` mapping was rejected after independent
review: POC numeric distance is not a general duration, and legal type-0/type-1
values can produce nonuniform distances or collapse to the same derived slot.
Fixed VUI cadence alone is not enough to replace contradictory HRD for arbitrary
reordered pictures. The receiver therefore does not choose POC over HRD or
invent timestamps for this sample.

Audio on the retained 36.05-s capture comprised 72 Grid messages, 1,687 AAC-LC
access units, and exactly 1,727,488 decoded core samples/channel at 48 kHz
(35.989333 s). Each ADTS AU contributes 1,024 samples; the message timestamp is
used as a chunk-start candidate and subsequent AUs are advanced by exact sample
counts. Inter-message residuals ranged from −12.000 to +9.333 ms, with +1.333 ms
cumulative residual; the raw audio Grid span was 35.500 s. This supports the
chunk-start interpretation for this capture only. The final video/audio
coverage-start difference was 43 ms and the coverage-boundary change was about
−26.333 ms; neither proves perceptual synchronization or source-clock drift.

The earlier bounded live failure remains distinct from the removed 1-ms gate:
the second picture's HRD-derived PTS contradicted increasing POC, producing
`h264_picture_order_conflict`. Its partial output decoded, but had no accepted
audio and was not a successful recording.

### Current-bundle live sample — 2026-10-27

The regenerated local bundle (`b3ed4729…`) completed a 60-second, Grid-only
recording from a listed public adult-rated broadcast using the authorized
account. It ended on `duration_limit`, exit 0. Independent FFmpeg 9.0.2
verification passed for the complete 60.97-second video and 61.01-second audio
coverage: 3,658 H.264 pictures, 2,860 AAC-LC access units, 2,928,640 decoded
samples/channel at 48 kHz, 341,905 TS packets, 61 PAT/PMT observations, 3,658
PCR samples, and zero TS continuity, PSI, PCR, DTS, or decode errors. The
SHA-256 of the captured H.264 and AAC elementary-stream byte sequences matched
the corresponding PES payloads extracted independently from the TS. The
receiver-observed stream had POC type 2, 3,627 P pictures and no B pictures;
this run therefore does not validate picture reordering.

For this sample, Grid video timestamp deltas had median 17 ms and mean
16.6683 ms (range 3–31 ms); candidate DTS was strictly increasing and the
HRD-derived PTS offset was 16.6667 ms. Thirty-one buffering-period epochs were
observed. Within each epoch, affine HRD-CPB/Grid fits had slopes 0.9945–1.0032
(median 1.000002); the median period RMS residual was 0.585 ms. Since each
period was at most about two seconds and buffering-period resets remove the
cross-period origin, this supports near-equal local rates for this sample only;
it does not establish a long-term transform or common epoch. The socket-arrival
clock was not captured, so model D cannot be tested here.

Audio arrived in 122 Grid messages, each timestamped 500 ms apart and containing
23 or 24 AAC-LC access units (2,860 total). Each AU decoded to 1,024 samples per
channel; sample-count duration was 61.0133 s. Batch timestamps behave as
chunk-start candidates in this capture: their residual against the continuous
sample clock ranged from −18.667 to +1.333 ms. This is evidence about batching
in this sample, not proof of a protocol-wide epoch relationship. Decoded stream
coverage offsets changed by 40.7 ms; the verifier classifies that as coverage
boundary change, not source-clock drift or perceptual A/V sync.

The normative meaning of the H.264 fields is narrower than the earlier local
gate assumed: `cpb_removal_delay` is relative to the access unit associated with
the most recent preceding buffering-period SEI and is modulo its signaled width;
`dpb_output_delay` specifies the wait after CPB removal before picture output.
Neither field defines a mapping to SOOP's proprietary Grid timestamp. See
[ITU-T H.264 (08/2024), clauses D.2.3 and C.2](https://www.itu.int/rec/dologin_pub.asp?id=T-REC-H.264-202408-I%21%21PDF-E&lang=e&type=items).

The prior POC/HRD-conflicting live encoder variation remains unsupported: this
successful sample is a different timing pattern and has no B pictures. The
30-minute run was not started because the persistent filesystem had only about
485 MB free, while this 60-second output alone was 64.3 MB. The 4-GiB `/dev/shm`
is volatile and does not satisfy the required persistent-evidence condition.
Current status remains PARTIAL; no long-duration or B-frame live success is
claimed. The recording, private raw capture, process diagnostics, and verifier
JSON are retained locally under
`validation-artifacts-grid-clock-20261027/live-short-final-b3ed/`, outside Git.

### Retained B-frame capture replay — 2026-10-27

To test reordering with real source bytes, the retained 36.8-second 1440p Grid
capture at `rea-research/captures/grid-highres-20261024T050701Z` was replayed
through the current modular `MediaSink`. The capture contains 2,209 H.264 access
units: 19 I, 981 P, and 1,209 B pictures, confirmed by both the current slice
header parser and FFprobe's independent decode. Its SPS signals POC type 0,
fixed VUI timing at 60 fps, and HRD timing with an 8.333333-ms tick. Grid
timestamp deltas are 16–17 ms, averaging 16.6667 ms.

Under Model A (Grid timestamp used as the DTS candidate and HRD DPB output delay
used only as the relative PTS offset), all 2,209 DTS values increase. PTS
reorders 967 times in decode order, has no POC-order inversion within IDR
epochs, and its display-order picture-type sequence exactly matches FFmpeg's
independent decoder output. The rebuilt TS passes the independent verifier:
2,209 video pictures and 1,704 AAC-LC access units decode; DTS regressions,
PTS-before-DTS, TS sync/continuity, PCR regression, PAT/PMT and full-decode
errors are all zero. The verifier's elementary-stream hashes match the
original H.264 and AAC bytes extracted from the TS.

This is an offline replay of retained real media through the current modular
source, not a new live B-frame recording and not a direct test of the standalone
bundle. It supports Model A for this capture; it does not prove that Grid is
protocol-defined DTS or that every encoder's HRD is consistent. The archived
TS that accompanied the same capture failed the current verifier: it had 966
non-monotonic DTS transitions, one missing DTS, no reported PTS reorder, and
FFmpeg stopped after two decoded video frames. The historical TS's producing
revision is not established, so that failure is evidence about that file, not
attribution to a specific source revision.

For this B-frame sample, 19 buffering-period-local affine fits of CPB-relative
time to Grid elapsed time had slopes 1.00000285–1.00006947 (median
1.00000285), residuals −0.358 to +0.358 ms, and median local RMS 0.272 ms.
These short-period fits are compatible with similar rates plus quantization;
they do not establish a single absolute epoch or a long-term conversion. An
HRD-only timeline therefore remains unproven as a common A/V clock. The
per-message socket-arrival clock needed to assess Model D was not captured.

Audio in that capture consists of 71 Grid messages, each containing 24 AAC-LC
access units. Each AU contributes 1,024 decoded core samples per channel at
48 kHz; the total is 1,744,896 samples/channel, or 36.352 seconds. Message
timestamps advance by 512 ms, equal to each batch's sample duration, which
supports a batch-start interpretation for this sample only. In the rebuilt TS,
decoded audio coverage starts 30.7 ms before video and ends about 495 ms before
video. That tail difference is roughly one audio batch and is reported as a
capture coverage-boundary difference, not as source clock drift or perceptual
sync. The capture does not prove that the final A/V content is complete or
perceptually synchronized.

The current-model TS and evidence are retained outside Git under
`validation-artifacts-grid-clock-20261027/retained-bframe-current-model/`:
`recording.ts`, `verification.json`, `historical-source-ts-verification.json`,
and `model-comparison.json`. The derived TS SHA-256 is
`9622fd5955af2ee82bfa316de0b83a01ba0e65f85f1624131d967a282a54a106`; the
bundle used as the contemporaneous build reference is
`b3ed4729795636942a1636a03ee2ea4921b7590c18d9fae24a4810347ac4e505`.

The requested 30-minute recording is still blocked. The guest does not expose
the reported 256-GB virtual disk: both read-only device inspection and the
elevated inspection show only a 64-GB `/dev/sda` with an ext4 `/dev/sda2` root
filesystem. The block device itself is not accessible to this execution
environment, `sudo` is blocked by `no new privileges`, and the persistent
filesystem is currently read-only in the sandbox view with roughly 234 MiB
free. No partition or filesystem command was run. A 30-minute live capture and
its complete persistent evidence set were not started.

### Grid/HRD comparison and diagnostic-only capture — 2026-10-27

The former 1-ms Grid/HRD cross-clock requirement was an implementation guard,
not a known SOOP protocol invariant. It is not used as a production acceptance
rule. The fresh 15-second diagnostic-only capture had H.264 HRD ticks of
`1/120 s` (8.333 ms) and 100-ns Grid timestamps. Video Grid deltas ranged from
13 to 21 ms, median 17 ms, mean 16.66776 ms. Regression of unwrapped HRD CPB
candidate time against Grid elapsed time gave slope 0.9999904, 0.893-ms RMS
residual and 5.854-ms maximum residual. This supports similar rates in that
sample, but not equality of individual frame instants and not the semantic
identity of the Grid clock.

The retained 36.8-second 1440p capture is a different stream: 2,209 progressive
H.264 access units, POC type 0, 1,209 B-slices. Grid deltas were 16–17 ms and
averaged 16.66667 ms. Its HRD/Grid affine slope was 1.000000008, RMS residual
0.272 ms and maximum absolute residual 0.334 ms. In this B-frame capture,
Grid-DTS plus parsed HRD DPB delay yields PTS order consistent with POC. In the
new diagnostic live capture, which had no B-slices in the observed 15 seconds,
the same HRD DPB calculation conflicts with POC. H.264 defines CPB removal
delay relative to its applicable buffering-period anchor and DPB output delay
after CPB removal; neither field is packet arrival time or a defined mapping to
the proprietary Grid timestamp. The two streams therefore show different
behavior; the contradiction must not be erased by relaxing a tolerance.

An offline POC/VUI candidate remux was also evaluated separately from the
production plugin. For the diagnostic capture it decoded 916 video pictures
and 727 AAC access units, with zero TS sync, continuity, PSI CRC, PCR or DTS
errors. For the retained B-frame capture it decoded 2,209 video pictures and
1,704 AAC access units, had 967 PTS reorder steps and zero DTS, PCR, PSI or
continuity errors. These results show that a candidate CFR presentation
schedule can produce structurally valid files from those exact payloads. They
do not prove that schedule is the original source timeline, a safe real-time
reorder policy, or a production-ready fallback. In the B-frame SPS,
`max_num_reorder_frames` was not explicitly signaled; 12 is a calculated DPB
upper bound, not a measured exact lookahead requirement. The independent review
also confirmed that the retained capture's earlier remux used decoder frame
positions to construct an offline CFR track, so it cannot serve as an
independent original PTS reference.

The AAC observations support—but do not prove—a Grid timestamp at the start of
each message. In the B-frame capture, 71 messages each contain 24 AAC-LC AUs;
each AU contributes 1,024 core samples/channel at 48 kHz, so a batch is exactly
512 ms and Grid timestamp steps are exactly 512 ms. In the diagnostic capture,
31 messages contain 727 AUs and 744,448 samples/channel (15.509333 s); Grid
steps are 500 ms and the accumulated sample-clock residual is 2.667 ms. Neither
sample proves a shared absolute audio/video epoch. Candidate-remux A/V offsets
are track-coverage boundaries only: the B-frame sample starts with audio 14 ms
before video and ends with audio 478.7 ms less coverage; the diagnostic sample
starts 379.3 ms earlier and ends 137.7 ms earlier. No perceptual synchronization
claim is supported.

Existing SOOPStreamer REA decompilation covers Grid message handling and push
callbacks, but not a confirmed timestamp-to-decoder/renderer path. The official
native executable was not available in this workspace and the current REA
session has no target open. There is consequently no evidence that the official
player uses the former 1-ms comparison, nor evidence sufficient to copy its
timestamp mapping.

**Decision:** keep production fail-closed on the observed HRD/POC presentation
conflict. Do not widen the old threshold and do not enable the offline POC/VUI
candidate as an automatic fallback. The diagnostic observation mode and
offline remux are not valid recording-success evidence.

### Updated storage check — 2026-10-27

The guest still sees a 64-GiB `/dev/sda` with a 64-GiB ext4 root partition and
about 223 MiB available; the requested 256-GiB virtual capacity is not visible.
The authorized NFS exports have about 1.9 TiB free but both are mounted
read-only. A non-destructive remount attempt failed with `Operation not
permitted`; no partition, filesystem, NFS content, or existing capture was
modified. A 30-minute run would exceed safe local free space, so that test
remains blocked until a writable persistent destination is exposed.

### NFS write authorization and independent parser cross-check — 2026-10-27

The user subsequently authorized temporary use of the existing NFS export. A
bounded write probe on that export succeeded and the probe file was removed;
the earlier read-only restriction therefore no longer blocks persistent
artifact storage. The existing captures were not changed.

The 15-second diagnostic capture's frame index accounts for all 15,234,030
bytes in its retained `video.h264` elementary stream. The local Annex-B parser
accepted all 916 indexed access units and reproduced every indexed POC. FFmpeg
9.0.2 `trace_headers` independently reported 916 CPB-removal-delay values and
916 DPB-output-delay values; both complete sequences matched the local parser.
It also reported eight buffering-period messages. For the first IDR, both
parsers read CPB/DPB delays 240/18 and buffering-period initial delay/offset
88,260/1,739; for the next picture both read 2/2. This cross-check supports
the local NAL/SEI extraction and access-unit association for this capture. It
does not establish what the proprietary Grid timestamp means, nor does it prove
that the HRD and Grid values share an absolute clock epoch. The unresolved
problem is clock interpretation and mapping, not an unverified disagreement
between the two parsers. The capture contained no B-slices, so it does not
validate live B-frame timing.

The previously reported 30-minute-configured run under
`live-30m-b3ed-20261027/` is not a 30-minute recording: its receiver summary
shows one playable video frame, no accepted AAC frames, and
`h264_picture_order_conflict`; the process exited 1 with 208,116 output bytes.
The capture and diagnostic records are retained on the authorized NFS export.
They document an early failure only and are not accepted as a timing or
endurance pass.

The earlier statement that a writable persistent destination remained
unavailable is superseded by the successful NFS write probe. The guest's
virtual disk remains 64 GiB; no partition or filesystem expansion was
performed.

### Current-bundle public Grid smoke — 2026-10-27

The regenerated single-file artifact
`af82bfaca211488c3a162f5f1f90d20932fd9828c375dbb37f3c74f0e3eb2cd0` was loaded
by a clean Streamlink 8.6.2 / Python 3.12.3 environment in explicit Grid-only
mode. A bounded public broadcast run ended at the configured duration limit
(process exit 0) after 18.47 seconds total, with 15.858 seconds since the last
media progress sample. It accepted 724 video pictures and 552 AAC-LC access
units (565,248 PCM samples/channel at 48 kHz). The observed stream was POC type
2, fixed VUI, no HRD and no B-slices. No fallback was available in the selected
Grid-only mode.

The NFS-retained output was independently checked with FFmpeg/FFprobe 9.0.2.
The 12.0667-second video and 11.776-second decoded audio passed full decode.
The 67,753-packet TS had zero sync, continuity, PAT/PMT, PSI CRC, PCR regression,
or DTS monotonicity errors. Source H.264 and AAC elementary-stream SHA-256
values matched their complete TS PES payload hashes exactly. Peak RSS was
59,858,944 bytes, with 3 threads, 12 open descriptors and a 408,336-byte buffer
peak. The decoded track-coverage offset changed by 290.667 ms over this short
capture; this is a boundary-coverage difference and does not establish clock
drift or perceptual A/V synchronization.

The run validates this no-HRD/no-B POC-2 path only. It does not resolve the
retained HRD/POC conflict, exercise B-frame timing, or qualify as long-duration
stability evidence. Its persistent evidence is under the private NFS run
directory `live-grid-smoke-af82-20261027/`;
the original Grid identifiers and payloads remain private there and are not
included in Git.

### 30-minute current-timeline Grid recording — 2026-10-27

After the user authorized use of the existing NFS export, the final-bundle
recording was resumed there. Bundle SHA-256 during capture:
`af82bfaca211488c3a162f5f1f90d20932fd9828c375dbb37f3c74f0e3eb2cd0`.
The later `bf42ec535870c72f89ee9fba32bfc58f36212cd7c115c53442c1b3afde0190ad`
bundle differs only in opt-in rejection diagnostics and capture bookkeeping;
the normal timestamp and mux path used by this successful run is unchanged.
The run used a clean Streamlink 8.6.2 / Python 3.12.3 install, explicit
Grid-only transport, a 1,800-second duration limit, a 4-GB wire cap and
private raw capture. It completed in 1,805.568 seconds with exit code 0,
`duration_limit`, no signal and no receiver error.

The stream was 1920x1080 H.264 plus stereo AAC-LC at 48 kHz. The observed
video was POC type 2 with fixed VUI, no HRD SEI and no B slices; the selected
timeline was `grid_no_reorder`. The receiver recorded 108,044 video frames
(1,800.717 seconds on its mux timeline), 84,408 AAC access units / 86,433,792
samples per channel, and 3,517 audio Grid messages. There was one Parent
connection and no failover, cache replay, missing frame, duplicate, identity
conflict or sequence discontinuity. Peak RSS was 52,039,680 bytes, three
threads and 12 open file descriptors; peak StreamIO buffer use was 2,482,664
bytes.

The full 1,890,816,016-byte TS passed the independent FFprobe/FFmpeg 9.0.2
verifier with exit code 0, zero diagnostic lines and no failed gates. It
contained 10,057,532 TS packets, 1,801 PAT and PMT sections, 108,044 PCR
samples, and zero sync, continuity, PSI CRC, PCR regression, DTS regression,
transport or PES-structure errors. Full decoding yielded 108,044 video frames
over 1,800.733667 seconds and 84,408 AAC frames / 86,433,792 PCM samples per
channel over 1,800.704 seconds. Video elementary-stream SHA-256
`25656e080070c3fbfb536d338ef9d113abbe6344fe5d6ec055186d38e52e1f3a` exactly
matched the complete video PES payload hash; the analogous AAC hash
`e7ce3d9fd147febcd16cb04efd14a7a704d8a28d87cb59d02b021723088f9325` also
matched. TS SHA-256 is
`beea8e5fb24477923c883dc0d6dc53652edb37d86feb6c589a292844164ef3a5`.

The decoded start coverage offset was −408 ms and end offset −437.667 ms,
changing by −29.667 ms. The verifier explicitly classifies these as track
coverage-boundary differences; they do not prove source-clock drift or
perceptual A/V synchronization. The stream had no B pictures or HRD timing, so
this 30-minute success validates only the no-reorder POC-2 path. It does not
resolve the earlier HRD/POC candidate-timeline conflict, and it is not a
30-minute B-frame test. The TS, raw capture, process/receiver summaries,
verifier JSON and checksums are retained outside Git at
`live-30m-af82-20261027/` on private NFS.

### Current-source replay of retained B-frame elementary streams — 2026-10-27

The current `MediaSink`, H.264 clock and TS muxer were replayed against the
retained 36.8-second Grid-derived H.264/AAC elementary streams, using the saved
per-message frame/timestamp index. It accepted and emitted 2,209 video pictures
(1,209 B-slices) and 1,704 AAC-LC access units (1,744,896 samples/channel at
48 kHz). FFmpeg/FFprobe 9.0.2 decoded all pictures and samples. The resulting
36.8163-second video track had 967 PTS reorder steps, zero DTS regressions, zero
PTS-before-DTS packets, and zero TS sync, continuity, PCR regression or PSI CRC
errors. Source H.264 and AAC SHA-256 values each matched the complete
corresponding TS PES payload hash. TS SHA-256:
`9622fd5955af2ee82bfa316de0b83a01ba0e65f85f1624131d967a282a54a106`.

This is a current-source offline replay of real Grid-derived elementary
payloads, not a new live B-frame reception. The retained capture's `grid-wire.bin`
is empty; packet boundaries and timestamps are reconstructed from its saved
index. The audio/video decoded coverage starts differ by 30.667 ms and ends
differ by about 495 ms; these are capture-boundary offsets, not evidence of
perceptual sync or source-clock drift. The verifier JSON and replay manifest are
retained outside Git under
`offline-replay-af82-bframe36-20261027/` on private NFS.

### Exact retained HRD/POC failure pair — 2026-10-27

The earlier `live-30m-b3ed-20261027/` failure can be replayed from its two
retained original `0xC748` packets. The frame index was 868372/I at raw Grid
timestamp 169366733334 and 868373/P at 169366903334: a 170,000-unit (17-ms)
Grid interval. Current local H.264 parsing gives POC 0 then POC 2, decode order
I then P, with HRD `cpb_removal_delay` / `dpb_output_delay` pairs 240/18 then
2/2. FFmpeg 9.0.2 `trace_headers` independently reports POC type 2, frame
numbers 0 then 1, I/P slice types, 16-bit CPB removal-delay syntax, CPB values
240/2 and DPB output-delay values 18/2 for these exact access units.

Under the receiver's candidate rule `DTS = Grid timestamp` and
`PTS = DTS + dpb_output_delay * (1/120 s)`, the 90-kHz values are:

| Picture | POC | DTS | DPB offset | Candidate PTS |
|---|---:|---:|---:|---:|
| first I | 0 | 90,000 | 13,500 | 103,500 |
| next P | 2 | 91,530 | 1,500 | 93,030 |

Thus DTS increases, but candidate PTS for POC 2 is 10,470 ticks (116.333 ms)
earlier than POC 0. The precise fail-closed error is `h264_picture_order_conflict`,
raised by the receiver's POC/PTS consistency check. It is not the removed
1-ms Grid/HRD cross-clock guard, and the observed payloads are not thereby
proved corrupt. This proves that this particular assumed transform cannot
simultaneously treat Grid time as CPB-removal/DTS and add the signaled DPB
delay while preserving the signaled picture order.

H.264 defines `cpb_removal_delay` relative to the access unit associated with
the most recent buffering-period SEI and `dpb_output_delay` as the wait after
CPB removal before DPB output. Neither definition maps those values to SOOP's
proprietary Grid timestamp. The standard therefore does not identify which
source clock or mapping is wrong. No arbitrary shift, nominal-FPS replacement
or exception bypass was applied. Until a frame-correlated official-client
timestamp consumer or another independent synchronized media clock is
obtained, this HRD/POC structure remains unsupported by the production
fail-closed mux path. Only these non-identifying numeric facts are recorded;
the two-packet original capture remains private on NFS.

### Diagnostic-only bundle smoke after rejection-capture correction — 2026-10-27

The rebuilt bundle
`bf42ec535870c72f89ee9fba32bfc58f36212cd7c115c53442c1b3afde0190ad` was
loaded as `soop_grid.py` in clean Streamlink 8.6.2 / Python 3.12.3, using
Grid-only mode and a 12-second limit. It ended with exit code 0 and
`duration_limit`; it accepted 756 Grid video messages (755 playable pictures)
and 600 AAC-LC access units / 614,400 samples per channel at 48 kHz. The
POC-2 stream had fixed VUI, no HRD and no B slices. Full FFprobe/FFmpeg 9.0.2
verification passed: 72,096 TS packets, zero sync/continuity/PAT/PMT/PSI-CRC/
PCR/DTS errors, 755 decoded H.264 pictures over 12.5827 seconds and all 600
AAC frames over 12.8 seconds. This validates normal receive behavior after the
diagnostic-only change, not the rejected HRD/POC case.

The private trace contains one last Grid video message at the duration boundary
that was not committed to the TS; the smoke therefore does not claim complete
flush of all input observed at shutdown. Emitted H.264 and AAC PES hashes match
the corresponding output media. The final configured offline run completed
554 tests with no skips. A separate 30-minute no-HRD/no-B capture passed whole
file decode and structural checks, but it does not validate HRD/B-frame timing
or perceptual A/V synchronization.

### Re-attempted official-client timestamp trace — 2026-10-27

The existing SOOPStreamer x64 executable was identified by SHA-256
`98a1741b2368be33e25958a46443760663ff8da225c742aabac16d625297709b`, matching
the identity recorded in the retained REA function dossiers. A fresh REA/Ghidra
session opened that exact PE, but Ghidra failed to complete its required binary
inventory/auto-analysis step (`read_binary_inventory_summary` provider adapter
failure) after several minutes; no new function or value-flow evidence was
returned, and the session was closed cleanly. Repeating the same analysis was
not attempted. The previously retained `FeatureSocketHandler::handlePushFrame`
and Parent cache-request pseudocode documents packet dispatch/request handling,
but does not show a frame-correlated Grid timestamp reaching a decoder PTS/DTS
setter. Thus the official player's timestamp consumer remains **unverified**;
this failed fresh analysis neither confirms nor refutes the existing candidate
time model.

### HRD buffering-period conformance audit — 2026-10-27

The current H.264 audit found a separate parser acceptance gap. ITU-T H.264
(08/2024), Annex D.2.2, states that when either `NalHrdBpPresentFlag` or
`VclHrdBpPresentFlag` is 1, a `buffering_period` SEI shall be present in every
IDR access unit (and every access unit associated with a recovery-point SEI).
The previous synthetic HRD-IDR vectors omitted this mandatory message, while
the parser accepted them. This was a non-conforming test/parser gap; it was not
the source-clock disagreement or the live HRD/POC presentation conflict.

The parser rejects an HRD IDR or recovery-point AU without `buffering_period`,
rejects that SEI when the active SPS signals no HRD, and rejects a zero
`initial_cpb_removal_delay` as forbidden by the standard. Recovery-point payload
syntax is parsed and bounded. Regression vectors cover NAL-HRD and VCL-HRD
independently, required and missing messages, zero delay, and transactional state
rollback. The parser deliberately supports only `pic_struct=0`; valid repeat
structures such as `pic_struct=7` or `8` remain unsupported because the muxer
represents one output picture per access unit. Focused H.264 tests pass (85 passed, 2 skips when the FFmpeg fixture variables
are absent); with FFmpeg/ffprobe configured the full modular suite passes 559
tests with no skips. The final bundled regression passes 551 tests. Current single-file
SHA-256: `cd1d390bd980c58f83a0d19284dac2e2eff7bffb50b433d926260e3dbdd076d5`.

This conformance fix does not resolve `h264_picture_order_conflict` for the
retained adult-stream sequence. No production timing check was weakened. The
30-minute recording described below predates this parser-only validation and
contains no HRD; it is not evidence for HRD/B-frame behavior.

### Exact-final-bundle 30-minute live validation — 2026-10-27

The exact final single-file artifact (`dist/soop_grid.py`, SHA-256
`cd1d390bd980c58f83a0d19284dac2e2eff7bffb50b433d926260e3dbdd076d5`) was
loaded in clean Streamlink 8.6.2 / Python 3.12.3 and used in Grid-only mode for
a bounded public broadcast. The run ended at the requested duration: 1,806.318
seconds, `duration_limit`, process exit 0, no HLS fallback. The receiver saw
POC type 2 with fixed VUI timing, no HRD and no B-slices. It emitted 108,098
video pictures and 84,456 AAC-LC access units (86,482,944 samples/channel at
48 kHz); no frame gaps, identity conflicts, duplicate output, or failover were
reported. Peak RSS was 48,697,344 bytes, peak media buffer 1,768,640 bytes,
three threads and at most 13 file descriptors.

The complete TS verifier passed with no failures. It decoded 108,098 H.264
pictures over 1,801.633667 seconds and all AAC samples over 1,801.728 seconds.
Across 10,062,565 TS packets there were zero sync, continuity, transport,
adaptation, scrambling, duplicate-packet, PSI CRC/structure, PCR-regression,
missing PTS/DTS/duration, nonmonotonic DTS, PTS-before-DTS, or audio-overlap
findings. All 1,802 PAT/PMT observations and 108,098 monotonic PCR samples
passed. The decoded coverage-boundary offset changed by 94.333 ms; this is not
source-clock drift or perceptual A/V-sync evidence.

In this capture, successive video Grid timestamps advanced by either 160,000
or 170,000 100-ns units (16 or 17 ms); the counts were 36,032 and 72,065,
respectively, over a 1,801.617-s raw Grid span. The 3,519 audio Grid messages
each carried 24 AAC-LC access units. At 1,024 samples per AU and 48 kHz, each
batch represents 512 ms; every adjacent audio-message timestamp advanced by
5,120,000 100-ns units, and the final chunk plus its sample duration spans
1,801.728 s. This supports a first-sample/batch-start interpretation for this
audio capture and confirms its chunk clock tracks decoded sample count. It does
not identify a common absolute audio/video epoch or perceptual lip-sync.

The private raw AAC elementary stream and its complete TS audio PES payload
have the same SHA-256. The raw Grid-derived H.264 capture is 6,578 bytes longer
than the output video PES: the output PES is an exact byte-for-byte prefix, and
the retained suffix is one NAL type 1 P-slice after the duration-limited output
boundary. Thus no bytes in the written video prefix were altered; one final
observed picture was outside the chosen duration cutoff. The full recording,
raw capture, verifier JSON, process/receiver telemetry, and redacted manifest
are preserved with owner-restricted permissions in the private NFS run
directory `live-30m-final-cd1d-20261027` (the machine-specific mount path is
omitted here). The TS SHA-256 is
`c60244efe265c38d847344ed6b3eb3ababa3158b859f0f61003b4df2f75859df`.

This is a successful 30-minute validation of the final artifact's **POC-2,
no-HRD, no-B-frame** path only. It does not exercise the failing HRD/POC case,
prove parent recovery, or establish perceptual A/V synchronization. The
retained two-picture HRD sequence still demonstrates that the provisional
`DTS=Grid, PTS=DTS+DPB output delay` mapping reverses signaled POC order. That
format remains fail-closed pending evidence for the Grid-to-HRD clock mapping.
The final artifact also passed a separate 15-second Grid-only live smoke and
full TS/media verification; its evidence is in the private NFS run directory
`live-final-cd1d-smoke-20261027`.

### Independent 30-minute artifact rescan — 2026-10-27

The preserved NFS recording was independently rescanned with FFmpeg/ffprobe
9.0.2. The verifier returned `verdict=pass`, no failures, and a passing
1,790-second minimum-duration gate. It decoded 108,044 H.264 pictures over
1,800.7337 seconds and 84,408 AAC-LC access units (86,433,792 samples/channel
at 48 kHz) over 1,800.704 seconds. FFprobe found no missing PTS/DTS/duration,
non-monotonic DTS, PTS-before-DTS, or malformed rows. The TS scan found
10,057,532 packets with zero sync, continuity, PAT/PMT, PSI CRC/structure,
PCR regression, transport, adaptation, scrambling, or duplicate-packet errors;
108,044 PCR samples advanced monotonically. H.264/AAC PES payload hashes and
the original raw elementary-stream hashes are recorded in the retained
verification JSON.

The machine command wrapper exited nonzero after a typo in its final JSON
summary-printing snippet, but the verifier itself completed and wrote a valid
JSON result with `passed=true`, `failures=[]`, and the recording SHA-256
`beea8e5fb24477923c883dc0d6dc53652edb37d86feb6c589a292844164ef3a5`.
The independent result is preserved at
the private NFS run directory `live-30m-af82-20261027/verification-rerun.json`.
It revalidates the earlier no-HRD/no-B recording; it does not establish the
current build's exact 30-minute execution or perceptual A/V synchronization.


### Independent adversarial review follow-up — 2026-10-27

A second review found that earlier synthetic `buffering_period` payload `0x81` encoded `initial_cpb_removal_delay=0`. H.264 Annex D.2.2 prohibits zero. The parser now rejects that value and test fixtures use a nonzero delay; a negative regression verifies transactional rejection. The review also identified that valid `pic_struct` frame-repeat values 7/8 are currently rejected as unsupported; this is a deliberate format limitation, not malformed syntax. The parser assumes each Grid video payload is one complete supported access unit. Prefix-only SEI in a separate transport chunk is not a supported layout and must not be used as evidence that HRD metadata is preserved across Grid chunks.

### Normative HRD reconstruction and clock-model comparison — 2026-10-27

The older wording that treated `cpb_removal_delay` as a per-adjacent-frame
delta was too loose. ITU-T H.264 (08/2024), Annex C.1.2 and D.2.3, defines
nominal CPB removal relative to the first access unit of the active buffering
period, with a modulo counter; a buffering-period access unit starts a new
reference point. The initial access unit that initializes the HRD uses
`initial_cpb_removal_delay`. Annex C.2.2 derives DPB output time by adding
`dpb_output_delay × clock_tick` to CPB removal time. DPB output order is tied to
the smallest `PicOrderCnt()` among pictures needing output. These fields
describe coded-video timing; they do not define a SOOP Grid-clock conversion.
See [ITU-T H.264 (08/2024), Annex C and D](https://www.itu.int/rec/dologin_pub.asp?id=T-REC-H.264-202408-I%21%21PDF-E&lang=e&type=items).

The retained 36.8-second real Grid-derived B-picture input was re-evaluated
from its indexed access units using those buffering-period rules. It contains
2,209 pictures over 19 IDR epochs. The first buffering-period removal time was
set to an arbitrary zero origin; each later period's first AU was placed using
its CPB delay from the preceding period, and subsequent AUs used their CPB
delay from that period's first AU. This is a relative-clock reconstruction,
not an absolute wall-clock mapping. Across the full sample, the Grid span and
HRD-derived CPB span were both 36.800 seconds. The affine fit had slope
0.999999991 and offset about +0.333 ms; Grid-minus-HRD residuals were within
±0.667 ms, consistent with the observed 16/17-ms Grid quantization versus the
16.667-ms HRD frame interval. Both Grid-DTS and HRD-derived DTS were strictly
increasing. For 2,190 adjacent within-epoch display-order comparisons, both
`DTS=Grid, PTS=DTS+DPB delay` and the independently reconstructed
`DTS=HRD CPB time, PTS=DTS+DPB delay` agreed with POC order. They each showed
967 PTS reorder steps. After per-epoch origin normalization, their PTS values
differed by at most 0.667 ms. FFmpeg/ffprobe 9.0.2 decoded all pictures and
the complete TS verifier passed. This supports a near-equal relative clock
mapping for this particular B-frame capture; the sample has no retained raw
TCP packet capture and cannot establish official SOOP semantics.

The two-picture failing sequence was also evaluated with the normative HRD
reference points. The IDR/BP AU has `dpb_output_delay=18`; the following POC-2
AU has `cpb_removal_delay=2` and `dpb_output_delay=2`, with a tick of
8.333333 ms. Relative to the BP AU, the following picture's signaled output
time is `(2 + 2 - 18) × tick = −116.667 ms` from the IDR's output time, even
though its POC is greater. Using Grid as the decode-time candidate gives the
same contradiction to within the measured 0.333-ms interval residual: Grid
advances 17 ms, while the DPB offsets make the POC-2 candidate 116.333 ms
earlier. The CPB/Grid interval itself is therefore not the cause. FFmpeg
`trace_headers` independently confirms the SEI values and AU sequence. Under
the parsed AU association, the input's HRD output schedule conflicts with its
POC output order; the available two-AU capture cannot determine whether the
originating encoder emitted inconsistent HRD metadata or whether an earlier
upstream AU/SEI association is missing. No timestamp relaxation or replacement
was made, and the production path continues to reject this sequence.

The receiver now retains privacy-safe numeric facts for this rejection:
candidate DTS/PTS, the conflicting picture's POC/PTS, Grid interval, CPB/DPB
delays and the exact HRD tick fraction. No payload, endpoint, ticket or account
data is included. A synthetic regression uses the observed 17-ms Grid interval
and asserts these diagnostics. Focused H.264 and diagnostics tests pass (116
passed, 2 FFmpeg-fixture skips before the full suite rerun).

This evidence supports the following bounded conclusion:

* The historical `h264_hrd_grid_clock_conflict` rule was a receiver-added
  cross-clock equality gate and was not a standard requirement; it remains
  removed. The old 8.333/8.200-ms comparison was an interval comparison, not
  an epoch offset. One 0.133-ms residual cannot establish drift or damage.
* On the retained B-frame sample, Grid and HRD CPB time share nearly identical
  relative progression, but that does not prove the Grid timestamp's official
  meaning or a common audio/video epoch.
* The adult-stream failure is a separate HRD/POC output-order contradiction,
  not evidence that the old 1-ms gate should be widened or restored. The
  current fail-closed result is appropriate until a longer source sequence or
  independent official-client timing evidence resolves the AU association.
* Model D (Grid as transport scheduling only) remains untestable here because
  there is no frame-correlated socket-arrival trace or official decoder clock
  consumer. The 30-minute no-HRD/no-B-frame recording validates that other
  path, not this HRD variation.

After adding the safe conflict facts, the deterministic bundle was regenerated
as `dist/soop_grid.py`, SHA-256
`4708e1efa392a519f9e2757431bd8583b1682efc8e67b034d95e5d0d235fc29b`.
The final offline run passed 559 modular tests and 551 bundled regressions,
with the FFmpeg fixtures enabled (no skips), plus Ruff, format, compileall,
bundle reproducibility, clean Streamlink 8.6.2 loading and `git diff --check`.
The 30-minute live recording above used the preceding artifact hash
`cd1d390b…`; this rebuild changes rejection diagnostics only, not media timing
or mux behavior. No new live run was performed against `4708e1…`, so this
artifact's live execution and the unsupported HRD case remain unverified.

### Follow-up model falsification — 2026-10-27

A bounded fixed-VUI/POC scheduler was prototyped against the retained captures,
then removed from the production path. It sorted decoded pictures by POC and
assigned the nominal VUI cadence while retaining Grid timestamps as DTS. The
B-frame capture decoded completely (2,209 video frames, 1,704 AAC access units;
TS continuity/PCR and DTS checks passed), and the no-B capture also decoded.
Those are codec/transport results only. The candidate used an SPS-derived DPB
ceiling as if it were an actual reorder depth; the ceiling is not an encoder
measurement. The resulting four-frame lead failed the existing synthetic
common-clock regression: the generated video and known audio reference began
66.7 ms apart. This candidate is therefore rejected and is not present in the
current module or generated bundle.

The retained 15.25-second no-B input still fails the production HRD/POC gate at
its second video access unit. The preceding IDR has POC 0, DTS 90,000 and HRD
candidate PTS 103,500; the next POC 2 picture has Grid delta 180,000 in the
100-ns representation, DTS 91,620 and HRD candidate PTS 93,120. POC requires
the second picture to be presented after the IDR, while the parsed HRD values
put it 10,380 ticks earlier. The failure is `h264_picture_order_conflict`,
not the removed Grid/HRD interval-equality check. The original indexed input
and its diagnostic-only decodable remux are retained outside the repository;
the remux is not an accepted recording.

The current production rule remains conservative: Grid supplies the
provisional decode-time candidate; valid HRD picture timing supplies relative
presentation delay; POC order independently checks the result. A fixed-VUI/POC
fallback is not accepted merely because a decoder can play it. In particular,
the retained captures show differing track coverage boundaries (about +242.7
ms in one candidate and −464.7 ms in another); these figures are boundary
coverage differences, not proof of perceptual synchronization or source-clock
drift. The official player's use of Grid timestamps and the common A/V clock
epoch remain unverified.

The full offline suite passes after removing the candidate (559 tests with
FFmpeg fixtures and loopback permission enabled). No new live recording or
30-minute run was performed after this model review: the previously tested
broadcast exposes the unresolved HRD/POC conflict, so a successful media
model has not been established for it. The currently generated artifact is
`dist/soop_grid.py`, SHA-256
`52666cc5435d02c933a6bc2662aa4efdbd38d7b5ecaae8cfddb11fcc73e4a52e`.

### NFS-backed continuation check — 2026-10-27

The existing NFS share was tested with a unique write/read probe and a private
run directory was created (directory mode 0700). `df` reported approximately
1.8 TiB available. The
mount options still display `ro`, and a remount attempt was denied by the
execution environment, but the elevated file operation successfully created,
read back, and persisted the probe and the bounded-test summary. Existing
recordings were not moved or removed.

One current-bundle Grid-only probe requested the adult-access check, a 10-second
duration limit, and a 120 MB wire limit. It exited 1 at live metadata lookup
with `channel_live_number_unavailable`; it wrote no recording or raw capture,
and did not invoke HLS. This is an unavailable/offline source result, not a
media decode result. No further live retry was made.

The retained 30-minute recording was independently inspected again from its
existing NFS result JSON (`passed: true`, TS SHA-256
`c60244efe265c38d847344ed6b3eb3ababa3158b859f0f61003b4df2f75859df`; result
JSON SHA-256
`4e841429db69d4f810ca649ff46992d4d1c7ce9d328b7ac9e3a7034b37ace5f4`). It
remains evidence for the earlier `cd1d390b…` artifact's POC-2, no-HRD,
no-B-frame path only. A fresh rerun with the current verifier reached its
full-file FFmpeg decode phase but was manually interrupted after the existing
complete verification had been located; the interrupted run produced no
result and is not counted as a pass or failure.

The current `52666cc5…` bundle differs from the 30-minute `cd1d390b…` bundle
in diagnostic allowlisted fields, richer rejection facts, and output-accounting
instrumentation. It does not change the H.264 timestamp formula or TS muxer.
That comparison is source evidence, not a substitute for a fresh exact-bundle
30-minute live run. The HRD/POC contradiction and lack of a currently available
live source remain unresolved blockers for that gate.

### Picture Timing clock-timestamp availability — 2026-10-27

FFmpeg 9.0.2 `trace_headers` was run over the retained 15-second HRD/POC
conflict sample and the 36-second HRD B-frame sample. It observed 916 and 2,209
Picture Timing `clock_timestamp_flag` fields respectively; every flag was zero.
The independent parser exited successfully on both elementary streams. These
samples therefore contain no optional Picture Timing wall-clock timestamp that
could independently anchor presentation time or settle the proprietary Grid
clock mapping. This does not prove other SOOP streams omit the field. The
redacted counts are retained in the private NFS workspace as
`sei-clock-timestamp-audit.json`.

This additional observation narrows the available evidence: the B-frame sample
has a self-consistent HRD/POC candidate, while the no-B sample has candidate
HRD output times that invert POC order; neither sample supplies a separate
clock-timestamp anchor. The mismatch remains a conflict between the current
candidate model and the signaled ordering, not proof that the proprietary Grid
timestamp has a particular clock meaning or that the encoded pictures are
corrupt. No production timestamp behavior was changed.

### Rejection of the speculative no-reorder fallback — 2026-10-28

The two-picture `h264_picture_order_conflict` in the retained 15-second
Grid capture was traced to the receiver's prior model, not to the removed
1-ms cross-clock check. The receiver had selected Grid as a provisional DTS,
then added `dpb_output_delay` to make PTS. For the initial IDR (POC 0), the
observed delay was 18 HRD ticks; the next POC-2 P picture had delay 2 ticks.
The resulting candidate placed the higher-POC picture before the IDR. The
available evidence does not establish whether the source encoder's SEI/AU
association is inconsistent or whether an earlier timing context is absent.
It does establish that these two parsed HRD values cannot both define the
observed POC order under that DTS mapping.

An intermediate implementation added a one-picture startup hold and a
`grid_no_reorder_observed` mode after the first IDR/P pair. Adversarial review
found that a finite I/P prefix cannot prove that later pictures will not be
reordered. The SPS reorder limit was not required by that selector, and the
branch replaced candidate DTS and PTS with Grid time despite unresolved clock
semantics. A test using the contradictory initial HRD/POC pair reproduced the
acceptance for SPS reorder values `None`, `0`, and `1`. This was unsafe evidence
and the fallback has been removed.

Current behavior does not reinterpret a contradictory HRD-timed picture using
Grid time. An HRD/POC ordering contradiction fails closed. The no-HRD
`grid_no_reorder` model is available only when the parser can prove the SPS
maximum reorder count is zero. No nominal FPS timestamps are synthesized to
make an unsupported stream pass. A regression test asserts that the
contradictory initial pair is rejected for all three SPS reorder
configurations.

The then-current bundle `e35dc98aced1149bb63325863d1ee92dd353fe3777fc6fb897d626082d03c702`
completed a 15-second public Grid-only live smoke with exit 0 and
`duration_limit`. It decoded 910 video frames and 696 AAC access units; TS
sync/continuity/PCR/DTS checks and full H.264/AAC decode passed. This live
sample had I/P video only, no B slices, and its audio/video coverage-boundary
change was about −319 ms. This does not establish perceptual sync. The capture
and verifier are preserved under the private NFS directory
`live-current-e35dc9-smoke-20261027/`. A 30-minute run on this exact bundle
subsequently completed; see the dated field report for the independent
whole-file result.

The AAC grouping is consistent across the retained B-frame sample and the
current live smoke: 71 and 29 Grid audio messages respectively each contain
24 AAC-LC access units, or 512 ms at 48 kHz with 1,024 samples per AU. Their
Grid timestamp spans are 35.840 s and 14.336 s; adding one final 512-ms batch
gives the observed AAC sample durations of 36.352 s and 14.848 s. In both
captures the measured Grid-chunk versus accumulated-sample residual is zero at
the recorded precision. This supports (but does not prove as a protocol rule)
interpreting each audio message timestamp as the start of its first AAC AU and
advancing later AUs by exact sample count. It also separates packet arrival
time from encoded sample duration.

The video Grid spans are 36.800 s and 15.133 s, so these bounded captures end
with audio coverage shorter by about 448 ms and 285 ms after the final AAC
batch is included. The 15-second TS verifier reports a −318.7-ms change in
track-coverage boundary offset. Because these recordings stop at an arbitrary
wall-clock duration and audio is delivered in 512-ms batches, edge truncation
is a plausible explanation, but the captures do not prove it. The audio and
video timestamps are treated as a common relative media clock in the muxer;
their common epoch and perceptual alignment are still unverified. No correction
is applied by stretching either track, and these values are not presented as
measured long-term source-clock drift.

### 30-minute current-bundle follow-up — 2026-10-27

The exact `e35dc98a…` standalone bundle completed a 1,811.624-second
Grid-only run, terminated by `duration_limit` with process exit 0. The full
recording independently passed TS inspection and H.264/AAC decode. It had
108,016 I/P video frames and 84,384 AAC access units; no B picture, HRD or
Picture Timing SEI was observed. All message frame numbers were unique and
contiguous in capture order; receiver counters reported no gaps or conflicts.
The generated video uses `grid_no_reorder`, which is limited evidence for this
observed no-reorder stream and does not settle the HRD/POC conflict on other
streams.

Grid audio messages each contained 24 AAC-LC access units (1,024 samples each
at 48 kHz), exactly 512 ms by sample count. Receiver chunk/sample clock
residuals were zero at recorded precision. Full decode yielded 86,409,216 PCM
samples/channel (1,800.192 s). The verifier found a −74.7-ms change between
track-coverage boundaries over the recording; this is not a source-clock drift
or perceptual-sync measurement. Full counters, commands, hashes and limits are
in [the field report](LIVE_VALIDATION_30M_20261027.md). This run had one Parent
connection and no recovery event.

### Raw-payload clock-model re-evaluation — 2026-10-27

After temporary NFS validation storage was authorized, the retained 15-second
capture was re-read from its concatenated original Grid payload and frame
index. The payload byte count reconciled exactly. The low-level parser
re-derived all 916 video POCs and all 916 `dpb_output_delay` contributions
recorded in the existing index; it found 8 IDR/buffering-period epochs, all
I/P pictures, and no B picture. This is a fresh calculation from the retained
input using the repository's syntax parsers; it is not a second independent
H.264 parser.

Within IDR epochs, both `DTS=Grid, PTS=DTS+DPB delay` and
`DTS=HRD CPB removal, PTS=DTS+DPB delay` have monotonic DTS but each produces
8 POC/display-order inversions, one at each epoch start. An affine diagnostic
fit of relative HRD CPB time to Grid time has slope 0.9999904, RMS residual
0.893 ms and maximum absolute residual 5.854 ms. Grid intervals range from
13 to 21 ms (median 17 ms), while two HRD ticks are 16.667 ms. This supports
similar relative clock progression in this short capture; it neither makes
the HRD output order valid nor proves the clocks' protocol meaning. Model C
inherits Model B's POC inversions. Using Grid for both PTS and DTS gives zero
order inversions on this I/P-only sample, but remains an empirical
no-reorder compatibility rule and is rejected as soon as a B picture or POC
regression appears.

The same raw-message pass found 31 AAC messages containing 727 AAC-LC access
units at 48 kHz, or 744,448 samples/channel and 15.509333 s. Grid message
timestamps advance by 500 ms; sample-clock residual versus message timestamps
ranges from −12 to +8 ms and ends at +2.667 ms. The first audio coverage starts
379.333 ms before the first video timestamp and ends 121.000 ms before the last
video timestamp. These are finite-capture track-boundary offsets, not a
perceptual-sync measurement or long-term drift. No stretching or timestamp
averaging was applied.

This recalculation does not change production code or the timing model. The
first shell lookup found no `ffmpeg`/`ffprobe` on `PATH`; private FFmpeg 9.0.2
binaries were later found under `/tmp`. A fresh full verifier run on a local
copy was interrupted after a long no-output interval, so it is not counted as
a new full-decode result. The prior successful FFmpeg `trace_headers` and
whole-file decode evidence above remain tied to their recorded runs. A
numeric-only copy of this re-evaluation is preserved beside the private NFS
capture as `model-comparison-independent-20261027.json` (SHA-256
`1275b81b4e90dcd66952fa7ce8e41882738ea86015d3bbd31bd18554347542cc`).

### Large-file verifier scan efficiency — 2026-10-27

The structural scanner previously called `read(188)` once per transport
packet. It now reads bounded 4,096-packet blocks while carrying incomplete
packet bytes across short reads; packet validation, deadlines, file hashing,
and tail rejection are unchanged. A new short-read/boundary regression passes.
On a byte-identical local copy of the 30-minute TS, the revised scanner
processed 10,058,300 packets in 50.651 seconds. It reproduced the file SHA-256
`e9536506134230d856e967e85bed1d9ccfb066718a59c6294e798199620056d4`, both
elementary PES payload SHA-256 values, and zero sync, continuity, PCR, PSI CRC,
PSI structure, adaptation, transport, or PES framing errors. This is a fresh
structural scan only; the prior full FFmpeg decode result remains the full-file
decode evidence. The numeric-only structural rescan is preserved beside the
private NFS recording as `structural-scan-rerun-20261027.json` (SHA-256
`13f69f13cd1e277d98f8ee3d9633586b3f435b4c31106da17398fd75602dfdde`).

After this verifier change, the complete modular suite passed 563 tests and
the bundled regression suite passed 555 tests. Ruff check and format, Python
compile validation, deterministic bundle check, checksum verification, and
`git diff --check` also passed. A fresh full-recording verifier run was
attempted both against the NFS original and an identical temporary local copy,
but each was interrupted after a prolonged no-output interval. These attempts
are not counted as full-verifier passes; the scanner-only result above and the
previously recorded full decode remain separate evidence. The temporary local
copy was removed, the original NFS recording and numeric reports were
preserved, and no existing tests or recordings were deleted.

### Fresh complete 30-minute verifier rerun — 2026-10-27

The same verifier was rerun with a 3,600-second total deadline and an explicit
1,800-second decoded A/V minimum. It completed in 780.0 seconds with exit 0,
`passed=true`, and no failures. The input TS SHA-256 remained
`e9536506134230d856e967e85bed1d9ccfb066718a59c6294e798199620056d4`; the
standalone plugin bundle was `e35dc98aced1149bb63325863d1ee92dd353fe3777fc6fb897d626082d03c702`.

The verifier decoded 108,016 H.264 pictures over 1,800.267 s and 84,384 AAC
access units to 86,409,216 PCM samples per channel at 48 kHz (1,800.192 s).
All packets had PTS, DTS, and duration; packet DTS regressions, PTS-before-DTS,
PTS overlap, TS sync/continuity errors, PCR regressions, transport/adaptation
errors, PSI CRC/structure errors, duplicate packets, malformed PES, and FFmpeg
diagnostic lines were all zero. PAT and PMT were each observed 1,801 times;
108,016 PCR samples were monotonic, with a maximum consecutive value delta of
17 ms. The 1,800-second decoded A/V duration gate passed.

The audio/video coverage-boundary offset changed by −74.667 ms across the
recording. This verifier explicitly classifies that value as a boundary
coverage comparison, not source-clock drift or perceptual A/V synchronization.
The capture contains no B pictures or HRD/SEI timing, so this fresh full-file
pass confirms only the observed no-reorder stream. The sanitized JSON result is
preserved beside the private recording as
`fresh-full-verification-20261027.json`, SHA-256
`ee7dfaa4ba1f756a67bc28e2b3259eeef4100f3865fac53f4ba23c11edae6dd2`.

### Fresh full verification of retained B-frame replay — 2026-10-27

The 76,866,432-byte replay TS was re-run through the current offline verifier
with a 36-second decoded-duration gate. It passed in 25.04 seconds (exit 0):
408,864 TS packets, zero sync/continuity/PCR/PSI errors, 2,209 decoded H.264
pictures over 36.8163 s, and 1,704 AAC-LC AUs yielding 1,744,896 PCM samples
per channel over 36.352 s. FFprobe found 967 video PTS reorder steps, with zero
DTS regressions, PTS-before-DTS packets, or overlaps. The source H.264/AAC
payload hashes remained equal to extracted TS PES hashes.

The audio/video end-coverage boundary delta changed by −464.333 ms; this is a
short capture boundary comparison, not measured clock drift or perceptual
synchronization. This is a fresh verification of an offline replay from a
retained Grid-derived capture, not a new live reception. Its sanitized JSON is
preserved beside the replay as `fresh-full-verification-20261027.json`, SHA-256
`6f0b890cca7379296c321f3a373e56ef2852f25edb3ca561cc31fc0bd1437ebb`.

### Independent HLS/Grid content-time cross-check — 2026-10-27

The retained HLS reference and the long Grid recording share 562 uniquely
matching AAC access-unit payload hashes. The comparison uses the **MPEG-TS
packet PTS values** for those matching AAC payloads, not raw Grid message
timestamps. Its Grid-output-PTS minus HLS-output-PTS offset is approximately
−723.000989 s, with an 11 μs range; the two matched PTS spans are both 11.968 s.
This shows same-rate timing for the matched AAC output with a different file
epoch. It does not directly establish the semantics of the source Grid clock.

To refine the video comparison, both files were independently decoded by
FFmpeg, scaled to 160×90 grayscale, and compared at 305 motion-selected frame
positions across the 12-second HLS sample. The minimum-error content alignment
was Grid-minus-HLS −722.984322 s; the best candidate at least 100 ms away had
1.1053 times the mean absolute pixel error. The search advances in 1/60-second
steps, so the selected video offset is quantized to one 60-fps frame. The
reported 10.7 μs residual is only the nearest-frame quantization residual, not
an estimate of alignment accuracy.

The audio and video offset estimates differ by 16.667 ms, exactly the video
search step. That difference could be a real one-frame track offset or search
quantization; this method cannot distinguish them. This is limited
cross-transport content correlation, not an official-player trace or a
perceptual lip-sync measurement. It supports that output timestamps progress
on a media-related clock with a stable file-epoch offset in this no-reorder
sample. Because PTS and DTS coincide for this stream, it cannot identify which
one the source Grid timestamp represents for reordered video. It does not
resolve the HRD/B-frame conflict or justify shifting either track.

The numeric-only report is preserved on the authorized NFS at
`private validation archive/video-reference-comparison-refined-20261027.json`
(SHA-256 `0a5e24f56f5a2ec3f14b7bf03be3af5d1822b16a45aadfbe87dee6fed511960d`).
