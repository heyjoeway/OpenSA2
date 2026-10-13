#!/usr/bin/env python3
"""Check draw_matrix JSONL and complete BGRA files against independent literal expectations.

Subtitle: reject clear-only, black, stale pixels, missing checkpoints and fake omission controls.
  check_draw_matrix.py RESULT/lines.jsonl --factory sdlgpu [--case NAME]
  check_draw_matrix.py RESULT/lines.jsonl --factory /abs/lib.so --expect-omission NAME [--case NAME]
"""
import argparse
import hashlib
import json
from pathlib import Path

BLUE = b"\xff\x00\x00\xff"
RED = b"\x00\x00\xff\xff"
GREEN = b"\x00\xff\x00\xff"
ZERO = b"\x00\x00\x00\x00"
CASES = {
    "dp_start_offset": ("solid-red", 1),
    "dip_signed_base_start": ("solid-red", 1),
    "dp_fan": ("solid-red", 1),
    "dpup_caller_lifetime": ("solid-green", 1),
    "dipup_prefix_caller_lifetime": ("solid-green", 1),
    "uniform_snapshot": ("left-red-right-green", 2),
    "vb_upload_order": ("left-red-right-green", 2),
    "draw_clear_draw": ("left-red-middle-blue-right-green", 2),
    "release_after_draw": ("solid-red", 1),
    "readback_texture_draw": ("solid-red", 2),
    "no_input_unbound": ("blue-with-red-at-32-32", 1),
    "no_input_unused_real": ("blue-with-red-at-32-32", 1),
    "null_only_unbound": ("blue-with-zero-at-32-32", 1),
    "null_only_unused_real": ("blue-with-zero-at-32-32", 1),
    "real0_null15": ("left-zero-right-blue", 1),
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def nonnegative_int(record, key):
    value = record.get(key)
    require(type(value) is int and value >= 0, "expected nonnegative integer: " + key)
    return value


def literal_bytes(pattern):
    if pattern == "solid-red":
        return RED * 4096
    if pattern == "solid-green":
        return GREEN * 4096
    if pattern == "left-red-right-green":
        return (RED * 32 + GREEN * 32) * 64
    if pattern == "left-red-middle-blue-right-green":
        return (RED * 32 + BLUE * 16 + GREEN * 16) * 64
    if pattern == "left-zero-right-blue":
        return (ZERO * 32 + BLUE * 32) * 64
    if pattern in ("blue-with-red-at-32-32", "blue-with-zero-at-32-32"):
        point = RED if pattern == "blue-with-red-at-32-32" else ZERO
        return BLUE * 2080 + point + BLUE * 2015
    raise ValueError("unknown literal pattern")


def checkpoints(selected):
    result = []
    for case in selected:
        if case == "readback_texture_draw":
            result.append((case, "seed", "solid-red", 1))
        pattern, calls = CASES[case]
        result.append((case, "final", pattern, calls))
    return result


def check(lines_path, factory, selected_case=None, omission=None):
    require(factory == "sdlgpu" or factory.startswith("/"), "factory must be sdlgpu or an absolute library path")
    lines_path = Path(lines_path).resolve(strict=True)
    rows, markers = [], []
    for line in lines_path.read_text().splitlines():
        if line.startswith("{"):
            row = json.loads(line)
            require(type(row) is dict, "JSON result row must be an object")
            rows.append(row)
        elif line:
            markers.append(line)
    require(rows and rows[0].get("type") == "header", "missing initial header")
    header = rows[0]
    require(type(header.get("adapter")) is str and bool(header["adapter"]), "missing adapter description")
    require(nonnegative_int(header, "schema") == 1 and header.get("factory") == factory, "wrong schema/factory")
    require((nonnegative_int(header, "width"), nonnegative_int(header, "height"), header.get("format")) == (64, 64, "BGRA8"), "wrong target")
    require(header.get("window") == "hidden" and nonnegative_int(header, "presents") == 0, "window/Present contract")
    require(header.get("omit_draw") == (omission or ""), "wrong omission identity")
    for field in ("executable", "factory_file"):
        path = Path(header[field])
        require(path.is_absolute() and path.resolve(strict=True) == path, "noncanonical binary path")
        hash_field = "executable_sha256" if field == "executable" else "factory_sha256"
        require(hashlib.sha256(path.read_bytes()).hexdigest() == header[hash_field], "binary changed: " + field)
    expected_factory = header["executable"] if factory == "sdlgpu" else str(Path(factory).resolve(strict=True))
    require(header["factory_file"] == expected_factory, "factory came from a different library")
    require(selected_case is None or selected_case in CASES, "unknown selected case")
    require(omission is None or omission in CASES, "unknown omitted case")
    require(not omission or not selected_case or omission == selected_case, "omission outside selection")
    selected = [selected_case] if selected_case else list(CASES)
    wanted = checkpoints(selected)
    if omission:
        stop = next(i for i, row in enumerate(wanted) if row[0] == omission)
        wanted = wanted[:stop + 1]
    actual = [row for row in rows if row.get("type") == "checkpoint"]
    require(len(actual) == len(wanted), "missing/extra checkpoints")
    for row, (case, label, pattern, calls) in zip(actual, wanted, strict=True):
        require((row.get("case"), row.get("checkpoint")) == (case, label), "wrong checkpoint order/identity")
        require(nonnegative_int(row, "draw_calls") == calls and row.get("before_present") is True, "draw/readback contract")
        omitted = calls if case == omission else 0
        require(nonnegative_int(row, "omitted_calls") == omitted, "omitted call count")
        require(row.get("expected_pattern") == pattern, "producer changed literal pattern")
        raw = Path(row["raw"])
        require(raw.is_absolute() and raw.resolve(strict=True) == raw, "noncanonical raw path")
        require(raw.parent == lines_path.parent and raw.name == f"{case}-{label}.bgra", "raw file outside result directory")
        data = raw.read_bytes()
        expected = literal_bytes(pattern)
        require(len(data) == 16384, "incomplete BGRA target")
        require(hashlib.sha256(data).hexdigest() == row.get("sha256"), "actual hash disagrees with raw target")
        require(hashlib.sha256(expected).hexdigest() == row.get("expected_sha256"), "expected hash differs from independent literal")
        matched = data == expected
        require(row.get("matched") is matched, "producer matched flag lies")
        mismatch = next((i for i, (a, b) in enumerate(zip(data, expected, strict=True)) if a != b), len(data))
        require(nonnegative_int(row, "first_mismatch_byte") == mismatch, "wrong first mismatch")
        require(matched == (case != omission), "literal pixel/no-op expectation")
        if case == omission:
            require(data == BLUE * 4096, "omission pixels differ from the exact blue clear")
    if omission:
        require(markers == ["DRAW_MATRIX_FAIL"], "omission did not fail through literal pixel check")
        require(len(rows) == len(actual) + 2 and rows[-1].get("type") == "failure", "omission failure record")
        require(rows[-1].get("reason") == f"literal pixel expectation failed: {wanted[-1][0]}/{wanted[-1][1]}", "omission failed for a collateral reason")
    else:
        require(markers == ["DRAW_MATRIX_OK"], "producer did not complete successfully")
        require(len(rows) == len(actual) + 2 and rows[-1].get("type") == "summary", "extra/missing result records")
        summary = rows[-1]
        require(nonnegative_int(summary, "cases") == len(selected) and nonnegative_int(summary, "checkpoints") == len(wanted), "summary count")
        require(summary.get("passed") is True and nonnegative_int(summary, "presents") == 0, "summary contract")
    return {"checkpoints": len(actual), "scope": "omission-rejected" if omission else "literal-pixels"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lines")
    parser.add_argument("--factory", required=True)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--expect-omission", choices=CASES)
    args = parser.parse_args()
    try:
        result = check(args.lines, args.factory, args.case, args.expect_omission)
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
        print(f"DRAW_MATRIX_CHECK_FAIL {error}")
        return 1
    print("DRAW_MATRIX_CHECK_OK " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
