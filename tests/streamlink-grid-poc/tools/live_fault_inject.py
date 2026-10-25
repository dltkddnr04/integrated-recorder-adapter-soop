#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Private, bounded live Grid test: close only this process's active Parent socket."""

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from field_validate import public_target  # noqa: E402
from soop_grid.auth import HTTP  # noqa: E402
from soop_grid.bootstrap import Config, bootstrap  # noqa: E402
from soop_grid.center import discover_parent  # noqa: E402
from soop_grid.diagnostics import Diagnostics, ERROR_CODES, EVENTS, safe_error  # noqa: E402
from soop_grid.gateway import obtain_certificate  # noqa: E402
from soop_grid.media import MediaSink, parse_media  # noqa: E402
from soop_grid.parent import receive_media  # noqa: E402
from soop_grid.protocol import GridError, Network  # noqa: E402


class ParentOnlyFaultNetwork(Network):
    def __init__(self, event):
        super().__init__()
        self.event = event
        self._id_key = secrets.token_bytes(32)
        self._addresses = {}
        self.last_connect_id = None
        self.parent_socket = None
        self.active_parent_id = None

    def _identify(self, address):
        value = f"{address[0]}:{address[1]}".encode("ascii")
        return hmac.new(self._id_key, value, hashlib.sha256).hexdigest()[:24]

    def connect(self, address):
        sock = super().connect(address)
        identity = self._identify(address)
        self._addresses[sock] = identity
        self.last_connect_id = identity
        return sock

    def send(self, sock, data):
        if len(data) >= 16:
            version, command = struct.unpack_from("<HH", data)
            if version == 0x202 and command == 0xCB2E:
                self.parent_socket = sock
                self.active_parent_id = self._addresses.get(sock)
        super().send(sock, data)

    def close_active_parent(self):
        sock = self.parent_socket
        if sock is None or sock.fileno() < 0:
            raise GridError("fault_injection_parent_socket_unavailable")
        identity = self.active_parent_id
        try:
            sock.shutdown(2)
        except OSError:
            pass
        sock.close()
        self.event("local_parent_socket_closed", parent_endpoint_id=identity)
        return identity

    def release(self, sock):
        if self.parent_socket is sock:
            self.parent_socket = None
        super().release(sock)


