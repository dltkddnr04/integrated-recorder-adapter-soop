#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Bounded, POSIX-only offline TS/AAC validation; not a perceptual quality claim.

Exit 0 means every structural, timestamp and decode gate completed successfully.
Exit 1 means failure or incomplete validation. Output contains fixed failure codes,
counts and timing facts, never subprocess diagnostics or input file paths.
--timeout is one wall-clock budget for the scan and all subprocesses combined.
AV offset changes compare coverage boundaries, not source clock drift or
perceptual sync. The legacy drift_seconds field is only a compatibility alias.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time


# A local media-clock continuity heuristic, not a PCR wire-arrival/repetition
# conformance test. This offline verifier has no transport bitrate or capture
# arrival timestamps from which to calculate actual PCR repetition intervals.
MAX_PCR_VALUE_DELTA_SECONDS = 0.100


class ValidationError(Exception):
    pass


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValidationError("validation_timeout")
    return remaining


def _crc_table():
    table = []
    for value in range(256):
        crc = value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
        table.append(crc)
    return tuple(table)


_CRC = _crc_table()


def _crc32(data):
    crc = 0xFFFFFFFF
    for value in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[(crc >> 24) ^ value]
    return crc


class PESPayload:
    """Exact PES elementary bytes, retaining only a <=264-byte optional header.

    Fixed SOOP video/audio PIDs are observed, never remuxed or normalized. A
    length-zero video PES ends at the next PUSI or EOF. Compare both byte count
    and digest with producer facts; the digest alone says nothing about quality.
    """

    def __init__(self, video):
        self.video = video
        self.digest = hashlib.sha256()
        self.header = bytearray()
        self.active = False
        self.remaining = None
        self.facts = dict(
            bytes=0,
            pes_packets=0,
            complete_pes_packets=0,
            header_errors=0,
            truncated_pes=0,
            orphan_payload_bytes=0,
            extra_payload_bytes=0,
        )

    def boundary(self):
        if self.active:
            if self.header or self.remaining is not None:
                self.facts["truncated_pes"] += 1
            else:
                self.facts["complete_pes_packets"] += 1
        self.active = False
        self.header.clear()
        self.remaining = None

    def feed(self, payload, start):
        if start:
            self.boundary()
            self.active = True
            self.facts["pes_packets"] += 1
            # A header is assembled in phases: prefix/length, flags, optional data.
            self.header = bytearray()
            self.remaining = -1
        if not self.active:
            self.facts["orphan_payload_bytes"] += len(payload)
            return
        view = memoryview(payload)
        if self.remaining == -1:
            while True:
                needed = 6 if len(self.header) < 6 else 9 if len(self.header) < 9 else 9 + self.header[8]
                take = min(needed - len(self.header), len(view))
                self.header.extend(view[:take])
                view = view[take:]
                if len(self.header) < needed:
                    return
                if len(self.header) == 6:
                    stream_id = self.header[3]
                    valid_id = 0xE0 <= stream_id <= 0xEF if self.video else 0xC0 <= stream_id <= 0xDF
                    if self.header[:3] != b"\0\0\1" or not valid_id:
                        self.facts["header_errors"] += 1
                        self.active = False
                        return
                    continue
                if len(self.header) == 9 and self.header[8]:
                    continue
                length = int.from_bytes(self.header[4:6], "big")
                optional = 3 + self.header[8]
                if (
                    self.header[6] & 0xF0 != 0x80
                    or (length and length < optional)
                    or (not length and not self.video)
                ):
                    self.facts["header_errors"] += 1
                    self.active = False
                    return
                self.remaining = length - optional if length else None
                self.header.clear()
                break
        count = len(view) if self.remaining is None else min(len(view), self.remaining)
        self.digest.update(view[:count])
        self.facts["bytes"] += count
        if self.remaining is not None:
            self.remaining -= count
            if self.remaining == 0:
                self.active = False
                self.facts["complete_pes_packets"] += 1
                self.facts["extra_payload_bytes"] += len(view) - count

    def finish(self):
        self.boundary()
        return dict(self.facts, sha256=self.digest.hexdigest())


