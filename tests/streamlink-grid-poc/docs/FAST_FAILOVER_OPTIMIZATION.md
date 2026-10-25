# Fast Failover and Cache Recovery Optimization

> This optimization report records the implementation-era baseline. Its live
> failover status predates the controlled live socket-close validation documented
> in [LIVE_FAILOVER_VALIDATION.md](LIVE_FAILOVER_VALIDATION.md): three sequential
> client-side Parent closes recovered through Center rediscovery and exact Cache
> overlap. Natural remote Parent failure and multi-hour operation remain unverified.

Status: experimental implementation; synthetic recovery benchmark and a short live
capture completed. No new live Parent outage was observed in this run.

## 1. Existing Recovery Architecture

The receiver connects to a Center-issued Parent candidate and performs the existing
`CB2E` / `CB2F` / `CB30` flow. On a transport error, it closes that socket and tries
the remaining candidates returned for the same Center session. If that finite list
is exhausted, it performs at most one fresh Center discovery for the outage. That
discovery returns a new Node Key together with its Parent candidates; the old key is
not carried into the new session. The per-outage connection-attempt bound remains
eight and resets only after a frame has passed continuity checks and has been
committed to the sink. One monotonic capture deadline covers connect, discovery,
recovery, and media receive.

During recovery the receiver requests from the last committed Frame Number, not the
next number, so the response must contain an overlap anchor. It verifies the anchor's
Frame Number, Kind, raw Timestamp, and SHA-256 of the original media Payload. The
anchor is excluded from output; later frames must be sequential. Missing overlap,
identity conflict, timestamp regression, or an unrecoverable sequence gap is an
error. The existing MPEG-TS muxer is retained across a verified overlap.

The receiver does not know a reliable server-side live-edge marker. `C742` causes a
`CB2B` request, now reported as `live_push_transition_requested`; this does not prove
that cache catch-up ended or that live reception resumed. No catch-up-complete or
live-resumed event is emitted. `catchup_state=unverified_live_boundary` is explicit.

## 2. Baseline Telemetry

The task supplied a prior live result: about 600.486 seconds of Grid media over
about 601.404 seconds of process runtime, ending at the configured duration limit
with exit code 0. That recording reportedly experienced one Parent TCP EOF, then one
Center rediscovery, a new Node Key, Parent reconnect, exact Cache overlap, and
duplicate exclusion, with no reported gap. Endpoint identity was not preserved, so
this does not establish whether the reconnect reached the same physical Parent. This
is prior supplied evidence, not a live run repeated during the present change.

The checked-in earlier field report documents shorter captures and had no live
failover. It is not used to claim that this version has repeated the 10-minute
recovery.

## 3. Identified Bottlenecks

The previous implementation waited a fixed 100 ms before every failover attempt,
including when another already-issued candidate was immediately available. That
delay did not improve candidate validity, handshake correctness, or overlap proof.
The implementation now tries those finite, server-issued candidates immediately.
It still uses the existing bounded candidate list, attempt limit, Center rediscovery
limit, cancellation, and global duration deadline.

The read path also had two competing idle thresholds: a 3-second frame read timeout
and a nominal 6-second media stall check. A silent socket therefore failed at the
shorter timeout, while control-only traffic could defer the stall check. The timeout
is now explicitly tied to a 3-second no-media threshold and shrinks with the elapsed
media-idle time. EOF and reset still fail immediately. A timeout with a partial
packet closes that connection; partial bytes are never reused as a valid frame.
This is not a claim that a 3-second silence can never occur on a healthy broadcast;
the threshold preserves the existing transport timeout rather than introducing a
more aggressive value.

## 4. Changes Implemented

Recovery now emits fixed, secret-free events for failure detection, candidate
selection, rediscovery start/completion, new Node Key acquisition, TCP connect,
handshake, Cache request/first media, overlap verification, first new committed
frame, recovery success/failure, and Live Push transition request. Opt-in JSONL
diagnostics use monotonic elapsed time, numeric counters, periodic heartbeat, flush,
and the existing atomic summary. They do not record endpoint, credentials, raw
exception text, or media bytes.

Measured fields include failure detection, candidate selection, Center discovery,
TCP connect, Parent handshake, first Cache media, overlap verification, first new
frame, total recovery, received Cache frames, committed recovery frames, overlap
duplicates, missing frames, identity conflicts, recovery wire/media bytes, and
connection/discovery counts. Recovery wire/media and frame counts are cumulative
over this capture; phase timings describe the latest observed phase/recovery.
Catch-up duration and throughput remain absent because a verified live boundary is
not observable.

Parent candidates parsed from one response are deduplicated by endpoint and Quality.
Candidate/Node Key pairing remains scoped to the Center discovery that returned
them. No endpoint is guessed or scanned.

