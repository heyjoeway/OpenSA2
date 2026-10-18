"""The rival-upgrades tweak (runtime/tweaks/rival-upgrades.c): Shadow, Rouge and Mech Tails share
the upgrades Sonic, Knuckles and Eggman have found, from the wrap of the per-frame 0x0044C260, and
an armored Mech Tails starts at the armored hit points, from the wrap of InitMechHP (0x007414D0).

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted body and the
runtime's guest memory. The guest is the few places the tweak reads: MainCharacter[0]
(0x01DEA6E0), CurrentLevel (0x01934B70), TwoPlayerMode (0x0174AFDE), the Got bytes at 0x01DEB300,
the armored hit points (the float 12.0 at 0x008AF738), and a task and its CharObj2Base on the heap.
The stand-in `__real_sub_44c260` counts its calls and sets EAX, which the tweak must never touch;
the stand-in `__real_sub_7414d0` gives the CharObj2Base in EAX the 10.0 hit points InitMechHP gives
Mech Tails. Each case sets the guest with `set ADDR LEN VALUE`, runs frames with `frame` and
InitMechHP with `init`, and prints words with `get ADDR LEN` and the hit points with `hp`; the
driver ends by printing the lifted bodies' calls and the tweak's writes.
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

TASK, CP = 0x00100000, 0x00100100
MAIN, LEVEL, TWO_P = 0x01DEA6E0, 0x01934B70, 0x0174AFDE
GLOVES, BRACELET, NECKLACE, ARMOR = 0x01DEB302, 0x01DEB304, 0x01DEB30D, 0x01DEB317
UNDERWATER, UPGRADES = CP + 0x16, CP + 0x24
SAY = "sa2 tweaks: rival upgrades shared: "

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

/* The guest: a few 0x400-byte pages at fixed addresses; anything else does not read. */
static const uint32_t page_at[] = {0x0174AC00u, 0x01934800u, 0x01DEA400u, 0x01DEB000u, 0x00100000u,
                                   0x008AF400u};
#define PAGES (sizeof page_at / sizeof page_at[0])
static uint8_t page[PAGES][0x400];
static unsigned reals, writes;
static uint32_t gpr[8];

static uint8_t *at(uint32_t a, uint32_t n) {
    for (unsigned i = 0; i < PAGES; i++)
        if (a >= page_at[i] && a + n <= page_at[i] + 0x400u) return page[i] + (a - page_at[i]);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    writes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_44c260(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reals++;
    gpr[SA2_EAX] = 0x01DEB69Cu;               /* the lifted body's last load */
    return memory;
}
void *__wrap_sub_44c260(void *state, uint32_t pc, void *memory);

void *__real_sub_7414d0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reals++;
    const float base = 10.0f;                  /* InitMechHP's start for Mech Tails */
    uint8_t *hp = at(gpr[SA2_EAX] + 0x48u, 4);
    if (hp) memcpy(hp, &base, 4);
    return memory;
}
void *__wrap_sub_7414d0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    const float armored = 12.0f;
    memcpy(at(0x008AF738u, 4), &armored, 4);
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "frame") == 0) {
            int token;
            if (__wrap_sub_44c260(&token, 0x0044C260u, &token) != &token) return 3;
            if (gpr[SA2_EAX] != 0x01DEB69Cu) return 4;
        } else if (strcmp(argv[i], "init") == 0) {
            int token;
            gpr[SA2_EAX] = 0x00100100u;
            if (__wrap_sub_7414d0(&token, 0x007414D0u, &token) != &token) return 3;
        } else if (strcmp(argv[i], "hp") == 0) {
            float v;
            memcpy(&v, at(0x00100148u, 4), 4);
            printf("hp %g\n", (double)v);
        } else if (strcmp(argv[i], "set") == 0 && i + 3 < argc) {
            const uint32_t a = (uint32_t)strtoul(argv[i + 1], NULL, 0), n = (uint32_t)strtoul(argv[i + 2], NULL, 0);
            const uint32_t v = (uint32_t)strtoul(argv[i + 3], NULL, 0);
            uint8_t *p = at(a, n);
            if (!p) return 5;
            memcpy(p, &v, n);
            i += 3;
        } else if (strcmp(argv[i], "get") == 0 && i + 2 < argc) {
            const uint32_t a = (uint32_t)strtoul(argv[i + 1], NULL, 0), n = (uint32_t)strtoul(argv[i + 2], NULL, 0);
            uint32_t v = 0;
            uint8_t *p = at(a, n);
            if (!p) return 6;
            memcpy(&v, p, n);
            printf("0x%08X\n", v);
            i += 2;
        } else {
            return 2;
        }
    }
    printf("reals %u writes %u\n", reals, writes);
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
    d = tmp_path_factory.mktemp("rival")
    (d / "driver.c").write_text(DRIVER)
    out = d / "rival"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "rival-upgrades.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def player(character: int, costume: int | None = None, level: int = 14, upgrades: int = 0,
           underwater: int = 0, got: tuple[int, ...] = ()) -> list[str]:
    """The guest with player 1 loaded: the character, its CharID2, the level, its Upgrades and
    UnderwaterTime, and the Got bytes that are set."""
    head = character << 8 | (character if costume is None else costume) << 24
    cmds = ["set", hex(MAIN), "4", hex(TASK), "set", hex(TASK + 0x40), "4", hex(CP),
            "set", hex(CP), "4", hex(head), "set", hex(LEVEL), "2", str(level),
            "set", hex(UPGRADES), "4", hex(upgrades), "set", hex(UNDERWATER), "2", str(underwater)]
    for a in got:
        cmds += ["set", hex(a), "1", "1"]
    return cmds


def run(exe: Path, *cmds: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *cmds], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr.splitlines()


ON = {"SA2_TWEAKS": "rival-upgrades"}
REPORT = "sa2 tweaks: rival upgrades shared (SA2_TWEAKS=rival-upgrades)"
ALL_GOT = (GLOVES, BRACELET, NECKLACE, ARMOR)
FRAMES = ["frame"] * 3


# ------------------------------------------------------------------ off

@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
@pytest.mark.parametrize("character, word, value", [(1, UPGRADES, 0x10000), (6, UPGRADES, 0x40),
                                                    (5, UNDERWATER, 7)])
def test_off_only_calls_the_lifted_body(exe, env, character, word, value):
    """Every rival, every Got byte set: off, nothing is read or written and nothing is said."""
    size = "2" if word == UNDERWATER else "4"
    guest = player(character, upgrades=value if word == UPGRADES else 0,
                   underwater=value if word == UNDERWATER else 0, got=ALL_GOT)
    out, err = run(exe, *guest, *FRAMES, "get", hex(word), size, **env)
    assert out == [f"0x{value:08X}", "reals 3 writes 0"]
    assert [e for e in err if "rival" in e] == []


def test_ignored_in_a_replay(exe):
    out, err = run(exe, *player(1, got=ALL_GOT), *FRAMES, "get", hex(UPGRADES), "4",
                   SA2_REPLAY_TICKS="/dev/null", **ON)
    assert out == ["0x00000000", "reals 3 writes 0"]
    assert err == ["sa2 tweaks: SA2_TWEAKS=rival-upgrades ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


# ------------------------------------------------------------------ Shadow

def test_shadow_gets_sonics_bracelet_and_gloves_once(exe):
    out, err = run(exe, *player(1, upgrades=0x10000, got=ALL_GOT), *FRAMES, "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00010014", "reals 3 writes 1"]
    assert err == [REPORT, SAY + "Shadow in level 14 gets the Bounce Bracelet and the Magic Gloves "
                   "(Upgrades 0x00010000 -> 0x00010014)"]


@pytest.mark.parametrize("got, bits, says", [
    ((BRACELET,), 0x10, "gets the Bounce Bracelet (Upgrades 0x00000000 -> 0x00000010)"),
    ((GLOVES,), 0x4, "gets the Magic Gloves (Upgrades 0x00000000 -> 0x00000004)"),
    ((NECKLACE, ARMOR), 0, "gets nothing: Sonic has not found the Bounce Bracelet or the Magic Gloves"),
])
def test_shadow_gets_only_what_sonic_has(exe, got, bits, says):
    out, err = run(exe, *player(1, got=got), *FRAMES, "get", hex(UPGRADES), "4", **ON)
    assert out == [f"0x{bits:08X}", f"reals 3 writes {int(bits != 0)}"]
    assert err == [REPORT, SAY + "Shadow in level 14 " + says]


def test_a_new_shadow_after_a_restart_gets_them_again(exe):
    """A restart loads the character anew, with the loader's bits only; the next frame ORs again,
    and the level is named once."""
    out, err = run(exe, *player(1, got=ALL_GOT), "frame", "set", hex(UPGRADES), "4", "0", "frame",
                   "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00000014", "reals 2 writes 2"]
    assert len(err) == 2


@pytest.mark.parametrize("level", [19, 42])
def test_shadow_in_the_sonic_vs_shadow_fights_is_left_alone(exe, level):
    out, err = run(exe, *player(1, level=level, got=ALL_GOT), *FRAMES, "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00000000", "reals 3 writes 0"]
    assert err == [REPORT, SAY + f"Shadow in level {level} is left alone (Sonic's upgrade models "
                   "would draw on him)"]


# ------------------------------------------------------------------ Mech Tails

def test_mech_tails_gets_eggmans_armor(exe):
    out, err = run(exe, *player(6, level=24, upgrades=0x40, got=ALL_GOT), *FRAMES,
                   "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00800040", "reals 3 writes 1"]
    assert err == [REPORT, SAY + "Mech Tails in level 24 gets the Protective Armor (Upgrades "
                   "0x00000040 -> 0x00800040)"]


def test_mech_tails_without_eggmans_armor(exe):
    out, err = run(exe, *player(6, level=24, got=(GLOVES, BRACELET, NECKLACE)), *FRAMES,
                   "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00000000", "reals 3 writes 0"]
    assert err == [REPORT, SAY + "Mech Tails in level 24 gets nothing: Eggman has not found the "
                   "Protective Armor"]


# ------------------------------------------------------------------ Rouge

def test_rouge_keeps_her_breath_while_knuckles_has_the_necklace(exe):
    """The timer is zeroed whenever it has counted up, never given the 0x2000 bit."""
    out, err = run(exe, *player(5, level=18, upgrades=0x2000000, underwater=1, got=ALL_GOT), "frame",
                   "get", hex(UNDERWATER), "2", "set", hex(UNDERWATER), "2", "1", "frame", "frame",
                   "get", hex(UNDERWATER), "2", "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00000000", "0x00000000", "0x02000000", "reals 3 writes 2"]
    assert err == [REPORT,
                   SAY + "Rouge in level 18 keeps her breath underwater (Knuckles has the Air Necklace)",
                   SAY + "Rouge's air timer held at 0 in level 18 (it read 1)"]


def test_rouge_without_the_necklace_counts_as_the_game_does(exe):
    out, err = run(exe, *player(5, level=18, underwater=0x473, got=(GLOVES, BRACELET, ARMOR)),
                   *FRAMES, "get", hex(UNDERWATER), "2", **ON)
    assert out == ["0x00000473", "reals 3 writes 0"]
    assert err == [REPORT, SAY + "Rouge in level 18 gets nothing: Knuckles has not found the Air Necklace"]


# ------------------------------------------------------------------ left alone

@pytest.mark.parametrize("character", [0, 2, 3, 4, 7])
def test_the_rivals_themselves_and_the_others_are_left_alone(exe, character):
    out, err = run(exe, *player(character, upgrades=0x5, underwater=9, got=ALL_GOT), *FRAMES,
                   "get", hex(UPGRADES), "4", "get", hex(UNDERWATER), "2", **ON)
    assert out == ["0x00000005", "0x00000009", "reals 3 writes 0"]
    assert err == [REPORT]


def test_a_costume_is_left_alone(exe):
    """Metal Sonic plays as Shadow's CharID with CharID2 12; his Bounce Attack would crash."""
    out, err = run(exe, *player(1, costume=12, got=ALL_GOT), *FRAMES, "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00000000", "reals 3 writes 0"]
    assert err == [REPORT, SAY + "character 12 playing as Shadow is left alone (a costume, not the character)"]


