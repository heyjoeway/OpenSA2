"""The S5 live run's relay, its comparers and its verdict (tools/s5/present_live.py).

A live run hands the recorder's stream to the DXVK player through a named pipe while the
game runs, then holds the run to the registered boot replay, the live frames to the file
replay's, and the run to having been live and its named frame to having been presented to
a window the compositor had mapped. The relay is the transport and the rest are measuring
instruments, so each is held here without the game, the player or a display:

  * the relay, over a real named pipe with a writer process and a reader process made
    here: every byte the writer wrote is in the copy and reaches the reader, in order,
    and reaches it while the writer is still writing; the writer is never held up by a
    reader that stops reading, and survives a reader that dies (no SIGPIPE: the pipe's
    read end stays open to the end); a writer that never opens the pipe ends the relay
    when its process ends;
  * FrameClock, which finds FRAME records in passing, on a stream cut into pieces at
    every size: the records and frames it counts are the stream's, and a bad record
    size is a problem;
  * the frame comparer, the black-frame rule (held to a hash the player wrote), the record
    comparer, the delivery check, the player check, the liveness check, the on-screen check
    and the named frame, the verdict, the summary and the checkpoint receipt, on literal
    inputs, each shown failing on a one-field difference;
  * the receipts' paths: nothing absolute into a checkout, nothing cited that is not there;
  * the command: present_run's command with only the two wall-clock bounds changed, the
    recorder writing into the pipe; and what counts as a gate pass to wait for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import present_live as PL  # noqa: E402


def pad4(n: int) -> int:
    return (n + 3) & ~3


RECORDER = {"recorder_sha256": "a" * 64, "stream_h_sha256": "b" * 64}


def stream(records: list[tuple[int, bytes]], meta: dict | None = None,
           schema: str = "sa2-d3d9-stream/v1") -> bytes:
    """A stream in host/sa2_stream.h's framing: the header, its meta embedded as a JSON
    string as the writer embeds it, then (kind, payload) records numbered from 0."""
    meta = {"build": "x", **RECORDER} if meta is None else meta
    head = json.dumps({"schema": schema, "meta": json.dumps(meta)}).encode()
    hb = 16 + pad4(len(head))
    out = bytearray(PL.MAGIC + struct.pack("<II", 1, hb) + head + b" " * (hb - 16 - len(head)))
    for i, (kind, payload) in enumerate(records):
        out += struct.pack("<III", kind, 12 + len(payload), i) + payload
    return bytes(out)


def frame(us: int, n: int) -> tuple[int, bytes]:
    return (PL.KIND_FRAME, struct.pack("<QII", us, n, 0))


def call(n: int) -> tuple[int, bytes]:
    return (1, bytes(range(n % 7, n % 7 + 4 * (1 + n % 5))))


BOOT_LIKE = stream([call(i) for i in range(5)] + [frame(83330, 1)]
                   + [call(i) for i in range(5, 9)] + [frame(99996, 2)] + [(5, b"")])


# ---------------------------------------------------------------- FrameClock

@pytest.mark.parametrize("piece", [1, 3, 11, 12, 13, 64, 10_000])
def test_frameclock_counts_the_stream_whatever_the_pieces(piece):
    clock = PL.FrameClock()
    times = []
    for k, at in enumerate(range(0, len(BOOT_LIKE), piece)):
        clock.feed(BOOT_LIKE[at:at + piece], float(k))
        times.append(float(k))
    assert clock.problem is None
    assert clock.records == 12
    assert len(clock.frames) == 2
    # Each frame is timed by the piece that brought its record's last byte.
    ends = []
    off = pad4(struct.unpack_from("<I", BOOT_LIKE, 12)[0])
    while off < len(BOOT_LIKE):
        kind, size, _ = struct.unpack_from("<III", BOOT_LIKE, off)
        if kind == PL.KIND_FRAME:
            ends.append(off + size)
        off += size
    assert clock.frames == [float((e - 1) // piece) for e in ends]


def test_frameclock_names_a_record_of_a_size_the_framing_forbids():
    bad = bytearray(BOOT_LIKE)
    first = pad4(struct.unpack_from("<I", bad, 12)[0])
    struct.pack_into("<I", bad, first + 4, 13)
    clock = PL.FrameClock()
    clock.feed(bytes(bad), 0.0)
    assert clock.problem == f"the record at offset {first} has size 13"
    assert clock.frames == []


def test_frameclock_refuses_what_is_not_a_stream():
    clock = PL.FrameClock()
    clock.feed(b"NOTASTREAM" * 3, 0.0)
    assert clock.problem and clock.frames == []


# ---------------------------------------------------------------- the relay

WRITER = r"""
import os, sys, time
path, data_path, pause = sys.argv[1], sys.argv[2], float(sys.argv[3])
data = open(data_path, "rb").read()
fd = os.open(path, os.O_WRONLY)
for at in range(0, len(data), 70_000):
    os.write(fd, data[at:at + 70_000])
    time.sleep(pause)
os.close(fd)
print(f"writer done {time.monotonic()!r}", flush=True)
"""

READER = r"""
import hashlib, os, sys, time
limit = int(sys.argv[2])
h, n, first = hashlib.sha256(), 0, None
while True:
    b = os.read(0, 1 << 16)
    if not b:
        break
    if first is None:
        first = time.monotonic()
    h.update(b); n += len(b)
    if limit and n >= limit:
        sys.exit(7)
