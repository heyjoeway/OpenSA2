#!/usr/bin/env python3
"""Write a build's record: the binaries it produced beside the sources they were built from.

Subtitle: host/player/build.sh and host/d3d9gpu/build.sh each start with `--start` and end
with their records, so a run can be tied to the tree it pins (tools/s5/compare_hosts.py
refuses a build whose records do not match the tree: a receipt must not pin sources that did
not build the binaries that ran).

  build_record.py --start --out REC.json --tree CHECKOUT --sources PATH...
  build_record.py --out REC.json --tree CHECKOUT --bin-dir OUT --binaries NAME... \
                  --sources PATH... [--generated PATH...] [--deps D.d... --compiler CXX]

Every source is named by its path relative to CHECKOUT (the tree the build read; a source
outside it keeps its absolute path). `--start`, before the build compiles anything, removes
REC.json and hashes the sources into REC.json.start; the closing call refuses (exit 1, no
record) when there is no REC.json.start, or when a source moved while the build ran: a record
must name the bytes the compiles read, and a source edited mid-build may not be them.

Two kinds of record:

  sa2-build-record/v1 (no --deps), a group record: a directory among --sources stands for
      every file under it but __pycache__; --generated names directories, whose files are
      recorded by "<directory name>/<file name>". The closing call's sources must be the
      start's, file for file and hash for hash.
  sa2-build-record/v2 (--deps), the exact record of one binary: its sources are what the
      compiler itself says it read (the -MMD depfiles of every compile that made the binary)
      plus the --sources named (files only: the build script, the generators, the pins),
      nothing else. Each path a depfile names is classified:
        under OUT (--bin-dir)                        generated: hashed, by its path under OUT
        under CHECKOUT, not under CHECKOUT/private/  source: hashed, by its path in CHECKOUT
        anything else (the install's headers)        external: hashed, by its absolute path,
                                                     beside the compiler that read them
      (system headers are not in an -MMD depfile; a CHECKOUT under OUT is refused, its
      sources would read as generated; --compiler is recorded verbatim with its --version
      line and its include search list, `-x c++ -E -v` run here, in the build's
      environment: where the system headers it read were, which tools/s5/compare_hosts.py
      dxvk-unchanged searches for a commit's file ahead of one). A path read through a
      symbolic link in CHECKOUT outside its private/ (a depfile's, or a --sources file) is
      refused: what it leads to is not the checkout's file at that path, and a commit holds
      the link, not its target. --generated names directories (their
      files) or files under OUT. Every source must have been hashed at the start with the
      hash it has now: a compile that read a file the start did not hash refuses the record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

SCHEMA_GROUP = "sa2-build-record/v1"
SCHEMA_EXACT = "sa2-build-record/v2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expand(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"build_record.py: no source {p}")
    return out


def rel_to(tree: Path, p: Path) -> str:
    """`p` by its path in `tree` (both resolved), or its absolute path outside it."""
    p = p.resolve()
    return str(p.relative_to(tree)) if p.is_relative_to(tree) else str(p)


def hash_sources(tree: Path, sources: list[Path]) -> dict[str, str]:
    """Every source by its path in `tree` (absolute outside it), with its sha256 now."""
    tree = tree.resolve()
    return {rel_to(tree, p): sha(p) for p in expand(sources)}


def changed(started: dict[str, str], now: dict[str, str]) -> list[str]:
    """The sources whose hash at the build's start is not their hash at its end, by path."""
    return [f"{p}: {started.get(p, 'absent')} at the start, {now.get(p, 'absent')} at the end"
            for p in sorted(set(started) | set(now)) if started.get(p) != now.get(p)]


def record(tree: Path, bin_dir: Path, binaries: list[str], sources: list[Path], generated: list[Path],
           started: dict[str, str] | None = None) -> dict:
    """The group record (v1); with `started` (the sources' hashes as the build began),
    refused by SystemExit when any source moved while the build ran."""
    srcs = hash_sources(tree, sources)
    if started is not None and (moved := changed(started, srcs)):
        raise SystemExit("build_record.py: refused, the sources changed while the build ran (rebuild):\n  "
                         + "\n  ".join(moved))
    return {"schema": SCHEMA_GROUP,
            "tree": str(tree.resolve()),
            "binaries": {n: sha(bin_dir / n) for n in binaries},
            "sources": srcs,
            "generated": {f"{d.name}/{f.name}": sha(f) for d in generated for f in sorted(d.iterdir()) if f.is_file()},
            "scope": "the binaries this build produced and what they were built from; no run is implied"}


