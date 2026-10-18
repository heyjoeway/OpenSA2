"""The force-hard-layout tweak (runtime/tweaks/force-hard-layout.c): LoadStageSETFile (0x00488F60)
picks the Hard Mode (mission 5) SET files in a one-player game whatever the mission, MissionNum
holds the picked mission again when the call returns, a stage whose Hard Mode layout leaves out an
upgrade the save does not have keeps its own layout, mission 5, a two-player game and a load by name
are left alone, and with the tweak off (or in a replay) the lifted body runs alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c and 2p-layout-in-1p.c,
which acts in this file's wrap of 0x00488F60, as test_tweaks.py builds no-event-bars, and linked
with a driver that stands in for the lifted body and the
runtime's peek and poke. The guest holds TwoPlayerMode at 0x0174AFDE, MissionNum at 0x0174AFE3,
CurrentLevel (a short) at 0x01934B70 and the save's upgrade flags from 0x01DEB300.

The stand-in `__real_sub_488f60` does what the lifted body does (0x00488F9F-0x004890A9): with
TwoPlayerMode non-zero it tries "set%04d_2p_s.bin" and "set%04d_2p_u.bin", with MissionNum 4
"_hd_s" and "_hd_u", else "_s" and "_u", and falls back to "_s" or "_u" for a name the
installation does not have. The installation's names are the real ones for Green Forest (3),
Metal Harbor (10), City Escape (13) and a boss stage with no Hard Mode files (19). Each driver step
prints "<the _s file> <the _u file> <MissionNum the body saw> <MissionNum after> <calls of the
body>".

The table of stages that keep their own layout is held to the game's files: for every stage with
Hard Mode SET files in the installation (private/install), the upgrade pedestals (LEVUPDAI) of the
pair the tweak loads and of the stage's own pair, each pedestal's upgrade read as the object's
main routine 0x006D8540 reads it from the image (private/exe/sonic2app-unpacked.exe).
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
from pathlib import Path

import iced_x86
import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
SETS = ROOT / "private" / "install" / "resource" / "gd_PC"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")

# argv: steps, each "<level>:<mission>:<TwoPlayerMode>" (mission 0-4, 4 is mission 5), or
#   noread     the three bytes stop reading for the steps after it
#   nowrite    MissionNum stops taking writes for the steps after it (it still reads)
#   nolevel    CurrentLevel stops reading for the steps after it
#   noupgrade  the upgrade flags stop reading for the steps after it
#   byname     ECX holds a base name's address for the steps after it (a Chao garden's load)
#   got=<hex>  the save has the upgrade whose byte is 0x01DEB300 + <hex>
#   shared     the steps after it call sa2_force_hard_layout_load_set with the body as `real`, as
#              a wrap another tweak shares would
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define TWO_P    0x0174AFDEu
#define MISSION  0x0174AFE3u
#define LEVEL    0x01934B70u
#define UPGRADES 0x01DEB300u
static uint8_t two_p, mission, upgrades[0x20];
static int16_t level;
static int readable = 1, writable = 1, level_readable = 1, upgrades_readable = 1;
static uint32_t gpr[SA2_GPR_COUNT];

static void *where(uint32_t a, uint32_t n) {
    if (!readable) return NULL;
    if (a == TWO_P && n == 1) return &two_p;
    if (a == MISSION && n == 1) return &mission;
    if (a == LEVEL && n == 2 && level_readable) return &level;
    if (a >= UPGRADES && a < UPGRADES + sizeof upgrades && n == 1 && upgrades_readable)
        return upgrades + (a - UPGRADES);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = where(a, n);
    if (!p || !writable) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

/* The installation's SET files for Green Forest, Metal Harbor, City Escape and Sonic vs Shadow 1,
 * matched without case as the game's file layer does. */
static const char *const FILES[] = {
    "set0003_s.bin", "set0003_u.bin", "set0003_hd_s.bin", "set0003_hd_u.bin",
    "set0010_s.bin", "set0010_u.bin", "set0010_2p_u.bin", "set0010_hd_s.bin", "set0010_hd_u.bin",
    "set0013_s.bin", "set0013_u.bin", "set0013_2p_s.bin", "set0013_2p_u.bin", "set0013_hd_s.bin",
    "set0019_s.bin", "set0019_u.bin",
};
static int present(const char *name) {
    for (unsigned i = 0; i < sizeof FILES / sizeof FILES[0]; i++)
        if (!strcasecmp(FILES[i], name)) return 1;
    return 0;
}
static char got_s[32], got_u[32];
static int saw, calls;
static void pick(char *out, const char *suffix, const char *fallback) {
    snprintf(out, 32, "set%04d%s", level, suffix);
    if (!present(out)) snprintf(out, 32, "set%04d%s", level, fallback);
}
void *__real_sub_488f60(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    saw = mission;
    calls++;
    if (two_p) { pick(got_s, "_2p_s.bin", "_s.bin"); pick(got_u, "_2p_u.bin", "_u.bin"); }
    else if (mission == 4) { pick(got_s, "_hd_s.bin", "_s.bin"); pick(got_u, "_hd_u.bin", "_u.bin"); }
    else { pick(got_s, "_s.bin", "_s.bin"); pick(got_u, "_u.bin", "_u.bin"); }
    return memory;
}
void *__wrap_sub_488f60(void *state, uint32_t pc, void *memory);
void *sa2_force_hard_layout_load_set(void *state, uint32_t pc, void *memory,
                                     void *(*real)(void *, uint32_t, void *));

int main(int argc, char **argv) {
    int token, shared = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "noread")) { readable = 0; continue; }
        if (!strcmp(argv[i], "nowrite")) { writable = 0; continue; }
        if (!strcmp(argv[i], "nolevel")) { level_readable = 0; continue; }
        if (!strcmp(argv[i], "noupgrade")) { upgrades_readable = 0; continue; }
        if (!strcmp(argv[i], "byname")) { gpr[SA2_ECX] = 0x0019FE00u; continue; }
        if (!strcmp(argv[i], "shared")) { shared = 1; continue; }
        if (!strncmp(argv[i], "got=", 4)) { upgrades[strtoul(argv[i] + 4, NULL, 16)] = 1; continue; }
        int l, m, t;
        if (sscanf(argv[i], "%d:%d:%d", &l, &m, &t) != 3) return 2;
        level = (int16_t)l; mission = (uint8_t)m; two_p = (uint8_t)t;
        calls = 0;
        void *r = shared ? sa2_force_hard_layout_load_set(&token, 0x00488F60u, &token, __real_sub_488f60)
                         : __wrap_sub_488f60(&token, 0x00488F60u, &token);
        if (r != &token) return 3;
        printf("%s %s %d %d %d\n", got_s, got_u, saw, mission, calls);
    }
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("tweak_force_hard_layout")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "force-hard-layout.c"),
                           str(RUNTIME / "tweaks" / "2p-layout-in-1p.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


ON = {"SA2_TWEAKS": "force-hard-layout"}
REPORT = "sa2 tweaks: hard layout (SA2_TWEAKS=force-hard-layout)"
LIGHT_SHOES = "got=00"      # Metal Harbor's pedestal's upgrade, 0x01DEB300

# City Escape and Metal Harbor, missions 1, 3 and 5, one player and two.
STEPS = ("13:0:0", "10:0:0", "13:2:0", "13:4:0", "10:4:0", "13:0:1")
GAME = ["set0013_s.bin set0013_u.bin 0 0 1",
        "set0010_s.bin set0010_u.bin 0 0 1",
        "set0013_s.bin set0013_u.bin 2 2 1",
        "set0013_hd_s.bin set0013_u.bin 4 4 1",
        "set0010_hd_s.bin set0010_hd_u.bin 4 4 1",
        "set0013_2p_s.bin set0013_2p_u.bin 0 0 1"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_says_nothing_of_the_layout(exe, env):
    out, err = run(exe, *STEPS, **env)
    assert out == GAME
    assert run(exe, LIGHT_SHOES, *STEPS, **env)[0] == GAME
    assert not [line for line in err if "hard layout" in line]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, LIGHT_SHOES, *STEPS, **ON, **{replay: "/dev/null"})
    assert out == GAME
    assert err == ["sa2 tweaks: SA2_TWEAKS=force-hard-layout ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_every_mission_loads_the_hard_mode_files_and_the_mission_comes_back(exe):
    out, err = run(exe, "13:0:0", "13:1:0", "13:2:0", "13:3:0", "19:0:0", **ON)
    assert out == ["set0013_hd_s.bin set0013_u.bin 4 0 1",    # no set0013_hd_u.bin: the game's fallback
                   "set0013_hd_s.bin set0013_u.bin 4 1 1",
                   "set0013_hd_s.bin set0013_u.bin 4 2 1",
                   "set0013_hd_s.bin set0013_u.bin 4 3 1",
                   "set0019_s.bin set0019_u.bin 4 0 1"]       # no Hard Mode files at all: its own
    assert err == [REPORT,
                   "sa2 tweaks: hard layout: stage 13 loads its Hard Mode object files where it has them",
                   "sa2 tweaks: hard layout: stage 19 loads its Hard Mode object files where it has them"]


def test_a_stage_whose_hard_layout_leaves_out_an_upgrade_waits_for_the_save_to_have_it(exe):
    out, err = run(exe, "10:0:0", "3:0:0", "10:1:0", LIGHT_SHOES, "10:0:0", "3:0:0", "got=01", "3:2:0",
                   **ON)
    assert out == ["set0010_s.bin set0010_u.bin 0 0 1",
                   "set0003_s.bin set0003_u.bin 0 0 1",
                   "set0010_s.bin set0010_u.bin 1 1 1",
                   "set0010_hd_s.bin set0010_hd_u.bin 4 0 1",   # the Light Shoes had
                   "set0003_s.bin set0003_u.bin 0 0 1",         # not Sonic's Ancient Light
                   "set0003_hd_s.bin set0003_hd_u.bin 4 2 1"]
    assert err == [REPORT,
                   "sa2 tweaks: hard layout: stage 10 keeps its own layout: its Hard Mode layout has no "
                   "upgrade pedestal, and this save does not have Sonic's Light Shoes yet",
                   "sa2 tweaks: hard layout: stage 3 keeps its own layout: its Hard Mode layout has no "
                   "upgrade pedestal, and this save does not have Sonic's Ancient Light yet"]


def test_mission_5_and_a_two_player_game_are_left_alone(exe):
    out, err = run(exe, "13:4:0", "10:4:0", "13:4:0", "13:0:1", "10:2:1", "13:0:0", **ON)
    assert out == [GAME[3], GAME[4], GAME[3], GAME[5], "set0010_s.bin set0010_2p_u.bin 2 2 1",
                   "set0013_hd_s.bin set0013_u.bin 4 0 1"]
    assert err == [REPORT,
                   "sa2 tweaks: hard layout: stage 13 is mission 5, which loads its Hard Mode layout already",
                   "sa2 tweaks: hard layout: stage 10 is mission 5, which loads its Hard Mode layout already",
                   "sa2 tweaks: hard layout: a two-player game keeps its two-player layout",
                   "sa2 tweaks: hard layout: stage 13 loads its Hard Mode object files where it has them"]


def test_memory_that_does_not_read_or_write_leaves_the_games_layout_and_says_so(exe):
    out, err = run(exe, "13:0:0", "nowrite", "13:1:0", "13:1:0", "noread", "13:0:0", **ON)
    assert out == ["set0013_hd_s.bin set0013_u.bin 4 0 1",
                   "set0013_s.bin set0013_u.bin 1 1 1",
                   "set0013_s.bin set0013_u.bin 1 1 1",
                   "set0013_s.bin set0013_u.bin 0 0 1"]
    assert err == [REPORT,
                   "sa2 tweaks: hard layout: stage 13 loads its Hard Mode object files where it has them",
                   "sa2 tweaks: hard layout: MissionNum (0x0174AFE3) does not write, so the stage loads "
                   "its own layout",
                   "sa2 tweaks: hard layout: TwoPlayerMode (0x0174AFDE) or MissionNum (0x0174AFE3) does "
                   "not read, so the stage loads its own layout"]
    out, err = run(exe, "noupgrade", "10:0:0", "13:0:0", **ON)
    assert out == ["set0010_s.bin set0010_u.bin 0 0 1", "set0013_hd_s.bin set0013_u.bin 4 0 1"]
    assert err[:2] == [REPORT, "sa2 tweaks: hard layout: the save's upgrade flag at 0x01DEB300 does not "
                               "read, so stage 10 keeps its own layout"]


def test_a_load_by_name_or_an_unread_level_keeps_its_own_layout_and_says_so(exe):
    out, err = run(exe, "byname", "13:0:0", "13:2:0", **ON)
    assert out == ["set0013_s.bin set0013_u.bin 0 0 1", "set0013_s.bin set0013_u.bin 2 2 1"]
    assert err == [REPORT, "sa2 tweaks: hard layout: a SET file loaded by name (ECX 0x0019FE00), not by "
                           "stage number, keeps its own layout"]
    out, err = run(exe, "nolevel", "13:0:0", "10:0:0", **ON)
    assert out == ["set0013_s.bin set0013_u.bin 0 0 1", "set0010_s.bin set0010_u.bin 0 0 1"]
    assert err == [REPORT, "sa2 tweaks: hard layout: CurrentLevel (0x01934B70) does not read, so the "
                           "stage loads its own layout"]


def test_each_stage_is_named_once_and_at_most_16(exe):
    levels = list(range(20, 40))
    out, err = run(exe, *[f"{lv}:0:0" for lv in levels] * 2, **ON)
    lost = {22: "Sonic's Flame Ring", 28: "Sonic's Bounce Bracelet", 32: "Knuckles' Sunglasses"}
    assert [row.split()[2:] for row in out] == [["0", "0", "1"] if lv in lost else ["4", "0", "1"]
                                                 for lv in levels * 2]
    named = [f"sa2 tweaks: hard layout: stage {lv} keeps its own layout: its Hard Mode layout has no upgrade "
             f"pedestal, and this save does not have {lost[lv]} yet" if lv in lost else
             f"sa2 tweaks: hard layout: stage {lv} loads its Hard Mode object files where it has them"
             for lv in levels[:16]]
    assert err == [REPORT] + named


def test_the_shared_entry_calls_the_real_it_is_given_once(exe):
    """sa2_force_hard_layout_load_set, as a wrap another tweak shares with this one calls it."""
    assert run(exe, "shared", "13:1:0", "13:4:0", "13:0:1")[0] == [GAME[2].replace(" 2 2 ", " 1 1 "),
                                                                  GAME[3], GAME[5]]
    out, _ = run(exe, "shared", "13:1:0", "10:0:0", **ON)
    assert out == ["set0013_hd_s.bin set0013_u.bin 4 1 1", "set0010_s.bin set0010_u.bin 0 0 1"]


BOTH = {"SA2_TWEAKS": "force-hard-layout,2p-layout-in-1p"}


def test_with_2p_layout_in_1p_on_too_one_call_of_the_body_takes_one_layout(exe):
    """2p-layout-in-1p acts in this file's wrap. A stage this tweak moves to its Hard Mode layout
    reaches 2p-layout-in-1p's half with MissionNum 4, which that tweak leaves to Hard Mode; on Metal
    Harbor without the Light Shoes this tweak keeps the stage's own layout and 2p-layout-in-1p loads
    the two-player files, as it does alone (the driver prints MissionNum, not TwoPlayerMode). Each
    load calls the body once, and with only 2p-layout-in-1p on this tweak's half says nothing."""
    out, err = run(exe, "13:0:0", "10:0:0", LIGHT_SHOES, "10:1:0", "13:0:1", **BOTH)
    assert out == ["set0013_hd_s.bin set0013_u.bin 4 0 1",
                   "set0010_s.bin set0010_2p_u.bin 0 0 1",
                   "set0010_hd_s.bin set0010_hd_u.bin 4 1 1",
                   "set0013_2p_s.bin set0013_2p_u.bin 0 0 1"]
    assert err == [
        "sa2 tweaks: 2p layout, hard layout (SA2_TWEAKS=force-hard-layout,2p-layout-in-1p)",
        "sa2 tweaks: 2p layout: stage 13, mission 5 keeps its Hard Mode layout",
        "sa2 tweaks: hard layout: stage 13 loads its Hard Mode object files where it has them",
        "sa2 tweaks: hard layout: stage 10 keeps its own layout: its Hard Mode layout has no upgrade "
        "pedestal, and this save does not have Sonic's Light Shoes yet",
        "sa2 tweaks: 2p layout: stage 10 loads its two-player object files where it has them",
        "sa2 tweaks: 2p layout: stage 10, mission 5 keeps its Hard Mode layout",
        "sa2 tweaks: hard layout: a two-player game keeps its two-player layout",
        "sa2 tweaks: 2p layout: a two-player game loads its two-player layout already"]
    out, err = run(exe, "13:0:0", "10:0:0", **{"SA2_TWEAKS": "2p-layout-in-1p"})
    assert out == ["set0013_2p_s.bin set0013_2p_u.bin 0 0 1", "set0010_s.bin set0010_2p_u.bin 0 0 1"]
    assert not [line for line in err if "hard layout" in line]


