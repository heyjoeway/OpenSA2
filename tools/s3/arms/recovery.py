#!/usr/bin/env python3
"""What the arm traces actually recovered, measured against what they cost.

Subtitle: the same instruction-set arithmetic `cost_image.py` predicted, redone
over the traces that really lifted, with every walk checked against the IR
remill really emitted.

`cost_image.py` answered "what would seeding cost" from a model of remill's
recursive descent. This answers "what did it recover" from the run, and it
differs in the two ways that matter:

  * only traces that **lifted and compiled** count, so a failed trace shows up
    as instructions still missing rather than as instructions recovered;
  * every trace's walk is validated against `pc_markers_in_define` from the
    run's own log -- remill publishes PC once per decoded instruction and once
    per exit block -- and a trace whose walk disagrees is excluded from the
    recovered set and listed, because an address set that does not reproduce
    the module's own markers is not evidence about that module.

Reported, all generated:

  recovered        instructions in a compiled arm trace and in no whole-image
                   module, against slice 2's 129 638;
  arms_absent      arms whose own address is still in no module;
  duplication      instructions lifted by the arm traces over instructions
                   recovered, against the 3.491x estimate;
  growth           IR and object bytes, measured, against the prediction of
                   3.46 GB -> ~5.0 GB and 259 MB -> ~372 MB.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/recovery.py --tag next [--jobs 16]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
from cost import walk  # noqa: E402

OUT = C.GEN / "s3" / "arms"
_IMAGE: bytes | None = None

# slice 2's published figures, the denominators this tool is checked against
SLICE2 = {
    "instructions_in_a_module": 1039542,
    "instructions_in_no_module": 129638,
    "ir_bytes_after_fold": 3459465434,
    "ir_bytes_raw_remill": 3460562350,
    "ir_bytes_after_pc24_pass": 3494244289,
    "object_bytes": 259220504,
    "modules": 12683,
    "predicted_duplication_factor": 3.491,
    "predicted_ir_bytes_after_fold": 5.0e9,
    "predicted_object_bytes": 372e6,
}


def _init() -> None:
    global _IMAGE
    _IMAGE = C.memory_image()


def unseeded_one(job: tuple[int, int]) -> list[int]:
    va, hi = job
    seen, _ = walk(_IMAGE, va, va, hi)
    return sorted(seen)


def arm_one(job: tuple[str, str, str, int]) -> tuple[str, list[int], int, int]:
    """(arm, decoded addresses, walk+exits, recorded IR markers)."""
    arm, lo, hi, markers = job
    seen, exits = walk(_IMAGE, int(arm, 16), int(lo, 16), int(hi, 16))
    return arm, sorted(seen), len(seen) + exits, markers


def dir_bytes(path: Path, suffix: str) -> tuple[int, int]:
    n = total = 0
    if not path.is_dir():
        return 0, 0
    for entry in os.scandir(path):
        if entry.is_file() and entry.name.endswith(suffix):
            total += entry.stat().st_size
            n += 1
    return n, total


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

    # ---- 1. what is in a module without any arm seeding
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
    print(f"whole-image modules cover {len(unseeded)} instructions "
          f"({time.time() - t0:.1f}s)", flush=True)

    # ---- 2. what the arm traces cover, validated trace by trace
    jobs = [(r["arm"], r["anchor"][0], r["anchor"][1], r.get("pc_markers_in_define", 0))
            for r in arms_log.values() if r.get("compiled")]
    arm_union: set[int] = set()
    lifted_instructions = 0
    disagreements: list[dict] = []
    arm_sets: dict[str, list[int]] = {}
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
        for arm, seen, walk_plus_exits, markers in ex.map(arm_one, jobs, chunksize=32):
            if walk_plus_exits != markers:
                disagreements.append({"arm": arm, "walk_plus_exits": walk_plus_exits,
                                      "ir_pc_markers": markers})
                continue
            arm_sets[arm] = seen
            arm_union.update(seen)
            lifted_instructions += len(seen)
    recovered = arm_union - unseeded
    print(f"{len(arm_sets)} validated traces, {len(recovered)} instructions recovered "
          f"({time.time() - t0:.1f}s)", flush=True)

    # ---- 2b. the 129 638 itself, recomputed, so the fraction has a denominator
    #
    # `cost_image.py` predicted the absent set with every arm walked inside its
    # *owner's* byte range. Eight arms of two owners lie outside it -- the map
    # under-reports those owners' sizes -- so the model walked them over a range
    # that does not contain them and scored them at zero instructions.
    # `armlist.py` anchors each arm on the range that does contain it, so the run
    # recovers instructions the estimate never counted. Reporting
    # `recovered / 129 638` as a percentage of a set the numerator is not a subset
    # of is exactly the kind of true-but-misleading number the operating notes
    # name; the intersection is computed instead.
    ojobs = []
    for i, va in enumerate(starts):
        fn = fmap[va]
        if not (lift.get(f"0x{va:08x}") or {}).get("lifted"):
            continue
        hi = starts[i + 1] if i + 1 < len(starts) else va + fn["size"]
        arms = sorted({int(t, 16)
                       for jt in (fn.get("jump_tables") or []) for t in jt["targets"]})
        for a in arms:
            ojobs.append((f"0x{a:08x}", f"0x{va:08x}", f"0x{hi:08x}", 0))
    predicted_union: set[int] = set()
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
        for _a, seen, _w, _m in ex.map(arm_one, ojobs, chunksize=32):
            predicted_union.update(seen)
    predicted_absent = predicted_union - unseeded      # slice 2's 129 638
    print(f"the estimate's absent set recomputes to {len(predicted_absent)}",
          flush=True)

    # ---- 3. arms still in no module
    covered = unseeded | arm_union
    all_arms = sorted({int(t, 16) for va in starts
                       for jt in (fmap[va].get("jump_tables") or [])
                       for t in jt["targets"]})
    absent = [a for a in all_arms if a not in covered]
    by_region: collections.Counter = collections.Counter()
    for a in absent:
        i = bisect.bisect_right(starts, a) - 1
        by_region[fmap[starts[i]]["region"] if i >= 0 else "?"] += 1

    # ---- 3b. decoded coverage per region, for the build that ships
    #
    # `indirect.py` publishes this for the arms-free lift, and that figure
    # (game 846518/971553, 87.1 %) is what METRICS.md carried while the
    # shipping build already contained the arms. A coverage number that
    # describes a build nobody links is the project's recurring defect --
    # a number present and meaning something else -- so the seeded figure is
    # computed here, on the same `covered` set the rest of this tool uses.
    def region_of(addr: int) -> str:
        i = bisect.bisect_right(starts, addr) - 1
        return fmap[starts[i]]["region"] if i >= 0 else "?"

    # The numerator has to live in the same universe as the denominator.
    # Ghidra counts a function's instructions inside [va, va+size); the lift
    # uses the wider --to-next-entry bound, so `covered` holds addresses above
    # a function's recorded end. Counting those in the numerator and not the
    # denominator is the two-range defect the G3 interrogation already found
    # once in `indirect.py`, and the first version of this block inherited it
    # one layer down. The ratio is now stated over the map's own extents, and
    # the out-of-extent addresses are reported beside it as what they are: a
    # consequence of the 691 function-size under-reports, not extra coverage.
    mapped_by_region: collections.Counter = collections.Counter()
    covered_by_region: collections.Counter = collections.Counter()
    outside_by_region: collections.Counter = collections.Counter()
    extents = []
    for va in starts:
        fn = fmap[va]
        mapped_by_region[fn["region"]] += int(fn.get("instructions") or 0)
        extents.append((va, va + int(fn.get("size") or 0)))

    def inside_its_owner_extent(addr: int) -> bool:
        i = bisect.bisect_right(starts, addr) - 1
        return i >= 0 and extents[i][0] <= addr < extents[i][1]

    for addr in covered:
        region = region_of(addr)
        if inside_its_owner_extent(addr):
            covered_by_region[region] += 1
        else:
            outside_by_region[region] += 1
    coverage_by_region = {
        region: {
            "mapped_instructions": mapped_by_region[region],
            "decoded_into_a_module": covered_by_region.get(region, 0),
            "percent_decoded": round(100 * covered_by_region.get(region, 0)
                                     / mapped_by_region[region], 2)
            if mapped_by_region[region] else None,
            "decoded_above_the_owner_recorded_end": outside_by_region.get(region, 0),
        }
        for region in sorted(mapped_by_region)
    }

    # ---- 4. failures, grouped by normalised error
    def normalise(text: str) -> str:
        t = re.sub(r"0x[0-9a-fA-F]+", "0x<addr>", text or "")
        t = re.sub(r"/[^\s:]+/", "<path>/", t)
        t = re.sub(r"\b\d{3,}\b", "<n>", t)
        return t.strip().splitlines()[0][:200] if t.strip() else "(no message)"

    groups: dict[str, dict] = {}
    for r in sorted(arms_log.values(), key=lambda x: x["va"]):
        if r.get("compiled"):
            continue
        stage = ("lift" if not r.get("lifted")
                 else "pass" if not r.get("pass", {}).get("ok")
                 else "compile")
        msg = normalise(r.get("error_head") if stage != "compile"
                        else r.get("compile_error_head", ""))
        key = f"{stage}: {msg}"
        g = groups.setdefault(key, {"stage": stage, "error": msg, "traces": 0,
                                    "first_arms": [], "instructions_lost": 0})
        g["traces"] += 1
        if len(g["first_arms"]) < 8:
            g["first_arms"].append(r["va"])
    # what the failures cost, counted as instructions the failed traces would
    # have recovered and nothing else did
    if groups:
        fjobs = [(r["arm"], r["anchor"][0], r["anchor"][1], 0)
                 for r in arms_log.values() if not r.get("compiled")]
        with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init) as ex:
            failed_union: set[int] = set()
            for arm, seen, _w, _m in ex.map(arm_one, fjobs, chunksize=8):
                failed_union.update(seen)
        lost = failed_union - covered
    else:
        lost = set()

    # ---- 5. what it cost on disk
    n_raw, b_raw = dir_bytes(OUT / "lift" / args.tag, ".ll")
    n_pc24, b_pc24 = dir_bytes(OUT / "lift" / args.tag, ".pc24.ll")
    b_raw -= b_pc24                                   # the raw .ll only
    n_raw -= n_pc24
    n_fold, b_fold = dir_bytes(OUT / "fold" / args.tag, ".pc24.ll")
    n_obj, b_obj = dir_bytes(OUT / "build" / args.tag, ".o")

    doc = {
        "tag": args.tag,
        "slice2_baseline": SLICE2,
        "traces": {
            "in_the_work_list": armlist["traces"],
            "attempted": len(arms_log),
            "lifted": sum(1 for r in arms_log.values() if r.get("lifted")),
            "compiled": sum(1 for r in arms_log.values() if r.get("compiled")),
            "failed": sum(1 for r in arms_log.values() if not r.get("compiled")),
            "walk_validated_against_remill_markers": len(arm_sets),
            "walk_disagreeing_with_remill_markers": len(disagreements),
            "disagreement_examples": disagreements[:10],
        },
        "failures": sorted(groups.values(), key=lambda g: -g["traces"]),
        "instructions_the_failures_cost": len(lost),
        "instructions_the_failures_cost_as_percent_of_129638":
            round(100 * len(lost) / SLICE2["instructions_in_no_module"], 3),
        "recovery": {
            "instructions_in_a_module_before": len(unseeded),
            "instructions_in_a_module_before_slice2_said": SLICE2["instructions_in_a_module"],
            "the_129638_recomputed_here": len(predicted_absent),
            "slice2_published": SLICE2["instructions_in_no_module"],
            "instructions_recovered": len(recovered),
            "of_the_129638_recovered": len(predicted_absent & recovered),
            "of_the_129638_still_in_no_module": len(predicted_absent - recovered),
            "percent_of_the_129638_recovered":
                round(100 * len(predicted_absent & recovered) / len(predicted_absent), 3)
                if predicted_absent else None,
            "recovered_beyond_the_estimate": len(recovered - predicted_absent),
            "why_beyond": "the estimate walked every arm inside its owner's byte range; "
                          "eight arms lie outside it because the map under-reports two "
                          "owners' sizes, so the estimate scored them at zero "
                          "instructions. armlist.py anchors an arm on the range that "
                          "contains it, and those traces decode real code.",
            "guest_instructions_covered_after": len(covered),
            "mapped_instructions": sum(mapped_by_region.values()),
            "decoded_inside_the_map_extents": sum(covered_by_region.values()),
            "decoded_above_the_owner_recorded_end": sum(outside_by_region.values()),
            "percent_decoded_after":
                round(100 * sum(covered_by_region.values())
                      / sum(mapped_by_region.values()), 3)
                if mapped_by_region else None,
            "why_not_len_covered": "an address above a function's recorded end is in "
                                   "the lift and not in Ghidra's instruction count, so "
                                   "counting it here would put the numerator in a wider "
                                   "universe than the denominator",
            "by_region": coverage_by_region,
            "measures": "the build that ships, whole-image modules plus arm traces; "
                        "indirect.py's decoded_coverage measures the arms-free lift "
                        "and is not the same build",
        },
        "duplication": {
            "instructions_lifted_by_the_arm_traces": lifted_instructions,
            "instructions_recovered": len(recovered),
            "measured_factor": round(lifted_instructions / len(recovered), 3)
            if recovered else None,
            "estimate_being_checked": SLICE2["predicted_duplication_factor"],
            "copies_per_covered_instruction":
                round((len(unseeded) + lifted_instructions) / len(covered), 3)
                if covered else None,
        },
        "arms": {
            "arms_in_the_map": len(all_arms),
            "arms_still_in_no_module": len(absent),
            "arms_still_in_no_module_by_region": dict(by_region),
            "first_arms_still_absent": [f"0x{a:08x}" for a in absent[:20]],
        },
        "growth": {
            "arm_ir_bytes_raw_remill": b_raw,
            "arm_ir_bytes_after_pc24_pass": b_pc24,
            "arm_ir_bytes_after_fold": b_fold,
            "arm_object_bytes": b_obj,
            "arm_modules": n_obj,
            "ir_bytes_after_fold_total": SLICE2["ir_bytes_after_fold"] + b_fold,
            "ir_bytes_after_fold_predicted": SLICE2["predicted_ir_bytes_after_fold"],
            "object_bytes_total": SLICE2["object_bytes"] + b_obj,
            "object_bytes_predicted": SLICE2["predicted_object_bytes"],
            "ir_growth_percent": round(100 * b_fold / SLICE2["ir_bytes_after_fold"], 2),
            "object_growth_percent": round(100 * b_obj / SLICE2["object_bytes"], 2),
            "modules_total": SLICE2["modules"] + n_obj,
        },
        "seconds": round(time.time() - t0, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"recovery.{args.tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "failures"}, indent=1))
    print(f"failure groups: {len(doc['failures'])}")
    for g in doc["failures"][:12]:
        print(f"  {g['traces']:5d}  {g['stage']}: {g['error'][:120]}  "
              f"first={g['first_arms'][:3]}")
    print(f"-> {OUT / f'recovery.{args.tag}.json'}")


if __name__ == "__main__":
    main()
