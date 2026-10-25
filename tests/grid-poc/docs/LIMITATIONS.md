# Limitations

- This is a research PoC, not an Integrated Recorder backend or a supported client. The parent Adapter Protocol v1 plugin remains HLS-only.
- The curated CLI has not been live-revalidated after packaging. Historical live results are summarized in [VALIDATION.md](VALIDATION.md).
- Only the public Watch path, native account TK/FTK path, a verified adult path, and an owner-operated password-protected test path are represented by the preserved work. Subscriber-only streams and other access tiers were not validated.
- The password encoder currently accepts printable ASCII from 1 to 12 bytes. Other character encodings are rejected.
- Current server packet layouts and quality behavior may change. A quality code is not a resolution assertion; actual media metadata must be measured.
- Captures are bounded to 30–60 seconds by the command and to at most 512 MiB of wire data. This does not establish long-duration stability or recovery behavior.
- The native client sends protocol control and cache requests. It does not implement media re-upload or peer serving.
- `ffmpeg`/`ffprobe` are optional for post-capture remux and decode verification. Without them, the receiver can still write raw Grid data and MPEG-TS, but playback verification is unavailable.
- In the historical password test, the MP4 decoder counted one fewer decoded frame than the TS/original H.264 while the MP4 samples and source NAL bytes were preserved. The cause remains unknown.
- No actual recordings, packet captures, account secrets, tickets, signed URLs, SOOP binaries, or decompiler dumps are included.

## License and provenance

This directory is covered by the repository's AGPL-3.0 license. Its Go sources were curated from this project's research implementation; the public projects cited in [PROTOCOL.md](PROTOCOL.md) are protocol references, and no source files or binaries from them are vendored. The nested module has no third-party Go dependencies.
