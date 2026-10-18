"""The no-powerup-music tweak (runtime/tweaks/no-powerup-music.c) on PlayJingle (0x00443480).

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted PlayJingle and the
runtime's peek and register accessors. The stand-in `__real_sub_443480` does what the lifted body
leaves behind that a caller can see: it counts a play of the jingle named by EBX, writes EAX, ECX
and EDX (the lifted body clobbers all three), sets EIP to the return address and pops it (ESP + 4).
Each call puts a caller's return address at guest [ESP] first, as the lifted CALL does.

The callers: PlayerSpeedUp's (return address 0x0046E077, EBX "speedup.adx"), the invincibility
handler's (0x0046E49A, "invncibl.adx"), and two of the others, the 1-up's and an arbitrary one,
which keep their jingle whether the tweak is on or off.
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
SPEEDUP, INVINC = "0x0046E077", "0x0046E49A"     # the two power-ups' return addresses
OTHER, OTHER2 = "0x00457C56", "0x00602369"       # two of PlayJingle's other callers

# The guest stack at 0x20000. argv: return addresses ("unreadable" for a stack that does not
# read); each call prints "<played> <EAX> <ECX> <EDX> <EIP> <ESP moved>".
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
static int played;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_443480(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    played++;
    gpr[SA2_EAX] = 0x11111111u;                 /* the body's clobbers */
    gpr[SA2_ECX] = 0x22222222u;
    gpr[SA2_EDX] = 0x33333333u;
    uint32_t ret = 0;
    sa2_peek(gpr[SA2_ESP], 4, &ret);
    gpr[SA2_EIP] = ret;                         /* the RET */
    gpr[SA2_ESP] += 4;
    return memory;
}
void *__wrap_sub_443480(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        const int bad = strcmp(argv[i], "unreadable") == 0;
        const uint32_t ret = bad ? 0x0046E077u : (uint32_t)strtoul(argv[i], NULL, 0);
        const uint32_t esp = bad ? 0x90000u : STACK + 0x80u;
        memcpy(stack + 0x80, &ret, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EBX] = 0x008B94FCu;
        gpr[SA2_EAX] = 0xAAAAAAAAu;
        gpr[SA2_ECX] = 0xCCCCCCCCu;
        gpr[SA2_EDX] = 0xDDDDDDDDu;
        gpr[SA2_EIP] = 0x00443480u;
        played = 0;
        int token;
        if (__wrap_sub_443480(&token, 0x00443480u, &token) != &token) return 3;
        printf("%d %08X %08X %08X %08X %u\n", played, gpr[SA2_EAX], gpr[SA2_ECX], gpr[SA2_EDX],
               gpr[SA2_EIP], gpr[SA2_ESP] - esp);
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
    d = tmp_path_factory.mktemp("tweak_no_powerup_music")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-powerup-music.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def played(ret: str) -> str:
    """What the lifted body leaves: the jingle played, its clobbers, the RET to `ret`."""
    return f"1 11111111 22222222 33333333 {int(ret, 16):08X} 4"


def skipped(ret: str) -> str:
    """The bare RET: no jingle, EAX, ECX and EDX as the caller left them."""
    return f"0 AAAAAAAA CCCCCCCC DDDDDDDD {int(ret, 16):08X} 4"


CALLS = (SPEEDUP, INVINC, OTHER, SPEEDUP, OTHER2, INVINC)


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_every_call_runs_the_lifted_body_and_says_nothing_of_it(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == [played(r) for r in CALLS]
    assert "powerup-music" not in err


def test_on_the_two_power_up_jingles_are_skipped_and_the_others_play(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-powerup-music")
    assert out == [skipped(SPEEDUP), skipped(INVINC), played(OTHER), skipped(SPEEDUP),
                   played(OTHER2), skipped(INVINC)]
    assert err.splitlines() == [
        "sa2 tweaks: powerup-music off (SA2_TWEAKS=no-powerup-music)",
        "sa2 tweaks: powerup-music off: the speed-up jingle (speedup.adx) not played",
        "sa2 tweaks: powerup-music off: the invincibility jingle (invncibl.adx) not played"]


def test_on_beside_another_tweak(exe):
    out, err = run(exe, INVINC, OTHER, SA2_TWEAKS="no-event-bars,no-powerup-music")
    assert out == [skipped(INVINC), played(OTHER)]
    assert err.splitlines()[0] == ("sa2 tweaks: event-bars off, powerup-music off "
                                   "(SA2_TWEAKS=no-event-bars,no-powerup-music)")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-powerup-music", **{replay: "/dev/null"})
    assert out == [played(r) for r in CALLS]
    assert "not played" not in err


def test_an_unreadable_stack_plays_the_jingle_and_says_so_once(exe):
    out, err = run(exe, "unreadable", "unreadable", SPEEDUP, SA2_TWEAKS="no-powerup-music")
    assert out[2] == skipped(SPEEDUP)
    assert out[0].startswith("1 ") and out[1].startswith("1 ")
    lines = err.splitlines()
    assert lines.count("sa2 tweaks: powerup-music off: the guest stack at 0x00090000 does not read, "
                       "so the caller is unknown and the jingle plays") == 1
