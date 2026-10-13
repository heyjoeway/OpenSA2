#!/usr/bin/env python3
"""Say whether a capture file is a capture, before anybody compares it.

Subtitle: an empty file and a file the hook never wrote look identical.

The project's own rule is that a comparison whose operands can both be empty
is not a comparison. A record-stream comparer handed two header-only CSVs
reports that they are identical. This refuses the inputs that would make a
later comparison pass for the wrong reason:

  * a file that does not exist, or is zero bytes;
  * a header-only stream with no data rows;
  * a ragged stream whose rows do not all have the header's width;
  * a stream whose LAST row is narrower than every other row, which is what a
    capture torn by the timeout kill looks like;
  * with --require-varying FIELD, a stream in which that field holds one
    value in every row, which is what a hook that fired once and a hook that
    fired never both produce.

The movement-trace format legitimately writes a short row when the player or
entity pointer is null, so --short-row-width takes the widths that are not
raggedness. The predecessor's `sa2_native_trace.c` writes `tick,player,entity`
in that case, so --short-row-width 3 is the usual value. A torn trailing row
is reported separately and is never excused by --short-row-width, because it
is the failure this check exists to find: four of the predecessor's 395
surviving captures end in a partial row written when `timeout` killed the
game, and nothing in its toolchain ever looked.

Usage:
  capture_check.py <capture.csv> [--require-varying FIELD ...] [--min-rows N]
                   [--short-row-width N ...] [--json]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys


def check(
    path: Path,
    require_varying: list[str],
    min_rows: int,
    short_row_widths: list[int] | None = None,
) -> dict:
    short_row_widths = short_row_widths or []
    problems: list[str] = []
    if not path.is_file():
        raise SystemExit(f"capture does not exist: {path}")
    size = path.stat().st_size
    if size == 0:
        raise SystemExit(f"capture is zero bytes: {path}")
    # A capture checked here is a CSV. A binary one (sa2-snapshots.bin) read as
    # text died with a UnicodeDecodeError traceback and no receipt, after the
    # game had run (demo-handoff-snap-v1a, 2026-09-23); it is named instead.
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream)
            try:
                header = next(reader)
            except StopIteration:
                raise SystemExit(f"capture has no header: {path}") from None
            rows = list(reader)
    except UnicodeDecodeError as e:
        raise SystemExit(f"capture is not UTF-8 text, so not a CSV: {path} ({e.reason} at byte "
                         f"{e.start}); pass a binary capture with --binary-capture") from None
    if not rows:
        problems.append(f"no data rows (header only, {len(header)} columns)")
    widths = {len(row) for row in rows}
    allowed = {len(header), *short_row_widths}
    unexpected = sorted(widths - allowed)
    if unexpected:
        problems.append(
            f"row width disagrees with the header: header={len(header)} "
            f"allowed={sorted(allowed)} unexpected={unexpected}"
        )
    if rows and len(rows) > 1:
        last = len(rows[-1])
        earlier = {len(row) for row in rows[:-1]}
        if last not in allowed and last < max(earlier | {len(header)}):
            problems.append(
                f"trailing row is torn: last row has {last} fields, "
                f"every earlier row has one of {sorted(earlier)} — "
                "the capture was cut mid-write, most likely by the run timeout"
            )
    if len(rows) < min_rows:
        problems.append(f"fewer than {min_rows} data rows ({len(rows)})")
    varying: dict[str, int] = {}
    for field in require_varying:
        if field not in header:
            problems.append(f"required field {field!r} is not in the header")
            continue
        index = header.index(field)
        values = {row[index] for row in rows if index < len(row)}
        varying[field] = len(values)
        if len(values) <= 1:
            problems.append(
                f"field {field!r} never varies across {len(rows)} rows "
                f"(value set: {sorted(values)})"
            )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema": "sa2-capture-check/v1",
        "path": str(path),
        "size": size,
        "sha256": digest,
        "columns": len(header),
        "header": header,
        "rows": len(rows),
        "row_widths": sorted(widths),
        "distinct_values": varying,
        "problems": problems,
        "ok": not problems,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--require-varying", action="append", default=[])
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--short-row-width", action="append", type=int, default=[],
                        help="a row width that is legitimate for this format, "
                             "not raggedness (the movement trace writes 3)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check(args.capture, args.require_varying, args.min_rows,
                   args.short_row_width)
    if args.json:
        json.dump(result, sys.stdout, indent=1, sort_keys=True)
        print()
    if not result["ok"]:
        for problem in result["problems"]:
            print(f"  {problem}", file=sys.stderr)
        raise SystemExit(
            f"SA2_CAPTURE_REJECTED {args.capture} problems={len(result['problems'])}"
        )
    print(
        f"SA2_CAPTURE_OK path={result['path']} rows={result['rows']} "
        f"columns={result['columns']} widths={result['row_widths']} "
        f"size={result['size']} sha256={result['sha256']}"
    )


if __name__ == "__main__":
    main()