class PSI:
    """At most one 1024-byte pending section per 13-bit PID."""

    def __init__(self, result):
        self.result = result
        self.buffers = {}
        self.pmt_pids = set()
        self.seen_pmts = set()
        self.pcr_pid_declarations = set()

    def section(self, pid, section):
        result = self.result
        if _crc32(section):
            result["psi_crc_errors"] += 1
            return
        if len(section) < 12 or section[1] & 0xF0 != 0xB0 or section[6] > section[7]:
            result["psi_structure_errors"] += 1
            return
        if not section[5] & 1:  # A future table is not evidence of the current program.
            return
        if pid == 0:
            if section[0] != 0 or (len(section) - 12) % 4:
                result["psi_structure_errors"] += 1
                return
            result["pat"] += 1
            for pos in range(8, len(section) - 4, 4):
                program = int.from_bytes(section[pos : pos + 2], "big")
                if program:
                    pmt_pid = ((section[pos + 2] & 31) << 8) | section[pos + 3]
                    if pmt_pid in (0, 0x1FFF):
                        result["psi_structure_errors"] += 1
                    else:
                        self.pmt_pids.add(pmt_pid)
        else:
            if section[0] != 2 or len(section) < 16:
                result["psi_structure_errors"] += 1
                return
            end = len(section) - 4
            if section[8] & 0xE0 != 0xE0:
                result["psi_structure_errors"] += 1
                return
            self.pcr_pid_declarations.add(((section[8] & 0x1F) << 8) | section[9])
            pos = 12 + (((section[10] & 15) << 8) | section[11])
            if pos > end:
                result["psi_structure_errors"] += 1
                return
            while pos < end:
                if pos + 5 > end:
                    result["psi_structure_errors"] += 1
                    return
                pos += 5 + (((section[pos + 3] & 15) << 8) | section[pos + 4])
            if pos != end:
                result["psi_structure_errors"] += 1
                return
            result["pmt"] += 1
            self.seen_pmts.add(pid)

    def feed(self, pid, payload, start):
        if pid != 0 and pid not in self.pmt_pids:
            return
        pending = self.buffers.setdefault(pid, bytearray())
        if start:
            if not payload or payload[0] >= len(payload):
                self.result["psi_structure_errors"] += 1
                pending.clear()
                return
            pointer = payload[0]
            if pending:
                self._append(pid, payload[1 : 1 + pointer], allow_new=False)
                if pending:
                    self.result["psi_structure_errors"] += 1
                    pending.clear()
            self._append(pid, payload[1 + pointer :])
        elif pending:
            self._append(pid, payload)

    def _append(self, pid, data, allow_new=True):
        pending = self.buffers[pid]
        # A packet is only 184 bytes; no unbounded section accumulation.
        pending.extend(data)
        while pending:
            if pending[0] == 0xFF:
                if any(value != 0xFF for value in pending):
                    self.result["psi_structure_errors"] += 1
                pending.clear()
                return
            if len(pending) < 3:
                return
            size = 3 + (((pending[1] & 15) << 8) | pending[2])
            if not 12 <= size <= 1024:
                self.result["psi_structure_errors"] += 1
                pending.clear()
                return
            if len(pending) < size:
                return
            self.section(pid, pending[:size])
            del pending[:size]
            if not allow_new:
                if any(value != 0xFF for value in pending):
                    self.result["psi_structure_errors"] += 1
                pending.clear()
                return


TS_PACKET_SIZE = 188
TS_READ_PACKETS = 4096


def _iter_ts_packets(stream, digest, packets_per_read=TS_READ_PACKETS):
    """Read bounded blocks while preserving TS packet boundaries and full-file hash."""
    carry = b""
    block_size = TS_PACKET_SIZE * packets_per_read
    while True:
        block = stream.read(block_size)
        if not block:
            break
        digest.update(block)
        data = carry + block if carry else block
        complete = len(data) - len(data) % TS_PACKET_SIZE
        view = memoryview(data)
        for offset in range(0, complete, TS_PACKET_SIZE):
            yield view[offset : offset + TS_PACKET_SIZE]
        carry = data[complete:]
    if carry:
        yield carry


