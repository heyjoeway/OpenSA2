#!/usr/bin/env python3
"""Fail when a receipt pins a tool digest that did not exist when it was written.

A receipt under `Documentation/receipts/` records a measurement, and its
`inputs.pipeline` object records what produced it: a map from a
repository-relative tool path to that tool's sha256. The pin is the receipt's
claim that *this* code produced *these* numbers, and it is the only part of a
receipt that can still be checked mechanically after the run is over.

Nothing checked it. `check_pins.py` covers `tools/pins.json`, which is the
toolchain -- Ghidra, Java 21, zig, the interpreter -- and not these.
`chaos_control.py audit` covers the `pins` nodes a receipt carries, and not
`inputs.pipeline`. So `Documentation/receipts/s3-whole-image-link-v1.json` was
committed pinning `tools/s3/whole_image/receipt.py` at a digest that appears at
no commit in this repository and on no file on disk, and all seventeen gates
passed over it. A receipt naming a version of a tool that never existed is a
receipt whose provenance is decoration: it looks like evidence and holds none.

The rule is not "match HEAD"
----------------------------

A receipt is a historical record. It is entirely correct for an old receipt to
pin an old version of a tool that has legitimately changed since -- the
measurement was taken with the old one, and that is the fact the receipt is
there to preserve. A gate demanding that every receipt match HEAD would demand
that history be rewritten every time a tool is edited, which is the opposite of
what a receipt is for, and would train everyone to refresh pins to silence it:
the exact shape AGENTS.md rule 8 forbids and `refresh_receipt_pins.py` is
careful to avoid.

So each pin is compared against the receipt's own moment:

  * a **committed** receipt is measured against the tool as it stood at the
    commit that last modified the receipt (`git log -1 -- <receipt>`), read
    with `git cat-file blob <commit>:<path>`;
  * a receipt with **staged or unstaged changes**, or one not committed at all,
    is measured against the **working tree**. The gates run before the commit
    (`ship.py` regenerates, gates, then commits), so a receipt being written in
    the commit under construction has no commit of its own to be measured
    against yet. This is `check_durable_records_fresh.py`'s `staged_update()`
    reasoning: a record being updated in the very commit under construction
    counts as current. That gate reads the update from the index, which is what
    the commit carries; this one still reads it from the working tree, so under
    `ship.py --no-add` a receipt left unstaged is measured here as if the commit
    carried it. The report names the basis used for each receipt, so a
    pass against the working tree is never mistaken for a pass against history.

A pinned file deleted or renamed *after* its receipt was written is therefore
invisible here, and that is the design rather than a gap: for a committed
receipt this gate never looks at HEAD or at the worktree, so a tool's later
life cannot make an honest old receipt fail. The only thing it can catch is a
digest that was already wrong at the moment it was recorded.

What is checked and what is not
-------------------------------

Scanned: every `*.json` under `Documentation/receipts/`, recursively, so
`residues/` counts too. A receipt whose JSON does not parse, or whose
`inputs.pipeline` cannot be interpreted as pins, fails -- an unreadable receipt
is an unverified one.

The `schema` field is deliberately not consulted. `s3-whole-image-link-v1.json`
carries `"schema": 1` where its siblings carry `"sa2-recomp-receipt/v1"`, so a
gate that filtered on schema would have skipped precisely the receipt holding
the defect this gate exists for.

The cases this gate had to decide, and what it decided:

  * **No `inputs.pipeline` at all**, or no `inputs`: nothing to check. Most
    receipts here are in this state and they are counted and named in the
    summary, not failed. This gate verifies the pins a receipt makes; it does
    not require a receipt to make any. "Every receipt must pin its pipeline"
    is a different rule, it would fail every receipt written before the
    convention existed, and it needs its own argument and its own commit.
  * **`inputs.pipeline` that is a string.** `emitter-determinism-v1.json`
    carries a prose command line there rather than a map. Prose is not a pin
    and cannot be compared, so it is counted as prose and not checked -- but
    the count is printed, because a way to hold no pins while looking like you
    do should at least be visible in the gate's own output.
  * **A value that is not a digest.** A pin value may be a 64-hex string, or an
    object carrying a 64-hex `sha256` (the shape receipt `pins` nodes use
    elsewhere). Anything else fails, naming the shape found. A pin whose digest
    is not a digest can never be compared, and a check that cannot fail is not
    a check.
  * **A path that is not repository-relative** (absolute, or climbing out with
    `..`) fails: it names a file this repository cannot vouch for.
  * **A pinned path absent at the reference** -- not in that commit, or not in
    the working tree when that is the basis -- fails, naming the reference. A
    receipt pinning a file that did not exist when it was written is pinning
    nothing, which is the same defect class as pinning a digest that never
    existed.

Output ends with `marker_sha256=<hex>` over the sorted receipt/path/digest
triples, as the other gates do, so any change in what the receipts claim to
have been produced by is visible in the gate output and can be pinned by a
suite in `Documentation/fidelity-ledger.json`.

The project root is `git rev-parse --show-toplevel` from the current directory,
or `--root PATH`; it is not derived from this file's location, so the tests can
run this script against throwaway repositories.

Usage: .venv/bin/python Documentation/scripts/check_receipt_pins.py [--list]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from gitlog import project_root

RECEIPTS = "Documentation/receipts"
SHA256 = re.compile(r"[0-9a-f]{64}")

# How a receipt's pins were resolved, for the per-receipt report line.
WORKTREE = "the working tree"


def receipt_files(root: Path) -> list[Path]:
    base = root / RECEIPTS
    if not base.is_dir():
        raise SystemExit(f"missing {RECEIPTS}; the receipt-pin gate has nothing to check")
    return sorted(path for path in base.rglob("*.json") if path.is_file())


def last_commit_sha(root: Path, relative: str) -> str:
    """The commit that last modified `relative`, or "" when it has none."""
    done = subprocess.run(
        ("git", "-C", str(root), "log", "-1", "--format=%H", "--", relative),
        capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"git log -- {relative} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def pending_update(root: Path, relative: str) -> bool:
    """True when the receipt has staged, unstaged or untracked changes.

    The same reasoning as `check_durable_records_fresh.staged_update`: the
    gate runs before the commit, so a receipt written in the commit under
    construction must be measured against the working tree it is being
    written in, not against a commit that does not exist yet. Unlike that
    gate, this reads `git status`, so a change the index does not hold counts
    too (the module docstring).
    """
    done = subprocess.run(("git", "-C", str(root), "status", "--porcelain", "--", relative),
                          capture_output=True, text=True, check=False)
    return done.returncode == 0 and bool(done.stdout.strip())


def blob_at(root: Path, commit: str, relative: str) -> bytes | None:
    """The file's bytes at `commit`, or None when it is not a file there."""
    done = subprocess.run(("git", "-C", str(root), "cat-file", "blob",
                           f"{commit}:{relative}"), capture_output=True, check=False)
    return done.stdout if done.returncode == 0 else None


