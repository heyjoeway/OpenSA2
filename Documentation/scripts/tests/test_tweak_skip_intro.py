"""The skip-intro tweak (runtime/tweaks/skip-intro.c) on the game mode handler (0x00434160), and the
test spawn's rule for the same function, which moved from its own wrap to the hook the tweak's wrap
calls (harness/replay/wrap_test_spawn.c, sa2_harness_gamemode_after).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted handler, LoadTipsTexs
(0x00427EF0) and the runtime's peek, poke, register and trap entries. A second build adds
harness/replay/wrap_test_spawn.c, as a stage replay's link does.

Each call is one argument, "<GameMode before>:<GameMode after>[:<story type>]". The stand-in
handler does what the lifted body's arm does to the state the tweak reads: GameMode = after;
NextGameMode = 13 when after is 0x15 (case 13's store at 0x0043459E), else after; the story type
byte, when given; then the RET (EIP = the return address at [ESP], ESP + 4) with EAX = 0 and ECX
and EDX clobbered. The stand-in LoadTipsTexs counts its calls, records ESI (the language), sets
[0x0093BC5A] = 1 as the body does, clobbers EAX, ECX and EDX, and returns through its own RET,
or, with SKIP_TIPS_BAD_RET set, returns without popping. The driver prints, per call,
"<GameMode> <NextGameMode> <EAX> <ECX> <ESI> <ESP moved> <tips calls> <tips ESI> <[0x0093BC5A]>".
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_TEST_SPAWN_LEVEL",
        "SA2_TEST_SPAWN_CHARACTER", "SKIP_TIPS_BAD_RET")

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_trap.h"

#define GAME_MODE  0x0174B040u
#define NEXT_MODE  0x0174B044u
#define STORY_TYPE 0x01DEB320u
#define LANGUAGE   0x0174AFD1u
#define TIPS_READY 0x0093BC5Au
#define STACK      0x00020000u
#define CALLER_RET 0x00433F7Bu

/* The guest: a few words of .dataa and 0x100 bytes of stack. */
static struct { uint32_t addr; uint8_t v; } cells[64];
static unsigned ncells;
static uint8_t stack[0x100];
static uint32_t gpr[SA2_GPR_COUNT];
static unsigned tips_calls;
static uint32_t tips_esi;

static uint8_t *cell(uint32_t a, int make) {
    if (a >= STACK && a < STACK + sizeof stack) return stack + (a - STACK);
    for (unsigned i = 0; i < ncells; i++) if (cells[i].addr == a) return &cells[i].v;
    if (!make || ncells == sizeof cells / sizeof cells[0]) return NULL;
    cells[ncells].addr = a;
    cells[ncells].v = 0;
    return &cells[ncells++].v;
}
static int known(uint32_t a) {
    return (a >= STACK && a < STACK + sizeof stack) || (a >= GAME_MODE && a < GAME_MODE + 8) ||
           a == STORY_TYPE || a == LANGUAGE || a == TIPS_READY;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    for (uint32_t i = 0; i < n; i++) if (!known(a + i)) return 1;
    for (uint32_t i = 0; i < n; i++) { uint8_t *c = cell(a + i, 1); ((uint8_t *)out)[i] = c ? *c : 0; }
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    for (uint32_t i = 0; i < n; i++) if (!known(a + i)) return 1;
    for (uint32_t i = 0; i < n; i++) *cell(a + i, 1) = ((const uint8_t *)in)[i];
    return SA2_OK;
}
static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static void wr8(uint32_t a, uint8_t v) { sa2_poke(a, 1, &v); }
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
void sa2_trap_note(const char *fmt, ...) { (void)fmt; fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}

static uint32_t next_after, next_type, has_type;
static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }

void *__real_sub_434160(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    wr32(GAME_MODE, next_after);
    wr32(NEXT_MODE, next_after == 0x15u ? 13u : next_after);
    if (has_type) wr8(STORY_TYPE, (uint8_t)next_type);
    gpr[SA2_EAX] = 0;
    gpr[SA2_ECX] = 0x0C0C0C0Cu;
    gpr[SA2_EDX] = 0x0D0D0D0Du;
    ret_();
    return memory;
}
void *sub_427ef0(void *state, uint32_t pc, void *memory) {
    (void)state;
    if (pc != 0x00427EF0u || gpr[SA2_EIP] != 0x00427EF0u) exit(5);
    tips_calls++;
    tips_esi = gpr[SA2_ESI];
    wr8(TIPS_READY, 1);
    gpr[SA2_EAX] = 0xA0A0A0A0u;
    gpr[SA2_ECX] = 0xC1C1C1C1u;
    gpr[SA2_EDX] = 0xD1D1D1D1u;
    if (getenv("SKIP_TIPS_BAD_RET")) gpr[SA2_EIP] = 0x00428004u;
    else ret_();
    return memory;
}
/* wrap_test_spawn.c's other wraps and callees, for the build that links it. */
void *__real_sub_77dd10(void *s, uint32_t pc, void *m) { (void)s; (void)pc; return m; }
void *__real_sub_4436a0(void *s, uint32_t pc, void *m) { (void)s; (void)pc; return m; }
void *__real_sub_426760(void *s, uint32_t pc, void *m) { (void)s; (void)pc; return m; }
void *sub_41d490(void *s, uint32_t pc, void *m) { (void)s; (void)pc; return m; }

void *__wrap_sub_434160(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    wr8(LANGUAGE, 1);
    for (int i = 1; i < argc; i++) {
        uint32_t before = 0;
        has_type = 0;
        int got = sscanf(argv[i], "%i:%i:%i", (int *)&before, (int *)&next_after, (int *)&next_type);
        if (got < 2) return 2;
        has_type = got == 3;
        wr32(GAME_MODE, before);
        const uint32_t esp = STACK + 0x80u;
        wr32(esp, CALLER_RET);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EIP] = 0x00434160u;
        gpr[SA2_ESI] = 0x5E5E5E5Eu;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        int token;
        if (__wrap_sub_434160(&token, 0x00434160u, &token) != &token) return 3;
        if (gpr[SA2_EIP] != CALLER_RET) return 4;
        uint8_t ready = 0;
        sa2_peek(TIPS_READY, 1, &ready);
        printf("%u %u %#x %#x %#x %u %u %u %u\n", rd32(GAME_MODE), rd32(NEXT_MODE), gpr[SA2_EAX],
               gpr[SA2_ECX], gpr[SA2_ESI], gpr[SA2_ESP] - esp, tips_calls, tips_esi, ready);
    }
    return 0;
}
"""

