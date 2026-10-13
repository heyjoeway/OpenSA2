"""The import log's write tick, with both probes running as they run in the game.

harness/replay/native_import_probe.c built with INPUT_END_DLL writes its log on
the tick before harness/replay/native_input_probe.c ends the run, by asking
that probe's SA2InputProbeEnd; built with TICK_LIMIT it writes at that tick, as
before. A registered replay's log must be counted at the replay's last captured
tick (tools/s4/native_imports.py replay_calls), and a recording's last tick is
decided while the user plays, so the one tick that matters is decided by two
probes' OnFrame callbacks in load order. That order is the thing to test, and
no unit test of either file alone can.

This builds both probe sources, unchanged, into one 32-bit Linux program with
a stand-in for the few Win32 calls they make (`windows.h` below): the loader's
module lookup, file writes, VirtualAlloc for the import probe's thunks, and
ExitProcess. The driver plays the Mod Loader: Init for each mod, then per tick
the game's controller updates (OnInput) and its import calls, the goal latch
at the game's address, and OnFrame in load order -- import probe, input probe,
then the tick probe, which it models by counting the rows the real one would
write. Import calls go through the fake image's import-address table and so
through the probe's own `lock inc` / `jmp` thunks, which run as built.

Each run is held to what the reader requires: one write, every row counted at
the run's last captured tick, each slot's count equal to the calls made up to
that tick's row and no later. Mutants that move either probe's tick by one
must fail the same check, so it is shown able to fail. The import probe looks
the input probe up once, in Init, which the Mod Loader calls only after it has
loaded every mod, and the driver counts every lookup, so one per frame fails.
Nothing here is from the executable: the image, its slots and the calls are
made up.

What the stand-in cannot show is the Windows side of the lookup: the DLL's
name and the export's undecorated name. The last tests build both probes with
tools/oracle/run_oracle_probe.py's own compile_probe, as a native run does,
and read the names from the DLLs it writes.
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
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import native_imports as NI  # noqa: E402  read_at: the reader the refusal must survive
import record_stage as R  # noqa: E402  INPUT_MOD and DEFINES: what the recorder builds
import run_oracle_probe as RP  # noqa: E402  compile_probe: how a native run builds a probe
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
IMPORT_PROBE = ROOT / "harness" / "replay" / "native_import_probe.c"
INPUT_PROBE = ROOT / "harness" / "replay" / "native_input_probe.c"
INPUT_END = 'INPUT_END_DLL="input_probe.dll"'
NSLOT = 3

WINDOWS_H = r"""
/* The Win32 surface the two probes use, for a 32-bit Linux test build. */
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef int BOOL;
typedef uint32_t DWORD;
typedef long LONG;
typedef void *HANDLE, *HINSTANCE, *HMODULE, *LPVOID;
typedef const char *LPCSTR;
typedef intptr_t (*FARPROC)(void);
#define TRUE 1
#define WINAPI
#define __cdecl
#define DLL_PROCESS_DETACH 0
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define GENERIC_WRITE 0x40000000u
#define CREATE_ALWAYS 2u
#define FILE_ATTRIBUTE_NORMAL 0x80u
#define MEM_COMMIT 0x1000u
#define MEM_RESERVE 0x2000u
#define PAGE_EXECUTE_READWRITE 0x40u
#define PAGE_READWRITE 0x04u
#define IMAGE_DIRECTORY_ENTRY_IMPORT 1
typedef struct { uint16_t e_magic; uint8_t pad[58]; int32_t e_lfanew; } IMAGE_DOS_HEADER;
typedef struct { uint32_t VirtualAddress, Size; } IMAGE_DATA_DIRECTORY;
typedef struct { IMAGE_DATA_DIRECTORY DataDirectory[16]; } IMAGE_OPTIONAL_HEADER32;
typedef struct { uint32_t Signature; IMAGE_OPTIONAL_HEADER32 OptionalHeader; } IMAGE_NT_HEADERS32;
typedef struct {
    uint32_t OriginalFirstThunk, TimeDateStamp, ForwarderChain, Name, FirstThunk;
} IMAGE_IMPORT_DESCRIPTOR;
HMODULE GetModuleHandleA(LPCSTR name);
FARPROC GetProcAddress(HMODULE module, LPCSTR name);
void *VirtualAlloc(void *at, size_t size, DWORD type, DWORD protect);
BOOL VirtualProtect(void *at, size_t size, DWORD protect, DWORD *old);
HANDLE GetCurrentProcess(void);
BOOL FlushInstructionCache(HANDLE process, const void *at, size_t size);
HANDLE CreateFileA(LPCSTR name, DWORD access, DWORD share, void *security, DWORD how,
                   DWORD flags, HANDLE templ);
