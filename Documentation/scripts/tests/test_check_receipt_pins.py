#!/usr/bin/env python3
"""Behaviour tests for check_receipt_pins.py against throwaway repositories.

Every test builds a git repository under tmp_path with a tool and a receipt
pinning it, then runs the real gate as a subprocess with cwd set to that
repository; the script locates the project with `git rev-parse --show-toplevel`
from cwd, which is what makes this possible. Nothing here reads or writes the
live tree, and nothing here touches a real receipt.

The load-bearing test is `test_a_digest_that_never_existed_fails`, which
reproduces the defect the gate was written for: a receipt committed pinning a
digest that no version of the file ever hashed to. A gate that has not been
shown to fail has not been shown to be a gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
GATE = SCRIPTS / "check_receipt_pins.py"

T0 = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)

TOOL = "tools/s3/whole_image/receipt.py"
OLD_TOOL = "print('the version that took the measurement')\n"
NEW_TOOL = "print('the version that came later')\n"
RECEIPT = "Documentation/receipts/s3-link-v1.json"
NEVER_EXISTED = "42a9ad8db88a44c408ffd62454fa64d75eb4529bbb6207f34aa0557d48cc89a1"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def git_env(when: datetime) -> dict[str, str]:
    stamp = when.isoformat()
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    })
    return env


def git(repo: Path, *args: str, when: datetime = T0) -> str:
    return subprocess.run(("git", "-C", str(repo)) + args, env=git_env(when),
                          capture_output=True, text=True, check=True).stdout.strip()


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def commit(repo: Path, when: datetime, message: str, files: dict[str, str]) -> str:
    for relative, content in files.items():
        write(repo, relative, content)
    git(repo, "add", "-A", when=when)
    git(repo, "commit", "-q", "-m", message, when=when)
    return git(repo, "rev-parse", "HEAD", when=when)


def receipt(pins: object, identifier: str = "s3-link-v1") -> str:
    # "schema": 1 on purpose: the live link receipt carries an integer schema
    # where its siblings carry a string, and the gate must not filter on it.
    return json.dumps({"schema": 1, "id": identifier,
                       "inputs": {"pipeline": pins}}, indent=1) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository whose receipt honestly pins the tool as it then stood."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(repo, T0, "the measurement and the receipt that records it",
           {TOOL: OLD_TOOL, RECEIPT: receipt({TOOL: sha(OLD_TOOL)})})
    return repo


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run((sys.executable, str(GATE)) + args, cwd=repo,
                          env=git_env(T0), capture_output=True, text=True)


# The honest cases ------------------------------------------------------------

def test_a_matching_pin_passes(repo: Path) -> None:
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "receipt pins OK: 1 pin(s) across 1 receipt(s)" in result.stdout
    assert "1 against the commit that wrote them, 0 against the working tree" in result.stdout
    assert "marker_sha256=" in result.stdout


def test_an_old_receipt_keeps_passing_when_the_tool_moves_on(repo: Path) -> None:
    """The subtle rule: a receipt pins history, so HEAD moving is not a failure.

    A gate that compared against HEAD would fail here and force the receipt to
    be rewritten, which is the opposite of what a receipt is for.
    """
    commit(repo, T0 + HOUR, "the tool is legitimately rewritten", {TOOL: NEW_TOOL})
    assert (repo / TOOL).read_text() == NEW_TOOL
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "checked against last committed" in result.stdout


def test_a_tool_deleted_after_the_receipt_does_not_fail(repo: Path) -> None:
    git(repo, "rm", "-q", TOOL, when=T0 + HOUR)
    git(repo, "commit", "-q", "-m", "the tool is retired", when=T0 + HOUR)
    result = run(repo)
    assert result.returncode == 0, result.stderr


def test_a_receipt_being_written_now_is_measured_against_the_working_tree(repo: Path) -> None:
    """The gate runs from ship.py before the commit, as pending_update() handles."""
    write(repo, TOOL, NEW_TOOL)
    write(repo, RECEIPT, receipt({TOOL: sha(NEW_TOOL)}))
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "checked against updated in the working tree" in result.stdout
    assert "0 against the commit that wrote them, 1 against the working tree" in result.stdout


def test_a_receipt_never_committed_is_measured_against_the_working_tree(repo: Path) -> None:
    write(repo, "Documentation/receipts/brand-new-v1.json",
          receipt({TOOL: sha(OLD_TOOL)}, identifier="brand-new-v1"))
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "checked against not committed yet" in result.stdout


# The defect this gate exists for ---------------------------------------------

def test_a_digest_that_never_existed_fails(repo: Path) -> None:
    """The real S3 defect: a pin committed at a digest nothing ever hashed to."""
    commit(repo, T0 + HOUR, "receipt rewritten with a digest from nowhere",
           {RECEIPT: receipt({TOOL: NEVER_EXISTED})})
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert f"inputs.pipeline pin {TOOL} at {NEVER_EXISTED}" in result.stderr
    assert f"that file is {sha(OLD_TOOL)}" in result.stderr
    assert "which last modified this receipt" in result.stderr
    assert "receipt pins FAIL: 1 finding(s) in 1 receipt(s)" in result.stderr
    assert "AGENTS.md rule 8" in result.stderr


def test_a_bad_pin_is_caught_before_the_commit(repo: Path) -> None:
    """The case that matters: ship.py gates the receipt before it becomes history.

    The receipt is edited in the working tree and not committed, so the basis
    is the working tree; the pin is wrong there, and the gate stops the commit
    that would otherwise have carried the defect into the log.
    """
    write(repo, RECEIPT, receipt({TOOL: NEVER_EXISTED}))
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert "checked against updated in the working tree" in result.stderr
    assert f"in the working tree that file is {sha(OLD_TOOL)}" in result.stderr


def test_a_pin_on_a_file_absent_at_that_commit_fails(repo: Path) -> None:
    commit(repo, T0 + HOUR, "pin a tool that is not there yet",
           {RECEIPT: receipt({"tools/s3/whole_image/added_later.py": sha(NEW_TOOL)})})
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert "names a file that is not present in commit" in result.stderr
    assert "is pinning nothing" in result.stderr


def test_a_value_that_is_not_a_digest_fails(repo: Path) -> None:
    commit(repo, T0 + HOUR, "a pin that cannot be compared",
           {RECEIPT: receipt({TOOL: "v2.1"})})
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert "carries the string 'v2.1', not a sha256" in result.stderr


def test_a_path_escaping_the_repository_fails(repo: Path) -> None:
    commit(repo, T0 + HOUR, "a pin on another tree",
           {RECEIPT: receipt({"../elsewhere/tool.py": sha(OLD_TOOL)})})
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert "is not a repository-relative path" in result.stderr


def test_an_unparseable_receipt_fails(repo: Path) -> None:
    commit(repo, T0 + HOUR, "truncated receipt",
           {"Documentation/receipts/broken-v1.json": '{"inputs": {"pipeline"'})
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert "cannot be read as JSON" in result.stderr


# The shapes that are not pins ------------------------------------------------

def test_a_nested_sha256_object_is_understood(repo: Path) -> None:
    commit(repo, T0 + HOUR, "the pins-node shape",
           {RECEIPT: receipt({TOOL: {"sha256": sha(OLD_TOOL), "size": len(OLD_TOOL)}})})
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "1 pin(s) across 1 receipt(s)" in result.stdout


def test_a_receipt_without_a_pipeline_is_counted_not_failed(repo: Path) -> None:
    """Most receipts here have no inputs.pipeline; requiring one is a different rule."""
    commit(repo, T0 + HOUR, "a receipt with no pipeline", {
        "Documentation/receipts/plain-v1.json":
            json.dumps({"schema": "sa2-recomp-receipt/v1", "id": "plain-v1",
                        "inputs": {"exe": "private/exe/sonic2app.exe"}}) + "\n",
        "Documentation/receipts/no-inputs-v1.json":
            json.dumps({"schema": "sa2-recomp-receipt/v1", "id": "no-inputs-v1"}) + "\n",
    })
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert "of 3 receipts, 2 carry no inputs.pipeline" in result.stdout


def test_a_prose_pipeline_is_counted_as_prose(repo: Path) -> None:
    """emitter-determinism-v1.json carries a command line where others carry a map."""
    commit(repo, T0 + HOUR, "a prose pipeline", {
        "Documentation/receipts/emitter-v1.json":
            receipt("remill-lift-14 --arch x86, then pc24pass.py", identifier="emitter-v1"),
    })
    result = run(repo)
    assert result.returncode == 0, result.stderr
    assert ("1 carry a prose pipeline (Documentation/receipts/emitter-v1.json)"
            in result.stdout)


def test_receipts_in_subdirectories_are_scanned(repo: Path) -> None:
    commit(repo, T0 + HOUR, "a residue receipt with a bad pin",
           {"Documentation/receipts/residues/pc53-v1.json":
                receipt({TOOL: NEVER_EXISTED}, identifier="pc53-v1")})
    result = run(repo)
    assert result.returncode != 0, result.stdout
    assert "residues/pc53-v1.json" in result.stderr


# Wiring ----------------------------------------------------------------------

def test_explicit_root_and_listing(repo: Path, tmp_path: Path) -> None:
    result = subprocess.run((sys.executable, str(GATE), "--root", str(repo), "--list"),
                            cwd=tmp_path, env=git_env(T0), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"OK  {RECEIPT}  {TOOL}" in result.stdout


def test_the_gate_is_registered_in_run_all_gates() -> None:
    sys.path.insert(0, str(SCRIPTS))
    import run_all_gates

    assert ("receipt pins", ("check_receipt_pins.py",)) in run_all_gates.GATES
