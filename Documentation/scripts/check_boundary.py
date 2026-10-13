#!/usr/bin/env python3
"""Fail if anything git tracks crosses the boundary in design section 10.

The repository is the clean side of the Unleashed Recompiled position:
derivative in the release binary, clean in the tree, assets never included.
That holds only if nothing shipped, nothing derived from the shipped
executable, and nothing from the user's installation is ever committed,
and "never" is not a rule an agent remembers, it is a rule a gate enforces
on every pass (S0 exit criterion; AGENTS.md rule 9).

Every git-tracked file (`git ls-files`, so the index, which is what a commit
would carry) fails the gate if it

  (a) hashes to a pinned executable sha256 from `tools/pins.json`
      ("executables": the shipped and the unpacked `sonic2app.exe`);
  (b) hashes to a file of the user's installation as recorded by
      `Documentation/receipts/install-hashes-v1.json` (schema
      `sa2-recomp-receipt/v1`, "files": [{"path","bytes","sha256"}], written
      by `tools/hash_install.py`); the receipt itself is skipped. An absent
      receipt is a failure that names the receipt and the tool: it used to be
      a note, which made a skip count as a pass, and the receipt now exists
      and is tracked. Receipt entries with `bytes == 0` are ignored, because
      the empty file's sha256 made every empty tracked file fail as a
      "shipped asset";
  (c) lives under `private/` or `build/`, or is a symlink whose target is
      inside either (the `.gitignore` comment explains why a link is as bad
      as the directory: it would replace the real directory on checkout);
  (d) has a shipped-data extension (`SHIPPED_EXTENSIONS`, verified against
      the receipt's and `Documentation/scope/install.csv`'s extension sets)
      outside `Documentation/tests/`, or under `Documentation/tests/` at
      4 KiB or more: a test fixture needs a header, not an asset, so the
      former blanket exemption became a size allowance;
  (e) carries the lifted-output layout: a first line equal to `LIFTED_BANNER`
      (a module constant the emitter will import from here, so the emitter
      and the gate cannot drift apart), or a name matching `sub_XXXXXXXX.c`,
      `lifted_*.c`, `function_table.c` or `lifted.h`; or, for a tracked
      .c/.h/.cpp/.inc file, content that looks lifted regardless of banner
      and name: three or more distinct `sub_XXXXXXXX` identifiers, or
      `GUEST_ADDRESS_LIMIT` (100) or more distinct guest-address literals
      `0x400000..0xFFFFFF`. Banner and filename alone were a string the
      emitter could change; content is not. The review proposed 40
      addresses, but the tracked hand-written hook mod
      `Documentation/scripts/sa2_native_trace.c` names 43 distinct game
      addresses, so 40 would fail the tree it was added to; lifted code
      names hundreds. No lifted output is ever committed, even as a sample;
  (f) is larger than 8 MiB, a size fuse: nothing the tree legitimately holds
      is that large, and every class of thing that must never be committed is;
  (g) is a text file (.json .md .txt .csv .py .c .h .cs .java .tsv) holding
      a contiguous run of 2048 or more base64 characters or 512 or more hex
      characters: whole-file hashing cannot see shipped bytes embedded in a
      fixture, and no legitimate file here holds such a run (a sha256 is 64
      hex characters). Nothing is skipped: the scan over every tracked text
      file including the 680 KB receipt measured 0.08 s.

It also fails if `.gitignore` lacks the exact lines `/private` and `/build`
(no trailing slash: with one, the pattern matches a directory but not a
symlink of the same name, which is how reference-project once committed a link).

Hashes are compared exactly: a known hash is replaced, never pattern-matched
by name (AGENTS.md rule 8). The project root is `git rev-parse
--show-toplevel` from the current directory, or `--root PATH`; it is not
derived from this file's location, so the tests can run this script against
throwaway repositories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from gitlog import project_root

PINS = "tools/pins.json"
INSTALL_RECEIPT = "Documentation/receipts/install-hashes-v1.json"
INSTALL_RECEIPT_SCHEMA = "sa2-recomp-receipt/v1"
INSTALL_RECEIPT_TOOL = "tools/hash_install.py"
IGNORED_DIRECTORIES = ("private", "build")
REQUIRED_IGNORE_LINES = ("/private", "/build")
# The receipt's extension set plus install.csv's, minus generic ones (.txt
# .ini .inf .conf) that a repository legitimately holds.
SHIPPED_EXTENSIONS = frozenset(
    ".exe .dll .prs .pak .adx .ahx .gvr .gvm .pvr .pvm .bin .mdl .set .chunk "
    ".xvm .sfd .csb .dds .scr .rel .mid .gvp .m1v .afs .str .plt .bnr .dat".split())
SHIPPED_EXTENSIONS_ALLOWED_UNDER = "Documentation/tests/"
FIXTURE_SIZE_ALLOWANCE = 4 * 1024

# The emitter imports this and writes it as the first line of every lifted
# file; the gate fails any tracked file that starts with it.
LIFTED_BANNER = "/* sa2-recomp lifted */"
LIFTED_NAMES = (re.compile(r"^sub_[0-9A-Fa-f]{8}\.c$"),
                re.compile(r"^lifted_.*\.c$"),
                re.compile(r"^function_table\.c$"),
                re.compile(r"^lifted\.h$"))
LIFTED_SOURCE_SUFFIXES = frozenset({".c", ".h", ".cpp", ".inc"})
SUB_IDENTIFIER = re.compile(rb"\bsub_[0-9A-Fa-f]{8}\b")
SUB_IDENTIFIER_LIMIT = 3
GUEST_ADDRESS_LITERAL = re.compile(rb"\b0x0*[4-9A-Fa-f][0-9A-Fa-f]{5}\b")
GUEST_ADDRESS_LIMIT = 100

SIZE_FUSE = 8 * 1024 * 1024

TEXT_SUFFIXES = frozenset(".json .md .txt .csv .py .c .h .cs .java .tsv".split())
BASE64_RUN = re.compile(rb"[A-Za-z0-9+/=]{2048,}")
HEX_RUN = re.compile(rb"[0-9a-fA-F]{512,}")


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(("git", "-C", str(root), "ls-files", "-z"),
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git ls-files failed: {result.stderr.strip()}")
    return [path for path in result.stdout.split("\0") if path]


def pinned_hashes(root: Path) -> dict[str, str]:
    """sha256 -> executable name from tools/pins.json."""
    pins = root / PINS
    if not pins.is_file():
        raise SystemExit(f"missing {PINS}; the boundary gate needs the executable pins")
    data = json.loads(pins.read_text())
    executables = data.get("executables")
    if not isinstance(executables, dict) or not executables:
        raise SystemExit(f"{PINS} has no \"executables\" section")
    known: dict[str, str] = {}
    for name, entry in executables.items():
        digest = (entry or {}).get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SystemExit(f"{PINS}: executables.{name}.sha256 is not a sha256")
        known[digest] = name
    return known


def install_hashes(root: Path) -> dict[str, str] | None:
    """sha256 -> install path from the receipt (zero-byte entries dropped), or None if absent."""
    receipt = root / INSTALL_RECEIPT
    if not receipt.is_file():
        return None
    data = json.loads(receipt.read_text())
    if data.get("schema") != INSTALL_RECEIPT_SCHEMA:
        raise SystemExit(f"{INSTALL_RECEIPT}: schema is {data.get('schema')!r}, "
                         f"expected {INSTALL_RECEIPT_SCHEMA!r}")
    files = data.get("files")
    if not isinstance(files, list):
        raise SystemExit(f"{INSTALL_RECEIPT}: \"files\" is not a list")
    known: dict[str, str] = {}
    for entry in files:
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SystemExit(f"{INSTALL_RECEIPT}: entry {entry.get('path')!r} "
                             f"has no sha256")
        if entry.get("bytes") == 0:
            continue
        known.setdefault(digest, str(entry.get("path")))
    return known


def symlink_into_ignored(root: Path, path: Path) -> bool:
    try:
        target = Path(os.readlink(path))
    except OSError:
        return False
    resolved = (path.parent / target).resolve() if not target.is_absolute() else target.resolve()
    for name in IGNORED_DIRECTORIES:
        try:
            resolved.relative_to((root / name).resolve())
            return True
        except ValueError:
            continue
    return False


def gitignore_ok(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return [".gitignore is missing"]
    lines = {line.strip() for line in gitignore.read_text().splitlines()}
    return [f".gitignore lacks the exact line {line!r}"
            for line in REQUIRED_IGNORE_LINES if line not in lines]


def lifted_content(data: bytes) -> str | None:
    """Why a source file's content looks lifted, or None."""
    subs = set(SUB_IDENTIFIER.findall(data))
    if len(subs) >= SUB_IDENTIFIER_LIMIT:
        return (f"{len(subs)} distinct sub_XXXXXXXX identifiers "
                f"(limit {SUB_IDENTIFIER_LIMIT})")
    addresses = {int(literal, 16) for literal in GUEST_ADDRESS_LITERAL.findall(data)}
    if len(addresses) >= GUEST_ADDRESS_LIMIT:
        return (f"{len(addresses)} distinct guest-address literals "
                f"(limit {GUEST_ADDRESS_LIMIT})")
    return None


