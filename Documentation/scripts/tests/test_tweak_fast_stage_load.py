"""The fast-stage-load tweak (runtime/tweaks/fast-stage-load.c) on GameStateHandler (0x0043A780).

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and with runtime/tweaks/pause-no-confirm.c, which acts in its wrap, and
linked with a driver that stands in for the lifted GameStateHandler and the runtime's guest
memory. Guest memory holds two words, GameState (0x01934BE0) and CurrentLevel (0x01934B70), and
pause-no-confirm's three box dwords (0x01A558B8, 0x01A558BC, 0x01A558C0), always 0 here;
`sa2_peek` and `sa2_poke` read and write them and refuse any other address, and a frame can make
GameState refuse. test_tweak_pause_no_confirm.py holds the two tweaks on together through a pause.

Two drivers' modes:

  frames  each argument "<before>:<level>:<left>" is one call: GameState is <before> and
          CurrentLevel <level> on entry, and the stand-in body leaves GameState <left> (the store
          the lifted arm would make). It prints GameState after the wrap returns. <before> "r"
          makes GameState unreadable for the frame, "w" unwritable.
  load    the stand-in body is a small model of the arms the header reads: state 0 loads for
          LOAD frames and then stores 1; state 1 counts frames and stores 2 when its counter
          reaches WAIT; states 2 to 15 step one a frame, up to 16 (Ingame). It prints the states
          frame by frame until Ingame.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env  # noqa: E402

TWEAK = RUNTIME / "tweaks" / "fast-stage-load.c"
ON = {"SA2_TWEAKS": "fast-stage-load"}
REPORT = "sa2 tweaks: fast stage load (SA2_TWEAKS=fast-stage-load)"
INACTIVE, LOADING, LOAD_ITEMS, LOAD_LEVEL, INGAME, LEAVE = 0, 1, 2, 3, 16, 0x21
PUMPKIN_HILL, METAL_HARBOR, CHAO_WORLD = 5, 10, 90
LOAD, WAIT = 4, 6

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define GAME_STATE    0x01934BE0u
#define CURRENT_LEVEL 0x01934B70u
static uint16_t game_state, current_level;
static int no_read, no_write;           /* this frame, GameState refuses a read or a write */
static uint16_t left;                   /* frames mode: what the stand-in body stores */
static int model, load_frames, wait_count;

static uint32_t boxes[3];              /* pause-no-confirm's box words, 0x01A558B8 on */

static void *word(uint32_t a, uint32_t n) {
    if (n == 4 && a >= 0x01A558B8u && a <= 0x01A558C0u && (a & 3) == 0)
        return &boxes[(a - 0x01A558B8u) / 4];
    if (n != 2) return NULL;
    if (a == GAME_STATE) return &game_state;
    if (a == CURRENT_LEVEL) return &current_level;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *w = word(a, n);
    if (!w || (a == GAME_STATE && no_read)) return 1;
    memcpy(out, w, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *w = word(a, n);
    if (!w || (a == GAME_STATE && no_write)) return 1;
    memcpy(w, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__real_sub_43a780(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    if (!model) { game_state = left; return memory; }
    switch (game_state) {
    case 0: if (++load_frames >= LOAD) game_state = 1; break;      /* 0x004728B0, then 0x0043A88D */
    case 1: if (++wait_count >= WAIT) game_state = 2; break;       /* [0x01A558A4] == 6 */
    default: if (game_state < 16) game_state++; break;             /* LoadItems on to Ingame */
    }
    return memory;
}
void *__wrap_sub_43a780(void *state, uint32_t pc, void *memory);

static void call(void) {
    int token;
    if (__wrap_sub_43a780(&token, 0x0043A780u, &token) != &token) exit(3);
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "load") == 0) {
        model = 1;
        current_level = (uint16_t)strtoul(argv[2], NULL, 0);
        for (int f = 0; f < 100 && game_state != 16; f++) {
            call();
            printf("%u%c", game_state, game_state == 16 ? '\n' : ' ');
        }
        return 0;
    }
    for (int i = 1; i < argc; i++) {
        char before[8];
        unsigned level, l;
        if (sscanf(argv[i], "%7[^:]:%u:%u", before, &level, &l) != 3) return 2;
        no_read = strcmp(before, "r") == 0;
        no_write = strcmp(before, "w") == 0;
        game_state = (no_read || no_write) ? 0 : (uint16_t)strtoul(before, NULL, 0);
        current_level = (uint16_t)level;
        left = (uint16_t)l;
        call();
        no_read = no_write = 0;
        printf("%u\n", game_state);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("fast_stage_load")
    (d / "driver.c").write_text(f"#define LOAD {LOAD}\n#define WAIT {WAIT}\n" + DRIVER)
    out = d / "fsl"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(TWEAK), str(RUNTIME / "tweaks" / "pause-no-confirm.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *args: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def frame(before, level, left) -> str:
    return f"{before}:{level}:{left}"


# Every transition the state machine makes out of states 0 and 1, and a few others: the load
# finishing (0 -> 1), still loading (0 -> 0), the arm's other stores (0 -> 2, 0 -> 3), the leave
# the stage path (0 -> 0x21), Loading ending (1 -> 2), and states that are no load at all.
FRAMES = [frame(INACTIVE, PUMPKIN_HILL, INACTIVE), frame(INACTIVE, PUMPKIN_HILL, LOADING),
          frame(LOADING, PUMPKIN_HILL, LOADING), frame(LOADING, PUMPKIN_HILL, LOAD_ITEMS),
          frame(INACTIVE, METAL_HARBOR, LOAD_ITEMS), frame(INACTIVE, METAL_HARBOR, LOAD_LEVEL),
          frame(INACTIVE, METAL_HARBOR, LEAVE), frame(INGAME, METAL_HARBOR, INGAME),
          frame(17, METAL_HARBOR, LOADING), frame(INACTIVE, METAL_HARBOR, LOADING)]
LIFTED = ["0", "1", "1", "2", "2", "3", "33", "16", "1", "1"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_says_nothing(exe, env):
    out, err = run(exe, *FRAMES, **env)
    assert out == LIFTED
    assert err == ([] if env.get("SA2_TWEAKS", "") == "" else
                   ["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"])


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *FRAMES, **ON, **{replay: "/dev/null"})
    assert out == LIFTED
    assert err == ["sa2 tweaks: SA2_TWEAKS=fast-stage-load ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)"]


def test_on_turns_only_the_finished_load_into_load_items(exe):
    """0 -> 1 becomes 0 -> 2; every other transition is the body's own."""
    out, err = run(exe, *FRAMES, **ON)
    assert out == ["0", "2", "1", "2", "2", "3", "33", "16", "1", "2"]
    assert err == [REPORT,
                   "sa2 tweaks: fast stage load: level 5's Loading state skipped (load 1)",
                   "sa2 tweaks: fast stage load: level 10's Loading state skipped (load 2)"]


