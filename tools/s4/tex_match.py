#!/usr/bin/env python3
"""Locate every texture blob a run recorded inside the user's installation.

Subtitle: the run's `d3d9.textures_from_files` rows, matched by SHA-256 against
the installation's files and against the members of the archives the run
actually opened, with the offset of each match.

Why a tool
----------
Two different things are checked by the same search, and neither is worth
believing without it:

  * **the runtime's SHA-256.** A hash function that is wrong is the perfect
    silent defect -- every row carries a plausible 64-character constant, every
    run agrees with every other run, and nothing downstream can tell. The
    runtime hashes the published NIST vectors at every run, and this matches
    its answers against `hashlib` over the same bytes from the other side,
    which is the independent half.
  * **the blob is the file's own bytes.** If a recorded hash is the hash of
    what the guest's own decompressor produced, or of a buffer with a header
    the engine rewrote, then "textures by hash" would not match anything S5
    later extracts from the installation. A hit at a byte offset inside a
    `.pak` says the engine handed d3dx9 the member verbatim.

The archives the run opened are searched first, so when the same asset appears
in more than one file -- and it does; one 699 180-byte DDS is byte-identical in
two of them -- the file the guest actually read is the one reported. `--deep`
searches every file in the installation by size order and can legitimately name
a different one holding identical bytes.

Nothing from the installation is written out. The output is paths, offsets,
sizes and hashes -- the same class of fact the run JSON already carries, and
never a byte of content (design rule 9).

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/tex_match.py --run private/generated/s4/run/final.on_a.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s4" / "receipts"


def sha_of(path: Path, off: int = 0, n: int | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        f.seek(off)
        left = n if n is not None else path.stat().st_size - off
        while left > 0:
            b = f.read(min(1 << 20, left))
            if not b:
                break
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--deep", action="store_true",
                    help="search every file in the installation, not only the ones the run "
                         "opened; slower, and the only way to tell 'the engine produced these "
                         "bytes' from 'they are in a file this run never touched'")
    ap.add_argument("--deep-max-bytes", type=int, default=256 << 20)
    args = ap.parse_args()

    doc = json.loads(Path(args.run).read_text())
    rows = doc["d3d9"]["textures_from_files"]["rows"]
    root = Path(doc["files"]["read_root"])

    def resolve(rel: str) -> Path | None:
        """The run records the path the *guest* asked for, and the guest's
        spelling is not the host's: `resource/gd_pc/soc/nowloading.pak` is
        `resource/gd_PC/SOC/NowLoading.pak` on disk. The runtime's file layer
        case-folds every component, and this has to as well -- the first
        version of this tool did not, so `p.is_file()` was false for most of
        the archives, they were silently dropped from the search, and sixteen
        of seventeen blobs came back "NOT FOUND" from a search that had never
        looked at the files holding them. It printed the shortened list as
        `archives_searched`, which is the only reason it was catchable."""
        cur = root
        for part in rel.replace("\\", "/").split("/"):
            if not part or part == ".":
                continue
            nxt = cur / part
            if not nxt.exists():
                low = part.lower()
                hit = [c for c in cur.iterdir() if c.name.lower() == low] if cur.is_dir() else []
                if len(hit) != 1:
                    return None
                nxt = hit[0]
            cur = nxt
        return cur if cur.is_file() else None

    opened, unresolved = [], []
    for o in doc["files"].get("opened", []):
        p = resolve(o["rel"])
        if p is None:
            unresolved.append(o["rel"])
        elif p not in opened:
            opened.append(p)

    # Every file in the installation, by size, for the whole-file case.
    by_size: dict[int, list[Path]] = {}
    for p in root.rglob("*"):
        if p.is_file():
            by_size.setdefault(p.stat().st_size, []).append(p)

    out = {"schema": "sa2-recomp-measurement/v1", "run": str(args.run),
           "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "install_root": str(root), "files_in_install": sum(len(v) for v in by_size.values()),
           "archives_searched": [str(p.relative_to(root)) for p in opened],
           "opened_paths_not_resolved": unresolved,
           "rows": []}

    # The bytes of each opened file, once: the archives are small and the
    # search is a substring scan per row.
    blobs = {p: p.read_bytes() for p in opened if p.stat().st_size < (64 << 20)}

    def scan(paths, rows_left: list[dict]) -> None:
        """Find each unmatched row's blob inside `paths`, by magic then hash."""
        for p in paths:
            if not rows_left:
                return
            try:
                b = p.read_bytes()
            except OSError:
                continue
            for r in list(rows_left):
                magic = bytes.fromhex(r["magic"])[:4]
                n, want = r["bytes"], r["sha256"]
                off = b.find(magic)
                while off >= 0:
                    if off + n <= len(b) and hashlib.sha256(b[off:off + n]).hexdigest() == want:
                        r["_hit"] = {"kind": "member of a file in the installation",
                                     "path": str(p.relative_to(root)), "offset": off}
                        rows_left.remove(r)
                        break
                    off = b.find(magic, off + 1)

    for r in rows:
        want, n = r["sha256"], r["bytes"]
        magic = bytes.fromhex(r["magic"])[:4]
        row = {"seq": r["seq"], "container": r["container"], "bytes": n,
               "sha256": want, "match": None}
        for p in by_size.get(n, []):
            if sha_of(p) == want:
                row["match"] = {"kind": "whole file",
                                "path": str(p.relative_to(root)), "offset": 0}
                break
        if row["match"] is None:
            for p, b in blobs.items():
                off = b.find(magic)
                while off >= 0:
                    if off + n <= len(b) and hashlib.sha256(b[off:off + n]).hexdigest() == want:
                        row["match"] = {"kind": "member of an archive the run opened",
                                        "path": str(p.relative_to(root)), "offset": off}
                        break
                    off = b.find(magic, off + 1)
                if row["match"]:
                    break
        out["rows"].append(row)

    if args.deep:
        left = []
        for row, r in zip(out["rows"], rows):
            if row["match"] is None:
                row["magic"] = r["magic"]
                left.append(row)
        paths = sorted((p for v in by_size.values() for p in v
                        if p.stat().st_size <= args.deep_max_bytes),
                       key=lambda q: q.stat().st_size)
        out["deep_files_searched"] = len(paths)
        scan(paths, left)
        for row in out["rows"]:
            if row.get("_hit"):
                row["match"] = row.pop("_hit")
            row.pop("magic", None)
            row.pop("_hit", None)

    found = sum(1 for r in out["rows"] if r["match"])
    out["summary"] = {"rows": len(out["rows"]), "located": found,
                      "unlocated": len(out["rows"]) - found,
                      "verdict": ("every recorded hash is the hash of bytes that are in the "
                                  "installation, so the runtime's SHA-256 agrees with hashlib "
                                  "over the same bytes and the guest passes the member verbatim")
                      if found == len(out["rows"]) and found
                      else "at least one recorded blob is not the installation's own bytes"}
    path = args.out or (OUT / "tex-match.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["summary"], indent=1))
    for r in out["rows"]:
        m = r["match"]
        print(f"  seq {r['seq']:3d} {r['container']:5s} {r['bytes']:9d} "
              + (f"{m['path']} +{m['offset']}" if m else "NOT FOUND"))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
