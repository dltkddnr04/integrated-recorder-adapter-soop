Current model: [Grid composition timing](GRID_COMPOSITION_TIMING.md). Historical HRD-based results below retain their original revision scope. Production now uses explicit nonnegative Grid composition, common startup origin and unchanged sample-count AAC timing. POC type 1 and Grid MMCO5 remain unsupported. No AU insertion/deletion, resampling, wall-clock synchronization, or perceptual sync guarantee is provided.

# Limitations, support boundaries, and licensing

## Historical live validation — 2026-10-28

The standalone artifact `dist/soop_grid.py` has SHA-256
`400f028ed99467ad644d61525f8a59f85477221dbba28708710ff06d77682cd0`.
That exact artifact completed a 1,802.831-second Grid-only recording, exited 0
at `duration_limit`, and passed a full-file structural, timestamp, H.264/AAC
and PCM decode verification. The 1.8-GB recording and private raw capture are
preserved outside the repository. Detailed counts, hashes and verifier scope are
in [the exact-artifact report](LIVE_VALIDATION_30M_20261028.md).

The exact `400f…` recording contained 108,010 H.264 pictures but no B slices.
A separate 30-minute recording using the earlier `03b31e…` bundle contained
80,179 B slices and passed its full-file checks. These are separate runs and
must not be described as one exact-build B-frame validation. The timing/muxing
path was unchanged between those artifacts, but official SOOP clock semantics
and B-frame behavior on the exact `400f…` live run remain unverified.

The final local offline run passed all 576 modular tests and all 568 bundled
regressions with FFmpeg/ffprobe fixtures enabled and loopback TCP permitted.
Ruff check/format, compileall, deterministic bundle verification and
`git diff --check` also passed. This does not replace the live evidence limits
above.

The historical timestamp mapping was an empirically tested candidate: increasing
Grid video time supplies a DTS candidate, HRD DPB output delay supplies relative
PTS, and POC checks picture ordering rather than acting as a clock. A distinct
observed HRD/POC ordering conflict still fails closed. AAC uses validated ADTS
access units and sample-count timing. Coverage-boundary comparisons are not
source-clock drift or perceptual lip-sync measurements; no independent
perceptual A/V synchronization test has been performed.

## Supported behavior and boundaries

- Experimental/Beta community plugin; it is not integrated into the production
  Source Adapter or Integrated Recorder Core.
- Native Grid reception uses one active Parent. Active-active multi-Parent
  aggregation and network striping are not implemented. Earlier research showed
  identical frame identities on two connections to the same endpoint; this is
  not independent-node redundancy.
- A 600-second recording and three controlled client-owned Parent socket-close
  recoveries in one session have been verified. One earlier natural Parent EOF
  recovered through Center rediscovery and exact Cache overlap, but endpoint
  identity was not retained. The exact `400f…` 30-minute run had no recovery
  event. Repeated natural failures and independent physical failure domains are
  not established. Missing overlap or an unprovable frame gap terminates with an
  error.
- H.264 support is limited to progressive Annex-B streams with parsed POC types
  0/2, one picture per Grid video chunk, and validated syntax. POC type 1
  and MMCO5 remain supported only by historical parser tests, not production Grid timing. The former 1 ms
  Grid/HRD equality test was an unsupported receiver assumption and was removed.
  Grid composition/POC contradictions remain fail-closed. HRD streams with
  `low_delay_hrd_flag=1`, interlace, FMO, unsupported slice layouts, HEVC, and
  midstream codec changes are unsupported.
- Production uses explicit Grid composition to determine PTS, including supported
  reordered pictures without HRD. It never invents FPS or ignores POC order.
  Unknown body versions/header layouts, negative composition and overflowing
  raw presentation timestamps fail closed.
- Audio support is limited to ADTS AAC-LC with one raw data block per ADTS frame,
  validated rate/channel/configuration, and bounded chunk sizes. The Grid audio
  message count is not the AAC access-unit count. Internal AU time is advanced
  using sample counts; the exact clock epoch relationship is not a protocol fact.
- Streamlink transport defaults to Grid-first `auto`. HLS fallback is allowed
  only for allowlisted initial transport failures before Grid bytes are emitted.
  Authentication denial, malformed protocol/media, unsupported timing, and gaps
  after output begins do not fall back to HLS. HLS uses Streamlink's built-in
  `soop` plugin and its own quality selection.
- Native Grid currently exposes only verified `original`. Multi-Parent reception,
  automatic session reauthentication, automatic broadcast-number changes, and
  broadcast-end versus network-failure classification are not supported.
- A three-second media stall may initiate recovery. A legitimate quiet interval
  can therefore cause a reconnect attempt. Recovery keeps a global duration
  deadline and requires exact cache overlap and identity continuity.
- Optional diagnostic capture is disabled by default. When enabled, it has a
  finite size limit but adds synchronous I/O and can consume substantial disk
  space. Diagnostics are intended for private troubleshooting, not public upload
  without review.
- Gateway/Center/Parent use SOOP's plain TCP framing; this client does not add
  TLS to those sockets. Authentication-bearing protocol values may be visible to
  an on-path observer. Use only a trusted network.
- Secrets are not intentionally logged or persisted by the plugin, but Python
  cannot guarantee zeroization of values in process memory. The plugin does not
  share Streamlink's built-in SOOP persistent cookie cache. `purge-credentials`
  applies only to the cache owned by the corresponding plugin.
- Linux, Python 3.12, Streamlink 8.6.2 and FFmpeg/ffprobe 9.0.2 were used for the
  stated validation. Windows/macOS runtime, all broadcaster configurations,
  multi-hour endurance, crash/power-loss durability, and long-term natural
  failure rates remain unverified.
- Reader shutdown cancels sockets and joins the worker with a bounded timeout.
  An OS/filesystem operation that blocks beyond the bound is reported as
  `reader_shutdown_timeout`; Python cannot forcibly terminate that daemon thread.

## License and provenance

The PoC is distributed under **AGPL-3.0-only** and derives from this project's Go
Grid implementation and Python port. The repository contains no proprietary SOOP
binaries, decompilation dumps, live credentials, raw broadcasts, or third-party
implementation source. Public protocol studies are attributed in the Go PoC.

Streamlink is consumed via its normal public plugin interfaces and its own installed
package; its code is not copied or relicensed. HLS delegation uses the installed
built-in SOOP plugin. PyCryptodome is likewise an external normal dependency.

The AGPL implementation cannot be represented as Streamlink's BSD-licensed code for
an upstream contribution. Reuse under other licensing would require review and
permission from relevant rights holders. No upstream Streamlink contribution is part
of this PoC.
