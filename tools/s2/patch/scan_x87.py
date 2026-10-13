#!/usr/bin/env python3
"""S2 patch: locate the x87 instruction classes design 3.4 names but the spike never ran.

The S1 opcode histogram (Documentation/receipts/s1-opcode-histogram-v1.json) counts
mnemonics over a linear sweep of `.text`; it does not record *where* they are. The
four pieces of the S2 patch work (fsqrt, the fistp out-of-range helper, the PC53
control-word column, and fixture rows for fsub/fsqrt/fldcw) each need an actual
drivable function, so this tool re-sweeps `.text` with iced-x86, keeps the site
address of every instruction in the classes below, attributes each site to the
function that contains it (private/generated/function-map.json) and ranks the
containing functions by drivability:

  leaf           the function makes no direct call, no import call and no indirect
                 call, and the map records no unresolved indirect jump: the spike
                 harness can run it with no callee stubs at all.
  near-leaf      only calls the CRT helpers the spike harness already stubs.

Classes: fsqrt, fsub family (fsub/fsubr/fsubp/fsubrp/fisub/fisubr), fistp family
(by operand width, which is what the out-of-range helper switches on), fldcw,
fnstcw, and the transcendentals, for the record.

The sweep is the histogram's: runs of 0xCC and runs of two or more 0x90 are
padding; a decode error resumes one byte later and the instructions from it to the
next 16-byte boundary are suspect and excluded (a jump table decoding as `fist` is
exactly the artefact this excludes).

Output: private/generated/s2/patch/x87_sites.json (ignored by git; it carries
addresses and mnemonics, never bytes).

Usage: .venv/bin/python tools/s2/patch/scan_x87.py
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from collections import Counter
from pathlib import Path

import iced_x86 as ix
import pefile

ROOT = Path(__file__).resolve().parents[3]
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
FUNCTION_MAP = ROOT / "private" / "generated" / "function-map.json"
OUT = ROOT / "private" / "generated" / "s2" / "patch" / "x87_sites.json"

FSUB_FAMILY = {"fsub", "fsubr", "fsubp", "fsubrp", "fisub", "fisubr"}
FISTP_FAMILY = {"fistp", "fist", "fisttp"}
TRANSCENDENTAL = {"fsin", "fcos", "fsincos", "fptan", "fpatan", "fyl2x", "fyl2xp1", "f2xm1", "fscale"}
CLASSES = {"fsqrt", "fldcw", "fnstcw", "fdiv", "fdivr", "fdivp", "fdivrp"} | FSUB_FAMILY | FISTP_FAMILY | TRANSCENDENTAL


def fistp_width(instr: "ix.Instruction") -> str:
    """fistps / fistpl / fistpll, the histogram receipt's names, from the memory operand size."""
    size = instr.memory_size
    return {ix.MemorySize.INT16: "fistps", ix.MemorySize.INT32: "fistpl", ix.MemorySize.INT64: "fistpll"}.get(size, "fistp?")