## 5. Parent Candidate Reuse Policy

1. Try remaining candidates from the current valid Center response, in server order.
2. Reject a candidate whose Quality differs from the established stream.
3. Only after the list is exhausted, perform one bounded Center rediscovery.
4. Use that discovery's newly returned Node Key only with its own candidates.
5. Stop after the existing attempt budget, explicit access denial, invalid protocol
   data, cancellation, or global duration deadline.

No Warm Standby is enabled. Prior protocol research found that two connections to
the same endpoint duplicate full media traffic; it is not an independent failure
domain. No distinct alternate Parent has been established from retained endpoint
evidence. `WARM_STANDBY: NOT_FEASIBLE` for the current implementation because a
quiet authenticated standby has not been shown to exist; a full duplicate stream is
not treated as standby.

## 6. Timeout and Stall Detection Policy

* TCP EOF/reset and write failure are handled immediately as transport failures.
* A Parent read with no media reaches the existing 3-second timeout and is treated
  as a stall/retry candidate; repeated control frames cannot keep a no-media
  connection alive beyond that threshold.
* Malformed frames, server denial, media identity/timestamp errors, and cache
  continuity failures are terminal; they are not retried as ordinary transport
  failures.
* Connect and read operations remain bounded by the single capture deadline.
* Cancellation closes registered sockets and interrupts waits.
* Connection attempts are limited to eight per outage, with at most one Center
  rediscovery per outage. Successful committed media resets the outage budget.

## 7. Cache Overlap and Catch-up

The overlap identity check and duplicate exclusion are unchanged. Recovery counters
now count observed C748 frames, frames accepted after overlap while continuity is
being committed, discarded overlap duplicates, missing Frame Numbers, and identity
conflicts. A successful recovery is recorded only after the first contiguous new
frame has been accepted and written by the sink.

That event measures time to the first committed post-overlap frame, not time to the
Live edge. The protocol behavior available to this receiver does not expose a
verified finite cache end or a proven cache-to-live boundary. Therefore
`cache_catchup_completed`, `live_receiving_resumed`, catch-up duration, and catch-up
throughput are intentionally not reported as successful facts. If the overlap is
absent or inconsistent, the receiver fails rather than skipping the gap.

## 8. Warm Standby Feasibility

Not implemented. The available experiments show same-endpoint concurrent sockets
can duplicate the full stream, but do not demonstrate an authenticated idle Parent
socket that can later begin Cache delivery without a new handshake or extra media
traffic. The prior successful failover used rediscovery and a new Node Key. There is
no evidence that keeping a second full-stream connection open improves total
recovery enough to justify doubled network traffic.

## 9. Fault Injection Results

The full Python suite passed, including loopback Parent framing/reconnect and TS
decode tests, plus synthetic EOF, reset, timeout, partial-packet reset, missing
overlap, sequence gap, timestamp regression, identity conflict, candidate
exhaustion, new Node Key, repeated recoveries, cancellation, deadline, and
control-only stall cases. The recovery suite also exercises bounded StreamIO
backpressure and worker/socket cleanup. One optional FFmpeg test is skipped if the
binary is unavailable; this execution environment has no `ffmpeg` or `ffprobe` on
PATH.

The run observed 187 passed and 4 skipped across the complete Python test suite.
Loopback socket tests required an allowed local test execution; the ordinary
restricted shell had denied socket creation. The escalated test run used only local
test sockets.

## 10. Baseline vs Optimized Benchmarks

`tools/benchmark_recovery.py --samples 30` replays the same in-process synthetic
two-candidate recovery. The baseline injects the exact former fixed 100 ms
pre-candidate wait; the optimized path uses the current immediate candidate reuse.
No sockets or SOOP traffic are used.

| Metric | Baseline | Optimized |
|---|---:|---:|
| Recovery latency median | 100.331 ms | 0.115 ms |
| Recovery latency P95 | 100.883 ms | 0.136 ms |
| Recovery latency maximum | 102.461 ms | 0.137 ms |
| Sample count | 30 | 30 |
| Recovered without a gap | 30/30 | 30/30 |

This isolates the removed fixed wait; it does not estimate SOOP RTT, Center
rediscovery, server Cache speed, or real-world P95. Actual network recovery time is
not claimed to have improved by the same amount in all conditions.

## 11. Live Validation Results

The regenerated standalone plugin was loaded by the existing isolated Streamlink
8.6.2 environment. A normal public listing candidate was selected by the bounded
field supervisor, which independently checked exact Live metadata and Watch access.
A 10-second capture with a 30 MiB wire limit ended at `duration_limit`, process exit
0, with one Parent connection, 720 C748 packets, 10,492,709 media Payload bytes,
10,553,909 receiver wire bytes, 698 video frames, 22 audio chunks, and no observed
recovery, gap, duplicate, or rediscovery. Peak RSS was 52,019,200 bytes and the
buffer peak was 234,436 bytes. This was a fresh short capture, not a failover test.

