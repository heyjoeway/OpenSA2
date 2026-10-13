#!/usr/bin/env python3
"""Re-check every collected capture and rewrite its receipt's verdict.

Subtitle: the identity column is a property of the format, not of the run.

The oracle runner takes one --require-varying field for the whole run, and a
run produces several formats at once: the movement trace is keyed on `tick`
and the collision, candidate, contact and branch streams are keyed on
`sequence`. Requiring `tick` of all of them rejects four correct captures for
a reason that is about the command line.

This re-runs `capture_check.py` over every artifact of every capture with the
identity column that artifact's own header carries, and rewrites the
`receipt.json` verdict in place. It refuses to pass an artifact whose header
carries neither identity, rather than quietly checking nothing — that is the
failure this project keeps finding, and the point of naming the fallback here
instead of defaulting to the first column.

Usage: .venv/bin/python tools/oracle/revalidate_captures.py [--min-rows N]
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capture_check  # noqa: E402

CAPTURES = HERE.parent.parent / "private" / "generated" / "oracle" / "captures"
IDENTITIES = ("tick", "sequence")
SHORT_ROW_WIDTHS = [3]


def identity_of(path: Path) -> str | None:
    with path.open(newline="") as stream:
        header = next(csv.reader(stream), [])
    for candidate in IDENTITIES:
        if candidate in header:
            return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-rows", type=int, default=100)
    args = parser.parse_args()
    if not CAPTURES.is_dir():
        raise SystemExit(f"no captures directory: {CAPTURES}")
    total = accepted = rejected = 0
    for directory in sorted(p for p in CAPTURES.iterdir() if p.is_dir()):
        receipt_path = directory / "receipt.json"
        if not receipt_path.is_file():
            raise SystemExit(f"capture directory without a receipt: {directory}")
        receipt = json.loads(receipt_path.read_text())
        for artifact in receipt["artifacts"]:
            if not artifact.get("present"):
                continue
            path = directory / artifact["file"]
            total += 1
            identity = identity_of(path)
            if identity is None:
                artifact["check"] = {
                    "ok": False,
                    "problems": [
                        "header carries none of the known identity columns "
                        f"{IDENTITIES}; this artifact was not checked"
                    ],
                }
                rejected += 1
                continue
            checked = capture_check.check(path, [identity], args.min_rows,
                                          SHORT_ROW_WIDTHS)
            checked["identity"] = identity
            artifact["check"] = checked
            accepted += checked["ok"]
            rejected += not checked["ok"]
            state = "ok" if checked["ok"] else "REJECTED"
            print(f"  {state} {directory.name}/{artifact['file']} "
                  f"identity={identity} rows={checked['rows']} "
                  f"columns={checked['columns']}")
            for problem in checked["problems"]:
                print(f"      {problem}")
        receipt["revalidated"] = True
        receipt_path.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"SA2_ORACLE_REVALIDATED artifacts={total} accepted={accepted} "
          f"rejected={rejected}")
    if rejected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
