#!/usr/bin/env python3
"""Run the S2 fixture corpus and fail on any regression against a tracked baseline.

Design 7.3 is the rule this enforces: "A function or replay that has passed
never silently stops being run... a drop is a gate failure." Until the G2
interrogation nothing ran it. M3 reported a ratio and a `regressions` list
that the corpus runner itself wrote, and the runner compared against
`private/generated/fixture-results.json` -- a file `.gitignore` excludes. On a
fresh checkout that file is absent, so the set of fixtures known to have
passed was empty, so "regressions 0" was true because nothing could be
compared, forever. A baseline that cannot survive a clone is not a baseline.

So the baseline is tracked: `Documentation/receipts/fixture-baseline-v1.json`,
fixture ids and pass/fail only -- no rows, no captured values, nothing derived
from the shipped executable (the boundary rule, AGENTS.md 9).

What this gate does
-------------------

1. Runs the corpus driver, `tools/s2/corpus_run/run.py`, as a subprocess,
   over every fixture it has a plan for. The driver is another unit's file and
   is never imported: a gate that imports the thing it checks shares its
   failure modes. Its JSON output (`results[{fixture, passed}]`) is the
   observed set.
2. Reads `private/generated/fixture-results.json` for fixtures the driver does
   not drive -- today the two the wrap's own runner covers. Those are
   *attested*, not re-run here, and the gate says so on a `note:` line that
   `run_all_gates.py` relays, so the weaker coverage is visible in the
   summary rather than implied by silence.
3. Fails when a fixture the baseline records as passing does not pass now,
   when such a fixture is no longer produced by either source at all, or when
   a fixture passes that the baseline does not record as passing (a stale
   baseline is as misleading as a missing one; `--refresh` is the answer).

Every fixture the baseline names is compared. The whole corpus runs on every
gate pass because it costs under a second of driver time; the gate prints the
seconds it took, so the day that stops being true is visible in the output
rather than discovered by someone waiting.

`--refresh`
-----------

Rewrites the baseline from what ran, and prints every move. It refuses to
record a pass -> fail move unless `--accept-regression REASON` says why, so a
red gate cannot be turned green by re-running the tool that draws the line.
A fixture legitimately entering the passing set (a new fixture, a fixed
defect) needs no reason; a fixture leaving it needs one, in the commit.

Output ends with `marker_sha256=<hex>` over the compared id -> status map, so
`Documentation/fidelity-ledger.json` can pin it and `check_ledger_suites.py`
can tell "the corpus still passes" from "the corpus was not run".

    check_fixture_corpus.py                 run, compare, exit 1 on a drop
    check_fixture_corpus.py --refresh       rewrite the baseline from this run
    --accept-regression REASON              allow --refresh to record a drop
    --baseline PATH                         baseline location
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

BASELINE = "Documentation/receipts/fixture-baseline-v1.json"
DRIVER = "tools/s2/corpus_run/run.py"
RESULTS = "private/generated/fixture-results.json"
REGISTRY = "Documentation/receipts/fixture-registry.json"
SCHEMA = "sa2-recomp-fixture-baseline/v1"

CORPUS_RUN = "corpus_run"
ATTESTED = "recorded"


class Unrunnable(Exception):
    """The corpus could not be run; an unrunnable suite is missing, not passing."""


def load_json(path: Path) -> object | None:
    """The document, or None when absent or unreadable.

    The results file is written by another unit while this gate is being
    written, so it is read defensively: absent, truncated or the wrong shape
    is reported by name, never assumed to mean "nothing failed".
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def string_set(document: object, key: str) -> set[str] | None:
    if not isinstance(document, dict):
        return None
    value = document.get(key)
    if not isinstance(value, list):
        return None
    return {str(item) for item in value}


def registered_ids(root: Path) -> set[str] | None:
    document = load_json(root / REGISTRY)
    if not isinstance(document, dict) or not isinstance(document.get("fixtures"), list):
        return None
    return {str(f.get("id")) for f in document["fixtures"] if isinstance(f, dict)}


