#!/usr/bin/env python3
"""Does seeding the arms once expose tables that a second round would have to
seed again?

Subtitle: count the indirect branches the arm traces stop at, and ask of each
one whether its targets are already in a module.

The G3 interrogation found 110 indirect branches that are themselves reachable
only through a jump table: code the whole-image lift never decoded, so the
tables at those sites were invisible to it. Seeding the arms decodes that code,
and the worry is a cascade -- each round exposes the next round's tables.

The worry is checkable rather than arguable, because the analyzer's map already
resolved every table it could, *statically*, whether or not remill ever decoded
the site. So the question is not "what new tables appear" but "does the work
list `armlist.py` built already contain their arms". Three populations are
counted separately:

  newly_visible_sites   map jump-table sites decoded by an arm trace and by no
                        whole-image module -- the interrogation's 110;
  targets_uncovered     their targets that are in no module after this round;
  unresolved_exits      indirect branches an arm trace stops at that the map
                        has no table for -- computed jumps nothing resolves,
                        which are not a second-iteration cost because a second
                        iteration would have nothing to seed them with.

`__remill_jump` sites are counted from the run's own log, raw and after the
fold, for the same reason slice 2 counted them in the whole image: they are
what will trap at run time.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/second_order.py --tag next [--jobs 16]
"""
from __future__ import annotations

import argparse
import bisect
import collections
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
from cost import walk, walk_detail  # noqa: E402

OUT = C.GEN / "s3" / "arms"
_IMAGE: bytes | None = None


def _init() -> None:
    global _IMAGE
    _IMAGE = C.memory_image()


def unseeded_one(job: tuple[int, int]) -> list[int]:
    va, hi = job
    seen, _ = walk(_IMAGE, va, va, hi)
    return sorted(seen)