def embedded_blob(data: bytes) -> str | None:
    """Why a text file looks like it embeds bytes, or None."""
    match = BASE64_RUN.search(data)
    if match:
        return f"{len(match.group())}-character base64 run at byte {match.start()}"
    match = HEX_RUN.search(data)
    if match:
        return f"{len(match.group())}-character hex run at byte {match.start()}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    args = parser.parse_args()
    root = project_root(args.root)

    failures: list[str] = gitignore_ok(root)
    pinned = pinned_hashes(root)
    installed = install_hashes(root)
    if installed is None:
        failures.append(f"{INSTALL_RECEIPT} is absent; the install-hash check cannot "
                        f"run (generate it with {INSTALL_RECEIPT_TOOL})")
        installed = {}

    tracked = tracked_files(root)
    hashed = 0
    absent = 0
    for rel in tracked:
        path = root / rel
        top = rel.split("/", 1)[0]
        if top in IGNORED_DIRECTORIES:
            failures.append(f"{rel}: tracked under {top}/, which is private or generated")
        if path.is_symlink():
            if symlink_into_ignored(root, path):
                failures.append(f"{rel}: symlink into "
                                f"{'/'.join(IGNORED_DIRECTORIES)} (would replace the "
                                f"directory on checkout)")
            continue
        name = path.name
        suffix = path.suffix.lower()
        if suffix in SHIPPED_EXTENSIONS:
            if not rel.startswith(SHIPPED_EXTENSIONS_ALLOWED_UNDER):
                failures.append(f"{rel}: shipped-data extension {suffix} outside "
                                f"{SHIPPED_EXTENSIONS_ALLOWED_UNDER}")
            elif path.is_file() and path.stat().st_size >= FIXTURE_SIZE_ALLOWANCE:
                failures.append(f"{rel}: shipped-data extension {suffix} under "
                                f"{SHIPPED_EXTENSIONS_ALLOWED_UNDER} but "
                                f"{path.stat().st_size} bytes is not under the "
                                f"{FIXTURE_SIZE_ALLOWANCE} byte fixture allowance")
        for pattern in LIFTED_NAMES:
            if pattern.match(name):
                failures.append(f"{rel}: lifted-output filename ({pattern.pattern})")
                break
        if not path.is_file():
            absent += 1          # deleted in the worktree but still in the index
            continue
        size = path.stat().st_size
        if size > SIZE_FUSE:
            failures.append(f"{rel}: {size} bytes exceeds the {SIZE_FUSE} byte fuse")
            continue             # not worth reading; it fails regardless
        data = path.read_bytes()
        first = data.split(b"\n", 1)[0].decode("utf-8", "replace")
        if first.startswith(LIFTED_BANNER):
            failures.append(f"{rel}: first line is the lifted-output banner")
        if suffix in LIFTED_SOURCE_SUFFIXES:
            reason = lifted_content(data)
            if reason:
                failures.append(f"{rel}: content looks lifted: {reason}")
        if suffix in TEXT_SUFFIXES:
            reason = embedded_blob(data)
            if reason:
                failures.append(f"{rel}: embedded bytes: {reason}")
        digest = hashlib.sha256(data).hexdigest()
        hashed += 1
        if digest in pinned:
            failures.append(f"{rel}: sha256 matches pinned executable "
                            f"{pinned[digest]} ({digest[:12]}...)")
        if rel != INSTALL_RECEIPT and digest in installed:
            failures.append(f"{rel}: sha256 matches installation file "
                            f"{installed[digest]!r} ({digest[:12]}...)")

    if failures:
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(f"boundary violated: {len(failures)} finding(s) across "
                         f"{len(tracked)} tracked files")
    print(f"boundary OK: {len(tracked)} tracked files, {hashed} hashed against "
          f"{len(pinned)} pinned executable(s) and {len(installed)} installation "
          f"hashes, scanned for lifted content and embedded bytes"
          + (f", {absent} tracked but absent from the worktree" if absent else "")
          + "; .gitignore holds /private and /build")


if __name__ == "__main__":
    main()
