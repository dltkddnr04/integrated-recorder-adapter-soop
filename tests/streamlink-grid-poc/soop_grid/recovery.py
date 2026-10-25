# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Conservative overlap validation; synthetic-tested, not seamless-live guaranteed."""

import hashlib
from collections import OrderedDict

from .protocol import GridError


class Continuity:
    def __init__(self, stats, report):
        self.stats, self.report = stats, report
        self.recent = OrderedDict()
        self.last = None
        self.timestamps = {}
        self.recovering = False
        self.anchor = False
        self.pending_resume = False
        for name in (
            "parent_connections",
            "failover_attempts",
            "failover_successes",
            "rediscoveries",
            "duplicate_frames_excluded",
            "gap_events",
            "recovered_ranges",
            "unrecoverable_ranges",
        ):
            stats.setdefault(name, 0)

    def begin(self):
        was_recovering = self.recovering
        self.recovering = self.last is not None
        self.anchor = False
        if self.recovering and not was_recovering:
            self.stats["gap_events"] += 1
            self.report("grid_frame_gap_detected")

    def reject(self, code):
        if not self.recovering:
            self.stats["gap_events"] += 1
            self.report("grid_frame_gap_detected")
        self.stats["unrecoverable_ranges"] += 1
        self.report("grid_frame_gap_unrecoverable")
        raise GridError(code)

    def accept(self, chunk):
        identity = (chunk.kind, chunk.timestamp, hashlib.sha256(chunk.payload).digest())
        if chunk.frame in self.recent:
            if self.recent[chunk.frame] != identity:
                self.reject("frame_identity_conflict")
            if self.recovering and chunk.frame == self.last:
                self.anchor = True
            self.stats["duplicate_frames_excluded"] += 1
            return False
        if self.last is not None:
            if self.recovering and not self.anchor:
                self.reject("recovery_overlap_unavailable")
            if chunk.frame != self.last + 1:
                self.reject("frame_sequence_gap_or_reset")
        # Per elementary-stream timestamps: audio/video interleaving need not be ordered.
        kind = "video" if chunk.kind in ("I", "P", "B") else chunk.kind
        if chunk.timestamp < self.timestamps.get(kind, 0):
            self.reject("frame_timestamp_regression")
        self.timestamps[kind] = chunk.timestamp
        self.recent[chunk.frame] = identity
        if len(self.recent) > 128:
            self.recent.popitem(last=False)
        self.last = chunk.frame
        self.stats["last_normal_frame"] = chunk.frame
        self.pending_resume = self.recovering
        return True

    def committed(self):
        # Called only AFTER accepted payload/TS has been written successfully.
        if self.pending_resume:
            self.pending_resume = self.recovering = False
            self.stats["recovered_ranges"] += 1
            self.stats["failover_successes"] += 1
            self.report("grid_frame_gap_recovered")
            self.report("parent_failover_succeeded")
