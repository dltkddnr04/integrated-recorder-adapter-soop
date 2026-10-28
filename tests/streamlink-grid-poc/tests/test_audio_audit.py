"""Independent synthetic AAC parsing, muxing, PCM and clock audit.

No network, credentials, or captured media. FFmpeg fixtures use distinct L/R tones.
Run with SOOP_TEST_FFMPEG and SOOP_TEST_FFPROBE pointing at the audit binaries.
"""

from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import media_body
from soop_grid.aac import MAX_ADTS_BYTES, MAX_ADTS_FRAMES, parse_adts
from soop_grid.h264clock import H264Clock
from soop_grid.media import MediaSink, adts_info
from soop_grid.mpegts import TSMux, nals
from soop_grid.protocol import Frame, GridError


BASE = 50_000_000
RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def header(size=8, index=3, channels=2, blocks=0, crc=False, profile=1):
    """Construct framing only; body is intentionally not a decodable AAC fixture."""
    return bytes(
        (
            255,
            0xF0 if crc else 0xF1,
            (profile << 6) | (index << 2) | (channels >> 2),
            ((channels & 3) << 6) | (size >> 11),
            (size >> 3) & 255,
            ((size & 7) << 5) | 31,
            0xFC | blocks,
        )
    )


def walk(data):
    """Independent framing oracle; samples are AAC core samples per channel.

    CRC values and raw_data_block syntax are not decoded by this helper. For
    protected multiple-block frames, extra syntax is inside the declared frame.
    """
    result = []
    offset = 0
    while offset < len(data):
        rest = data[offset:]
        if len(rest) < 7 or rest[0] != 255 or rest[1] & 0xF6 != 0xF0:
            raise ValueError("header")
        index = (rest[2] >> 2) & 15
        if index >= len(RATES):
            raise ValueError("sample rate")
        length = ((rest[3] & 3) << 11) | (rest[4] << 3) | (rest[5] >> 5)
        minimum = 7 if rest[1] & 1 else 9
        if length < minimum or length > len(rest):
            raise ValueError("length")
        blocks = (rest[6] & 3) + 1
        result.append((rest[:length], RATES[index], 1024 * blocks))
        offset += length
    if not result:
        raise ValueError("empty")
    return result


def add_audio(sink, payload, number=1, timestamp=BASE):
    return sink.add(Frame(0xC748, media_body(payload, "A", number, timestamp)))


def pes_packets(data):
    """Recover audio PES including PTS, independently of the production mux."""
    result = []
    for offset in range(0, len(data), 188):
        packet = data[offset : offset + 188]
        assert len(packet) == 188 and packet[0] == 0x47
        if ((packet[1] & 31) << 8) | packet[2] != 0x101:
            continue
        pos = 4 + (1 + packet[4] if packet[3] & 32 else 0)
        if packet[1] & 64:
            result.append(bytearray())
        result[-1].extend(packet[pos:])
    output = []
    for packet in result:
        assert packet[:4] == b"\0\0\1\xc0"
        assert int.from_bytes(packet[4:6], "big") == len(packet) - 6
        p = packet[9:14]
        pts = ((p[0] >> 1) & 7) << 30 | p[1] << 22 | (p[2] >> 1) << 15 | p[3] << 7 | p[4] >> 1
        output.append((pts, bytes(packet[9 + packet[8] :])))
    return output


@pytest.mark.parametrize(
    "bad",
    [
        header(0),
        header(6),
        header(100) + b"x",
        header(8, crc=True) + b"x",
        header(8) + b"xjunk",
        header(8) + b"x" + header(100) + b"x",
        header(8) + b"x" + header(8, index=15) + b"x",
        header(8) + b"x" + bytes((255, 0xF7)) + header(8)[2:] + b"x",
    ],
)
def test_rejects_invalid_adts_boundaries(bad):
    with pytest.raises(ValueError):
        walk(bad)
    with pytest.raises(GridError):
        adts_info(bad)


@pytest.mark.parametrize("blocks", [0, 1, 2, 3])
def test_oracle_counts_raw_data_blocks_and_crc_header(blocks):
    # Framing-only fixtures, not claims of decoder support for multiple blocks.
    frames = walk(header(16, blocks=blocks, crc=True) + b"\0" * 9)
    assert frames[0][2] == 1024 * (blocks + 1)


def test_channel_configuration_is_not_a_channel_count():
    assert adts_info(header(8, channels=7) + b"x")["channels"] == 8


