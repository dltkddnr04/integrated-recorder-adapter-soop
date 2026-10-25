# Multi-Parent identity and selective-frame feasibility

**Observed:** 2026-10-25 UTC, one explicitly public live target selected from at most
eight listing entries. The receiver independently verified the exact channel/BNO and
received a normal Watch ticket. The target identifier, Parent address, credentials,
frame hashes, and raw media are kept out of this report. The bounded raw summaries
and per-frame hashes remain in private temporary directories outside the repository.

This is research evidence, not an Active-Active implementation. The ordinary
Streamlink receiver, Go PoC, production Adapter, and Integrated Recorder were not
changed. No Git commit or push was made.

## Experiment A — simultaneous Parent frame identity

The server's Center response contained one usable Parent endpoint at the selected
quality. The probe opened two independent TCP sockets to that **same** server-issued
endpoint, using the same Center session and Node Key generation. This is explicitly
`SAME_ENDPOINT_TWO_CONNECTIONS`; it is not evidence about two distinct Parents or
independent failure domains.

Both sockets completed the normal handshake and received `0xC748` concurrently for
20.005 seconds. Each received 1,363 media packets and 19,602,817 raw payload bytes.
Both observed the same contiguous frame-number interval, 1,363/1,363 Frame Numbers
matched, and all compared fields matched: frame kind, raw timestamp, payload length,
and SHA-256 of the unmodified media payload. There were no conflicts, incomparable
frames, or missing numbers in the shared interval. The sample included 43 matching
audio chunks and 1,320 matching video chunks.

| Measurement | Socket A | Socket B |
|---|---:|---:|
| Received media packets | 1,363 | 1,363 |
| Raw media payload | 19,602,817 B | 19,602,817 B |
| Received Parent application wire | 19,719,765 B | 19,719,765 B |
| Sent Parent application wire | 96 B | 96 B |
| Frame identity matches | 1,363 | 1,363 |
| Identity conflicts / missing | 0 | 0 |

The absolute arrival-time difference between matching frames had median 1.08 ms,
p95 30.88 ms, and maximum 59.52 ms. These are two connections to one endpoint; the
timing does not measure cross-Parent skew.

The two sockets transferred 39,439,722 application-level bytes combined (including
Parent framing and the small client requests). A single socket transferred
19,719,861 application-level bytes. Thus two sockets to the same endpoint duplicated
the full observed stream, approximately doubling network transfer. TCP/IP headers
and connection setup bytes are not included.

### Experiment A verdicts

```text
CONCURRENT_CONNECTION: VERIFIED (same endpoint, two sockets)
CONCURRENT_MEDIA: VERIFIED (same endpoint; both sent 0xC748)
FRAME_NUMBER_COMPATIBILITY: VERIFIED (for this endpoint/session/quality sample)
PAYLOAD_HASH_IDENTITY: VERIFIED (1,363 exact frame identities)
DISTINCT_PARENT_IDENTITY: NOT_AVAILABLE (Center supplied one endpoint)
```

This verifies duplicate delivery by concurrent connections to one endpoint. It does
not establish that different Parent nodes share a frame-number space or payloads.

## Experiment B — Cache start and finite-range control

### Existing protocol evidence

The current Python and Go receivers send a 16-byte `0xCB30` body with a quality word,
a 64-bit frame value, and a final 32-bit state word. Existing code sends the first
state word as `0xffffffff` and later uses `6`; those values are retained as observed
client behavior, not assigned a new meaning here. The current candidate's
`sendReqCacheDataVer2` at `0x1402f6050` emits a 16-byte body after the cache-header
handler at `0x1402f32e0`; retained static analysis says its data is session/state
derived and does not establish an end-frame field. The candidate binary's provenance
is not verified, so these static observations are qualified evidence.

The observed `0xCB30` body has one explicit 64-bit position-like field. The retained
source/research contains no verified end-frame, range-length, stop-cache, or
cancel-cache field/command. The final word is not modified or treated as a length.
No undocumented values were fuzzed.

### Bounded live start-position test

An initial request used the Center entry's `cur_frame_no + 1`; that entry reported
zero, so starts 1 and 6 were stale relative to the live stream and are not used to
claim start control. A corrected short test first observed a live Frame Number, then
requested that observed number plus five on a fresh socket to the same issued
endpoint and same session. The requested frame was about two seconds old but remained
within the server's cache. The first returned frame was exactly the requested number;
zero earlier frames arrived. The same result was repeated in a second short run.

