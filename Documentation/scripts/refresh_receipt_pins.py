#!/usr/bin/env python3
"""Emerald Cascade: move stale receipt pins to the files they already name.

A slice that edits a checker, a kernel or a journal leaves every receipt that
pins that file by hash reporting stale in `chaos_control.py audit`. Refreshing
them was a hand-written script per slice, and one such script once matched a
pin by name and moved the shipped executable's hash onto a script's. This
tool moves a pin only when all three of these hold:

  * the audit reported that exact pin as stale, naming the file, the old
    hash and the new hash;
  * the receipt node still carries the old hash the audit reported; and
  * the file on disk hashes to the new value the audit reported.

Then it writes the new hash and the file's current size into that node and
nothing else. Nodes carrying `historical` or `valid_at` are never touched:
they record what was true when written, and the audit does not ask for them.
A receipt whose formatting cannot be reproduced byte for byte from its parsed
content is left alone and reported, so no receipt is silently reformatted.

Moving a receipt's pin changes the receipt, so the receipts whose `extends`
pin it go stale in turn; the tool repeats audit-and-move until an audit
reports nothing it can move.

Ledger pins are never moved here. A claim source pin, a suite file pin and a
suite marker in `Documentation/fidelity-ledger.json` belong to the suite that
verifies the claim, and AGENTS.md rule 8 lets them move only after that suite
has been re-run; this tool runs no suite, so an earlier version of it that
rewrote claim pins was exactly the "refresh a pin to silence the audit" shape
the rule forbids. For every stale claim pin the audit reports, the tool prints
the owning suite's id and command and the instruction to re-run it, then
replace the exact old hash by hand (the `ledger suites` gate compares the
marker the suite prints with the pinned one).

    python3 Documentation/scripts/refresh_receipt_pins.py          # dry run
    python3 Documentation/scripts/refresh_receipt_pins.py --apply  # move them, re-audit

Inputs: the audit's output. Outputs: the receipts, edited in place under
--apply, and a per-pin report. `--root PATH` names the repository to work in
(default: the one containing this script); the audit is always the
`chaos_control.py` beside this file, run with `--repository` at that root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


STALE = re.compile(
    r"^CHAOS_CONTROL_ERROR receipt (?P<receipt>\S+) pin (?P<pin>\S+) stale "
    r"(?P<kind>sha256|size) for (?P<file>\S+): (?P<new>\S+) != (?P<old>\S+)$")
STALE_CLAIM = re.compile(
    r"^CHAOS_CONTROL_ERROR claim (?P<claim>\S+) stale sha256 for "
    r"(?P<file>\S+): (?P<new>\S+) != (?P<old>\S+)$")
LEDGER = "Documentation/fidelity-ledger.json"
MAX_ROUNDS = 96


CHAOS_CONTROL = Path(__file__).resolve().parent / "chaos_control.py"


def audit(project: Path) -> list[str]:
    completed = subprocess.run(
        [sys.executable, str(CHAOS_CONTROL), "--repository", str(project),
         "audit"], cwd=project, text=True, capture_output=True)
    return (completed.stdout + completed.stderr).splitlines()


def owning_suites(project: Path, claim: str) -> list[tuple[str, list[str]]]:
    """(id, command) of every ledger suite that lists `claim`."""
    try:
        ledger = json.loads((project / LEDGER).read_text())
    except (OSError, ValueError):
        return []
    return [(suite.get("id", "?"), list(suite.get("command", [])))
            for suite in ledger.get("suites", [])
            if claim in suite.get("claims", [])]


def resolve(root, dotted: str):
    """Walk the audit's dotted trail: dict keys (which may themselves
    contain dots, as file names do) and list indices."""
    parts = dotted.split(".")
    node = root
    index = 0
    while index < len(parts):
        found = False
        for width in range(len(parts) - index, 0, -1):
            key = ".".join(parts[index:index + width])
            if isinstance(node, dict) and key in node:
                node = node[key]
                index += width
                found = True
                break
            if (width == 1 and isinstance(node, list) and key.isdigit()
                    and int(key) < len(node)):
                node = node[int(key)]
                index += 1
                found = True
                break
        if not found:
            return None
    return node


def detect_dump(text: str):
    """Return kwargs that reproduce this receipt's formatting, or None."""
    payload = json.loads(text)
    for indent in (1, 2, 4):
        for ensure_ascii in (False, True):
            for sort_keys in (False, True):
                candidate = json.dumps(payload, indent=indent,
                                       ensure_ascii=ensure_ascii,
                                       sort_keys=sort_keys) + "\n"
                if candidate == text:
                    return payload, {"indent": indent,
                                     "ensure_ascii": ensure_ascii,
                                     "sort_keys": sort_keys}
    return payload, None