class FaultSink:
    def __init__(self, sink, network, report, max_faults, frames_between, started):
        self.inner = sink
        self.network = network
        self.report = report
        self.max_faults = max_faults
        self.frames_between = frames_between
        self.started = started
        self.stats = sink.stats
        self.mux = sink.mux
        self.records = []
        self.current = None
        self.accepted_since_recovery = 0
        self.seen_recoveries = 0
        self.last_accepted_identity = None

    def _elapsed(self):
        return round((time.monotonic() - self.started) * 1000, 3)

    def _identity(self, chunk):
        return {
            "frame_number": chunk.frame,
            "frame_kind": chunk.kind,
            "raw_timestamp": chunk.timestamp,
            "payload_length": len(chunk.payload),
            "payload_sha256": hashlib.sha256(chunk.payload).hexdigest(),
        }

    def add(self, frame, discard=False):
        chunk = parse_media(frame.body)
        identity = self._identity(chunk)
        if discard:
            record = self.current
            if record and chunk.frame == record["last_accepted_frame"]["frame_number"]:
                record["cache_overlap_frame_count"] += 1
                record["overlap_identity"] = identity
                record["overlap_identity_exact"] = identity == record["last_accepted_frame"]
            return self.inner.add(frame, discard=True)

        result = self.inner.add(frame, discard=False)
        self.last_accepted_identity = identity
        completed = int(self.stats.get("failover_successes", 0))
        if completed > self.seen_recoveries:
            self.seen_recoveries = completed
            self.accepted_since_recovery = 0
        self.accepted_since_recovery += 1

        if self.current is None and len(self.records) < self.max_faults:
            if self.accepted_since_recovery >= self.frames_between:
                parent_id = self.network.close_active_parent()
                item = {
                    "injection_number": len(self.records) + 1,
                    "injection_type": "INJECT_LOCAL_CLOSE",
                    "injection_monotonic_ms": self._elapsed(),
                    "last_accepted_frame": identity,
                    "parent_endpoint_id_before": parent_id,
                    "parent_endpoint_id_after": None,
                    "recovery_result": "PENDING",
                    "failure_code": None,
                    "center_rediscovery_count_before": int(self.stats.get("rediscoveries", 0)),
                    "parent_connections_before": int(self.stats.get("parent_connections", 0)),
                    "duplicate_frames_before": int(self.stats.get("duplicate_frames_excluded", 0)),
                    "gap_events_before": int(self.stats.get("gap_events", 0)),
                    "unrecoverable_ranges_before": int(self.stats.get("unrecoverable_ranges", 0)),
                    "cache_overlap_frame_count": 0,
                    "overlap_identity": None,
                    "overlap_identity_exact": None,
                    "events_ms": {},
                }
                self.records.append(item)
                self.current = item
                self.accepted_since_recovery = 0
                self.report("fault_injected_local_close", extra={"injection_number": item["injection_number"]})
        return result

    def close(self):
        return self.inner.close()

    def reject(self, frame):
        return self.inner.reject(frame)

    def finish_recovery(self, event, endpoint_id=None):
        item = self.current
        if item is None:
            return
        elapsed = self._elapsed()
        item["events_ms"][event] = elapsed
        if event == "parent_failure_detected":
            item["failure_detected_ms"] = elapsed
        elif event == "recovery_started":
            item["recovery_started_ms"] = elapsed
        elif event == "parent_tcp_connected":
            item["parent_tcp_connected_ms"] = elapsed
            item["parent_endpoint_id_after"] = endpoint_id
        elif event in (
            "new_node_key_obtained",
            "parent_handshake_completed",
            "cache_request_sent",
            "cache_first_media_received",
            "overlap_frame_verified",
            "first_new_frame_accepted",
            "recovery_completed",
        ):
            item[event + "_ms"] = elapsed
        if event == "overlap_frame_verified":
            item["cache_overlap_verified"] = True
        if event == "new_node_key_obtained":
            item["new_node_key_issued"] = True
        if event == "recovery_completed":
            item["recovery_result"] = "SUCCESS"
            item["recovery_duration_ms"] = self.stats.get("total_recovery_ms")
            item["cache_duplicate_discard_count"] = (
                int(self.stats.get("overlap_duplicates_discarded", 0))
                - item["duplicate_frames_before"]
            )
            item["missing_frame_count"] = int(self.stats.get("missing_frames", 0))
            item["identity_conflict_count"] = int(self.stats.get("frame_identity_conflicts", 0))
            item["center_rediscovery_count"] = (
                int(self.stats.get("rediscoveries", 0)) - item["center_rediscovery_count_before"]
            )
            item["parent_connection_count"] = (
                int(self.stats.get("parent_connections", 0)) - item["parent_connections_before"]
            )
            item["recovery_failure_code"] = self.stats.get("recovery_failure_code")
            if item["parent_endpoint_id_after"] is None:
                item["endpoint_relation"] = "ENDPOINT_IDENTITY_UNAVAILABLE"
            elif item["parent_endpoint_id_after"] == item["parent_endpoint_id_before"]:
                item["endpoint_relation"] = "SAME_ENDPOINT_RECONNECT"
            else:
                item["endpoint_relation"] = "DIFFERENT_ENDPOINT_RECONNECT"
            self.current = None
            self.accepted_since_recovery = 0

    def summary(self):
        for item in self.records:
            if self.current is item:
                item["recovery_result"] = "FAILED"
                item["recovery_failure_code"] = self.stats.get("recovery_failure_code") or "not_recovered_before_stop"
                item["missing_frame_count"] = int(self.stats.get("missing_frames", 0))
                item["identity_conflict_count"] = int(self.stats.get("frame_identity_conflicts", 0))
        return self.records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New private output directory")
    parser.add_argument("--faults", type=int, choices=(1, 3), required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--frames-between", type=int, default=100)
    parser.add_argument("--max-wire-bytes", type=int, default=256 << 20)
    parser.add_argument("--discover-public", action="store_true", required=True)
    args = parser.parse_args()
    duration_limit = 120 if args.faults == 1 else 180
    if not 30 <= args.duration <= duration_limit:
        parser.error(f"duration must be 30..{duration_limit} seconds for this bounded run")
    if not 100 <= args.frames_between <= 500:
        parser.error("frames-between must be 100..500")
    if not 16 << 20 <= args.max_wire_bytes <= 512 << 20:
        parser.error("max-wire-bytes must be between 16 and 512 MiB")

    run_dir = args.directory
    run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    events_path = run_dir / "fault-events.jsonl"
    events_fd = os.open(events_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    events_file = os.fdopen(events_fd, "w", encoding="utf-8")
    started = time.monotonic()
    stats = {}
    network = None
    diagnostics = None
    session = credentials = sink = config = None
    error_code = None
    target_url = None

    def event_writer(item):
        events_file.write(json.dumps(item, sort_keys=True) + "\n")
        events_file.flush()

    def report(event, extra=None):
        payload = {"elapsed_ms": round((time.monotonic() - started) * 1000, 3), "event": event}
        if extra:
            payload.update(extra)
        event_writer(payload)
        if diagnostics and event in EVENTS | ERROR_CODES:
            diagnostics.event("parent" if event.startswith(("parent", "cache", "overlap")) else None, event)
        if sink:
            sink.finish_recovery(event, network.last_connect_id if event == "parent_tcp_connected" else None)

    result = {
        "run_id": run_dir.name,
        "injection_type": "INJECT_LOCAL_CLOSE",
        "requested_injection_count": args.faults,
        "frames_between_injections": args.frames_between,
        "duration_limit_seconds": args.duration,
        "max_wire_bytes": args.max_wire_bytes,
        "target_identity_verified": False,
        "receiver_returned_normally": False,
        "stop_reason": None,
        "error_code": None,
        "parent_endpoint_ids_are_run_scoped_hmac": True,
    }
    try:
        target_url = public_target()
        # The URL and target identifiers are deliberately omitted from all output.
        channel, bno = target_url.rstrip("/").rsplit("/", 2)[-2:]
        config = Config(
            broadcaster=channel,
            bno=bno,
            duration=args.duration,
            max_bytes=args.max_wire_bytes,
            capture_dir=str(run_dir / "capture"),
        )
        network = ParentOnlyFaultNetwork(lambda name, **fields: event_writer({"elapsed_ms": round((time.monotonic() - started) * 1000, 3), "event": name, **fields}))
        diagnostics = Diagnostics(run_dir / "receiver", lambda: stats)
        diagnostics.start()
        report("receiver_started")
        session = bootstrap(config, report, http_factory=lambda: HTTP(network.stop))
        result["target_identity_verified"] = True  # Exact BNO/BJID and Watch checks passed in bootstrap.
        credentials = obtain_certificate(network, session, report)
        parents, node_key = discover_parent(network, session.center, credentials, report)
        result["initial_parent_candidate_count"] = len(parents)
        result["parent_endpoint_ids_initial"] = [network._identify(parent.address) for parent in parents]
        base_sink = MediaSink(lambda _data: None, str(run_dir / "capture"))
        stats.update(base_sink.stats)
        base_sink.stats = stats
        sink = FaultSink(base_sink, network, report, args.faults, args.frames_between, started)
        receive_media(
            network,
            parents,
            session.bno,
            node_key,
            sink,
            report,
            args.duration,
            args.max_wire_bytes,
            rediscover=lambda: discover_parent(network, session.center, credentials, report),
        )
        result["receiver_returned_normally"] = True
        result["stop_reason"] = stats.get("stop_reason")
        if not sink.mux.video_frames:
            raise GridError("no_playable_grid_video")
    except BaseException as exc:
        error_code = safe_error(exc)
        result["error_code"] = error_code
        if diagnostics:
            diagnostics.fail(exc)
    finally:
        if sink:
            try:
                sink.close()
            except Exception:
                result.setdefault("cleanup_error", "capture_close_failed")
        if network:
            network.close()
        if credentials:
            credentials.clear()
        if session:
            session.clear()
        if config:
            config.clear()
        if diagnostics:
            diagnostics.complete(stats.get("stop_reason", "receiver_error"))
            diagnostics.summary()
            diagnostics.close()
        events_file.close()

    result["injections"] = sink.summary() if sink else []
    result["stats"] = {
        name: stats.get(name)
        for name in (
            "parent_connections", "parent_connection_attempts", "rediscoveries", "failover_successes",
            "recovery_success", "recovery_failures", "last_normal_frame", "media_packets", "wire_bytes",
            "payload_bytes", "video_frames", "audio_frames", "duplicate_frames_excluded",
            "overlap_duplicates_discarded", "missing_frames", "frame_identity_conflicts",
            "unrecoverable_ranges", "sequence_discontinuities", "stop_reason", "recovery_failure_code",
        )
        if name in stats
    }
    result["capture_analysis"] = {
        key: stats[key]
        for key in (
            "video",
            "audio",
            "playable_video_frames",
            "media_timeline_seconds",
            "last_video_dts_90k",
            "last_video_pts_90k",
            "last_video_slice_type",
            "last_video_poc",
        )
        if key in stats
    }
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["receiver_process_exit_code"] = 0 if result["receiver_returned_normally"] else 1
    result["all_requested_recoveries_succeeded"] = (
        len(result["injections"]) == args.faults
        and all(item.get("recovery_result") == "SUCCESS" for item in result["injections"])
    )
    result["cache_overlap_identities_exact"] = all(
        item.get("overlap_identity_exact") is True for item in result["injections"]
    ) if result["injections"] else False
    result["all_recoveries_gap_free"] = all(
        item.get("missing_frame_count") == 0 and item.get("identity_conflict_count") == 0
        for item in result["injections"]
    ) if result["injections"] else False
    result["duration_limit_exit_0"] = (
        result["receiver_returned_normally"] and stats.get("stop_reason") == "duration_limit"
    )
    result["fault_event_log"] = "fault-events.jsonl"
    summary_path = run_dir / "live-fault-summary.json"
    fd = os.open(summary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as summary:
        json.dump(result, summary, indent=2)
        summary.write("\n")
        summary.flush()
        os.fsync(summary.fileno())
    print(json.dumps({key: value for key, value in result.items() if key != "capture_analysis"}, indent=2))
    return 0 if result["all_requested_recoveries_succeeded"] and result["duration_limit_exit_0"] and result["all_recoveries_gap_free"] and result["cache_overlap_identities_exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
