"""The no-loading-tips tweak (runtime/tweaks/no-loading-tips.c) on the loading screen's frame,
0x00428010.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted function and the
runtime's peek and register accessors. The stand-in `__real_sub_428010` marks that it ran (EAX =
0x10AD, as the drawing clobbers EAX), and does the RET that pops the return address into EIP
(ESP + 4). Each call puts a caller's return address at guest [ESP] first, as the lifted CALL
does, so a case reads what the caller would see: whether the body ran, EIP, and how far ESP moved.
"""
from __future__ import annotations

import importlib.util
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
STAGE, CURRENT, LOOP = "0x004285DC", "0x00428933", "0x0041B338"   # the three callers' returns
OTHER = "0x00401234"                                              # no caller of 0x00428010

# The guest stack at 0x20000. argv: return addresses, or "unreadable" for an ESP outside the
# stack; each call prints "<ran> <EIP> <ESP moved> <EBX>", EBX a register nothing may touch.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100];
static uint32_t gpr[SA2_GPR_COUNT];

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_428010(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    gpr[SA2_EAX] = 0x10ADu;                   /* the frame was drawn */
    uint32_t r = 0;
    sa2_peek(gpr[SA2_ESP], 4, &r);
    gpr[SA2_EIP] = r;                          /* the RET */
    gpr[SA2_ESP] += 4;
    return memory;
}
void *__wrap_sub_428010(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        const int bad = strcmp(argv[i], "unreadable") == 0;
        const uint32_t ret = bad ? 0x004285DCu : (uint32_t)strtoul(argv[i], NULL, 0);
        const uint32_t esp = STACK + 0x80u;
        memcpy(stack + 0x80, &ret, 4);
        gpr[SA2_ESP] = bad ? 0x90000u : esp;
        gpr[SA2_EAX] = 0;
        gpr[SA2_EBX] = 0xB0B0B0B0u;
        gpr[SA2_EIP] = 0x00428010u;
        int token;
        if (__wrap_sub_428010(&token, 0x00428010u, &token) != &token) return 3;
        if (bad) {   /* nothing on that stack to return to: only whether the body ran */
            printf("%u - -\n", gpr[SA2_EAX] == 0x10ADu);
            continue;
        }
        printf("%u 0x%08X %u 0x%08X\n", gpr[SA2_EAX] == 0x10ADu, gpr[SA2_EIP], gpr[SA2_ESP] - esp,
               gpr[SA2_EBX]);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("no_loading_tips")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-loading-tips.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


CALLS = (STAGE, CURRENT, LOOP, STAGE, OTHER)
DRAWN = [f"1 {r} 4 0xB0B0B0B0" for r in CALLS]
SKIPPED = [f"0 {r} 4 0xB0B0B0B0" for r in CALLS]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_draws_every_frame_and_says_nothing_of_it(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == DRAWN
    assert "loading-tips" not in err


def test_on_returns_to_each_caller_without_drawing(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-loading-tips")
    assert out == SKIPPED
    assert err.splitlines() == [
        "sa2 tweaks: loading-tips off (SA2_TWEAKS=no-loading-tips)",
        "sa2 tweaks: loading-tips off: loading screen not drawn (the stage load, 0x00428470)",
        "sa2 tweaks: loading-tips off: loading screen not drawn (the load over the current frame, "
        "0x00428740)",
        "sa2 tweaks: loading-tips off: loading screen not drawn (the game loop's loading frame, "
        "0x0041B2D0)",
        "sa2 tweaks: loading-tips off: loading screen not drawn (a call returning to 0x00401234)"]


def test_on_beside_the_other_tweak(exe):
    out, err = run(exe, STAGE, SA2_TWEAKS="no-event-bars,no-loading-tips")
    assert out == ["0 0x004285DC 4 0xB0B0B0B0"]
    assert err.splitlines()[0] == ("sa2 tweaks: event-bars off, loading-tips off "
                                   "(SA2_TWEAKS=no-event-bars,no-loading-tips)")


def test_an_unreadable_stack_draws_the_frame_and_says_so_once(exe):
    out, err = run(exe, "unreadable", "unreadable", STAGE, SA2_TWEAKS="no-loading-tips")
    assert out == ["1 - -", "1 - -", "0 0x004285DC 4 0xB0B0B0B0"]
    said = [l for l in err.splitlines() if "does not read" in l]
    assert said == ["sa2 tweaks: loading-tips off: the guest stack at 0x00090000 does not read, so "
                    "the loading screen is drawn"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-loading-tips", **{replay: "/dev/null"})
    assert out == DRAWN
    assert err == ("sa2 tweaks: SA2_TWEAKS=no-loading-tips ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


# ------------------------------------------------------------------ what the player is told
# The wrap skips the frame for every caller, but only some callers clear to black first:
# 0x00428740 (the load over the current frame) draws the current picture before its call, so a
# stage's title card stays on screen without its "Now Loading" ring. The player's wording names
# both outcomes and never promises only a black screen.
GAME_SETTINGS = ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java"
README_LINE = ("no Omochao tips or Now Loading animation while loading: black, or the picture "
               "already on screen")
ANDROID = ("Skip loading tips",
           "Hides Omochao's tips and the Now Loading animation while the game loads. Most loads "
           "show a black screen instead; a load over a picture already on screen, such as a "
           "stage's title card, keeps that picture.")


def test_the_release_readme_says_black_or_the_picture_already_on_screen():
    spec = importlib.util.spec_from_file_location("release_build_nlt_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    lines = re.findall(r"^    SA2_TWEAKS=no-loading-tips +(.+)$",
                       rb.player_readme("x86_64-linux"), re.M)
    assert lines == [README_LINE]


def test_the_android_switch_says_black_or_the_picture_already_on_screen():
    m = re.search(r'new Tweak\("no-loading-tips", "([^"]*)", "([^"]*)"\)',
                  GAME_SETTINGS.read_text())
    assert m, "GameSettings.TWEAKS has no no-loading-tips line"
    assert m.groups() == ANDROID
