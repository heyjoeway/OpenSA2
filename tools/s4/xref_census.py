#!/usr/bin/env python3
"""Do `xrefs.py` and the S1 import census agree about who reaches each import?

Subtitle: for every IAT slot, count the call, jump and pointer-load sites
`tools/s4/xrefs.py` finds, and compare them field by field with
`Documentation/receipts/s1-import-classification-v1.json`. The two are decoders
anchored differently -- the census sweeps `.text` linearly, `xrefs.py` decodes
from each analyzer entry -- so when they disagree one of them is wrong, and the
disagreement is the finding rather than something to average.

Every run compares both of `xrefs.build`'s bounds. "next" is the adopted one and
must agree with the census on every field of every import. "recorded" is the
bound `xrefs.py` used until 2026-09-22 and is the negative control: it must
disagree, or this comparison has stopped being able to see the defect it was
built to catch, and the run fails either way.

Nothing from the image is written: addresses and counts only (rule 9).

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/xref_census.py            # compare; exit 1 on failure
  .venv/bin/python tools/s4/xref_census.py --write    # and write the receipt
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import image as IM  # noqa: E402
import xrefs as X  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CENSUS = ROOT / "Documentation" / "receipts" / "s1-import-classification-v1.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-xref-census-v1.json"
PIPELINE = ("tools/s4/xrefs.py", "tools/s4/image.py", "tools/s4/xref_census.py")

# The census fields compared, in the census's own names.
FIELDS = ("direct_call_sites", "thunks", "slot_pointer_loads",
          "calls_to_thunks", "jumps_to_thunks", "thunk_pointer_loads")


def counts(xref: dict, iref: dict, slot: int) -> dict:
    """The census's fields for one slot, computed from an `xrefs.build` index,
    plus the conditional jumps into its thunks, which the census does not count.

    Thunks are the `jmp [slot]` sites this index finds, not the census's list,
    so a thunk the census missed or invented shows up as a disagreement.
    """
    sites = iref.get(slot, [])
    thunks = sorted(s for s, kind, _ in sites if kind == "jmp")

    def to_thunks(kind: str) -> list[int]:
        return sorted(s for t in thunks for s, k, _ in xref.get(t, []) if k == kind)

    return {
        "direct_call_sites": sum(1 for _, kind, _ in sites if kind == "call"),
        "thunks": [f"0x{t:08X}" for t in thunks],
        "slot_pointer_loads": sum(1 for _, kind, _ in sites if kind == "mem"),
        "calls_to_thunks": len(to_thunks("call")),
        "jumps_to_thunks": len(to_thunks("jmp")),
        "thunk_pointer_loads": len(to_thunks("imm")),
        "jcc_to_thunks": [f"0x{s:08X}" for s in to_thunks("jcc")],
    }


def compare(img: "IM.Image", fmap: dict, census: list[dict], bound: str) -> dict:
    xref, iref, _ = X.build(img, fmap, bound=bound)
    rows, unseen = [], []
    for rec in census:
        name = rec.get("conventional_name") or rec["name"]
        mine = counts(xref, iref, int(rec["iat_slot"], 16))
        for field in FIELDS:
            if mine[field] != rec[field]:
                rows.append({"dll": rec["dll"], "import": name, "field": field,
                             "census": rec[field], "xrefs": mine[field]})
        if mine["jcc_to_thunks"]:
            unseen.append({"dll": rec["dll"], "import": name,
                           "jcc_to_thunk_sites": mine["jcc_to_thunks"]})
    return {"disagreements": len(rows),
            "imports_disagreeing": len({(r["dll"], r["import"]) for r in rows}),
            "rows": rows,
            # A conditional tail call into a thunk reaches the import as surely as
            # a call does. The census's static_call_sites counts calls and
            # unconditional jumps only, so these are sites it cannot see.
            "conditional_jumps_to_thunks": unseen}


def witness(img: "IM.Image", fmap: dict, census: list[dict], name: str) -> dict:
    """Where the adopted bound finds the calls to one import's thunks, and what
    the map says about the function each was decoded from."""
    xref, iref, entries = X.build(img, fmap)
    rec = next(r for r in census if (r.get("conventional_name") or r["name"]) == name)
    thunks = counts(xref, iref, int(rec["iat_slot"], 16))["thunks"]
    starts = [a for a, _, _ in entries]
    sites = []
    for t in thunks:
        for site, kind, origin in sorted(xref.get(int(t, 16), [])):
            if kind != "call":
                continue
            entry, size, _ = entries[bisect.bisect_right(starts, site) - 1]
            sites.append({"site": f"0x{site:08X}", "thunk": t, "map_entry": f"0x{entry:08X}",
                          "recorded_size": hex(size),
                          "inside_recorded_size": site < entry + size,
                          "decoded_from": "map entry" if origin in starts
                                          else f"after data, resumed at 0x{origin:08X}"})
    return {"import": name, "census_calls_to_thunks": rec["calls_to_thunks"], "sites": sites}


def map_facts(fmap: dict) -> dict:
    mapped = sorted((int(f["address"], 16), f.get("size") or 0) for f in fmap["functions"])
    past = sum(1 for (a, size), (b, _) in zip(mapped, mapped[1:]) if a + size > b)
    return {"entries": len(mapped), "recorded_extent_runs_past_next_entry": past}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--map", type=Path, default=X.DEFAULT_MAP)
    ap.add_argument("--write", action="store_true", help=f"write {IM.rel(RECEIPT)}")
    args = ap.parse_args()

    census = json.loads(CENSUS.read_text(encoding="utf-8"))["outputs"]["imports"]
    img = IM.Image(args.exe)
    fmap = json.loads(args.map.read_text(encoding="utf-8"))
    bounds = {bound: compare(img, fmap, census, bound) for bound in X.BOUNDS}

    adopted, control = bounds["next"], bounds["recorded"]
    problems = []
    if adopted["disagreements"]:
        problems.append(f"the adopted bound disagrees with the census "
                        f"{adopted['disagreements']} time(s)")
    if not control["disagreements"]:
        problems.append("the negative control agrees with the census, so this "
                        "comparison can no longer see the defect it was built for")
    for bound, result in bounds.items():
        print(f"bound {bound:8s}: {result['disagreements']} field disagreement(s) over "
              f"{result['imports_disagreeing']} of {len(census)} imports")
        for row in result["rows"]:
            print(f"    {row['dll']}!{row['import']} {row['field']}: "
                  f"census {row['census']} xrefs {row['xrefs']}")
        for row in result["conditional_jumps_to_thunks"]:
            print(f"    not in the census: {row['dll']}!{row['import']} reached by a "
                  f"conditional jump into its thunk at {', '.join(row['jcc_to_thunk_sites'])}")

    if args.write:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
        doc = {
            "schema": "sa2-recomp-receipt/v1",
            "id": "s4-xref-census-v1",
            "written": time.strftime("%Y-%m-%d"),
            "commit": head,
            "generated_by": "tools/s4/xref_census.py",
            "question": "The S1 census records two calls to XInputSetState's thunk and "
                        "xrefs.py finds none. Which measurement is wrong, and do the two "
                        "agree on every import once it is fixed?",
            "inputs": {
                "executable": IM.rel(args.exe),
                "executable_sha256": img.sha256,
                "function_map": IM.rel(args.map),
                "function_map_sha256": IM.sha256_of(args.map),
                "census": IM.rel(CENSUS),
                "census_sha256": IM.sha256_of(CENSUS),
                "pipeline": {p: IM.sha256_of(ROOT / p) for p in PIPELINE},
            },
            "command": ".venv/bin/python tools/s4/xref_census.py --write",
            "outputs": {
                "imports": len(census),
                "fields": list(FIELDS),
                "function_map": map_facts(fmap),
                "witness": witness(img, fmap, census, "XInputSetState"),
                "bounds": bounds,
                "verdict": "; ".join(problems) or
                           "the adopted bound agrees with the census on every field of "
                           "every import, and the negative control does not",
            },
        }
        RECEIPT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {IM.rel(RECEIPT)}")

    for problem in problems:
        print(f"FAIL: {problem}")
    print("XREF_CENSUS_" + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
