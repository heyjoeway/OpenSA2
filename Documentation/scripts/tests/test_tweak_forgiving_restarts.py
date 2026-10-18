"""The forgiving-restarts tweak (runtime/tweaks/forgiving-restarts.c): the pause menu's restart
keeps the rings and the life, a miss keeps the score, and with the tweak off (or in a replay) the
lifted bodies run alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted bodies and the runtime's
peek, poke and register accessors. The guest holds Life_Count at 0x0174B024 and RingCount at
0x0174B028 (two int16 each), ScoreP1 at 0x0174B050, the two-player flag at 0x0174AFDE, and the
HUD's pointer at 0x01A5A230, which points at a block whose + 8, + 0xC and + 0x18 hold the copies.
Each call puts its caller's return address at guest [ESP] first, as the lifted CALL does.

The stand-in `__real_sub_43c370` (RestartStageTeardown) does what its call of 0x0043D7A0 does with
the flag 0x01A558A0 at 0: the rings and the score go back to their backups (ring_backup for
player 1, 0 unless a "backup=" step sets it, as 0x0044CA50 sets 50 in Final Hazard, level 0x42;
and SCORE_BACKUP), with their HUD copies, and a dead player 1 loses a life (0x0046BEB5-0x0046BEE6:
down by one, clamped to 99, stored in the HUD's copy) and is no longer dead; then the RET. The
stand-in `__real_sub_43c940` (RestartInitStageRuntime) resets the rings and the score again, as
0x0043C7C0 and its own call of 0x0043D7A0 do. Each driver step prints
"<rings 1> <rings 2> <HUD rings 1> <HUD rings 2> <lives 1> <lives 2> <HUD lives 1> <HUD lives 2>
<score> <HUD score> <ESP moved>".
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

# argv: steps.
#   rings=A,B score=S lives=A,B   set the counts and their HUD copies
#   backup=N what each reset sets player 1's rings to from then on (0x0044CA50: 50 in level 0x42)
#   pause    the pause menu's restart: player 1 dead, 0x0043C370 from 0x0043BB03, then 0x0043C940
#            from 0x0043BB0D (GameStateHandler's state 0x0D)
#   miss     a miss's restart: player 1 dead, 0x0043C370 from 0x0043BA96 (state 9)
#   other    0x0043C370 from 0x0043BB84 (state 0x0C), player 1 not dead
#   init     0x0043C940 from 0x0043BB0D with no teardown before it
#   2p / 1p  the two-player flag set / cleared
#   nohud    the HUD's pointer becomes 0 for the steps after it
#   nostack  the guest stack stops reading for the steps after it
#   noscore  ScoreP1 stops reading for the steps after it
#   lifted:  the steps after it call the lifted stand-ins directly, not the wraps
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define LIVES 0x0174B024u
#define RINGS 0x0174B028u
#define SCORE 0x0174B050u
#define TWOP  0x0174AFDEu
#define HUDP  0x01A5A230u
#define HUD   0x01A60000u
#define STACK 0x20000u
#define SCORE_BACKUP 0
static int16_t lives[2] = {4, 4}, rings[2], ring_backup;
static int32_t score;
static uint8_t two_p, stack[0x100], hud[0x20];
static uint32_t hudp = HUD, gpr[8];
static int stack_reads = 1, score_reads = 1, dead;

static void *where(uint32_t a, uint32_t n) {
    if (a >= LIVES && a + n <= LIVES + 4) return (uint8_t *)lives + (a - LIVES);
    if (a >= RINGS && a + n <= RINGS + 4) return (uint8_t *)rings + (a - RINGS);
    if (score_reads && a >= SCORE && a + n <= SCORE + 4) return (uint8_t *)&score + (a - SCORE);
    if (a == TWOP && n == 1) return &two_p;
    if (a >= HUDP && a + n <= HUDP + 4) return (uint8_t *)&hudp + (a - HUDP);
    if (a >= HUD && a + n <= HUD + sizeof hud) return hud + (a - HUD);
    if (stack_reads && a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
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
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

/* The game's stores go through the HUD's pointer, as the lifted code does ([0x01A5A230] + off). */
static void hud_store(uint32_t off, const void *v, uint32_t n) { if (hudp) memcpy(hud + (hudp - HUD) + off, v, n); }
static void reset_counts(void) {                    /* 0x0044CA50 and the score's copy */
    rings[0] = ring_backup; rings[1] = 0;
    hud_store(0xC, rings, 4);
    score = SCORE_BACKUP;
    hud_store(0x18, &score, 4);
}
void *__real_sub_43c370(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reset_counts();
    if (dead && !two_p) {                           /* 0x0046BE30 for player 1 */
        lives[0]--;
        if (lives[0] > 99) lives[0] = 99;
        hud_store(8, lives, 2);
    }
    dead = 0;
    gpr[SA2_ESP] += 4;                              /* the RET */
    return memory;
}
void *__real_sub_43c940(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reset_counts();                                 /* 0x0043C7C0 */
    reset_counts();                                 /* 0x0043D7A0, no one dead now */
    gpr[SA2_ESP] += 4;
    return memory;
}
void *__wrap_sub_43c370(void *state, uint32_t pc, void *memory);
void *__wrap_sub_43c940(void *state, uint32_t pc, void *memory);

static int direct;
static void *call(void *(*wrap)(void *, uint32_t, void *), void *(*real)(void *, uint32_t, void *),
                  uint32_t pc, uint32_t ret) {
    int token;
    gpr[SA2_ESP] = STACK + 0x80u;
    memcpy(stack + 0x80, &ret, 4);
    void *r = (direct ? real : wrap)(&token, pc, &token);
    if (r != &token) exit(3);
    return r;
}

int main(int argc, char **argv) {
    hud_store(8, lives, 4);
    for (int i = 1; i < argc; i++) {
        const char *s = argv[i];
        int a, b;
        if (sscanf(s, "rings=%d,%d", &a, &b) == 2) { rings[0] = (int16_t)a; rings[1] = (int16_t)b; hud_store(0xC, rings, 4); continue; }
        if (sscanf(s, "lives=%d,%d", &a, &b) == 2) { lives[0] = (int16_t)a; lives[1] = (int16_t)b; hud_store(8, lives, 4); continue; }
        if (sscanf(s, "backup=%d", &a) == 1) { ring_backup = (int16_t)a; continue; }
        if (sscanf(s, "score=%d", &a) == 1) { score = a; hud_store(0x18, &score, 4); continue; }
        if (!strcmp(s, "2p")) { two_p = 1; continue; }
        if (!strcmp(s, "1p")) { two_p = 0; continue; }
        if (!strcmp(s, "nohud")) { hudp = 0; continue; }
        if (!strcmp(s, "nostack")) { stack_reads = 0; continue; }
        if (!strcmp(s, "noscore")) { score_reads = 0; continue; }
        if (!strcmp(s, "lifted:")) { direct = 1; continue; }
        uint32_t esp0 = STACK + 0x80u;
        if (!strcmp(s, "pause")) {
            dead = 1;
            call(__wrap_sub_43c370, __real_sub_43c370, 0x0043C370u, 0x0043BB03u);
            call(__wrap_sub_43c940, __real_sub_43c940, 0x0043C940u, 0x0043BB0Du);
        } else if (!strcmp(s, "miss")) {
            dead = 1;
            call(__wrap_sub_43c370, __real_sub_43c370, 0x0043C370u, 0x0043BA96u);
        } else if (!strcmp(s, "other")) {
            call(__wrap_sub_43c370, __real_sub_43c370, 0x0043C370u, 0x0043BB84u);
        } else if (!strcmp(s, "init")) {
            call(__wrap_sub_43c940, __real_sub_43c940, 0x0043C940u, 0x0043BB0Du);
        } else return 2;
        int16_t h[6];
        int32_t hs;
        memcpy(h, hud + 8, 12);
        memcpy(&hs, hud + 0x18, 4);
        printf("%d %d %d %d %d %d %d %d %d %d %u\n", rings[0], rings[1], h[2], h[3], lives[0], lives[1],
               h[0], h[1], score, hs, gpr[SA2_ESP] - esp0);
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
    d = tmp_path_factory.mktemp("forgiving_restarts")
    (d / "driver.c").write_text(DRIVER)
    out = d / "fr"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "forgiving-restarts.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *argv: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *argv], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr.splitlines()


ON = {"SA2_TWEAKS": "forgiving-restarts"}
# 37 rings and a score of 1200 at four lives: the pause menu's restart, then rings and score
# again, a miss, then the restart RestartStageTeardown's third caller makes.
STEPS = ("rings=37,0", "score=1200", "pause", "rings=12,0", "score=900", "miss", "rings=5,0",
         "score=300", "other")
# The lifted bodies alone: each restart takes the rings and the score to 0, and both the pause
# menu's restart and the miss cost a life.
LIFTED = ["0 0 0 0 3 4 3 4 0 0 4", "0 0 0 0 2 4 2 4 0 0 4", "0 0 0 0 2 4 2 4 0 0 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_bodies_alone(exe, env):
    out, err = run(exe, *STEPS, **env)
    assert out == LIFTED
    assert run(exe, "lifted:", *STEPS)[0] == LIFTED
    assert not [line for line in err if "forgiving restarts" in line]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *STEPS, **ON, **{replay: "/dev/null"})
    assert out == LIFTED
    assert err == ["sa2 tweaks: SA2_TWEAKS=forgiving-restarts ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)"]


def test_on_the_pause_restart_keeps_rings_and_life_and_a_miss_keeps_the_score(exe):
    out, err = run(exe, *STEPS, **ON)
    assert out == [
        "37 0 37 0 4 4 4 4 0 0 4",      # pause: rings and the life kept, the score reset
        "0 0 0 0 3 4 3 4 900 900 4",    # miss: the score kept, the rings and a life lost
        "0 0 0 0 3 4 3 4 0 0 4"]        # state 0x0C's teardown: the game's own
    assert err == [
        "sa2 tweaks: forgiving restarts (SA2_TWEAKS=forgiving-restarts)",
        "sa2 tweaks: forgiving restarts: the pause menu's restart kept 37 rings (not 0) and 4 lives "
        "(not 3)",
        "sa2 tweaks: forgiving restarts: the miss kept the score at 900 (not 0); a result this run is "
        "an assisted one",
        "sa2 tweaks: forgiving restarts: RestartStageTeardown called from 0x0043BB84 (not the pause "
        "menu's restart or a miss) is left to the game"]


def test_with_both_tweaks_on(exe):
    out, err = run(exe, "rings=20,0", "pause", SA2_TWEAKS="no-event-bars,forgiving-restarts")
    assert out == ["20 0 20 0 4 4 4 4 0 0 4"]
    assert err[0] == ("sa2 tweaks: forgiving restarts, event-bars off "
                      "(SA2_TWEAKS=no-event-bars,forgiving-restarts)")


def test_the_hud_copies_are_put_back_as_they_were(exe):
    """The 99 clamp on the lives is undone with the HUD's copy; both players' rings come back."""
    out, _ = run(exe, "lives=120,7", "rings=150,3", "pause", "score=7", "miss", **ON)
    assert out == ["150 3 150 3 120 7 120 7 0 0 4", "0 0 0 0 99 7 99 7 7 7 4"]


def test_a_miss_with_no_score_to_keep_says_nothing(exe):
    out, err = run(exe, "rings=0,0", "score=0", "miss", **ON)
    assert out == ["0 0 0 0 3 4 3 4 0 0 4"]
    assert err == ["sa2 tweaks: forgiving restarts (SA2_TWEAKS=forgiving-restarts)"]


def test_many_restarts_are_said_sixteen_times_each(exe):
    out, err = run(exe, *(["rings=10,0", "pause"] * 20), **ON)
    assert set(out) == {"10 0 10 0 4 4 4 4 0 0 4"} and len(out) == 20
    assert len([line for line in err if "the pause menu's restart kept" in line]) == 16


def test_a_two_player_game_is_left_to_the_game_and_said_once(exe):
    out, err = run(exe, "2p", "rings=30,40", "score=500", "pause", "score=500", "miss", "1p",
                   "rings=30,40", "pause", **ON)
    assert out == ["0 0 0 0 4 4 4 4 0 0 4", "0 0 0 0 4 4 4 4 0 0 4", "30 40 30 40 4 4 4 4 0 0 4"]
    assert err.count("sa2 tweaks: forgiving restarts: a two-player game's restart is left to the "
                     "game") == 1


def test_an_init_without_the_pause_teardown_is_the_games(exe):
    out, err = run(exe, "rings=30,0", "init", "rings=30,0", "other", "init", **ON)
    assert out == ["0 0 0 0 4 4 4 4 0 0 4", "0 0 0 0 4 4 4 4 0 0 4", "0 0 0 0 4 4 4 4 0 0 4"]
    assert not [line for line in err if "kept" in line]


def test_an_unreadable_hud_keeps_the_counts_and_says_so(exe):
    out, err = run(exe, "rings=30,0", "score=800", "nohud", "pause", "score=800", "miss", **ON)
    # The HUD's pointer reads 0, so neither the game nor the tweak stores the copies.
    assert out == ["30 0 30 0 4 4 4 4 0 800 4", "0 0 30 0 3 4 4 4 800 800 4"]
    assert ("sa2 tweaks: forgiving restarts: the pause menu's restart kept 30 rings (not 0) and 4 "
            "lives (not 3); the HUD's copies did not read, so the HUD may show the game's counts "
            "until they change") in err
    assert ("sa2 tweaks: forgiving restarts: the miss kept the score at 800 (not 0); a result this "
            "run is an assisted one; the HUD's copy did not read, so the HUD may show the game's "
            "score until it changes") in err


def test_an_unreadable_stack_is_left_to_the_game_and_said_once(exe):
    out, err = run(exe, "rings=30,0", "score=800", "nostack", "pause", "rings=30,0", "miss", **ON)
    assert out == ["0 0 0 0 3 4 3 4 0 0 4", "0 0 0 0 2 4 2 4 0 0 4"]
    assert err.count("sa2 tweaks: forgiving restarts: the guest stack at 0x00020080 does not read in "
                     "RestartStageTeardown (0x0043C370), so the caller is unknown and the restart is "
                     "left to the game") == 1


def test_an_unreadable_score_costs_it_as_usual_and_says_so(exe):
    out, err = run(exe, "score=800", "noscore", "miss", "miss", **ON)
    assert out == ["0 0 0 0 3 4 3 4 0 0 4", "0 0 0 0 2 4 2 4 0 0 4"]
    assert err.count("sa2 tweaks: forgiving restarts: the score at 0x0174B050 does not read, so the "
                     "restart is left to the game") == 1


def test_a_reset_that_gives_more_rings_keeps_the_games_count(exe):
    """Final Hazard's reset sets 50 rings (0x0044CA50, level 0x42): a restart with 5 left keeps the
    game's 50 and its HUD copy, and only the life is put back."""
    lifted, _ = run(exe, "lifted:", "backup=50", "rings=5,0", "pause")
    assert lifted == ["50 0 50 0 3 4 3 4 0 0 4"]
    out, err = run(exe, "backup=50", "rings=5,0", "pause", **ON)
    assert out == ["50 0 50 0 4 4 4 4 0 0 4"]
    assert err == [
        "sa2 tweaks: forgiving restarts (SA2_TWEAKS=forgiving-restarts)",
        "sa2 tweaks: forgiving restarts: the pause menu's restart left the game's 50 rings (more than "
        "the 5 before) and 4 lives (not 3)"]


def test_rings_are_kept_per_player_only_where_the_reset_left_fewer(exe):
    """Player 1 had more than the reset's 50 and keeps them; player 2's 3 come back over the reset's
    0; the same restart with 50 for player 1 keeps the game's 50."""
    out, err = run(exe, "backup=50", "rings=60,3", "pause", "rings=50,3", "pause", **ON)
    assert out == ["60 3 60 3 4 4 4 4 0 0 4", "50 3 50 3 4 4 4 4 0 0 4"]
    assert err[1:] == [
        "sa2 tweaks: forgiving restarts: the pause menu's restart kept 60 rings (not 50) and 4 lives "
        "(not 3)",
        "sa2 tweaks: forgiving restarts: the pause menu's restart kept 50 rings and 4 lives (not 3)"]


def test_off_with_a_reset_that_gives_rings_is_the_lifted_bodies_alone(exe):
    steps = ("backup=50", "rings=5,0", "pause", "rings=80,0", "pause", "miss")
    assert run(exe, *steps)[0] == run(exe, "lifted:", *steps)[0]
