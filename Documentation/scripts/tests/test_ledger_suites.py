"""Tests for check_ledger_suites.py, the gate that runs every ledger suite.

Each test builds a throwaway git repository whose ledger registers one suite
with a tiny checker that prints a marker, then runs the real gate as a
subprocess with cwd at that repository (it finds the root by
`git rev-parse --show-toplevel`). Nothing here touches the live tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
GATE = SCRIPTS / "check_ledger_suites.py"
LEDGER = "Documentation/fidelity-ledger.json"
CHECKER = "Documentation/scripts/check_thing.py"
MARKER = "a" * 64
CHECKER_SOURCE = (
    "print('thing adapted_since_import=3')\n"
    f"print('thing OK: marker_sha256={MARKER}')\n"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(("git", "-C", str(repo)) + args, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


def write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def ledger_for(repo: Path, marker: str = MARKER, checker: str = CHECKER) -> dict:
    checker_path = repo / checker
    pin = sha256(checker_path.read_bytes()) if checker_path.is_file() else "0" * 64
    return {
        "schema": "sa2-fidelity-ledger/v1",
        "claims": [{
            "id": "thing", "summary": "A thing.", "authority": "observed",
            "depends_on": [], "outputs": [],
            "sources": [{"path": checker, "sha256": pin}],
        }],
        "suites": [{
            "id": "thing-check", "summary": "Check the thing.",
            "claims": ["thing"], "depends_on": [], "checker": checker,
            "files": [{"path": checker, "sha256": pin}],
            "command": ["python3", checker],
            "marker": {"kind": "sha256", "value": marker},
        }],
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    write(tmp_path, CHECKER, CHECKER_SOURCE)
    write(tmp_path, LEDGER, json.dumps(ledger_for(tmp_path), indent=2) + "\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run((sys.executable, str(GATE)) + args, cwd=repo,
                          capture_output=True, text=True, check=False)


def test_matching_marker_passes_and_relays_degraded_lines(repo: Path):
    result = run_gate(repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"OK      thing-check  marker_sha256={MARKER}" in result.stdout
    assert "thing adapted_since_import=3" in result.stdout
    assert result.stdout.rstrip().splitlines()[-1] == \
        "ledger suites OK: 1 suites, markers match"


def test_mismatched_marker_fails_with_both_values(repo: Path):
    pinned = "b" * 64
    write(repo, LEDGER, json.dumps(ledger_for(repo, marker=pinned), indent=2) + "\n")
    result = run_gate(repo)
    assert result.returncode != 0
    assert f"FAIL    thing-check  marker mismatch: printed {MARKER} != pinned {pinned}" \
        in result.stdout
    assert "ledger suites FAIL: 1 of 1 suites" in result.stderr
    assert "ledger suites OK" not in result.stdout


def test_stale_pinned_file_fails_naming_suite_and_file(repo: Path):
    # The checker changes but the ledger keeps the old pin: the suite that
    # would run is not the one that was registered, even though its marker
    # would still match.
    stale_pin = sha256((repo / CHECKER).read_bytes())
    write(repo, CHECKER, CHECKER_SOURCE + "# edited after pinning\n")
    fresh = sha256((repo / CHECKER).read_bytes())
    result = run_gate(repo)
    assert result.returncode != 0
    assert (f"FAIL    thing-check  pinned file stale: {CHECKER} working tree "
            f"{fresh} != pinned {stale_pin}") in result.stdout
    assert "ledger suites FAIL: 1 of 1 suites" in result.stderr


def test_missing_checker_is_missing_and_fails(repo: Path):
    absent = "Documentation/scripts/check_absent.py"
    write(repo, LEDGER, json.dumps(ledger_for(repo, checker=absent), indent=2) + "\n")
    result = run_gate(repo)
    assert result.returncode != 0
    # The pin check runs first and the file is absent; either way the suite
    # cannot pass and the reason names the file.
    assert "thing-check" in result.stdout and absent in result.stdout
    assert "ledger suites FAIL: 1 of 1 suites" in result.stderr


def test_command_that_cannot_start_is_missing(repo: Path):
    ledger = ledger_for(repo)
    ledger["suites"][0]["command"] = ["python3", "${SA2_PRIVATE_FIXTURES}"]
    write(repo, LEDGER, json.dumps(ledger, indent=2) + "\n")
    env = {key: value for key, value in os.environ.items()
           if key != "SA2_PRIVATE_FIXTURES"}
    result = subprocess.run((sys.executable, str(GATE)), cwd=repo, env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "MISSING thing-check  ${SA2_PRIVATE_FIXTURES} is not set" in result.stdout


def test_nonzero_checker_exit_fails_even_with_matching_marker(repo: Path):
    write(repo, CHECKER, CHECKER_SOURCE + "raise SystemExit(3)\n")
    write(repo, LEDGER, json.dumps(ledger_for(repo), indent=2) + "\n")
    result = run_gate(repo)
    assert result.returncode != 0
    assert "FAIL    thing-check  exit 3:" in result.stdout
