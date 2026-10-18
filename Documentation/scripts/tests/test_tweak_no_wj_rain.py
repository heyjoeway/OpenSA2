"""no-wj-rain (runtime/tweaks/no-wj-rain.c): White Jungle's rain task, its main AmeExec (0x00630550)
and its display (0x006308D0), skipped; the drops' creation (0x006303C0) is not touched.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py does
for no-event-bars, and linked with a driver that stands in for the two lifted functions and the
runtime's peek and register accessors. Each stand-in does what its lifted body does to what a
caller can see: the main deletes its task when its player's MainCharObj1 slot (0x01DEA6C0) is
empty and otherwise moves the drops (counted), the display draws (counted); EAX is clobbered as a
lifted body may, and each return is the RET, EIP = [ESP] and ESP + 4. Each call puts the task
runner's return address at guest [ESP] and the task at [ESP + 4] first, as the runner's push and
the lifted CALL do.
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

RUNNER_RET = 0x0046F991        # after the task runner's `call eax` at 0x0046F98F
DISPLAY_RET = 0x0046F9E1       # another runner's call (the display list's)

# Guest memory: the stack at 0x20000, the rain task at 0x30000 with its data at 0x30100 (the player
# at +3), MainCharObj1 at 0x01DEA6C0. argv: one call each,
#   "exec:<player>:<slot>"  the main, its data naming player 0 or 1, that slot set (1) or empty (0)
#   "display"               the display
#   "exec:unreadable:1" / "display:unreadable"  a stack that does not read
#   "exec:badtask:1"        a task pointer whose data does not read
# Each prints "<kind> <moved> <drawn> <deleted> <ESP moved> <EIP> <EAX>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
#define TASK  0x30000u
#define MCO1  0x01DEA6C0u
static uint8_t stack[0x100], task[0x200], mco1[8];
static uint32_t gpr[SA2_GPR_COUNT];
static int moved, drawn, deleted;

static int in(uint32_t a, uint32_t n, uint32_t base, uint32_t size) {
    return a >= base && a + n <= base + size;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (in(a, n, STACK, sizeof stack)) { memcpy(out, stack + (a - STACK), n); return SA2_OK; }
    if (in(a, n, TASK, sizeof task)) { memcpy(out, task + (a - TASK), n); return SA2_OK; }
    if (in(a, n, MCO1, sizeof mco1)) { memcpy(out, mco1 + (a - MCO1), n); return SA2_OK; }
    return 1;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static void *ret(void *memory) {
    uint32_t r = 0;
    if (sa2_peek(gpr[SA2_ESP], 4, &r) != SA2_OK) r = 0xFFFFFFFFu;
    gpr[SA2_EIP] = r;
    gpr[SA2_ESP] += 4;
    gpr[SA2_EAX] = 0x11111111u;                /* a lifted body leaves EAX as it likes */
    return memory;
}
void *__real_sub_630550(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    uint32_t t = 0, d = 0, s = 0;
    uint8_t p = 0;
    sa2_peek(gpr[SA2_ESP] + 4, 4, &t);
    if (sa2_peek(t + 0x34, 4, &d) == SA2_OK && sa2_peek(d + 3, 1, &p) == SA2_OK
        && sa2_peek(MCO1 + 4u * p, 4, &s) == SA2_OK && s == 0) deleted = 1;
    else moved = 1;
    return ret(memory);
}
void *__real_sub_6308d0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    drawn = 1;
    return ret(memory);
}
void *__wrap_sub_630550(void *state, uint32_t pc, void *memory);
void *__wrap_sub_6308d0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char kind[16] = {0}, where[16] = {0};
        unsigned slot = 0;
        if (sscanf(argv[i], "%15[a-z]:%15[a-z0-9]:%u", kind, where, &slot) < 1) return 2;
        const int unreadable = strcmp(where, "unreadable") == 0;
        const int badtask = strcmp(where, "badtask") == 0;
        const unsigned player = (unsigned)strtoul(where, NULL, 0) & 1u;
        const int exec = strcmp(kind, "exec") == 0;
        const uint32_t r = exec ? 0x0046F991u : 0x0046F9E1u;
        const uint32_t t = badtask ? 0x90000u : TASK;
        const uint32_t d = TASK + 0x100u;
        const uint32_t other = 0x01AE0000u;    /* the other player's slot, always set */
        memcpy(stack + 0x80, &r, 4);
        memcpy(stack + 0x84, &t, 4);
        memcpy(task + 0x34, &d, 4);
        task[0x103] = (uint8_t)player;
        const uint32_t mine = slot ? 0x01AD0000u : 0u;
        memcpy(mco1 + 4 * player, &mine, 4);
        memcpy(mco1 + 4 * (player ^ 1u), &other, 4);
        const uint32_t esp = unreadable ? 0x90000u : STACK + 0x80u;
        gpr[SA2_ESP] = esp;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        gpr[SA2_EIP] = 0;
        moved = drawn = deleted = 0;
        int token;
        void *m = exec ? __wrap_sub_630550(&token, 0x00630550u, &token)
                       : __wrap_sub_6308d0(&token, 0x006308D0u, &token);
        if (m != &token) return 3;
        printf("%s %d %d %d %u 0x%08X 0x%08X\n", kind, moved, drawn, deleted, gpr[SA2_ESP] - esp,
               gpr[SA2_EIP], gpr[SA2_EAX]);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("no_wj_rain")
    (d / "driver.c").write_text(DRIVER)
    out = d / "wjr"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-wj-rain.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


MOVED = f"exec 1 0 0 4 0x{RUNNER_RET:08X} 0x11111111"        # the lifted main moved the drops
DELETED = f"exec 0 0 1 4 0x{RUNNER_RET:08X} 0x11111111"      # the lifted main deleted its task
DRAWN = f"display 0 1 0 4 0x{DISPLAY_RET:08X} 0x11111111"    # the lifted display drew
EXEC_SKIPPED = f"exec 0 0 0 4 0x{RUNNER_RET:08X} 0xDEADBEEF"  # the RET alone: EAX as it was
DISPLAY_SKIPPED = f"display 0 0 0 4 0x{DISPLAY_RET:08X} 0xDEADBEEF"

# A frame of one-player White Jungle (the main, then the display), a second frame, player 2's
# task in a two-player game, and a frame after player 1's object is gone.
CALLS = ["exec:0:1", "display", "exec:0:1", "display", "exec:1:1", "display", "exec:0:0"]
GAME = [MOVED, DRAWN, MOVED, DRAWN, MOVED, DRAWN, DELETED]

SKIPPED_LINES = [
    "sa2 tweaks: wj rain off: rain not moved (AmeExec 0x00630550 skipped)",
    "sa2 tweaks: wj rain off: rain not drawn (display 0x006308D0 skipped)"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_code_and_says_nothing_of_the_rain(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == GAME
    assert not [line for line in err if "rain" in line]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-wj-rain", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ["sa2 tweaks: SA2_TWEAKS=no-wj-rain ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_the_rain_is_neither_moved_nor_drawn_but_its_task_still_deletes_itself(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-wj-rain")
    assert out == [EXEC_SKIPPED, DISPLAY_SKIPPED, EXEC_SKIPPED, DISPLAY_SKIPPED, EXEC_SKIPPED,
                   DISPLAY_SKIPPED, DELETED]
    assert err == ["sa2 tweaks: wj rain off (SA2_TWEAKS=no-wj-rain)", *SKIPPED_LINES,
                   "sa2 tweaks: wj rain off: player 1's rain task deletes itself (its player is "
                   "gone)"]


def test_player_twos_task_reads_player_twos_slot(exe):
    out, err = run(exe, "exec:1:0", "exec:1:1", SA2_TWEAKS="no-wj-rain")
    assert out == [DELETED, EXEC_SKIPPED]
    assert err[1] == ("sa2 tweaks: wj rain off: player 2's rain task deletes itself (its player is "
                      "gone)")


def test_each_line_is_said_once(exe):
    out, err = run(exe, *(["exec:0:1", "display"] * 3), "exec:0:0", "exec:0:0",
                   SA2_TWEAKS="no-wj-rain")
    assert out == [EXEC_SKIPPED, DISPLAY_SKIPPED] * 3 + [DELETED, DELETED]
    assert len(err) == 4


def test_with_another_tweak_on_it_still_acts(exe):
    out, err = run(exe, "exec:0:1", "display", SA2_TWEAKS="hide-hud,no-wj-rain")
    assert out == [EXEC_SKIPPED, DISPLAY_SKIPPED]
    assert err == ["sa2 tweaks: hud hidden, wj rain off (SA2_TWEAKS=hide-hud,no-wj-rain)",
                   *SKIPPED_LINES]


def test_unreadable_guest_memory_lets_the_call_through_and_says_so(exe):
    out, err = run(exe, "exec:unreadable:1", "display:unreadable", "exec:badtask:1", "exec:0:1",
                   "display", SA2_TWEAKS="no-wj-rain")
    assert out == ["exec 1 0 0 4 0xFFFFFFFF 0x11111111", "display 0 1 0 4 0xFFFFFFFF 0x11111111",
                   MOVED, EXEC_SKIPPED, DISPLAY_SKIPPED]
    assert err[1:] == [
        "sa2 tweaks: wj rain off: the guest stack at 0x00090000 does not read, so the rain's main "
        "goes through",
        "sa2 tweaks: wj rain off: the rain task 0x00090000, its data or its player's slot does not "
        "read, so the rain's main goes through",
        *SKIPPED_LINES]
