#!/usr/bin/env python3
"""Behaviour tests for the closing charge in metrics.py (its module docstring, "A gate's
closing hours"): the user's ruling of 2026-10-10 that G5's closing hours are charged to S5
and S7 starts at zero.

Each test builds a throwaway repository with test_metrics' helpers: S4 closed by commit
spacing, S5 counted from transcripts and closed at a gate whose ledger was recorded to 7 h,
the gate committed at 7.5 h, and the stage after it, S6 here, open. The closing commit,
the first after the gate, is made at 10 h; until it is made HEAD is the gate commit and
the closing commit is the working tree being prepared, as during its own gate run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_metrics as TM
from test_metrics import metrics_module, repo  # noqa: F401  (fixtures)

REPO_ROOT = Path(__file__).resolve().parents[3]
RECORD = "Documentation/decisions/2026-01-01-close-answers.md"
QUOTE = "Charge the closing hours to S5. S6 starts at zero."
CHARGE = {"record": RECORD, "quote": QUOTE}


def write_record(repo: Path, text: str | None = None) -> None:
    path = repo / RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else
                    "# The user's answers\n\n> 1. Close it.\n>\n> 2. " + QUOTE + "\n\nRecorded.\n",
                    encoding="utf-8")


def write_stages(repo: Path, gate4: str, start4: str, gate5: str | None,
                 charge: dict | None = CHARGE, **s4_extra) -> None:
    s5 = {"id": "S5", "title": "Presentation", "budget_hours": 400, "start_commit": None,
          "gate_commit": None if gate5 is None else gate5[:7], "main_thread": "transcripts"}
    if charge is not None:
        s5["closing_charge"] = charge
    stages = [{"id": "S4", "title": "Runtime", "budget_hours": 110, "start_commit": start4[:7],
               "gate_commit": gate4[:7], **s4_extra}, s5,
              {"id": "S6", "title": "Breadth", "budget_hours": 55, "start_commit": None,
               "gate_commit": None, "main_thread": "transcripts"}]
    TM.write_json(repo, "Documentation/stages.json", {"schema": "sa2-recomp-stages/v1",
                                                      "idle_gap_hours": 2.0, "stages": stages})


def gated(repo: Path, charge: dict | None = CHARGE) -> tuple[str, str, str]:
    """S4 closed at 6.5 h, S5 worked from 6 h and closed at its gate commit at 7.5 h, whose
    ledger holds the main session to 7 h. HEAD is the gate commit; stages.json, written
    after it, records the gate (the closing commit being prepared)."""
    start4 = TM.commit_central(repo, "S4 start", 5)
    TM.commit_central(repo, "S4 work", 6)
    gate4 = TM.commit_central(repo, "S4 gate", 6.5)
    write_record(repo)
    TM.write_sessions(repo, TM.session("main", (6, 7)), recorded_at=7.0)
    gate5 = TM.commit_central(repo, "S5 gate", 7.5)
    write_stages(repo, gate4, start4, gate5, charge)
    return start4, gate4, gate5


def s5(metrics_module, repo: Path) -> dict:
    return next(s for s in metrics_module.closed_stage_spend(repo) if s["stage"] == "S5")


def test_before_the_closing_commit_the_closed_stage_takes_everything_and_the_next_nothing(
        repo: Path, metrics_module) -> None:
    """The closing commit's own gate run: HEAD is the gate commit and the ledger was just
    recorded to 9 h. S5 is charged every window from its opening, its gate ledger's 7 h
    and the 2 h after it; S6, the stage the gate opens, is charged nothing and counted from
    the ledger's reach."""
    gated(repo)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    closed = s5(metrics_module, repo)
    assert closed["main_thread_hours"] == 2.5 and closed["spent_hours"] == 2.5
    assert (closed["ledger_rev"], closed["counted_to"]) == ("working tree", None)
    assert closed["charged_through"] is None and closed["closing_charge"] == RECORD
    info = metrics_module.budget_info(repo)
    assert info["active_stage"] == "S6"
    assert (info["main_thread_hours"], info["spent_hours"], info["percent"]) == (0.0, 0.0, 0.0)
    assert info["counted_from"] == "2026-01-01T18:00:00Z"        # 9 h, the ledger's reach
    assert info["method_spent_hours"] == 0.0
    assert info["start_commit"].endswith("closing commit, still being prepared")


