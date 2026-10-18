"""radar-all-pieces (runtime/tweaks/radar-all-pieces.c): the later treasure-hunt pieces get what the
game gives the first one. Their radar icon, drawn by 0x00739FE0 from the radar's display at
0x0073A7C4, is the distance-banded one; the '!' over the player (0x0073A450) is drawn for a later
piece under 30.0, once a frame; and the radar main's beep distance (0x0077FBD0, called at
0x00739D74) is taken to the nearest piece still to find, so the beep keeps that piece's pace.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the two lifted functions, the
runtime's guest call (sa2_guest_stdcall) and its peek and register accessors. The driver's guest
memory holds a stack, the image's four bounds (500.0 at 0x00904F30, 300.0f at 0x0109E030, 100.0
at 0x00904E30, 30.0 at 0x00905090), the radar's data block (its pointer at 0x01AF014C; slot i's
byte at +0x2C + 16i, its position at +0x30 + 16i) and the player's position.

A drawing call lays out the stack as the display leaves it at the callee's entry: the return
address at [ESP], the slot (the pushed ESI) at [ESP+4], the blink clock (EBP) at [ESP+8], and the
piece's distance, the caller's float at its [esp+0x14], at [ESP+0x20]. The stand-in
`__real_sub_739fe0` records the icon in ECX and the slot it sees, leaves EAX, ECX and EDX as a body
would, and pops the return address, as the lifted RET does. A distance call puts the return
address at [ESP], a slot's position in EAX and the player's in ECX; the stand-in
`__real_sub_77fbd0` records the position it is given and pops the return address.
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
LATER, TRACKED, TWO_P = "0x0073A7C9", "0x0073A816", "0x0073A92B"   # return addresses
BEEP = "0x00739D79"            # after the radar main's one-player distance call at 0x00739D74
ON = {"SA2_TWEAKS": "radar-all-pieces"}
BANG = "! 0x0073A450 0 kept"   # the '!' drawn, no argument, EAX, ECX and EDX put back

# argv, in order:
#   "bounds=<500>,<300>,<100>"   the image's bounds (by default 500.0, 300.0f, 100.0)
#   "pieces=<b>,<x>,<y>,<z>;..." the three slots' bytes (0xFE found) and positions
#   "player=<x>,<y>,<z>"         the player's position (by default 0,0,0)
#   "data=unreadable"            the radar's data pointer does not read
#   "dist <ret> <slot>"          a distance call: EAX the slot's position; prints
#                                "dist <slot whose position the lifted body got> <ESP moved>"
#   <ret|unreadable> <ECX> <slot on the stack> <distance>
#                                a drawing call; prints "<ECX the lifted body saw> <slot it saw>
#                                <ESP moved>", then BANG's shape for each '!' it drew
DRIVER = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_guest_call.h"

#define STACK 0x20000u
#define DATA 0x30000u
#define PLAYER 0x31000u
static uint8_t stack[0x100], data[0x60];
static float player[3];
static int data_unreadable;
static double b500 = 500.0, b100 = 100.0, b30 = 30.0;
static float b300 = 300.0f;
static uint32_t gpr[SA2_GPR_COUNT];

static int in(uint32_t a, uint32_t n, uint32_t base, uint32_t size) { return a >= base && a + n <= base + size; }
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    const uint32_t ptr = DATA;
    if (in(a, n, STACK, sizeof stack)) { memcpy(out, stack + (a - STACK), n); return SA2_OK; }
    if (in(a, n, DATA, sizeof data)) { memcpy(out, data + (a - DATA), n); return SA2_OK; }
    if (in(a, n, PLAYER, sizeof player)) { memcpy(out, (uint8_t *)player + (a - PLAYER), n); return SA2_OK; }
    if (a == 0x01AF014Cu && n == 4 && !data_unreadable) { memcpy(out, &ptr, 4); return SA2_OK; }
    if (a == 0x00904F30u && n == 8) { memcpy(out, &b500, 8); return SA2_OK; }
    if (a == 0x0109E030u && n == 4) { memcpy(out, &b300, 4); return SA2_OK; }
    if (a == 0x00904E30u && n == 8) { memcpy(out, &b100, 8); return SA2_OK; }
    if (a == 0x00905090u && n == 8) { memcpy(out, &b30, 8); return SA2_OK; }
    return 1;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

/* What a lifted body leaves in EAX, ECX and EDX, and what the '!' leaves there. */
#define LEFT_EAX 0xA0A0A0A0u
#define LEFT_ECX 0xC0C0C0C0u
#define LEFT_EDX 0xD0D0D0D0u
static uint32_t seen_ecx, seen_slot, seen_eax, bang_fn, bang_argc, bang_esp;
static int bangs;
void *__real_sub_739fe0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    seen_ecx = gpr[SA2_ECX];
    if (sa2_peek(gpr[SA2_ESP] + 4, 4, &seen_slot) != SA2_OK) seen_slot = 0xFFFFFFFFu;
    gpr[SA2_EAX] = LEFT_EAX, gpr[SA2_ECX] = LEFT_ECX, gpr[SA2_EDX] = LEFT_EDX;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__real_sub_77fbd0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    seen_eax = gpr[SA2_EAX];
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
uint32_t sa2_guest_stdcall(void *s, uint32_t fn, unsigned argc, const uint32_t *argv) {
    (void)s; (void)argv;
    bangs++, bang_fn = fn, bang_argc = argc, bang_esp = gpr[SA2_ESP];
    gpr[SA2_EAX] = gpr[SA2_ECX] = gpr[SA2_EDX] = 0xBADu;
    return 0xBADu;
}
void *__wrap_sub_739fe0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_77fbd0(void *state, uint32_t pc, void *memory);

static void pieces(const char *p) {
    for (int i = 0; i < 3; i++) {
        char *e;
        data[0x2C + 16 * i] = (uint8_t)strtoul(p, &e, 0);
        for (int k = 0; k < 3; k++) {
            const float v = strtof(e + 1, &e);   /* "nan" too */
            memcpy(data + 0x30 + 16 * i + 4 * k, &v, 4);
        }
        p = e + 1;
    }
}

int main(int argc, char **argv) {
    int token;
    for (int i = 1; i < argc;) {
        if (strncmp(argv[i], "bounds=", 7) == 0) {
            char *e;
            b500 = strtod(argv[i] + 7, &e);
            b300 = strtof(e + 1, &e);
            b100 = strtod(e + 1, NULL);
            i++;
        } else if (strncmp(argv[i], "pieces=", 7) == 0) {
            pieces(argv[i++] + 7);
        } else if (strncmp(argv[i], "player=", 7) == 0) {
            char *e = argv[i++] + 6;
            for (int k = 0; k < 3; k++) player[k] = strtof(e + 1, &e);
        } else if (strcmp(argv[i], "data=unreadable") == 0) {
            data_unreadable = 1;
            i++;
        } else if (strcmp(argv[i], "dist") == 0 && i + 2 < argc) {
            const uint32_t ret = (uint32_t)strtoul(argv[i + 1], NULL, 0);
            const uint32_t slot = (uint32_t)strtoul(argv[i + 2], NULL, 0), esp = STACK + 0x40u;
            memset(stack, 0xCC, sizeof stack);
            memcpy(stack + 0x40, &ret, 4);
            gpr[SA2_ESP] = esp;
            gpr[SA2_EAX] = DATA + 0x30u + 16u * slot;
            gpr[SA2_ECX] = PLAYER;
            if (__wrap_sub_77fbd0(&token, 0x0077FBD0u, &token) != &token) return 3;
            const uint32_t off = seen_eax - (DATA + 0x30u);
            printf("dist %d %u\n", off % 16u == 0 && off / 16u < 3 ? (int)(off / 16u) : -1,
                   gpr[SA2_ESP] - esp);
            i += 3;
        } else if (i + 3 < argc) {
            const int unreadable = strcmp(argv[i], "unreadable") == 0;
            const uint32_t ret = unreadable ? 0 : (uint32_t)strtoul(argv[i], NULL, 0);
            const uint32_t ecx = (uint32_t)strtoul(argv[i + 1], NULL, 0);
            const uint32_t slot = (uint32_t)strtoul(argv[i + 2], NULL, 0), clock = 0x1234u;
            const float d = strcmp(argv[i + 3], "nan") == 0 ? NAN : strtof(argv[i + 3], NULL);
            const uint32_t esp = unreadable ? 0x90000u : STACK + 0x40u;
            memset(stack, 0xCC, sizeof stack);
            if (!unreadable) {
                memcpy(stack + 0x40, &ret, 4);
                memcpy(stack + 0x44, &slot, 4);
                memcpy(stack + 0x48, &clock, 4);
                memcpy(stack + 0x60, &d, 4);
            }
            gpr[SA2_ESP] = esp;
            gpr[SA2_ECX] = ecx;
            seen_slot = 0xFFFFFFFFu;
            bangs = 0;
            if (__wrap_sub_739fe0(&token, 0x00739FE0u, &token) != &token) return 3;
            printf("0x%02X %d %u\n", seen_ecx, (int)seen_slot, gpr[SA2_ESP] - esp);
            for (int b = 0; b < bangs; b++)
                printf("! 0x%08X %u %s\n", bang_fn, bang_argc,
                       bang_esp == esp + 4u && gpr[SA2_EAX] == LEFT_EAX && gpr[SA2_ECX] == LEFT_ECX &&
                       gpr[SA2_EDX] == LEFT_EDX ? "kept" : "changed");
            i += 4;
        } else {
            return 2;
        }
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
    d = tmp_path_factory.mktemp("radar")
    (d / "driver.c").write_text(DRIVER)
    out = d / "radar"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "radar-all-pieces.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def later(slot: int, d: str) -> tuple[str, ...]:
    """The display's call for a later piece: ECX = slot + 7, the slot on the stack."""
    return (LATER, str(slot + 7), str(slot), d)


