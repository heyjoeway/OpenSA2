"""The model-tint-fix tweak (runtime/tweaks/model-tint-fix.c): on LoadLevelManager's first call
(0x0043CB50), the 23 diffuse material settings of SA2 Render Fix's model tint list go from the
default 0xB2B2B2FF to 0xFFFFFFFF, all or none; with the tweak off, the models are the executable's.

The tweak's file and runtime/sa2_tweaks.c are built with the pinned zig as test_tweaks.py builds
them (-O2, x86_64-linux-musl, -Werror), linked with a driver that stands in for the guest's .dataa
at the 23 addresses and for the lifted LoadLevelManager. The stand-in `__real_sub_43cb50` reads the
23 dwords from guest memory, as the lifted draw code would, and prints them, so each line shows
what the level saw on that call. argv sets one dword's starting value (index and value, or -1 for
the executable's bytes everywhere), one address that does not read and one that does not write
(-1 for none), and how many level starts to run.
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env  # noqa: E402

SOURCE = RUNTIME / "tweaks" / "model-tint-fix.c"
# The 23 settings, in the walk's order (the file's header), read from the unpacked image.
AT = [0x00B466DC, 0x00B45D94, 0x00B45BCC, 0x00B3A7F4, 0x00B3A1BC, 0x00B39FF4, 0x01176984,
      0x011763A4, 0x011658A4, 0x0116561C, 0x0161A974, 0x0161A6EC, 0x00B110BC, 0x00AEF61C,
      0x00AEFD3C, 0x01177134, 0x0117774C, 0x01177B64, 0x01166124, 0x0161B20C, 0x00B3CD64,
      0x0109214C, 0x01092934]
# Render Fix's list (rfg_mdltint.c): the object trees, then the models.
OBJECTS = [0x00B46A38, 0x00B3AA70, 0x01176AE8, 0x0116598C, 0x0161AA38]
MODELS = [0x00B0D7CC, 0x00AEED4C, 0x011772D8, 0x01177334, 0x01177BF0, 0x011659C4, 0x0160132C,
          0x00B3B67C, 0x01091CE4, 0x01092464]
WAS, NOW = "b2b2b2ff", "ffffffff"
SAID = "sa2 tweaks: model tint fixed: 23 materials (diffuse 0xB2B2B2FF -> 0xFFFFFFFF)"
ON = "sa2 tweaks: model tint fixed (SA2_TWEAKS=model-tint-fix)"

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "cpu_state.h"
#include "guest_memory.h"

static const uint32_t AT[23] = {%s};
static uint32_t value[23];
static int unreadable = -1, unwritable = -1, pokes;

static int slot(uint32_t a, uint32_t n) {
    if (n != 4) return -1;
    for (int i = 0; i < 23; i++) if (AT[i] == a) return i;
    return -1;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    const int i = slot(a, n);
    if (i < 0 || i == unreadable) return 1;
    *(uint32_t *)out = value[i];
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    const int i = slot(a, n);
    if (i < 0) return 1;
    pokes++;
    if (i == unwritable) return 0;          /* says it wrote, but the dword keeps its value */
    value[i] = *(const uint32_t *)in;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__real_sub_43cb50(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    for (int i = 0; i < 23; i++) printf("%%s%%08x", i ? " " : "", value[i]);
    printf("\n");
    return memory;
}
void *__wrap_sub_43cb50(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc != 6) return 2;
    for (int i = 0; i < 23; i++) value[i] = 0xB2B2B2FFu;
    const int at = atoi(argv[1]);
    if (at >= 0) value[at] = (uint32_t)strtoul(argv[2], NULL, 16);
    unreadable = atoi(argv[3]);
    unwritable = atoi(argv[4]);
    const int loads = atoi(argv[5]);
    for (int i = 0; i < loads; i++) {
        int token;
        if (__wrap_sub_43cb50(&token, 0x0043CB50u, &token) != &token) return 3;
    }
    printf("pokes %%d\n", pokes);
    return 0;
}
""" % ", ".join(f"0x{a:08X}u" for a in AT)


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("model_tint")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(SOURCE), "-o", str(out)], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, loads: int = 2, at: int = -1, value: str = "0", unreadable: int = -1,
        unwritable: int = -1, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), str(at), value, str(unreadable), str(unwritable), str(loads)],
                       capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def seen(*values: str) -> str:
    """What the stand-in body prints: the 23 dwords, one value each, or one value for all."""
    return " ".join(values if len(values) == 23 else values * 23)


