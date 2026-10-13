#!/usr/bin/env python3
"""Prove the guest never wrote to the user's installation.

Subtitle: a manifest of every file under the install root -- path, size,
mode, mtime to the nanosecond, and the content hash of everything small --
taken before a run and compared after it.

Why this is not the runtime's own counter
-----------------------------------------
`runtime/sa2_files.c` counts `install_write_attempts`, and that counter is a
guard against a future edit passing the wrong root, not evidence: it lives in
the same process as the code it is checking and it is structurally zero. The
evidence that the installation is untouched has to come from outside, and it
has to be taken *before* as well as after -- an "after" reading on its own
compares a number with nothing.

What the manifest does and does not contain
-------------------------------------------
Path names, sizes, modes, mtimes, and SHA-256 digests. No file content, and
nothing derived from content except a digest, which is the whole point of a
digest. The manifest lands under `private/generated/`, which is git-ignored,
and nothing in it would be a boundary violation if it did not.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/install_guard.py --snapshot before
  # ... run the guest ...
  .venv/bin/python tools/s4/install_guard.py --snapshot after --compare before
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s4" / "guard"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: Path, hash_under: int) -> dict:
    rows = {}
    hashed = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            try:
                st = p.lstat()
            except OSError as e:
                rows[rel] = {"error": str(e)}
                continue
            row = {"size": st.st_size, "mode": st.st_mode, "mtime_ns": st.st_mtime_ns}
            if st.st_size <= hash_under and not os.path.islink(p):
                try:
                    row["sha256"] = sha256_file(p)
                    hashed += 1
                except OSError as e:
                    row["error"] = str(e)
            rows[rel] = row
    dirs = sum(1 for _ in os.walk(root, followlinks=False))
    return {"root": str(root), "files": len(rows), "directories": dirs,
            "hashed": hashed, "hash_under": hash_under,
            "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}


def compare(before: dict, after: dict) -> dict:
    b, a = before["rows"], after["rows"]
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed = []
    for k in sorted(set(a) & set(b)):
        if a[k] != b[k]:
            diff = {f: [b[k].get(f), a[k].get(f)] for f in set(b[k]) | set(a[k])
                    if b[k].get(f) != a[k].get(f)}
            changed.append({"path": k, "fields": diff})
    # Both sides must be non-empty, or "no differences" means "nothing was
    # compared". That is the shape of check that has failed to fail here before.
    compared = len(set(a) & set(b))
    return {"files_before": before["files"], "files_after": after["files"],
            "compared": compared, "added": added, "removed": removed,
            "changed": changed,
            "identical": compared > 0 and not added and not removed and not changed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="default $SA2_INSTALL or private/install")
    ap.add_argument("--snapshot", required=True, help="a name, e.g. before / after")
    ap.add_argument("--compare", default=None, help="a previous snapshot name")
    ap.add_argument("--hash-under", type=int, default=1 << 20)
    args = ap.parse_args()

    root = Path(args.root or os.environ.get("SA2_INSTALL")
                or (C.ROOT / "private" / "install")).resolve()
    if not root.is_dir():
        raise SystemExit(f"install root is not a directory: {root}")
    OUT.mkdir(parents=True, exist_ok=True)

    snap = snapshot(root, args.hash_under)
    path = OUT / f"install.{args.snapshot}.json"
    path.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
    print(f"{args.snapshot}: {snap['files']} files, {snap['directories']} directories, "
          f"{snap['hashed']} hashed -> {path}")

    if args.compare:
        prev = OUT / f"install.{args.compare}.json"
        if not prev.is_file():
            raise SystemExit(f"no snapshot to compare with: {prev}")
        res = compare(json.loads(prev.read_text()), snap)
        res["before"] = str(prev.relative_to(C.ROOT))
        res["after"] = str(path.relative_to(C.ROOT))
        cf = OUT / f"install.compare.{args.compare}-{args.snapshot}.json"
        cf.write_text(json.dumps(res, indent=1) + "\n")
        print(f"compared {res['compared']} files: added={len(res['added'])} "
              f"removed={len(res['removed'])} changed={len(res['changed'])} "
              f"identical={res['identical']} -> {cf}")
        raise SystemExit(0 if res["identical"] else 1)


if __name__ == "__main__":
    main()
