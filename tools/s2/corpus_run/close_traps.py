#!/usr/bin/env python3
"""Close the lifted set over the addresses the runs actually trap at.

The function map's `direct_calls` is a `call` graph: it does not name a `jmp` into
another mapped function's entry, and the CRT's float-to-int and transcendental paths
are full of them (`0x008287C0` ends in `je 0x008287F6`, a different map entry). Such a
jump becomes `__remill_missing_block` at a table address, so the runs report it as a
trap address and the fix is to lift that function too.

This is the fixpoint: run every plan, take the distinct trap addresses that are entries
in the function map, lift them and their `direct_calls` closure, rebuild, run again.
It stops when a round adds nothing, or at --max-rounds.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/close_traps.py [--max-rounds 6] [--limit 64]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "private" / "generated" / "s2" / "corpus_run"
PY = sys.executable
# The mapped image, runtime/guest_memory.c: base 0x00400000, size 0x02375000.
IMAGE_BASE = 0x00400000
IMAGE_END = 0x02775000


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rounds", type=int, default=6)
    # A row prefix is not a fixpoint: `vector-to-angles-v1` first reaches the CRT's
    # pi/2 tail at row 131, so closing over the first 64 rows converges without ever
    # lifting 0x0085A3C8 and the full run then traps there. Whole fixtures cost
    # milliseconds (108604 rows in about 0.2 s), so the default drives all of them.
    ap.add_argument("--limit", type=int, default=0,
                    help="rows per fixture while closing (0 = every row, the default)")
    args = ap.parse_args()

    fmap = {int(f["address"], 16) for f in
            json.loads((ROOT / "private" / "generated" / "function-map.json").read_text())["functions"]}
    history = []
    def link_closed() -> list[str]:
        """Build, and lift whatever the link says is undefined, until it links."""
        added: list[str] = []
        for _ in range(6):
            run([PY, str(HERE / "build.py"), "--variant", "pc24"])
            bl = json.loads((OUT / "build.pc24.json").read_text())
            undef = bl.get("undefined_sub_symbols") or []
            if not undef:
                return added
            added += undef
            mapped = [a for a in undef if int(a, 16) in fmap]
            orphan = [a for a in undef if int(a, 16) not in fmap]
            if mapped:
                run([PY, str(HERE / "lift_set.py"), "--only"] + mapped)
            if orphan:
                run([PY, str(HERE / "lift_set.py"), "--block"] + orphan)
        return added

    for rnd in range(args.max_rounds):
        linked = link_closed()
        p = run([PY, str(HERE / "run.py"), "--limit", str(args.limit),
                 "--out", str(OUT / "run.closing.json")])
        results = json.loads((OUT / "run.closing.json").read_text())["results"]
        traps: set[int] = set()
        for r in results:
            for a in (r.get("trap_addresses") or {}):
                va = int(a, 16)
                if va in fmap:
                    traps.add(va)
        lifted = {int(a, 16) for a, e in
                  json.loads((OUT / "lift.json").read_text())["functions"].items() if e.get("lifted")}
        # A trap address is only a *code* address worth lifting when it is inside the
        # mapped image. `candidate-enumeration-v1` traps at 0xFFFFFFF4 (a load through a
        # null group-list pointer), which is not code: lifting it is impossible, so it
        # was re-proposed every round and the fixpoint never converged.
        #
        # That one was caught by the range test alone, and until the G2b fold a *host*
        # fault address could reach this loop the same way: `runtime/sa2_trap.c` put the
        # low 32 bits of `si_addr` into `exit_pc`, and any host pointer landing in
        # 0x00400000..0x02775000 would have been handed to the lifter as guest code. A
        # host fault is now SA2_TRAP_HOST_FAULT with `exit_pc` 0 and the host address in
        # its own field, so it can never be mistaken for one again; 0 fails the range
        # test below as well, which is belt and braces rather than the only guard.
        orphans: set[int] = set()
        for r in results:
            for a in (r.get("trap_addresses") or {}):
                va = int(a, 16)
                if va not in fmap and IMAGE_BASE <= va < IMAGE_END:
                    orphans.add(va)
        new = sorted(traps - lifted)
        new_orphans = sorted(orphans - lifted)
        history.append({"round": rnd, "trap_entries": [f"0x{a:08x}" for a in sorted(traps)],
                        "newly_lifted": [f"0x{a:08x}" for a in new],
                        "newly_lifted_orphan_blocks": [f"0x{a:08x}" for a in new_orphans],
                        "callees_lifted_to_link": linked,
                        "passing": sum(1 for r in results if r.get("passed")), "of": len(results)})
        print(f"round {rnd}: {history[-1]['passing']}/{len(results)} passing, "
              f"{len(new)} new trap entries, {len(new_orphans)} orphan blocks to lift")
        if not new and not new_orphans:
            break
        if new:
            run([PY, str(HERE / "lift_set.py"), "--only"] + [f"0x{a:08x}" for a in new])
            run([PY, str(HERE / "lift_set.py")])
        if new_orphans:
            run([PY, str(HERE / "lift_set.py"), "--block"] + [f"0x{a:08x}" for a in new_orphans])
    (OUT / "close_traps.json").write_text(json.dumps({"rounds": history}, indent=1) + "\n")


if __name__ == "__main__":
    main()
