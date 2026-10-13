#!/usr/bin/env python3
"""The image-wide arm work list, and the assertion that keeps it linkable.

Subtitle: one (containing range, arm) trace per jump-table arm, refusing to
emit any arm whose symbol the whole-image build already defines.

**The rule is a symbol, not a decode.** Slice 4 dropped the 343 arms that
remill's recursive descent already reaches from the owner's entry, on the
premise that direct flow reaches them so they need no new trace. The premise is
true. The corollary it carried -- that they therefore need no *table row* -- is
false, and slice 6 measured the cost of it against the linked binary: 343 of
the 649 remaining unresolved dispatch targets were exactly those arms. Their
code is lifted, inside the owner's module, as an unnamed basic block; no object
defines `sub_<arm>`; an indirect jump to one is a function-table miss and traps
`SA2_TRAP_CONTROL_LEFT`.

Entering the owner instead does not work and is not a matter of opinion:
`harness/owner_entry_probe.c` registers `sub_439570` under its own arm
0x00439592 in a linked binary and drives it, and 8 of 10 inputs come back with
another arm's answer and **nothing traps**. remill's lifted functions do not
select a block by `%program_counter`; the entry block stores it into `%NEXT_PC`
and branches unconditionally into the entry instruction's block.

So the work list is every distinct arm, and the direct-flow walk stays only to
say how many of them direct flow also reaches -- a reconciliation number, not a
filter. `--drop-reached` reproduces slice 4's list, and the coverage assertion
below then refuses, which is how the corollary is kept dead.

`tools/s3/arms/cost_image.py` costed the arm-seeding fix. This turns that into
the actual work list the lifter consumes, and it fixes two things the cost model
did not have to care about because it never handed a byte range to remill:

1. **An arm is anchored on the range that contains it, not on its owner's.**
   Eight arms of two owners lie outside their owner's `--to-next-entry` range,
   because the analyzer under-reports those owners' sizes (S3_INPUTS section 1)
   and the wider bound truncates at the next map entry, which for these two
   arrives long before their jump tables end. Handing remill a byte blob that
   does not contain `--entry_address` decodes nothing. For all but those eight
   the containing `--to-next-entry` range **is** the owner's range, so this rule
   is a strict generalisation of what the six-arm experiment did, not a change
   to it.

2. **Four arms are also map entries, and seeding them is a link failure.**
   `0x006d09b0`, `0x006d11b0`, `0x0079b3e0` and `0x0079ba70` already have a
   module and a function-table row; a trace seeded at one of them emits a
   second `define @sub_<arm>` and the link dies with a duplicate symbol
   (`tools/s3/arms/dup_probe.py` demonstrates the failure). They are skipped.

The skip is not written as "skip these four addresses". It is written as the
invariant that makes them wrong -- *no arm trace may define a symbol the
whole-image build already defines* -- checked against
`build/<tag>.jsonl`'s recorded defines, so an arm that becomes a map entry
after a future re-analysis is caught by the same check rather than by nobody.
`--no-dedupe` reproduces the failure on demand: it keeps the offenders and the
assertion refuses, which is how a check is shown to be able to fail.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/armlist.py --tag next [--jobs 16]
                                            [--no-dedupe] [--drop-reached]
"""
from __future__ import annotations

import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
from cost import walk  # the descent validated against remill's own IR markers  # noqa: E402

OUT = C.GEN / "s3" / "arms"
_IMAGE: bytes | None = None


def _init() -> None:
    global _IMAGE
    _IMAGE = C.memory_image()


def reached_by_direct_flow(job: tuple[int, int, list[int]]) -> tuple[int, list[int]]:
    """Which of an owner's arms remill already decodes from the owner's entry.

    Identical to `cost_image.one`'s membership test, deliberately: the count
    this produces has to reconcile with `cost.image.<tag>.json` or one of the
    two is wrong.
    """
    va, hi, arms = job
    unseeded, _ = walk(_IMAGE, va, va, hi)
    return va, [a for a in arms if a in unseeded]


