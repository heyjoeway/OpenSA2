"""The chao-key-always tweak (runtime/tweaks/chao-key-always.c) on SetNextLevel (0x0043C4D0).

The tweak's file and runtime/sa2_tweaks.c are built with the pinned zig, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted SetNextLevel and the
runtime's peek and poke. The guest bytes the wrap and the body use are a small table: TwoPlayerMode
(0x0174AFDE), the byte at 0x0174B003, HaveChaoKey (0x0174B004), CurrentLevel (int16 at
0x01934B70), GameState (int16 at 0x01934BE0) and NextLevel (int16 at 0x01934BEC).

The stand-in `__real_sub_43c4d0` records HaveChaoKey as it finds it on entry and then does what the
lifted body does with it: in a two-player game it clears the key; in one-player play, with the
key held and the level outside Cannon's Core's sections 35 to 38 (or the byte at 0x0174B003 1),
it stores GameState 11 and NextLevel 90 (Chao World); otherwise GameState 8, and in Cannon's Core
the next section's level from the table at 0x0043C7A0 (36->35->37->38->34->0x42). Level 34, the
last section, is outside the 35 to 38 test at 0x0043C6C9, so with the key held it always routes.

Each call is "<two-player> <level> <key> <0x0174B003>" and prints
"<key on entry> <key after> <GameState> <NextLevel>".
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "CHAO_KEY_FAIL")
TWEAK = "chao-key-always"

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "guest_memory.h"

#define TWO_PLAYER 0x0174AFDEu
#define LAST_PART  0x0174B003u
#define HAVE_KEY   0x0174B004u
#define LEVEL      0x01934B70u
#define STATE      0x01934BE0u
#define NEXT       0x01934BECu

static uint8_t two_player, last_part, key;
static uint16_t level, state, next;
static const char *fail;     /* CHAO_KEY_FAIL: "peek" or "poke" fails at HaveChaoKey */

static void *at(uint32_t a, uint32_t n) {
    if (a == TWO_PLAYER && n == 1) return &two_player;
    if (a == LAST_PART && n == 1) return &last_part;
    if (a == HAVE_KEY && n == 1) return &key;
    if (a == LEVEL && n <= 2) return &level;     /* little endian: 1 byte is the low byte */
    if (a == STATE && n == 2) return &state;
    if (a == NEXT && n == 2) return &next;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = at(a, n);
    if (!p || (a == HAVE_KEY && fail && strcmp(fail, "peek") == 0)) return SA2_E_RANGE;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = at(a, n);
    if (!p || (a == HAVE_KEY && fail && strcmp(fail, "poke") == 0)) return SA2_E_RANGE;
    memcpy(p, in, n);
    return SA2_OK;
}

static uint8_t entry_key;
static const uint16_t cc_next[5] = {0x42, 37, 35, 38, 34};   /* 0x0043C7A0: levels 34 to 38 */
void *__real_sub_43c4d0(void *s, uint32_t pc, void *memory) {
    (void)s; (void)pc;
    entry_key = key;
    if (two_player) { key = 0; state = 14; return memory; }
    if (key && (level < 35 || level > 38 || last_part == 1)) { state = 11; next = 90; return memory; }
    state = 8;
    if (level >= 34 && level <= 38) next = cc_next[level - 34];
    return memory;
}
void *__wrap_sub_43c4d0(void *s, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    fail = getenv("CHAO_KEY_FAIL");
    for (int i = 1; i + 3 < argc; i += 4) {
        two_player = (uint8_t)strtoul(argv[i], NULL, 0);
        level = (uint16_t)strtoul(argv[i + 1], NULL, 0);
        key = (uint8_t)strtoul(argv[i + 2], NULL, 0);
        last_part = (uint8_t)strtoul(argv[i + 3], NULL, 0);
        state = 16;
        next = 0;
        int token;
        if (__wrap_sub_43c4d0(&token, 0x0043C4D0u, &token) != &token) return 3;
        printf("%u %u %u %u\n", entry_key, key, state, next);
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
    d = tmp_path_factory.mktemp("chao-key")
    (d / "driver.c").write_text(DRIVER)
    out = d / "ck"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / f"{TWEAK}.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


REPORT = f"sa2 tweaks: chao key always (SA2_TWEAKS={TWEAK})"
SAY = "sa2 tweaks: chao key always: "

# One-player: City Escape (13) without the key, then with it; Metal Harbor (10) without it;
# Chao World's own end (90); a two-player game; Cannon's Core, Tails's section (36), which goes to
# 35 and does not route unless the byte at 0x0174B003 is 1, without that byte and then with it;
# Cannon's Core, Sonic's last section (34), which routes even with 0x0174B003 = 0.
CALLS = ("0", "13", "0", "0", "0", "13", "1", "0", "0", "10", "0", "0", "0", "90", "0", "0",
         "1", "13", "0", "0", "0", "36", "0", "0", "0", "36", "0", "1", "0", "34", "0", "0")
GAME = ["0 0 8 0", "1 1 11 90", "0 0 8 0", "0 0 8 0", "0 0 14 0", "0 0 8 35", "0 0 8 35",
        "0 0 8 66"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body_and_says_nothing_of_the_key(exe, env):
    out, err = run(exe, *CALLS, **env)
    assert out == GAME
    assert not [line for line in err if line.startswith(SAY)]


def test_on_one_player_stage_clears_go_to_chao_world(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS=TWEAK)
    assert out == ["1 1 11 90",      # 13 without the key: held on entry, Chao World, still held
                   "1 1 11 90",      # 13 with the key found: the game's own route
                   "1 1 11 90",      # 10 without the key
                   "0 0 8 0",        # Chao World's own end: left alone
                   "0 0 14 0",       # two-player: left alone, and the game clears the key
                   "1 0 8 35",       # 36: no route, the key put back, on to section 35
                   "1 1 11 90",      # 36 with the byte at 0x0174B003 set: Chao World
                   "1 1 11 90"]      # 34, Sonic's last section: Chao World
    assert err == [REPORT,
                   SAY + "level 13 cleared without the Chao Key: going to Chao World",
                   SAY + "level 13 cleared with the Chao Key found",
                   SAY + "level 10 cleared without the Chao Key: going to Chao World",
                   SAY + "Chao World's own end is left alone",
                   SAY + "a two-player game is left alone",
                   SAY + "level 36: no Chao World route from this section, so the key is put back",
                   SAY + "level 36 cleared without the Chao Key: going to Chao World",
                   SAY + "level 34 cleared without the Chao Key: going to Chao World"]


def test_the_spec_check_level_10_then_90(exe):
    """The key as SetNextLevel finds it: 1 at Metal Harbor's end, 0 at Chao World's."""
    out, _ = run(exe, "0", "10", "0", "0", "0", "90", "0", "0", SA2_TWEAKS=TWEAK)
    assert [line.split()[0] for line in out] == ["1", "0"]


def test_only_the_low_byte_of_current_level_is_read(exe):
    """As the Codes.lst entry reads it: 0x015A has the low byte 90."""
    out, _ = run(exe, "0", str(0x015A), "0", "0", SA2_TWEAKS=TWEAK)
    assert out == ["0 0 8 0"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS=TWEAK, **{replay: "/dev/null"})
    assert out == GAME
    assert not [line for line in err if line.startswith(SAY)]


@pytest.mark.parametrize("fail, said", [
    ("peek", "TwoPlayerMode, CurrentLevel or HaveChaoKey does not read, so the key is left alone"),
    ("poke", "HaveChaoKey at 0x0174B004 does not write, so the key is left alone")])
def test_a_key_that_does_not_read_or_write_is_left_alone_and_said_once(exe, fail, said):
    out, err = run(exe, "0", "13", "0", "0", "0", "10", "0", "0", SA2_TWEAKS=TWEAK,
                   CHAO_KEY_FAIL=fail)
    assert out == ["0 0 8 0", "0 0 8 0"]
    assert err == [REPORT, SAY + said]


def test_routes_are_said_32_times(exe):
    out, err = run(exe, *("0", "13", "0", "0") * 34, SA2_TWEAKS=TWEAK)
    assert out == ["1 1 11 90"] * 34
    said = [line for line in err if line.startswith(SAY)]
    assert len(said) == 32
    assert said[-1] == SAY + ("level 13 cleared without the Chao Key: going to Chao World; later "
                              "ones are not said")


def test_cannons_core_off_follows_the_section_table(exe):
    """Off, with the key found, each Cannon's Core section as the game takes it: 34 routes (it is
    outside 35 to 38), 35 to 38 go on to the next section from 0x0043C7A0 unless the byte at
    0x0174B003 is 1."""
    calls = [c for lv in (34, 35, 36, 37, 38) for c in ("0", str(lv), "1", "0")]
    calls += ["0", "37", "1", "1"]
    out, _ = run(exe, *calls)
    assert out == ["1 1 11 90", "1 1 8 37", "1 1 8 35", "1 1 8 38", "1 1 8 34", "1 1 11 90"]
