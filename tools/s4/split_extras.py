#!/usr/bin/env python3
"""Re-lift the extras the function map cut short: their own code branches past their end.

Subtitle: for every lifted extra whose range ends at a function-map entry, walk
its reachable code; if a branch or fall-through reaches that entry, the map
split one function in two, and the extra is re-lifted over the whole function
with a generated reason.

    source tools/env.sh
    .venv/bin/python tools/s4/split_extras.py            # list what it would re-lift
    .venv/bin/python tools/s4/split_extras.py --relift   # and re-lift them

Why
---
`tools/s4/lift_extra.py` lifts an extra to the next map entry. For a CRI class
whose 63-entry vtable the .rdata sweep brought in (0x00896E18 onward), each
method is a guard, `cmp [ebp+14h],0; jne body`, then "Cue is not set" and
`ret 4`, and the map lists the body as a function of its own. So the extra ends
at the body, its own `jne` becomes a missing-block edge of kind jcc-taken, and
the edge policy refuses it. The Metal Harbor test-spawn run stopped on two of
these in its first rounds (0x008305CC and 0x0082F706, 2026-09-23). A fix by
hand is `lift_extra.py --relift --entry VA --end END`; the class is sixty-odd
methods, which is AGENTS.md rule 13's case for a tool.

The walk is recursive descent from the extra's entry, confined to where it is
looking: the same reachability tools/s4/mb_edges.py uses, so an extra is
extended only when a path from its own entry reaches the entry it stops at.
The extension runs to the end of that map function, and again if the
extended range reaches the next entry, up to MAX_EXTEND functions.
"""

from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402

from iced_x86 import Decoder, FlowControl  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
STATE = C.GEN / "s4" / "link" / "extra_functions.next+allarms.json"
MAX_EXTEND = 4
STOPS = (FlowControl.UNCONDITIONAL_BRANCH, FlowControl.RETURN, FlowControl.INDIRECT_BRANCH,
         FlowControl.INTERRUPT, FlowControl.EXCEPTION)


def reaches(image: bytes, va: int, limit: int, target: int) -> bool:
    """Whether recursive descent from `va`, inside [va, limit), reaches `target`
    the way only one function's own code does: by a conditional branch, or by
    falling into it from an instruction that is neither a call nor a jump.

    A `jmp` to `target` is a tail call and proves nothing -- this tool once
    merged 0x00791EA0 with FUN_00791FC0, a function with its own frame, on the
    strength of the one `jmp` that reaches it (the rule-5 review, 2026-09-23).
    Falling through from a `call` means the call does not return, which is the
    end of a function, not the middle of one."""
    seen, work = set(), [va]
    while work:
        ip = work.pop()
        if ip in seen or not (va <= ip < limit):
            continue
        seen.add(ip)
        off = ip - C.IMAGE_BASE
        ins = next(iter(Decoder(32, image[off:off + 16], ip=ip)))
        if ins.is_invalid:
            continue
        fc = ins.flow_control
        if fc in (FlowControl.UNCONDITIONAL_BRANCH, FlowControl.CONDITIONAL_BRANCH):
            t = ins.near_branch_target
            if t == target and fc == FlowControl.CONDITIONAL_BRANCH:
                return True
            if t != target:
                work.append(t)
        if fc not in STOPS:
            if ins.next_ip == target:
                if fc not in (FlowControl.CALL, FlowControl.INDIRECT_CALL):
                    return True
                continue
            work.append(ins.next_ip)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relift", action="store_true")
    args = ap.parse_args()
    image = C.memory_image()
    fmap = json.loads((C.GEN / "function-map.json").read_text())["functions"]
    ents = sorted((int(f["address"], 16) if isinstance(f["address"], str) else f["address"],
                   int(f.get("size") or 0)) for f in fmap)
    starts = [e[0] for e in ents]
    size_of = dict(ents)
    rows = [r for r in json.loads(STATE.read_text())["rows"]
            if r.get("compiled") and not r.get("rejected")]
    plan = []
    for r in rows:
        va, end = int(r["va"], 16), int(r["end"], 16)
        if end not in size_of or not reaches(image, va, end + 1, end):
            continue
        new_end, steps = end, 0
        while new_end in size_of and steps < MAX_EXTEND and \
                reaches(image, va, new_end + 1, new_end):
            nxt = new_end + max(size_of[new_end], 1)
            k = bisect.bisect_right(starts, new_end)
            new_end = starts[k] if k < len(starts) and starts[k] < nxt + 0x40 else nxt
            steps += 1
        plan.append({"va": r["va"], "was_end": r["end"], "new_end": f"{new_end:#010x}",
                     "split_at": f"{end:#010x}", "functions_joined": steps,
                     "why_before": r.get("why", "")[:160]})
    print(json.dumps({"extras": len(rows), "cut_short": len(plan), "plan": plan[:80]}, indent=1))
    if not args.relift:
        return 0
    for p in plan:
        why = (f"{p['why_before'].split(':')[0]}; re-lifted to {p['new_end']} by "
               f"tools/s4/split_extras.py because its own code reaches {p['split_at']}, the map "
               f"entry its first lift stopped at: the map split one function there, and the "
               f"jcc or fall-through into it was a refused missing-block edge")
        c = subprocess.run([PY, str(HERE / "lift_extra.py"), "--tag", "next+allarms",
                            "--lift-tag", "next", "--arms-tag", "next", "--seed-arms",
                            "--seed-continuations", "--entry", p["va"], "--end", p["new_end"],
                            "--relift", "--why", why],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
        print(f"SPLIT {'relifted' if c.returncode == 0 else 'REFUSED'} {p['va']} -> {p['new_end']}")
        if c.returncode:
            print(c.stderr[-800:])
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
