# 30-minute Native Grid validation — 2026-10-27

**Result: duration and structural/decode gates passed for this one captured stream.**
This is not a universal stability claim, a B-frame live test, or proof of perceptual A/V sync.

## Run identity and environment

- Repository: `integrated-recorder/source.soop`; branch `main`.
- Source HEAD: `8366328d0968c16942045f1ccb0c65c98172cbbc`, with existing local changes; no commit or push was made.
- Exact standalone artifact used: `dist/soop_grid.py`, SHA-256 `e35dc98aced1149bb63325863d1ee92dd353fe3777fc6fb897d626082d03c702`.
- Linux x86_64, Python 3.12.3, Streamlink 8.6.2; FFmpeg/ffprobe `n9.0.2-35-g9a9b70bb8c-20261023`.
- Explicit Grid-only mode; no HLS fallback. The report omits the broadcast URL, channel and network endpoints.
- Started `2026-10-27T19:07:30.556864Z`; exited `2026-10-27T19:37:42.180750Z` after 1,811.624 wall seconds.
- Process exit 0, stop reason `duration_limit`, no termination signal and no supervisor timeout. Receiver's last media event was at 1,804.015 seconds.
- Recording size: 1,890,960,400 bytes. Receiver peak RSS 48,478,160 bytes (46.2 MiB), 3 threads, 13 open descriptors, peak StreamIO buffer 1,012,240 bytes.
- Private capture, recording, summaries, verifier output and checksums are retained in the NFS run directory `live-current-e35dc9-30m-20261027/`. Raw captures are not included in the repository.

## Receiver and frame evidence

- 111,532 Grid media messages: 108,016 video frames and 3,516 audio messages.
- Video: 108,016 output/playable H.264 frames; SPS reported POC type 2 and fixed VUI timing at 60 fps. No HRD or Picture Timing SEI was present. Observed video was I/P only: 901 I and 107,115 P frames; no B frame occurred.
- The 111,532 media-message frame numbers were unique and strictly contiguous in capture order: zero duplicate steps, regressions or gaps. Receiver counters also reported zero sequence discontinuities, missing frames, duplicate exclusions and identity conflicts.
- Audio: 84,384 AAC-LC access units, 1,024 samples each at 48 kHz stereo, totaling 86,409,216 samples per channel or 1,800.192 seconds. The 3,516 Grid audio messages carried 24 access units each. Their observed timestamp step was 512 ms, matching 24 × 1,024 / 48,000; receiver chunk/sample clock residual counters were zero at recorded precision. This supports the observed chunk-start/sample-expansion behavior for this stream, not a protocol-wide timestamp guarantee.
- Video raw Grid timestamp span was 1,800.272 seconds; generated video packet duration was 1,800.267 seconds. The selected `grid_no_reorder` model assigned observed Grid media time to DTS and PTS. As the stream had no B pictures, this does not establish reordered-picture timing.
- One Parent connection was used. No EOF, reconnect, Center rediscovery or cache recovery occurred, so this run provides no new failover evidence.

## Independent whole-file verification

The repository verifier scanned the full transport stream, probed all packets, decoded all video and audio with `ffmpeg -xerror -err_detect explode`, counted PCM samples, and independently parsed the decoded AAC access units. It returned `passed: true`, with no FFmpeg or ffprobe diagnostics.

| Check | Result |
| --- | ---: |
| TS packets / sync errors | 10,058,300 / 0 |
| Continuity / transport / adaptation errors | 0 / 0 / 0 |
| PAT / PMT / PSI CRC or structure errors | 1,801 / 1,801 / 0 |
| PCR samples / regressions / maximum gap | 108,016 / 0 / 17 ms |
| Video packet timestamps | 108,016; missing PTS/DTS/duration 0; DTS regressions 0 |
| Video PTS reorder steps / PTS-before-DTS | 0 / 0 |
| Full H.264 decode | 108,016 frames, 1,800.267 s |
| Audio packet timestamps | 84,384; missing PTS/DTS/duration 0; DTS regressions 0 |
| Full AAC decode | 84,384 AUs; 86,409,216 PCM samples/channel; 1,800.192 s |
| AAC trailing bytes / packet overlaps | 0 / 0 |
| Duration gate (each track ≥1,790 s) | passed |

The TS PES video payload SHA-256 exactly matched the captured elementary `video.h264` SHA-256 (`2c40e3279406be7b1f0ad7a7f80f5cb8309994d3a3da91a3233d452ba89046f3`); the audio PES payload SHA-256 exactly matched `audio.aac` (`b14a63d5fd5e3013279dfd6070a6aadb99c6879665791f3ea3bc004dff452dfa`). This verifies byte preservation from the captured elementary streams through TS PES extraction. It is not an independent source-side payload oracle.

TS SHA-256: `e9536506134230d856e967e85bed1d9ccfb066718a59c6294e798199620056d4`. The verifier JSON and original capture have separate SHA-256 entries in the private NFS manifest.

### Fresh independent verifier rerun

On 2026-10-27, the same byte-identical TS was rechecked with an explicit
1,800-second decoded-duration minimum and a 3,600-second verifier deadline.
It passed in 780.0 seconds with exit 0; all structural, packet timestamp,
H.264/AAC decode and duration gates passed. Its sanitized JSON is retained at
`live-current-e35dc9-30m-20261027/fresh-full-verification-20261027.json` on the
private NFS evidence store (SHA-256
`ee7dfaa4ba1f756a67bc28e2b3259eeef4100f3865fac53f4ba23c11edae6dd2`). The
earlier 562/554 regression counts below are the counts at the time of the live
run; the later working-tree rerun passed 563 modular and 555 bundled tests.

## Offline regression after the run

With FFmpeg/ffprobe explicitly configured, the modular suite passed **562/562**
tests and the bundled regression passed **554/554**. Ruff check, Ruff format
check, `compileall`, deterministic bundle `--check`, Markdown relative-link
check (22 Markdown files, zero broken local links), and `git diff --check` also
passed. The bundle hash remained unchanged. The first bundled test invocation
inside the restricted sandbox failed 14 loopback cases with `EPERM`; rerunning
under the permitted local-test execution context passed all 554 tests. No tests
or recordings were deleted to produce these results.

## A/V interpretation and limits

The packet timeline starts differ by 22 ms (audio earlier); the ending track coverage boundaries differ by 96.7 ms. Their change is −74.7 ms over this recording. The verifier explicitly classifies that value as a difference in track-coverage boundaries, **not** source-clock drift or perceptual synchronization. Receiver Grid audio chunk/sample counters show no measured residual, but no independent source clock, official-player timing trace, or lip-sync reference was available. Therefore container timeline consistency is verified; perceptual A/V synchronization is not.

This sample had no HRD/SEI and no B pictures. It does not resolve the separate `h264_hrd_grid_clock_conflict` / HRD-POC issue observed on another stream, and does not validate live B-frame timestamping. The fail-closed policy remains for reordered streams whose timing cannot be established. Only one 30-minute run was performed; repeated natural failures, multi-hour endurance, automatic reauthentication, all encoder variants and all operating systems remain unverified. The plugin remains Experimental/Beta.