def run_corpus(root: Path) -> tuple[dict[str, bool], float, str]:
    """{fixture id: passed} from a fresh run of the corpus driver, and its seconds."""
    driver = root / DRIVER
    if not driver.is_file():
        raise Unrunnable(f"no corpus driver at {DRIVER}")
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "run.json"
        started = time.monotonic()
        try:
            finished = subprocess.run([sys.executable, str(driver), "--out", str(out)],
                                      cwd=root, capture_output=True, text=True, check=False)
        except OSError as error:
            raise Unrunnable(f"could not start {DRIVER}: {error}") from error
        elapsed = time.monotonic() - started
        if finished.returncode != 0:
            tail = (finished.stderr.strip() or finished.stdout.strip()).splitlines()
            raise Unrunnable(f"{DRIVER} exited {finished.returncode}: "
                             + (tail[-1] if tail else "(no output)"))
        document = load_json(out)
        if not isinstance(document, dict) or not isinstance(document.get("results"), list):
            raise Unrunnable(f"{DRIVER} wrote no results[] array")
        observed: dict[str, bool] = {}
        for result in document["results"]:
            if isinstance(result, dict) and "fixture" in result:
                observed[str(result["fixture"])] = bool(result.get("passed"))
        if not observed:
            raise Unrunnable(f"{DRIVER} drove no fixtures")
        tail = finished.stdout.strip().splitlines()
        return observed, elapsed, tail[-1] if tail else ""


def load_baseline(path: Path) -> dict[str, dict]:
    document = load_json(path)
    if document is None:
        raise Unrunnable(f"no baseline at {path}; write one with --refresh and commit it")
    if not isinstance(document, dict) or not isinstance(document.get("fixtures"), list):
        raise Unrunnable(f"{path} has no fixtures[] array")
    baseline: dict[str, dict] = {}
    for fixture in document["fixtures"]:
        if not isinstance(fixture, dict) or "id" not in fixture:
            raise Unrunnable(f"{path} has a fixtures[] entry with no id")
        status = fixture.get("status")
        if status not in ("pass", "fail"):
            raise Unrunnable(f"{path}: fixture {fixture['id']} has status {status!r}")
        baseline[str(fixture["id"])] = {"status": status,
                                        "runner": str(fixture.get("runner") or CORPUS_RUN)}
    return baseline


