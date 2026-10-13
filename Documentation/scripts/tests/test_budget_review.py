"""Regression cases required by the budget instrument's counting and ruling reviews."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
import test_units as tu
import test_units as TU
import test_metrics as TM
from test_units import units, project
from test_metrics import repo, metrics_module

def iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(seconds))


def committed(units, project: Path, monkeypatch, point: float, held: list[dict]) -> None:
    """HEAD holds both ledgers: the session ledger recorded at `point`, the unit ledger
    `held`."""
    docs = project / "Documentation"
    docs.mkdir()
    monkeypatch.setattr(units, "UNITS_FILE", docs / "agent-units.json")
    (docs / "agent-sessions.json").write_text(json.dumps({"recorded_at": iso(point),
                                                          "sessions": []}))
    units.UNITS_FILE.write_text(json.dumps({"schema": units.SCHEMA, "idle_gap_hours": 2.0,
                                            "units": held}))
    tu.git(project, "init", "-q")
    tu.git(project, "config", "user.email", "u@example.invalid")
    tu.git(project, "config", "user.name", "U")
    tu.git(project, "add", "Documentation")
    tu.git(project, "commit", "-q", "-m", "ship")


def test_a_quiet_transcript_is_judged_stale_as_of_the_point_not_now(units, project: Path,
                                                                     monkeypatch, capsys) -> None:
    """K43 (`now = as_of` dropped). A sub-agent with no run journal, waiting on its own
    tool call, was quiet 11.5 h at the recording point: in flight then (STALE_HOURS is
    12). Judged now, 12.5 h, it would be stale, taken to be finished, and fail."""
    point = time.time() - 3600.0
    committed(units, project, monkeypatch, point, [])
    nested = tu.harness_session(project) / "subagents"
    nested.mkdir()
    quiet = tu.transcript(nested, "aquiet", "2026-09-29T01:00:00Z", "2026-09-29T02:00:00Z",
                          name="agent-aquiet.jsonl", answered=False)
    tu.age(quiet, 12.5 * 60)
    tu.age(nested, 12.5 * 60)
    capsys.readouterr()
    assert units.command_audit(tu.audit_args(None, grace_minutes=10.0)) == 0
    out = capsys.readouterr().out
    assert "PENDING aquiet" in out and "its last turn is no final answer yet" in out


def test_growth_after_the_point_is_the_next_commit_s(units, project: Path, monkeypatch,
                                                     capsys) -> None:
    """K15 (the "after" skip in the growth check dropped). A unit recorded in the commit
    was messaged after the recording point and worked half an hour more. That growth is
    the next commit's to judge; judged here it fails GROWN over the limit."""
    point = time.time() - 3600.0
    held = {"id": "arec", "stage": "S5", "label": "reviewer", "role": "review",
            "started": "2026-09-29T08:00:00Z", "ended": "2026-09-29T08:30:00Z", "hours": 0.5,
            "source": "agent-arec.jsonl",
            "windows": [["2026-09-29T08:00:00.000Z", "2026-09-29T08:30:00.000Z"]], "gaps": []}
    committed(units, project, monkeypatch, point, [held])
    nested = tu.harness_session(project) / "subagents"
    nested.mkdir()
    path = tu.transcript(nested, "arec", "2026-09-29T08:00:00Z", "2026-09-29T08:30:00Z",
                         name="agent-arec.jsonl")
    tu.append(path, "arec", *[f"2026-09-29T09:{m:02d}:00Z" for m in range(0, 35, 5)])
    capsys.readouterr()
    assert units.command_audit(tu.audit_args(None, grace_minutes=10.0)) == 0
    assert "GROWN arec" not in capsys.readouterr().out


def test_a_run_record_written_after_the_point_does_not_end_the_run_as_of_it(
        units, project: Path, monkeypatch, capsys) -> None:
    """K13 (the run record's instant not compared with the point). The run's agent was
    quiet in a tool call at the recording point and the run ended after it (its record
    is stamped after the point): as of the point the agent was running."""
    point = time.time() - 3600.0
    committed(units, project, monkeypatch, point, [])
    session = tu.harness_session(project)
    run = session / "subagents" / "workflows" / "wf_late"
    agent = tu.workflow_agent(run, "aquiet", "build:beta", "2026-09-29T02:00:00Z",
                              "2026-09-29T02:10:00Z")
    for entry in [agent, *run.iterdir(), run]:
        tu.age(entry, 180)
    (session / "workflows").mkdir(exist_ok=True)
    (session / "workflows" / "wf_late.json").write_text(json.dumps(
        {"runId": "wf_late", "workflowName": "late", "status": "killed",
         "timestamp": iso(point + 600.0)}))
    capsys.readouterr()
    assert units.command_audit(tu.audit_args(None, grace_minutes=10.0)) == 0
    assert "PENDING aquiet" in capsys.readouterr().out