BOOL WriteFile(HANDLE f, const void *data, DWORD size, DWORD *written, void *overlapped);
BOOL CloseHandle(HANDLE f);
void ExitProcess(unsigned code);
"""

DRIVER_C = r"""
/* The Mod Loader and the game, as far as the two probes can tell. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "windows.h"

#define NSLOT %(nslot)d
void imp_Init(const char *, const void *, unsigned);
void imp_OnFrame(void);
BOOL imp_DllMain(HINSTANCE, DWORD, LPVOID);
void inp_Init(const char *, const void *, unsigned) __attribute__((weak));
void inp_OnFrame(void) __attribute__((weak));
void inp_OnInput(void) __attribute__((weak));
unsigned long SA2InputProbeEnd(void) __attribute__((weak));

static struct image {
    IMAGE_DOS_HEADER dos;
    IMAGE_NT_HEADERS32 nt;
    IMAGE_IMPORT_DESCRIPTOR desc[2];
    uint32_t iat[NSLOT + 1];
    char dll[8];
} img;
static void target0(void) {}
static void target1(void) {}
static void target2(void) {}
static void (*const targets[NSLOT])(void) = {target0, target1, target2};
static int exited, log_opens, input_loaded, lookups;

HMODULE GetModuleHandleA(LPCSTR name) {
    if (!name) return (HMODULE)&img;
    ++lookups;
    if (input_loaded && SA2InputProbeEnd && !strcmp(name, "input_probe.dll")) return (HMODULE)8;
    return NULL;
}
FARPROC GetProcAddress(HMODULE m, LPCSTR name) {
    if (m == (HMODULE)8 && !strcmp(name, "SA2InputProbeEnd"))
        return (FARPROC)(void (*)(void))SA2InputProbeEnd;
    return NULL;
}
void *VirtualAlloc(void *at, size_t size, DWORD type, DWORD protect) {
    (void)at, (void)type, (void)protect;
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE | PROT_EXEC,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return p == MAP_FAILED ? NULL : p;
}
BOOL VirtualProtect(void *at, size_t size, DWORD protect, DWORD *old) {
    (void)at, (void)size, (void)protect;
    *old = 0;
    return TRUE;
}
HANDLE GetCurrentProcess(void) { return NULL; }
BOOL FlushInstructionCache(HANDLE p, const void *at, size_t size) {
    (void)p, (void)at, (void)size;
    return TRUE;
}
HANDLE CreateFileA(LPCSTR name, DWORD access, DWORD share, void *security, DWORD how, DWORD flags,
                   HANDLE templ) {
    (void)access, (void)share, (void)security, (void)how, (void)flags, (void)templ;
    if (strstr(name, "sa2-import-calls.csv")) ++log_opens;
    FILE *f = fopen(name, "wb");
    return f ? (HANDLE)f : INVALID_HANDLE_VALUE;
}
BOOL WriteFile(HANDLE f, const void *data, DWORD size, DWORD *written, void *overlapped) {
    (void)overlapped;
    *written = (DWORD)fwrite(data, 1, size, (FILE *)f);
    return TRUE;
}
BOOL CloseHandle(HANDLE f) { return fclose((FILE *)f) == 0; }
void ExitProcess(unsigned code) { (void)code; exited = 1; }

static void call(unsigned slot) { ((void (*)(void))(uintptr_t)img.iat[slot])(); }

/* argv: directory, goal tick (0: never), tick cap, input probe loaded (0/1),
 * the tick probe's TICK_LIMIT (0: none). */
