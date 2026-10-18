"""The drives-to-minimals tweak (runtime/tweaks/drives-to-minimals.c): an enemy whose drop flags ask
for a Chaos Drive drops one animal instead, through EnemySetDead (0x0047ABB0), and with the tweak
off (or in a replay) the lifted body runs alone.

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py builds
no-event-bars, and linked with a driver that stands in for the lifted body and the runtime's peek,
poke and register accessors. The guest stack is at 0x20000 and holds the enemy's data pointer at
[ESP + 4], as the lifted CALL leaves it; the enemy's data is at 0x30000, its drop flags at + 0x4C;
TwoPlayerMode is the byte at 0x0174AFDE.

The stand-in `__real_sub_47abb0` does what the lifted body does with the flags: it records the byte
on entry, clears bit 0x20 when set (0x0047AC7A-0x0047AC8A), drops nothing unless a bit of 0x1C is
set (0x0047AC98), and in a 1P game (0x0047ADAD) calls ChaosDrive_Load for 0x10 (0x0047ADB6) and the
animal spawn for 0x08 (0x0047ADC5), then does the RET (ESP + 4). Each driver step prints
"<flags on entry> <flags after> <drives> <animals>".
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
ON = "sa2 tweaks: drives become minimals (SA2_TWEAKS=drives-to-minimals)"
FIRST = ("sa2 tweaks: drives become minimals: an enemy left an animal in place of its Chaos Drive "
         "(flags 0x{:02X}, made 0x{:02X} for the call)")

# argv: steps, each a flag byte (0x18) for one call of 0x0047ABB0 with those flags, or a switch for
# the steps after it:
#   2p / 1p    TwoPlayerMode 1 / 0
#   nostack    the guest stack stops reading (ESP moves outside it)
#   noflags    the enemy's data pointer points at memory that does not read
#   readonly   the enemy's flags read but take no write
#   lifted:    the steps after it call the lifted stand-in directly, not the wrap
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
#define ENEMY 0x30000u
#define TWO_P 0x0174AFDEu
static uint8_t stack[0x100], enemy[0x80], two_p;
static uint32_t gpr[8];
static int readonly;

static void *where(uint32_t a, uint32_t n) {
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    if (a >= ENEMY && a + n <= ENEMY + sizeof enemy) return enemy + (a - ENEMY);
    if (a == TWO_P && n == 1) return &two_p;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = where(a, n);
    if (!p || (readonly && a >= ENEMY && a < ENEMY + sizeof enemy)) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static int entry_flags, drives, animals;
void *__real_sub_47abb0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    uint32_t data = 0;
    uint8_t f = 0;
    sa2_peek(gpr[SA2_ESP] + 4, 4, &data);
    if (sa2_peek(data + 0x4C, 1, &f) == SA2_OK) {
        entry_flags = f;
        if (f & 0x20) { uint8_t g = f & 0xDF; memcpy(enemy + 0x4C, &g, 1); f = g; }
        if ((f & 0x1C) && !two_p) {
            if (f & 0x10) drives++;
            if (f & 0x08) animals++;
        }
    } else entry_flags = -1;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_47abb0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    int direct = 0, token;
    uint32_t esp = STACK + 0x80u, data = ENEMY;
    for (int i = 1; i < argc; i++) {
        const char *s = argv[i];
        if (!strcmp(s, "2p")) { two_p = 1; continue; }
        if (!strcmp(s, "1p")) { two_p = 0; continue; }
        if (!strcmp(s, "nostack")) { esp = 0x90000u; continue; }
        if (!strcmp(s, "noflags")) { data = 0x90000u; continue; }
        if (!strcmp(s, "readonly")) { readonly = 1; continue; }
        if (!strcmp(s, "lifted:")) { direct = 1; continue; }
        enemy[0x4C] = (uint8_t)strtoul(s, NULL, 0);
        enemy[0x4D] = 0x5A;                    /* the word's other byte, which nothing changes */
        if (esp == STACK + 0x80u) memcpy(stack + 0x84, &data, 4);
        gpr[SA2_ESP] = esp;
        drives = animals = 0;
        entry_flags = -2;
        void *ret = (direct ? __real_sub_47abb0 : __wrap_sub_47abb0)(&token, 0x0047ABB0u, &token);
        if (ret != &token || gpr[SA2_ESP] != esp + 4 || enemy[0x4D] != 0x5A) return 3;
        printf("0x%02X 0x%02X %d %d\n", entry_flags & 0xFF, enemy[0x4C], drives, animals);
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
    d = tmp_path_factory.mktemp("drives_to_minimals")
    (d / "driver.c").write_text(DRIVER)
    out = d / "dtm"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "drives-to-minimals.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


# Flags the game's enemies carry: none, an animal, a drive, both, a drive with the 0x20 bit the body
# clears, and a drive with the effect bit 0x04.
FLAGS = ("0x00", "0x08", "0x10", "0x18", "0x30", "0x14")
GAME = ["0x00 0x00 0 0", "0x08 0x08 0 1", "0x10 0x10 1 0", "0x18 0x18 1 1", "0x30 0x10 1 0",
        "0x14 0x14 1 0"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_body(exe, env):
    out, err = run(exe, *FLAGS, **env)
    assert out == GAME
    assert run(exe, "lifted:", *FLAGS)[0] == GAME
    assert "drives" not in err


def test_on_a_drive_becomes_one_animal_and_the_flags_come_back(exe):
    """During the call 0x10 reads as 0x08; after it the 0x10 and 0x08 bits are the enemy's own
    again and bit 0x20 stays as the body left it (cleared)."""
    out, err = run(exe, *FLAGS, SA2_TWEAKS="drives-to-minimals")
    assert out == ["0x00 0x00 0 0", "0x08 0x08 0 1", "0x08 0x10 0 1", "0x08 0x18 0 1",
                   "0x28 0x10 0 1", "0x0C 0x14 0 1"]
    assert err.splitlines() == [ON, FIRST.format(0x10, 0x08)]


def test_the_spec_cases(exe):
    out, _ = run(exe, "0x10", "0x18", "0x08", "0x00", SA2_TWEAKS="drives-to-minimals")
    assert [line.split()[0] for line in out] == ["0x08", "0x08", "0x08", "0x00"]
    assert [line.split()[1] for line in out] == ["0x10", "0x18", "0x08", "0x00"]


def test_a_two_player_game_drops_nothing_either_way_and_says_so_once(exe):
    out, err = run(exe, "2p", "0x10", "0x18", "0x08", "1p", "0x10", SA2_TWEAKS="drives-to-minimals")
    assert out == ["0x08 0x10 0 0", "0x08 0x18 0 0", "0x08 0x08 0 0", "0x08 0x10 0 1"]
    assert run(exe, "2p", "0x10", "0x18", "0x08")[0] == ["0x10 0x10 0 0", "0x18 0x18 0 0",
                                                         "0x08 0x08 0 0"]
    assert err.splitlines() == [
        ON,
        "sa2 tweaks: drives become minimals: TwoPlayerMode is 1 (a 2P game, or the title screen's "
        "demo), where no enemy drops a drive or an animal, so there is nothing to change",
        FIRST.format(0x10, 0x08)]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *FLAGS, SA2_TWEAKS="drives-to-minimals", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=drives-to-minimals ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)\n")


def test_beside_no_event_bars(exe):
    out, err = run(exe, "0x18", SA2_TWEAKS="no-event-bars,drives-to-minimals")
    assert out == ["0x08 0x18 0 1"]
    assert err.splitlines()[0].startswith("sa2 tweaks: drives become minimals, event-bars off (")


@pytest.mark.parametrize("switch, va, verb", [("nostack", 0x00090004, "read"),
                                              ("noflags", 0x0009004C, "read"),
                                              ("readonly", 0x0003004C, "take a write")])
def test_memory_that_does_not_answer_keeps_the_drive_and_says_so_once(exe, switch, va, verb):
    out, err = run(exe, switch, "0x10", "0x18", SA2_TWEAKS="drives-to-minimals")
    if switch == "readonly":
        assert out == ["0x10 0x10 1 0", "0x18 0x18 1 1"]
    else:
        # the stand-in body cannot find the flags either; nothing was changed by the wrap
        assert [line.split()[1:] for line in out] == [["0x10", "0", "0"], ["0x18", "0", "0"]]
    assert err.splitlines() == [
        ON,
        f"sa2 tweaks: drives become minimals: {'the guest stack' if switch == 'nostack' else 'the enemy' + chr(39) + 's drop flags'} "
        f"at 0x{va:08X} does not {verb}, so the enemy keeps its Chaos Drive"]
