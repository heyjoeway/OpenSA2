#!/usr/bin/env python3
"""Behaviour tests for check_gate_criteria.py.

The gate exists because `Documentation/gate-criteria.json` held a criterion
that printed a hand-typed sentence as its measured result, for four commits,
beside a ratio that had since contradicted it. The first test below is that
exact string; the rest are the shapes a future one could take.

Every test runs the real script as a subprocess against a criteria file under
tmp_path, with `--root` at the live repository only where a check names a
script that has to exist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
GATE = SCRIPTS / "check_gate_criteria.py"

# The literal that was committed, verbatim.
STALE_SENTENCE = (
    "- no ceiling is established: the 2026-09-20 fold drove 99 percent of registered "
    "rows after the reachability claim was falsified, and the remaining fixtures are "
    "unsurveyed, not unreachable")


def run(criteria: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(ROOT), "--criteria", str(criteria), *args],
        cwd=ROOT, capture_output=True, text=True)


def criteria_file(tmp_path: Path, check: str, gate: str = "G2") -> Path:
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({
        "schema": "sa2-recomp-gate-criteria/v1",
        gate: [{"criterion": "M3 = 100% of fixtures passing (design 5, S2)", "check": check}],
    }))
    return path


def inline(body: str) -> str:
    return '{python} -c "' + body + '"'


def test_the_committed_stale_sentence_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, inline(
        "import re,sys; t=open('METRICS.md').read(); "
        "m=re.search(r'M3 fixtures', t); "
        f"print('M3', m.group(0), '{STALE_SENTENCE}'); sys.exit(0)"))
    result = run(path)
    assert result.returncode == 1
    assert "not computed from its inputs" in result.stdout
    assert "28 words" in result.stdout


def test_a_short_label_beside_a_computed_value_passes(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, inline(
        "import re,sys; t=open('METRICS.md').read(); "
        "m=re.search(r'M3 fixtures \\\\| (\\\\d+)/(\\\\d+)', t); "
        "print('M3', m.group(1)+'/'+m.group(2), 'failed', m.group(2)); "
        "sys.exit(0 if m else 1)"))
    result = run(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate criteria OK: 1 criteria over 1 gates" in result.stdout
    assert "marker_sha256=" in result.stdout


def test_a_typed_measurement_is_rejected(tmp_path: Path) -> None:
    """Short enough to be a label, but it carries a number nothing computed."""
    path = criteria_file(tmp_path, inline("print('M3 is 54 of 54')"))
    result = run(path)
    assert result.returncode == 1
    assert "a run of two or more digits" in result.stdout


def test_a_typed_percentage_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, inline("print('margin 9.3% of budget')"))
    result = run(path)
    assert result.returncode == 1
    assert "not computed from its inputs" in result.stdout


def test_prose_inside_an_fstring_is_rejected(tmp_path: Path) -> None:
    """A sentence one indirection deep is still a sentence."""
    path = criteria_file(tmp_path, inline(
        "n=1; print(f'{n} - the ceiling was never established, and this is typed prose')"))
    result = run(path)
    assert result.returncode == 1
    assert "not computed from its inputs" in result.stdout


def test_prose_inside_a_conditional_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, inline(
        "ok=True; print('fine' if ok else "
        "'no ceiling is established, the remaining fixtures are unsurveyed and not unreachable')"))
    result = run(path)
    assert result.returncode == 1


def test_prose_inside_a_join_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, inline(
        "print(' '.join(['ok', 'the remaining fixtures are unsurveyed, not unreachable']))"))
    result = run(path)
    assert result.returncode == 1


def test_an_unparseable_inline_check_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, inline("import sys; print('x'"))
    result = run(path)
    assert result.returncode == 1
    assert "does not parse" in result.stdout


def test_a_check_that_does_not_name_python_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, "python3 -c \"print('ok')\"")
    result = run(path)
    assert result.returncode == 1
    assert "does not name {python}" in result.stdout


def test_a_script_check_naming_a_missing_file_is_rejected(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, "{python} Documentation/scripts/check_nothing_at_all.py")
    result = run(path)
    assert result.returncode == 1
    assert "which does not exist" in result.stdout


def test_a_script_check_naming_a_real_gate_passes(tmp_path: Path) -> None:
    path = criteria_file(tmp_path, "{python} Documentation/scripts/run_all_gates.py")
    result = run(path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_gate_with_no_criteria_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({"schema": "x", "G2": []}))
    result = run(path)
    assert result.returncode == 1
    assert "is not a non-empty array" in result.stdout


def test_a_key_that_is_not_a_gate_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({"schema": "x", "notes": ["free text"]}))
    result = run(path)
    assert result.returncode == 1
    assert "neither 'schema' nor a gate name" in result.stdout


def test_the_live_criteria_file_passes() -> None:
    """The file this gate exists for, as it stands in the tree."""
    result = subprocess.run([sys.executable, str(GATE)], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate criteria OK" in result.stdout


def test_the_marker_moves_when_a_criterion_changes(tmp_path: Path) -> None:
    first = run(criteria_file(tmp_path, inline("print('ok')"))).stdout
    second = run(criteria_file(tmp_path, inline("print('fine')"))).stdout
    markers = [line for out in (first, second) for line in out.splitlines()
               if line.startswith("marker_sha256=")]
    assert len(markers) == 2
    assert markers[0] != markers[1]
