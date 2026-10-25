# Bootstrap and bounded Parent recovery

The modular Python implementation is canonical. These changes do not touch the Go
PoC, production adapter, or Streamlink core.

## Exact public target

No broadcast listing is requested by bootstrap. The channel/BNO supplied by the
user is validated; channel-only URLs resolve `window.nBroadNo` from the first-party
page once. `type=live` must report RESULT=1, an exact BJID/BNO match, and a recognized
BPWD value. A different BNO is an error, never an automatic channel switch.
Anonymous password access is rejected. Explicit paid/subscription restriction flags,
if present, are rejected too. Malformed/missing required metadata fails closed.

A live first-party schema inspection on 2026-10-25 confirmed BPWD, BJID and BNO but
**no verified equivalent of listing.subscription_only**. Absence of such a field is
not proof of unrestricted access. The normal anonymous Watch endpoint must explicitly
issue a valid ticket for the exact requested target; its denial is final. Optional
returned target identifiers are also checked. No ticket is synthesized. This is
server-authorized exact-target admission, not equivalent client-side reconstruction
of every listing policy. Account TK/FTK, adult checks and Center-only password
handling retain their existing paths.

## State machine

`issued candidates -> connect -> normal handshake -> cache -> live`

Transport connect/write/read failure, EOF/reset, timeout, or six seconds of control
traffic without media closes the socket and tries the next Center-issued candidate.
Malformed headers/payload, explicit negative replies, changed stream quality,
conflicting frame identity, or unprovable continuity never become successful media.
There is no invented Parent auth-denial command: known parent framing has no result
field; unknown server behavior is bounded by packet/time limits, not called permission.

After candidate exhaustion at most one normal Center connection may rediscover
parents. It uses the same issued certificate for the same BNO and uses the **new
Center-issued node key**, never a previous connection's key. A rejected/expired
certificate terminates; automatic account re-login or certificate refresh is NOT
implemented. Secrets are already cleared and are not retained for silent renewal.
A user can explicitly open a fresh stream to authenticate again normally.

## Cache and continuity

Initial CB30 remains the Go-proven current-frame + 1 request. After interrupted media,
CB30 requests the last accepted frame as an overlap anchor. Recovery accepts that
anchor only if frame number, type, timestamp and SHA-256 payload identity all match.
This is a narrowly checked replay hypothesis, synthetic-tested; live server replay
of this overlap is **not verified**. Frame numbers alone never establish a shared
sequence space across peers or Center sessions.

A 128-entry identity window excludes exact duplicates from ES/TS output. Received
C748 raw wire/payload still includes duplicates in arrival order. The next accepted
frame must be exactly last+1, with nondecreasing per-elementary-stream timestamps.
No overlap, missing frame, numbering reset, timestamp regression, quality change or
conflicting replay is an explicit error. No media is fabricated, and no gap is
silently labelled continuous. Missing-frame counts are not inferred across unknown
sequence spaces. Counters distinguish interruption/gap events from proved recovered
ranges; a recovered range means validated overlap followed by committed new media,
not proof that arbitrarily old cache data was available.

The same muxer retains PAT/PMT cadence, continuity counters and timestamp base across
validated overlap. No TS state reset or arbitrary timestamp rebasing is used. An
unrecoverable gap terminates the TS output; restarting at an IDR with discontinuity
signaling is not implemented. B-frame DTS reconstruction remains unsupported.

## Budgets and observability

One 1–60 second Parent-receive budget includes connection, handshake, backoff and
rediscovery; reconnects never reset it. Initial HTTP/Gateway/Center bootstrap occurs
before that budget with existing per-operation timeouts. At most eight connection
attempts, one rediscovery, 45 cache requests, 8192 packets and 512 MiB received C748
wire apply globally. Backoff (100ms) is cancellable. Close cancels socket reads and
backoff. A deadline during incomplete recovery is an error, not healthy EOF.

Fixed stage events and counters contain no addresses, URLs, accounts or credentials.
Statistics: connections/attempts, failover attempts/successes, rediscoveries, last
normal frame, excluded duplicates, gap events, recovered and unrecoverable ranges.
Raw frame numbers alone do not prove losslessness. Stages remain in reader.stages;
private capture JSON contains the counters. Actual peer failure is not induced on
SOOP infrastructure.

## Validation boundary

Local fragmented-byte simulators and real loopback TCP servers cover candidate
failure, EOF/reset/timeouts, replay cache, missing cache, duplicate/changed frames,
number resets, timestamp regressions, rediscovery/new keys, deadlines, cancellation,
malformed packets and terminal access denial. Optional FFmpeg generates a local
H.264/AAC test pattern and decodes recovered TS in full. Existing Golden Vectors and
StreamIO/CLI tests remain. This is not a long-duration reliability claim.

LIVE_FAILOVER_NOT_VERIFIED. Protected Python live authentication remains unverified.
