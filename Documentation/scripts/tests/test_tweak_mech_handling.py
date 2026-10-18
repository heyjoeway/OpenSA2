"""The mech-handling tweak (runtime/tweaks/mech-handling.c): on the first call of
SetPhysicsParamsAndGiveUpgrades (0x004599C0), in physics-preset.c's wrap of it, five values of the
walkers' PhysicsArray entries (6 Mech Tails, 7 Mech Eggman, 13 Chao Walker, 14 Dark Chao Walker) are
written: SpeedMaxH, RushSpeed, RunAccel, AirAccel and RunDecel. With physics-preset on too, the
preset is written first. With the tweak off the table is the executable's and nothing is read.

Built as test_tweak_physics_preset.py builds that tweak (its image table and its stand-in body,
which copies entry [CharID2] as the `rep movsd` at 0x00459A6E does and reloads Weight from entry
[CharID] as 0x00459910 does, printing what the character was given), with mech-handling.c beside
physics-preset.c. The argument "dump" prints the whole table as it stands. argv: the table's file,
the guest memory's mode ("rw", "unreadable", "readonly", or "fail:<address>", where a poke of that
address fails) and the steps.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, GAME_SETTINGS, RUNTIME, TS, VARS, ZIG, zig_env  # noqa: E402
from test_tweak_physics_preset import ENTRIES, ENTRY, IMAGE, METAL_SAID, TABLE, WEIGHT  # noqa: E402

SONIC, SHADOW, MECH_TAILS, MECH_EGGMAN, METAL, WALKER, DARK_WALKER = 0, 1, 6, 7, 12, 13, 14
WALKERS = (MECH_TAILS, MECH_EGGMAN, WALKER, DARK_WALKER)
CHARID = {8: 0, 12: 1, 13: 6, 14: 7, 15: 4, 16: 5}
# The five dwords written: SpeedMaxH, RushSpeed, RunAccel, AirAccel, RunDecel.
FIELDS = (4, 11, 15, 16, 17)
# The image's five values per walker (float bits) and the ones the tweak writes, each exact.
BEFORE = {
    MECH_TAILS: (0x3F800000, 0x3FE66666, 0x3DCCCCCD, 0x3CFDF3B6, 0xBDCCCCCD),    # 1, 1.8, 0.1, 0.031, -0.1
    MECH_EGGMAN: (0x3F800000, 0x3FE66666, 0x3DCCCCCD, 0x3CFDF3B6, 0xBDCCCCCD),
    WALKER: (0x3FB33333, 0x3FE66666, 0x3E4CCCCD, 0x3D3C6A7F, 0xBDCCCCCD),        # 1.4, 1.8, 0.2, 0.046, -0.1
    DARK_WALKER: (0x3F333333, 0x3FE66666, 0x3D8F5C29, 0x3CCCCCCD, 0xBDCCCCCD),   # 0.7, 1.8, 0.07, 0.025, -0.1
}
AFTER = {
    MECH_TAILS: (0x3FA00000, 0x40000000, 0x3E23D70A, 0x3D4B295F, 0xBD75C28F),    # 1.25, 2, 0.16, 0.0496, -0.06
    MECH_EGGMAN: (0x3FA00000, 0x40000000, 0x3E23D70A, 0x3D4B295F, 0xBD75C28F),
    WALKER: (0x3FE00000, 0x40000000, 0x3EA3D70A, 0x3D96BB99, 0xBD75C28F),        # 1.75, 2, 0.32, 0.0736, -0.06
    DARK_WALKER: (0x3F600000, 0x40000000, 0x3DE56042, 0x3D23D70A, 0xBD75C28F),   # 0.875, 2, 0.112, 0.04, -0.06
}
ADDRESS = {MECH_TAILS: 0x01739500, MECH_EGGMAN: 0x01739584, WALKER: 0x0173989C, DARK_WALKER: 0x01739920}

M = "sa2 tweaks: mech handling: "
ON = "sa2 tweaks: mech handling (SA2_TWEAKS=mech-handling)"
SAID = [
    M + "Mech Tails (entry 6 at 0x01739500): SpeedMaxH 1 -> 1.25, RushSpeed 1.8 -> 2, RunAccel 0.1 -> 0.16, "
    "AirAccel 0.031 -> 0.0496, RunDecel -0.1 -> -0.06",
    M + "Mech Eggman (entry 7 at 0x01739584): SpeedMaxH 1 -> 1.25, RushSpeed 1.8 -> 2, RunAccel 0.1 -> 0.16, "
    "AirAccel 0.031 -> 0.0496, RunDecel -0.1 -> -0.06",
    M + "Chao Walker (entry 13 at 0x0173989C): SpeedMaxH 1.4 -> 1.75, RushSpeed 1.8 -> 2, RunAccel 0.2 -> 0.32, "
    "AirAccel 0.046 -> 0.0736, RunDecel -0.1 -> -0.06",
    M + "Dark Chao Walker (entry 14 at 0x01739920): SpeedMaxH 0.7 -> 0.875, RushSpeed 1.8 -> 2, RunAccel 0.07 -> "
    "0.112, AirAccel 0.025 -> 0.04, RunDecel -0.1 -> -0.06",
]

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define TABLE 0x017391E8u
static uint8_t data[19 * 0x84];
static int unreadable, readonly, pokes, loading;
static uint32_t fail_at;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (unreadable || a < TABLE || a + n > TABLE + sizeof data) return 1;
    memcpy(out, data + (a - TABLE), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (readonly || (fail_at && a == fail_at) || a < TABLE || a + n > TABLE + sizeof data) return 1;
    memcpy(data + (a - TABLE), in, n);
    pokes++;
    return SA2_OK;
}
static int charid(int c) {
    switch (c) { case 8: return 0; case 12: return 1; case 13: return 6; case 14: return 7;
                 case 15: return 4; case 16: return 5; default: return c; }
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__real_sub_4599c0(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    uint32_t cp[33];
    memcpy(cp, data + loading * 0x84, sizeof cp);   /* the body's rep movsd from guest memory */
    memcpy(&cp[30], data + charid(loading) * 0x84 + 0x78, 4);   /* 0x00459910: Weight from [CharID] */
    printf("%d", loading);
    for (int i = 0; i < 33; i++) printf(" %08x", cp[i]);
    printf("\n");
    return memory;
}
void *__wrap_sub_4599c0(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(data, 1, sizeof data, f) != sizeof data) return 4;
    fclose(f);
    unreadable = strcmp(argv[2], "unreadable") == 0;
    readonly = strcmp(argv[2], "readonly") == 0;
    if (strncmp(argv[2], "fail:", 5) == 0) fail_at = (uint32_t)strtoul(argv[2] + 5, NULL, 16);
    for (int i = 3; i < argc; i++) {
        if (strcmp(argv[i], "dump") == 0) {
            for (int e = 0; e < 19; e++) {
                uint32_t w[33];
                memcpy(w, data + e * 0x84, sizeof w);
                printf("entry %d", e);
                for (int k = 0; k < 33; k++) printf(" %08x", w[k]);
                printf("\n");
            }
            continue;
        }
        loading = atoi(argv[i]);
        int token;
        if (__wrap_sub_4599c0(&token, 0x004599C0u, &token) != &token) return 3;
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("mech_handling")
    (d / "driver.c").write_text(DRIVER)
    (d / "table.bin").write_bytes(b"".join(IMAGE))
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "physics-preset.c"),
                           str(RUNTIME / "tweaks" / "mech-handling.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: int | str, mode: str = "rw", **env: str) -> tuple[list[str], dict[int, list[int]], str]:
    """The stand-in body's lines, the table after the steps (a "dump" is added last), and stderr."""
    clean = {k: v for k, v in os.environ.items() if k not in VARS + ("SA2_PHYSICS_PRESET",)}
    p = subprocess.run([str(exe), str(exe.parent / "table.bin"), mode, *map(str, steps), "dump"],
                       capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    table = {int(x.split()[1]): [int(w, 16) for w in x.split()[2:]] for x in lines if x.startswith("entry ")}
    return [x for x in lines if not x.startswith("entry ")], table, p.stderr


def words(entry: bytes) -> list[int]:
    return list(struct.unpack("<33I", entry))


def image_words() -> dict[int, list[int]]:
    return {e: words(IMAGE[e]) for e in range(ENTRIES)}


def handled(table: dict[int, list[int]]) -> dict[int, list[int]]:
    """`table` with the tweak's five values in each walker's entry."""
    out = {e: list(w) for e, w in table.items()}
    for e in WALKERS:
        for f, v in zip(FIELDS, AFTER[e]):
            out[e][f] = v
    return out


def given(character: int, table: dict[int, list[int]]) -> str:
    """The stand-in body's line when `character` loads from `table`."""
    w = list(table[character])
    w[WEIGHT] = table[CHARID.get(character, character)][WEIGHT]
    return " ".join([str(character)] + [f"{x:08x}" for x in w])


def test_the_walkers_entries_here_are_the_executables():
    """The five values of each walker, as BEFORE has them, are the image's (the table literal is held
    to the executable by test_tweak_physics_preset.py), and so is the reading the header rests on."""
    for e in WALKERS:
        assert TABLE + e * ENTRY == ADDRESS[e]
        assert tuple(words(IMAGE[e])[f] for f in FIELDS) == BEFORE[e]
    exe_path = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
    if not exe_path.is_file():
        pytest.skip("no unpacked executable in private/exe")
    pefile = pytest.importorskip("pefile")
    pe = pefile.PE(str(exe_path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase

    def at(a: int, n: int) -> bytes:
        return pe.get_data(a - base, n)

    def call(a: int, target: int) -> None:
        b = at(a, 5)
        assert b[0] == 0xE8 and (a + 5 + struct.unpack("<i", b[1:])[0]) & 0xFFFFFFFF == target, hex(a)

    # The four walker loaders install MechEggman_Main (mov edi,0x00741530) and call 0x004599C0.
    for a in (0x00740C84, 0x00740EE4, 0x00741119, 0x007412F9):
        assert at(a, 5) == bytes.fromhex("bf30157400"), hex(a)
    for a in (0x00740D32, 0x00740F92, 0x007411BE, 0x0074139E):
        call(a, 0x004599C0)
    # MechEggman_Main's calls of the shared ground movement and the air movement.
    call(0x00741F95, 0x0045B610)
    call(0x00742178, 0x0045B610)
    call(0x0074250E, 0x0045D770)
    # The reads of the copied values: SpeedMaxH cp+0xD0 (with the drag times cp+0x114), RushSpeed
    # cp+0xEC, RunAccel cp+0xFC, AirAccel cp+0x100, RunDecel cp+0x104, and the mech code's own reads.
    assert at(0x0045B90D, 15) == bytes.fromhex("d94564" "d8a5d0000000" "d88d14010000")
    assert at(0x0045BB7A, 6) == bytes.fromhex("d985ec000000")
    assert at(0x0045BABC, 6) == bytes.fromhex("d88dfc000000")
    assert at(0x0045D8C7, 6) == bytes.fromhex("d98300010000")
    assert at(0x0045B9CE, 6) == at(0x0045B9EA, 6) == bytes.fromhex("d98504010000")
    assert at(0x00742100, 6) == at(0x0074274D, 6) == bytes.fromhex("d987d0000000")
    assert at(0x007417E4, 6) == bytes.fromhex("d987ec000000")
    # 0x00464DD8: cp+0xFC set again from entry [CharID2]+0x3C (fld [edx+0x01739224]; fstp [ecx+0xFC]).
    assert at(0x00464DD8, 12) == bytes.fromhex("d98224927301" "d999fc000000")
    # The jump table after the copy writes cp+0x114 and cp+0x110, which the tweak leaves alone.
    assert at(0x00459A90, 7) == bytes.fromhex("ff248d9c9d4500")
    assert at(0x00459AAD, 6) == bytes.fromhex("d99d14010000") and at(0x00459AB9, 6) == bytes.fromhex("d99d10010000")


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_leaves_the_table_and_says_nothing(exe, env):
    out, table, err = run(exe, MECH_EGGMAN, MECH_TAILS, **env)
    assert out == [given(MECH_EGGMAN, image_words()), given(MECH_TAILS, image_words()), "pokes 0"]
    assert table == image_words()
    assert "mech" not in err


def test_physics_preset_alone_leaves_the_walkers_alone(exe):
    out, table, err = run(exe, MECH_EGGMAN, SA2_TWEAKS="physics-preset")
    assert [table[e] for e in WALKERS] == [image_words()[e] for e in WALKERS]
    assert out == [given(MECH_EGGMAN, image_words()), "pokes 2"]
    assert "mech handling" not in err


def test_on_writes_the_five_values_of_each_walker_from_the_first_load(exe):
    out, table, err = run(exe, MECH_EGGMAN, MECH_TAILS, SONIC, MECH_EGGMAN, SA2_TWEAKS="mech-handling")
    after = handled(image_words())
    assert table == after
    assert out == [given(MECH_EGGMAN, after), given(MECH_TAILS, after), given(SONIC, image_words()),
                   given(MECH_EGGMAN, after), "pokes 20"]
    assert err.splitlines() == [ON] + SAID
    # Exactly the five dwords of the four entries changed; the other 15 entries are the image's.
    changed = {(e, k) for e in range(ENTRIES) for k in range(33) if table[e][k] != image_words()[e][k]}
    assert changed == {(e, f) for e in WALKERS for f in FIELDS}
    assert [table[MECH_EGGMAN][f] for f in FIELDS] == [0x3FA00000, 0x40000000, 0x3E23D70A, 0x3D4B295F, 0xBD75C28F]
    # AirDecel and AirResist (+0x50, +0x54: cp+0x110, cp+0x114) and Weight stay the image's.
    for e in WALKERS:
        assert [table[e][k] for k in (20, 21, WEIGHT)] == [image_words()[e][k] for k in (20, 21, WEIGHT)]


def test_the_walkers_load_their_new_values(exe):
    """A Chao Walker (CharID2 13, CharID 6) copies entry 13's new values, its Weight from Mech Tails'."""
    out, _, _ = run(exe, WALKER, DARK_WALKER, SA2_TWEAKS="mech-handling")
    w13, w14 = (out[0].split()[1:], out[1].split()[1:])
    assert [int(w13[f], 16) for f in FIELDS] == list(AFTER[WALKER])
    assert [int(w14[f], 16) for f in FIELDS] == list(AFTER[DARK_WALKER])
    assert int(w13[WEIGHT], 16) == words(IMAGE[MECH_TAILS])[WEIGHT]


def test_on_with_no_load_writes_nothing(exe):
    out, table, err = run(exe, SA2_TWEAKS="mech-handling")
    assert out == ["pokes 0"] and table == image_words() and err.splitlines() == [ON]


def test_with_the_default_preset_the_preset_comes_first(exe):
    out, table, err = run(exe, SHADOW, MECH_EGGMAN, SA2_TWEAKS="mech-handling,physics-preset")
    preset = image_words()
    preset[SONIC] = preset[SHADOW] = list(preset[METAL])
    after = handled(preset)
    assert table == after
    assert out == [given(SHADOW, after), given(MECH_EGGMAN, after), "pokes 22"]
    assert err.splitlines() == ["sa2 tweaks: mech handling, physics preset "
                                "(SA2_TWEAKS=mech-handling,physics-preset)"] + METAL_SAID + SAID


def test_a_preset_naming_a_walker_as_to_loses_the_five_values_by_name(exe):
    out, table, err = run(exe, MECH_EGGMAN, SA2_TWEAKS="physics-preset,mech-handling",
                          SA2_PHYSICS_PRESET="mech-eggman=metal-sonic")
    preset = image_words()
    preset[MECH_EGGMAN] = list(preset[METAL])
    after = handled(preset)
    assert table == after
    # Metal Sonic's other values stay (JumpSpeed 2.52), the five are mech handling's.
    assert table[MECH_EGGMAN][6] == words(IMAGE[METAL])[6]
    assert out == [given(MECH_EGGMAN, after), "pokes 21"]
    said = err.splitlines()[1:]
    assert said[0].startswith("sa2 tweaks: physics preset: Mech Eggman takes Metal Sonic's physics (")
    assert SAID[0] in said and SAID[2] in said and SAID[3] in said
    assert M + ("Mech Eggman (entry 7 at 0x01739584): SpeedMaxH 3 -> 1.25, RushSpeed 2.3 -> 2, RunAccel 0.2 -> "
                "0.16, AirAccel 0.05 -> 0.0496, RunDecel -0.01 -> -0.06") in said
    assert M + ("Mech Eggman's SpeedMaxH 3, RushSpeed 2.3, RunAccel 0.2, AirAccel 0.05, RunDecel -0.01 from the "
                "physics preset are replaced (mech handling is written after the preset)") in said


def test_a_preset_taking_a_walkers_numbers_takes_the_images_by_name(exe):
    out, table, err = run(exe, SONIC, SA2_TWEAKS="physics-preset,mech-handling",
                          SA2_PHYSICS_PRESET="sonic=mech-tails")
    assert table[SONIC] == words(IMAGE[MECH_TAILS])
    assert [table[SONIC][f] for f in FIELDS] == list(BEFORE[MECH_TAILS])
    assert "sa2 tweaks: physics preset: Sonic takes Mech Tails's numbers as the game has them, not mech " \
           "handling's (that tweak changes the walkers' own entries, after the preset)" in err.splitlines()
    assert out[-1] == "pokes 21"
    _, _, alone = run(exe, SONIC, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="sonic=mech-tails")
    assert "mech handling" not in alone


def test_an_unreadable_table_is_named_per_walker(exe):
    out, _, err = run(exe, MECH_EGGMAN, mode="unreadable", SA2_TWEAKS="mech-handling")
    assert out == [given(MECH_EGGMAN, image_words()), "pokes 0"]
    assert err.splitlines()[1:] == [
        M + f"{who}'s entry at 0x{ADDRESS[e]:08X} does not read, so {who} keeps its handling"
        for e, who in zip(WALKERS, ("Mech Tails", "Mech Eggman", "Chao Walker", "Dark Chao Walker"))]


def test_an_unwritable_table_is_named_per_walker(exe):
    out, table, err = run(exe, MECH_EGGMAN, mode="readonly", SA2_TWEAKS="mech-handling")
    assert out == [given(MECH_EGGMAN, image_words()), "pokes 0"] and table == image_words()
    assert err.splitlines()[1:] == [
        M + f"{who}'s SpeedMaxH at 0x{ADDRESS[e] + 0x10:08X} does not write, so {who} keeps its handling"
        for e, who in zip(WALKERS, ("Mech Tails", "Mech Eggman", "Chao Walker", "Dark Chao Walker"))]


def test_a_value_that_does_not_write_puts_that_walkers_back(exe):
    """Mech Eggman's RunAccel (0x017395C0) fails: its SpeedMaxH and RushSpeed, already written, are put
    back, and the other three walkers take their values."""
    out, table, err = run(exe, MECH_EGGMAN, mode="fail:017395C0", SA2_TWEAKS="mech-handling")
    expect = handled(image_words())
    expect[MECH_EGGMAN] = image_words()[MECH_EGGMAN]
    assert table == expect
    assert out == [given(MECH_EGGMAN, expect), "pokes 19"]   # 15 values, 2 written and 2 put back
    assert err.splitlines()[1:] == [
        SAID[0], M + "Mech Eggman's RunAccel at 0x017395C0 does not write, so Mech Eggman keeps its handling",
        SAID[2], SAID[3]]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, table, err = run(exe, MECH_EGGMAN, SA2_TWEAKS="mech-handling", **{replay: "/dev/null"})
    assert out == [given(MECH_EGGMAN, image_words()), "pokes 0"] and table == image_words()
    assert err == ("sa2 tweaks: SA2_TWEAKS=mech-handling ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_the_player_facing_text_names_the_16_as_a_factor_on_the_acceleration():
    """The tweak multiplies RunAccel and AirAccel by 1.6, which the ground code uses as one factor
    among others; measured, the walker gains speed about 1.1 to 1.3 times as fast, not 1.6. The
    README's summary names the parameter, and the Android note promises no measured factor."""
    (row,) = [r for r in TS.every_row(RUNTIME) if r["name"] == "mech-handling"]
    assert row["summary"] == (
        "the walkers (Tails' and Eggman's, and the 2-player Chao Walkers) get ground and air "
        "acceleration 1.6 times the game's value, slow down less when you let go and have a higher "
        "top speed; starting values, still to be tuned")
    (line,) = [ln.strip() for ln in GAME_SETTINGS.read_text().splitlines() if 'Tweak("mech-handling"' in ln]
    assert line == (
        'new Tweak("mech-handling", "Livelier walkers", "Tails\' Cyclone and Eggman\'s Egg Walker (and '
        'the Chao Walkers in 2-player) pick up speed faster once moving, lose less speed when you let go '
        'of the stick, and reach a higher top speed. These are starting values that may change. Off in '
        'replays; nothing is saved."),')