def one_round(project: Path, apply: bool) -> tuple[int, int, list[str]]:
    """Audit once and move what the audit names. Returns moved, skipped and
    the audit lines the tool does not handle."""
    lines = audit(project)
    stale: dict[tuple[str, str], dict] = {}
    claims: dict[tuple[str, str], tuple[str, str]] = {}
    other = []
    for line in lines:
        if not line.startswith("CHAOS_CONTROL_ERROR"):
            continue
        match = STALE.match(line)
        if match:
            entry = stale.setdefault(
                (match["receipt"], match["pin"]), {"file": match["file"]})
            entry[match["kind"]] = (match["old"], match["new"])
            continue
        match = STALE_CLAIM.match(line)
        if match:
            claims[(match["claim"], match["file"])] = (match["old"], match["new"])
            continue
        other.append(line)

    verb = "MOVE" if apply else "WOULD MOVE"
    moved = skipped = 0
    receipts: dict[str, tuple[dict, dict | None]] = {}

    def load(relative: str):
        if relative not in receipts:
            payload, dump = detect_dump((project / relative).read_text())
            if dump is None:
                print(f"SKIP {relative}: formatting not reproducible")
            receipts[relative] = (payload, dump)
        return receipts[relative]

    def current_hash(relative: str, reported_new: str, label: str):
        target = project / relative
        if not target.is_file():
            print(f"SKIP {label}: {relative} missing")
            return None
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != reported_new:
            print(f"SKIP {label}: {relative} hashes to {actual[:12]}, "
                  f"audit reported {reported_new[:12]}")
            return None
        return actual

    for (receipt, pin), entry in sorted(stale.items()):
        payload, dump = load(receipt)
        if dump is None:
            skipped += 1
            continue
        node = resolve(payload, pin)
        label = f"{receipt} {pin}"
        if not isinstance(node, dict) or "sha256" not in node:
            print(f"SKIP {label}: node not found or has no sha256")
            skipped += 1
            continue
        if "historical" in node or "valid_at" in node:
            print(f"SKIP {label}: historical node")
            skipped += 1
            continue
        if "sha256" not in entry:
            print(f"SKIP {label}: size moved without the hash")
            skipped += 1
            continue
        old, new = entry["sha256"]
        if node["sha256"] != old:
            print(f"SKIP {label}: receipt holds {node['sha256'][:12]}, "
                  f"audit reported {old[:12]}")
            skipped += 1
            continue
        if current_hash(entry["file"], new, label) is None:
            skipped += 1
            continue
        print(f"{verb} {label}: {entry['file']} {old[:12]} -> {new[:12]}")
        node["sha256"] = new
        if "size" in node:
            node["size"] = (project / entry["file"]).stat().st_size
        moved += 1

    for (identifier, relative), (old, new) in sorted(claims.items()):
        owners = owning_suites(project, identifier)
        if owners:
            names = ", ".join(name for name, _ in owners)
            commands = "; ".join(" ".join(command) for _, command in owners)
        else:
            names = "(no suite lists this claim; the audit will say so)"
            commands = "-"
        print(f"STALE CLAIM PIN claim {identifier} source {relative}: "
              f"ledger holds {old}, file hashes to {new}. Not moved: re-run "
              f"the owning suite {names} ({commands}) from the repository "
              f"root, then replace the exact old hash in {LEDGER} by hand "
              f"(AGENTS.md rule 8).")
        skipped += 1

    if apply:
        for relative, (payload, dump) in receipts.items():
            if dump is not None:
                (project / relative).write_text(
                    json.dumps(payload, **dump) + "\n")
    return moved, skipped, other


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--root", type=Path, default=None,
                        help="repository to work in (default: this script's)")
    args = parser.parse_args()
    project = (args.root.resolve() if args.root is not None
               else Path(__file__).resolve().parents[2])
    total_moved = total_skipped = 0
    rounds = 0
    other: list[str] = []
    while True:
        rounds += 1
        moved, skipped, other = one_round(project, args.apply)
        total_moved += moved
        total_skipped += skipped
        if not args.apply or moved == 0 or rounds >= MAX_ROUNDS:
            break
    for line in other:
        print(line)
    if args.apply:
        remaining = [line for line in audit(project)
                     if line.startswith("CHAOS_CONTROL_ERROR")]
        print(f"EMERALD_CASCADE_SUMMARY rounds={rounds} moved={total_moved} "
              f"skipped={total_skipped} audit_errors_after={len(remaining)}")
        if remaining:
            raise SystemExit("EMERALD_CASCADE_AUDIT_STILL_FAILS")
    else:
        print(f"EMERALD_CASCADE_SUMMARY would_move={total_moved} "
              f"skipped={total_skipped} other_errors={len(other)} "
              "(dry run, first round only; --apply moves and repeats)")


if __name__ == "__main__":
    main()
