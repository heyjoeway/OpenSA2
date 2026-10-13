#!/usr/bin/env python3
"""Who reaches this address? A cross-reference index over the shipped image.

Subtitle: decode every function the analyzer map names, from its own entry, and
record every call target, jump target and immediate operand, so "which code
pushes 0x008653d0" and "who calls the CRI file server" are one command instead
of a byte search that cannot tell a pointer from a coincidence.

Why a tool and not a byte search
--------------------------------
`grep` over the image for a little-endian dword finds data tables, relocated
pointer arrays and accidental byte runs with equal confidence. This decodes
from analyzer function entries -- the same anchored decode `image.py` uses --
so every hit it reports is an operand of a real instruction at a real
instruction boundary, and the kind of reference (`call`, `jmp`, `jcc`, other
immediate, memory displacement) is part of the answer.

How far each entry is decoded
-----------------------------
To the next map entry, not to the map's recorded size. The recorded size is
wrong 691 times (`Documentation/S3_INPUTS.md` section 1), and code the map has
no entry for -- methods reached only through a vtable, and a function nothing
references -- sits in exactly those gaps. Bounded by recorded size, this tool
found no caller of `XInputSetState` where the S1 census records two, disagreed
with the census about dozens of other imports, and counted an instruction twice
wherever two recorded extents overlap; `Documentation/receipts/s4-xref-census-v1.json`
lists every disagreement and counts the overlaps. The next-entry bound is the
one the whole-image lift adopted (`--to-next-entry`), and the extents partition
the image, so nothing is decoded twice.

A range can hold data, most often a switch's tables after its function. The
decode resumes at the next byte, as the census's linear sweep does, and every
site found after that point is printed as `after data` with where its decode
resumed, because it is no longer anchored at an entry.
`tools/s4/xref_census.py` compares both bounds with the census and keeps the
recorded one as its negative control.

It is deliberately read-only and prints text. It never writes image bytes.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/xrefs.py 0x008653d0 0x00853192
  .venv/bin/python tools/s4/xrefs.py --import WaitForSingleObject
  .venv/bin/python tools/s4/xrefs.py --in 0x007c0000-0x007e0000 --import ReadFile
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import image as IM  # noqa: E402
from iced_x86 import Decoder, FlowControl, OpKind  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "private" / "generated" / "function-map.json"


BOUNDS = ("next", "recorded")


def build(img: "IM.Image", fmap: dict, bound: str = "next") -> tuple[dict, dict, list]:
    """(target -> [(site, kind, origin)], import_slot -> [(site, kind, origin)], entries).

    `bound` is how far each entry is decoded: "next" to the next map entry (the
    last one to the end of its section), "recorded" to the map's recorded size,
    stopping at the first invalid instruction. `origin` is the map entry the
    site was decoded from, or the address the decode resumed at after data.
    """
    if bound not in BOUNDS:
        raise ValueError(f"bound must be one of {BOUNDS}, not {bound!r}")
    xref: dict[int, list] = {}
    iref: dict[int, list] = {}
    entries = []
    mapped = sorted((int(f["address"], 16), f.get("size") or 0, f.get("name") or "")
                    for f in fmap["functions"])
    for k, (va, size, name) in enumerate(mapped):
        if size <= 0:
            continue
        sec = img.section_of(va)
        if sec is None or not sec.executable:
            continue
        entries.append((va, size, name))
        data = img._section_bytes[sec.name]
        off = va - sec.va
        if bound == "recorded":
            end = va + size
        else:
            nxt = mapped[k + 1][0] if k + 1 < len(mapped) else sec.end
            end = min(nxt, sec.end)
        decoder = Decoder(32, data[off:end - sec.va], ip=va)
        origin = va     # where the decode that reaches each site started
        while decoder.can_decode:
            ins = decoder.decode()
            if ins.is_invalid:
                if bound == "recorded":
                    break
                # Data inside the range, typically a switch's tables after its
                # function: resume at the next byte, as the census's sweep does.
                # Sites found from here on are not anchored at a map entry, and
                # the third field of each says so.
                decoder.position = ins.ip + 1 - va
                decoder.ip = origin = ins.ip + 1
                continue
            site = ins.ip
            fc = ins.flow_control
            if fc in (FlowControl.CALL, FlowControl.UNCONDITIONAL_BRANCH,
                      FlowControl.CONDITIONAL_BRANCH):
                if ins.op0_kind in (OpKind.NEAR_BRANCH32, OpKind.NEAR_BRANCH16):
                    kind = {FlowControl.CALL: "call",
                            FlowControl.UNCONDITIONAL_BRANCH: "jmp"}.get(fc, "jcc")
                    xref.setdefault(ins.near_branch_target, []).append((site, kind, origin))
            disp = IM.abs_displacement(ins)
            if disp is not None:
                if disp in img.imports:
                    kind = {FlowControl.INDIRECT_CALL: "call",
                            FlowControl.INDIRECT_BRANCH: "jmp"}.get(fc, "mem")
                    iref.setdefault(disp, []).append((site, kind, origin))
                else:
                    xref.setdefault(disp, []).append((site, "mem", origin))
            for imm in IM.immediates(ins):
                xref.setdefault(imm, []).append((site, "imm", origin))
    return xref, iref, sorted(entries)


def owner(entries: list, va: int) -> str:
    import bisect
    i = bisect.bisect_right(entries, (va, 1 << 62, "")) - 1
    if i < 0:
        return "?"
    a, size, name = entries[i]
    return (f"{a:#010x}" + (f" ({name})" if name and not name.startswith("FUN_") else "")
            + (f", past its recorded size {size:#x}" if va >= a + size else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--import", dest="imp", default=None,
                    help="report every call site of this imported name")
    ap.add_argument("--in", dest="rng", default=None,
                    help="restrict reported sites to LO-HI")
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--map", type=Path, default=DEFAULT_MAP)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    img = IM.Image(args.exe)
    fmap = json.loads(args.map.read_text())
    xref, iref, entries = build(img, fmap)

    lo = hi = None
    if args.rng:
        a, b = args.rng.split("-")
        lo, hi = int(a, 0), int(b, 0)

    def keep(site: int) -> bool:
        return lo is None or (lo <= site < hi)

    anchors = {a for a, _, _ in entries}

    def where(site: int, origin: int) -> str:
        own = owner(entries, site)
        return own if origin in anchors else f"{own}, after data (decode resumed at {origin:#010x})"

    out: dict = {}
    for t in args.targets:
        out[t] = [(f"{s:#010x}", k, where(s, f))
                  for s, k, f in xref.get(int(t, 0), []) if keep(s)]
    if args.imp:
        for slot, names in img.imports.items():
            if args.imp.lower() in names[-1].lower():
                key = "!".join(names)
                rows = [(f"{s:#010x}", kind, where(s, f))
                        for s, kind, f in iref.get(slot, []) if keep(s)]
                out.setdefault(key, []).extend(rows)

    if args.json:
        print(json.dumps(out, indent=1))
        return
    for k, rows in out.items():
        print(f"== {k}: {len(rows)} site(s)")
        for s, kind, own in rows:
            print(f"   {s}  {kind:6} in {own}")


if __name__ == "__main__":
    main()
