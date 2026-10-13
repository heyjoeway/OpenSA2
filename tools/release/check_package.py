#!/usr/bin/env python3
"""Read a ZIP, APK or DEB back and check its files before uploading a release.

Usage: python3 tools/release/check_package.py PACKAGE [--deny-file PRIVATE.json]
       [--forbidden-hashes RECEIPT.json ...] [--report CHECK.json]
Privacy terms stay in the caller's private file, outside the public repository.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def hashes(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        # Tool pins also name dependencies and patches. Only executable pins
        # identify original game files; those other inputs are distributable.
        if "executables" in value and "tools" in value:
            return hashes(value["executables"])
        for key, child in value.items():
            if key == "sha256" and isinstance(child, str) and re.fullmatch(r"[a-fA-F0-9]{64}", child):
                found.add(child.lower())
            else:
                found.update(hashes(child))
    elif isinstance(value, list):
        for child in value:
            found.update(hashes(child))
    return found


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive path: {name}")


def files(package: Path):
    if package.suffix in {".zip", ".apk"}:
        with zipfile.ZipFile(package) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError("Duplicate ZIP entries")
            for entry in archive.infolist():
                safe_name(entry.filename)
                if entry.is_dir():
                    continue
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"Unexpected ZIP symlink: {entry.filename}")
                yield entry.filename, archive.read(entry)
    elif package.suffix == ".deb":
        result = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(package)],
                                check=True, stdout=subprocess.PIPE)
        with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
            for entry in archive:
                safe_name(entry.name)
                if entry.isdir():
                    continue
                if not entry.isfile():
                    raise ValueError(f"Unexpected DEB link or special file: {entry.name}")
                stream = archive.extractfile(entry)
                assert stream is not None
                yield entry.name, stream.read()
        control = subprocess.run(["dpkg-deb", "--ctrl-tarfile", str(package)],
                                 check=True, stdout=subprocess.PIPE)
        with tarfile.open(fileobj=io.BytesIO(control.stdout)) as archive:
            for entry in archive:
                safe_name(entry.name)
                if entry.isfile():
                    stream = archive.extractfile(entry)
                    assert stream is not None
                    yield "DEBIAN/" + entry.name, stream.read()
    else:
        raise ValueError("Expected a .zip, .apk or .deb package")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--deny-file", type=Path)
    parser.add_argument("--forbidden-hashes", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    terms = json.loads(args.deny_file.read_text())["terms"] if args.deny_file else []
    forbidden: set[str] = set()
    for receipt in args.forbidden_hashes:
        forbidden.update(hashes(json.loads(receipt.read_text())))
    records = {}
    failures = []
    for name, data in files(args.package):
        digest = hashlib.sha256(data).hexdigest()
        records[name] = {"bytes": len(data), "sha256": digest}
        if PurePosixPath(name).name.lower() in {"sonic2app.exe", "sonic2app-unpacked.exe"}:
            failures.append(f"Original executable filename: {name}")
        if PurePosixPath(name).suffix.lower() in {".pdb", ".runrec", ".jsonl", ".log"}:
            failures.append(f"Unexpected development file: {name}")
        if digest in forbidden:
            failures.append(f"Original game file hash: {name}")
        lower = data.lower()
        for index, term in enumerate(terms):
            if term and any(term.lower().encode(encoding) in lower
                            for encoding in ("utf-8", "utf-16-le")):
                failures.append(f"Private term {index + 1} in {name}")
    report = {"schema": "opensa2-package-check/v1", "package": args.package.name,
              "bytes": args.package.stat().st_size,
              "sha256": hashlib.sha256(args.package.read_bytes()).hexdigest(),
              "privacy_terms": len(terms), "forbidden_hashes": len(forbidden),
              "files": records, "failures": failures}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"{args.package.name}: {len(records)} files, {len(failures)} failures")
    for failure in failures:
        print(failure)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
