#!/usr/bin/env python3
"""Jump tables inside a byte range, read the way MSVC lays them out.

Subtitle: every `jmp [r*4+table]` in a range decoded from its start, with the
arms its own bound check says the table has.

Why this exists
---------------
The G3 decision (`Documentation/decisions/2026-09-20-G3-jump-table-arms.md`)
lifts every jump-table arm as its own remill trace with its own function-table
row: remill ends a trace at an indirect branch, so an arm with no row is a
function-table miss. S3 seeded those arms from the analyzer's own jump-table
records (`tools/s3/arms/armlist.py`), and the analyzer has records only for the
functions it found. A function lifted later by `tools/s4/lift_extra.py` -- by
the `.rdata` pointer sweep, or by `reach_extend.py` -- has none, so its switches
had no arms. 0x0081C0E0, a decoder reached from a pointer run in `.rdata`,
dispatches through an 86-entry table at 0x0081F8C4, and on 2026-09-22 a worker
thread died at its arm 0x0081F363.

The two shapes MSVC emits, each bounded by the switch's own check:

    cmp k, N ; ja default ;                              jmp [k*4+table]   N+1 arms
    cmp k, N ; ja default ; movzx r, byte [k+index] ;    jmp [r*4+table]   max(index[0..N])+1 arms

A dispatch with no bound check this matcher recognises within LOOK_BACK
instructions is read by its relocations instead. 0x00860830 is a whole
function that is nothing but `jmp [eax*4+0x860850]`, its callers holding the
key in range. Entries continue while each is a relocated dword pointing into
the range. A relocation is what makes a dword a pointer rather than a
coincidence of bytes, and every absolute code pointer in this image has one.
Such a run stops at the next table or index table `census` knows of, because
tables sit back to back: 0x008427D1's run otherwise reads on into
0x00842867's. The switch records which bound it used.

A two-level switch with no immediate bound -- 0x00508F1B compares against a
register, 0x0077E8A7 has no check at all -- is read by its layout. MSVC puts
the pointer table immediately before the index table, so the pointer table's
entries are the relocated pointers from its start up to the index table. The
same rule gives 0x0081F8C4's 86 entries: its index table starts at
0x0081FA1C, 86 dwords on. The switch records that bound as "layout".

A table entry that is not a relocated pointer into the range, inside the bound
the check gives, is returned as a `problem` with no arms: reported, not guessed
at.

Usage (a census of every lifted module's switches, printed):
  .venv/bin/python tools/s4/switches.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from iced_x86 import Decoder, MemorySize, Mnemonic, OpKind, Register, RegisterInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import image as IM  # noqa: E402

LOOK_BACK = 8           # instructions searched before a dispatch for its bound check


@dataclass
class Switch:
    dispatch: int
    table: int
    index_table: int | None = None
    key: str = ""                       # the register the bound check compares
    cases: int = 0                      # key values the bound admits (0 with no check)
    bound_from: str = ""                # "check", "layout" or "relocations"
    arms: list[int] = field(default_factory=list)   # distinct targets, table order
    entries: int = 0                    # pointer-table entries read
    problem: str | None = None
    run: list = field(default_factory=list)   # (address, value) read by relocations


def _full(reg: Register) -> Register:
    return RegisterInfo(reg).full_register32 if reg != Register.NONE else reg


def switches(img: "IM.Image", lo: int, hi: int) -> list[Switch]:
    """Every jump-table dispatch in [lo, hi), decoded linearly from `lo`."""
    sec = img.section_of(lo)
    if sec is None or not sec.executable:
        return []
    data = img._section_bytes[sec.name]
    stop = min(hi, sec.va + len(data))
    ins = [i for i in Decoder(32, data[lo - sec.va:stop - sec.va], ip=lo) if not i.is_invalid]
    found = []
    for k, i in enumerate(ins):
        if not (i.mnemonic == Mnemonic.JMP and i.op0_kind == OpKind.MEMORY
                and i.memory_base == Register.NONE and i.memory_index != Register.NONE
                and i.memory_index_scale == 4):
            continue
        sw = Switch(dispatch=i.ip, table=i.memory_displacement & 0xFFFFFFFF)
        key = _full(i.memory_index)
        bound = None
        for j in range(k - 1, max(k - 1 - LOOK_BACK, -1), -1):
            p = ins[j]
            if (sw.index_table is None and p.mnemonic == Mnemonic.MOVZX
                    and p.op0_kind == OpKind.REGISTER and _full(p.op0_register) == key
                    and p.op1_kind == OpKind.MEMORY and p.memory_base != Register.NONE
                    and p.memory_index == Register.NONE):
                if p.memory_size != MemorySize.UINT8:
                    sw.problem = "an index table that is not bytes"
                    break
                sw.index_table = p.memory_displacement & 0xFFFFFFFF
                key = _full(p.memory_base)
                continue
            if (p.mnemonic == Mnemonic.CMP and p.op0_kind == OpKind.REGISTER
                    and _full(p.op0_register) == key
                    and p.op1_kind in (OpKind.IMMEDIATE8, OpKind.IMMEDIATE32,
                                       OpKind.IMMEDIATE8TO32)
                    and j + 1 < len(ins) and ins[j + 1].mnemonic in (Mnemonic.JA, Mnemonic.JAE)):
                imm = p.immediate(1) & 0xFFFFFFFF
                bound = imm + 1 if ins[j + 1].mnemonic == Mnemonic.JA else imm
                break
        sw.key = str(key).rsplit(".", 1)[-1].lower() if key != Register.NONE else ""
        if sw.problem:
            found.append(sw)
            continue
        if bound is None and sw.index_table is None:
            sw.bound_from = "relocations"
            at = sw.table
            while at in img.relocated and lo <= img.pe.get_dword_at_rva(at - img.base) < hi:
                sw.run.append((at, img.pe.get_dword_at_rva(at - img.base)))
                at += 4
            sw.entries = len(sw.run)
            sw.arms = list(dict.fromkeys(v for _a, v in sw.run))
            if not sw.arms:
                sw.problem = (f"no bound check within {LOOK_BACK} instructions and no "
                              f"relocated entry at {sw.table:#010x}")
            found.append(sw)
            continue
        if bound is None and sw.index_table is not None and sw.table < sw.index_table:
            sw.bound_from = "layout"
            arms = []
            at = sw.table
            while at < sw.index_table:
                v = img.pe.get_dword_at_rva(at - img.base)
                if at not in img.relocated or not lo <= v < hi:
                    sw.problem = (f"table entry {(at - sw.table) // 4} at {at:#010x}, before the "
                                  f"index table, is not a relocated pointer into the range")
                    break
                if v not in arms:
                    arms.append(v)
                at += 4
            sw.entries = (at - sw.table) // 4
            if sw.problem is None:
                sw.arms = arms
            found.append(sw)
            continue
        if bound is None or not 0 < bound <= 4096:
            sw.problem = "an index table but no `cmp key, N; ja` within %d instructions" % LOOK_BACK
            found.append(sw)
            continue
        sw.cases = bound
        sw.bound_from = "check"
        if sw.index_table is not None:
            idx = img.read(sw.index_table, bound)
            if idx is None or len(idx) != bound:
                sw.problem = f"the index table at {sw.index_table:#010x} cannot be read"
                found.append(sw)
                continue
            sw.entries = max(idx) + 1
        else:
            sw.entries = bound
        arms: list[int] = []
        for n in range(sw.entries):
            at = sw.table + 4 * n
            if at not in img.relocated:
                sw.problem = f"table entry {n} at {at:#010x} is not a relocated dword"
                break
            v = img.pe.get_dword_at_rva(at - img.base)
            if not lo <= v < hi:
                sw.problem = f"table entry {n} at {at:#010x} points outside the range ({v:#010x})"
                break
            if v not in arms:
                arms.append(v)
        if sw.problem is None:
            sw.arms = arms
        found.append(sw)
    return found


def census(img: "IM.Image", ranges: list[tuple[int, int]],
           recorded: dict[int, list[int]]) -> dict[int, Switch]:
    """Every dispatch in every range, each read once.

    `ranges` are the lifted modules' byte ranges -- the map's and the extras' --
    and `recorded` maps a dispatch to the targets the analyzer recorded for it.
    Three rules decide a dispatch's arms:

    * read here and recorded: the union. The analyzer leaves out a default arm
      the index table also routes through the pointer table -- 0x0043658D's
      seventh entry is its default, 0x004368F8, and an indirect jump to it
      misses like any other -- so neither list alone is complete;
    * not readable here, recorded, and unreadable only because the dword at the
      displacement is not a relocated pointer: the analyzer's targets.
      `_memmove`'s and `_memcpy`'s tables are reached through an offset key --
      from 1, or through a negated one from -4 -- so the displacement is not
      where the table starts, and the analyzer read them correctly. Any other
      problem stands, recorded or not: the analyzer's lists are sometimes
      short (0x0068EC09's code admits 6 arms and it lists 3);
    * read cleanly from any range: read. 0x008690CD's targets lie in an
      extra's range beyond its map module's, and the extra's range reads it.

    A run read by relocations stops at the next table or index table any
    dispatch names. What is left unreadable keeps its `problem`.
    """
    out: dict[int, Switch] = {}
    for lo, hi in ranges:
        for sw in switches(img, lo, hi):
            prev = out.get(sw.dispatch)
            if prev is None or (prev.problem and not sw.problem):
                out[sw.dispatch] = sw
    starts = sorted({sw.table for sw in out.values()}
                    | {sw.index_table for sw in out.values() if sw.index_table is not None})
    for sw in out.values():
        if sw.bound_from != "relocations" or sw.problem:
            continue
        nxt = next((t for t in starts if t > sw.table), None)
        if nxt is not None:
            sw.run = [(a, v) for a, v in sw.run if a < nxt]
            sw.entries = len(sw.run)
            sw.arms = list(dict.fromkeys(v for _a, v in sw.run))
    for dispatch, targets in recorded.items():
        sw = out.get(dispatch)
        if sw is None:
            continue
        if sw.problem:
            if sw.problem.startswith("no bound check") and "no relocated entry" in sw.problem:
                sw.problem, sw.bound_from, sw.arms = None, "analyzer", list(targets)
        else:
            sw.arms = sw.arms + [t for t in targets if t not in sw.arms]
    return out


def recorded_tables(fmap: dict) -> dict[int, list[int]]:
    """The analyzer's jump tables, dispatch address to targets."""
    out: dict[int, list[int]] = {}
    for fn in fmap.values():
        for jt in fn.get("jump_tables") or []:
            out.setdefault(int(jt["at"], 16), [])
            for t in jt["targets"]:
                v = int(t, 16)
                if v not in out[int(jt["at"], 16)]:
                    out[int(jt["at"], 16)].append(v)
    return out


def main() -> None:
    root = HERE.parents[1]
    sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
    import common as C  # noqa: PLC0415
    img = IM.Image()
    fmap = C.by_address()
    extras = json.loads((root / "Documentation" / "lifted-extras.json").read_text())["rows"]
    ranges = [(int(d["va"], 16), int(d["end"], 16))
              for d in C.read_jsonl(C.LIFT / "next.jsonl").values()]
    ranges += [(int(r["va"], 16), int(r["end"], 16)) for r in extras]
    found = census(img, ranges, recorded_tables(fmap))
    arms = sorted({a for sw in found.values() for a in sw.arms})
    by = {}
    for sw in found.values():
        by[sw.bound_from or "unread"] = by.get(sw.bound_from or "unread", 0) + 1
    print(f"{len(found)} dispatches in {len(ranges)} module ranges, {len(arms)} distinct arms; "
          f"bounds: {by}")
    for sw in sorted(found.values(), key=lambda s: s.dispatch):
        if sw.problem:
            print(f"  {sw.dispatch:#010x} table {sw.table:#010x} PROBLEM {sw.problem}")


if __name__ == "__main__":
    main()
