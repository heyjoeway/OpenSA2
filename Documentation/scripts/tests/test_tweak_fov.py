"""The fov tweak (runtime/tweaks/fov.c) on SetViewStuff (0x0042B300): the gameplay camera's angle in
ESI scaled for the lifted body and put back after, and every other caller's left alone.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted body and the runtime's peek
and register accessors. The stand-in `__real_sub_42b300` records the ESI it was called with and
leaves ESI alone, as the lifted body does, then pops the return address (the RET). Each call puts
the caller's return address at guest [ESP] and CameraApplyView's caller's at [ESP+0x64], as the
lifted CALLs leave the stack, and GameMode (0x0174B040) in the fake guest memory.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_FOV_SCALE")

APPLY, SCREEN = "0x0077E1A5", "0x004EB635"      # CameraApplyView's call, CameraApplyScreenView's
HUD_RESTORE = "0x00458FFA"                      # 0x00458EE0's call (the HUD's restore)
EVENT_VIEW = "0x005FA1B0"                       # some other caller of CameraApplyView

# argv: groups of four (return address, CameraApplyView's caller, GameMode, ESI); a return address
# "unreadable" puts ESP where nothing reads, a GameMode "none" makes GameMode unreadable. Each call
# prints "<ESI the lifted body saw> <ESI after> <ESP moved>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
#define GAME_MODE 0x0174B040u
static uint8_t stack[0x200];
static uint32_t gpr[8], game_mode, seen;
static int mode_reads;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a == GAME_MODE && n == 4 && mode_reads) { memcpy(out, &game_mode, 4); return SA2_OK; }
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_42b300(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    seen = gpr[SA2_ESI];
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_42b300(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i + 3 < argc; i += 4) {
        const uint32_t ret = (uint32_t)strtoul(argv[i], NULL, 0), outer = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        const uint32_t esp = strcmp(argv[i], "unreadable") == 0 ? 0x90000u : STACK + 0x80u;
        memset(stack, 0, sizeof stack);
        if (esp == STACK + 0x80u) {
            memcpy(stack + 0x80, &ret, 4);
            memcpy(stack + 0x80 + 0x64, &outer, 4);
        }
        mode_reads = strcmp(argv[i + 2], "none") != 0;
        game_mode = (uint32_t)strtoul(argv[i + 2], NULL, 0);
        gpr[SA2_ESP] = esp;
        gpr[SA2_ESI] = (uint32_t)strtoul(argv[i + 3], NULL, 0);
        seen = 0xDEADBEEFu;
        int token;
        if (__wrap_sub_42b300(&token, 0x0042B300u, &token) != &token) return 3;
        printf("0x%X 0x%X %u\n", seen, gpr[SA2_ESI], gpr[SA2_ESP] - esp);
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
    d = tmp_path_factory.mktemp("fov")
    (d / "driver.c").write_text(DRIVER)
    out = d / "fov"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "fov.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


# The gameplay camera in a level twice (0x2E38, then 0x3000), the HUD's restore of the scaled angle,
# an event's view through CameraApplyView, and the gameplay camera's call outside a level.
CALLS = (APPLY, SCREEN, "5", "0x2E38",
         APPLY, SCREEN, "5", "0x3000",
         HUD_RESTORE, "0", "5", "0x39C6",
         APPLY, EVENT_VIEW, "5", "0x2E38",
         APPLY, SCREEN, "7", "0x2E38")
GAME = ["0x2E38 0x2E38 4", "0x3000 0x3000 4", "0x39C6 0x39C6 4", "0x2E38 0x2E38 4", "0x2E38 0x2E38 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_FOV_SCALE": "1.4"}])
def test_off_is_the_lifted_body_and_reads_nothing(exe, env):
    out, err = run(exe, *CALLS, APPLY, SCREEN, "none", "0x2E38", "unreadable", "0", "5", "0x2E38", **env)
    assert out == GAME + ["0x2E38 0x2E38 4", "0x2E38 0x2E38 4"]
    assert [line for line in err if "fov" in line] == []


def test_on_scales_only_the_gameplay_camera_in_a_level_and_puts_esi_back(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="fov")
    # 0x2E38 x 1.25 = 0x39C6, which the HUD's restore passes back unscaled; 0x3000 x 1.25 = 0x3C00.
    assert out == ["0x39C6 0x2E38 4", "0x3C00 0x3000 4", "0x39C6 0x39C6 4", "0x2E38 0x2E38 4",
                   "0x2E38 0x2E38 4"]
    assert err == [
        "sa2 tweaks: field of view scaled (SA2_TWEAKS=fov)",
        "sa2 tweaks: fov 1.250: the gameplay camera's angle 0x2E38 -> 0x39C6",
        "sa2 tweaks: fov: the gameplay camera's view outside a level (GameMode 7) keeps the game's angle"]


@pytest.mark.parametrize("scale, k, seen", [("1.3", "1.300", "0x3C16"), ("0.8", "0.800", "0x24FA"),
                                            ("1", "1.000", "0x2E38"), ("1.000", "1.000", "0x2E38"),
                                            ("1.4", "1.400", "0x40B5"), (".9", "0.900", "0x2999")])
def test_the_scale_comes_from_sa2_fov_scale(exe, scale, k, seen):
    out, err = run(exe, APPLY, SCREEN, "5", "0x2E38", SA2_TWEAKS="fov", SA2_FOV_SCALE=scale)
    assert out == [f"{seen} 0x2E38 4"]
    assert err == ["sa2 tweaks: field of view scaled (SA2_TWEAKS=fov)",
                   f"sa2 tweaks: fov {k}: the gameplay camera's angle 0x2E38 -> {seen}"]


def test_the_scaled_angle_rounds_half_up(exe):
    # 0x2E3A x 1.25 = 14792.5 -> 14793 = 0x39C9; 0x2E39 x 1.25 = 14791.25 -> 0x39C7.
    out, _ = run(exe, APPLY, SCREEN, "5", "0x2E3A", APPLY, SCREEN, "5", "0x2E39", SA2_TWEAKS="fov")
    assert out == ["0x39C9 0x2E3A 4", "0x39C7 0x2E39 4"]


@pytest.mark.parametrize("scale, k, said", [
    ("2", "1.400", "SA2_FOV_SCALE=2 is outside 0.8 to 1.4, so the scale is 1.400"),
    ("0.5", "0.800", "SA2_FOV_SCALE=0.5 is outside 0.8 to 1.4, so the scale is 0.800"),
    ("1,3", "1.250", "SA2_FOV_SCALE=1,3 is not a number like 1.25, so the scale is 1.25"),
    ("wide", "1.250", "SA2_FOV_SCALE=wide is not a number like 1.25, so the scale is 1.25"),
    ("1.2345", "1.250", "SA2_FOV_SCALE=1.2345 is not a number like 1.25, so the scale is 1.25"),
])
def test_a_scale_that_does_not_read_or_is_out_of_range_is_named(exe, scale, k, said):
    out, err = run(exe, APPLY, SCREEN, "5", "0x2000", SA2_TWEAKS="fov", SA2_FOV_SCALE=scale)
    assert f"sa2 tweaks: fov: {said}" in err
    assert any(line.startswith(f"sa2 tweaks: fov {k}: ") for line in err)


def test_the_angle_is_held_within_its_bounds_unless_the_game_is_already_beyond(exe):
    calls = (APPLY, SCREEN, "5", "0x6000",      # x1.4 = 0x8666 -> held at 0x7000
             APPLY, SCREEN, "5", "0x7800",      # already beyond 0x7000: the game's kept
             APPLY, SCREEN, "5", "0x0")         # 0: left
    out, err = run(exe, *calls, SA2_TWEAKS="fov", SA2_FOV_SCALE="1.4")
    assert out == ["0x7000 0x6000 4", "0x7800 0x7800 4", "0x0 0x0 4"]
    assert "sa2 tweaks: fov: the angle 0x6000 x 1.400 is held at 0x7000 (the bounds are 0x1000 to 0x7000)" in err
    out, _ = run(exe, APPLY, SCREEN, "5", "0x1200", APPLY, SCREEN, "5", "0x0E00",
                 SA2_TWEAKS="fov", SA2_FOV_SCALE="0.8")
    assert out == ["0x1000 0x1200 4", "0xE00 0xE00 4"]


def test_an_unreadable_stack_or_game_mode_keeps_the_game_angle_and_says_so(exe):
    out, err = run(exe, "unreadable", "0", "5", "0x2E38", APPLY, SCREEN, "none", "0x2E38",
                   APPLY, SCREEN, "5", "0x2E38", SA2_TWEAKS="fov")
    assert out == ["0x2E38 0x2E38 4", "0x2E38 0x2E38 4", "0x39C6 0x2E38 4"]
    assert ("sa2 tweaks: fov: the guest stack at 0x00090000 does not read, so the caller is unknown "
            "and the view keeps the game's angle") in err
    assert ("sa2 tweaks: fov: GameMode at 0x0174B040 does not read, so the gameplay camera's view "
            "keeps the game's angle") in err


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="fov", SA2_FOV_SCALE="1.3", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ["sa2 tweaks: SA2_TWEAKS=fov ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_the_player_readme_lists_fov_in_name_order_with_its_summary():
    """fov is in the README's share/game.env block on both desktop targets, in name order with every
    other tweak, ahead of no-event-bars (test_release_build.py does not pin which tweak comes first)."""
    spec = importlib.util.spec_from_file_location("release_build_fov_under_test",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rb
    spec.loader.exec_module(rb)
    for target, env in (("x86_64-windows", "share\\game.env"), ("x86_64-linux", "share/game.env")):
        text = rb.player_readme(target, rb.NATIVE_MODE)
        names = re.findall(r"^    SA2_TWEAKS=(\S+) ", text, re.M)
        assert names == sorted(names) and names.index("fov") < names.index("no-event-bars")
        assert f"  {env}:\n    SA2_TWEAKS=" in text
        assert re.search(r"^    SA2_TWEAKS=fov +a wider view for the gameplay camera, 1\.25 times its angle "
                         r"\(SA2_FOV_SCALE=0\.8 to 1\.4 sets it\)$", text, re.M)