def test_two_player_mode_is_left_alone(exe):
    out, err = run(exe, *player(1, got=ALL_GOT), "set", hex(TWO_P), "1", "1", *FRAMES,
                   "get", hex(UPGRADES), "4", **ON)
    assert out == ["0x00000000", "reals 3 writes 0"]
    assert err == [REPORT, SAY + "two-player mode is left alone"]


def test_no_player_outside_a_level_says_nothing(exe):
    out, err = run(exe, *FRAMES, **ON)
    assert out == ["reals 3 writes 0"] and err == [REPORT]
    out, err = run(exe, "set", hex(MAIN), "4", hex(TASK), *FRAMES, **ON)   # a task with no Data2
    assert out == ["reals 3 writes 0"] and err == [REPORT]


def test_an_unreadable_character_is_said_once(exe):
    out, err = run(exe, "set", hex(MAIN), "4", "0x00900000", *FRAMES, **ON)
    assert out == ["reals 3 writes 0"]
    assert err == [REPORT, SAY + "player 1's task at 0x00900040 does not read, so this frame is left alone"]


# ------------------------------------------------------------------ Mech Tails's armored start

def test_an_armored_mech_tails_starts_at_the_armored_hit_points(exe):
    out, err = run(exe, *player(6, level=9, got=(ARMOR,)), "init", "hp", "init", "hp", **ON)
    assert out == ["hp 12", "hp 12", "reals 2 writes 2"]
    assert err == [REPORT, SAY + "Mech Tails in level 9 starts with the armored hit points (12), as "
                   "Mech Eggman does"]


@pytest.mark.parametrize("character, costume, got, env", [
    (6, None, (ARMOR,), {}),                          # off
    (6, None, (GLOVES, BRACELET, NECKLACE), ON),      # Eggman has no armor
    (7, None, (ARMOR,), ON),                          # Mech Eggman: InitMechHP's own
    (6, 13, (ARMOR,), ON),                            # the Chao Walker costume
])
def test_init_is_left_alone_otherwise(exe, character, costume, got, env):
    out, err = run(exe, *player(character, costume, got=got), "init", "hp", **env)
    assert out == ["hp 10", "reals 1 writes 0"]
    assert [e for e in err if "rival" in e and e != REPORT] == []


def test_init_leaves_player_2_and_two_player_mode_alone(exe):
    p2 = player(6, got=(ARMOR,)) + ["set", hex(CP), "1", "1"]
    assert run(exe, *p2, "init", "hp", **ON)[0] == ["hp 10", "reals 1 writes 0"]
    two = player(6, got=(ARMOR,)) + ["set", hex(TWO_P), "1", "1"]
    assert run(exe, *two, "init", "hp", **ON)[0] == ["hp 10", "reals 1 writes 0"]