int main(int argc, char **argv) {
    if (argc != 6 || chdir(argv[1])) return 2;
    const unsigned long goal = strtoul(argv[2], 0, 10), cap = strtoul(argv[3], 0, 10);
    const unsigned long tick_probe_limit = strtoul(argv[5], 0, 10);
    input_loaded = atoi(argv[4]);
    if (mmap((void *)0x01740000u, 0x320000u, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != (void *)0x01740000u)
        return 3;                       /* the goal latch and ControllersRaw live here */
    img.dos.e_lfanew = (int32_t)offsetof(struct image, nt);
    img.nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress =
        offsetof(struct image, desc);
    img.desc[0].Name = offsetof(struct image, dll);
    img.desc[0].FirstThunk = offsetof(struct image, iat);
    for (unsigned i = 0; i < NSLOT; ++i) img.iat[i] = (uint32_t)(uintptr_t)targets[i];

    unsigned long made[NSLOT] = {0}, through[NSLOT] = {0}, rows = 0, exit_tick = 0;
    imp_Init("", 0, 0);
    if (input_loaded && inp_Init) inp_Init("", 0, 1);
    const int init_lookups = lookups, init_opens = log_opens;
    call(0), ++made[0];                     /* a call between Init and the first frame */
    for (unsigned long t = 1; t <= cap; ++t) {
        if (input_loaded && inp_OnInput) inp_OnInput(), inp_OnInput();
        call((unsigned)(t %% NSLOT)), ++made[t %% NSLOT];
        call(0), ++made[0];
        if (goal && t == goal) *(volatile uint8_t *)0x0174B002u = 1;
        imp_OnFrame();                      /* the loader's order: import probe first */
        if (input_loaded && inp_OnFrame) inp_OnFrame();
        if (exited) { exit_tick = t; break; }
        rows = t;                           /* the tick probe's row for tick t */
        memcpy(through, made, sizeof made);
        if (tick_probe_limit && t >= tick_probe_limit) { exit_tick = t; break; }
    }
    imp_DllMain(NULL, DLL_PROCESS_DETACH, NULL);
    printf("rows=%%lu exit_tick=%%lu log_opens=%%d lookups=%%d init_lookups=%%d init_opens=%%d",
           rows, exit_tick, log_opens, lookups, init_lookups, init_opens);
    for (unsigned i = 0; i < NSLOT; ++i) printf(" through%%u=%%lu", i, through[i]);
    printf("\n");
    return 0;
}
""" % {"nslot": NSLOT}


def zig(*args: str, cwd: Path) -> None:
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(cwd / "zig-cache"),
               ZIG_GLOBAL_CACHE_DIR=str(cwd / "zig-global"))
    p = subprocess.run([str(ZIG), "cc", "-target", "x86-linux-musl", "-O2", "-Wall", "-Wextra",
                        "-Werror", "-I", str(cwd / "inc"), *args], cwd=cwd, env=env,
                       capture_output=True, text=True)
    if p.returncode:
        raise AssertionError(f"zig cc {' '.join(args)}:\n{p.stderr}")


class Harness:
    """Builds and runs the driver over probe sources, caching each object by
    its source and defines."""

    def __init__(self, work: Path):
        self.work = work
        (work / "inc").mkdir(parents=True)
        (work / "inc" / "windows.h").write_text(WINDOWS_H)
        (work / "driver.c").write_text(DRIVER_C)
        self.objects: dict[tuple, Path] = {}
        self.programs: dict[tuple, Path] = {}
        self.n = 0

    def obj(self, source: Path, prefix: str, defines: tuple[str, ...]) -> Path:
        key = (str(source), source.read_bytes(), prefix, defines)
        if key not in self.objects:
            self.n += 1
            out = self.work / f"o{self.n}.o"
            names = ("Init", "OnFrame", "OnInput", "SA2ModInfo", "DllMain")
            zig("-c", str(source), "-o", str(out), "-D__declspec(x)=",
                *[f"-D{n}={prefix}_{n}" for n in names], *[f"-D{d}" for d in defines],
                cwd=self.work)
            self.objects[key] = out
        return self.objects[key]

    def script(self, rows: list[tuple[int, int]]) -> str:
        """An INPUT_MODE=2 header of neutral rows keyed (tick, ord)."""
        path = self.work / f"script{len(rows)}.h"
        body = ",\n".join("    {%d,%d,0,0,0,0,0,0,0,0,0,0,0}" % r for r in rows)
        path.write_text(f"static const uint32_t in_rows[][13] = {{\n{body}\n}};\n"
                        f"static const unsigned in_count = {len(rows)}u;\n")
        return f'INPUT_SCRIPT="{path}"'

    def run(self, *, importer: tuple[str, ...], inputer: tuple[str, ...] | None, goal: int = 0,
            cap: int = 2000, tick_probe_limit: int = 0, import_source: Path = IMPORT_PROBE,
            input_source: Path = INPUT_PROBE, input_file_fails: bool = False) -> dict:
        """One run. input_file_fails puts a directory where the input probe's
        file goes, so its fopen fails, as a full or read-only disk would."""
        objs = [self.obj(import_source, "imp", importer)]
        if inputer is not None:
            objs.append(self.obj(input_source, "inp", inputer))
        exe = self.programs.get(tuple(objs))
        if exe is None:
            self.n += 1
            exe = self.programs[tuple(objs)] = self.work / f"driver{self.n}"
            zig(str(self.work / "driver.c"), *map(str, objs), "-o", str(exe), cwd=self.work)
        self.n += 1
        run_dir = self.work / f"run{self.n}"
        (run_dir / "mods").mkdir(parents=True)
        if input_file_fails:
            for name in ("sa2-input.csv", "sa2-input-applied.csv"):
                (run_dir / "mods" / name).mkdir()
        p = subprocess.run([str(exe), str(run_dir), str(goal), str(cap),
                            "1" if inputer is not None else "0", str(tick_probe_limit)],
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        out = dict(kv.split("=") for kv in p.stdout.split())
        got = {k: int(v) for k, v in out.items()}
        log = run_dir / "mods" / "sa2-import-calls.csv"
        got["log"] = log.read_text() if log.is_file() else None
        end = run_dir / "mods" / "sa2-input-end.csv"
        got["end"] = next(csv.DictReader(end.open())) if end.is_file() else None
        return got


def log_problems(got: dict) -> list[str]:
    """What native_imports.replay_calls would hold against this run's log:
    written once, every row counted at the run's last captured tick, and
    each count the calls made up to that tick's row."""
    if got["log"] is None:
        return ["no log"]
    rows = list(csv.reader(got["log"].splitlines()))
    out = []
    if got["log_opens"] != 1:
        out.append(f"written {got['log_opens']} times")
    if rows[0] != ["slot", "calls", "ours", "tick"] or len(rows) != NSLOT + 1:
        return out + [f"not one row per slot: {rows}"]
    for k, r in enumerate(rows[1:]):
        if int(r[3]) != got["rows"]:
            out.append(f"slot {k} counted at tick {r[3]}, the last captured tick is {got['rows']}")
        if int(r[1]) != got[f"through{k}"]:
            out.append(f"slot {k} counts {r[1]} calls, {got[f'through{k}']} were made by then")
        if r[2] != "1":
            out.append(f"slot {k} lost its thunk")
    return out


@pytest.fixture(scope="module")
def h(tmp_path_factory):
    """The harness, removed after the module: its zig caches are most of the
    150 MB a run of this file writes, and pytest keeps three runs' worth."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    work = tmp_path_factory.mktemp("import-end")
    yield Harness(work)
    shutil.rmtree(work, ignore_errors=True)


def first_end(goal: int, limit: int, exit_after: int) -> int:
    """The tick the input probe ended the run on before SA2InputProbeEnd
    existed: the first tick at which its old expression held, verbatim."""
    t = 0
    while True:
        t += 1
        if (goal and t >= goal + exit_after) or (limit and t >= limit):
            return t


# ------------------------------------------------------------ the recorder's runs


def test_a_recording_s_log_covers_the_whole_run_whatever_tick_the_goal_ends_it_on(h):
    """The session's recording: no tick limit, the goal at tick 40, so the
    input probe ends the run 300 ticks later and the log is counted on the
    tick before, the last the tick capture holds."""
    got = h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1",), goal=40)
    assert (got["exit_tick"], got["rows"]) == (340, 339)
    assert got["end"] == {"goal_tick": "40", "end_tick": "340", "misses": "0"}
    assert log_problems(got) == []
    assert (got["lookups"], got["init_lookups"]) == (1, 1)     # once, in Init, not per frame


def test_a_preflight_pass_s_log_is_counted_at_599_as_it_always_was(h):
    got = h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1", "TICK_LIMIT=600"))
    assert (got["exit_tick"], got["rows"]) == (600, 599)
    assert log_problems(got) == []


@pytest.mark.parametrize("goal, end", [(40, 340), (150, 400)])
def test_a_replay_s_log_is_counted_at_its_own_last_tick(h, goal, end):
    """record_stage replays with TICK_LIMIT at the recording's end plus 60: a
    replay reaching the goal on time ends where the recording did, and one
    whose goal comes late ends at the cap; either way the log is its own last
    captured tick's."""
    script = h.script([(t, o) for t in range(0, 420) for o in (0, 1)])
    got = h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=2", script, "TICK_LIMIT=400"),
                goal=goal)
    assert (got["exit_tick"], got["rows"]) == (end, end - 1)
    assert got["end"]["misses"] == "0"
    assert log_problems(got) == []