open(sys.argv[1], "w").write(f"{n} {h.hexdigest()} {first!r}")
"""


def relay_run(tmp_path: Path, payload: bytes, *, reader_limit: int = 0,
              stalled_reader: bool = False, pause: float = 0.0):
    """One relay over a real pipe: a writer process, the relay, and a reader process
    (or a pipe nobody reads), as run_live wires them."""
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    fifo, copy_, got = tmp_path / "s.fifo", tmp_path / "copy.bin", tmp_path / "got.txt"
    if stalled_reader:
        r, sink = os.pipe()
        reader = None
    else:
        reader = subprocess.Popen([sys.executable, "-c", READER, str(got), str(reader_limit)],
                                  stdin=subprocess.PIPE)
        sink = os.dup(reader.stdin.fileno())
        reader.stdin.close()
    box: dict = {}

    def go():   # in a thread, so a relay that waits on its reader fails instead of hanging
        box["fd"] = PL.open_pipe(fifo)
        box["st"] = PL.relay(box["fd"], copy_, sink,
                             lambda: "writer" not in box or box["writer"].poll() is None,
                             grace=0 if stalled_reader else 30)

    t = time.monotonic()
    th = threading.Thread(target=go, daemon=True)
    th.start()
    while not fifo.exists() and time.monotonic() - t < 10:
        time.sleep(0.001)
    writer = subprocess.Popen([sys.executable, "-c", WRITER, str(fifo), str(src), str(pause)],
                              stdout=subprocess.PIPE, text=True)
    box["writer"] = writer
    th.join(timeout=30)
    relay_s = time.monotonic() - t
    if th.is_alive():
        writer.kill()
        if reader:
            reader.kill()
        pytest.fail(f"the relay did not end within 30 s (writer rc {writer.poll()})")
    st = box["st"]
    os.close(box["fd"])
    out = writer.communicate(timeout=30)[0]
    if stalled_reader:
        os.close(r)       # the feeder, blocked on the full pipe, now fails and ends
        st["feed_thread"].join(timeout=30)
    elif reader:
        reader.wait(timeout=30)
    return st, writer, out, copy_, got, relay_s, reader


def test_relay_copies_and_delivers_every_byte(tmp_path):
    payload = os.urandom(1_234_567)
    st, writer, out, copy_, got, _, reader = relay_run(tmp_path, payload, pause=0.001)
    assert writer.returncode == 0 and out.startswith("writer done")
    assert st["end"] == "the writer closed the pipe"
    assert st["bytes"] == len(payload) == copy_.stat().st_size
    assert copy_.read_bytes() == payload
    assert st["feed"]["bytes"] == len(payload) and st["feed"]["error"] is None
    assert reader.returncode == 0
    n, digest, _first = got.read_text().split()
    assert (int(n), digest) == (len(payload), hashlib.sha256(payload).hexdigest())


def test_relay_hands_the_reader_bytes_while_the_writer_is_still_writing(tmp_path):
    # Twenty chunks 0.05 s apart: the reader has its first bytes about a second before the
    # writer closes the pipe. A feeder that waited for the close would hand it nothing
    # until then: a file replayed after the game, not a live run.
    payload = os.urandom(20 * 70_000)
    st, writer, out, _, got, _, reader = relay_run(tmp_path, payload, pause=0.05)
    assert reader.returncode == 0 and writer.returncode == 0
    writer_closed = float(out.split()[-1])
    reader_first = float(got.read_text().split()[2])
    assert reader_first < writer_closed - 0.5
    assert st["feed"]["first_write_monotonic"] < st["closed_monotonic"] - 0.5
    assert st["feed"]["bytes_taken_while_open"] >= len(payload) // 2


def test_relay_never_holds_the_writer_for_a_reader_that_stops_reading(tmp_path):
    # 3 MB through a pipe whose far end nobody reads: the writer still finishes, and the
    # copy is whole, while the feeder waits; closing the far end then ends the feeder.
    payload = os.urandom(3_000_000)
    st, writer, out, copy_, _, relay_s, _ = relay_run(tmp_path, payload, stalled_reader=True)
    assert writer.returncode == 0 and out.startswith("writer done")
    assert copy_.read_bytes() == payload and st["bytes"] == len(payload)
    assert relay_s < 20
    assert st["feed"]["error"] and "Broken pipe" in st["feed"]["error"]
    assert st["feed"]["bytes"] < len(payload)


def test_relay_outlives_a_reader_that_dies_and_the_writer_is_not_signalled(tmp_path):
    # The writer pauses longer than the relay's poll between chunks, as the game does
    # between frames, and goes on writing well after the reader has died.
    payload = os.urandom(700_000)
    st, writer, out, copy_, _, _, reader = relay_run(tmp_path, payload, reader_limit=100_000,
                                                     pause=0.15)
    assert reader.returncode == 7
    assert writer.returncode == 0 and out.startswith("writer done")   # not -SIGPIPE
    assert copy_.read_bytes() == payload
    assert st["end"] == "the writer closed the pipe"
    assert "Broken pipe" in (st["feed"]["error"] or "")


def test_relay_ends_when_no_writer_ever_opens_the_pipe(tmp_path):
    # Opening the pipe must not wait for a writer, and a writer that never comes must not
    # hold the relay once its process is gone. Run in a thread, so a relay that waits
    # fails the test instead of hanging it.
    fifo = tmp_path / "s.fifo"
    r, sink = os.pipe()
    deadline = time.monotonic() + 0.5
    box: dict = {}

    def go():
        fd = PL.open_pipe(fifo)
        box["opened_at"] = time.monotonic()
        box["st"] = PL.relay(fd, tmp_path / "copy.bin", sink,
                             lambda: time.monotonic() < deadline, grace=5)
        os.close(fd)

    th = threading.Thread(target=go, daemon=True)
    th.start()
    th.join(timeout=20)
    if th.is_alive():
        if "opened_at" not in box:          # a blocking open: let it return, then fail
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        pytest.fail("the relay did not end within 20 s of its writer's process ending")
    os.close(r)
    st = box["st"]
    assert box["opened_at"] < deadline
    assert st["end"] == "the game ended without opening the pipe"
    assert st["bytes"] == 0 and st["feed"]["bytes"] == 0 and st["feed"]["error"] is None


def test_relay_times_the_frames_it_passes(tmp_path):
    st, *_ = relay_run(tmp_path, BOOT_LIKE)
    assert st["frames_arrived"] == 2 and st["records"] == 12 and st["framing_problem"] is None
    assert len(st["frame_arrival_s"]) == 2


# ---------------------------------------------------------------- frames

def test_black_hash_is_the_hash_the_player_wrote_for_a_black_boot_frame():
    # Frame 190 of the boot, as host/player wrote it into frames.y4m.times.tsv
    # (private/generated/s5/live/run-full/player): a black 1280 x 720 back buffer.
    assert PL.black_hash(1280, 720) == "0x4bd87a07a4536325"
    assert PL.black_hash(1280, 719) != "0x4bd87a07a4536325"


def rows(hashes: list[str]) -> list[dict]:
    return [{"frame": str(i + 1), "seq": str(100 + i), "virtual_us": str(16666 * (i + 1)),
             "guest_frames": str(i + 1), "polls": str(i), "wall_us": str(1000 * i + 7),
             "bgrx_hash": x} for i, x in enumerate(hashes)]


def test_frames_compare_equal_whatever_the_wall_clock():
    mine, theirs = rows(["0x1", "0x2", "0x3"]), rows(["0x1", "0x2", "0x3"])
    for r in theirs:
        r["wall_us"] = "999999"
    assert PL.compare_frames(mine, theirs) == {"live": 3, "file": 3, "compared": 3,
                                               "differing": 0, "first_differing": [],
                                               "seq_offsets": [0], "ok": True}


@pytest.mark.parametrize("col", ("virtual_us", "guest_frames", "polls", "bgrx_hash"))
def test_frames_compare_fails_on_any_picture_or_time_column(col):
    mine, theirs = rows(["0x1", "0x2", "0x3"]), rows(["0x1", "0x2", "0x3"])
    theirs[1][col] = "0xdead"
    got = PL.compare_frames(mine, theirs)
    assert got["ok"] is False and got["differing"] == 1 and got["first_differing"] == ["2"]


def test_frames_compare_takes_a_stream_that_gained_records_before_its_first_frame():
    # The recorder fold writes Direct3DCreate9 at seq 0: every Present's seq is one higher.
    mine, theirs = rows(["0x1", "0x2", "0x3"]), rows(["0x1", "0x2", "0x3"])
    for r in mine:
        r["seq"] = str(int(r["seq"]) + 1)
    got = PL.compare_frames(mine, theirs)
    assert got["ok"] is True and got["seq_offsets"] == [1]


def test_frames_compare_fails_when_one_frame_moved_in_the_stream():
    mine, theirs = rows(["0x1", "0x2", "0x3"]), rows(["0x1", "0x2", "0x3"])
    mine[2]["seq"] = str(int(mine[2]["seq"]) + 5)
    got = PL.compare_frames(mine, theirs)
    assert got["ok"] is False and got["differing"] == 0 and got["seq_offsets"] == [0, 5]


def test_frames_compare_fails_on_a_missing_frame():
    got = PL.compare_frames(rows(["0x1", "0x2"]), rows(["0x1", "0x2", "0x3"]))
    assert got["ok"] is False and got["differing"] == 0 and got["compared"] == 2


def y4m(header: bytes, frames: list[bytes]) -> bytes:
    return header + b"".join(b"FRAME\n" + f for f in frames)


def test_y4m_frames_equal_under_stream_headers_that_differ(tmp_path):
    a, b = tmp_path / "a.y4m", tmp_path / "b.y4m"
    frames = [bytes([i]) * 6 for i in range(3)]
    a.write_bytes(y4m(b"YUV4MPEG2 W2 H2 F60:1 Ip A1:1 C420jpeg XCOLORRANGE=LIMITED\n", frames))
    b.write_bytes(y4m(b"YUV4MPEG2 W2 H2 F60:1 Ip A1:1 C420jpeg\n", frames))
    got = PL.same_y4m_frames(a, b)
    assert got["ok"] is True and got["bytes"][0] == got["bytes"][1] == 3 * 12
    assert got["headers"][0].endswith("XCOLORRANGE=LIMITED")


def test_y4m_frames_differ_on_one_pixel_byte_or_on_geometry(tmp_path):
    a, b = tmp_path / "a.y4m", tmp_path / "b.y4m"
    frames = [bytes([i]) * 6 for i in range(3)]
    a.write_bytes(y4m(b"YUV4MPEG2 W2 H2 F60:1\n", frames))
    b.write_bytes(y4m(b"YUV4MPEG2 W2 H2 F60:1\n", frames[:2] + [b"\x02\x02\x02\x02\x02\x03"]))
    got = PL.same_y4m_frames(a, b)
    assert got["ok"] is False and got["first_differing_byte"] == 2 * 12 + 6 + 5
    b.write_bytes(y4m(b"YUV4MPEG2 W4 H1 F60:1\n", frames))
    assert PL.same_y4m_frames(a, b)["ok"] is False


def test_first_game_frame_is_the_first_that_is_not_black():
    black = PL.black_hash(4, 2)
    got = PL.first_game_frame(rows([black, black, "0x77", black]), 4, 2)
    assert got == {"index": 3, "seq": 102, "virtual_us": 49998, "bgrx_hash": "0x77",
                   "black_hash": black}
    assert PL.first_game_frame(rows([black, black]), 4, 2) is None


def png(n: int, live: str, file: str | None) -> dict:
    return {n: {"png": f"live/frame-{n:05d}.png", "png_sha256": live,
                "file_replay_png": None if file is None else f"file/frame-{n:05d}.png",
                "file_replay_png_sha256": file}}


def test_first_non_black_holds_when_its_png_is_the_file_replays():
    black = PL.black_hash(4, 2)
    got = PL.first_non_black(rows([black, "0x77"]), 4, 2, png(2, "e" * 64, "e" * 64))
    assert got["ok"] is True and got["frame"]["index"] == 2 and got["frame"]["png_equal"] is True


@pytest.mark.parametrize("pngs", [png(2, "e" * 64, "f" * 64), png(2, "e" * 64, None), {}])
def test_first_non_black_fails_without_the_file_replays_png(pngs):
    black = PL.black_hash(4, 2)
    got = PL.first_non_black(rows([black, "0x77"]), 4, 2, pngs)
    assert got["ok"] is False and got["frame"]["index"] == 2


def test_same_bytes_names_the_first_differing_byte(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 5000 + b"A" + b"y" * 10)
    b.write_bytes(b"x" * 5000 + b"B" + b"y" * 10)
    got = PL.same_bytes(a, b)
    assert got["ok"] is False and got["first_differing_byte"] == 5000
    b.write_bytes(a.read_bytes())
    assert PL.same_bytes(a, b)["ok"] is True


# ---------------------------------------------------------------- records

RECS = [call(i) for i in range(4)] + [frame(1, 1), (5, b"")]


def test_records_equal_under_headers_whose_meta_differs(tmp_path):
    a, b = tmp_path / "a.sa2s", tmp_path / "b.sa2s"
    a.write_bytes(stream(RECS, {"build": "present-live", "transport": "fifo", **RECORDER}))
    b.write_bytes(stream(RECS, {"build": "present-boot", **RECORDER}))
    got = PL.compare_records(a, b)
    assert got["ok"] is True and got["same_recorder"] is True
    assert got["header_keys_differing"] == ["meta"]
    assert got["meta_keys_differing"] == ["build", "transport"]
    assert got["records_sha256"][0] == got["records_sha256"][1]


@pytest.mark.parametrize("key", ["recorder_sha256", "stream_h_sha256"])
def test_records_from_another_recorder_are_not_comparable(tmp_path, key):
    a, b = tmp_path / "a.sa2s", tmp_path / "b.sa2s"
    a.write_bytes(stream(RECS))
    b.write_bytes(stream(RECS, {"build": "x", **RECORDER, key: "c" * 64}))
    got = PL.compare_records(a, b)
    assert got["records_sha256"][0] == got["records_sha256"][1]
    assert got["same_recorder"] is False and got["ok"] is False


def test_records_do_not_hide_a_header_that_differs_beyond_its_meta(tmp_path):
    a, b = tmp_path / "a.sa2s", tmp_path / "b.sa2s"
    a.write_bytes(stream(RECS))
    b.write_bytes(stream(RECS, schema="sa2-d3d9-stream/v2"))
    got = PL.compare_records(a, b)
    assert got["header_keys_differing"] == ["schema"]
    assert got["records_sha256"][0] == got["records_sha256"][1]
    assert got["ok"] is False


def test_records_differ_on_one_byte(tmp_path):
    a, b = tmp_path / "a.sa2s", tmp_path / "b.sa2s"
    a.write_bytes(stream(RECS))
    data = bytearray(stream(RECS))
    data[-20] ^= 1
    b.write_bytes(bytes(data))
    got = PL.compare_records(a, b)
    hb = pad4(struct.unpack_from("<I", data, 12)[0])
    assert got["ok"] is False and got["first_differing_record_byte"] == len(data) - 20 - hb


# ---------------------------------------------------------------- delivery

SIDECAR = "closed: 4188919 records, 965419824 bytes, 1335 frames\n"


def relay_counts(**over) -> dict:
    """relay.json's counts for a whole run whose recorder wrote SIDECAR."""
    r = {"bytes": 965419824, "records": 4188919, "frames_arrived": 1335,
         "end": "the writer closed the pipe", "framing_problem": None,
         "feed": {"bytes": 965419824, "bytes_taken_while_open": 965000000, "error": None}}
    r.update(over)
    return r


