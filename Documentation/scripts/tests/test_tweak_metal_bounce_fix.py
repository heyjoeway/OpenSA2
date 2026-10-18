"""The metal-bounce-fix tweak (runtime/tweaks/metal-bounce-fix.c): on LoadMetalSonic's first call
(0x00717640), MetalSonicAnimList's entry 30 at 0x00A1A9C8 goes from animation 30 on model 71
(1E 00 47 00) to animation 12 on model 418 (0C 00 A2 01), the Mod Loader's "Fix Metal Sonic Bounce
Attack Crash" writes; with the tweak off, the table is the executable's.

The tweak's file and runtime/sa2_tweaks.c are built with the pinned zig as test_tweaks.py builds
them (-O2, x86_64-linux-musl, -Werror), linked with a driver that stands in for the guest's .dataa
around the entry and for the lifted LoadMetalSonic. The stand-in `__real_sub_717640` reads the
entry's 16 bytes from guest memory, as the lifted body's consumers would, and prints them, so each
line shows what the body saw on that call. argv sets the entry's starting bytes ("game" for the
executable's, "cheated" for bytes some other change left, "unreadable" for a guest read that
fails) and how many loads to run.
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

ENTRY = 0x00A1A9C8
# The executable's entry 30 and its neighbours, read from the unpacked image (MetalSonicAnimList,
# 0x00A1A7E8, 16-byte entries): 29, 30, 31.
IMAGE = {
    29: "1d 00 a2 01 03 00 1d 00 00 00 80 3d cd cc 4c 3e",
    30: "1e 00 47 00 03 00 1e 00 00 00 00 3f 00 00 80 3f",
    31: "1f 00 a2 01 09 00 23 00 00 00 00 3e cd cc 4c 3e",
}
FIXED_30 = "0c 00 a2 01 03 00 1e 00 00 00 00 3f 00 00 80 3f"
SAID = ("sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose (0x00A1A9C8) now animation 12 "
        "on model 418")

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define TABLE 0x00A1A7E8u
static uint8_t data[3 * 16];            /* entries 29, 30 and 31 */
#define LO (TABLE + 29 * 16)
static int unreadable, pokes;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (unreadable || a < LO || a + n > LO + sizeof data) return 1;
    memcpy(out, data + (a - LO), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (a < LO || a + n > LO + sizeof data) return 1;
    memcpy(data + (a - LO), in, n);
    pokes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; (void)idx; return 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

void *__real_sub_717640(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    for (int e = 0; e < 3; e++) {
        for (int i = 0; i < 16; i++) printf("%s%02x", i ? " " : "", data[e * 16 + i]);
        printf(e < 2 ? " | " : "\n");
    }
    return memory;
}
void *__wrap_sub_717640(void *state, uint32_t pc, void *memory);

static void hex(const char *s, uint8_t *out) {
    for (int i = 0; i < 16; i++) out[i] = (uint8_t)strtoul(s + 3 * i, NULL, 16);
}

int main(int argc, char **argv) {
    if (argc != 6) return 2;
    hex(argv[1], data); hex(argv[2], data + 16); hex(argv[3], data + 32);
    unreadable = strcmp(argv[4], "unreadable") == 0;
    const int loads = atoi(argv[5]);
    for (int i = 0; i < loads; i++) {
        int token;
        if (__wrap_sub_717640(&token, 0x00717640u, &token) != &token) return 3;
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("metal_bounce")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "metal-bounce-fix.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, entry30: str = IMAGE[30], loads: int = 2, mode: str = "readable",
        **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), IMAGE[29], entry30, IMAGE[31], mode, str(loads)],
                       capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def seen(entry30: str) -> str:
    """What the stand-in body prints when entry 30 holds `entry30`."""
    return f"{IMAGE[29]} | {entry30} | {IMAGE[31]}"


def test_the_image_bytes_here_are_the_executables():
    """IMAGE is the unpacked executable's, when the private tree has it."""
    exe_path = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
    if not exe_path.is_file():
        pytest.skip("no unpacked executable in private/exe")
    pefile = pytest.importorskip("pefile")
    pe = pefile.PE(str(exe_path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    for e, want in IMAGE.items():
        got = pe.get_data(0x00A1A7E8 + 16 * e - base, 16)
        assert got.hex(" ") == want, e
    section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".dataa")
    lo = base + section.VirtualAddress
    assert lo <= ENTRY < lo + section.Misc_VirtualSize


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_leaves_the_table_and_says_nothing(exe, env):
    out, err = run(exe, **env)
    assert out == [seen(IMAGE[30]), seen(IMAGE[30]), "pokes 0"]
    assert "metal-bounce" not in err


def test_on_writes_entry_30_before_the_first_load_and_once(exe):
    out, err = run(exe, loads=3, SA2_TWEAKS="metal-bounce-fix")
    assert out == [seen(FIXED_30)] * 3 + ["pokes 1"]
    assert err.splitlines() == ["sa2 tweaks: metal-bounce fixed (SA2_TWEAKS=metal-bounce-fix)", SAID]


def test_on_with_no_load_writes_nothing(exe):
    """Nothing changes until a two-player game loads Metal Sonic."""
    out, err = run(exe, loads=0, SA2_TWEAKS="metal-bounce-fix")
    assert out == ["pokes 0"]
    assert err.splitlines() == ["sa2 tweaks: metal-bounce fixed (SA2_TWEAKS=metal-bounce-fix)"]


def test_with_the_other_tweak_on_too(exe):
    out, err = run(exe, loads=1, SA2_TWEAKS="no-event-bars,metal-bounce-fix")
    assert out == [seen(FIXED_30), "pokes 1"]
    assert err.splitlines() == [
        "sa2 tweaks: metal-bounce fixed, event-bars off (SA2_TWEAKS=no-event-bars,metal-bounce-fix)",
        SAID]


@pytest.mark.parametrize("entry30, found", [
    ("0c 00 a2 01 03 00 1e 00 00 00 00 3f 00 00 80 3f", "0C 00 A2 01"),   # already the fix's
    ("1e 00 47 01 03 00 1e 00 00 00 00 3f 00 00 80 3f", "1E 00 47 01"),   # one byte off
])
def test_bytes_that_are_not_the_games_are_left_alone_by_name(exe, entry30, found):
    out, err = run(exe, entry30=entry30, SA2_TWEAKS="metal-bounce-fix")
    assert out == [seen(entry30), seen(entry30), "pokes 0"]
    assert err.splitlines()[1:] == [
        f"sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose at 0x00A1A9C8 holds {found}, "
        "not the game's 1E 00 47 00, so it is left alone"]


def test_an_unreadable_entry_is_left_alone_by_name(exe):
    out, err = run(exe, mode="unreadable", SA2_TWEAKS="metal-bounce-fix")
    assert out == [seen(IMAGE[30]), seen(IMAGE[30]), "pokes 0"]
    assert err.splitlines()[1:] == [
        "sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose at 0x00A1A9C8 does not read, so "
        "it is left alone"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, SA2_TWEAKS="metal-bounce-fix", **{replay: "/dev/null"})
    assert out == [seen(IMAGE[30]), seen(IMAGE[30]), "pokes 0"]
    assert err == ("sa2 tweaks: SA2_TWEAKS=metal-bounce-fix ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
