"""The infinite-lives tweak (runtime/tweaks/infinite-lives.c): the restart's decrement in
0x0046BE30 and the kart race's in 0x0043D7A0 are given back, the HUD's copies with them, and with
the tweak off (or in a replay) the lifted bodies run alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted bodies and the
runtime's peek and poke. The guest holds Life_Count at 0x0174B024 (two int16) and the HUD's
pointer at 0x01A5A230, which points at a HUD block whose + 8 and + 0xA are the lives' copies.

The stand-in `__real_sub_46be30` does what the lifted body does (0x0046BEB5-0x0046BEE6): when the
player is dead and the 2P flag is 0, player 1's count goes down by one, is clamped to 99, and is
stored in the HUD's copy. The stand-in `__real_sub_43d7a0` calls `__wrap_sub_46be30`, as the lifted
call does under -wrap, and in the kart race (0x0043D7EE-0x0043D828) decrements both slots, clamps
each to 99 and stores each in its HUD copy. Each driver step prints
"<count 1> <count 2> <HUD 1> <HUD 2> <calls to the lifted bodies>".
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

# argv: the two starting counts, then steps:
#   miss     0x0046BE30 for a dead player (the decrement's path)
#   alive    0x0046BE30 for a player that is not dead (no decrement)
#   2p       0x0046BE30 for a dead player in a two-player game (no decrement)
#   oneup    0x0046BE30 whose body adds a life instead (a count that comes back higher)
#   restart  0x0043D7A0 outside the kart race (it calls 0x0046BE30 for a dead player)
#   kart     0x0043D7A0 in the kart race with no MainCharacter (both slots decremented)
#   nohud    the HUD's pointer becomes 0 for the steps after it
#   nolives  Life_Count stops reading for the steps after it
#   lifted:  the steps after it call the lifted stand-ins directly, not the wraps
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define LIVES 0x0174B024u
#define HUDP  0x01A5A230u
#define HUD   0x01A60000u
static int16_t lives[2], hud[8];
static uint32_t hudp = HUD;
static int lives_read = 1, dead, two_p, oneup, kart, calls;

static void *where(uint32_t a, uint32_t n) {
    if (lives_read && a >= LIVES && a + n <= LIVES + 4) return (uint8_t *)lives + (a - LIVES);
    if (a >= HUDP && a + n <= HUDP + 4) return (uint8_t *)&hudp + (a - HUDP);
    if (a >= HUD && a + n <= HUD + sizeof hud) return (uint8_t *)hud + (a - HUD);
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
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

static void clamp_store(int p) {
    if (lives[p] > 99) lives[p] = 99;
    hud[4 + p] = lives[p];                       /* HUD + 8 + 2p */
}
void *__real_sub_46be30(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    calls++;
    if (oneup) { lives[0]++; clamp_store(0); }
    else if (dead && !two_p) { lives[0]--; clamp_store(0); }
    return memory;
}
void *__wrap_sub_46be30(void *state, uint32_t pc, void *memory);
void *__real_sub_43d7a0(void *state, uint32_t pc, void *memory) {
    (void)pc;
    calls++;
    if (!kart) return __wrap_sub_46be30(state, 0x0046BE30u, memory);
    for (int p = 0; p < 2; p++) { lives[p]--; clamp_store(p); }
    return memory;
}
void *__wrap_sub_43d7a0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    lives[0] = (int16_t)atoi(argv[1]); lives[1] = (int16_t)atoi(argv[2]);
    hud[4] = lives[0]; hud[5] = lives[1];
    int direct = 0, token;
    for (int i = 3; i < argc; i++) {
        const char *s = argv[i];
        void *ret = &token;
        dead = two_p = oneup = kart = 0;
        calls = 0;
        if (!strcmp(s, "nohud")) { hudp = 0; continue; }
        if (!strcmp(s, "nolives")) { lives_read = 0; continue; }
        if (!strcmp(s, "lifted:")) { direct = 1; continue; }
        if (!strcmp(s, "miss")) dead = 1;
        else if (!strcmp(s, "2p")) dead = two_p = 1;
        else if (!strcmp(s, "oneup")) oneup = 1;
        else if (!strcmp(s, "restart")) dead = 1;
        else if (!strcmp(s, "kart")) kart = 1;
        else if (strcmp(s, "alive")) return 2;
        const int outer = !strcmp(s, "restart") || !strcmp(s, "kart");
        if (outer) ret = (direct ? __real_sub_43d7a0 : __wrap_sub_43d7a0)(&token, 0x0043D7A0u, &token);
        else ret = (direct ? __real_sub_46be30 : __wrap_sub_46be30)(&token, 0x0046BE30u, &token);
        if (ret != &token) return 3;
        printf("%d %d %d %d %d\n", lives[0], lives[1], hud[4], hud[5], calls);
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
    d = tmp_path_factory.mktemp("infinite_lives")
    (d / "driver.c").write_text(DRIVER)
    out = d / "il"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "infinite-lives.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *argv: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *argv], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr.splitlines()


ON = {"SA2_TWEAKS": "infinite-lives"}
STEPS = ("miss", "alive", "2p", "miss", "restart", "kart", "oneup", "miss")
# The lifted bodies alone, from 3 and 2 lives: each miss costs player 1 a life, the restart costs
# one through 0x0046BE30, the kart race one from each slot, the 1-up gives one back.
LIFTED = ["2 2 2 2 1", "2 2 2 2 1", "2 2 2 2 1", "1 2 1 2 1", "0 2 0 2 2", "-1 1 -1 1 1",
          "0 1 0 1 1", "-1 1 -1 1 1"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_bodies_alone(exe, env):
    out, err = run(exe, "3", "2", *STEPS, **env)
    assert out == LIFTED
    assert run(exe, "3", "2", "lifted:", *STEPS)[0] == LIFTED
    assert not [line for line in err if "lives held" in line]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, "3", "2", *STEPS, **ON, **{replay: "/dev/null"})
    assert out == LIFTED
    assert err == ["sa2 tweaks: SA2_TWEAKS=infinite-lives ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_no_miss_costs_a_life_and_a_1up_still_counts(exe):
    out, err = run(exe, "3", "2", *STEPS, **ON)
    assert out == ["3 2 3 2 1", "3 2 3 2 1", "3 2 3 2 1", "3 2 3 2 1", "3 2 3 2 2", "3 2 3 2 1",
                   "4 2 4 2 1", "4 2 4 2 1"]
    assert err == [
        "sa2 tweaks: lives held (SA2_TWEAKS=infinite-lives)",
        "sa2 tweaks: lives held: a life lost at 0x0046BEC5 was given back (lives held at 3)",
        "sa2 tweaks: lives held: a life lost at 0x0043D7FD (the kart race) was given back (lives "
        "held at 3)"]


def test_on_many_misses_never_reach_a_game_over(exe):
    out, _ = run(exe, "1", "0", *(["miss", "restart"] * 50), **ON)
    assert set(out) == {"1 0 1 0 1", "1 0 1 0 2"} and len(out) == 100


def test_on_the_hud_copy_is_put_back_as_it_was(exe):
    """The HUD's copy is restored to what it held, not to the count; the 99 clamp is undone."""
    out, _ = run(exe, "99", "5", "miss", "kart", **ON)
    assert out == ["99 5 99 5 1", "99 5 99 5 1"]