def test_the_sidecar_is_read_as_the_recorder_wrote_it():
    assert PL.sidecar_counts(SIDECAR) == {"records": 4188919, "bytes": 965419824, "frames": 1335}
    assert PL.sidecar_counts("closed: counting only") is None
    assert PL.sidecar_counts(None) is None


def test_delivery_holds_when_recorder_relay_copy_and_player_agree():
    got = PL.check_delivery(relay_counts(), 965419824, "d" * 64, SIDECAR)
    assert got["ok"] is True and got["recorder_wrote"]["bytes"] == 965419824
    assert got["player_given_while_the_pipe_was_open"] == 965000000


@pytest.mark.parametrize("over, copied, sidecar", [
    ({}, 965419823, SIDECAR),                                           # the copy is short
    ({"bytes": 965419823}, 965419824, SIDECAR),                         # the relay read less
    ({}, 965419824, "closed: 4188919 records, 965419825 bytes, 1335 frames"),  # the recorder wrote more
    ({}, 965419824, "closed: 4188918 records, 965419824 bytes, 1335 frames"),  # other records
    ({}, 965419824, "closed: 4188919 records, 965419824 bytes, 1334 frames"),  # other frames
    ({}, 965419824, None),                                              # no sidecar
    ({"feed": {"bytes": 965419800, "error": None}}, 965419824, SIDECAR),  # the player got less
    ({"feed": {"bytes": 965419824, "error": "BrokenPipeError: x"}}, 965419824, SIDECAR),
    ({"framing_problem": "the record at offset 16 has size 13"}, 965419824, SIDECAR),
    ({"end": "the game ended without opening the pipe"}, 965419824, SIDECAR),
])
def test_delivery_fails_on_each_count(over, copied, sidecar):
    assert PL.check_delivery(relay_counts(**over), copied, "d" * 64, sidecar)["ok"] is False


