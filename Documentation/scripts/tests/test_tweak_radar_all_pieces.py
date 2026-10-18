"""radar-all-pieces (runtime/tweaks/radar-all-pieces.c): the later treasure-hunt pieces' radar icon,
drawn by 0x00739FE0 from the radar's update at 0x0073A7C4, is the distance-banded one the first
piece gets.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted drawing function and
the runtime's peek and register accessors. The driver's guest memory holds a stack and the image's
three bounds (500.0 at 0x00904F30, 300.0f at 0x0109E030, 100.0 at 0x00904E30). Each call lays out
the stack as the radar's update leaves it at the callee's entry: the return address at [ESP], the
slot (the pushed ESI) at [ESP+4], the blink clock (EBP) at [ESP+8], and the piece's distance, the
caller's float at its [esp+0x14], at [ESP+0x20]. The stand-in `__real_sub_739fe0` records the icon
in ECX and the slot it sees and pops the return address, as the lifted RET does.
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
LATER, TRACKED, TWO_P = "0x0073A7C9", "0x0073A816", "0x0073A92B"   # return addresses
ON = {"SA2_TWEAKS": "radar-all-pieces"}

# argv: optional "bounds=<500>,<300>,<100>" first (the image's by default), then groups of
# (return address | "unreadable", ECX, slot on the stack, distance); each call prints
# "<ECX the lifted body saw> <slot it saw> <ESP moved>".
DRIVER = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100];
static double b500 = 500.0, b100 = 100.0;
static float b300 = 300.0f;
static uint32_t gpr[8];

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a >= STACK && a + n <= STACK + sizeof stack) { memcpy(out, stack + (a - STACK), n); return SA2_OK; }
    if (a == 0x00904F30u && n == 8) { memcpy(out, &b500, 8); return SA2_OK; }
    if (a == 0x0109E030u && n == 4) { memcpy(out, &b300, 4); return SA2_OK; }
    if (a == 0x00904E30u && n == 8) { memcpy(out, &b100, 8); return SA2_OK; }
    return 1;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static uint32_t seen_ecx, seen_slot;
void *__real_sub_739fe0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    seen_ecx = gpr[SA2_ECX];
    if (sa2_peek(gpr[SA2_ESP] + 4, 4, &seen_slot) != SA2_OK) seen_slot = 0xFFFFFFFFu;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_739fe0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int i = 1;
    if (argc > 1 && strncmp(argv[1], "bounds=", 7) == 0) {
        b500 = strtod(argv[1] + 7, &argv[1]);
        b300 = strtof(argv[1] + 1, &argv[1]);
        b100 = strtod(argv[1] + 1, NULL);
        i = 2;
    }
    for (; i + 3 < argc; i += 4) {
        const int unreadable = strcmp(argv[i], "unreadable") == 0;
        const uint32_t ret = unreadable ? 0 : (uint32_t)strtoul(argv[i], NULL, 0);
        const uint32_t ecx = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        const uint32_t slot = (uint32_t)strtoul(argv[i + 2], NULL, 0), clock = 0x1234u;
        const float d = strcmp(argv[i + 3], "nan") == 0 ? NAN : strtof(argv[i + 3], NULL);
        const uint32_t esp = unreadable ? 0x90000u : STACK + 0x40u;
        memset(stack, 0xCC, sizeof stack);
        if (!unreadable) {
            memcpy(stack + 0x40, &ret, 4);
            memcpy(stack + 0x44, &slot, 4);
            memcpy(stack + 0x48, &clock, 4);
            memcpy(stack + 0x60, &d, 4);
        }
        gpr[SA2_ESP] = esp;
        gpr[SA2_ECX] = ecx;
        seen_slot = 0xFFFFFFFFu;
        int token;
        if (__wrap_sub_739fe0(&token, 0x00739FE0u, &token) != &token) return 3;
        printf("0x%02X %d %u\n", seen_ecx, (int)seen_slot, gpr[SA2_ESP] - esp);
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
    d = tmp_path_factory.mktemp("radar")
    (d / "driver.c").write_text(DRIVER)
    out = d / "radar"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "radar-all-pieces.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def later(slot: int, d: str) -> tuple[str, ...]:
    """The radar update's call for a later piece: ECX = slot + 7, the slot on the stack."""
    return (LATER, str(slot + 7), str(slot), d)


