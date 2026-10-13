"""The word log, from the probe that writes it to the check that accepts it.

harness/replay/native_tick_probe.c keeps a log of every word of `.dataa` at
every tick (harness/replay/word_log_core.h) and writes it once; tools/s4/
wordlog.py reads it, rebuilds any tick, computes the digest columns the game
no longer computes, and holds a capture's log to its CSV. The log replaces the
game's own digest as what M5 publishes, so it is a measuring instrument: the
agreements below come with breaks that must make them disagree, and the
refusals are tested with logs and CSVs made wrong one way each -- the file cut
short at every kind of place, and, under "the refusals", one case for each
refusal the unit's two reviews found untested and for each rule added since,
held to the refusal's own words. Not every refusal has a case of its own; a
record of no known kind, a run outside the section and a second region map,
for example, are refused by the reader and reached by no test here.

The probe runs here as it runs in the game, as far as it can tell. Its source
is built, unchanged, into a 32-bit Linux program with a stand-in for the Win32
calls it makes (`windows.h` below). The driver plays the Mod Loader and the
game: it maps a made-up section at `.dataa`'s address, calls Init, and on each
tick changes words the way a game would -- the frame counter every tick, a
byte field, bursts, the first word, each of the six words after the last whole
64-byte line in turn, a word of that last whole line every tick, a run across
a line boundary, a word that goes A to B and back -- then calls OnFrame, and
the input probe's end where one is loaded. After each OnFrame it writes what
the log must hold: a hash of the whole section, and the row today's
tick_probe_core.h writes with the tree's digest mask over the true memory, the
reference the rebuilt digests are held to. It counts every Win32 call and the
tick it was made on. Nothing here is from the executable.

The breaks: the scan without its tail loop, the scan without its shadow
update, the log a tick off against its CSV, the file cut short at every kind
of place, the core at the edges of its buffer, and the refusals above.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import wordlog as W  # noqa: E402

REPLAY = ROOT / "harness" / "replay"
PROBE = REPLAY / "native_tick_probe.c"
ZIG = W.ZIG
BASE, SIZE = 0x00917000, 0x01D59598
NWORDS = SIZE // 4
INPUT_END = 'INPUT_END_DLL="input_probe.dll"'
DIFF_WORD = 0x00917000 + 4 * 12345          # the word the diff test's second run moves
RANGE = (0x00917000 + 4 * 12288, 0x00917000 + 4 * 12288 + 4096)   # the range dumped every tick

WINDOWS_H = r"""
/* The Win32 surface native_tick_probe.c uses, for a 32-bit Linux test build. */
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef int BOOL;
typedef uint32_t DWORD;
typedef long LONG;
typedef size_t SIZE_T;
typedef void *HANDLE, *HMODULE, *PVOID;
typedef const void *LPCVOID;
typedef const char *LPCSTR;
typedef intptr_t (*FARPROC)(void);
typedef union { struct { DWORD LowPart; LONG HighPart; } u; long long QuadPart; } LARGE_INTEGER;
typedef struct {
    PVOID BaseAddress, AllocationBase;
    DWORD AllocationProtect;
    SIZE_T RegionSize;
    DWORD State, Protect, Type;
} MEMORY_BASIC_INFORMATION;
#define TRUE 1
#define WINAPI
#define __cdecl
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define GENERIC_WRITE 0x40000000u
#define CREATE_ALWAYS 2u
#define FILE_ATTRIBUTE_NORMAL 0x80u
#define MEM_COMMIT 0x1000u
#define MEM_RESERVE 0x2000u
#define MEM_FREE 0x10000u
#define MEM_TOP_DOWN 0x100000u
#define PAGE_READWRITE 0x04u
void *VirtualAlloc(void *at, SIZE_T size, DWORD type, DWORD protect);
SIZE_T VirtualQuery(LPCVOID at, MEMORY_BASIC_INFORMATION *m, SIZE_T size);
BOOL QueryPerformanceFrequency(LARGE_INTEGER *f);
BOOL QueryPerformanceCounter(LARGE_INTEGER *c);
HANDLE CreateFileA(LPCSTR name, DWORD access, DWORD share, void *security, DWORD how,
                   DWORD flags, HANDLE templ);
BOOL WriteFile(HANDLE f, const void *data, DWORD size, DWORD *written, void *overlapped);
BOOL CloseHandle(HANDLE f);
HMODULE GetModuleHandleA(LPCSTR name);
FARPROC GetProcAddress(HMODULE module, LPCSTR name);
void ExitProcess(unsigned code);
"""

DRIVER_C = r"""
/* The Mod Loader, the game and the input probe, as far as the tick probe can tell. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include "windows.h"

void tp_Init(const char *, const void *, unsigned);
void tp_OnFrame(void);
void tp_OnExit(void);

#define SECTION 0x00917000u
#define NW (0x01D59598u / 4u)
#define MAPPED 0x01D5A000u
static volatile uint32_t *const G = (volatile uint32_t *)SECTION;

/* ---- the truth: the row today's tick_probe_core.h writes over true memory */
static uint32_t tp_read(uint32_t va, unsigned width) {
    uint32_t v = 0;
    for (unsigned i = 0; i < width; ++i) v |= (uint32_t)((volatile uint8_t *)(uintptr_t)va)[i] << (8 * i);
    return v;
}
static const uint32_t *tp_block(uint32_t va, uint32_t bytes) {
    (void)bytes;    /* as native: the first block starts below the section, and the
                     * words the mask selects in it are the section's */
    return (const uint32_t *)(uintptr_t)va;
}
#include "tick_digest.h"
#include "tick_probe_core.h"

/* ---- the stand-in Win32, counting every call and the tick it was made on */
static unsigned long cur_tick, calls_in_run, call_tick_first, call_tick_last, creates,
    create_tick, exits, exit_tick, input_end_calls, end_known_from, planned_end;
static int in_run, input_loaded;
static struct { uint32_t base, size; } regions[16];
static unsigned nregions;

static void counted(void) {
    if (!in_run) return;
    ++calls_in_run;
    if (!call_tick_first) call_tick_first = cur_tick;
    call_tick_last = cur_tick;
}
void *VirtualAlloc(void *at, SIZE_T size, DWORD type, DWORD protect) {
    (void)at, (void)type, (void)protect;
    counted();
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return NULL;
    regions[nregions].base = (uint32_t)(uintptr_t)p, regions[nregions++].size = (uint32_t)size;
    return p;
}
SIZE_T VirtualQuery(LPCVOID at, MEMORY_BASIC_INFORMATION *m, SIZE_T size) {
    (void)size;
    counted();
    const uint32_t a = (uint32_t)(uintptr_t)at;
    uint32_t best = 0xFFFFFFFFu;
    unsigned k = 0;
    for (unsigned i = 0; i < nregions; ++i)
        if (regions[i].base + regions[i].size > a && regions[i].base < best) best = regions[i].base, k = i;
    if (best == 0xFFFFFFFFu) return 0;
    memset(m, 0, sizeof *m);
    if (a >= best) {
        m->BaseAddress = (PVOID)(uintptr_t)best, m->RegionSize = regions[k].size, m->State = MEM_COMMIT;
    } else {
        m->BaseAddress = (PVOID)(uintptr_t)a, m->RegionSize = best - a, m->State = MEM_FREE;
    }
    return sizeof *m;
}
BOOL QueryPerformanceFrequency(LARGE_INTEGER *f) { counted(); f->QuadPart = 10000000; return TRUE; }
BOOL QueryPerformanceCounter(LARGE_INTEGER *c) {
    counted();
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    c->QuadPart = (long long)ts.tv_sec * 10000000 + ts.tv_nsec / 100;
    return TRUE;
}
HANDLE CreateFileA(LPCSTR name, DWORD access, DWORD share, void *security, DWORD how, DWORD flags,
                   HANDLE templ) {
    (void)access, (void)share, (void)security, (void)how, (void)flags, (void)templ;
    counted();
    ++creates;
    create_tick = cur_tick;
    const int fd = open(name, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    return fd < 0 ? INVALID_HANDLE_VALUE : (HANDLE)(intptr_t)(fd + 1);
}
BOOL WriteFile(HANDLE f, const void *data, DWORD size, DWORD *written, void *overlapped) {
    (void)overlapped;
    counted();
    const ssize_t n = write((int)(intptr_t)f - 1, data, size);
    *written = n < 0 ? 0 : (DWORD)n;
    return n >= 0;
}
BOOL CloseHandle(HANDLE f) { counted(); return close((int)(intptr_t)f - 1) == 0; }
static unsigned long input_end(void) {
    ++input_end_calls;
    return cur_tick >= end_known_from ? planned_end : 0;
}
HMODULE GetModuleHandleA(LPCSTR name) {
    counted();
    return input_loaded && !strcmp(name, "input_probe.dll") ? (HMODULE)8 : NULL;
}
FARPROC GetProcAddress(HMODULE m, LPCSTR name) {
    counted();
    if (m == (HMODULE)8 && !strcmp(name, "SA2InputProbeEnd"))
        return (FARPROC)(void (*)(void))input_end;
    return NULL;
}
void ExitProcess(unsigned code) { (void)code; counted(); ++exits; exit_tick = cur_tick; }

/* ---- the game */
static uint32_t rs;
static uint32_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 17; rs ^= rs << 5; return rs; }

static void keyframe_fill(void) {
    for (uint32_t i = 0; i < NW; i += 1 + rnd() % 7)
        G[i] = (rnd() & 1) ? rnd() : (rnd() & 0xFFu);
}

static uint32_t saved, heap_base, own_buffer;
static void game_tick(unsigned long t, int perturb) {
    *(volatile uint32_t *)0x0174B038u = (uint32_t)t;                  /* frame_count */
    if (t % 5 == 0) *(volatile uint8_t *)0x0174AFF9u ^= 1;           /* a byte field */
    *(volatile uint32_t *)0x0174B040u = (uint32_t)(t / 50) & 7u;      /* game_mode */
    const unsigned n = t % 9 == 0 ? 0 : t == 37 ? 200000 : 1 + rnd() % 400;
    for (unsigned k = 0; k < n; ++k) {
        const uint32_t i = rnd() % NW;
        G[i] = rnd() % 3 == 0 ? G[i] : rnd();                        /* some writes change nothing */
    }
    if (t % 3 == 0) G[NW - 6 + (t / 3) % 6] = rnd();                  /* each tail word in turn */
    G[(NW / 16 - 1) * 16 + t % 16] = rnd();                           /* the last whole line */
    if (t % 11 == 0) G[0] = rnd();                                    /* the first word */
    if (t % 4 == 1) { G[16 * 1000 + 15] = rnd(); G[16 * 1000 + 16] = rnd(); }  /* across a line */
    if (t % 10 == 2) { saved = G[5000]; G[5000] = saved ^ 0x5a5a5a5au; }       /* A to B */
    if (t % 10 == 3) G[5000] = saved;                                 /* and back to A */
    G[16 * 777 + t % 16] = rnd();                                     /* every place in a line */
    if (perturb && t >= 30 && t <= 40) G[(DIFF_WORD - SECTION) / 4u] ^= 0x100u;
    if (t == 50) G[(RANGE_LO - SECTION) / 4u + 7] = SECTION + 0x1234u;  /* a pointer into the section */
    if (t == 60) G[(RANGE_LO - SECTION) / 4u + 9] = heap_base + 0x40u;  /* into a heap above 2 GB */
    if (t == 70) G[(RANGE_LO - SECTION) / 4u + 11] = own_buffer + 0x100u; /* data that lands in the log */
}