def arm_detail(job: tuple[str, str, str]) -> tuple[str, list[int], list[int]]:
    """(arm, decoded addresses, indirect-branch exits inside the byte range)."""
    from iced_x86 import Decoder, FlowControl
    arm, lo, hi = job
    lo_i, hi_i = int(lo, 16), int(hi, 16)
    seen, exits = walk_detail(_IMAGE, int(arm, 16), lo_i, hi_i)
    indirect = []
    for e in exits:
        if not lo_i <= e < hi_i:          # an edge that left the range, not a branch
            continue
        ins = Decoder(32, _IMAGE[e - C.IMAGE_BASE:e - C.IMAGE_BASE + 16], ip=e).decode()
        if ins.flow_control == FlowControl.INDIRECT_BRANCH:
            indirect.append(e)
    return arm, sorted(seen), sorted(indirect)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    args = ap.parse_args()
    t0 = time.time()

    fmap = C.by_address()
    starts = sorted(fmap)
    lift = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")
    arms_log = C.read_jsonl(OUT / f"arms.{args.tag}.jsonl")
    armlist = json.loads((OUT / f"armlist.{args.tag}.json").read_text())
    in_work_list = {row["arm"] for row in armlist["work"]}

    # every table the analyzer resolved, by the site of the branch
    site_targets: dict[int, list[int]] = {}
    site_owner: dict[int, int] = {}
    for va in starts:
        for jt in fmap[va].get("jump_tables") or []:
            at = int(jt["at"], 16)
            site_targets.setdefault(at, [])
            site_targets[at] += [int(t, 16) for t in jt.get("targets", [])]
            site_owner.setdefault(at, va)

    ujobs = []
    for i, va in enumerate(starts):
        if not (lift.get(f"0x{va:08x}") or {}).get("lifted"):
            continue
        hi = starts[i + 1] if i + 1 < len(starts) else va + fmap[va]["size"]
        ujobs.append((va, hi))
    unseeded: set[int] = set()
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
        for s in ex.map(unseeded_one, ujobs, chunksize=32):
            unseeded.update(s)

    jobs = [(r["arm"], r["anchor"][0], r["anchor"][1])
            for r in arms_log.values() if r.get("compiled")]
    arm_union: set[int] = set()
    indirect_exits: collections.Counter = collections.Counter()
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
        for _arm, seen, indirect in ex.map(arm_detail, jobs, chunksize=32):
            arm_union.update(seen)
            indirect_exits.update(indirect)
    covered = unseeded | arm_union
    print(f"{len(jobs)} traces walked, {len(indirect_exits)} distinct indirect-branch "
          f"exits ({time.time() - t0:.1f}s)", flush=True)

    # ---- the interrogation's 110: table sites only an arm trace decodes
    newly_visible = sorted(s for s in site_targets if s in arm_union and s not in unseeded)
    sites_never_decoded = sorted(s for s in site_targets if s not in covered)
    uncovered_targets: list[int] = []
    new_work: list[dict] = []
    for s in newly_visible:
        for t in sorted(set(site_targets[s])):
            if t in covered:
                continue
            uncovered_targets.append(t)
            new_work.append({"site": f"0x{s:08x}", "target": f"0x{t:08x}",
                             "owner": f"0x{site_owner[s]:08x}",
                             "already_in_the_work_list": f"0x{t:08x}" in in_work_list})

    # every uncovered target of every resolved table, not only the newly visible
    # ones -- the honest second-round work list
    all_uncovered = sorted({t for s, ts in site_targets.items() for t in set(ts)
                            if t not in covered})

    # indirect branches an arm trace stops at that no table explains
    unresolved = sorted(e for e in indirect_exits if e not in site_targets)
    unresolved_new = [e for e in unresolved if e not in unseeded]

    # what a second round would cost, if it had anything to seed
    cost_instructions = 0
    if all_uncovered:
        def rng(a: int) -> tuple[int, int]:
            i = bisect.bisect_right(starts, a) - 1
            lo = starts[i]
            hi = starts[i + 1] if i + 1 < len(starts) else lo + fmap[lo]["size"]
            return lo, hi
        cjobs = [(f"0x{a:08x}", f"0x{rng(a)[0]:08x}", f"0x{rng(a)[1]:08x}")
                 for a in all_uncovered]
        with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
            for _a, seen, _i in ex.map(arm_detail, cjobs, chunksize=8):
                cost_instructions += len(seen)

    jump_raw = sum(r.get("remill_jump_calls", 0) for r in arms_log.values()
                   if r.get("compiled"))
    jump_fold = sum((r.get("after_fold") or {}).get("remill_jump_calls", 0)
                    for r in arms_log.values() if r.get("compiled"))
    with_jump = sum(1 for r in arms_log.values()
                    if r.get("compiled") and (r.get("after_fold") or {}).get(
                        "remill_jump_calls", 0))

    doc = {
        "tag": args.tag,
        "question": "does seeding once expose tables a second round must seed?",
        "remill_jump_sites_in_the_arm_traces": {
            "raw_modules": jump_raw,
            "after_the_fold": jump_fold,
            "traces_carrying_at_least_one": with_jump,
            "traces_compiled": len(jobs),
            "whole_image_after_slice2": 917,
            "note": "an indirect branch remill could not resolve to a block; every one "
                    "dispatches through the function table at run time",
        },
        "jump_table_sites": {
            "resolved_by_the_analyzer": len(site_targets),
            "decoded_only_by_an_arm_trace": len(newly_visible),
            "decoded_only_by_an_arm_trace_first": [f"0x{s:08x}" for s in newly_visible[:20]],
            "in_no_module_even_now": len(sites_never_decoded),
            "in_no_module_even_now_first": [f"0x{s:08x}" for s in sites_never_decoded[:20]],
            "interrogation_said": 110,
        },
        # The interrogation counted indirect *branches* reachable only through a
        # table. Split by whether the analyzer resolved a table for them, they are
        # the 109 above plus the ones nothing resolved, and the two populations
        # have different fates: the first is finished by this round, the second
        # cannot be finished by any lift.
        "interrogation_reconciliation": {
            "resolved_table_sites_decoded_only_by_an_arm_trace": len(newly_visible),
            "unresolved_indirect_branches_decoded_only_by_an_arm_trace":
                len(unresolved_new),
            "total": len(newly_visible) + len(unresolved_new),
            "interrogation_said": 110,
        },
        "second_round_work": {
            "targets_of_newly_visible_tables_still_in_no_module": len(uncovered_targets),
            "targets_of_any_resolved_table_still_in_no_module": len(all_uncovered),
            "first": [f"0x{a:08x}" for a in all_uncovered[:20]],
            "of_those_already_in_this_round_s_work_list":
                sum(1 for w in new_work if w["already_in_the_work_list"]),
            "instructions_a_second_round_would_lift": cost_instructions,
            "examples": new_work[:20],
        },
        "indirect_branches_no_table_explains": {
            "distinct_sites_the_arm_traces_stop_at": len(unresolved),
            "of_those_in_no_whole_image_module": len(unresolved_new),
            "first": [f"0x{e:08x}" for e in unresolved[:20]],
            "why_a_second_round_does_not_help": "there is no target list to seed with; "
                    "these need a resolved table from the analyzer, not another lift",
        },
        "answer": None,
        "seconds": round(time.time() - t0, 2),
    }
    doc["answer"] = (
        "no second iteration: every target of every table the analyzer resolved is "
        "already in a module"
        if not all_uncovered else
        f"a second iteration has {len(all_uncovered)} arms to seed, "
        f"{cost_instructions} instructions, about "
        f"{round(100 * len(all_uncovered) / max(armlist['traces'], 1), 2)} % of this "
        f"round's trace count")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"second_order.{args.tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1)[:4000])
    print(f"-> {OUT / f'second_order.{args.tag}.json'}")


if __name__ == "__main__":
    main()
