"""The hide-hud tweak (runtime/tweaks/hide-hud.c): the score window's call (0x0044E9C0) taken as its
own byte-0 path takes it, with the HUD's x origin O (0x01A559E4) written as the window's layout
would write it, and the kart HUD's three calls skipped as Codes.lst's NOPs skip them (0x0061BAE0
from 0x0061B120, O written as the kart time window would; 0x0061B1D0 from 0x0061B7D3 and
0x0061B87E).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the lifted functions, the
runtime's peek, poke and register accessors, and runtime/sa2_widescreen.c's sa2_hud43_hud_h (the H
the HUD's layout reads; a case sets what it answers). Each stand-in `__real_` does what its lifted
body does to what the wrap can see: it counts the call, and the two kart callees end in the RET
that pops the return address (EIP = [ESP], ESP + 4) after setting EAX, ECX and EDX, which the
real bodies clobber. Each call puts the caller's return address at guest [ESP] first, as the
lifted CALL does. The score window's entry is called as runtime/sa2_widescreen.c's wrap calls it:
1 means the tweak took the call, 0 that the lifted body runs. The score window and its O through
the real widescreen wrap, with SA2_PLAY_HUD43 on and off, are test_widescreen.py's hide-hud cases.
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")

SCORE = "0x0043D274"                      # after the stage loop's call of 0x0044E9C0 at 0x0043D26F
LAYOUT = "0x0061B125"                     # after 0x0061B060's call of 0x0061BAE0 at 0x0061B120
TIMEEXT_A, TIMEEXT_B = "0x0061B7D8", "0x0061B883"   # after the timeExt display's two calls
OTHER = "0x0061B8D9"                      # after another caller of 0x0061B1D0 (0x0061B8D4)
O_START = "0xC0DEC0DE"                    # what O holds before a case (not a float the game writes)

# argv: commands, each printing one line.
#   res <H> <V>            : guest H and V (floats); prints nothing
#   hudh <H>|-             : what sa2_hud43_hud_h answers (a fixed H, or the H it is given)
#   switch <afcc> <afce>   : the score window's switches; prints nothing
#   score <ret>|unreadable : prints "score <answer> <EIP> <ESP moved> <EAX> <ECX> <EDX> <O bits>"
#   layout <ret> | element <ret> | ... unreadable : prints "<callee> <real calls> <EIP>
#       <ESP moved> <EAX> <ECX> <EDX> <the stack word at the entry ESP> <O bits>"
#   lock <what>            : the guest memory `what` (switch, res, origin) stops reading and writing
#   origin <H> <V>         : prints sa2_hide_hud_origin(H, V)'s bits
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define STACK 0x20000u
static uint8_t stack[0x100], afcc = 1, afce = 1;
static float res_h = 640.0f, res_v = 480.0f;
static uint32_t origin = 0xC0DEC0DEu;
static int lock_switch, lock_res, lock_origin;
static int hudh_fixed;
static float hudh_value;
static uint32_t gpr[SA2_GPR_COUNT];
static int n_layout, n_element;

int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a == 0x0174AFCCu && n == 1 && !lock_switch) { *(uint8_t *)out = afcc; return SA2_OK; }
    if (a == 0x0174AFCEu && n == 1 && !lock_switch) { *(uint8_t *)out = afce; return SA2_OK; }
    if (a == 0x0174BA64u && n == 4 && !lock_res) { memcpy(out, &res_h, 4); return SA2_OK; }
    if (a == 0x0174BA68u && n == 4 && !lock_res) { memcpy(out, &res_v, 4); return SA2_OK; }
    if (a == 0x01A559E4u && n == 4 && !lock_origin) { memcpy(out, &origin, 4); return SA2_OK; }
    if (a < STACK || a + n > STACK + sizeof stack) return 1;
    memcpy(out, stack + (a - STACK), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (a == 0x01A559E4u && n == 4 && !lock_origin) { memcpy(&origin, in, 4); return SA2_OK; }
    return 1;      /* the switches, H and V are never written */
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
uint32_t sa2_hud43_hud_h(uint32_t h_bits, uint32_t v_bits) {
    (void)v_bits;
    if (!hudh_fixed) return h_bits;
    uint32_t b;
    memcpy(&b, &hudh_value, 4);
    return b;
}

static void ret(void) {
    uint32_t r = 0;
    if (gpr[SA2_ESP] >= STACK && gpr[SA2_ESP] + 4 <= STACK + sizeof stack)
        memcpy(&r, stack + (gpr[SA2_ESP] - STACK), 4);
    gpr[SA2_EIP] = r;
    gpr[SA2_ESP] += 4;
}
static void *kart(int *n, void *memory) {
    (*n)++;
    gpr[SA2_EAX] = 0x11111111u; gpr[SA2_ECX] = 0x22222222u; gpr[SA2_EDX] = 0x33333333u;
    ret();
    return memory;
}
void *__real_sub_61bae0(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; return kart(&n_layout, memory); }
void *__real_sub_61b1d0(void *state, uint32_t pc, void *memory) { (void)state; (void)pc; return kart(&n_element, memory); }
void *__wrap_sub_61bae0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_61b1d0(void *state, uint32_t pc, void *memory);
int sa2_tweak_hide_hud_score_window(void *state);
float sa2_hide_hud_origin(float h, float v);

static void call(uint32_t retaddr, int readable) {
    memset(gpr, 0, sizeof gpr);
    gpr[SA2_ESP] = readable ? STACK + 0x80u : 0x90000u;
    if (readable) memcpy(stack + 0x80, &retaddr, 4);
    gpr[SA2_EAX] = 0xA0A0A0A0u; gpr[SA2_ECX] = 0xC0C0C0C0u; gpr[SA2_EDX] = 0xD0D0D0D0u;
    gpr[SA2_EIP] = 0xEEEEEEEEu;
}

int main(int argc, char **argv) {
    int token;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "res")) {
            res_h = strtof(argv[i + 1], NULL); res_v = strtof(argv[i + 2], NULL); i += 2;
        } else if (!strcmp(argv[i], "hudh")) {
            const char *h = argv[++i];
            hudh_fixed = strcmp(h, "-") != 0;
            if (hudh_fixed) hudh_value = strtof(h, NULL);
        } else if (!strcmp(argv[i], "switch")) {
            afcc = (uint8_t)strtoul(argv[i + 1], NULL, 0); afce = (uint8_t)strtoul(argv[i + 2], NULL, 0); i += 2;
        } else if (!strcmp(argv[i], "lock")) {
            const char *w = argv[++i];
            if (!strcmp(w, "switch")) lock_switch = 1;
            else if (!strcmp(w, "res")) lock_res = 1;
            else if (!strcmp(w, "origin")) lock_origin = 1;
            else return 5;
        } else if (!strcmp(argv[i], "origin")) {
            const float o = sa2_hide_hud_origin(strtof(argv[i + 1], NULL), strtof(argv[i + 2], NULL));
            uint32_t b;
            memcpy(&b, &o, 4);
            printf("origin 0x%08X\n", b);
            i += 2;
        } else if (!strcmp(argv[i], "score")) {
            const char *r = argv[++i];
            const int readable = strcmp(r, "unreadable") != 0;
            call(readable ? (uint32_t)strtoul(r, NULL, 0) : 0, readable);
            const uint32_t esp = gpr[SA2_ESP];
            const int took = sa2_tweak_hide_hud_score_window(&token);
            printf("score %d 0x%08X %u 0x%08X 0x%08X 0x%08X 0x%08X\n", took, gpr[SA2_EIP],
                   gpr[SA2_ESP] - esp, gpr[SA2_EAX], gpr[SA2_ECX], gpr[SA2_EDX], origin);
        } else {
            const int layout = !strcmp(argv[i], "layout");
            const char *r = argv[++i];
            const int readable = strcmp(r, "unreadable") != 0;
            call(readable ? (uint32_t)strtoul(r, NULL, 0) : 0, readable);
            const uint32_t esp = gpr[SA2_ESP];
            void *m = layout ? __wrap_sub_61bae0(&token, 0x0061BAE0u, &token)
                             : __wrap_sub_61b1d0(&token, 0x0061B1D0u, &token);
            if (m != &token) return 3;
            uint32_t top = 0;
            if (readable) memcpy(&top, stack + 0x80, 4);
            printf("%s %d 0x%08X %u 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X\n", layout ? "layout" : "element",
                   layout ? n_layout : n_element, gpr[SA2_EIP], gpr[SA2_ESP] - esp, gpr[SA2_EAX],
                   gpr[SA2_ECX], gpr[SA2_EDX], top, origin);
        }
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
    d = tmp_path_factory.mktemp("hide_hud")
    (d / "driver.c").write_text(DRIVER)
    out = d / "hud"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "hide-hud.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *cmds: str, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *cmds], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr.splitlines()


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def guest_origin(h: float, v: float) -> str:
    """O as the guest's x87 code at PC24 computes it (0x0044E146-0x0044E1A0): every step rounded
    to float, which for float operands is the double result rounded once."""
    h, v = f32(h), f32(v)
    k = f32(f32(h / 640) / f32(v / 480))
    o = f32(40 - f32(f32(k - 1) * 276))
    return "0x%08X" % struct.unpack("<I", struct.pack("<f", o))[0]


def word(x: str) -> str:
    return f"0x{int(x, 16):08X}"


def lifted(callee: str, n: int, ret: str, o: str = O_START) -> str:
    """What the lifted body leaves: its registers, its RET to `ret`."""
    return f"{callee} {n} {word(ret)} 4 0x11111111 0x22222222 0x33333333 {word(ret)} {o}"


def skipped(callee: str, n: int, ret: str, o: str = O_START) -> str:
    """A NOP's view: the RET alone, every register as the caller left it, the body not run."""
    return f"{callee} {n} {word(ret)} 4 0xA0A0A0A0 0xC0C0C0C0 0xD0D0D0D0 {word(ret)} {o}"


