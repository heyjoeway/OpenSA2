"""rail-forgiving (runtime/tweaks/rail-forgiving.c): after the grind balance, 0x00725F30, the
balance counter (s16 at SonicCharObj2+0x360) is held at 0x78 when the body raised it past that in
an off-balance animation (0x70 or 0x71, the next animation at CharObj2Base+0x176 or the current one
at +0x178), so the next frame's fall test (0x0071A704, `cmp word [esi+360h],78h; jle`) keeps the
character on the rail.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted body and the
runtime's peek, poke and register accessors. Guest memory is the stack, where Sonic_Main's call at
0x00718359 leaves the CharObj2Base at [ESP+0x0C] and the SonicCharObj2 at [ESP+0x10], and the
character's work (one pointer for both, as in the game, or two when a test asks). Each frame the
driver first runs the fall test as the action checks do (above 0x78: the character falls, action
10, and the run stops), then the wrap, whose stand-in body sets the two animations and adds to or
zeroes the counter as the frame's script says. It prints "<counter> <next> <cur>" after each frame
and "fell <frame>" when the character falls, then "pokes <n>".
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_tweaks as TT  # noqa: E402

RUNTIME = TT.RUNTIME

# argv[1]: flags ("-" for none), any of 2 (the CharObj2Base and the SonicCharObj2 are two
# pointers), s (the stack does not read), r (the work does not read after the body), w (the counter
# does not write), t (the second half of the frames runs a second character: another work); s, r
# and w fail the tweak's sa2_peek and sa2_poke alone, since the lifted body reads guest memory
# directly. argv[2..]: one frame each,
# "<next>,<cur>,<op>", the animations in hex and op "+<n>" (add n) or "z" (zero the counter).
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK  0x00020000u
#define WORK   0x01DE9600u
#define WORK2  0x01DE9A00u   /* the SonicCharObj2 when it is apart from the CharObj2Base (flag 2) */
#define WORK3  0x01DEA000u   /* a second character's work (flag t) */
#define SPAN   0x400u
static uint8_t stack[0x40], work[SPAN], work2[SPAN], work3[SPAN];
static uint32_t gpr[8];
static int unreadable_stack, unreadable_work, locked, after_body, pokes;
static int next_anim, cur_anim, op_zero, op_add;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    if (a >= WORK && a + n <= WORK + SPAN) return work + (a - WORK);
    if (a >= WORK2 && a + n <= WORK2 + SPAN) return work2 + (a - WORK2);
    if (a >= WORK3 && a + n <= WORK3 + SPAN) return work3 + (a - WORK3);
    return NULL;
}
/* The flags fail the tweak's own reads and writes alone; the stand-in body and the driver read
 * guest memory directly, as the lifted code does. */
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p || (unreadable_stack && a >= STACK && a < STACK + sizeof stack) ||
        (unreadable_work && after_body && !(a >= STACK && a < STACK + sizeof stack)))
        return SA2_E_RANGE;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p || locked) return SA2_E_RANGE;
    memcpy(p, in, n);
    pokes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static uint8_t *mem(uint32_t a) { return at(a, 2); }
static int16_t counter_of(uint32_t s2) { int16_t c; memcpy(&c, mem(s2 + 0x360), 2); return c; }

/* The grind balance's part that matters (0x00726B92-0x00726C0C): the animations and the counter,
 * read from the stack as the body does (ebp = [ESP+0x0C], edx = [ESP+0x10] of the entry). */
void *__real_sub_725f30(void *state, uint32_t pc, void *memory) {
    (void)state;
    if (pc != 0x00725F30u) return NULL;
    uint32_t co2, s2;
    memcpy(&co2, stack + 0x0C, 4);
    memcpy(&s2, stack + 0x10, 4);
    const uint16_t nx = (uint16_t)next_anim, cu = (uint16_t)cur_anim;
    memcpy(mem(co2 + 0x176), &nx, 2);
    memcpy(mem(co2 + 0x178), &cu, 2);
    int16_t c = counter_of(s2);
    c = op_zero ? 0 : (int16_t)(c + op_add);
    memcpy(mem(s2 + 0x360), &c, 2);
    after_body = 1;
    return memory;
}
void *__wrap_sub_725f30(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    const char *flags = argv[1];
    const int apart = strchr(flags, '2') != NULL;
    unreadable_stack = strchr(flags, 's') != NULL;
    unreadable_work = strchr(flags, 'r') != NULL;
    locked = strchr(flags, 'w') != NULL;
    const int second = strchr(flags, 't') != NULL;
    const int frames = argc - 2;
    for (int f = 0; f < frames; f++) {
        const uint32_t co2 = second && f >= frames / 2 ? WORK3 : WORK;
        const uint32_t s2 = apart ? WORK2 : co2;
        /* the action checks' fall test, earlier in the frame (0x0071A704) */
        if (counter_of(s2) > 0x78) { printf("fell %d\n", f); break; }
        const char *fr = argv[2 + f];
        char *e;
        next_anim = (int)strtol(fr, &e, 16);
        cur_anim = (int)strtol(e + 1, &e, 16);
        op_zero = e[1] == 'z';
        op_add = op_zero ? 0 : atoi(e + 2);
        memcpy(stack + 0x0C, &co2, 4);
        memcpy(stack + 0x10, &s2, 4);
        gpr[SA2_ESP] = STACK;
        after_body = 0;
        int token;
        if (__wrap_sub_725f30(&token, 0x00725F30u, &token) != &token) return 3;
        uint16_t nx, cu;
        memcpy(&nx, mem(co2 + 0x176), 2);
        memcpy(&cu, mem(co2 + 0x178), 2);
        printf("%d %x %x\n", counter_of(s2), nx, cu);
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""

CLEAN = {k: v for k, v in os.environ.items() if k not in TT.VARS}
ON = "sa2 tweaks: rails forgiving (SA2_TWEAKS=rail-forgiving)"


def held(counter: int, anim: int, work: int = 0x01DE9600) -> str:
    return (f"sa2 tweaks: rails forgiving: held on the rail (balance 0x{counter:02X} -> 0x78, "
            f"animation 0x{anim:02X}, work 0x{work:08X})")


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not TT.ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {TT.ZIG}")
    d = tmp_path_factory.mktemp("rail_forgiving")
    (d / "driver.c").write_text(DRIVER)
    out = d / "rf"
    done = subprocess.run([str(TT.ZIG), "cc", *TT.CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "rail-forgiving.c"), "-o", str(out)],
                          capture_output=True, text=True, env=TT.zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, flags: str, frames: list[str], **env: str) -> tuple[list[str], int, list[str]]:
    p = subprocess.run([str(exe), flags or "-", *frames], capture_output=True, text=True, timeout=30,
                       env=dict(CLEAN, **env))
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert lines[-1].startswith("pokes ")
    return lines[:-1], int(lines[-1].split()[1]), p.stderr.splitlines()


def counters(rows: list[str]) -> list[int]:
    return [int(r.split()[0]) for r in rows if not r.startswith("fell")]


def fell(rows: list[str]) -> int | None:
    return next((int(r.split()[1]) for r in rows if r.startswith("fell")), None)


# A grind that loses its balance: steady (animation 0x6A, the counter zeroed), then 130 frames off
# balance at +1 a frame (0x70; the body adds si, set to 1 at 0x007266CD), then the character
# recovers. Off, the counter passes 0x78 on the 121st off-balance frame and the next frame's test
# throws the character off, as a stage run of Sky Rail shows [private].
STEADY = ["6a,6a,z"] * 5
WOBBLE = ["70,6a,+1"] + ["70,70,+1"] * 129
RIGHT = ["6a,70,z"] + ["6a,6a,z"] * 4
CURVE = STEADY + WOBBLE + RIGHT


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_character_falls_and_nothing_is_written(exe, env):
    rows, pokes, err = run(exe, "", CURVE, **env)
    assert pokes == 0
    assert fell(rows) == 5 + 121
    assert counters(rows)[-1] == 121
    assert err == ([] if not env.get("SA2_TWEAKS") else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


def test_on_the_character_stays_on_through_the_wobble(exe):
    rows, pokes, err = run(exe, "", CURVE, SA2_TWEAKS="rail-forgiving")
    assert fell(rows) is None and len(rows) == len(CURVE)
    c = counters(rows)
    assert c[:5] == [0] * 5
    assert c[5:125] == list(range(1, 121))            # the game's own counts up to 0x78 (120)
    assert c[125:135] == [120] * 10                    # held at 0x78 while off balance
    assert c[135:] == [0] * 5                          # the body zeroes it when the character recovers
    assert pokes == 10                                 # one write a frame past 0x78, no other
    assert err == [ON, held(0x79, 0x70)]


def test_the_counter_at_0x78_is_left_and_one_past_it_is_held(exe):
    rows, pokes, _ = run(exe, "", ["70,70,+120", "70,70,+0", "70,70,+1", "70,70,+0"],
                         SA2_TWEAKS="rail-forgiving")
    assert counters(rows) == [120, 120, 120, 120] and pokes == 1


@pytest.mark.parametrize("frame, said", [("71,6a,+130", 0x71),     # just set by the body
                                         ("6a,71,+130", 0x71),     # 0x00726C89: current alone
                                         ("70,71,+130", 0x70)])
def test_either_off_balance_animation_holds_it(exe, frame, said):
    rows, pokes, err = run(exe, "", [frame, "6a,6a,z"], SA2_TWEAKS="rail-forgiving")
    assert fell(rows) is None and counters(rows) == [120, 0] and pokes == 1
    assert err == [ON, held(130, said)]


# The image's two ways into the wobble (0x00725F30): from 0x6A, 0x6B, 0x6E and 0x6F once the pose
# has played (0x00726AAE-0x00726B0E, no speed test), and from 0x68, 0x69, 0x6C and 0x6D when the
# speed is also low (0x00726B2B-0x00726B81). Both write the next animation, 0x70 or 0x71, before
# the add (0x00726B97, 0x00726BBA), so the wrap holds the character from every grind pose.
@pytest.mark.parametrize("pose, nxt", [(0x6A, 0x70), (0x6B, 0x71), (0x6E, 0x70), (0x6F, 0x71),
                                       (0x68, 0x70), (0x69, 0x71), (0x6C, 0x70), (0x6D, 0x71)])
def test_the_wobble_from_every_grind_pose_holds_it(exe, pose, nxt):
    frames = ([f"{pose:x},{pose:x},z"] * 3 + [f"{nxt:x},{pose:x},+1"] + [f"{nxt:x},{nxt:x},+1"] * 129
              + [f"{pose:x},{nxt:x},z"])
    rows, pokes, err = run(exe, "", frames, SA2_TWEAKS="rail-forgiving")
    assert fell(rows) is None and len(rows) == len(frames)
    c = counters(rows)
    assert c[3:123] == list(range(1, 121)) and c[123:133] == [120] * 10 and c[133] == 0
    assert pokes == 10
    assert err == [ON, held(0x79, nxt)]
    off_rows, off_pokes, _ = run(exe, "", frames)
    assert fell(off_rows) == 3 + 121 and off_pokes == 0


@pytest.mark.parametrize("frame", ["6a,6a,+130", "0f,10,+130", "72,6f,+130"])
def test_a_counter_raised_outside_the_off_balance_animations_is_left_and_falls(exe, frame):
    rows, pokes, err = run(exe, "", [frame, "6a,6a,+0"], SA2_TWEAKS="rail-forgiving")
    assert counters(rows) == [130] and fell(rows) == 1 and pokes == 0
    assert err == [ON]


def test_the_counter_is_the_sonic_work_and_the_animations_the_base(exe):
    """[ESP+0x10] names the counter's work and [ESP+0x0C] the animations', as the body reads them."""
    rows, pokes, err = run(exe, "2", WOBBLE + RIGHT, SA2_TWEAKS="rail-forgiving")
    assert fell(rows) is None and max(counters(rows)) == 120 and pokes == 10
    assert err == [ON, held(0x79, 0x70, 0x01DE9A00)]


def test_each_wobble_is_named_once_and_after_16_the_run_says_it_stops_naming(exe):
    once = ["70,70,+125", "70,70,+5", "6a,6a,z"]
    rows, pokes, err = run(exe, "", once * 18, SA2_TWEAKS="rail-forgiving")
    assert fell(rows) is None and pokes == 36
    assert err == [ON] + [held(125, 0x70)] * 16 + [
        "sa2 tweaks: rails forgiving: 16 holds named; later ones are not"]


def test_two_characters_are_named_apart(exe):
    """A 2P battle: the second character's first hold is named though the first is held."""
    frames = ["70,70,+125"] * 4
    rows, pokes, err = run(exe, "t", frames, SA2_TWEAKS="rail-forgiving")
    assert fell(rows) is None and pokes == 4
    assert err == [ON, held(125, 0x70), held(125, 0x70, 0x01DEA000)]


@pytest.mark.parametrize("flags, said", [
    ("s", "sa2 tweaks: rails forgiving: the guest stack at 0x00020000 does not read, so the "
          "rail's balance check is left alone"),
    ("r", "sa2 tweaks: rails forgiving: the character's work at 0x01DE9600 does not read, so the "
          "rail's balance check is left alone"),
    ("w", "sa2 tweaks: rails forgiving: the balance counter at 0x01DE9960 does not write, so the "
          "character falls off as usual"),
])
def test_a_read_or_write_that_fails_is_named_once_and_the_fall_happens(exe, flags, said):
    rows, pokes, err = run(exe, flags, WOBBLE, SA2_TWEAKS="rail-forgiving")
    assert pokes == 0 and fell(rows) == 121
    assert counters(rows) == list(range(1, 122))       # the body ran every frame, as in the game
    assert err == [ON, said]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    rows, pokes, err = run(exe, "", CURVE, SA2_TWEAKS="rail-forgiving", **{replay: "/dev/null"})
    assert pokes == 0 and fell(rows) == 126
    assert err == ["sa2 tweaks: SA2_TWEAKS=rail-forgiving ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]
