"""The physics-preset tweak (runtime/tweaks/physics-preset.c): on the first call of
SetPhysicsParamsAndGiveUpgrades (0x004599C0), before its lifted body copies a character's entry of
PhysicsArray (0x017391E8, 19 entries of 0x84 bytes) into the character, the entries the preset
names are rewritten: by default Sonic's and Shadow's become Metal Sonic's, and SA2_PHYSICS_PRESET
gives <to>=<from> pairs. With the tweak off the table is the executable's and nothing is read.

The tweak's file and runtime/sa2_tweaks.c are built with the pinned zig as test_tweaks.py builds
them (-O2, x86_64-linux-musl, -Werror), linked with a driver that stands in for the guest's .dataa
holding the table and for the lifted body. The stand-in `__real_sub_4599c0` copies entry
[CharID2] from guest memory, 33 dwords, as the body's `rep movsd` at 0x00459A6E does, then reloads
the Weight dword from entry [CharID] as 0x00459910 does, and prints the character and the dwords, so
each line shows what that character was given. The argument 717a75 stands for the Sonic code's read
at 0x00717A75 and prints the four values it takes from Sonic's entry. argv: the table's file, the
guest memory's mode ("rw", "unreadable" or "readonly") and the characters to load.
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
from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env  # noqa: E402

TABLE, ENTRY, ENTRIES = 0x017391E8, 0x84, 19
SONIC, SHADOW, TAILS, KNUCKLES, ROUGE, MECH_TAILS, AMY, METAL, TIKAL = 0, 1, 2, 4, 5, 6, 8, 12, 15
WEIGHT = 30
# Each character's CharID (the byte at CharObj2Base+1), where its loader stores one unlike its
# CharID2; 0x00459910 reloads Weight from entry [CharID]. Checked against the image below.
CHARID = {AMY: SONIC, METAL: SHADOW, 13: MECH_TAILS, 14: 7, TIKAL: KNUCKLES, 16: ROUGE}
# The executable's PhysicsArray, read from the unpacked image: each distinct entry once, with the
# characters (Characters enum) whose entries hold it.
ROWS = {
    (0, 1, 9, 10, 11): (
        "3c000000000000400000804100008041000040409a99193fe17ad43f000040401f856b3e1f85eb3e85ebb13f"
        "33331340cdcc6c4048e1a240e3a59b3dcdcc4c3db6f3fd3c8fc275bdec5138be7b142ebe4260e5bc6f1203bc"
        "0ad723bccdccccbecdccccbd9a9919bfd7a390be9a99993e00008040000020410ad7a33d0000e040cdccac40"
    ),
    (2,): (
        "280000000000c03f00008041000080419a99193f9a99193fe17ad43f000040401f856b3e1f85eb3e85ebb13f"
        "33331340cdcc6c4000008040e3a59b3da69b443db6f3fd3c8fc275bdec5138be7b142ebe4260e5bc0ad723bc"
        "0ad723bccdccccbecdccccbd9a9919bfcdccacbe9a99993e00006040000010410ad7a33d0000c04000009040"
    ),
    (3,): (
        "00000000000040400000804100008041000040400000803fe17ad43f000040401f856b3e1f85eb3e85ebb13f"
        "33331340cdcc6c4048e1a240e3a59b3d8fc2753db6f3fd3c8fc275bdec5138be7b142ebe4260e5bc6f1203bc"
        "0ad723bccdccccbecdccccbd9a9919bfcdccacbe9a99993e00000841000078410ad7a33d0000684100002041"
    ),
    (4, 5): (
        "3c000000000000400000804100008041000020409a99193fe17ad43f000040401f856b3e1f85eb3e85ebb13f"
        "66664640cdcc6c4048e1a240e3a59b3dcdcc4c3db6f3fd3c8fc275bdec5138be7b142ebe4260e5bc6f1203bc"
        "0ad723bccdccccbecdccccbd9a9919bfcdccacbe9a99993e00008040666636410ad7a33d000010416666b640"
    ),
    (6, 7): (
        "100000000000404000008041000080410000803f0000803f66662640000040401f856b3e1f85eb3e85ebb13f"
        "6666e63f00000040000040405c8f423ecdcccc3db6f3fd3ccdccccbdec5138be7b142ebe4260e5bc426065bc"
        "0ad7a3bccdccccbecdccccbd9a9919bfcdccacbe9a99993e000000410000a841cdcc4c3e0000a04100007041"
    ),
    (8,): (
        "3c0000000000004000008041000080416666a63f9a99193f6666a63f000040401f856b3e1f85eb3e85ebb13f"
        "33331340cdcc6c4048e1a240e3a59b3da69b443db6f3fd3c8fc275bdec5138be7b142ebe4260e5bc6f1203bc"
        "0ad723bccdccccbecdccccbd9a9919bfd7a390be9a99993e00008040000020410ad7a33d0000e040cdccac40"
    ),
    (12, 17, 18): (
        "3c000000000000400000804100008041000040409a99193fae472140000040401f856b3e1f85eb3e85ebb13f"
        "33331340cdcc6c4048e1a240e3a59b3dcdcc4c3ecdcc4c3d0ad723bc0ad7a3bc8fc2f5bc4260e5bc6f1203bc"
        "0ad723bccdccccbecdccccbe9a9919bfd7a390be9a99993e00008040000020410ad7a33d0000e040cdccac40"
    ),
    (13,): (
        "100000000000404000008041000080413333b33f0000803f66662640000040401f856b3e1f85eb3e85ebb13f"
        "6666e63f00000040000040405c8f423ecdcc4c3e7f6a3c3dcdccccbdec5138be7b142ebe4260e5bc426065bc"
        "0ad7a3bccdccccbecdccccbe9a9919bfcdccacbe9a99993e000000410000a841cdcc4c3e0000a04100007041"
    ),
    (14,): (
        "100000000000404000008041000080413333333f0000803f66662640000040401f856b3e1f85eb3e85ebb13f"
        "6666e63f00000040000040405c8f423e295c8f3dcdcccc3ccdccccbdec5138be7b142ebe4260e5bc426065bc"
        "0ad7a3bccdccccbe295c8fbd9a9919bfcdccacbe9a99993e000000410000a841cdcc4c3e0000a04100007041"
    ),
    (15,): (
        "3c000000000000400000804100008041000020409a99193fe17ad43f000040401f856b3e1f85eb3e85ebb13f"
        "66664640cdcc6c4048e1a240e3a59b3d8fc2753db6f3fd3c8fc275bdcdcc4cbe7b142ebe4260e5bc6f1203bc"
        "0ad723bccdccccbe9a9999be9a9919bf6666e6be9a99993e00008040666636410ad7a33d000010416666b640"
    ),
    (16,): (
        "3c0000000000004000008041000080410000803f9a99193fe17ad43f000040401f856b3e1f85eb3e85ebb13f"
        "66664640cdcc6c4048e1a240e3a59b3dcdcc4c3db6f3fd3c8fc275bdec5138be7b142ebe4260e5bc6f1203bc"
        "0ad723bccdccccbeec51b8bd9a9919bfcdccacbe9a99993e00008040666636410ad7a33d000010416666b640"
    ),
}


def image_table() -> list[bytes]:
    out = [b""] * ENTRIES
    for ids, text in ROWS.items():
        for i in ids:
            out[i] = bytes.fromhex("".join(text))
    assert all(len(e) == ENTRY for e in out)
    return out


IMAGE = image_table()
ON = "sa2 tweaks: physics preset (SA2_TWEAKS=physics-preset)"
METAL_CHANGES = ("7 of 33 values: JumpSpeed 1.66 -> 2.52, RunAccel 0.05 -> 0.2, AirAccel 0.031 -> 0.05, "
                 "RunDecel -0.06 -> -0.01, RunBrake -0.18 -> -0.02, AirBrake -0.17 -> -0.03, "
                 "GroundFriction -0.1 -> -0.4")
METAL_SAID = [f"sa2 tweaks: physics preset: Sonic takes Metal Sonic's physics ({METAL_CHANGES})",
              f"sa2 tweaks: physics preset: Shadow takes Metal Sonic's physics ({METAL_CHANGES})"]

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

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (unreadable || a < TABLE || a + n > TABLE + sizeof data) return 1;
    memcpy(out, data + (a - TABLE), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (readonly || a < TABLE || a + n > TABLE + sizeof data) return 1;
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
    for (int i = 3; i < argc; i++) {
        if (strcmp(argv[i], "717a75") == 0) {   /* Sonic's FloorGrip, Radius, Height, CenterHeight */
            uint32_t v[4];
            memcpy(&v[0], data + 0x04, 4); memcpy(&v[1], data + 0x70, 4);
            memcpy(&v[2], data + 0x74, 4); memcpy(&v[3], data + 0x80, 4);
            printf("717a75 %08x %08x %08x %08x\n", v[0], v[1], v[2], v[3]);
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
    d = tmp_path_factory.mktemp("physics_preset")
    (d / "driver.c").write_text(DRIVER)
    (d / "table.bin").write_bytes(b"".join(IMAGE))
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "physics-preset.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *loads: int | str, mode: str = "rw", table: list[bytes] | None = None,
        **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS + ("SA2_PHYSICS_PRESET",)}
    path = exe.parent / "table.bin"
    if table is not None:
        path = exe.parent / "table-custom.bin"
        path.write_bytes(b"".join(table))
    p = subprocess.run([str(exe), str(path), mode, *map(str, loads)],
                       capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def words(entry: bytes) -> list[int]:
    return list(struct.unpack("<33I", entry))


def given(character: int, entry: int | None = None, table: list[bytes] | None = None) -> str:
    """The stand-in body's line when `character` loads and copies entry `entry` of the image, its
    Weight then reloaded from entry [CharID] of `table` (the table as the preset left it; by
    default the image)."""
    w = words(IMAGE[character if entry is None else entry])
    w[WEIGHT] = words((table or IMAGE)[CHARID.get(character, character)])[WEIGHT]
    return " ".join([str(character)] + [f"{x:08x}" for x in w])


def sonic_code_read(sonic_entry: int) -> str:
    """The stand-in for 0x00717A75: Sonic's FloorGrip, Radius, Height and CenterHeight, Sonic's entry
    holding entry `sonic_entry` of the image."""
    w = words(IMAGE[sonic_entry])
    return "717a75 " + " ".join(f"{w[i]:08x}" for i in (1, 28, 29, 32))


def preset(**swaps: int) -> list[bytes]:
    """The image's table with each named entry given another's, as the preset writes it."""
    out = list(IMAGE)
    for to, src in swaps.items():
        out[globals()[to.upper()]] = IMAGE[src]
    return out


def test_the_table_here_is_the_executables():
    """ROWS is the unpacked executable's PhysicsArray, in .dataa, when the private tree has it."""
    exe_path = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
    if not exe_path.is_file():
        pytest.skip("no unpacked executable in private/exe")
    pefile = pytest.importorskip("pefile")
    pe = pefile.PE(str(exe_path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    assert pe.get_data(TABLE - base, ENTRY * ENTRIES) == b"".join(IMAGE)
    section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".dataa")
    lo = base + section.VirtualAddress
    assert lo <= TABLE and TABLE + ENTRY * ENTRIES <= lo + section.SizeOfRawData
    # The copy the tweak's header reads: movsx esi,byte [ebp+3]; imul esi,0x84; ...;
    # add esi,0x017391E8; lea edi,[ebp+0xC0]; mov ecx,0x21; rep movsd.
    code = pe.get_data(0x00459A38 - base, 0x38)
    assert code.startswith(bytes.fromhex("0fbe7503" "69f684000000"))
    assert bytes.fromhex("81c6e8917301" "8dbdc0000000" "b921000000" "f3a5") in code
    # 0x00459910's reload: movsx edx,byte [ecx+1] (CharID); imul edx,0x84; fld [edx+0x01739260]
    # (the entry's Weight, +0x78); fstp [ecx+0x138].
    assert pe.get_data(0x0045995D - base, 0x16) == bytes.fromhex(
        "0fbe5101" "69d284000000" "d98260927301" "d99938010000")
    # The loaders that store a CharID unlike the CharID2: mov byte [r+1],CharID; mov byte [r+3],CharID2.
    stores = {0x00717186: 0x45, 0x007176E6: 0x45, 0x0072895B: 0x45, 0x00728C00: 0x47,
              0x007411A7: 0x47, 0x00741387: 0x47}
    found = {}
    for at, modrm in stores.items():
        b = pe.get_data(at - base, 8)
        assert b[:3] == bytes([0xC6, modrm, 0x01]) and b[4:7] == bytes([0xC6, modrm, 0x03]), hex(at)
        found[b[7]] = b[3]
    assert found == CHARID
    # 0x00717A75: Sonic's Height, Radius, FloorGrip and CenterHeight into CharObj2Base+0x134, +0x130,
    # +0xC4 and +0x140, in the main function 0x00717840 that four loaders install.
    assert pe.get_data(0x00717A75 - base, 0x30) == bytes.fromhex(
        "d9055c927301d99f34010000" "d90558927301d99f30010000" "d905ec917301d99fc4000000"
        "d90568927301d99f40010000")
    for at in (0x00716E36, 0x007170EC, 0x00717396, 0x0071764C):   # Sonic, Amy, Shadow, Metal Sonic
        assert pe.get_data(at - base, 5) == bytes.fromhex("bf40787100"), hex(at)


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_PHYSICS_PRESET": "shadow=amy"}])
def test_off_leaves_the_table_and_says_nothing(exe, env):
    out, err = run(exe, SONIC, SHADOW, METAL, **env)
    assert out == [given(SONIC), given(SHADOW), given(METAL), "pokes 0"]
    assert "physics" not in err


def test_on_gives_sonic_and_shadow_metal_sonics_physics_from_the_first_load(exe):
    out, err = run(exe, SHADOW, SONIC, METAL, KNUCKLES, SHADOW, SA2_TWEAKS="physics-preset")
    assert out == [given(SHADOW, METAL), given(SONIC, METAL), given(METAL), given(KNUCKLES),
                   given(SHADOW, METAL), "pokes 2"]
    assert err.splitlines() == [ON] + METAL_SAID


def test_metal_sonic_names_the_default(exe):
    assert run(exe, SHADOW, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="metal-sonic") == \
        run(exe, SHADOW, SA2_TWEAKS="physics-preset")


def test_on_with_no_load_writes_nothing(exe):
    out, err = run(exe, SA2_TWEAKS="physics-preset")
    assert out == ["pokes 0"] and err.splitlines() == [ON]


def test_pairs_read_the_executables_table_so_two_can_trade(exe):
    out, err = run(exe, TAILS, AMY, SONIC, SA2_TWEAKS="physics-preset",
                   SA2_PHYSICS_PRESET=" tails=amy , amy=tails,")
    assert out == [given(TAILS, AMY), given(AMY, TAILS), given(SONIC), "pokes 2"]
    said = err.splitlines()[1:]
    assert said[0].startswith("sa2 tweaks: physics preset: Tails takes Amy's physics (")
    assert said[1].startswith("sa2 tweaks: physics preset: Amy takes Tails's physics (")
    assert "HangTime 40 -> 60" in said[0] and "HangTime 60 -> 40" in said[1]


@pytest.mark.parametrize("value, who, whose", [("shadow=sonic", "Shadow", "Sonic"),
                                                ("rouge=knuckles", "Rouge", "Knuckles"),
                                                ("mech-tails=mech-eggman", "Mech Tails", "Mech Eggman")])
def test_a_swap_between_equal_entries_changes_nothing_and_says_so(exe, value, who, whose):
    """Sonic and Shadow, Knuckles and Rouge, and the two mechs have the same numbers in the game."""
    out, err = run(exe, SHADOW, ROUGE, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET=value)
    assert out == [given(SHADOW), given(ROUGE), "pokes 0"]
    assert err.splitlines()[1:] == [
        f"sa2 tweaks: physics preset: {who} takes {whose}'s physics, the same numbers, so nothing changes"]


P = "sa2 tweaks: physics preset: "
SONIC_CODE = "(the Sonic code they share sets them from Sonic's entry at 0x00717A75)"


def test_a_change_to_sonics_shared_values_reaches_shadow_amy_and_metal_sonic_by_name(exe):
    """0x00717A75 sets FloorGrip, Radius, Height and CenterHeight from Sonic's entry for every character
    that runs Sonic's code, so sonic=knuckles gives Amy and Metal Sonic Knuckles' Height there."""
    out, err = run(exe, AMY, METAL, SHADOW, "717a75", SA2_TWEAKS="physics-preset",
                   SA2_PHYSICS_PRESET="sonic=knuckles")
    t = preset(sonic=KNUCKLES)
    assert out == [given(AMY, table=t), given(METAL, table=t), given(SHADOW, table=t),
                   "717a75 40000000 40800000 41366666 40b66666", "pokes 1"]
    assert out[3] == sonic_code_read(KNUCKLES)          # Height 11.4, CenterHeight 5.7: Knuckles'
    said = err.splitlines()[1:]
    assert said[0].startswith(P + "Sonic takes Knuckles's physics (6 of 33 values: ")
    assert said[1:] == [P + "Shadow, Amy and Metal Sonic also take Sonic's new Height 10 -> 11.4, "
                        "CenterHeight 5.4 -> 5.7 " + SONIC_CODE]


def test_a_change_to_sonics_weight_reaches_amy_by_name(exe):
    """Amy's CharID is Sonic's, so 0x00459910 gives her Sonic's entry's Weight: 0.2 with sonic=mech-tails."""
    out, err = run(exe, AMY, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="sonic=mech-tails")
    assert out == [given(AMY, table=preset(sonic=MECH_TAILS)), "pokes 1"]
    assert out[0].split()[1 + WEIGHT] == "3e4ccccd"      # 0.2, where the image gives Amy 0.08
    said = err.splitlines()[1:]
    assert said[0].startswith(P + "Sonic takes Mech Tails's physics (19 of 33 values: ")
    assert said[1:] == [
        P + "Amy also takes Sonic's new Weight 0.08 -> 0.2 (0x00459910 reloads it from entry [CharID], Sonic's)",
        P + "Shadow, Amy and Metal Sonic also take Sonic's new FloorGrip 2 -> 3, Radius 4 -> 8, Height 10 -> 21, "
        "CenterHeight 5.4 -> 15 " + SONIC_CODE]


def test_a_named_character_whose_weight_is_anothers_is_told_so(exe):
    """amy=mech-tails copies Mech Tails' entry into Amy, but her Weight stays Sonic's 0.08, and the Sonic
    code sets her four shared values back to Sonic's: the list leaves Weight out and two lines say so."""
    out, err = run(exe, AMY, "717a75", SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="amy=mech-tails")
    assert out == [given(AMY, MECH_TAILS), sonic_code_read(SONIC), "pokes 1"]
    assert out[0].split()[1 + WEIGHT] == "3da3d70a"      # 0.08, not Mech Tails' 0.2
    said = err.splitlines()[1:]
    assert said[0].startswith(P + "Amy takes Mech Tails's physics (18 of 33 values: HangTime 60 -> 16, ")
    assert "Weight" not in said[0]
    assert said[1:] == [
        P + "Amy's Weight is 0.08, Sonic's, not Mech Tails's 0.2: 0x00459910 reloads it from entry [CharID]",
        P + "at 0x00717A75 the Sonic code that Amy runs sets back Sonic's FloorGrip 2, Radius 4, Height 10, "
        "CenterHeight 5.4"]


def test_a_change_to_shadows_weight_reaches_metal_sonic_by_name(exe):
    out, err = run(exe, METAL, SHADOW, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="shadow=mech-tails")
    t = preset(shadow=MECH_TAILS)
    assert out == [given(METAL, table=t), given(SHADOW, MECH_TAILS, table=t), "pokes 1"]
    assert out[0].split()[1 + WEIGHT] == "3e4ccccd"
    assert err.splitlines()[2:] == [
        P + "Metal Sonic also takes Shadow's new Weight 0.08 -> 0.2 (0x00459910 reloads it from entry "
        "[CharID], Shadow's)",
        P + "at 0x00717A75 the Sonic code that Shadow runs sets back Sonic's FloorGrip 2, Radius 4, Height 10, "
        "CenterHeight 5.4"]


def test_no_shared_line_when_sonic_takes_the_same_shared_values(exe):
    """With Sonic's entry given the same numbers, Shadow keeps the four values 0x00717A75 sets."""
    _, err = run(exe, SHADOW, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="sonic=knuckles,shadow=knuckles")
    said = err.splitlines()[1:]
    assert len(said) == 3 and said[2].startswith(P + "Shadow takes Knuckles's physics (6 of 33 values: ")


def test_a_swap_whose_only_difference_is_an_unused_weight_changes_nothing_and_says_so(exe):
    """Tikal's Weight comes from Knuckles' entry; a table where the two differ only there."""
    table = list(IMAGE)
    w = words(IMAGE[KNUCKLES])
    w[WEIGHT] = 0x3E4CCCCD
    table[TIKAL] = struct.pack("<33I", *w)
    out, err = run(exe, TIKAL, table=table, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="tikal=knuckles")
    assert out == [given(TIKAL, KNUCKLES), "pokes 0"]
    assert err.splitlines()[1:] == [
        P + "Tikal takes Knuckles's physics; the one value that differs is Weight, which Tikal takes from "
        "Knuckles's entry (0x00459910 reloads it from entry [CharID]), so nothing changes"]


@pytest.mark.parametrize("value, why", [
    ("shadow=metal", 'no character is named "metal"'),
    ("knux=sonic", 'no character is named "knux"'),
    ("Shadow=sonic", 'no character is named "Shadow"'),
    ("shadow", '"shadow" is no <to>=<from> pair'),
    ("shadow=sonic,shadow=amy", "shadow is given physics twice"),
    (",", "it names no <to>=<from> pair"),
])
def test_a_value_that_is_no_preset_leaves_the_table_by_name(exe, value, why):
    out, err = run(exe, SONIC, SHADOW, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET=value)
    assert out == [given(SONIC), given(SHADOW), "pokes 0"]
    assert err.splitlines()[1:] == [
        f"sa2 tweaks: physics preset: SA2_PHYSICS_PRESET={value} is no preset ({why}; it takes "
        "metal-sonic or <to>=<from> pairs), so the physics are left alone"]


def test_an_unreadable_table_is_left_alone_by_name(exe):
    out, err = run(exe, SONIC, SHADOW, mode="unreadable", SA2_TWEAKS="physics-preset")
    assert out == [given(SONIC), given(SHADOW), "pokes 0"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: physics preset: the physics table at 0x017391E8 does not read, so the physics "
        "are left alone"]


def test_an_unwritable_entry_is_named_and_keeps_its_physics(exe):
    out, err = run(exe, SONIC, SHADOW, SHADOW, mode="readonly", SA2_TWEAKS="physics-preset")
    assert out == [given(SONIC), given(SHADOW), given(SHADOW), "pokes 0"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: physics preset: Sonic's entry at 0x017391E8 does not write, so Sonic keeps its physics",
        "sa2 tweaks: physics preset: Shadow's entry at 0x0173926C does not write, so Shadow keeps its physics"]


def test_with_the_other_tweak_on_too(exe):
    out, err = run(exe, SHADOW, SA2_TWEAKS="no-event-bars,physics-preset")
    assert out == [given(SHADOW, METAL), "pokes 2"]
    assert err.splitlines() == [
        "sa2 tweaks: event-bars off, physics preset (SA2_TWEAKS=no-event-bars,physics-preset)"] + METAL_SAID


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, SONIC, SHADOW, SA2_TWEAKS="physics-preset", SA2_PHYSICS_PRESET="shadow=amy",
                   **{replay: "/dev/null"})
    assert out == [given(SONIC), given(SHADOW), "pokes 0"]
    assert err == ("sa2 tweaks: SA2_TWEAKS=physics-preset ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