@pytest.mark.parametrize("exit_after", [2, 300])
@pytest.mark.parametrize("limit", [0, 100, 341])
@pytest.mark.parametrize("goal", [0, 1, 40, 97])
def test_the_input_probe_ends_the_run_where_its_old_rule_did_and_the_log_follows(
        h, goal, limit, exit_after):
    """planned_end() replaced the input probe's own end expression; on every
    combination of goal, cap and EXIT_AFTER (2, the least the import probe
    needs, and the default) the run ends on the tick the old expression names,
    and when it ends there the log is that run's."""
    got = h.run(importer=(INPUT_END,),
                inputer=("INPUT_MODE=1", f"TICK_LIMIT={limit}", f"EXIT_AFTER={exit_after}"),
                goal=goal, cap=700)
    if not goal and not limit:
        assert (got["exit_tick"], got["log"]) == (0, None)   # nothing ends it: see below
        return
    want = first_end(goal, limit, exit_after)
    assert got["exit_tick"] == want
    assert log_problems(got) == []


# ------------------------------------------------------------ the other builds


def test_a_boot_build_with_a_tick_limit_writes_there_and_nowhere_else(h):
    """The boot replay's import log: TICK_LIMIT 12 and no input probe, the tick
    probe ending the run on the same tick."""
    got = h.run(importer=("TICK_LIMIT=12",), inputer=None, tick_probe_limit=12)
    assert (got["exit_tick"], got["rows"]) == (12, 12)
    assert log_problems(got) == []


