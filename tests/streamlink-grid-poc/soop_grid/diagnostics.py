# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Opt-in diagnostics: fixed codes and numeric facts, never exception text or targets."""

import errno
import json
import logging
import math
import os
from pathlib import Path
import threading
import time

from .protocol import Cancelled, GridError

_log = logging.getLogger("streamlink.plugins.soop_grid")

EVENTS = frozenset(
    [
        "account_session_confirmed",
        "center_join_accepted",
        "center_rediscovery_failed",
        "center_rediscovery_completed",
        "cleanup_failed_after_primary_error",
        "gateway_certificate_received",
        "gateway_login_accepted",
        "grid_duration_limit_reached",
        "grid_frame_gap_detected",
        "grid_frame_gap_recovered",
        "grid_frame_gap_unrecoverable",
        "grid_live_resumed",
        "live_push_transition_requested",
        "grid_media_byte_limit_reached",
        "heartbeat",
        "media_packet_rejected",
        "native_tk_ftk_metadata_received",
        "new_node_key_obtained",
        "parent_c748_media_received",
        "parent_candidate_failed",
        "parent_candidate_selected",
        "parent_connected",
        "parent_connection_lost",
        "parent_failure_detected",
        "parent_handshake_completed",
        "parent_last_media_received",
        "parent_tcp_connect_started",
        "parent_tcp_connected",
        "parent_discovery_received",
        "parent_failover_exhausted",
        "parent_failover_started",
        "parent_failover_succeeded",
        "parent_rediscovery_started",
        "recovery_completed",
        "recovery_started",
        "recovery_failed",
        "cache_request_sent",
        "cache_first_media_received",
        "overlap_frame_verified",
        "first_new_frame_accepted",
        "public_watch_ticket_received",
        "reader_closed",
        "reader_eof",
        "reader_error",
        "receiver_started",
        "unknown_event",
        "worker_finished",
    ]
)

NUMERIC_FIELDS = frozenset(
    """
media_packets payload_bytes wire_bytes parent_wire_bytes video_frames audio_frames sequence_discontinuities
parent_connections failover_attempts failover_successes rediscoveries duplicate_frames_excluded
gap_events recovered_ranges unrecoverable_ranges parent_connection_attempts parent_packets
last_normal_frame last_video_dts_90k last_video_pts_90k active_parents last_control_command
cache_request_count last_cache_request_start last_media_monotonic
failure_detection_ms candidate_selection_ms center_rediscovery_ms parent_tcp_connect_ms
parent_handshake_ms cache_first_byte_ms overlap_verification_ms first_new_frame_ms
total_recovery_ms cache_frames_received cache_frames_replayed overlap_duplicates_discarded
missing_frames frame_identity_conflicts recovery_wire_bytes recovery_media_bytes
recovery_success recovery_failures recovery_failure_attempts catchup_duration_ms catchup_throughput_bytes_per_sec
failover_backoff_ms_saved
rejected_body_bytes rejected_header_bytes rejected_extension_bytes rejected_payload_bytes
rejected_packet_preservation_failed
center_rediscovery_failed
extended_media_packets media_extension_bytes
""".split()
)


def safe_error(error):
    code = str(error) if isinstance(error, GridError) else "receiver_internal_failure"
    return code if code in ERROR_CODES else "receiver_internal_failure"


STOP_REASONS = frozenset(
    """user_cancelled duration_limit byte_limit network_eof network_timeout
parent_stalled parent_retry_exhausted center_rediscovery_failed authentication_denied
cache_overlap_unavailable frame_sequence_gap media_timestamp_invalid buffer_backpressure
disk_write_error receiver_internal_error receiver_error receiver_completed_unknown""".split()
)

TRACE_FUNCTIONS = frozenset(
    """receive receive_media parse_media read_frame read_exact
inspect add write close put _wait discover_parent obtain_certificate bootstrap
auth_body join_body parse_parents accept reject""".split()
)


def safe_sites(error):
    sites = []
    trace = error.__traceback__
    while trace is not None:
        name = trace.tb_frame.f_code.co_name
        if name.startswith("_sg_"):
            name = name.split("_", 3)[-1]
        if name in TRACE_FUNCTIONS:
            sites.append({"function": name, "line": trace.tb_lineno})
        trace = trace.tb_next
    return sites[-4:]


