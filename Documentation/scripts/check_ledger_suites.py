#!/usr/bin/env python3
"""Run every suite the fidelity ledger registers and compare its marker.

`chaos_control.py audit` checks that a suite's marker is well formed; it never
runs the suite. Until this gate existed nothing compared the marker a checker
prints with the one the ledger pins, so a pinned marker was a claim nobody
tested. This is the `ledger suites` gate of `run_all_gates.py`.

For every entry in `suites` of `Documentation/fidelity-ledger.json`:

  1. every `files[]` pin must equal the working-tree hash of that file (a
     stale checker pin means the suite that would be run is not the suite
     that was registered);
  2. the `command` runs from the repository root with `python3` replaced by
     this interpreter and `${NAME}` tokens taken from the environment; it must
     exit 0;
  3. for a `sha256` marker, the last output line containing `marker_sha256=`
     must carry exactly the pinned hex; for a `text` marker, some output line
     must equal the pinned text.

A suite whose command cannot start (checker file absent, interpreter absent,
`${NAME}` unset) is MISSING, and MISSING fails: an unrunnable suite is not a
passing one (AGENTS.md rule 8). The gate prints one line per suite, relays the
suite's own `note:` / `warning:` / `adapted...=` / `skipped` lines under it so
a degraded pass stays visible, and ends with

    ledger suites OK: N suites, markers match

Root is `git rev-parse --show-toplevel` from the current directory, or
`--root PATH`; `--ledger PATH` overrides the ledger location.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from gitlog import project_root

LEDGER = "Documentation/fidelity-ledger.json"
MARKER_LINE = re.compile(r"marker_sha256=([0-9a-fA-F]+)")
TOKEN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
# Lines a suite prints that describe a degraded or partial pass. The runner
# surfaces the same shapes, so they must survive this gate's summary.
RELAY = re.compile(r"^\s*(note:|warning:)|adapted\w*=|skipped")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Missing(Exception):
    """The suite command cannot start."""


def resolve_command(root: Path, command: list[str]) -> list[str]:
    if not command:
        raise Missing("empty command")
    argv: list[str] = []
    for index, word in enumerate(command):
        token = TOKEN.match(word)
        if token:
            value = os.environ.get(token.group(1))
            if value is None:
                raise Missing(f"${{{token.group(1)}}} is not set in the environment")
            word = value
        argv.append(word)
    if argv[0] == "python3":
        argv[0] = sys.executable
    elif shutil.which(argv[0]) is None and not Path(argv[0]).is_file():
        raise Missing(f"no such interpreter: {argv[0]}")
    if len(argv) > 1 and command[0] == "python3":
        script = root / argv[1]
        if not script.is_file():
            raise Missing(f"no such checker: {argv[1]}")
    return argv


def check_suite(root: Path, suite: dict) -> tuple[str, str, list[str]]:
    """Return (status, detail, relayed lines) for one suite."""
    identifier = str(suite.get("id", "<unnamed>"))
    for pin in suite.get("files", ()):
        rel = pin.get("path")
        pinned = pin.get("sha256")
        path = root / str(rel)
        if not path.is_file():
            return "FAIL", f"pinned file missing: {rel}", []
        actual = sha256(path)
        if actual != pinned:
            return "FAIL", (f"pinned file stale: {rel} working tree {actual} "
                            f"!= pinned {pinned}"), []

    try:
        argv = resolve_command(root, list(suite.get("command", ())))
    except Missing as why:
        return "MISSING", str(why), []
    try:
        finished = subprocess.run(argv, cwd=root, capture_output=True,
                                  text=True, check=False)
    except OSError as error:
        return "MISSING", f"could not start: {error}", []

    output = (finished.stdout + finished.stderr).splitlines()
    relayed = [line.rstrip() for line in output if RELAY.search(line)]
    if finished.returncode != 0:
        tail = [line for line in output if line.strip()][-3:]
        return "FAIL", (f"exit {finished.returncode}: "
                        + (" | ".join(tail) if tail else "(no output)")), relayed

    marker = suite.get("marker") or {}
    kind = marker.get("kind")
    pinned = str(marker.get("value", ""))
    if kind == "sha256":
        found = [MARKER_LINE.search(line) for line in output]
        found = [match for match in found if match]
        if not found:
            return "FAIL", "printed no marker_sha256= line", relayed
        printed = found[-1].group(1).lower()
        if printed != pinned.lower():
            return "FAIL", (f"marker mismatch: printed {printed} "
                            f"!= pinned {pinned}"), relayed
        return "OK", f"marker_sha256={printed}", relayed
    if kind == "text":
        if not any(line.strip() == pinned for line in output):
            return "FAIL", f"text marker not printed: {pinned!r}", relayed
        return "OK", f"marker text {pinned!r}", relayed
    return "FAIL", f"unknown marker kind {kind!r}", relayed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--ledger", type=Path, default=None,
                        help=f"ledger path (default: <root>/{LEDGER})")
    args = parser.parse_args()
    root = project_root(args.root)
    ledger_path = args.ledger if args.ledger is not None else root / LEDGER
    if not ledger_path.is_file():
        print(f"ledger suites FAIL: no ledger at {ledger_path}", file=sys.stderr)
        return 1
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"ledger suites FAIL: cannot read {ledger_path}: {error}",
              file=sys.stderr)
        return 1
    suites = ledger.get("suites") if isinstance(ledger, dict) else None
    if not isinstance(suites, list):
        print("ledger suites FAIL: ledger has no suites array", file=sys.stderr)
        return 1

    failures = 0
    for suite in suites:
        if not isinstance(suite, dict):
            print("FAIL <malformed> suite entry is not an object")
            failures += 1
            continue
        identifier = str(suite.get("id", "<unnamed>"))
        status, detail, relayed = check_suite(root, suite)
        print(f"{status:7s} {identifier}  {detail}")
        for line in relayed:
            print(f"        {line}")
        if status != "OK":
            failures += 1

    if failures:
        sys.stdout.flush()
        print(f"ledger suites FAIL: {failures} of {len(suites)} suites",
              file=sys.stderr)
        return 1
    print(f"ledger suites OK: {len(suites)} suites, markers match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
