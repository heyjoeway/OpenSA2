#!/usr/bin/env python3
"""Behaviour tests for ship.py's order of steps, the session ledger's first.

From S5 on the budget counts the main thread from the session ledger, which is
only as current as its last recording; ship.py records it before it regenerates
METRICS.md and runs the gates, stages it even under --no-add, and leaves it alone
when the commit rewrites the reach reproduction receipt, whose digest covers it.
Every command is captured instead of run; nothing here commits or pushes.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

SHIP = Path(__file__).resolve().parents[1] / "ship.py"


@pytest.fixture
def ship(monkeypatch, tmp_path: Path):
    monkeypatch.syspath_prepend(str(SHIP.parent))
    spec = importlib.util.spec_from_file_location("ship_under_test", SHIP)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ship_under_test", module)
    spec.loader.exec_module(module)
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "run", lambda cmd, cwd: calls.append([str(c) for c in cmd]))
    monkeypatch.setattr(module, "project_root", lambda explicit: tmp_path)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Namespace(stdout="abc1234\n"))
    pending = types.SimpleNamespace(receipt_pending=lambda root: False,
                                    RECEIPT_REL="Documentation/receipts/r.json")
    monkeypatch.setitem(sys.modules, "check_reach_reproduction", pending)
    module.calls, module.pending = calls, pending
    return module


def steps(ship, monkeypatch, *args: str) -> list[str]:
    monkeypatch.setattr(sys, "argv", ["ship.py", "subject", *args])
    ship.main()
    return [" ".join(Path(c).name if "/" in c else c for c in call[1:3]) if call[0] != "git"
            else " ".join(call[:3]) for call in ship.calls]


def test_the_session_ledger_is_recorded_and_staged_first(ship, monkeypatch) -> None:
    assert steps(ship, monkeypatch, "--no-push") == [
        "sessions.py record", "git add --", "metrics.py --write", "git add -A",
        "run_all_gates.py", "git commit -q"]
    assert ship.calls[1] == ["git", "add", "--", "Documentation/agent-sessions.json"]


def test_the_ledger_is_staged_under_no_add(ship, monkeypatch) -> None:
    """ship_subset.py runs ship.py with --no-add in its worktree; the recording is part
    of the commit, or the worktree holds a change the commit does not and the push is
    refused."""
    assert steps(ship, monkeypatch, "--no-push", "--no-add") == [
        "sessions.py record", "git add --", "metrics.py --write", "git add --",
        "run_all_gates.py", "git commit -q"]


def test_no_record_skips_it_but_checks_the_ledger_was_recorded_since_head(
        ship, monkeypatch, capsys) -> None:
    """A commit carrying a ledger recorded before HEAD would fail the session ledger
    gate's STALE check at every clean checkout of it, so the skip still needs a ledger
    recorded since HEAD (`sessions.py fresh`, which stops the ship when it fails)."""
    assert steps(ship, monkeypatch, "--no-push", "--no-record")[:2] == [
        "sessions.py fresh", "metrics.py --write"]
    assert "not recorded (--no-record)" in capsys.readouterr().out


def test_a_commit_rewriting_the_reach_receipt_skips_the_recording(ship, monkeypatch,
                                                                   capsys) -> None:
    """The receipt's export digest covers the ledger, so the ledger is recorded before
    reach_reproduce.py --write; recording it here would move a digested file. Whether it
    was recorded is checked instead."""
    monkeypatch.setattr(ship.pending, "receipt_pending", lambda root: True)
    assert steps(ship, monkeypatch, "--no-push")[:2] == ["sessions.py fresh",
                                                        "metrics.py --write"]
    assert "record it (tools/sessions.py record) before tools/s4/reach_reproduce.py" in \
        capsys.readouterr().out


def test_a_failing_step_zero_commits_nothing(ship, monkeypatch) -> None:
    """record (and fresh) exit 1 exactly when the session ledger gate would fail on what
    they recorded or checked; the ship stops there, before its gates."""
    def refuse(cmd, cwd):
        ship.calls.append([str(c) for c in cmd])
        if cmd[-1] in ("record", "fresh"):
            raise SystemExit("ship: failed; nothing committed")

    monkeypatch.setattr(ship, "run", refuse)
    monkeypatch.setattr(sys, "argv", ["ship.py", "subject", "--no-push"])
    with pytest.raises(SystemExit, match="nothing committed"):
        ship.main()
    assert [Path(call[1]).name for call in ship.calls] == ["sessions.py"]
