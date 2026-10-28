# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Independent generated media, corrupted TS, and hostile subprocess contracts."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tracemalloc

import pytest
from soop_grid.mpegts import TSMux, crc_mpeg

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Verifier subprocess execution requires POSIX pipes/selectors"
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recording_verifier", ROOT / "tools/verify_recording.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.fixture(scope="module")
def ff_tools():
    ffmpeg = os.environ.get("SOOP_TEST_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("SOOP_TEST_FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("set SOOP_TEST_FFMPEG and SOOP_TEST_FFPROBE for generated-media tests")
    return ffmpeg, ffprobe


@pytest.fixture(scope="module")
def generated(tmp_path_factory, ff_tools):
    ffmpeg, _ = ff_tools
    path = tmp_path_factory.mktemp("verifier-generated") / "private-input-name.ts"
    process = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-bf",
            "2",
            "-threads:v",
            "1",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    return path


def executable(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return str(path)


def fake_probe(tmp_path, packets=None, video_codec="h264", audio_codec="aac"):
    metadata = {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": video_codec, "width": 16, "height": 16},
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": audio_codec,
                "sample_rate": "48000",
                "channels": 1,
            },
        ]
    }
    packets = packets or (
        "stream_index=0|pts_time=1.0|dts_time=1.0|duration_time=0.04\n"
        "stream_index=1|pts_time=1.0|dts_time=1.0|duration_time=0.021333\n"
    )
    return executable(
        tmp_path,
        "probe",
        f"import sys\nprint({packets!r} if '-show_packets' in sys.argv else {json.dumps(metadata)!r})\n",
    )


def test_failed_probe_cli_is_nonzero_redacted_json(tmp_path):
    path = tmp_path / "private-file-name.ts"
    path.write_bytes(b"invalid")
    probe = executable(
        tmp_path, "failed-probe", "import sys\nprint('private-token', file=sys.stderr)\nsys.exit(7)\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/verify_recording.py"),
            str(path),
            "--ffprobe",
            probe,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["verdict"] == "fail" and report["ffprobe_exit"] == 7
    assert "ffprobe_failed" in report["failures"]
    assert "private" not in result.stdout and not result.stderr


def test_missing_tool_and_missing_input_are_safe_failures(tmp_path):
    path = tmp_path / "recording.ts"
    path.write_bytes(b"invalid")
    result = verifier.verify(path, ffprobe=str(tmp_path / "absent"))
    assert result["verdict"] == "fail" and result["probe"]["launch_failed"]
    assert verifier.verify(tmp_path / "private-missing-file")["failures"] == ["input_not_regular_file"]


def test_decode_failure_is_a_gate_even_with_successful_probe(tmp_path):
    path = tmp_path / "recording.ts"
    path.write_bytes(b"invalid")
    ffmpeg = executable(
        tmp_path, "decoder", "import sys\nprint('private-url-token', file=sys.stderr)\nsys.exit(9)\n"
    )
    result = verifier.verify(path, fake_probe(tmp_path), ffmpeg)
    assert not result["passed"] and "decode_failed" in result["failures"]
    assert result["decode"]["process"]["exit_code"] == 9
    assert "private-url" not in json.dumps(result)


def test_missing_timestamps_are_counted_for_both_streams_and_fail(tmp_path):
    path = tmp_path / "recording.ts"
    path.write_bytes(b"invalid")
    probe = fake_probe(
        tmp_path,
        (
            "stream_index=0|pts_time=N/A|dts_time=1.0|duration_time=N/A\n"
            "stream_index=0|pts_time=1.04|dts_time=N/A|duration_time=0.04\n"
            "stream_index=1|pts_time=N/A|dts_time=N/A|duration_time=0.021333\n"
        ),
    )
    ffmpeg = executable(tmp_path, "decoder", "import sys\nsys.exit(1)\n")
    result = verifier.verify(path, probe, ffmpeg)
    video, audio = (result["packets"]["streams"][kind] for kind in ("video", "audio"))
    assert video["packets"] == 2 and video["missing_pts"] == video["missing_dts"] == 1
    assert video["missing_duration"] == 1
    assert audio["packets"] == 1 and audio["missing_pts"] == audio["missing_dts"] == 1
    assert {"video_missing_pts", "video_missing_dts", "audio_missing_pts", "audio_missing_dts"} <= set(
        result["failures"]
    )


