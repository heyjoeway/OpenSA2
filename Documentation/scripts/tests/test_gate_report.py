"""Standing question 6 at G5: S5's rate hook answers it, and a stage without one does not.

The defect this exists for
--------------------------
Design 4.7 asks question 6 ("Is the rate over this stage consistent with the budget of
the next?") at every gate, answered with numbers, and the verdict fails on any question
that reads "not measured". S5's entry in Documentation/stages.json had no rate_unit and
no remaining_units, so G5's Rate line, its Projection and question 6 all read not
measured, and the verdict could not pass whatever the exit criteria said
(agents-journals/gates/G5-400-active-report.txt).

S5's entry now names M7 as its unit, the one clause of S5's exit with an n/d pair, as
S4's names M4's (stages.json's rate_note says why). These tests run the report the way
the gate does: a repository with S5 open, M7's inputs and METRICS.md at G4's gate
commit, rendered by gate_report.render. They use S5's hook as the real stages.json
writes it, so the hook the tests pass with is the one the gate reads.

The hook reaches every caller of metrics.budget_info, not only the gate: one that passes
no metrics, as tools/s5/host2_estimate.py does to read the continuation, gets S5's rate
as not measured rather than an exception, and a hook naming no metric still fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_report as G  # noqa: E402
import metrics as M  # noqa: E402
import test_metrics as TM  # noqa: E402
from test_metrics import metrics_module, repo  # noqa: E402,F401  (fixtures)

REPO_ROOT = Path(__file__).resolve().parents[3]
UNIT = "used device methods held"
S6 = {"id": "S6", "title": "Breadth", "budget_hours": None, "start_commit": None,
      "gate_commit": None, "main_thread": "transcripts"}
# S7 at the user's 24 hours of 2026-10-10 (Documentation/decisions/2026-10-10-G5-user-answers.md);
# its plan stays the design heading's 40 h (DESIGN below), which question 6 prices from.
S7 = {"id": "S7", "title": "Portability", "budget_hours": 24, "start_commit": None,
      "gate_commit": None, "main_thread": "transcripts"}
DESIGN = ("### S5. Presentation (budget: 120 h)\n\n"
          "### S6. Breadth (budget: per-stage rate, measured; no fixed ceiling)\n\n"
          "### S7. Portability (budget: 40 h)\n")


def real_stage(stage_id: str) -> dict:
    doc = json.loads((REPO_ROOT / "Documentation/stages.json").read_text(encoding="utf-8"))
    return next(s for s in doc["stages"] if s["id"] == stage_id)


def s5_hook() -> dict:
    stage = real_stage("S5")
    return {key: stage[key] for key in ("rate_unit", "remaining_units")}


def metrics_table(m7: str) -> str:
    return ("| Metric | Value | Denominator source | Status |\n"
            "|---|---|---|---|\n"
            f"| M7 device | {m7} | analyzer call-site scan | measured |\n")


def m7_repo(repo: Path, *, hook: bool | dict, start: str, called: int, implemented: int,
            held: int | None = None, spent: float = 40.0, after: tuple[dict, ...] = (S6,)) -> None:
    """S5 open after G4's gate, with M7 reading `start` in METRICS.md at that gate and
    `implemented` (or, with `held`, the three-way numerator's held methods) of `called`
    now, and `spent` hours of S5 in the session ledger. `after` follows S5, in order.
    `hook` is True for S5's real hook, False for none, or the hook to write instead."""
    (repo / "METRICS.md").write_text(metrics_table(start), encoding="utf-8")
    extra = hook if isinstance(hook, dict) else s5_hook() if hook else None
    TM.transcript_repo(repo, s5_extra=extra)
    doc = json.loads((repo / "Documentation/stages.json").read_text(encoding="utf-8"))
    doc["stages"].extend(after)
    TM.write_json(repo, "Documentation/stages.json", doc)
    names = [f"IDirect3DDevice9::Method{i}" for i in range(called)]
    scan: dict = {"called": names}
    if held is not None:
        scan["three_way_numerator"] = {"measured": True, "held_methods": names[:held],
                                       "not_held": names[held:]}
    TM.write_json(repo, "Documentation/receipts/d3d9-methods.json", scan)
    TM.write_json(repo, "private/generated/device-status.json",
                  {"implemented": names[:implemented]})
    (repo / "Documentation/SA2_RECOMP_DESIGN.md").write_text(DESIGN, encoding="utf-8")
    # S5 opens at G4's gate, 6.5 h after T0 (TM.transcript_repo).
    TM.write_sessions(repo, TM.session("main", (6.5, 6.5 + spent)))


def render(repo: Path, monkeypatch, metrics_module) -> dict[str, str]:  # noqa: F811
    """The G5 report, keyed by the lines question 6 is read from."""
    monkeypatch.setitem(sys.modules, "metrics", metrics_module)
    gate_report = TM.load_module(monkeypatch, "gate_report_q6", TM.GATE_REPORT)
    report = gate_report.render(repo, "G5")
    lines = report.splitlines()
    pick = {"Rate:": "rate", "Projection:": "projection", "    6. ": "q6", "Verdict:": "verdict",
            "    M7 device: ": "m7"}
    out = {}
    for text in lines:
        for start, key in pick.items():
            if text.startswith(start):
                out[key] = text[len(start):].strip() if key in ("q6", "m7") else \
                    text[G.LABEL:]
    return out


def test_the_real_s5_entry_names_m7_and_says_why() -> None:
    """The hook these tests pass with is S5's own; a rate_note records the choice."""
    assert s5_hook() == {"rate_unit": UNIT, "remaining_units": "M7"}
    note = real_stage("S5")["rate_note"]
    assert "Of the four only M7's has an n/d pair" in note
    assert M.load_stages(REPO_ROOT)  # the real stage file still loads


def test_the_m7_row_opens_with_its_ratio() -> None:
    """The hook takes the first n/d pair in M7's row; the row must open with its ratio,
    or the rate would count some other pair the row prints."""
    row = M.parse_table((REPO_ROOT / "METRICS.md").read_text(encoding="utf-8"))["M7 device"]
    pair = M.units_pair(row, None)
    assert pair is not None, row
    done, total = pair
    assert row.startswith(f"{done}/{total} (")


def test_a_stage_with_the_hook_answers_question_6(repo: Path, monkeypatch,  # noqa: F811
                                                  metrics_module) -> None:  # noqa: F811
    """S5 with M7 as its unit: one method more than at G4 in 40 h is 0.025 an hour, and
    the five left take 200 h. S6 has no ceiling, so that is consistent, and question 6
    is no longer among the questions the verdict says were not measured. The Rate line
    prints the figure the Projection line divides by."""
    m7_repo(repo, hook=True, start="2/5 (40.0%)", called=8, implemented=3)
    out = render(repo, monkeypatch, metrics_module)
    assert out["m7"].startswith("3/8 (37.5%) | 2/5 (40.0%) | +1")
    assert out["rate"] == (f"0.0250 {UNIT}/h over S5 (1 {UNIT} in 40.00 h; M7 device 2 -> 3 "
                           "of 8, 5 remaining)")
    assert out["projection"] == f"5 {UNIT} remaining / 0.0250 {UNIT}/h = 200.0 h"
    assert out["q6"] == (f"rate 0.0250 {UNIT}/h over S5; projection 5 {UNIT} remaining / "
                         f"0.0250 {UNIT}/h = 200.0 h; next stage S6 has no fixed ceiling: "
                         "consistent")
    # M1 to M6 have no inputs here and question 7 no journal; 6 is answered.
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 7")


def test_a_stage_without_the_hook_still_reads_not_measured(repo: Path, monkeypatch,  # noqa: F811
                                                           metrics_module) -> None:  # noqa: F811
    """The same repository with no rate_unit: nothing to count, so question 6 is not
    measured and the verdict names it."""
    m7_repo(repo, hook=False, start="2/5 (40.0%)", called=8, implemented=3)
    out = render(repo, monkeypatch, metrics_module)
    assert out["rate"] == ("not measured (stage S5 defines no unit; add rate_unit to its entry "
                           "in Documentation/stages.json)")
    assert out["projection"] == "not available: S5 defines no rate_unit in Documentation/stages.json"
    assert out["q6"] == ("not measured (rate not measured (stage S5 defines no unit; add "
                         "rate_unit to its entry in Documentation/stages.json); projection not "
                         "available: S5 defines no rate_unit in Documentation/stages.json; next "
                         "stage S6 has no fixed ceiling)")
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 6, 7")


def test_a_numerator_that_falls_below_g4s_leaves_question_6_unanswered(
        repo: Path, monkeypatch, metrics_module) -> None:  # noqa: F811
    """M7's numerator was the device's at G4; held three ways it is never more. A held
    count under the start is a rate below zero, which projects nothing: question 6 stays
    not measured rather than reading the fall as progress."""
    m7_repo(repo, hook=True, start="4/5 (80.0%)", called=8, implemented=6, held=3)
    out = render(repo, monkeypatch, metrics_module)
    assert out["m7"].startswith("3/8 (37.5%) | 4/5 (80.0%) | -1")
    assert out["projection"] == (f"not available: rate is -0.0250 {UNIT}/h with 5 {UNIT} "
                                 "remaining")
    assert out["q6"].startswith("not measured (rate ")
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 6, 7")


@pytest.mark.parametrize("called, remaining", [(8, 5), (50, 47)])
def test_no_movement_since_g4_projects_nothing(repo: Path, monkeypatch, metrics_module,  # noqa: F811
                                               called: int, remaining: int) -> None:
    """M7's numerator where G4 left it: a rate of zero with methods left divides by
    nothing, so the projection is not available and question 6 is not measured. A
    denominator that grows (the census the user ruled) adds methods left, not progress."""
    m7_repo(repo, hook=True, start="3/5 (60.0%)", called=called, implemented=3)
    out = render(repo, monkeypatch, metrics_module)
    assert out["projection"] == (f"not available: rate is 0.00 {UNIT}/h with {remaining} "
                                 f"{UNIT} remaining")
    assert out["q6"].startswith("not measured (rate ")
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 6, 7")


def test_a_caller_that_passes_no_metrics_reads_s5s_rate_as_not_measured(
        repo: Path, metrics_module) -> None:  # noqa: F811
    """tools/s5/host2_estimate.py reads the continuation through budget_info with no
    metrics. S5's hook names M7, which that caller did not collect: the rate reads not
    measured, saying so, and the rest of the budget block is what it is without a hook."""
    m7_repo(repo, hook=True, start="2/5 (40.0%)", called=8, implemented=3)
    info = metrics_module.budget_info(repo, [])
    assert info["rate"] == "not measured (M7 is not among the metrics this caller passed)"
    assert (info["rate_per_hour"], info["units_remaining"]) == (None, None)
    assert info["active_stage"] == "S5" and info["spent_hours"] == 40.0


def test_a_hook_that_names_no_metric_still_fails(repo: Path, metrics_module) -> None:  # noqa: F811
    """A remaining_units id outside the design's metrics is a malformed stage file, whether
    or not the caller passed metrics."""
    m7_repo(repo, hook={"rate_unit": UNIT, "remaining_units": "M9"}, start="2/5 (40.0%)",
            called=8, implemented=3)
    for passed in ([], None):
        with pytest.raises(metrics_module.MalformedInput, match="unknown metric 'M9'"):
            metrics_module.budget_info(repo, passed)


def test_m7_at_100_percent_prices_the_next_stage_from_s5s_hours(
        repo: Path, monkeypatch, metrics_module) -> None:  # noqa: F811
    """Nothing remains, so the unit rate prices nothing next; question 6 is answered from
    S5's hours per planned hour (the design's 120 h). S6 has no ceiling: consistent."""
    m7_repo(repo, hook=True, start="2/5 (40.0%)", called=8, implemented=8)
    out = render(repo, monkeypatch, metrics_module)
    assert out["q6"] == (f"S5 has no {UNIT} remaining, so its unit rate prices nothing next; "
                         "next stage S6 has no fixed ceiling: consistent")
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 7")


@pytest.mark.parametrize("after, ending", [
    ((S7, S6), "200.0 h; next stage S7 budget 24h: inconsistent"),
    ((S6, S7), "200.0 h; next stage S6 has no fixed ceiling: consistent"),
])
def test_g5_prices_the_stage_listed_after_s5(repo: Path, monkeypatch,  # noqa: F811
                                             metrics_module, after, ending) -> None:  # noqa: F811
    """The user's order of 2026-10-07 (G7 before G6) lists S7 after S5. Question 6 prices
    the stage G5 opens, the next entry in the list, not S(n+1) by id: until 2026-10-10 it
    read S6 with S7 listed first. 200 h left against S7's 24 h reads inconsistent; the
    same numbers with S6 listed first read against S6, which has no ceiling."""
    m7_repo(repo, hook=True, start="2/5 (40.0%)", called=8, implemented=3, after=after)
    out = render(repo, monkeypatch, metrics_module)
    assert out["q6"] == (f"rate 0.0250 {UNIT}/h over S5; projection 5 {UNIT} remaining / "
                         f"0.0250 {UNIT}/h = {ending}")
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 7")


def test_a_finished_s5_prices_s7_on_both_bases(repo: Path, monkeypatch,  # noqa: F811
                                               metrics_module) -> None:  # noqa: F811
    """M7 at 100 %: S5's own units are done, so question 6 prices S7's planned 40 h (the
    design heading, not the 24 h ceiling) at S5's hours per planned hour and per ceiling
    hour, as G5's report of record does. 600 h against 120 h planned and a 400 h ceiling
    is 200 h and 60 h, both over S7's 24 h: inconsistent, and measured."""
    m7_repo(repo, hook=True, start="2/5 (40.0%)", called=8, implemented=8, spent=600.0,
            after=(S7, S6))
    out = render(repo, monkeypatch, metrics_module)
    assert out["q6"] == (
        f"S5 has no {UNIT} remaining, so its unit rate prices nothing next; S5 spent 600.00 h "
        "against 120 h planned (5.00 h per planned hour) and against its 400 h ceiling (1.50 h "
        "per ceiling hour), so S7's 40 h planned is 200.0 h at S5's measured rate on the "
        "plan's basis and 60.0 h on the ceiling's; next stage S7 budget 24h: inconsistent "
        "(proposed, on the two bases: S7 budget 200h or 60h; the S7 figure is the user's to "
        "set)")
    assert out["verdict"].endswith("standing questions not measured: 1, 2, 3, 4, 5, 7")


def g7_repo(repo: Path) -> str:
    """S5 closed at its gate with S7 open after it and S6 after S7, the real list's order;
    a residue the gate's tree held and one added since. Returns S5's gate commit."""
    residues = repo / "Documentation/receipts/residues"
    residues.mkdir(parents=True)
    (residues / "old.json").write_text("{}")
    (repo / "METRICS.md").write_text(metrics_table("94/94 (100.0%)"), encoding="utf-8")
    _, _, gate5 = TM.transcript_repo(repo, TM.session("main", (6, 7)), close_s5=True)
    doc = json.loads((repo / "Documentation/stages.json").read_text(encoding="utf-8"))
    s6 = doc["stages"].pop()
    assert s6["id"] == "S6"
    doc["stages"] += [S7, s6]
    TM.write_json(repo, "Documentation/stages.json", doc)
    (residues / "new.json").write_text("{}")
    (repo / "Documentation/SA2_RECOMP_DESIGN.md").write_text(DESIGN, encoding="utf-8")
    TM.write_sessions(repo, TM.session("main", (6, 9)))
    return gate5


def test_g7_reads_g5_as_its_previous_gate_and_prices_s6(repo: Path, monkeypatch,  # noqa: F811
                                                        metrics_module) -> None:  # noqa: F811
    """G7 follows G5: its previous review and its residues added are read at S5's gate
    commit, not at S6's, which has none, and the stage it opens is S6."""
    gate5 = g7_repo(repo)
    monkeypatch.setitem(sys.modules, "metrics", metrics_module)
    gate_report = TM.load_module(monkeypatch, "gate_report_g7", TM.GATE_REPORT)
    report = gate_report.render(repo, "G7")
    assert "Stage: S7 Portability    Budget: 24h    Spent: " in report
    assert (f"Residues added:       1 (since G5's gate commit {gate5[:7]})\n"
            "    Documentation/receipts/residues/new.json\n"
            "Residues carried:     1, the previous gate's tree held them\n") in report
    assert "    M7 device: not measured" in report and " | 94/94 (100.0%) | " in report
    q6 = next(l for l in report.splitlines() if l.startswith("    6. "))
    assert q6.endswith("next stage S6 has no fixed ceiling)"), q6


def test_g6_reads_g7_as_its_previous_gate(repo: Path, monkeypatch,  # noqa: F811
                                          metrics_module) -> None:  # noqa: F811
    """G6 follows G7, so with S7 still open G6 has no previous gate commit to count residues
    against (by id it would have counted against G5's), and nothing follows S6 here."""
    g7_repo(repo)
    monkeypatch.setitem(sys.modules, "metrics", metrics_module)
    gate_report = TM.load_module(monkeypatch, "gate_report_g6", TM.GATE_REPORT)
    report = gate_report.render(repo, "G6")
    assert ("Residues added:       0 (not measured: S7 records no gate commit)\n"
            "Residues carried:     2, not counted as added\n") in report
    assert "    M7 device: not measured" in report and " | none | n/a (no previous review)" in report
    q6 = next(l for l in report.splitlines() if l.startswith("    6. "))
    assert q6.endswith("no stage follows S6)"), q6


@pytest.mark.parametrize("rate, printed", [
    (1.7142857, "1.71"), (0.47, "0.47"), (0.1, "0.10"), (0.0, "0.00"), (-0.5, "-0.50"),
    (0.0424953, "0.0425"), (0.025, "0.0250"), (-0.0021, "-0.00210"), (0.09999, "0.1000"),
    (5e-05, "0.0000500"),
])
def test_the_rate_prints_three_figures_below_a_tenth(rate: float, printed: str) -> None:
    """Two decimals print S5's 0.0425 as 0.04, beside "12 remaining / 0.04 = 282.4 h",
    a division that gives 300; below 0.1 the figure keeps three significant figures, in
    fixed notation however small, and at and above 0.1 it prints as every earlier gate's."""
    assert M.rate_figure(rate) == printed


def test_below_a_tenth_the_printed_divisor_gives_the_printed_hours() -> None:
    """Every rate below 0.1 prints within half a percent of itself, so the hours a
    reader divides from the printed figure are the hours printed beside it; S5's own
    rate, 24 methods in 564.77 h with 12 left, reads 282.4 h either way."""
    rates = [10 ** (-6 + k / 50) for k in range(250)]
    assert all(abs(float(M.rate_figure(r)) - r) <= 0.005 * r for r in rates)
    rate = 24 / 564.77
    assert f"{12 / rate:.1f}" == f"{12 / float(M.rate_figure(rate)):.1f}" == "282.4"
