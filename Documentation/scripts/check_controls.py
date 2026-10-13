#!/usr/bin/env python3
"""Run the S2 negative controls and fail when a control stops holding.

The hole this closes
--------------------

`tools/s2/corpus_run/controls.py` is the only thing in S2 that answers the
question a pass cannot answer about itself: **did this fixture compare
anything?** A plan that feeds an input the guest never reads, or peeks a word
the guest never writes, passes bit for bit while proving nothing. The controls
test for both -- a `break` control perturbs a word an output provably depends
on and requires the run to *fail*; an `inert` control perturbs a word the
window provably never reads and requires the run to still pass.

Until this gate existed that tool was run by hand and its result was quoted
into a receipt. That is the identical hole the G2 fold closed one level down:
`check_fixture_corpus.py` exists because M3's regression rule was enforced by
a runner nobody was required to run. Closing it for the fixtures and leaving
it open for the controls means the corpus is gated and the proof that the
corpus compares anything is not. So the controls run in `run_all_gates.py`,
under a tracked marker, like everything else.

What this gate does
-------------------

1. Runs `tools/s2/corpus_run/controls.py` as a subprocess, writing its JSON
   to a temporary file. The harness is another unit's file and is never
   imported: a gate that imports the thing it checks shares its failure
   modes, and this one has to survive that harness being edited under it.
2. Reads the per-control report and builds the **hold map**: fixture ->
   control name -> `holds` / `FAILS` / `probe`. `probe` controls assert
   nothing by construction (they report which columns came back poisoned),
   so they are recorded and not judged.
3. Fails when any asserting control does not hold, and reports each one with
   the instruction its claim rests on, so a reader can check the claim
   against the image rather than against this file.
4. Prints `marker_sha256=` over the hold map. `Documentation/fidelity-ledger.json`
   pins it and `check_ledger_suites.py` compares, so "the controls still
   hold" is distinguishable from "the controls were not run" -- and from
   "the controls are a smaller set than they were", which a pass/fail line
   alone cannot say.

A harness that cannot start (no driver built, the tool absent, unreadable
output) is MISSING, and MISSING fails: an unrunnable suite is not a passing
one (AGENTS.md rule 8).

`--refresh`
-----------

Re-runs the controls and writes, into the ledger, the new marker, this
checker's own file pin, and the source pins of the claims this suite verifies
-- which includes the harness itself, the file most likely to have moved.
AGENTS.md rule 8 lets a pin move only after the suite that owns it has been
re-run in the same commit; this command is that re-run, which is why
`refresh_receipt_pins.py` refuses to touch ledger pins and this tool may. It refuses to record a holds -> FAILS move, or a
control leaving the map, unless `--accept-regression REASON` says why, so a
red gate cannot be turned green by re-running the tool that draws the line. A
control *entering* the map needs no reason.

    check_controls.py                       run, compare, exit 1 on a break
    check_controls.py --refresh             re-run and move the ledger marker
    --accept-regression REASON              allow --refresh to record a drop
    --root PATH                             repository root (default: git toplevel)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gitlog import project_root

LEDGER = "Documentation/fidelity-ledger.json"
CONTROLS = "tools/s2/corpus_run/controls.py"
SUITE_ID = "s2-negative-controls-hold"
SELF = "Documentation/scripts/check_controls.py"

HOLDS = "holds"
FAILS = "FAILS"
PROBE = "probe"


class Unrunnable(Exception):
    """The controls could not be run; an unrunnable suite is missing, not passing."""


def run_controls(root: Path) -> tuple[dict, float]:
    """(the harness's report, seconds). Its exit status is not the verdict here.

    The harness exits non-zero when a control breaks, which is a FAIL this
    gate reports itself from the report; it is Unrunnable only when there is
    no report to read.
    """
    tool = root / CONTROLS
    if not tool.is_file():
        raise Unrunnable(f"no controls harness at {CONTROLS}")
    with tempfile.TemporaryDirectory() as workspace:
        out = Path(workspace) / "controls.json"
        started = time.monotonic()
        try:
            finished = subprocess.run(
                [sys.executable, str(tool), "--out", str(out)],
                cwd=root, capture_output=True, text=True, check=False)
        except OSError as error:
            raise Unrunnable(f"could not start {CONTROLS}: {error}") from error
        seconds = time.monotonic() - started
        if not out.is_file():
            tail = [l for l in (finished.stderr + finished.stdout).splitlines() if l.strip()]
            raise Unrunnable(f"{CONTROLS} wrote no report (exit {finished.returncode}): "
                             + (tail[-1] if tail else "no output"))
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except ValueError as error:
            raise Unrunnable(f"{CONTROLS} wrote unreadable JSON: {error}") from error
    if not isinstance(report, dict):
        raise Unrunnable(f"{CONTROLS} report is not an object of fixture -> controls")
    return report, seconds


def hold_map(report: dict) -> tuple[dict[str, dict[str, str]], list[str]]:
    """(fixture -> control -> verdict, the broken controls' report lines)."""
    held: dict[str, dict[str, str]] = {}
    broken: list[str] = []
    for fixture in sorted(report):
        entries = report[fixture]
        if not isinstance(entries, list):
            raise Unrunnable(f"{CONTROLS} report for {fixture} is not an array")
        for entry in entries:
            if not isinstance(entry, dict) or "control" not in entry:
                raise Unrunnable(f"{CONTROLS} report for {fixture} has an entry with no control")
            name = str(entry["control"])
            kind = str(entry.get("kind", "?"))
            if kind == PROBE:
                held.setdefault(fixture, {})[name] = PROBE
                continue
            ok = bool(entry.get("control_holds"))
            held.setdefault(fixture, {})[name] = HOLDS if ok else FAILS
            if not ok:
                broken.append(
                    f"CONTROL BROKEN {fixture} [{kind}] {name}: "
                    f"{entry.get('rows_matching_native')}/{entry.get('rows')} rows match "
                    f"native; basis {entry.get('basis')!r}")
    return held, broken


def marker(held: dict[str, dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps(held, sort_keys=True).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(held: dict[str, dict[str, str]]) -> tuple[int, int, int]:
    """(asserting controls holding, asserting controls, probes)."""
    verdicts = [v for controls in held.values() for v in controls.values()]
    asserting = [v for v in verdicts if v != PROBE]
    return sum(1 for v in asserting if v == HOLDS), len(asserting), len(verdicts) - len(asserting)


def load_ledger(root: Path) -> tuple[dict, Path]:
    path = root / LEDGER
    if not path.is_file():
        raise Unrunnable(f"no ledger at {LEDGER}")
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except ValueError as error:
        raise Unrunnable(f"cannot read {LEDGER}: {error}") from error


def find_suite(ledger: dict) -> dict:
    for suite in ledger.get("suites") or ():
        if isinstance(suite, dict) and suite.get("id") == SUITE_ID:
            return suite
    raise Unrunnable(f"{LEDGER} registers no suite {SUITE_ID!r}")


def pinned_map(root: Path) -> dict[str, dict[str, str]] | None:
    """The hold map the pinned marker was taken over, when the ledger carries one.

    The ledger pins the marker, not the map, so a drop is detected as a marker
    mismatch by `check_ledger_suites.py`. What this reads is the optional
    `hold_map` the refresh writes beside it, which is what lets `--refresh`
    tell a control that *left* the map from one that changed verdict.
    """
    try:
        ledger, _ = load_ledger(root)
        suite = find_suite(ledger)
    except Unrunnable:
        return None
    previous = suite.get("hold_map")
    return previous if isinstance(previous, dict) else None


def refresh(root: Path, held: dict[str, dict[str, str]], broken: list[str],
            reason: str | None) -> int:
    ledger, path = load_ledger(root)
    suite = find_suite(ledger)
    if broken and not reason:
        # A baseline is a record of a holding set. Pinning a map that contains
        # a broken control would make the ledger say "this is how it is
        # supposed to be" about a fixture that compared nothing.
        print(f"controls REFUSED: {len(broken)} asserting control(s) do not hold, so there is "
              f"no holding set to baseline; fix the harness or pass --accept-regression REASON",
              file=sys.stderr)
        for line in broken:
            print(f"  {line}", file=sys.stderr)
        return 1
    previous = pinned_map(root) or {}
    drops: list[str] = []
    gains: list[str] = []
    for fixture in sorted(set(previous) | set(held)):
        was = previous.get(fixture, {})
        now = held.get(fixture, {})
        for control in sorted(set(was) | set(now)):
            before, after = was.get(control), now.get(control)
            if before == after:
                continue
            if before == HOLDS and after != HOLDS:
                drops.append(f"{fixture} / {control}: {before} -> {after or 'absent'}")
            elif after is not None and before is None:
                gains.append(f"{fixture} / {control}: new ({after})")
            elif before is not None:
                gains.append(f"{fixture} / {control}: {before} -> {after or 'absent'}")
    if drops and not reason:
        print(f"controls REFUSED: --refresh would record {len(drops)} holds -> broken move(s); "
              f"pass --accept-regression REASON and put the reason in the commit",
              file=sys.stderr)
        for line in drops:
            print(f"  {line}", file=sys.stderr)
        return 1
    suite["marker"] = {"kind": "sha256", "value": marker(held)}
    suite["hold_map"] = held
    # The suite's checker pin and the pins of the claims this suite verifies.
    # The harness itself is one of those claim sources, and it is the file most
    # likely to have moved, so one command closes the whole set rather than
    # leaving `chaos_control.py audit` red for a suite that has just been run.
    owned = set(suite.get("claims") or ())
    pins = list(suite.get("files") or ())
    for claim in ledger.get("claims") or ():
        if isinstance(claim, dict) and claim.get("id") in owned:
            pins.extend(claim.get("sources") or ())
    for pin in pins:
        if isinstance(pin, dict) and isinstance(pin.get("path"), str):
            target = root / pin["path"]
            if target.is_file():
                pin["sha256"] = sha256_file(target)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for line in gains:
        print(f"  entered the map: {line}")
    for line in drops:
        print(f"  left the holding set: {line}  ({reason})")
    holding, asserting, probes = counts(held)
    print(f"controls baseline written: {holding} of {asserting} asserting controls hold, "
          f"{probes} probe(s) -> {LEDGER} suite {SUITE_ID}")
    print(f"marker_sha256={marker(held)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--refresh", action="store_true",
                        help="re-run and move the ledger marker for this suite")
    parser.add_argument("--accept-regression", default=None, metavar="REASON",
                        help="allow --refresh to record a holds -> broken move, with a reason")
    args = parser.parse_args()
    root = project_root(args.root)

    try:
        report, seconds = run_controls(root)
        held, broken = hold_map(report)
    except Unrunnable as why:
        print(f"controls MISSING: {why}", file=sys.stderr)
        return 1

    holding, asserting, probes = counts(held)
    print(f"controls harness: {len(held)} fixture(s), {asserting} asserting control(s), "
          f"{probes} probe(s) in {seconds:.1f}s")

    if args.refresh:
        try:
            return refresh(root, held, broken, args.accept_regression)
        except Unrunnable as why:
            print(f"controls MISSING: {why}", file=sys.stderr)
            return 1

    for line in broken:
        print(f"  {line}")
    if broken:
        sys.stdout.flush()
        print(f"controls FAIL: {len(broken)} of {asserting} asserting controls do not hold; "
              f"a fixture whose break control still passes compared nothing there",
              file=sys.stderr)
        return 1
    if asserting == 0:
        print("controls FAIL: the harness asserted nothing, which is the vacuity "
              "this gate exists to detect", file=sys.stderr)
        return 1
    print(f"controls OK: {holding} of {asserting} asserting controls hold across "
          f"{len(held)} fixtures, {probes} probe(s) reported")
    print(f"marker_sha256={marker(held)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
