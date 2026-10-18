"""The action-buttons tweak (runtime/tweaks/action-buttons.c): the light dash on Y, in
Sonic_CheckActionWindow (0x007230E0), and X held starting the spin dash at once, in
Sonic_ChecksForDamage (0x00719920).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the two lifted bodies,
Sonic_PerformLightDash (0x00724010) and the runtime's peek, poke, register and trap entries. The
guest memory holds Controllers (0x01DEFC00, 0x34 bytes a player), Action_Pressed (0x01DEFB00),
Action_Held (0x01DEFBF0), an EntityData1, a CharObj2Base that is also the SonicCharObj2 (the
window at +4, its count at +0x0C, the pick at +0x0D, the action taken at +0x0E, the hold word at
+0x368) and a stack at 0x20000.

The stand-in `__real_sub_7230e0` is the body's decision as the image has it: with Action_Pressed
clear it returns 0 when B or X is held, and 1 (the light attack, 0x00724070) when the first action
is 60; with it set it takes the pick when it is in the window, else the first action, stores it at
+0x0E and, for 59, pushes 0x00723194 and calls the stand-in Sonic_PerformLightDash, then returns 1;
action 22 stands for the other arms (clears the hold, returns 1); any other returns 0. The
stand-in Sonic_PerformLightDash notes its registers, sets the action byte to 59, clobbers ECX, EDX,
EBX and ESI and RETs. The stand-in `__real_sub_719920`, when it is told the somersault is reached,
does the somersault's charge path: Action_Pressed writes 1 to the hold; Action_Held with the hold
1 or more adds 1 and, past the compare (24, or 10 as spin-dash-fast's variant has it), sets the
action to 3 (CharID2 0 or 1) and clears the hold; it notes the hold it read.

Each argument is one step:

  pad:P:ON:PRESS        Controllers[P]'s held buttons and press edges (B 0x2, Y 0x200, X 0x400)
  flags:P:HELD:PRESSED  Action_Held[P] and Action_Pressed[P]
  win:P:C:N:ITEMS:PICK  the window: player byte P, CharID2 C, count N, the actions (comma list,
                        - for none) and the pick
  hold:N                the hold word
  reach:R past:N        whether the ChecksForDamage stand-in reaches the somersault; its compare
  fail:A                guest address A fails to read and write
  aw                    the action window's call from 0x00719B44 (return address 0x00719B49)
  dmg                   Sonic_Main's call of Sonic_ChecksForDamage (return address 0x00717918)
  via                   sa2_action_buttons_window with another tweak's `real` (it counts, then
                        calls the stand-in body)

After each call the driver prints "key=value" fields: EAX, EIP, how far ESP moved, the bodies run,
the light dashes, the registers the light dash saw (ok when EAX the SonicCharObj2, EDX the
CharObj2Base, ECX the EntityData1 and the sentinel return address), the action byte, +0x0E, the
hold, the hold the somersault read, and whether EBX, EBP, ESI and EDI are the caller's.
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "AB_BAD_RET")

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_trap.h"

#define PADS    0x01DEFC00u
#define PRESSED 0x01DEFB00u
#define HELD    0x01DEFBF0u
#define DATA1   0x0A000000u
#define CO2     0x0A001000u
#define DATA2   0x0A002000u
#define STACK   0x20000u
#define AW_RET  0x00719B49u
#define DMG_RET 0x00717918u

static uint8_t pads[4 * 0x34], pressed[4], held[4], data1[0x40], co2[0x400], stack[0x200];
static uint32_t fail = 1, gpr[SA2_GPR_COUNT];
static unsigned real_calls, dashes, via_calls;
static int dash_regs = -1, seen = -1, reach, past = 24;

static void *where(uint32_t a, uint32_t n) {
    if (fail != 1 && a <= fail && fail < a + n) return NULL;
    if (a >= PADS && a + n <= PADS + sizeof pads) return pads + (a - PADS);
    if (a >= PRESSED && a + n <= PRESSED + 4) return pressed + (a - PRESSED);
    if (a >= HELD && a + n <= HELD + 4) return held + (a - HELD);
    if (a >= DATA1 && a + n <= DATA1 + sizeof data1) return data1 + (a - DATA1);
    if (a >= CO2 && a + n <= CO2 + sizeof co2) return co2 + (a - CO2);
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
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
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
void sa2_trap_note(const char *fmt, ...) { fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}

static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static int16_t hold_word(void) { int16_t v; memcpy(&v, co2 + 0x368, 2); return v; }
static void set_hold(int16_t v) { memcpy(co2 + 0x368, &v, 2); }
static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }

/* Sonic_PerformLightDash: EAX the SonicCharObj2, EDX the CharObj2Base, ECX the EntityData1. */
void *sub_724010(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    dashes++;
    dash_regs = gpr[SA2_EAX] == CO2 && gpr[SA2_EDX] == CO2 && gpr[SA2_ECX] == DATA1 &&
                (rd32(gpr[SA2_ESP]) == SA2_CALL_SENTINEL || rd32(gpr[SA2_ESP]) == 0x00723194u);
    data1[0] = 59;
    gpr[SA2_ECX] = gpr[SA2_EDX] = gpr[SA2_EBX] = gpr[SA2_ESI] = 0x5A5A5A5Au;
    if (getenv("AB_BAD_RET")) { gpr[SA2_EIP] = 0x00724066u; return m; }
    ret_();
    return m;
}

/* Sonic_CheckActionWindow, as the image decides. */
void *__real_sub_7230e0(void *s, uint32_t pc, void *m) {
    real_calls++;
    const int p = (int8_t)co2[0], count = (int8_t)co2[0x0C];
    uint32_t eax = 0;
    if (!pressed[p]) {
        uint32_t on = 0;
        memcpy(&on, pads + 0x34 * p + 8, 4);
        if (!(on & 0x402) && count > 0 && (int8_t)co2[4] == 60) eax = 1;
    } else if (count != 0) {
        int i = 0;
        while (i < count && (int8_t)co2[4 + i] != (int8_t)co2[0x0D]) i++;
        const int8_t action = i < count ? (int8_t)co2[0x0D] : (int8_t)co2[4];
        co2[0x0E] = (uint8_t)action;
        if (action == 59) {
            const uint32_t keep_esp = gpr[SA2_ESP];
            gpr[SA2_EDX] = CO2; gpr[SA2_EAX] = rd32(gpr[SA2_ESP] + 4); gpr[SA2_ECX] = DATA1;
            gpr[SA2_ESP] -= 4;
            wr32(gpr[SA2_ESP], 0x00723194u);
            m = sub_724010(s, 0x00724010u, m);
            gpr[SA2_ESP] = keep_esp;
            gpr[SA2_EBX] = 0x33330003u; gpr[SA2_ESI] = 0x33330006u;   /* popped back */
            eax = 1;
        } else if (action == 22) {
            set_hold(0);
            eax = 1;
        }
    }
    (void)pc;
    gpr[SA2_EAX] = eax;
    ret_();
    return m;
}

/* Sonic_ChecksForDamage, with the somersault's charge path when it is reached. */
void *__real_sub_719920(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    real_calls++;
    const int p = (int8_t)co2[0];
    if (reach) {
        if (pressed[p]) set_hold(1);
        else if (held[p]) {
            const int16_t c = hold_word();
            seen = c;
            if (c >= 1) {
                set_hold((int16_t)(c + 1));
                if (c + 1 > past) {
                    if (co2[3] <= 1) data1[0] = 3;
                    set_hold(0);
                }
            }
        } else seen = hold_word();
    }
    ret_();
    return m;
}

void *__wrap_sub_7230e0(void *s, uint32_t pc, void *m);
void *__wrap_sub_719920(void *s, uint32_t pc, void *m);
void *sa2_action_buttons_window(void *s, uint32_t pc, void *m, void *(*real)(void *, uint32_t, void *));

static void *via(void *s, uint32_t pc, void *m) { via_calls++; return __real_sub_7230e0(s, pc, m); }

int main(int argc, char **argv) {
    int token;
    for (int i = 1; i < argc; i++) {
        char *c = argv[i];
        if (!strncmp(c, "pad:", 4)) {
            int p = 0; unsigned on = 0, press = 0;
            sscanf(c + 4, "%d:%i:%i", &p, &on, &press);
            memcpy(pads + 0x34 * p + 8, &on, 4);
            memcpy(pads + 0x34 * p + 0x10, &press, 4);
            continue;
        }
        if (!strncmp(c, "flags:", 6)) {
            int p = 0, h = 0, pr = 0;
            sscanf(c + 6, "%d:%d:%d", &p, &h, &pr);
            held[p] = (uint8_t)h; pressed[p] = (uint8_t)pr;
            continue;
        }
        if (!strncmp(c, "win:", 4)) {
            int p = 0, ch = 0, n = 0, pick = 0;
            char items[64] = "";
            sscanf(c + 4, "%d:%d:%d:%63[^:]:%d", &p, &ch, &n, items, &pick);
            co2[0] = (uint8_t)p; co2[3] = (uint8_t)ch; co2[0x0C] = (uint8_t)n; co2[0x0D] = (uint8_t)pick;
            memset(co2 + 4, 0, 8);
            co2[0x0E] = 0xEE;
            int k = 0;
            for (char *t = strtok(items, ","); t && k < 8; t = strtok(NULL, ","))
                if (strcmp(t, "-")) co2[4 + k++] = (uint8_t)atoi(t);
            continue;
        }
        if (!strncmp(c, "hold:", 5)) { set_hold((int16_t)atoi(c + 5)); continue; }
        if (!strncmp(c, "reach:", 6)) { reach = atoi(c + 6); continue; }
        if (!strncmp(c, "past:", 5)) { past = atoi(c + 5); continue; }
        if (!strncmp(c, "fail:", 5)) { fail = (uint32_t)strtoul(c + 5, NULL, 0); continue; }
        for (int r = 0; r < SA2_GPR_COUNT; r++) gpr[r] = 0x33330000u + (uint32_t)r;
        const uint32_t esp = STACK + 0x100u;
        gpr[SA2_ESP] = esp;
        gpr[SA2_EIP] = 0xDEAD0001u;
        dash_regs = -1; seen = -1;
        void *back;
        if (!strcmp(c, "dmg")) {
            const uint32_t frame[5] = {DMG_RET, DATA1, DATA2, CO2, CO2};
            memcpy(stack + 0x100, frame, sizeof frame);
            back = __wrap_sub_719920(&token, 0x00719920u, &token);
        } else {
            const uint32_t frame[2] = {AW_RET, CO2};
            memcpy(stack + 0x100, frame, sizeof frame);
            gpr[SA2_EAX] = DATA1; gpr[SA2_ECX] = CO2; gpr[SA2_EDX] = DATA2;
            back = strcmp(c, "via") ? __wrap_sub_7230e0(&token, 0x007230E0u, &token)
                                    : sa2_action_buttons_window(&token, 0x007230E0u, &token, via);
        }
        if (back != &token) return 3;
        const int saved = gpr[SA2_EBX] == 0x33330003u && gpr[SA2_EBP] == 0x33330005u &&
                          gpr[SA2_ESI] == 0x33330006u && gpr[SA2_EDI] == 0x33330007u;
        printf("call=%s eax=%u eip=%08x esp=%+d real=%u dash=%u regs=%d action=%u taken=%d hold=%d "
               "seen=%d saved=%d via=%u\n", c, gpr[SA2_EAX], gpr[SA2_EIP], (int)(gpr[SA2_ESP] - esp),
               real_calls, dashes, dash_regs, data1[0], (int8_t)co2[0x0E], hold_word(), seen, saved,
               via_calls);
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
    d = tmp_path_factory.mktemp("action-buttons")
    (d / "driver.c").write_text(DRIVER)
    out = d / "ab"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "action-buttons.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, rc: int = 0, **env: str) -> tuple[list[dict], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == rc, p.stderr
    rows = [dict(f.split("=", 1) for f in line.split()) for line in p.stdout.splitlines()]
    return rows, [line for line in p.stderr.splitlines()
                  if not line.startswith("sa2 tweaks: Y light dash, X spin dash (")]


ON = {"SA2_TWEAKS": "action-buttons"}
P = "sa2 tweaks: Y light dash, X spin dash: "
B, Y, X = 0x2, 0x200, 0x400
AW_RET, DMG_RET = "00719b49", "00717918"


def pick(row: dict, *keys: str) -> tuple:
    return tuple(row[k] for k in keys)


# The action window's decision table: the window (count, actions, pick), the buttons (held, pressed)
# and Action_Pressed; then what the call does with the tweak off and on: EAX, the bodies run, the
# light dashes and the action taken at +0x0E (-18 is the 0xEE the driver leaves there).
WINDOW = [
    # A light-dash line, B pressed: off the body light-dashes; on no light dash, no body, 0.
    (("1", "59", "59"), (B, B, 1), ("1", "1", "1", "59"), ("0", "0", "0", "59")),
    # 59 first, another action picked that is not offered: the body takes 59 off; on it passes.
    (("2", "59,22", "40"), (X, X, 1), ("1", "1", "1", "59"), ("0", "0", "0", "59")),
    # 59 offered but 22 picked: the body takes 22 off and on.
    (("2", "59,22", "22"), (B, B, 1), ("1", "1", "0", "22"), ("1", "1", "0", "22")),
    # 59 picked but not first: the body takes 59 off; on it passes.
    (("2", "22,59", "59"), (B, B, 1), ("1", "1", "1", "59"), ("0", "0", "0", "59")),
    # Y at a light-dash line: off the body returns 0; on the light dash, without the body.
    (("1", "59", "59"), (Y, Y, 0), ("0", "1", "0", "-18"), ("1", "0", "1", "59")),
    # Y with 59 second and another action picked: on the light dash all the same.
    (("2", "22,59", "22"), (Y, Y, 0), ("0", "1", "0", "-18"), ("1", "0", "1", "59")),
    # Y and B pressed together at a light-dash line: on Y wins.
    (("1", "59", "59"), (Y | B, Y | B, 1), ("1", "1", "1", "59"), ("1", "0", "1", "59")),
    # Y with no light dash offered: the body, off and on.
    (("1", "22", "22"), (Y, Y, 0), ("0", "1", "0", "-18"), ("0", "1", "0", "-18")),
    # Y with an empty window: the body.
    (("0", "-", "0"), (Y, Y, 0), ("0", "1", "0", "-18"), ("0", "1", "0", "-18")),
    # Y held, not pressed, at a light-dash line: the body.
    (("1", "59", "59"), (Y, 0, 0), ("0", "1", "0", "-18"), ("0", "1", "0", "-18")),
    # B pressed with an empty window: the body (0).
    (("0", "-", "0"), (B, B, 1), ("0", "1", "0", "-18"), ("0", "1", "0", "-18")),
    # No press, the light attack offered first: the body (1).
    (("1", "60", "60"), (0, 0, 0), ("1", "1", "0", "-18"), ("1", "1", "0", "-18")),
]


@pytest.mark.parametrize("window, buttons, off, on", WINDOW)
def test_the_action_windows_decisions_off_and_on(exe, window, buttons, off, on):
    n, items, chosen = window
    held, press, flag = buttons
    steps = (f"pad:0:{held}:{press}", f"flags:0:{int(bool(held & 0x402))}:{flag}",
             f"win:0:0:{n}:{items}:{chosen}", "aw")
    keys = ("eax", "real", "dash", "taken")
    for env in ({}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}):
        rows, err = run(exe, *steps, **env)
        assert pick(rows[0], *keys) == off
        assert not any(line.startswith(P) for line in err)
    rows, _ = run(exe, *steps, **ON)
    assert pick(rows[0], *keys) == on
    # Every path returns as the body's `ret` does, with the caller's EBX, EBP, ESI and EDI.
    assert pick(rows[0], "eip", "esp", "saved") == (AW_RET, "+4", "1")


def test_on_the_light_dash_is_called_as_the_body_calls_it_and_says_so(exe):
    rows, err = run(exe, "pad:0:0x200:0x200", "win:0:0:3:22,59,60:22", "aw", **ON)
    assert pick(rows[0], "eax", "dash", "regs", "action", "taken") == ("1", "1", "1", "59", "59")
    assert err == [P + "player 0's Y light-dashes (actions offered 22,59,60)"]


def test_on_b_at_a_light_dash_line_goes_on_and_says_so(exe):
    _, err = run(exe, "pad:1:2:2", "flags:1:1:1", "win:1:0:1:59:59", "aw", **ON)
    assert err == [P + "player 1's B or X at a light-dash line goes on to the somersault"]


def test_on_the_player_byte_picks_the_pad(exe):
    """Player 1's window reads Controllers[1]: player 0's Y does nothing for it."""
    rows, _ = run(exe, "pad:0:0x200:0x200", "win:1:0:1:59:59", "aw",
                  "pad:1:0x200:0x200", "aw", **ON)
    assert [r["dash"] for r in rows] == ["0", "1"]


def test_the_first_16_of_a_kind_are_listed(exe):
    _, err = run(exe, "pad:0:0x200:0x200", "win:0:0:1:59:59", *["aw"] * 18, **ON)
    assert err == [P + "player 0's Y light-dashes (actions offered 59)"] * 16 + \
        [P + "16 light dashes listed; later ones are not"]


def test_a_light_dash_that_does_not_return_to_the_sentinel_stops_the_run(exe):
    _, err = run(exe, "pad:0:0x200:0x200", "win:0:0:1:59:59", "aw", rc=7, AB_BAD_RET="1", **ON)
    assert err[-1] == "trap 1 at 0x00724066"


# The spin dash: X pressed (Action_Pressed: the somersault writes 1), then held frame by frame.
def hold_frames(button: int, frames: int, *extra: str) -> tuple:
    return ("reach:1", *extra, "win:0:0:0:-:0", f"pad:0:{button}:{button}", "flags:0:1:1", "dmg",
            f"pad:0:{button}:0", "flags:0:1:0", *["dmg"] * frames)


def spin_frame(rows: list[dict]) -> int | None:
    """The call after which the action is the spin dash (3): 1 is the first held frame."""
    return next((i for i, r in enumerate(rows) if r["action"] == "3"), None)


@pytest.mark.parametrize("button, past, off, on", [
    (X, 24, 24, 1),        # X: off after the 24-frame charge, on the first held frame after the press
    (B, 24, 24, 24),       # B keeps the usual hold
    (X | B, 24, 24, 24),   # X with B is B's
    (X, 10, 10, 1),        # spin-dash-fast's compare: 10 frames off, still the first held frame on
])
def test_the_spin_dash_starts_on_the_first_held_frame_with_x(exe, button, past, off, on):
    steps = hold_frames(button, 30, f"past:{past}")
    assert spin_frame(run(exe, *steps)[0]) == off
    rows, err = run(exe, *steps, **ON)
    assert spin_frame(rows) == on
    assert all(r["eip"] == DMG_RET and r["esp"] == "+4" and r["saved"] == "1" for r in rows)
    if on == 1:
        assert rows[1]["seen"] == "24" and rows[1]["hold"] == "0"
        assert err == [P + "player 0's held X starts the spin dash at once (hold 1)"]
    else:
        assert err == []


def test_on_x_with_b_latched_by_hold_toggle_keeps_the_usual_hold(exe):
    """hold-toggle latches B alone: B shows held on every frame and X is pressed then held. The
    preset needs X held without B, so the spin dash comes after the usual 24 frames, as off."""
    steps = ("reach:1", "past:24", "win:0:0:0:-:0", f"pad:0:{X | B}:{X}", "flags:0:1:1", "dmg",
             f"pad:0:{X | B}:0", "flags:0:1:0", *["dmg"] * 30)
    assert spin_frame(run(exe, *steps)[0]) == 24
    rows, err = run(exe, *steps, **ON)
    assert spin_frame(rows) == 24
    assert rows[1]["seen"] == "1"
    assert err == []


def test_off_the_hold_counts_up_frame_by_frame(exe):
    rows, _ = run(exe, *hold_frames(X, 3))
    assert [r["hold"] for r in rows] == ["1", "2", "3", "4"]


def test_on_a_tap_still_somersaults_from_the_same_hold(exe):
    """X pressed and let go: the release reads the hold the press left (1), not a preset."""
    rows, _ = run(exe, "reach:1", "win:0:0:0:-:0", "pad:0:0x400:0x400", "flags:0:1:1", "dmg",
                  "pad:0:0:0", "flags:0:0:0", "dmg", **ON)
    assert pick(rows[1], "seen", "hold", "action") == ("1", "1", "0")


def test_on_the_hold_is_put_back_when_the_somersault_is_not_reached(exe):
    rows, err = run(exe, "reach:0", "win:0:0:0:-:0", "hold:3", "pad:0:0x400:0", "flags:0:1:0",
                    "dmg", "dmg", **ON)
    assert [pick(r, "hold", "action", "real") for r in rows] == [("3", "0", "1"), ("3", "0", "2")]
    assert err == []


def test_on_a_hold_another_path_cleared_stays_cleared(exe):
    """The preset is put back only while the word still holds it."""
    rows, _ = run(exe, "reach:1", "win:0:0:0:-:0", "hold:2", "pad:0:0x400:0", "flags:0:0:0",
                  "dmg", **ON)
    assert pick(rows[0], "hold", "seen") == ("2", "2")


@pytest.mark.parametrize("char", ["8", "12"])
def test_on_a_character_with_no_spin_dash_is_left_alone_and_said_once(exe, char):
    steps = ("reach:1", f"win:0:{char}:0:-:0", "pad:0:0x400:0", "flags:0:1:0", "hold:1", "dmg",
             "dmg")
    off, _ = run(exe, *steps)
    rows, err = run(exe, *steps, **ON)
    assert rows == off
    assert [r["hold"] for r in rows] == ["2", "3"]
    assert err == [P + f"character {char} (CharID2) has no spin dash, so its held X is left to "
                   "the game"]


@pytest.mark.parametrize("steps, line", [
    (("fail:0x00020104", "aw"),
     "the guest stack at 0x00020100 or the action window at 0x0A001000 does not read, so the "
     "action window is left to the game"),
    (("fail:0x01DEFC10", "aw"),
     "player 0's buttons do not read, so the action window is left to the game"),
    (("win:5:0:1:59:59", "aw"),
     "a character's player byte is 5, not 0 to 3, so its buttons are left to the game"),
    (("win:0:0:9:59:59", "aw"),
     "an action window holds 9 actions, not 0 to 8, so it is left to the game"),
    (("fail:0x0002010C", "dmg"),
     "the guest stack at 0x00020100 or the character does not read, so its spin dash is left to "
     "the game"),
    (("fail:0x0A001368", "dmg"),
     "player 0's buttons or hold do not read, so the spin dash is left to the game"),
])
def test_a_state_that_does_not_read_is_left_to_the_body_and_said_once(exe, steps, line):
    base = ("pad:0:0x200:0x200", "win:0:0:1:59:59")
    rows, err = run(exe, *base, *steps, steps[-1], **ON)
    assert err == [P + line]
    assert [r["real"] for r in rows] == ["1", "2"]


def test_another_tweaks_real_is_the_one_called(exe):
    """sa2_action_buttons_window calls the `real` it is given: off once per call, on only where
    it leaves the call to the body."""
    steps = ("pad:0:0x200:0x200", "win:0:0:1:22:22", "via", "win:0:0:1:59:59", "via")
    rows, _ = run(exe, *steps)
    assert [pick(r, "via", "dash") for r in rows] == [("1", "0"), ("2", "0")]
    rows, _ = run(exe, *steps, **ON)
    assert [pick(r, "via", "dash") for r in rows] == [("1", "0"), ("1", "1")]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    steps = ("pad:0:0x200:0x200", "win:0:0:1:59:59", "aw", *hold_frames(X, 3))
    on, err = run(exe, *steps, **ON, **{replay: "/dev/null"})
    assert on == run(exe, *steps)[0]
    assert not any(line.startswith(P) for line in err)
