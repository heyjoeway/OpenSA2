"""The 2p-layout-in-1p tweak (runtime/tweaks/2p-layout-in-1p.c): LoadStageSETFile (0x00488F60)
picks the two-player SET files in a one-player game, Hard Mode and a two-player game are left
alone, TwoPlayerMode is back to its value when the call returns, and with the tweak off (or in a
replay) the lifted body runs alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted body and the
runtime's peek and poke. The guest holds TwoPlayerMode at 0x0174AFDE, MissionNum at 0x0174AFE3 and
CurrentLevel (a short) at 0x01934B70.

The stand-in `__real_sub_488f60` does what the lifted body does (0x00488F9F-0x004890A9): with
TwoPlayerMode non-zero it tries "set%04d_2p_s.bin" and "set%04d_2p_u.bin", with MissionNum 4
"_hd_s" and "_hd_u", else "_s" and "_u", and falls back to "_s" or "_u" for a name the
installation does not have. The installation's names are the real ones for two stages: Metal
Harbor (10) has a two-player _u file and no two-player _s file, City Escape (13) has both, and both
have Hard Mode files; Pumpkin Hill (5) and Iron Gate (11), the two stages the tweak leaves alone,
are there too. Each driver step prints "<the _s file> <the _u file> <TwoPlayerMode the body saw>
<TwoPlayerMode after>".

The stages the wrap lets through are held to the game's files: for every one-player stage with a
two-player SET file in the installation (private/install), the pair the swap would load must have
every object id inside the object list the stage's loader passes to 0x00487E40 (read from
private/exe/sonic2app-unpacked.exe) and keep a GOALRING where the stage's own pair has one. Iron
Gate's set0011_2p_s.bin fails the first (38 records of id 92 against a 92-entry list), Pumpkin
Hill's two-player pair the second.
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
SETS = ROOT / "private" / "install" / "resource" / "gd_PC"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")

# argv: steps, each "<level>:<mission>:<TwoPlayerMode>" (mission 0-4, 4 is Hard Mode), or
#   noread   the three bytes stop reading for the steps after it
#   nowrite  TwoPlayerMode stops taking writes for the steps after it (it still reads)
#   nolevel  CurrentLevel stops reading for the steps after it (the two bytes still read)
#   byname   ECX holds a base name's address for the steps after it (a Chao garden's load)
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define TWO_P   0x0174AFDEu
#define MISSION 0x0174AFE3u
#define LEVEL   0x01934B70u
static uint8_t two_p, mission;
static int16_t level;
static int readable = 1, writable = 1, level_readable = 1;
static uint32_t gpr[SA2_GPR_COUNT];

static void *where(uint32_t a, uint32_t n) {
    if (!readable) return NULL;
    if (a == TWO_P && n == 1) return &two_p;
    if (a == MISSION && n == 1) return &mission;
    if (a == LEVEL && n == 2 && level_readable) return &level;
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

/* The installation's SET files for Pumpkin Hill, Metal Harbor, Iron Gate and City Escape, matched
 * without case as the game's file layer does. */
static const char *const FILES[] = {
    "set0005_s.bin", "set0005_u.bin", "set0005_2P_s.bin", "set0005_2P_u.bin", "set0005_hd_s.bin",
    "set0010_s.bin", "set0010_u.bin", "set0010_2p_u.bin", "set0010_hd_s.bin", "set0010_hd_u.bin",
    "set0011_s.bin", "set0011_u.bin", "set0011_2p_s.bin", "set0011_hd_s.bin", "set0011_hd_u.bin",
    "set0013_s.bin", "set0013_u.bin", "set0013_2p_s.bin", "set0013_2p_u.bin", "set0013_hd_s.bin",
};
static int present(const char *name) {
    for (unsigned i = 0; i < sizeof FILES / sizeof FILES[0]; i++)
        if (!strcasecmp(FILES[i], name)) return 1;
    return 0;
}
static char got_s[32], got_u[32];
static int saw;
static void pick(char *out, const char *suffix, const char *fallback) {
    snprintf(out, 32, "set%04d%s", level, suffix);
    if (!present(out)) snprintf(out, 32, "set%04d%s", level, fallback);
}
void *__real_sub_488f60(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    saw = two_p;
    if (two_p) { pick(got_s, "_2p_s.bin", "_s.bin"); pick(got_u, "_2p_u.bin", "_u.bin"); }
    else if (mission == 4) { pick(got_s, "_hd_s.bin", "_s.bin"); pick(got_u, "_hd_u.bin", "_u.bin"); }
    else { pick(got_s, "_s.bin", "_s.bin"); pick(got_u, "_u.bin", "_u.bin"); }
    return memory;
}
void *__wrap_sub_488f60(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int token;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "noread")) { readable = 0; continue; }
        if (!strcmp(argv[i], "nowrite")) { writable = 0; continue; }
        if (!strcmp(argv[i], "nolevel")) { level_readable = 0; continue; }
        if (!strcmp(argv[i], "byname")) { gpr[SA2_ECX] = 0x0019FE00u; continue; }
        int l, m, t;
        if (sscanf(argv[i], "%d:%d:%d", &l, &m, &t) != 3) return 2;
        level = (int16_t)l; mission = (uint8_t)m; two_p = (uint8_t)t;
        if (__wrap_sub_488f60(&token, 0x00488F60u, &token) != &token) return 3;
        printf("%s %s %d %d\n", got_s, got_u, saw, two_p);
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
    d = tmp_path_factory.mktemp("tweak_2p_layout")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
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


ON = {"SA2_TWEAKS": "2p-layout-in-1p"}
REPORT = "sa2 tweaks: 2p layout (SA2_TWEAKS=2p-layout-in-1p)"

# Metal Harbor and City Escape, missions 1 and 5, one player and two.
STEPS = ("10:0:0", "13:0:0", "10:4:0", "13:4:0", "10:0:1", "13:0:1")
GAME = ["set0010_s.bin set0010_u.bin 0 0",
        "set0013_s.bin set0013_u.bin 0 0",
        "set0010_hd_s.bin set0010_hd_u.bin 0 0",
        "set0013_hd_s.bin set0013_u.bin 0 0",
        "set0010_s.bin set0010_2p_u.bin 1 1",
        "set0013_2p_s.bin set0013_2p_u.bin 1 1"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_says_nothing_of_the_layout(exe, env):
    out, err = run(exe, *STEPS, **env)
    assert out == GAME
    assert not [line for line in err if "2p layout" in line]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *STEPS, **ON, **{replay: "/dev/null"})
    assert out == GAME
    assert err == ["sa2 tweaks: SA2_TWEAKS=2p-layout-in-1p ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_a_one_player_stage_loads_the_two_player_files_and_mode_goes_back_to_0(exe):
    out, err = run(exe, "10:0:0", "13:1:0", "13:3:0", "10:2:0", **ON)
    assert out == ["set0010_s.bin set0010_2p_u.bin 1 0",       # no set0010_2p_s.bin: the game's fallback
                   "set0013_2p_s.bin set0013_2p_u.bin 1 0",
                   "set0013_2p_s.bin set0013_2p_u.bin 1 0",
                   "set0010_s.bin set0010_2p_u.bin 1 0"]
    assert err == [REPORT,
                   "sa2 tweaks: 2p layout: stage 10 loads its two-player object files where it has them",
                   "sa2 tweaks: 2p layout: stage 13 loads its two-player object files where it has them"]


def test_hard_mode_keeps_its_layout(exe):
    out, err = run(exe, "10:4:0", "13:4:0", "10:4:0", "10:0:0", **ON)
    assert out == ["set0010_hd_s.bin set0010_hd_u.bin 0 0",
                   "set0013_hd_s.bin set0013_u.bin 0 0",
                   "set0010_hd_s.bin set0010_hd_u.bin 0 0",
                   "set0010_s.bin set0010_2p_u.bin 1 0"]
    assert err == [REPORT,
                   "sa2 tweaks: 2p layout: stage 10, mission 5 keeps its Hard Mode layout",
                   "sa2 tweaks: 2p layout: stage 13, mission 5 keeps its Hard Mode layout",
                   "sa2 tweaks: 2p layout: stage 10 loads its two-player object files where it has them"]


def test_a_two_player_game_is_left_alone(exe):
    out, err = run(exe, "10:0:1", "13:4:1", **ON)
    assert out == GAME[4:5] + ["set0013_2p_s.bin set0013_2p_u.bin 1 1"]
    assert err == [REPORT, "sa2 tweaks: 2p layout: a two-player game loads its two-player layout already"]


def test_memory_that_does_not_read_or_write_leaves_the_games_layout_and_says_so(exe):
    out, err = run(exe, "10:0:0", "nowrite", "13:0:0", "13:0:0", "noread", "10:0:0", **ON)
    assert out == ["set0010_s.bin set0010_2p_u.bin 1 0",
                   "set0013_s.bin set0013_u.bin 0 0",
                   "set0013_s.bin set0013_u.bin 0 0",
                   "set0010_s.bin set0010_u.bin 0 0"]
    assert err == [REPORT,
                   "sa2 tweaks: 2p layout: stage 10 loads its two-player object files where it has them",
                   "sa2 tweaks: 2p layout: TwoPlayerMode (0x0174AFDE) does not write, so the stage "
                   "loads its own layout",
                   "sa2 tweaks: 2p layout: TwoPlayerMode (0x0174AFDE) or MissionNum (0x0174AFE3) does "
                   "not read, so the stage loads its own layout"]


# The one-player stages whose two-player layout the wrap loads, and the two it leaves alone.
LOADS = {3, 4, 6, 8, 10, 13, 14, 25, 26, 30, 32, 40, 44}
KEEPS = {5: "its two-player files place no goal ring",
         11: "its two-player file is not for this stage (object ids past the stage's object list, "
             "no goal ring)"}


def line(level: int) -> str:
    if level in LOADS:
        return f"sa2 tweaks: 2p layout: stage {level} loads its two-player object files where it has them"
    if level in KEEPS:
        return f"sa2 tweaks: 2p layout: stage {level} keeps its own layout: {KEEPS[level]}"
    return (f"sa2 tweaks: 2p layout: stage {level} has no two-player layout checked for one player, "
            f"so it loads its own")


def test_each_stage_is_named_once_and_at_most_16(exe):
    steps = [f"{lv}:0:0" for lv in range(3, 23)] * 2
    out, err = run(exe, *steps, **ON)
    assert len(out) == 40
    assert [line_.split()[2:] for line_ in out] == [["1", "0"] if lv in LOADS else ["0", "0"]
                                                     for lv in list(range(3, 23)) * 2]
    assert err == [REPORT] + [line(lv) for lv in range(3, 19)]


def test_the_wrap_swaps_exactly_the_checked_stages(exe):
    levels = range(0, 71)
    out, err = run(exe, *[f"{lv}:0:0" for lv in levels], **ON)
    assert {lv for lv, row in zip(levels, out) if row.split()[2] == "1"} == LOADS
    assert all(row.endswith(" 0") for row in out)


def test_iron_gate_and_pumpkin_hill_keep_their_own_layout_and_say_why(exe):
    out, err = run(exe, "11:0:0", "5:1:0", "11:2:0", "13:0:0", **ON)
    assert out == ["set0011_s.bin set0011_u.bin 0 0",       # not set0011_2p_s.bin
                   "set0005_s.bin set0005_u.bin 0 0",       # not set0005_2P_s/u
                   "set0011_s.bin set0011_u.bin 0 0",
                   "set0013_2p_s.bin set0013_2p_u.bin 1 0"]
    assert err == [REPORT,
                   "sa2 tweaks: 2p layout: stage 11 keeps its own layout: its two-player file is not "
                   "for this stage (object ids past the stage's object list, no goal ring)",
                   "sa2 tweaks: 2p layout: stage 5 keeps its own layout: its two-player files place "
                   "no goal ring",
                   "sa2 tweaks: 2p layout: stage 13 loads its two-player object files where it has them"]


def test_a_two_player_game_on_iron_gate_is_still_the_games(exe):
    out, _ = run(exe, "11:0:1", **ON)
    assert out == ["set0011_2p_s.bin set0011_u.bin 1 1"]


def test_a_load_by_name_or_an_unread_level_keeps_its_own_layout_and_says_so(exe):
    out, err = run(exe, "byname", "10:0:0", "13:0:0", **ON)
    assert out == ["set0010_s.bin set0010_u.bin 0 0", "set0013_s.bin set0013_u.bin 0 0"]
    assert err == [REPORT, "sa2 tweaks: 2p layout: a SET file loaded by name (ECX 0x0019FE00), not by "
                           "stage number, keeps its own layout"]
    out, err = run(exe, "nolevel", "10:0:0", "13:0:0", **ON)
    assert out == ["set0010_s.bin set0010_u.bin 0 0", "set0013_s.bin set0013_u.bin 0 0"]
    assert err == [REPORT, "sa2 tweaks: 2p layout: CurrentLevel (0x01934B70) does not read, so the "
                           "stage loads its own layout"]


# The game's files. Each one-player stage with two-player SET files: the `push imm32` of its object
# list's head before its loader's call to 0x00487E40, next or after one `mov [imm32],eax` (SA Tools'
# ObjectLists_data.ini names the same pushes). The versus stages load only in a two-player game, which the wrap leaves alone.
LIST_PUSH = {3: 0x005ED1F2, 4: 0x0062EA92, 5: 0x004B3855, 6: 0x005B9912, 8: 0x006345A2,
             10: 0x006F299F, 11: 0x00699423, 13: 0x005DCF67, 14: 0x004DE9D8, 25: 0x006AA294,
             26: 0x0068F984, 30: 0x004AC938, 32: 0x005BDFD5, 40: 0x004E6DB3, 44: 0x0064E2AE}
VERSUS = {15, 39, 41, *range(45, 60)}
SET_OBJECTS = 0x00487E40


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


def stage_list(image: Image, stage: int) -> list[str]:
    """The names of the object list the stage's loader hands 0x00487E40, in id order."""
    push = LIST_PUSH[stage]
    call = push + 10 if image.read(push + 5, 1) == b"\xA3" else push + 5   # past a mov [imm32],eax
    assert image.read(push, 1) == b"\x68" and image.read(call, 1) == b"\xE8", f"stage {stage}"
    assert (call + 5 + struct.unpack("<i", image.read(call + 1, 4))[0]) & 0xFFFFFFFF == SET_OBJECTS, \
        f"stage {stage}"
    head = image.u32(push + 1)
    count, entries = image.u32(head), image.u32(head + 4)
    return [image.cstr(image.u32(entries + 16 * i + 12)) for i in range(count)]


