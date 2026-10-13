#!/usr/bin/env python3
"""Rouge Ledger: verify a private SA2 evidence packet against its receipt.

Tracked receipts contain hashes and metadata only.  Private decompilation and
assembly remain outside Git in the owned-install research tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


SCHEMA = "sa2-native-evidence-receipt/v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path, check=True,
        capture_output=True, text=True,
    )
    return Path(result.stdout.strip()).resolve()


def beneath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def fail(message: str) -> None:
    raise SystemExit(f"SA2_EVIDENCE_RECEIPT_ERROR {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="Tracked hash-only receipt")
    parser.add_argument("packet", type=Path, help="Private evidence packet directory")
    parser.add_argument(
        "--source", type=Path,
        help="Optional private executable whose hash must match the receipt",
    )
    args = parser.parse_args()

    project = git_root(Path(__file__).parent)
    receipt = args.receipt.expanduser().resolve()
    packet = args.packet.expanduser().resolve()
    if not beneath(receipt, project) or not receipt.is_file():
        fail(f"receipt must be a tracked file inside Git: {receipt}")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(receipt.relative_to(project))],
        cwd=project, capture_output=True,
    )
    if tracked.returncode != 0:
        fail(f"receipt is not tracked by Git: {receipt}")
    if beneath(packet, project) or not packet.is_dir():
        fail(f"packet must be an existing private directory outside Git: {packet}")

    try:
        payload = json.loads(receipt.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read receipt: {error}")
    if payload.get("schema") != SCHEMA or payload.get("complete") is not True:
        fail(f"receipt must be a complete {SCHEMA} document")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        fail("receipt has no artifacts")

    seen: set[str] = set()
    verified = 0
    for entry in artifacts:
        if not isinstance(entry, dict):
            fail("artifact entry is not an object")
        name = entry.get("file")
        expected_size = entry.get("size")
        expected_hash = entry.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or name in seen:
            fail(f"unsafe or duplicate artifact name: {name!r}")
        if not isinstance(expected_size, int) or expected_size < 0:
            fail(f"invalid size for {name}")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            fail(f"invalid SHA-256 for {name}")
        seen.add(name)
        artifact = packet / name
        if not artifact.is_file():
            fail(f"missing artifact: {name}")
        actual_size = artifact.stat().st_size
        if actual_size != expected_size:
            fail(f"{name} size {actual_size} != {expected_size}")
        actual_hash = sha256(artifact)
        if actual_hash != expected_hash:
            fail(f"{name} SHA-256 {actual_hash} != {expected_hash}")
        verified += 1

    if args.source is not None:
        source = args.source.expanduser().resolve()
        expected = payload.get("source", {}).get("sha256")
        if not source.is_file() or not isinstance(expected, str):
            fail("source file or receipt source hash is missing")
        actual = sha256(source)
        if actual != expected:
            fail(f"source SHA-256 {actual} != {expected}")

    print(
        f"SA2_EVIDENCE_RECEIPT_OK artifacts={verified} "
        f"receipt={receipt.name} packet={packet}"
    )


if __name__ == "__main__":
    main()