def score(took: int, o: str = O_START, ret: str = SCORE) -> str:
    """The score window's entry: 1 and the RET alone, or 0 and nothing moved for the body to run."""
    if took:
        return f"score 1 {word(ret)} 4 0xA0A0A0A0 0xC0C0C0C0 0xD0D0D0D0 {o}"
    return f"score 0 0xEEEEEEEE 0 0xA0A0A0A0 0xC0C0C0C0 0xD0D0D0D0 {o}"


O_43 = "0x42200000"            # 40.0: k = 1
O_2340 = "0xC3048000"          # -132.5 at 2340x1080: k = 1.625

ALL = ("res", "2340", "1080", "score", SCORE, "layout", LAYOUT, "element", TIMEEXT_A,
       "element", OTHER, "element", TIMEEXT_B, "layout", "0x0061B999")


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_TWEAKS": "hide-hud", "SA2_REPLAY_TICKS": "/dev/null"}])
def test_off_is_the_lifted_code_alone(exe, env):
    """Off (unset, empty, another tweak, or in a replay): the score window's body runs, every kart
    call reaches the lifted body, O is not written, and the tweak says nothing of its own."""
    out, err = run(exe, *ALL, **env)
    assert out == [score(0), lifted("layout", 1, LAYOUT), lifted("element", 1, TIMEEXT_A),
                   lifted("element", 2, OTHER), lifted("element", 3, TIMEEXT_B),
                   lifted("layout", 2, "0x0061B999")]
    assert not [line for line in err if line.startswith("sa2 tweaks: hud hidden")]