def stop_reason(error):
    code = safe_error(error)
    if isinstance(error, Cancelled):
        return "user_cancelled"
    if isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EIO, errno.EDQUOT):
        return "disk_write_error"
    if "denied" in code or "verification_required" in code:
        return "authentication_denied"
    if code == "buffer_backpressure_timeout":
        return "buffer_backpressure"
    if code == "peer_closed_or_truncated":
        return "network_eof"
    if code in ("network_read_timeout", "reader_timeout"):
        return "network_timeout"
    if code == "parent_media_stalled":
        return "parent_stalled"
    if code in ("parent_retry_limit", "recovery_candidates_exhausted"):
        return "parent_retry_exhausted"
    if code == "recovery_overlap_unavailable":
        return "cache_overlap_unavailable"
    if code in ("frame_sequence_gap_or_reset", "frame_identity_conflict"):
        return "frame_sequence_gap"
    if code.startswith(
        ("h264_", "media_dts_", "media_pts_", "media_timestamp_", "grid_video_", "frame_timestamp_")
    ):
        return "media_timestamp_invalid"
    return "receiver_internal_error" if code == "receiver_internal_failure" else "receiver_error"


class Diagnostics:
    def __init__(self, directory, snapshot):
        self.started = time.monotonic()
        self.snapshot = snapshot
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.reason = None
        self.error_code = None
        self.exception_category = None
        self.error_sites = []
        self.last_transport_error = None
        self.stage = "initializing"
        self.failed = False
        self.path = Path(directory) if directory else None
        self.file = None
        if self.path:
            try:
                self.path.mkdir(mode=0o700, parents=False, exist_ok=False)
                fd = os.open(self.path / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                self.file = os.fdopen(fd, "w", encoding="utf-8")
            except OSError:
                raise GridError("diagnostic_write_failed") from None

    def facts(self):
        data = self.snapshot()
        result = {"elapsed_seconds": round(time.monotonic() - self.started, 3), "stage": self.stage}
        for key in NUMERIC_FIELDS | {"buffer_used_bytes", "buffer_peak_bytes", "active_sockets"}:
            value = data.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                if key == "last_media_monotonic":
                    result["last_media_elapsed_seconds"] = round(value - self.started, 3)
                else:
                    result[key] = value
        state = data.get("reader_state")
        if state in ("initializing", "streaming", "eof", "error", "closed"):
            result["reader_state"] = state
        catchup_state = data.get("catchup_state")
        if catchup_state in ("not_started", "unverified_live_boundary"):
            result["catchup_state"] = catchup_state
        recovery_failure_code = data.get("recovery_failure_code")
        if recovery_failure_code in ERROR_CODES:
            result["recovery_failure_code"] = recovery_failure_code
        result.update(
            stop_reason=self.reason,
            error_code=self.error_code,
            exception_category=self.exception_category,
            diagnostic_io_failed=self.failed,
            error_sites=self.error_sites,
            last_transport_error=self.last_transport_error,
        )
        return result

    def _io_failure(self):
        if not self.failed:
            _log.error("SOOP Grid diagnostic_write_failed")
        self.failed = True

    def event(self, stage=None, event="heartbeat"):
        # Callers supply fixed internal literals; unknown event strings are dropped.
        with self.lock:
            if event in (
                "peer_closed_or_truncated",
                "network_read_timeout",
                "network_read_failed",
                "network_write_failed",
                "tcp_connect_failed",
                "parent_media_stalled",
            ):
                self.last_transport_error = event
            if stage is not None:
                self.stage = (
                    stage
                    if stage
                    in (
                        "receiver",
                        "bootstrap",
                        "gateway",
                        "center",
                        "parent",
                        "reader",
                        "cleanup",
                        "initializing",
                    )
                    else "receiver"
                )
            if self.file and not self.failed:
                try:
                    self.file.write(
                        json.dumps(
                            {
                                **self.facts(),
                                "event": event
                                if event in EVENTS or event in ERROR_CODES
                                else "unknown_event",
                            }
                        )
                        + "\n"
                    )
                    self.file.flush()
                except OSError:
                    self._io_failure()

    def fail(self, error, reason=None):
        with self.lock:
            if self.reason is None:
                self.reason = reason if reason in STOP_REASONS else stop_reason(error)
                self.error_code = safe_error(error)
                self.error_sites = safe_sites(error)
                self.exception_category = (
                    "grid_error"
                    if isinstance(error, GridError)
                    else "os_error"
                    if isinstance(error, OSError)
                    else "unexpected_exception"
                )

    def complete(self, reason):
        with self.lock:
            if self.reason is None:
                self.reason = reason if reason in STOP_REASONS else "receiver_completed_unknown"

    def summary(self):
        with self.lock:
            if not self.path or self.failed:
                return
            temporary = self.path / "summary.tmp"
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(self.facts(), out, indent=2)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, self.path / "summary.json")
            except OSError:
                self._io_failure()

    def start(self):
        if self.file:
            self.event(event="receiver_started")
            self.thread = threading.Thread(target=self._heartbeat, name="soop-grid-diagnostics", daemon=True)
            self.thread.start()

    def _heartbeat(self):
        while not self.stop.wait(2):
            self.event()

    def close(self):
        self.stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(3)
        with self.lock:
            self.summary()
            if self.file:
                try:
                    self.file.close()
                except OSError:
                    self._io_failure()
                self.file = None