# ------------------------------------------------------------------ the game's files

# Each stage's loader is the Init of its level header ("STGnn", then field_8, then Init at +12);
# the loader pushes its object list's head before its call to 0x00487E40.
SET_OBJECTS = 0x00487E40
PEDESTAL_ROWS = 0x00B35390           # 36 bytes a row; +0x0C, the upgrade's byte past UPGRADES
UPGRADES = 0x01DEB300
LOST = {3: 0x01, 4: 0x10, 6: 0x11, 10: 0x00, 12: 0x15, 16: 0x0E, 18: 0x1C, 22: 0x03, 28: 0x04, 32: 0x0B}


class Image:
    """The unpacked image's sections, read by virtual address."""

    def __init__(self, path: Path):
        self.data = path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        nsec, opt_size = struct.unpack_from("<H", self.data, pe + 6)[0], struct.unpack_from("<H", self.data, pe + 20)[0]
        self.base = struct.unpack_from("<I", self.data, pe + 24 + 28)[0]
        self.sections = []
        for i in range(nsec):
            o = pe + 24 + opt_size + 40 * i
            vsize, va, rsize, raw = struct.unpack_from("<IIII", self.data, o + 8)
            self.sections.append((self.base + va, max(vsize, rsize), raw, rsize))

    def read(self, va: int, n: int) -> bytes:
        for start, size, raw, rsize in self.sections:
            if start <= va and va + n <= start + size:
                off = va - start
                return self.data[raw + off:raw + off + n].ljust(n, b"\0") if off < rsize else b"\0" * n
        raise AssertionError(f"0x{va:08X} is in no section")

    def u32(self, va: int) -> int:
        return struct.unpack("<I", self.read(va, 4))[0]

    def cstr(self, va: int) -> str:
        return self.read(va, 32).split(b"\0")[0].decode("latin-1").strip() if va else ""


