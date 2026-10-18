"""runtime/sa2_tweaks.c: play's SA2_TWEAKS, read once at the start, and its first tweak,
no-event-bars, on the events' "shown at 4:3?" answer (0x005F8EC0).

The file is built with the pinned zig, as tools/s4/link_s4.py compiles a runtime source (-O2,
x86_64-linux-musl), and linked with a driver that stands in for the lifted answer and the
runtime's peek and register accessors. The stand-in `__real_sub_5f8ec0` does what the lifted body
does: EAX = 1 when ECX is in the game's table of 4:3 events, else 0, then the RET that pops the
return address (ESP + 4). Each call puts a caller's return address at guest [ESP] first, as the
lifted CALL does, so a case reads what each caller would see in EAX.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
import link_s4 as LS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
EXECUTOR, MOVIE = "0x005FB508", "0x00601948"     # the two callers' return addresses

# The guest stack at 0x20000. argv: pairs of (return address, event ID); each prints
# "<event> <EAX> <ESP moved>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100];
static uint32_t gpr[8];

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static const uint32_t table[14] = {21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409};
void *__real_sub_5f8ec0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    gpr[SA2_EAX] = 0;
    for (int i = 0; i < 14; i++) if (gpr[SA2_ECX] == table[i]) gpr[SA2_EAX] = 1;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_5f8ec0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i + 1 < argc; i += 2) {
        const uint32_t ret = (uint32_t)strtoul(argv[i], NULL, 0), event = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        const uint32_t esp = strcmp(argv[i], "unreadable") == 0 ? 0x90000u : STACK + 0x80u;
        if (esp == STACK + 0x80u) memcpy(stack + 0x80, &ret, 4);
        gpr[SA2_ESP] = esp;
        gpr[SA2_ECX] = event;
        gpr[SA2_EAX] = 0xDEADBEEFu;
        int token;
        if (__wrap_sub_5f8ec0(&token, 0x005F8EC0u, &token) != &token) return 3;
        printf("%u %u %u\n", event, gpr[SA2_EAX], gpr[SA2_ESP] - esp);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("tweaks")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           "-o", str(out)], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


# The intro (361) and a stage's (21) in-engine events, an event outside the table (50), and the
# one movie in the table (409, at the movie player's call).
CALLS = (EXECUTOR, "361", EXECUTOR, "21", EXECUTOR, "50", MOVIE, "409", EXECUTOR, "361")
GAME = ["361 1 4", "21 1 4", "50 0 4", "409 1 4", "361 1 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}])
def test_off_is_the_lifted_answer_and_says_nothing(exe, env):
    assert run(exe, *CALLS, **env) == (GAME, "")


def test_no_event_bars_answers_0_to_the_event_executor_alone(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars")
    assert out == ["361 0 4", "21 0 4", "50 0 4", "409 1 4", "361 0 4"]
    assert err.splitlines() == ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)",
                                "sa2 tweaks: event-bars off: event 361's side bars not drawn",
                                "sa2 tweaks: event-bars off: event 21's side bars not drawn"]


def test_the_movie_keeps_its_bars(exe):
    out, err = run(exe, MOVIE, "409", MOVIE, "21", SA2_TWEAKS="no-event-bars")
    assert out == ["409 1 4", "21 1 4"]
    assert err == "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)\n"


def test_names_are_trimmed_and_repeats_are_one(exe):
    out, err = run(exe, EXECUTOR, "361", SA2_TWEAKS=" no-event-bars ,no-event-bars,")
    assert out == ["361 0 4"]
    assert err.splitlines()[0] == "sa2 tweaks: event-bars off (SA2_TWEAKS= no-event-bars ,no-event-bars,)"


@pytest.mark.parametrize("value, on", [("event-bars", False), ("No-Event-Bars", False),
                                       ("no-event-bars,widescreen-fmv", True)])
def test_a_name_that_is_no_tweak_is_named_and_ignored(exe, value, on):
    out, err = run(exe, EXECUTOR, "361", SA2_TWEAKS=value)
    assert out == (["361 0 4"] if on else ["361 1 4"])
    unknown = value.split(",")[-1]
    assert err.splitlines()[0] == (
        f"sa2 tweaks: {'event-bars off' if on else 'none on'} (SA2_TWEAKS={value}; no tweak is named "
        f"\"{unknown}\", so it is ignored; the tweaks: no-event-bars)")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="no-event-bars", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=no-event-bars ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
    assert run(exe, *CALLS, **{replay: "/dev/null"}) == (GAME, "")


def test_an_unreadable_stack_keeps_the_bars_and_says_so(exe):
    out, err = run(exe, "unreadable", "361", EXECUTOR, "361", SA2_TWEAKS="no-event-bars")
    assert out == ["361 1 4", "361 0 4"]
    assert ("sa2 tweaks: event-bars off: the guest stack at 0x00090000 does not read, so the caller "
            "is unknown and the event keeps its bars") in err.splitlines()


def test_every_link_wraps_the_answer_and_no_replay_gets_the_setting(exe):
    assert "sa2_tweaks.c" in LS.RUNTIME_C
    assert "sub_5f8ec0" in LS.PRODUCTION_WRAPS
    assert LS.PRODUCTION_WRAPS.index("sub_5f8ec0") < LS.PRODUCTION_WRAPS.index("sub_405310")
    import device_profile as DP
    assert "SA2_TWEAKS" in DP.PLAY_ENV and "SA2_TWEAKS" in DP.OWNED
