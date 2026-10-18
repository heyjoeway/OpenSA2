"""The stream relay and the player's live reader, as compiled C++ unit tests.

host/launch/test_relay.cpp holds sa2l::FrameParser (host/launch/sa2_relay.cpp) to the parser it
replaced over streams fed in pieces of every size, and the relay on a FIFO opened and sized as the
launcher does, with lead 1: its pace (a writer that writes each frame in one write and then takes
its pace byte, as the recorder does, and a slow reader: the writer may never return from a Present
more than two frames ahead of the reader); a writer that takes no pace bytes waiting on its full
FIFO, whose size must never change meanwhile; the stall watch's one nudge, about a second after a
writer stops part way through a frame, also after a hold that the writer ends between two of the
reader's looks; and the pace pipe's end of file once the player goes. host/player/test_stream.cpp is the reader's own test (host/player/stream.cpp), with the
live reader's reuse of its record's storage and its bodies read in place.

Each is built with the system compiler into the test's temporary folder and run there; the pass
is the binary's own verdict line and its exit status.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CXX = shutil.which("c++") or shutil.which("g++")

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler on PATH")


def _build_and_run(tmp_path: Path, name: str, sources: list[str], flags: list[str], libs: list[str]) -> str:
    exe = tmp_path / name
    cmd = [CXX, "-std=c++17", "-O2", *flags, "-o", str(exe), *[str(ROOT / s) for s in sources], *libs]
    built = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr[-4000:]
    ran = subprocess.run([str(exe), str(tmp_path)], capture_output=True, text=True, timeout=300)
    assert ran.returncode == 0, ran.stdout[-4000:] + ran.stderr[-2000:]
    return ran.stdout


def test_the_relay_parses_every_piece_and_paces_the_writer_two_frames_ahead(tmp_path):
    out = _build_and_run(tmp_path, "test_relay",
                         ["host/launch/test_relay.cpp", "host/launch/sa2_relay.cpp", "host/launch/sa2_launch_posix.cpp"],
                         [f"-I{ROOT / 'host/launch'}"], ["-lpthread"])
    assert "RELAY_PARSER_OK" in out
    pace = [line for line in out.splitlines() if line.startswith("RELAY_PACE_OK")]
    assert pace, out
    ahead = int(pace[0].split("at most ")[1].split()[0])
    assert ahead <= 2, pace[0]
    # The FIFO is never resized under a waiting writer, the stall watch nudges once, a hold the
    # writer ends between two of the reader's looks leaves the watch on, and the pace pipe ends
    # with the player.
    for verdict in ("RELAY_NO_RESIZE_OK", "RELAY_STALL_OK", "RELAY_HOLD_END_OK", "RELAY_GONE_OK"):
        assert any(line.startswith(verdict) for line in out.splitlines()), out


def test_the_live_reader_passes_its_own_checks(tmp_path):
    out = _build_and_run(tmp_path, "test_stream",
                         ["host/player/test_stream.cpp", "host/player/stream.cpp", "host/player/sha256.cpp"],
                         [f"-I{ROOT / 'host'}"], ["-lzstd"])
    last = out.strip().splitlines()[-1]
    assert last.startswith("stream: ") and last.endswith(" 0 failed"), out[-2000:]