# ---------------------------------------------------------------- the player's verdict

def report(**over) -> dict:
    """A player report of a whole, passing play of a 3-frame stream whose bytes hash to
    'd' * 64, in host/player/player.cpp's keys."""
    r = {"complete": True, "pass": True, "exit_status": 0, "failures": [], "presents": 3,
         "frames_out": {"frames": 3}, "errors": 0, "stop": "end of stream",
         "present_failures": 0, "readback_failures": 0,
         "calls": {"handled": 90, "ignored_by_design": 5, "skipped_by_stage": 0,
                   "not_handled": 0},
         "records": {"call": 95, "d3dx": 1, "frame": 3, "note": 0, "end": 1, "seq_gaps": 0},
         "stream_sha256": "d" * 64}
    r.update(over)
    return r


def test_the_player_check_passes_a_whole_play_of_the_relayed_bytes():
    got = PL.check_player(report(), "d" * 64, 3)
    assert got["ok"] is True and got["not_handled"] == 0 and got["frames_written"] == 3


@pytest.mark.parametrize("over", [
    {"pass": False}, {"exit_status": 4}, {"complete": False}, {"presents": 2},
    {"frames_out": {"frames": 2}}, {"errors": 1}, {"stop": "the window was closed"},
    {"calls": {"handled": 90, "ignored_by_design": 4, "skipped_by_stage": 0, "not_handled": 1}},
    {"records": {"call": 95, "d3dx": 1, "frame": 3, "note": 0, "end": 1, "seq_gaps": 1}},
    {"stream_sha256": "e" * 64}, {"stream_sha256": None},
    {"present_failures": 1}, {"readback_failures": 1}, {"present_failures": None},
])
def test_the_player_check_fails_on_each_field(over):
    assert PL.check_player(report(**over), "d" * 64, 3)["ok"] is False


def test_the_player_check_fails_when_the_stream_had_other_frames_or_no_copy():
    assert PL.check_player(report(), "d" * 64, 4)["ok"] is False
    assert PL.check_player(report(), None, 3)["ok"] is False


# ---------------------------------------------------------------- liveness

# One clock, CLOCK_MONOTONIC: the relay started at T0, the player 10 ms before it. Three
# frames reach the relay at 1.0, 1.1 and 1.2 s and are read back at 1.005, 1.108 and
# 1.203 s; the game entered GameLoop at 0.5 s, its three ticks at 0.6, 1.05 and 1.25 s,
# and it exited at 1.3 s (relay seconds).
T0, T_PLAYER = 1000.0, 999.99


def live_relay(**over) -> dict:
    r = {"t0_monotonic": T0, "frame_arrival_s": [1.0, 1.1, 1.2],
         "closed_monotonic": T0 + 1.29, "player_spawned_monotonic": T_PLAYER - 0.2,
         "feed": {"blocked_s": 0.01, "blocked_max_s": 0.005}}
    r.update(over)
    return r


def live_rows(read_back=(1.005, 1.108, 1.203)) -> list[dict]:
    out = rows(["0x1", "0x2", "0x3"])
    for r, x in zip(out, read_back):
        r["wall_us"] = str(round((x + T0 - T_PLAYER) * 1e6))
        r["virtual_us"] = str(83330 + 16666 * (int(r["frame"]) - 1))
    return out


def live_report(**over) -> dict:
    return report(t_start_monotonic_s=T_PLAYER, live_input=True, **over)


GAME = {"from": "run/lifted-words.bin", "whole": True, "first_entry_monotonic_s": T0 + 0.5,
        "exit_monotonic_s": T0 + 1.3, "tick_entry_monotonic_s": [T0 + 0.6, T0 + 1.05, T0 + 1.25]}


def test_frame_lags_are_read_back_minus_arrival_on_one_clock():
    lags = PL.frame_lags(live_relay(), live_rows(), T_PLAYER)
    assert [round(x, 6) for x in lags] == [0.005, 0.008, 0.003]


def test_liveness_holds_when_every_frame_is_read_back_after_it_arrived_and_before_the_exit():
    got = PL.liveness(live_relay(), live_rows(), live_report(), GAME)
    assert got["ok"] is True and got["lag_exact"] is True
    assert got["game_exit_s"] == 1.3 and got["game_first_entry_s"] == 0.5
    assert got["game_ticks"] == 3 and got["game_last_tick_s"] == 1.25
    assert got["ticks_entered_after_frame_1_was_read_back"] == 2
    assert "tick_entry_monotonic_s" not in got["game_clock"]
    assert got["read_back_at_or_after_game_exit"] == {"count": 0, "first": []}
    assert got["last_read_back_before_exit_s"] == pytest.approx(0.097, abs=1e-6)
    assert got["lag_s"]["min"] == 0.003 and got["lag_s"]["max"] == 0.008
    assert got["read_back_before_the_next_frame_arrived"] == 2
    # 33,332 virtual microseconds over the 0.2 s the frames took to arrive.
    assert got["pace_virtual_s_per_wall_s"] == pytest.approx(0.1667, abs=1e-4)