| Trial | Requested start | First returned | Frames received | Earlier than start | Received app wire | Sent app wire | First media delay |
|---|---:|---:|---:|---:|---:|---:|
| Initial stale baseline (not start-control evidence) | 1 | 804,027 | 175 | 0 | 2,689,516 B | 80 B | 62.87 ms |
| Known-frame trial 1 | 804,032 | 804,032 | 483 | 0 | 6,675,295 B | 80 B | not recorded |
| Known-frame trial 2 | 808,369 | 808,369 | 367 | 0 | 5,015,912 B | 80 B | 19.05 ms |

The stale baseline row's latency is a connection/request measurement only; its start
value was not a valid current Frame ID. The known-frame requests both returned the
requested first frame exactly. In the second known-frame trial, first media arrived
19.05 ms after `0xCB30`. The output then continued through 483 and 367 consecutive
frames for the bounded observation windows (5 seconds and 2 seconds). The client
ended each trial at its own time/read deadline; the server did not provide an
observed finite-range completion marker in those windows.

Each trial saw one `0xC747` and no second cache-header request. The probe therefore
did not send an unsolicited repeated `0xCB30`. It also deliberately did not send
`0xCB2B`, yet received `0xC748` media after the cache request and observed `0xC742`
notifications. This confirms media delivery without a client `0xCB2B` in the tested
flow, but does not identify whether the server considers those packets a finite cache
batch, an implicit transition, or another mode. Other control commands were recorded
by number only and are not semantically named here.

### Experiment B verdicts

```text
CACHE_START_FRAME_CONTROL: VERIFIED (recent cached start was honored)
CACHE_END_FRAME_CONTROL: NOT_VERIFIED (no known end field/command)
CACHE_REQUEST_LENGTH_CONTROL: NOT_VERIFIED (opaque state word unchanged)
CACHE_ONLY_MODE: INCONCLUSIVE (0xC748 arrived without 0xCB2B; mode semantics unresolved)
REPEATED_CACHE_REQUEST: NOT_OBSERVED (no second 0xC747 invitation)
LIVE_PUSH_SUPPRESSION: INCONCLUSIVE (no 0xCB2B sent; 0xC742 and continued media observed)
SELECTIVE_FRAME_DELIVERY: PARTIAL (start boundary honored; finite range not shown)
TRUE_NETWORK_STRIPING: NOT_VERIFIED
```

The start-position result shows the server omitted earlier frames for a valid recent
start value. It does **not** show that the receiver can stop after a chosen last
frame. A short client-side socket close can bound bytes, but is not proof of a
server-side range request and would require reconnection overhead.

## Combined feasibility

```text
FEASIBILITY_RESULT: INCONCLUSIVE for cross-Parent striping;
                    SAME_ENDPOINT duplicate delivery and start positioning verified
```

The evidence establishes:

1. Two simultaneous sockets to one issued endpoint can each receive the same stream.
2. On that endpoint, identical Frame Numbers carried identical raw payload hashes,
   kind, timestamp, and length for 1,363 consecutive samples.
3. A recent `0xCB30` start position was honored exactly in two short trials; one
   instrumented trial measured 19.05 ms to its first returned frame.
4. No finite end/range-length control or server-side stop was demonstrated.
5. Two simultaneous sockets duplicated the whole observed stream and doubled the
   application transfer. No network-saving result was demonstrated.
6. Center provided no second distinct Parent, so cross-node identity and independent
   failure-domain behavior remain unverified.

Accordingly, the desired combination—choose disjoint finite frame ranges from
different Parents while keeping total network transfer near one stream—has **not**
been established. With the evidence currently available, do not implement a range
scheduler or Active-Active aggregator.

## Next evidence needed

1. On a later authorized public test, obtain two distinct same-quality endpoints from
   a normal Center response before testing cross-node frame identity.
2. Analyze the exact current client's `0xCB30` state transform and surrounding
   handlers against a provenance-verified binary or observed native-client traffic;
   do not vary the opaque state word speculatively.
3. Find a normal, documented control path for a finite end or cache stop. If none
   exists, test whether a bounded client close plus reconnect can achieve useful
   chunks, including its actual duplicate bytes and latency, before considering
   striping.
4. Preserve the current single-Parent receiver and failover behavior meanwhile.

All live probes were capped at two Parent sockets, per-trial time limits, and a
32 MiB application-wire/payload bound in the Cache harness. Only the first-page
public listing sample was used; the exact target was not written to these artifacts.
