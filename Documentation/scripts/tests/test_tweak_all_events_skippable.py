"""all-events-skippable (runtime/tweaks/all-events-skippable.c) on the event executor, 0x005FB270.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted executor and the
runtime's guest accessors. The stand-in `__real_sub_5fb270` does what the executor's play arm does
with Start (0x005FB5E8-0x005FB631, then 0x005FB637-0x005FB64A): in state 3, with [0x01DB0F80] == 0,
the event's view count not 0, the byte at 0x0174AFC2 not 0 or the event not one of 0x1C, 0xD0,
0xD2, 0xD3, Start newly pressed on ControllerPointers[0] and [0x01DB0BE8] == 0, it writes state 4;
then, when the event's own end has come (the call's `end` field), state 4 and [0x01DB0EB0] = 1,
else [0x01DB0EB0] = 0. It reads guest memory directly and records the gate byte it saw, so a case
shows what the lifted code would see; the wrap's own sa2_peek and sa2_poke calls are counted
apart, so off can be shown to touch nothing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, RUNTIME, VARS, zig, zig_env  # noqa: E402,F401  (zig is a fixture)

LOCKED = [28, 208, 210, 211]
REPORT = "sa2 tweaks: every event skippable (SA2_TWEAKS=all-events-skippable)"

# argv: one call per argument, "mode,event,start,views,gate,end[,clobber]"; optional leading
# "unmap=<address>" makes that guest address fail to read and write. Each call prints
# "<state after> <gate the executor saw> <gate after> <[0x01DB0EB0]> <wrap's guest accesses>".
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STATE   0x01A283FCu
#define EVENT   0x01DB0FCCu
#define GATE    0x0174AFC2u
#define PADS    0x01DEFB60u
#define ENDED   0x01DB0EB0u
#define LOCK1   0x01DB0F80u
#define LOCK2   0x01DB0BE8u
#define VIEWS   0x01DEB080u
#define PAD     0x00030000u

static uint8_t m_state[4], m_event[4], m_gate[1], m_pads[4], m_ended[4], m_lock1[4], m_lock2[4];
static uint8_t m_views[0x200], m_pad[0x20];
static const struct { uint32_t a; uint32_t n; uint8_t *p; } REGIONS[] = {
    {STATE, 4, m_state}, {EVENT, 4, m_event}, {GATE, 1, m_gate}, {PADS, 4, m_pads},
    {ENDED, 4, m_ended}, {LOCK1, 4, m_lock1}, {LOCK2, 4, m_lock2},
    {VIEWS, sizeof m_views, m_views}, {PAD, sizeof m_pad, m_pad},
};
static uint32_t unmapped = 0xFFFFFFFFu;
static unsigned wrap_accesses;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a <= unmapped && unmapped < a + n) return NULL;
    for (unsigned i = 0; i < sizeof REGIONS / sizeof REGIONS[0]; i++)
        if (a >= REGIONS[i].a && a + n <= REGIONS[i].a + REGIONS[i].n) return REGIONS[i].p + (a - REGIONS[i].a);
    return NULL;
}
static uint32_t rd32(uint32_t a) { uint32_t v; memcpy(&v, at(a, 4), 4); return v; }
static void wr32(uint32_t a, uint32_t v) { memcpy(at(a, 4), &v, 4); }

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    wrap_accesses++;
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    wrap_accesses++;
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

static int end_now, clobber, gate_seen;
void *__real_sub_5fb270(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    gate_seen = m_gate[0];
    if (rd32(STATE) != 3) return memory;
    const uint32_t ecx = rd32(EVENT);
    int skip = rd32(LOCK1) == 0 && m_views[ecx] != 0;
    if (skip && m_gate[0] == 0 && (ecx == 0x1C || ecx == 0xD0 || ecx == 0xD2 || ecx == 0xD3)) skip = 0;
    if (skip && !(rd32(rd32(PADS) + 0x10) & 8)) skip = 0;
    if (skip && rd32(LOCK2) != 0) skip = 0;
    if (skip) wr32(STATE, 4);                      /* 0x005FB631: state = EBP = 4 */
    if (end_now) { wr32(STATE, 4); wr32(ENDED, 1); }   /* 0x005FB644 */
    else wr32(ENDED, 0);                           /* 0x005FB40F */
    if (clobber) m_gate[0] = (uint8_t)clobber;
    return memory;
}
void *__wrap_sub_5fb270(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    wr32(PADS, PAD);
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "unmap=", 6) == 0) { unmapped = (uint32_t)strtoul(argv[i] + 6, NULL, 0); continue; }
        unsigned mode, event, start, views, gate, end, clob = 0;
        if (sscanf(argv[i], "%u,%u,%u,%u,%u,%u,%u", &mode, &event, &start, &views, &gate, &end, &clob) < 6) return 2;
        wr32(STATE, mode); wr32(EVENT, event); wr32(PAD + 0x10, start ? 8u : 0u);
        m_views[event] = (uint8_t)views; m_gate[0] = (uint8_t)gate; wr32(ENDED, 7);
        end_now = (int)end; clobber = (int)clob; wrap_accesses = 0; gate_seen = -1;
        int token;
        if (__wrap_sub_5fb270(&token, 0x005FB270u, &token) != &token) return 3;
        printf("%u %d %u %u %u\n", rd32(STATE), gate_seen, m_gate[0], rd32(ENDED), wrap_accesses);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(zig, tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("tweak_all_events_skippable")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(zig), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "all-events-skippable.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def call(event: int, start: int = 1, mode: int = 3, views: int = 1, gate: int = 0, end: int = 0,
         clobber: int = 0) -> str:
    return f"{mode},{event},{start},{views},{gate},{end},{clobber}"


# Every locked event and an ordinary one (100), with Start and without, in each state the executor
# has around play, with the gate already open, an unseen event, and an event's own end.
CALLS = ([call(e, s) for e in LOCKED + [100] for s in (1, 0)]
         + [call(28, mode=m) for m in (0, 1, 2, 4)]
         + [call(28, gate=2), call(28, views=0), call(28, end=1), call(100, end=1)])
# What the lifted executor alone does with them: "<state> <gate seen> <gate after> <ended>".
LIFTED = (["3 0 0 0", "3 0 0 0"] * 4 + ["4 0 0 0", "3 0 0 0"]
          + ["0 0 0 7", "1 0 0 7", "2 0 0 7", "4 0 0 7"]
          + ["4 2 2 0", "3 0 0 0", "4 0 0 1", "4 0 0 1"])


def results(out: list[str]) -> list[str]:
    return [line.rsplit(" ", 1)[0] for line in out]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_executor_and_touches_no_guest_memory(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert results(out) == LIFTED
    assert [line.rsplit(" ", 1)[1] for line in out] == ["0"] * len(CALLS)
    assert err == ([] if not env.get("SA2_TWEAKS") else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


def test_on_start_skips_the_four_locked_events_and_the_gate_is_put_back(exe):
    out, err = run(exe, *[call(e) for e in LOCKED], call(28), SA2_TWEAKS="all-events-skippable")
    assert results(out) == ["4 1 0 0"] * 5
    assert err == [REPORT] + [f"sa2 tweaks: every event skippable: event {e} skipped with Start"
                              for e in LOCKED]


def test_on_changes_nothing_but_the_locked_events_skip(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="all-events-skippable")
    expect = list(LIFTED)
    for i in (0, 2, 4, 6):                        # each locked event with Start
        expect[i] = "4 1 0 0"
    for i in (1, 3, 5, 7):                        # without Start: the gate is opened and put back
        expect[i] = "3 1 0 0"
    expect[14 + 1] = "3 1 0 0"                    # 28 never seen: the view count still says no
    expect[14 + 2] = "4 1 0 1"                    # 28's own end; no skip is reported
    assert results(out) == expect
    assert err == [REPORT] + [f"sa2 tweaks: every event skippable: event {e} skipped with Start"
                              for e in LOCKED]


def test_on_leaves_the_gate_alone_outside_play_and_for_other_events(exe):
    calls = [call(28, mode=m) for m in (0, 1, 2, 4)] + [call(100), call(100, 0), call(28, gate=2)]
    out, _ = run(exe, *calls, SA2_TWEAKS="all-events-skippable")
    assert results(out) == ["0 0 0 7", "1 0 0 7", "2 0 0 7", "4 0 0 7", "4 0 0 0", "3 0 0 0",
                            "4 2 2 0"]


def test_a_gate_the_executor_changed_is_not_put_back(exe):
    out, _ = run(exe, call(28, clobber=5), SA2_TWEAKS="all-events-skippable")
    assert results(out) == ["4 1 5 0"]


def test_a_gate_that_does_not_read_keeps_the_event_locked_and_says_so(exe):
    out, err = run(exe, "unmap=0x0174AFC2", call(28), call(208), SA2_TWEAKS="all-events-skippable")
    assert [line.split()[0] for line in out] == ["3", "3"]
    assert err == [REPORT, "sa2 tweaks: every event skippable: guest 0x0174AFC2 does not read, so "
                           "the executor runs as with the tweak off"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="all-events-skippable", **{replay: "/dev/null"})
    assert results(out) == LIFTED
    assert [line.rsplit(" ", 1)[1] for line in out] == ["0"] * len(CALLS)
    assert err == ["sa2 tweaks: SA2_TWEAKS=all-events-skippable ignored in a replay "
                   "(SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)"]
