"""pause-no-confirm (runtime/tweaks/pause-no-confirm.c): the pause menu's Restart and Exit boxes
answer Yes on their first frame, in fast-stage-load.c's wrap of GameStateHandler (0x0043A780).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c and fast-stage-load.c,
whose wrap it acts in, as test_tweaks.py builds no-event-bars, and linked with a driver that stands
in for the lifted GameStateHandler and the runtime's guest memory. Guest memory holds GameState
(the int16 at 0x01934BE0), CurrentLevel (0x01934B70), the Restart box, the Exit box and the answer
(the dwords at 0x01A558B8, 0x01A558BC and 0x01A558C0), ControllerPointers[0] (0x01DEFB60) and the
PDS_PERIPHERAL it points to, whose press word is at +0x10.

The stand-in `__real_sub_43a780` is a small model of the arms the tweak's header reads:

  state 17  PauseMenu_Main first: with a box open, MenuButtons_Pressed Left (0x40) stores answer 1
            and Right (0x80) answer 0. Then the Exit box (tested first), else the Restart box: A or
            Start (press & 0x0C) closes it and, with answer 1, stores GameState 0x15 (Exit) or 0x0D
            (Restart); B (0x02) closes it. With no box open, A or Start selects: selection 0
            unpauses (16), 1 of 6 opens the Restart box, 4 of 5 or 5 of 6 the Exit box, each with
            answer 0.
  state 0   the load finishes: GameState 1 (fast-stage-load makes it 2).

Each argument is one frame, comma-separated key=value pairs: gs (GameState on entry, kept from the
last frame when left out), press (player 1's press word, 0 when left out), menu
(MenuButtons_Pressed), sel and count (PauseSelection and PauseOptionCount, kept), bc and b8 (set a
box before the frame), nopad (ControllerPointers[0] is 0 for the frame) and ro (the answer word
does not write). A frame prints "gs=<GameState> b8=<Restart box> bc=<Exit box> ans=<answer>
seen=<the press word the body saw> press=<the press word after the call>".
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env

ON = {"SA2_TWEAKS": "pause-no-confirm"}
REPORT = "sa2 tweaks: pause without confirm (SA2_TWEAKS=pause-no-confirm)"
SAID = "sa2 tweaks: pause without confirm: "
A, B, START, LEFT, RIGHT = 0x04, 0x02, 0x08, 0x40, 0x80
PAUSED, INGAME, EXITED, RESTARTED = 17, 16, 0x15, 0x0D

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define GAME_STATE    0x01934BE0u
#define CURRENT_LEVEL 0x01934B70u
#define BOXES         0x01A558B8u   /* Restart, Exit, answer */
#define PADS          0x01DEFB60u
#define PAD           0x01DEF000u   /* where ControllerPointers[0] points */
static uint16_t game_state, current_level = 13;
static uint32_t box[3];             /* [0] Restart, [1] Exit, [2] answer */
static uint32_t pad_ptr = PAD, pad[8];
static int ro;                      /* the answer word does not write */
static uint32_t menu, seen;
static int sel = 0, count = 6;

static void *at(uint32_t a, uint32_t n) {
    if (a == GAME_STATE && n == 2) return &game_state;
    if (a == CURRENT_LEVEL && n == 2) return &current_level;
    if (n == 4 && a >= BOXES && a < BOXES + 12 && (a & 3) == 0) return &box[(a - BOXES) / 4];
    if (n == 4 && a == PADS) return &pad_ptr;
    if (n == 4 && a >= PAD && a < PAD + sizeof pad && (a & 3) == 0) return &pad[(a - PAD) / 4];
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *w = at(a, n);
    if (!w) return 1;
    memcpy(out, w, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *w = at(a, n);
    if (!w || (ro && a == BOXES + 8)) return 1;
    memcpy(w, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__real_sub_43a780(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t press = pad[4];
    seen = press;
    if (game_state == 0) { game_state = 1; return memory; }
    if (game_state != 17) return memory;
    if (box[1] == 1 || box[0] == 1) {              /* PauseMenu_Main, 0x0043D400 on */
        if (menu & 0x40) box[2] = 1;
        else if (menu & 0x80) box[2] = 0;
    }
    if (box[1] == 1 || box[0] == 1) {              /* 0x0043B2D3, 0x0043B417 */
        uint32_t *open = box[1] == 1 ? &box[1] : &box[0];
        if (press & 0x0C) {
            *open = 0;
            if (box[2] == 1) game_state = open == &box[1] ? 0x15 : 0x0D;
        } else if (press & 0x02) {
            *open = 0;
        }
        return memory;
    }
    if (press & 0x0C) {                            /* 0x0043B0A8 */
        if (sel == 0) game_state = 16;
        else if (sel == 1 && count == 6) { box[0] = 1; box[2] = 0; }
        else if ((sel == 4 && count == 5) || (sel == 5 && count == 6)) { box[1] = 1; box[2] = 0; }
    }
    return memory;
}
void *__wrap_sub_43a780(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char buf[256];
        snprintf(buf, sizeof buf, "%s", argv[i]);
        pad[4] = 0; menu = 0; ro = 0; pad_ptr = PAD;
        for (char *kv = strtok(buf, ","); kv; kv = strtok(NULL, ",")) {
            char *eq = strchr(kv, '=');
            const unsigned long v = eq ? strtoul(eq + 1, NULL, 0) : 1;
            if (eq) *eq = 0;
            if (!strcmp(kv, "gs")) game_state = (uint16_t)v;
            else if (!strcmp(kv, "press")) pad[4] = (uint32_t)v;
            else if (!strcmp(kv, "menu")) menu = (uint32_t)v;
            else if (!strcmp(kv, "sel")) sel = (int)v;
            else if (!strcmp(kv, "count")) count = (int)v;
            else if (!strcmp(kv, "bc")) box[1] = (uint32_t)v;
            else if (!strcmp(kv, "b8")) box[0] = (uint32_t)v;
            else if (!strcmp(kv, "nopad")) pad_ptr = 0;
            else if (!strcmp(kv, "ro")) ro = 1;
            else if (strcmp(kv, "-")) return 2;
        }
        int token;
        if (__wrap_sub_43a780(&token, 0x0043A780u, &token) != &token) return 3;
        printf("gs=%u b8=%u bc=%u ans=%u seen=0x%x press=0x%x\n", game_state, box[0], box[1], box[2],
               seen, pad[4]);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("pause_no_confirm")
    (d / "driver.c").write_text(DRIVER)
    out = d / "pnc"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "fast-stage-load.c"),
                           str(RUNTIME / "tweaks" / "pause-no-confirm.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *frames: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *frames], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def row(gs, b8=0, bc=0, ans=0, seen=0, press=0) -> str:
    return f"gs={gs} b8={b8} bc={bc} ans={ans} seen={seen:#x} press={press:#x}"


# Exit picked (selection 5 of 6), then two frames with nothing pressed.
EXIT = [f"gs={PAUSED},sel=5,count=6,press={A}", "-", "-"]
EXIT_LIFTED = [row(PAUSED, bc=1, seen=A, press=A), row(PAUSED, bc=1), row(PAUSED, bc=1)]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "fast-stage-load"}])
def test_off_the_box_waits_for_the_player(exe, env):
    out, err = run(exe, *EXIT, **env)
    assert out == EXIT_LIFTED
    assert err == ([] if env.get("SA2_TWEAKS", "") == "" else
                   ["sa2 tweaks: fast stage load (SA2_TWEAKS=fast-stage-load)"])


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *EXIT, **ON, **{replay: "/dev/null"})
    assert out == EXIT_LIFTED
    assert err == ["sa2 tweaks: SA2_TWEAKS=pause-no-confirm ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_exit_answers_yes_on_the_next_frame_and_the_press_word_is_put_back(exe):
    """The frame after the box opens, the body sees A and answer 1 and takes the Exit Yes path;
    after the call player 1's press word is the player's own again (0)."""
    out, err = run(exe, *EXIT, **ON)
    assert out == [row(PAUSED, bc=1, seen=A, press=A), row(EXITED, ans=1, seen=A, press=0),
                   row(EXITED, ans=1)]
    assert err == [REPORT, SAID + "the Exit box answered Yes (GameState 17 -> 21, answer 1)"]


@pytest.mark.parametrize("sel, count, box, after, said", [
    (1, 6, "b8", RESTARTED, "the Restart box answered Yes (GameState 17 -> 13, answer 1)"),
    (4, 5, "bc", EXITED, "the Exit box answered Yes (GameState 17 -> 21, answer 1)"),
])
def test_restart_and_the_five_item_menus_exit(exe, sel, count, box, after, said):
    out, err = run(exe, f"gs={PAUSED},sel={sel},count={count},press={START}", "-", **ON)
    opened = row(PAUSED, **{box: 1}, seen=START, press=START)
    assert out == [opened, row(after, ans=1, seen=A, press=0)]
    assert err == [REPORT, SAID + said]


def test_the_players_own_buttons_are_kept_beside_the_a(exe):
    out, _ = run(exe, *EXIT[:1], "press=0x300", **ON)
    assert out[1] == row(EXITED, ans=1, seen=0x304, press=0x300)


def test_right_on_the_boxs_frame_answers_no(exe):
    out, err = run(exe, *EXIT[:1], f"menu={RIGHT}", "-", **ON)
    assert out[1:] == [row(PAUSED, seen=A, press=0), row(PAUSED)]
    assert err == [REPORT, SAID + "the Exit box was answered No on its frame (Right was pressed), so "
                   "the game stays paused"]


def test_no_cancel_outside_the_boxs_one_frame(exe):
    """The player-facing text promises no cancel: Right pressed with the opening A comes before the
    box exists, B on the box's frame loses to the added A, and Right a frame late finds the Yes path
    already taken."""
    out, _ = run(exe, f"gs={PAUSED},sel=5,count=6,press={A},menu={RIGHT}", "-", **ON)
    assert out == [row(PAUSED, bc=1, seen=A, press=A), row(EXITED, ans=1, seen=A, press=0)]
    out, _ = run(exe, *EXIT[:1], f"press={B}", **ON)
    assert out[1] == row(EXITED, ans=1, seen=A | B, press=B)
    out, _ = run(exe, *EXIT[:2], f"menu={RIGHT}", **ON)
    assert out[1:] == [row(EXITED, ans=1, seen=A, press=0), row(EXITED, ans=1)]


def test_only_a_box_that_opened_on_the_last_frame_is_answered(exe):
    """A box already open when the tweak first sees it, or one left a frame outside the pause, is
    the player's."""
    out, err = run(exe, f"gs={PAUSED},bc=1", "-", **ON)
    assert out == [row(PAUSED, bc=1), row(PAUSED, bc=1)]
    out, err = run(exe, *EXIT[:1], f"gs={INGAME}", f"gs={PAUSED}", "-", **ON)
    assert out[1:] == [row(INGAME, bc=1), row(PAUSED, bc=1), row(PAUSED, bc=1)]
    assert err == [REPORT]


