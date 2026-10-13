#!/usr/bin/env python3
"""The project's SDL3 patches (patches/sdl3/), checked against tools/pins.json and applied strictly.

tools/s5/build_host_deps.sh runs this before it configures a patched SDL3. Nothing here is
from the SDL project, and nothing is prepared for or sent to SDL (the user's second rulings of
2026-10-04, Documentation/decisions/2026-10-04-S5-user-rulings-2.md).

  sdl3_patches.py check --root ROOT
      the patch set: the files patches/sdl3/*.patch, in file-name order, are exactly the ones
      tools.sdl3.patches pins, each a regular file that hashes to its pin; tools.sdl3.vendor_info
      is the identity derived from those hashes (identity below); and each patch's header,
      the text before its first "diff --git" line, says it is not from the SDL project and names
      the pinned commit (tools.sdl3.commit) and the pinned tarball's sha256 (tools.sdl3.sha256).
      Prints each patch with its sha256, then "VENDOR_INFO <identity>".
  sdl3_patches.py source --root ROOT --tree TREE
      the extracted tarball at TREE is the pinned commit: its .git-hash reads tools.sdl3.commit
      and its REVISION.txt reads release-<version>-0-g<the commit's first 9 digits>.
  sdl3_patches.py apply --root ROOT --tree TREE
      `check`, then each patch applied to TREE in order by GNU patch with no fuzz
      (--fuzz=0 --forward --batch), first as a dry run. GNU patch is the one tools.patch pins
      (its path, and its --version line holding the pinned version), run in a fixed
      environment (PATH /usr/bin:/bin and LC_ALL=C, plus helper-owned TMPDIR for application;
       nothing of the caller's: no LANGUAGE, which
      would translate the messages read below, and no POSIXLY_CORRECT or PATCH_* variable, which
      change what patch does). It stops with a message naming the
      patch when a hunk does not apply exactly (also at an offset: patch would place it, the
      pinned tarball never needs it), when the patch names no file, when a file it names is
      unchanged afterwards (an apply that changes nothing), or when a .rej or .orig file is
      left in TREE. Prints SDL3_PATCHES_APPLIED <n> <identity>.
  sdl3_patches.py identity FILE...
      the vendor identity of these patch files, in the order given.

The identity, as each patch's header states: "sa2-recomp sdl3-patches " and the first 12 hex
digits of the sha256 of L, where L is, for each patch file in order, its sha256 as 64 lowercase
hex digits followed by one newline byte. The build passes it to SDL as SDL_VENDOR_INFO, so
SDL_GetRevision() reads "SDL-<REVISION.txt> (<identity>)": the library is marked as altered
(SDL's zlib licence, clause 2) and names the patch set it was built from.

Exit 0 on success; 1 with "sdl3_patches.py: <reason>" on stderr otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = "patches/sdl3"
VENDOR_PREFIX = "sa2-recomp sdl3-patches "
NOT_SDL = "NOT FROM THE SDL PROJECT"
# GNU patch's environment: its messages in the C locale, which MOVED reads, and none of the
# caller's variables (LANGUAGE, POSIXLY_CORRECT, PATCH_GET, VERSION_CONTROL, ...).
PATCH_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
# GNU patch's report of a hunk it placed anywhere but where the patch says.
MOVED = re.compile(r"^Hunk #\d+ succeeded at \d+ (with fuzz \d+ )?\(offset -?\d+ lines?\)|with fuzz \d+", re.M)


class PatchError(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(hashes: list[str]) -> str:
    return VENDOR_PREFIX + sha256("".join(h + "\n" for h in hashes).encode())[:12]


def sdl3_pin(root: Path) -> dict:
    pin = json.loads((root / "tools" / "pins.json").read_text())["tools"].get("sdl3")
    if not isinstance(pin, dict):
        raise PatchError("tools/pins.json pins no sdl3")
    return pin


def header(text: str) -> str:
    """The patch's own text before its first diff."""
    at = re.search(r"^diff --git ", text, re.M)
    return text if at is None else text[:at.start()]


