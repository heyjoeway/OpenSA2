"""The gold-beetle-stays tweak (runtime/tweaks/gold-beetle-stays.c) on the Gold Beetle's flying
state, 0x00505FA0.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted body and the
runtime's peek, poke and register accessors. The guest memory is a taskwk (its state byte at +0
and the u16 timer at +6), an ewp (the s16 lifetime at +0x48) and the stack, where [ESP+8] holds
the ewp, as E_GOLD's call at 0x00506576 leaves it, and the TimeStopped byte at 0x0174AFF7. The
stand-in `__real_sub_505fa0` does what the
lifted body does to the timer (0x00506042-0x00506069): unless TimeStopped, the timer goes up by
one (a u16, so 0xFFFF wraps to 0, as `inc word [esi+6]` does); when it is then above the lifetime
(u16 against s16, a signed compare), the state becomes 4 and the timer 0. Each frame prints "<state> <timer>" after the call.
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
TASKWK = 0x00C00000

# argv[1]: the beetle "<lifetime>,<timer>[,<flags>]", flags any of s (TimeStopped), r (the ewp
# does not read), w (the timer does not write), t (a second beetle: another taskwk), x (the
# TimeStopped byte does not read). argv[2]:
# frames to run. Prints "<state> <timer>" after each frame and stops after the state leaves 1.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define TASKWK 0x00C00000u
#define TASKWK2 0x00C00100u
#define EWP    0x00C01000u
#define STACK  0x00020000u
#define TIME_STOPPED 0x0174AFF7u
static uint8_t taskwk[0x200], ewp[0x200], stack[0x40], stopped;
static uint32_t gpr[8];
static int unreadable, locked, nostop;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= TASKWK && a + n <= TASKWK + sizeof taskwk) return taskwk + (a - TASKWK);
    if (!unreadable && a >= EWP && a + n <= EWP + sizeof ewp) return ewp + (a - EWP);
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    if (!nostop && a == TIME_STOPPED && n == 1) return &stopped;
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
    if (!p || locked) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

/* The lifted body's timer: 0x00506042-0x00506069. */
void *__real_sub_505fa0(void *state, uint32_t pc, void *memory) {
    (void)state;
    if (pc != 0x00505FA0u) return NULL;
    uint32_t ew = 0;
    memcpy(&ew, stack + 8, 4);
    uint8_t *tw = taskwk + (gpr[SA2_EAX] - TASKWK);
    uint8_t *e = ewp + (ew - EWP);
    if (tw[0] == 5) return memory;
    uint16_t t;
    int16_t life;
    memcpy(&t, tw + 6, 2);
    memcpy(&life, e + 0x48, 2);
    if (!stopped) t++;
    if ((int32_t)t > (int32_t)life) { tw[0] = 4; t = 0; }
    memcpy(tw + 6, &t, 2);
    return memory;
}
void *__wrap_sub_505fa0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char *w = argv[1];
    int16_t life = (int16_t)strtol(w, &w, 0);
    uint16_t t = (uint16_t)strtoul(w + 1, &w, 0);
    const char *flags = *w == ',' ? w + 1 : "";
    stopped = strchr(flags, 's') != NULL;
    nostop = strchr(flags, 'x') != NULL;
    unreadable = strchr(flags, 'r') != NULL;
    locked = strchr(flags, 'w') != NULL;
    const uint32_t tw = strchr(flags, 't') ? TASKWK2 : TASKWK;
    const uint32_t ew = EWP;
    taskwk[tw - TASKWK] = 1;
    memcpy(taskwk + (tw - TASKWK) + 6, &t, 2);
    memcpy(ewp + 0x48, &life, 2);
    memcpy(stack + 8, &ew, 4);
    const int frames = atoi(argv[2]);
    for (int f = 0; f < frames; f++) {
        gpr[SA2_EAX] = tw;
        gpr[SA2_ESP] = STACK;
        int token;
        if (__wrap_sub_505fa0(&token, 0x00505FA0u, &token) != &token) return 3;
        uint16_t now;
        memcpy(&now, taskwk + (tw - TASKWK) + 6, 2);
        printf("%u %u\n", taskwk[tw - TASKWK], now);
        if (taskwk[tw - TASKWK] != 1) break;
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
    d = tmp_path_factory.mktemp("gold_beetle")
    (d / "driver.c").write_text(DRIVER)
    out = d / "gb"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "gold-beetle-stays.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, beetle: str, frames: int, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), beetle, str(frames)], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


# Metal Harbor's beetle: a lifetime of 20 frames, the timer from 0. It flies frames 1..20 and on
# the 21st (timer 21 > 20) leaves: state 4, timer 0.
GAME = [f"1 {t}" for t in range(1, 21)] + ["4 0"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_beetle_leaves_when_its_lifetime_is_over(exe, env):
    out, err = run(exe, "20,0", 40, **env)
    assert out == GAME
    assert "gold beetle" not in err


def test_off_in_a_replay(exe):
    out, err = run(exe, "20,0", 40, SA2_TWEAKS="gold-beetle-stays", SA2_REPLAY_TICKS="/dev/null")
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=gold-beetle-stays ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_on_the_beetle_flies_as_in_the_game_then_stays(exe):
    out, err = run(exe, "20,0", 40, SA2_TWEAKS="gold-beetle-stays")
    assert out[:20] == GAME[:20]
    assert out[20:] == ["1 20"] * 20
    assert err.splitlines() == [
        "sa2 tweaks: gold beetle stays (SA2_TWEAKS=gold-beetle-stays)",
        f"sa2 tweaks: gold beetle stays: gold beetle held at t=20 (lifetime 20 frames, taskwk "
        f"0x{TASKWK:08X})"]


def test_on_a_timer_already_past_the_edge_is_kept_below_it(exe):
    """A timer above the lifetime (none in the game reaches one; any write would) is kept at the
    lifetime, not zeroed, and the beetle stays."""
    out, err = run(exe, "20,300", 3, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["1 20"] * 3
    assert "gold beetle held at t=300 (lifetime 20 frames" in err


def test_on_while_time_is_stopped(exe):
    """TimeStopped holds the timer, and the test still runs: there is no increment, so the timer
    is set to the lifetime itself."""
    out, _ = run(exe, "20,20,s", 3, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["1 20"] * 3
    off, _ = run(exe, "20,21,s", 3)
    assert off == ["4 0"]


def test_on_each_beetle_is_named_once(exe):
    _, err = run(exe, "20,25,t", 5, SA2_TWEAKS="gold-beetle-stays")
    assert err.count("gold beetle held") == 1
    assert "taskwk 0x00C00100" in err


def test_off_a_lifetime_of_0_leaves_on_its_first_frame(exe):
    out, err = run(exe, "0,0", 5)
    assert out == ["4 0"]
    assert err == ""


def test_on_a_lifetime_of_0_stays(exe):
    """rot.x's low byte 0 gives a lifetime of 0 (never less: (rot.x & 0xFF) * 10). The wrap's
    0xFFFF wraps to 0 in the body's 16-bit increment, which passes the test, as the Codes.lst JMP
    keeps such a beetle."""
    out, err = run(exe, "0,0", 5, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["1 0"] * 5
    assert err.splitlines() == [
        "sa2 tweaks: gold beetle stays (SA2_TWEAKS=gold-beetle-stays)",
        f"sa2 tweaks: gold beetle stays: gold beetle held at t=0 (lifetime 0 frames, taskwk "
        f"0x{TASKWK:08X})"]


def test_on_a_lifetime_of_0_stays_while_time_is_stopped(exe):
    """No increment while TimeStopped, so 0xFFFF would compare above 0 and leave: the wrap writes
    the lifetime, 0."""
    out, _ = run(exe, "0,0,s", 3, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["1 0"] * 3
    off, _ = run(exe, "0,1,s", 3)
    assert off == ["4 0"]


def test_on_an_unreadable_time_stopped_leaves_as_usual_and_says_so(exe):
    out, err = run(exe, "20,19,x", 3, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["1 20", "4 0"]
    assert err.splitlines() == [
        "sa2 tweaks: gold beetle stays (SA2_TWEAKS=gold-beetle-stays)",
        "sa2 tweaks: gold beetle stays: TimeStopped at 0x0174AFF7 does not read, so a gold beetle "
        "leaves as usual"]


def test_on_an_unreadable_ewp_leaves_as_usual_and_says_so(exe):
    """The stand-in body reads the ewp itself; the wrap's peek fails and it changes nothing."""
    out, err = run(exe, "20,20,r", 1, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["4 0"]
    assert ("sa2 tweaks: gold beetle stays: a gold beetle's work does not read (taskwk 0x00C00000, "
            "ewp 0x00C01000), so it leaves as usual") in err.splitlines()


def test_on_an_unwritable_timer_leaves_as_usual_and_says_so(exe):
    out, err = run(exe, "20,20,w", 2, SA2_TWEAKS="gold-beetle-stays")
    assert out == ["4 0"]
    assert ("sa2 tweaks: gold beetle stays: a gold beetle's timer at 0x00C00006 does not write, so "
            "it leaves as usual") in err.splitlines()
    assert "held" not in err
