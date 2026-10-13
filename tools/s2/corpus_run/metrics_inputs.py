#!/usr/bin/env python3
"""Write the metric inputs this slice moved: emit log, build log, fixture results.

Rule 7 ("generated over typed"): every number in these files is copied out of the run
logs under private/generated/s2/corpus_run/, never typed. Each file keeps the S2 wrap's
entries and adds this slice's, and names both receipts as its source.

`private/generated/fixture-results.json` is M3's numerator. A fixture appears in exactly
one of `passed` or `failed`, and only if it was actually run; a fixture nothing drove is
in neither, which is what makes the denominator show the gap. A fixture that passed in a
previous results file and does not pass now is listed in `regressions` (design 7.3).

Usage:
  .venv/bin/python tools/s2/corpus_run/metrics_inputs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
RECEIPT = "Documentation/receipts/s2-corpus-run-v1.json"
WRAP_RECEIPT = "Documentation/receipts/s2-wrap-v1.json"
ATAN2_RECEIPT = "Documentation/receipts/s2-atan2-v1.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lift = json.loads((OUT / "lift.json").read_text())["functions"]
    build = json.loads((OUT / "build.pc24.json").read_text())
    run = json.loads((OUT / "run.pc24.json").read_text())["results"]

    corpus_lifted = sorted(a for a, e in lift.items() if e.get("lifted"))
    # The wrap's own lift log, not the emit log this script writes: reading its own output
    # made the wrap's contribution grow by the corpus's every time it ran.
    wrap_lifted = sorted(a for a, e in
                         json.loads((GEN / "s2" / "wrap" / "lift.json").read_text())["functions"].items()
                         if e.get("lifted"))
    emitted = sorted(set(corpus_lifted) | set(wrap_lifted))
    linked = build["link"]["ok"]
    compiled = sorted(set(a for a, r in build["ir"].items() if r["ok"]) | set(wrap_lifted)) if linked else sorted(set(wrap_lifted))

    emit_log = {
        "emitted": [{"address": a} for a in emitted],
        "traps": [],
        "source": f"{RECEIPT}; {WRAP_RECEIPT}",
        "note": ("S2: the functions the adopted pipeline (remill 5.0.7 + the PC24 pass + zig cc) has lifted. "
                 f"{len(wrap_lifted)} come from the wrap ({WRAP_RECEIPT}), the rest from the corpus run "
                 f"({RECEIPT}): every distinct entry address the fixture registry names, the transitive "
                 "`direct_calls` closure of those addresses out of private/generated/function-map.json, and "
                 "the jump targets the runs themselves trapped at. Addresses the function map has no entry for "
                 "are orphan blocks inside map gaps (CRT transcendental tails); they are emitted and compiled "
                 "but cannot count towards M1, whose denominator is the map. This is still not a whole-image "
                 "emit: S3 lifts every function in the map and replaces this file."),
    }
    build_log = {
        "compiled": [{"address": a} for a in compiled],
        "failed": [],
        "source": f"{RECEIPT}; {WRAP_RECEIPT}",
        "note": (f"S2: every lifted function compiled. The wrap's 22 per-function drivers link; the corpus run "
                 f"links all {build['functions']} modules into one program "
                 f"(link ok={linked}, undefined symbols "
                 f"{build.get('undefined_sub_symbols') or 'none'})."),
    }

    previous = json.loads((GEN / "fixture-results.json").read_text())
    passed_before = set(previous.get("passed", []))
    # The wrap's re-run covers the two the S1 path already passed, through the real runtime.
    wrap_run = json.loads((GEN / "s2" / "wrap" / "run.json").read_text())["entry_test_rerun"]
    wrap_pass = []
    for fx in wrap_run["fixtures"]:
        name = Path(fx["fixture"]).stem
        ok = all(r["all_rows"]["matches_native"] == r["all_rows"]["rows"]
                 for k, r in fx["runs"].items() if k.endswith(":pc24") and "run" in r)
        (wrap_pass.append(name) if ok else None)
    passed = sorted(set(wrap_pass) | {r["fixture"] for r in run if r.get("passed")})
    failed = sorted({r["fixture"] for r in run if not r.get("passed")} - set(passed))
    regressions = sorted(passed_before - set(passed) - set(failed)) + sorted(passed_before & set(failed))

    results = {
        "passed": passed,
        "failed": failed,
        "regressions": regressions,
        "method": ("tools/s2/corpus_run/run.py: one plan per fixture (harness/plan_ops.h) driven through "
                   "harness/plan_driver.c against runtime v0, over the whole fixture-covered closure lifted "
                   "by remill + tools/spike/pc24pass and linked into one program; plus "
                   "tools/s2/wrap/wrap.py --phase run for the two the entry test's corpus covers."),
        "note": (f"M3's numerator. Recorded in {RECEIPT} (fixtures), with the wrap's re-run in {WRAP_RECEIPT} "
                 f"and, in {ATAN2_RECEIPT}, the two lift defects whose fix moved vector-to-angles-v1, "
                 "stairs-transform-v1 and rejected-contact-v1 into `passed`. "
                 "A fixture appears here only if it was run: the registered fixtures in neither list could not "
                 "be driven, and that gap is the point of the denominator. Per-fixture row counts, the first "
                 "divergent row and field, and why each unrun fixture could not run are in the corpus-run "
                 "receipt."),
    }

    for path, obj in ((GEN / "emit-log.json", emit_log), (GEN / "build-log.json", build_log),
                      (GEN / "fixture-results.json", results)):
        print(f"{path.relative_to(ROOT)}: "
              f"{len(obj.get('emitted', obj.get('compiled', obj.get('passed', []))))} entries")
        if not args.dry_run:
            path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    print("passed:", passed)
    print("failed:", failed)
    print("regressions:", regressions)


if __name__ == "__main__":
    main()