def test_other_pause_frames_are_the_lifted_body(exe):
    """Unpause, B on an open box and a selection that opens nothing are the body's own."""
    frames = [f"gs={PAUSED},sel=0,press={A}", f"gs={PAUSED},sel=2,count=6,press={A}",
              f"gs={PAUSED},sel=1,count=5,press={B}"]
    off, _ = run(exe, *frames)
    on, err = run(exe, *frames, **ON)
    assert on == off == [row(INGAME, seen=A, press=A), row(PAUSED, seen=A, press=A),
                         row(PAUSED, seen=B, press=B)]
    assert err == [REPORT]


def test_a_controller_or_answer_that_fails_leaves_the_box_and_says_so_once(exe):
    again = f"gs={PAUSED},bc=0,press={A}"
    out, err = run(exe, *EXIT[:1], "nopad", again, "nopad", again, "ro", **ON)
    assert out == [row(PAUSED, bc=1, seen=A, press=A), row(PAUSED, bc=1),
                   row(PAUSED, bc=1, seen=A, press=A), row(PAUSED, bc=1),
                   row(PAUSED, bc=1, seen=A, press=A), row(PAUSED, bc=1)]
    assert err == [REPORT,
                   SAID + "player 1's controller (0x01DEFB60 -> 0x00000000) does not read, so the "
                   "Exit box is left to the player",
                   SAID + "the answer at 0x01A558C0 or the press word at 0x01DEF010 does not write, "
                   "so the Exit box is left to the player"]