# Explicit source-defined codes. Unknown exception strings are never exported.
ERROR_CODES = frozenset(
    [
        "aac_pes_limit",
        "account_credentials_required",
        "account_input_invalid",
        "account_login_denied_or_verification_required",
        "account_session_unconfirmed",
        "adts_header_invalid",
        "adts_sample_rate_invalid",
        "adult_check_unconfirmed",
        "audio_prefix_unconfirmed",
        "bootstrap_schema_invalid",
        "broadcast_number_bounds",
        "broadcast_target_invalid",
        "buffer_backpressure_timeout",
        "cache_frame_bounds",
        "cache_request_limit",
        "capture_byte_limit_bounds",
        "capture_close_failed",
        "capture_deadline_reached",
        "capture_duration_bounds",
        "capture_limits_invalid",
        "center_access_denied",
        "center_body_limit",
        "center_init_unexpected",
        "center_rediscovery_failed",
        "center_rediscovery_failed",
        "center_session_missing",
        "certificate_length_invalid",
        "channel_live_number_unavailable",
        "control_checksum_invalid",
        "diagnostic_write_failed",
        "first_party_http_failed",
        "first_party_http_rejected",
        "first_party_json_invalid",
        "frame_identity_conflict",
        "frame_sequence_gap_or_reset",
        "frame_timestamp_regression",
        "gateway_body_limit",
        "gateway_certificate_denied",
        "gateway_certificate_fields_invalid",
        "gateway_cookie_missing_or_invalid",
        "gateway_login_denied",
        "gateway_login_fields_invalid",
        "gateway_login_response_layout",
        "gateway_login_unterminated_field",
        "gateway_negative_reply",
        "gateway_ticket_missing_or_invalid",
        "grid_video_dts_not_increasing",
        "guid_invalid",
        "h264_chroma_unsupported",
        "h264_codec_configuration_changed",
        "h264_dimensions_invalid",
        "h264_field_bounds",
        "h264_golomb_limit",
        "h264_header_truncated",
        "h264_hrd_limit",
        "h264_interlaced_unsupported",
        "h264_level_dpb_invalid",
        "h264_multiple_pictures_per_grid_chunk",
        "h264_nal_header_invalid",
        "h264_parameter_set_missing",
        "h264_poc_mode_unsupported",
        "h264_poc_regression_out_of_bound",
        "h264_poc_width_invalid",
        "h264_pps_id_limit",
        "h264_pts_before_dts",
        "h264_reorder_bound_invalid",
        "h264_reorder_bound_unconfirmed",
        "h264_reorder_limit",
        "h264_separate_colour_plane_unsupported",
        "h264_slice_count_limit",
        "h264_slice_groups_unsupported",
        "h264_slice_type_invalid",
        "h264_sps_id_limit",
        "h264_switching_slice_unsupported",
        "h264_timing_changed",
        "h264_timing_unconfirmed",
        "h264_vcl_partition_or_extension_unsupported",
        "h264_vui_timing_invalid",
        "http_destination_invalid",
        "http_response_limit",
        "incoming_body_limit",
        "invalid_server_endpoint",
        "issued_certificate_invalid",
        "issued_seed_required",
        "issued_ticket_required",
        "join_field_bounds",
        "live_metadata_denied_or_offline",
        "live_target_changed",
        "media_dts_bounds",
        "media_dts_not_monotonic",
        "media_header_short",
        "media_kind_unconfirmed",
        "media_layout_unconfirmed",
        "media_pts_bounds",
        "media_timestamp_bounds",
        "network_read_failed",
        "network_read_timeout",
        "network_write_failed",
        "no_playable_grid_video",
        "no_usable_server_parent",
        "parent_access_denied",
        "parent_body_limit",
        "parent_discovery_missing",
        "parent_header_invalid",
        "parent_list_invalid",
        "parent_list_length",
        "parent_list_short",
        "parent_media_stalled",
        "parent_packet_limit",
        "parent_quality_changed",
        "parent_rediscovery_empty",
        "parent_retry_limit",
        "password_state_unknown",
        "peer_closed_or_truncated",
        "private_capture_create_failed",
        "public_policy_restricted_or_unknown",
        "public_watch_access_denied",
        "public_watch_schema_invalid",
        "reader_closed",
        "reader_timeout",
        "receiver_closed",
        "receiver_internal_failure",
        "recovery_candidates_exhausted",
        "recovery_deadline_exhausted",
        "recovery_overlap_unavailable",
        "scoped_cookie_invalid",
        "sps_chroma",
        "sps_cycle_limit",
        "sps_dimensions_invalid",
        "sps_golomb_limit",
        "sps_poc_invalid",
        "sps_truncated",
        "stream_password_encoding_invalid",
        "stream_password_state_mismatch",
        "tcp_connect_failed",
        "video_prefix_unconfirmed",
        "watch_target_changed",
    ]
)
