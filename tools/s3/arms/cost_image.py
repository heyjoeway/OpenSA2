#!/usr/bin/env python3
"""The arm-seeding duplication factor, image-wide, recomputed from the map.

Subtitle: the same walk `tools/s3/arms/cost.py` validated on one function,
run over all 12 683, so the G3 gate report's 3.49x estimate can be checked
rather than quoted.

For every map function, on the adopted `--to-next-entry` bound:

  unseeded   remill's recursive descent from the entry -- what the whole-image
             lift contains today;
  seeded     one further descent per distinct jump-table arm -- what
             `--entry_address` per arm would add.

  lifted     instructions decoded by the arm traces, counted once per trace,
             which is what they cost in IR and object bytes;
  recovered  instructions in some arm trace and in no unseeded trace anywhere
             in the image, which is what they buy.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/cost_image.py [--jobs 16] [--tag next]
"""
from __future__ import annotations

import argparse
import bisect
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
from cost import walk  # the walk validated against remill's own IR markers  # noqa: E402

OUT = C.GEN / "s3" / "arms"
_IMAGE: bytes | None = None


def _init() -> None:
    global _IMAGE
    _IMAGE = C.memory_image()


def one(job: tuple[int, int, list[int]]) -> tuple[int, list[int], list[list[int]], list[int]]:
    va, hi, arms = job
    unseeded, _ = walk(_IMAGE, va, va, hi)
    out, needed = [], []
    for a in arms:
        s, _ = walk(_IMAGE, a, va, hi)
        out.append((a, sorted(s)))
        # An arm direct control flow already reaches needs no trace of its own,
        # only a table row pointing at the owner's define -- which cannot work,
        # because remill's define ignores `%program_counter` (slice 2 `[d3]`).
        # Counted, not assumed.
        if a not in unseeded:
            needed.append(a)
    return va, sorted(unseeded), out, needed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--tag", default="next")
    args = ap.parse_args()

    fmap = C.by_address()
    starts = sorted(fmap)
    lift = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")
    jobs = []
    pairs = 0
    for i, va in enumerate(starts):
        fn = fmap[va]
        hi = starts[i + 1] if i + 1 < len(starts) else va + fn["size"]
        rec = lift.get(f"0x{va:08x}")
        if not (rec and rec.get("lifted")):
            continue
        arms = sorted({int(t, 16)
                       for jt in (fn.get("jump_tables") or []) for t in jt["targets"]})
        pairs += len(arms)
        jobs.append((va, hi, arms))

    print(f"{len(jobs)} lifted functions, {pairs} distinct (owner, arm) pairs", flush=True)
    t0 = time.time()
    all_unseeded: set[int] = set()
    arm_union: set[int] = set()
    lifted = lifted_needed = 0
    arm_traces = arm_traces_needed = 0
    arms_that_are_map_entries = []
    entries = set(starts)
    per_function = []
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
        for n, (va, unseeded, arm_sets, needed) in enumerate(
                ex.map(one, jobs, chunksize=32), 1):
            all_unseeded |= set(unseeded)
            nset = set(needed)
            for a, s in arm_sets:
                arm_union |= set(s)
                lifted += len(s)
                arm_traces += 1
                if a in nset:
                    lifted_needed += len(s)
                    arm_traces_needed += 1
                if a in entries:
                    arms_that_are_map_entries.append(f"0x{a:08x}")
            if arm_sets:
                per_function.append({"owner": f"0x{va:08x}", "arms": len(arm_sets),
                                     "unseeded": len(unseeded),
                                     "lifted_by_arms": sum(len(s) for _a, s in arm_sets)})
            if n % 2000 == 0:
                print(f"  {n}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)

    recovered = arm_union - all_unseeded
    doc = {
        "tag": args.tag,
        "functions": len(jobs),
        "arm_traces": arm_traces,
        "instructions_in_a_module_today": len(all_unseeded),
        "instructions_lifted_by_the_arm_traces": lifted,
        "instructions_recovered": len(recovered),
        "duplication_factor_lifted_over_recovered":
            round(lifted / len(recovered), 3) if recovered else None,
        "arm_traces_after_dropping_arms_direct_flow_already_reaches": arm_traces_needed,
        "instructions_lifted_after_that_drop": lifted_needed,
        "duplication_factor_after_that_drop":
            round(lifted_needed / len(recovered), 3) if recovered else None,
        "arms_that_are_already_map_entries": sorted(set(arms_that_are_map_entries)),
        "arms_that_are_already_map_entries_count": len(set(arms_that_are_map_entries)),
        "g3_estimate": {"lifted": 452533, "recovered": 130310, "factor": 3.49},
        "instructions_in_a_module_after": len(all_unseeded) + lifted,
        "guest_instructions_covered_after": len(all_unseeded | arm_union),
        "copies_per_covered_instruction":
            round((len(all_unseeded) + lifted) / len(all_unseeded | arm_union), 3),
        "wall_seconds": round(time.time() - t0, 1),
        "top_20_by_cost": sorted(per_function, key=lambda r: -r["lifted_by_arms"])[:20],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"cost.image.{args.tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "top_20_by_cost"}, indent=1))
    print(f"-> {OUT / f'cost.image.{args.tag}.json'}")


if __name__ == "__main__":
    main()