def test_liveness_fails_without_the_players_start_time():
    # The spawn, 1 ms before the start, stands in: every read-back still falls after its
    # arrival and before the exit, and the values are kept, marked, but the check fails.
    rep = live_report()
    del rep["t_start_monotonic_s"]
    got = PL.liveness(live_relay(player_spawned_monotonic=T_PLAYER - 0.001), live_rows(), rep,
                      GAME)
    assert got["read_back_not_after_arrival"]["count"] == 0
    assert got["read_back_at_or_after_game_exit"]["count"] == 0
    assert got["ok"] is False and got["lag_exact"] is False
    assert got["player_start"].startswith("NOT EXACT")


def test_liveness_fails_when_a_frame_is_read_back_after_the_game_exited():
    # What a player fed only after the game ended shows: a file replayed afterwards.
    got = PL.liveness(live_relay(), live_rows((1.005, 1.108, 1.31)), live_report(), GAME)
    assert got["ok"] is False
    assert got["read_back_at_or_after_game_exit"] == {"count": 1, "first": [3]}


def test_liveness_fails_when_frame_1_is_read_back_after_the_games_last_tick():
    # Every frame before the exit, but the game had entered its last tick first: not
    # "while it runs".
    game = {**GAME, "tick_entry_monotonic_s": [T0 + 0.6, T0 + 0.8, T0 + 1.0]}
    got = PL.liveness(live_relay(), live_rows(), live_report(), game)
    assert got["read_back_at_or_after_game_exit"]["count"] == 0
    assert got["ticks_entered_after_frame_1_was_read_back"] == 0
    assert got["ok"] is False


def test_liveness_fails_when_a_frame_is_read_back_before_its_bytes_arrived():
    got = PL.liveness(live_relay(), live_rows((1.005, 1.099, 1.203)), live_report(), GAME)
    assert got["ok"] is False and got["read_back_not_after_arrival"] == {"count": 1, "first": [2]}


@pytest.mark.parametrize("change", ["no_exit", "short_rows", "extra_arrival", "file_input",
                                    "no_arrivals", "no_ticks"])
def test_liveness_fails_on_each_missing_piece(change):
    relay_st, rws, rep, game = live_relay(), live_rows(), live_report(), dict(GAME)
    if change == "no_exit":
        game["exit_monotonic_s"] = None
    elif change == "short_rows":
        rws = rws[:2]
    elif change == "extra_arrival":
        relay_st["frame_arrival_s"] = [1.0, 1.1, 1.2, 1.25]
    elif change == "file_input":
        rep["live_input"] = False
    elif change == "no_arrivals":
        relay_st["frame_arrival_s"] = []
    elif change == "no_ticks":
        game["tick_entry_monotonic_s"] = []
    assert PL.liveness(relay_st, rws, rep, game)["ok"] is False


# ---------------------------------------------------------------- on screen

FOCUS, SUSPENDED = PL.SDL_WINDOW_INPUT_FOCUS, PL.SDL_WINDOW_OCCLUDED
VULKAN = 0x10000000


def sess(locked: str = "no", idle: str = "no", live: bool = True, remote: str = "no") -> dict:
    return {"live_desktop_session": live,
            "logind": {"session": "2", "Type": "wayland", "Active": "yes", "Remote": remote,
                       "Seat": "seat0", "LockedHint": locked, "IdleHint": idle}}


def screen_report(runs=((1, 1, VULKAN), (2, 3, VULKAN | FOCUS)), **over) -> dict:
    return report(host={"sdl_video_driver": "wayland"},
                  device={"back_buffer": [4, 2, 21]},
                  window={"flags_at_present": [list(r) for r in runs]}, **over)


def screen_rows() -> list[dict]:
    return rows(["0x11", "0x22", "0x33"])


def screen_pngs(frame_png: str = "e" * 64) -> dict:
    return {**png(1, "a" * 64, "a" * 64), **png(2, "e" * 64, frame_png)}


def test_window_flags_expand_the_players_runs():
    assert PL.window_flags({"window": {"flags_at_present": [[1, 1, 4], [2, 4, 512]]}}) == {
        1: 4, 2: 512, 3: 512, 4: 512}
    assert PL.window_flags({}) == {}


def test_the_named_frame_is_the_first_after_the_mapping_present_with_focus_not_suspended():
    black = PL.black_hash(4, 2)
    r = rows(["0x11", black, "0x33", "0x44", "0x55"])
    flags = {1: FOCUS, 2: FOCUS, 3: FOCUS | SUSPENDED, 4: 0, 5: FOCUS}
    # 1 is the Present that maps the window, 2 is black, 3 suspended, 4 without focus.
    assert PL.named_frame(r, flags, black) == {"index": 5, "seq": 104, "virtual_us": 83330,
                                               "bgrx_hash": "0x55", "window_flags": "0x200"}
    assert PL.named_frame(r, {1: FOCUS}, black) is None


def test_on_screen_holds_for_a_focused_frame_on_an_unlocked_active_session():
    got = PL.on_screen(sess(), sess(), screen_report(), screen_rows(), screen_pngs())
    assert got["ok"] is True
    assert got["frame"]["index"] == 2 and got["frame"]["png_equal"] is True
    assert got["presents_with"] == {"keyboard_focus": 2, "suspended": 0,
                                    "keyboard_focus_and_not_suspended": 2}
    assert got["window_sampled_at_every_present"] is True


@pytest.mark.parametrize("change", [
    "locked_before", "locked_after", "idle_before", "idle_after", "not_live", "no_logind",
    "driver", "unsampled_present", "present_failed", "no_focus", "suspended", "png_differs",
    "png_missing",
])
def test_on_screen_fails_on_each_clause(change):
    before, after, pngs = sess(), sess(), screen_pngs()
    rep = screen_report()
    if change == "locked_before":
        before = sess(locked="yes")
    elif change == "locked_after":
        after = sess(locked="yes")
    elif change == "idle_before":
        before = sess(idle="yes")
    elif change == "idle_after":
        after = sess(idle="yes")
    elif change == "not_live":
        before = sess(live=False)
    elif change == "no_logind":
        after = {"live_desktop_session": True}
    elif change == "driver":
        rep["host"] = {"sdl_video_driver": "offscreen"}
    elif change == "unsampled_present":
        rep["window"]["flags_at_present"] = [[1, 1, VULKAN], [2, 2, VULKAN | FOCUS]]
    elif change == "present_failed":
        rep["present_failures"] = 1
    elif change == "no_focus":
        rep["window"]["flags_at_present"] = [[1, 3, VULKAN]]
    elif change == "suspended":
        rep["window"]["flags_at_present"] = [[1, 1, VULKAN], [2, 3, VULKAN | FOCUS | SUSPENDED]]
    elif change == "png_differs":
        pngs = screen_pngs("f" * 64)
    elif change == "png_missing":
        pngs = png(1, "a" * 64, "a" * 64)
    assert PL.on_screen(before, after, rep, screen_rows(), pngs)["ok"] is False