def loaders(image: Image) -> dict[int, int]:
    """Each stage's loader, from its level header."""
    out = {}
    for m in re.finditer(rb"STG(\d\d)   ", image.data):
        init = struct.unpack_from("<I", image.data, m.start() + 12)[0]
        if 0x00401000 <= init < 0x00870600:
            out.setdefault(int(m.group(1)), init)
    return out


def stage_list(image: Image, init: int) -> list[str]:
    """The names of the object list the loader hands 0x00487E40, in id order: the last
    `push imm32` before its call, the loader decoded from its start."""
    head = None
    last = None
    for ins in iced_x86.Decoder(32, image.read(init, 0x400), ip=init):
        if ins.mnemonic == iced_x86.Mnemonic.PUSH and ins.op0_kind == iced_x86.OpKind.IMMEDIATE32:
            last = ins.immediate32
        elif ins.mnemonic == iced_x86.Mnemonic.CALL and ins.near_branch_target == SET_OBJECTS:
            head = last
            break
    assert head, f"0x{init:08X} calls no 0x{SET_OBJECTS:08X} after a push"
    count, entries = image.u32(head), image.u32(head + 4)
    return [image.cstr(image.u32(entries + 16 * i + 12)) for i in range(count)]


def pedestals(image: Image, names: list[str], files: dict[str, Path], pair: list[str]) -> list[int]:
    """The upgrade byte (past UPGRADES) of each LEVUPDAI record in the pair, as 0x006D85DE-0x006D8649
    reads it: int(Scale.x), low byte, modulo 28, the row's word at +0x0C."""
    out = []
    for name in pair:
        data = files[name].read_bytes()
        for i in range(struct.unpack(">I", data[:4])[0]):
            rec = data[32 + 32 * i:64 + 32 * i]
            oid = struct.unpack(">H", rec[:2])[0] & 0x7FFF
            if oid < len(names) and names[oid] == "LEVUPDAI":
                row = (int(struct.unpack(">f", rec[20:24])[0]) & 0xFF) % 28
                out.append(image.u32(PEDESTAL_ROWS + 36 * row + 0x0C))
    return out