def test_monotonic_audio_pts_can_still_overlap_packet_duration(generated, ff_tools, tmp_path):
    ffmpeg, _ = ff_tools
    probe = fake_probe(
        tmp_path,
        "stream_index=0|pts_time=1.0|dts_time=1.0|duration_time=0.04\n"
        "stream_index=1|pts_time=1.0|dts_time=1.0|duration_time=0.021333\n"
        "stream_index=1|pts_time=1.01|dts_time=1.01|duration_time=0.021333\n",
    )
    result = verifier.verify(generated, probe, ffmpeg, timeout=30)
    assert result["packets"]["streams"]["audio"]["pts_overlap_packets"] == 1
    assert result["packets"]["streams"]["audio"]["max_pts_overlap_seconds"] == pytest.approx(0.011333)
    assert "audio_packet_timeline_overlap" in result["failures"]


def test_generated_bframes_aac_counts_and_timing(generated, ff_tools):
    ffmpeg, ffprobe = ff_tools
    result = verifier.verify(generated, ffprobe, ffmpeg, timeout=30)
    assert result["passed"], result
    assert result["packets"]["streams"]["video"]["pts_reorder_steps"] > 0
    assert result["decode"]["streams"]["video"]["frames"] == 50
    assert result["decode"]["streams"]["video"]["duration_seconds"] == pytest.approx(2)
    # An independent raw-PCM decode checks the byte accounting without framehash.
    pcm = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(generated),
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ],
        capture_output=True,
        timeout=20,
    )
    assert pcm.returncode == 0
    audio = result["decode"]["streams"]["audio"]
    assert audio["pcm_bytes"] == len(pcm.stdout)
    assert audio["pcm_samples_per_channel"] == len(pcm.stdout) // 2
    assert audio["pcm_duration_seconds"] == pytest.approx(len(pcm.stdout) / 2 / 48000)
    assert result["aac"]["aac_access_units"] * 1024 == audio["pcm_samples_per_channel"]
    assert result["ts"]["psi_crc_errors"] == result["ts"]["adaptation_errors"] == 0
    assert result["packet_av"]["drift_seconds"] == pytest.approx(
        result["packet_av"]["end_offset_seconds"] - result["packet_av"]["start_offset_seconds"]
    )
    assert "private-input-name" not in json.dumps(result)


def test_minimum_duration_gate_rejects_valid_but_short_audiovideo(generated, ff_tools):
    ffmpeg, ffprobe = ff_tools
    result = verifier.verify(generated, ffprobe, ffmpeg, timeout=30, minimum_duration=3.0)
    assert not result["passed"]
    assert result["failures"] == ["minimum_duration_not_met"]
    assert result["duration_gate"]["video_seconds"] == pytest.approx(2.0)
    assert result["duration_gate"]["audio_pcm_seconds"] > 2.0


def test_minimum_duration_gate_rejects_invalid_configuration(generated, ff_tools):
    ffmpeg, ffprobe = ff_tools
    result = verifier.verify(generated, ffprobe, ffmpeg, timeout=30, minimum_duration=float("nan"))
    assert not result["passed"]
    assert result["failures"] == ["minimum_duration_invalid"]


def test_verifier_rejects_unexpected_video_codec(generated, ff_tools, tmp_path):
    ffmpeg, _ = ff_tools
    result = verifier.verify(generated, fake_probe(tmp_path, video_codec="hevc"), ffmpeg, timeout=30)
    assert "video_codec_unsupported" in result["failures"]
    assert not result["passed"]


