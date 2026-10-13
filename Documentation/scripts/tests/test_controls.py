#!/usr/bin/env python3
"""Behaviour tests for check_controls.py.

The gate runs `tools/s2/corpus_run/controls.py` as a subprocess, so every test
builds a throwaway repository with a stand-in harness at that path: a script
that writes the report the real one writes. That is exactly the coupling the
gate has in production -- it never imports the harness -- so a stand-in tests
the gate and not the harness, and these tests do not need a built driver.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
GATE = SCRIPTS / "check_controls.py"

HARNESS = '''#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--out", type=Path, default=None)
args = ap.parse_args()
report = json.loads(Path("report.json").read_text())
if report == "no report":
    sys.exit(3)
args.out.write_text(json.dumps(report))
print("stand-in controls harness")
'''

ONE_FIXTURE = {
    "ground-tail-v4": [
        {"control": "work_w13-drives-world_w6", "kind": "break", "basis": "[ebx+54h]",
         "rows": 29, "rows_matching_native": 0, "control_holds": True},
        {"control": "token-2-address-is-a-name", "kind": "inert", "basis": "0x004635E4",
         "rows": 29, "rows_matching_native": 29, "control_holds": True},
        {"control": "written-columns-poisoned", "kind": "probe", "basis": "n/a",
         "rows": 29, "rows_matching_native": 29, "control_holds": False},
    ]
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools/s2/corpus_run").mkdir(parents=True)
    (repo / "Documentation").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / "tools/s2/corpus_run/controls.py").write_text(HARNESS)
    ledger(repo, "0" * 64)
    return repo


def ledger(repo: Path, marker: str, hold_map: dict | None = None) -> None:
    suite = {"id": "s2-negative-controls-hold",
             "checker": "Documentation/scripts/check_controls.py",
             "command": ["python3", "Documentation/scripts/check_controls.py"],
             "files": [], "claims": [], "depends_on": [],
             "marker": {"kind": "sha256", "value": marker}, "summary": "stand-in"}
    if hold_map is not None:
        suite["hold_map"] = hold_map
    (repo / "Documentation/fidelity-ledger.json").write_text(json.dumps(
        {"schema": "sa2-fidelity-ledger/v1", "claims": [], "suites": [suite],
         "coverage": {"patterns": []}}, indent=2, sort_keys=True) + "\n")


def report(repo: Path, doc) -> None:
    (repo / "report.json").write_text(json.dumps(doc))


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GATE), *args], cwd=repo,
                          capture_output=True, text=True)


def suite_of(repo: Path) -> dict:
    return json.loads((repo / "Documentation/fidelity-ledger.json").read_text())["suites"][0]


def test_every_asserting_control_holding_passes(repo: Path) -> None:
    report(repo, ONE_FIXTURE)
    result = run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "controls OK: 2 of 2 asserting controls hold across 1 fixtures, 1 probe(s)" \
        in result.stdout
    assert "marker_sha256=" in result.stdout


def test_a_broken_control_fails_and_names_its_basis(repo: Path) -> None:
    """A break control that still passes means that column compared nothing."""
    broken = json.loads(json.dumps(ONE_FIXTURE))
    broken["ground-tail-v4"][0]["control_holds"] = False
    broken["ground-tail-v4"][0]["rows_matching_native"] = 29
    report(repo, broken)
    result = run(repo)
    assert result.returncode == 1
    assert "CONTROL BROKEN ground-tail-v4 [break] work_w13-drives-world_w6" in result.stdout
    assert "'[ebx+54h]'" in result.stdout
    assert "compared nothing there" in result.stderr


def test_a_probe_alone_is_not_a_pass(repo: Path) -> None:
    """A harness that asserts nothing is the vacuity this gate detects."""
    report(repo, {"f": [{"control": "p", "kind": "probe", "rows": 1,
                         "rows_matching_native": 1, "control_holds": False}]})
    result = run(repo)
    assert result.returncode == 1
    assert "asserted nothing" in result.stderr


def test_a_harness_that_writes_no_report_is_missing(repo: Path) -> None:
    report(repo, "no report")
    result = run(repo)
    assert result.returncode == 1
    assert "controls MISSING" in result.stderr
    assert "wrote no report (exit 3)" in result.stderr


def test_an_absent_harness_is_missing(repo: Path) -> None:
    (repo / "tools/s2/corpus_run/controls.py").unlink()
    result = run(repo)
    assert result.returncode == 1
    assert "no controls harness at tools/s2/corpus_run/controls.py" in result.stderr


def test_the_marker_moves_when_a_control_stops_holding(repo: Path) -> None:
    report(repo, ONE_FIXTURE)
    first = [l for l in run(repo).stdout.splitlines() if l.startswith("marker_sha256=")][0]
    broken = json.loads(json.dumps(ONE_FIXTURE))
    broken["ground-tail-v4"][0]["control_holds"] = False
    report(repo, broken)
    second = [l for l in run(repo).stdout.splitlines() if l.startswith("marker_sha256=")]
    assert second == []                      # a failing run prints no marker
    # ... and the map it would have produced is a different one.
    broken["ground-tail-v4"][0]["control_holds"] = True
    broken["ground-tail-v4"].pop()            # the probe leaves the map
    report(repo, broken)
    third = [l for l in run(repo).stdout.splitlines() if l.startswith("marker_sha256=")][0]
    assert third != first


def test_refresh_writes_the_marker_and_the_hold_map(repo: Path) -> None:
    report(repo, ONE_FIXTURE)
    result = run(repo, "--refresh")
    assert result.returncode == 0, result.stdout + result.stderr
    suite = suite_of(repo)
    assert suite["marker"]["value"] != "0" * 64
    assert suite["hold_map"]["ground-tail-v4"]["work_w13-drives-world_w6"] == "holds"
    assert suite["hold_map"]["ground-tail-v4"]["written-columns-poisoned"] == "probe"
    # The written marker is the one a later plain run prints.
    assert f"marker_sha256={suite['marker']['value']}" in run(repo).stdout


def test_refuses_to_refresh_over_a_regression_without_a_reason(repo: Path) -> None:
    report(repo, ONE_FIXTURE)
    run(repo, "--refresh")
    broken = json.loads(json.dumps(ONE_FIXTURE))
    broken["ground-tail-v4"][0]["control_holds"] = False
    report(repo, broken)
    before = suite_of(repo)["marker"]["value"]
    result = run(repo, "--refresh")
    assert result.returncode == 1
    assert "no holding set to baseline" in result.stderr
    assert "CONTROL BROKEN ground-tail-v4" in result.stderr
    assert suite_of(repo)["marker"]["value"] == before

    allowed = run(repo, "--refresh", "--accept-regression", "the plan changed shape")
    assert allowed.returncode == 0, allowed.stderr
    assert "left the holding set" in allowed.stdout
    assert suite_of(repo)["marker"]["value"] != before


def test_refusing_to_baseline_a_broken_map_needs_no_previous_pin(repo: Path) -> None:
    """The first refresh of a harness that is mid-repair must not pin it green."""
    broken = json.loads(json.dumps(ONE_FIXTURE))
    broken["ground-tail-v4"][0]["control_holds"] = False
    report(repo, broken)
    result = run(repo, "--refresh")
    assert result.returncode == 1
    assert "no holding set to baseline" in result.stderr
    assert "hold_map" not in suite_of(repo)


def test_a_control_leaving_the_map_is_a_regression_too(repo: Path) -> None:
    """A control that disappears is as misleading as one that fails."""
    report(repo, ONE_FIXTURE)
    run(repo, "--refresh")
    smaller = json.loads(json.dumps(ONE_FIXTURE))
    smaller["ground-tail-v4"] = smaller["ground-tail-v4"][1:]
    report(repo, smaller)
    result = run(repo, "--refresh")
    assert result.returncode == 1
    assert "holds -> absent" in result.stderr


def test_a_new_control_needs_no_reason(repo: Path) -> None:
    report(repo, ONE_FIXTURE)
    run(repo, "--refresh")
    bigger = json.loads(json.dumps(ONE_FIXTURE))
    bigger["ground-tail-v4"].append(
        {"control": "a-new-break", "kind": "break", "basis": "0x1", "rows": 1,
         "rows_matching_native": 0, "control_holds": True})
    report(repo, bigger)
    result = run(repo, "--refresh")
    assert result.returncode == 0, result.stderr
    assert "entered the map: ground-tail-v4 / a-new-break: new (holds)" in result.stdout