# The boot as the lifted handler runs it (the D4 trace's game modes): 2 to 13, 13 to the logos
# (0x15), the logos to 13 (here, the visit after them), 13 to the title, the title at once to
# LoadStory with story type 4 (the opening), then 13 and the title again.
BOOT = ("2:13", "13:21", "13:14", "14:12:4", "13:14")
# Each line: GameMode, NextGameMode, EAX, ECX, ESI, ESP moved, tips calls, tips ESI, [0x0093BC5A].
CLOBBER = "0xc0c0c0c 0x5e5e5e5e 4"
VANILLA = [f"13 13 0 {CLOBBER} 0 0 0", f"21 13 0 {CLOBBER} 0 0 0", f"14 14 0 {CLOBBER} 0 0 0",
           f"12 12 0 {CLOBBER} 0 0 0", f"14 14 0 {CLOBBER} 0 0 0"]
SKIPPED = [f"13 13 0 {CLOBBER} 0 0 0", f"13 13 0 {CLOBBER} 1 1 1", f"14 14 0 {CLOBBER} 1 1 1",
           f"13 13 0 {CLOBBER} 1 1 1", f"14 14 0 {CLOBBER} 1 1 1"]
LOGOS = "sa2 tweaks: intro skipped: the logos and intro skipped; the loading tips' textures loaded in their place"
OPENING = "sa2 tweaks: intro skipped: the opening cutscene (story type 4) skipped; the title screen follows"


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


def build(tmp_path_factory, name: str, *extra: Path) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp(name)
    (d / "driver.c").write_text(DRIVER)
    out = d / "si"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "skip-intro.c"), *map(str, extra), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    return build(tmp_path_factory, "skip-intro")


@pytest.fixture(scope="module")
def spawn_exe(tmp_path_factory) -> Path:
    """Linked as a stage replay links: the test spawn's file defines the hook."""
    return build(tmp_path_factory, "skip-intro-spawn", ROOT / "harness" / "replay" / "wrap_test_spawn.c")


