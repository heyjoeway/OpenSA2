"""ring-loss-cap (runtime/tweaks/ring-loss-cap.c): a hit costs at most 20 rings, at AddRings
(0x0044CE10) when DropRings (0x006C1AF0) calls it, and the 100-ring 1-up is not paid twice for the
same hundred.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c and no-2p-specials.c,
whose wrap of AddRings it acts in, as test_tweaks.py builds no-event-bars, and linked with a driver
that stands in for the lifted AddRings and the runtime's peek, poke and register accessors. The
stand-in `__real_sub_44ce10` does what the lifted body does (0x0044CE10-0x0044CEAB): the ring count
of player EAX & 1 (the word at 0x0174B028 + 2p in the driver's guest memory) plus EDX, clamped to
0..999; when the new count's hundreds are higher than the old count's and TwoPlayerMode (the byte
at 0x0174AFDE) is 0, AddLives: Life_Count plus the hundreds gained, clamped to 99; EBP kept
(AddRings pushes and pops it), then the RET that pops the return address (ESP + 4). Each call puts
its caller's return address at guest [ESP] first, as the lifted CALL does, and sets the registers
DropRings has at its call: EDX = -rings and EBP = min(rings, 20), the rings it will scatter. Each
call prints the ring count after it, EBP, how far ESP moved and the player's lives (4 at the
start), so a case reads what the game sees after it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env

DROP = "0x006C1B3F"        # DropRings' return address, after its call at 0x006C1B3A
PICKUP = "0x006C1648"      # another AddRings caller's (the call at 0x006C1643)
TWEAK = {"SA2_TWEAKS": "ring-loss-cap"}
REPORT = "sa2 tweaks: ring loss capped at 20 (SA2_TWEAKS=ring-loss-cap)"

# argv: groups of (return address, player, ring count before or "-" to keep it, EDX or "all" for
# minus the count); each prints "<rings after> <EBP> <ESP moved> <lives>". RL_TWO_PLAYER=1 sets
# TwoPlayerMode; RL_NO_RINGS=1 makes the ring counts, and RL_NO_MODE=1 TwoPlayerMode, not read.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
#define MODE 0x0174AFDEu
#define LIVES 0x0174B024u
#define RINGS 0x0174B028u
#define GLOBALS MODE
static uint8_t stack[0x100], globals[RINGS + 4 - GLOBALS];
static uint32_t gpr[8];
static int no_rings, no_mode;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    if (a >= GLOBALS && a + n <= GLOBALS + sizeof globals) {
        if (no_rings && a + n > RINGS && a < RINGS + 4) return NULL;
        if (no_mode && a <= MODE && a + n > MODE) return NULL;
        return globals + (a - GLOBALS);
    }
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
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static int16_t *word(uint32_t a) { return (int16_t *)(globals + (a - GLOBALS)); }

void *__real_sub_44ce10(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t p = gpr[SA2_EAX] & 1;
    const int16_t old = *word(RINGS + 2 * p);
    const int16_t sum = (int16_t)(uint16_t)((uint16_t)old + gpr[SA2_EDX]);
    const int16_t now = sum > 999 ? 999 : sum < 0 ? 0 : sum;
    *word(RINGS + 2 * p) = now;
    if (old / 100 < now / 100 && globals[MODE - GLOBALS] == 0) {   /* AddLives, BX = the gain */
        const int lives = *word(LIVES + 2 * p) + now / 100 - old / 100;
        *word(LIVES + 2 * p) = (int16_t)(lives > 99 ? 99 : lives);
    }
    gpr[SA2_ECX] = 0x11111111u; gpr[SA2_EDX] = 0x22222222u;   /* clobbered, as the body does */
    gpr[SA2_ESP] += 4;                                         /* the RET */
    return memory;
}
void *__real_sub_46aa80(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; return memory; }
void *__wrap_sub_44ce10(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    no_rings = getenv("RL_NO_RINGS") != NULL;
    no_mode = getenv("RL_NO_MODE") != NULL;
    globals[MODE - GLOBALS] = getenv("RL_TWO_PLAYER") != NULL;
    *word(LIVES) = *word(LIVES + 2) = 4;
    for (int i = 1; i + 3 < argc; i += 4) {
        const uint32_t ret = (uint32_t)strtoul(argv[i], NULL, 0);
        const uint32_t player = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        int16_t *rings = word(RINGS + 2 * (player & 1));
        if (strcmp(argv[i + 2], "-") != 0) *rings = (int16_t)strtol(argv[i + 2], NULL, 0);
        const int32_t edx = strcmp(argv[i + 3], "all") == 0 ? -*rings
                                                            : (int32_t)strtol(argv[i + 3], NULL, 0);
        const uint32_t esp = strcmp(argv[i], "unreadable") == 0 ? 0x90000u : STACK + 0x80u;
        if (esp == STACK + 0x80u) memcpy(stack + 0x80, &ret, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EAX] = player;
        gpr[SA2_EDX] = (uint32_t)edx;
        gpr[SA2_EBP] = (uint32_t)(*rings < 20 ? *rings : 20);
        int token;
        if (__wrap_sub_44ce10(&token, 0x0044CE10u, &token) != &token) return 3;
        printf("%d %u %u %d\n", *rings, gpr[SA2_EBP], gpr[SA2_ESP] - esp, *word(LIVES + 2 * (player & 1)));
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> str:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("ring-loss-cap")
    (d / "driver.c").write_text(DRIVER)
    out = d / "rl"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "ring-loss-cap.c"),
                           str(RUNTIME / "tweaks" / "no-2p-specials.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return str(out)


def run(exe: str, *calls, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([exe, *map(str, calls)], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def drop(rings: int, player: int = 0) -> tuple:
    """DropRings' call for a player with `rings`: EDX = -rings."""
    return (DROP, player, rings, -rings)