def test_verifier_rejects_pcr_value_delta_over_policy(generated, ff_tools, monkeypatch):
    ffmpeg, ffprobe = ff_tools
    original = verifier.scan_ts

    def over_interval(path, deadline=None):
        facts = original(path, deadline)
        facts["max_pcr_value_delta_seconds"] = verifier.MAX_PCR_VALUE_DELTA_SECONDS + 0.001
        return facts

    monkeypatch.setattr(verifier, "scan_ts", over_interval)
    result = verifier.verify(generated, ffprobe, ffmpeg, timeout=30)
    assert "ts_pcr_value_delta_exceeded" in result["failures"]
    assert not result["passed"]


def test_verifier_requires_multiple_pcr_samples(generated, ff_tools, monkeypatch):
    ffmpeg, ffprobe = ff_tools
    original = verifier.scan_ts

    def one_sample(path, deadline=None):
        facts = original(path, deadline)
        facts["pcr_samples"] = 1
        facts["pcr_progression_pair_counts"] = {}
        return facts

    monkeypatch.setattr(verifier, "scan_ts", one_sample)
    result = verifier.verify(generated, ffprobe, ffmpeg, timeout=30)
    assert "ts_pcr_progression_unverified" in result["failures"]
    assert not result["passed"]


def test_verifier_requires_two_pcr_samples_on_one_pid(generated, ff_tools, monkeypatch):
    ffmpeg, ffprobe = ff_tools
    original = verifier.scan_ts

    def split_pid_samples(path, deadline=None):
        facts = original(path, deadline)
        facts["pcr_samples"] = 2
        facts["pcr_progression_pair_counts"] = {}
        return facts

    monkeypatch.setattr(verifier, "scan_ts", split_pid_samples)
    result = verifier.verify(generated, ffprobe, ffmpeg, timeout=30)
    assert "ts_pcr_progression_unverified" in result["failures"]
    assert not result["passed"]


def test_verifier_rejects_unexpected_audio_codec(generated, ff_tools, tmp_path):
    ffmpeg, _ = ff_tools
    result = verifier.verify(generated, fake_probe(tmp_path, audio_codec="mp3"), ffmpeg, timeout=30)
    assert "audio_codec_unsupported" in result["failures"]
    assert not result["passed"]


def first_pat(data):
    for offset in range(0, len(data), 188):
        if data[offset + 1] & 0x1F == 0 and data[offset + 2] == 0:
            return offset
    raise AssertionError("generated source has no PAT")


def test_batched_ts_reader_preserves_packet_boundaries_hash_and_partial_tail():
    data = b"".join(bytes((0x47, index & 255)) + bytes((index,)) * 186 for index in range(19)) + b"tail"

    class ShortReader:
        def __init__(self, value):
            self.value = value
            self.offset = 0
            self.calls = 0

        def read(self, requested):
            limits = (71, 239, 503, 41)
            size = min(requested, limits[self.calls % len(limits)])
            self.calls += 1
            result = self.value[self.offset : self.offset + size]
            self.offset += len(result)
            return result

    digest = hashlib.sha256()
    packets = list(verifier._iter_ts_packets(ShortReader(data), digest, packets_per_read=7))
    expected = [data[offset : offset + 188] for offset in range(0, 19 * 188, 188)] + [b"tail"]

    assert [bytes(packet) for packet in packets] == expected
    assert digest.digest() == hashlib.sha256(data).digest()


def test_crc_corruption_detected_without_hardcoded_pmt_pid(generated, tmp_path):
    data = bytearray(generated.read_bytes())
    offset = first_pat(data)
    payload = offset + 4
    if (data[offset + 3] >> 4) & 3 == 3:
        payload += 1 + data[offset + 4]
    section = payload + 1 + data[payload]
    size = 3 + (((data[section + 1] & 15) << 8) | data[section + 2])
    data[section + size - 1] ^= 1
    path = tmp_path / "bad-crc.ts"
    path.write_bytes(data)
    facts = verifier.scan_ts(path)
    assert facts["psi_crc_errors"] == 1
    assert facts["sync_errors"] == facts["continuity_errors"] == 0


