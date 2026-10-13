"""Thread-row reuse (runtime/sa2_threads.h SA2_MAX_GUEST_THREADS, sa2_sched_spawn),
held by its unit probe after the 2026-10-02 review of the change.

harness/thread_reuse_probe.c is built with the pinned zig from the production
runtime/sa2_threads.c and runtime/sa2_trap.c, with malloc and free wrapped so
the probe counts the signal stacks. Each test holds a literal expectation and
names the break or defect that moves it:

* **creation order survives reuse (S2):** the review's seeded workload, run with
  a row per thread and again with SA2_THREAD_ROWS small enough that rows are
  reused a couple of hundred times, writes the same trace byte for byte (every
  guest-visible event keyed by creation number, never by row); under
  SA2_THREADS_BREAK=rowindex, which walks rows by index as before reuse, the
  traces differ, and with no reuse the break changes nothing;
* **a trapped worker keeps its row (S1):** its trap note is the evidence the
  run tools read, so its row is never given to a later thread -- nor the row of
  one whose FLS callback trapped after it called ExitThread -- while the rows
  of a worker that called ExitThread and one that returned are;
* **each ended thread frees its host memory (S3):** the signal stack
  sa2_trap_thread_init made and the State sa2_call keeps, once per thread,
  after its last call;
* **the refusals name their cause (N1):** SA2_THREADS_BREAK=noreuse refuses by
  its own name, and the refusal with reuse on says what holds the rows.

Without the pinned zig the build fails rather than skips: a skip would pass
with the order unheld."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
PROBE = ROOT / "harness" / "thread_reuse_probe.c"
RUNTIME = ROOT / "runtime"
# -O2, as tools/s4/link_s4.py compiles the runtime (OPT).
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
ROUNDS = "150"
SETTINGS = ("SA2_THREAD_ROWS", "SA2_THREADS_BREAK", "SA2_POLL_HANDOFF", "SA2_SPIN_YIELDS",
            "SA2_FRAME_US", "SA2_VIRTUAL_BUDGET_MS")


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    """The built probe: a function (args, **settings) -> (its JSON, the trace's bytes)."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("thread-reuse-probe")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    exe = d / "probe"
    built = subprocess.run([str(ZIG), "cc", *CC, str(PROBE), str(RUNTIME / "sa2_threads.c"),
                            str(RUNTIME / "sa2_trap.c"), "-Wl,-wrap=malloc", "-Wl,-wrap=free",
                            "-o", str(exe)], capture_output=True, text=True, env=env)
    assert built.returncode == 0, built.stderr
    clean = {k: v for k, v in os.environ.items() if k not in SETTINGS}
    runs = [0]

    def run(*args: str, **settings) -> tuple[dict, bytes | None]:
        runs[0] += 1
        trace = d / f"trace-{runs[0]}.txt"
        argv = [str(exe), args[0]] + ([str(trace), *args[1:]] if args[0] == "order" else [])
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                           env=dict(clean, **settings))
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout), (trace.read_bytes() if args[0] == "order" else None)
    return run


# (seed, threads the workload creates): every thread fits a row of its own at the default.
WORKLOADS = ((1, 193), (7, 218))


@pytest.mark.parametrize("seed,threads", WORKLOADS)
@pytest.mark.parametrize("rows", [11, 16])
def test_reused_rows_change_nothing_a_thread_can_see(probe, seed, threads, rows):
    """Every event each thread sees -- its stack, clean TLS and FLS, each slice, each
    pulse that wakes a waiter, each tick row with the retired threads' guest calls --
    is the same with rows reused as with a row per thread."""
    whole, whole_trace = probe("order", str(seed), ROUNDS)
    assert (whole["threads"], whole["refused"], whole["rows"], whole["rows_reused"],
            whole["dirty"]) == (threads, 0, threads + 1, 0, 0)
    reused, reused_trace = probe("order", str(seed), ROUNDS, SA2_THREAD_ROWS=str(rows))
    assert (reused["threads"], reused["refused"], reused["rows"], reused["rows_reused"],
            reused["dirty"]) == (threads, 0, rows, threads + 1 - rows, 0)
    assert reused_trace == whole_trace


