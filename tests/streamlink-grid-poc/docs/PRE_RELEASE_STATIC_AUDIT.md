# SOOP Native Grid Pre-Release Static Audit

**Audit date:** 2026-10-25 UTC
**Audited HEAD:** `8b31e14120b5d9b09f48e9721a1ae5c229da8abe`
**Scope:** `tests/streamlink-grid-poc/`, including modular Python sources, tests,
documentation, and generated `dist/soop_grid.py`.
**Decision:** **BETA_GO_WITH_LIMITATIONS**

**Packaging note:** the distribution artifact is `dist/soop_grid.py`; the current
user installation name is `soop_grid.py` and external ID is `soop_grid`. The
built-in ID remains `soop`.
See [SINGLEFILE.md](SINGLEFILE.md) for the current installation instructions.

This was an offline/static audit. No SOOP live request, recording, fault injection,
endurance run, commit, push, tag, or release was performed.

## Summary

No BLOCKER or code-level MAJOR issue was found in the audited Grid receive path,
failover integrity checks, Streamlink integration, or single-file distribution.
Offline tests and the clean Streamlink sideload check passed.

One MINOR operational issue was identified: if opt-in diagnostics are enabled,
`parent_last_media_received` is written and flushed for every media packet. This
makes diagnostic disk use and synchronous diagnostic I/O proportional to media
packet count. The default capture path does not enable diagnostics. The limitation
is documented in [DIAGNOSTICS.md](DIAGNOSTICS.md) and the community guide; the
diagnostic write behavior was not changed in this static-audit-only task.

The 3-second media-stall detector can also classify a legitimate interval without
media as a transient Parent failure. Recovery still has bounded attempts and strict
Cache overlap / sequence validation, so it does not silently claim continuity when
the overlap is missing. This is a documented behavior and a reason not to describe
the plugin as stable or lossless under all server conditions.

## Findings by severity

### BLOCKER

None found.

### MAJOR

None found.

### MINOR

