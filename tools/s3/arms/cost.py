#!/usr/bin/env python3
"""What seeding the arms costs and what it recovers, counted per instruction.

Subtitle: remill's recursive descent, reproduced and then checked against
remill's own per-instruction markers in the IR it emitted.

`remill-lift-14` decodes a trace by recursive descent from one entry: it
follows direct jumps and both edges of a conditional branch, resumes after a
call at the return address, and stops at a `ret`, at an indirect branch and at
any target outside the byte range it was handed. This tool performs that walk
so the decoded *address set* is available -- something the IR does not name --
and validates itself by comparing the size of each walk with the number of
`store i32 %k, ptr %PC` markers in the module remill actually produced. If the
two disagree the walk is wrong and the tool says so instead of publishing.

With the sets in hand the two numbers the decision turns on are subtraction:

  recovered  instructions in some arm's walk and in no unseeded walk -- what
             the seeding buys;
  lifted     instructions in the arm walks at all -- what it costs, because
             every arm re-lifts the tail the arms share.

`lifted / recovered` is the duplication factor the G3 gate report estimates at
3.49x image-wide.

Usage:
  .venv/bin/python tools/s3/arms/cost.py --owner 0x004015f0 --tag fun4015f0
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "arms"
PC_STORE = re.compile(r"^  store i32 %\d+, ptr %PC, align 4$", re.M)
DEF_SPLIT = re.compile(r"^define [^\n]*@([\w.$]+)\(", re.M)


def pc_stores(path: Path, symbol: str) -> int | None:
    if not path.is_file():
        return None
    parts = DEF_SPLIT.split(path.read_text(errors="replace"))
    for i in range(1, len(parts), 2):
        if parts[i] == symbol:
            return len(PC_STORE.findall(parts[i + 1]))
    return None


def walk(img: bytes, entry: int, lo: int, hi: int) -> tuple[set[int], int]:
    """remill's recursive descent over [lo, hi) from `entry`.

    Returns the decoded address set and the number of *exit blocks* remill
    emits for it. The second number matters only for the self-check: remill
    publishes PC once per decoded instruction and once more per exit -- a
    `ret`, an indirect branch, or an edge that leaves the byte range -- which a
    three-byte calibration (`90 c3` gives 3 markers for 2 instructions,
    `74 01 c3 ff e0` gives 5 for 3 instructions and two exits) pins down.
    """
    seen, exits = walk_detail(img, entry, lo, hi)
    return seen, len(exits)


def walk_detail(img: bytes, entry: int, lo: int, hi: int) -> tuple[set[int], set[int]]:
    """`walk`, with the exit *addresses* instead of their count.

    Added for `tools/s3/arms/second_order.py`, which has to ask which exits are
    indirect branches -- the sites that stop a trace and would need a further
    seeding round. `walk` is this function's caller so the descent the six-arm
    slice validated against remill's own markers stays the only copy.
    """
    from iced_x86 import Decoder, FlowControl
    seen: set[int] = set()
    exits: set[int] = set()
    todo = [entry]
    while todo:
        pc = todo.pop()
        while True:
            if not (lo <= pc < hi):
                exits.add(pc)                 # leaves the range: a missing block
                break
            if pc in seen:
                break
            seen.add(pc)
            ins = Decoder(32, img[pc - C.IMAGE_BASE:pc - C.IMAGE_BASE + 16], ip=pc).decode()
            fc = ins.flow_control
            if fc in (FlowControl.NEXT, FlowControl.CALL, FlowControl.INDIRECT_CALL):
                pc = ins.next_ip              # a callee is another trace
            elif fc == FlowControl.UNCONDITIONAL_BRANCH:
                pc = ins.near_branch32
            elif fc == FlowControl.CONDITIONAL_BRANCH:
                todo.append(ins.near_branch32)
                pc = ins.next_ip
            else:                             # RETURN, INDIRECT_BRANCH, INTERRUPT
                exits.add(pc)
                break
    return seen, exits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--whole-tag", default="next")
    args = ap.parse_args()
    owner = int(args.owner, 16)
    img = C.memory_image()
    fmap = C.by_address()
    starts = sorted(fmap)
    i = bisect.bisect_right(starts, owner)
    hi = starts[i] if i < len(starts) else owner + fmap[owner]["size"]
    fn = fmap[owner]
    arms = [int(t, 16) for jt in (fn.get("jump_tables") or []) for t in jt["targets"]]
    arms = sorted(set(arms))

    owner_set, owner_exits = walk(img, owner, owner, hi)
    walked = {a: walk(img, a, owner, hi) for a in arms}
    arm_sets = {a: s for a, (s, _e) in walked.items()}
    arm_exits = {a: e for a, (_s, e) in walked.items()}

    # self-check against remill's own markers
    checks = {
        f"sub_{owner:x}": (len(owner_set) + owner_exits,
                           pc_stores(C.LIFT / args.whole_tag / f"sub_{owner:08X}.ll",
                                     f"sub_{owner:x}")),
    }
    for a in arms:
        checks[f"sub_{a:x}"] = (len(arm_sets[a]) + arm_exits[a],
                                pc_stores(OUT / "lift" / args.tag / f"sub_{a:08X}.ll",
                                          f"sub_{a:x}"))
    disagree = {k: v for k, v in checks.items() if v[1] is not None and v[0] != v[1]}

    union_arms: set[int] = set().union(*arm_sets.values()) if arm_sets else set()
    recovered = union_arms - owner_set
    lifted = sum(len(s) for s in arm_sets.values())
    # every instruction the map says the function has, decoded linearly
    from iced_x86 import Decoder
    guest, pc = 0, owner
    end_map = owner + fn["size"]
    while pc < end_map:
        ins = Decoder(32, img[pc - C.IMAGE_BASE:pc - C.IMAGE_BASE + 16], ip=pc).decode()
        guest += 1
        pc = ins.next_ip

    doc = {
        "owner": f"0x{owner:08x}", "lift_range": [f"0x{owner:08x}", f"0x{hi:08x}"],
        "map_instructions": fn["instructions"], "instructions_decoded_linearly": guest,
        "walk_validated_against_remill_ir": not disagree,
        "walk_vs_ir_pc_markers": {k: {"walk_plus_exits": v[0], "ir_pc_markers": v[1]}
                                  for k, v in checks.items()},
        "disagreements": disagree,
        "owner_unseeded_instructions": len(owner_set),
        "per_arm_instructions": {f"0x{a:08x}": len(s) for a, s in arm_sets.items()},
        "instructions_lifted_by_the_arm_traces": lifted,
        "instructions_recovered": len(recovered),
        "recovered_addresses": [f"0x{a:08x}" for a in sorted(recovered)],
        "duplication_factor_lifted_over_recovered":
            round(lifted / len(recovered), 3) if recovered else None,
        "image_wide_estimate_being_tested": 3.49,
        "instructions_in_a_module_before": len(owner_set),
        "instructions_in_a_module_after": len(owner_set) + lifted,
        "guest_instructions_covered_after": len(owner_set | union_arms),
        "copies_per_covered_instruction":
            round((len(owner_set) + lifted) / len(owner_set | union_arms), 3),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"cost.{args.tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "recovered_addresses"}, indent=1))
    if disagree:
        sys.exit("REFUSED: the walk does not reproduce remill's own instruction markers")
    print(f"-> {OUT / f'cost.{args.tag}.json'}")


if __name__ == "__main__":
    main()
