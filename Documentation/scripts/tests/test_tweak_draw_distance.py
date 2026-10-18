"""The draw-distance tweak (runtime/tweaks/draw-distance.c) on the two SET object range checks,
0x00488340 (one player) and 0x004881F0 (two players).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted bodies and the
runtime's peek, poke and register accessors. Each stand-in `__real_` does what the lifted body
does with the stack: it reads r^2 at guest ESP + 0x10 (under the return address the lifted CALL
pushed), answers EAX = 1 when the squared distance it is given is below r^2, else 0, and RETs (ESP
+ 4). It records the r^2 it saw, so a case reads the radius the game's check used, and the caller's
slot after the call, which the tweak must have put back.
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
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")

# The guest stack at 0x20000, ESP at 0x20080 on entry. argv: triples (function, d2, r2) with
# function "1p", "2p" or "unreadable" (a 1P call with ESP outside the stack); d2 and r2 are floats
# given as the hex of their bits. Each call prints "<EAX> <r^2 the body saw> <slot after> <ESP moved>",
# the floats as the hex of their bits.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100];
static uint32_t gpr[8];
static uint32_t seen_r2;
static float d2;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(stack + (a - STACK), in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static void *body(void *memory) {
    const uint32_t esp = gpr[SA2_ESP];
    float r2 = 0;
    if (esp >= STACK && esp + 0x14 <= STACK + sizeof stack) memcpy(&r2, stack + (esp + 0x10 - STACK), 4);
    else r2 = -1.0f;                          /* the stand-in's stack is unreadable too */
    memcpy(&seen_r2, &r2, 4);
    gpr[SA2_EAX] = d2 < r2;
    gpr[SA2_ESP] += 4;                        /* the RET */
    return memory;
}
void *__real_sub_488340(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; return body(memory); }
void *__real_sub_4881f0(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; return body(memory); }
void *__wrap_sub_488340(void *state, uint32_t pc, void *memory);
void *__wrap_sub_4881f0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i + 2 < argc; i += 3) {
        const uint32_t d2bits = (uint32_t)strtoul(argv[i + 1], NULL, 16), r2bits = (uint32_t)strtoul(argv[i + 2], NULL, 16);
        memcpy(&d2, &d2bits, 4);
        const int unreadable = strcmp(argv[i], "unreadable") == 0;
        const uint32_t esp = unreadable ? 0x90000u : STACK + 0x80u;
        memset(stack, 0xA5, sizeof stack);
        const uint32_t ret = 0x0048871Cu;
        memcpy(stack + 0x80, &ret, 4);
        memcpy(stack + 0x90, &r2bits, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        int token;
        void *r = strcmp(argv[i], "2p") == 0 ? __wrap_sub_4881f0(&token, 0x004881F0u, &token)
                                             : __wrap_sub_488340(&token, 0x00488340u, &token);
        if (r != &token) return 3;
        uint32_t slot;
        memcpy(&slot, stack + 0x90, 4);
        for (unsigned k = 0; k < sizeof stack; k++)
            if ((k < 0x80 || k >= 0x94) && stack[k] != 0xA5) return 4;   /* nothing else written */
        printf("%u %08x %08x %u\n", gpr[SA2_EAX], seen_r2, slot, gpr[SA2_ESP] - esp);
    }
    return 0;
}
"""


