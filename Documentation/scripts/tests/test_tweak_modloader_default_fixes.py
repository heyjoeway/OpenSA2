"""The modloader-default-fixes tweak (runtime/tweaks/modloader-default-fixes.c): two of the SA2 Mod
Loader's default fixes as wraps. ScreenFade (0x00478730) toward a target below its step uses the
loader's signed rule, so a fade to 0 ends at 0 instead of going 3, 9, 3, 9; every other call runs the
lifted body. chDrawCnk (0x0056DDD0) with [0x01AED2CC] == 0 and bit 4 of [0x01A27594] clear runs
with the bit set, so it builds the environment map's matrix, as under the loader's six NOPs at
0x0056DE7D, and the flag is stored back after.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted functions and the runtime's
peek, poke and register accessors. The guest holds the fade's alpha byte at 0x0171CDA3, the step's
two switches at 0x0174AFCF (a byte) and 0x01A5A274 (a dword), chDrawCnk's two flags at 0x01AED2CC
and 0x01A27594, and a stack at 0x20000.

The stand-in `__real_sub_478730` does what the lifted ScreenFade does (0x00478730-0x0047878D, its
unsigned compares included): EAX the answer, ECX the step, ESI and EDX kept, and the RET. The
stand-in `__real_sub_56ddd0` does chDrawCnk's branch (0x0056DE69-0x0056DF29): it builds the matrix
when the drawer flag is set or bit 4 is, draws through 0x0056D9F0 when the drawer flag is set and
0x0056D7C0 when not, and RETs (cdecl: the caller drops the argument).
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
ON = "sa2 tweaks: mod loader fixes (SA2_TWEAKS=modloader-default-fixes)"

# argv: steps, each one of
#   alpha:A  mode:M  objsub:V      set the alpha byte, the byte at 0x0174AFCF, CurrentObjectSub
#   fade:R:T:N                     N calls to ScreenFade toward T from return address R (hex);
#                                  prints the alphas after each call, then the last call's
#                                  "eax ecx edx esi returned"
#   table                          every alpha 0..255, target 0..255 and step (5, 6): prints how
#                                  many calls differ from the loader's rule and from the game's,
#                                  and how many leave a register or the stack other than the RET
#   drawer:D  env:M                set [0x01AED2CC] and [0x01A27594]
#   draw                           one call to chDrawCnk from 0x0056D570; prints "<built>
#                                  <0x0056D9F0 or 0x0056D7C0> <bit 4 seen> <[0x01A27594] after>
#                                  <returned>"
#   drawwrites:M                   the draw stores M to [0x01A27594] (for the next draws)
#   nostack  noflags  nofade       the stack, chDrawCnk's flags, or the fade's state stop reading
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define ALPHA  0x0171CDA3u
#define MODE   0x0174AFCFu
#define OBJSUB 0x01A5A274u
#define DRAWER 0x01AED2CCu
#define ENV    0x01A27594u
#define STACK  0x00020000u
static uint8_t alpha = 0, mode = 2, stack[0x100];
static uint32_t objsub, drawer, env, writes_env, draw_writes;
static uint32_t gpr[SA2_GPR_COUNT];
static int stack_reads = 1, flags_read = 1, fade_reads = 1;

static void *where(uint32_t a, uint32_t n) {
    if (fade_reads && a == ALPHA && n == 1) return &alpha;
    if (fade_reads && a == MODE && n == 1) return &mode;
    if (fade_reads && a == OBJSUB && n == 4) return &objsub;
    if (flags_read && a == DRAWER && n == 4) return &drawer;
    if (flags_read && a == ENV && n == 4) return &env;
    if (stack_reads && a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static uint32_t ld32(uint32_t a) { uint32_t v = 0; memcpy(&v, stack + (a - STACK), 4); return v; }
static void ret(void) { gpr[SA2_EIP] = ld32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }

/* ScreenFade, 0x00478730: EDX the target. */
void *__real_sub_478730(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t step = mode != 2 || objsub != 0 ? 5u : 6u;
    const uint32_t a = alpha, t = gpr[SA2_EDX];
    gpr[SA2_ECX] = step;
    if (a == t) { gpr[SA2_EAX] = 1; ret(); return memory; }
    uint32_t r;
    if (a > step + t) r = a - step;           /* 0x00478761: cmp eax,esi / jbe */
    else if (a < t - step) r = a + step;      /* 0x00478774: cmp eax,esi / jae */
    else r = t;
    alpha = (uint8_t)r;
    gpr[SA2_EAX] = 0;
    ret();
    return memory;
}
void *__wrap_sub_478730(void *state, uint32_t pc, void *memory);

/* chDrawCnk, 0x0056DDD0, its branch at 0x0056DE69. */
static int built, via_d9f0, bit_seen;
void *__real_sub_56ddd0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    bit_seen = (env & 4u) != 0;
    built = drawer != 0 || bit_seen;
    via_d9f0 = drawer != 0;
    if (draw_writes) env = writes_env;
    gpr[SA2_EAX] = 1;
    ret();
    return memory;
}
void *__wrap_sub_56ddd0(void *state, uint32_t pc, void *memory);

static int returned;
static void call(void *(*fn)(void *, uint32_t, void *), uint32_t at, uint32_t from) {
    const uint32_t esp = STACK + 0x80u;
    memcpy(stack + (esp - 4 - STACK), &from, 4);   /* the lifted CALL's push */
    gpr[SA2_ESP] = esp - 4;
    gpr[SA2_EIP] = at;
    int token;
    if (fn(&token, at, &token) != &token) exit(3);
    returned = gpr[SA2_ESP] == esp && gpr[SA2_EIP] == from;
}

static uint32_t loader(uint32_t a, uint32_t t, uint32_t step, uint32_t *out) {
    const int ia = (int)a, it = (int)t, is = (int)step;
    if (ia == it) { *out = a; return 1; }
    if (ia > it) *out = (uint32_t)(ia - is <= it ? it : ia - is);
    else *out = (uint32_t)(ia + is >= it ? it : ia + is);
    return 0;
}

static void table(void) {
    unsigned from_loader = 0, from_game = 0, regs = 0;
    for (int s = 0; s < 2; s++) {
        mode = s ? 2 : 1;
        for (uint32_t t = 0; t < 256; t++)
            for (uint32_t a = 0; a < 256; a++) {
                const uint32_t step = s ? 6u : 5u;
                alpha = (uint8_t)a;
                gpr[SA2_EDX] = t;
                gpr[SA2_ESI] = 0x5151u;
                gpr[SA2_ECX] = 0xC0C0u;
                call(__wrap_sub_478730, 0x00478730u, 0x0044F885u);
                const uint32_t got = alpha, eax = gpr[SA2_EAX];
                uint32_t want;
                const uint32_t wa = loader(a, t, step, &want);
                if (got != want || eax != wa) from_loader++;
                uint32_t g;
                if (a == t) g = a;
                else if (a > step + t) g = a - step;
                else if (a < t - step) g = a + step;
                else g = t;
                if (got != g || eax != (a == t)) from_game++;
                if (!returned || gpr[SA2_ECX] != step || gpr[SA2_EDX] != t || gpr[SA2_ESI] != 0x5151u) regs++;
            }
    }
    mode = 2;
    printf("%u %u %u\n", from_loader, from_game, regs);
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        const char *s = argv[i];
        unsigned v = 0, r = 0, t = 0, n = 0;
        if (sscanf(s, "alpha:%u", &v) == 1) alpha = (uint8_t)v;
        else if (sscanf(s, "mode:%u", &v) == 1) mode = (uint8_t)v;
        else if (sscanf(s, "objsub:%u", &v) == 1) objsub = v;
        else if (sscanf(s, "drawer:%u", &v) == 1) drawer = v;
        else if (sscanf(s, "env:%u", &v) == 1) env = v;
        else if (sscanf(s, "drawwrites:%u", &v) == 1) { draw_writes = 1; writes_env = v; }
        else if (!strcmp(s, "nostack")) stack_reads = 0;
        else if (!strcmp(s, "noflags")) flags_read = 0;
        else if (!strcmp(s, "nofade")) fade_reads = 0;
        else if (!strcmp(s, "table")) table();
        else if (sscanf(s, "fade:%x:%u:%u", &r, &t, &n) == 3) {
            for (unsigned k = 0; k < n; k++) {
                gpr[SA2_EDX] = t;
                gpr[SA2_ESI] = 0x5151u;
                call(__wrap_sub_478730, 0x00478730u, r);
                printf("%s%u", k ? " " : "", alpha);
            }
            printf(" | %u %u %u %x %d\n", gpr[SA2_EAX], gpr[SA2_ECX], gpr[SA2_EDX], gpr[SA2_ESI], returned);
        } else if (!strcmp(s, "draw")) {
            built = via_d9f0 = bit_seen = -1;
            call(__wrap_sub_56ddd0, 0x0056DDD0u, 0x0056D570u);
            printf("%d %s %d %u %d\n", built, via_d9f0 == 1 ? "0x0056D9F0" : via_d9f0 == 0 ? "0x0056D7C0" : "none",
                   bit_seen, env, returned);
        } else return 2;
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
    d = tmp_path_factory.mktemp("modloader_default_fixes")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "modloader-default-fixes.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *args: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


# CalcTotalScore's pose: the alpha from 255 toward 0 with step 6, 46 calls; then step 5.
DOWN6 = ("alpha:255", "fade:44F885:0:46")
DOWN5 = ("alpha:255", "mode:1", "fade:450FD1:0:53")
GAME6 = (" ".join(str(255 - 6 * k) for k in range(1, 43)) + " 9 3 9 3 | 0 6 0 5151 1")
GAME5 = (" ".join(str(255 - 5 * k) for k in range(1, 51)) + " 10 5 10 | 0 5 0 5151 1")


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_fade_to_0_bounces_as_the_game_does(exe, env):
    for steps, game in ((DOWN6, GAME6), (DOWN5, GAME5)):
        out, err = run(exe, *steps, **env)
        assert out == [game]
        assert "mod loader fixes" not in err


def test_on_the_fade_to_0_ends_at_0_and_answers_1(exe):
    out, err = run(exe, *DOWN6, "fade:44F885:0:2", SA2_TWEAKS="modloader-default-fixes")
    assert out == [" ".join(str(255 - 6 * k) for k in range(1, 43)) + " 0 0 0 0 | 1 6 0 5151 1",
                   "0 0 | 1 6 0 5151 1"]
    assert err.splitlines() == [
        ON,
        "sa2 tweaks: mod loader fixes: ScreenFade to alpha 0 from CalcTotalScore (0x0044F885): "
        "alpha 3 -> 0, where the game's unsigned compare steps it to 9"]
    out, err = run(exe, *DOWN5, SA2_TWEAKS="modloader-default-fixes")
    assert out == [" ".join(str(255 - 5 * k) for k in range(1, 51)) + " 0 0 0 | 1 5 0 5151 1"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: mod loader fixes: ScreenFade to alpha 0 from NotCalcTotalScore (0x00450FD1): "
        "alpha 5 -> 0, where the game's unsigned compare steps it to 10"]


def test_each_fade_site_is_named_once_and_the_fades_up_are_the_games(exe):
    steps = ("alpha:9", "fade:450837:0:3", "alpha:4", "fade:4510DA:0:1", "alpha:2", "fade:450837:0:1",
             "alpha:0", "fade:44F7ED:255:3", "fade:44F8CD:128:3", "alpha:3", "fade:401234:2:1")
    want_on = ["3 0 0 | 1 6 0 5151 1", "0 | 0 6 0 5151 1", "0 | 0 6 0 5151 1",
               "6 12 18 | 0 6 255 5151 1", "24 30 36 | 0 6 128 5151 1", "2 | 0 6 2 5151 1"]
    out, err = run(exe, *steps, SA2_TWEAKS="modloader-default-fixes")
    assert out == want_on
    assert err.splitlines()[1:] == [
        "sa2 tweaks: mod loader fixes: ScreenFade to alpha 0 from CalcTotalTime (0x00450837): "
        "alpha 3 -> 0, where the game's unsigned compare steps it to 9",
        "sa2 tweaks: mod loader fixes: ScreenFade to alpha 0 from DispWinnerAndContinue "
        "(0x004510DA): alpha 4 -> 0, where the game's unsigned compare steps it to 10",
        "sa2 tweaks: mod loader fixes: ScreenFade to alpha 2 from an unlisted caller (0x00401234): "
        "alpha 3 -> 2, where the game's unsigned compare steps it to 9"]
    assert run(exe, *steps)[0] == ["3 9 3 | 0 6 0 5151 1", "10 | 0 6 0 5151 1", "8 | 0 6 0 5151 1",
                                   "6 12 18 | 0 6 255 5151 1", "24 30 36 | 0 6 128 5151 1",
                                   "9 | 0 6 2 5151 1"]


def test_on_every_call_is_the_loaders_rule_and_differs_from_the_games_only_below_the_step(exe):
    # For a target t below the step, the game and the loader differ at every alpha from 0 to t + step
    # but t itself: 25 + 10 calls with step 5, 36 + 15 with step 6.
    assert run(exe, "table", SA2_TWEAKS="modloader-default-fixes")[0] == ["0 86 0"]
    assert run(exe, "table")[0] == ["86 0 0"]


def test_an_unreadable_fade_runs_the_games_and_says_so_once(exe):
    out, err = run(exe, "alpha:3", "nostack", "fade:44F885:0:2", SA2_TWEAKS="modloader-default-fixes")
    assert out == ["9 3 | 0 6 0 5151 1"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: mod loader fixes: ScreenFade's state (the stack at 0x0002007C, 0x0174AFCF, "
        "0x01A5A274, 0x0171CDA3) does not read, so the game's fade runs"]


DRAWS = ("draw", "env:4", "draw", "drawer:1", "env:0", "draw", "drawer:0", "env:9", "draw", "env:0",
         "draw")


def test_off_chdrawcnk_skips_the_matrix_as_the_game_does(exe):
    for env in ({}, {"SA2_TWEAKS": "no-event-bars"}):
        out, err = run(exe, *DRAWS, **env)
        assert out == ["0 0x0056D7C0 0 0 1", "1 0x0056D7C0 1 4 1", "1 0x0056D9F0 0 0 1",
                       "0 0x0056D7C0 0 9 1", "0 0x0056D7C0 0 0 1"]
        assert "mod loader fixes" not in err


def test_on_chdrawcnk_builds_the_matrix_and_the_flag_is_stored_back(exe):
    out, err = run(exe, *DRAWS, SA2_TWEAKS="modloader-default-fixes")
    assert out == ["1 0x0056D7C0 1 0 1", "1 0x0056D7C0 1 4 1", "1 0x0056D9F0 0 0 1",
                   "1 0x0056D7C0 1 9 1", "1 0x0056D7C0 1 0 1"]
    assert err.splitlines() == [
        ON,
        "sa2 tweaks: mod loader fixes: chDrawCnk (0x0056DDD0) from 0x0056D570 builds the environment "
        "map's matrix it skips (0x0056DE7D)"]


def test_a_flag_the_draw_changed_is_left_and_said_once(exe):
    out, err = run(exe, "drawwrites:2", "draw", "env:0", "draw", SA2_TWEAKS="modloader-default-fixes")
    assert out == ["1 0x0056D7C0 1 2 1", "1 0x0056D7C0 1 2 1"]
    assert err.splitlines()[2:] == [
        "sa2 tweaks: mod loader fixes: chDrawCnk's flag at 0x01A27594 was 0x00000002 after the draw, "
        "not the 0x00000004 the wrap set, so it is left as the draw left it"]


def test_unreadable_flags_run_the_games_draw_and_say_so_once(exe):
    out, err = run(exe, "noflags", "draw", "draw", SA2_TWEAKS="modloader-default-fixes")
    assert out == ["0 0x0056D7C0 0 0 1", "0 0x0056D7C0 0 0 1"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: mod loader fixes: chDrawCnk's flags (0x01AED2CC, 0x01A27594) do not read, so "
        "the game's draw runs"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *DOWN6, *DRAWS, SA2_TWEAKS="modloader-default-fixes", **{replay: "/dev/null"})
    assert out == [GAME6, "0 0x0056D7C0 0 0 1", "1 0x0056D7C0 1 4 1", "1 0x0056D9F0 0 0 1",
                   "0 0x0056D7C0 0 9 1", "0 0x0056D7C0 0 0 1"]
    assert err == ("sa2 tweaks: SA2_TWEAKS=modloader-default-fixes ignored in a replay "
                   "(SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)\n")