def checked_patches(root: Path, pin: dict | None = None) -> tuple[list[tuple[Path, str]], str]:
    """The pinned patches in order, each (file, sha256), and their identity; PatchError naming
    the first thing that does not hold."""
    pin = sdl3_pin(root) if pin is None else pin
    pinned = pin.get("patches")
    if not isinstance(pinned, list) or not pinned:
        raise PatchError("tools/pins.json pins no sdl3 patches (tools.sdl3.patches)")
    rows = []
    for row in pinned:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not isinstance(row.get("sha256"), str):
            raise PatchError(f"a pinned patch is not {{path, sha256}}: {row!r}")
        rows.append((row["path"], row["sha256"]))
    on_disk = sorted(f"{HOME}/{p.name}" for p in (root / HOME).glob("*.patch"))
    if [p for p, _ in rows] != on_disk:
        raise PatchError(f"the patches under {HOME}/ are {on_disk}, the pins name {[p for p, _ in rows]} "
                         "(the same files, in file-name order)")
    commit, tarball = pin.get("commit"), pin.get("sha256")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PatchError(f"tools.sdl3.commit is not a 40-digit commit: {commit!r}")
    out = []
    for rel, want in rows:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise PatchError(f"{rel} is not a regular file")
        data = path.read_bytes()
        if sha256(data) != want:
            raise PatchError(f"{rel} hashes to {sha256(data)}, not its pin {want}: it is not the pinned patch")
        text = header(data.decode("utf-8", "replace"))
        for words, what in ((NOT_SDL, "that it is not from the SDL project"), (commit, "the pinned commit"),
                            (str(tarball), "the pinned tarball's sha256")):
            if words not in text:
                raise PatchError(f"{rel}'s header does not name {what} ({words!r})")
        out.append((path, want))
    vendor = identity([h for _, h in out])
    if pin.get("vendor_info") != vendor:
        raise PatchError(f"tools.sdl3.vendor_info is {pin.get('vendor_info')!r}; the pinned patches' identity "
                         f"is {vendor!r}")
    return out, vendor


def patch_program(root: Path) -> str:
    """The pinned GNU patch (tools.patch): its path, its --version line holding the version."""
    pin = json.loads((root / "tools" / "pins.json").read_text())["tools"].get("patch")
    if not isinstance(pin, dict) or not isinstance(pin.get("path"), str) or not isinstance(pin.get("version"), str):
        raise PatchError("tools/pins.json pins no patch (tools.patch: path and version)")
    try:
        r = subprocess.run([pin["path"], "--version"], capture_output=True, text=True, env=PATCH_ENV)
    except OSError as error:
        raise PatchError(f"cannot run the pinned patch {pin['path']}: {error}") from None
    first = (r.stdout.splitlines() or [""])[0]
    # the pinned version whole, as check_pins.py reads a --version line ("2.8" is not "2.8.1")
    if r.returncode != 0 or not re.search(r"(?<![0-9.])" + re.escape(pin["version"]) + r"(?![0-9]|\.[0-9])", first):
        raise PatchError(f"{pin['path']} --version reports {first!r}, not the pinned {pin['version']!r}")
    return pin["path"]


def check_source(root: Path, tree: Path) -> str:
    pin = sdl3_pin(root)
    commit, version = pin.get("commit", ""), pin.get("version", "")
    try:
        stamp = (tree / ".git-hash").read_text().strip()
        revision = (tree / "REVISION.txt").read_text().strip()
    except OSError as error:
        raise PatchError(f"{tree} is not an extracted SDL tarball: {error}") from None
    if stamp != commit:
        raise PatchError(f"{tree}/.git-hash reads {stamp!r}, not the pinned commit {commit!r}")
    if revision != f"release-{version}-0-g{commit[:9]}":
        raise PatchError(f"{tree}/REVISION.txt reads {revision!r}, not release-{version}-0-g{commit[:9]}")
    return f"{tree.name} is commit {commit} ({revision})"