def test_without_the_ruling_the_gate_run_and_the_closing_work_go_to_the_next_stage(
        repo: Path, metrics_module) -> None:
    """The control: the same ledgers with no closing charge keep the rule before the
    ruling, S5 frozen at its gate ledger and S6 charged everything after it."""
    gated(repo, charge=None)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    assert s5(metrics_module, repo)["main_thread_hours"] == 0.5
    info = metrics_module.budget_info(repo)
    assert info["main_thread_hours"] == 2.0 and info["counted_from"] == "2026-01-01T16:00:00Z"


def test_at_the_closing_commit_the_next_stage_reads_zero_and_the_figure_holds(
        repo: Path, metrics_module) -> None:
    """A clean checkout of the closing commit reads what its gate run read: S5's 2.5 h,
    charged through the closing commit at 10 h, and S6 at zero by every reading."""
    gated(repo)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    during_gate_run = s5(metrics_module, repo)["spent_hours"]
    closing = TM.commit_central(repo, "the closing commit", 10)
    closed = s5(metrics_module, repo)
    assert closed["spent_hours"] == during_gate_run == 2.5
    assert closed["charged_through"] == closing[:7]
    assert (closed["counted_to"], closed["ledger_rev"]) == ("2026-01-01T19:00:00Z",
                                                           "working tree")
    info = metrics_module.budget_info(repo)
    assert (info["main_thread_hours"], info["spent_hours"]) == (0.0, 0.0)
    assert info["first_to_last_spent_hours"] == 0.0 and info["method_spent_hours"] == 0.0
    assert info["counted_from"] == "2026-01-01T19:00:00Z"
    assert info["start_commit"] == f"after {closing[:7]}, the commit that closed gate {closed['end_commit']}"
    text = metrics_module.render_budget(info)
    assert (f"      charged through its closing commit {closing[:7]}, the user's ruling "
            f"({RECORD})\n") in text
    assert ("Closed stages (charged to their own gate commit, or under a closing charge to "
            "their closing commit, not to HEAD):\n") in text


def test_the_closing_gate_run_joins_the_closed_stage_at_the_next_recording_then_freezes(
        repo: Path, metrics_module) -> None:
    """The closing commit's gate run (9 h to 10 h) is in no ledger at the closing commit.
    The next ship's recording holds it, and S5 takes it there; S6 is charged from 10 h.
    From the first ledger committed after the closing commit and recorded past its instant,
    S5 moves no more, whatever a later recording says of the time before it."""
    gated(repo)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    TM.commit_central(repo, "the closing commit", 10)
    TM.write_sessions(repo, TM.session("main", (6, 12)), recorded_at=12.0)
    assert s5(metrics_module, repo)["main_thread_hours"] == 3.5
    assert metrics_module.budget_info(repo)["main_thread_hours"] == 2.0
    first = TM.commit_central(repo, "S6's first commit", 12.5)
    frozen = s5(metrics_module, repo)
    assert frozen["main_thread_hours"] == 3.5 and frozen["ledger_rev"] == f"commit {first}"
    # A later recording that reads the time before the closing commit differently.
    TM.write_sessions(repo, TM.session("main", (6, 8), (9, 14)), recorded_at=14.0)
    assert s5(metrics_module, repo) == frozen
    assert metrics_module.budget_info(repo)["main_thread_hours"] == 4.0      # 10 h .. 14 h