def test_dynamic_pmt_and_split_psi_sections(generated, tmp_path, ff_tools):
    ffmpeg, _ = ff_tools
    remuxed = tmp_path / "different-pmt.ts"
    process = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(generated),
            "-c",
            "copy",
            "-mpegts_pmt_start_pid",
            "500",
            "-f",
            "mpegts",
            str(remuxed),
        ],
        capture_output=True,
        timeout=20,
    )
    assert process.returncode == 0, process.stderr
    assert verifier.scan_ts(remuxed)["pmt"] > 0
    data = generated.read_bytes()
    offset = first_pat(data)
    packet = data[offset : offset + 188]
    section = packet[5 : 5 + 3 + (((packet[6] & 15) << 8) | packet[7])]
    # Split a real CRC-protected PAT across TS packets; no verifier CRC helper is used.
    payload = b"\0" + section[:8]
    adaptation = 183 - len(payload)
    first = bytes((0x47, 0x40, 0, 0x30, adaptation, 0)) + b"\xff" * (adaptation - 1) + payload
    second = bytes((0x47, 0, 0, 0x11)) + section[8:]
    second += b"\xff" * (188 - len(second))
    split = tmp_path / "split.ts"
    split.write_bytes(first + second)
    facts = verifier.scan_ts(split)
    assert facts["pat"] == 1 and facts["psi_crc_errors"] == facts["psi_structure_errors"] == 0


@pytest.mark.parametrize(
    "mutation,metric",
    [
        ("truncated", "sync_errors"),
        ("tei", "transport_errors"),
        ("scrambled", "scrambled_packets"),
        ("adaptation", "adaptation_errors"),
        ("counter", "continuity_errors"),
    ],
)
def test_independent_ts_structural_corruptions(generated, tmp_path, mutation, metric):
    data = bytearray(generated.read_bytes())
    if mutation == "truncated":
        del data[-1]
    elif mutation == "tei":
        data[1] |= 0x80
    elif mutation == "scrambled":
        data[3] |= 0x80
    elif mutation == "adaptation":
        data[3], data[4] = 0x30, 184
    else:
        offset = first_pat(data)
        data[offset + 3] ^= 7
    path = tmp_path / "corrupt.ts"
    path.write_bytes(data)
    assert verifier.scan_ts(path)[metric] > 0


def test_null_counters_and_signaled_discontinuity(tmp_path):
    # Null CC is unspecified; a signaled discontinuity resets continuity and PCR history.
    null = bytes((0x47, 0x1F, 0xFF, 0x10)) + b"\xff" * 184
    packet = bytes((0x47, 1, 0, 0x10)) + b"\0" * 184
    reset = bytes((0x47, 1, 0, 0x39, 1, 0x80)) + b"\0" * 182
    path = tmp_path / "discontinuity.ts"
    path.write_bytes(null + null + packet + reset)
    facts = verifier.scan_ts(path)
    assert facts["continuity_errors"] == 0 and facts["explicit_discontinuities"] == 1


@pytest.mark.skipif(
    sys.platform != "linux" or not Path("/proc").is_dir(),
    reason="Descendant process-state observation requires Linux procfs",
)
def test_subprocess_timeout_kills_parent_and_descendant(tmp_path):
    pid_file = tmp_path / "child-pid"
    script = executable(
        tmp_path,
        "hang",
        (
            "import subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
            f"open({str(pid_file)!r},'w').write(str(child.pid))\n"
            "while True:\n sys.stdout.buffer.write(b'x'*65536);sys.stdout.buffer.flush()\n"
        ),
    )
    started = time.monotonic()
    result = verifier.run_process([script], lambda chunk: None, started + 1)
    assert result["timed_out"] and time.monotonic() - started < 4
    child = int(pid_file.read_text())
    state = Path(f"/proc/{child}/stat")
    for _ in range(100):
        try:
            process_state = state.read_text().split()[2]
        except (FileNotFoundError, ProcessLookupError):
            break
        if process_state == "Z":
            break
        time.sleep(0.01)  # Signal delivery to the descendant is asynchronous.
    else:
        pytest.fail("subprocess descendant survived timeout cleanup")


