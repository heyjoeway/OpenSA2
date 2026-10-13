#!/usr/bin/env python3
"""Hash every file in a tree, and say exactly what changed between two hashes.

Subtitle: prove the staged installation was not written.

The predecessor's probe runner mutated the private installation and restored
it in a `finally`. Restoration was checked for one file (the Mod Loader
profile index) by comparing bytes it had itself saved. Nothing checked the
rest of the tree, and nothing could have noticed a file a probe created.
This takes a full content snapshot so the question "did the run write the
installation" has an answer that can come back "yes".

The snapshot is sha256 per regular file, plus the symlink target for every
symlink, plus the sorted set of directories. An empty tree, a missing tree
and an unreadable tree are errors, never an empty snapshot that compares
equal to another empty snapshot.

Usage:
  tree_snapshot.py snapshot <dir> <out.json>
  tree_snapshot.py compare <before.json> <after.json>   # exit 1 if different
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

SCHEMA = "sa2-tree-snapshot/v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(root: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    files: dict[str, str] = {}
    links: dict[str, str] = {}
    dirs: list[str] = []
    errors: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames.sort()
        filenames.sort()
        rel_dir = here.relative_to(root).as_posix()
        if rel_dir != ".":
            dirs.append(rel_dir)
        for name in list(dirnames):
            entry = here / name
            if entry.is_symlink():
                dirnames.remove(name)
                links[entry.relative_to(root).as_posix()] = os.readlink(entry)
        for name in filenames:
            entry = here / name
            rel = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                links[rel] = os.readlink(entry)
                continue
            try:
                files[rel] = sha256_file(entry)
            except OSError as error:
                errors.append(f"{rel}: {error}")
    if not files and not links:
        # A snapshot of nothing would compare equal to a snapshot of nothing,
        # which is the shape of a check that cannot fail.
        raise SystemExit(f"refusing to snapshot an empty tree: {root}")
    return {
        "schema": SCHEMA,
        "root": str(root),
        "file_count": len(files),
        "symlink_count": len(links),
        "dir_count": len(dirs),
        "files": files,
        "symlinks": links,
        "dirs": sorted(dirs),
        "unreadable": sorted(errors),
    }


def compare(before: dict, after: dict) -> list[str]:
    for side, snap in (("before", before), ("after", after)):
        if snap.get("schema") != SCHEMA:
            raise SystemExit(f"{side} snapshot is not {SCHEMA}")
        if not snap.get("files") and not snap.get("symlinks"):
            raise SystemExit(f"{side} snapshot is empty; refusing a vacuous comparison")
    if before["root"] != after["root"]:
        raise SystemExit(
            f"snapshots describe different roots: {before['root']} vs {after['root']}"
        )
    differences: list[str] = []
    for kind in ("files", "symlinks"):
        old, new = before[kind], after[kind]
        for rel in sorted(set(old) - set(new)):
            differences.append(f"removed {kind[:-1]}: {rel}")
        for rel in sorted(set(new) - set(old)):
            differences.append(f"created {kind[:-1]}: {rel}")
        for rel in sorted(set(old) & set(new)):
            if old[rel] != new[rel]:
                differences.append(f"modified {kind[:-1]}: {rel} {old[rel]} -> {new[rel]}")
    for rel in sorted(set(before["dirs"]) - set(after["dirs"])):
        differences.append(f"removed dir: {rel}")
    for rel in sorted(set(after["dirs"]) - set(before["dirs"])):
        differences.append(f"created dir: {rel}")
    return differences


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    action = sys.argv[1]
    if action == "snapshot":
        if len(sys.argv) != 4:
            raise SystemExit("usage: tree_snapshot.py snapshot <dir> <out.json>")
        snap = snapshot(Path(sys.argv[2]))
        out = Path(sys.argv[3])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
        print(
            f"SA2_TREE_SNAPSHOT root={snap['root']} files={snap['file_count']} "
            f"symlinks={snap['symlink_count']} dirs={snap['dir_count']} "
            f"unreadable={len(snap['unreadable'])} out={out}"
        )
    elif action == "compare":
        if len(sys.argv) != 4:
            raise SystemExit("usage: tree_snapshot.py compare <before.json> <after.json>")
        before = json.loads(Path(sys.argv[2]).read_text())
        after = json.loads(Path(sys.argv[3]).read_text())
        differences = compare(before, after)
        if differences:
            print(f"SA2_TREE_CHANGED root={before['root']} differences={len(differences)}")
            for line in differences[:200]:
                print(f"  {line}")
            raise SystemExit(1)
        print(
            f"SA2_TREE_UNCHANGED root={before['root']} files={before['file_count']} "
            f"symlinks={before['symlink_count']} dirs={before['dir_count']}"
        )
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