def test_a_tick_limit_build_still_writes_at_detach_when_the_tick_never_came(h):
    """The existing fallback, kept for TICK_LIMIT builds: the tick reached."""
    got = h.run(importer=("TICK_LIMIT=999",), inputer=None, tick_probe_limit=12)
    assert got["log_opens"] == 1 and got["log"].splitlines()[1].endswith(",12")


def test_an_input_end_build_never_writes_at_detach(h):
    """A run that ends other than at the input probe's tick -- here the cap,
    with no goal and no limit -- leaves no log rather than one written under
    the loader lock at detach."""
    got = h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1",), cap=50)
    assert (got["exit_tick"], got["log_opens"], got["log"]) == (0, 0, None)


def test_an_input_end_build_without_the_input_probe_refuses_in_init(h, tmp_path):
    """Written at once, from Init, before any frame; and the reason is one CSV
    field, so the reader repeats it whole rather than split at a comma."""
    got = h.run(importer=(INPUT_END,), inputer=None, tick_probe_limit=20)
    assert (got["log_opens"], got["init_opens"], got["lookups"]) == (1, 1, 1)
    reason = ("input_probe.dll is not loaded or does not export SA2InputProbeEnd; the tick the "
              "run ends on is unknown")
    assert got["log"] == "slot,calls,ours,tick\nrefused,0,0,0\n" + reason
    (tmp_path / "log.csv").write_text(got["log"])
    with pytest.raises(SystemExit) as e:
        NI.read_at(tmp_path / "log.csv", table={})
    assert str(e.value.code).endswith(f": the probe did not patch: {reason}")


def test_an_input_probe_that_cannot_open_its_file_names_no_end_tick(h):
    """Its OnFrame ends a run only while its file is open, so with the file
    unopened nothing ends the run, and the export says so with 0: the import
    probe writes no log rather than one counted at a tick the run runs past.
    The mutant that exports the planned tick regardless shows the difference."""
    kw = dict(importer=(INPUT_END,), inputer=("INPUT_MODE=1", "TICK_LIMIT=100"), cap=150,
              input_file_fails=True)
    got = h.run(**kw)
    assert (got["exit_tick"], got["rows"], got["log_opens"], got["log"]) == (0, 150, 0, None)
    bad = h.run(input_source=mutant(h, INPUT_PROBE, "    return out ? planned_end() : 0;",
                                    "    return planned_end();"), **kw)
    assert (bad["exit_tick"], bad["rows"], bad["log_opens"]) == (0, 150, 1)
    assert {row.split(",")[3] for row in bad["log"].splitlines()[1:]} == {"99"}


