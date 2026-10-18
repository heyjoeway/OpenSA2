"""The hide-upgrade-models tweak (runtime/tweaks/hide-upgrade-models.c): the five player displays
that draw upgrade models (Sonic 0x00720090, Knuckles 0x0072EF20, Rouge 0x00730970, Mech Eggman
0x007444F0, Mech Tails 0x00747DD0) run with the model bits of their player's Upgrades
(CharObj2Base+0x24) cleared, except a walker's guns (its shots start from them), and the dword is
the player's own again after each call.

The tweak's file is built beside runtime/sa2_tweaks.c with the pinned zig, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted displays and the
runtime's guest memory. The guest is a stack page (the display's argument, the task, at [ESP+4]
as the lifted CALL leaves it), a task at 0x00100000 whose Data2 (+0x40) is a CharObj2Base at
0x00100100. Each stand-in `__real_` records the Upgrades dword it reads, as the lifted body's
`test [ebx+24h], bit` would, sets EAX (which the tweak must never touch), pops the return address
(the RET), and, when a case asks, changes Upgrades itself. `call N` runs display N and prints
"<display> seen <dword the body read> after <dword after the call> esp <ESP moved>"; the driver
ends with the bodies' calls and the tweak's writes.
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

DISPLAYS = ["Sonic_Display", "Knuckles_Display", "Rouge_Display", "MechEggman_Display",
            "MechTails_Display"]
MODELS = 0x0E953C5D
# A walker's guns, drawn with the tweak on: Mech Tails' Bazooka 0x80 and Laser Blaster 0x100, Mech
# Eggman's Large Cannon 0x200000 and Laser Blaster 0x400000.
GUNS = 0x80 | 0x100 | 0x200000 | 0x400000
# Each display's own hidden model bits, as its body tests them (the tweak file's header).
OWN = {"Sonic_Display": 0x1 | 0x4 | 0x8 | 0x10 | 0x10000 | 0x40000,
       "Knuckles_Display": 0x400 | 0x800 | 0x1000 | 0x2000,
       "Rouge_Display": 0x2000000 | 0x4000000 | 0x8000000,
       "MechEggman_Display": 0x100000 | 0x800000,
       "MechTails_Display": 0x40}
# The bits with no model, which the tweak never clears: Ancient Lights, Mystic Melodies, Super Sonic.
NO_MODEL = 0x2 | 0x20000 | 0x20 | 0x200 | 0x4000 | 0x80000 | 0x1000000 | 0x10000000 | 0x8000
SAY = "sa2 tweaks: upgrade models hidden: "

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

/* The guest: a stack page and a heap page; anything else does not read. */
static const uint32_t page_at[] = {0x00020000u, 0x00100000u};
#define PAGES (sizeof page_at / sizeof page_at[0])
static uint8_t page[PAGES][0x400];
static unsigned reals, writes, readonly, notask;
static uint32_t gpr[8], seen, change;

static uint8_t *at(uint32_t a, uint32_t n) {
    for (unsigned i = 0; i < PAGES; i++)
        if (a >= page_at[i] && a + n <= page_at[i] + 0x400u) return page[i] + (a - page_at[i]);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p || readonly) return 1;
    memcpy(p, in, n);
    writes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

static uint32_t u32(uint32_t a) {
    uint32_t v = 0xFFFFFFFFu;
    if (at(a, 4)) memcpy(&v, at(a, 4), 4);
    return v;
}

/* A lifted display: reads its player's Upgrades through [[ESP+4]+0x40]+0x24, as the body does. */
static void *body(void *memory) {
    reals++;
    const uint32_t task = u32(gpr[SA2_ESP] + 4u), cp = u32(task + 0x40u);
    seen = u32(cp + 0x24u);
    if (change && at(cp + 0x24u, 4)) { const uint32_t v = seen | change; memcpy(at(cp + 0x24u, 4), &v, 4); }
    change = 0;
    gpr[SA2_EAX] = 0x00C0FFEEu;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
#define DISPLAY(sub) \
    void *__real_##sub(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; return body(memory); } \
    void *__wrap_##sub(void *state, uint32_t pc, void *memory);
DISPLAY(sub_720090) DISPLAY(sub_72ef20) DISPLAY(sub_730970) DISPLAY(sub_7444f0) DISPLAY(sub_747dd0)
typedef void *fn(void *, uint32_t, void *);
static fn *const wraps[5] = {__wrap_sub_720090, __wrap_sub_72ef20, __wrap_sub_730970, __wrap_sub_7444f0,
                             __wrap_sub_747dd0};
static const uint32_t pcs[5] = {0x00720090u, 0x0072EF20u, 0x00730970u, 0x007444F0u, 0x00747DD0u};

int main(int argc, char **argv) {
    uint32_t esp = 0x00020100u;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "call") == 0 && i + 1 < argc) {
            const int d = atoi(argv[++i]);
            const uint32_t task = 0x00100000u, cp = 0x00100100u, ret = 0x00440000u;
            uint8_t *s = at(esp, 8);
            if (s) { memcpy(s, &ret, 4); memcpy(s + 4, &task, 4); }
            const uint32_t data2 = notask ? 0u : cp;
            memcpy(at(task + 0x40u, 4), &data2, 4);
            gpr[SA2_ESP] = esp;
            gpr[SA2_EAX] = 0xDEADBEEFu;
            seen = 0xFFFFFFFFu;
            int token;
            if (wraps[d](&token, pcs[d], &token) != &token) return 3;
            if (gpr[SA2_EAX] != 0x00C0FFEEu) return 4;
            printf("%d seen 0x%08X after 0x%08X esp %u\n", d, seen, u32(cp + 0x24u), gpr[SA2_ESP] - esp);
        } else if (strcmp(argv[i], "up") == 0 && i + 1 < argc) {
            const uint32_t v = (uint32_t)strtoul(argv[++i], NULL, 0);
            memcpy(at(0x00100124u, 4), &v, 4);
        } else if (strcmp(argv[i], "change") == 0 && i + 1 < argc) {
            change = (uint32_t)strtoul(argv[++i], NULL, 0);
        } else if (strcmp(argv[i], "unreadable") == 0) {
            esp = 0x00900000u;
        } else if (strcmp(argv[i], "notask") == 0) {
            notask = 1;
        } else if (strcmp(argv[i], "readonly") == 0) {
            readonly = 1;
        } else {
            return 2;
        }
    }
    printf("reals %u writes %u\n", reals, writes);
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
    d = tmp_path_factory.mktemp("upgrades")
    (d / "driver.c").write_text(DRIVER)
    out = d / "upgrades"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "hide-upgrade-models.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *args: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout.splitlines(), p.stderr


ON = {"SA2_TWEAKS": "hide-upgrade-models"}
LATE = MODELS | GUNS | NO_MODEL               # every upgrade the story gives
assert LATE == 0x1FFFFFFF
ALL_CALLS = [x for d in range(5) for x in ("call", str(d))]


def hidden_line(d: int, up: int) -> str:
    return f"{SAY}{DISPLAYS[d]} draws without upgrades 0x{up & MODELS:08X} (Upgrades 0x{up:08X})"


def test_the_mask_is_the_union_of_the_bits_the_displays_hide():
    union = 0
    for bits in OWN.values():
        assert not union & bits
        union |= bits
    assert union == MODELS and not MODELS & (NO_MODEL | GUNS)


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_each_body_sees_the_players_own_upgrades_and_nothing_is_written(exe, env):
    out, err = run(exe, "up", hex(LATE), *ALL_CALLS, **env)
    assert out == [f"{d} seen 0x{LATE:08X} after 0x{LATE:08X} esp 4" for d in range(5)] + ["reals 5 writes 0"]
    assert "upgrade models" not in err


def test_on_each_body_sees_no_model_bit_and_the_dword_comes_back(exe):
    out, err = run(exe, "up", hex(LATE), *ALL_CALLS, *ALL_CALLS, **ON)
    kept = LATE & ~MODELS
    assert kept == NO_MODEL | GUNS == 0x116AC3A2
    assert out == [f"{d} seen 0x{kept:08X} after 0x{LATE:08X} esp 4" for d in range(5)] * 2 + [
        "reals 10 writes 20"]
    assert err.splitlines() == ["sa2 tweaks: upgrade models hidden (SA2_TWEAKS=hide-upgrade-models)"] + [
        hidden_line(d, LATE) for d in range(5)]


@pytest.mark.parametrize("d", range(5))
def test_each_displays_own_upgrades_are_hidden_from_it(exe, d):
    own = OWN[DISPLAYS[d]]
    out, _ = run(exe, "up", hex(own | 0x8000), "call", str(d), **ON)
    assert out == [f"{d} seen 0x00008000 after 0x{own | 0x8000:08X} esp 4", "reals 1 writes 2"]


@pytest.mark.parametrize("d,guns", [(3, 0x200000 | 0x400000), (4, 0x80 | 0x100)])
def test_a_walkers_guns_stay_drawn_and_only_its_other_upgrades_hide(exe, d, guns):
    """The walker displays save where the shots start from the gun they draw, so the guns' bits
    reach the body as the player has them; a walker with only guns costs the tweak no write."""
    out, _ = run(exe, "up", hex(guns), "call", str(d), **ON)
    assert out == [f"{d} seen 0x{guns:08X} after 0x{guns:08X} esp 4", "reals 1 writes 0"]
    up = guns | OWN[DISPLAYS[d]]
    out, err = run(exe, "up", hex(up), "call", str(d), **ON)
    assert out == [f"{d} seen 0x{guns:08X} after 0x{up:08X} esp 4", "reals 1 writes 2"]
    assert err.splitlines()[1:] == [hidden_line(d, up)]


def test_no_model_bit_no_write_and_no_line(exe):
    """Super Sonic, the Ancient Lights and the Mystic Melodies are left alone, and a player with
    no model upgrade costs the tweak no write."""
    out, err = run(exe, "up", hex(NO_MODEL), *ALL_CALLS, **ON)
    assert out == [f"{d} seen 0x{NO_MODEL:08X} after 0x{NO_MODEL:08X} esp 4" for d in range(5)] + [
        "reals 5 writes 0"]
    assert err == "sa2 tweaks: upgrade models hidden (SA2_TWEAKS=hide-upgrade-models)\n"


def test_ignored_in_a_replay(exe):
    out, err = run(exe, "up", hex(LATE), *ALL_CALLS, SA2_REPLAY_TICKS="/dev/null", **ON)
    assert out == [f"{d} seen 0x{LATE:08X} after 0x{LATE:08X} esp 4" for d in range(5)] + ["reals 5 writes 0"]
    assert "ignored in a replay" in err and SAY not in err


def test_a_change_the_body_makes_is_kept_and_said(exe):
    out, err = run(exe, "up", "0x15", "change", "0x20", "call", "0", "call", "0", **ON)
    assert out == ["0 seen 0x00000000 after 0x00000035 esp 4", "0 seen 0x00000020 after 0x00000035 esp 4",
                   "reals 2 writes 4"]
    assert err.splitlines()[1:] == [
        hidden_line(0, 0x15),
        f"{SAY}Sonic_Display changed Upgrades while drawing (to 0x00000020); its change is kept and the "
        "hidden bits put back"]


def test_an_unreadable_stack_or_task_draws_as_the_game_has_it_and_says_so(exe):
    out, err = run(exe, "up", "0x15", "unreadable", "call", "0", "call", "0", **ON)
    assert out == ["0 seen 0xFFFFFFFF after 0x00000015 esp 4"] * 2 + ["reals 2 writes 0"]
    assert err.splitlines()[1:] == [
        f"{SAY}Sonic_Display draws as the game has it: its player's Upgrades do not read (the guest stack "
        "at 0x00900000)"]
    out, err = run(exe, "up", "0x15", "notask", "call", "1", **ON)
    assert out == ["1 seen 0xFFFFFFFF after 0x00000015 esp 4", "reals 1 writes 0"]
    assert err.splitlines()[1:] == [
        f"{SAY}Knuckles_Display draws as the game has it: its player's Upgrades do not read (the guest "
        "stack at 0x00020100)"]


def test_an_unwritable_dword_draws_as_the_game_has_it_and_says_so(exe):
    out, err = run(exe, "up", "0x400", "readonly", "call", "1", "call", "1", **ON)
    assert out == ["1 seen 0x00000400 after 0x00000400 esp 4"] * 2 + ["reals 2 writes 0"]
    assert err.splitlines()[1:] == [
        f"{SAY}Knuckles_Display draws as the game has it: Upgrades at 0x00100124 does not write"]


# The image's own bytes (private/exe/sonic2app-unpacked.exe): the facts the wraps rest on. Without
# the image these cases are skipped by name; the cases above still run.
IMAGE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
# Each wrapped display's first byte and the byte after its last RET (int3 padding follows, or for
# Sonic_Display a NOP before its jump table at 0x00720B28).
SPAN = {"Sonic_Display": (0x00720090, 0x00720B27), "Knuckles_Display": (0x0072EF20, 0x0072F689),
        "Rouge_Display": (0x00730970, 0x007311D3), "MechEggman_Display": (0x007444F0, 0x00745302),
        "MechTails_Display": (0x00747DD0, 0x00748AEF)}
# The header's list: each test of the Upgrades dword (+0x24 of the CharObj2Base the body holds).
TESTS = [
    ("Sonic_Display", 0x007207CC, "8b4324a908000400", 0x40008),     # mov eax,[ebx+24h]; test eax,40008h
    ("Sonic_Display", 0x007208C1, "f6432410", 0x10),
    ("Sonic_Display", 0x007209C0, "f6432404", 0x4),
    ("Sonic_Display", 0x007209EA, "f6432401", 0x1),
    ("Sonic_Display", 0x00720A34, "f7432400000100", 0x10000),
    ("Knuckles_Display", 0x0072F4CD, "f7452400080000", 0x800),
    ("Knuckles_Display", 0x0072F528, "f7452400200000", 0x2000),
    ("Knuckles_Display", 0x0072F564, "f7452400140000", 0x1400),
    ("Rouge_Display", 0x00730FEF, "f7452400000004", 0x4000000),
    ("Rouge_Display", 0x0073104B, "f7452400000008", 0x8000000),
    ("Rouge_Display", 0x007310B8, "f7452400000002", 0x2000000),
    ("Rouge_Display", 0x007310FF, "f7452400000002", 0x2000000),
    ("MechEggman_Display", 0x0074490B, "f7412400004000", 0x400000),
    ("MechEggman_Display", 0x00744DF3, "f7402400002000", 0x200000),
    ("MechEggman_Display", 0x00744FD1, "f7472400001000", 0x100000),
    ("MechEggman_Display", 0x00745112, "f7472400008000", 0x800000),
    ("MechTails_Display", 0x0074815F, "f7412400010000", 0x100),
    ("MechTails_Display", 0x0074860E, "f6402480", 0x80),
    ("MechTails_Display", 0x007488CF, "f6422440", 0x40),
]
# Where a walker saves its shot points, each after the gun test that picks the model it reads them
# from: the Laser Blaster's gun walk (a cmp with the muzzle node, then lea edx,[esi+23Ch]) and the
# Large Cannon's or Bazooka's end node (lea edx,[esi+248h] and [esi+2E4h]). The tweak keeps these
# bits so the points are the ones the game saves for the player's own upgrades.
SHOT_POINTS = {
    "MechEggman_Display": [(0x0074490B, 0x00744B3D, "3b0d2c9ede01"), (0x0074490B, 0x00744BDC, "8d963c020000"),
                           (0x00744DF3, 0x00744E38, "8b355c9ede01"), (0x00744DF3, 0x00744F0B, "8d9648020000"),
                           (0x00744DF3, 0x00744F2B, "8d96e4020000")],
    "MechTails_Display": [(0x0074815F, 0x00748375, "3b0d9c9fde01"), (0x0074815F, 0x007483F8, "8d963c020000"),
                          (0x0074860E, 0x00748807, "8d9648020000"), (0x0074860E, 0x00748827, "8d96e4020000")],
}


@pytest.fixture(scope="module")
def text() -> tuple[int, bytes]:
    """The image's .text: its first address and its file-backed bytes."""
    if not IMAGE.is_file():
        pytest.skip(f"the unpacked image is not present at {IMAGE}, so its code bytes are not available")
    import pefile
    pe = pefile.PE(str(IMAGE), fast_load=True)
    for s in pe.sections:
        if s.Name.rstrip(b"\0") == b".text":
            return pe.OPTIONAL_HEADER.ImageBase + s.VirtualAddress, s.get_data()
    pytest.fail(f"{IMAGE} has no .text section")


