#!/usr/bin/env python3
"""Tail-call targets lifted code jumps to that no lifted module starts at.

Subtitle: every `jmp` edge in the missing-block edge table whose target has no
lifted entry, lifted as an extra with the edge as its evidence, the table
regenerated, and again until a round finds nothing.

    source tools/env.sh
    .venv/bin/python tools/s4/edge_targets.py            # list them
    .venv/bin/python tools/s4/edge_targets.py --lift     # lift them to a fixed point

Why
---
The edge table (`tools/s4/mb_edges.py`) holds every place reachable lifted
code leaves its own module, decoded from the code the lift reaches, and
SA2_MB_REACHED dispatches a `jmp` edge through the function table. A `jmp`
edge whose target no module starts at is therefore a function-table miss
waiting for the run that takes it. The Metal Harbor stage, driven with
synthetic input (`tools/s4/synth_input.py`), stopped on one: 0x006C74F0,
reached by `jmp` at 0x006C7B14, named by no base relocation, so
`tools/s4/address_taken.py` could not see it (2026-09-23). The table already
knows every such target, so they are lifted from it at once rather than one run
at a time.

Every such target is lifted, to the end of the lifted module that holds its
bytes, as any extra is. Most lie outside every map function: destructors and
cleanup code that only exception-handling funclets reach, by `jmp`. Those are
dead until the runtime unwinds C++ exceptions -- RaiseException traps today --
so for them "a miss waiting for the run that takes it" is a miss waiting for
exception support; lifting them early costs only build time. A few lie
inside one, reached by a module that itself starts inside that function -- an
arm or a continuation -- jumping back to code before its own start; those
become a second entry into the function, as the arms and continuations already
are, and their reason says which.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import lift_extra as LE  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
TAG = "next+allarms"
EXTRAS = C.GEN / "s4" / "link" / f"extra_functions.{TAG}.json"
EDGES_C = C.GEN / "s4" / "link" / f"mb_edges.{TAG}.c"
OUT = C.GEN / "s4" / "edge_targets.json"
TABLE = C.GEN / "s4" / "edge_targets.txt"
GROUP = "jmp_edge_target"
EDGE_JMP = 1                          # SA2_MB_EDGE_JMP in the generated table
MAX_ROUNDS = 12
ROW = re.compile(r"\{0x([0-9a-f]+)u, 0x([0-9a-f]+)u, (\d+),")


def survey() -> dict:
    fmap = json.loads((C.GEN / "function-map.json").read_text())["functions"]
    ents = sorted(((int(f["address"], 16) if isinstance(f["address"], str) else f["address"]),
                   max(int(f.get("size") or 0), 1)) for f in fmap)
    starts = [a for a, _ in ents]
    size = dict(ents)

    def owner(v: int) -> int | None:
        i = bisect.bisect_right(starts, v) - 1
        return starts[i] if i >= 0 and starts[i] <= v < starts[i] + size[starts[i]] else None

    state = json.loads(EXTRAS.read_text())["rows"]
    rows = [r for r in state if r.get("compiled") and not r.get("rejected")]
    entries = {a for a, _e, _n in LE.module_ranges("next", "next")} | {int(r["va"], 16) for r in rows}
    # A target lift_extra already tried and did not compile, or rejected, or
    # found in no module's range: not lifted again, named instead, so a round
    # cannot repeat on it until the round limit.
    tried = {int(r["va"], 16): (r.get("error_head") or ("rejected" if r.get("rejected") else "not compiled"))
             for r in state if not (r.get("compiled") and not r.get("rejected"))}
    edges = [(int(t, 16), int(s, 16)) for t, s, k in ROW.findall(EDGES_C.read_text())
             if int(k) == EDGE_JMP]
    lift, inside, failed = {}, {}, {}
    for t, s in edges:
        if t in entries:
            continue
        if t in tried:
            failed[t] = tried[t]
            continue
        o = owner(t)
        lift.setdefault(t, s)
        if o is not None:
            inside.setdefault(t, {"source": f"0x{s:08x}", "map_function": f"0x{o:08x}"})
    return {"jmp_edges": len(edges), "lift": lift, "inside": inside, "failed": failed}


def write(sv: dict) -> None:
    OUT.write_text(json.dumps({
        "what": "tools/s4/edge_targets.py", "tag": TAG, "jmp_edges": sv["jmp_edges"],
        "to_lift": [{"va": f"0x{t:08x}", "jmp_at": f"0x{s:08x}"} for t, s in sorted(sv["lift"].items())],
        "of_them_inside_a_map_function": {f"0x{t:08x}": v for t, v in sorted(sv["inside"].items())},
        "tried_and_not_lifted": {f"0x{t:08x}": str(v)[:200] for t, v in sorted(sv["failed"].items())},
    }, indent=1) + "\n")
    lines = ["# <hex address> <group> <evidence>; tools/s4/edge_targets.py"]
    for t, s in sorted(sv["lift"].items()):
        where = (f"inside map function {sv['inside'][t]['map_function']}, a second entry into it"
                 if t in sv["inside"] else "and no map function holds it")
        lines.append(f"0x{t:08x} {GROUP} the missing-block edge table's jmp at 0x{s:08x}, in "
                     f"reachable lifted code, reaches it; no lifted module starts there, {where}")
    TABLE.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lift", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    for rnd in range(1, MAX_ROUNDS + 1):
        sv = survey()
        write(sv)
        print(f"EDGE_TARGETS round {rnd}: {sv['jmp_edges']} jmp edges; {len(sv['lift'])} targets "
              f"to lift, {len(sv['inside'])} of them inside a map function "
              f"({sorted(f'0x{t:08x}' for t in sv['inside'])[:6]}); tried and not lifted: "
              f"{len(sv['failed'])} {sorted(f'0x{t:08x}' for t in sv['failed'])[:6]}", flush=True)
        if not a.lift or not sv["lift"]:
            return 0
        p = subprocess.run([PY, str(HERE / "lift_extra.py"), "--tag", TAG, "--lift-tag", "next",
                            "--arms-tag", "next", "--seed-arms", "--seed-continuations",
                            "--from-list", str(TABLE), "--group", GROUP, "--jobs", str(a.jobs)],
                           cwd=str(ROOT), capture_output=True, text=True)
        if p.returncode:
            print(p.stdout[-1500:], p.stderr[-1500:])
            return p.returncode
        e = subprocess.run([PY, str(HERE / "mb_edges.py"), "--tag", TAG, "--lift-tag", "next",
                            "--arms-tag", "next", "--allow-from", str(EXTRAS)],
                           cwd=str(ROOT), capture_output=True, text=True)
        if e.returncode:
            print(e.stdout[-1500:], e.stderr[-1500:])
            return e.returncode
    print(f"EDGE_TARGETS_REFUSED no fixed point in {MAX_ROUNDS} rounds")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
