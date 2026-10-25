# Limitations, support boundaries, and licensing

- Experimental community plugin, not integrated into the production Source Adapter.
- Multi-Parent active-active and cross-parent aggregation are not implemented; live
  concurrency, node-key scope, and shared frame sequence are unverified.
- Single-Parent failover and exact-overlap cache recovery passed synthetic/loopback
  tests, but live SOOP Parent failover and server cache overlap are not verified.
  Missing overlap or any unprovable gap ends the current output with an error.
- Continuous-duration capture is implemented with bounded queues, but multi-hour
  live durability, session reauthentication, crash resume, and broadcast rollover
  are not verified. No automatic certificate refresh is performed.
- Current muxer supports progressive Annex-B H.264 with POC type 0, fixed VUI frame
  timing, supported SPS/PPS/slice subset, and one picture per Grid video chunk. Other
  timing/POC/interlace layouts fail closed. HEVC and other video codecs are not
  supported. Audio is observed ADTS AAC.
- DTS reconstruction uses Grid's observed decode-order timestamp and H.264 POC for
  presentation order. Synthetic B-frame and one historical private captured stream
  decode cleanly; arbitrary broadcaster clock semantics, long-run wrap/drift and
  perceptual A/V sync are not exhaustively validated.
- `auto` HLS fallback is only for allowlisted Grid transport failures before Grid
  bytes are emitted. It is not a protocol bridge and will not hide auth denial,
  malformed packets, unsupported media, or a gap after output begins.
- HLS behavior is delegated to Streamlink's built-in SOOP plugin. HLS transport has
  not been freshly live-tested in this revision.
- Linux / Python 3.12.3 / Streamlink 8.6.2 single-file runtime is tested. macOS and
  Windows install paths are documented but runtime loading is not tested here.
- Python strings/bytes cannot guarantee memory zeroization. Secrets are not logged
  or persisted by the plugin, but process-memory inspection is outside its control.
- Long recording writes through Streamlink output. Disk-full, abrupt power loss,
  filesystem durability and crash recovery are not claimed.

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
