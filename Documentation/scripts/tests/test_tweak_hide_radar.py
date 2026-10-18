"""hide-radar (runtime/tweaks/hide-radar.c): the emerald radar's display (0x0073A6F0) and its beep
(sound 0x1009, played by the radar's main through the sound effect player 0x00437260 at five calls).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py does
for no-event-bars, and linked with a driver that stands in for the two lifted functions and the
runtime's peek and register accessors. Each stand-in does what its lifted body does to what a
caller can see: the display draws (counted) and returns, the sound player plays ESI (counted),
sets EAX as its result, and returns; each return is the RET, EIP = [ESP] and ESP + 4. Each call
puts a caller's return address at guest [ESP] first, as the lifted CALL does.
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

# The radar main's five beep calls: (call, return address).
BEEP = [(0x00739CDF, 0x00739CE4), (0x00739D11, 0x00739D16), (0x00739D35, 0x00739D3A),
        (0x00739DF2, 0x00739DF7), (0x00739E0D, 0x00739E12)]
DISPATCH_RET = 0x0046F9A3      # any return address: the display is skipped whoever calls it
OTHER_RET = 0x00739B3E         # a sound call that is not the radar's beep (a stand-in caller)

# The guest stack at 0x20000. argv: one call each, "display:<ret>" or "sound:<ret>:<esi>", or
# "display:unreadable" / "sound:unreadable:<esi>" for a stack that does not read. Each prints
# "<kind> <lifted body ran> <ESP moved> <EIP> <EAX> <ESI>".
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
static int ran;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static void *ret(void *memory) {
    uint32_t r = 0;
    if (sa2_peek(gpr[SA2_ESP], 4, &r) != SA2_OK) r = 0xFFFFFFFFu;
    gpr[SA2_EIP] = r;
    gpr[SA2_ESP] += 4;
    return memory;
}
void *__real_sub_73a6f0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    ran = 1;
    return ret(memory);
}
void *__real_sub_437260(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    ran = 1;
    gpr[SA2_EAX] = 2;                          /* the sound's channel, as the lifted body returns */
    return ret(memory);
}
void *__wrap_sub_73a6f0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_437260(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char kind[16] = {0}, where[16] = {0};
        unsigned esi = 0;
        if (sscanf(argv[i], "%15[a-z]:%15[A-Za-z0-9]:%x", kind, where, &esi) < 2) return 2;
        const int unreadable = strcmp(where, "unreadable") == 0;
        const uint32_t r = unreadable ? 0 : (uint32_t)strtoul(where, NULL, 0);
        const uint32_t esp = unreadable ? 0x90000u : STACK + 0x80u;
        memcpy(stack + 0x80, &r, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_ESI] = esi;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        gpr[SA2_EIP] = 0;
        ran = 0;
        int token;
        void *m = strcmp(kind, "display") == 0 ? __wrap_sub_73a6f0(&token, 0x0073A6F0u, &token)
                                               : __wrap_sub_437260(&token, 0x00437260u, &token);
        if (m != &token) return 3;
        printf("%s %d %u 0x%08X 0x%08X 0x%X\n", kind, ran, gpr[SA2_ESP] - esp, gpr[SA2_EIP],
               gpr[SA2_EAX], gpr[SA2_ESI]);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("hide_radar")
    (d / "driver.c").write_text(DRIVER)
    out = d / "hr"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "hide-radar.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def display(ret: int) -> str:
    return f"display:0x{ret:08X}"


def sound(ret: int, esi: int = 0x1009) -> str:
    return f"sound:0x{ret:08X}:{esi:x}"


def played(kind: str, ret: int, esi: int = 0) -> str:
    """What a caller sees after the lifted body ran: the RET, and the sound player's EAX."""
    eax = 2 if kind == "sound" else 0xDEADBEEF
    return f"{kind} 1 4 0x{ret:08X} 0x{eax:08X} 0x{esi:X}"


def skipped(kind: str, ret: int, esi: int = 0) -> str:
    """What a caller sees after the RET alone: EAX as it was."""
    return f"{kind} 0 4 0x{ret:08X} 0xDEADBEEF 0x{esi:X}"


# A radar frame in a one-player hunt (the display, then the beep twice from its two one-player
# calls), a sound from elsewhere, and the two-player beeps.
CALLS = [display(DISPATCH_RET), sound(BEEP[3][1]), sound(BEEP[4][1]), sound(OTHER_RET, 0x1009),
         sound(OTHER_RET, 0x2004), *[sound(r) for _, r in BEEP[:3]], display(DISPATCH_RET)]
GAME = [played("display", DISPATCH_RET), played("sound", BEEP[3][1], 0x1009),
        played("sound", BEEP[4][1], 0x1009), played("sound", OTHER_RET, 0x1009),
        played("sound", OTHER_RET, 0x2004), *[played("sound", r, 0x1009) for _, r in BEEP[:3]],
        played("display", DISPATCH_RET)]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_code_and_says_nothing_of_the_radar(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == GAME
    assert not [line for line in err if "radar" in line]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="hide-radar", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ["sa2 tweaks: SA2_TWEAKS=hide-radar ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_the_display_is_skipped_and_only_the_radars_beep_is_silenced(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="hide-radar")
    assert out == [skipped("display", DISPATCH_RET), skipped("sound", BEEP[3][1], 0x1009),
                   skipped("sound", BEEP[4][1], 0x1009), played("sound", OTHER_RET, 0x1009),
                   played("sound", OTHER_RET, 0x2004),
                   *[skipped("sound", r, 0x1009) for _, r in BEEP[:3]],
                   skipped("display", DISPATCH_RET)]
    assert err == [
        "sa2 tweaks: radar hidden (SA2_TWEAKS=hide-radar)",
        "sa2 tweaks: radar hidden: radar not drawn (display 0x0073A6F0 skipped)",
        "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at 0x00739DF2)",
        "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at 0x00739E0D)",
        "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at 0x00739CDF)",
        "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at 0x00739D11)",
        "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at 0x00739D35)"]


def test_each_line_is_said_once(exe):
    calls = [display(DISPATCH_RET), sound(BEEP[3][1])] * 3
    out, err = run(exe, *calls, SA2_TWEAKS="hide-radar")
    assert out == [skipped("display", DISPATCH_RET), skipped("sound", BEEP[3][1], 0x1009)] * 3
    assert len(err) == 3


def test_another_sound_from_a_beep_site_plays_and_is_named(exe):
    out, err = run(exe, sound(BEEP[0][1], 0x2004), sound(BEEP[0][1]), SA2_TWEAKS="hide-radar")
    assert out == [played("sound", BEEP[0][1], 0x2004), skipped("sound", BEEP[0][1], 0x1009)]
    assert err[1:] == [
        "sa2 tweaks: radar hidden: the call at 0x00739CDF plays sound 0x2004, not the radar beep "
        "0x1009, so it plays",
        "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at 0x00739CDF)"]


def test_an_unreadable_stack_lets_the_call_through_and_says_so(exe):
    out, err = run(exe, "display:unreadable", "sound:unreadable:1009", display(DISPATCH_RET),
                   SA2_TWEAKS="hide-radar")
    assert out == ["display 1 4 0xFFFFFFFF 0xDEADBEEF 0x0", "sound 1 4 0xFFFFFFFF 0x00000002 0x1009",
                   skipped("display", DISPATCH_RET)]
    assert err[1] == ("sa2 tweaks: radar hidden: the guest stack at 0x00090000 does not read, so "
                      "the radar's display goes through")
    assert err[2] == "sa2 tweaks: radar hidden: radar not drawn (display 0x0073A6F0 skipped)"