def test_streaming_output_memory_and_line_limit(tmp_path):
    producer = executable(
        tmp_path,
        "producer",
        (
            "import sys\n"
            "for _ in range(1024):\n"
            " sys.stdout.buffer.write(b'x'*65536);sys.stderr.buffer.write(b'error\\n'*1000)\n"
        ),
    )
    total = 0

    def consume(chunk):
        nonlocal total
        total += len(chunk)

    tracemalloc.start()
    try:
        status = verifier.run_process([producer], consume, time.monotonic() + 10)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert status["exit_code"] == 0 and total == 64 << 20
    assert status["diagnostic_lines"] == 1_024_000
    assert peak < 2 << 20
    with pytest.raises(verifier.ValidationError, match="subprocess_output_limit"):
        verifier.run_process([producer], verifier.Lines(lambda line: None), time.monotonic() + 5)


def test_metadata_is_bounded_and_redacted(tmp_path):
    path = tmp_path / "recording.ts"
    path.write_bytes(b"invalid")
    probe = executable(tmp_path, "large-probe", "import sys\nsys.stdout.buffer.write(b'x'*(2<<20))\n")
    report = verifier.verify(path, probe)
    assert not report["passed"] and "metadata_output_limit" in report["failures"]


def test_pcm_and_aac_truncation_are_not_silently_accepted():
    decoded = verifier.DecodeFacts(channels=2)
    decoded.line("#tb 1: 1/48000")
    with pytest.raises(verifier.ValidationError, match="pcm_alignment_invalid"):
        decoded.line("1, 0, 0, 1024, 3, checksum")
    adts = verifier.ADTSFacts()
    with pytest.raises(verifier.ValidationError, match="adts_structure_invalid"):
        adts(b"invalid header")


def ts_payload_packet(pid, counter, payload, start=False):
    assert 1 <= len(payload) <= 182
    adaptation = 183 - len(payload)
    return (
        bytes((0x47, (pid >> 8) | (0x40 if start else 0), pid & 255, 0x30 | counter, adaptation, 0))
        + b"\xff" * (adaptation - 1)
        + payload
    )


def ts_pcr_packet(pid, pcr_ticks, discontinuity=False):
    base, extension = divmod(pcr_ticks, 300)
    pcr = bytes(
        (
            (base >> 25) & 0xFF,
            (base >> 17) & 0xFF,
            (base >> 9) & 0xFF,
            (base >> 1) & 0xFF,
            ((base & 1) << 7) | 0x7E | (extension >> 8),
            extension & 0xFF,
        )
    )
    flags = 0x10 | (0x80 if discontinuity else 0)
    return bytes((0x47, (pid >> 8) & 0x1F, pid & 0xFF, 0x20, 183, flags)) + pcr + b"\xff" * 176


def test_pcr_progression_requires_positive_same_pid_pair(tmp_path):
    path = tmp_path / "pcr-progress.ts"
    path.write_bytes(ts_pcr_packet(0x100, 27_000_000) + ts_pcr_packet(0x100, 27_270_000))
    facts = verifier.scan_ts(path)
    assert facts["pcr_progression_pair_counts"] == {"0x100": 1}
    assert facts["max_pcr_value_delta_seconds"] == pytest.approx(0.01)


@pytest.mark.parametrize(
    "packets",
    [
        # Equal values are samples but do not establish a progressing clock.
        ts_pcr_packet(0x100, 27_000_000) + ts_pcr_packet(0x100, 27_000_000),
        # One sample on each of two PIDs is not a same-clock pair.
        ts_pcr_packet(0x100, 27_000_000) + ts_pcr_packet(0x101, 27_270_000),
        # Explicit discontinuity breaks continuity between otherwise ordered values.
        ts_pcr_packet(0x100, 27_000_000) + ts_pcr_packet(0x100, 27_270_000, discontinuity=True),
    ],
)
def test_pcr_progression_rejects_equal_split_or_discontinuous_samples(tmp_path, packets):
    path = tmp_path / "pcr-no-progress.ts"
    path.write_bytes(packets)
    facts = verifier.scan_ts(path)
    assert facts["pcr_progression_pair_counts"] == {}