def test_on_screen_says_what_sdl_can_and_cannot_see_on_wayland():
    got = PL.on_screen(sess(), sess(), screen_report(), screen_rows(), screen_pngs())
    seen = " ".join(got["what_sdl_observes_on_wayland"]["observed"])
    unseen = " ".join(got["what_sdl_observes_on_wayland"]["not_observed"])
    assert "wl_keyboard.enter" in seen and "suspended" in seen
    assert "minimize" in unseen and "SDL_HideWindow" in unseen and "capture" in unseen


# ---------------------------------------------------------------- the verdict and the receipt

def passing_full() -> dict:
    ok = {"ok": True}
    return {"inputs_check": dict(ok), "build_check": dict(ok),
            "checks": {"run": {k: dict(ok) for k in ("lifted_csv", "reach", "words", "stream")},
                       "records": dict(ok), "delivery": dict(ok),
                       "player": {"ok": True, "presents": 3, "host": {"sdl_video_driver": "wayland"}},
                       "frames": {"ok": True, "against": [{"file_replay": "private/f", "ok": True,
                                                           "times": {}, "y4m": {}}]},
                       "first_non_black": {"ok": True, "frame": {"index": 1}},
                       "liveness": {"ok": True, "lag_s": {"median": 0.006},
                                    "pace_virtual_s_per_wall_s": 0.27},
                       "on_screen": {"ok": True, "sdl_video_driver": "wayland",
                                     "frame": {"index": 2, "png_sha256": "e" * 64}}},
            "receipt_paths": dict(ok), "run": {"rc": 0, "player_rc": 0}}


def test_the_verdict_holds_when_every_part_holds():
    assert PL.verdict(passing_full()) is True
    assert PL.failed_parts(passing_full()) == []


@pytest.mark.parametrize("part", [
    "inputs_check", "build_check", "run.words", "records", "delivery", "player", "frames",
    "first_non_black", "liveness", "on_screen", "receipt_paths", "rc", "player_rc", "no_run",
])
def test_the_verdict_fails_on_each_part(part):
    full = passing_full()
    if part in ("inputs_check", "build_check", "receipt_paths"):
        full[part]["ok"] = False
    elif part == "run.words":
        full["checks"]["run"]["words"]["ok"] = False
    elif part in ("rc", "player_rc"):
        full["run"][part] = 1
    elif part == "no_run":
        full["checks"]["run"] = {}
    else:
        full["checks"][part]["ok"] = False
    assert PL.verdict(full) is False
    assert PL.failed_parts(full)


def test_the_summary_names_the_process_boundary_and_the_named_frame():
    text = PL.summary(passing_full())
    assert "separate player process" in text and "static binary" in text
    assert "recording device's COM objects" in text
    assert "Frame 2 was presented while the window held the compositor's keyboard focus" in text
    assert "e" * 64 in text and "No capture of the screen was taken" in text


def test_the_summary_of_a_run_that_did_not_hold_names_what_failed():
    full = passing_full()
    full["checks"]["liveness"]["ok"] = False
    full["run"]["player_rc"] = 4
    assert PL.summary(full) == ("The live run did not hold: liveness, the player's exit "
                                "status 4.")


def test_the_checkpoint_receipt_answers_from_the_verdict_and_cites_the_kept_word_log(tmp_path):
    lp = PL.live_paths(tmp_path / "run")
    p = lp["game"]
    p["out"].mkdir(parents=True)
    p["words_zst"].write_bytes(b"x")
    spec = f"ticks|SA2_REPLAY_WORDS={PL.rel(p['words'])}|SA2_X=1"
    full = passing_full()
    full["run"]["command"] = ["python", "loop_run.py", "--run", spec]
    got = PL.checkpoint_receipt(full, lp)
    assert got["answer"] == "yes" and got["ok"] is True and got["failed"] == []
    assert got["how_the_calls_reach_dxvk"] == PL.TRANSPORT
    assert got["command"]["game"][3] == f"ticks|SA2_REPLAY_WORDS={PL.rel(p['words_zst'])}|SA2_X=1"
    assert "compressed" in got["command"]["game_note"]
    # Nothing in the receipt names the log the tool removed (the first live run's receipt
    # did, in this note, and its receipt_paths check failed on it).
    assert not re.search(re.escape(PL.rel(p["words"])) + r"(?!\.zst)", json.dumps(got))
    full["checks"]["on_screen"]["ok"] = False
    no = PL.checkpoint_receipt(full, lp)
    assert no["answer"] == "no: see the checks" and no["failed"] == ["on_screen"]


def test_the_command_keeps_its_word_log_path_while_the_log_is_uncompressed(tmp_path):
    lp = PL.live_paths(tmp_path / "run")
    p = lp["game"]
    p["out"].mkdir(parents=True)
    p["words"].write_bytes(b"x")
    cmd = ["--run", f"SA2_REPLAY_WORDS={PL.rel(p['words'])}|SA2_X=1"]
    assert PL.kept_command(cmd) == (cmd, None)
    p["words_zst"].write_bytes(b"x")          # both: the log the run was given is still there
    assert PL.kept_command(cmd) == (cmd, None)
    assert PL.kept_command(None) == (None, None)


def test_the_command_cites_the_kept_log_wherever_the_run_is_held_from(tmp_path):
    # The run's command names its own directory; a --check from another --out still
    # rewrites it to the .zst the run kept, and leaves the rest of the command alone.
    ran = tmp_path / "ran"
    ran.mkdir()
    (ran / "lifted-words.bin.zst").write_bytes(b"x")
    cmd = ["python", "--run", f"ticks|SA2_REPLAY_WORDS={ran}/lifted-words.bin|SA2_X=1"]
    got, note = PL.kept_command(cmd)
    assert got == ["python", "--run", f"ticks|SA2_REPLAY_WORDS={ran}/lifted-words.bin.zst|SA2_X=1"]
    assert note and "compressed" in note


def test_the_rule_11_record_takes_its_risk_from_the_structure_and_says_what_was_not_exercised():
    checks = passing_full()["checks"]
    checks["on_screen"]["presents_with"] = {"keyboard_focus": 2, "suspended": 0}
    got = PL.focus_and_occlusion({}, (sess(), sess()), checks, {}, 84.0, {"row_s": 84.8})
    assert got["determinism_risk_for_replays"] is False
    assert got["occlusion"] == ("not exercised: the compositor never suspended the window at "
                                "a Present")
    assert got["structure"]["player_takes_input"].startswith("no input except a close request")
    assert got["measurement"]["game_row_s"] == 84.0
    assert got["measurement"]["file_run"] == {"row_s": 84.8}
    del checks["on_screen"]["presents_with"]
    unmeasured = PL.focus_and_occlusion({}, (sess(), sess()), checks, {}, 84.0, {})
    assert unmeasured["occlusion"] == "not measured: the player sampled no window state"
    checks["on_screen"]["presents_with"] = {"keyboard_focus": 2, "suspended": 1}
    assert PL.focus_and_occlusion({}, (sess(), sess()), checks, {}, 84.0, {})["occlusion"] == (
        "suspended at 1 Presents")


