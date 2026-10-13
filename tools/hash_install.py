#!/usr/bin/env python3
"""Hash the shipped files of the user's SA2 installation into a receipt.

Writes Documentation/receipts/install-hashes-v1.json: relative path, size and
SHA-256 for every shipped file of the installation. The hashes are not
content and may be committed; the boundary gate
(Documentation/scripts/check_boundary.py) uses them to refuse any tracked
file that is a shipped asset, and `configure` (S4) uses them to verify a
user's installation. Deterministic: sorted by path.

What the adversarial review of 2026-09-19 changed here, and why:

- `--install` defaults to the live Steam installation when it exists and to
  `private/install` otherwise, because the first receipt hashed
  `private/install`, a symlink to the predecessor's modded staging copy: of
  its 4446 entries, 250 lay outside `resource/` (59 under `mods/`, 157 under
  `redist/`, 15 crash dumps, the Mod Loader's own files), and three
  mod-supplied files (`SA2ModLoader.dll`, `sonic2app-unpacked.exe`, the Mod
  Loader's proxy `Data_DLL.dll` beside the renamed `Data_DLL_orig.dll`) sit
  where a path rule cannot tell them from shipped ones, so only a clean tree
  gives a clean receipt.
- Only `resource/` and the top-level `.exe`/`.dll` files are hashed, and
  under those `mods/`, `CrashDump/`, `SAVEDATA/`, `*.ini`, `*.cfg` and
  `*_orig.*` are excluded, so hashing a modded tree at least drops what a
  path can identify. `SOC/` stays in although the review listed it: on the
  live Steam tree it is 90 shipped `.pak` menu files that
  `Documentation/scope/install.csv` lists, and a hash set that omits them
  would let one be committed unnoticed.
- Zero-byte files are skipped and counted, because the empty file's SHA-256
  is not evidence of anything and its presence made every empty tracked file
  fail the boundary gate as a "shipped asset".
- The receipt records the resolved tree (`hashed_tree`), whether Mod Loader
  files were present (`modded`, `mod_loader_markers_present`), counts per
  top-level entry, the `resource/gd_PC` file count and a `notes` field that
  diffs that directory against `install.csv`, so a reader can see the hashed
  tree is the shipped one without trusting this docstring.

Usage: .venv/bin/python tools/hash_install.py [--install DIR] [--out FILE]
       [--install-csv Documentation/scope/install.csv]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
STEAM_INSTALL = Path.home() / ".local/share/Steam/steamapps/common/Sonic Adventure 2"
STAGED_INSTALL = ROOT / "private" / "install"
INSTALL_CSV = ROOT / "Documentation" / "scope" / "install.csv"
DEFAULT_OUT = ROOT / "Documentation" / "receipts" / "install-hashes-v1.json"

SHIPPED_TREE = "resource"            # the shipped data tree
GD_PC = "resource/gd_PC"             # what install.csv enumerates
TOP_LEVEL_SUFFIXES = frozenset({".exe", ".dll"})
EXCLUDED_DIRECTORIES = frozenset({"mods", "CrashDump", "SAVEDATA"})
EXCLUDED_SUFFIXES = frozenset({".ini", ".cfg"})
ORIG_COPY = re.compile(r"_orig\.[^/]*$")
# Any of these present means the Mod Loader was installed into the tree.
MOD_LOADER_MARKERS = ("SA2ModLoader.dll", "sa2mlver.txt", "mods",
                      "resource/gd_PC/DLL/Win32/Data_DLL_orig.dll")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def exclusion(rel: PurePosixPath, size: int) -> str | None:
    """Why `rel` is not hashed, or None when it is a shipped file."""
    top_level = len(rel.parts) == 1
    if not (rel.parts[0] == SHIPPED_TREE
            or (top_level and rel.suffix.lower() in TOP_LEVEL_SUFFIXES)):
        return "outside resource/ and not a top-level .exe/.dll"
    excluded = EXCLUDED_DIRECTORIES.intersection(rel.parts[:-1])
    if excluded:
        return f"under {sorted(excluded)[0]}/"
    if rel.suffix.lower() in EXCLUDED_SUFFIXES:
        return f"user config ({rel.suffix.lower()})"
    if ORIG_COPY.search(rel.name):
        return "*_orig.* (Mod Loader's renamed original)"
    if size == 0:
        return "zero bytes"
    return None


def csv_paths(install_csv: Path) -> list[str]:
    with install_csv.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "path" not in reader.fieldnames:
            raise SystemExit(f"{install_csv}: no 'path' column in header "
                             f"{reader.fieldnames}")
        return [row["path"] for row in reader if row["path"]]


def default_install() -> Path:
    return STEAM_INSTALL if STEAM_INSTALL.is_dir() else STAGED_INSTALL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install", type=Path, default=None,
                        help=f"installation to hash (default: {STEAM_INSTALL} if it "
                             f"exists, else private/install)")
    parser.add_argument("--install-csv", type=Path, default=INSTALL_CSV,
                        help="the shipped-file enumeration to diff resource/gd_PC against")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    install_arg = args.install if args.install is not None else default_install()
    install = install_arg.resolve()
    if not install.is_dir():
        raise SystemExit(f"no installation at {install}")
    if not args.install_csv.is_file():
        raise SystemExit(f"no shipped-file enumeration at {args.install_csv}")
    expected = csv_paths(args.install_csv)

    on_disk = sorted(p for p in install.rglob("*") if p.is_file())
    entries: list[dict] = []
    excluded_under_resource: list[dict] = []
    excluded_counts: Counter[str] = Counter()
    top_level: dict[str, dict[str, int]] = {}
    zero_byte_skipped = 0
    gd_pc_on_disk: list[str] = []
    gd_pc_hashed = 0
    for path in on_disk:
        rel = PurePosixPath(path.relative_to(install).as_posix())
        size = path.stat().st_size
        bucket = top_level.setdefault(rel.parts[0], {"on_disk": 0, "hashed": 0})
        bucket["on_disk"] += 1
        under_gd_pc = rel.as_posix().startswith(GD_PC + "/")
        if under_gd_pc:
            gd_pc_on_disk.append(rel.as_posix()[len(GD_PC) + 1:])
        reason = exclusion(rel, size)
        if reason is not None:
            excluded_counts[reason] += 1
            if reason == "zero bytes":
                zero_byte_skipped += 1
            if rel.parts[0] == SHIPPED_TREE:
                excluded_under_resource.append({"path": rel.as_posix(), "reason": reason})
            continue
        bucket["hashed"] += 1
        if under_gd_pc:
            gd_pc_hashed += 1
        entries.append({"path": rel.as_posix(), "bytes": size, "sha256": sha256(path)})

    markers = [m for m in MOD_LOADER_MARKERS if (install / m).exists()]
    modded = bool(markers)
    extra = sorted(set(gd_pc_on_disk) - set(expected))
    missing = sorted(set(expected) - set(gd_pc_on_disk))
    csv_rel = (args.install_csv.resolve().relative_to(ROOT).as_posix()
               if args.install_csv.resolve().is_relative_to(ROOT) else str(args.install_csv))
    gd_pc_excluded = [e for e in excluded_under_resource
                      if e["path"].startswith(GD_PC + "/")]
    notes = (
        f"{GD_PC} holds {len(gd_pc_on_disk)} files on disk; {csv_rel} lists "
        f"{len(expected)} (its 'path' column is relative to {GD_PC}). "
        f"Extra on disk, not in the csv: {extra if extra else 'none'}. "
        f"In the csv, missing on disk: {missing if missing else 'none'}. "
        f"Of the {len(gd_pc_on_disk)} on disk, {gd_pc_hashed} were hashed and "
        f"{len(gd_pc_excluded)} excluded by rule: "
        f"{[f'{e['path']} ({e['reason']})' for e in gd_pc_excluded] if gd_pc_excluded else 'none'}. "
        + (f"The hashed tree carries Mod Loader files (markers present: {markers}); "
           f"the rules above excluded them."
           if modded else "No Mod Loader files were found in the hashed tree.")
    )
    total = sum(e["bytes"] for e in entries)
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "install-hashes-v1",
        "question": "What are the sizes and SHA-256 hashes of every shipped file "
                    "of the SA2 installation?",
        "inputs": {"install": str(install_arg), "install_csv": csv_rel},
        "command": f"tools/hash_install.py --install '{install}'",
        "hashed_tree": str(install),
        "modded": modded,
        "mod_loader_markers_present": markers,
        "rules": {
            "included": f"files under {SHIPPED_TREE}/ plus top-level "
                        f"{sorted(TOP_LEVEL_SUFFIXES)} files",
            "excluded_directories": sorted(EXCLUDED_DIRECTORIES),
            "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
            "excluded_name_pattern": ORIG_COPY.pattern,
            "zero_byte_files": "skipped",
        },
        "file_count": len(entries),
        "total_bytes": total,
        "zero_byte_skipped": zero_byte_skipped,
        "excluded_counts": dict(sorted(excluded_counts.items())),
        "excluded_under_resource": excluded_under_resource,
        "top_level_counts": dict(sorted(top_level.items())),
        "gd_pc_file_count": len(gd_pc_on_disk),
        "gd_pc_hashed": gd_pc_hashed,
        "notes": notes,
        "authority": "observed",
        "files": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    out = args.out.resolve()
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"INSTALL_HASHES_OK files={len(entries)} bytes={total} "
          f"zero_byte_skipped={zero_byte_skipped} modded={modded} "
          f"gd_pc={len(gd_pc_on_disk)}/{len(expected)} out={shown}")
    if extra or missing:
        print(f"  gd_PC differs from the csv: {len(extra)} extra, {len(missing)} missing",
              file=sys.stderr)


if __name__ == "__main__":
    main()