def bits(x: float) -> str:
    return f"{struct.unpack('<I', struct.pack('<f', x))[0]:08x}"


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("draw_distance")
    (d / "driver.c").write_text(DRIVER)
    out = d / "dd"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "draw-distance.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: tuple[str, float, float], **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    argv = [x for fn, d2, r2 in calls for x in (fn, bits(d2), bits(r2))]
    p = subprocess.run([str(exe), *argv], capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


def row(eax: int, seen: float, slot: float) -> str:
    return f"{eax} {bits(seen)} {bits(slot)} 4"


# The default load distance (160000.0, 400 squared), objects at 300, 500, 700 and 900 units, a SET
# object's clip r^2 (load r^2 + 100.0), and the 2P check at the same distances.
R2 = 160000.0
CALLS = (("1p", 300.0 ** 2, R2), ("1p", 500.0 ** 2, R2), ("1p", 700.0 ** 2, R2), ("1p", 900.0 ** 2, R2),
         ("1p", 500.0 ** 2, R2 + 100.0), ("2p", 300.0 ** 2, R2), ("2p", 700.0 ** 2, R2))
OFF = [row(1, R2, R2), row(0, R2, R2), row(0, R2, R2), row(0, R2, R2), row(0, R2 + 100.0, R2 + 100.0),
       row(1, R2, R2), row(0, R2, R2)]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_body_sees_the_callers_radius_and_nothing_is_said(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == OFF
    assert "draw distance" not in err


def test_on_the_body_sees_four_times_r2_and_the_callers_slot_is_put_back(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="draw-distance")
    assert out == [row(1, 640000.0, R2), row(1, 640000.0, R2), row(1, 640000.0, R2), row(0, 640000.0, R2),
                   row(1, 640400.0, R2 + 100.0), row(1, 640000.0, R2), row(1, 640000.0, R2)]
    assert err.splitlines() == [
        "sa2 tweaks: draw distance doubled (SA2_TWEAKS=draw-distance)",
        "sa2 tweaks: draw distance doubled: the first 1P object range check runs at twice the radius "
        "(r^2 160000 -> 640000)",
        "sa2 tweaks: draw distance doubled: the first 2P object range check runs at twice the radius "
        "(r^2 160000 -> 640000)"]


def test_the_radius_doubles_exactly(exe):
    """An object just inside twice the radius is in range, one just outside is not."""
    r = 400.0
    out, _ = run(exe, ("1p", (2 * r - 0.5) ** 2, r * r), ("1p", (2 * r + 0.5) ** 2, r * r),
                 SA2_TWEAKS="draw-distance")
    assert [line.split()[0] for line in out] == ["1", "0"]


def test_r2_zero_stays_zero_and_huge_r2_becomes_infinity(exe):
    out, _ = run(exe, ("1p", 1.0, 0.0), ("1p", 1e30, 3.0e38), SA2_TWEAKS="draw-distance")
    assert out == [row(0, 0.0, 0.0), row(1, float("inf"), 3.0e38)]


def test_ignored_in_a_replay(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="draw-distance", SA2_REPLAY_TICKS="/dev/null")
    assert out == OFF
    assert err == ("sa2 tweaks: SA2_TWEAKS=draw-distance ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_an_unreadable_stack_keeps_the_radius_and_says_so_once(exe):
    out, err = run(exe, ("unreadable", 1.0, R2), ("unreadable", 1.0, R2), ("1p", 500.0 ** 2, R2),
                   SA2_TWEAKS="draw-distance")
    assert out[2] == row(1, 640000.0, R2)
    lines = err.splitlines()
    said = ("sa2 tweaks: draw distance doubled: the guest stack at 0x00090010 does not read, so this 1P "
            "object range check keeps its radius")
    assert lines.count(said) == 1


@pytest.mark.parametrize("target,env", [("x86_64-linux", "  share/game.env:\n"),
                                        ("x86_64-windows", "  share\\game.env:\n")])
def test_the_readme_lists_it_under_game_env_ahead_of_no_event_bars(target, env):
    """draw-distance sorts before no-event-bars, so it opens the README's list, not no-event-bars."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_build_draw_distance_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    readme = rb.player_readme(target)
    listed = [line.split()[0] for line in readme.splitlines() if line.startswith("    SA2_TWEAKS=")]
    assert listed.index("SA2_TWEAKS=draw-distance") < listed.index("SA2_TWEAKS=no-event-bars")
    assert env + "    SA2_TWEAKS=" in readme
    assert env + "    SA2_TWEAKS=no-event-bars " not in readme