def test_the_closed_stage_s_interval_runs_to_its_closing_commit(repo: Path,
                                                                 metrics_module) -> None:
    """What tools/sessions.py's LABEL check and tools/units.py's stage hint read: S5's end is
    open while the closing commit is prepared, then the closing commit's instant, where S6
    starts; a unit of the closing work started after the gate is inside S5."""
    gated(repo)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    bounds = {s["id"]: s for s in metrics_module.stage_bounds(metrics_module.load_stages(repo))}
    assert metrics_module.stage_instants(repo, bounds["S5"])[1] is None
    assert metrics_module.stage_instants(repo, bounds["S6"])[0] == TM.hours_after_t0(9)
    TM.commit_central(repo, "the closing commit", 10)
    assert metrics_module.stage_instants(repo, bounds["S5"])[1] == TM.hours_after_t0(10)
    assert metrics_module.stage_instants(repo, bounds["S6"])[0] == TM.hours_after_t0(10)


def test_the_commit_spacing_reading_shares_the_boundary(repo: Path, metrics_module) -> None:
    """The commit-spacing figure printed beside the count ends S5 at the closing commit and
    starts S6 after it: the closing commit's 2.5 h gap is S5's, and S6 starts at zero."""
    gated(repo, charge=None)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    TM.commit_central(repo, "the closing commit", 9.5)
    assert metrics_module.budget_info(repo)["method_main_thread_hours"] == 2.0
    write_stages_charge = json.loads((repo / "Documentation/stages.json").read_text())
    write_stages_charge["stages"][1]["closing_charge"] = CHARGE
    TM.write_json(repo, "Documentation/stages.json", write_stages_charge)
    assert metrics_module.budget_info(repo)["method_main_thread_hours"] == 0.0
    assert s5(metrics_module, repo)["commits"] == 2              # the gate and the closing commit


@pytest.mark.parametrize(("change", "message"), [
    (lambda doc, repo: doc["stages"][0].update(closing_charge=CHARGE),
     "is only for a stage counted from transcripts"),
    (lambda doc, repo: doc["stages"][1].update(closing_charge={"record": RECORD}),
     "must be"),
    (lambda doc, repo: doc["stages"][1].update(
        closing_charge={"record": "Documentation/decisions/absent.md", "quote": QUOTE}),
     "which is no decision record"),
    (lambda doc, repo: doc["stages"][1].update(
        closing_charge={"record": RECORD, "quote": "Charge the closing hours."}),
     "do not name S5"),
    (lambda doc, repo: write_record(repo, "# Agent's note\n\n" + QUOTE + "\n"),
     "which no quoted passage"),
])
def test_the_charge_is_refused_unless_it_is_the_user_s_quoted_words(repo: Path, metrics_module,
                                                                    change, message) -> None:
    """The field moves where a closed stage ends, so a stage file carrying it without the
    user's quoted words naming the stage is refused, as an unquoted halt ruling is."""
    gated(repo)
    TM.write_sessions(repo, TM.session("main", (6, 9)), recorded_at=9.0)
    doc = json.loads((repo / "Documentation/stages.json").read_text())
    change(doc, repo)
    TM.write_json(repo, "Documentation/stages.json", doc)
    with pytest.raises(metrics_module.MalformedInput, match=message):
        metrics_module.load_stages(repo)


def test_the_repository_s_charge_is_the_user_s_answer_on_s5() -> None:
    """The repository's own stage file: S5 carries the charge, citing the user's answers of
    2026-10-10, and the real reader accepts it (load_stages refuses the field otherwise)."""
    import importlib.util
    import sys

    sys.path.insert(0, str(REPO_ROOT / "Documentation" / "scripts"))
    spec = importlib.util.spec_from_file_location("metrics_live_closing",
                                                  REPO_ROOT / "Documentation/scripts/metrics.py")
    live = importlib.util.module_from_spec(spec)
    sys.modules["metrics_live_closing"] = live
    spec.loader.exec_module(live)
    stages = {s["id"]: s for s in live.load_stages(REPO_ROOT)}
    assert stages["S5"][live.CLOSING_CHARGE_FIELD] == {
        "record": "Documentation/decisions/2026-10-10-G5-close-answers.md",
        "quote": "Charge G5's closing hours to S5. S7 starts at zero."}
    assert not [s for s, entry in stages.items()
                if s != "S5" and live.CLOSING_CHARGE_FIELD in entry]