def build(tag: str, jobs: int, dedupe: bool = True, drop_reached: bool = False) -> dict:
    fmap = C.by_address()
    starts = sorted(fmap)
    lift = C.read_jsonl(C.LIFT / f"{tag}.jsonl")
    build_log = C.read_jsonl(C.BUILD / f"{tag}.jsonl")

    already_defined: dict[str, str] = {}
    for rec in build_log.values():
        if rec.get("compiled"):
            for sym in rec.get("defines", []):
                already_defined.setdefault(sym, rec["va"])

    def containing(addr: int) -> tuple[int, int]:
        """The adopted `--to-next-entry` range that contains `addr`."""
        i = bisect.bisect_right(starts, addr) - 1
        lo = starts[i]
        hi = starts[i + 1] if i + 1 < len(starts) else lo + fmap[lo]["size"]
        return lo, hi

    walk_jobs, owner_of = [], {}
    for i, va in enumerate(starts):
        fn = fmap[va]
        if not (lift.get(f"0x{va:08x}") or {}).get("lifted"):
            continue
        hi = starts[i + 1] if i + 1 < len(starts) else va + fn["size"]
        arms = sorted({int(t, 16)
                       for jt in (fn.get("jump_tables") or []) for t in jt["targets"]})
        if not arms:
            continue
        for a in arms:
            owner_of.setdefault(a, []).append(va)
        walk_jobs.append((va, hi, arms))

    t0 = time.time()
    reached: set[int] = set()
    with cf.ProcessPoolExecutor(max_workers=jobs, initializer=_init) as ex:
        for _va, hit in ex.map(reached_by_direct_flow, walk_jobs, chunksize=32):
            reached.update(hit)

    work, also_reached, skipped_reached, skipped_defined, reanchored = [], [], [], [], []
    shared_arms = {f"0x{a:08x}": [f"0x{o:08x}" for o in os_]
                   for a, os_ in owner_of.items() if len(os_) > 1}
    for a in sorted(owner_of):
        owner = owner_of[a][0]
        if a in reached:
            # Direct flow already decodes this arm inside the owner's module, so
            # its *code* is lifted. That is not a symbol, and dispatch needs a
            # symbol, so it is seeded anyway. `--drop-reached` restores slice 4's
            # behaviour so the coverage assertion can be seen to fail.
            (skipped_reached if drop_reached else also_reached).append(f"0x{a:08x}")
            if drop_reached:
                continue
        sym = f"sub_{a:x}"
        if sym in already_defined:
            skipped_defined.append({"arm": f"0x{a:08x}", "symbol": sym,
                                    "defined_by_module": already_defined[sym]})
            if dedupe:
                continue
        lo, hi = containing(a)
        i = bisect.bisect_right(starts, owner)
        ohi = starts[i] if i < len(starts) else owner + fmap[owner]["size"]
        if lo != owner:
            reanchored.append({"arm": f"0x{a:08x}", "owner": f"0x{owner:08x}",
                               "owner_range": [f"0x{owner:08x}", f"0x{ohi:08x}"],
                               "anchored_on": [f"0x{lo:08x}", f"0x{hi:08x}"]})
        work.append({"arm": f"0x{a:08x}", "owner": f"0x{owner:08x}",
                     "lo": f"0x{lo:08x}", "hi": f"0x{hi:08x}",
                     "bytes": hi - lo,
                     "region": fmap[owner]["region"],
                     "owner_name": fmap[owner]["name"]})

    doc = {
        "tag": tag,
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dedupe_map_entry_arms": dedupe,
        "owners_with_jump_tables_lifted": len(walk_jobs),
        "distinct_arms": len(owner_of),
        "arms_shared_by_more_than_one_owner": shared_arms,
        "drop_arms_reached_by_direct_flow": drop_reached,
        # How many arms remill's recursive descent also reaches from the owner's
        # entry. A reconciliation number: their code is in the owner's module
        # either way, and they are in the work list because dispatch needs a
        # symbol and a block is not one.
        "arms_reached_by_direct_flow": len(also_reached) + len(skipped_reached),
        "arms_reached_by_direct_flow_seeded_anyway": len(also_reached),
        "arms_reached_by_direct_flow_dropped": len(skipped_reached),
        # The addresses themselves: slice 6 measured these as 343 of the 649
        # unresolved dispatch targets, and anything that wants to say "this
        # exercises one of them" needs the list rather than the count.
        "arms_reached_by_direct_flow_list": sorted(also_reached + skipped_reached),
        "arms_whose_symbol_the_whole_image_build_already_defines": skipped_defined,
        "arms_reanchored_off_their_owner_range": reanchored,
        "traces": len(work),
        "whole_image_defines": len(already_defined),
        "walk_seconds": round(time.time() - t0, 2),
        "work": work,
    }
    return doc


