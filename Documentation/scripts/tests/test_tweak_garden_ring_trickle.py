"""The garden-ring-trickle tweak (runtime/tweaks/garden-ring-trickle.c) on ALW_Control_Main
(0x00530850), the main of the task every Chao World area runs once a frame.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted ALW_Control_Main and the
runtime's guest memory. Guest memory holds CurrentLevel (int16 0x01934B70), TotalRings (int32
0x0174B05C), the ring counter's task pointer (0x01AEDBFC), its total (0x01A2787C) and its seven
digits (0x01A27884); `sa2_peek` and `sa2_poke` refuse any other address, and a frame can make one
of them refuse.

The driver's arguments are steps, each "<frames>:<level>[:<flag>]": that many calls of the wrap with
CurrentLevel <level>. The stand-in body counts its calls and records TotalRings as it sees it on
entry, so a case shows that the body runs every frame, after the wrap's write. Before the steps,
the driver sets TotalRings, the counter's task and total from GRT_TOTAL, GRT_TASK, GRT_SHOWN. A
step's flag: "rt" makes TotalRings unreadable for those frames, "wt" unwritable, "rl" CurrentLevel
unreadable, "wd" the digits unwritable. After each step it prints "<body calls> <TotalRings>
<counter total> <digits>", the digits as 7 hex bytes, least significant first.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env  # noqa: E402

TWEAK = RUNTIME / "tweaks" / "garden-ring-trickle.c"
ON = {"SA2_TWEAKS": "garden-ring-trickle"}
REPORT = "sa2 tweaks: garden ring trickle (SA2_TWEAKS=garden-ring-trickle)"
CHAO_WORLD, METAL_HARBOR = 90, 10
CAP = 9999999
SAY = "sa2 tweaks: garden ring trickle: "

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define CURRENT_LEVEL  0x01934B70u
#define TOTAL_RINGS    0x0174B05Cu
#define COUNTER_TASK   0x01AEDBFCu
#define COUNTER_TOTAL  0x01A2787Cu
#define COUNTER_DIGITS 0x01A27884u
static int16_t level;
static uint32_t total, task, shown;
static uint8_t digits[7] = {0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE};
static char flag[4];
static unsigned long calls;
static uint32_t seen;

static void *at(uint32_t a, uint32_t n, int write) {
    if (a == CURRENT_LEVEL && n == 2) return strcmp(flag, "rl") == 0 ? NULL : &level;
    if (a == TOTAL_RINGS && n == 4) return strcmp(flag, write ? "wt" : "rt") == 0 ? NULL : &total;
    if (a == COUNTER_TASK && n == 4) return &task;
    if (a == COUNTER_TOTAL && n == 4) return &shown;
    if (a == COUNTER_DIGITS && n == 7) return write && strcmp(flag, "wd") == 0 ? NULL : digits;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = at(a, n, 0);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = at(a, n, 1);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__real_sub_530850(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    calls++;
    seen = total;
    return memory;
}
void *__wrap_sub_530850(void *state, uint32_t pc, void *memory);

static uint32_t env(const char *k) { const char *v = getenv(k); return v ? (uint32_t)strtoul(v, NULL, 0) : 0; }

int main(int argc, char **argv) {
    total = env("GRT_TOTAL");
    task = env("GRT_TASK");
    shown = env("GRT_SHOWN");
    for (int i = 1; i < argc; i++) {
        unsigned frames;
        int lv;
        flag[0] = 0;
        if (sscanf(argv[i], "%u:%d:%3s", &frames, &lv, flag) < 2) return 2;
        level = (int16_t)lv;
        for (unsigned f = 0; f < frames; f++) {
            int token;
            if (__wrap_sub_530850(&token, 0x00530850u, &token) != &token) return 3;
            if (seen != total) return 4;               /* the body sees the frame's total */
        }
        printf("%lu %u %u", calls, total, shown);
        putchar(' ');
        for (int d = 0; d < 7; d++) printf("%02X", digits[d]);
        putchar('\n');
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("garden_ring_trickle")
    (d / "driver.c").write_text(DRIVER)
    out = d / "grt"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(TWEAK), "-o", str(out)], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, total=0, task=0, shown=0, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, GRT_TOTAL=str(total), GRT_TASK=str(task), GRT_SHOWN=str(shown),
                                **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr.splitlines()


def digits(v: int) -> str:
    """0x005A6520's seven digit bytes for v, least significant first, 0xFF for a leading blank."""
    out = []
    for _ in range(7):
        out.append(0xFF if v == 0 else v % 10)
        v //= 10
    if out[0] == 0xFF:
        out[0] = 0
    return "".join(f"{b:02X}" for b in out)


UNTOUCHED = "EE" * 7


def row(calls: int, total: int, shown: int = 0, d: str = UNTOUCHED) -> str:
    return f"{calls} {total} {shown} {d}"


def test_the_digits_model_is_the_games():
    assert digits(0) == "00FFFFFFFFFFFF"
    assert digits(1201) == "01000201FFFFFF"
    assert digits(CAP) == "09090909090909"


