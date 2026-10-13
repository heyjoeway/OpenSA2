"""The missing-block edge table's decode: dead filler after a jump is not a fall-through.

`tools/s4/mb_edges.py` decodes each module's lifted range linearly. The
alignment filler after an unconditional jump -- `lea esp,[esp]` and
`add eax,0` in the CRT's hand-written math -- decoded as instructions that
"fell through" into the next function, and a fall-through is the strictest
edge kind, so it overrode the real `jmp` into the same address and the tail
call stopped dispatching (0x0082C7D0, 2026-09-23). These tests hold the rule
that replaced it on synthetic bytes: an instruction records edges only if it
is reachable within its range from the module's entry or a lifted entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import mb_edges as MB  # noqa: E402

BASE = 0x00400000


def image(code: dict[int, bytes], size: int = 0x2000) -> bytes:
    """A flat view the decoder indexes by guest address minus the image base."""
    buf = bytearray(b"\xcc" * size)
    for va, raw in code.items():
        off = va - BASE
        buf[off:off + len(raw)] = raw
    return bytes(buf)


def rel32(src: int, target: int, length: int = 5) -> bytes:
    return (target - (src + length)).to_bytes(4, "little", signed=True)


# Module A at 0x401000: `jmp short 0x401000` then filler that runs into 0x401010.
# Module B at 0x401100: `jmp 0x401010`, the real tail call. 0x401010 is in no module.
FILLER = bytes([0x8d, 0xa4, 0x24, 0x00, 0x00, 0x00, 0x00,   # lea esp,[esp+0]
                0x05, 0x00, 0x00, 0x00, 0x00])              # add eax,0
A = bytes([0xeb, 0xfe, 0x90, 0x90]) + FILLER                # 0x401000: jmp $; 2 nops; filler
B_JMP = 0x401100


def build():
    code = {0x401000: A, B_JMP: b"\xe9" + rel32(B_JMP, 0x401010)}
    ranges = {0x401000: (0x401000, 0x401000 + len(A)), B_JMP: (B_JMP, B_JMP + 5)}
    return image(code), ranges


def by_target(edges):
    return {e["target"]: e for e in edges}


def test_filler_after_a_jump_does_not_fall_through():
    img, ranges = build()
    edges, _ = MB.decode_edges(img, ranges)
    e = by_target(edges)[0x401010]
    assert e["kind"] == MB.EDGE_JMP, e
    assert e["source"] == B_JMP


def test_the_old_linear_rule_is_what_this_replaced():
    """The control: with the module's entry pointing into the filler, the filler
    is live, its fall-through is recorded, and it is stricter than the jmp."""
    img, ranges = build()
    edges, _ = MB.decode_edges(img, ranges, entries={0x401004})
    e = by_target(edges)[0x401010]
    assert e["kind"] == MB.EDGE_FALL, e


def test_a_live_branch_into_the_filler_makes_it_reachable():
    """A branch from live code that lands in the filler makes it reachable, and
    then its fall-through is real."""
    code = bytes([0x74, 0x02, 0xeb, 0xfe]) + FILLER               # je +2 -> filler; jmp $
    img = image({0x401000: code, B_JMP: b"\xe9" + rel32(B_JMP, 0x401010)})
    ranges = {0x401000: (0x401000, 0x401000 + len(code)), B_JMP: (B_JMP, B_JMP + 5)}
    assert by_target(MB.decode_edges(img, ranges)[0])[0x401010]["kind"] == MB.EDGE_FALL


def test_a_dead_branch_does_not_revive_what_it_targets():
    """The first cut revived code at any branch target, so a `jne` lying dead
    after a `ret` (0x00828F93) kept four real tail calls refused; liveness is
    now reachability from the entry, to a fixed point."""
    code = A[:2] + bytes([0x74, 0x00]) + FILLER                   # jmp $; (dead) je -> filler
    img = image({0x401000: code, B_JMP: b"\xe9" + rel32(B_JMP, 0x401010)})
    ranges = {0x401000: (0x401000, 0x401000 + len(code)), B_JMP: (B_JMP, B_JMP + 5)}
    assert by_target(MB.decode_edges(img, ranges)[0])[0x401010]["kind"] == MB.EDGE_JMP


def test_a_lifted_extra_inside_a_range_keeps_its_edges():
    """An extra sitting after a `ret` in a map module's range is entered from
    elsewhere; its `jmp` must still be recorded."""
    thunk = 0x401004
    code = bytes([0xc3, 0xcc, 0xcc, 0xcc]) + b"\xe9" + rel32(thunk, 0x401200)
    img = image({0x401000: code})
    ranges = {0x401000: (0x401000, 0x401000 + len(code))}
    assert 0x401200 not in by_target(MB.decode_edges(img, ranges)[0])
    edges, _ = MB.decode_edges(img, ranges, entries={thunk})
    e = by_target(edges)[0x401200]
    assert e["kind"] == MB.EDGE_JMP and e["source"] == thunk
