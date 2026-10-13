"""The lifted side of the word log, from the wrapper that writes it to the check
that holds it to the row.

harness/replay/wrap_tick_probe.c is the lifted build's tick hook. With
SA2_REPLAY_WORDS=<path> it logs every word of `.dataa` at every GameLoop return
with the writer the native tick probe runs (harness/replay/word_log_core.h), and
its row keeps the digest columns it computes in the process, which are now the
cross-check the host's recompute from the log is held to (tools/s4/wordlog.py
check_capture with native=False). The log decides what M5 publishes, so it is a
measuring instrument, and every agreement below has a break that must make it
disagree.

The wrapper runs here as it runs in the lifted build. Its source is compiled,
unchanged, for the lifted build's target, against the runtime's own
guest_memory.h, and linked with a driver that plays the runtime and the game: it
maps a guest space with a made-up section at `.dataa`'s address, calls
__wrap_sub_433ee0 once a tick, and its __real_sub_433ee0 (GameLoop's body)
changes words the way a game would -- a named field every tick, bursts, the
first word and the six after the last whole 64-byte line, a run across a line
boundary, a word that goes A to B and back -- and can end the run inside the
body as a trap does (longjmp back to main, which returns) or die there without
exiting. After each return the driver hashes the whole section with
word_log_core.h's own wl_hash, the truth the rebuilt log is held to; before the
first call it hashes the section the keyframe must hold. Before main returns it
takes the guest away, as harness/crt_probe.c's main does (sa2_runtime_shutdown),
so an end record that read guest memory would crash the run. Nothing here is
from the executable.

The breaks: the log keyed a tick off, no end record at exit, a wrong in-process
digest, no write-out after each tick, no flush after each tick, the keyframe
taken after the first body, an end record written after a torn tick, and an end
record written for a run in which GameLoop never returned.

Every run's files are removed when its test ends, and the work directory when
the module ends: a log here is over 30 MB, and /tmp is a shared RAM disk.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import wordlog as W  # noqa: E402

REPLAY = ROOT / "harness" / "replay"
RUNTIME = ROOT / "runtime"
ZIG = W.ZIG
TICKS = 120
TD_BLOCKS = len(W.mask_blocks())            # the tree's mask: the row's digest columns
NAMED = len(W.named_fields())

DRIVER_C = r"""
/* The runtime and the game, as far as the lifted tick hook can tell. */
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "guest_memory.h"
#define WL_FORMAT_ONLY                      /* the format and wl_hash, not the writer */
#include "word_log_core.h"

sa2_guest_t sa2_guest;                      /* the one guest space, as guest_memory.c keeps it */
int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    uint8_t *p = sa2_gp(addr, len);
    if (!p) return SA2_E_RANGE;
    memcpy(out, p, len);
    return SA2_OK;
}

void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory);
unsigned long sa2_replay_gameloop_returns(void);

#define SECTION WL_BASE
#define NW WL_WORDS
#define MAPPED 0x02700000u                  /* the section and every digest block */
static volatile uint32_t *G;

static uint32_t rs;
static uint32_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5; return rs; }
static uint32_t *W32(uint32_t va) { return (uint32_t *)(sa2_guest.base + va); }

static unsigned long T, end_at;
static int end_how;                         /* 0 none, 1 a trap (longjmp to main), 2 death (_exit),
                                               3 a host fault inside the hook's scan */
static sigjmp_buf trap;
#define UNREAD_PAGE 0x00920000u             /* no named field, in no digest block: only the scan reads it */
static void on_fault(int sig) { (void)sig; siglongjmp(trap, 1); }   /* as sa2_trap.c's on_fault */
static uint32_t saved;