def test_the_list_is_render_fixs_walked_over_the_image():
    """AT is what walking Render Fix's 5 object trees and 10 models finds in the unpacked
    executable: the first diffuse material (id 5) of each meshset, each 0xB2B2B2FF, in .dataa."""
    exe_path = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
    if not exe_path.is_file():
        pytest.skip("no unpacked executable in private/exe")
    pefile = pytest.importorskip("pefile")
    pe = pefile.PE(str(exe_path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase

    def u32(a): return struct.unpack("<I", pe.get_data(a - base, 4))[0]
    def u16(a): return struct.unpack("<H", pe.get_data(a - base, 2))[0]
    def u8(a): return pe.get_data(a - base, 1)[0]

    found = []

    def model(m):
        for sets, count in ((u32(m + 8), u16(m + 0x10)), (u32(m + 0xC), u16(m + 0x12))):
            for k in range(count):
                mats, n = u32(sets + 16 * k), u32(sets + 16 * k + 4)
                ids = [u8(mats + 8 * i) for i in range(n)]
                if 5 in ids:
                    found.append(mats + 8 * ids.index(5) + 4)

    def obj(o):
        if u32(o + 4): model(u32(o + 4))
        if u32(o + 0x2C): obj(u32(o + 0x2C))
        if u32(o + 0x30): obj(u32(o + 0x30))

    for o in OBJECTS: obj(o)
    for m in MODELS: model(m)
    assert found == AT
    assert all(u32(a) == 0xB2B2B2FF for a in AT)
    section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".dataa")
    lo = base + section.VirtualAddress
    assert all(lo <= a < lo + section.Misc_VirtualSize for a in AT)


def test_the_file_writes_the_list():
    """The file's MODEL_TINT_AT is AT, in order."""
    table = re.search(r"MODEL_TINT_AT\[\] = \{(.*?)\};", SOURCE.read_text(), re.S).group(1)
    assert [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{8})u", table)] == AT


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "metal-bounce-fix"}])
def test_off_leaves_the_models_and_says_nothing(exe, env):
    out, err = run(exe, **env)
    assert out == [seen(WAS), seen(WAS), "pokes 0"]
    assert "model tint" not in err


def test_on_writes_all_23_before_the_first_level_and_once(exe):
    out, err = run(exe, loads=3, SA2_TWEAKS="model-tint-fix")
    assert out == [seen(NOW)] * 3 + ["pokes 23"]
    assert err.splitlines() == [ON, SAID]


def test_on_with_no_level_writes_nothing(exe):
    out, err = run(exe, loads=0, SA2_TWEAKS="model-tint-fix")
    assert out == ["pokes 0"]
    assert err.splitlines() == [ON]


def test_with_another_tweak_on_too(exe):
    out, err = run(exe, loads=1, SA2_TWEAKS="metal-bounce-fix,model-tint-fix")
    assert out == [seen(NOW), "pokes 23"]
    assert err.splitlines() == [
        "sa2 tweaks: metal-bounce fixed, model tint fixed (SA2_TWEAKS=metal-bounce-fix,model-tint-fix)",
        SAID]


@pytest.mark.parametrize("at, value", [(0, "ffffffff"), (22, "b2b2b2fe"), (13, "00000000")])
def test_one_dword_not_the_games_leaves_all_alone_by_name(exe, at, value):
    out, err = run(exe, at=at, value=value, SA2_TWEAKS="model-tint-fix")
    values = [WAS] * 23
    values[at] = value
    assert out == [seen(*values), seen(*values), "pokes 0"]
    assert err.splitlines()[1:] == [
        f"sa2 tweaks: model tint fixed: 1 of the 23 materials is not the game's 0xB2B2B2FF "
        f"(0x{AT[at]:08X} holds 0x{int(value, 16):08X}), so none is changed"]


def test_an_unreadable_dword_leaves_all_alone_by_name(exe):
    out, err = run(exe, unreadable=5, SA2_TWEAKS="model-tint-fix")
    assert out == [seen(WAS), seen(WAS), "pokes 0"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: model tint fixed: the material at 0x00B39FF4 does not read, so none of the 23 "
        "is changed"]


def test_a_write_that_does_not_hold_puts_the_others_back_by_name(exe):
    """Dword 4's write does not hold: dwords 0-4 are written back to the game's value."""
    out, err = run(exe, unwritable=4, SA2_TWEAKS="model-tint-fix")
    assert out == [seen(WAS), seen(WAS), "pokes 10"]   # 5 written, then the 5 put back
    assert err.splitlines()[1:] == [
        "sa2 tweaks: model tint fixed: the material at 0x00B3A1BC could not be written, so the 4 "
        "before it are put back and none is changed"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, SA2_TWEAKS="model-tint-fix", **{replay: "/dev/null"})
    assert out == [seen(WAS), seen(WAS), "pokes 0"]
    assert err == ("sa2 tweaks: SA2_TWEAKS=model-tint-fix ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")