def set_ids(files: dict[str, Path], name: str) -> list[int]:
    data = files[name.lower()].read_bytes()
    count = struct.unpack(">I", data[:4])[0]
    assert len(data) == 32 + 32 * count, name
    return [struct.unpack_from(">H", data, 32 + 32 * i)[0] & 0x7FFF for i in range(count)]   # 0x00488462


@pytest.fixture(scope="module")
def game():
    for need in (EXE, SETS):
        if not need.exists():
            pytest.fail(f"the game's files are not present: {need}")
    return Image(EXE), {p.name.lower(): p for p in SETS.iterdir()}


def test_the_table_is_the_stages_whose_two_player_files_fit(game):
    image, files = game
    with_2p = {int(n[3:7]) for n in files if n.startswith("set00") and "_2p_" in n}
    assert with_2p - VERSUS == set(LIST_PUSH)
    fits, why = set(), {}
    for stage in sorted(LIST_PUSH):
        names = stage_list(image, stage)
        pick = [f"set{stage:04d}_2p_{x}.bin" if f"set{stage:04d}_2p_{x}.bin" in files
                else f"set{stage:04d}_{x}.bin" for x in "su"]           # the game's fallback
        mix = set_ids(files, pick[0]) + set_ids(files, pick[1])
        own = set_ids(files, f"set{stage:04d}_s.bin") + set_ids(files, f"set{stage:04d}_u.bin")
        past = sorted({i for i in mix if i >= len(names)})
        goal = "GOALRING" in {names[i] for i in mix if i < len(names)}
        own_goal = "GOALRING" in {names[i] for i in own}
        if not past and (goal or not own_goal):
            fits.add(stage)
        else:
            why[stage] = (pick, past, goal)
    assert fits == LOADS
    assert why == {5: (["set0005_2p_s.bin", "set0005_2p_u.bin"], [], False),
                   11: (["set0011_2p_s.bin", "set0011_u.bin"], [92], False)}
    assert len(stage_list(image, 11)) == 92
    assert set_ids(files, "set0011_2p_s.bin").count(92) == 38