def test_a_lookup_on_every_frame_is_caught(h):
    """The count above can fail. A probe that looks the input probe up from
    OnFrame takes the loader lock in the game loop: the first version did so
    once, at its first frame, which init_lookups catches; this one does so on
    every frame."""
    source = mutant(h, IMPORT_PROBE, "    if (!written && input_end) {",
                    "    find_input_end();\n    if (!written && input_end) {")
    got = h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1",), goal=40,
                import_source=source)
    assert got["lookups"] > 1


def test_one_build_cannot_be_given_both_ways_to_pick_the_tick(h):
    with pytest.raises(AssertionError) as e:
        h.run(importer=(INPUT_END, "TICK_LIMIT=5"), inputer=("INPUT_MODE=1",))
    assert "INPUT_END_DLL and TICK_LIMIT each choose the tick" in str(e.value)


def test_an_exit_after_below_two_is_refused_at_build(h):
    with pytest.raises(AssertionError) as e:
        h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1", "EXIT_AFTER=1"))
    assert "EXIT_AFTER below 2" in str(e.value)


def test_a_tick_limit_of_one_is_refused_at_build(h):
    """A cap of 1 ends the run on tick 1, before the tick probe's first row:
    the log would be counted at 1 beside an empty capture."""
    with pytest.raises(AssertionError) as e:
        h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1", "TICK_LIMIT=1"))
    assert "a TICK_LIMIT of 1 ends the run" in str(e.value)


# ------------------------------------------------------------ the check fails


def mutant(h: Harness, source: Path, old: str, new: str) -> Path:
    text = source.read_text()
    assert text.count(old) == 1, old
    out = h.work / f"mutant-{abs(hash((old, new)))}-{source.name}"
    out.write_text(text.replace(old, new))
    return out


@pytest.mark.parametrize("source, old, new", [
    (IMPORT_PROBE, "tick + 1 >= end", "tick >= end"),
    (IMPORT_PROBE, "tick + 1 >= end", "tick + 2 >= end"),
    (INPUT_PROBE, "const int done = last && tick >= last;",
     "const int done = last && tick > last;"),
    (INPUT_PROBE, "    return out ? planned_end() : 0;", "    return out ? planned_end() + 1 : 0;"),
    (INPUT_PROBE, "    return out ? planned_end() : 0;", "    return out ? planned_end() - 1 : 0;"),
], ids=["import-late", "import-early", "input-late", "export-late", "export-early"])
def test_a_probe_off_by_one_tick_fails_the_log_check(h, source, old, new):
    """Each mutant moves one probe's tick by one; the recording's run must then
    fail log_problems, which is what shows it can fail."""
    kw = {"import_source" if source == IMPORT_PROBE else "input_source":
          mutant(h, source, old, new)}
    got = h.run(importer=(INPUT_END,), inputer=("INPUT_MODE=1",), goal=40, **kw)
    assert log_problems(got) != []


# ------------------------------------------------------------ the Windows side


@pytest.fixture(scope="module")
def runner_builds(tmp_path_factory):
    """Both probes as a native recording builds them: run_oracle_probe's
    compile_probe (its zig, flags, -Werror, output name and export check),
    with its output directory moved to a temporary one, removed after."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    work = tmp_path_factory.mktemp("runner-build")
    mp = pytest.MonkeyPatch()
    mp.setattr(RP, "OUT", work)
    try:
        out = {}
        for mod, source in R.PROBES:
            if mod in ("import_probe", R.INPUT_MOD):
                defines = R.DEFINES.get(mod, []) + (["INPUT_MODE=1"] if mod == R.INPUT_MOD else [])
                out[mod] = RP.compile_probe(ROOT / source, mod, defines)[0]
    finally:
        mp.undo()
    yield out
    shutil.rmtree(work, ignore_errors=True)


def test_the_define_names_the_dll_the_runner_writes_for_the_input_probe(runner_builds):
    """GetModuleHandleA matches a loaded module by its file's base name, so
    the define must be the name compile_probe gave the input probe's DLL."""
    dll = runner_builds[R.INPUT_MOD]
    assert R.DEFINES["import_probe"] == [f'INPUT_END_DLL="{dll.name}"']


def test_the_input_probe_exports_the_end_tick_undecorated(runner_builds):
    """GetProcAddress looks up "SA2InputProbeEnd" exactly; a __stdcall export
    would be decorated and not found."""
    assert "SA2InputProbeEnd" in RP.dll_exports(runner_builds[R.INPUT_MOD])
    assert "SA2InputProbeEnd" not in RP.dll_exports(runner_builds["import_probe"])