# 150 rings, 5, exactly 20, 21, the most (999), and player 2 (index 1) with 60; then a ring
# pickup (+1) and a call from another site that takes 150.
CALLS = (*drop(150), *drop(5), *drop(20), *drop(21), *drop(999), *drop(60, player=1),
         PICKUP, 0, 10, 1, PICKUP, 0, 150, -150)


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_every_hit_takes_every_ring(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == ["0 20 4 4", "0 5 4 4", "0 20 4 4", "0 20 4 4", "0 20 4 4", "0 20 4 4", "11 10 4 4",
                   "0 20 4 4"]
    assert not [line for line in err if "ring loss" in line]


def test_on_a_hit_takes_at_most_the_20_it_scatters(exe):
    out, err = run(exe, *CALLS, **TWEAK)
    assert out == ["130 20 4 4", "0 5 4 4", "0 20 4 4", "1 20 4 4", "979 20 4 4", "40 20 4 4",
                   "11 10 4 4", "0 20 4 4"]
    assert err == [REPORT,
                   "sa2 tweaks: ring loss capped at 20: player 0 lost 20 of 150 rings",
                   "sa2 tweaks: ring loss capped at 20: player 0 lost 5 of 5 rings",
                   "sa2 tweaks: ring loss capped at 20: player 0 lost 20 of 20 rings",
                   "sa2 tweaks: ring loss capped at 20: player 0 lost 20 of 21 rings",
                   "sa2 tweaks: ring loss capped at 20: player 0 lost 20 of 999 rings",
                   "sa2 tweaks: ring loss capped at 20: player 1 lost 20 of 60 rings"]


def test_the_first_16_hits_are_listed_then_one_line(exe):
    out, err = run(exe, *[x for _ in range(20) for x in drop(100)], **TWEAK)
    assert out == ["80 20 4 4"] * 20
    assert err[1:] == (["sa2 tweaks: ring loss capped at 20: player 0 lost 20 of 100 rings"] * 16
                       + ["sa2 tweaks: ring loss capped at 20: 16 hits listed; later hits are not"])


def test_an_unreadable_stack_leaves_the_change_alone_and_says_so_once(exe):
    out, err = run(exe, "unreadable", 0, 150, -150, "unreadable", 0, 150, -150, *drop(150), **TWEAK)
    assert out == ["0 20 4 4", "0 20 4 4", "130 20 4 4"]
    assert err == [REPORT,
                   "sa2 tweaks: ring loss capped at 20: the guest stack at 0x00090000 does not "
                   "read, so the caller is unknown and the ring change is left alone",
                   "sa2 tweaks: ring loss capped at 20: player 0 lost 20 of 150 rings"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *drop(150), **TWEAK, **{replay: "/dev/null"})
    assert out == ["0 20 4 4"]
    assert err == ["sa2 tweaks: SA2_TWEAKS=ring-loss-cap ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def hit(player: int = 0) -> tuple:
    """DropRings' call for the player's current ring count."""
    return (DROP, player, "-", "all")


def pickup(rings: int, player: int = 0) -> tuple:
    """Another site's AddRings call adding `rings` to the current count."""
    return (PICKUP, player, "-", rings)


def held(old: int, new: int, upto: int, award: int, due: int, player: int = 0) -> str:
    return (f"sa2 tweaks: ring loss capped at 20: player {player} went from {old} to {new} rings; "
            f"the 1-up for each hundred up to {upto} was given before a capped hit, so {award} of "
            f"{due} lives are given")


def lost(n: int, of: int, player: int = 0) -> str:
    return f"sa2 tweaks: ring loss capped at 20: player {player} lost {n} of {of} rings"


# A player at 95 picks up 5 (a 1-up, as in the game), takes a hit at 100 and picks the 20 scattered
# rings back up.
REGAIN = (PICKUP, 0, 95, 5, *hit(), *pickup(20))


def test_off_a_hit_then_the_scatter_regained_pays_one_1up(exe):
    out, err = run(exe, *REGAIN)
    assert out == ["100 20 4 5", "0 20 4 5", "20 0 4 5"]
    assert not [line for line in err if "ring loss" in line]


def test_on_the_scattered_rings_regained_pay_no_second_1up(exe):
    out, err = run(exe, *REGAIN, **TWEAK)
    assert out == ["100 20 4 5", "80 20 4 5", "100 20 4 5"]
    assert err == [REPORT, lost(20, 100), held(80, 100, 100, 0, 1)]


def test_on_hits_and_regains_repeated_never_pay_and_the_first_16_are_listed(exe):
    out, err = run(exe, *REGAIN, *[x for _ in range(18) for x in (*hit(), *pickup(20))], **TWEAK)
    assert out == ["100 20 4 5"] + ["80 20 4 5", "100 20 4 5"] * 19
    regains = [line for line in err if "went from" in line or "held-back" in line]
    assert regains == [held(80, 100, 100, 0, 1)] * 16 + [
        "sa2 tweaks: ring loss capped at 20: 16 held-back 1-ups listed; later ones are not"]


def test_on_a_hundred_not_reached_before_still_pays(exe):
    # 80 -> 100 is held back; 100 -> 200 is a new hundred; a hit at 205 and 25 back pay nothing.
    out, err = run(exe, *REGAIN, *pickup(100), *pickup(5), *hit(), *pickup(25), **TWEAK)
    assert out == ["100 20 4 5", "80 20 4 5", "100 20 4 5", "200 20 4 6", "205 20 4 6",
                   "185 20 4 6", "210 20 4 6"]
    assert err == [REPORT, lost(20, 100), held(80, 100, 100, 0, 1), lost(20, 205),
                   held(185, 210, 200, 0, 1)]


def test_on_one_call_across_a_paid_and_a_new_hundred_pays_the_new_one(exe):
    out, err = run(exe, *REGAIN, *hit(), *pickup(130), **TWEAK)
    assert out[-1] == "210 20 4 6"
    assert err[-1] == held(80, 210, 100, 1, 2)


def test_on_back_at_0_every_hundred_pays_again(exe):
    # A full loss (15 rings) and a stage start (the count set to 0) both clear the mark.
    out, err = run(exe, *REGAIN, DROP, 0, 15, -15, *pickup(100),
                   *hit(), PICKUP, 0, 0, 100, **TWEAK)
    assert out == ["100 20 4 5", "80 20 4 5", "100 20 4 5", "0 15 4 5", "100 0 4 6", "80 20 4 6",
                   "100 0 4 7"]
    assert err == [REPORT, lost(20, 100), held(80, 100, 100, 0, 1), lost(15, 15), lost(20, 100)]


def test_each_player_keeps_its_own_mark(exe):
    out, _ = run(exe, PICKUP, 0, 100, 0, *hit(0), PICKUP, 1, 80, 20, *pickup(20, 0), **TWEAK)
    assert out == ["100 20 4 4", "80 20 4 4", "100 20 4 5", "100 20 4 4"]


@pytest.mark.parametrize("env", [{}, TWEAK])
def test_2p_has_no_ring_1ups_and_the_count_is_left_alone(exe, env):
    out, err = run(exe, *REGAIN, RL_TWO_PLAYER="1", **env)
    assert out == ["100 20 4 4", "80 20 4 4" if env else "0 20 4 4", "100 20 4 4" if env else "20 0 4 4"]
    assert not [line for line in err if "went from" in line]


def test_on_an_unreadable_ring_count_leaves_the_call_alone_and_says_so_once(exe):
    out, err = run(exe, *drop(150), *drop(150), RL_NO_RINGS="1", **TWEAK)
    assert out == ["0 20 4 4", "0 20 4 4"]
    assert err == [REPORT, "sa2 tweaks: ring loss capped at 20: the ring count at 0x0174B028 does "
                   "not read, so the ring change is left alone"]


def test_on_an_unreadable_two_player_mode_pays_as_the_game_does_and_says_so_once(exe):
    out, err = run(exe, *REGAIN, *hit(), *pickup(20), RL_NO_MODE="1", **TWEAK)
    assert out == ["100 20 4 5", "80 20 4 5", "100 20 4 6", "80 20 4 6", "100 20 4 7"]
    assert err == [REPORT, lost(20, 100),
                   "sa2 tweaks: ring loss capped at 20: TwoPlayerMode at 0x0174AFDE does not read, "
                   "so a 1-up a capped hit already paid may be paid again", lost(20, 100)]


def test_the_android_note_says_what_is_saved_and_that_lives_are_not_farmed():
    java = (Path(__file__).resolve().parents[3] / "host" / "android" / "java" / "com" / "sa2recomp"
            / "app" / "GameSettings.java").read_text()
    note = re.search(r'new Tweak\("ring-loss-cap", "[^"]*", "([^"]*)"\)', java).group(1)
    assert "no second extra life" in note
    assert "ring counts, scores and ranks it brings are saved" in note