def test_on_hides_the_score_window_and_the_kart_hud_and_keeps_the_origin(exe):
    out, err = run(exe, *ALL, SA2_TWEAKS="hide-hud")
    assert out == [
        score(1, O_2340),                       # the RET alone; O as 0x0044E080 writes it
        skipped("layout", 0, LAYOUT, O_2340),   # the layout task is never loaded; O as 0x0044E350
        skipped("element", 0, TIMEEXT_A, O_2340),
        lifted("element", 1, OTHER, O_2340),    # its other callers pass through
        skipped("element", 1, TIMEEXT_B, O_2340),
        lifted("layout", 1, "0x0061B999", O_2340),   # an unknown caller: left alone, and said
    ]
    assert err == [
        "sa2 tweaks: hud hidden (SA2_TWEAKS=hide-hud)",
        "sa2 tweaks: hud hidden: score window not drawn (x origin -132.5 written as 0x0044E080 would)",
        "sa2 tweaks: hud hidden: kart layout not loaded (call at 0x0061B120 skipped; x origin -132.5 "
        "written as 0x0044E350 would)",
        "sa2 tweaks: hud hidden: kart timeExt element not drawn (call at 0x0061B7D3 skipped)",
        "sa2 tweaks: hud hidden: kart timeExt element not drawn (call at 0x0061B87E skipped)",
        "sa2 tweaks: hud hidden: 0x0061BAE0 called from 0x0061B999, not 0x0061B125, so the kart "
        "layout is left alone",
    ]


@pytest.mark.parametrize("h, v, o", [
    (640, 480, O_43), (1440, 1080, O_43), (1920, 1440, O_43),
    (2340, 1080, O_2340), (3120, 1440, O_2340),
    (2560, 1080, "0xC32EAAAB"),                 # -174.66667
    (1280, 720, "0xC2500002"),                  # -52.000008: k - 1 times 276 rounded to float first
    (1920, 1080, "0xC2500002"),
])
def test_the_origin_is_the_guests_arithmetic(exe, h, v, o):
    """After a hidden frame O holds what 0x0044E080 stores at this H and V (40 at 4:3)."""
    assert guest_origin(h, v) == o
    out, _ = run(exe, "origin", str(h), str(v), "res", str(h), str(v), "score", SCORE,
                 SA2_TWEAKS="hide-hud")
    assert out == [f"origin {o}", score(1, o)]


