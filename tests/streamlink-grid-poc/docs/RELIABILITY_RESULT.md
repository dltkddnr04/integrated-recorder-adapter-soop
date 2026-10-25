# Reliability and single-file result — 2026-10-25

> Historical snapshot: this report documents the pre-production-readiness revision.
> Its then-current PTS-only/DTS-limited implementation, finite 1–60 second/512 MiB
> limits, old plugin replacement behavior, and 145-test count were superseded by the
> subsequent revision. See [PRODUCTION_READINESS_RESULT.md](PRODUCTION_READINESS_RESULT.md)
> for current code and verification. The prior live measurement below remains a
> historical observation and is not reclassified as a current-code live test.

Base repository commit: `8987d8811a9b7ac5b6144eca461637e171313572`.
Changes are limited to this Python PoC. The Go PoC, production adapter, Streamlink
core and original research archives were not changed. No media/credentials are
published. See [machine-readable measurements](reliability-validation.json).

## Offline and isolated evidence

- 145 Python tests passed, including the original Go-generated Golden Vectors,
  source-module tests, new bootstrap/fault tests, AST/linker and real CLI tests.
- The **same 140 non-construction regression tests** also passed against bundled
  objects via the test-only adapter. This includes loopback TCP Parent failover,
  duplicated cache overlap, exact TS parity, generated H.264/AAC full TS decode,
  terminal gaps, rediscovery/new node key, cancellation and retry limits.
- `ruff check`, compileall, deterministic build/check, Go PoC test/vet/build and
  production adapter test/vet/build passed. Pytest explicitly selects current source,
  avoiding an older installed development wheel when invoked from a parent directory.
- New clean venv: official Streamlink 8.6.2 + its dependencies only. No project wheel
  or soop_grid package installed. PYTHONPATH unset and unrelated cwd. Copied one-file
  discovery, builtin override, JSON Grid streams, options and secret-redaction tests
  passed. Linux runtime tested; macOS/Windows paths are instructions, not runtime proof.
- Source and bundle are AGPL-3.0-only. External dependencies are not vendored.

## Live evidence

Four bounded smoke captures on one allowed public target were used as revisions were
finalized: configured budgets 15, 8, 5 and 5 seconds. All returned CLI exit 0 and all
checked target metadata remained exact. At each check the target was absent from
page 1. Listing reads were **test selection/verification only**, never bootstrap
prerequisites. The final file's SHA-256 is recorded in the JSON; the last capture used
that exact standalone file in the Streamlink-only venv.

Final artifact: actual C748 packets **188**, payload **2,937,237 bytes**, wire
**2,953,217 bytes**, TS **3,022,664 bytes**. H.264 **1920×1080, 30fps**; AAC **44.1kHz
stereo**; **176 video frames**, 12 audio chunks; media timeline 5.833s, TS duration
6.154333s, process wall time 5.881s. Cache preroll explains why the media timeline can
exceed connection budget. Independent wire/checksum/ES reassembly and SHA-256 checks
passed; output TS matched the private sink TS after exit and durable copy.

H.264 ES, AAC ES and full TS decode to raw video/PCM sinks passed with no error output.
The standard `-xerror ... -f null -` timestamped-output check exited 0 **but emitted
non-monotonic DTS diagnostics**. The source contains I/B/P frames (3/108/65), and the
existing muxer is PTS-only. This is not called an error-free presentation timeline or
A/V sync validation. No transcoding was performed during receive or storage; raw
video/PCM output was discarded solely for decoder validation. Longer initial smoke
capture: 491 C748 packets, 7,957,379 payload bytes, 15.636333s TS at the same resolution.

There was one successful Parent connection in the final capture and no natural
outage. **LIVE_FAILOVER_NOT_VERIFIED**. Offline cache proof is not evidence that a
real SOOP Parent will honor overlap replay or retain needed frames. Protected-stream
Python live authentication was not re-tested.

## Status and bounds

```text
PUBLIC_FIRST_PAGE_DEPENDENCY_REMOVED: YES
PUBLIC_EXACT_TARGET_VALIDATION: VERIFIED
PUBLIC_ACCESS_CONTROL_PRESERVED: EXACT_TARGET_SERVER_AUTHORIZATION; POLICY_COVERAGE_DOCUMENTED
PARENT_CANDIDATE_FAILOVER: SYNTHETIC_AND_LOOPBACK_VERIFIED
PARENT_REDISCOVERY: IMPLEMENTED; SYNTHETIC_VERIFIED; NEW_NODE_KEY
CACHE_RECOVERY: SYNTHETIC_VERIFIED_EXACT_OVERLAP_ONLY
DUPLICATE_FRAME_PREVENTION: VERIFIED_FOR_ES_TS; RAW_REPLAY_PRESERVED
FRAME_GAP_DETECTION: VERIFIED; CROSS_PEER_NUMBERING_NOT_ASSUMED
UNRECOVERABLE_GAP_REPORTING: VERIFIED; TERMINATES_OUTPUT
FAILOVER_RETRY_BOUNDED: VERIFIED
CANCELLATION_TEST: PASS
SINGLE_FILE_GENERATED: YES
SINGLE_FILE_REPRODUCIBLE: YES_ON_TESTED_PYTHON_3_12_TOOLCHAIN
SOOP_GRID_PACKAGE_REQUIRED: NO
EXTRA_PIP_INSTALL_REQUIRED: NO_BEYOND_STREAMLINK_NORMAL_DEPENDENCIES
CLEAN_VENV_PLUGIN_DISCOVERY: PASS
CLEAN_VENV_CLI_TEST: PASS
MODULAR_SINGLEFILE_PARITY: PASS
PYTHON_TESTS: 145_PASS; BUNDLED_REGRESSIONS_140_PASS
GO_POC_REGRESSION: PASS
SOURCE_ADAPTER_REGRESSION: PASS
SECRET_AUDIT: PASS
LIVE_PUBLIC_CAPTURE: VERIFIED
LIVE_FAILOVER_VALIDATION: NOT_VERIFIED
```

No unlimited lossless failover claim. Missing cached overlap, frame reset, timestamp
regression or quality/identity conflict terminates. The muxer is retained across exact
validated replay; no fabricated gap filling or discontinuity/IDR restart is provided.
Certificate expiry requires a fresh user-initiated stream, not silent reauthentication.
Both modular source and generated file retain 1–60s/512MiB limits. See
[RELIABILITY.md](RELIABILITY.md) and [SINGLEFILE.md](SINGLEFILE.md).
