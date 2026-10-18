"""The m4-time-rank tweak (runtime/tweaks/m4-time-rank.c): mission 4 ranked by the clear time, on
AwardRankForScore (0x0044F670).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted body and the runtime's peek,
poke and register accessors. The stand-in `__real_sub_44f670` does what the lifted body does with
the results at ECX: the score and the time bonus into [ecx+0xC] and [ecx+8], the Perfect flag into
[ecx+3], the rings added to the total at 0x0174B05C, the rank (A for a Perfect, else from the
mission's score table) into [ecx+2], CurrentLevelRank (0x0174B001) and [[0x01A5A230]+3], ECX left
as the rank (the body's `mov cl,[ecx+2]`), and the RET (ESP + 4). The guest's Mission4ClearTimes
(0x0173B318) is the image's table, MISSION4_CLEAR_TIMES below, which a test holds to the unpacked
image.
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
IMAGE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
LINE = "sa2 tweaks: mission 4 ranked by time"

# Mission4ClearTimes (0x0173B318): (level, minutes, seconds), then level 0x5B.
MISSION4_CLEAR_TIMES = [
    (13, 3, 0), (16, 2, 0), (9, 3, 0), (10, 2, 0), (3, 4, 0), (5, 3, 0), (17, 3, 30), (7, 3, 30),
    (23, 3, 30), (28, 4, 0), (25, 5, 0), (24, 5, 0), (32, 4, 30), (22, 5, 0), (30, 5, 0), (11, 4, 0),
    (18, 3, 30), (21, 4, 0), (14, 3, 0), (26, 3, 0), (27, 3, 30), (12, 2, 45), (8, 3, 30), (4, 3, 0),
    (6, 2, 0), (44, 4, 30), (43, 8, 0), (40, 5, 30), (36, 7, 0), (31, 5, 0)]

# Guest memory: the results and the stack (0x20000), the globals around 0x0174B000 and the tables
# at 0x0173xxxx (0x01730000), CurrentLevel (0x01934B00), and the pointer at 0x01A5A230. The HUD's
# object, at [0x01A5A230], is at 0x22000; argv's "badhud" points it at 0x90000, which does not read.
# argv: groups of 8, "<mission> <level> <minutes> <seconds> <frames> <score> <rings> <perfect at>",
# each printing "<[ecx+2]> <CurrentLevelRank> <[hud+3]> <[ecx+3]> <[ecx+8]> <[ecx+0xC]> <total
# rings> <ESP moved> <ECX>"; "badhud" before a group points the HUD pointer away for that group.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

static uint8_t low[0x3000];            /* 0x20000 */
static uint8_t data[0x20000];          /* 0x01730000 */
static uint8_t level_page[0x100];      /* 0x01934B00 */
static uint8_t hud_page[0x100];        /* 0x01A5A200 */
static uint32_t gpr[8];

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= 0x20000u && a + n <= 0x20000u + sizeof low) return low + (a - 0x20000u);
    if (a >= 0x01730000u && a + n <= 0x01730000u + sizeof data) return data + (a - 0x01730000u);
    if (a >= 0x01934B00u && a + n <= 0x01934B00u + sizeof level_page) return level_page + (a - 0x01934B00u);
    if (a >= 0x01A5A200u && a + n <= 0x01A5A200u + sizeof hud_page) return hud_page + (a - 0x01A5A200u);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static int8_t  g8(uint32_t a)  { int8_t v; memcpy(&v, at(a, 1), 1); return v; }
static int16_t g16(uint32_t a) { int16_t v; memcpy(&v, at(a, 2), 2); return v; }
static uint32_t g32(uint32_t a) { uint32_t v; memcpy(&v, at(a, 4), 4); return v; }
static void p8(uint32_t a, int8_t v) { sa2_poke(a, 1, &v); }   /* "badhud": nowhere */
static void p16(uint32_t a, int16_t v) { memcpy(at(a, 2), &v, 2); }
static void p32(uint32_t a, uint32_t v) { memcpy(at(a, 4), &v, 4); }

/* The mission 4 score table's rows the tests use (0x0173AE70; the image's values), and one row
 * for the other missions' table: level, then the four thresholds of (score + time bonus) / 100. */
static const int16_t m4_scores[][5] = {{13, 120, 140, 160, 180}, {10, 120, 140, 160, 180},
                                       {34, 200, 250, 270, 290}, {12, 200, 250, 280, 300}};
static const int16_t other_scores[][5] = {{10, 60, 80, 100, 120}};

/* AwardRankForScore as the lifted body does it (the header). */
void *__real_sub_44f670(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t ecx = gpr[SA2_ECX];
    p32(ecx + 0xC, g32(0x0174B050u));
    uint32_t t = (uint32_t)(g8(0x0174AFDBu) * 60 + g8(0x0174AFDCu));
    if (t < 60) t = 60;
    uint32_t bonus = (500u - (t - 60u)) * 20u;
    p32(ecx + 8, bonus > 10000u ? 0u : bonus);
    p8(ecx + 3, g16(0x0174B028u) >= g16(0x0174B02Eu));
    uint32_t total = g32(0x0174B05Cu) + (uint32_t)(int32_t)g16(0x0174B028u);
    p32(0x0174B05Cu, total > 9999999u ? 9999999u : total);
    int8_t rank = 0;
    if (g8(ecx + 3) == 1) rank = 4;
    else {
        const uint32_t score = (g32(ecx + 8) + g32(ecx + 0xC)) / 100u;
        const int m4 = g8(0x0174AFE3u) == 3;
        const int16_t (*rows)[5] = m4 ? m4_scores : other_scores;
        const unsigned n = m4 ? 4u : 1u;
        for (unsigned r = 0; r < n; r++) {
            if (rows[r][0] != g16(0x01934B70u)) continue;
            for (int k = 1; k <= 4 && score >= (uint32_t)rows[r][k]; k++) rank++;
        }
    }
    p8(ecx + 2, rank);
    p8(0x0174B001u, rank);
    p8(g32(0x01A5A230u) + 3, rank);
    gpr[SA2_ECX] = (uint32_t)(uint8_t)rank;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_44f670(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    static const uint8_t clear_times[] = {CLEAR_TIMES 0x5B};
    memcpy(at(0x0173B318u, sizeof clear_times), clear_times, sizeof clear_times);
    for (int i = 1; i < argc;) {
        const int badhud = strcmp(argv[i], "badhud") == 0;
        if (badhud) i++;
        if (i + 8 > argc) return 2;
        long v[8];
        for (int k = 0; k < 8; k++) v[k] = strtol(argv[i + k], NULL, 0);
        i += 8;
        memset(low, 0, sizeof low);
        p32(0x01A5A230u, badhud ? 0x90000u : 0x22000u);
        p8(0x0174AFE3u, (int8_t)v[0]);
        p16(0x01934B70u, (int16_t)v[1]);
        p8(0x0174AFDBu, (int8_t)v[2]);
        p8(0x0174AFDCu, (int8_t)v[3]);
        p8(0x0174AFDDu, (int8_t)v[4]);
        p32(0x0174B050u, (uint32_t)v[5]);
        p16(0x0174B028u, (int16_t)v[6]);
        p16(0x0174B02Eu, (int16_t)v[7]);
        p8(0x0174B001u, 0x55);
        p32(0x0174B05Cu, 0);                   /* the rings' total, per clear */
        const uint32_t results = 0x20100u, esp = 0x20800u;
        gpr[SA2_ECX] = results;
        gpr[SA2_ESP] = esp;
        int token;
        if (__wrap_sub_44f670(&token, 0x0044F670u, &token) != &token) return 3;
        printf("%d %d %d %d %u %u %u %u %u\n", g8(results + 2), g8(0x0174B001u),
               badhud ? -1 : g8(0x22000u + 3), g8(results + 3), g32(results + 8), g32(results + 0xC),
               g32(0x0174B05Cu), gpr[SA2_ESP] - esp, gpr[SA2_ECX]);
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
    d = tmp_path_factory.mktemp("m4")
    table = "".join(f"{lv}, {m}, {s}, " for lv, m, s in MISSION4_CLEAR_TIMES)
    (d / "driver.c").write_text(DRIVER.replace("CLEAR_TIMES", table))
    out = d / "m4"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "m4-time-rank.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *map(str, calls)], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


ON = {"SA2_TWEAKS": "m4-time-rank"}
M4, MH = 3, 10            # MissionNum 3 is mission 4; Metal Harbor is level 10, its limit 2:00

# Metal Harbor's mission 4, fast with a low score and slow with a high score; mission 1 and
# mission 5 there; a Perfect; City Escape (13); Cannon's Core (34); a level with no row (15).
CLEARS = [
    (M4, MH, 0, 50, 0, 0, 10, 300),           # score 0, bonus 10000: 100 -> E by score
    (M4, MH, 1, 55, 0, 30000, 10, 300),       # score 30000, bonus 8900: 389 -> A by score
    (0, MH, 0, 50, 0, 0, 10, 300),            # mission 1: 100 -> C by its table's row
    (4, MH, 1, 55, 0, 30000, 10, 300),        # mission 5: A
    (M4, MH, 1, 55, 0, 0, 300, 300),          # a Perfect: A
    (M4, 13, 1, 40, 30, 5000, 0, 300),        # 5000 + 9200: 142 -> C by score
    (M4, 34, 4, 30, 0, 9000, 0, 300),         # 9000 + 5800: 148 -> E by score
    (M4, 15, 0, 50, 0, 0, 0, 300),            # no row in either table: E
]
# What the lifted body gives each, with the tweak off: [ecx+2], CurrentLevelRank, [hud+3], the
# Perfect flag, the time bonus, the score, the total rings, ESP moved, ECX.
GAME = [
    "0 0 0 0 10000 0 10 4 0",
    "4 4 4 0 8900 30000 10 4 4",
    "3 3 3 0 10000 0 10 4 3",
    "4 4 4 0 8900 30000 10 4 4",
    "4 4 4 1 8900 0 300 4 4",
    "2 2 2 0 9200 5000 0 4 2",
    "0 0 0 0 5800 9000 0 4 0",
    "0 0 0 0 10000 0 0 4 0",
]


def flat(*clears) -> list:
    return [x for c in clears for x in c]


def with_rank(row: str, rank: int) -> str:
    """The lifted body's row with the three rank bytes set to `rank` (ECX stays the body's)."""
    f = row.split()
    return " ".join([str(rank)] * 3 + f[3:])


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_says_nothing(exe, env):
    out, err = run(exe, *flat(*CLEARS), **env)
    assert out == GAME
    assert err == ([] if not env.get("SA2_TWEAKS") else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


def test_on_ranks_mission_4_by_time_and_leaves_the_rest(exe):
    out, err = run(exe, *flat(*CLEARS), **ON)
    assert out == [
        with_rank(GAME[0], 4),    # 0:50 of 2:00 (42%): A, where the score gave E
        with_rank(GAME[1], 0),    # 1:55 of 2:00 (96%): E, where the score gave A
        GAME[2], GAME[3],         # missions 1 and 5: the score's rank
        GAME[4],                  # a Perfect keeps its A
        with_rank(GAME[5], 4),    # City Escape, 1:40 and 30 frames, counted 1:41, of 3:00 (56%): A
        with_rank(GAME[6], 3),    # Cannon's Core (34) by row 0x24, 7:00: 4:30 (64%): B
        GAME[7],                  # level 15 has no row: the score's E
    ]
    assert err == [
        "sa2 tweaks: mission 4 ranked by time (SA2_TWEAKS=m4-time-rank)",
        f"{LINE}: level 10 cleared in 0:50 of 2:00: rank A (the score gave E)",
        f"{LINE}: level 10 cleared in 1:55 of 2:00: rank E (the score gave A)",
        f"{LINE}: level 10: a Perfect keeps its A",
        f"{LINE}: level 13 cleared in 1:41 of 3:00: rank A (the score gave C)",
        f"{LINE}: level 34 cleared in 4:30 of 7:00: rank B (the score gave E)",
        f"{LINE}: level 15 has no row in Mission4ClearTimes (0x0173B318), so the clear keeps the "
        "score's rank (E)",
    ]


@pytest.mark.parametrize("seconds, frames, rank", [
    (72, 0, 4), (72, 1, 3), (73, 0, 3), (84, 0, 3), (85, 0, 2), (96, 0, 2), (97, 0, 1),
    (108, 0, 1), (109, 0, 0), (119, 59, 0)])
def test_each_threshold_is_passed_at_or_under_it(exe, seconds, frames, rank):
    """Metal Harbor's 2:00: A to 1:12 (60%), B to 1:24, C to 1:36, D to 1:48, then E; a clear
    time with frames past a whole second counts the next second, as the game's time ranks do."""
    out, _ = run(exe, M4, MH, seconds // 60, seconds % 60, frames, 0, 0, 300, **ON)
    assert [int(x) for x in out[0].split()[:3]] == [rank] * 3


def test_cannons_core_levels_use_the_row_for_0x24(exe):
    out, err = run(exe, *flat(*[(M4, lv, 2, 0, 0, 0, 0, 300) for lv in (0x22, 0x23, 0x24, 0x25, 0x26, 0x27)]), **ON)
    assert [o.split()[0] for o in out] == ["4"] * 5 + ["0"]
    assert err[1:] == [f"{LINE}: level {lv} cleared in 2:00 of 7:00: rank A (the score gave E)"
                       for lv in (34, 35, 36, 37, 38)] + [
        f"{LINE}: level 39 has no row in Mission4ClearTimes (0x0173B318), so the clear keeps the "
        "score's rank (E)"]


def test_the_side_effects_are_the_lifted_bodys(exe):
    """The Perfect flag, the time bonus, the score, the rings' total, the RET and ECX are what the
    lifted body leaves (ECX, the body's copy of the rank, is left as the body leaves it; its caller
    does not read it); only the three rank bytes change."""
    on, _ = run(exe, *flat(*CLEARS), **ON)
    for a, b in zip(on, GAME):
        assert a.split()[3:] == b.split()[3:]


def test_ignored_in_a_replay(exe):
    out, err = run(exe, *flat(*CLEARS), SA2_REPLAY_TICKS="/dev/null", **ON)
    assert out == GAME
    assert err == ["sa2 tweaks: SA2_TWEAKS=m4-time-rank ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_a_rank_it_cannot_write_in_full_is_named(exe):
    out, err = run(exe, "badhud", *CLEARS[0], **ON)
    assert out == ["4 4 -1 0 10000 0 10 4 0"]
    assert err[1] == (f"{LINE}: level 10: rank A by time not written in full (the results at "
                      "0x00020100, CurrentLevelRank, [0x01A5A230]+3 = 0x00090003)")


def test_the_clear_times_are_the_images():
    if not IMAGE.is_file():
        pytest.fail(f"the unpacked image is not present: {IMAGE}")
    import pefile
    pe = pefile.PE(str(IMAGE), fast_load=True)
    data = pe.get_data(0x0173B318 - pe.OPTIONAL_HEADER.ImageBase, 3 * len(MISSION4_CLEAR_TIMES) + 1)
    assert data == bytes(x for row in MISSION4_CLEAR_TIMES for x in row) + b"\x5b"
    rows = struct.unpack_from("<5h", pe.get_data(0x0173AE70 - pe.OPTIONAL_HEADER.ImageBase, 40), 10)
    assert rows == (16, 80, 100, 140, 160)       # the score table the stand-in's rows come from
