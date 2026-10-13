#!/usr/bin/env python3
"""Report the first tick and field at which two captures disagree.

Subtitle: first-divergence reporting for the live oracle's own output.

`Documentation/predecessor/OMOCHAO_SPLIT.md` describes the predecessor's
record-stream comparer, which compares normalised uint32 words. The movement
traces the oracle produces are decimal float text with a mix of row widths,
so they are not valid input to it. This is the same idea for that format:
align on an identity column, compare field by field in header order, stop at
the first difference and print both values with their source lines.

It refuses the inputs that would let a comparison pass for the wrong reason,
which is the failure mode this project keeps finding:

  * either stream empty, or header-only;
  * different headers, or a different column order;
  * an identity value present in one stream and not the other;
  * the shared identities appearing in a different order;
  * and, with --require-overlap N, fewer than N identities in common, so
    "the first 3 ticks agree" cannot be reported as agreement.

Rows narrower than the header (the trace format's `tick,player,entity` rows,
written when the entity pointer is null) are compared on the fields they
have, and a width difference at the same identity is itself a divergence.

Exit codes: 0 identical over the compared identities, 1 different, 2 invalid.

Usage:
  compare_captures.py --identity tick A.csv B.csv [--require-overlap N]
                      [--limit-identities N] [--json]
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


def invalid(message: str) -> None:
    """Exit 2, the documented code for a schema or identity problem."""
    print(message, file=sys.stderr)
    raise SystemExit(2)


def load(path: Path, identity: str) -> tuple[list[str], dict[str, tuple[int, list[str]]], list[str]]:
    if not path.is_file():
        invalid(f"capture does not exist: {path}")
    with path.open(newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            invalid(f"empty stream: {path}")
        rows = [(number, row) for number, row in enumerate(reader, start=2)]
    if not rows:
        invalid(f"header-only stream, no records: {path}")
    if identity not in header:
        invalid(f"identity field {identity!r} not in header of {path}")
    index = header.index(identity)
    records: dict[str, tuple[int, list[str]]] = {}
    order: list[str] = []
    for number, row in rows:
        if index >= len(row):
            continue                      # a row too short to carry an identity
        key = row[index]
        if key in records:
            continue                      # first occurrence of a tick wins
        records[key] = (number, row)
        order.append(key)
    if not records:
        invalid(f"no row carries the identity field in {path}")
    return header, records, order


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--identity", default="tick")
    parser.add_argument("--require-overlap", type=int, default=1)
    parser.add_argument("--limit-identities", type=int, default=0,
                        help="compare only the first N shared identities (0 = all)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()

    left_header, left, left_order = load(args.left, args.identity)
    right_header, right, right_order = load(args.right, args.identity)
    if left_header != right_header:
        invalid(
            f"headers differ ({len(left_header)} vs {len(right_header)} columns)"
        )

    shared = [key for key in left_order if key in right]
    if args.limit_identities:
        shared = shared[: args.limit_identities]
    if len(shared) < args.require_overlap:
        invalid(
            f"only {len(shared)} shared {args.identity} values, "
            f"--require-overlap is {args.require_overlap}; a comparison over "
            "this little is not a comparison"
        )
    right_shared = [key for key in right_order if key in left]
    if args.limit_identities:
        right_shared = right_shared[: args.limit_identities]
    if shared != right_shared:
        invalid(
            f"the shared {args.identity} values appear in a different order"
        )

    result = {
        "schema": "sa2-oracle-compare/v1",
        "identity": args.identity,
        "left": {"path": str(args.left), "records": len(left)},
        "right": {"path": str(args.right), "records": len(right)},
        "compared_identities": len(shared),
        "compared_fields": len(left_header) - 1,
        "left_only": len(left) - len(shared),
        "right_only": len(right) - len(shared),
    }
    for key in shared:
        left_line, left_row = left[key]
        right_line, right_row = right[key]
        if len(left_row) != len(right_row):
            result["status"] = "different"
            result["difference"] = {
                "identity": key, "field": "<row width>",
                "left": {"line": left_line, "value": len(left_row)},
                "right": {"line": right_line, "value": len(right_row)},
            }
            break
        for position, field in enumerate(left_header):
            if position >= len(left_row):
                break
            if left_row[position] != right_row[position]:
                result["status"] = "different"
                result["difference"] = {
                    "identity": key, "field": field, "column": position,
                    "left": {"line": left_line, "value": left_row[position]},
                    "right": {"line": right_line, "value": right_row[position]},
                }
                break
        if "difference" in result:
            break
    else:
        result["status"] = "identical"

    if args.json:
        json.dump(result, sys.stdout, indent=1, sort_keys=True)
        print()
    if result["status"] == "identical":
        print(
            f"SA2_ORACLE_IDENTICAL identities={result['compared_identities']} "
            f"fields={result['compared_fields']} identity={args.identity} "
            f"left_only={result['left_only']} right_only={result['right_only']}"
        )
        return
    difference = result["difference"]
    print(
        f"SA2_ORACLE_DIVERGENT first {args.identity}={difference['identity']} "
        f"field={difference['field']} "
        f"left={difference['left']['value']!r} (line {difference['left']['line']}) "
        f"right={difference['right']['value']!r} (line {difference['right']['line']}) "
        f"after {result['compared_identities']} compared identities"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