def named_files(text: str) -> list[str]:
    """The files a unified diff writes, from its +++ lines (b/ stripped, as -p1 does)."""
    names = []
    for line in text.splitlines():
        if line.startswith("+++ "):
            name = line[4:].split("\t")[0].strip()
            if name != "/dev/null":
                names.append(name.split("/", 1)[1] if "/" in name else name)
    return names


def file_hash(path: Path) -> str | None:
    return sha256(path.read_bytes()) if path.is_file() else None


def apply_one(tree: Path, patch: Path, program: str) -> int:
    """`patch` applied to `tree` by `program` (patch_program's), strictly: the number of files
    it changed; PatchError otherwise."""
    text = patch.read_text("utf-8", "replace")
    names = named_files(text)
    if not names:
        raise PatchError(f"{patch.name} names no file: nothing would be applied")
    before = {n: file_hash(tree / n) for n in names}
    base = [program, "-p1", "--fuzz=0", "--forward", "--batch", "--no-backup-if-mismatch", "-d", str(tree),
            "-i", str(patch)]
    # Child storage is fresh and belongs to this physical tree, independent of caller TMPDIR.
    with tempfile.TemporaryDirectory(prefix="sa2-gnu-patch-", dir=tree.resolve().parent) as scratch:
        env = dict(PATCH_ENV, TMPDIR=scratch)
        for argv in (base + ["--dry-run"], base):
            r = subprocess.run(argv, capture_output=True, text=True, env=env)
            said = (r.stdout + r.stderr).strip()
            # What the refusal leaves: a dry run writes nothing; the patches before this one stay
            # applied either way, so the tree is not one to build from.
            left = ("the dry run refused it, so it changed nothing" if "--dry-run" in argv
                    else "it failed after its dry run passed, so the tree may hold part of it")
            left += "; any patch before it stays applied, and nothing may be built from this tree"
            if r.returncode != 0:
                raise PatchError(f"{patch.name} does not apply cleanly to {tree} (GNU patch, --fuzz=0, exit "
                                 f"{r.returncode}): {said[-1500:]}. No fuzz is allowed: {left}")
            if MOVED.search(said):
                raise PatchError(f"{patch.name} applies to {tree} only at an offset or with fuzz: {said[-1500:]}. "
                                 f"No offset is allowed: {left}")
    unchanged = [n for n in names if file_hash(tree / n) == before[n]]
    if unchanged:
        raise PatchError(f"{patch.name} left {unchanged} unchanged: an apply that changes nothing is refused")
    left = sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.suffix in (".rej", ".orig"))
    if left:
        raise PatchError(f"{patch.name} left {left} in {tree}")
    return len(names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "source", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, required=True)
        if name != "check":
            p.add_argument("--tree", type=Path, required=True)
    ident = sub.add_parser("identity")
    ident.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "identity":
            print(identity([sha256(f.read_bytes()) for f in args.files]))
        elif args.cmd == "source":
            print(check_source(args.root.resolve(), args.tree))
        else:
            patches, vendor = checked_patches(args.root.resolve())
            if args.cmd == "check":
                for path, digest in patches:
                    print(f"{digest}  {path.relative_to(args.root.resolve())}")
            else:
                check_source(args.root.resolve(), args.tree)
                program = patch_program(args.root.resolve())
                for path, digest in patches:
                    n = apply_one(args.tree, path, program)
                    print(f"applied {path.name} ({digest[:16]}): {n} files changed")
                print(f"SDL3_PATCHES_APPLIED {len(patches)} {vendor}")
            print(f"VENDOR_INFO {vendor}")
    except (PatchError, OSError, ValueError) as error:
        print(f"sdl3_patches.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
