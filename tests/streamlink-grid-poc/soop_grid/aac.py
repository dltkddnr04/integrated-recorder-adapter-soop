# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Bounded ADTS framing, independent of media clocks and transport muxing.

Accepts complete concatenated ADTS frames only; no resync, partial buffering, or
AAC bitstream decoding. CRC field/position bounds are checked, not CRC values.
Main, LC, SSR and MPEG-4 LTP headers are preserved; this is not a decoder-support
claim. Every raw_data_block contributes 1024 *core* samples per channel. Implicit
HE-AAC SBR/PS may change the decoded output rate, sample count or channel count;
ADTS alone cannot establish those output properties or encoder priming/padding.

Frame views retain the input. Callers must not mutate a writable backing buffer
while using a result. Config changes are exposed, not silently reconciled. The
caller owns the policy for accepting them, PES splitting and timestamp checks.
"""

from dataclasses import dataclass
from fractions import Fraction

from .protocol import GridError


MAX_ADTS_BYTES = 4 << 20
MAX_ADTS_FRAMES = 8192
SAMPLE_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)
CHANNEL_COUNTS = (0, 1, 2, 3, 4, 5, 6, 8)


@dataclass(frozen=True, slots=True)
class AACConfig:
    mpeg_id: int
    audio_object_type: int
    sample_rate: int
    channel_configuration: int
    channels: int


@dataclass(frozen=True, slots=True, repr=False)
class ADTSFrame:
    """One complete transport frame, containing 1..4 AAC raw data blocks.

    ``data`` includes the complete ADTS header and any CRCs, unchanged.
    Protected multi-block positions are relative to the first raw data block.
    Unprotected multi-block boundaries require AAC decoding and are not exposed.
    """

    data: memoryview
    offset: int
    header_bytes: int
    config: AACConfig
    raw_data_blocks: int
    core_samples: int
    crc_present: bool
    raw_data_block_positions: tuple[int, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ADTSResult:
    frames: tuple[ADTSFrame, ...]
    configurations: tuple[AACConfig, ...]
    core_samples: int
    duration_seconds: Fraction

    @property
    def config_changed(self):
        return len(self.configurations) > 1

    @property
    def config(self):
        """The uniform configuration, or None if it changes within this chunk."""
        return None if self.config_changed else self.configurations[0]

    @property
    def access_unit_count(self):
        """Number of raw data blocks; distinguish this from len(frames)."""
        return sum(frame.raw_data_blocks for frame in self.frames)


def parse_adts(payload):
    """Validate all framing before returning any views or sample counts.

    Bounded to 4 MiB and 8192 frames. Rejects reserved rate/layer/profile syntax,
    PCE channel configuration 0, truncated frames/CRC syntax, invalid protected
    block positions, and trailing bytes. Configurations retain encounter order;
    each frame carries its own config, so transitions across/within chunks are
    available to the caller. Duration is an exact sum even across rate changes.
    """
    try:
        view = memoryview(payload)
        if not view.c_contiguous:
            raise GridError("adts_input_invalid")
        if not 0 < view.nbytes <= MAX_ADTS_BYTES:
            raise GridError("adts_chunk_limit")
        view = view.cast("B").toreadonly()
    except (TypeError, ValueError):
        raise GridError("adts_input_invalid") from None

    offset = total_samples = 0
    frames = []
    configurations = {}
    duration = Fraction(0)
    while offset < len(view):
        if len(frames) >= MAX_ADTS_FRAMES:
            raise GridError("adts_frame_limit")
        if len(view) - offset < 7:
            raise GridError("adts_header_invalid")
        h = view[offset : offset + 7]
        if h[0] != 255 or h[1] & 0xF6 != 0xF0:
            raise GridError("adts_header_invalid")
        index = (h[2] >> 2) & 15
        if index >= len(SAMPLE_RATES):
            raise GridError("adts_sample_rate_invalid")
        mpeg_id, object_type = (h[1] >> 3) & 1, (h[2] >> 6) + 1
        if mpeg_id == 1 and object_type == 4:
            raise GridError("adts_profile_unsupported")  # Reserved MPEG-2 profile.
        channels = ((h[2] & 1) << 2) | (h[3] >> 6)
        if channels == 0:
            raise GridError("adts_channel_config_unsupported")
        config = AACConfig(mpeg_id, object_type, SAMPLE_RATES[index], channels, CHANNEL_COUNTS[channels])
        config = configurations.setdefault(config, config)

        length = ((h[3] & 3) << 11) | (h[4] << 3) | (h[5] >> 5)
        blocks = (h[6] & 3) + 1
        crc = not bool(h[1] & 1)
        header_bytes = 7 if not crc else 9 + 2 * (blocks - 1)
        # Protected multiple-block frames have a CRC after EACH raw block,
        # in addition to the header CRC and block-position table.
        block_crc_bytes = 2 if crc and blocks > 1 else 0
        minimum = header_bytes + blocks * (1 + block_crc_bytes)
        if length < minimum or length > len(view) - offset:
            raise GridError("adts_frame_length_invalid")
        data = view[offset : offset + length]
        positions = ()
        if crc and blocks > 1:
            positions = tuple((data[i] << 8) | data[i + 1] for i in range(7, header_bytes - 2, 2))
            starts = (0,) + positions + (length - header_bytes,)
            if any(end - start < 3 for start, end in zip(starts, starts[1:], strict=False)):
                raise GridError("adts_crc_layout_invalid")
        samples = 1024 * blocks
        frames.append(ADTSFrame(data, offset, header_bytes, config, blocks, samples, crc, positions))
        total_samples += samples
        duration += Fraction(samples, config.sample_rate)
        offset += length

    return ADTSResult(tuple(frames), tuple(configurations), total_samples, duration)
