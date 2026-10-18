"""The walker-cannon-fix tweak (runtime/tweaks/walker-cannon-fix.c) on the cannon upgrade's draw,
WalkerBazooka_Disp (0x0075D880).

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted body and the
runtime's peek and poke. The guest memory is the three places the body reads: MainCharObj1[0] at
0x01DEA6E0, the object's CharObj2 pointer at +0x40, and the CharObj2's first bytes. The stand-in
`__real_sub_75d880` does what the lifted body's test does (0x0075D90E-0x0075D930): it reads
CharID at CharObj2 + 1, through the same peek, and picks [0x01DE9EA4]'s model list for 7,
[0x01DEA05C]'s for 6, and none otherwise. Each call prints
"<CharID the body saw> <the list it drew> <CharID after the call> <guest writes>".
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
WALKER = "13"   # a Chao Walker's CharID2: any ID that is not a Mech's (6 or 7)

# argv: one word per call. "<CharID>,<CharID2>" sets player 1's CharObj2; "null" leaves
# MainCharObj1[0] at 0 (no player 1); "locked,<CharID>,<CharID2>" makes CharID refuse a write.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define PLAYER1 0x01DEA6E0u
#define OBJ     0x00C00000u
#define CO2     0x00C01000u
static uint32_t player1, obj_co2;
static uint8_t co2[16];
static int locked, writes;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a == PLAYER1 && n == 4) return (uint8_t *)&player1;
    if (a == OBJ + 0x40u && n == 4) return (uint8_t *)&obj_co2;
    if (a >= CO2 && a + n <= CO2 + sizeof co2) return co2 + (a - CO2);
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
    if (!p || (locked && a == CO2 + 1u)) return 1;
    memcpy(p, in, n);
    writes++;
    return SA2_OK;
}

/* The lifted body's test: CharID at [[0x01DEA6E0] + 0x40] + 1. */
static int seen;
static const char *drew;
void *__real_sub_75d880(void *state, uint32_t pc, void *memory) {
    (void)state;
    if (pc != 0x0075D880u) return NULL;
    uint32_t o = 0, p = 0;
    uint8_t id = 0xFF;
    sa2_peek(PLAYER1, 4, &o);
    sa2_peek(o + 0x40u, 4, &p);
    if (sa2_peek(p + 1u, 1, &id) != SA2_OK) { seen = -1; drew = "fault"; return memory; }
    seen = id;
    drew = id == 7 ? "list-1DE9EA4" : id == 6 ? "list-1DEA05C" : "none";
    return memory;
}
void *__wrap_sub_75d880(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char *w = argv[i];
        locked = strncmp(w, "locked,", 7) == 0;
        if (locked) w += 7;
        memset(co2, 0, sizeof co2);
        player1 = strcmp(w, "null") == 0 ? 0u : OBJ;
        obj_co2 = CO2;
        if (player1) {
            co2[1] = (uint8_t)strtoul(w, &w, 0);
            co2[3] = (uint8_t)strtoul(w + 1, NULL, 0);
        }
        writes = 0;
        int token;
        if (__wrap_sub_75d880(&token, 0x0075D880u, &token) != &token) return 3;
        printf("%d %s %u %d\n", seen, drew, co2[1], writes);
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
    d = tmp_path_factory.mktemp("walker_cannon")
    (d / "driver.c").write_text(DRIVER)
    out = d / "wc"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "walker-cannon-fix.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


# Mech Eggman (7, 7), Mech Tails (6, 6), a Chao Walker on each Mech, Sonic (0, 0).
CALLS = ("7,7", "6,6", f"7,{WALKER}", f"6,{WALKER}", "0,0")
GAME = ["7 list-1DE9EA4 7 0", "6 list-1DEA05C 6 0", "7 list-1DE9EA4 7 0",
        "6 list-1DEA05C 6 0", "0 none 0 0"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_writes_nothing(exe, env):
    out, err = run(exe, *CALLS, "null", **env)
    assert out == GAME + ["-1 fault 0 0"]
    assert "walker-cannon" not in err


def test_on_a_chao_walker_draws_no_mech_model_and_keeps_its_charid(exe):
    out, err = run(exe, *CALLS, f"6,{WALKER}", SA2_TWEAKS="walker-cannon-fix")
    assert out == ["7 list-1DE9EA4 7 0", "6 list-1DEA05C 6 0", f"{WALKER} none 7 2",
                   f"{WALKER} none 6 2", "0 none 0 0", f"{WALKER} none 6 2"]
    assert err.splitlines() == [
        "sa2 tweaks: walker-cannon fix (SA2_TWEAKS=walker-cannon-fix)",
        f"sa2 tweaks: walker-cannon fix: the cannon's model test read CharID2 {WALKER} in place of "
        "CharID 7"]


def test_on_beside_another_tweak(exe):
    out, err = run(exe, f"6,{WALKER}", SA2_TWEAKS="no-event-bars,walker-cannon-fix")
    assert out == [f"{WALKER} none 6 2"]
    assert err.splitlines()[0] == ("sa2 tweaks: event-bars off, walker-cannon fix "
                                   "(SA2_TWEAKS=no-event-bars,walker-cannon-fix)")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="walker-cannon-fix", **{replay: "/dev/null"})
    assert out == GAME
    assert "walker-cannon fix:" not in err


def test_no_player_1_calls_the_body_unchanged_and_says_so_once(exe):
    out, err = run(exe, "null", "null", f"6,{WALKER}", SA2_TWEAKS="walker-cannon-fix")
    assert out == ["-1 fault 0 0", "-1 fault 0 0", f"{WALKER} none 6 2"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: walker-cannon fix: player 1's CharObj2 does not read (object 0x00000000, "
        "CharObj2 0x00000000), so the cannon is drawn as the game does",
        f"sa2 tweaks: walker-cannon fix: the cannon's model test read CharID2 {WALKER} in place of "
        "CharID 6"]


def test_a_charid_that_does_not_write_leaves_the_game_alone_and_says_so_once(exe):
    out, err = run(exe, f"locked,6,{WALKER}", f"locked,7,{WALKER}", SA2_TWEAKS="walker-cannon-fix")
    assert out == ["6 list-1DEA05C 6 0", "7 list-1DE9EA4 7 0"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: walker-cannon fix: CharID at 0x00C01001 does not write, so the cannon is "
        "drawn as the game does"]


def header() -> str:
    text = (RUNTIME / "tweaks" / "walker-cannon-fix.c").read_text()
    return " ".join(text[text.index("/*") + 2:text.index("*/")].replace("*", " ").split())


def test_the_crash_is_named_at_the_upgraded_shot_not_a_break():
    """WalkerBazooka_Disp's object is made when the walker fires its upgraded cannon (the
    walkers' main: reload byte CharObj2 + 0x360 at 0x00741617, fire flag at 0x0074162A, then
    0x0075DCA0 beside the projectile), so no text tells a player the crash comes from a walker
    breaking apart or losing its cannon."""
    h = header()
    assert h.startswith("walker-cannon-fix: when a Chao Walker that has the Mech's cannon upgrade "
                        "fires it, the shot's cannon object is drawn")
    for cited in ("0x00741617", "0x0074162A", "0x007416A0", "0x007416EE", "0x0075E1D0",
                  "0x0075E310", "drawing the upgraded cannon shots",
                  "the game crashes on the walker's first upgraded shot"):
        assert cited in h, cited
    x_line = next(line for line in (RUNTIME / "sa2_tweaks.h").read_text().splitlines()
                  if '"walker-cannon-fix"' in line)
    android = next(line for line in (ROOT / "host" / "android" / "java" / "com" / "sa2recomp" /
                                     "app" / "GameSettings.java").read_text().splitlines()
                   if 'new Tweak("walker-cannon-fix"' in line)
    wrong = re.compile(r"\b(break\w*|broken|los(e|es|ing)|lost|loose|apart)\b", re.I)
    for text in (h, x_line, android):
        assert not wrong.search(text), text
