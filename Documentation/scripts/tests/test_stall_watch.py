"""The stall watch (runtime/sa2_stall.h), the runtime's SIGURG nudge and SA2_GUEST_HOST_CPUS's lines
(runtime/sa2_threads.c), after the AYN Thor's stall at the SEGA logo (2026-10-17).

harness/stall_watch_probe.c links runtime/sa2_stall.c, runtime/sa2_threads.c and runtime/sa2_trap.c,
the production files, with stand-ins for the rest of the runtime, built with the pinned zig as
tools/s4/link_s4.py builds the runtime. Its `format` mode prints sa2_stall_format's table of a fixed
snapshot, whole and cut short; its `watch` mode has a host thread take guest thread 0's row, cross a
frame boundary and then sleep in write(2) on a full pipe holding the guest token, as the recorder's
Present did on the Thor, while the main thread starts the watch, waits, and sends itself SIGURG.

Held here: the table's text, line for line; the watch's one table on stderr after
SA2_STALL_DUMP_MS without a frame, naming the writer's host thread, the kernel's sleep in pipe_write
and the write it is inside; one table a stall however long it lasts, and one more for a stall after
a frame; the line that says the game waits for the player, and that a paused player or an app in
the background looks like this, when the writer is in the recorder's write of the stream FIFO
(SA2_REC_TRACE) or its read of the pace pipe (SA2_REC_PACE_FD), and only then (the Android app's
backgrounding printed a table each time, and a second after 30 s, with nothing to say it was the
player's pause); no table and no thread without SA2_STALL_DUMP_MS, and a refusal line for a value
that is not a count; the nudge taken by the main thread and passed on to the token's holder, whose
write SA_RESTART restarts rather than cuts short; and SA2_GUEST_HOST_CPUS's lines, said only after
the call and only when the thread does not run where it was asked.

Linux only: the watch's host columns are /proc's.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
PROBE = ROOT / "harness" / "stall_watch_probe.c"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
SETTINGS = ("SA2_STALL_DUMP_MS", "SA2_GUEST_HOST_CPUS", "SA2_THREADS_BREAK", "SA2_REC_TRACE", "SA2_REC_PACE_FD")

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the watch reads Linux's /proc")


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("stall-watch-probe")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    exe = d / "probe"
    built = subprocess.run([str(ZIG), "cc", *CC, str(PROBE), str(RUNTIME / "sa2_stall.c"),
                            str(RUNTIME / "sa2_threads.c"), str(RUNTIME / "sa2_trap.c"), "-o", str(exe)],
                           capture_output=True, text=True, env=env)
    assert built.returncode == 0, built.stderr
    clean = {k: v for k, v in os.environ.items() if k not in SETTINGS}

    def run(*args: str, **settings) -> subprocess.CompletedProcess:
        p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60, env=dict(clean, **settings))
        assert p.returncode == 0, p.stderr
        return p
    return run


TABLE = """\
sa2 stall: no frame for 3.2 s after frame 237 (virtual 3.950012 s); the token names guest thread 0; the guest lock is held; 2 nudges taken
sa2 stall: row host_tid start      state    susp wait             object     deadline_us where                last_caller calls blocks wakes | host: state wchan call cpus (last)
sa2 stall:   0     4242 0x00000000 running     0 not-blocked      0x00000000           0 frame                0x00425da0 123456 300 300 | S pipe_write write(5 game.fifo, 1120 bytes) on 0-7 (last 7)
sa2 stall:   3     4250 0x0085c3a0 blocked     1 event            0x00000108     3966666 WaitForSingleObject  0x0085c4c1 99 12 11 | S futex_wait_queue on 7 (last 6)
sa2 stall: and 1 finished threads' rows
sa2 stall: vfs: a read of "resource/gd_PC/PRS/stg13.prs" (65536 bytes) in progress for 2.5 s on host tid 4242
"""
CUT = """\
sa2 stall: no frame for 3.2 s after frame 237 (virtual 3.950012 s); the token names guest thread 0; the guest lock is held; 2 nudges taken
sa2 stall: row host_tid start      state    susp wait             object     deadline_us where                last_caller calls blocks wakes | host: state wchan call cpus (last)
sa2 stall: (the table was cut short)
"""


PLAYER_LINE = ("sa2 stall: guest thread {thread} (host tid {tid}) is in the recorder's {wait}: the game waits for the "
               "player to take its frames. A player paused or in the background (an Android app out of view, a "
               "window pause-unfocused holds) looks like this, and frames go on when it reads again; a player that "
               "stopped reading looks the same")


def test_the_table_names_each_live_row_its_wait_counts_the_finished_and_names_the_file_read(probe):
    whole, rest = probe("format").stdout.split("--- cut to 420 bytes\n")
    assert whole == TABLE
    cut, size, player = rest.split("--- ")
    assert cut == CUT and size == f"{len(CUT)} bytes\n"
    first, others = TABLE.split("\n", 1)
    line = PLAYER_LINE.format(thread=0, tid=4242, wait="write of the stream FIFO")
    assert player == f"waiting for the player\n{first}\n{line}\n{others}"


def tables(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("sa2 stall: no frame for ")]


@pytest.mark.parametrize("kind, wait, call", [
    ("fifo", "write of the stream FIFO", " write("),
    ("pace", "read of the pace pipe", " read("),
])
def test_a_writer_in_the_recorders_wait_on_the_player_is_said_to_wait_for_the_player(probe, tmp_path, kind, wait,
                                                                                       call):
    """The recorder's write of the stream FIFO (SA2_REC_TRACE's file) or its read of the pace pipe
    (SA2_REC_PACE_FD's descriptor), where a paused player leaves the game: the table's second line
    names the thread and the call, and says a paused player or an app in the background looks so."""
    p = probe("player", kind, "1000", str(tmp_path), SA2_STALL_DUMP_MS="300")
    got = json.loads(p.stdout)
    lines = p.stderr.splitlines()
    assert len(tables(p.stderr)) == 1, p.stderr
    at = lines.index(tables(p.stderr)[0])
    assert lines[at + 1] == PLAYER_LINE.format(thread=0, tid=got["writer_tid"], wait=wait)
    [row] = [line for line in lines if line.startswith("sa2 stall:   0 ")]
    assert call in row.split(" | ")[1], row
    assert (got["wrote"], got["read"]) == ((100, -2) if kind == "fifo" else (-2, 1))


def test_a_stall_elsewhere_is_not_said_to_wait_for_the_player(probe, tmp_path):
    """A write into another pipe, with both settings naming others: no such line."""
    other = tmp_path / "other.fifo"
    os.mkfifo(other)
    p = probe("watch", "1000", SA2_STALL_DUMP_MS="300", SA2_REC_TRACE=str(other), SA2_REC_PACE_FD="99")
    assert len(tables(p.stderr)) == 1 and "waits for the player" not in p.stderr, p.stderr


def test_one_table_a_stall_and_one_more_after_a_frame(probe):
    p = probe("episodes", "1200", SA2_STALL_DUMP_MS="200")
    got = json.loads(p.stdout)
    found = tables(p.stderr)
    assert len(found) == 2, p.stderr
    assert " after frame 1 " in found[0] and " after frame 2 " in found[1]
    assert got["wrote"] == 100 and got["wrote2"] == 100


def test_a_stall_past_30_s_still_has_one_table(probe):
    """The watch once wrote a second table at 30 s (SA2_STALL_LATER_S): an app in the background
    for a minute got two. One table a stall, however long."""
    p = probe("watch", "31000", SA2_STALL_DUMP_MS="500")
    assert len(tables(p.stderr)) == 1, p.stderr


def test_the_watch_says_once_where_the_token_holder_sleeps_and_the_nudge_restarts_its_write(probe):
    p = probe("watch", "1000", SA2_STALL_DUMP_MS="300")
    got = json.loads(p.stdout)
    tables = [line for line in p.stderr.splitlines() if line.startswith("sa2 stall: no frame for ")]
    assert len(tables) == 1, p.stderr
    assert tables[0].startswith("sa2 stall: no frame for 0.") and " after frame 1 " in tables[0]
    assert "the token names guest thread 0; the guest lock is held; 0 nudges taken" in tables[0]
    [row] = [line for line in p.stderr.splitlines() if line.startswith("sa2 stall:   0 ")]
    assert f" {got['writer_tid']} 0x00000000 running " in row
    host = row.split(" | ")[1]
    # The kernel's view: asleep, inside the write of 100 bytes into the pipe (wchan where the
    # kernel names it).
    assert host.startswith("S ") and " write(" in host and " pipe:[" in host and ", 100 bytes)" in host, row
    assert "sa2 stall: vfs: no read or write in progress" in p.stderr
    # The nudge: taken on the main thread, passed on to the writer, whose write was restarted
    # (still asleep on the full pipe until the drain) and then wrote all 100 bytes.
    assert got["nudges"] == 2 and got["wrote_before_drain"] == -2
    assert got["wrote"] == 100 and got["write_errno"] == 0
    assert got["row0_tid"] == got["writer_tid"] and got["current"] == 0 and got["pipe_size"] == 4096


def test_no_watch_without_its_setting_and_a_refusal_for_a_bad_one(probe):
    quiet = probe("watch", "500")
    assert "sa2 stall" not in quiet.stderr and json.loads(quiet.stdout)["wrote"] == 100
    off = probe("watch", "500", SA2_STALL_DUMP_MS="0")
    assert "sa2 stall" not in off.stderr
    bad = probe("watch", "100", SA2_STALL_DUMP_MS="3s")
    assert bad.stderr.splitlines() == [
        "sa2 stall: SA2_STALL_DUMP_MS=3s is not a count of milliseconds from 1 to 3600000; no stall watch"]


def test_the_cpu_line_comes_after_the_call_and_says_when_it_did_not_hold(probe):
    ok = probe("watch", "50", SA2_GUEST_HOST_CPUS="0")
    assert ok.stderr.splitlines() == ["sa2: SA2_GUEST_HOST_CPUS=0: guest threads run on those CPUs"]
    failed = probe("watch", "50", SA2_GUEST_HOST_CPUS="1000").stderr.splitlines()
    assert len(failed) == 2, failed
    assert failed[0].startswith("sa2: SA2_GUEST_HOST_CPUS: guest thread 0 (start 0x00000000, host tid ")
    assert "): sched_setaffinity to CPUs 1000 failed: Invalid argument (errno 22); it runs on CPUs " in failed[0]
    assert failed[1] == ("sa2: SA2_GUEST_HOST_CPUS=1000: guest threads are asked onto those CPUs; guest thread 0's "
                         "call failed (above)")
    # A list naming a CPU this machine does not have: the call holds for the rest, and the line
    # says which CPUs the thread got.
    narrowed = probe("watch", "50", SA2_GUEST_HOST_CPUS="0,1000").stderr.splitlines()
    assert narrowed[0].endswith(" asked for CPUs 0,1000 and runs on CPUs 0"), narrowed
    assert narrowed[1].endswith("guest thread 0 runs on a different set (above)")
