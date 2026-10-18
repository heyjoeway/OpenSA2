"""analog-fix (runtime/tweaks/analog-fix.c): the analog stroke's divisor, the .rdata double at
0x00905790 that WriteAnalogs (0x00441EF0) divides r^3 by, changes from 125000.0 to 1404928.0 once,
on the tweak's first call, and only when it holds exactly 125000.0.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for guest memory and the lifted body. Guest
memory is the 8 bytes at 0x00905790 alone (any other address does not read); sa2_poke counts its
writes. The stand-in `__real_sub_441ef0` does what WriteAnalogs does for player 0: the square
deadzone of 14 off each of x1 and y1, r^2 = x^2 + y^2, stroke = sqrt(r^2) * r^2 / [0x00905790]
in float as the game stores it, held to 1.0. Each argv pair is one call with that stick; the driver
prints "<x1> <y1> <stroke> <divisor after the call>", then "pokes <n>".
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_tweaks as TT  # noqa: E402

ROOT = TT.ROOT
RUNTIME = TT.RUNTIME

# argv[1]: what 0x00905790 holds at the start, a number, or "unreadable" (the peek fails), or
# "readonly:<number>" (it reads that and the poke fails).
DRIVER = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "guest_memory.h"

#define DIVISOR 0x00905790u
static double divisor;
static int readable = 1, writable = 1, pokes;
static int stick_x, stick_y;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (!readable || a != DIVISOR || n != 8) return SA2_E_RANGE;
    memcpy(out, &divisor, 8);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (!writable || a != DIVISOR || n != 8) return SA2_E_RANGE;
    memcpy(&divisor, in, 8);
    pokes++;
    return SA2_OK;
}

static float stroke;
static float deadzone(int v) { return v > 14 ? (float)(v - 14) : v < -14 ? (float)(v + 14) : 0.0f; }
void *__real_sub_441ef0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const float x = deadzone(stick_x), y = deadzone(stick_y);
    if (x == 0.0f && y == 0.0f) { stroke = 0.0f; return memory; }
    const float r2 = x * x + y * y;
    const float s = (float)((double)sqrtf(r2) * r2 / divisor);   /* the game reads the divisor each call */
    stroke = s > 1.0f ? 1.0f : s;
    return memory;
}
void *__wrap_sub_441ef0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    const char *start = argv[1];
    if (strcmp(start, "unreadable") == 0) readable = 0;
    else if (strncmp(start, "readonly:", 9) == 0) { writable = 0; divisor = strtod(start + 9, NULL); }
    else divisor = strtod(start, NULL);
    for (int i = 2; i + 1 < argc; i += 2) {
        stick_x = atoi(argv[i]); stick_y = atoi(argv[i + 1]);
        int token;
        if (__wrap_sub_441ef0(&token, 0x00441EF0u, &token) != &token) return 3;
        printf("%d %d %.6f %.1f\n", stick_x, stick_y, stroke, divisor);
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""

CLEAN = {k: v for k, v in os.environ.items() if k not in TT.VARS}
ON = "sa2 tweaks: analog full throw (SA2_TWEAKS=analog-fix)"
DONE = ("sa2 tweaks: analog full throw: the stroke is full at 112 past the deadzone, not 50 "
        "(0x00905790: 125000.0 -> 1404928.0)")
D = 1404928.0                                    # 112^3
# x1, y1 pairs, gamepad values as measured in a play run (the game's pad fill): at rest, inside the
# deadzone, half a push right (76), the vanilla curve's full point (64), full right and left (224,
# -225), a full diagonal (138, 138); then the keyboard's held directions (127 right, -128 up, and
# both: the fill writes 127 or -128 for a held key).
STICKS = ["0", "0", "10", "-12", "76", "0", "64", "0", "224", "0", "-225", "0", "138", "138",
          "127", "0", "0", "-128", "127", "-128"]


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not TT.ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {TT.ZIG}")
    d = tmp_path_factory.mktemp("analog_fix")
    (d / "driver.c").write_text(DRIVER)
    out = d / "af"
    done = subprocess.run([str(TT.ZIG), "cc", *TT.CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "analog-fix.c"), "-lm", "-o", str(out)],
                          capture_output=True, text=True, env=TT.zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, start: str, sticks: list[str], **env: str) -> tuple[list[tuple], int, list[str]]:
    p = subprocess.run([str(exe), start, *sticks], capture_output=True, text=True, timeout=30,
                       env=dict(CLEAN, **env))
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    rows = [(int(a), int(b), float(s), float(d)) for a, b, s, d in (ln.split() for ln in lines[:-1])]
    assert lines[-1].startswith("pokes ")
    return rows, int(lines[-1].split()[1]), p.stderr.splitlines()


def curve(x1: int, y1: int, divisor: float) -> float:
    """WriteAnalogs's stroke: min(1, r^3 / divisor), r past the square deadzone of 14."""
    dz = [v - 14 if v > 14 else v + 14 if v < -14 else 0 for v in (x1, y1)]
    r = math.hypot(*dz)
    return min(1.0, r ** 3 / divisor)