def tracked(slot: int, d: str) -> tuple[str, ...]:
    """The display's call for the first piece, here with its 100-300 icon slot + 0xD."""
    return (TRACKED, str(slot + 0xD), str(slot), d)


# Slots 1 and 2 (later pieces) at the spec's four distances, and slot 0 at one of them: the far,
# 300-500, 100-300 and near bands.
CALLS = (*later(1, "600"), *later(2, "400"), *later(1, "200"), *later(2, "50"), *later(0, "350"))
PLAIN = ["0x08 1 4", "0x09 2 4", "0x08 1 4", "0x09 2 4", "0x07 0 4"]
OFF_ENVS = [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}]


@pytest.mark.parametrize("env", OFF_ENVS)
def test_off_draws_the_plain_icon_and_says_nothing_of_the_radar(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == PLAIN
    assert not any("radar" in line for line in err)


def test_on_each_later_piece_gets_the_tracked_paths_icon(exe):
    out, err = run(exe, *CALLS, **ON)
    # 600 > 500: slot+4; 400 > 300: slot+0x10; 200 > 100: slot+0xD; 50: slot+0xA.
    assert out == ["0x05 1 4", "0x12 2 4", "0x0E 1 4", "0x0C 2 4", "0x10 0 4"]
    assert err == [
        "sa2 tweaks: radar tracks all pieces (SA2_TWEAKS=radar-all-pieces)",
        "sa2 tweaks: radar tracks all pieces: slot 1 tracked (icon 0x05 for 0x08, distance 600.0)",
        "sa2 tweaks: radar tracks all pieces: slot 2 tracked (icon 0x12 for 0x09, distance 400.0)",
        "sa2 tweaks: radar tracks all pieces: slot 0 tracked (icon 0x10 for 0x07, distance 350.0)"]


@pytest.mark.parametrize("d, icon", [
    ("500", 0x10), ("500.0001", 0x04),          # 500.0 itself is not "> 500"
    ("300", 0x0D), ("300.00003", 0x10),         # the float just above 300.0f
    ("100", 0x0A), ("100.00001", 0x0D),
    ("0", 0x0A), ("-1", 0x0A), ("nan", 0x0A),  # a NaN fails every compare, as x87's do
    ("1e30", 0x04)])
def test_the_bands_edges_are_the_tracked_paths(exe, d, icon):
    out, _ = run(exe, *later(0, d), **ON)
    assert out[0] == f"0x{icon:02X} 0 4"
    # 0 and -1 are under 30.0 and no first piece drew its '!' before.
    assert out[1:] == ([BANG] if d in ("0", "-1") else [])


def test_the_bounds_are_read_from_the_image(exe):
    out, _ = run(exe, "bounds=1000,800,10", *later(1, "600"), *later(1, "900"), *later(1, "5"), **ON)
    assert out == ["0x0E 1 4", "0x11 1 4", "0x0B 1 4", BANG]


@pytest.mark.parametrize("ret", [TRACKED, TWO_P, "0x005FB508"])
def test_other_callers_are_left_alone(exe, ret):
    """The first piece's own call (0x0073A811), the two-player radar and any other caller."""
    out, err = run(exe, ret, "9", "2", "50", ret, "0x0C", "2", "600", ret, "0x0C", "2", "10", **ON)
    assert out == ["0x09 2 4", "0x0C 2 4", "0x0C 2 4"]
    assert err == ["sa2 tweaks: radar tracks all pieces (SA2_TWEAKS=radar-all-pieces)"]


def test_a_slot_that_does_not_match_is_drawn_as_it_is_and_said_once(exe):
    out, err = run(exe, LATER, "8", "0", "50", LATER, "12", "5", "10", *later(2, "50"), **ON)
    assert out == ["0x08 0 4", "0x0C 5 4", "0x0C 2 4"]
    assert err[1:] == [
        "sa2 tweaks: radar tracks all pieces: icon 0x08 with slot 0 on the stack is no piece's "
        "plain icon, so it is drawn as it is",
        "sa2 tweaks: radar tracks all pieces: slot 2 tracked (icon 0x0C for 0x09, distance 50.0)"]


def test_an_unreadable_stack_draws_the_plain_icon_and_says_so_once(exe):
    out, err = run(exe, "unreadable", "8", "1", "10", "unreadable", "8", "1", "50",
                   *later(1, "50"), **ON)
    assert out[:2] == ["0x08 -1 4", "0x08 -1 4"] and out[2:] == ["0x0B 1 4"]
    assert err[1:] == [
        "sa2 tweaks: radar tracks all pieces: the guest bytes at 0x00090000 do not read, so the "
        "game's radar is left as it is there",
        "sa2 tweaks: radar tracks all pieces: slot 1 tracked (icon 0x0B for 0x08, distance 50.0)"]


# The '!' (0x0073A450): one frame is the first piece's call, then the later pieces'.

@pytest.mark.parametrize("env", OFF_ENVS)
def test_off_draws_no_bang_for_a_later_piece(exe, env):
    out, err = run(exe, *tracked(0, "50"), *later(1, "20"), *later(2, "5"), **env)
    assert out == ["0x0D 0 4", "0x08 1 4", "0x09 2 4"]
    assert not any("radar" in line for line in err)


def test_on_a_later_piece_under_30_draws_the_bang_once_a_frame(exe):
    out, err = run(exe, *tracked(0, "50"), *later(1, "20"), *later(2, "25"),   # frame 1: slot 1's
                   *tracked(0, "50"), *later(1, "40"), *later(2, "25"),        # frame 2: slot 2's
                   *tracked(0, "50"), *later(1, "40"), *later(2, "35"), **ON)  # frame 3: none
    assert out == ["0x0D 0 4", "0x0B 1 4", BANG, "0x0C 2 4",
                   "0x0D 0 4", "0x0B 1 4", "0x0C 2 4", BANG,
                   "0x0D 0 4", "0x0B 1 4", "0x0C 2 4"]
    assert [line for line in err if "'!'" in line] == [
        "sa2 tweaks: radar tracks all pieces: the '!' shows for slot 1 (distance 20.0)",
        "sa2 tweaks: radar tracks all pieces: the '!' shows for slot 2 (distance 25.0)"]


def test_on_the_first_pieces_own_bang_is_not_drawn_twice(exe):
    """Under 30.0 the display draws the first piece's '!' itself (0x0073A82F)."""
    out, _ = run(exe, *tracked(0, "10"), *later(1, "20"), *later(2, "5"), **ON)
    assert out == ["0x0D 0 4", "0x0B 1 4", "0x0C 2 4"]


@pytest.mark.parametrize("d, drawn", [("30", False), ("29.999998", True), ("nan", False),
                                      ("-5", True)])
def test_the_bangs_bound_is_the_displays(exe, d, drawn):
    """`fcomp` with 30.0 and `jp`: under 30.0 draws; 30.0 itself and a NaN do not."""
    out, _ = run(exe, *tracked(0, "50"), *later(1, d), **ON)
    assert out[2:] == ([BANG] if drawn else [])


# The beep: the radar main's distance call at 0x00739D74 (return address 0x00739D79). The player
# is at the origin; slot 0 (the first piece) at 600, slot 1 at 50, slot 2 at 200.
FAR_NEAR_MID = "pieces=0,600,0,0;0,0,0,50;0,0,200,0"


@pytest.mark.parametrize("env", OFF_ENVS)
def test_off_the_beep_keeps_the_first_piece(exe, env):
    out, err = run(exe, FAR_NEAR_MID, "dist", BEEP, "0", **env)
    assert out == ["dist 0 4"]
    assert not any("radar" in line for line in err)


def test_on_the_beep_follows_the_nearest_piece_said_once(exe):
    out, err = run(exe, FAR_NEAR_MID, "dist", BEEP, "0", "dist", BEEP, "0",
                   "pieces=0,600,0,0;0,0,0,250;0,0,200,0", "dist", BEEP, "0", **ON)
    assert out == ["dist 1 4", "dist 1 4", "dist 2 4"]
    assert err == ["sa2 tweaks: radar tracks all pieces (SA2_TWEAKS=radar-all-pieces)",
                   "sa2 tweaks: radar tracks all pieces: the beep follows slot 1, nearer than slot 0",
                   "sa2 tweaks: radar tracks all pieces: the beep follows slot 2, nearer than slot 0"]


@pytest.mark.parametrize("pieces, first, near", [
    ("pieces=0,50,0,0;0,0,0,200;0,0,600,0", 0, 0),        # the first piece is the nearest
    ("pieces=0,50,0,0;0,0,50,0;0,0,0,50", 0, 0),          # a tie keeps the first piece
    ("pieces=0,600,0,0;0,nan,0,0;0,0,0,700", 0, 0),       # a NaN is never the nearest
    ("pieces=0xFE,0,0,1;0,0,300,0;0,40,0,0", 1, 2),       # slot 0 found: slot 1 is the first
    ("pieces=0,600,0,0;0xFE,0,0,1;0,0,400,0", 0, 2),      # a found piece is never the nearest
    ("pieces=0xFE,0,0,1;0xFE,0,0,1;0,0,400,0", 2, 2),     # the last piece alone
    ("pieces=0,-30,40,0;0,0,49.9,0;0,0,0,50.1", 0, 1),    # 50 against 49.9
])
def test_on_the_nearest_piece_still_to_find(exe, pieces, first, near):
    out, err = run(exe, pieces, "player=0,0,0", "dist", BEEP, str(first), **ON)
    assert out == [f"dist {near} 4"]
    assert len(err) == (2 if near != first else 1)


def test_on_the_nearest_is_measured_from_the_player(exe):
    out, _ = run(exe, FAR_NEAR_MID, "player=600,10,0", "dist", BEEP, "0", **ON)
    assert out == ["dist 0 4"]


@pytest.mark.parametrize("ret", ["0x0073A7B7", "0x00739AFA", "0x00739B55", "0x005FB508"])
def test_other_distance_calls_are_left_alone(exe, ret):
    """The display's own distance call, the two-player beep's two and any other caller."""
    out, err = run(exe, FAR_NEAR_MID, "dist", ret, "0", **ON)
    assert out == ["dist 0 4"]
    assert err == ["sa2 tweaks: radar tracks all pieces (SA2_TWEAKS=radar-all-pieces)"]


def test_with_hide_radar_the_beep_keeps_the_first_piece(exe):
    out, err = run(exe, FAR_NEAR_MID, "dist", BEEP, "0",
                   SA2_TWEAKS="radar-all-pieces,hide-radar")
    assert out == ["dist 0 4"]
    assert not any("beep follows" in line for line in err)


def test_a_distance_call_that_is_not_the_first_pieces_is_left_and_said_once(exe):
    out, err = run(exe, FAR_NEAR_MID, "dist", BEEP, "2", "dist", BEEP, "1",
                   "pieces=0xFE,0,0,1;0xFE,0,0,1;0xFE,0,0,1", "dist", BEEP, "0", **ON)
    assert out == ["dist 2 4", "dist 1 4", "dist 0 4"]
    assert err[1:] == ["sa2 tweaks: radar tracks all pieces: the beep's distance call has EAX "
                       "0x00030050, not the first piece's position, so the beep keeps it"]


def test_an_unreadable_radar_leaves_the_beep_and_says_so_once(exe):
    out, err = run(exe, FAR_NEAR_MID, "data=unreadable", "dist", BEEP, "0", "dist", BEEP, "0", **ON)
    assert out == ["dist 0 4", "dist 0 4"]
    assert err[1:] == ["sa2 tweaks: radar tracks all pieces: the guest bytes at 0x01AF014C do not "
                       "read, so the game's radar is left as it is there"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, *tracked(0, "50"), *later(1, "20"), FAR_NEAR_MID, "dist", BEEP, "0",
                   **ON, **{replay: "/dev/null"})
    assert out == [*PLAIN, "0x0D 0 4", "0x08 1 4", "dist 0 4"]
    assert err == ["sa2 tweaks: SA2_TWEAKS=radar-all-pieces ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)"]
