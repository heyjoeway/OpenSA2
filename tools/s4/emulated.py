#!/usr/bin/env python3
"""The instructions remill cannot lift, and the continuations the runtime needs.

Subtitle: every site of the instructions `runtime/sa2_emulate.cpp` executes,
in every lifted module's range, with the address after each.

Why this exists
---------------
remill 5.0.7 has no semantics for `paddusb` and `paddusw` in their MMX forms,
`cvtps2pi`, and the x87 environment instructions `fnstenv`, `fldenv`,
`fnsave` and `frstor`. For each it ends the trace: sync hypercall 256
(kX86EmulateInstruction) at the instruction, then `__remill_error` at the next
instruction. The runtime executes the instruction and continues at the next
one through the function table (`runtime/remill_runtime.cpp`), so every site
needs a lifted entry at its successor, or the continuation traps by name.
`tools/s4/lift_extra.py --seed-continuations` lifts them from this census, and
the `lifted extras` gate requires every one to have a row.

The forms are the ones S3's census found remill refusing
(`Documentation/receipts/s3-whole-image-lift-v1.json`,
`unsupported_instructions`) -- the SSE2 xmm forms of the two adds are left out,
because remill lifts them -- and `psubsw` in both forms, which no map function
uses and two of S4's address-taken functions do; the IR check below found it.
The census must contain every site S3 listed, and `main` says so or fails.

A linear decode finds candidates, and remill's reach decides them. A linear
decode loses step after a jump table inside a function and reads data as code:
it finds `fnstenv [eax+eax-63h]` three times inside `Tails_Main`. So a site
counts only when a walk from some lifted module's entry reaches it, which is
`tools/s3/arms/cost.py`'s descent -- the copy S3 validated against remill's
own IR -- run from every map module, S3 arm and extra whose range holds a
candidate. That walk goes on through an emulated instruction where remill's
trace stops, so it also covers what the continuations reach, which is what the
runtime executes.

Usage (a census of every lifted module's sites, printed):
  .venv/bin/python tools/s4/emulated.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from iced_x86 import Code, Decoder

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import image as IM  # noqa: E402

FORMS = {
    Code.PADDUSB_MM_MMM64: "paddusb",
    Code.PADDUSW_MM_MMM64: "paddusw",
    Code.CVTPS2PI_MM_XMMM64: "cvtps2pi",
    Code.FNSTENV_M28BYTE: "fnstenv",
    Code.FLDENV_M28BYTE: "fldenv",
    Code.FNSAVE_M108BYTE: "fnsave",
    Code.FRSTOR_M108BYTE: "frstor",
    # Found by the IR check (ir_disagreements) on 2026-09-23, in two IDCT
    # routines the address-taken lift brought in: remill has no psubsw in
    # either form, though it lifts paddsw beside it.
    Code.PSUBSW_MM_MMM64: "psubsw",
    Code.PSUBSW_XMM_XMMM128: "psubsw xmm",
}


@dataclass(frozen=True)
class Site:
    ip: int
    next_ip: int
    kind: str


def sites(img: "IM.Image", lo: int, hi: int) -> list[Site]:
    """Every emulated instruction in [lo, hi), decoded linearly from `lo`."""
    sec = img.section_of(lo)
    if sec is None or not sec.executable:
        return []
    data = img._section_bytes[sec.name]
    stop = min(hi, sec.va + len(data))
    return [Site(i.ip, i.next_ip, FORMS[i.code])
            for i in Decoder(32, data[lo - sec.va:stop - sec.va], ip=lo) if i.code in FORMS]


def candidates(img: "IM.Image", ranges: list[tuple[int, int]]) -> dict[int, Site]:
    """Every emulated form a linear decode of any range finds: a superset."""
    out: dict[int, Site] = {}
    for lo, hi in ranges:
        for s in sites(img, lo, hi):
            out.setdefault(s.ip, s)
    return out


def reached(found: dict[int, Site], modules: list[tuple[int, int, int]],
            memory: bytes) -> dict[int, Site]:
    """The candidates some module's walk reaches. `modules` are (entry, lo, hi):
    the byte range each was lifted over."""
    sys.path.insert(0, str(HERE.parent / "s3" / "arms"))
    from cost import walk_detail  # noqa: PLC0415
    keys = sorted(found)
    hit: set[int] = set()
    for entry, lo, hi in modules:
        import bisect  # noqa: PLC0415
        i = bisect.bisect_left(keys, lo)
        if i == len(keys) or keys[i] >= hi:
            continue                                  # no candidate in its range
        seen, _exits = walk_detail(memory, entry, lo, hi)
        hit |= seen & set(found)
    return {va: found[va] for va in sorted(hit)}


def modules(common, extra_rows: list[dict]) -> list[tuple[int, int, int]]:
    """(entry, lo, hi) for every lifted module: the map's, S3's arms, the extras."""
    out = [(int(d["va"], 16), int(d["va"], 16), int(d["end"], 16))
           for d in common.read_jsonl(common.LIFT / "next.jsonl").values()]
    arms = common.GEN / "s3" / "arms" / "arms.next.jsonl"
    for line in arms.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            lo, hi = (int(x, 16) for x in d["anchor"])
            out.append((int(d["entry"], 16), lo, hi))
    for r in extra_rows:
        va = int(r["va"], 16)
        out.append((va, int(r.get("anchor") or r["va"], 16), int(r["end"], 16)))
    return out


def census(img: "IM.Image", common, extra_rows: list[dict]) -> dict[int, Site]:
    """The sites lifted code can reach."""
    mods = modules(common, extra_rows)
    return reached(candidates(img, [(lo, hi) for _e, lo, hi in mods]), mods,
                   common.memory_image())


# Lifted modules whose IR asks the runtime to emulate an instruction that is
# not a census site, each with why that is not a missing site. A new entry needs
# the same: an unaccounted hypercall is an instruction the emulator would refuse.
IR_EXCEPTIONS = {
    0x007C0CF0: "a data table after DirectInput8Create's import thunk, lifted as code from "
                "dispatch_surface's rdata_vtable_entry_unmapped list; its walk decodes 256 fdiv "
                "and 256 or, and the hypercall is remill refusing one of its garbage bytes",
}


def ir_disagreements(common, extra_rows: list[dict]) -> list[tuple]:
    """Ground truth from remill's own IR: every lifted module's count of sync
    hypercall 256 against the sites a remill-faithful walk -- one that stops at
    an emulated instruction, as remill's trace does -- reaches from its entry.
    A module whose IR holds more has an instruction the census does not know.
    First written by the review of the slice that added the emulator."""
    import re
    from iced_x86 import FlowControl
    mem = common.memory_image()
    define = re.compile(r"^define [^\n]*@([\w.$]+)\(", re.M)
    hyper = re.compile(r"@__remill_sync_hyper_call\([^\n]*i32 noundef 256\)")

    def stopwalk(entry: int, lo: int, hi: int) -> int:
        seen, sites, todo = set(), set(), [entry]
        while todo:
            pc = todo.pop()
            while lo <= pc < hi and pc not in seen:
                seen.add(pc)
                ins = Decoder(32, mem[pc - common.IMAGE_BASE:pc - common.IMAGE_BASE + 16], ip=pc).decode()
                if ins.code in FORMS:
                    sites.add(pc)
                    break
                fc = ins.flow_control
                if fc in (FlowControl.NEXT, FlowControl.CALL, FlowControl.INDIRECT_CALL):
                    pc = ins.next_ip
                elif fc == FlowControl.UNCONDITIONAL_BRANCH:
                    pc = ins.near_branch32
                elif fc == FlowControl.CONDITIONAL_BRANCH:
                    todo.append(ins.near_branch32)
                    pc = ins.next_ip
                else:
                    break
        return len(sites)

    mods = [(int(d["va"], 16), int(d["va"], 16), int(d["end"], 16),
             common.LIFT / "next" / f"sub_{int(d['va'], 16):08X}.ll")
            for d in common.read_jsonl(common.LIFT / "next.jsonl").values()]
    for line in (common.GEN / "s3" / "arms" / "arms.next.jsonl").read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            lo, hi = (int(x, 16) for x in d["anchor"])
            e = int(d["entry"], 16)
            mods.append((e, lo, hi, common.GEN / "s3" / "arms" / "lift" / "next" / f"sub_{e:08X}.ll"))
    for r in extra_rows:
        va = int(r["va"], 16)
        mods.append((va, int(r.get("anchor") or r["va"], 16), int(r["end"], 16),
                     common.GEN / "s4" / "lift" / "ll" / f"sub_{va:08X}.ll"))
    out = []
    for entry, lo, hi, ll in mods:
        if not ll.is_file():
            out.append((entry, None, None, f"{ll.name} is missing"))
            continue
        text = ll.read_text(errors="replace")
        n = len(hyper.findall(text)) if "i32 noundef 256)" in text else 0
        if not n:
            continue
        w = stopwalk(entry, lo, hi)
        if n != w:
            out.append((entry, n, w, IR_EXCEPTIONS.get(entry)))
    return out


def hypercall_objects(common, extra_rows: list[dict]) -> list[Path]:
    """The linked objects of every lifted module whose IR asks the runtime to
    emulate an instruction: where the lifted code's tail call into
    __remill_error has to be a tail call."""
    lls = [p for p in (common.LIFT / "next").glob("sub_*.ll") if not p.name.endswith(".pc24.ll")]
    lls += list((common.GEN / "s3" / "arms" / "lift" / "next").glob("sub_*.ll"))
    extras = {int(r["va"], 16) for r in extra_rows}
    lls += [common.GEN / "s4" / "lift" / "ll" / f"sub_{va:08X}.ll" for va in sorted(extras)]
    import re
    hyper = re.compile(r"@__remill_sync_hyper_call\([^\n]*i32 noundef 256\)")
    out = []
    for ll in lls:
        if ll.name.endswith(".pc24.ll") or not ll.is_file():
            continue
        # The hypercall itself: `i32 noundef 256)` alone also matches a store of
        # the constant 256 (__remill_write_memory_32(..., i32 noundef 256)).
        if not hyper.search(ll.read_text(errors="replace")):
            continue
        va = int(ll.stem[4:], 16)
        cands = [common.GEN / "s4" / "lift" / "obj" / f"ir_{va:08X}.o"] if va in extras else []
        cands.append(common.BUILD / "next+allarms" / f"ir_{va:08X}.o")
        obj = next((c for c in cands if c.is_file()), None)
        out.append(obj if obj else Path(f"<missing object for {ll}>"))
    return out


def s3_sites(root: Path) -> set[int]:
    """The sites S3's census found remill refusing, from the receipt."""
    d = json.loads((root / "Documentation" / "receipts" / "s3-whole-image-lift-v1.json").read_text())
    run = d["unsupported_instructions"]["to_next_entry_run"]["unsupported_instructions"]
    return {int(s["address"], 16) for s in run["sites_listed"]}


def main() -> int:
    root = HERE.parents[1]
    sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
    import common as C  # noqa: PLC0415
    img = IM.Image()
    extras = json.loads((root / "Documentation" / "lifted-extras.json").read_text())["rows"]
    mods = modules(C, extras)
    cand = candidates(img, [(lo, hi) for _e, lo, hi in mods])
    found = reached(cand, mods, C.memory_image())
    by = {}
    for s in found.values():
        by[s.kind] = by.get(s.kind, 0) + 1
    missing = sorted(s3_sites(root) - set(found))
    print(f"{len(found)} sites reached from {len(mods)} lifted modules, of {len(cand)} a "
          f"linear decode finds: {by}; {len(s3_sites(root))} listed by S3, "
          f"{len(missing)} of them not reached")
    for va in sorted(set(cand) - set(found)):
        print(f"  not reached (a linear-decode candidate only): {va:#010x} {cand[va].kind}")
    for va in missing:
        print(f"  S3 site {va:#010x} not reached")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
