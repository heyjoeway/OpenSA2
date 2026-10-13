"""A closed budget can move only through an explicit, recorded override."""
import json
from pathlib import Path
import pytest
import test_units as TU
from test_units import units, project


def test_audit_record_refuses_all_changes_if_any_target_stage_is_closed(units, tmp_path, capsys):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for ident, stage in (("aopen", "S5"), ("aclosed", "S4")):
        root = TU.transcript(tasks, ident, "2026-09-29T15:00:00Z", "2026-09-29T15:30:00Z",
                             spawns=((ident + "child", "review child"),))
        TU.transcript(tasks, ident + "child", "2026-09-29T15:05:00Z", "2026-09-29T15:20:00Z")
        units.command_record(TU.record_args(root, stage, "parent", "review", closed_stage=True))
    original = units.UNITS_FILE.read_bytes()
    with pytest.raises(SystemExit, match="--stage S4 closed"):
        units.command_audit(TU.audit_args(tasks, record=True, closed_stage=False))
    assert units.UNITS_FILE.read_bytes() == original
    capsys.readouterr()
    assert units.command_audit(TU.audit_args(tasks, record=True, closed_stage=True)) == 0
    out = capsys.readouterr().out
    assert "CLOSED_STAGE_MOVED S4 +0.2500 h" in out
    held = TU.ledger(units)
    assert held["aclosedchild"]["closed_stage_override"] == "development_snapshot"
    assert "closed_stage_override" not in held["aopenchild"]


def closed_gate(units, project, monkeypatch):
    docs = project / "Documentation"
    docs.mkdir()
    unit = {"id": "held", "stage": "S5", "started": "2026-09-29T15:00:00Z",
            "ended": "2026-09-29T15:30:00Z", "hours": 0.5}
    (docs / "agent-units.json").write_text(json.dumps({"units": [unit]}))
    (docs / "agent-sessions.json").write_text(json.dumps({"sessions": [{"id": "main"}]}))
    TU.git(project, "init", "-q")
    TU.git(project, "config", "user.email", "test@example.invalid")
    TU.git(project, "config", "user.name", "Test")
    TU.git(project, "add", "Documentation")
    TU.git(project, "commit", "-q", "-m", "gate")
    gate = TU.git(project, "rev-parse", "HEAD")
    monkeypatch.setattr(units, "stage_table", lambda: [{"id": "S5", "gate_commit": gate}])
    return unit, gate


def test_closed_stage_gate_units_are_a_baseline_and_later_changes_need_a_marker(
        units, project, monkeypatch):
    unit, gate = closed_gate(units, project, monkeypatch)
    assert units.closed_stage_problems([unit]) == []
    changed = dict(unit, ended="2026-09-29T16:00:00Z", hours=1.0)
    assert "without the --closed-stage marker" in units.closed_stage_problems([changed])[0]
    changed["closed_stage_override"] = gate
    assert units.closed_stage_problems([changed]) == []
    added = dict(unit, id="new")
    assert "added after gate" in units.closed_stage_problems([unit, added])[0]
    added["closed_stage_override"] = "another-gate"
    assert units.closed_stage_problems([unit, added])
    added["closed_stage_override"] = gate
    assert units.closed_stage_problems([unit, added]) == []


def test_a_gate_counted_main_cannot_be_charged_as_a_unit_even_with_override(
        units, project, monkeypatch):
    unit, gate = closed_gate(units, project, monkeypatch)
    moved = dict(unit, id="main", closed_stage_override=gate)
    assert "also counted in its gate's main ledger" in units.closed_stage_problems([unit, moved])[0]