def test_answers_are_said_for_the_first_16(exe):
    out, err = run(exe, *[f for _ in range(17) for f in EXIT[:2]], **ON)
    assert out[1::2] == [row(EXITED, ans=1, seen=A, press=0)] * 17
    said = err[1:]
    assert len(said) == 16
    assert said[-1] == SAID + ("the Exit box answered Yes (GameState 17 -> 21, answer 1); later "
                               "answers are not said")


def test_with_fast_stage_load_on_too_each_acts_on_its_own_frames(exe):
    """A load (0 -> 1, made 2 by fast-stage-load) and an Exit through the one wrap."""
    tweaks = {"SA2_TWEAKS": "fast-stage-load,pause-no-confirm"}
    out, err = run(exe, "gs=0", *EXIT[:2], "gs=0", **tweaks)
    assert out == [row(2), row(PAUSED, bc=1, seen=A, press=A), row(EXITED, ans=1, seen=A, press=0),
                   row(2, ans=1)]
    assert err == ["sa2 tweaks: fast stage load, pause without confirm "
                   "(SA2_TWEAKS=fast-stage-load,pause-no-confirm)",
                   "sa2 tweaks: fast stage load: level 13's Loading state skipped (load 1)",
                   SAID + "the Exit box answered Yes (GameState 17 -> 21, answer 1)",
                   "sa2 tweaks: fast stage load: level 13's Loading state skipped (load 2)"]
    off, _ = run(exe, "gs=0", *EXIT[:2], "gs=0", SA2_TWEAKS="fast-stage-load")
    assert off == [row(2), row(PAUSED, bc=1, seen=A, press=A), row(PAUSED, bc=1), row(2, bc=1)]
