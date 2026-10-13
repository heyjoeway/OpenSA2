#!/usr/bin/env python3
"""A synthetic controller recording, in the native recorder's own format.

Subtitle: a scripted pattern -- neutral, then the stick held forward with a
jump every so often -- written as `harness/replay/native_input_probe.c`
writes a human's play, so the lifted build can be driven through a stage by
`tools/s4/stage_run.py --no-native --input` while no human recording exists.

    .venv/bin/python tools/s4/synth_input.py [--pattern forward-jump] [--ticks 1300]
    .venv/bin/python tools/s4/stage_run.py --no-native --input private/generated/replay/synth/forward-jump/sa2-input.csv

Why
---
The stage replay's input is a human's run (Documentation/decisions/
2026-09-23-S4-stage-route.md, item 4), and it waits on the user and the live
oracle. What does not wait is S4's queue item "the walls gameplay hits":
missing blocks, unmapped vtable slots and imports that only running, jumping
and hitting objects reach. A scripted pattern reaches them on the lifted side
alone. It is not a replay -- there is no native run of it to compare with --
and it is never registered as one; it is a way to walk walls.

The values are the ones the native recorder stores for `ControllersRaw[0]`
(PDS_PERIPHERAL): `on` the held buttons, `off` their complement, `press` and
`release` the edges, `y1` the stick's forward axis and `x1` its sideways one.
The forward value is the one the native "stick forward" test fed
(captures/mh-inputtest-synth, 2026-09-23): 0xFF81, -127. Button A is bit 2
(SA2ModLoader's Buttons enum, makemasks order C, B, A). B, the button
forward-action presses, is bit 1: ButtonBits_B = 1 in
private/predecessor-tools/sa2-mod-loader/SA2ModLoader/include/SA2Enums.h.

No native capture has confirmed which way x1's two values, 0xFF81 and 0x7F,
steer: none has fed or recorded a nonzero x1 (2026-09-23), and y1's value
above is the only stick value one has fed. STICK_LEFT and STICK_RIGHT are
named from the executable instead. The controller update 0x0077E780 scales
the X that 0x00425700 read into x1 with its sign kept (0x0077E7F6) and the
Y into y1 negated (0x0077E81B). 0x00425700's keyboard path sets X to -128
for action 3 and +127 for action 4 (masks at 0x008AE160), and 0x008AE040
binds those by default to keys 0x7A and 0x7B. A key is an index into the
game's table at 0x0089E9E8, 144 DirectInput key codes in ascending order,
which the keyboard's key test reads as [key*4+0x0089E9E8] (0x008635A8):
entry 0x7A holds 0xCB, DIK_LEFT, and entry 0x7B holds 0xCD, DIK_RIGHT, so a
negative x1 is left. 0x00425700 sets Y to +127 for action 1 (mask 8,
0x004259CC), bound to key 0x78,
whose entry holds 0xC8, DIK_UP, which puts up at a negative y1, the sign the
capture fed as forward. zigzag-jump swings both ways, so the naming does not
matter to it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "private" / "generated" / "replay" / "synth"
HEADER = "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old"
BUTTON_A = 0x4
STICK_FORWARD = 0xFF81        # int16 -127 in the recorder's 16-bit field


BUTTON_B = 0x2                # the action button: somersault, spin dash, light dash
STICK_LEFT, STICK_RIGHT = 0xFF81, 0x7F


def row(tick: int, on: int = 0, press: int = 0, release: int = 0, y1: int = 0,
        x1: int = 0) -> str:
    return (f"{tick},0,{on:#x},{(~on) & 0xFFFFFFFF:#x},{press:#x},{release:#x},0x0,0x0,"
            f"{x1:#x},{y1:#x},0x0,0x0,0x0")


def forward_jump(ticks: int, start: int, period: int, hold: int, button: int = BUTTON_A,
                 steer: int = 0) -> list[str]:
    """Neutral until `start`, then the stick forward; `button` pressed for
    `hold` ticks every `period` ticks after that. With `steer`, the stick also
    swings left and right, `steer` ticks each way."""
    out = []
    for t in range(ticks):
        if t < start:
            out.append(row(t))
            continue
        phase = (t - start) % period
        held = phase < hold and t >= start + period
        on = button if held else 0
        press = button if held and phase == 0 else 0
        release = button if (not held and phase == hold and t >= start + period) else 0
        x1 = 0
        if steer:
            x1 = STICK_LEFT if ((t - start) // steer) % 2 else STICK_RIGHT
        out.append(row(t, on, press, release, STICK_FORWARD, x1))
    return out


PATTERNS = {"forward-jump": lambda n: forward_jump(n, start=200, period=90, hold=12),
            "forward": lambda n: forward_jump(n, start=200, period=10 ** 9, hold=0),
            "forward-jump-fast": lambda n: forward_jump(n, start=200, period=45, hold=8),
            "zigzag-jump": lambda n: forward_jump(n, start=200, period=70, hold=10, steer=40),
            "forward-action": lambda n: forward_jump(n, start=200, period=60, hold=6,
                                                     button=BUTTON_B)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", default="forward-jump", choices=sorted(PATTERNS))
    ap.add_argument("--ticks", type=int, default=1300)
    a = ap.parse_args()
    out = OUT / a.pattern / "sa2-input.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEADER + "\n" + "\n".join(PATTERNS[a.pattern](a.ticks)) + "\n")
    print(f"SYNTH_INPUT {a.pattern}: {a.ticks} rows -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