def test_channel_configuration_zero_requires_pce_or_rejection():
    with pytest.raises(GridError):
        adts_info(header(8, channels=0) + b"x")


@pytest.mark.parametrize(
    "second",
    [
        header(8, index=4),
        header(8, channels=1),
        header(8, profile=0),
        bytes((255, 0xF9)) + header(8)[2:],
    ],
)
def test_config_changes_fail_closed(second):
    sink = MediaSink(lambda _: None)
    try:
        add_audio(sink, header(8) + b"x")
        with pytest.raises(GridError):
            add_audio(sink, second + b"x", number=2, timestamp=BASE + 213333)
    finally:
        sink.close()


def test_message_au_and_core_sample_counts_are_separate():
    sink = MediaSink(lambda _: None)
    try:
        add_audio(sink, (header(8) + b"x") * 2)
        assert sink.stats["audio_frames"] == 1  # Keep existing diagnostics compatible.
        assert sink.stats.get("audio_adts_frames") == 2
        assert sink.stats.get("audio_access_units") == 2
        assert sink.stats.get("audio_core_samples") == 2048
    finally:
        sink.close()


def test_exact_pes_payload_limit_and_failed_primitive_call_is_atomic():
    mux = TSMux()
    mux.started = True
    # Eight independently framed ADTS AUs, total exactly 65,527 bytes.
    payload = (header(8191) + b"x" * 8184) * 7 + header(8190) + b"x" * 8183
    assert len(payload) == 65527 and len(walk(payload)) == 8
    assert pes_packets(mux.add_audio(90000, payload)) == [(90000, payload)]
    too_large = payload + header(8) + b"x"
    with pytest.raises(GridError, match="aac_pes_limit"):
        mux._pes(False, 90000, too_large)
    assert mux.audio_chunks == 1


@pytest.mark.parametrize("index,rate", [(3, 48000), (4, 44100)])
def test_large_adts_chunk_splits_on_boundaries(index, rate):
    mux = TSMux()
    mux.started = True
    payload = (header(8191, index=index) + b"x" * 8184) * 9
    packets = pes_packets(mux.add_audio(90000, payload))
    assert len(packets) == 2
    assert b"".join(data for _, data in packets) == payload
    for _, data in packets:
        walk(data)
    # Eight maximum-size frames total 65,528 bytes: exactly one byte too many.
    assert len(packets[0][1]) == 7 * 8191
    assert packets[1][0] == 90000 + round(Fraction(7 * 1024 * 90000, rate))


def test_absolute_audio_clock_has_no_six_hour_rescale_drift():
    clock = H264Clock()
    clock.origin_dts, clock.reorder_lead = BASE, 6000
    origin = clock.audio_pts(BASE)
    for rate in (44100, 48000):
        for samples in (0, 1024, rate * 60, rate * 3600, rate * 21600):
            raw = BASE + samples * 10_000_000 // rate
            expected = Fraction(samples * 90000, rate) + origin
            actual = clock.audio_pts(raw)
            # Nearest-tick accuracy plus at most 100 ns input quantization.
            assert abs(expected - actual) <= Fraction(509, 1000)
    assert clock.audio_pts(BASE + 2_500_000) == origin + 22500
    assert clock.audio_pts(BASE - 2_500_000) == origin - 22500


def test_clock_trusts_grid_timestamps_including_drift_and_regression():
    clock = H264Clock()
    assert clock.audio_pts(BASE) is None
    clock.origin_dts, clock.reorder_lead = BASE, 0
    # A 100 ppm error in Grid audio timestamps gives 360 ms error after an hour.
    assert clock.audio_pts(BASE + 36_003_600_000) - clock.audio_pts(BASE + 36_000_000_000) == 32400
    assert clock.audio_pts(BASE) == clock.audio_pts(BASE)
    assert clock.audio_pts(BASE - 20_000_000) == -90000
    mux = TSMux()
    mux.started = True
    with pytest.raises(GridError, match="media_pts_bounds"):
        mux.add_audio(clock.audio_pts(BASE - 20_000_000), header(8) + b"x")


@pytest.fixture(scope="module")
def audio_tools():
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("SOOP_TEST_FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg/ffprobe required for synthetic audio audit")
    return ffmpeg, ffprobe