def write_baseline(path: Path, statuses: dict[str, str], runners: dict[str, str]) -> None:
    document = {
        "schema": SCHEMA,
        "id": path.stem,
        "stage": "S2",
        "question": ("Which registered fixtures have been observed passing, so that design 7.3's "
                     "regression rule has something tracked to compare against on any checkout?"),
        "note": ("Ids and pass/fail only: no rows, no captured values, nothing derived from the "
                 "shipped executable. `runner` says which runner's result this is -- corpus_run "
                 "for a fixture Documentation/scripts/check_fixture_corpus.py re-runs on every "
                 "gate pass, recorded for one only attested by "
                 "private/generated/fixture-results.json, which is gitignored and machine-local. "
                 "A fixture registered but never run appears in neither list and is the gap M3's "
                 "denominator reports."),
        "authority": "observed",
        "command": "Documentation/scripts/check_fixture_corpus.py --refresh",
        "written_by": "Documentation/scripts/check_fixture_corpus.py --refresh",
        "fixtures": [{"id": fixture, "status": statuses[fixture],
                      "runner": runners.get(fixture, CORPUS_RUN)}
                     for fixture in sorted(statuses)],
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def marker(statuses: dict[str, str]) -> str:
    canonical = json.dumps(statuses, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--baseline", type=Path, default=None,
                        help=f"baseline path (default: <root>/{BASELINE})")
    parser.add_argument("--refresh", action="store_true",
                        help="rewrite the baseline from this run")
    parser.add_argument("--accept-regression", default=None, metavar="REASON",
                        help="allow --refresh to record a pass -> fail move, with a reason")
    args = parser.parse_args()

    root = project_root(args.root)
    baseline_path = args.baseline if args.baseline is not None else root / BASELINE

    try:
        observed, seconds, summary = run_corpus(root)
    except Unrunnable as why:
        print(f"fixture corpus MISSING: {why}", file=sys.stderr)
        return 1
    print(f"corpus driver: {len(observed)} fixtures in {seconds:.1f}s  {summary}")

    recorded_passed = string_set(load_json(root / RESULTS), "passed")
    recorded_failed = string_set(load_json(root / RESULTS), "failed")
    if recorded_passed is None:
        print(f"note: {RESULTS} is absent or has no passed[]; only the driven fixtures "
              f"can be compared")
        recorded_passed, recorded_failed = set(), set()
    if recorded_failed is None:
        recorded_failed = set()

    # Everything that has a status now, and where the status came from.
    statuses: dict[str, str] = {}
    runners: dict[str, str] = {}
    for fixture, passed in observed.items():
        statuses[fixture] = "pass" if passed else "fail"
        runners[fixture] = CORPUS_RUN
    for fixture in sorted(recorded_passed | recorded_failed):
        if fixture in statuses:
            continue
        statuses[fixture] = "pass" if fixture in recorded_passed else "fail"
        runners[fixture] = ATTESTED

    registered = registered_ids(root)
    if registered is None:
        print(f"note: {REGISTRY} unreadable; fixture ids are not checked against the registry")
    else:
        unregistered = sorted(set(statuses) - registered)
        if unregistered:
            print(f"note: ran fixtures not in the registry: {', '.join(unregistered)}")

    if args.refresh:
        try:
            previous = load_baseline(baseline_path)
        except Unrunnable:
            previous = {}
        drops = sorted(f for f, was in previous.items()
                       if was["status"] == "pass" and statuses.get(f) != "pass")
        gains = sorted(f for f, status in statuses.items()
                       if status == "pass" and previous.get(f, {}).get("status") != "pass")
        if drops and not args.accept_regression:
            print(f"fixture corpus REFUSED: --refresh would record {len(drops)} pass -> fail "
                  f"move(s) ({', '.join(drops)}); pass --accept-regression REASON and put the "
                  f"reason in the commit", file=sys.stderr)
            return 1
        write_baseline(baseline_path, statuses, runners)
        for fixture in gains:
            print(f"  entered the passing set: {fixture}")
        for fixture in drops:
            print(f"  left the passing set: {fixture}  ({args.accept_regression})")
        try:
            shown = baseline_path.relative_to(root)
        except ValueError:                      # --baseline outside the repository
            shown = baseline_path
        print(f"fixture corpus baseline written: {len(statuses)} fixtures, "
              f"{sum(1 for s in statuses.values() if s == 'pass')} passing -> {shown}")
        print(f"marker_sha256={marker(statuses)}")
        return 0

    try:
        baseline = load_baseline(baseline_path)
    except Unrunnable as why:
        print(f"fixture corpus MISSING: {why}", file=sys.stderr)
        return 1

    failures: list[str] = []
    attested: list[str] = []
    for fixture, was in sorted(baseline.items()):
        if was["status"] != "pass":
            continue
        if fixture in observed:
            if not observed[fixture]:
                failures.append(f"REGRESSION {fixture}: baseline pass, the corpus run fails it")
            continue
        if fixture in recorded_passed:
            attested.append(fixture)
            continue
        if fixture in recorded_failed:
            failures.append(f"REGRESSION {fixture}: baseline pass, {RESULTS} records it failed")
        else:
            failures.append(f"NOT RUN {fixture}: baseline pass, no runner produced a result "
                            f"(design 7.3: a passing fixture never silently stops being run)")
    for fixture, status in sorted(statuses.items()):
        if status != "pass":
            continue
        if baseline.get(fixture, {}).get("status") != "pass":
            failures.append(f"BASELINE STALE {fixture}: passes now, the baseline does not record "
                            f"it passing; rerun with --refresh and commit the baseline")

    if attested:
        print(f"note: attested by {RESULTS}, not re-run by this gate: {', '.join(attested)}")
    for line in failures:
        print(f"  {line}")

    compared = {fixture: statuses.get(fixture, "absent") for fixture in baseline}
    passing = sum(1 for was in baseline.values() if was["status"] == "pass")
    if failures:
        sys.stdout.flush()
        print(f"fixture corpus FAIL: {len(failures)} of {len(baseline)} baseline fixtures",
              file=sys.stderr)
        return 1
    print(f"fixture corpus OK: {passing} of {len(baseline)} baseline fixtures passing, "
          f"0 regressions ({len(observed)} re-run, {len(attested)} attested)")
    print(f"marker_sha256={marker(compared)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
