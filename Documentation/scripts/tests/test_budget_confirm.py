"""Confirm-review regressions with isolated ledgers and no repository operations."""
from argparse import Namespace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


TREE = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def at(hours):
    return T0 + timedelta(hours=hours)


def stamp(hours):
    return at(hours).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def budget(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "path", list(sys.path))
    modules = {}
    for name, relative in (("gitlog", "Documentation/scripts/gitlog.py"),
                           ("harness", "tools/harness.py"),
                           ("metrics", "Documentation/scripts/metrics.py"),
                           ("sessions", "tools/sessions.py"),
                           ("units", "tools/units.py")):
        path = TREE / relative
        module = ModuleType(name)
        module.__file__ = str(path)
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        modules[name] = module
    env = SimpleNamespace(**modules)
    monkeypatch.setattr(env.sessions, "ROOT", tmp_path)
    monkeypatch.setattr(env.sessions, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(env.sessions, "UNITS_FILE", tmp_path / "units.json")
    monkeypatch.setattr(env.sessions, "opening_instant", lambda: T0)
    monkeypatch.setattr(env.sessions, "stale", lambda point, committed: None)
    monkeypatch.setattr(env.harness, "committed_instant", lambda root: None)
    monkeypatch.setattr(env.metrics, "load_stages", lambda root: [])
    monkeypatch.setattr(env.metrics, "stage_bounds", lambda stages: stages)
    env.sessions.UNITS_FILE.write_text(json.dumps({"units": []}))
    return env


def trace(path, hours):
    rows = [{"type": "user", "timestamp": stamp(hour), "sessionId": "main",
             "uuid": f"stamp-{hour}", "turnOrigin": "human"} for hour in hours]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def save_sessions(sessions, point, rows):
    sessions.SESSIONS_FILE.write_text(json.dumps({"schema": sessions.SCHEMA,
                                                 "recorded_at": stamp(point),
                                                 "sessions": rows}))


def test_audit_rejects_an_absent_main_with_only_singleton_split_windows(
        budget, tmp_path, monkeypatch, capsys):
    sessions, harness = budget.sessions, budget.harness
    path = trace(tmp_path / "main.jsonl", [0, 4, 8])
    scanned = sessions.scan(path, at(12), T0)
    assert scanned["windows"] == [(at(0), at(0)), (at(4), at(4)), (at(8), at(8))]
    save_sessions(sessions, 12, [])
    monkeypatch.setattr(sessions, "transcript_places", lambda given, notes=None:
                        [harness.Place(tmp_path, (path,), "sessions")])
    assert sessions.command_audit(Namespace(transcripts=None, transcripts_optional=False,
                                           grace_hours=2)) == 1
    captured = capsys.readouterr()
    assert "UNCOVERED main: 8.0000 h" in captured.err
    assert "failures=1 uncovered=1 uncovered_hours=8.0000 pending=0" in captured.out


def test_partial_main_coverage_fails_for_time_older_than_grace_on_either_reading(
        budget, tmp_path, monkeypatch, capsys):
    sessions = budget.sessions
    path = trace(tmp_path / "main.jsonl", [0, 1, 5, 5.5])
    scanned = sessions.scan(path, at(6), T0)
    held = sessions.entry(dict(scanned, windows=[(at(0), at(1))], gaps=[]), stamp(6))
    failures, notes, counts = sessions.judge_transcripts(
        [scanned], [], {scanned["id"]: held}, T0, at(6), 2)
    assert len(failures) == 1
    assert "UNCOVERED main: 3.0000 h" in failures[0]
    assert counts["uncovered"] == 1 and counts["uncovered_hours"] == 3 * 3600
    assert counts["pending"] == 1
    assert any("PENDING main   1.5000 h" in note for note in notes)
    capsys.readouterr()
    save_sessions(sessions, 6, [held])
    monkeypatch.setattr(sessions, "transcript_places", lambda given, notes=None:
                        [budget.harness.Place(tmp_path, (path,), "sessions")])
    assert sessions.command_audit(Namespace(transcripts=None, transcripts_optional=False,
                                           grace_hours=2)) == 1
    captured = capsys.readouterr()
    assert "UNCOVERED main: 3.0000 h" in captured.err
    assert "failures=1 uncovered=1 uncovered_hours=3.0000 pending=1" in captured.out


def test_record_keeps_the_first_to_last_envelope_after_a_singleton_start_is_truncated(
        budget, tmp_path, monkeypatch, capsys):
    sessions, gitlog = budget.sessions, budget.gitlog
    path = trace(tmp_path / "main.jsonl", [0, 4, 5])
    before = sessions.entry(sessions.scan(path, at(12), T0), stamp(12))
    save_sessions(sessions, 12, [before])
    monkeypatch.setattr(sessions, "clock", lambda: at(12))
    trace(path, [4, 5])
    assert sessions.command_record(Namespace(transcript=[path], transcripts=None,
                                            allow_shrink=False, grace_hours=2)) == 0
    captured = capsys.readouterr()
    assert "SESSION_SHRINK main.jsonl: recording it again would remove 4.0000 h" in captured.err
    assert "SESSION_RECORDED" not in captured.out
    after = json.loads(sessions.SESSIONS_FILE.read_text())["sessions"][0]
    assert after == before
    assert gitlog.span(gitlog.first_to_last_windows(gitlog.windows(after))) == timedelta(hours=5)
    assert sessions.command_record(Namespace(transcript=[path], transcripts=None,
                                            allow_shrink=True, grace_hours=2)) == 0
    assert "SESSION_SHRINK" not in capsys.readouterr().err
    allowed = json.loads(sessions.SESSIONS_FILE.read_text())["sessions"][0]
    assert allowed["windows"] == [[stamp(4), stamp(5)]]
    assert gitlog.span(gitlog.first_to_last_windows(gitlog.windows(allowed))) == timedelta(hours=1)


def test_record_protects_the_binding_windows_when_the_envelope_is_equal(
        budget, tmp_path, monkeypatch, capsys):
    sessions = budget.sessions
    path = trace(tmp_path / "main.jsonl", [0, 1, 2, 3, 4, 5])
    before = sessions.entry(sessions.scan(path, at(12), T0), stamp(12))
    save_sessions(sessions, 12, [before])
    monkeypatch.setattr(sessions, "clock", lambda: at(12))
    trace(path, [0, 1, 5])
    assert sessions.command_record(Namespace(transcript=[path], transcripts=None,
                                            allow_shrink=False, grace_hours=2)) == 0
    assert "SESSION_SHRINK main.jsonl: recording it again would remove 4.0000 h" \
        in capsys.readouterr().err
    assert json.loads(sessions.SESSIONS_FILE.read_text())["sessions"][0] == before


def closed_units(budget, tmp_path, monkeypatch, baseline):
    units, harness = budget.units, budget.harness
    gate = "closed-gate"
    monkeypatch.setattr(units, "ROOT", tmp_path)
    monkeypatch.setattr(units, "UNITS_FILE", tmp_path / "closed-units.json")
    monkeypatch.setattr(units, "stage_table", lambda: [{"id": "S5", "gate_commit": gate}])
    monkeypatch.setattr(units, "audit_places", lambda args, notes: [])
    monkeypatch.setattr(harness, "primary_checkout", lambda root: root)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    def gate_read(root, *args):
        assert args[0] == "show"
        relative = args[-1].split(":", 1)[1]
        body = {"units": baseline} if relative == harness.UNITS_LEDGER else {"sessions": []}
        return SimpleNamespace(returncode=0, stdout=json.dumps(body).encode())

    monkeypatch.setattr(harness, "_git", gate_read)
    return gate


def unit(ident):
    return {"id": ident, "stage": "S5", "started": stamp(0), "ended": stamp(1),
            "hours": 1, "role": "review", "label": "held", "source": "pruned.jsonl"}


@pytest.mark.parametrize("partial", [False, True], ids=["last-row", "partial-removal"])
def test_audit_rejects_removal_of_gate_recorded_units_without_local_transcripts(
        budget, tmp_path, monkeypatch, capsys, partial):
    held, retained = unit("held"), unit("retained")
    baseline = [held, retained] if partial else [held]
    current = [retained] if partial else []
    closed_units(budget, tmp_path, monkeypatch, baseline)
    units = budget.units
    units.UNITS_FILE.write_text(json.dumps({"schema": units.SCHEMA, "units": current}))
    problems = units.closed_stage_problems(current)
    assert problems == ["S5 held: gate-recorded window starting 2026-10-01T00:00:00.000Z "
                        "is missing after gate closed-gate"]
    assert units.command_audit(Namespace(tasks=None, all_files=False, tasks_optional=True,
                                        grace_minutes=10, record=False, role_from_parent=False,
                                        role="review", closed_stage=False)) == 1
    captured = capsys.readouterr()
    assert "CLOSED_STAGE_UNMARKED S5 held: gate-recorded window" in captured.out
    assert "AUDIT_FAIL:" in captured.err and "is missing after gate closed-gate" in captured.err


def test_closed_stage_additions_and_changed_windows_still_require_the_matching_marker(
        budget, tmp_path, monkeypatch):
    held = unit("held")
    gate = closed_units(budget, tmp_path, monkeypatch, [held])
    units = budget.units
    assert units.closed_stage_problems([held]) == []
    changed = dict(held, ended=stamp(2), hours=2)
    assert "without the --closed-stage marker" in units.closed_stage_problems([changed])[0]
    changed["closed_stage_override"] = gate
    assert units.closed_stage_problems([changed]) == []
    added = dict(unit("new"), closed_stage_override="another-gate")
    assert "without the --closed-stage marker" in units.closed_stage_problems([held, added])[0]
    added["closed_stage_override"] = gate
    assert units.closed_stage_problems([held, added]) == []


def test_a_gate_main_session_is_rejected_as_a_unit_even_with_a_closed_stage_marker(
        budget, tmp_path, monkeypatch):
    held = unit("held")
    gate = closed_units(budget, tmp_path, monkeypatch, [held])

    def gate_read(root, *args):
        relative = args[-1].split(":", 1)[1]
        body = {"units": [held]} if relative == budget.harness.UNITS_LEDGER \
            else {"sessions": [{"id": "main"}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(body).encode())

    monkeypatch.setattr(budget.harness, "_git", gate_read)
    moved = dict(unit("main"), closed_stage_override=gate)
    assert budget.units.closed_stage_problems([held, moved]) == [
        "S5 main: also counted in its gate's main ledger"]


def test_historic_stages_keep_their_settled_closed_stage_method(budget, monkeypatch):
    monkeypatch.setattr(budget.units, "stage_table", lambda:
                        [{"id": f"S{number}", "gate_commit": "historic-gate"}
                         for number in range(5)])

    def no_gate_read(*args):
        raise AssertionError("the S5 baseline rule must not read historic gate ledgers")

    monkeypatch.setattr(budget.harness, "_git", no_gate_read)
    assert budget.units.closed_stage_problems([]) == []


def test_counted_windows_binds_the_split_windows_and_reports_the_envelope(
        budget, tmp_path, monkeypatch):
    """The ruling of 2026-10-04: the binding count is active time, the windows split at
    the idle gap; the first-to-last envelope, the gap included, is the other reading."""
    metrics = budget.metrics
    stage = {"id": "S5", "gate_commit": None, "main_thread": "transcripts"}
    ledger = {"sessions": [{"id": "main", "windows": [[stamp(0), stamp(1)],
                                                        [stamp(5), stamp(6)]]}]}
    monkeypatch.setattr(metrics, "load_stages", lambda root: [stage])
    monkeypatch.setattr(metrics, "stage_instants", lambda root, row: (at(1), None))
    monkeypatch.setattr(metrics, "stage_ledger", lambda root, row: ledger)
    binding, _ = metrics.counted_windows(tmp_path, stage)
    envelope, _ = metrics.counted_windows(tmp_path, stage, first_to_last=True)
    assert binding == {"main": [(at(5), at(6))]}
    assert envelope == {"main": [(at(1), at(6))]}