def run(exe: Path, *calls: str, rc: int = 0, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == rc, p.stderr
    return p.stdout.splitlines(), p.stderr


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_handler_alone(exe, env):
    out, err = run(exe, *BOOT, **env)
    assert out == VANILLA
    assert "intro skipped" not in err


def test_on_skips_the_logos_and_the_opening_and_loads_the_tips(exe):
    out, err = run(exe, *BOOT, SA2_TWEAKS="skip-intro")
    assert out == SKIPPED
    assert err.splitlines() == ["sa2 tweaks: intro skipped (SA2_TWEAKS=skip-intro)", LOGOS, OPENING]


def test_ignored_in_a_replay(exe):
    out, err = run(exe, *BOOT, SA2_TWEAKS="skip-intro", SA2_REPLAY_TICKS="/dev/null")
    assert out == VANILLA
    assert err == ("sa2 tweaks: SA2_TWEAKS=skip-intro ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_a_players_story_start_is_never_turned_back(exe):
    """Story types other than 4 (the Hero, Dark and Last stories, 1-3 and 5) leave the title for
    LoadStory as the game sends them, first time or not."""
    for kind in (1, 2, 3, 5):
        out, _ = run(exe, "2:13", "13:21", "13:14", f"14:12:{kind}", SA2_TWEAKS="skip-intro")
        assert out[-1] == f"12 12 0 {CLOBBER} 1 1 1"


def test_only_the_boots_opening_is_skipped_and_the_idle_one_is_said(exe):
    out, err = run(exe, *BOOT, "14:12:4", "13:14", "14:12:4", SA2_TWEAKS="skip-intro")
    assert out[5:] == [f"12 12 0 {CLOBBER} 1 1 1", f"14 14 0 {CLOBBER} 1 1 1",
                       f"12 12 0 {CLOBBER} 1 1 1"]
    assert err.splitlines()[3:] == [
        "sa2 tweaks: intro skipped: the title's idle opening plays (only the boot's is skipped)"]


def test_a_later_logos_return_is_skipped_too_without_loading_the_tips_again(exe):
    out, err = run(exe, *BOOT, "13:21", "13:21", SA2_TWEAKS="skip-intro")
    assert out[5:] == [f"13 13 0 {CLOBBER} 1 1 1"] * 2
    assert err.splitlines()[3:] == [
        "sa2 tweaks: intro skipped: the logos skipped again on a return to the title"]


def test_other_transitions_are_left_alone(exe):
    calls = ("2:13", "13:14", "14:9", "4:5", "16:13", "0x16:0x15")
    on, err = run(exe, *calls, SA2_TWEAKS="skip-intro")
    off, _ = run(exe, *calls)
    assert on == off
    assert err.splitlines() == ["sa2 tweaks: intro skipped (SA2_TWEAKS=skip-intro)"]


def test_load_tips_texs_that_does_not_return_stops_the_run_by_name(exe):
    out, err = run(exe, "2:13", "13:21", rc=7, SA2_TWEAKS="skip-intro", SKIP_TIPS_BAD_RET="1")
    assert out == [f"13 13 0 {CLOBBER} 0 0 0"]
    assert "trap note: sa2 tweaks: intro skipped: LoadTipsTexs (%#010x) returned to" in err
    assert err.splitlines()[-1] == "trap 1 at 0x00428004"


@pytest.mark.parametrize("tweaks", ["", "skip-intro"])
def test_the_test_spawn_still_starts_its_level_through_the_hook(spawn_exe, tweaks):
    """SA2_TEST_SPAWN_LEVEL set: 13 to 0x15 becomes 13 to 4 (the Mod Loader's --level start), with
    the tweak off or on; the tweak then sees 4, not 0x15, and neither skips nor loads the tips
    (the test spawn loads them itself)."""
    out, err = run(spawn_exe, "2:13", "13:21", SA2_TWEAKS=tweaks, SA2_TEST_SPAWN_LEVEL="10")
    assert out == [f"13 13 0 {CLOBBER} 0 0 0", f"4 13 0 {CLOBBER} 0 0 0"]
    assert "intro skipped:" not in err


def test_without_the_test_spawn_the_harness_hook_changes_nothing(spawn_exe):
    assert run(spawn_exe, *BOOT)[0] == VANILLA
    assert run(spawn_exe, *BOOT, SA2_TWEAKS="skip-intro")[0] == SKIPPED