/* GameLoop's body. */
void *__real_sub_433ee0(void *state, uint32_t pc, void *memory) {
    (void)pc, (void)memory;
    const unsigned long t = ++T;
    *W32(0x0174B038u) = (uint32_t)t;                                  /* frame_count */
    if (t % 5 == 0) *(uint8_t *)(sa2_guest.base + 0x0174AFF9u) ^= 1;   /* a byte field */
    *W32(0x0174B040u) = (uint32_t)(t / 50) & 7u;                      /* game_mode */
    if (end_how && t == end_at) {
        G[777] = 0xDEADBEEFu;               /* a write of the tick that never returns */
        if (end_how == 1) siglongjmp(trap, 1);
        if (end_how == 3) {                 /* the body returns; the scan then faults on this page */
            mprotect(sa2_guest.base + UNREAD_PAGE, 4096, PROT_NONE);
            return state;
        }
        _exit(0);
    }
    /* The tick before a death changes few words, so its record is smaller than
     * stdio's buffer: only the hook's flush after each tick puts it on disk. */
    const unsigned n = t % 9 == 0 || (end_how == 2 && t + 1 == end_at) ? 0
                     : t == 37 ? 200000 : 1 + rnd() % 400;
    for (unsigned k = 0; k < n; ++k) {
        const uint32_t i = rnd() % NW;
        G[i] = rnd() % 3 == 0 ? G[i] : rnd();                        /* some writes change nothing */
    }
    if (t % 3 == 0) G[NW - 6 + (t / 3) % 6] = rnd();                  /* every tail word */
    G[(NW / 16 - 1) * 16 + t % 16] = rnd();                           /* the last whole line */
    if (t % 11 == 0) G[0] = rnd();                                    /* the first word */
    if (t % 4 == 1) { G[16 * 1000 + 15] = rnd(); G[16 * 1000 + 16] = rnd(); }  /* across a line */
    if (t % 10 == 2) { saved = G[5000]; G[5000] = saved ^ 0x5a5a5a5au; }       /* A to B */
    if (t % 10 == 3) G[5000] = saved;                                 /* and back to A */
    G[16 * 777 + t % 16] = rnd();                                     /* every place in a line */
    return state;
}

static uint64_t hash(void) {                /* the reader's `hashes`: word_log_core.h's wl_hash */
    return wl_hash((const uint32_t *)G, NW);
}

/* argv: dir, ticks, seed, how the run ends (0 after the ticks, 1 a trap, 2 death, 3 a fault in
 * the scan), at which tick */
