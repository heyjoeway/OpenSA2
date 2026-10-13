"""Tests for refresh_receipt_pins.py (Emerald Cascade).

The tool may move a stale receipt pin; it must never move a ledger claim pin,
because a claim pin belongs to the suite that verifies the claim and moves
only after that suite is re-run (AGENTS.md rule 8). Each test builds a
throwaway git repository with a ledger, one claim, one suite and one receipt,
then runs the real tool with `--root` at that repository.
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
TOOL = SCRIPTS / "refresh_receipt_pins.py"
LEDGER = "Documentation/fidelity-ledger.json"
SOURCE = "Documentation/receipts/thing-v1.json"
CHECKER = "Documentation/scripts/check_thing.py"
RECEIPT = "Documentation/receipts/derived-v1.json"
NOTE = "Documentation/notes.txt"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def dump(payload: dict) -> str:
    return json.dumps(payload, indent=2) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    write(tmp_path, SOURCE, dump({"schema": "receipt/v1", "measured": 1}))
    write(tmp_path, NOTE, "first\n")
    write(tmp_path, CHECKER, "print('thing OK: marker_sha256=' + 'a' * 64)\n")
    write(tmp_path, RECEIPT, dump({
        "schema": "receipt/v1",
        "inputs": [{"file": NOTE, "sha256": sha256(tmp_path / NOTE),
                    "size": (tmp_path / NOTE).stat().st_size}],
    }))
    write(tmp_path, LEDGER, dump({
        "schema": "sa2-fidelity-ledger/v1",
        "coverage": {"patterns": []},
        "claims": [{
            "id": "thing", "summary": "A thing.", "authority": "observed",
            "depends_on": [], "outputs": [],
            "sources": [{"path": SOURCE, "sha256": sha256(tmp_path / SOURCE)}],
        }],
        "suites": [{
            "id": "thing-check", "summary": "Check the thing.",
            "claims": ["thing"], "depends_on": [], "checker": CHECKER,
            "files": [{"path": CHECKER, "sha256": sha256(tmp_path / CHECKER)}],
            "command": ["python3", CHECKER],
            "marker": {"kind": "sha256", "value": "a" * 64},
        }],
    }))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "baseline")
    return tmp_path


def run_tool(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run((sys.executable, str(TOOL), "--root", str(repo)) + args,
                          cwd=repo, capture_output=True, text=True, check=False)


def test_clean_repository_reports_nothing_to_move(repo: Path):
    result = run_tool(repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "EMERALD_CASCADE_SUMMARY would_move=0 skipped=0 other_errors=0" in result.stdout


def test_stale_claim_pin_is_reported_and_not_rewritten_under_apply(repo: Path):
    before = (repo / LEDGER).read_text()
    old = sha256(repo / SOURCE)
    write(repo, SOURCE, dump({"schema": "receipt/v1", "measured": 2}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "remeasure")
    new = sha256(repo / SOURCE)

    result = run_tool(repo, "--apply")

    assert (repo / LEDGER).read_text() == before, "ledger claim pin was rewritten"
    assert result.returncode != 0
    line = next(line for line in result.stdout.splitlines()
                if line.startswith("STALE CLAIM PIN"))
    assert f"claim thing source {SOURCE}" in line
    assert f"ledger holds {old}" in line and f"file hashes to {new}" in line
    assert "re-run the owning suite thing-check (python3 " + CHECKER + ")" in line
    assert f"replace the exact old hash in {LEDGER} by hand" in line
    assert "MOVE claim" not in result.stdout
    assert "audit_errors_after=1" in result.stdout
    assert "EMERALD_CASCADE_AUDIT_STILL_FAILS" in result.stderr


def test_dry_run_also_reports_stale_claim_pin_without_writing(repo: Path):
    before = (repo / LEDGER).read_text()
    write(repo, SOURCE, dump({"schema": "receipt/v1", "measured": 2}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "remeasure")
    result = run_tool(repo)
    assert (repo / LEDGER).read_text() == before
    assert "STALE CLAIM PIN claim thing" in result.stdout
    assert "would_move=0 skipped=1" in result.stdout


def test_stale_receipt_pin_is_moved_under_apply(repo: Path):
    # The receipt path is what the tool is for; prove it still works after
    # the claim path was removed.
    write(repo, NOTE, "second\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "edit note")
    new = sha256(repo / NOTE)

    dry = run_tool(repo)
    assert dry.returncode == 0, dry.stderr + dry.stdout
    assert "WOULD MOVE" in dry.stdout
    assert json.loads((repo / RECEIPT).read_text())["inputs"][0]["sha256"] != new

    result = run_tool(repo, "--apply")
    assert result.returncode == 0, result.stderr + result.stdout
    node = json.loads((repo / RECEIPT).read_text())["inputs"][0]
    assert node["sha256"] == new
    assert node["size"] == len("second\n")
    assert "moved=1" in result.stdout and "audit_errors_after=0" in result.stdout
