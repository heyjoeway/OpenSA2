#!/usr/bin/env python3
"""Inventory the owned SA2 installation's shipped data by kind, as counts.

Scope enumeration, fourth dimension: the other three count object types,
actions and camera modes; this counts what the installation ships, grouped by
the name prefixes and extensions the game uses (SET files, camera files, land
tables, models, textures, motions, fonts, audio, event data), per stage where
the name carries a stage number. Numbers and receipts only; no claim about
what any file means beyond its name.

Usage: python3 Documentation/scripts/enumerate_sa2_install_inventory.py
       [--install DIR] [--out Documentation/scope]
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import os
from pathlib import Path
import re
import sys

DEFAULT_INSTALL = Path.home() / ".local/share/Steam/steamapps/common/Sonic Adventure 2"
# SET files carry four digits (set0010_s.bin); stage and camera files carry
# two (stg10cam.prs, stg10_light.bin). The first version required four and so
# classified only the SET files; a review found the per-stage table one
# column wide. Both widths are accepted and the stage number is normalised
# to two digits.
STAGE = re.compile(r"^(?P<prefix>[a-z]+?)(?P<stage>\d{2,4})(?P<rest>.*)$", re.I)


def classify(name: str) -> tuple[str, str | None, str]:
    """(kind, stage or None, variant) from a file name."""
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    m = STAGE.match(stem)
    if m and m.group("prefix").lower() in ("set", "cam", "stg", "objlandtable", "stage"):
        return (m.group("prefix").lower(), m.group("stage")[-2:].zfill(2),
                m.group("rest").lower())
    return ext.lstrip(".") or "noext", None, ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, default=DEFAULT_INSTALL)
    parser.add_argument("--out", type=Path, default=Path("Documentation/scope"))
    args = parser.parse_args()
    root = args.install / "resource" / "gd_PC"
    if not root.is_dir():
        sys.exit(f"missing installation data directory: {root}")
    rows = []
    by_kind: collections.Counter = collections.Counter()
    by_ext: collections.Counter = collections.Counter()
    stages: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    total_bytes = 0
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for path in files:
        rel = path.relative_to(root).as_posix()
        kind, stage, variant = classify(path.name)
        size = path.stat().st_size
        total_bytes += size
        by_kind[kind] += 1
        by_ext[path.suffix.lower() or "(none)"] += 1
        if stage is not None:
            stages[stage][kind] += 1
        rows.append((rel, kind, stage or "", variant, size))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "install.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["path", "kind", "stage", "variant", "bytes"])
        writer.writerows(rows)
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(str(path.stat().st_size).encode())
    lines = ["# Shipped data inventory (owned installation)", "",
             f"Directory: `{root}`", "",
             f"Files: {len(files)}; bytes: {total_bytes}; name+size digest: `{digest.hexdigest()}`", "",
             "## By kind (name prefix with a stage number, else extension)", "",
             "| kind | files |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in by_kind.most_common()]
    lines += ["", "## By extension", "", "| extension | files |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in by_ext.most_common()]
    lines += ["", f"## Per stage number ({len(stages)} stage numbers carry files)", "",
              "| stage | " + " | ".join(sorted({k for c in stages.values() for k in c})) + " |",
              "|---|" + "---|" * len({k for c in stages.values() for k in c})]
    kinds = sorted({k for c in stages.values() for k in c})
    for stage in sorted(stages):
        lines.append(f"| {stage} | " + " | ".join(str(stages[stage].get(k, 0)) for k in kinds) + " |")
    lines += ["", "## Method", "",
              "Every regular file under the data directory is classified by name: a "
              "prefix of `set`, `cam`, `stg`, `objlandtable` or `stage` followed by four "
              "digits is a per-stage file of that kind with that stage number and the "
              "remainder of the stem as its variant; anything else is grouped by "
              "extension. Nothing is opened; this is a count of what ships, not of what "
              "it contains.", "",
              "## Receipts", "",
              f"Script sha256: `{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}`",
              f"Listing digest (names and sizes, sorted): `{digest.hexdigest()}`"]
    (args.out / "install.md").write_text("\n".join(lines) + "\n")
    print(f"install inventory: files={len(files)} kinds={len(by_kind)} stages={len(stages)} "
          f"digest={digest.hexdigest()}")


if __name__ == "__main__":
    main()
