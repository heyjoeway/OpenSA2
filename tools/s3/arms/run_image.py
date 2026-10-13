#!/usr/bin/env python3
"""The whole arm-seeding slice, in order, in one command.

Subtitle: work list, lift, recovery, second-order check, table input, symbol
check -- each with its wall time, each refusing on its own terms.

AGENTS.md rule 13: a repeated manual step becomes a tool before it is repeated
a third time. These six steps have an order (the lift needs the work list, the
measurements need the lift, the table input needs the objects) and every one of
them can refuse, so running them by hand is a chance to run them out of order
or to miss a refusal in a scrollback.

The link is not among them. The runtime is being changed concurrently; the two
commands that would link this are printed by `table_input.py` into
`table_input.<tag>+arms.json`.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/run_image.py --tag next --jobs 16
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "arms"
PY = str(C.ROOT / ".venv" / "bin" / "python")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()
    j, t = str(args.jobs), args.tag

    steps = [
        ("armlist", [PY, "tools/s3/arms/armlist.py", "--tag", t, "--jobs", j]),
        ("lift+compile", [PY, "tools/s3/arms/lift_arms_image.py", "--tag", t,
                          "--jobs", j]),
        ("recovery", [PY, "tools/s3/arms/recovery.py", "--tag", t, "--jobs", j]),
        ("second_order", [PY, "tools/s3/arms/second_order.py", "--tag", t, "--jobs", j]),
        ("table_input", [PY, "tools/s3/arms/table_input.py", "--tag", t]),
        ("symbol_check", [PY, "tools/s3/arms/symbol_check.py", "--tag", t]),
    ]
    results = []
    t0 = time.time()
    for name, cmd in steps:
        s = time.time()
        p = subprocess.run(cmd, cwd=str(C.ROOT))
        results.append({"step": name, "command": " ".join(cmd),
                        "returncode": p.returncode,
                        "wall_seconds": round(time.time() - s, 2)})
        print(f"== {name}: rc={p.returncode} {results[-1]['wall_seconds']}s", flush=True)
        if p.returncode != 0:
            break
    doc = {"tag": t, "jobs": args.jobs, "steps": results,
           "wall_seconds": round(time.time() - t0, 2),
           "ok": all(r["returncode"] == 0 for r in results) and len(results) == len(steps)}
    (OUT / f"slice.{t}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    if not doc["ok"]:
        sys.exit("a step refused; nothing downstream of it ran")


if __name__ == "__main__":
    main()