def strokes(rows) -> list[float]:
    return [s for _, _, s, _ in rows]


def expect(sticks: list[str], divisor: float) -> list[float]:
    return [curve(int(sticks[i]), int(sticks[i + 1]), divisor) for i in range(0, len(sticks), 2)]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_vanilla_curve_and_writes_nothing(exe, env):
    rows, pokes, err = run(exe, "125000", STICKS, **env)
    assert pokes == 0 and all(d == 125000.0 for *_, d in rows)
    assert strokes(rows) == pytest.approx(expect(STICKS, 125000.0), abs=1e-5)
    # the vanilla curve: x1 64 (r = 50) is a full stroke already, and so is half a push
    assert strokes(rows)[2:4] == [1.0, 1.0]
    assert err == ([] if not env.get("SA2_TWEAKS") else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


def test_on_writes_the_divisor_once_before_the_first_call(exe):
    rows, pokes, err = run(exe, "125000", STICKS, SA2_TWEAKS="analog-fix")
    assert pokes == 1 and all(d == D for *_, d in rows)
    assert err == [ON, DONE]
    got = strokes(rows)
    assert got == pytest.approx(expect(STICKS, D), abs=1e-5)
    assert got[:2] == [0.0, 0.0]                              # at rest and inside the deadzone
    assert got[2] == pytest.approx(62 ** 3 / D, abs=1e-5)     # half a push: 0.170, a walk
    assert got[3] == pytest.approx(50 ** 3 / D, abs=1e-5)     # 0.089 where vanilla was full
    assert got[4:7] == [1.0, 1.0, 1.0]                        # a full push right, left, diagonal
    assert got[7:10] == [1.0, 1.0, 1.0]                       # the keyboard: still full


def test_full_stroke_comes_at_r_112_in_every_direction(exe):
    """Past the deadzone, the stroke is 1.0 from r = 112 on and under it below, all round; with
    the tweak off, from r = 50."""
    at, under = [], []
    for deg in range(0, 360, 5):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        for out, r in ((at, 112.5), (under, 110.0)):
            x, y = r * c, r * s
            out += [str(round(x + math.copysign(14, x))) if abs(x) > 0.5 else "0",
                    str(round(y + math.copysign(14, y))) if abs(y) > 0.5 else "0"]
    n = len(at) // 2
    rows, _, _ = run(exe, "125000", at + under, SA2_TWEAKS="analog-fix")
    got = strokes(rows)
    assert got == pytest.approx(expect(at + under, D), abs=1e-5)
    assert min(got[:n]) > 0.99 and max(got[n:]) < 0.97
    vanilla, _, _ = run(exe, "125000", under)
    assert strokes(vanilla) == [1.0] * n


@pytest.mark.parametrize("start, said", [
    ("1000000", "0x00905790 holds 1000000, not 125000.0"),
    ("125000.5", "0x00905790 holds 125000.5, not 125000.0"),
    ("0", "0x00905790 holds 0, not 125000.0"),
])
def test_another_value_is_left_alone_and_named(exe, start, said):
    rows, pokes, err = run(exe, start, STICKS, SA2_TWEAKS="analog-fix")
    assert pokes == 0 and all(d == float(start) for *_, d in rows)
    assert err == [ON, f"sa2 tweaks: analog full throw: {said}, so the stick's curve is left alone"]


def test_an_unreadable_divisor_is_left_alone_and_named(exe):
    _, pokes, err = run(exe, "unreadable", ["76", "0", "76", "0"], SA2_TWEAKS="analog-fix")
    assert pokes == 0
    assert err == [ON, "sa2 tweaks: analog full throw: 0x00905790 does not read (-1), so the "
                       "stick's curve is left alone"]


def test_a_failed_write_is_named_and_the_curve_stays_vanilla(exe):
    rows, pokes, err = run(exe, "readonly:125000", ["76", "0", "76", "0"], SA2_TWEAKS="analog-fix")
    assert pokes == 0 and strokes(rows) == [1.0, 1.0]
    assert err == [ON, "sa2 tweaks: analog full throw: 0x00905790 does not write (-1), so the "
                       "stick's curve is left alone"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    rows, pokes, err = run(exe, "125000", STICKS, SA2_TWEAKS="analog-fix", **{replay: "/dev/null"})
    assert pokes == 0
    assert strokes(rows) == pytest.approx(expect(STICKS, 125000.0), abs=1e-5)
    assert err == ["sa2 tweaks: SA2_TWEAKS=analog-fix ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]
