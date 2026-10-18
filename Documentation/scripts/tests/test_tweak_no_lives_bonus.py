"""The no-lives-bonus tweak (runtime/tweaks/no-lives-bonus.c): AddLives (0x0044CB10) called from
AddRings' ring bonus (return address 0x0044CEAB) returns at once, so no life, no HUD change and no
jingle; its other callers, and every call with the tweak off (or in a replay), run the lifted body.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted functions and the runtime's
peek and register accessors. The guest holds Life_Count at 0x0174B024 and RingCount at 0x0174B028
(two int16 each), the HUD's pointer at 0x01A5A230 (its + 8 and + 0xA are the lives' copies, + 0xC
and + 0xE the rings'), and a stack at 0x20000.

The stand-in `__real_sub_44cb10` does what the lifted AddLives does (0x0044CB10-0x0044CB85): when
BX > 0 it plays sound 0x1008 (the driver counts it), adds BX to the player's count, clamps it to
0..99, stores it in the HUD's copy, and RETs (EIP = [ESP], ESP + 4). The stand-in AddRings does what
0x0044CE10-0x0044CEBE does: adds the rings, clamps them to 0..999, and when the hundreds went up and
the 2P flag is 0, pushes 0x0044CEAB and calls the wrap with EAX = the player and BX = the hundreds
gained, as the lifted CALL does under -wrap; then it stores the rings in the HUD's copy. The other
callers push their own return addresses. Each step prints
"<rings 1> <rings 2> <lives 1> <lives 2> <HUD lives 1> <HUD lives 2> <jingles> <returned>", where
<returned> is 1 when the call came back with ESP where it was before the push and EIP at the
return address.
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
ON = "sa2 tweaks: ring 1-ups off (SA2_TWEAKS=no-lives-bonus)"

# argv: the two starting ring counts and the two starting life counts, then steps:
#   rings:P:N   AddRings for player P (0 or 1) with N rings
#   2p          the 2P flag is set for the steps after it
#   dig:P       GiveDiggableItem's call (return 0x00737B18), one life for player P
#   minimal:P   MinimalCounterExecutor's call (return 0x00489341), one life for player P
#   other:P     a call from 0x00401234, one life for player P
#   nostack     the guest stack stops reading for the steps after it
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define LIVES 0x0174B024u
#define RINGS 0x0174B028u
#define HUDP  0x01A5A230u
#define HUD   0x01A60000u
#define STACK 0x00020000u
static int16_t lives[2], rings[2], hud[8];
static uint32_t hudp = HUD;
static uint8_t stack[0x100];
static uint32_t gpr[SA2_GPR_COUNT];
static int stack_reads = 1, two_p, jingles;

static void *where(uint32_t a, uint32_t n) {
    if (a >= LIVES && a + n <= LIVES + 4) return (uint8_t *)lives + (a - LIVES);
    if (a >= RINGS && a + n <= RINGS + 4) return (uint8_t *)rings + (a - RINGS);
    if (a >= HUDP && a + n <= HUDP + 4) return (uint8_t *)&hudp + (a - HUDP);
    if (a >= HUD && a + n <= HUD + sizeof hud) return (uint8_t *)hud + (a - HUD);
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

static uint32_t ld32(uint32_t a) { uint32_t v = 0; memcpy(&v, stack + (a - STACK), 4); return v; }

/* AddLives, 0x0044CB10: EAX = the player, BX = how many. */
void *__real_sub_44cb10(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const int p = gpr[SA2_EAX] & 1;
    const int16_t bx = (int16_t)gpr[SA2_EBX];
    if (bx > 0) jingles++;                      /* sound 0x1008 at 0x0044CB23 */
    lives[p] = (int16_t)(lives[p] + bx);
    if (lives[p] > 99 || (lives[p] < 0 && bx > 0)) lives[p] = 99;
    hud[4 + p] = lives[p];                      /* [[0x01A5A230] + 8 + 2p] */
    gpr[SA2_EAX] = (uint16_t)lives[p];
    gpr[SA2_EIP] = ld32(gpr[SA2_ESP]);          /* the RET */
    gpr[SA2_ESP] += 4;
    return memory;
}
void *__wrap_sub_44cb10(void *state, uint32_t pc, void *memory);

static int returned = 1;
static void call(uint32_t ret, int p, uint32_t bx) {
    const uint32_t esp = STACK + 0x80u;
    memcpy(stack + (esp - 4 - STACK), &ret, 4);  /* the lifted CALL's push */
    gpr[SA2_ESP] = esp - 4;
    gpr[SA2_EIP] = 0x0044CB10u;
    gpr[SA2_EAX] = (uint32_t)p;
    gpr[SA2_EBX] = bx;
    int token;
    if (__wrap_sub_44cb10(&token, 0x0044CB10u, &token) != &token) exit(3);
    returned = gpr[SA2_ESP] == esp && gpr[SA2_EIP] == ret;
}

/* AddRings, 0x0044CE10: EAX = the player, EDX = the rings to add. */
static void add_rings(int p, int n) {
    const int old = rings[p];
    int now = old + n;
    if (now > 999) now = 999;
    if (now < 0) now = 0;
    rings[p] = (int16_t)now;
    if (old / 100 < now / 100 && !two_p) call(0x0044CEABu, p, (uint32_t)(now / 100 - old / 100));
    hud[6 + p] = rings[p];                      /* 0x0044CEB9 */
}

int main(int argc, char **argv) {
    if (argc < 5) return 2;
    rings[0] = (int16_t)atoi(argv[1]); rings[1] = (int16_t)atoi(argv[2]);
    lives[0] = (int16_t)atoi(argv[3]); lives[1] = (int16_t)atoi(argv[4]);
    hud[4] = lives[0]; hud[5] = lives[1];
    for (int i = 5; i < argc; i++) {
        const char *s = argv[i];
        int p = 0, n = 0;
        returned = 1;
        if (!strcmp(s, "2p")) { two_p = 1; continue; }
        if (!strcmp(s, "nostack")) { stack_reads = 0; continue; }
        if (sscanf(s, "rings:%d:%d", &p, &n) == 2) add_rings(p, n);
        else if (sscanf(s, "dig:%d", &p) == 1) call(0x00737B18u, p, 1);
        else if (sscanf(s, "minimal:%d", &p) == 1) call(0x00489341u, p, 1);
        else if (sscanf(s, "other:%d", &p) == 1) call(0x00401234u, p, 1);
        else return 2;
        printf("%d %d %d %d %d %d %d %d\n", rings[0], rings[1], lives[0], lives[1], hud[4], hud[5],
               jingles, returned);
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
    d = tmp_path_factory.mktemp("no_lives_bonus")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-lives-bonus.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *args: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


# 95 rings and 4 lives: 10 rings cross 100, 3 more stay under 200, 100 more cross 200, a
# 250-ring gain crosses 300, 400 and 500 at once, then player 2 crosses 100.
STEPS = ("95", "0", "4", "4", "rings:0:10", "rings:0:3", "rings:0:100", "rings:0:250", "rings:1:120")
GAME = ["105 0 5 4 5 4 1 1", "108 0 5 4 5 4 1 1", "208 0 6 4 6 4 2 1", "458 0 8 4 8 4 3 1",
        "458 120 8 5 8 5 4 1"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_gives_the_ring_bonus_as_the_game_does(exe, env):
    out, err = run(exe, *STEPS, **env)
    assert out == GAME
    assert "ring 1-ups" not in err


def test_on_the_ring_bonus_gives_no_life_and_no_jingle(exe):
    out, err = run(exe, *STEPS, SA2_TWEAKS="no-lives-bonus")
    assert out == ["105 0 4 4 4 4 0 1", "108 0 4 4 4 4 0 1", "208 0 4 4 4 4 0 1",
                   "458 0 4 4 4 4 0 1", "458 120 4 4 4 4 0 1"]
    assert err.splitlines() == [
        ON,
        "sa2 tweaks: ring 1-ups off: the extra life for 100 rings was not given (player 1 has 105 "
        "rings and stays at 4 lives)"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *STEPS, SA2_TWEAKS="no-lives-bonus", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=no-lives-bonus ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_the_other_callers_still_give_their_lives_and_say_so_once(exe):
    steps = ("0", "0", "4", "4", "dig:0", "minimal:1", "other:0", "dig:0", "rings:0:100", "other:1")
    out, err = run(exe, *steps, SA2_TWEAKS="no-lives-bonus")
    assert out == ["0 0 5 4 5 4 1 1", "0 0 5 5 5 5 2 1", "0 0 6 5 6 5 3 1", "0 0 7 5 7 5 4 1",
                   "100 0 7 5 7 5 4 1", "100 0 7 6 7 6 5 1"]
    assert err.splitlines() == [
        ON,
        "sa2 tweaks: ring 1-ups off: the life from GiveDiggableItem (0x00737B18) is not the ring "
        "count's, so it is given",
        "sa2 tweaks: ring 1-ups off: the life from MinimalCounterExecutor (0x00489341) is not the "
        "ring count's, so it is given",
        "sa2 tweaks: ring 1-ups off: AddLives was called from 0x00401234, which is not the ring "
        "count's call, so the life is given",
        "sa2 tweaks: ring 1-ups off: the extra life for 100 rings was not given (player 1 has 100 "
        "rings and stays at 7 lives)"]
    assert run(exe, *steps)[0] == ["0 0 5 4 5 4 1 1", "0 0 5 5 5 5 2 1", "0 0 6 5 6 5 3 1",
                                   "0 0 7 5 7 5 4 1", "100 0 8 5 8 5 5 1", "100 0 8 6 8 6 6 1"]


def test_the_2p_game_never_asks_and_the_tweak_does_nothing(exe):
    steps = ("90", "90", "2", "2", "2p", "rings:0:20", "rings:1:20")
    for env in ({}, {"SA2_TWEAKS": "no-lives-bonus"}):
        out, err = run(exe, *steps, **env)
        assert out == ["110 90 2 2 2 2 0 1", "110 110 2 2 2 2 0 1"]
        assert "ring 1-ups off:" not in err


def test_an_unreadable_stack_gives_the_life_and_says_so(exe):
    out, err = run(exe, "95", "0", "4", "4", "nostack", "rings:0:10", "rings:0:100",
                   SA2_TWEAKS="no-lives-bonus")
    assert out == ["105 0 5 4 5 4 1 1", "205 0 6 4 6 4 2 1"]
    assert err.splitlines() == [
        ON,
        "sa2 tweaks: ring 1-ups off: the guest stack at 0x0002007C does not read, so the caller is "
        "unknown and the life is given"]