The TS scanner found 57,515 packets, zero sync errors, zero continuity errors, 12
PATs, 12 PMTs, 698 PCR samples, zero PCR regressions, and zero explicit
discontinuities. It could not run ffprobe/FFmpeg media decode because those binaries
are unavailable. This short capture does not revalidate B-frame decoding, A/V sync,
or failover under live conditions.

## 12. Known Limitations

* A live recovery was not observed during the new 10-second capture.
* The supplied earlier 10-minute recovery is not independently repeated here, and
  its physical Parent endpoint identity was not retained.
* Cache catch-up completion/live-edge detection is unknown.
* Distinct-Parent failover is not established.
* Cache overlap only proves the exact anchor and subsequent observed sequential
  frames; it does not prove that every outage duration fits within Parent Cache.
* FFmpeg/ffprobe full decode of this live capture was not possible in this runtime.
* Long-duration endurance was not repeated; this task does not claim new
  multi-hour stability evidence.

## 13. Remaining Risks

The 3-second no-media timeout can trigger reconnection during an unusually long
healthy media pause. Conversely, server-side Cache eviction may make recovery
impossible regardless of client speed. The retry budget is bounded but serial
connect attempts can still consume time when multiple candidates are unreachable.
The receiver terminates on any continuity condition it cannot prove; it does not
fabricate, conceal, or mark missing media as recovered.

## 14. Final Recommendation

Keep single-Parent reception and bounded failover as the default. Use the new
diagnostics for the next naturally occurring outage and compare endpoint identity
only through an ephemeral keyed identifier. Do not enable Warm Standby or
multi-Parent aggregation until a server-issued independent candidate and a measured
benefit are established. Treat Cache catch-up as unverified past the first
post-overlap committed frame.

## Final Status Matrix

| Item | Status | Evidence |
|---|---|---|
| `SINGLE_ACTIVE_RECORDING` | VERIFIED | New 10-second live recording; prior 10-minute result supplied as historical evidence. |
| `TCP_EOF_RECOVERY` | VERIFIED | Prior 10-minute live recovery supplied; loopback tests pass. |
| `TCP_RESET_RECOVERY` | VERIFIED | Synthetic reset and partial-packet reset tests pass. |
| `SILENT_STALL_RECOVERY` | VERIFIED | Synthetic no-media/control-only timeout test passes at 3 seconds. |
| `CENTER_REDISCOVERY` | VERIFIED | Synthetic and prior supplied live path; one bounded discovery. |
| `NEW_NODE_KEY_RECOVERY` | VERIFIED | Synthetic test checks rediscovery's new key is used. |
| `CACHE_OVERLAP` | VERIFIED | Synthetic exact identity/hash check; prior supplied live overlap. |
| `MULTIPLE_CONSECUTIVE_RECOVERIES` | VERIFIED | Synthetic sequence covers 11 consecutive recoveries. |
| `ZERO_UNVERIFIED_FRAME_GAPS` | VERIFIED | New short live TS had zero continuity errors; prior supplied 10-minute run reported zero gaps. Not a new live-outage test. |
| `CACHE_CATCHUP` | INCONCLUSIVE | No verified live-edge signal; only first post-overlap commit is timed. |
| `WARM_STANDBY` | NOT_TESTED | Not implemented; no quiet standby protocol evidence. |
| `DISTINCT_PARENT_FAILOVER` | INCONCLUSIVE | Prior live endpoint identity was not retained. |
| `BASELINE_MEDIAN_RECOVERY_MS` | VERIFIED | 100.331 ms, synthetic exact prior-delay model, n=30. |
| `OPTIMIZED_MEDIAN_RECOVERY_MS` | VERIFIED | 0.115 ms, synthetic same response model, n=30. |
| `BASELINE_P95_RECOVERY_MS` | VERIFIED | 100.883 ms, synthetic, n=30. |
| `OPTIMIZED_P95_RECOVERY_MS` | VERIFIED | 0.136 ms, synthetic, n=30. |
| `RECOVERY_SUCCESS_RATE` | VERIFIED | 30/30 in each synthetic benchmark arm; not a live success rate. |
| `PEAK_MEMORY` | VERIFIED | 52,019,200 bytes for the 10-second live capture. |
| `TEST_RESULTS` | VERIFIED | 187 passed, 4 skipped. |
| `LIVE_RESULTS` | VERIFIED | 10-second public capture; no natural failure occurred. |