def test_pcr_gate_requires_progression_on_pmt_declared_pid(tmp_path):
    tables = bytearray(TSMux().tables())
    pmt_packet = memoryview(tables)[188:376]
    payload = 5 + pmt_packet[4]
    section_start = payload + 1
    section_size = 3 + (((pmt_packet[section_start + 1] & 15) << 8) | pmt_packet[section_start + 2])
    section = bytearray(pmt_packet[section_start : section_start + section_size])
    section[8:10] = b"\xe1\x01"  # PMT declares PCR_PID 0x101 instead of actual 0x100.
    section[-4:] = crc_mpeg(section[:-4]).to_bytes(4, "big")
    pmt_packet[section_start : section_start + section_size] = section
    path = tmp_path / "pcr-wrong-signaled-pid.ts"
    path.write_bytes(tables + ts_pcr_packet(0x100, 27_000_000) + ts_pcr_packet(0x100, 27_270_000))
    facts = verifier.scan_ts(path)
    assert facts["psi_crc_errors"] == facts["psi_structure_errors"] == 0
    assert facts["pcr_pid_declarations"] == ["0x101"]
    assert facts["pcr_progression_pair_counts"] == {"0x100": 1}
    assert not verifier.has_signaled_pcr_progression(facts)


def test_null_pid_pcr_cannot_satisfy_signaled_clock_gate(tmp_path):
    tables = bytearray(TSMux().tables())
    pmt_packet = memoryview(tables)[188:376]
    section_start = 5 + pmt_packet[4] + 1
    section_size = 3 + (((pmt_packet[section_start + 1] & 15) << 8) | pmt_packet[section_start + 2])
    section = bytearray(pmt_packet[section_start : section_start + section_size])
    section[8:10] = b"\xff\xff"  # Null PID means the PMT signals no PCR PID.
    section[-4:] = crc_mpeg(section[:-4]).to_bytes(4, "big")
    pmt_packet[section_start : section_start + section_size] = section
    path = tmp_path / "null-pcr.ts"
    path.write_bytes(tables + ts_pcr_packet(0x1FFF, 27_000_000) + ts_pcr_packet(0x1FFF, 27_270_000))
    facts = verifier.scan_ts(path)
    assert facts["pcr_pid_declarations"] == ["0x1fff"]
    assert facts["pcr_progression_pair_counts"] == {}
    assert not verifier.has_signaled_pcr_progression(facts)


def test_pes_hashes_split_headers_lengths_and_video_boundaries(tmp_path):
    import hashlib

    video = b"\0\0\0\1\x65" + bytes(range(256)) * 3
    audio = b"\xff\xf1" + bytes(range(99))
    # Optional PES data is deliberately not valid PTS: extraction must preserve
    # opaque elementary bytes without depending on a demuxer or decoder.
    video_header = b"\0\0\1\xe0\0\0\x80\x80\x05" + b"abcde"
    audio_header = b"\0\0\1\xc0" + (len(audio) + 8).to_bytes(2, "big") + b"\x80\x80\x05abcde"
    packets = []
    for pid, payload, header in ((0x100, video, video_header), (0x101, audio, audio_header)):
        wire = header + payload
        # Every header phase, including the 16-bit packet length, crosses TS boundaries.
        counter = 0
        for size in (1, 4, 2, 3, 2, 2):
            packets.append(ts_payload_packet(pid, counter, wire[:size], start=(counter == 0)))
            wire = wire[size:]
            counter += 1
        while wire:
            packets.append(ts_payload_packet(pid, counter & 15, wire[:182]))
            counter += 1
            wire = wire[182:]
    # The next video PUSI terminates the first length-zero PES.
    packets.append(ts_payload_packet(0x100, 11, video_header + b"last", start=True))
    path = tmp_path / "independent-pes.ts"
    path.write_bytes(b"".join(packets))
    facts = verifier.scan_ts(path)["pes_payloads"]
    for pid, expected, count in (("0x100", video + b"last", 2), ("0x101", audio, 1)):
        assert facts[pid]["sha256"] == hashlib.sha256(expected).hexdigest()
        assert facts[pid]["bytes"] == len(expected)
        assert facts[pid]["complete_pes_packets"] == facts[pid]["pes_packets"] == count
        assert facts[pid]["header_errors"] == facts[pid]["truncated_pes"] == 0