1. **Opt-in diagnostics scale with media packet count.**
   [`parent.py`](../soop_grid/parent.py#L317) reports a media event for each
   received `0xC748` chunk. [`stream.py`](../soop_grid/stream.py#L176) forwards
   it, and [`diagnostics.py`](../soop_grid/diagnostics.py#L238) serializes and
   flushes each event. Thus `events.jsonl` is unbounded with recording duration
   when diagnostics are enabled, and each media packet adds synchronous file I/O.
   This is not an unbounded in-memory queue and does not affect the normal
   diagnostics-disabled path. Mitigation: keep diagnostics disabled for routine
   long captures or monitor free disk while collecting them. The user-facing
   diagnostic documentation now states this explicitly.

2. **Stall detection is intentionally aggressive.**
   [`parent.py`](../soop_grid/parent.py#L15) sets `MEDIA_STALL_TIMEOUT = 3.0`;
   the receive loop applies it when no media arrives
   ([`parent.py`](../soop_grid/parent.py#L234)). A quiet interval lasting three
   seconds can trigger reconnect even when the server/broadcast is merely paused.
   This can add reconnect work or fail if continuity cannot be proven. It does not
   bypass the strict overlap and gap checks. The reliability documentation's stale
   six-second description was corrected to three seconds.

### DOCUMENTED_LIMITATION

- The actual recovery evidence is a **controlled client-side close** of the
  receiver-owned Parent TCP socket during a real public broadcast. Three sequential
  closes recovered through Center rediscovery, a new Node Key, Parent reconnect,
  and exact server Cache overlap. This does not establish natural remote Parent
  failure behavior or independent physical failure domains. The old blanket
  “live failover unverified” wording was updated across the current guide and
  architecture/reliability documentation. Earlier result documents are marked as
  historical snapshots and link to the later evidence.
- The 600-second live recording and 150-second three-recovery run are bounded field
  observations, not 30-minute or multi-hour endurance evidence.
- Automatic account/certificate reauthentication is not implemented. An expired or
  rejected session must fail explicitly and be restarted with normal authorization.
- The receiver cannot yet reliably distinguish an orderly broadcast end from all
  Parent/network disconnects.
- Multi-Parent Active-Active and frame-level striping are not implemented. No
  universal zero-loss or seamless recovery guarantee is made.
- H.264 handling is a constrained progressive Annex-B / POC type 0 / fixed VUI timing
  subset, plus observed ADTS AAC. Unsupported timing, codec, interlace, or parameter
  changes fail closed. Long-run timestamp wrap/drift and perceptual A/V sync are not
  exhaustively established.
- HLS is delegated to Streamlink's built-in SOOP plugin; Auto fallback is limited to
  an allowlisted initial Grid transport failure before Grid bytes are emitted.
  Mid-stream HLS concatenation is not performed.
- Linux was exercised directly. macOS and Windows remain unverified at runtime.

## Static implementation review

### Memory, timing, and resource bounds

- `BoundedBuffer` caps the byte buffer at 16 MiB
  ([`stream.py`](../soop_grid/stream.py#L37)); producer backpressure times out
  rather than growing the queue.
- The accepted-frame identity history is an `OrderedDict` capped at 128 entries
  ([`recovery.py`](../soop_grid/recovery.py#L57)). The per-stream timestamp map
  and H.264 parameter-set maps are bounded by parsed stream IDs.
- Parent discovery candidates are finite; recovery is limited to eight connection
  attempts and one Center rediscovery per no-progress outage
  ([`parent.py`](../soop_grid/parent.py#L15)). A committed accepted media frame
  resets the outage retry counters ([`parent.py`](../soop_grid/parent.py#L320)).
- A configured duration is one monotonic deadline applied across Parent connection,
  handshake, retry, and recovery; it is not reset on reconnect. Bootstrap has
  separately bounded HTTP operations.
- Protocol body sizes are capped before body reads/allocation (Parent packet body
  maximum 4 MiB); HTTP bootstrap has response bounds and rejects redirects. Network
  endpoints are validated as globally routable addresses with valid ports.
- `Network.close()` closes tracked sockets to interrupt blocked work. Reader close
  cancels the producer, closes network sockets, joins its worker with a finite
  timeout, clears secret-bearing config, and preserves worker errors after buffered
  bytes drain. Reader buffer and stage history are bounded.
- Grid frame IDs are validated as unsigned 64-bit values; Python counters do not
  overflow at machine-word width. TS timestamp fields serialize on the 33-bit MPEG
  clock, while the H.264 clock keeps unwrapped internal values and rejects
  non-monotonic decode timestamps. Extreme-duration wrap/drift behavior is not
  field-verified.
- The muxer retains counters/clock state, not the complete recording. Per-call
  packet/NAL buffers are bounded by the accepted Grid frame size. Output is written
  through Streamlink; filesystem exhaustion and power-loss durability remain outside
  the receiver's guarantees.
- Parameter-set/quality/timestamp changes that cannot be reconciled are rejected;
  no evidence was found of silently mixing another quality or broadcast into the
  same TS. Normal broadcast-end classification remains incomplete.

### Failover and integrity

- EOF/truncated frame, socket reset/read/write error, read timeout, and media stall
  enter bounded recovery. Socket timeout and receive OSErrors are mapped to fixed
  error codes; explicit access denial, malformed framing, timestamp regression,
  identity conflict, missing overlap, and frame gap are terminal.
- A new Center session supplies the new Node Key. Existing session keys are not
  reused as proof of validity for a new session.
- Cache recovery requests the last accepted frame as an anchor. Recovery requires
  matching frame number, kind, timestamp, and SHA-256 payload; the overlap is
  discarded from output and the next accepted frame must be consecutive. The
  existing muxer/clock is retained only after proven overlap.
- The 3-second stall threshold can cause false-positive recovery in quiet periods.
  The cache/identity checks protect output integrity, but the audit does not claim
  every transient outage is recoverable.

### Streamlink, packaging, and security

- Streamlink 8.6.2's installed public `Stream` / `StreamIO` integration is covered
  by the offline plugin and reader tests. Partial reads, `readinto`, EOF, buffered
  error propagation, close/cancellation, and plugin stream enumeration are exercised.
- The current installed filename/ID is `soop_grid.py` / `soop_grid`; the built-in `soop` plugin remains
  loaded and is used for HLS delegation. Grid, HLS,
  and Auto routes are covered offline. The bundle imports no `soop_grid` package
  at runtime.
- The static builder rejects dynamic execution/import-loader patterns. The bundle
  contains ordinary readable Python with the AGPL-3.0-only header and source/license
  links. No Go executable, SOOP agent, or FFmpeg receive process is required.
- Endpoint validation, fixed error codes, hidden secret fields in repr/JSON, and
  secret-log tests were reviewed. The targeted scan of 61 tracked files found no
  real credentials, home paths, or oversized (>5 MiB) tracked files. Its two
  pattern hits were reviewed as a placeholder in the installation example and a
  private-address test fixture; neither is a credential or live endpoint.
- Existing ignored local captures/caches were left untouched and were not staged.

## Offline verification

Environment: Linux x86-64, Python 3.12.3, Streamlink 8.6.2, PyCryptodome 3.24.1,
FFmpeg/FFprobe n9.0.2-35-g9a9b70bb8c-20261023. The clean sideload subprocess used
`/tmp/soop-grid-clean-venv`, with no project package and no `PYTHONPATH`.
Loopback tests require local socket permission; the sandbox-denied first attempt was
rerun with that permission. No test connected to SOOP.

| Check | Result |
| --- | --- |
| Python suite, synthetic FFmpeg fixtures enabled | **193 passed, 0 skipped** |
| Bundled regression suite | **188 passed** |
| Ruff | **PASS** — no lint findings |
| Python compileall (`soop_grid`, `plugins`, `tools`) | **PASS** |
| Bundle builder reproducibility check | **PASS** — generated source and checksum match |
| Clean Streamlink-only sideload / CLI JSON enumeration | **PASS** — exercised in suite; built-in SOOP remains available |
| Modular/bundled golden and behavior parity | **PASS** — bundled regression suite |
| FFmpeg-dependent B-frame/AAC/MPEG-TS synthetic decode tests | **PASS** — no skips with FFmpeg on PATH |
| Targeted secret and large-file scan | **PASS after reviewing two harmless pattern matches** |
| `git diff --check` | **PASS** after documentation updates |
| Live recording / live fault injection / endurance | **NOT RUN by instruction** |

The generated artifact already matched the deterministic builder; the expected SHA
did not change:

```text
Artifact: dist/soop_grid.py
Installed filename at audited revision: historical sideload filename
SHA-256: 4752f2b4ad99c0f647adb9c992beaa0a9e4f635b3ca3ce5fa30e2edbcaea52e7
License: AGPL-3.0-only
Bundler reproducibility: PASS
Clean Streamlink import: PASS
Embedded secrets: NONE FOUND
```

The current distribution checksum matches `dist/SHA256SUMS`. The earlier known SHA
is identical; no source or bundle changes were made in this audit.

These artifact fields describe the baseline at the time of the static audit and are
retained as historical evidence. The filename follow-up changes the source plugin
module/logger namespace and is verified separately; the source artifact path remains
`dist/soop_grid.py`.

## Beta publication notes

For the community post, state that:

1. The plugin is **Experimental/Beta**, not a stable long-recording guarantee.
2. A 600-second recording and a bounded 150-second run with three controlled
   client-side Parent socket closes were previously verified; natural remote Parent
   failures and 30-minute/multi-hour endurance were not.
3. Diagnostics are opt-in but produce one flushed event per media packet; monitor
   disk usage or leave them disabled for routine long captures.
4. A three-second no-media interval triggers Parent recovery; quiet/paused streams
   may reconnect. Missing/ambiguous Cache overlap ends with an error rather than
   fabricating continuity.
5. H.264 support is a validated subset; account reauthentication, automatic
   broadcast rollover, multi-Parent reception, and Windows/macOS runtime support
   are not established.
6. Use only streams the user is authorized to watch and record; respect rights and
   service terms.

## Final decision

```text
PRE_RELEASE_AUDIT: COMPLETE
BLOCKER: NONE FOUND
MAJOR: NONE FOUND
MINOR: OPT-IN DIAGNOSTIC JSONL GROWS WITH MEDIA PACKET COUNT
LIVE_TESTS: NOT RUN
GIT_COMMIT: NOT PERFORMED
GIT_PUSH: NOT PERFORMED
BETA_DECISION: BETA_GO_WITH_LIMITATIONS
```

The identified diagnostic limitation is isolated to explicitly enabled diagnostics;
it does not invalidate the tested default receive path. Beta publication is reasonable
with the listed usage caveats. Do not advertise stable or universal lossless behavior.

## Current packaging details

The generated artifact path is `dist/soop_grid.py`, the source entrypoint is
`plugins/soop_grid.py`, and the user installs it as `soop_grid.py`. Streamlink resolves the external ID as `soop_grid`; its built-in
`soop` plugin remains unchanged. The existing default transport remains `auto`
(Grid-first). No receiver, transport, or protocol code changed.

Offline follow-up results: **196 Python tests passed**, including clean Streamlink
8.6.2 plugin discovery, default resolver selection with built-in `soop` loaded,
transport fallback regressions, and a duplicate-install test. The collision test
confirmed that two external copies matching the same URL can make Streamlink's
equal-priority selection depend on plugin load order. Install only the documented
external plugin file.
Bundled regressions: **189 passed**. Ruff, compileall, and deterministic regeneration
passed.

```text
Current artifact: dist/soop_grid.py
Current install name: soop_grid.py
Current external plugin ID: soop_grid
Built-in plugin ID: soop
Current SHA-256: 8b2532eeb4cefb0e149ed2d55c7a4b5e789f6246b5b849806fc5efa13a485243
Previous audit SHA-256: 4752f2b4ad99c0f647adb9c992beaa0a9e4f635b3ca3ce5fa30e2edbcaea52e7
```

The digest differs from the earlier audit artifact because the diagnostic logger
namespaces use `streamlink.plugins.soop_grid`, and Ruff formatting of the receiver
and research/test sources refreshed the deterministic builder's embedded source
digests. The formatting change does not alter receiver behavior. The installer
filename determines the external plugin ID. This report's packaging snapshot was
recorded before the final publication; use the commit-pinned download URL in the
release announcement.
