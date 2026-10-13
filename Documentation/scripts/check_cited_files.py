#!/usr/bin/env python3
"""Every file a durable record cites must exist.

In `reference-project` the camera journal cited a 106-column native trace at a
path that held a 52-column file, and the checker that read it could not run;
nothing noticed until the scope enumeration re-read the path. A durable
record that describes a file that does not exist is the same failure as a
timeline that describes work that did not happen, so it gets the same
treatment: a gate. That version covered one artifact class (private
captures under `native-run/`). Design section 9 asks for the cited-file gate
over every artifact class, so this one covers them all.

What is scanned (the citing sources): `Documentation/**/*.md`,
`Documentation/**/*.json` (receipts), `agents-journals/**/*.md`,
`AGENTS.md`, `README.md`.

What counts as a citation: a token starting with one of the repository's
top-level directories (`private/`, `Documentation/`, `tools/`, `runtime/`,
`emitter/`, `host/`, `harness/`, `agents-journals/`) whose last component
ends in a file extension or a version suffix such as `-v3`, plus
`native-run/...` (the predecessor's capture directory), which resolves under
`private/predecessor-native-run/`. Prose fragments that end in a hyphen or
underscore (a glob a record wrote out, such as `contact-survey-`), bare
directory names, and globs (`Documentation/**/*.md`) are not citations. Each
citation must exist relative to the project root; `private/` paths are
checked on disk even though git ignores them, and a symlink counts.

Imported sources (`Documentation/predecessor/`, `Documentation/scope/`,
`Documentation/native-evidence-receipts/`, `Documentation/native-evidence-sources/`,
`Documentation/tests/`, the import manifest, and the design document, which
was written against the predecessor's layout) are scanned like everything
else, but a path they cite that does not exist here is resolved against the
predecessor checkout named in `Documentation/provenance/import-manifest.json`
and counted as predecessor-resolved when it exists there. Citations of the
Unity-era layout (`Assets/`, `LocalAssets/`, `Library/`, `work/`) are
counted and skipped: the import manifest records that tree. If the
predecessor checkout is absent on this machine the gate fails, because a
citation it cannot verify is not a citation it has verified; pass
`--allow-missing-predecessor` to count them as unverified instead, and the
OK line says so.

`METRICS.md` is not a citing source: it is generated, and its "input absent"
notes name the contract paths later stages will produce. For the same
reason `Documentation/stages.json` is not one: a stage's `remaining_units`
hook lists the receipts the stage must deliver, which do not exist until
it does; metrics.py counts how many of them are tracked, which is the
measurement, so treating the list as citations would make every stage
start red. For the same
reason fenced code blocks (```...```) in Markdown are not scanned: a gate
journal quotes the generated report verbatim inside a fence, and the
report names those same contract paths as absent inputs. A fence is quoted
output, not a claim that a file exists.

Paths a record cites knowingly as absent are listed in KNOWN_ABSENT with the
record that explains them; anything else missing fails. The seventeen lost
captures of 2026-09-19 (design section 2.3) are carried over verbatim.
Prints the count and a sha256 marker over the sorted cited set so a change
in what the records cite is visible in the gate output.

The project root is `git rev-parse --show-toplevel` from the current
directory, or `--root PATH`; it is not derived from this file's location, so
the tests can run this script against throwaway repositories.

Usage: .venv/bin/python Documentation/scripts/check_cited_files.py
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from gitlog import project_root

REPO_PREFIXES = ("private", "Documentation", "tools", "runtime", "emitter",
                 "host", "harness", "agents-journals")
# The predecessor's capture directory, re-rooted here.
NATIVE_RUN_PREFIX = "native-run"
NATIVE_RUN_ROOT = "private/predecessor-native-run"
# Unity-era layout of reference-project; cited by imported records, absent here.
SKIPPED_PREFIXES = ("Assets", "LocalAssets", "Library", "work")

# `+` is here because the project's build tags use it -- `next+allarms` is the
# arm-seeded whole image -- and without it a cited path truncates at the plus
# to a prefix naming no file, which the gate then reports as a missing file
# that was never cited. It cost two receipts a spurious failure before the
# alphabet was the suspect rather than the records.
PATH_CHARS = r"[A-Za-z0-9_.+\-/]"
# A repository path cannot be preceded by a path character (so
# `/home/x/Documentation/y.md` and `../Documentation/y.md` are not citations
# of this tree), and it must be followed by a non-path character.
CITED = re.compile(
    r"(?<![A-Za-z0-9_.+\-/])((?:" + "|".join(REPO_PREFIXES + SKIPPED_PREFIXES)
    + r")/" + PATH_CHARS + r"+)")
# `native-run/` may follow a slash: records wrote `$SA2_PRIVATE_WORK/native-run/...`.
CITED_NATIVE_RUN = re.compile(
    r"(?<![A-Za-z0-9_.+\-])(" + NATIVE_RUN_PREFIX + r"/" + PATH_CHARS + r"+)")
TRAILING_PUNCTUATION = ".,;:)]'\"`"
# The last component names a file (an extension) or a versioned capture
# directory (`-v3`, `-v10a`); a bare word or a fragment ending in - or _ does not.
CITABLE_LAST = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_+\-]*?(?:\.[A-Za-z0-9]+|-v\d+[a-z]?)$")

FENCE = re.compile(r"^```.*?^```[ \t]*$", re.DOTALL | re.MULTILINE)

SOURCE_GLOBS = (
    ("Documentation", "**/*.md"),
    ("Documentation", "**/*.json"),
    ("agents-journals", "**/*.md"),
)
SOURCE_FILES = ("AGENTS.md", "README.md")
# Records imported from, or written against, the predecessor's layout.
IMPORTED_SOURCE_PREFIXES = (
    "Documentation/predecessor/", "Documentation/scope/",
    "Documentation/native-evidence-receipts/", "Documentation/native-evidence-sources/",
    "Documentation/tests/", "Documentation/provenance/import-manifest.json",
    "Documentation/SA2_RECOMP_DESIGN.md",
)
IMPORT_MANIFEST = "Documentation/provenance/import-manifest.json"

# Paths a record cites as absent on purpose, with the record that says so.
# Keys are repository-relative, as the gate normalises citations.
KNOWN_ABSENT: dict[str, str] = {
    f"{NATIVE_RUN_ROOT}/metalharbor-camera-default-native-trace.csv":
        "Documentation/scope/camera.md: the second documented trace location, absent",
    # Retired with the shared object it was the version script of: the second host is its
    # own statically linked player since, and no build reads it.
    "host/d3d9gpu/libsa2_d3d9gpu.map":
        "agents-journals/s5/2026-10-03-r2a-host-contract.md: the version script of "
        "libsa2_d3d9gpu.so, read by no build since the second host became its own "
        "statically linked player (sa2_player_gpu); the committed receipt "
        "Documentation/receipts/s5-host2-r2a-v1.json pins it as it stood at development_snapshot",
    # The run's environment as the arm64 runs set it: the word log was written there and then
    # compressed to the .zst beside it, which the receipt cites and pins by its sha256.
    **{f"private/generated/s7/wave3/arm64/runs6-a64/{replay}/lifted-words.bin":
       "Documentation/receipts/s7-arm64-replays-v1.json: SA2_REPLAY_WORDS as the run set it; "
       "the log is kept compressed beside it, as lifted-words.bin.zst"
       for replay in ("boot", "metalharbor")},
}
# Captures cited by records of 2026-09-16 and 2026-09-17 that exist nowhere
# on the machine (not in the private tree, not in any development snapshot; found
# by this gate's first run on 2026-09-19, searched by basename). The fixtures
# staged from them survive under LocalAssets and still gate the kernels; the
# captures themselves cannot be re-read, so the records that cite them
# describe evidence that is unreproducible at the capture level. Listed here
# so the gate holds from this date; adding a new entry requires the same
# search and a journal line saying why.
LOST_2026_09_19 = (
    "collision-step-augmented",
    "collision-step-augmented/metalharbor-augmented-native-trace-v2.csv",
    "collision-step-augmented/metalharbor-collision-candidates-v2.csv",
    "collision-step-augmented/metalharbor-collision-step-v2.csv",
    "collision-step-branch-probe/metalharbor-branch-collision-candidates-v3.csv",
    "collision-step-branch-probe/metalharbor-branch-collision-step-v3.csv",
    "collision-step-branch-probe/metalharbor-branch-native-trace-v3.csv",
    "collision-step-branch-probe/metalharbor-branch-precise-contact-v3.csv",
    "collision-step-branch-probe/native-trace-v2-objdump.txt",
    "collision-step-geometry-probe/reconstruct_plane_basis_v1.py",
    "collision-step-precise/sa2-native-collision-candidates.csv",
    "collision-step-precise/sa2-native-collision-step.csv",
    "collision-step-precise/sa2-native-trace.csv",
    "metalharbor-homing-native-trace.csv",
    "metalharbor-jump-native-trace.csv",
    "one-sided-oracle-v10",
    "one-sided-oracle-v3/sa2-one-sided-oracle-v1-corrected-audit.csv",
)
for _rel in LOST_2026_09_19:
    KNOWN_ABSENT[f"{NATIVE_RUN_ROOT}/{_rel}"] = (
        "design section 2.3 / reference-project agents-journals/operations/"
        "2026-09-19-scope-enumeration.md: capture lost; the fixture staged "
        "from it survives")


def normalise(token: str) -> str | None:
    """The repository-relative path a token cites, or None if not a citation."""
    token = token.rstrip(TRAILING_PUNCTUATION)
    parts = token.split("/")
    if any(part == "" for part in parts) or len(parts) < 2:
        return None          # a trailing slash, a doubled slash, a bare prefix
    if not CITABLE_LAST.match(parts[-1]):
        return None          # a bare word or a glob fragment, not a path
    if parts[0] == NATIVE_RUN_PREFIX:
        return "/".join([NATIVE_RUN_ROOT, *parts[1:]])
    return token


NOT_SOURCES = ("Documentation/stages.json",)


def sources(root: Path) -> list[Path]:
    found: set[Path] = set()
    for directory, pattern in SOURCE_GLOBS:
        base = root / directory
        if base.is_dir():
            found.update(path for path in base.glob(pattern) if path.is_file()
                         and path.relative_to(root).as_posix() not in NOT_SOURCES)
    for name in SOURCE_FILES:
        if (root / name).is_file():
            found.add(root / name)
    return sorted(found)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--list", action="store_true",
                        help="print every cited path and its citing records")
    args = parser.parse_args()
    root = project_root(args.root)
    predecessor: Path | None = None
    predecessor_commit = ""
    manifest = root / IMPORT_MANIFEST
    if manifest.is_file():
        import json
        info = json.loads(manifest.read_text())["predecessor"]
        candidate = Path(info["path"])
        predecessor_commit = info["commit"]
        predecessor = candidate if (candidate / ".git").exists() else None

    def in_predecessor(rel: str) -> bool:
        """True when the path exists in the predecessor at its pinned commit."""
        if predecessor is None:
            return False
        done = subprocess.run(
            ["git", "-C", str(predecessor), "cat-file", "-e",
             f"{predecessor_commit}:{rel}"], capture_output=True)
        return done.returncode == 0

    records = sources(root)
    cited: dict[str, set[str]] = {}
    skipped: dict[str, set[str]] = {}
    for path in records:
        text = path.read_text(errors="replace")
        if path.suffix == ".md":
            text = FENCE.sub("", text)
        who = path.relative_to(root).as_posix()
        for pattern in (CITED, CITED_NATIVE_RUN):
            for match in pattern.finditer(text):
                rel = normalise(match.group(1))
                if rel is None:
                    continue
                bucket = skipped if rel.split("/", 1)[0] in SKIPPED_PREFIXES else cited
                bucket.setdefault(rel, set()).add(who)

    missing: dict[str, list[str]] = {}
    predecessor_resolved: list[str] = []
    for rel in sorted(cited):
        if (root / rel).exists() or (root / rel).is_symlink():
            continue
        if rel in KNOWN_ABSENT:
            continue
        if all(who.startswith(IMPORTED_SOURCE_PREFIXES) for who in cited[rel]):
            if in_predecessor(rel):
                predecessor_resolved.append(rel)
                continue
        missing[rel] = sorted(cited[rel])
    if predecessor is None and any(
            all(who.startswith(IMPORTED_SOURCE_PREFIXES) for who in cited[rel])
            for rel in missing):
        sys.exit("cited files: the predecessor checkout named in "
                 f"{IMPORT_MANIFEST} is absent, so imported records' citations "
                 "cannot be verified; this gate does not pass without it")

    if args.list:
        for rel in sorted(cited):
            print(f"  {rel}  <- {', '.join(sorted(cited[rel]))}")

    marker = hashlib.sha256("\n".join(sorted(cited)).encode()).hexdigest()
    if missing:
        lines = [f"  {rel}  (cited by {', '.join(who[:3])}"
                 f"{' ...' if len(who) > 3 else ''})" for rel, who in missing.items()]
        sys.exit(f"cited files missing ({len(missing)} of {len(cited)} distinct paths):\n"
                 + "\n".join(lines))
    print(f"cited files OK: {len(cited)} distinct paths cited by {len(records)} "
          f"records exist, resolve in the predecessor at {predecessor_commit[:12]} "
          f"({len(predecessor_resolved)}), or are listed as known absent "
          f"({len(KNOWN_ABSENT)} known absent); {len(skipped)} Unity-era paths "
          f"skipped; marker_sha256={marker}")


if __name__ == "__main__":
    main()
