# Receiver and process diagnostics

Diagnostics are opt-in. The one-file plugin needs no helper package:

```sh
streamlink --soop-grid-transport grid \
  --soop-grid-diagnostics /private/path/new-run \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

The destination must be a **new directory** whose parent already exists. An existing
directory or symlink is rejected. On POSIX the new directory is mode 0700 and files
are mode 0600. `SOOP_GRID_DIAGNOSTICS_DIR` is an alternative input. Diagnostics use
Streamlink's normal Python dependencies; the validation tools below are optional.

## Files and privacy

`events.jsonl` contains a heartbeat every two seconds and fixed state/error events.
It records monotonic elapsed time, numeric packet/frame/byte counts, last committed
frame, last control command, cache request position, active sockets/Parent count,
buffer occupancy/peak, and last video DTS/PTS. Invalid media packets contribute only
their numeric body/header/extension/payload sizes. No packet body, URL, endpoint,
account, Cookie, Ticket, node key, exception message, or traceback is serialized.
Unknown event/error strings are replaced with fixed generic codes.

Each event is flushed. `summary.json` is written through a temporary file and atomic
replacement at worker completion and reader close; the file is flushed and fsynced.
There is no per-frame diagnostic fsync. Sudden process death or storage failure can
prevent the final summary, so absence of a summary is not evidence of normal EOF.
The heartbeat thread is stopped and joined on close.

The receiver currently emits `parent_last_media_received` for every media packet;
when diagnostics are enabled this creates and flushes one JSONL record per packet in
addition to the two-second heartbeat. Diagnostic disk usage and synchronous write
overhead therefore grow with packet count. For long captures, leave diagnostics
disabled unless that detailed event stream is needed, and monitor available disk space.

Worker errors are retained while existing output bytes drain. Once the buffer is
empty the reader raises `OSError`, as Streamlink 8.6.2's output runner requires.
Startup still uses `StreamError`. A cleanup exception does not replace a prior
parser/network error. User cancellation discards pending output and closes sockets.
Diagnostic reason selection retains the first terminal cause, including across close.

`duration_limit` and `byte_limit` are explicit local stops. Network EOF, timeout,
stall, rejected authentication, failed overlap, frame gap, and timestamp rejection
remain errors. Normal broadcast-end signaling and certificate-expiration signaling
have not been established; they are not inferred from a disconnected socket.

## Private field-test supervisor

Use a development installation for these tools, plus a separate clean environment
containing official Streamlink 8.6.2 only. Copy `dist/soop.py` to a temporary plugin
directory as `soop_grid.py`:

```sh
python tools/field_validate.py \
  --python /path/to/clean-venv/bin/python \
  --plugin-dir /private/plugins \
  --directory /private/new-field-run --duration 300 \
  --discover-public
```

The supervisor checks at most eight entries from one normal public listing page and
selects an explicitly password-free, non-subscription, non-adult candidate. The
receiver independently checks exact Live metadata and requires a positive Watch
ticket. Discovery is a test convenience, not a receiver prerequisite. Alternatively,
provide `--url` with an exact authorized channel/BNO. The child has no `PYTHONPATH`.

The supervisor drains **both stdout and stderr**. Streamlink's normal log messages
can be on stdout; the previous field wrapper discarded that channel. Log lines are
classified into fixed known codes or `redacted_cli_line`; raw text is never saved.
`process.jsonl` records numeric resource samples on Linux and output file growth.
`process-summary.json` records exit status, signal if any, configured bounds, artifact
SHA-256, UTC start/end, last receiver heartbeat, and final receiver summary. No command
target or credential is stored. CPU/RSS/FD observations are unavailable on platforms
without `/proc`; this is not an OS-wide OOM monitor. An unexplained exit remains unknown.

The supervisor has a duration-plus-120-second process deadline and terminates a
stuck child. That termination is explicitly marked. A normal field pass requires
process exit 0, the intended local stop reason, media verification, and intact output;
the supervisor's own successful exit alone does not establish receiver success.

`--capture-raw` enables additional **private media content** files and preservation of
the first rejected `0xC748` packet. This is separate from diagnostics, can use several
times the TS file size, and must never be uploaded as a public diagnostic log.

Post-capture checks run outside the receiving path:

```sh
python tools/verify_recording.py /private/new-field-run/recording.ts \
  --ffprobe /path/to/ffprobe --ffmpeg /path/to/ffmpeg
```

This checks all TS packets and video packet timestamps, then decodes the full video
and audio with `-xerror`. PTS reordering in decode packet order is expected for B
pictures; DTS regression is not. A/V stream start/duration measurements do not prove
perceptual synchronization.

## Bounded concurrent-connection research

After ordinary reception works, `tools/probe_parent_identity.py --discover-public
--directory /private/new-parent-probe --seconds 20` obtains one normal Center session
and uses at most two TCP connections for at most 30 seconds, 64 MiB per connection.
Only server-issued endpoints and the corresponding session node key are used. If
only one endpoint is available, two sockets test concurrency at that same endpoint;
this does not establish independent Parent redundancy.

The probe stops both connections on a connection/handshake/read failure. It stores
at most 4,096 hashes per connection and compares raw media frame number, kind,
timestamp, size, and SHA-256 before any TS muxing. Private frame-identity details
contain no media bytes; the public summary has only aggregates and delay statistics.
Missing counts cover only the overlapping frame-number interval. Disjoint number
spaces are incomparable. This tool does not enable an Active-Active receiver.
