#!/usr/bin/env python3
"""Demonstrate the map-entry-arm link failure, so the guard against it is not
a guard against something nobody has seen.

Subtitle: seed one of the four arms that are also map entries, compile it, and
put its object in front of the linker beside the whole-image object for the
same address.

`agents-journals/s3/2026-09-20-s3-arm-seeding-experiment.md` finding 3 says
these four are a hard duplicate-symbol failure rather than a table-row
overwrite. That was read off the IR. This runs it: a relocatable link
(`zig cc -r`, which resolves symbols without needing a runtime, an entry point
or any of the files another agent is concurrently editing) of exactly two
objects. `tools/s3/arms/armlist.py` refuses to put these arms in the work list;
this is the failure that refusal prevents.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/dup_probe.py [--arm 0x006d09b0] [--tag next]
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
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import lift_arms as L  # noqa: E402
import spikelib as S  # noqa: E402

OUT = C.GEN / "s3" / "arms"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="0x006d09b0")
    ap.add_argument("--tag", default="next")
    args = ap.parse_args()

    arm = int(args.arm, 16)
    fmap = C.by_address()
    if arm not in fmap:
        sys.exit(f"{args.arm} is not a map entry; this probe is about the arms that are")
    starts = sorted(fmap)
    hi = starts[starts.index(arm) + 1]

    scratch = OUT / "dup_probe"
    lldir, folddir, objdir = scratch / "lift", scratch / "fold", scratch / "build"
    for d in (lldir, folddir, objdir):
        d.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    rec = L.lift_one(arm, hi, arm, lldir, C.memory_image())
    if not rec["lifted"]:
        sys.exit(f"the arm trace did not lift: {rec['error_head']}")
    rec.update(L.fold_and_compile(arm, lldir, folddir, objdir))
    if not rec["compiled"]:
        sys.exit(f"the arm trace did not compile: {rec['compile_error_head']}")

    seeded = objdir / f"ir_{arm:08X}.o"
    whole = C.BUILD / args.tag / f"ir_{arm:08X}.o"
    if not whole.is_file():
        sys.exit(f"no whole-image object at {whole}")
    cmd = [str(S.ZIG), "cc", "-target", "x86_64-linux-musl", "-r",
           str(whole), str(seeded), "-o", str(scratch / "rel.o")]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    err = (p.stderr + p.stdout).strip()
    duplicate = "duplicate symbol" in err

    doc = {
        "arm": args.arm,
        "question": "does seeding an arm that is also a map entry break the link?",
        "seeded_object": str(seeded), "whole_image_object": str(whole),
        "seeded_object_defines": rec["defines_after_fold"],
        "link_command": " ".join(cmd),
        "link_returncode": p.returncode,
        "duplicate_symbol_reported": duplicate,
        "linker_output_head": "\n".join(err.splitlines()[:8]),
        "answer": ("yes: a duplicate symbol, which is why armlist.py refuses these arms"
                   if duplicate else
                   "NO -- the hazard did not reproduce; armlist.py's guard needs rethinking"),
        "seconds": round(time.time() - t0, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"dup_probe.{arm:08x}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    if not duplicate:
        sys.exit("the probe did not reproduce the failure it exists to demonstrate")


if __name__ == "__main__":
    main()
