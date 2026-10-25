# Historical live validation

The figures below summarize experiments in the private research workspace. They are evidence for the research result, not a live test of the curated package. No broadcast identifiers, tickets, cookies, endpoints, media files, wire captures, or media hashes are published here.

| Path | Historical evidence | Result |
| --- | --- | --- |
| Public stream | Native TCP Parent path; actual `0xC748`; H.264/AAC; 2560×1440 at 60 fps; 36.816667 s playback file; full FFmpeg decode passed | Verified before packaging |
| Grade-19 stream | Go account login and first-party TK/FTK; Gateway → native Center → Parent; 2,233 media packets; 37,061,190 payload bytes; H.264 1920×1080 about 60 fps; AAC-LC; 36.050313 s; full decode passed | Grade verified before and after capture in that experiment |
| Owner-operated password-protected test | Go account login; the provided stream password was added to Center JOIN Service 70; 3,096 media packets; 16,459,084 payload bytes; H.264 1920×1080 at 60 fps; AAC-LC; 50.272667 s media timeline; source bytes preserved | Native Grid media and decode verified in that experiment |

## Password capture frame-count note

The password-protected capture's MPEG-TS and original H.264 decode contain 2,998 video frames. The stream-copy MP4 contains 2,998 video samples, while the tested FFmpeg decoder reports 2,997 decoded frames from that MP4. Both TS and MP4 full decode commands exited successfully; all extracted MP4 NAL bytes and order were verified against the original. The one-frame MP4 decoder count difference was not explained. The original TS remains the reference when exact frame count matters.

## What was and was not revalidated here

The research checkout and the latest password-capture archive had identical copies of the receiver source files at the time of review. This repository packaging then changed the module path, CLI surface, public target checks, and metadata handling; it also removed one-off probes and transport experiments. The transport encoders, Center/Parent exchange, packet parser, media splitter, and TS muxer were carried into this PoC with focused cleanup. Consequently, historical media success is not a claim that this curated package has been live-run unchanged.

The curated package has offline synthetic tests for login fields, protocol framing, Center/password JOIN construction, Parent packet parsing, codec metadata parsing, byte handling, and TS output. The tests contain no real broadcast media. See [LIMITATIONS.md](LIMITATIONS.md) for unverified areas.

## Source size

Counting regular files under the historical `rea-research/grid-receiver` and this PoC directory, the source was reduced from 83 files (81 Go files, 11,619 Go lines) to 49 files (43 Go files, 5,985 Go lines). The count excludes the surrounding research reports and success archives; the curated tree contains no capture or build-output files.