def sweep(data: bytes, base: int) -> tuple[list[dict], dict]:
    """(sites, stats) over one section: the histogram's padding and suspect rules."""
    fmt = ix.Formatter(ix.FormatterSyntax.INTEL)
    sites: list[dict] = []
    stats = Counter()
    pos, n = 0, len(data)
    suspect_until = -1
    while pos < n:
        b = data[pos]
        if b == 0xCC:
            while pos < n and data[pos] == 0xCC:
                pos += 1
            continue
        if b == 0x90 and pos + 1 < n and data[pos + 1] == 0x90:
            while pos < n and data[pos] == 0x90:
                pos += 1
            continue
        dec = ix.Decoder(32, data[pos:pos + 16], ip=base + pos)
        instr = dec.decode()
        if instr.code == ix.Code.INVALID:
            stats["decode_errors"] += 1
            suspect_until = ((base + pos) + 16) & ~0xF
            pos += 1
            continue
        mnem = fmt.format(instr).split()[0]
        stats["instructions"] += 1
        if mnem in CLASSES:
            suspect = (base + pos) < suspect_until
            stats["suspect" if suspect else "clean"] += 1
            if not suspect:
                sites.append({"va": base + pos, "mnemonic": mnem,
                              "form": fistp_width(instr) if mnem in FISTP_FAMILY and instr.op0_kind == ix.OpKind.MEMORY else mnem,
                              "text": fmt.format(instr)})
        pos += instr.len
    return sites, dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=EXE)
    ap.add_argument("--function-map", type=Path, default=FUNCTION_MAP)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()

    pe = pefile.PE(str(a.exe), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    data = text.get_data()[:text.Misc_VirtualSize]
    sites, stats = sweep(data, base + text.VirtualAddress)

    fmap = json.loads(a.function_map.read_text())
    funcs = sorted(({"va": int(f["address"], 16), **f} for f in fmap["functions"]), key=lambda f: f["va"])
    starts = [f["va"] for f in funcs]
    by_va = {f["va"]: f for f in funcs}

    def owner(va: int) -> dict | None:
        i = bisect.bisect_right(starts, va) - 1
        if i < 0:
            return None
        f = funcs[i]
        return f if va < f["va"] + f["size"] else None

    per_function: dict[int, dict] = {}
    unmapped = Counter()
    for s in sites:
        f = owner(s["va"])
        if f is None:
            unmapped[s["form"]] += 1
            s["function"] = None
            continue
        s["function"] = f["address"]
        e = per_function.setdefault(f["va"], {
            "address": f["address"], "name": f["name"], "region": f["region"], "size": f["size"],
            "instructions": f["instructions"], "direct_calls": len(f["direct_calls"]), "import_calls": len(f["import_calls"]),
            "indirect_call_sites": f["indirect_call_sites"], "unresolved_indirect_jumps": f["unresolved_indirect_jumps"],
            "jump_tables": len(f["jump_tables"]), "fs0_sites": f["fs0_sites"], "forms": Counter()})
        e["forms"][s["form"]] += 1

    for e in per_function.values():
        e["forms"] = dict(sorted(e["forms"].items()))
        e["leaf"] = (e["direct_calls"] == 0 and e["import_calls"] == 0 and e["indirect_call_sites"] == 0
                     and e["unresolved_indirect_jumps"] == 0 and e["jump_tables"] == 0 and e["fs0_sites"] == 0)

    def bucket(form_pred) -> dict:
        hits = [s for s in sites if form_pred(s["form"])]
        by_region = Counter()
        for s in hits:
            f = by_va.get(int(s["function"], 16)) if s["function"] else None
            by_region[f["region"] if f else "unmapped"] += 1
        fns = sorted((e for e in per_function.values() if any(form_pred(k) for k in e["forms"])),
                     key=lambda e: (not e["leaf"], e["region"] != "game", e["instructions"]))
        return {"sites": len(hits), "by_region": dict(sorted(by_region.items())),
                "functions": len(fns),
                "leaf_game_functions": sum(1 for e in fns if e["leaf"] and e["region"] == "game"),
                "smallest_drivable": [{k: v for k, v in e.items() if k != "forms"} | {"forms": e["forms"]} for e in fns[:12]]}

    out = {
        "schema": "sa2-recomp-x87-sites/v1",
        "exe": str(a.exe.relative_to(ROOT)),
        "exe_sha256": hashlib.sha256(a.exe.read_bytes()).hexdigest(),
        "function_map_sha256": hashlib.sha256(a.function_map.read_bytes()).hexdigest(),
        "sweep": stats,
        "sites_unmapped_by_form": dict(sorted(unmapped.items())),
        "classes": {
            "fsqrt": bucket(lambda f: f == "fsqrt"),
            "fsub_family": bucket(lambda f: f in FSUB_FAMILY),
            "fistp_family": bucket(lambda f: f.startswith("fistp") or f in FISTP_FAMILY),
            "fistpl": bucket(lambda f: f == "fistpl"),
            "fistpll": bucket(lambda f: f == "fistpll"),
            "fistps": bucket(lambda f: f == "fistps"),
            "fldcw": bucket(lambda f: f == "fldcw"),
            "fnstcw": bucket(lambda f: f == "fnstcw"),
            "transcendental": bucket(lambda f: f in TRANSCENDENTAL),
        },
        "all_fsqrt_sites": [s for s in sites if s["form"] == "fsqrt"],
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1) + "\n")
    for name, b in out["classes"].items():
        print(f"S2_X87 {name:14s} sites {b['sites']:5d} {b['by_region']} functions {b['functions']} leaf-game {b['leaf_game_functions']}")
    print(f"S2_X87 wrote {a.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
