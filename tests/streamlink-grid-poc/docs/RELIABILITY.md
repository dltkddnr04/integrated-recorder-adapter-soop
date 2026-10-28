# Reliability policy and supported recovery

## Exact-target anonymous bootstrap

No live-list request is needed. The requested channel/BNO is checked against direct
Live metadata; channel-only URLs resolve one current BNO from the first-party page
and then pin it. BJID/BNO mismatch, offline state, unknown password status, explicit
paid/subscription restrictions, invalid metadata, or Watch denial fail closed. A
normal server-issued ticket is required. Live metadata does not expose every policy
field, so lack of a known restriction flag is not itself authorization. No target
switch, ticket synthesis, or access-control bypass is performed.

## Single active Parent and failover

The receiver keeps the Center-provided candidates and connects to one at a time.
Connect/read/write failure, EOF/reset, read timeout, or three seconds without media
is treated as transient. Explicit negative replies, malformed framing, changed
quality, conflicting frame identity, and unprovable continuity are terminal.

The receiver tries at most eight connections during a single no-progress outage and
one Center rediscovery. Rediscovery creates a fresh Center session and uses its newly
issued node key. Retry/backoff is cancellable; a user duration applies as one deadline
across Parent connect, handshake, backoff, and rediscovery and never resets on
failover. HTTP/Gateway bootstrap precedes that media-capture budget and has separate
bounded operation timeouts. Credentials are not retained for automatic login or
certificate renewal; start a new authorized Streamlink session after expiry.

There is no multi-Parent active-active connection or aggregator. Concurrent Parent
support, node-key sharing, and cross-Parent frame-sequence identity are not proven by
the available protocol evidence.

## Cache overlap and gaps

After an outage, the next Parent is asked for the last accepted frame. Recovery is
accepted only when that exact overlap matches frame number, type, timestamp and
payload hash; subsequent frames must be consecutive and each elementary stream's
timestamp must not regress. Raw received bytes retain duplicates in arrival order,
while duplicates are excluded from H.264/AAC and TS output. The single muxer and
clock origin survive proven overlap.

Cache overlap was verified in a controlled live test that closed the client-owned
Parent socket; this does not simulate a remote Parent's natural failure. If the overlap is absent,
cache ends, numbering resets, quality changes, timestamp regresses, or identity
conflicts, the receiver reports the gap and stops. It does not invent frames or
restart at an unverified IDR boundary. The resulting partial file is not described
as complete.

## Recording budgets and memory

By default there is no cumulative capture duration or lifetime wire-byte cap. Set
`--soop-grid-duration` and/or `--soop-grid-max-bytes` for explicit limits. The
duration clock starts when Parent media reception starts (after HTTP/Gateway/Center
bootstrap) and includes Parent-side recovery overhead. `parent_packets` is cumulative
for diagnostics, but the 8192 control/no-media limit is a no-progress window that
resets after accepted media; it cannot cap a long healthy recording. Cache requests
are similarly bounded per no-progress interval.

Memory does not grow with recording duration: StreamIO is bounded by Streamlink's
ring buffer (clamped to 188 bytes–16 MiB), each protocol frame is bounded, and the
continuity identity window retains at most 128 hashes. No complete recording is
accumulated in RAM. OS/file-output buffering remains controlled by Streamlink.

The implementation does not automatically reauthenticate, detect every broadcast
end status, move to a newly started BNO, or resume a partial file after process crash.
A BNO change is not joined as continuation. Network/session expiry is surfaced as an
error; the user may start a fresh session.

## H.264 and AAC timing

POC 0/1/2 tracks picture order only. The current supported mapping derives DTS
from observed Grid time and, when HRD is present, PTS from Picture Timing SEI DPB
output delay. Fixed VUI timing alone does not map arbitrary POC distances to time.
Without HRD, only constrained POC-2 or zero-reorder input may use PTS=DTS. Conflicts
fail closed; missing evidence is not replaced by estimated FPS. Audio uses the same
clock origin with no arbitrary reorder lead. AAC access-unit/sample counts and
clock residuals are tracked independently of Grid message count.

PES/PCR serialization wraps by specification while internal timestamps remain
unwrapped. Bounded startup retains early audio until valid SPS/PPS/IDR; unsupported
parameter changes, timestamp regressions, and unprovable gaps terminate recording.
See [H264_TIMING_DIAGNOSIS.md](H264_TIMING_DIAGNOSIS.md) for exact support boundaries
and [COMPREHENSIVE_HARDENING.md](COMPREHENSIVE_HARDENING.md) for current verification.
Perceptual A/V sync requires independent evidence beyond a decodable TS.