def stamp_of(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(seconds))


def test_pin_a_ship_gate_run_judges_units_now_when_only_the_session_ledger_moved(
        units, project: Path, capsys) -> None:
    """ship.py's step 0 records the session ledger; the unit ledger is still HEAD's when
    nobody recorded a unit since HEAD. That tree is being prepared, so a unit that
    finished after HEAD's recording point and was never recorded must fail the unit gate.
    Judged as HEAD instead (committed_instant comparing only the unit ledger), the unit is
    'written after the recording point' and PENDING, and the ship passes with it unrecorded:
    the unit gate could then never fail on a unit that finished since the last ship."""
    docs = project / "Documentation"
    docs.mkdir()
    units.UNITS_FILE = docs / "agent-units.json"
    head_point = time.time() - 5 * 3600.0
    (docs / "agent-sessions.json").write_text(json.dumps(
        {"recorded_at": stamp_of(head_point), "sessions": []}))
    units.UNITS_FILE.write_text(json.dumps({"schema": units.SCHEMA, "units": []}))
    TU.git(project, "init", "-q")
    TU.git(project, "config", "user.email", "u@example.invalid")
    TU.git(project, "config", "user.name", "U")
    TU.git(project, "add", "Documentation")
    TU.git(project, "commit", "-q", "-m", "the previous ship")
    session = TU.harness_session(project)
    (session / "subagents").mkdir()
    finished = TU.transcript(session / "subagents", "aforgot", stamp_of(time.time() - 3 * 3600),
                             stamp_of(time.time() - 2 * 3600), name="agent-aforgot.jsonl")
    TU.age(finished, 120)
    # Step 0 of the next ship: the session ledger recorded now; the unit ledger untouched.
    (docs / "agent-sessions.json").write_text(json.dumps(
        {"recorded_at": stamp_of(time.time() - 60), "sessions": []}))
    capsys.readouterr()
    assert units.harness.committed_instant(project) is None
    assert units.command_audit(TU.audit_args(None)) == 1
    out = capsys.readouterr().out
    assert "UNRECORDED_ROOT aforgot" in out and "judged as of" not in out


def test_pin_a_closed_transcript_stage_s_report_prints_both_figures(
        repo: Path, metrics_module, monkeypatch) -> None:
    """The rulings ask for both figures and both readings at every review, with the
    threshold stated. A G5 report generated once S5 is closed (a report of record
    reproduced after the gate, as G4's was at development_snapshot) takes the closed branch of
    gate_report.render; it prints all of them, and this holds it."""
    TM.transcript_repo(repo, TM.session("main", (6, 7)), close_s5=True)
    TM.write_sessions(repo, TM.session("main", (6, 9)))
    monkeypatch.setitem(sys.modules, "metrics", metrics_module)
    gate_report = TM.load_module(monkeypatch, "gate_report_closed_s5", TM.GATE_REPORT)
    report = gate_report.render(repo, "G5")
    assert "Spent: 0.50 h agent wall-hours (0.1%), final at gate" in report
    assert ("the transcript count (active time, each main session and each unit split at "
            "idle gaps over 2 h that its own work did not end") in report
    assert "), ledger recorded to " in report
    assert "First to last:        0.50 h spent (0.1 %), 0.50 h main thread" in report
    assert "Commit spacing:" in report and "by the commit-spacing method" in report
    assert "Transcript gaps:" in report and "over the 2 h idle-gap threshold" in report
    assert "Unit gaps:" in report and "inside units bridged by the unit's own work" in report


def test_pin_the_unit_ledger_gate_is_registered() -> None:
    """The session ledger gate's registration is pinned (test_sessions.py); the unit
    ledger gate's, the other half of the same budget, is pinned nowhere."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_all_gates

    assert ("unit ledger", ("tools/units.py", "audit", "--tasks-optional")) in run_all_gates.GATES
    assert ("session ledger", ("tools/sessions.py", "audit", "--transcripts-optional")) \
        in run_all_gates.GATES
