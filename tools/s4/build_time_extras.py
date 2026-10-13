#!/usr/bin/env python3
"""The build-time kill criterion, measured over the build that ships: the extras too.

Subtitle: every lifted extra lifted, passed, folded and compiled again at one
job into a scratch directory, timed, checked byte for byte against the object
the build links, and added to S3's directly measured whole-image figure.

    .venv/bin/python tools/s4/build_time_extras.py            # measure and print
    .venv/bin/python tools/s4/build_time_extras.py --write    # and write the receipt

Why
---
G1 kill criterion 1 fires a pivot to the custom emitter when the full-image
lift+compile passes 60 wall minutes on this machine.
`Documentation/receipts/s3-build-time-v2.json` measured it directly and
serially over the map's modules and S3's jump-table arms: the shipping image as
it stood at G3. S4 has since added `tools/s4/lift_extra.py`'s extras to the
image that links and ships: functions a run reached, code pointers from
`.rdata`, the switch arms the analyzer never recorded, and the continuations
after the instructions remill cannot lift. The G4 gate journal carried making
the criterion count them as a strengthening owed before G4 closes. Until now
their cost was read from the per-module seconds a parallel run recorded, which
is not a serial measurement.

This measures them the way S3 measured the rest: one job, the whole set, the
wall clock around it. It proves it measured the build that ships by requiring
every rebuilt object to be byte-identical to the one the link uses. It writes
`Documentation/receipts/s4-build-time-v1.json`, whose
`kill_criterion_1.minutes_total` is S3's measured total plus this, and which
`Documentation/kill-criteria.json` evaluates. The threshold is untouched; what
moved is which build the criterion is measured against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402
import lift_extra as LE  # noqa: E402

import contextlib  # noqa: E402

# zig caches every `zig cc -c` by its inputs, so a build re-measured over
# sources it has compiled before is timed against the cache: a re-measurement
# on 2026-09-23 compiled the 18 394 map functions in 42 s at --jobs 4, where the
# measurement before it took 239 s, and the total fell while thousands of
# extras were added. A cold compile of one large function took 3.4-4.7 s and a
# repeat against the same cache about none. The timed work therefore runs
# against a cache made empty for it.
SCRATCH = C.GEN / "s4" / "build-time-scratch"


def unwind_on_sigterm() -> None:
    """`timeout` sends SIGTERM, which skips `finally`; raising SystemExit from
    it runs the cleanup, so an interrupted measurement leaves no cold cache or
    scratch build behind (the review found 0.9 GB left on /tmp)."""
    import signal

    def stop(_sig, _frame):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, stop)


COLD_CACHE = ("every timed phase ran with ZIG_GLOBAL_CACHE_DIR and ZIG_LOCAL_CACHE_DIR set "
              "to a new, empty directory, removed afterwards, so no compile was answered by "
              "zig's cache")


@contextlib.contextmanager
def cold_compiler_cache():
    """zig's global and local caches pointed at a new, empty directory for the
    duration, inherited by every child process and fork, then restored."""
    # On disk beside the build, as zig's own cache is, not on /tmp's tmpfs,
    # where cache writes would land in memory and read a little fast.
    SCRATCH.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="sa2-zig-cold-", dir=SCRATCH))
    keys = ("ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["ZIG_GLOBAL_CACHE_DIR"] = str(d / "global")
    os.environ["ZIG_LOCAL_CACHE_DIR"] = str(d / "local")
    try:
        yield d
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)

STATE = C.GEN / "s4" / "link" / "extra_functions.next+allarms.json"
BASE = ROOT / "Documentation" / "receipts" / "s3-build-time-v2.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-build-time-v1.json"
PIPELINE = (
    "tools/s4/build_time_extras.py",
    "tools/s4/lift_extra.py",
    "tools/spike/pc24pass/pc24pass.py",
    "tools/s2/corpus_run/internalize.py",
    "tools/s2/corpus_run/speed_rewrite.py",
    "tools/s2/corpus_run/state_local.py",
    "Documentation/lifted-extras.json",
)
THRESHOLD = 60.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def origin(why: str) -> str:
    """How an extra came in, from its reason's generated opening."""
    if why.startswith("the continuation after"):
        return "continuation"
    if why.startswith("an arm of the"):
        return "switch arm"
    if why.startswith("an aligned dword in"):
        return ".rdata sweep"
    if why.startswith("function-table miss at"):
        return "reach_extend"
    if " in dispatch_surface" in why or "dispatch target the whole-image lift" in why:
        return "dispatch surface"
    if why.startswith("reloc_address_taken in "):
        return "address taken"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="measure only the first N (a trial)")
    args = ap.parse_args()
    state = json.loads(STATE.read_text())
    rows = [r for r in state["rows"] if r.get("compiled") and not r.get("rejected")]
    if args.limit:
        rows = rows[:args.limit]
    image = C.memory_image()
    unwind_on_sigterm()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sa2-extras-build-", dir=SCRATCH))
    dirs = [work / "ll", work / "fold", work / "obj"]
    for d in dirs:
        d.mkdir()
    mismatched, failed, by = [], [], {}
    cold = contextlib.ExitStack()
    cold.enter_context(cold_compiler_cache())
    t0 = time.monotonic()
    try:
        for r in rows:
            va, end = int(r["va"], 16), int(r["end"], 16)
            anchor = int(r["anchor"], 16) if r.get("anchor") else None
            rec = LE.lift_one(va, end, image, dirs[0], anchor)
            if rec.get("lifted"):
                rec.update(LE.fold_and_compile(va, dirs[0], dirs[1], dirs[2]))
            if not rec.get("compiled"):
                failed.append(r["va"])
                continue
            mine, theirs = Path(rec["object"]), Path(r["object"])
            if not theirs.is_file() or sha256(mine) != sha256(theirs):
                mismatched.append(r["va"])
            k = origin(r.get("why", ""))
            by[k] = by.get(k, 0) + 1
        wall = time.monotonic() - t0
    finally:
        cold.close()
        shutil.rmtree(work, ignore_errors=True)
    base = json.loads(BASE.read_text())["kill_criterion_1"]
    extras_min = round(wall / 60.0, 3)
    total = round(base["minutes_total"] + extras_min, 3)
    print(f"{len(rows)} extras rebuilt serially in {extras_min} min ({by}); "
          f"{len(mismatched)} object(s) differ from the build's, {len(failed)} failed; "
          f"S3's measured {base['minutes_total']} + {extras_min} = {total} of {THRESHOLD}")
    if mismatched or failed:
        print("  differing:", mismatched[:10], " failed:", failed[:10])
        return 1
    if not args.write or args.limit:
        return 0
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-build-time-v1",
        "question": "Does the full-image lift+compile, counting the extras S4 added to the "
                    "image that ships, exceed G1 kill criterion 1's 60 wall minutes?",
        "generated_by": "tools/s4/build_time_extras.py",
        "commit": head,
        "command": ".venv/bin/python tools/s4/build_time_extras.py --write",
        "inputs": {
            "pipeline": {p: sha256(ROOT / p) for p in PIPELINE},
            "base_receipt": {"path": str(BASE.relative_to(ROOT)), "sha256": sha256(BASE),
                             "minutes_total": base["minutes_total"],
                             "covers": base.get("covers")},
            "extras_state": str(STATE.relative_to(ROOT)),
        },
        "method": "every compiled, unrejected extra lifted (remill-lift-14 under unshare), passed "
                  "(pc24pass), folded and compiled (zig cc -O2) again, one at a time, into a "
                  "scratch directory; the wall clock around the whole loop; each object required "
                  "to be byte-identical to the object the build links. The same phases S3's serial "
                  "measurement timed, and no link, as there. " + COLD_CACHE + ".",
        "extras": {"count": len(rows), "by_origin": by, "serial_minutes": extras_min,
                   "objects_identical_to_the_build": len(rows) - len(mismatched)},
        "kill_criterion_1": {
            "basis": "S3's direct whole-image serial run plus the extras' direct serial run",
            "covers": "the shipping build: map-function modules, S3's jump-table arm traces, and "
                      "S4's lifted extras",
            "minutes_total": total,
            "percent_of_60_minute_threshold": round(100.0 * total / THRESHOLD, 2),
            "threshold_minutes": THRESHOLD,
            # Over the threshold the criterion used to be read against. The user
            # redefined the criterion at --jobs 4, measured by build_time_jobs.py;
            # this receipt is the tracked Build time serial row, not the trigger,
            # so it says `over_threshold` and not `fires`.
            "over_threshold": total > THRESHOLD,
            "jobs": 1,
            "is_the_trigger": False,
            "trigger_decision": "Documentation/decisions/2026-09-23-G1-1-build-time-jobs4.md",
        },
        "limits": [
            "The two halves were measured on different days on the same machine; S3's figure is "
            "not re-measured here, and its limits (warm page cache, no link) carry over.",
            "Only this half ran against a cold compiler cache made for it. S3's half ran on "
            "zig's default cache and was cold because its build tag's paths were new, which "
            "zig keys its cache on; its map compile wall fits a cold one. Neither half is "
            "known to have been answered from the cache.",
            "Each extra is re-lifted from its recorded range and anchor; the extras' own "
            "discovery (a driven run, the census tools) is not build time and is not timed, as "
            "S3 did not time the analyzer.",
        ],
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"wrote {RECEIPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
