"""quiet-omochao (runtime/tweaks/quiet-omochao.c): Omochao_Main (0x006C0780) returns at once and
the Omochao maker (0x006C0A90) makes nothing and answers EAX 0, as Codes.lst's "Disable Omochao"
bytes make them; off, both are the lifted bodies.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the two lifted functions and the
runtime's peek and register accessors. Each stand-in does what its lifted body leaves behind that
a caller can see: Omochao_Main's runs the object (EAX and EDX changed, a byte of the object
written), the maker's returns an object in EAX (ECX changed); both end in the RET (EIP the return
address at guest [ESP], ESP + 4). Each call first puts a caller's return address at guest [ESP]
and its argument at [ESP+4], as the lifted CALL leaves them, and sets every register to a known
value, so a case reads exactly what the caller would see after the call.
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

# The object loop's call of an object main, and three of the maker's eight callers (return
# addresses: the call + 5).
LOOP_RET = "0x00470ABC"
SHADOW_RET, LAST_BOSS_RET, CREDITS_RET = "0x004C82D5", "0x00499B59", "0x007623C9"

# argv: triples of (main|make|main-unreadable|make-unreadable, return address, argument); main's
# argument is the ObjectMaster, make's the mode in EAX. Each prints
# "<fn> real=<n> eax=<EAX> esp=+<moved> eip=<EIP> regs=<same|changed> object=<byte>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
#define OBJECT 0x30000u
static uint8_t stack[0x100], object[0x40];
static uint32_t gpr[SA2_GPR_COUNT];
static int reals;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    if (a >= OBJECT && a + n <= OBJECT + sizeof object) return object + (a - OBJECT);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static void ret(void) {
    uint32_t r = 0;
    memcpy(&r, at(gpr[SA2_ESP], 4) ? at(gpr[SA2_ESP], 4) : (uint8_t *)&r, 4);
    gpr[SA2_EIP] = r;
    gpr[SA2_ESP] += 4;
}
void *__real_sub_6c0780(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reals++;
    object[2] = 6;                              /* the object's state byte, as 0x006C095F sets */
    gpr[SA2_EAX] = 0x11111111u;
    gpr[SA2_EDX] = 0x22222222u;
    ret();
    return memory;
}
void *__real_sub_6c0a90(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    reals++;
    gpr[SA2_EAX] = 0x00ABC000u;                 /* the object it made */
    gpr[SA2_ECX] = 0x33333333u;
    ret();
    return memory;
}
void *__wrap_sub_6c0780(void *state, uint32_t pc, void *memory);
void *__wrap_sub_6c0a90(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    static const uint32_t start[SA2_GPR_COUNT] = {0, 0x0C0C0C0Cu, 0x0D0D0D0Du, 0x0B0B0B0Bu, 0,
                                                  0x0E0E0E0Eu, 0x05050505u, 0x0D1D1D1Du, 0};
    for (int i = 1; i + 2 < argc; i += 3) {
        const int main_fn = strncmp(argv[i], "main", 4) == 0;
        const int unreadable = strstr(argv[i], "unreadable") != NULL;
        const uint32_t r = (uint32_t)strtoul(argv[i + 1], NULL, 0), arg = (uint32_t)strtoul(argv[i + 2], NULL, 0);
        const uint32_t esp = unreadable ? 0x90000u : STACK + 0x80u;
        memcpy(gpr, start, sizeof gpr);
        memset(object, 0, sizeof object);
        memcpy(stack + 0x80, &r, 4);
        memcpy(stack + 0x84, main_fn ? &arg : &(uint32_t){0}, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_EAX] = main_fn ? 0xA5A5A5A5u : arg;
        gpr[SA2_EIP] = main_fn ? 0x006C0780u : 0x006C0A90u;
        reals = 0;
        int token;
        void *out = main_fn ? __wrap_sub_6c0780(&token, 0x006C0780u, &token)
                            : __wrap_sub_6c0a90(&token, 0x006C0A90u, &token);
        if (out != &token) return 3;
        int same = 1;
        for (int k = SA2_ECX; k <= SA2_EDI; k++)
            if (k != SA2_ESP && gpr[k] != start[k]) same = 0;
        printf("%s real=%d eax=0x%08X esp=+%u eip=0x%08X regs=%s object=%u\n", main_fn ? "main" : "make",
               reals, gpr[SA2_EAX], gpr[SA2_ESP] - esp, gpr[SA2_EIP], same ? "same" : "changed", object[2]);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("quiet_omochao")
    (d / "driver.c").write_text(DRIVER)
    out = d / "qo"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "quiet-omochao.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


# Two objects' mains from the loop, then the maker from three callers, in modes 1, 1 and 2, and
# Shadow's control again.
CALLS = ("main", LOOP_RET, "0x00030000", "main", LOOP_RET, "0x00030010",
         "make", SHADOW_RET, "2", "make", LAST_BOSS_RET, "1", "make", CREDITS_RET, "2",
         "make", SHADOW_RET, "2")
GAME = ["main real=1 eax=0x11111111 esp=+4 eip=0x00470ABC regs=changed object=6",
        "main real=1 eax=0x11111111 esp=+4 eip=0x00470ABC regs=changed object=6",
        "make real=1 eax=0x00ABC000 esp=+4 eip=0x004C82D5 regs=changed object=0",
        "make real=1 eax=0x00ABC000 esp=+4 eip=0x00499B59 regs=changed object=0",
        "make real=1 eax=0x00ABC000 esp=+4 eip=0x007623C9 regs=changed object=0",
        "make real=1 eax=0x00ABC000 esp=+4 eip=0x004C82D5 regs=changed object=0"]
QUIET = ["main real=0 eax=0xA5A5A5A5 esp=+4 eip=0x00470ABC regs=same object=0",
         "main real=0 eax=0xA5A5A5A5 esp=+4 eip=0x00470ABC regs=same object=0",
         "make real=0 eax=0x00000000 esp=+4 eip=0x004C82D5 regs=same object=0",
         "make real=0 eax=0x00000000 esp=+4 eip=0x00499B59 regs=same object=0",
         "make real=0 eax=0x00000000 esp=+4 eip=0x007623C9 regs=same object=0",
         "make real=0 eax=0x00000000 esp=+4 eip=0x004C82D5 regs=same object=0"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_bodies_and_says_nothing_of_omochao(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == GAME
    assert "omochao" not in err


def test_on_main_is_the_ret_and_the_maker_makes_nothing(exe):
    """Main: the `ret` at its entry, every register but ESP and EIP as the caller left it, the
    object untouched. Maker: EAX 0, the `xor eax,eax; ...; ret` the NOPs fall through to."""
    out, err = run(exe, *CALLS, SA2_TWEAKS="quiet-omochao")
    assert out == QUIET
    assert err.splitlines() == [
        "sa2 tweaks: omochao quiet (SA2_TWEAKS=quiet-omochao)",
        "sa2 tweaks: omochao quiet: first Omochao skipped (object 0x00030000; it stays and does nothing)",
        "sa2 tweaks: omochao quiet: no Omochao made for the call at 0x004C82D0 (mode 2)",
        "sa2 tweaks: omochao quiet: no Omochao made for the call at 0x00499B54 (mode 1)",
        "sa2 tweaks: omochao quiet: no Omochao made for the call at 0x007623C4 (mode 2)"]


def test_on_with_another_tweak(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars,quiet-omochao")
    assert out == QUIET
    assert err.splitlines()[0] == ("sa2 tweaks: event-bars off, omochao quiet "
                                   "(SA2_TWEAKS=no-event-bars,quiet-omochao)")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="quiet-omochao", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=quiet-omochao ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_an_unreadable_stack_leaves_omochao_to_the_game_and_says_so_once(exe):
    out, err = run(exe, "main-unreadable", LOOP_RET, "0x00030000", "make-unreadable", SHADOW_RET, "2",
                   "main", LOOP_RET, "0x00030000", SA2_TWEAKS="quiet-omochao")
    assert out[0].startswith("main real=1 ") and out[1].startswith("make real=1 ")
    assert out[2] == QUIET[0]
    lines = err.splitlines()
    assert lines.count("sa2 tweaks: omochao quiet: the guest stack at 0x00090000 does not read, so "
                       "this Omochao is left to the game") == 1
    assert "sa2 tweaks: omochao quiet: first Omochao skipped (object 0x00030000; it stays and does nothing)" in lines


def test_at_most_eight_callers_are_named(exe):
    calls = [x for k in range(10) for x in ("make", hex(0x00400005 + 0x10 * k), "1")]
    out, err = run(exe, *calls, SA2_TWEAKS="quiet-omochao")
    assert all(line.startswith("make real=0 eax=0x00000000 esp=+4 ") for line in out) and len(out) == 10
    named = [line for line in err.splitlines() if "no Omochao made" in line]
    assert named == [f"sa2 tweaks: omochao quiet: no Omochao made for the call at 0x{0x00400000 + 0x10 * k:08X} "
                     f"(mode 1)" for k in range(8)]