@pytest.mark.parametrize("seed,threads", WORKLOADS)
def test_walking_rows_by_index_breaks_the_creation_order(probe, seed, threads):
    """SA2_THREADS_BREAK=rowindex orders the hand-off and the pulse by row, as before
    reuse: with rows reused a thread on a low row runs ahead of older ones and the
    trace moves; with a row per thread index and creation order agree and it does
    not. So the comparison above is one that can fail, and the runs' byte-identical
    replays, where no two live rows hold threads out of order in four of five, do
    not carry this claim by themselves (the 2026-10-02 review, S2)."""
    _, whole_trace = probe("order", str(seed), ROUNDS)
    _, broken = probe("order", str(seed), ROUNDS, SA2_THREAD_ROWS="11",
                      SA2_THREADS_BREAK="rowindex")
    assert broken != whole_trace
    _, unreused = probe("order", str(seed), ROUNDS, SA2_THREADS_BREAK="rowindex")
    assert unreused == whole_trace


TRAP_NOTE = "probe: a guest access the runtime refused at 0x00500000"
FLS_NOTE = ("probe: no function for the FLS callback (in the FLS callback 0x00006000(0x00001234) "
            "for index 3, run as the thread ended)")


def test_a_row_whose_thread_trapped_is_never_reused(probe):
    """Rows 1 to 4 hold a worker that trapped, one that called ExitThread, one whose FLS
    callback trapped after it called ExitThread, and one that returned, every handle
    closed. The six workers after them each take row 2, the lowest row a thread may be
    reused from, never row 1 or 3, whose notes stay as the traps left them; the two
    waiters after them take rows 2 and 4, and the next creation is refused, naming what
    holds the rows (the 2026-10-02 review, S1; the FLS case is the fold's own, which
    the review's `trap_status && !exit_called` would have reused)."""
    got, _ = probe("trapkeep", SA2_THREAD_ROWS="5")
    assert got["placed"] == [1, 2, 3, 4, 2, 2, 2, 2, 2, 2, 2, 4, -1]
    assert got["rows"] == [
        {"row": 1, "thread": 1, "trap_status": 2, "exit_code": 0, "note": TRAP_NOTE},
        {"row": 2, "thread": 11, "trap_status": 0, "exit_code": 0, "note": ""},
        {"row": 3, "thread": 3, "trap_status": 3, "exit_code": 7, "note": FLS_NOTE},
        {"row": 4, "thread": 12, "trap_status": 0, "exit_code": 0, "note": ""}]
    assert (got["rows_reused"], got["refused"]) == (8, 1)
    assert got["refused_why"] == ("every thread row holds a thread that is running, whose "
                                  "handle is still open, or that ended in a trap of its own")


def test_the_noreuse_break_refuses_by_its_own_name(probe):
    """Under the break no row is reused, so the fifth creation and every one after it
    is refused, and the refusal says the break did it rather than that the rows were
    held (rows 2 and 4 were finished and closed)."""
    got, _ = probe("trapkeep", SA2_THREAD_ROWS="5", SA2_THREADS_BREAK="noreuse")
    assert got["placed"] == [1, 2, 3, 4, -1, -1, -1, -1, -1, -1]
    assert [(r["thread"], r["trap_status"], r["exit_code"], r["note"]) for r in got["rows"]] == [
        (1, 2, 0, TRAP_NOTE), (2, 1, 7, "probe: ExitThread(7)"), (3, 3, 7, FLS_NOTE),
        (4, 0, 101, "")]
    assert (got["rows_reused"], got["refused"]) == (0, 6)
    assert got["refused_why"] == "SA2_THREADS_BREAK=noreuse: every thread row has been used"


@pytest.mark.parametrize("args,settings,started,finished", [
    (("order", "1", ROUNDS), {"SA2_THREAD_ROWS": "11"}, 193, 191),
    (("order", "1", ROUNDS), {}, 193, 191),
    (("trapkeep",), {"SA2_THREAD_ROWS": "5"}, 12, 10)])
def test_each_ended_thread_frees_its_signal_stack_and_its_state(probe, args, settings, started,
                                                                finished):
    """Every host thread made one 64 KiB signal stack; every one whose guest thread
    ended freed it and its State once, after its last call; only the threads still
    running (the waiters) hold theirs (the 2026-10-02 review, S3)."""
    got, _ = probe(*args, **settings)
    assert (got["started"], got["finished"]) == (started, finished)
    assert got["signal_stacks"] == {"made": started, "freed": finished,
                                    "live": started - finished, "untracked": 0}
    assert (got["state_frees"], got["calls_after_free"]) == (finished, 0)
