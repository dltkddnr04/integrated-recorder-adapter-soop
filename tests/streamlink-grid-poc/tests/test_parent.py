# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
import json
import struct
import threading

import pytest

from conftest import media_body, synthetic_sps
from soop_grid.center import json_body
from soop_grid.media import MediaSink, parse_media
from soop_grid.parent import Parent, parse_parents, receive_media
from soop_grid.protocol import Frame, GridError, parent_packet


def test_parent_byte_order_and_bounds():
    p = parse_parents(
        json_body(
            {"list": [{"parent_ip": 0x08080808, "parent_port": 0x1027, "quality": 1, "cur_frame_no": 99}]}
        )
    )[0]
    assert p.address == ("8.8.8.8", 10000) and p.frame == 99
    repeated = parse_parents(
        json_body(
            {
                "list": [
                    {"parent_ip": 0x08080808, "parent_port": 0x1027, "quality": 1, "cur_frame_no": 99},
                    {"parent_ip": 0x08080808, "parent_port": 0x1027, "quality": 1, "cur_frame_no": 100},
                ]
            }
        )
    )
    assert len(repeated) == 1
    for body in [
        b"",
        struct.pack("<I", 99999) + b"{}",
        json_body({"list": [{}] * 9}),
        json_body({"list": [{"parent_ip": 0x0100007F, "parent_port": 0x1027}]}),
    ]:
        with pytest.raises(GridError):
            parse_parents(body)


def test_parent_sequence_capture_and_live_push(monkeypatch, tmp_path, encoded_bframes):
    import soop_grid.parent as parent

    class Net:
        def __init__(self):
            self.stop = threading.Event()
            self.wires = []
            self.released = False

        def connect(self, address):
            return object()

        def send(self, sock, wire):
            self.wires.append(wire)
            if struct.unpack_from("<H", wire, 2)[0] == 0xCB2B:
                self.stop.set()

        def release(self, sock):
            self.released = True

    payload, _ = encoded_bframes[0]
    body = media_body(payload)
    wire = parent_packet(0xC748, body)
    frames = iter(
        [Frame(0xC746, b""), Frame(0xC747, b""), Frame(0xC748, body, wire=wire), Frame(0xC742, b"")]
    )
    monkeypatch.setattr(parent, "read_frame", lambda *a: next(frames))
    out = []
    events = []
    n = Net()
    directory = tmp_path / "capture"
    sink = MediaSink(out.append, str(directory))
    receive_media(n, [Parent(("8.8.8.8", 10000), 1, 99)], 12345, 42, sink, events.append)
    sink.close()
    sink.close()
    assert [struct.unpack_from("<H", x, 2)[0] for x in n.wires] == [0xCB2E, 0xCB2F, 0xCB30, 0xCB2B]
    assert struct.unpack_from("<IQI", n.wires[2], 16) == (1, 100, 0xFFFFFFFF)
    assert (directory / "grid-payload.bin").read_bytes() == payload
    assert (directory / "grid-wire.bin").read_bytes() == wire
    assert (directory / "recording.ts").read_bytes() == b"".join(out)
    assert json.loads((directory / "grid-analysis.json").read_text())["video"]["height"] == 96
    assert n.released and "parent_c748_media_received" in events


def test_existing_capture_directory_is_not_modified(tmp_path):
    path = tmp_path / "existing"
    path.mkdir()
    (path / "keep").write_text("synthetic")
    with pytest.raises(GridError):
        MediaSink(lambda _: None, str(path))
    assert sorted(p.name for p in path.iterdir()) == ["keep"]


def test_capture_close_attempts_every_file_after_a_disk_error():
    sink = MediaSink(lambda _: None)
    closed = []

    class File:
        def __init__(self, fail):
            self.fail = fail

        def close(self):
            closed.append(self.fail)
            if self.fail:
                raise OSError("fixture-private-disk-diagnostic")

    sink.files = {"one": File(True), "two": File(False)}
    with pytest.raises(GridError, match="capture_close_failed"):
        sink.close()
    assert closed == [True, False]
    sink.close()


@pytest.mark.parametrize("at_deadline", [True, False])
def test_duration_timeout_is_eof_only_at_the_configured_deadline(monkeypatch, at_deadline):
    import soop_grid.parent as parent

    class Net:
        stop = threading.Event()

        def connect(self, address):
            return object()

        def send(self, *args):
            pass

        def release(self, *args):
            pass

    clock = [0]
    monkeypatch.setattr(parent.time, "monotonic", lambda: clock[0])

    def timeout(*args):
        clock[0] = 2 if at_deadline else 0
        raise GridError("network_read_timeout")

    monkeypatch.setattr(parent, "read_frame", timeout)
    args = (Net(), [Parent(("8.8.8.8", 1), 1, 1)], 12345, 42, None, lambda _: None)
    if at_deadline:
        receive_media(*args, duration=1)
    else:
        with pytest.raises(GridError, match="network_read_timeout"):
            receive_media(*args, duration=1)


@pytest.mark.parametrize("mutation", ["short", "header", "extension", "size", "video"])
def test_media_boundaries(mutation):
    b = bytearray(media_body(synthetic_sps()))
    if mutation == "short":
        b = b[:68]
    elif mutation == "header":
        b[2] = 0
    elif mutation == "extension":
        b[4] = 1
    elif mutation == "size":
        b[8] ^= 1
    else:
        b[69] = 1
    with pytest.raises(GridError):
        parse_media(bytes(b))