def test_with_pause_no_confirm_on_too_the_loads_are_the_same(exe):
    """pause-no-confirm acts in this wrap on frames that begin in the pause (17) with a box opened;
    no frame here does, so the frames and the skips are this tweak's alone."""
    out, err = run(exe, *FRAMES, SA2_TWEAKS="fast-stage-load,pause-no-confirm")
    assert out == ["0", "2", "1", "2", "2", "3", "33", "16", "1", "2"]
    assert err == ["sa2 tweaks: fast stage load, pause without confirm "
                   "(SA2_TWEAKS=fast-stage-load,pause-no-confirm)",
                   "sa2 tweaks: fast stage load: level 5's Loading state skipped (load 1)",
                   "sa2 tweaks: fast stage load: level 10's Loading state skipped (load 2)"]
    out, err = run(exe, *FRAMES, SA2_TWEAKS="pause-no-confirm")
    assert out == LIFTED
    assert err == ["sa2 tweaks: pause without confirm (SA2_TWEAKS=pause-no-confirm)"]


def test_chao_world_keeps_its_loading_state_and_says_so_once(exe):
    out, err = run(exe, frame(INACTIVE, CHAO_WORLD, LOADING), frame(INACTIVE, CHAO_WORLD, LOADING),
                   frame(INACTIVE, PUMPKIN_HILL, LOADING), **ON)
    assert out == ["1", "1", "2"]
    assert err == [REPORT, "sa2 tweaks: fast stage load: Chao World keeps its Loading state",
                   "sa2 tweaks: fast stage load: level 5's Loading state skipped (load 1)"]