# Slots 1 and 2 (later pieces) at the spec's four distances, and slot 0 at one of them: the far,
# 300-500, 100-300 and near bands.
CALLS = (*later(1, "600"), *later(2, "400"), *later(1, "200"), *later(2, "50"), *later(0, "350"))
PLAIN = ["0x08 1 4", "0x09 2 4", "0x08 1 4", "0x09 2 4", "0x07 0 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_draws_the_plain_icon_and_says_nothing_of_the_radar(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == PLAIN
    assert not any("radar" in line for line in err)


def test_on_each_later_piece_gets_the_tracked_paths_icon(exe):
    out, err = run(exe, *CALLS, **ON)
    # 600 > 500: slot+4; 400 > 300: slot+0x10; 200 > 100: slot+0xD; 50: slot+0xA.
    assert out == ["0x05 1 4", "0x12 2 4", "0x0E 1 4", "0x0C 2 4", "0x10 0 4"]
    assert err == [
        "sa2 tweaks: radar tracks all pieces (SA2_TWEAKS=radar-all-pieces)",
        "sa2 tweaks: radar tracks all pieces: slot 1 tracked (icon 0x05 for 0x08, distance 600.0)",
        "sa2 tweaks: radar tracks all pieces: slot 2 tracked (icon 0x12 for 0x09, distance 400.0)",
        "sa2 tweaks: radar tracks all pieces: slot 0 tracked (icon 0x10 for 0x07, distance 350.0)"]


@pytest.mark.parametrize("d, icon", [
    ("500", 0x10), ("500.0001", 0x04),          # 500.0 itself is not "> 500"
    ("300", 0x0D), ("300.00003", 0x10),         # the float just above 300.0f
    ("100", 0x0A), ("100.00001", 0x0D),
    ("0", 0x0A), ("-1", 0x0A), ("nan", 0x0A),  # a NaN fails every compare, as x87's do
    ("1e30", 0x04)])
def test_the_bands_edges_are_the_tracked_paths(exe, d, icon):
    out, _ = run(exe, *later(0, d), **ON)
    assert out == [f"0x{icon:02X} 0 4"]


def test_the_bounds_are_read_from_the_image(exe):
    out, _ = run(exe, "bounds=1000,800,10", *later(1, "600"), *later(1, "900"), *later(1, "5"), **ON)
    assert out == ["0x0E 1 4", "0x11 1 4", "0x0B 1 4"]


@pytest.mark.parametrize("ret", [TRACKED, TWO_P, "0x005FB508"])
def test_other_callers_are_left_alone(exe, ret):
    """The first piece's own call (0x0073A811), the two-player radar and any other caller."""
    out, err = run(exe, ret, "9", "2", "50", ret, "0x0C", "2", "600", **ON)
    assert out == ["0x09 2 4", "0x0C 2 4"]
    assert err == ["sa2 tweaks: radar tracks all pieces (SA2_TWEAKS=radar-all-pieces)"]


def test_a_slot_that_does_not_match_is_drawn_as_it_is_and_said_once(exe):
    out, err = run(exe, LATER, "8", "0", "50", LATER, "12", "5", "50", *later(2, "50"), **ON)
    assert out == ["0x08 0 4", "0x0C 5 4", "0x0C 2 4"]
    assert err[1:] == [
        "sa2 tweaks: radar tracks all pieces: icon 0x08 with slot 0 on the stack is no piece's "
        "plain icon, so it is drawn as it is",
        "sa2 tweaks: radar tracks all pieces: slot 2 tracked (icon 0x0C for 0x09, distance 50.0)"]


def test_an_unreadable_stack_draws_the_plain_icon_and_says_so_once(exe):
    out, err = run(exe, "unreadable", "8", "1", "50", "unreadable", "8", "1", "50",
                   *later(1, "50"), **ON)
    assert out[:2] == ["0x08 -1 4", "0x08 -1 4"] and out[2] == "0x0B 1 4"
    assert err[1:] == [
        "sa2 tweaks: radar tracks all pieces: the guest bytes at 0x00090000 do not read, so the "
        "later pieces keep the plain icon",
        "sa2 tweaks: radar tracks all pieces: slot 1 tracked (icon 0x0B for 0x08, distance 50.0)"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, **ON, **{replay: "/dev/null"})
    assert out == PLAIN
    assert err == ["sa2 tweaks: SA2_TWEAKS=radar-all-pieces ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)"]