def depfile_paths(text: str) -> list[str]:
    """The prerequisites of every rule in a Makefile-syntax depfile (g++ -MMD): continuation
    lines joined, each rule's target dropped, `\\ ` an escaped space, `$$` a dollar."""
    joined = text.replace("\\\r\n", " ").replace("\\\n", " ")
    out: list[str] = []
    for line in joined.splitlines():
        words, cur, i = [], "", 0
        while i < len(line):
            c = line[i]
            if c == "\\" and i + 1 < len(line) and line[i + 1] in " #":
                cur += line[i + 1]
                i += 2
                continue
            if c == "$" and line[i + 1:i + 2] == "$":
                cur += "$"
                i += 2
                continue
            if c in " \t":
                if cur:
                    words.append(cur)
                cur = ""
            else:
                cur += c
            i += 1
        if cur:
            words.append(cur)
        # the rule's target ends with the first word ending in ':'
        colon = next((k for k, w in enumerate(words) if w.endswith(":")), None)
        if colon is None:
            if words:
                raise SystemExit(f"build_record.py: a depfile line with no rule: {line[:200]!r}")
            continue
        out += words[colon + 1:]
    return out


def tree_links(tree: Path, p: Path) -> list[str]:
    """The symbolic links in the checkout `tree` outside its private/ that the path `p`
    passes through, each by its path in the checkout, found as the system follows the path:
    component by component, `..` from where the path has led so far. A file read through one
    is not the checkout's file at that path, and a commit holds the link, not what it leads
    to; a link above the checkout, or in its private/ (a clone's link to the repository's),
    is not one of them."""
    tree = tree.resolve()
    cur = Path("/") if p.is_absolute() else Path.cwd().resolve()
    out: list[str] = []
    for part in p.parts[1:] if p.is_absolute() else p.parts:
        if part == ".":
            continue
        if part == "..":
            cur = cur.parent
            continue
        nxt = cur / part
        if nxt.is_symlink():
            if nxt.is_relative_to(tree) and not nxt.is_relative_to(tree / "private"):
                out.append(str(nxt.relative_to(tree)))
            nxt = nxt.resolve()
        cur = nxt
    return out


def refuse_links(tree: Path, p: Path, what: str) -> None:
    """Refused (SystemExit) when `p` passes through a link of tree_links."""
    links = tree_links(tree, p)
    if links:
        raise SystemExit(f"build_record.py: refused, {what} {p} through the checkout's symbolic link {links[0]}: "
                         f"what it leads to is not the checkout's file at that path, and a commit holds the link, "
                         f"not its target")


def classify(tree: Path, bin_dir: Path, paths: list[Path]) -> dict[str, dict[str, str]]:
    """Each path (resolved) as a source, a generated input or an external one, hashed;
    refused when one passes through a symbolic link in the checkout (refuse_links)."""
    tree, bin_dir = tree.resolve(), bin_dir.resolve()
    out: dict[str, dict[str, str]] = {"sources": {}, "generated": {}, "external": {}}
    for p in paths:
        refuse_links(tree, p, "the compile read")
        r = p.resolve()
        if not r.is_file():
            raise SystemExit(f"build_record.py: the compile read {p}, which is not there now")
        if r.is_relative_to(bin_dir):
            out["generated"][str(r.relative_to(bin_dir))] = sha(r)
        elif r.is_relative_to(tree) and not r.is_relative_to(tree / "private"):
            out["sources"][str(r.relative_to(tree))] = sha(r)
        else:
            out["external"][str(r)] = sha(r)
    return out


SEARCH_STARTS = ('#include "..." search starts here:', "#include <...> search starts here:")
SEARCH_ENDS = "End of search list."