def code(text: tuple[int, bytes], va: int, n: int) -> bytes:
    lo, data = text
    assert lo <= va and va + n <= lo + len(data), f"0x{va:08X} is outside .text"
    return data[va - lo:va - lo + n]


def test_the_upgrade_tests_the_header_lists_are_in_the_image_inside_their_display(text):
    union = 0
    for display, va, raw, bit in TESTS:
        want = bytes.fromhex(raw)
        assert code(text, va, len(want)) == want, (display, hex(va))
        lo, hi = SPAN[display]
        assert lo <= va < hi, (display, hex(va))
        assert int.from_bytes(want[-4:] if want[0] != 0xF6 else want[-1:], "little") == bit
        union |= bit
    assert union == MODELS | GUNS


def test_each_wrapped_display_ends_in_a_ret_before_padding(text):
    for display, (lo, hi) in SPAN.items():
        want = b"\xc3\x90" if display == "Sonic_Display" else b"\xc3\xcc"
        assert code(text, hi - 1, 2) == want, display


@pytest.mark.parametrize("display", sorted(SHOT_POINTS))
def test_a_walker_saves_its_shot_points_after_the_gun_tests_the_tweak_keeps(text, display):
    """Each shot point is saved inside the display, after the test of a bit the tweak keeps: hiding
    that model would save the plain model's point instead and move where the shots start."""
    lo, hi = SPAN[display]
    for test_va, va, raw in SHOT_POINTS[display]:
        want = bytes.fromhex(raw)
        assert code(text, va, len(want)) == want, (display, hex(va))
        assert lo <= test_va < va < hi, (display, hex(va))
        bit = next(b for d, t, _, b in TESTS if d == display and t == test_va)
        assert bit & GUNS and not bit & MODELS, (display, hex(test_va))