def test_a_guest_word_that_fails_keeps_the_loading_state_and_says_so_once(exe):
    out, err = run(exe, frame("r", PUMPKIN_HILL, LOADING), frame("r", PUMPKIN_HILL, LOADING),
                   frame("w", PUMPKIN_HILL, LOADING), frame("w", PUMPKIN_HILL, LOADING),
                   frame(INACTIVE, PUMPKIN_HILL, LOADING), **ON)
    assert out == ["1", "1", "1", "1", "2"]
    assert err == [REPORT,
                   "sa2 tweaks: fast stage load: GameState at 0x01934BE0 does not read, so the "
                   "Loading state is kept",
                   "sa2 tweaks: fast stage load: GameState at 0x01934BE0 does not write, so the "
                   "Loading state is kept",
                   "sa2 tweaks: fast stage load: level 5's Loading state skipped (load 1)"]


def test_skips_are_said_for_the_first_32_loads(exe):
    out, err = run(exe, *[frame(INACTIVE, PUMPKIN_HILL, LOADING)] * 34, **ON)
    assert out == ["2"] * 34
    said = err[1:]
    assert len(said) == 32
    assert said[0] == "sa2 tweaks: fast stage load: level 5's Loading state skipped (load 1)"
    assert said[-1] == ("sa2 tweaks: fast stage load: level 5's Loading state skipped (load 32); "
                        "later skips are not said")


def test_a_whole_load_reaches_ingame_sooner_and_never_shows_loading(exe):
    """The model of the arms: LOAD frames in state 0, WAIT frames in state 1, then one frame for
    each of states 2 to 15. Off: 4 + 6 + 14 frames to Ingame; on: the 6 Loading frames are gone."""
    off, _ = run(exe, "load", str(PUMPKIN_HILL))
    on, err = run(exe, "load", str(PUMPKIN_HILL), **ON)
    off, on = off[0].split(), on[0].split()
    assert off == ["0"] * 3 + ["1"] * 6 + [str(s) for s in range(2, 17)]
    assert on == ["0"] * 3 + [str(s) for s in range(2, 17)]
    assert len(off) - len(on) == WAIT and "1" not in on
    assert err == [REPORT, "sa2 tweaks: fast stage load: level 5's Loading state skipped (load 1)"]


def test_the_player_readme_lists_it_before_no_event_bars():
    """tools/s7/release_build.py writes one share/game.env line per tweak in the table's name
    order, so fast-stage-load's line, with its summary, comes before no-event-bars'."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_build_fast_stage_load",
                                                  ROOT / "tools" / "s7" / "release_build.py")
    rb = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rb
    spec.loader.exec_module(rb)
    readme = rb.player_readme("x86_64-linux", rb.game_f80_mode({"game": {"sha256": "b"}}))
    lines = readme.split("\n")
    first = lines.index("  share/game.env:") + 1
    names = [l.split()[0] for l in lines[first:] if l.startswith("    SA2_TWEAKS=")]
    at = next(i for i in range(first, len(lines)) if lines[i].startswith("    SA2_TWEAKS=fast-stage-load "))
    assert lines[at].split() == ["SA2_TWEAKS=fast-stage-load", "a", "stage", "starts", "as",
                                 "soon", "as", "its", "files", "are", "loaded,", "without",
                                 "the", "loading", "screen's", "wait"]
    assert names.index("SA2_TWEAKS=fast-stage-load") < names.index("SA2_TWEAKS=no-event-bars")