def scan_ts(path, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 1800
    result = dict.fromkeys(
        (
            "packets",
            "sync_errors",
            "continuity_errors",
            "pat",
            "pmt",
            "pcr_samples",
            "pcr_regressions",
            "explicit_discontinuities",
            "transport_errors",
            "scrambled_packets",
            "adaptation_errors",
            "psi_crc_errors",
            "psi_structure_errors",
            "duplicate_packets",
        ),
        0,
    )
    result["max_pcr_value_delta_seconds"] = 0.0
    result["pcr_progression_pair_counts"] = {}
    counts, last_packets, last_pcr = {}, {}, {}
    duplicates = set()
    psi = PSI(result)
    payloads = {0x100: PESPayload(video=True), 0x101: PESPayload(video=False)}
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for packet in _iter_ts_packets(stream, digest):
            if result["packets"] % 4096 == 0:
                _remaining(deadline)
            if len(packet) != TS_PACKET_SIZE or packet[0] != 0x47:
                result["sync_errors"] += 1
                # Keep hashing the entire file, even when structural validation fails.
                if len(packet) != TS_PACKET_SIZE:
                    break
                result["packets"] += 1
                continue
            result["packets"] += 1
            pid = ((packet[1] & 31) << 8) | packet[2]
            afc, cc = (packet[3] >> 4) & 3, packet[3] & 15
            result["transport_errors"] += bool(packet[1] & 0x80)
            result["scrambled_packets"] += bool(packet[3] & 0xC0)
            offset = 4
            discontinuity = False
            if afc == 0:
                result["adaptation_errors"] += 1
                continue
            if afc in (2, 3):
                length = packet[4]
                offset = 5 + length
                if (afc == 2 and length != 183) or (afc == 3 and length > 182):
                    result["adaptation_errors"] += 1
                    continue
                if length:
                    flags = packet[5]
                    discontinuity = bool(flags & 0x80)
                    if discontinuity:
                        result["explicit_discontinuities"] += 1
                        last_pcr.pop(pid, None)
                        counts.pop(pid, None)
                        duplicates.discard(pid)
                        psi.buffers.pop(pid, None)
                    cursor = 6
                    for mask in (0x10, 0x08):  # PCR and OPCR
                        if flags & mask:
                            if cursor + 6 > offset:
                                result["adaptation_errors"] += 1
                                break
                            b = packet[cursor : cursor + 6]
                            extension = ((b[4] & 1) << 8) | b[5]
                            if b[4] & 0x7E != 0x7E or extension >= 300:
                                result["adaptation_errors"] += 1
                            if mask == 0x10 and pid != 0x1FFF:
                                base = (int.from_bytes(b[:4], "big") << 1) | (b[4] >> 7)
                                pcr = base * 300 + extension
                                if pid in last_pcr:
                                    delta = (pcr - last_pcr[pid]) % ((1 << 33) * 300)
                                    if delta >= (1 << 32) * 300:
                                        result["pcr_regressions"] += 1
                                    else:
                                        result["max_pcr_value_delta_seconds"] = max(
                                            result["max_pcr_value_delta_seconds"], delta / 27_000_000
                                        )
                                        if delta > 0:
                                            pcr_key = f"0x{pid:x}"
                                            result["pcr_progression_pair_counts"][pcr_key] = (
                                                result["pcr_progression_pair_counts"].get(pcr_key, 0) + 1
                                            )
                                last_pcr[pid] = pcr
                                result["pcr_samples"] += 1
                            cursor += 6
                    else:
                        cursor += bool(flags & 4)  # splice countdown
                        for mask in (2, 1):  # private data and adaptation extension
                            if flags & mask:
                                if cursor >= offset:
                                    cursor = offset + 1
                                    break
                                cursor += 1 + packet[cursor]
                        if cursor > offset or any(value != 0xFF for value in packet[cursor:offset]):
                            result["adaptation_errors"] += 1
            if pid == 0x1FFF:
                continue  # Null packet counters are unspecified.
            has_payload = afc in (1, 3)
            if pid in counts:
                expected = (counts[pid] + has_payload) & 15
                if cc != expected:
                    if (
                        has_payload
                        and cc == counts[pid]
                        and packet == last_packets[pid]
                        and pid not in duplicates
                    ):
                        duplicates.add(pid)
                        result["duplicate_packets"] += 1
                        continue
                    result["continuity_errors"] += 1
                    psi.buffers.pop(pid, None)
                else:
                    duplicates.discard(pid)
            counts[pid], last_packets[pid] = cc, packet
            if has_payload and not packet[1] & 0x80 and not packet[3] & 0xC0:
                psi.feed(pid, packet[offset:], bool(packet[1] & 0x40))
                if pid in payloads:
                    payloads[pid].feed(packet[offset:], bool(packet[1] & 0x40))
    result["psi_structure_errors"] += sum(bool(pending) for pending in psi.buffers.values())
    result["missing_pmt_pids"] = len(psi.pmt_pids - psi.seen_pmts)
    result["pcr_pid_declarations"] = sorted(f"0x{pid:x}" for pid in psi.pcr_pid_declarations)
    result["pes_payloads"] = {f"0x{pid:03x}": state.finish() for pid, state in payloads.items()}
    result["sha256"] = digest.hexdigest()  # File identity only, not a quality gate.
    return result


def has_signaled_pcr_progression(ts):
    declared = {pid for pid in ts["pcr_pid_declarations"] if pid != "0x1fff"}
    progressing = {pid for pid, pairs in ts["pcr_progression_pair_counts"].items() if pairs > 0}
    return bool(declared.intersection(progressing))


def run_process(args, consume, deadline):
    """Drain both pipes concurrently, retain no payload/logs, and reap on every exit."""
    if os.name != "posix":
        raise ValidationError("validation_platform_unsupported")
    result = dict(exit_code=None, timed_out=False, launch_failed=False, diagnostic_lines=0)
    process = None
    diagnostic_tail = b""
    try:
        _remaining(deadline)
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        with selectors.DefaultSelector() as selector:
            for pipe in (process.stdout, process.stderr):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            while selector.get_map():
                for key, _ in selector.select(min(0.2, _remaining(deadline))):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.fileobj is process.stdout:
                        consume(chunk)
                    else:
                        result["diagnostic_lines"] += chunk.count(b"\n")
                        diagnostic_tail = chunk[-1:]
            result["exit_code"] = process.wait(timeout=_remaining(deadline))
    except ValidationError as error:
        if str(error) != "validation_timeout":
            raise
        result["timed_out"] = True
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
    except OSError:
        result["launch_failed"] = True
    finally:
        if process is not None:
            # A child can keep the pipes open after its parent exits; kill the group
            # on timeout/parse failure too, then wait and close both descriptors.
            if result["exit_code"] is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()
    result["diagnostic_lines"] += bool(diagnostic_tail and diagnostic_tail != b"\n")
    return result


class Lines:
    def __init__(self, consume):
        self.consume = consume
        self.pending = bytearray()

    def __call__(self, chunk):
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            if len(line) > 65536:
                raise ValidationError("subprocess_output_limit")
            self.consume(line.decode("ascii", "replace").strip())
        if len(self.pending) > 65536:
            raise ValidationError("subprocess_output_limit")

    def finish(self):
        if self.pending:
            self.consume(self.pending.decode("ascii", "replace").strip())
            self.pending.clear()


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and abs(value) <= 1e100 else None
    except (ValueError, TypeError):
        return None


def timing():
    return dict(
        packets=0,
        missing_pts=0,
        missing_dts=0,
        missing_duration=0,
        nonmonotonic_dts=0,
        pts_reorder_steps=0,
        pts_before_dts=0,
        pts_overlap_packets=0,
        max_pts_overlap_seconds=0.0,
        start_seconds=None,
        end_seconds=None,
    )


class PacketFacts:
    def __init__(self, streams):
        self.kinds = {s["index"]: s["codec_type"] for s in streams}
        self.facts = {kind: timing() for kind in self.kinds.values()}
        self.previous = {}
        self.malformed_rows = 0

    def line(self, line):
        if not line:
            return
        values = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        try:
            index = int(values["stream_index"])
        except (KeyError, ValueError):
            self.malformed_rows += 1
            return
        if index not in self.kinds:
            return
        facts = self.facts[self.kinds[index]]
        pts, dts, duration = (number(values.get(k)) for k in ("pts_time", "dts_time", "duration_time"))
        facts["packets"] += 1
        for key, value in (("pts", pts), ("dts", dts), ("duration", duration)):
            facts["missing_" + key] += value is None or (key == "duration" and value <= 0)
        previous_pts, previous_dts, previous_end = self.previous.get(index, (None, None, None))
        facts["nonmonotonic_dts"] += dts is not None and previous_dts is not None and dts <= previous_dts
        facts["pts_reorder_steps"] += pts is not None and previous_pts is not None and pts < previous_pts
        facts["pts_before_dts"] += pts is not None and dts is not None and pts < dts - 0.000001
        if self.kinds[index] == "audio" and pts is not None and previous_end is not None:
            overlap = previous_end - pts
            if overlap > 1 / 90_000:
                facts["pts_overlap_packets"] += 1
                facts["max_pts_overlap_seconds"] = max(facts["max_pts_overlap_seconds"], overlap)
        self.previous[index] = (pts, dts, pts + duration if pts is not None and duration else None)
        if pts is not None:
            start = facts["start_seconds"]
            facts["start_seconds"] = pts if start is None else min(start, pts)
            if duration is not None and duration > 0:
                end = pts + duration
                facts["end_seconds"] = max(end, facts["end_seconds"] or end)


class DecodeFacts:
    def __init__(self, channels):
        self.channels = channels
        self.time_bases = {}
        self.facts = {
            kind: dict(frames=0, start_seconds=None, end_seconds=None) for kind in ("video", "audio")
        }
        self.facts["audio"].update(pcm_bytes=0, pcm_samples_per_channel=0)

    def line(self, line):
        if line.startswith("#tb "):
            index, ratio = line[4:].split(":", 1)
            index = int(index)
            numerator, denominator = map(int, ratio.split("/"))
            if index not in (0, 1) or denominator <= 0 or numerator <= 0:
                raise ValidationError("decode_output_invalid")
            self.time_bases[index] = numerator / denominator
        elif line and not line.startswith("#"):
            try:
                index, _dts, pts, duration, size, _checksum = [part.strip() for part in line.split(",")]
                index, pts, duration, size = int(index), int(pts), int(duration), int(size)
                if index not in (0, 1) or duration <= 0 or size <= 0:
                    raise ValueError
                scale = self.time_bases[index]
            except (ValueError, KeyError):
                raise ValidationError("decode_output_invalid") from None
            facts = self.facts["video" if index == 0 else "audio"]
            facts["frames"] += 1
            start, end = pts * scale, (pts + duration) * scale
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValidationError("decode_output_invalid")
            facts["start_seconds"] = (
                min(start, facts["start_seconds"]) if facts["start_seconds"] is not None else start
            )
            facts["end_seconds"] = max(end, facts["end_seconds"]) if facts["end_seconds"] is not None else end
            if index == 1:
                if size % (2 * self.channels):
                    raise ValidationError("pcm_alignment_invalid")
                facts["pcm_bytes"] += size
                facts["pcm_samples_per_channel"] += size // (2 * self.channels)


class ADTSFacts:
    def __init__(self):
        self.pending = bytearray()
        self.facts = dict(adts_frames=0, aac_access_units=0, compressed_bytes=0)

    def __call__(self, chunk):
        self.pending.extend(chunk)
        while len(self.pending) >= 7:
            b = self.pending
            if b[0] != 0xFF or b[1] & 0xF6 != 0xF0 or (b[2] >> 2) & 15 >= 13:
                raise ValidationError("adts_structure_invalid")
            size = ((b[3] & 3) << 11) | (b[4] << 3) | (b[5] >> 5)
            if size < (7 if b[1] & 1 else 9):
                raise ValidationError("adts_structure_invalid")
            if len(b) < size:
                return
            self.facts["adts_frames"] += 1
            # ADTS number_of_raw_data_blocks_in_frame counts AAC access units.
            self.facts["aac_access_units"] += 1 + (b[6] & 3)
            self.facts["compressed_bytes"] += size
            del b[:size]


def _process_ok(result):
    return result["exit_code"] == 0 and not result["timed_out"] and not result["launch_failed"]


def _av_facts(facts):
    """Compare coverage boundaries; these differences do not measure clock drift."""
    result = {}
    for data in facts.values():
        start, end = data["start_seconds"], data["end_seconds"]
        data["duration_seconds"] = end - start if start is not None and end is not None else None
        if "pcm_duration_seconds" in data:
            duration = data["duration_seconds"]
            data["timeline_minus_pcm_duration_seconds"] = (
                duration - data["pcm_duration_seconds"] if duration is not None else None
            )
    video, audio = facts["video"], facts["audio"]
    for key, field in (("start_offset_seconds", "start_seconds"), ("end_offset_seconds", "end_seconds")):
        a, v = audio[field], video[field]
        result[key] = a - v if a is not None and v is not None else None
    start, end = result["start_offset_seconds"], result["end_offset_seconds"]
    result["coverage_offset_change_seconds"] = end - start if start is not None and end is not None else None
    result["drift_seconds"] = result["coverage_offset_change_seconds"]  # Legacy compatibility alias.
    result["interpretation"] = "coverage_boundaries_only_not_source_clock_drift_or_perceptual_sync"
    return result  # Positive offsets mean audio is later; this is not perceptual sync.


def verify(path, ffprobe="ffprobe", ffmpeg="ffmpeg", timeout=1800, minimum_duration=None):
    result = dict(verdict="fail", passed=False, failures=[], scope="structural_decode_and_timing_only")
    failures = result["failures"]
    try:
        if os.name != "posix":
            raise ValidationError("validation_platform_unsupported")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("timeout_invalid")
        if minimum_duration is not None and (not math.isfinite(minimum_duration) or minimum_duration <= 0):
            raise ValidationError("minimum_duration_invalid")
        deadline = time.monotonic() + timeout
        path = Path(path).resolve()
        if not path.is_file():
            raise ValidationError("input_not_regular_file")
        initial_stat = path.stat()
        result["ts"] = scan_ts(path, deadline)
        ts = result["ts"]
        result["ts_sha256"] = ts.pop("sha256")
        for key in (
            "sync_errors",
            "continuity_errors",
            "pcr_regressions",
            "transport_errors",
            "scrambled_packets",
            "adaptation_errors",
            "psi_crc_errors",
            "psi_structure_errors",
            "missing_pmt_pids",
        ):
            if ts[key]:
                failures.append("ts_" + key)
        if not has_signaled_pcr_progression(ts):
            failures.append("ts_pcr_progression_unverified")
        if ts["max_pcr_value_delta_seconds"] > MAX_PCR_VALUE_DELTA_SECONDS:
            failures.append("ts_pcr_value_delta_exceeded")
        for key in ("packets", "pat", "pmt", "pcr_samples"):
            if not ts[key]:
                failures.append("ts_missing_" + key)
        for pid, facts in ts["pes_payloads"].items():
            if any(
                facts[key]
                for key in ("header_errors", "truncated_pes", "orphan_payload_bytes", "extra_payload_bytes")
            ):
                failures.append("pes_payload_invalid_" + pid)
        metadata = bytearray()

        def collect(chunk):
            if len(metadata) + len(chunk) > 1 << 20:
                raise ValidationError("metadata_output_limit")
            metadata.extend(chunk)

        probe = run_process(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=index,codec_type,codec_name,width,height,sample_rate,channels:format=duration,size",
                "-of",
                "json",
                str(path),
            ],
            collect,
            deadline,
        )
        result["probe"] = probe
        result["ffprobe_exit"] = probe["exit_code"]
        if not _process_ok(probe):
            raise ValidationError("ffprobe_failed")
        if probe["diagnostic_lines"]:
            failures.append("ffprobe_diagnostics")
        try:
            media = json.loads(metadata)
            streams = [
                next(s for s in media["streams"] if s.get("codec_type") == kind)
                for kind in ("video", "audio")
            ]
            for stream in streams:
                if type(stream["index"]) is not int or not re.fullmatch(
                    r"[a-zA-Z0-9_]{1,64}", stream["codec_name"]
                ):
                    raise ValueError
            channels, sample_rate = int(streams[1]["channels"]), int(streams[1]["sample_rate"])
            if not 1 <= channels <= 64 or not 1 <= sample_rate <= 768000:
                raise ValueError
            for stream in streams:
                for key in ("width", "height", "channels", "sample_rate"):
                    if key in stream:
                        stream[key] = int(stream[key])
        except (ValueError, KeyError, TypeError, StopIteration):
            raise ValidationError("media_metadata_invalid_or_missing_av") from None
        if streams[0]["codec_name"] != "h264":
            failures.append("video_codec_unsupported")
        if streams[1]["codec_name"] != "aac":
            failures.append("audio_codec_unsupported")
        result["media"] = {
            "streams": [
                {
                    k: s[k]
                    for k in (
                        "index",
                        "codec_type",
                        "codec_name",
                        "width",
                        "height",
                        "sample_rate",
                        "channels",
                    )
                    if k in s
                }
                for s in streams
            ]
        }
        packets = PacketFacts(streams)
        lines = Lines(packets.line)
        status = run_process(
            [
                ffprobe,
                "-v",
                "error",
                "-show_packets",
                "-show_entries",
                "packet=stream_index,pts_time,dts_time,duration_time",
                "-of",
                "compact=p=0:nk=0",
                str(path),
            ],
            lines,
            deadline,
        )
        lines.finish()
        result["packets"] = dict(process=status, streams=packets.facts, malformed_rows=packets.malformed_rows)
        if not _process_ok(status) or status["diagnostic_lines"] or packets.malformed_rows:
            failures.append("packet_probe_failed")
        for kind, facts in packets.facts.items():
            if not facts["packets"]:
                failures.append(kind + "_packets_missing")
            for key in (
                "missing_pts",
                "missing_dts",
                "missing_duration",
                "nonmonotonic_dts",
                "pts_before_dts",
            ):
                if facts[key]:
                    failures.append(kind + "_" + key)
        if packets.facts["audio"]["pts_reorder_steps"]:
            failures.append("audio_pts_reorder_steps")
        if packets.facts["audio"]["pts_overlap_packets"]:
            failures.append("audio_packet_timeline_overlap")
        result["packet_av"] = _av_facts(packets.facts)
        decoded = DecodeFacts(channels)
        lines = Lines(decoded.line)
        status = run_process(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-err_detect",
                "explode",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-copyts",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "rawvideo",
                "-threads:v",
                "1",
                "-fps_mode:v",
                "passthrough",
                "-enc_time_base:v",
                "demux",
                "-c:a",
                "pcm_s16le",
                "-ac",
                str(channels),
                "-ar",
                str(sample_rate),
                "-f",
                "framehash",
                "-hash",
                "adler32",
                "pipe:1",
            ],
            lines,
            deadline,
        )
        lines.finish()
        result["decode"] = dict(process=status, streams=decoded.facts)
        if not _process_ok(status) or status["diagnostic_lines"]:
            failures.append("decode_failed")
        for kind, facts in decoded.facts.items():
            if not facts["frames"]:
                failures.append(kind + "_decoded_frames_missing")
        decoded.facts["audio"].update(sample_rate=sample_rate, channels=channels)
        decoded.facts["audio"]["pcm_duration_seconds"] = (
            decoded.facts["audio"]["pcm_samples_per_channel"] / sample_rate
        )
        result["decoded_av"] = _av_facts(decoded.facts)
        if minimum_duration is not None:
            video_duration = decoded.facts["video"]["duration_seconds"]
            audio_duration = decoded.facts["audio"]["pcm_duration_seconds"]
            result["duration_gate"] = {
                "minimum_seconds": minimum_duration,
                "video_seconds": video_duration,
                "audio_pcm_seconds": audio_duration,
                "passed": video_duration is not None
                and audio_duration is not None
                and video_duration >= minimum_duration
                and audio_duration >= minimum_duration,
            }
            if not result["duration_gate"]["passed"]:
                failures.append("minimum_duration_not_met")
        if streams[1]["codec_name"] == "aac":
            adts = ADTSFacts()
            status = run_process(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-c:a",
                    "copy",
                    "-f",
                    "adts",
                    "pipe:1",
                ],
                adts,
                deadline,
            )
            result["aac"] = dict(process=status, **adts.facts, trailing_bytes=len(adts.pending))
            if (
                not _process_ok(status)
                or status["diagnostic_lines"]
                or adts.pending
                or not adts.facts["aac_access_units"]
            ):
                failures.append("aac_validation_failed")
        else:
            failures.append("aac_stream_required")
        final_stat = path.stat()
        if any(
            getattr(initial_stat, key) != getattr(final_stat, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        ):
            failures.append("input_changed_during_validation")
    except ValidationError as error:
        failures.append(str(error))
    except OSError:
        failures.append("input_io_failed")
    except (ValueError, KeyError, TypeError, OverflowError):
        failures.append("validation_data_invalid")
    result["passed"] = not failures
    result["verdict"] = "pass" if result["passed"] else "fail"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--timeout", type=float, default=1800, help="Total wall-clock budget in seconds")
    parser.add_argument(
        "--minimum-duration",
        type=float,
        help="Require both decoded video coverage and decoded audio samples to meet this duration",
    )
    args = parser.parse_args()
    result = verify(args.path, args.ffprobe, args.ffmpeg, args.timeout, args.minimum_duration)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