@pytest.fixture(scope="module")
def game():
    for need in (EXE, SETS):
        if not need.exists():
            pytest.fail(f"the game's files are not present: {need}")
    return Image(EXE), {p.name.lower(): p for p in SETS.iterdir()}


def test_the_pedestal_reads_its_upgrade_as_the_table_says(game):
    image, _ = game
    # fld [ebx+0x20] (Scale.x) ... mov ecx,0x1C ... idiv ecx; the row's +0x0C word; cmp [eax+UPGRADES],0
    assert image.read(0x006D85DE, 3) == bytes.fromhex("d94320")
    assert image.read(0x006D85E1, 5) == bytes.fromhex("b91c000000")
    assert image.read(0x006D8609, 7) == bytes.fromhex("8d0495") + struct.pack("<I", PEDESTAL_ROWS)
    assert image.read(0x006D8646, 3) == bytes.fromhex("8b420c")
    assert image.read(0x006D8649, 7) == bytes.fromhex("80b8") + struct.pack("<I", UPGRADES) + b"\0"


def test_the_choice_is_where_the_header_says(game):
    image, _ = game
    assert image.read(0x00489036, 9) == bytes.fromhex("803de3af740104754c")      # cmp [MissionNum],4; jne
    assert image.read(0x00488F9F, 7) == bytes.fromhex("803ddeaf740100")          # cmp [TwoPlayerMode],0
    for va, s in ((0x008B97BC, "%s_hd_s.bin"), (0x008B97C8, "%s_hd_u.bin"), (0x008B9798, "%s_s.bin"),
                  (0x008B97B0, "%s_u.bin"), (0x008B9784, "set%04d")):
        assert image.cstr(va) == s


