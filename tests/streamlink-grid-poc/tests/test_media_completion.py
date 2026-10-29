# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
from fractions import Fraction
from types import SimpleNamespace

import pytest

from soop_grid.media import MediaSink
from soop_grid.protocol import GridError
import soop_grid.stream as stream_module
from soop_grid.bootstrap import Config
from soop_grid.protocol import Network
from soop_grid.stream import receive, validate_media_output


def sink_with_timeline(video_count, audio_count, *, video_offset=0, audio_offset=0, extra_audio_samples=0):
    output = MediaSink(lambda _data: None)
    output.audio_config = SimpleNamespace(sample_rate=48000)
    for index in range(video_count):
        output._record_output(
            b"T" * 188,
            video=(b"same-picture-bytes", {"pts": video_offset + index * 3000, "decoder_prefix": b""}),
        )
    for index in range(audio_count):
        pts = Fraction(audio_offset, 1) + Fraction(index * 1024 * 90000, 48000)
        output._record_output(
            b"T" * 188,
            audio=[(b"aac", 1, 1024, pts)],
        )
    if extra_audio_samples:
        pts = Fraction(audio_offset, 1) + Fraction(audio_count * 1024 * 90000, 48000)
        output._record_output(
            b"T" * 188,
            audio=[(b"large-aac-bundle", 1, extra_audio_samples, pts)],
        )
    output.close()
    return output.stats


def test_track_span_check_uses_clock_progress_not_payload_identity():
    # This is an arithmetic unit test (not an AAC/H.264 parser test): repeated
    # payload bytes must not affect the independently supplied track clocks.
    facts = sink_with_timeline(video_count=30, audio_count=47)
    assert facts["av_track_span_status"] == "consistent"
    validate_media_output(facts)


@pytest.mark.parametrize(
    ("video_count", "audio_count"),
    [(10, 47), (30, 10)],
    ids=["video-stops-while-audio-progresses", "audio-stops-while-video-progresses"],
)
def test_track_stall_during_other_track_progress_is_not_complete(video_count, audio_count):
    facts = sink_with_timeline(video_count, audio_count)
    assert facts["av_track_span_status"] == "incomplete"
    with pytest.raises(GridError, match="media_track_coverage_incomplete"):
        validate_media_output(facts)


def test_a_single_video_picture_is_not_enough_to_claim_track_coverage():
    facts = sink_with_timeline(video_count=1, audio_count=47)
    assert facts["av_track_span_status"] == "unverified"
    with pytest.raises(GridError, match="media_track_coverage_unverified"):
        validate_media_output(facts)


def test_missing_audio_cannot_be_accepted_as_complete_video_recording():
    facts = sink_with_timeline(video_count=30, audio_count=0)
    assert facts["av_track_span_status"] == "incomplete"
    with pytest.raises(GridError, match="no_playable_grid_audio"):
        validate_media_output(facts)


def test_receive_checks_media_coverage_after_sink_final_drain(monkeypatch):
    closed = []
    session = SimpleNamespace(
        center=object(),
        bno=12345,
        clear_gateway_auth=lambda: None,
        clear=lambda: None,
    )
    credentials = SimpleNamespace(clear=lambda: None)

    class Sink:
        def __init__(self, *args, **kwargs):
            self.stats = {}

        def close(self):
            closed.append(True)
            self.stats.update(
                video_output_frames=10,
                audio_output_access_units=10,
                av_track_span_status="incomplete",
            )

    monkeypatch.setattr(stream_module, "bootstrap", lambda *a, **k: session)
    monkeypatch.setattr(stream_module, "obtain_certificate", lambda *a: credentials)
    monkeypatch.setattr(stream_module, "discover_parent", lambda *a: ([], 1))
    monkeypatch.setattr(stream_module, "MediaSink", Sink)
    monkeypatch.setattr(stream_module, "receive_media", lambda *a, **k: None)
    with pytest.raises(GridError, match="media_track_coverage_incomplete"):
        receive(Config("fixture_channel", "12345"), Network(), lambda _x: None, lambda _x: None, {})
    assert closed


def test_equal_length_but_disjoint_track_windows_are_not_consistent():
    facts = sink_with_timeline(video_count=30, audio_count=47, audio_offset=10 * 90000)
    assert facts["av_track_span_status"] == "incomplete"
    with pytest.raises(GridError, match="media_track_coverage_incomplete"):
        validate_media_output(facts)


def test_one_oversized_aac_bundle_cannot_hide_a_track_span_gap():
    facts = sink_with_timeline(video_count=30, audio_count=47, extra_audio_samples=48000 * 10)
    assert facts["audio_output_last_packet_duration_90k"] == 900000
    assert facts["av_output_coverage_edge_90k"] < 900000
    assert facts["av_track_span_status"] == "incomplete"
    with pytest.raises(GridError, match="media_track_coverage_incomplete"):
        validate_media_output(facts)