int main(int argc, char **argv) {
    if (argc != 6 || chdir(argv[1])) return 2;
    const unsigned long ticks = strtoul(argv[2], 0, 10);
    rs = (uint32_t)strtoul(argv[3], 0, 10) | 1u;
    end_how = atoi(argv[4]);
    end_at = strtoul(argv[5], 0, 10);
    void *m = mmap(NULL, MAPPED, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (m == MAP_FAILED) return 3;
    sa2_guest.base = (uint8_t *)m;
    sa2_guest.size = SA2_GUEST_END;
    sa2_guest.ready = 1;
    G = (volatile uint32_t *)(sa2_guest.base + SECTION);
    for (uint32_t i = 0; i < NW; i += 1 + rnd() % 7)
        G[i] = (rnd() & 1) ? rnd() : (rnd() & 0xFFu);
    FILE *hashes = fopen("truth-hashes.csv", "wb");
    fputs("tick,hash\n", hashes);
    fprintf(hashes, "0,0x%016llx\n", (unsigned long long)hash());  /* at GameLoop's first entry */
    fflush(hashes);
    static unsigned long rows;             /* read after a longjmp: not a register */
    signal(SIGSEGV, on_fault);
    if (!sigsetjmp(trap, 1)) {
        for (unsigned long t = 1; t <= ticks; ++t) {
            __wrap_sub_433ee0(0, 0, 0);
            rows = t;
            fprintf(hashes, "%lu,0x%016llx\n", t, (unsigned long long)hash());
            fflush(hashes);                 /* a run that dies keeps its truth */
        }
    }
    fclose(hashes);
    printf("rows=%lu returns=%lu\n", rows, sa2_replay_gameloop_returns());
    fflush(stdout);
    /* As crt_probe.c's main: sa2_runtime_shutdown takes the guest away before main returns
     * and the process exits, so a read of it from an exit handler faults. */
    signal(SIGSEGV, SIG_DFL);
    mprotect(sa2_guest.base, MAPPED, PROT_NONE);
    return 0;
}
"""

LOG_A_TICK_OFF = ("wl_tick(&wlog, (uint32_t)gameloop_returns,",
                  "wl_tick(&wlog, (uint32_t)gameloop_returns + 1u,")
NO_END_AT_EXIT = ("if (words_out && atexit(words_end))", "if (words_out && ((void)words_end, 0))")
WRONG_DIGEST = ("return (const uint32_t *)sa2_gp(va, bytes);",
                "return (const uint32_t *)sa2_gp(va + 4u, bytes);")
NO_DRAIN_PER_TICK = ("        wl_tick(&wlog, (uint32_t)gameloop_returns, t_enter, wl_clock());\n"
                     "        words_drain();\n",
                     "        wl_tick(&wlog, (uint32_t)gameloop_returns, t_enter, wl_clock());\n")
NO_FLUSH_PER_TICK = ("    wl_drained(&wlog);\n    if (fflush(words_out))\n",
                     "    wl_drained(&wlog);\n    if (0)\n")
OPEN = "    if (!words_out && !words_off)\n        words_open();\n"
BODY = "    void *r = __real_sub_433ee0(state, pc, memory);\n"
KEYFRAME_AFTER_THE_BODY = [(OPEN + BODY, BODY + OPEN)]
END_AFTER_A_TORN_TICK = ("    if (words_in_tick) {", "    if (0 && words_in_tick) {")
END_FOR_NO_TICK = ("    if (!wlog.last_tick) {", "    if (0 && !wlog.last_tick) {")


def zig(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(cwd / "zig-cache"),
               ZIG_GLOBAL_CACHE_DIR=str(W.BUILD / "zig-global"))
    return subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-Wall",
                           "-Wextra", "-Werror", *args], cwd=cwd, env=env, capture_output=True,
                          text=True)


class Harness:
    """Builds the hook from a copy of harness/replay (mutated or not) with the
    driver, and runs it; each build cached by its mutation."""

    def __init__(self, work: Path):
        self.work = work
        (work / "driver.c").write_text(DRIVER_C)
        self.built: dict[tuple, Path] = {}
        self.n = 0

    def program(self, mutate: list[tuple[str, str]] | tuple[str, str] | None = None) -> Path:
        muts = [mutate] if isinstance(mutate, tuple) else list(mutate or [])
        key = tuple(muts)
        if key in self.built:
            return self.built[key]
        self.n += 1
        rep = self.work / f"replay{self.n}"
        shutil.copytree(REPLAY, rep, ignore=shutil.ignore_patterns("masks"))
        hook = rep / "wrap_tick_probe.c"
        text = hook.read_text()
        for old, new in muts:
            assert text.count(old) == 1, f"the mutation's anchor is not in the hook once: {old!r}"
            text = text.replace(old, new)
        hook.write_text(text)
        exe = self.work / f"driver{self.n}"
        p = zig(str(self.work / "driver.c"), str(hook), "-o", str(exe), "-I", str(rep),
                "-I", str(RUNTIME), cwd=self.work)
        assert p.returncode == 0, p.stderr
        self.built[key] = exe
        return exe

    def run(self, exe: Path, *, ticks: bool = True, words: bool | str = True, seed: int = 7,
            end_how: int = 0, end_at: int = 0, extra: dict | None = None) -> dict:
        self.n += 1
        d = self.work / f"run{self.n}"
        d.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
        if ticks:
            env["SA2_REPLAY_TICKS"] = str(d / "lifted.csv")
        if words:
            env["SA2_REPLAY_WORDS"] = words if isinstance(words, str) else str(d / "words.bin")
        env.update(extra or {})
        p = subprocess.run([str(exe), str(d), str(TICKS), str(seed), str(end_how), str(end_at)],
                           capture_output=True, text=True, timeout=300, env=env)
        got: dict = {"rc": p.returncode, "stderr": p.stderr, "dir": d,
                     "csv": d / "lifted.csv", "log": d / "words.bin"}
        if end_how != 2:
            assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
            got.update({k: int(v) for k, v in (kv.split("=") for kv in p.stdout.split())})
        return got


@pytest.fixture(scope="module")
def H(tmp_path_factory):
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is missing: {ZIG}")
    work = tmp_path_factory.mktemp("lifted-wordlog")
    yield Harness(work)
    shutil.rmtree(work, ignore_errors=True)


@pytest.fixture
def run(H):
    """H.run, with the run's files removed when the test ends."""
    made: list[Path] = []

    def go(exe: Path, **kw) -> dict:
        got = H.run(exe, **kw)
        made.append(got["dir"])
        return got
    yield go
    for d in made:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def plain(H):
    """The capture alone, as every replay ran until now."""
    got = H.run(H.program(), words=False)
    yield got
    shutil.rmtree(got["dir"], ignore_errors=True)


@pytest.fixture(scope="module")
def good(H):
    """The same run with the word log on."""
    got = H.run(H.program())
    yield got
    shutil.rmtree(got["dir"], ignore_errors=True)


def truth_hashes(run: dict) -> dict[int, str]:
    with (run["dir"] / "truth-hashes.csv").open() as f:
        return {int(r["tick"]): r["hash"] for r in csv.DictReader(f)}


def assert_rebuilt_everywhere(run: dict) -> None:
    """Every tick the driver ran, the keyframe as tick 0, rebuilt word for word.
    The reader's refusal is an error here, not a mismatch."""
    got, want = W.hashes(run["log"]), truth_hashes(run)
    assert sorted(got) == sorted(want)
    bad = [t for t in want if got[t] != want[t]]
    assert not bad, f"{len(bad)} ticks rebuilt wrong, first {bad[0]}"


# ------------------------------------------------------------- what a good run is

def test_the_log_leaves_the_capture_as_it_was(plain, good):
    assert plain["rows"] == good["rows"] == TICKS == good["returns"]
    assert not plain["log"].exists() and good["log"].is_file()
    assert good["csv"].read_bytes() == plain["csv"].read_bytes()
    assert truth_hashes(good) == truth_hashes(plain)


def test_every_tick_of_a_lifted_run_is_rebuilt_from_its_log(good):
    assert len(truth_hashes(good)) == TICKS + 1
    assert_rebuilt_everywhere(good)


def test_the_log_is_the_native_format_ended_at_the_process_exit(good):
    i = W.info(good["log"])
    assert (i["whole"], i["version"], i["ticks"], i["last_tick"], i["end_rule"],
            i["end_reason"], i["tick_limit"], i["overflow_tick"], i["noop_words"]) == (
        True, 2, TICKS, TICKS, 0, "on_exit", 0, 0, 0)
    assert (i["base"], i["size"]) == (0x00917000, 0x01D59598)
    assert i["buffer_bytes"] == W.buffer_bytes_required()
    # Which words are addresses is native's to say; the end-of-run check reads the guest,
    # which is gone when the lifted end record is written.
    assert (i["regions_init"], i["regions_end"], i["regions_init_present"],
            i["regions_end_present"], i["own_blocks"], i["shadow_checked"]) == (0, 0, 0, 0, 0, 0)


def test_the_in_process_digests_equal_the_host_s_recompute_from_the_log(good):
    got = W.check_capture(good["csv"], good["log"], native=False)
    assert got["ok"], got["problems"]
    assert got["named_cells_compared"] == TICKS * NAMED
    assert (got["digest_cells_compared"], got["digest_cells_differing"]) == (TICKS * TD_BLOCKS, 0)
    assert W.capture_csv(good["log"], native=False) == good["csv"].read_text()


def test_the_native_rules_refuse_a_lifted_log(good):
    """A lifted log cannot pass for a native capture: it has no planned end."""
    got = W.check_capture(good["csv"], good["log"])
    assert not got["ok"]
    assert any("no planned end" in p for p in got["problems"]), got["problems"]


def test_a_lifted_log_is_refused_as_evidence_of_pointers(good):
    """It carries no region map, so the pointer test must refuse it rather than
    find no pointer words (snapshot_fields.py's lifted path, U4)."""
    with pytest.raises(W.WordLogError, match="no region map"):
        W.pointer_words(good["log"])


def test_a_run_that_ends_in_a_trap_keeps_every_tick_that_returned(H, run, plain):
    got = run(H.program(), end_how=1, end_at=40)
    assert got["rows"] == got["returns"] == 39
    i = W.info(got["log"])
    assert (i["whole"], i["ticks"], i["end_reason"]) == (True, 39, "on_exit")
    assert_rebuilt_everywhere(got)
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert check["ok"], check["problems"]
    lines = plain["csv"].read_text().splitlines(keepends=True)
    assert got["csv"].read_text() == "".join(lines[:40])      # the header and ticks 1-39


def test_a_run_in_which_gameloop_never_returns_leaves_no_end_record(H, run):
    """A trap inside GameLoop's first call: the capture has its header and no
    row, and a whole log of no tick would pass the lifted check with nothing
    compared, so the log keeps no end record and is refused."""
    got = run(H.program(), end_how=1, end_at=1)
    assert (got["rows"], got["returns"]) == (0, 0)
    assert len(got["csv"].read_text().splitlines()) == 1
    assert "GameLoop never returned, so the log holds no tick" in got["stderr"]
    i = W.info(got["log"])
    assert (i["whole"], i["ticks"], i["last_tick"]) == (False, 0, 0)
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert not check["ok"]
    assert any("does not read whole" in p for p in check["problems"]), check["problems"]


def test_a_run_that_dies_leaves_its_ticks_and_no_end_record(H, run):
    got = run(H.program(), end_how=2, end_at=40)
    assert got["rc"] == 0 and got["log"].is_file()
    i = W.info(got["log"])
    assert (i["whole"], i["ticks"], i["last_tick"]) == (False, 39, 39)
    with pytest.raises(W.WordLogError):
        W.capture_csv(got["log"], native=False)
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert not check["ok"]
    assert any("does not read whole" in p for p in check["problems"]), check["problems"]


def test_a_run_that_ends_inside_the_scan_leaves_no_end_record(H, run):
    """A trap can land inside the hook itself (a host fault, or the runtime's
    wall-clock alarm) while a tick's record is half made. The process still
    exits through main, and the log must not end whole on a torn record."""
    got = run(H.program(), end_how=3, end_at=40)
    assert (got["rows"], got["returns"]) == (39, 40)        # GameLoop returned a 40th time,
    assert len(got["csv"].read_text().splitlines()) == 1 + 40  # its row is out, its scan is not
    assert "the process exited inside a tick's scan" in got["stderr"]
    with got["log"].open("rb") as f:                        # no end record after the torn one
        f.seek(-44, os.SEEK_END)
        assert f.read(4) != b"SA2E"
    i = W.info(got["log"])
    assert (i["whole"], i["ticks"], i["last_tick"]) == (False, 39, 39)
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert not check["ok"]
    assert any("does not read whole" in p for p in check["problems"]), check["problems"]


def test_the_log_is_the_same_without_the_capture(H, run, good):
    got = run(H.program(), ticks=False)
    assert not got["csv"].exists()
    assert W.capture_csv(got["log"], native=False) == good["csv"].read_text()


def test_nothing_is_logged_unless_asked_and_the_snapshots_are_retired(H, run, plain):
    got = run(H.program(), words=False,
              extra={"SA2_REPLAY_SNAPSHOTS": "snapshots.bin", "SA2_REPLAY_SNAP_EVERY": "10",
                     "SA2_REPLAY_WORDS": ""})
    assert sorted(p.name for p in got["dir"].iterdir()) == ["lifted.csv", "truth-hashes.csv"]
    assert got["csv"].read_bytes() == plain["csv"].read_bytes()


def test_a_log_that_cannot_be_kept_says_so_and_leaves_the_run_alone(H, run, plain, tmp_path):
    got = run(H.program(), words=str(tmp_path / "absent" / "words.bin"))
    assert "SA2_REPLAY_WORDS: the file does not open; " in got["stderr"]
    assert not (tmp_path / "absent").exists()
    assert got["csv"].read_bytes() == plain["csv"].read_bytes()


def test_a_log_an_earlier_run_left_cannot_pass_for_this_run_s(H, run, plain, good):
    """The path does not open (here, a read-only file an earlier run left): the
    earlier log would pass the check with this run's CSV, so it must go."""
    stale = H.work / "stale" / "words.bin"
    stale.parent.mkdir()
    shutil.copyfile(good["log"], stale)
    assert W.check_capture(plain["csv"], stale, native=False)["ok"]   # what it would pass for
    stale.chmod(0o444)
    try:
        got = run(H.program(), words=str(stale))
        assert "the file does not open (a file an earlier run left there is removed)" in got["stderr"]
        assert not stale.exists()
        assert got["csv"].read_bytes() == plain["csv"].read_bytes()
    finally:
        shutil.rmtree(stale.parent, ignore_errors=True)


# ------------------------------------------------------------------- the breaks

def test_break_the_log_a_tick_off(H, run):
    got = run(H.program(LOG_A_TICK_OFF))
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert not check["ok"]


def test_break_no_end_record_at_exit(H, run):
    got = run(H.program(NO_END_AT_EXIT))
    assert not W.info(got["log"])["whole"]
    assert not W.check_capture(got["csv"], got["log"], native=False)["ok"]


def test_break_the_in_process_digest(H, run):
    """The row's digests read four bytes off: the named fields still agree and
    the host's recompute does not, which is what the cross-check is for."""
    got = run(H.program(WRONG_DIGEST))
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert not check["ok"]
    assert check["named_cells_differing"] == 0 and check["digest_cells_differing"] > 0


def test_break_no_write_out_after_each_tick(H, run):
    """Kept until the exit, the log is whole when the process exits and empty
    of ticks when it dies: the streaming is what keeps a dead run's ticks."""
    got = run(H.program(NO_DRAIN_PER_TICK), end_how=2, end_at=40)
    assert W.info(got["log"])["ticks"] != 39


def test_break_no_flush_after_each_tick(H, run):
    """Written and not flushed, the small record of the tick before a death
    stays in stdio's buffer and dies with the process."""
    got = run(H.program(NO_FLUSH_PER_TICK), end_how=2, end_at=40)
    i = W.info(got["log"])
    assert (i["whole"], i["ticks"], i["last_tick"]) == (False, 38, 38)


def test_break_the_keyframe_taken_after_the_first_body(H, run):
    """The mutation's own signature: tick 0 is rebuilt as tick 1's state, and
    every tick from 1 is still right."""
    got = run(H.program(KEYFRAME_AFTER_THE_BODY))
    rebuilt, truth = W.hashes(got["log"]), truth_hashes(got)
    assert sorted(rebuilt) == sorted(truth) == list(range(TICKS + 1))
    assert rebuilt[0] != truth[0] and rebuilt[0] == truth[1]
    assert all(rebuilt[t] == truth[t] for t in range(1, TICKS + 1))


def test_break_an_end_record_after_a_torn_tick(H, run):
    """Without the in-tick guard the end record follows half a record, and the
    log must still not pass: the reader's own parse is then all that refuses it."""
    got = run(H.program(END_AFTER_A_TORN_TICK), end_how=3, end_at=40)
    assert "inside a tick's scan" not in got["stderr"]
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert not check["ok"]


def test_break_an_end_record_for_a_run_of_no_tick(H, run):
    """Without the guard the log of a run in which GameLoop never returned ends
    whole, and the lifted check passes it with nothing compared: the vacuous
    pass the guard exists to stop. If the check learns to refuse it too
    (MANIFEST, the request to U1), the second assertion goes and the first stays."""
    got = run(H.program(END_FOR_NO_TICK), end_how=1, end_at=1)
    i = W.info(got["log"])
    assert (i["whole"], i["ticks"], i["end_reason"]) == (True, 0, "on_exit")
    check = W.check_capture(got["csv"], got["log"], native=False)
    assert check["ok"] and check["named_cells_compared"] == 0, check