def every_arm_of_a_lifted_owner(tag: str) -> set[int]:
    """The distinct arm set, recomputed from the map and the lift log.

    Deliberately not read back out of `build()`'s own bookkeeping: the coverage
    assertion below is the one that would have caught slice 4's dropped 343, and
    a check that consults the thing it is checking cannot catch it.
    """
    fmap = C.by_address()
    lift = C.read_jsonl(C.LIFT / f"{tag}.jsonl")
    out: set[int] = set()
    for va, fn in fmap.items():
        if not (lift.get(f"0x{va:08x}") or {}).get("lifted"):
            continue
        for jt in fn.get("jump_tables") or []:
            out.update(int(t, 16) for t in jt["targets"])
    return out


def assert_linkable(doc: dict) -> list[str]:
    """Every way this work list could produce a link the S3 slice cannot use."""
    fmap = C.by_address()
    build_log = C.read_jsonl(C.BUILD / f"{doc['tag']}.jsonl")
    defined = {sym: rec["va"] for rec in build_log.values() if rec.get("compiled")
               for sym in rec.get("defines", [])}
    fails: list[str] = []
    seen: set[str] = set()

    # Coverage. Every arm the analyzer names must end up with a `sub_<arm>`
    # symbol somewhere, or an indirect jump to it is a function-table miss no
    # matter how thoroughly its code was lifted into somebody else's module.
    in_work = {int(row["arm"], 16) for row in doc["work"]}
    all_arms = every_arm_of_a_lifted_owner(doc["tag"])
    uncovered = sorted(a for a in all_arms
                       if a not in in_work and f"sub_{a:x}" not in defined)
    if uncovered:
        fails.append(
            f"{len(uncovered)} arms are in neither the work list nor the whole-image "
            f"build's defines, so nothing will define sub_<arm> for them and every "
            f"indirect jump to one is a table miss: "
            f"{[f'0x{a:08x}' for a in uncovered[:5]]}")
    doc["coverage"] = {
        "distinct_arms_of_lifted_owners": len(all_arms),
        "in_the_work_list": len(all_arms & in_work),
        "already_defined_by_the_whole_image_build":
            sum(1 for a in all_arms if f"sub_{a:x}" in defined),
        "covered_by_neither": len(uncovered),
        "first_uncovered": [f"0x{a:08x}" for a in uncovered[:20]],
    }

    for row in doc["work"]:
        a = int(row["arm"], 16)
        lo, hi = int(row["lo"], 16), int(row["hi"], 16)
        if not lo <= a < hi:
            fails.append(f"{row['arm']}: outside its own byte range "
                         f"{row['lo']}..{row['hi']} -- remill would decode nothing")
        sym = f"sub_{a:x}"
        if sym in defined:
            fails.append(f"{row['arm']}: {sym} is already defined by whole-image "
                         f"module {defined[sym]} -- duplicate symbol at link")
        if a in fmap:
            fails.append(f"{row['arm']}: is itself a function-map entry")
        if row["arm"] in seen:
            fails.append(f"{row['arm']}: appears twice in the work list")
        seen.add(row["arm"])
    return fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--no-dedupe", action="store_true",
                    help="keep arms the whole-image build already defines, so the "
                         "assertion below can be seen to fail")
    ap.add_argument("--drop-reached", action="store_true",
                    help="drop the arms remill's direct flow already decodes, which is "
                         "what slice 4 did. The coverage assertion then refuses, which "
                         "is how a check is shown to be able to fail.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    doc = build(args.tag, args.jobs, dedupe=not args.no_dedupe,
                drop_reached=args.drop_reached)
    fails = assert_linkable(doc)
    doc["assertion"] = {
        "checks": ["arm inside its own byte range",
                   "arm's symbol not already defined by the whole-image build",
                   "arm is not itself a function-map entry",
                   "no arm appears twice",
                   "every arm of a lifted owner is in the work list or already "
                   "defined by the whole-image build"],
        "failures": fails,
        "ok": not fails,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = ("" + (".nodedupe" if args.no_dedupe else "")
              + (".dropreached" if args.drop_reached else ""))
    out = args.out or (OUT / f"armlist.{args.tag}{suffix}.json")
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items()
                      if k not in ("work", "arms_reanchored_off_their_owner_range")},
                     indent=1))
    print(f"-> {out}")
    if fails:
        for f in fails:
            print(f"  FAIL {f}", file=sys.stderr)
        sys.exit(f"REFUSED: {len(fails)} arm traces would not link")


if __name__ == "__main__":
    main()