def test_every_call_is_direct_and_only_one_passes_a_name(game):
    image, _ = game
    text_start, text_size = 0x00401000, 0x0046F600                           # .text
    text = image.read(text_start, text_size)
    calls = [text_start + i for i in range(text_size - 5) if text[i] == 0xE8
             and (text_start + i + 5 + struct.unpack_from("<i", text, i + 1)[0]) & 0xFFFFFFFF == 0x00488F60]
    assert len(calls) == 51
    assert struct.pack("<I", 0x00488F60) not in image.data
    by_name = [va for va in calls if image.read(va - 2, 2) != b"\x33\xc9"]   # xor ecx,ecx just before
    assert by_name == [0x0054B1CA]


def test_the_table_is_the_stages_whose_hard_layout_leaves_out_an_upgrade(game):
    image, files = game
    hard = sorted({int(n[3:7]) for n in files if re.fullmatch(r"set00\d\d_hd_[su]\.bin", n)})
    assert len(hard) == 33
    init = loaders(image)
    lost = {}
    for stage in hard:
        names = stage_list(image, init[stage])
        own = [f"set{stage:04d}_{x}.bin" for x in "su"]
        pick = [f"set{stage:04d}_hd_{x}.bin" if f"set{stage:04d}_hd_{x}.bin" in files
                else f"set{stage:04d}_{x}.bin" for x in "su"]           # the game's fallback
        mine, theirs = pedestals(image, names, files, own), pedestals(image, names, files, pick)
        assert len(mine) <= 1 and len(theirs) <= 1, stage
        if mine and not theirs:
            lost[stage] = mine[0]
        else:
            assert mine == theirs, stage
    assert lost == LOST


def test_the_tweak_files_table_is_the_test_table():
    text = (RUNTIME / "tweaks" / "force-hard-layout.c").read_text()
    rows = re.findall(r"\{(\d+), 0x([0-9A-F]{2}), \"", text)
    assert {int(lv): int(flag, 16) for lv, flag in rows} == LOST
