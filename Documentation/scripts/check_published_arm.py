#!/usr/bin/env python3
"""The published metrics and the receipts must come from the same arm.

S3 lifts the image twice: `map` uses the analyzer's recorded function sizes,
`next` uses `--to-next-entry`. Slice 2 adopted `next`. Slice 1 had already
published it *by accident*, while every prose record said `map`, and nothing
noticed -- `metrics.py` regenerates `METRICS.md` from the very log that names
the arm, so the two can never disagree with each other no matter which arm
wrote them. The gate that was missing is this one: a comparison between the
published log and an independent record of what was adopted.

The S3 interrogation then found that `s3_metrics_inputs.py` still defaulted
its `--tag` to the abandoned arm, so republishing exactly as the journals
documented it would have moved the Traps metric from 611 back to 1080 with
all gates green. The flag is required now; this checks the outcome rather
than the flag, because a required flag can still be given the wrong value.

It fails when:

  * the emit log and the build log name different arms -- they are written by
    one command and disagreeing means something rewrote one of them;
  * an S3 receipt states an `arm` that the published logs do not match;
  * the published arm is not the adopted one named in the ledger of adopted
    arms below, which is a constant here precisely so that changing the
    adopted arm is an edit somebody reviews.

Usage: .venv/bin/python Documentation/scripts/check_published_arm.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# The arm S3 adopted, and where the adoption is recorded. Changing this is a
# decision, so it is a line somebody has to edit and a reviewer can see.
ADOPTED = "next"
ADOPTED_WHERE = "Documentation/receipts/s3-whole-image-lift-v1.json (to_next_entry.decision)"

PUBLISHED_LOGS = ("private/generated/emit-log.json", "private/generated/build-log.json")
RECEIPTS = Path("Documentation") / "receipts"


def project_root(given: Path | None) -> Path:
    return given.resolve() if given else Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    root = project_root(args.root)

    failures: list[str] = []
    reports: list[str] = []

    arms: dict[str, str | None] = {}
    for name in PUBLISHED_LOGS:
        path = root / name
        if not path.is_file():
            reports.append(f"{name}: absent, nothing published yet")
            continue
        try:
            arms[name] = json.loads(path.read_text()).get("arm")
        except (OSError, ValueError) as problem:
            failures.append(f"{name} is unreadable: {problem}")

    if not arms:
        print("published arm OK: nothing is published yet, so nothing can disagree")
        return 0

    named = {name: arm for name, arm in arms.items() if arm is not None}
    for name, arm in arms.items():
        if arm is None:
            failures.append(f"{name} carries no `arm` field, so what it publishes cannot "
                            f"be checked; s3_metrics_inputs.py writes one")
    distinct = set(named.values())
    if len(distinct) > 1:
        failures.append("the published logs disagree about the arm: "
                        + ", ".join(f"{n} says {a!r}" for n, a in sorted(named.items()))
                        + "; one command writes both, so something rewrote one of them")
    for name, arm in named.items():
        if arm != ADOPTED:
            failures.append(f"{name} publishes arm {arm!r}, but S3 adopted {ADOPTED!r} "
                            f"({ADOPTED_WHERE}). Republish with "
                            f"`s3_metrics_inputs.py --tag {ADOPTED}`, or, if the adopted "
                            f"arm really changed, change ADOPTED here in the same commit "
                            f"as the decision that changed it")
        else:
            reports.append(f"{name}: arm {arm!r}, the adopted one")

    for receipt in sorted((root / RECEIPTS).glob("*.json")):
        try:
            document = json.loads(receipt.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict) or document.get("stage") != "S3":
            continue
        arm = document.get("arm")
        if arm is None:
            continue
        rel = receipt.relative_to(root)
        if arm != ADOPTED:
            failures.append(f"{rel} states arm {arm!r}; the published metrics are "
                            f"{ADOPTED!r}, so the metric cites a receipt measured on a "
                            f"different arm")
        else:
            reports.append(f"{rel}: arm {arm!r}")

    for line in reports:
        print(f"  {line}", file=sys.stderr if failures else sys.stdout)
    if failures:
        raise SystemExit("published arm mismatch:\n  " + "\n  ".join(failures))
    digest = hashlib.sha256(
        json.dumps({"adopted": ADOPTED, "arms": arms}, sort_keys=True).encode()).hexdigest()
    print(f"published arm OK: {len(named)} published log(s) and every S3 receipt that "
          f"names an arm agree on {ADOPTED!r}; marker_sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
