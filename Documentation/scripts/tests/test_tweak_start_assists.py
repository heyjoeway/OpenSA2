"""start-assists (runtime/tweaks/start-assists.c): after the lifted body of 0x0044C1A0 each frame,
player 1's Powerups gets invincibility and both barriers (0x8003: the plain barrier's bit so its
item box returns at once, as the other two do) and Upgrades the character's own bits, found
through MainCharacter[0] (0x01DEA6E0) -> task+0x40 -> CharObj2Base.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted function and the
runtime's accessors over a fake guest heap. The stand-in `__real_sub_44c1a0` counts its calls and
leaves EAX as the lifted body would (its frame time is on the x87 stack, which no test can see
here, so a register stands for it); the driver's sa2_peek and sa2_poke count every guest access,
so "off" can be shown to read and write nothing.

argv is a list of commands applied in order: `task=`, `cp=` (the word at task+0x40), `char=ID,ID2`,
`pw=`, `up=`, `level=`, `two=` (TwoPlayerMode) set the fake heap; `frame` runs the wrap once and
prints "<Powerups> <Upgrades> <real calls> <peeks> <pokes> <EAX>" (hex, counters since the last
frame).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
TASK, CP = 0x00100000, 0x00100100           # player 1's task and CharObj2Base in the fake heap
UNMAPPED = 0x0F000000

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

/* The guest addresses the tweak touches, each a small mapped region; anything else fails. */
static struct { uint32_t base, size; uint8_t bytes[0x200]; } region[] = {
    {0x01DEA6E0u, 4, {0}},       /* MainCharacter[0] */
    {0x01934B70u, 2, {0}},       /* CurrentLevel */
    {0x0174AFDEu, 1, {0}},       /* TwoPlayerMode */
    {0x00100000u, 0x200, {0}},   /* the heap: the task at +0, its CharObj2Base at +0x100 */
};
static unsigned peeks, pokes, reals;
static uint32_t gpr[8];

static uint8_t *at(uint32_t a, uint32_t n) {
    for (unsigned i = 0; i < sizeof region / sizeof region[0]; i++)
        if (a >= region[i].base && a + n <= region[i].base + region[i].size)
            return region[i].bytes + (a - region[i].base);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    peeks++;
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    pokes++;
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_44c1a0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reals++;
    gpr[SA2_EAX] = 0x00C0FFEEu;                /* the lifted body's EAX, -[0x01DEB694] */
    return memory;
}
void *__wrap_sub_44c1a0(void *state, uint32_t pc, void *memory);

static void put(uint32_t a, uint32_t n, uint32_t v) {
    uint8_t *p = at(a, n);
    if (!p) { fprintf(stderr, "driver: 0x%08X is not mapped\n", a); exit(2); }
    memcpy(p, &v, n);
}
static uint32_t get(uint32_t a, uint32_t n) { uint32_t v = 0; memcpy(&v, at(a, n), n); return v; }

int main(int argc, char **argv) {
    uint32_t task = 0, cp = 0;
    for (int i = 1; i < argc; i++) {
        char *eq = strchr(argv[i], '=');
        const uint32_t v = eq ? (uint32_t)strtoul(eq + 1, NULL, 0) : 0;
        if (!strncmp(argv[i], "task=", 5)) { task = v; put(0x01DEA6E0u, 4, v); }
        else if (!strncmp(argv[i], "cp=", 3)) { cp = v; put(task + 0x40u, 4, v); }
        else if (!strncmp(argv[i], "char=", 5)) {
            put(cp + 1u, 1, v);
            put(cp + 3u, 1, (uint32_t)strtoul(strchr(eq, ',') + 1, NULL, 0));
        }
        else if (!strncmp(argv[i], "pw=", 3)) put(cp + 0x10u, 2, v);
        else if (!strncmp(argv[i], "up=", 3)) put(cp + 0x24u, 4, v);
        else if (!strncmp(argv[i], "level=", 6)) put(0x01934B70u, 2, v);
        else if (!strncmp(argv[i], "two=", 4)) put(0x0174AFDEu, 1, v);
        else if (!strcmp(argv[i], "frame")) {
            peeks = pokes = reals = 0;
            gpr[SA2_EAX] = 0xDEADBEEFu;
            int token;
            if (__wrap_sub_44c1a0(&token, 0x0044C1A0u, &token) != &token) return 3;
            const int heap = cp >= 0x00100000u && cp + 0x28u <= 0x00100200u;
            printf("%04X %08X %u %u %u %08X\n", heap ? get(cp + 0x10u, 2) : 0, heap ? get(cp + 0x24u, 4) : 0,
                   reals, peeks, pokes, gpr[SA2_EAX]);
        }
        else { fprintf(stderr, "driver: what is %s\n", argv[i]); return 2; }
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("start_assists")
    (d / "driver.c").write_text(DRIVER)
    out = d / "sa"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "start-assists.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *cmds: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *cmds], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def player(char: str, pw: int = 0, up: int = 0, level: int = 13) -> list[str]:
    return [f"task={TASK:#x}", f"cp={CP:#x}", f"char={char}", f"pw={pw:#x}", f"up={up:#x}",
            f"level={level}"]


ON = {"SA2_TWEAKS": "start-assists"}
REPORT = "sa2 tweaks: start assists (SA2_TWEAKS=start-assists)"


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_TWEAKS": "start-assists", "SA2_REPLAY_TICKS": "/dev/null"}])
def test_off_calls_the_lifted_body_and_touches_no_guest_memory(exe, env):
    out, err = run(exe, *player("0,0", pw=0x0001, up=0x5), "frame", "frame", **env)
    assert out == ["0001 00000005 1 0 0 00C0FFEE"] * 2
    assert not [line for line in err if "start assists" in line]


def test_sonic_gets_the_barrier_invincibility_and_his_own_upgrades_once(exe):
    out, err = run(exe, *player("0,0"), "frame", "frame", **ON)
    # The first frame writes both words; the next reads them and writes nothing.
    assert out == ["8003 0000003F 1 7 2 00C0FFEE", "8003 0000003F 1 7 0 00C0FFEE"]
    assert err == [REPORT,
                   "sa2 tweaks: start assists: Sonic in level 13: Powerups 0x0000 -> 0x8003, "
                   "Upgrades 0x00000000 -> 0x0000003F"]


def test_bits_already_there_are_kept_and_a_lost_barrier_comes_back(exe):
    # Super Sonic's bit and another Powerups bit stay; a hit that clears both barriers is undone.
    out, _ = run(exe, *player("0,0", pw=0x0010, up=0x8010), "frame", "pw=0x8000", "frame", **ON)
    assert out == ["8013 0000803F 1 7 2 00C0FFEE", "8003 0000803F 1 7 1 00C0FFEE"]


@pytest.mark.parametrize("pw, said", [
    # A plain barrier from a Barrier box, as AwardBarrier stores it ((pw & ~2) | 1): the magnetic
    # barrier and invincibility are added and the plain barrier's bit kept.
    (0x0001, "0x0001 -> 0x8003"),
    # The magnetic barrier alone: AwardBarrier tests 0x0001 alone (0x0046E306), so that bit is added
    # too, or a Barrier box would still spawn its bubble and voice line.
    (0x0002, "0x0002 -> 0x8003"),
    (0x8002, "0x8002 -> 0x8003"),
])
def test_every_item_box_bit_is_held_so_each_award_returns_at_once(exe, pw, said):
    out, err = run(exe, *player("0,0", pw=pw), "frame", "frame", **ON)
    assert out == ["8003 0000003F 1 7 2 00C0FFEE", "8003 0000003F 1 7 0 00C0FFEE"]
    assert err[1] == (f"sa2 tweaks: start assists: Sonic in level 13: Powerups {said}, "
                      "Upgrades 0x00000000 -> 0x0000003F")


def test_the_android_note_says_what_the_save_keeps():
    # The granted bits are never saved, but what they reach is (upgrade pedestals: Got bytes, kept
    # by the save at 0x0044371B), so the player's note has to say so, not "nothing is saved".
    java = (ROOT / "host" / "android" / "java" / "com" / "sa2recomp" / "app" /
            "GameSettings.java").read_text()
    line = next(x for x in java.splitlines() if 'new Tweak("start-assists"' in x)
    assert "upgrades you pick up" in line and "Lost Chao" in line and "emblems" in line, line
    assert "Nothing is added to your save" not in line, line


@pytest.mark.parametrize("char, name, own", [
    ("1,1", "Shadow", 0x000F0000), ("4,4", "Knuckles", 0x00007C00), ("5,5", "Rouge", 0x1E000000),
    ("6,6", "Mech Tails", 0x000003C0), ("7,7", "Mech Eggman", 0x01F00000)])
def test_each_character_gets_only_its_own_upgrades(exe, char, name, own):
    out, err = run(exe, *player(char, level=20), "frame", **ON)
    assert out == [f"8003 {own:08X} 1 7 2 00C0FFEE"]
    assert err[1] == (f"sa2 tweaks: start assists: {name} in level 20: Powerups 0x0000 -> 0x8003, "
                      f"Upgrades 0x00000000 -> 0x{own:08X}")


@pytest.mark.parametrize("char, name", [
    ("0,8", "Amy"), ("1,12", "Metal Sonic"), ("6,13", "Chao Walker"), ("7,14", "Dark Chao Walker"),
    ("4,15", "Tikal"), ("5,16", "Chaos"), ("0,9", "Super Sonic"), ("2,2", "Tails"), ("0,40", "character 40")])
def test_a_character_without_its_own_set_gets_no_upgrades_and_it_is_said(exe, char, name):
    out, err = run(exe, *player(char, up=0x4), "frame", **ON)
    assert out == ["8003 00000004 1 7 1 00C0FFEE"]
    assert err[1] == (f"sa2 tweaks: start assists: {name} in level 13: Powerups 0x0000 -> 0x8003, "
                      f"upgrades left alone ({name} has none of its own)")


def test_null_pointers_are_skipped_without_a_write(exe):
    out, err = run(exe, "task=0", "frame", f"task={TASK:#x}", "cp=0", "frame", **ON)
    assert out == ["0000 00000000 1 1 0 00C0FFEE", "0000 00000000 1 2 0 00C0FFEE"]
    assert err == [REPORT]


def test_two_player_mode_is_left_alone_and_said_once(exe):
    out, err = run(exe, *player("0,0"), "two=1", "frame", "frame", **ON)
    assert out == ["0000 00000000 1 3 0 00C0FFEE"] * 2
    assert err == [REPORT, "sa2 tweaks: start assists: two-player mode is left alone"]


def test_an_unreadable_character_is_left_alone_and_said_once(exe):
    out, err = run(exe, f"task={TASK:#x}", f"cp={UNMAPPED:#x}", "frame", "frame", **ON)
    assert out == ["0000 00000000 1 4 0 00C0FFEE"] * 2
    assert err == [REPORT, "sa2 tweaks: start assists: player 1's CharObj2Base at 0x0F000000 does "
                           "not read or write, so this frame is left alone"]


def test_each_character_and_level_is_named_once(exe):
    _, err = run(exe, *player("0,0"), "frame", "level=14", "frame", "level=13", "frame",
                 "char=1,1", "frame", **ON)
    assert err[1:] == [
        "sa2 tweaks: start assists: Sonic in level 13: Powerups 0x0000 -> 0x8003, Upgrades 0x00000000 -> 0x0000003F",
        "sa2 tweaks: start assists: Sonic in level 14: Powerups 0x8003 -> 0x8003, Upgrades 0x0000003F -> 0x0000003F",
        "sa2 tweaks: start assists: Shadow in level 13: Powerups 0x8003 -> 0x8003, Upgrades 0x0000003F -> 0x000F003F"]