def run(tool, *args):
    result = subprocess.run(
        [tool, "-v", "error", "-nostdin", *map(str, args)]
        if Path(tool).name == "ffmpeg"
        else [tool, "-v", "error", *map(str, args)],
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout


@pytest.fixture(scope="module")
def tone_aac(audio_tools, tmp_path_factory):
    path = tmp_path_factory.mktemp("audio-audit") / "tones.aac"
    run(
        audio_tools[0],
        "-f",
        "lavfi",
        "-i",
        "aevalsrc=0.25*sin(2*PI*440*t)|0.125*sin(2*PI*997*t):s=48000",
        "-frames:a",
        2016,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-f",
        "adts",
        path,
    )
    frames = walk(path.read_bytes())
    # The encoder may flush one additional priming/tail packet. Select exact
    # ADTS boundaries so the audit duration is independent of encoder flushing.
    assert len(frames) >= 2016
    frames = frames[:2016]
    path.write_bytes(b"".join(frame for frame, _, _ in frames))
    return path, [frame for frame, _, _ in frames]


def grouped_sink(frames, video, *, wrong_timestamps=False):
    output = []
    sink = MediaSink(output.append)
    events = [(BASE + i * 10_000_000 // 30, kind, payload) for i, (payload, kind) in enumerate(video)]
    for i in range(87):
        start, end = i * len(frames) // 87, (i + 1) * len(frames) // 87
        stamp = i * 100_000 if wrong_timestamps else start * 1024 * 10_000_000 // 48000
        events.append((BASE + stamp, "A", b"".join(frames[start:end])))
    try:
        for number, (timestamp, kind, payload) in enumerate(sorted(events, key=lambda e: e[0]), 1):
            sink.add(Frame(0xC748, media_body(payload, kind, number, timestamp)))
    finally:
        sink.close()
    return b"".join(output), sink.stats


@pytest.mark.parametrize("wrong_timestamps", [False, True], ids=["correct-clock", "overlapping-clock"])
def test_87_messages_preserve_2016_aus_and_pcm_but_pcm_alone_cannot_prove_sync(
    tone_aac, encoded_bframes, audio_tools, tmp_path, wrong_timestamps
):
    raw, frames = tone_aac
    if wrong_timestamps:
        with pytest.raises(GridError, match="audio_"):
            grouped_sink(frames, encoded_bframes, wrong_timestamps=True)
        return
    ts, stats = grouped_sink(frames, encoded_bframes, wrong_timestamps=wrong_timestamps)
    path = tmp_path / "grouped.ts"
    path.write_bytes(ts)
    pes = pes_packets(ts)
    assert stats["audio_frames"] == 87 and len(pes) == 87
    assert stats["audio_adts_frames"] == stats["audio_access_units"] == 2016
    assert stats["audio_core_samples"] == stats["audio_output_core_samples"] == 2016 * 1024
    assert b"".join(payload for _, payload in pes) == raw.read_bytes()
    assert sum(len(walk(payload)) for _, payload in pes) == 2016
    reference = run(audio_tools[0], "-i", raw, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    actual = run(audio_tools[0], "-i", path, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    assert actual == reference
    assert len(actual) // 4 == 2016 * 1024  # Stereo s16, samples per channel.
    # Independently protect channel order: distinct frequency/amplitude tones.
    assert actual[0:40000:4] != actual[2:40000:4]
    probe = json.loads(
        run(
            audio_tools[1],
            "-select_streams",
            "a:0",
            "-show_packets",
            "-show_entries",
            "packet=pts,duration",
            "-of",
            "json",
            path,
        )
    )["packets"]
    assert len(probe) == 2016
    pts = [int(p["pts"]) for p in probe]
    assert all(abs((b - a) - 1920) <= 1 for a, b in zip(pts, pts[1:], strict=False))
    assert abs((pts[-1] - pts[0]) - 2015 * 1920) <= 1
    assert pts[0] == 90000
    assert pts == [90000 + i * 1920 for i in range(2016)]
    (tmp_path / "evidence.json").write_text(
        json.dumps(
            {
                "messages": 87,
                "adts_frames": 2016,
                "samples_per_channel": len(actual) // 4,
                "sample_duration_seconds": len(actual) / 4 / 48000,
                "pcm_sha256": hashlib.sha256(actual).hexdigest(),
                "first_pts": pts[0],
                "last_pts": pts[-1],
                "wrong_timestamps": wrong_timestamps,
            },
            indent=2,
        )
    )


def test_real_encoded_chunk_above_pes_limit_preserves_payload_pcm_and_pts(tone_aac, audio_tools, tmp_path):
    path, _ = tone_aac
    payload = path.read_bytes()
    assert len(payload) > 65527
    mux = TSMux()
    mux.started = True
    ts = mux.tables() + mux.add_audio(90000, payload)
    packets = pes_packets(ts)
    assert len(packets) > 1
    assert b"".join(data for _, data in packets) == payload
    samples = 0
    for pts, data in packets:
        assert len(data) <= 65527
        assert pts == 90000 + round(Fraction(samples * 90000, 48000))
        samples += sum(n for _, _, n in walk(data))
    assert samples == 2016 * 1024
    target = tmp_path / "split.ts"
    target.write_bytes(ts)
    reference = run(audio_tools[0], "-i", path, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    actual = run(audio_tools[0], "-i", target, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    assert actual == reference and len(actual) // 4 == samples


def test_metadata_and_ffmpeg_both_reject_a_truncated_real_au(tone_aac, audio_tools, tmp_path):
    _, frames = tone_aac
    bad = tmp_path / "truncated.aac"
    bad.write_bytes(frames[0][:100])
    with pytest.raises(GridError, match="adts_frame_length_invalid"):
        adts_info(bad.read_bytes())
    result = subprocess.run(
        [
            audio_tools[0],
            "-v",
            "error",
            "-xerror",
            "-nostdin",
            "-f",
            "aac",
            "-i",
            str(bad),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0


def test_two_raw_blocks_preserve_pcm_but_ffmpeg_reports_timestamp_overlap(tone_aac, audio_tools, tmp_path):
    _, frames = tone_aac
    grouped = []
    for first, second in zip(frames[:48:2], frames[1:48:2], strict=True):
        data = bytearray(first + second[7:])
        size = len(data)
        data[3] = (data[3] & 0xFC) | (size >> 11)
        data[4] = (size >> 3) & 255
        data[5] = (data[5] & 31) | ((size & 7) << 5)
        data[6] = (data[6] & 0xFC) | 1
        grouped.append(data)
    raw = tmp_path / "single-rdb.aac"
    raw.write_bytes(b"".join(frames[:48]))
    multi = tmp_path / "two-rdb.aac"
    multi.write_bytes(b"".join(grouped))
    assert sum(samples for _, _, samples in walk(multi.read_bytes())) == 48 * 1024
    result = subprocess.run(
        [
            audio_tools[0],
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(multi),
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0 and len(result.stdout) // 4 == 48 * 1024
    assert result.stdout == run(audio_tools[0], "-i", raw, "-c:a", "pcm_s16le", "-f", "s16le", "-")
    # This expectation documents the supplied n9.0 build's timestamp behavior;
    # future decoder fixes should deliberately update this version-specific test.
    assert b"non monotonically increasing dts" in result.stderr


def test_pre_idr_audio_chunk_is_preserved_when_its_tail_overlaps_video(tone_aac, encoded_bframes):
    _, frames = tone_aac
    output = []
    sink = MediaSink(output.append)
    try:
        # 0.512 s of audio starts 0.25 s before IDR: 0.262 s overlaps it.
        audio = b"".join(frames[:24])
        add_audio(sink, audio, timestamp=BASE - 2_500_000)
        payload, kind = encoded_bframes[0]
        sink.add(Frame(0xC748, media_body(payload, kind, 2, BASE)))
        sink.close()  # The bounded HRD probe flushes at EOF when no conflicting AU arrived.
        assert sink.stats["audio_frames"] == 1
        assert sink.mux.audio_chunks == 1
        assert pes_packets(b"".join(output)) == [(90000, audio)]
        assert sink.stats["last_video_dts_90k"] == 112500  # Same affine shift for A/V.
        assert sink.stats["audio_output_core_samples"] == 24 * 1024
        assert not sink.pending_audio and sink.pending_audio_bytes == 0
    finally:
        sink.close()


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"\xff",
        header(0),
        header(6),
        header(7),
        header(100) + b"x",
        header(8, crc=True) + b"x",
        header(9, crc=True) + b"xx",
        header(8) + b"xjunk",
        header(8) + b"x" + header(100) + b"x",
        header(8, index=13) + b"x",
        header(8, index=14) + b"x",
        header(8, index=15) + b"x",
        header(8, channels=0) + b"x",
        bytes((255, 0xF7)) + header(8)[2:] + b"x",
        bytes((255, 0xF9)) + header(8, profile=3)[2:] + b"x",
    ],
)
def test_standalone_parser_strict_boundaries(bad):
    with pytest.raises(GridError):
        parse_adts(bad)


@pytest.mark.parametrize("blocks", [1, 2, 3, 4])
@pytest.mark.parametrize("crc", [False, True])
def test_standalone_parser_core_samples_crc_syntax_and_views(blocks, crc):
    if crc and blocks > 1:
        # Relative offsets include each preceding block's two-byte CRC.
        positions = b"".join((3 * i).to_bytes(2, "big") for i in range(1, blocks))
        body = positions + b"\0\0" + (b"\xe0\0\0" * blocks)
    elif crc:
        body = b"\0\0\xe0"
    else:
        body = b"\xe0" * blocks
    payload = header(7 + len(body), blocks=blocks - 1, crc=crc) + body
    parsed = parse_adts(payload)
    frame = parsed.frames[0]
    assert frame.data == payload and frame.data.obj is payload and frame.data.readonly
    assert frame.offset == 0 and frame.crc_present == crc
    assert frame.header_bytes == (7 if not crc else 9 + 2 * (blocks - 1))
    assert frame.raw_data_blocks == blocks and frame.core_samples == 1024 * blocks
    assert parsed.core_samples == 1024 * blocks and parsed.access_unit_count == blocks
    assert parsed.duration_seconds == Fraction(1024 * blocks, 48000)
    assert parsed.config == frame.config and not parsed.config_changed


@pytest.mark.parametrize("positions", [(0,), (2,), (4,), (65535,), (6, 3)])
def test_standalone_parser_rejects_invalid_crc_block_positions(positions):
    blocks = len(positions) + 1
    body = b"".join(p.to_bytes(2, "big") for p in positions) + b"\0\0" + b"\xe0\0\0" * blocks
    payload = header(7 + len(body), blocks=blocks - 1, crc=True) + body
    with pytest.raises(GridError, match="adts_crc_layout_invalid"):
        parse_adts(payload)


@pytest.mark.parametrize("blocks", [2, 3, 4])
def test_standalone_parser_rejects_missing_crc_position_table_or_block_crcs(blocks):
    payload = header(10, blocks=blocks - 1, crc=True) + b"\0\0\xe0"
    with pytest.raises(GridError, match="adts_frame_length_invalid"):
        parse_adts(payload)


def test_standalone_parser_reports_all_config_transitions_and_exact_duration():
    first = header(8) + b"x"
    second = header(8, index=4, channels=7, profile=0) + b"x"
    parsed = parse_adts(first + second + first)
    assert len(parsed.frames) == 3 and len(parsed.configurations) == 2
    assert parsed.config_changed and parsed.config is None
    assert parsed.frames[0].config is parsed.frames[2].config
    assert parsed.frames[1].config.channels == 8
    assert parsed.frames[1].config.channel_configuration == 7
    assert parsed.frames[1].config.audio_object_type == 1
    assert [f.offset for f in parsed.frames] == [0, 8, 16]
    assert parsed.core_samples == 3072
    assert parsed.duration_seconds == Fraction(2048, 48000) + Fraction(1024, 44100)
    assert b"".join(f.data for f in parsed.frames) == first + second + first


@pytest.mark.parametrize("profile", [0, 1, 2, 3])
def test_standalone_parser_preserves_mpeg4_profiles_with_core_semantics(profile):
    parsed = parse_adts(header(8, profile=profile) + b"x")
    assert parsed.config.audio_object_type == profile + 1
    assert parsed.core_samples == 1024


def test_standalone_parser_resource_limits_and_noncontiguous_input():
    with pytest.raises(GridError, match="adts_chunk_limit"):
        parse_adts(b"\0" * (MAX_ADTS_BYTES + 1))
    small = header(8) + b"x"
    with pytest.raises(GridError, match="adts_frame_limit"):
        parse_adts(small * (MAX_ADTS_FRAMES + 1))
    assert len(parse_adts(small * MAX_ADTS_FRAMES).frames) == MAX_ADTS_FRAMES
    with pytest.raises(GridError, match="adts_input_invalid"):
        parse_adts(memoryview(small)[::2])
    with pytest.raises(GridError, match="adts_input_invalid"):
        parse_adts(None)


def test_standalone_parser_matches_real_ffmpeg_fixture(tone_aac):
    path, frames = tone_aac
    parsed = parse_adts(path.read_bytes())
    assert len(parsed.frames) == len(frames) == 2016
    assert parsed.access_unit_count == 2016 and parsed.core_samples == 2064384
    assert parsed.duration_seconds == Fraction(5376, 125)
    assert parsed.config.sample_rate == 48000 and parsed.config.channels == 2
    assert [bytes(f.data) for f in parsed.frames] == frames


@pytest.mark.parametrize("blocks", [2, 3, 4])
def test_production_rejects_multiple_raw_blocks_but_parser_counts_them(blocks):
    payload = header(7 + blocks, blocks=blocks - 1) + b"\xe0" * blocks
    assert parse_adts(payload).core_samples == blocks * 1024
    sink = MediaSink(lambda _: None)
    mux = TSMux()
    mux.started = True
    try:
        with pytest.raises(GridError, match="aac_raw_data_blocks_unsupported"):
            add_audio(sink, payload)
        with pytest.raises(GridError, match="aac_raw_data_blocks_unsupported"):
            mux.add_audio(90000, payload)
        assert sink.stats["audio_frames"] == sink.stats["audio_core_samples"] == 0
        assert mux.audio_chunks == 0
    finally:
        sink.close()


@pytest.mark.parametrize("profile", [0, 2, 3])
def test_production_rejects_unverified_non_lc_profiles(profile):
    payload = header(8, profile=profile) + b"x"
    assert parse_adts(payload).config.audio_object_type == profile + 1
    sink = MediaSink(lambda _: None)
    mux = TSMux()
    mux.started = True
    try:
        with pytest.raises(GridError, match="aac_"):
            add_audio(sink, payload)
        with pytest.raises(GridError, match="aac_"):
            mux.add_audio(90000, payload)
    finally:
        sink.close()


@pytest.mark.parametrize("delta", [0, -1])
def test_sink_rejects_equal_or_regressing_raw_audio_timestamps(delta):
    sink = MediaSink(lambda _: None)
    try:
        add_audio(sink, header(8) + b"x")
        with pytest.raises(GridError, match="audio_timestamp_regression"):
            add_audio(sink, header(8) + b"x", number=2, timestamp=BASE + delta)
        assert sink.stats["audio_frames"] == 1
    finally:
        sink.close()


def test_sink_measures_bounded_batch_clock_overlap_without_claiming_duplicate_audio():
    sink = MediaSink(lambda _: None)
    sink.mux.started = True
    sink.video_clock.origin_dts = BASE
    try:
        add_audio(sink, (header(8) + b"x") * 2, number=1, timestamp=BASE)
        one_au = Fraction(1024 * 10_000_000, 48_000)
        # The next chunk starts after the previous chunk's final AU start, but
        # before its sample-count-derived end. This is observed Grid batch-clock
        # jitter, not evidence that an AAC access unit was duplicated.
        add_audio(sink, header(8) + b"x", number=2, timestamp=BASE + round(one_au * 1.5))
        assert 0 < sink.stats["audio_chunk_overlap_100ns"] < round(one_au)
        assert sink.mux.last_audio_pts == 90_000 + 2 * 1_920
        assert sink.stats["audio_frames"] == 2
    finally:
        sink.close()


@pytest.mark.parametrize("delta", [Fraction(1024 * 10_000_000, 48_000), -Fraction(1024 * 10_000_000, 48_000)])
def test_sink_rejects_audio_batch_clock_error_of_one_access_unit(delta):
    sink = MediaSink(lambda _: None)
    try:
        add_audio(sink, (header(8) + b"x") * 2, number=1, timestamp=BASE)
        expected = BASE + Fraction(2 * 1024 * 10_000_000, 48_000)
        expected_error = "audio_timestamp_regression" if delta < 0 else "audio_chunk_clock_mismatch"
        with pytest.raises(GridError, match=expected_error):
            add_audio(sink, header(8) + b"x", number=2, timestamp=round(expected + delta))
        assert sink.stats["audio_frames"] == 1
    finally:
        sink.close()


def test_sink_rejects_cumulative_sub_access_unit_audio_clock_drift():
    sink = MediaSink(lambda _: None)
    one_au = Fraction(1024 * 10_000_000, 48_000)
    per_chunk_drift = 5_000  # 0.5 ms, individually below the AU tolerance.
    try:
        add_audio(sink, header(8) + b"x", number=1, timestamp=BASE)
        with pytest.raises(GridError, match="audio_timestamp_cumulative_drift"):
            for i in range(1, 50):
                timestamp = BASE + round(i * (one_au + per_chunk_drift))
                add_audio(sink, header(8) + b"x", number=i + 1, timestamp=timestamp)
        assert abs(sink.stats["audio_clock_drift_100ns"]) < round(one_au)
        assert sink.stats["audio_frames"] < 50
    finally:
        sink.close()


def test_half_second_batches_report_clock_residual_without_two_ms_rejection():
    sink = MediaSink(lambda _: None)
    sink.mux.started = True
    sink.video_clock.origin_dts = BASE
    try:
        for i in range(72):
            first = i * 24000 // 1024
            end = (i + 1) * 24000 // 1024
            add_audio(sink, (header(8) + b"x") * (end - first), number=i + 1, timestamp=BASE + i * 5_000_000)
            if i:
                assert abs(sink.stats["audio_chunk_clock_delta_100ns"]) > 20_000
                assert abs(sink.stats["audio_clock_drift_100ns"]) <= 213334
        assert sink.stats["audio_frames"] == 72
        assert sink.stats["audio_core_samples"] == (72 * 24000 // 1024) * 1024
    finally:
        sink.close()


def test_pre_idr_audio_retention_message_bound():
    sink = MediaSink(lambda _: None)
    try:
        for i in range(64):
            add_audio(sink, header(8) + b"x", number=i + 1, timestamp=BASE + i * 213333)
        with pytest.raises(GridError, match="audio_preroll_limit"):
            add_audio(sink, header(8) + b"x", number=65, timestamp=BASE + 64 * 213333)
        assert len(sink.pending_audio) == 64
    finally:
        sink.close()


def test_pre_idr_audio_retention_byte_bound():
    payload = (header(8191) + b"x" * 8184) * 16
    sink = MediaSink(lambda _: None)
    try:
        for i in range(8):
            add_audio(sink, payload, number=i + 1, timestamp=BASE + i * 3_413_333)
        with pytest.raises(GridError, match="audio_preroll_limit"):
            add_audio(sink, payload, number=9, timestamp=BASE + 8 * 3_413_333)
        assert sink.pending_audio_bytes == 8 * len(payload) <= 1 << 20
    finally:
        sink.close()


def test_detached_video_config_preserves_preroll_and_following_audio_pcm(
    tone_aac, encoded_bframes, audio_tools, tmp_path
):
    _, audio_frames = tone_aac
    first, _ = encoded_bframes[0]
    units = nals(first)
    config = b"".join(b"\0\0\0\1" + n for n in units if n[0] & 31 in (7, 8))
    picture_only = b"".join(b"\0\0\0\1" + n for n in units if n[0] & 31 not in (7, 8))
    audio = b"".join(audio_frames[:48])
    emitted, raw_video = [], []
    sink = MediaSink(emitted.append)
    sink.write = lambda name, data: raw_video.append(data) if name == "video.h264" else None
    try:
        sink.add(Frame(0xC748, media_body(config, "I", 1, BASE - 500000)))
        add_audio(sink, b"".join(audio_frames[:24]), number=2, timestamp=BASE - 2_500_000)
        sink.add(Frame(0xC748, media_body(picture_only, "I", 3, BASE)))
        add_audio(sink, b"".join(audio_frames[24:48]), number=4, timestamp=BASE + 2_620_000)
    finally:
        sink.close()
    assert raw_video == [config, picture_only]
    assert sink.stats["video_parameter_prefix_bytes"] == len(config)
    assert sink.stats["video_output_sha256"] == hashlib.sha256(config + picture_only).hexdigest()
    assert sink.stats["audio_output_sha256"] == hashlib.sha256(audio).hexdigest()
    assert sink.stats["audio_core_samples"] == sink.stats["audio_output_core_samples"] == 48 * 1024
    ts = b"".join(emitted)
    packets = pes_packets(ts)
    assert [p for p, _ in packets] == [90000, 136080]
    assert sink.stats["last_video_dts_90k"] == 112500
    assert b"".join(data for _, data in packets) == audio
    target, reference = tmp_path / "detached.ts", tmp_path / "original.aac"
    target.write_bytes(ts)
    reference.write_bytes(audio)
    actual = run(audio_tools[0], "-i", target, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    expected = run(audio_tools[0], "-i", reference, "-c:a", "pcm_s16le", "-f", "s16le", "-")
    assert actual == expected and len(actual) // 4 == 48 * 1024


def test_two_second_video_startup_wait_retains_buffered_audio(
    encoded_bframes, tone_aac, audio_tools, tmp_path
):
    _, frames = tone_aac
    emitted = []
    sink = MediaSink(emitted.append)
    try:
        # Continuous audio spans 2.048 seconds; the fourth batch overlaps IDR.
        for i in range(4):
            add_audio(
                sink, b"".join(frames[i * 24 : (i + 1) * 24]), number=i + 1, timestamp=BASE + i * 5_120_000
            )
        payload, kind = encoded_bframes[0]
        sink.add(Frame(0xC748, media_body(payload, kind, 5, BASE + 20_000_000)))
        sink.close()  # One-picture fixture: finalize its parsed HRD timing at EOF.
        packets = pes_packets(b"".join(emitted))
        assert len(packets) == 4 and packets[0][0] >= 0
        assert sink.stats["audio_output_core_samples"] == 96 * 1024
        assert sink.stats["last_video_dts_90k"] - packets[0][0] == 180000
        target, raw = tmp_path / "startup.ts", tmp_path / "startup.aac"
        target.write_bytes(b"".join(emitted))
        raw.write_bytes(b"".join(frames[:96]))
        actual = run(audio_tools[0], "-i", target, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
        expected = run(audio_tools[0], "-i", raw, "-c:a", "pcm_s16le", "-f", "s16le", "-")
        assert actual == expected and len(actual) // 4 == 96 * 1024
    finally:
        sink.close()


def test_preroll_flush_uses_contiguous_audio_sample_clock(encoded_bframes):
    emitted = []
    sink = MediaSink(emitted.append)
    try:
        add_audio(sink, header(8) + b"x", timestamp=BASE)
        one_au = round(Fraction(1024 * 10_000_000, 48_000))
        add_audio(sink, header(8) + b"x", number=2, timestamp=BASE + 1)
        payload, kind = encoded_bframes[0]
        sink.add(Frame(0xC748, media_body(payload, kind, 3, BASE + 2_500_000)))
        sink.close()  # One-picture fixture: flush the held IDR and audio preroll.
        assert emitted and sink.stats["audio_output_access_units"] == 2
        audio_pts = [pts for pts, _ in pes_packets(b"".join(emitted))]
        assert audio_pts == [90_000, 90_000 + round(Fraction(1024 * 90_000, 48_000))]
        assert sink.stats["audio_chunk_clock_delta_100ns"] == 1 - one_au
    finally:
        sink.close()


def test_low_bitrate_44100_large_pes_has_no_accumulating_packet_drift(audio_tools, tmp_path):
    raw = tmp_path / "long-44100.aac"
    run(
        audio_tools[0],
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-frames:a",
        6000,
        "-c:a",
        "aac",
        "-f",
        "adts",
        raw,
    )
    frames = walk(raw.read_bytes())[:6000]
    assert len(frames) == 6000
    payload = b"".join(data for data, _, _ in frames)
    raw.write_bytes(payload)
    mux = TSMux()
    mux.started = True
    ts = mux.tables() + mux.add_audio(90000, payload)
    assert len(pes_packets(ts)) >= 2
    path = tmp_path / "long-44100.ts"
    path.write_bytes(ts)
    packets = json.loads(
        run(
            audio_tools[1],
            "-select_streams",
            "a:0",
            "-show_packets",
            "-show_entries",
            "packet=pts",
            "-of",
            "json",
            path,
        )
    )["packets"]
    assert len(packets) == 6000
    for index, packet in enumerate(packets):
        expected = 90000 + Fraction(index * 1024 * 90000, 44100)
        assert abs(int(packet["pts"]) - expected) <= 1
    expected_pcm = run(audio_tools[0], "-i", raw, "-c:a", "pcm_s16le", "-f", "s16le", "-")
    actual_pcm = run(audio_tools[0], "-i", path, "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    assert actual_pcm == expected_pcm and len(actual_pcm) // 4 == 6000 * 1024


def test_mux_split_sample_offsets_do_not_accumulate_six_hour_44100_drift():
    payload = (header(8191, index=4) + b"x" * 8184) * 9
    mux = TSMux()
    mux.started = True
    for hour in range(7):
        sample = hour * 3600 * 44100 + hour * 1024
        exact_start = 90000 + Fraction(sample * 90000, 44100)
        packets = pes_packets(mux.add_audio(round(exact_start), payload))
        assert len(packets) == 2
        consumed = 0
        for pts, data in packets:
            expected = exact_start + Fraction(consumed * 90000, 44100)
            assert abs(pts - expected) <= 1  # Two independent nearest roundings.
            consumed += sum(samples for _, _, samples in walk(data))