# ---------------------------------------------------------------- the receipts' paths

def test_relativized_writes_every_path_into_the_checkout_relative_to_it():
    root = str(PL.ROOT)
    got = PL.relativized({f"{root}/host/a.c": [f"{root}/.venv/bin/python",
                                               f"x={root}/tools/b.py|y", 3, None],
                          "cwd": root, "other": "/usr/bin/zstd"})
    assert got == {"host/a.c": [".venv/bin/python", "x=tools/b.py|y", 3, None],
                   "cwd": ".", "other": "/usr/bin/zstd"}


def test_receipt_paths_hold_for_a_receipt_citing_files_that_exist():
    got = PL.receipt_path_problems('{"a": "tools/s5/present_live.py"}', "{}", [])
    assert got["ok"] is True and got["cited"] == 1 and got["missing"] == []


def test_receipt_paths_fail_on_a_cited_file_that_is_not_there(tmp_path):
    text = '{"spec": "SA2_REPLAY_WORDS=private/generated/s5/live/no-such-run/lifted-words.bin|x"}'
    got = PL.receipt_path_problems(text, "{}", [])
    assert got["ok"] is False
    assert got["missing"] == ["private/generated/s5/live/no-such-run/lifted-words.bin"]
    # A receipt being written in the same step is not missing.
    writing = [PL.ROOT / "private/generated/s5/live/no-such-run/lifted-words.bin"]
    assert PL.receipt_path_problems(text, "{}", writing)["ok"] is True


@pytest.mark.parametrize("where", ["checkpoint", "full"])
def test_receipt_paths_fail_on_an_absolute_path_into_a_checkout(where):
    bad = json.dumps({"x": f"{PL.ROOT}/host/player/dxvk.conf"})
    worktree = json.dumps({"x": "/home/u/repo/.claude/worktrees/wf_1/host/player/dxvk.conf"})
    for text in (bad, worktree):
        got = (PL.receipt_path_problems(text, "{}", []) if where == "checkpoint"
               else PL.receipt_path_problems("{}", text, []))
        assert got["ok"] is False and got["absolute"]


def test_pins_come_from_tools_pins_json_alone():
    pins = json.loads((PL.ROOT / "tools" / "pins.json").read_text())["tools"]
    name = next(iter(pins))
    assert PL.pinned(name) == {"source": "tools/pins.json", **pins[name]}
    assert PL.pinned("no-such-tool") is None


def test_the_code_state_hashes_every_host_and_tools_s5_file():
    got = PL.code_state()
    here = "tools/s5/present_live.py"
    assert got["files_sha256"][here] == hashlib.sha256((PL.ROOT / here).read_bytes()).hexdigest()
    assert all(k.startswith(("host/", "tools/s5/")) for k in got["files_sha256"])
    assert got["commit_names_the_code"] is (got["code_differing_from_commit"] == [])
    assert got["commit"] == PL.PR.git_head()


# ---------------------------------------------------------------- the command

def test_live_command_changes_only_the_two_wall_clock_bounds(tmp_path):
    lp = PL.live_paths(tmp_path / "run")
    p = lp["game"]
    meta = {"build": "present-live"}
    base, spec = PL.PR.command("present-live", p, {}, meta)
    live, spec2 = PL.live_command("present-live", p, {}, meta)
    assert spec2 == spec and len(live) == len(base)
    changed = {i: (a, b) for i, (a, b) in enumerate(zip(base, live)) if a != b}
    assert changed == {base.index("--probe-arg=--timeout") + 1:
                       (base[base.index("--probe-arg=--timeout") + 1], "--probe-arg=0"),
                       base.index("--timeout") + 1: (base[base.index("--timeout") + 1], "3600")}
    # The recorder writes into the pipe, and the relay's copy is what the checks hold.
    assert f"SA2_REC_TRACE={PL.PR.rel(lp['fifo'])}" in spec.split("|")
    assert p["trace"] == lp["copy"] and p["partial"] == lp["fifo"]


def test_live_command_refuses_a_command_it_cannot_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(PL.PR, "command", lambda *a: (["loop_run.py", "--run", "x"], "x"))
    with pytest.raises(SystemExit):
        PL.live_command("present-live", {}, {}, {})


@pytest.mark.parametrize("argv, want", [
    ([".venv/bin/python", "Documentation/scripts/run_all_gates.py"], True),
    (["/home/x/.venv/bin/python3", "/home/x/repo/Documentation/scripts/ship.py", "msg"], True),
    (["timeout", "7200", ".venv/bin/python", "Documentation/scripts/run_all_gates.py"], False),
    (["/bin/bash", "-c", "until ! pgrep -f 'run_all_gates.py'; do sleep 30; done"], False),
    (["/bin/bash", "-c", ".venv/bin/python Documentation/scripts/run_all_gates.py > x"], False),
    ([".venv/bin/python", "tools/s5/present_live.py"], False),
    ([], False),
])
def test_a_gate_pass_is_a_python_running_a_gate_script(argv, want):
    assert PL.is_gate_pass(argv) is want


@pytest.mark.parametrize("argv, want", [
    ([".venv/bin/python", "tools/s5/present_run.py"], True),
    (["/x/.venv/bin/python3", "/x/tree/tools/s5/present_live.py", "--out", "o"], True),
    ([".venv/bin/python", "tools/s5/present_live.py", "--check"], False),
    ([".venv/bin/python", "tools/s5/present_run.py", "--check"], False),
    (["/x/.venv/bin/python", "/x/tools/s4/loop_run.py", "--name", "present-boot.t", "--run", "x"],
     True),
    (["/x/.venv/bin/python", "/x/tools/s4/loop_run.py", "--name", "replay-boot", "--run", "x"],
     False),
    (["/bin/bash", "-c", ".venv/bin/python tools/s5/present_run.py"], False),
    ([], False),
])
def test_a_presentation_run_is_a_python_running_the_game_on_a_present_build(argv, want):
    assert PL.is_presentation_run(argv) is want


AUDIO_SCENES_RUNS = [
    ([".venv/bin/python", "tools/s5/audio_scenes.py", "all", "--replace"], True),
    (["/usr/bin/python3", "/elsewhere/tree/tools/s5/audio_scenes.py", "run", "menu"], True),
    ([".venv/bin/python", "tools/s5/audio_scenes.py", "receipt"], False),
    (["bash", "-c", ".venv/bin/python tools/s5/audio_scenes.py all"], False),
    ([".venv/bin/python", "-m", "pytest", "Documentation/scripts/tests/test_s5_audio_scenes.py"],
     False),
]


@pytest.mark.parametrize("argv, want", AUDIO_SCENES_RUNS)
def test_an_audio_scenes_session_is_a_presentation_run(argv, want):
    """tools/s5/audio_scenes.py `run` and `all` build present-as-* images and run the game and
    the player; between its loop_run children only the tool itself shows, so a live session's
    guard has to see the tool. It reads a session as the tool's own guard does."""
    import audio_scenes as A  # noqa: PLC0415  the tool's own reading of its sessions

    assert PL.is_presentation_run(argv) is want
    assert A.is_audio_scenes_run(argv) is want


