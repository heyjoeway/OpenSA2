#!/usr/bin/env python3
"""Correct the frozen v1 oracle CSV header without changing captured rows."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


RAW_SHA256 = "7a4b4897e6275949205d3467803422b010e3caa6724a2a90998f44a26ef00a5f"
CORRECTED_SHA256 = "74517d3b51c0420a1501d61c8843c7fa34099c34a69a578997e8971861ba1cb5"
RAW_FIELD = b",one_sided_mask,matrix_argument,"
CORRECTED_FIELD = b",late_scratch_1c_bits,matrix_argument,"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_csv", type=Path)
    parser.add_argument("corrected_csv", type=Path)
    args = parser.parse_args()

    raw_path = args.raw_csv.expanduser().resolve()
    corrected_path = args.corrected_csv.expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if raw_path == corrected_path:
        raise SystemExit("Raw and corrected paths must be different")
    try:
        corrected_path.relative_to(project)
    except ValueError:
        pass
    else:
        raise SystemExit("Corrected evidence must remain outside the Git repository")
    if corrected_path.exists() or corrected_path.is_symlink():
        raise SystemExit(f"Refusing to overwrite corrected evidence: {corrected_path}")

    raw = raw_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != RAW_SHA256:
        raise SystemExit(
            f"{raw_path}: expected frozen raw SHA-256 {RAW_SHA256}, got {digest}"
        )
    if raw.count(RAW_FIELD) != 1 or CORRECTED_FIELD in raw:
        raise SystemExit(f"{raw_path}: unexpected v1 header schema")
    corrected = raw.replace(RAW_FIELD, CORRECTED_FIELD, 1)
    corrected_digest = hashlib.sha256(corrected).hexdigest()
    if corrected_digest != CORRECTED_SHA256:
        raise SystemExit(
            f"Corrected schema SHA-256 mismatch: {corrected_digest}"
        )
    corrected_path.parent.mkdir(parents=True, exist_ok=True)
    corrected_path.write_bytes(corrected)
    print(
        "SA2_ONE_SIDED_ORACLE_SCHEMA_OK "
        f"raw_sha256={digest} "
        f"corrected_sha256={corrected_digest}"
    )


if __name__ == "__main__":
    main()