static uint64_t hash(void) {                /* FNV-1a 64, the standard basis and prime */
    uint64_t h = 14695981039346656037ull;
    for (uint32_t i = 0; i < NW; ++i) h = (h ^ G[i]) * 1099511628211ull;
    return h;
}

/* argv: dir, ticks, seed, input probe loaded (0/1), the tick the input probe
 * learns its end (0: never), its end, perturb (0/1), OnExit after the ticks (0/1),
 * how many words the game changes after its last tick and before OnExit */
int main(int argc, char **argv) {
    if (argc != 10 || chdir(argv[1])) return 2;
    const unsigned long ticks = strtoul(argv[2], 0, 10);
    rs = (uint32_t)strtoul(argv[3], 0, 10) | 1u;
    input_loaded = atoi(argv[4]);
    end_known_from = strtoul(argv[5], 0, 10);
    planned_end = strtoul(argv[6], 0, 10);
    const int perturb = atoi(argv[7]), on_exit = atoi(argv[8]);
    const unsigned late = (unsigned)strtoul(argv[9], 0, 10);
    if (!end_known_from) end_known_from = 0xFFFFFFFFul;
    if (mmap((void *)SECTION, MAPPED, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != (void *)SECTION)
        return 3;
    regions[nregions].base = SECTION, regions[nregions++].size = MAPPED;
    keyframe_fill();
    FILE *truth = fopen("truth.csv", "wb"), *hashes = fopen("truth-hashes.csv", "wb");
    FILE *range = fopen("truth-range.bin", "wb");
    tp_write_header(truth);
    fputs("tick,hash\n", hashes);
    tp_Init("", 0, 0);
    own_buffer = nregions > 2 ? regions[2].base : 0;   /* the probe's second block: the buffer */
    void *heap = mmap(NULL, 0x100000u, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (heap == MAP_FAILED) return 4;
    heap_base = (uint32_t)(uintptr_t)heap;             /* the game's own memory, mapped after Init */
    regions[nregions].base = heap_base, regions[nregions++].size = 0x100000u;
    fprintf(hashes, "0,0x%016llx\n", (unsigned long long)hash());
    fwrite((const void *)(uintptr_t)RANGE_LO, 1, RANGE_BYTES, range);
    in_run = 1;
    unsigned long rows = 0;
    for (unsigned long t = 1; t <= ticks; ++t) {
        cur_tick = t;
        game_tick(t, perturb);
        if (input_loaded && planned_end && t >= end_known_from && t >= planned_end) {
            exit_tick = t;                  /* the input probe, loaded first, ends the run */
            break;
        }
        tp_OnFrame();
        rows = t;
        tp_write_row(truth, t);
        fprintf(hashes, "%lu,0x%016llx\n", t, (unsigned long long)hash());
        fwrite((const void *)(uintptr_t)RANGE_LO, 1, RANGE_BYTES, range);
        if (exits) break;
    }
    if (on_exit && !exits) {
        for (unsigned k = 0; k < late; ++k) G[100000 + 16 * k] ^= 0x80000000u;  /* after the last scan */
        tp_OnExit();
    }
    fclose(truth), fclose(hashes), fclose(range);
    printf("rows=%lu exits=%lu exit_tick=%lu creates=%lu create_tick=%lu calls_in_run=%lu "
           "call_tick_first=%lu call_tick_last=%lu input_end_calls=%lu\n",
           rows, exits, exit_tick, creates, create_tick, calls_in_run, call_tick_first,
           call_tick_last, input_end_calls);
    return 0;
}
"""

TAIL_LOOP = """    /* the tail: the words after the last whole line */
    for (uint32_t i = lines * WL_LINE_WORDS; i < WL_WORDS; ++i)
        if (!wl_word(L, &r, i, game[i]))
            goto full;
"""
SHADOW_UPDATE = "    L->shadow[i] = v;               /* the shadow update */\n"


def zig(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(cwd / "zig-cache"),
               ZIG_GLOBAL_CACHE_DIR=str(cwd / "zig-global"))
    return subprocess.run([str(ZIG), "cc", "-target", "x86-linux-musl", "-O2", "-Wall", "-Wextra",
                           "-Werror", *args], cwd=cwd, env=env, capture_output=True, text=True)


class Harness:
    """Builds the probe from a copy of harness/replay (mutated or not) and the
    driver, and runs them; each build cached by its sources and defines."""

    def __init__(self, work: Path):
        self.work = work
        (work / "inc").mkdir(parents=True)
        (work / "inc" / "windows.h").write_text(WINDOWS_H)
        (work / "driver.c").write_text(DRIVER_C)
        self.built: dict[tuple, Path] = {}
        self.n = 0

    def replay_copy(self, mutate: tuple[str, str] | None) -> Path:
        self.n += 1
        d = self.work / f"replay{self.n}"
        shutil.copytree(REPLAY, d, ignore=shutil.ignore_patterns("masks"))
        if mutate:
            old, new = mutate
            core = d / "word_log_core.h"
            text = core.read_text()
            assert text.count(old) == 1, f"the mutation's anchor is not in word_log_core.h once: {old!r}"
            core.write_text(text.replace(old, new))
        return d

    def program(self, defines: tuple[str, ...] = ("TICK_LIMIT=120",),
                mutate: tuple[str, str] | None = None) -> Path:
        key = (defines, mutate)
        if key in self.built:
            return self.built[key]
        rep = self.replay_copy(mutate)
        self.n += 1
        obj, exe = self.work / f"probe{self.n}.o", self.work / f"driver{self.n}"
        names = ("Init", "OnFrame", "OnExit", "SA2ModInfo")
        p = zig("-c", str(rep / "native_tick_probe.c"), "-o", str(obj), "-I", str(self.work / "inc"),
                "-D__declspec(x)=", *[f"-D{n}=tp_{n}" for n in names],
                *[f"-D{d}" for d in defines], cwd=self.work)
        assert p.returncode == 0, p.stderr
        p = zig(str(self.work / "driver.c"), str(obj), "-o", str(exe), "-I", str(self.work / "inc"),
                "-I", str(REPLAY), f"-DDIFF_WORD={DIFF_WORD:#x}u", f"-DRANGE_LO={RANGE[0]:#x}u",
                f"-DRANGE_BYTES={RANGE[1] - RANGE[0]}u", cwd=self.work)
        assert p.returncode == 0, p.stderr
        self.built[key] = exe
        return exe

    def run(self, exe: Path, *, ticks: int = 120, seed: int = 7, input_loaded: bool = False,
            end_known_from: int = 0, planned_end: int = 0, perturb: bool = False,
            on_exit: bool = False, late_writes: int = 0) -> dict:
        self.n += 1
        d = self.work / f"run{self.n}"
        (d / "mods").mkdir(parents=True)
        p = subprocess.run([str(exe), str(d), str(ticks), str(seed), str(int(input_loaded)),
                            str(end_known_from), str(planned_end), str(int(perturb)),
                            str(int(on_exit)), str(late_writes)],
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        got: dict = {k: int(v) for k, v in (kv.split("=") for kv in p.stdout.split())}
        got["dir"] = d
        got["log"] = d / "mods" / "sa2-word-log.bin"
        got["csv"] = d / "mods" / "sa2-tick-capture.csv"
        return got


@pytest.fixture(scope="module")
def H(tmp_path_factory) -> Harness:
    """The harness, and every run's files, removed when the module is done:
    each run leaves a log of ~35 MB, and pytest's temporary directory is a
    shared RAM disk here."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is missing: {ZIG}")
    h = Harness(tmp_path_factory.mktemp("wordlog"))
    yield h
    shutil.rmtree(h.work, ignore_errors=True)


@pytest.fixture(scope="module")
def scratch(H) -> Path:
    """One directory for the logs the tests below make and read back, each
    written over the last, inside the harness's so it goes with it."""
    d = H.work / "scratch"
    d.mkdir()
    return d


@pytest.fixture(scope="module")
def good(H) -> dict:
    """One run of the probe as the boot capture builds it: TICK_LIMIT 120."""
    return H.run(H.program())


@pytest.fixture(scope="module")
def perturbed(H) -> dict:
    """The same run, with DIFF_WORD toggled on ticks 30 to 40."""
    return H.run(H.program(), perturb=True)


SMALL_BUFFER = 30774680 + 60 + 24 + (16 + 8 * 4096) + 16 + (24 + 16 + 8 * 4096 + 44) + (1 << 20)


@pytest.fixture(scope="module")
def overflowed(H) -> dict:
    """A buffer with room for what Init writes and about a megabyte: the burst
    at tick 37 does not fit."""
    return H.run(H.program(("TICK_LIMIT=120", f"WL_BUFFER_BYTES={SMALL_BUFFER}u")))


def truth_hashes(run: dict) -> dict[int, str]:
    with (run["dir"] / "truth-hashes.csv").open() as f:
        return {int(r["tick"]): r["hash"] for r in csv.DictReader(f)}


def rebuilt_everywhere(run: dict) -> bool:
    """Every tick the driver ran, keyframe included, rebuilt word for word."""
    try:
        got = W.hashes(run["log"])
    except W.WordLogError:
        return False
    want = truth_hashes(run)
    return got == {t: h for t, h in want.items() if t <= max(got)} and max(got) == run["rows"]


# ------------------------------------------------------------- what a good run is

def test_every_tick_of_a_run_is_rebuilt_from_its_log(good):
    assert good["rows"] == 120 and good["exits"] == 1 and good["exit_tick"] == 120
    assert rebuilt_everywhere(good)
    i = W.info(good["log"])
    assert (i["whole"], i["ticks"], i["end_reason"], i["overflow_tick"], i["noop_words"]) == (
        True, 120, "tick_limit", 0, 0)
    assert i["buffer_bytes"] == W.buffer_bytes_required() == 192 << 20
    # the end-of-run check: the rebuild hashes to the probe's shadow, and a
    # plain loop over the whole section finds nothing the scan did not log
    assert (i["shadow_checked"], i["shadow_unscanned"]) == (1, 0)


def test_the_driver_moves_every_word_at_the_scan_s_edges(good):
    """The scan compares whole 64-byte lines, then the six words after the last
    one: the run changes each of those six, and every word of that last whole
    line, so a scan that stops a line or a word short loses a change."""
    moved = W.touched(good["log"])
    last_line = BASE + 4 * (NWORDS // 16 - 1) * 16
    assert all(BASE + 4 * i in moved for i in range(NWORDS - 6, NWORDS))
    assert all(last_line + 4 * k in moved for k in range(16))


def fnv1a64(data: bytes) -> int:
    """The standard 64-bit FNV-1a over little-endian words, written here from
    its definition: offset basis 14695981039346656037, prime 1099511628211."""
    h = 14695981039346656037
    for (w,) in struct.iter_unpack("<I", data):
        h = ((h ^ w) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def test_the_section_hash_is_the_standard_fnv1a_64(good):
    """The reader's per-tick hash and the probe's end-of-run hash are the one
    function, word_log_core.h wl_hash; held here to the definition."""
    want = fnv1a64(W.state(good["log"], 120))
    assert int(W.hashes(good["log"])[120], 16) == want
    log = good["log"].read_bytes()
    verify = [r for r in records(log) if r[0] == "SA2V"]
    assert len(verify) == 1
    assert struct.unpack_from("<IIIQ", log, verify[0][1] + 4) == (120, 0, 0, want)


def test_the_digest_columns_are_today_s_probe_s_over_the_true_memory(good):
    """The reader's capture, named fields and digest columns, is byte for byte
    the row today's tick_probe_core.h writes with the tree's mask over the
    memory the game held: the reference is the code the game ran until now.
    The same with the native probe's rules applied, as the runners call it."""
    truth = (good["dir"] / "truth.csv").read_text()
    assert W.capture_csv(good["log"]) == truth
    assert W.capture_csv(good["log"], native=True) == truth


def test_the_receipt_fields_say_what_the_log_is(good):
    got = W.receipt_fields(good["log"])
    assert got["sha256"] == W.sha256(good["log"])
    assert {k: got[k] for k in ("version", "buffer_bytes", "end_rule", "tick_limit", "ticks",
                                "end_reason", "overflow_tick", "noop_words", "whole", "problem",
                                "own_blocks", "regions_init_present", "regions_end_present",
                                "regions_init_more", "regions_end_more", "shadow_checked",
                                "shadow_unscanned")} == {
        "version": 2, "buffer_bytes": 192 << 20, "end_rule": 1, "tick_limit": 120, "ticks": 120,
        "end_reason": "tick_limit", "overflow_tick": 0, "noop_words": 0, "whole": True,
        "problem": "", "own_blocks": 2, "regions_init_present": 1, "regions_end_present": 1,
        "regions_init_more": 0, "regions_end_more": 0, "shadow_checked": 1,
        "shadow_unscanned": 0}
    assert got["cost"]["ticks"] == 120 and got["cost"]["scan_ms"]["max"] >= 0


def test_the_digests_equal_a_reference_written_from_the_mask_alone(good):
    """And an implementation that shares nothing with the C side: the mask's
    words read from the header text, FNV-1a over the rebuilt section."""
    import word_attribution as WA
    header, rows = W.read_capture_text(W.capture_csv(good["log"]))
    blocks = [c for c in header if c.startswith("d_")]
    words = {c: WA.digest_words(int(c[2:], 16)) for c in blocks}
    moved = 0
    for t in (1, 36, 37, 38, 120):
        section = W.state(good["log"], t)
        for k, c in enumerate(blocks):
            h = 2166136261
            for va in words[c]:
                h = ((h ^ struct.unpack_from("<I", section, va - BASE)[0]) * 16777619) & 0xFFFFFFFF
            assert rows[t][header.index(c) - 1] == f"0x{h:08x}", (t, c)
            moved += rows[t][header.index(c) - 1] != rows[1][header.index(c) - 1]
    assert moved, "the synthetic run never moved a masked word, so this compared constants"


def test_the_capture_check_accepts_a_good_run(good):
    got = W.check_capture(good["csv"], good["log"])
    assert got["ok"], got["problems"]
    assert got["named_cells_compared"] == 120 * len(W.named_fields())
    assert (got["shadow_checked"], got["shadow_unscanned"]) == (1, 0)
    assert got["shift_control_rows_differing"] == {"log_tick_before": 119, "log_tick_after": 119}


def test_the_probe_row_holds_the_named_fields_alone(good):
    header = good["csv"].read_text().splitlines()[0].split(",")
    assert header == ["tick", *[n for n, _, _ in W.named_fields()]]


def test_no_os_call_is_made_while_the_game_runs(good):
    """Every Win32 call after Init is made on the tick the log is written, and
    the file is created once, then."""
    assert good["creates"] == 1 and good["create_tick"] == 120
    assert good["call_tick_first"] == good["call_tick_last"] == 120
    assert good["calls_in_run"] >= 5        # regions, the counter, create, write, close, exit


def test_the_region_maps_hold_the_section_and_the_probe_s_own_blocks(good):
    """The walk covers the whole 32-bit space: the probe's shadow and buffer,
    mapped here where a 32-bit process's mmap puts them, above 2 GB, as Proton's
    large-address-aware game put them in wordlog-u1-boot-a, whose map (walked
    to 0x80000000 then) held neither. The log names the two as its own."""
    maps = W.regions(good["log"])
    assert (BASE, 0x01D5A000) in maps["init"]
    sizes = {n for _, n in maps["init"]}
    assert {SIZE, 192 << 20} <= sizes
    assert any(b >= 0x80000000 for b, _ in maps["init"])
    assert sorted(n for _, n in maps["own"]) == [SIZE, 192 << 20]
    assert {b for b, _ in maps["own"]} <= {b for b, _ in maps["init"]}
    rows = list(csv.DictReader(io.StringIO(W.run("regions", good["log"]).stdout)))
    assert {r["tick"] for r in rows if r["which"] == "end"} == {"120"}


# ------------------------------------------------------ the ends a run can have

def test_a_run_the_input_probe_ends_is_logged_on_its_last_captured_tick(H):
    """The recording's, the replays' and the preflight's end: the input probe,
    loaded first, ends the run at 55; this probe writes at 54 and never ends
    the process itself."""
    got = H.run(H.program((INPUT_END,)), input_loaded=True, end_known_from=50, planned_end=55)
    assert (got["rows"], got["exits"], got["exit_tick"]) == (54, 0, 55)
    assert got["creates"] == 1 and got["create_tick"] == 54
    assert got["call_tick_first"] == got["call_tick_last"] == 54
    assert got["input_end_calls"] == 54        # asked once a tick, never before Init's lookup
    i = W.info(got["log"])
    assert (i["end_reason"], i["ticks"], i["end_rule"]) == ("input_end", 54, 2)
    assert rebuilt_everywhere(got)
    assert W.check_capture(got["csv"], got["log"])["ok"]


def test_a_run_without_the_input_probe_says_so_at_once(H):
    got = H.run(H.program((INPUT_END,)), input_loaded=False, ticks=20)
    assert got["creates"] == 1 and got["calls_in_run"] == 0     # written at Init, before the run
    i = W.info(got["log"])
    assert (i["end_reason"], i["ticks"], i["whole"]) == ("refused", 0, True)
    assert not W.check_capture(got["csv"], got["log"])["ok"]
    with pytest.raises(W.WordLogError, match="refused it where it began"):
        W.capture_csv(got["log"])


def test_a_log_written_at_on_exit_reads_but_is_not_a_capture(H):
    """And the end-of-run check counts the three words the game changed after
    the last scan: the plain loop sees what no tick record holds."""
    got = H.run(H.program(("TICK_LIMIT=0",)), ticks=30, on_exit=True, late_writes=3)
    i = W.info(got["log"])
    assert (i["end_reason"], i["ticks"], i["whole"], i["end_rule"]) == ("on_exit", 30, True, 0)
    assert (i["shadow_checked"], i["shadow_unscanned"]) == (1, 3)
    assert rebuilt_everywhere(got)
    checked = W.check_capture(got["csv"], got["log"])
    problems = checked["problems"]
    assert problems == ["the probe had no planned end (neither TICK_LIMIT nor INPUT_END_DLL), "
                        "so the log was written, if at all, at OnExit"], problems
    assert (checked["shadow_checked"], checked["shadow_unscanned"]) == (1, 3)
    # capture_csv takes it as any log, and refuses it as a native one; so does the command
    assert len(W.capture_csv(got["log"]).splitlines()) == 1 + 30
    with pytest.raises(W.WordLogError, match="no planned end"):
        W.capture_csv(got["log"], native=True)
    out = got["dir"] / "cli.csv"
    assert W.main(["csv", str(got["log"]), "--out", str(out)]) == 0
    assert len(out.read_text().splitlines()) == 1 + 30
    with pytest.raises(W.WordLogError, match="no planned end"):
        W.main(["csv", str(got["log"]), "--native", "--out", str(out)])


def test_an_overflow_ends_the_log_whole_and_refuses_the_capture(overflowed):
    """The burst at tick 37 does not fit, and the log keeps ticks 1 to 36,
    whole, without the end-of-run check (the dropped tick had set the shadow)."""
    got = overflowed
    i = W.info(got["log"])
    assert (i["whole"], i["overflow_tick"], i["ticks"], i["shadow_checked"]) == (True, 37, 36, 0)
    assert W.hashes(got["log"]) == {t: h for t, h in truth_hashes(got).items() if t <= 36}
    checked = W.check_capture(got["csv"], got["log"])
    problems = checked["problems"]
    assert (checked["shadow_checked"], checked["shadow_unscanned"]) == (0, 0)
    assert any("overflowed its buffer at tick 37" in p for p in problems), problems
    assert any("not the one size" in p for p in problems), problems
    assert not any("end-of-run check" in p for p in problems), problems
    with pytest.raises(W.WordLogError, match="overflowed its buffer at tick 37"):
        W.capture_csv(got["log"])
    assert len(W.capture_csv(got["log"], allow_overflow=True).splitlines()) == 1 + 36
    with pytest.raises(W.WordLogError, match="not the one size"):
        W.capture_csv(got["log"], allow_overflow=True, native=True)


# ------------------------------------------------------------------- the breaks

def test_break_the_scan_without_its_tail_loop(H):
    """The last six words of the section follow the last whole 64-byte line;
    without the tail loop their changes never reach the log."""
    got = H.run(H.program(mutate=(TAIL_LOOP, "")))
    assert not rebuilt_everywhere(got)


def test_break_the_scan_without_its_shadow_update(H):
    """A word that goes A to B and back to A is back where the stale shadow
    says it is, so the return is never logged; and every other change is
    logged again on every later tick, which the reader counts."""
    got = H.run(H.program(("TICK_LIMIT=60",), mutate=(SHADOW_UPDATE, "")), ticks=60)
    assert W.info(got["log"])["ticks"] == 60
    assert not rebuilt_everywhere(got)
    assert W.info(got["log"])["noop_words"] > 0
    assert not W.check_capture(got["csv"], got["log"], native=False)["ok"]
    with pytest.raises(W.WordLogError, match="already held"):
        W.capture_csv(got["log"])


def records(log: bytes) -> list[tuple[str, int, int]]:
    """A version-2 log's records: (kind, start, end)."""
    out, p = [], 60
    while p < len(log):
        kind = log[p:p + 4].decode()
        if kind == "SA2O":
            end = p + 8 + 8 * struct.unpack_from("<I", log, p + 4)[0]
        elif kind == "SA2R":
            end = p + 16 + 8 * struct.unpack_from("<I", log, p + 8)[0]
        elif kind == "SA2K":
            end = p + 16 + struct.unpack_from("<I", log, p + 12)[0]
        elif kind == "SA2D":
            q = p + 8
            while struct.unpack_from("<I", log, q)[0] != 0xFFFFFFFF:
                q += 8 + 4 * struct.unpack_from("<I", log, q + 4)[0]
            end = q + 4 + 32
        elif kind == "SA2V":
            end = p + 24
        elif kind == "SA2E":
            end = p + 44
        else:
            raise AssertionError(f"no record at {p}")
        out.append((kind, p, end))
        p = end
    return out


def test_break_the_log_a_tick_off_against_its_csv(H, good, scratch):
    """The CSV's rows 2 to 120 numbered 1 to 119, against the log without its
    last tick: both whole, both 119 ticks, and a tick apart."""
    log = good["log"].read_bytes()
    recs = records(log)
    last = [r for r in recs if r[0] == "SA2D"][-1]
    end_regions = [r for r in recs if r[0] == "SA2R"][-1]
    body = log[:last[1]] + log[end_regions[1]:end_regions[2]]
    end = bytearray(log[recs[-1][1]:recs[-1][2]])
    struct.pack_into("<I", end, 4, 119)
    struct.pack_into("<Q", end, 36, len(body))
    (scratch / "shifted.bin").write_bytes(body + bytes(end))
    lines = good["csv"].read_text().splitlines()
    shifted = [lines[0]] + [f"{k}," + row.split(",", 1)[1] for k, row in enumerate(lines[2:], 1)]
    (scratch / "shifted.csv").write_text("\n".join(shifted) + "\n")
    i = W.info(scratch / "shifted.bin")
    assert (i["whole"], i["ticks"]) == (True, 119)
    got = W.check_capture(scratch / "shifted.csv", scratch / "shifted.bin", native=False)
    assert any(p.startswith("frame_count differs from the log's bytes on 119 ticks")
               for p in got["problems"]), got["problems"]
    # and the same pair unshifted passes, so the failure is the shift's
    (scratch / "unshifted.csv").write_text("\n".join(lines[:120]) + "\n")
    assert W.check_capture(scratch / "unshifted.csv", scratch / "shifted.bin", native=False)["ok"]


CUT_BEFORE_THE_TICKS = {
    "header": "is cut short in its header",
    "own blocks": "is cut short in its own-block record, or names more than 8",
    "init regions": "is cut short in its region map at Init",
    "keyframe": "is cut short in its keyframe"}
CUT_AFTER_THE_KEYFRAME = {
    "after the keyframe": "the log ends without its end record",
    "a tick": "a tick record is cut short",
    "before the end-of-run check": "the log ends without its end record",
    "the end-of-run check": "the end-of-run check is cut short",
    "before the end regions": "the log ends without its end record",
    "before the end record": "the log ends without its end record",
    "the end record": "the end record is cut short",
    "the last byte": "the end record is cut short"}


@pytest.mark.parametrize("where", ["header", "own blocks", "init regions", "keyframe",
                                   "after the keyframe", "a tick", "before the end-of-run check",
                                   "the end-of-run check", "before the end regions",
                                   "before the end record", "the end record", "the last byte"])
def test_break_the_file_cut_short(good, scratch, where):
    log = good["log"].read_bytes()
    recs = records(log)
    kinds = [r[0] for r in recs]
    assert kinds[:3] == ["SA2O", "SA2R", "SA2K"] and kinds[-3:] == ["SA2V", "SA2R", "SA2E"]
    ticks = [r for r in recs if r[0] == "SA2D"]
    by = {"header": 30, "own blocks": recs[0][1] + 12, "init regions": recs[1][1] + 20,
          "keyframe": recs[2][1] + 1000, "after the keyframe": recs[2][2],
          "a tick": ticks[40][1] + 12, "before the end-of-run check": recs[-3][1],
          "the end-of-run check": recs[-3][1] + 10, "before the end regions": recs[-2][1],
          "before the end record": recs[-1][1], "the end record": recs[-1][1] + 20,
          "the last byte": len(log) - 1}
    cut = scratch / "cut.bin"
    cut.write_bytes(log[:by[where]])
    if where in CUT_BEFORE_THE_TICKS:           # the reader stops at once (exit 2)
        with pytest.raises(W.WordLogError, match=re.escape(CUT_BEFORE_THE_TICKS[where])):
            W.info(cut)
    else:                                       # it reads to the cut and says where it stopped
        assert W.info(cut)["problem"] == CUT_AFTER_THE_KEYFRAME[where]
    got = W.check_capture(good["csv"], cut)
    assert not got["ok"] and got["problems"], got
    with pytest.raises(W.WordLogError):
        W.capture_csv(cut)
    with pytest.raises(W.WordLogError):     # every mode but info and stats refuses a part
        W.hashes(cut)


def test_bytes_after_the_end_record_are_refused(good, scratch):
    (scratch / "long.bin").write_bytes(good["log"].read_bytes() + b"\0" * 4)
    assert "bytes follow the end record" in W.info(scratch / "long.bin")["problem"]


# ------------------------------------------------------- changes, words, diffs

def truth_range(run: dict) -> list[list[int]]:
    """The dumped range at Init (row 0) and after every tick."""
    data = (run["dir"] / "truth-range.bin").read_bytes()
    n = (RANGE[1] - RANGE[0]) // 4
    return [list(struct.unpack_from(f"<{n}I", data, k * 4 * n)) for k in range(len(data) // (4 * n))]


def test_changes_are_every_change_in_the_range(good):
    want = []
    rows = truth_range(good)
    for t in range(1, len(rows)):
        for j, (old, new) in enumerate(zip(rows[t - 1], rows[t])):
            if old != new:
                want.append((t, RANGE[0] + 4 * j, old, new))
    got = [c for c in W.changes(good["log"], *RANGE) if c[0] > 0]
    assert got == want and want


def test_diff_names_every_word_two_runs_disagree_on(good, perturbed):
    other = perturbed
    a, b = truth_range(good), truth_range(other)
    want = {}
    for t in range(1, len(a)):
        for j in range(len(a[t])):
            if a[t][j] != b[t][j]:
                w = want.setdefault(RANGE[0] + 4 * j, {"first_tick": t, "a": a[t][j], "b": b[t][j],
                                                       "ticks_differing": 0})
                w["last_tick"] = t
                w["ticks_differing"] += 1
    rows, meta = W.diff(good["log"], other["log"], *RANGE)
    assert meta["compared_through"] == 120
    assert {r["va"]: {k: r[k] for k in ("first_tick", "last_tick", "ticks_differing", "a", "b")}
            for r in rows} == want
    assert DIFF_WORD in want and want[DIFF_WORD]["first_tick"] == 30
    # the toggle on ticks 30 to 40 makes it differ on 30 and agree again on 31
    rows, _ = W.diff(good["log"], other["log"], *RANGE, ticks=[29, 30, 31])
    assert [(r["va"], r["first_tick"], r["ticks_differing"]) for r in rows] == [(DIFF_WORD, 30, 1)]
    whole, _ = W.diff(good["log"], other["log"])
    assert [r["va"] for r in whole] == sorted(want)


def test_touched_counts_every_tick_a_word_changed_on(good):
    rows = truth_range(good)
    want: dict[int, int] = {}
    for t in range(1, len(rows)):
        for j, (old, new) in enumerate(zip(rows[t - 1], rows[t])):
            if old != new:
                want[RANGE[0] + 4 * j] = want.get(RANGE[0] + 4 * j, 0) + 1
    got = {va: n for va, n in W.touched(good["log"]).items() if RANGE[0] <= va < RANGE[1]}
    assert got == want and want


def test_pointer_data_words_are_the_flagged_words_that_hold_data_too(good):
    """A word the pointer test flags for one value may hold data at other ticks
    (a value in committed memory is not always an address; the review of the G4
    round-one fold, Reviewer B's finding 1): the reader names each such word and
    the first tick it holds a value neither zero nor an address by the same test,
    the keyframe as tick 0."""
    maps = W.regions(good["log"])
    own = [(b, b + ((n + 0xFFF) & ~0xFFF)) for b, n in maps["init"] if n in (SIZE, 192 << 20)]
    spans = []
    for lo, n in maps["init"] + maps["end"]:
        pieces = [(lo, lo + n)]
        for olo, ohi in own:
            pieces = [p for a, b in pieces
                      for p in ([(a, b)] if b <= olo or a >= ohi else
                                [q for q in ((a, olo), (ohi, b)) if q[0] < q[1]])]
        spans += pieces

    def addr(v: int) -> bool:
        return v >= 0x10000 and any(a <= v < b for a, b in spans)

    flagged, data = set(), {}
    for t, row in enumerate(truth_range(good)):
        for j, v in enumerate(row):
            va = RANGE[0] + 4 * j
            if addr(v):
                flagged.add(va)
            elif v and va not in data:
                data[va] = t
    want = {va: t for va, t in data.items() if va in flagged}
    got = {va: t for va, t in W.pointer_data_words(good["log"]).items()
           if RANGE[0] <= va < RANGE[1]}
    assert got == want and want                     # the driver's log has such words
    assert set(got) <= set(W.pointer_words(good["log"]))
    # one reader pass gives both: the scan's words are pointer_words', each with its data tick
    scan = W.pointer_scan(good["log"])
    assert {va: (t, v) for va, (t, v, _d) in scan.items()} == W.pointer_words(good["log"])
    assert {va: d for va, (_t, _v, d) in scan.items() if d is not None} == \
        W.pointer_data_words(good["log"])


def test_pointer_words_are_snapshot_fields_test_over_every_value(good):
    """A word that ever holds a value inside a region the log's own maps list
    (and not below 0x10000), with the first tick it does; the driver plants
    one at tick 50."""
    maps = W.regions(good["log"])
    # the probe's blocks, found by what the stand-in allocated, not by what the
    # reader says the log names, so a reader that forgets them fails here too
    own = [(b, b + ((n + 0xFFF) & ~0xFFF)) for b, n in maps["init"] if n in (SIZE, 192 << 20)]
    assert len(own) == 2

    def cut(lo: int, hi: int) -> list[tuple[int, int]]:
        pieces = [(lo, hi)]
        for olo, ohi in own:
            pieces = [p for a, b in pieces
                      for p in ([(a, b)] if b <= olo or a >= ohi else
                                [q for q in ((a, olo), (ohi, b)) if q[0] < q[1]])]
        return pieces

    spans = [(a, b - a) for lo, n in maps["init"] + maps["end"] for a, b in cut(lo, lo + n)]
    want: dict[int, tuple[int, int]] = {}
    for t, row in enumerate(truth_range(good)):
        for j, v in enumerate(row):
            va = RANGE[0] + 4 * j
            if va not in want and v >= 0x10000 and any(b <= v < b + n for b, n in spans):
                want[va] = (t, v)
    got = {va: x for va, x in W.pointer_words(good["log"]).items() if RANGE[0] <= va < RANGE[1]}
    assert got == want
    assert want.get(RANGE[0] + 28, (None, None))[1] == BASE + 0x1234
    assert want.get(RANGE[0] + 36, (None, None))[0] == 60          # the heap above 2 GB
    assert not any(olo <= v < ohi for _, v in got.values() for olo, ohi in own)
    # and the view a walk that stopped at 2 GB had: only the regions below it
    low: dict[int, tuple[int, int]] = {}
    for t, row in enumerate(truth_range(good)):
        for j, v in enumerate(row):
            va = RANGE[0] + 4 * j
            if va not in low and v >= 0x10000 and any(b <= v < b + n for b, n in spans
                                                      if b < 0x80000000):
                low[va] = (t, v)
    got = {va: x for va, x in W.pointer_words(good["log"], below=0x80000000).items()
           if RANGE[0] <= va < RANGE[1]}
    assert got == low and len(low) < len(want)
    # and a span cut out of every region as the probe's blocks are (snapshot_fields.py
    # cuts the executable's image; the G4 interrogation, B1): here the region the
    # planted value at tick 50 lies in, so that word is flagged only for another value
    # outside the span, or not at all
    planted = BASE + 0x1234
    hit = next((b, b + n) for b, n in spans if b <= planted < b + n)
    outside: dict[int, tuple[int, int]] = {}
    for t, row in enumerate(truth_range(good)):
        for j, v in enumerate(row):
            va = RANGE[0] + 4 * j
            if va not in outside and v >= 0x10000 and not hit[0] <= v < hit[1] \
                    and any(b <= v < b + n for b, n in spans):
                outside[va] = (t, v)
    got = {va: x for va, x in W.pointer_words(good["log"], cut=hit).items()
           if RANGE[0] <= va < RANGE[1]}
    assert got == outside and RANGE[0] + 28 not in outside
    assert not any(hit[0] <= v < hit[1] for _, v in got.values())
    got = {va: x for va, x in W.pointer_words(good["log"], below=0x80000000, cut=hit).items()
           if RANGE[0] <= va < RANGE[1]}
    assert got == {va: x for va, x in outside.items()
                   if any(b <= x[1] < b + n for b, n in spans if b < 0x80000000)}
    with pytest.raises(ValueError):
        W.pointer_words(good["log"], cut=(hit[1], hit[0]))


def test_state_is_the_section_at_that_tick(good):
    rows = truth_range(good)
    for t in (0, 1, 37, 120):
        s = W.state(good["log"], t)
        assert len(s) == SIZE
        n = (RANGE[1] - RANGE[0]) // 4
        assert list(struct.unpack_from(f"<{n}I", s, RANGE[0] - BASE)) == rows[t]


def test_word_attribution_reads_two_logs(good, perturbed):
    import word_attribution as WA
    other = perturbed
    diff = WA.differing_logs(good["log"], other["log"], [DIFF_WORD, DIFF_WORD + 4], None)
    assert list(diff) == [DIFF_WORD] and diff[DIFF_WORD]["first_tick"] == 30
    assert WA.load_kind(good["log"]) == "log"
    assert WA.differing_logs(good["log"], other["log"], [DIFF_WORD], [29]) == {}


STREAM_C = r"""
/* Two writers over one changing section: one keeps the log and writes it at the
 * end, as the native probe does; one writes each record out as it is made and
 * drains the buffer, as a run that can end without a last call must. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
static const uint32_t *wl_section(void);
static uint64_t wl_clock(void);
#include "word_log_core.h"
static uint32_t mem[WL_WORDS], shadow_a[WL_WORDS], shadow_b[WL_WORDS];
static uint64_t now;
static const uint32_t *wl_section(void) { return mem; }
static uint64_t wl_clock(void) { return ++now; }
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    static uint8_t buf_a[48u << 20], buf_b[48u << 20];
    WlLog A, B;
    WlStart s = {WL_RULE_TICK_LIMIT, 20, 10000000, 1, 1};
    uint32_t r = 12345;
    for (uint32_t i = 0; i < WL_WORDS; i += 3) mem[i] = i * 2654435761u;
    if (!wl_begin(&A, buf_a, sizeof buf_a, shadow_a, &s) || !wl_begin(&B, buf_b, sizeof buf_b, shadow_b, &s)) return 3;
    FILE *fb = fopen(argv[2], "wb");
    wl_keyframe(&A), wl_keyframe(&B);
    fwrite(B.buf, 1, B.len, fb), wl_drained(&B);
    for (uint32_t t = 1; t <= 20; ++t) {
        for (unsigned k = 0; k < 500 * t; ++k) { r ^= r << 13; r ^= r >> 17; r ^= r << 5; mem[r % WL_WORDS] = r; }
        mem[WL_WORDS - 1] = t;
        const uint64_t e = now;
        wl_tick(&A, t, e, e), now = e, wl_tick(&B, t, e, e);
        fwrite(B.buf, 1, B.len, fb), wl_drained(&B);
    }
    wl_close(&A), wl_close(&B);
    wl_end(&A, WL_END_TICK_LIMIT, 2, now), wl_end(&B, WL_END_TICK_LIMIT, 2, now);
    fwrite(B.buf, 1, B.len, fb), fclose(fb);
    FILE *fa = fopen(argv[1], "wb");
    fwrite(A.buf, 1, A.len, fa), fclose(fa);
    return 0;
}
"""


def test_a_streamed_log_is_the_buffered_log_byte_for_byte(H):
    """The lifted side may have to stream (its run can end in a trap); the
    format must not care which, or the two sides' logs would differ for a
    reason that is not the game's."""
    src = H.work / "stream.c"
    src.write_text(STREAM_C)
    exe = H.work / "stream"
    p = zig(str(src), "-o", str(exe), "-I", str(REPLAY), cwd=H.work)
    assert p.returncode == 0, p.stderr
    a, b = H.work / "buffered.bin", H.work / "streamed.bin"
    subprocess.run([str(exe), str(a), str(b)], check=True, timeout=120)
    assert a.read_bytes() == b.read_bytes()
    i = W.info(b)
    assert (i["whole"], i["ticks"], i["noop_words"]) == (True, 20, 0)


# ------------------------------------------------ the probe as the runner builds it

def test_the_probe_builds_as_the_runner_builds_it(H, monkeypatch):
    import run_oracle_probe as RP
    monkeypatch.setattr(RP, "OUT", H.work / "runner")      # its zig caches go with the harness
    for defines in (["TICK_LIMIT=1200"], [INPUT_END]):
        dll, _ = RP.compile_probe(PROBE, "tick_probe", defines)
        assert dll.is_file()


def test_the_two_ends_cannot_both_be_given(H):
    p = zig("-c", str(PROBE), "-o", str(H.work / "both.o"), "-I", str(H.work / "inc"),
            "-D__declspec(x)=", "-DTICK_LIMIT=5", f"-D{INPUT_END}", cwd=H.work)
    assert p.returncode and "give one" in p.stderr


@pytest.mark.parametrize("prefix,why", [
    ("", "include a digest mask"),
    ('#define TP_NO_DIGEST 1\n#include "tick_digest.h"\n', "has the digest columns or it does not"),
])
def test_the_row_says_which_row_it_is(H, prefix, why):
    src = H.work / "row.c"
    src.write_text("#include <stdint.h>\nstatic uint32_t tp_read(uint32_t v, unsigned w) "
                   "{ return v + w; }\n" + prefix + '#include "tick_probe_core.h"\n'
                   "int main(void) { tp_write_header(stdout); return 0; }\n")
    p = zig("-c", str(src), "-o", str(H.work / "row.o"), "-I", str(REPLAY), cwd=H.work)
    assert p.returncode and why in p.stderr


def test_the_native_probe_does_not_reach_the_mask():
    """The mask is the host's input now: a new mask is not a new native
    configuration. tick_replay.probe_headers is how a capture is tied to what
    its probe includes."""
    import tick_replay as T
    got = T.probe_headers(PROBE)
    assert "tick_digest.h" not in got
    assert {"tick_probe_core.h", "tick_fields.h", "word_log_core.h"} <= set(got)


# ------------------------------------------------------ the core at its edges

EDGE_C = r"""
/* The core at the edges of its buffer, with everything after the buffer's end
 * painted. LEAST is counted here from the format, not from word_log_core.h's
 * own sum: what Init writes at most (the header, the own blocks, a whole
 * region map, the keyframe) and the end reserve (the end-of-run check, a whole
 * region map, the end record). */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static const uint32_t *wl_section(void);
static uint64_t wl_clock(void);
#include "word_log_core.h"
static uint32_t mem[WL_WORDS], shadow[WL_WORDS];
static const uint32_t *wl_section(void) { return mem; }
static uint64_t wl_clock(void) { return 7; }
#define MAP (16u + 8u * 4096u)
#define LEAST ((size_t)60u + 24u + MAP + 16u + 0x01D59598u + 24u + MAP + 44u)
static uint8_t buf[LEAST + 65536];
static const WlStart start = {WL_RULE_TICK_LIMIT, 1, 10000000, 1, 1};

static int painted_from(size_t from) {
    for (size_t k = from; k < sizeof buf; ++k)
        if (buf[k] != 0xA5) return 0;
    return 1;
}
static void whole_map(WlLog *L, uint32_t tick) {      /* more regions than a map holds */
    wl_regions_open(L, tick);
    for (uint32_t k = 0; k < 4097u; ++k) wl_region(L, 0x10000u * k, 0x1000u);
    wl_regions_close(L);
}
/* One tick of n contiguous changed words after a bare Init: it must be dropped
 * whole, the log must keep to its cap, and the end records to the buffer. */
static int dropped(uint32_t n) {
    WlLog L;
    memset(mem, 0, sizeof mem);
    memset(buf, 0xA5, sizeof buf);
    if (!wl_begin(&L, buf, LEAST, shadow, &start)) return 0;
    wl_keyframe(&L);
    const size_t at = L.len;
    for (uint32_t i = 0; i < n; ++i) mem[1000 + i] = i + 1;
    if (wl_tick(&L, 1, 0, 0) || L.overflow_tick != 1 || L.len != at || !painted_from(L.cap))
        return 0;
    wl_close(&L), wl_verify(&L), whole_map(&L, 1), wl_end(&L, WL_END_TICK_LIMIT, 2, 7);
    return L.len <= LEAST && painted_from(LEAST);
}
int main(void) {
    WlLog L;
    memset(buf, 0xA5, sizeof buf);
    /* 1: wl_begin takes no less than LEAST, and at LEAST everything Init and
     * the end can write fits */
    if (wl_begin(&L, buf, LEAST - 1, shadow, &start)) return 1;
    if (!wl_begin(&L, buf, LEAST, shadow, &start)) return 2;
    wl_own(&L, shadow, WL_SIZE, buf, (uint32_t)LEAST);
    whole_map(&L, 0);
    wl_keyframe(&L);
    if (L.len != L.cap) return 3;
    wl_tick(&L, 1, 0, 0);
    wl_close(&L), wl_verify(&L), whole_map(&L, 1), wl_end(&L, WL_END_TICK_LIMIT, 2, 7);
    if (L.overflow_tick != 1 || L.len > LEAST || !painted_from(LEAST)) return 4;
    /* 2: a record whose words fit and whose trailer does not */
    if (!wl_begin(&L, buf, LEAST, shadow, &start)) return 5;
    const size_t room = L.cap - (L.len + 16u + WL_SIZE);
    const uint32_t fit = (uint32_t)((room - 16u) / 4u);
    if (16u + 4u * (size_t)fit > room || 16u + 4u * (size_t)fit + WL_TICK_TRAILER_BYTES <= room)
        return 6;
    if (!dropped(fit)) return 7;
    /* 3: one run that alone would pass the buffer's end */
    if (!dropped((uint32_t)((room + WL_END_RESERVE) / 4u + 512u))) return 8;
    return 0;
}
"""


def test_the_core_keeps_to_its_buffer(H):
    """wl_begin refuses a buffer a byte short of what Init and the end records
    can write; a tick whose trailer, or whose one long run, does not fit is
    dropped whole, and nothing is written past the cap or the buffer."""
    src = H.work / "edge.c"
    src.write_text(EDGE_C)
    exe = H.work / "edge"
    p = zig(str(src), "-o", str(exe), "-I", str(REPLAY), cwd=H.work)
    assert p.returncode == 0, p.stderr
    assert subprocess.run([str(exe)], timeout=120).returncode == 0


# ------------------------------------------------------------------ the refusals
# Logs and CSVs made from the good run, each wrong in one way. Each test holds
# the refusal to its own words, so a check that is taken out, or that refuses
# for another reason, fails its test.

@pytest.fixture(scope="module")
def raw(good) -> bytes:
    return good["log"].read_bytes()


@pytest.fixture(scope="module")
def lines(good) -> list[str]:
    return good["csv"].read_text().splitlines()


@pytest.fixture(scope="module")
def tick_hash(good) -> dict[int, int]:
    return {t: int(h, 16) for t, h in W.hashes(good["log"]).items()}


def parts(log: bytes):
    recs = records(log)
    return recs, [r for r in recs if r[0] == "SA2D"], recs[-2], recs[-1]


def with_end(log: bytes, body: bytes, last_tick: int, tick_hash: dict[int, int]) -> bytes:
    """body, then an end-of-run check of `last_tick`, the log's end region map,
    and an end record naming `last_tick` and its own offset."""
    _, _, end_map, end_rec = parts(log)
    end = bytearray(log[end_rec[1]:end_rec[2]])
    struct.pack_into("<I", end, 4, last_tick)
    b = (body + b"SA2V" + struct.pack("<IIIQ", last_tick, 0, 0, tick_hash[last_tick])
         + log[end_map[1]:end_map[2]])
    struct.pack_into("<Q", end, 36, len(b))
    return b + bytes(end)


def cut_to(log: bytes, n: int, tick_hash: dict[int, int]) -> bytes:
    """A whole log of ticks 1 to n."""
    _, ticks, _, _ = parts(log)
    return with_end(log, log[:ticks[n][1]], n, tick_hash)


def without(log: bytes, kind: str, last: bool = False) -> bytes:
    """The log without its first (or last) record of a kind, the end record's
    offset moved to match."""
    recs = [r for r in records(log) if r[0] == kind]
    k = recs[-1] if last else recs[0]
    b = bytearray(log[:k[1]] + log[k[2]:])
    struct.pack_into("<Q", b, len(b) - 8, len(b) - 44)
    return bytes(b)


def patched(log: bytes, at: int, fmt: str, value: int) -> bytes:
    b = bytearray(log)
    struct.pack_into(fmt, b, at, value)
    return bytes(b)


def check(where: Path, csv_lines: list[str], log: bytes, **kw) -> dict:
    (where / "c.csv").write_text("\n".join(csv_lines) + "\n")
    (where / "l.bin").write_bytes(log)
    return W.check_capture(where / "c.csv", where / "l.bin", **kw)


def resealed(where: Path, log: bytes) -> bytes:
    """The log with its end-of-run hash set to what its words rebuild to at
    the last tick, after an edit that changed them."""
    (where / "reseal.bin").write_bytes(log)
    got = W.run("hashes", where / "reseal.bin", partial=True).stdout.splitlines()
    last = int(got[-1].split(",")[1], 16)
    at = [r for r in records(log) if r[0] == "SA2V"][0][1]
    return patched(log, at + 16, "<Q", last)


def test_a_csv_a_tick_longer_than_its_input_end_log_is_refused(H, scratch):
    """The load-order risk: a tick probe loaded before the input probe writes a
    row on the tick the input probe ends, one more than its log holds."""
    got = H.run(H.program((INPUT_END,)), input_loaded=True, end_known_from=50, planned_end=55)
    lines = got["csv"].read_text().splitlines()
    t, rest = lines[-1].split(",", 1)
    (scratch / "long.csv").write_text("\n".join(lines + [f"{int(t) + 1},{rest}"]) + "\n")
    problems = W.check_capture(scratch / "long.csv", got["log"])["problems"]
    assert problems == ["the CSV holds 55 ticks and the log 54",
                        "the log holds no row for 1 of the CSV's ticks, first 55"], problems


def test_a_csv_one_tick_longer_than_its_log_is_refused(raw, lines, tick_hash, scratch):
    got = check(scratch, lines, cut_to(raw, 119, tick_hash), native=False)
    assert got["problems"] == ["the CSV holds 120 ticks and the log 119",
                               "the log holds no row for 1 of the CSV's ticks, first 120"]


def test_a_tick_limit_log_that_ends_short_is_refused(raw, lines, tick_hash, scratch):
    got = check(scratch, lines[:120], cut_to(raw, 119, tick_hash))
    assert got["problems"] == ["the log ends at tick 119, not at its TICK_LIMIT 120"]


def test_a_log_written_by_another_end_than_its_rule_is_refused(raw, lines, scratch):
    got = check(scratch, lines, patched(raw, len(raw) - 44 + 12, "<I", 3))     # reason: on_exit
    assert got["problems"] == ["the log was written by on_exit, not at its planned end (tick_limit)"]


def test_a_csv_with_a_missing_row_is_refused(raw, lines, scratch):
    got = check(scratch, lines[:60] + lines[61:], raw)
    assert any(p.endswith("the ticks are not 1 to 119 without a gap") for p in got["problems"]), got


def test_a_csv_with_a_tick_twice_is_refused(raw, lines, scratch):
    got = check(scratch, lines[:61] + [lines[60]] + lines[61:], raw)
    assert got["problems"] == [f"the CSV cannot be read: {scratch / 'c.csv'}: tick 60 has two rows"]


def test_a_csv_whose_header_is_not_the_field_list_is_refused(raw, lines, scratch):
    head = lines[0].replace(",window_active", ",window_active_x")
    got = check(scratch, [head] + lines[1:], raw)
    assert len(got["problems"]) == 1 and got["problems"][0].endswith(
        "the header is not tick and tick_fields.h's fields in order"), got


@pytest.mark.parametrize("k", range(len(W.named_fields())))
def test_every_named_field_is_compared(raw, lines, scratch, k):
    name = W.named_fields()[k][0]
    rows = [ln.split(",") for ln in lines]
    rows[77][1 + k] = f"0x{int(rows[77][1 + k], 16) ^ 1:08x}"
    got = check(scratch, [",".join(r) for r in rows], raw)
    assert got["problems"] == [f"{name} differs from the log's bytes on 1 ticks, first 77 "
                               f"(CSV {rows[77][1 + k]}, log {lines[77].split(',')[1 + k]})"]


def test_a_capture_the_shift_control_cannot_see_into_is_refused(raw, lines, tick_hash, scratch):
    """One tick: the named fields a tick off are compared nowhere, so the check
    could not have seen a shift, and says so."""
    got = check(scratch, lines[:2], cut_to(raw, 1, tick_hash), native=False)
    assert got["problems"] == ["the named fields are the same a tick off, so this check could not "
                               "see the log shifted a tick against the CSV"]


def test_a_digest_cell_that_differs_is_refused(good, raw, scratch):
    full = W.capture_csv(good["log"]).splitlines()
    assert check(scratch, full, raw, native=False)["ok"]
    rows = [ln.split(",") for ln in full]
    col = len(rows[0]) - 1
    rows[50][col] = f"0x{int(rows[50][col], 16) ^ 1:08x}"
    got = check(scratch, [",".join(r) for r in rows], raw, native=False)
    assert got["problems"] == [f"1 digest cells differ from the log's, first tick 50 in {rows[0][col]}"]


def test_a_word_logged_with_its_own_value_is_refused(good, raw, lines, scratch):
    """A record naming a word with the value it already held: the shadow's
    check, alone (the word is no named field's, and the end-of-run hash is set
    to the edited words)."""
    spans = [(va, va + w) for _, va, w in W.named_fields()]
    _, ticks, _, _ = parts(raw)
    t = 40
    prev = W.state(good["log"], t - 1)
    p = ticks[t - 1][1] + 8
    while True:
        first, count = struct.unpack_from("<II", raw, p)
        assert first != 0xFFFFFFFF, "tick 40 has no run off the named fields"
        va = BASE + 4 * first
        if not any(lo < va + 4 and va < hi for lo, hi in spans):
            break
        p += 8 + 4 * count
    b = bytearray(raw)
    b[p + 8:p + 12] = prev[4 * first:4 * first + 4]
    got = check(scratch, lines, resealed(scratch, bytes(b)))
    assert got["problems"] == ["1 words are logged with the value they already held: the "
                               "writer's shadow went wrong"]


def test_a_version_1_log_reads_but_is_not_a_native_capture(raw, lines, scratch):
    """The prototype's format: a 24-byte header, the keyframe, the ticks and a
    16-byte end record, nothing else."""
    recs = records(raw)
    key = next(r for r in recs if r[0] == "SA2K")
    ticks = [r for r in recs if r[0] == "SA2D"]
    v1 = (b"SA2F" + struct.pack("<IIIQ", 1, BASE, SIZE, struct.unpack_from("<Q", raw, 36)[0])
          + raw[key[1]:key[2]] + raw[ticks[0][1]:ticks[-1][2]] + b"SA2E" + bytes(12))
    got = check(scratch, lines, v1)
    i = W.info(scratch / "l.bin")
    assert (i["version"], i["whole"], i["ticks"]) == (1, True, 120)
    assert "the log is version 1, not the probe's 2" in got["problems"], got["problems"]
    assert check(scratch, lines, v1, native=False)["ok"]


def _more_at_init(raw):
    return patched(raw, records(raw)[1][1] + 12, "<I", 1)


@pytest.mark.parametrize("make,why", [
    (lambda raw: without(raw, "SA2O"),
     "the log names 0 blocks of its own (SA2O), not the probe's shadow and buffer: a pointer "
     "test would take them for the game's memory"),
    (lambda raw: without(raw, "SA2R"), "the log has no region map at Init"),
    (lambda raw: without(raw, "SA2R", last=True), "the log has no region map at the end"),
    (_more_at_init, "a region map stopped at WL_MAX_REGIONS regions: a pointer test would miss "
                    "what lies past them"),
    (lambda raw: without(raw, "SA2V"), "the log carries no end-of-run check (SA2V): the probe "
                                       "writes one after its last tick unless it overflowed"),
])
def test_a_native_log_without_the_probe_s_records_is_refused(raw, lines, scratch, make, why):
    """What the native probe always writes, and a pointer test or the rebuild
    needs; a log of another writer may leave them out."""
    log = make(raw)
    assert check(scratch, lines, log)["problems"] == [why]
    assert check(scratch, lines, log, native=False)["ok"]


def test_the_pointer_test_refuses_region_maps_it_cannot_trust(raw, scratch):
    for log, why in ((_more_at_init(raw), "stopped at WL_MAX_REGIONS regions"),
                     (without(raw, "SA2R"), "has no region map at Init or at the end"),
                     (without(raw, "SA2R", last=True), "has no region map at Init or at the end")):
        (scratch / "p.bin").write_bytes(log)
        with pytest.raises(W.WordLogError, match=why):
            W.pointer_words(scratch / "p.bin")


def _gap(raw, th):
    _, t, _, _ = parts(raw)
    return with_end(raw, raw[:t[59][1]] + raw[t[60][1]:t[-1][2]], 120, th)


def _dup(raw, th):
    _, t, _, _ = parts(raw)
    return with_end(raw, raw[:t[60][1]] + raw[t[59][1]:t[59][2]] + raw[t[60][1]:t[-1][2]], 120, th)


def _trailer(raw, th):
    _, t, _, _ = parts(raw)
    q = t[49][2] - 32
    return patched(raw, q, "<I", struct.unpack_from("<I", raw, q)[0] + 1)


def _adjacent_runs(raw, th):
    """The second run of a tick moved to start where the first ends."""
    _, ticks, _, _ = parts(raw)
    for _, start, _ in ticks:
        p = start + 8
        f1, c1 = struct.unpack_from("<II", raw, p)
        q = p + 8 + 4 * c1
        f2, _ = struct.unpack_from("<II", raw, q)
        if f1 != 0xFFFFFFFF and f2 != 0xFFFFFFFF and f2 > f1 + c1:
            return patched(raw, q, "<I", f1 + c1)
    raise AssertionError("no tick with two runs")


def _tick_after_the_check(raw, th):
    """The end-of-run check before the last tick instead of after it."""
    _, t, _, _ = parts(raw)
    body = raw[:t[-1][1]] + b"SA2V" + struct.pack("<IIIQ", 119, 0, 0, th[119]) + raw[t[-1][1]:t[-1][2]]
    _, _, end_map, end_rec = parts(raw)
    b = bytearray(body + raw[end_map[1]:end_map[2]] + raw[end_rec[1]:end_rec[2]])
    struct.pack_into("<Q", b, len(b) - 8, len(b) - 44)
    return bytes(b)


def _check_at(raw):
    return [r for r in records(raw) if r[0] == "SA2V"][0][1]


def _check_twice(raw, th):
    """The end-of-run check written a second time, after the first."""
    at = _check_at(raw)
    b = bytearray(raw[:at + 24] + raw[at:])
    struct.pack_into("<Q", b, len(b) - 8, len(b) - 44)
    return bytes(b)


@pytest.mark.parametrize("make,why", [
    (_gap, "the ticks do not run on from 1 without a gap"),
    (_dup, "the ticks do not run on from 1 without a gap"),
    (_trailer, "a tick's trailer counts other runs or words than it holds"),
    (lambda raw, th: patched(raw, len(raw) - 8, "<Q", len(raw) - 44 + 4),
     "the end record names another offset than its own"),
    (lambda raw, th: patched(raw, len(raw) - 44 + 4, "<I", 119),
     "the end record names another last tick than the last record"),
    (lambda raw, th: patched(raw, len(raw) - 44 + 16, "<I", 1),
     "the end record's reserved word is not 0"),
    (lambda raw, th: patched(raw, len(raw) - 44 + 8, "<I", 50),
     "the end record names an overflow that is not at the tick after its last"),
    (_adjacent_runs, "runs do not ascend apart from each other"),
    (lambda raw, th: patched(raw, _check_at(raw) + 16, "<Q", th[120] ^ 1),
     "the words the log rebuilds at its last tick are not the writer's shadow (the end-of-run "
     "check's hash)"),
    (lambda raw, th: patched(raw, _check_at(raw) + 4, "<I", 119),
     "the end-of-run check names another tick than the last record"),
    (lambda raw, th: patched(raw, _check_at(raw) + 12, "<I", 1),
     "the end-of-run check's reserved word is not 0"),
    (_tick_after_the_check, "a tick after the end-of-run check or the region map at the end"),
    (_check_twice, "an end-of-run check out of its place"),
])
def test_the_reader_refuses_a_malformed_log(good, raw, tick_hash, scratch, make, why):
    (scratch / "bad.bin").write_bytes(make(raw, tick_hash))
    i = W.info(scratch / "bad.bin")
    assert (i["whole"], i["problem"]) == (False, why)
    assert not W.check_capture(good["csv"], scratch / "bad.bin")["ok"]
    with pytest.raises(W.WordLogError):
        W.capture_csv(scratch / "bad.bin")


@pytest.mark.parametrize("where,why", [
    (lambda raw: 28, "line_words is not word_log_core.h's WL_LINE_WORDS"),
    (lambda raw: 32, "a header whose reserved word is not 0"),
    (lambda raw: next(r for r in records(raw) if r[0] == "SA2K")[1] + 4,
     "a keyframe whose reserved word is not 0"),
])
def test_the_reader_refuses_a_header_or_keyframe_it_does_not_know(raw, scratch, where, why):
    (scratch / "bad.bin").write_bytes(patched(raw, where(raw), "<I", 8))
    with pytest.raises(W.WordLogError, match=why):
        W.info(scratch / "bad.bin")


def test_the_reader_refuses_a_keyframe_of_another_section(raw, scratch):
    k = [r for r in records(raw) if r[0] == "SA2K"][0]
    (scratch / "bad.bin").write_bytes(patched(raw, k[1] + 8, "<I", BASE + 0x1000))
    with pytest.raises(W.WordLogError, match="no keyframe where one must be"):
        W.info(scratch / "bad.bin")


# ------------------------------------------------ the check never raises

def test_the_capture_check_never_raises(good, overflowed, tmp_path, monkeypatch):
    """Whatever stops the check is a problem: an overflowed log against a CSV
    that carries digest columns (the lifted side's case) is compared on the
    ticks it holds; a CSV, a log or a mask that cannot be read, and a failure
    inside the check itself, are each named."""
    got = W.check_capture(overflowed["dir"] / "truth.csv", overflowed["log"], native=False)
    blocks = len(W.mask_blocks())
    assert not got["ok"]
    assert got["problems"][0].startswith("the log overflowed its buffer at tick 37")
    assert (got["digest_cells_compared"], got["digest_cells_differing"]) == (36 * blocks, 0)
    assert W.check_capture(tmp_path / "none.csv", good["log"])["problems"][-1].startswith(
        "the CSV cannot be read")
    assert W.check_capture(good["csv"], tmp_path / "none.bin")["problems"] == [
        f"the reader refused the log: wordlog info {tmp_path / 'none.bin'}: exit 2: "
        f"wordlog: {tmp_path / 'none.bin'}: cannot be opened"]
    (tmp_path / "bad-mask.h").write_text("not a mask\n")
    got = W.check_capture(good["dir"] / "truth.csv", good["log"], mask=tmp_path / "bad-mask.h")
    assert got["problems"][-1].startswith(f"the mask {tmp_path / 'bad-mask.h'} cannot be read")

    def broken(*a, **k):
        raise W.WordLogError("the reader broke")
    monkeypatch.setattr(W, "field_values", broken)
    got = W.check_capture(good["csv"], good["log"])
    assert (got["ok"], got["problems"]) == (
        False, ["the check could not finish: WordLogError: the reader broke"])


# ------------------------------------------------ the digest columns, any mask

MASK2 = """#ifndef SA2_TICK_DIGEST_H
#define SA2_TICK_DIGEST_H
#include <stdint.h>
#define TD_BLOCKS 2u
#define TD_DATA_END 0x02670598u
static const uint32_t td_base[TD_BLOCKS] = {
    0x01740000u,
    0x00920000u,
};
static const uint32_t td_mask[TD_BLOCKS][512] = {
    {%s},
    {%s},
};
#endif
"""


def test_the_digest_columns_follow_the_mask_they_are_given(good, raw, scratch, tmp_path):
    """A second mask, blocks out of address order: frame_count's word, and the
    16 words the synthetic game writes one of every tick. The reader built for
    it gives its columns, equal to FNV-1a over the rebuilt words, and the check
    holds a CSV to the mask it is given."""
    import word_attribution as WA
    m1 = ["0"] * 512
    m1[(0x0174B038 - 0x01740000) // 4 // 32] = f"0x{1 << ((0x0174B038 - 0x01740000) // 4 % 32):x}u"
    m2 = ["0"] * 512
    m2[(0x00917000 + 4 * 16 * 777 - 0x00920000) // 4 // 32] = "0xffff0000u"
    mask = tmp_path / "mask2.h"
    mask.write_text(MASK2 % (",".join(m1), ",".join(m2)))
    tree = W.capture_csv(good["log"]).splitlines()[0]
    got = W.capture_csv(good["log"], mask).splitlines()
    assert tree != got[0]
    header = got[0].split(",")
    assert header[-2:] == ["d_01740000", "d_00920000"] and len(header) == 1 + len(W.named_fields()) + 2
    words = {c: WA.digest_words(int(c[2:], 16), header=mask) for c in header[-2:]}
    assert len(words["d_01740000"]) == 1 and len(words["d_00920000"]) == 16
    seen = set()
    for t in (1, 2, 37, 120):
        section = W.state(good["log"], t)
        row = got[t].split(",")
        for c in header[-2:]:
            h = 2166136261
            for va in words[c]:
                h = ((h ^ struct.unpack_from("<I", section, va - BASE)[0]) * 16777619) & 0xFFFFFFFF
            assert row[header.index(c)] == f"0x{h:08x}", (t, c)
            seen.add((c, row[header.index(c)]))
    assert len(seen) == 8, "the columns must move from tick to tick"
    got_ok = check(scratch, got, raw, mask=mask, native=False)
    assert got_ok["ok"] and got_ok["digest_cells_compared"] == 120 * 2, got_ok
    got_bad = check(scratch, got, raw, native=False)
    assert any(p.endswith("its digest columns are not tick_digest.h's blocks")
               for p in got_bad["problems"]), got_bad


# ------------------------------------------------ the reader's build and tools

def test_the_reader_is_built_again_for_other_flags_or_another_compiler(monkeypatch):
    """The build cache is keyed by what makes the program: the sources, the
    compiler's path and its flags. (The second compiler is a link at a fixed
    place beside the builds, so its build is made once, not once a session.)"""
    first = W.reader()
    monkeypatch.setattr(W, "CFLAGS", W.CFLAGS + ("-DWORDLOG_CACHE_TEST=1",))
    assert W.reader() != first
    monkeypatch.undo()
    link = W.BUILD / "cache-test" / "zig"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() and link.resolve() != W.ZIG.resolve():
        link.unlink()
    if not link.is_symlink():
        link.symlink_to(W.ZIG)
    monkeypatch.setattr(W, "ZIG", link)
    other = W.reader()
    assert other != first and other.is_file()


def test_state_makes_its_build_directory(good, tmp_path, monkeypatch):
    want = W.state(good["log"], 0)
    exe = W.reader()
    monkeypatch.setattr(W, "BUILD", tmp_path / "fresh" / "wordlog")
    monkeypatch.setattr(W, "reader", lambda mask=None: exe)
    assert W.state(good["log"], 0) == want


def test_word_attribution_compares_only_the_logged_section(good, perturbed):
    """The first and last digest blocks reach past `.dataa`; their words
    outside it are in no log and are left out, not refused."""
    import word_attribution as WA
    words = [BASE - 8, BASE - 4, DIFF_WORD, BASE + SIZE - 4, BASE + SIZE, BASE + SIZE + 4]
    assert list(WA.differing_logs(good["log"], perturbed["log"], words, None)) == [DIFF_WORD]


def test_word_attribution_reports_the_ticks_as_a_list_and_a_range():
    import word_attribution as WA
    head = WA.report_head(Path("a.bin"), Path("b.bin"), True, list(range(1, 121)))
    assert (head["kind"], head["ticks"], head["ticks_range"]) == ("log", list(range(1, 121)), [1, 120])


def test_word_attribution_counts_the_words_it_compares(good, perturbed, tmp_path, monkeypatch, capsys):
    """On logs the report's words_compared is the words the logs hold: the
    first and last digest blocks reach past `.dataa`, and their words outside
    it are not compared; the report counts them as outside the log instead
    of as compared (the fold review's W1). The analyzer's
    map and the executable are stood in for: no word here has an owner."""
    import word_attribution as WA
    assert len(WA.logged(list(range(0x02670000, 0x02680000, 4)))) == 358
    assert len(WA.logged(list(range(0x00910000, 0x00920000, 4)))) == 9216
    gen = tmp_path / "gen"
    gen.mkdir()
    (gen / "function-map.json").write_text('{"functions": []}')
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(GEN=gen))
    monkeypatch.setattr(WA, "relocation_targets", lambda exe, lo, hi: {})
    report = tmp_path / "report.json"
    blocks = ["0x00910000", "0x00920000", "0x02670000"]
    assert WA.main([str(good["log"]), str(perturbed["log"]), "--all-words", "--json", str(report),
                    *[x for b in blocks for x in ("--block", b)]]) == 0
    got = {b["block"]: (b["words_compared"], b["words_outside_the_log"], b["words_differing"])
           for b in json.loads(report.read_text())["blocks"]}
    assert got == {"0x00910000": (9216, 7168, 0), "0x00920000": (16384, 0, 3),
                   "0x02670000": (358, 16026, 0)}
    assert "block=0x02670000 compared=358 outside_the_log=16026 differing=0 " in capsys.readouterr().out
    # the three are the words the perturbed run moves, all in the dumped range
    # (test_diff_names_every_word_two_runs_disagree_on holds them to the truth)
    rows = json.loads(report.read_text())["blocks"][1]["rows"]
    assert [r["word"] for r in rows] == ["0x00923024", "0x0092302c", f"{DIFF_WORD:#010x}"]


def capture_csv_calls_not_naming_native(root: Path) -> list[str]:
    """Every call of capture_csv under the tree's tools, harness and scripts,
    tests aside, that does not name `native`, as path:line."""
    out = []
    for top in ("tools", "harness", "Documentation/scripts"):
        if not (root / top).is_dir():
            continue
        for f in sorted((root / top).rglob("*.py")):
            if f.name.startswith("test_") or f.name == "conftest.py":
                continue
            for node in ast.walk(ast.parse(f.read_text(), str(f))):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name == "capture_csv" and not any(k.arg == "native" for k in node.keywords):
                    out.append(f"{f.relative_to(root)}:{node.lineno}")
    return out


def test_every_tool_that_takes_a_capture_from_a_log_says_whether_it_is_native(tmp_path):
    """capture_csv's default is the lifted side's (native=False), so a native
    caller that forgot the flag would take a capture from an OnExit log or a
    log of another buffer size (the fold review's N1). Every call in the
    tree's tools names `native` instead, so forgetting it fails here. Tests
    are left out: they call it on synthetic logs on purpose. The finder is
    held to a made-up tree first, so an empty answer is not a finder that
    sees nothing."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "a.py").write_text(
        "import wordlog as WL\n"
        "WL.capture_csv(log)\n"
        "WL.capture_csv(log, mask,\n    native=True)\n"
        "capture_csv(log, native=False)\n"
        "capture_csv(log, **kw)\n")
    (tmp_path / "tools" / "test_a.py").write_text("WL.capture_csv(log)\n")
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / "b.py").write_text("WL.capture_csv(log)\n")
    (tmp_path / "Documentation" / "scripts").mkdir(parents=True)
    (tmp_path / "Documentation" / "scripts" / "c.py").write_text("WL.capture_csv(log)\n")
    assert capture_csv_calls_not_naming_native(tmp_path) == [
        "tools/a.py:2", "tools/a.py:6", "harness/b.py:1", "Documentation/scripts/c.py:1"]
    assert capture_csv_calls_not_naming_native(ROOT) == []


# ------------------------------------------------------------ kept by content

def test_a_capture_is_kept_by_its_log_s_bytes_not_its_path(good, perturbed, tmp_path):
    """capture_csv, field_values and diff keep what they read per process by the
    log's sha256 and the reader's build (the U6 fold: the replay gate reads the
    same logs once per falsification). A log whose bytes change at one path is
    read again, and the same bytes at another path give what they gave. The
    two runs differ only in DIFF_WORD, so the capture is taken under a mask that
    holds it."""
    import snapshot_fields as SF
    W.forget()
    mask = tmp_path / "diff-word.h"
    SF.write_mask([DIFF_WORD], [" * the word the perturbed run moves"], mask)
    field = [("d", DIFF_WORD, 4)]
    log = tmp_path / "moving.bin"
    shutil.copyfile(good["log"], log)
    first, first_fields = W.capture_csv(log, mask), W.field_values(log, field)
    assert first == W.capture_csv(good["log"], mask)
    shutil.copyfile(perturbed["log"], log)          # the same path, other bytes
    assert W.capture_csv(log, mask) == W.capture_csv(perturbed["log"], mask) != first
    assert W.field_values(log, field) == W.field_values(perturbed["log"], field) != first_fields
    moved = W.diff(good["log"], log)[0]
    assert DIFF_WORD in [r["va"] for r in moved] and moved == W.diff(good["log"], perturbed["log"])[0]
    shutil.copyfile(good["log"], log)               # back: the kept answer is the first one
    assert W.capture_csv(log, mask) == first and W.field_values(log, field) == first_fields
    assert W.diff(good["log"], log)[0] == []
    # Other questions of the same bytes are other keys: another field list, another range.
    two = [("d", DIFF_WORD, 4), ("e", DIFF_WORD + 4, 4)]
    assert all(len(v) == 2 for v in W.field_values(log, two).values())
    assert W.diff(good["log"], perturbed["log"])[0] != []
    assert W.diff(good["log"], perturbed["log"], DIFF_WORD + 4, DIFF_WORD + 8)[0] == []


def test_a_refusal_is_not_kept(overflowed, tmp_path):
    """A refused log is asked again, so a log refused as native and then read
    as any log gives its capture, and the refusal stands on the next ask."""
    W.forget()
    with pytest.raises(SystemExit):
        W.capture_csv(overflowed["log"])
    assert W.capture_csv(overflowed["log"], allow_overflow=True).startswith("tick,")
    with pytest.raises(SystemExit):
        W.capture_csv(overflowed["log"])
