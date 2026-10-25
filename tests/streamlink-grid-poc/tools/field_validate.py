#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Private bounded live-test supervisor; captures both CLI log channels as fixed codes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

from soop_grid.diagnostics import ERROR_CODES, EVENTS, STOP_REASONS


def log_code(line):
    if "No space left on device" in line or "Disk quota exceeded" in line:
        return "disk_write_error"
    if "Error when writing to output" in line:
        return "streamlink_output_error"
    words = set(re.findall(r"[a-z][a-z0-9_]{2,80}", line))
    known = sorted(words & (ERROR_CODES | EVENTS | STOP_REASONS))
    return known[0] if known else "redacted_cli_line"


def atomic_json(path, data):
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def resources(pid):
    result = {}
    try:
        text = Path(f"/proc/{pid}/status").read_text()
        for field, key in (("VmRSS", "rss_bytes"), ("VmHWM", "rss_peak_bytes"), ("Threads", "threads")):
            match = re.search(rf"^{field}:\s+(\d+)", text, re.M)
            if match:
                result[key] = int(match[1]) * (1024 if field != "Threads" else 1)
        result["open_fds"] = len(list(Path(f"/proc/{pid}/fd").iterdir()))
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        result["cpu_seconds"] = (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        pass
    return result


def last_heartbeat(path):
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def oom_counters():
    try:
        lines = Path("/sys/fs/cgroup/memory.events").read_text().splitlines()
        return {
            key: int(value) for key, value in (line.split() for line in lines) if key in ("oom", "oom_kill")
        }
    except (OSError, ValueError):
        return {}


def supervise(command, directory, timeout, env=None):
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    oom_before = oom_counters()
    lock = threading.Lock()
    fd = os.open(directory / "process.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    events = os.fdopen(fd, "w", encoding="utf-8")
    counts = {"stdout": 0, "stderr": 0}
    seen_codes = set()
    io_failed = [False]

    def record(data):
        with lock:
            if not io_failed[0]:
                try:
                    events.write(
                        json.dumps({"elapsed_seconds": round(time.monotonic() - started, 3), **data}) + "\n"
                    )
                    events.flush()
                except OSError:
                    io_failed[0] = True  # Keep draining pipes even when diagnostic storage fails.

    def drain(pipe, source):
        for line in iter(lambda: pipe.readline(8192), b""):
            code = log_code(line.decode("utf-8", "replace"))
            counts[source] += 1
            seen_codes.add(code)
            record({"event": "cli_log", "source": source, "code": code})
        pipe.close()

    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError:
        events.close()
        raise SystemExit("Streamlink process launch failed; command and paths redacted") from None
    record({"event": "process_started", "command": "python -m streamlink_cli <redacted-target-and-paths>"})
    readers = [
        threading.Thread(target=drain, args=(pipe, source))
        for pipe, source in ((proc.stdout, "stdout"), (proc.stderr, "stderr"))
    ]
    for reader in readers:
        reader.start()
    peak = 0
    resource_peaks = {}
    last_cpu = None
    samples = 0
    timed_out = False
    try:
        while proc.poll() is None:
            if time.monotonic() - started > timeout:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            facts = resources(proc.pid)
            peak = max(peak, facts.get("rss_peak_bytes", facts.get("rss_bytes", 0)))
            for key in ("threads", "open_fds"):
                if key in facts:
                    resource_peaks[key] = max(resource_peaks.get(key, 0), facts[key])
            last_cpu = facts.get("cpu_seconds", last_cpu)
            output = directory / "recording.ts"
            if output.exists():
                st = output.stat()
                facts.update(output_bytes=st.st_size, output_mtime_ns=st.st_mtime_ns)
            record({"event": "process_heartbeat", **facts})
            samples += 1
            time.sleep(1)
    except KeyboardInterrupt:
        record({"event": "supervisor_interrupted"})
        proc.send_signal(signal.SIGINT)
    finally:
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        for reader in readers:
            reader.join(5)
        heartbeat = last_heartbeat(directory / "receiver" / "events.jsonl")
        receiver_summary = None
        try:
            receiver_summary = json.loads((directory / "receiver" / "summary.json").read_text())
        except (OSError, ValueError):
            pass
        reason = receiver_summary.get("stop_reason") if receiver_summary else None
        if proc.returncode < 0:
            reason = "process_signal"
        elif "disk_write_error" in seen_codes:
            reason = "disk_write_error"
        elif "streamlink_output_error" in seen_codes:
            reason = "streamlink_output_error"
        elif proc.returncode != 0 and reason in (
            "duration_limit",
            "byte_limit",
            "receiver_completed_unknown",
        ):
            reason = "process_external_termination_unknown"
        elif not reason:
            reason = "process_external_termination_unknown"
        result = {
            "started_utc": started_utc,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "process_exit_code": proc.returncode,
            "termination_signal": -proc.returncode if proc.returncode < 0 else None,
            "supervisor_timeout": timed_out,
            "stop_reason": reason,
            "rss_peak_bytes": peak,
            "resource_peaks": resource_peaks,
            "cpu_seconds_last_sample": last_cpu,
            "resource_samples": samples,
            "cli_log_counts": counts,
            "cli_codes": sorted(seen_codes),
            "last_heartbeat": heartbeat,
            "receiver_summary": receiver_summary,
            "oom_evidence": "not_observed_or_not_accessible",
            "supervisor_diagnostic_io_failed": io_failed[0],
        }
        oom_after = oom_counters()
        if oom_before and oom_after:
            result["visible_cgroup_oom_counter_delta"] = {k: oom_after[k] - oom_before[k] for k in oom_before}
            result["oom_counter_scope"] = "visible_root_cgroup_not_process_specific"
        output = directory / "recording.ts"
        if output.exists():
            result["output_bytes"] = output.stat().st_size
            result["output_mtime_ns"] = output.stat().st_mtime_ns
        try:
            atomic_json(directory / "process-summary.json", result)
        except OSError:
            result["supervisor_diagnostic_io_failed"] = True
        record({"event": "process_finished", "exit_code": proc.returncode, "stop_reason": reason})
        try:
            events.close()
        except OSError:
            result["supervisor_diagnostic_io_failed"] = True
    return result


def public_target():
    import requests

    url = "https://live.sooplive.com/api/main_broad_list_api.php"
    response = requests.get(
        url,
        params={"lang": "ko_KR", "orderType": "view_cnt", "pageNo": 1, "selectType": "action"},
        timeout=(3, 8),
    )
    response.raise_for_status()
    for item in response.json().get("broad", [])[:8]:
        if str(item.get("broad_grade")) == "19":
            continue
        if str(item.get("is_password")).upper() not in ("N", "0", "FALSE"):
            continue
        if str(item.get("subscription_only")).upper() not in ("N", "0", "FALSE"):
            continue
        channel, bno = item.get("user_id"), str(item.get("broad_no", ""))
        if isinstance(channel, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", channel) and bno.isdigit():
            # Receiver verifies exact Live identity and requires a positive Watch ticket.
            return f"https://play.sooplive.com/{channel}/{bno}"
    raise SystemExit("No explicitly public candidate in the limited listing sample")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--python", required=True, help="Clean Streamlink-only interpreter")
    p.add_argument("--plugin-dir", required=True)
    p.add_argument("--directory", type=Path, required=True, help="New private output directory")
    p.add_argument("--duration", type=int, default=300)
    p.add_argument("--max-bytes", type=int, default=2_000_000_000)
    p.add_argument(
        "--capture-raw", action="store_true", help="Private raw media and first rejected C748 packet (large)"
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--url")
    target.add_argument("--discover-public", action="store_true")
    args = p.parse_args()
    if args.duration <= 0 or args.max_bytes <= 0:
        p.error("positive limits required")
    url = public_target() if args.discover_public else args.url
    env = dict(os.environ)
    for name in ("PYTHONPATH", "SOOP_GRID_CAPTURE_DIR", "SOOP_GRID_DIAGNOSTICS_DIR"):
        env.pop(name, None)
    if args.discover_public:
        for name in ("SOOP_ACCOUNT_ID", "SOOP_ACCOUNT_PASSWORD", "SOOP_STREAM_PASSWORD"):
            env.pop(name, None)
    if args.capture_raw:
        env["SOOP_GRID_CAPTURE_DIR"] = str(args.directory / "private-capture")
    command = [
        args.python,
        "-m",
        "streamlink_cli",
        "--plugin-dir",
        args.plugin_dir,
        "--loglevel",
        "debug",
        "--soop-grid-transport",
        "grid",
        "--soop-grid-duration",
        str(args.duration),
        "--soop-grid-max-bytes",
        str(args.max_bytes),
        "--soop-grid-diagnostics",
        str(args.directory / "receiver"),
        url,
        "original",
        "-o",
        str(args.directory / "recording.ts"),
    ]
    result = supervise(command, args.directory, args.duration + 120, env)
    artifact = Path(args.plugin_dir) / "soop_grid.py"
    result.update(
        configured_duration=args.duration,
        configured_max_wire_bytes=args.max_bytes,
        plugin_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    try:
        atomic_json(args.directory / "process-summary.json", result)
    except OSError:
        result["supervisor_diagnostic_io_failed"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