def test_pes_audio_truncation_extra_bytes_and_partial_header(tmp_path):
    header = b"\0\0\1\xc0\0\x07\x80\0\0"  # Exactly four elementary bytes promised.
    for label, payload, metric in (
        ("short", header + b"ab", "truncated_pes"),
        ("extra", header + b"abcdef", "extra_payload_bytes"),
        ("header", header[:5], "truncated_pes"),
        ("unbounded-audio", b"\0\0\1\xc0\0\0\x80\0\0abc", "header_errors"),
    ):
        path = tmp_path / (label + ".ts")
        path.write_bytes(ts_payload_packet(0x101, 0, payload, start=True))
        facts = verifier.scan_ts(path)["pes_payloads"]["0x101"]
        assert facts[metric] > 0
    assert facts["bytes"] == 0


def test_streaming_pes_matches_independent_adts_copy(generated, ff_tools):
    import hashlib

    ffmpeg, _ = ff_tools
    copied = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(generated),
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-f",
            "adts",
            "pipe:1",
        ],
        capture_output=True,
        timeout=20,
    )
    assert copied.returncode == 0
    facts = verifier.scan_ts(generated)["pes_payloads"]["0x101"]
    assert facts["bytes"] == len(copied.stdout)
    assert facts["sha256"] == hashlib.sha256(copied.stdout).hexdigest()
    assert facts["truncated_pes"] == facts["extra_payload_bytes"] == 0


def test_recording_changed_during_validation_is_rejected(generated, tmp_path, ff_tools, monkeypatch):
    path = tmp_path / "changing.ts"
    shutil.copyfile(generated, path)
    ffmpeg, ffprobe = ff_tools
    original = verifier.run_process
    touched = False

    def run(*args, **kwargs):
        nonlocal touched
        result = original(*args, **kwargs)
        if not touched:
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            touched = True
        return result

    monkeypatch.setattr(verifier, "run_process", run)
    result = verifier.verify(path, ffprobe, ffmpeg, timeout=30)
    assert not result["passed"] and "input_changed_during_validation" in result["failures"]


def test_coverage_offset_change_does_not_imply_audio_clock_drift():
    # Different A/V coverage can coexist with a continuous sample-exact audio timeline.
    facts = {
        "video": dict(start_seconds=1.0, end_seconds=61.0),
        "audio": dict(start_seconds=0.628333, end_seconds=60.913666, pcm_duration_seconds=60.285333),
    }
    result = verifier._av_facts(facts)
    assert result["start_offset_seconds"] == pytest.approx(-0.371667)
    assert result["end_offset_seconds"] == pytest.approx(-0.086334)
    assert result["coverage_offset_change_seconds"] == pytest.approx(0.285333)
    assert result["drift_seconds"] == result["coverage_offset_change_seconds"]
    assert result["interpretation"] == "coverage_boundaries_only_not_source_clock_drift_or_perceptual_sync"
    assert facts["audio"]["timeline_minus_pcm_duration_seconds"] == pytest.approx(0)


def test_audio_timeline_minus_pcm_duration_reports_span_residual_or_unknown():
    facts = {
        "video": dict(start_seconds=1.0, end_seconds=3.0),
        "audio": dict(start_seconds=1.0, end_seconds=3.25, pcm_duration_seconds=2.0),
    }
    verifier._av_facts(facts)
    assert facts["audio"]["timeline_minus_pcm_duration_seconds"] == 0.25
    facts["audio"]["end_seconds"] = None
    result = verifier._av_facts(facts)
    assert facts["audio"]["timeline_minus_pcm_duration_seconds"] is None
    assert result["coverage_offset_change_seconds"] is result["drift_seconds"] is None