# Two minutes in Chao World from 1200 rings, with no counter on screen.
GARDEN = ("59:90", "1:90", "60:90", "3480:90")


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_says_nothing(exe, env):
    out, err = run(exe, *GARDEN, total=1200, task=0x0A000000, shown=1200, **env)
    assert out == [row(59, 1200, 1200), row(60, 1200, 1200), row(120, 1200, 1200), row(3600, 1200, 1200)]
    assert err == ([] if not env.get("SA2_TWEAKS") else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *GARDEN, total=1200, **ON, **{replay: "/dev/null"})
    assert out == [row(59, 1200), row(60, 1200), row(120, 1200), row(3600, 1200)]
    assert err == ["sa2 tweaks: SA2_TWEAKS=garden-ring-trickle ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_adds_one_ring_every_60_frames_in_chao_world(exe):
    out, err = run(exe, *GARDEN, total=1200, **ON)
    assert out == [row(59, 1200), row(60, 1201), row(120, 1202), row(3600, 1260)]
    assert err == [REPORT, SAY + "ring total 1200 -> 1201 (one ring every 60 frames in Chao World)"]


def test_outside_chao_world_nothing_is_added_and_the_count_waits(exe):
    """A stage's frames neither add nor count; Chao World's count carries on where it was."""
    out, err = run(exe, "30:90", "600:10", "30:90", **ON, total=5)
    assert out == [row(30, 5), row(630, 5), row(660, 6)]
    assert err == [REPORT, SAY + "ALW_Control runs in level 10, outside Chao World, so no ring is added there",
                   SAY + "ring total 5 -> 6 (one ring every 60 frames in Chao World)"]


def test_the_total_stops_at_the_games_cap(exe):
    out, err = run(exe, "60:90", "120:90", total=CAP - 1, **ON)
    assert out == [row(60, CAP), row(180, CAP)]
    assert err == [REPORT, SAY + f"ring total {CAP - 1} -> {CAP} (one ring every 60 frames in Chao World)",
                   SAY + f"the ring total is {CAP}, the game's cap of {CAP}, so no ring is added"]


def test_a_total_above_the_cap_is_left_alone(exe):
    out, err = run(exe, "120:90", total=0x80000000, **ON)
    assert out == [row(120, 0x80000000)]
    assert err == [REPORT, SAY + f"the ring total is {0x80000000}, the game's cap of {CAP}, so no ring is added"]


def test_the_counter_on_screen_follows_the_total(exe):
    """While the Black Market's counter is up with the game's total, its total and digits move too,
    as 0x005A6B30 moves them, so a purchase subtracts from the total with the trickled rings."""
    out, err = run(exe, "60:90", "60:90", "540:90", total=1199, task=0x0A000000, shown=1199, **ON)
    assert out == [row(60, 1200, 1200, digits(1200)), row(120, 1201, 1201, digits(1201)),
                   row(660, 1210, 1210, digits(1210))]
    assert err == [REPORT, SAY + "the ring counter on screen follows the total (1200)",
                   SAY + "ring total 1199 -> 1200 (one ring every 60 frames in Chao World)"]


def test_a_counter_left_by_a_deleted_task_is_left_alone(exe):
    """A counter pointer whose total is not the game's is left alone, said once; rings still count."""
    out, err = run(exe, "120:90", total=300, task=0x0A000000, shown=250, **ON)
    assert out == [row(120, 302, 250)]
    assert err == [REPORT, SAY + "the ring counter (task 0x0A000000) holds 250, not the ring total 300, so it "
                   "is left alone and the rings go to the total only",
                   SAY + "ring total 300 -> 301 (one ring every 60 frames in Chao World)"]


def test_a_guest_read_or_write_that_fails_adds_nothing_and_says_so_once(exe):
    """A failed read or write adds nothing; the ring comes on the next frame that works."""
    out, err = run(exe, "60:90:rt", "5:90:rt", "1:90:wt", "1:90", "30:90:rl", "59:90", "1:90", **ON, total=7)
    assert out == [row(60, 7), row(65, 7), row(66, 7), row(67, 8), row(97, 8), row(156, 8), row(157, 9)]
    assert err == [REPORT,
                   SAY + "TotalRings at 0x0174B05C does not read, so no ring is added",
                   SAY + "TotalRings at 0x0174B05C does not write, so no ring is added",
                   SAY + "ring total 7 -> 8 (one ring every 60 frames in Chao World)"]


def test_a_counter_whose_digits_do_not_write_is_said(exe):
    out, err = run(exe, "60:90:wd", "60:90", total=10, task=0x0A000000, shown=10, **ON)
    assert out == [row(60, 11, 11), row(120, 12, 12, digits(12))]
    assert err == [REPORT, SAY + "the ring counter's digit string at 0x01A27884 does not write, so no ring is added",
                   SAY + "the ring counter on screen follows the total (12)",
                   SAY + "ring total 11 -> 12 (one ring every 60 frames in Chao World)"]