def test_the_origin_follows_the_hud_layouts_width(exe):
    """Under SA2_PLAY_HUD43 the layout reads the 4:3 H (sa2_hud43_hud_h), and so does O: 40 at
    2340x1080, where the real width's O would be -132.5; the kart's write the same."""
    out, err = run(exe, "res", "2340", "1080", "hudh", "1440", "score", SCORE, "switch", "1", "1",
                   "res", "2340", "1080", "layout", LAYOUT, SA2_TWEAKS="hide-hud")
    assert out == [score(1, O_43), skipped("layout", 0, LAYOUT, O_43)]
    assert "sa2 tweaks: hud hidden: score window not drawn (x origin 40 written as 0x0044E080 would)" in err


def test_the_games_own_0_runs_the_body_and_leaves_the_origin(exe):
    """The game's own 0 (an event, the Chao World, the kart): the lifted body runs and draws
    nothing, O keeps what it held, and nothing is said."""
    out, err = run(exe, "switch", "0", "1", "score", SCORE, SA2_TWEAKS="hide-hud")
    assert out == [score(0)]
    assert err == ["sa2 tweaks: hud hidden (SA2_TWEAKS=hide-hud)"]


def test_a_window_without_its_layout_leaves_the_origin(exe):
    """0x0174AFCE not 1: the window would not call 0x0044E080, so O is not written; still hidden."""
    out, err = run(exe, "res", "2340", "1080", "switch", "1", "0", "score", SCORE, SA2_TWEAKS="hide-hud")
    assert out == [score(1)]
    assert err[1:] == ["sa2 tweaks: hud hidden: score window not drawn (its layout off, 0x0174AFCE = "
                       "0, so the x origin is left as it is)"]


def test_each_line_is_said_once(exe):
    out, err = run(exe, "score", SCORE, "score", SCORE, "layout", LAYOUT, "layout", LAYOUT,
                   "element", TIMEEXT_A, "element", TIMEEXT_A, SA2_TWEAKS="hide-hud")
    assert out == [score(1, O_43), score(1, O_43), skipped("layout", 0, LAYOUT, O_43),
                   skipped("layout", 0, LAYOUT, O_43), skipped("element", 0, TIMEEXT_A, O_43),
                   skipped("element", 0, TIMEEXT_A, O_43)]
    assert len(err) == 4


def test_unreadable_guest_memory_is_said_and_the_call_goes_through(exe):
    out, err = run(exe, "score", "unreadable", "layout", "unreadable", "element", "unreadable",
                   SA2_TWEAKS="hide-hud")
    assert out[0] == "score 0 0xEEEEEEEE 0 0xA0A0A0A0 0xC0C0C0C0 0xD0D0D0D0 " + O_START
    assert out[1].startswith("layout 1 ") and out[2].startswith("element 1 ")
    assert err[1:] == [
        "sa2 tweaks: hud hidden: the guest stack at 0x00090000 does not read, so the caller of "
        "0x0044E9C0 is unknown and the call goes through",
    ]


@pytest.mark.parametrize("lock, line", [
    ("switch", "sa2 tweaks: hud hidden: the score window's switches at 0x0174AFCC do not read, so "
               "the score window is drawn"),
    ("res", "sa2 tweaks: hud hidden: the resolution at 0x0174BA64 does not read, so the HUD's x "
            "origin cannot be written as 0x0044E080 would and 0x0044E080 is drawn"),
    ("origin", "sa2 tweaks: hud hidden: the HUD's x origin at 0x01A559E4 does not write, so "
               "0x0044E080 is drawn"),
])
def test_an_origin_it_cannot_keep_leaves_the_window_drawn_and_says_so(exe, lock, line):
    """Better the window drawn than a kept element in the wrong place: said once, by name."""
    out, err = run(exe, "lock", lock, "score", SCORE, "score", SCORE, SA2_TWEAKS="hide-hud")
    o = O_START if lock == "origin" else "0x%08X" % 0xC0DEC0DE
    assert out == [score(0, o), score(0, o)]
    assert err[1:] == [line]


def test_a_kart_origin_it_cannot_write_loads_the_layout_and_says_so(exe):
    out, err = run(exe, "lock", "origin", "layout", LAYOUT, SA2_TWEAKS="hide-hud")
    assert out == [lifted("layout", 1, LAYOUT)]
    assert err[1:] == ["sa2 tweaks: hud hidden: the HUD's x origin at 0x01A559E4 does not write, so "
                       "the kart layout is drawn"]