def test_a_live_session_sees_an_audio_scenes_run_on_this_machine():
    """The guard as present_live and play_live run it, processes(is_presentation_run), over
    /proc: a process whose argv is an audio-scenes session is listed; one running its
    receipt is not."""
    code = "import time; time.sleep(15)"  # past the 10 s deadline; a killed test frees it soon
    run = subprocess.Popen([sys.executable, "-c", code, "tools/s5/audio_scenes.py", "all"])
    receipt = subprocess.Popen([sys.executable, "-c", code, "tools/s5/audio_scenes.py",
                                "receipt"])
    try:
        deadline = time.monotonic() + 10
        while True:
            busy = PL.processes(PL.is_presentation_run)
            if any(b.startswith(f"{run.pid}: ") for b in busy) or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        argv = " ".join((sys.executable, "-c", code, "tools/s5/audio_scenes.py", "all"))
        assert [b for b in busy if b.startswith(f"{run.pid}: ")] == [f"{run.pid}: {argv[:160]}"]
        assert not [b for b in busy if b.startswith(f"{receipt.pid}: ")]
    finally:
        for proc in (run, receipt):
            proc.kill()
            proc.wait()


def test_a_session_holds_only_when_live_unlocked_and_not_idle():
    assert PL.session_holds(sess()) is True
    for s in (sess(locked="yes"), sess(idle="yes"), sess(live=False), {}):
        assert PL.session_holds(s) is False

# Direct checks of the machine observations used by the live receipt.
def test_game_clock_maps_tick_entries_and_exit_to_monotonic_seconds(monkeypatch, tmp_path):
    words = tmp_path / "words.bin"
    words.write_bytes(b"test")
    info = {"whole": True, "ref_hz": 1_000_000_000,
            "ref_init": 5_000_000_000, "ref_end": 11_000_000_000,
            "clk_init": 12_000_000_000, "clk_end": 30_000_000_000,
            "last_tick": 2, "end_reason": 3}
    monkeypatch.setattr(PL.WL, "info", lambda _: dict(info))
    monkeypatch.setattr(PL.WL, "stats", lambda _: [{"t_enter": 15_000_000_000},
                                                  {"t_enter": 27_000_000_000}])
    got = PL.game_clock(words)
    assert got["first_entry_monotonic_s"] == 5.0
    assert got["exit_monotonic_s"] == 11.0
    assert got["tick_entry_monotonic_s"] == [6.0, 10.0]
    info["whole"] = False
    incomplete = PL.game_clock(words)
    assert incomplete["first_entry_monotonic_s"] is None
    assert incomplete["exit_monotonic_s"] is None
    assert incomplete["tick_entry_monotonic_s"] == []


def desktop_session(monkeypatch, *, properties=None, env=None, no_display=False):
    monkeypatch.setattr(PL.os, "getuid", lambda: 1000)
    for key, value in {"XDG_SESSION_TYPE": "wayland", "XDG_SESSION_ID": "",
                       "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", **(env or {})}.items():
        monkeypatch.setenv(key, value)
    props = {"User": "1000", "Type": "wayland", "Active": "yes", "Remote": "no",
             "Seat": "seat0", "Class": "user", "LockedHint": "no", "IdleHint": "no",
             "Display": "", **(properties or {})}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["loginctl", "show-user"]:
            assert command[2] == "1000"
            output = "Display=\n" if no_display else "Display=own-session\n"
        elif command[:2] == ["loginctl", "show-session"]:
            assert command[2] == "own-session"
            output = "\n".join(f"{k}={v}" for k, v in props.items())
        elif command[:2] == ["gsettings", "get"]:
            output = "uint32 5000\n"
        else:
            pytest.fail(f"unexpected session query: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(PL.subprocess, "run", run)
    return PL.session(), calls


def test_session_selects_the_current_users_display_and_accepts_its_wayland_window(monkeypatch):
    got, calls = desktop_session(monkeypatch)
    assert got["logind"]["session"] == "own-session"
    assert got["live_desktop_session"] is True
    assert calls[0] == ["loginctl", "show-user", "1000", "-p", "Display"]


@pytest.mark.parametrize("properties, env", [
    ({"Active": "no"}, {}), ({"Remote": "yes"}, {}),
    ({"Seat": "seat1"}, {}), ({"Class": "greeter"}, {}),
    ({"User": "2000"}, {}), ({"Type": "tty"}, {}),
    ({}, {"XDG_SESSION_TYPE": "tty"}),
    ({}, {"XDG_SESSION_ID": "another-session"}),
    ({}, {"WAYLAND_DISPLAY": ""}),
    ({"Type": "x11", "Display": ":1"}, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}),
])
def test_session_refuses_a_session_that_does_not_describe_the_players_desktop(monkeypatch, properties, env):
    got, _ = desktop_session(monkeypatch, properties=properties, env=env)
    assert got["live_desktop_session"] is False


def test_session_accepts_the_current_users_matching_x11_display(monkeypatch):
    got, _ = desktop_session(monkeypatch, properties={"Type": "x11", "Display": ":0"},
                            env={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"})
    assert got["live_desktop_session"] is True


def test_session_refuses_a_user_without_a_graphical_display(monkeypatch):
    got, calls = desktop_session(monkeypatch, no_display=True)
    assert got["live_desktop_session"] is False
    assert all(command[:2] != ["loginctl", "show-session"] for command in calls)


@pytest.mark.parametrize("paths, status", [
    ([], "final"), (None, "draft"), (["tools/s4/x.py"], "draft"),
    (["runtime/x.c"], "draft"), (["harness/x.c"], "draft"),
    (["Documentation/scripts/x.py"], "draft"), (["host/x.cpp"], "draft"),
])
def test_checkpoint_status_requires_a_known_clean_code_tree(monkeypatch, tmp_path, paths, status):
    monkeypatch.setattr(PL, "CODE_DIRS", ())
    monkeypatch.setattr(PL, "git_status_paths", lambda: paths)
    monkeypatch.setattr(PL.PR, "git_head", lambda: "committed-code")
    state = PL.code_state()
    assert state["commit_names_the_code"] is (status == "final")
    full = passing_full()
    full["inputs"] = {"code": state}
    got = PL.checkpoint_receipt(full, PL.live_paths(tmp_path / "run"))
    assert got["status"] == status


def test_focus_risk_is_structural_even_when_the_run_and_frame_comparison_fail():
    full = passing_full()
    full["checks"]["run"]["words"]["ok"] = False
    full["checks"]["frames"]["ok"] = False
    got = PL.focus_and_occlusion({}, (sess(), sess()), full["checks"], {}, 1.0, {})
    assert got["determinism_risk_for_replays"] is False
    assert got["measurement"]["lifted_outputs_equal_the_registered_replay"] is False