def test_with_both_tweaks_on(exe):
    out, err = run(exe, "3", "2", "miss", SA2_TWEAKS="no-event-bars,infinite-lives")
    assert out == ["3 2 3 2 1"]
    assert err[0] == "sa2 tweaks: lives held, event-bars off (SA2_TWEAKS=no-event-bars,infinite-lives)"


def test_an_unreadable_hud_keeps_the_count_and_says_so(exe):
    out, err = run(exe, "3", "2", "nohud", "miss", "miss", **ON)
    assert out == ["3 2 2 2 1", "3 2 2 2 1"]
    assert err.count("sa2 tweaks: lives held: the HUD's lives at [0x01A5A230] + 8 did not read, so "
                     "the HUD may show one life fewer than the count") == 1


def test_unreadable_lives_cost_a_life_as_usual_and_say_so(exe):
    out, err = run(exe, "3", "2", "nolives", "miss", "miss", **ON)
    # Life_Count no longer reads through the stand-in peek, so the lives here are the driver's own.
    assert out == ["2 2 2 2 1", "1 2 1 2 1"]
    assert err.count("sa2 tweaks: lives held: the lives at 0x0174B024 do not read, so a miss costs "
                     "a life as usual") == 1


def test_a_miss_at_zero_lives_is_named(exe):
    out, err = run(exe, "0", "0", "miss", "miss", **ON)
    assert out == ["0 0 0 0 1", "0 0 0 0 1"]
    assert err.count("sa2 tweaks: lives held: player 1 has 0 lives, which the game takes as out; the "
                     "tweak keeps the count and does not raise it, so a miss now can still end in a "
                     "game over") == 1