def search_list(cxx: str) -> list[str] | None:
    """The directories the compiler searches after every -I, as it says itself
    (`-x c++ -E -v`), in this process's environment (CPATH and CPLUS_INCLUDE_PATH, when set,
    among them): where the system headers are, none of which an -MMD depfile names. None
    when it cannot be run or gives no list."""
    try:
        p = subprocess.run([*shlex.split(cxx), "-x", "c++", "-E", "-v", "-"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    lines = p.stderr.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() in SEARCH_STARTS]
    ends = [i for i, line in enumerate(lines) if line.strip() == SEARCH_ENDS]
    if p.returncode != 0 or not starts or not ends or ends[-1] < starts[0]:
        return None
    dirs = [line.strip().removesuffix(" (framework directory)") for line in lines[starts[0] + 1:ends[-1]]
            if line.startswith(" ")]
    return dirs or None


def compiler_line(cxx: str | None) -> dict | None:
    """The compiler as the build named it (the command verbatim, flags it carries included),
    its own first line of `--version` and its include search list (search_list, in the
    build's environment), or None when none is named; refused when it gives no list."""
    if not cxx:
        return None
    p = subprocess.run([*shlex.split(cxx), "--version"], capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout:
        raise SystemExit(f"build_record.py: {cxx} --version failed")
    search = search_list(cxx)
    if search is None:
        raise SystemExit(f"build_record.py: {cxx} gives no include search list (-x c++ -E -v)")
    return {"command": cxx, "version": p.stdout.splitlines()[0], "search": search}


def exact_record(tree: Path, bin_dir: Path, binaries: list[str], sources: list[Path], generated: list[Path],
                 deps: list[Path], started: dict[str, str], compiler: str | None = None) -> dict:
    """The exact record (v2) of the binaries the depfiles' compiles made: refused by
    SystemExit when a source it names was not hashed at the start, or has moved since."""
    tree_r, bin_r = tree.resolve(), bin_dir.resolve()
    if not deps:
        raise SystemExit("build_record.py: an exact record needs the compiles' depfiles (--deps)")
    if tree_r.is_relative_to(bin_r):
        # a path under both reads as generated first: the checkout's sources would leave the record
        raise SystemExit(f"build_record.py: refused, the checkout {tree_r} is under the build's {bin_r}, where its "
                         f"sources would be recorded as generated: build into a directory outside the checkout's")
    read: list[Path] = []
    for d in deps:
        # a depfile's paths are as the compiler was given them, from the build's directory
        read += [Path(p) for p in depfile_paths(Path(d).read_text())]
    got = classify(tree_r, bin_r, read)
    for p in sources:
        if not p.is_file():
            raise SystemExit(f"build_record.py: an exact record names files, not {p}")
        refuse_links(tree_r, p, "the build names")
        got["sources"][rel_to(tree_r, p)] = sha(p)
    for g in generated:
        for f in (sorted(x for x in g.iterdir() if x.is_file()) if g.is_dir() else [g]):
            if not f.resolve().is_relative_to(bin_r):
                raise SystemExit(f"build_record.py: generated {f} is not under {bin_dir}")
            got["generated"][str(f.resolve().relative_to(bin_r))] = sha(f)
    bad = []
    for path, h in sorted(got["sources"].items()):
        if path not in started:
            bad.append(f"the compile read {path}, which the build did not hash at its start")
        elif started[path] != h:
            bad.append(f"{path}: {started[path]} at the start, {h} at the end")
    if bad:
        raise SystemExit("build_record.py: refused, the sources changed while the build ran (rebuild), or the "
                         "build read what it did not hash first:\n  " + "\n  ".join(bad))
    return {"schema": SCHEMA_EXACT,
            "tree": str(tree_r),
            "binaries": {n: sha(bin_dir / n) for n in binaries},
            "sources": dict(sorted(got["sources"].items())),
            "generated": dict(sorted(got["generated"].items())),
            "external": dict(sorted(got["external"].items())),
            "compiler": compiler,
            "depfiles": {str(Path(d).resolve().relative_to(bin_r)): sha(Path(d)) for d in sorted(deps)},
            "scope": "exactly what the compiles of these binaries read (their depfiles), the build script, its "
                     "generators and pins; the compiler (with its include search list) and the external headers "
                     "are recorded, not held to the tree; no run is implied"}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", action="store_true", help="hash the sources as the build starts")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--tree", type=Path, required=True)
    p.add_argument("--bin-dir", type=Path)
    p.add_argument("--binaries", nargs="+")
    p.add_argument("--sources", nargs="+", type=Path, required=True)
    p.add_argument("--generated", nargs="*", type=Path, default=[])
    p.add_argument("--deps", nargs="+", type=Path, help="the -MMD depfiles of the binaries' compiles (v2)")
    p.add_argument("--compiler", help="the compiler that read them (recorded verbatim, with its --version line)")
    a = p.parse_args(argv)
    start = a.out.with_name(a.out.name + ".start")
    if a.start:
        a.out.unlink(missing_ok=True)
        start.write_text(json.dumps(hash_sources(a.tree, a.sources), indent=1) + "\n")
        print(f"{start.name}: {len(json.loads(start.read_text()))} sources at the build's start")
        return
    if not a.bin_dir or not a.binaries:
        p.error("the record needs --bin-dir and --binaries")
    try:
        started = json.loads(start.read_text())
    except (OSError, ValueError):
        raise SystemExit(f"build_record.py: refused, no {start} (the build script runs --start before it compiles)")
    start.unlink()
    if a.deps:
        rec = exact_record(a.tree, a.bin_dir, a.binaries, a.sources, a.generated, a.deps, started,
                           compiler_line(a.compiler))
    else:
        rec = record(a.tree, a.bin_dir, a.binaries, a.sources, a.generated, started)
    a.out.write_text(json.dumps(rec, indent=1) + "\n")
    print(f"{a.out.name}: {len(rec['binaries'])} binaries, {len(rec['sources'])} sources"
          + (f", {len(rec['generated'])} generated, {len(rec['external'])} external" if a.deps else "")
          + ", unchanged since the start")


if __name__ == "__main__":
    main()