def bytes_in_worktree(root: Path, relative: str) -> bytes | None:
    path = root / relative
    if not path.is_file():
        return None
    return path.read_bytes()


def pin_digest(value: object) -> str | None:
    """The 64-hex digest a pin value carries, or None when it carries none.

    A pin is either the digest itself or an object with a `sha256` field, the
    shape receipt `pins` nodes use. Nothing else is a digest.
    """
    if isinstance(value, str):
        return value if SHA256.fullmatch(value) else None
    if isinstance(value, dict):
        inner = value.get("sha256")
        if isinstance(inner, str) and SHA256.fullmatch(inner):
            return inner
    return None


def describe(value: object) -> str:
    """A short, safe description of a pin value that is not a digest."""
    if isinstance(value, str):
        return f"the string {value[:24]!r}" + ("..." if len(value) > 24 else "")
    if isinstance(value, dict):
        return f"an object with keys {sorted(value)[:6]}"
    return f"a {type(value).__name__}"


def repository_relative(path: str) -> bool:
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    parts = path.replace("\\", "/").split("/")
    return ".." not in parts and "" not in parts[:-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--list", action="store_true",
                        help="print every pin, its receipt and the basis it was checked against")
    args = parser.parse_args()
    root = project_root(args.root)

    failures: list[str] = []
    reports: list[str] = []
    listing: list[str] = []
    triples: list[str] = []

    receipts = receipt_files(root)
    with_pins = 0
    without_pins: list[str] = []
    prose_pins: list[str] = []
    verified = 0
    at_commit = 0
    at_worktree = 0

    for path in receipts:
        relative = path.relative_to(root).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            failures.append(f"{relative}: cannot be read as JSON ({error}); a receipt "
                            f"that cannot be parsed cannot be verified")
            continue
        if not isinstance(data, dict):
            failures.append(f"{relative}: top-level JSON is a "
                            f"{type(data).__name__}, not an object")
            continue
        identifier = data.get("id") or relative
        inputs = data.get("inputs")
        pipeline = inputs.get("pipeline") if isinstance(inputs, dict) else None

        if pipeline is None:
            without_pins.append(relative)
            continue
        if isinstance(pipeline, str):
            # Prose, not a pin map. Counted so the shape stays visible.
            prose_pins.append(relative)
            continue
        if not isinstance(pipeline, dict):
            failures.append(f"{relative} ({identifier}): inputs.pipeline is "
                            f"{describe(pipeline)}; the gate understands a path -> sha256 "
                            f"map or a prose command line, and cannot verify this")
            continue

        with_pins += 1
        commit = last_commit_sha(root, relative)
        if not commit:
            use_worktree, basis_note = True, "not committed yet"
        elif pending_update(root, relative):
            use_worktree, basis_note = True, "updated in the working tree"
        else:
            use_worktree, basis_note = False, f"last committed {commit[:7]}"
        if use_worktree:
            at_worktree += 1
            where = WORKTREE
        else:
            at_commit += 1
            where = f"commit {commit[:7]} (which last modified this receipt)"

        for pinned_path in sorted(pipeline):
            value = pipeline[pinned_path]
            triples.append(f"{relative}\t{pinned_path}\t"
                           f"{pin_digest(value) or describe(value)}")
            label = f"{relative} ({identifier}): inputs.pipeline pin {pinned_path}"
            if not repository_relative(pinned_path):
                failures.append(f"{label} is not a repository-relative path, so this "
                                f"repository cannot vouch for what it names")
                continue
            expected = pin_digest(value)
            if expected is None:
                failures.append(f"{label} carries {describe(value)}, not a sha256. A pin "
                                f"whose digest is not a digest can never be compared; "
                                f"record the file's sha256 or drop the entry")
                continue
            content = (bytes_in_worktree(root, pinned_path) if use_worktree
                       else blob_at(root, commit, pinned_path))
            if content is None:
                failures.append(f"{label} names a file that is not present in {where}. A "
                                f"receipt that pins a file which did not exist when it "
                                f"was written is pinning nothing")
                continue
            actual = hashlib.sha256(content).hexdigest()
            if args.list:
                listing.append(f"  {'OK ' if actual == expected else 'BAD'} {relative}  "
                               f"{pinned_path}  {expected[:12]}  ({basis_note})")
            if actual != expected:
                failures.append(
                    f"{label} at {expected}, but in {where} that file is {actual}. The "
                    f"receipt records a measurement made with a version of the tool that "
                    f"was not there when the receipt was written")
                continue
            verified += 1

        reports.append(f"{relative}: {len(pipeline)} pipeline pin(s), checked against "
                       f"{basis_note}")

    marker = hashlib.sha256("\n".join(sorted(triples)).encode()).hexdigest()

    stream = sys.stderr if failures else sys.stdout
    for line in reports:
        print(f"  {line}", file=stream)
    for line in listing:
        print(line, file=stream)

    if failures:
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        print("  What to do: a receipt is history and is not refreshed to silence a "
              "gate (AGENTS.md rule 8). Re-run the tool that writes the receipt so it "
              "records a real measurement, or, if the measurement stands, replace the "
              "exact wrong digest with the one the file actually had at that commit and "
              "say in the stage journal which of the two it was and why.",
              file=sys.stderr)
        print(f"  marker_sha256={marker}", file=sys.stderr)
        broken = sorted({line.split(":", 1)[0] for line in failures})
        raise SystemExit(f"receipt pins FAIL: {len(failures)} finding(s) in "
                         f"{len(broken)} receipt(s): {', '.join(broken)}")

    print(f"receipt pins OK: {verified} pin(s) across {with_pins} receipt(s) match the "
          f"tool as of the receipt's own moment ({at_commit} against the commit that "
          f"wrote them, {at_worktree} against the working tree); of {len(receipts)} "
          f"receipts, {len(without_pins)} carry no inputs.pipeline and "
          f"{len(prose_pins)} carry a prose pipeline "
          f"({', '.join(prose_pins) if prose_pins else 'none'}); "
          f"marker_sha256={marker}")


if __name__ == "__main__":
    main()
