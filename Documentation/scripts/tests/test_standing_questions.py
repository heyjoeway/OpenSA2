"""A structural zero does not answer the standing question of the stage that owes it.

The defect this exists for
--------------------------
On 2026-09-22 `METRICS.md` read

    | M5 replays | 0 replays | harness level 2/3 | measured (stage not started) |
    | M6 stages  | booting 0/66 (0.0%); replay complete 0/66 (0.0%) | ... | measured (stage not started) |

`Metric.measured` is True on that status -- `stage_not_started` is a separate
flag -- and `gate_report.py`'s section 4.7 backstop only failed a question
whose metric was `not measured`. So at G4, questions 4 and 5 were answered,
every exit criterion then on file was met, and **the verdict would have read
PASS with no replay run and no stage booting**, closable under the standing
authorisation.

"The stage has not started" is not an answer to "is the stage finished?". It is
the question restated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_report as G  # noqa: E402
import metrics as M  # noqa: E402


def metric(mid: str, measured: bool = True, not_started: bool = False) -> M.Metric:
    return M.Metric(mid, f"{mid} row", "0 of everything", "x", measured, [],
                    stage_not_started=not_started)


def current(**flags: bool) -> dict[str, M.Metric]:
    out = {}
    for mid in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"):
        out[mid] = metric(mid, not_started=flags.get(mid, False))
    return out


def ask(cur: dict[str, M.Metric], stage_id: str) -> dict[str, bool]:
    qs = G.standing_questions(cur, {"id": stage_id}, None, {}, ("x", None), ("y", True))
    return {n: measured for n, _, measured in qs}


def test_structural_zero_unanswers_its_own_stages_question() -> None:
    answered = ask(current(M5=True, M6=True), "S4")
    assert answered["4"] is False, "M5 is S4's to produce; a structural zero is not an answer"
    assert answered["5"] is False, "M6 is S4's to produce; a structural zero is not an answer"


def test_the_same_zero_does_not_fail_an_earlier_gate() -> None:
    """At G3, S4 has not run and M5/M6 are not S3's to produce. Failing G3 for
    them would make every gate wait for every later stage."""
    answered = ask(current(M5=True, M6=True), "S3")
    assert answered["4"] is True
    assert answered["5"] is True


def test_a_measured_metric_still_answers() -> None:
    assert ask(current(), "S4")["4"] is True


def test_not_measured_still_fails_as_before() -> None:
    cur = current()
    cur["M5"] = metric("M5", measured=False)
    assert ask(cur, "S4")["4"] is False


@pytest.mark.parametrize("stage,owed", sorted(G.PRODUCED_BY.items()))
def test_every_mapped_metric_is_a_real_metric(stage: str, owed: tuple) -> None:
    known = {m.id for m in M.collect(Path(__file__).resolve().parents[3])}
    for mid in owed:
        assert mid in known, f"{stage} is mapped to {mid}, which metrics.py does not emit"


def test_removing_the_rule_lets_the_zero_through() -> None:
    """The falsification: with the map emptied, the 2026-09-22 state passes
    again. If this ever stops holding, the rule has stopped doing anything."""
    saved = G.PRODUCED_BY
    try:
        G.PRODUCED_BY = {}
        assert ask(current(M5=True, M6=True), "S4")["4"] is True
    finally:
        G.PRODUCED_BY = saved
    assert ask(current(M5=True, M6=True), "S4")["4"] is False


# ------------------------------------------------------------ question 6 with nothing left
#
# The G4 interrogation, Reviewer A's finding 1: S4's rate unit was M4's replay-reachable
# imports, 124 of 124 at the review, so the projection was 0 h whatever the rate and any
# next budget read "consistent". Its fold made the question read "not measured" there,
# and the fold's review (Reviewer A's finding 1) showed that fails every gate whose
# stage met its exit criteria. The question is answered as the user asked at G4: the
# next stage's planned hours at this stage's measured hours per planned hour, each
# stage's plan the design's stage heading (gate_report.planned).

S4 = {"id": "S4", "rate_unit": "imports", "budget_hours": 110, "design_budget_hours": 80}
S5 = {"id": "S5", "budget_hours": 120, "design_budget_hours": 120}


def q6(remaining: int, hours: float, next_stage: dict | None = S5,
       stage: dict = S4) -> tuple[str, bool]:
    info = {"active_stage": "S4", "rate_per_hour": 0.47, "rate": "0.47 imports/h over S4",
            "units_remaining": remaining, "spent_hours": 265.78}
    projected = (f"{remaining} imports remaining / 0.47 imports/h = {hours:.1f} h", hours)
    qs = G.standing_questions(current(), stage, next_stage, info, projected, ("y", True))
    (_, text, measured), = [q for q in qs if q[0] == "6"]
    return text, measured


def test_question_6_with_no_units_remaining_prices_the_next_plan_at_the_measured_rate() -> None:
    """With the stage's ceiling at its plan there is one basis, printed as before."""
    text, measured = q6(0, 0.0, stage=dict(S4, budget_hours=80))
    assert (text, measured) == (
        "S4 has no imports remaining, so its unit rate prices nothing next; S4 spent 265.78 h "
        "against 80 h planned (3.32 h per planned hour), so S5's 120 h planned is 398.7 h at "
        "S4's measured rate; next stage S5 budget 120h: inconsistent (proposed: S5 budget "
        "399h, the user's to set)", True)
    # measured, so a finished stage's gate can pass; inconsistent is the user's to rule
    assert G.verdict(4, [(True, "c", "[met] c")], [("6", text, measured)]).startswith("PASS")
    # the ceiling the user sets from the proposal reads consistent, and does not move it
    raised = q6(0, 0.0, dict(S5, budget_hours=399), stage=dict(S4, budget_hours=80))[0]
    assert "is 398.7 h" in raised and raised.endswith("next stage S5 budget 399h: consistent")
    assert q6(0, 0.0, dict(S5, budget_hours=398), stage=dict(S4, budget_hours=80))[0].endswith(
        "(proposed: S5 budget 399h, the user's to set)")
    # a stage with no ceiling has one basis too
    assert q6(0, 0.0, stage=dict(S4, budget_hours=None))[0] == text


def test_question_6_prints_both_bases_when_the_ceiling_is_not_the_plan() -> None:
    """G4's second interrogation, Reviewer D's finding 4: S4 ran under the 110 h ceiling
    the user set, and the answer gave only the 80 h plan's basis, 37 % above the other.
    Both are printed, each times the next stage's plan, and the S5 figure is the user's."""
    text, measured = q6(0, 0.0)
    assert (text, measured) == (
        "S4 has no imports remaining, so its unit rate prices nothing next; S4 spent 265.78 h "
        "against 80 h planned (3.32 h per planned hour) and against its 110 h ceiling (2.42 h "
        "per ceiling hour), so S5's 120 h planned is 398.7 h at S4's measured rate on the "
        "plan's basis and 289.9 h on the ceiling's; next stage S5 budget 120h: inconsistent "
        "(proposed, on the two bases: S5 budget 399h or 290h; the S5 figure is the user's to "
        "set)", True)
    assert G.verdict(4, [(True, "c", "[met] c")], [("6", text, measured)]).startswith("PASS")
    # the plan's basis does not move with S4's ceiling; the ceiling's does
    moved = q6(0, 0.0, stage=dict(S4, budget_hours=165))[0]
    assert "is 398.7 h at S4's measured rate on the plan's basis and 193.3 h" in moved
    # consistent only when the next ceiling covers both bases
    assert q6(0, 0.0, dict(S5, budget_hours=399))[0].endswith("budget 399h: consistent")
    assert q6(0, 0.0, dict(S5, budget_hours=300))[0].endswith(
        "(proposed, on the two bases: S5 budget 399h or 290h; the S5 figure is the user's to "
        "set)")
    # a ceiling below the plan makes the ceiling's basis the larger, and it still has to be met
    lowered = q6(0, 0.0, dict(S5, budget_hours=399), stage=dict(S4, budget_hours=60))[0]
    assert "and 531.6 h on the ceiling's" in lowered and "inconsistent" in lowered


def test_question_6_with_no_planned_hours_is_not_measured() -> None:
    text, measured = q6(0, 0.0, S5, {"id": "S4", "rate_unit": "imports", "budget_hours": 110,
                                     "design_budget_hours": None})
    assert measured is False and text.startswith("not measured (S4 has no imports remaining")
    text, measured = q6(0, 0.0, dict(S5, design_budget_hours=None))
    assert measured is False and "S5 has no planned hours to price" in text
    # a next stage with no ceiling has nothing to exceed
    assert q6(0, 0.0, dict(S5, budget_hours=None, design_budget_hours=None)) == (
        "S4 has no imports remaining, so its unit rate prices nothing next; next stage S5 has "
        "no fixed ceiling: consistent", True)


def test_a_stage_s_planned_hours_are_the_design_s_stage_heading() -> None:
    """The plan is read from the design (check_kill_criteria.design_budgets), not copied:
    S4's heading says 80 h where stages.json's ceiling is 110 h by the user's ruling,
    S5's says 120 h, and S6's heading gives no figure."""
    root = Path(__file__).resolve().parents[3]
    stages = {s["id"]: s for s in M.load_stages(root)}
    assert G.planned(root, stages["S4"])["design_budget_hours"] == 80
    assert G.planned(root, stages["S4"])["budget_hours"] == stages["S4"]["budget_hours"]
    assert G.planned(root, stages["S5"])["design_budget_hours"] == 120
    assert G.planned(root, stages["S6"])["design_budget_hours"] is None
    assert G.planned(root, None) is None


def test_question_6_with_units_remaining_still_projects() -> None:
    assert q6(47, 100.0) == ("rate 0.47 imports/h over S4; projection 47 imports remaining / "
                             "0.47 imports/h = 100.0 h; next stage S5 budget 120h: consistent",
                             True)
    assert q6(94, 200.0)[0].endswith("next stage S5 budget 120h: inconsistent")


# ------------------------------------------------------------ question 7 reads the findings
#
# G4's second interrogation, Reviewer D's finding 2: the Interrogation line counted
# reviewers and required an "### Act on" heading, and read nothing about whether the
# findings were folded, so G4's report read PASS with two of its own items open.

JOURNAL = """# G4 gate review

## Interrogation (design section 4.5)

### Reviewers

- Reviewer A (one): measurement.
- Reviewer B (two): the comparer.

### Act on

1. [folded] **A finding.** Folded by the fold.
   - a nested note is part of its item, not an item
2. [the user's] **A question only the user may settle.**
3. {third} **Another finding.**

Prose after the list is no item.

### Reviewers (round two)

- Reviewer C (three): against.

### Act on (round two)

- [folded] The round's finding.

## Report
"""


def journal_repo(tmp_path: Path, third: str) -> Path:
    (tmp_path / "agents-journals/gates").mkdir(parents=True)
    (tmp_path / "agents-journals/gates/2026-01-01-g4.md").write_text(
        JOURNAL.format(third=third), encoding="utf-8")
    return tmp_path


def test_question_7_counts_the_findings_and_the_open_ones(tmp_path: Path) -> None:
    root = journal_repo(tmp_path, "[folded]")
    text, measured, still_open = G.interrogation_record(root, 4)
    assert (text, measured, still_open) == (
        "interrogation recorded in agents-journals/gates/2026-01-01-g4.md: 3 reviewers; "
        "4 findings, 0 open, 1 the user's", True, 1)
    # the user's finding is unsettled: under the standing authorisation a PASS would let
    # the agent close the gate over it (AGENTS.md rule 10)
    assert G.verdict(4, [(True, "c", "[met] c")], [("7", text, measured)], still_open) == (
        "FAIL: interrogation findings open or the user's to settle: 1 (question 7)")
    settled = journal_repo(tmp_path / "settled", "[folded]")
    path = settled / "agents-journals/gates/2026-01-01-g4.md"
    path.write_text(path.read_text().replace("[the user's]", "[folded]"))
    text, measured, still_open = G.interrogation_record(settled, 4)
    assert text.endswith("4 findings, 0 open") and still_open == 0
    verdict = G.verdict(4, [(True, "c", "[met] c")], [("7", text, measured)], still_open)
    assert verdict.startswith("PASS") and "no interrogation finding open" in verdict


def test_an_open_finding_fails_the_verdict(tmp_path: Path) -> None:
    root = journal_repo(tmp_path, "[open]")
    text, measured, still_open = G.interrogation_record(root, 4)
    assert text.endswith("4 findings, 1 open, 1 the user's") and measured and still_open == 2
    assert G.verdict(4, [(True, "c", "[met] c")], [("7", text, measured)], still_open) == (
        "FAIL: interrogation findings open or the user's to settle: 2 (question 7)")


@pytest.mark.parametrize("third", [" 3. [open] **Another finding.**",
                                   "  - [open] **Another finding.**",
                                   "+ [open] **Another finding.**",
                                   "* [open] **Another finding.**"])
def test_a_top_level_item_in_any_commonmark_form_is_a_finding(tmp_path: Path,
                                                              third: str) -> None:
    """A list item with up to three spaces before its marker, or a `+` or `*` bullet, is
    a top-level item as CommonMark renders it, and cannot leave the count by its form."""
    (tmp_path / "agents-journals/gates").mkdir(parents=True)
    (tmp_path / "agents-journals/gates/2026-01-01-g4.md").write_text(
        JOURNAL.format(third="[folded]").replace("3. [folded] **Another finding.**", third),
        encoding="utf-8")
    text, _, still_open = G.interrogation_record(tmp_path, 4)
    assert "4 findings, 1 open, 1 the user's" in text and still_open == 2, text


def test_a_finding_with_no_status_is_open(tmp_path: Path) -> None:
    """A finding cannot pass by being left unmarked, and a status typed inside the
    text instead of opening the item is no status."""
    for third in ("", "**Folded [folded]**"):
        root = journal_repo(tmp_path / str(len(third)), third)
        text, measured, still_open = G.interrogation_record(root, 4)
        assert text.endswith("4 findings, 1 open (1 with no status, read as open), "
                             "1 the user's"), text
        assert still_open == 2
        assert G.verdict(4, [], [("7", text, measured)], still_open).startswith("FAIL")


def test_g0_is_exempt_from_question_7_s_open_findings() -> None:
    """G0 is exempt from 4.7, question 7 included, as before."""
    assert G.verdict(0, [(True, "c", "[met] c")], [("7", "x", True)], 2).startswith("PASS")


# ------------------------------------------------------------ the gap figure

def gap_repo(tmp_path: Path, units: list[dict]) -> Path:
    """S0 active from its first commit: commits at 0, 1, 4.5 and 10 h, so two gaps
    exceed the 2 h session gap (3.5 h and 5.5 h), each charged 0.25 h."""
    import json
    import os
    import subprocess
    from datetime import datetime, timedelta, timezone
    root = tmp_path
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    t0 = datetime(2026, 9, 20, tzinfo=timezone.utc)
    (root / "Documentation").mkdir()
    (root / "Documentation/agent-units.json").write_text(json.dumps({"units": units}))
    for n, hours in enumerate((0, 1, 4.5, 10)):
        (root / f"f{n}").write_text(str(n))
        stamp = (t0 + timedelta(hours=hours)).isoformat()
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", f"c{n}"], check=True,
                       env=dict(env, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp))
    first = subprocess.run(["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip()
    (root / "Documentation/stages.json").write_text(json.dumps({"stages": [
        {"id": "S0", "title": "t", "budget_hours": 10, "start_commit": first[:7],
         "gate_commit": None}]}))
    return root


def unit(started_hours: float, stage: str = "S0") -> dict:
    stamp = f"2026-09-20T{int(started_hours):02d}:{int(started_hours % 1 * 60):02d}:00Z"
    return {"id": stamp, "stage": stage, "started": stamp, "ended": stamp, "hours": 0.1}


def test_the_gap_figure_counts_the_gaps_the_method_charges(tmp_path: Path) -> None:
    """The user's ruling of 2026-09-24: every gate review reports how many gaps exceed
    the session gap, what they span and what the method charged; G4's first report
    did not (G4's second interrogation, Reviewer D's finding 1)."""
    # units started at 1.5 and 4 h (inside the first gap: 2.5 h apart) and at 6 h
    # (alone in the second: no span); one at 4.25 h belongs to another stage, and one
    # at exactly the second gap's opening commit (4.5 h) is not inside it.
    root = gap_repo(tmp_path, [unit(1.5), unit(4), unit(6), unit(4.25, "S1"), unit(4.5)])
    text = G.gap_figure(root, "S0", 3.0, 6.0)
    assert text == (
        "2 commit gaps in S0 over the 2 h session gap, spanning 9.00 wall-hours, charged "
        "0.50 h by the budget's method; units were started inside 2 of them, the first start "
        "to the last spanning 2.50 wall-hours, a lower bound on the main thread's time in "
        "them; the method's 3.00 h plus what those spans exceed its charge by: 5.25 h, within "
        "the halt at 6 h, which the method's 3.00 h is within")
    assert G.gap_figure(root, "S0", 3.0, 5.0).endswith(
        "5.25 h, past the halt at 5 h, which the method's 3.00 h is within")
    # at the halt is past it, as the kill gate's ge reads a halt, for either figure
    assert G.gap_figure(root, "S0", 3.0, 5.25).endswith(
        "5.25 h, past the halt at 5.25 h, which the method's 3.00 h is within")
    assert G.gap_figure(root, "S0", 5.25, 5.25).endswith(
        "7.50 h, past the halt at 5.25 h, which the method's 5.25 h is past")
    assert G.gap_figure(root, "S0", None, None).endswith("the main thread's time in them")
    assert G.gap_figure(root, "S1", 3.0, 6.0) == "not measured (S1 has not started)"


def test_the_gap_figure_reads_the_ledger_in_time_order(tmp_path: Path) -> None:
    """tools/units.py appends in record order, not start order: the span is first start
    to last start whatever order the ledger holds them in."""
    root = gap_repo(tmp_path, [unit(4), unit(6), unit(1.5)])
    assert "the first start to the last spanning 2.50 wall-hours" in G.gap_figure(
        root, "S0", 3.0, 6.0)


def test_a_closed_stage_s_gaps_end_at_its_gate(tmp_path: Path) -> None:
    """A closed stage is charged to its own gate commit (metrics.stage_bounds), so a gap
    after the gate is the next stage's, not this one's."""
    import json
    import subprocess
    root = gap_repo(tmp_path, [])
    doc = json.loads((root / "Documentation/stages.json").read_text())
    gate = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD~1"], capture_output=True,
                          text=True, check=True).stdout.strip()   # the 4.5 h commit
    doc["stages"][0]["gate_commit"] = gate[:7]
    doc["stages"].append({"id": "S1", "title": "u", "budget_hours": 10, "start_commit": None,
                          "gate_commit": None})
    (root / "Documentation/stages.json").write_text(json.dumps(doc))
    assert G.gap_figure(root, "S0", None, None).startswith(
        "1 commit gap in S0 over the 2 h session gap, spanning 3.50 wall-hours")
    assert G.gap_figure(root, "S1", None, None).startswith(
        "1 commit gap in S1 over the 2 h session gap, spanning 5.50 wall-hours")


def test_a_gap_of_exactly_the_session_gap_is_charged_as_spacing() -> None:
    """wall_hours charges a gap of at most SESSION_GAP_HOURS as itself, so it is no
    session gap; gitlog.session_gaps and wall_hours must agree on the boundary."""
    import gitlog
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 20, tzinfo=timezone.utc)
    at = [gitlog.Commit(str(n), t0 + timedelta(hours=h), "s")
          for n, h in enumerate((0, gitlog.SESSION_GAP_HOURS, gitlog.SESSION_GAP_HOURS + 2.01))]
    assert [(a.hash, b.hash) for a, b, _ in gitlog.session_gaps(at)] == [("1", "2")]
    charged = gitlog.wall_hours(at)
    assert charged == gitlog.SESSION_START_HOURS * 2 + gitlog.SESSION_GAP_HOURS


def test_the_gap_figure_with_no_long_gap_says_so(tmp_path: Path) -> None:
    root = gap_repo(tmp_path, [])
    doc = __import__("json").loads((root / "Documentation/stages.json").read_text())
    import subprocess
    third = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD~1"],
                           capture_output=True, text=True, check=True).stdout.strip()
    doc["stages"][0]["start_commit"] = third[:7]          # only the 4.5 h to 10 h commits
    (root / "Documentation/stages.json").write_text(__import__("json").dumps(doc))
    assert G.gap_figure(root, "S0", 1.0, None) == (
        "1 commit gap in S0 over the 2 h session gap, spanning 5.50 wall-hours, charged "
        "0.25 h by the budget's method; units were started inside 0 of them, the first start "
        "to the last spanning 0.00 wall-hours, a lower bound on the main thread's time in "
        "them; the method's 1.00 h plus what those spans exceed its charge by: 1.00 h")
    doc["stages"][0]["start_commit"] = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
        check=True).stdout.strip()[:7]
    (root / "Documentation/stages.json").write_text(__import__("json").dumps(doc))
    assert G.gap_figure(root, "S0", 1.0, None) == (
        "no commit gap in S0 exceeds the 2 h session gap")


# ------------------------------------------------------------ the words M5 judges

def judged_repo(tmp_path: Path, mask: dict | None, receipt: dict | None) -> Path:
    import json
    (tmp_path / "Documentation/receipts").mkdir(parents=True)
    replay = {"name": "boot", "result": {"mask": mask or {}}}
    (tmp_path / G.TICK_REPLAY_RECEIPT).write_text(json.dumps({"replays": [replay]}))
    if receipt is not None:
        (tmp_path / G.MASK_RECEIPT.format(name="boot")).write_text(json.dumps(receipt))
    return tmp_path


USED = {"mask": "harness/replay/masks/boot.h", "mask_sha256": "ab"}
MASK = {"emitted": {"path": "harness/replay/masks/boot.h", "sha256": "ab"},
        "counts": {"varying": 920330, "selected": 760611}}


def test_m5_says_how_many_varying_words_each_replay_judges(tmp_path: Path) -> None:
    """G4's second interrogation, Reviewer D's finding 5: a word the mask drops is
    compared on no tick, and the row a reader sees did not say how many are judged."""
    root = judged_repo(tmp_path, USED, MASK)
    assert G.judged_words(root) == (
        "judged words of the words native runs vary on: boot 760611 of 920330 (82.6%) (each "
        "replay's mask receipt, counts.selected of counts.varying)")
    qs = G.standing_questions(current(), {"id": "S4"}, None, {}, ("x", None), ("y", True),
                              G.judged_words(root))
    (_, text, measured), = [q for q in qs if q[0] == "4"]
    assert text == "M5 row: 0 of everything; " + G.judged_words(root) and measured


@pytest.mark.parametrize("used,receipt,why", [
    (USED, None, "no mask receipt Documentation/receipts/s4-mask-boot-v1.json"),
    (dict(USED, mask_sha256="cd"), MASK, "describes another mask than the one"),
    ({}, MASK, "compared with no mask"),
    (USED, dict(MASK, counts={"varying": 10, "selected": 11}), "no selected and varying counts"),
    (USED, dict(MASK, counts={"selected": 11}), "no selected and varying counts"),
])
def test_m5_s_judged_words_are_not_measured_without_the_replay_s_own_mask(
        tmp_path: Path, used: dict, receipt: dict | None, why: str) -> None:
    text = G.judged_words(judged_repo(tmp_path, used, receipt))
    assert text.startswith("judged words of the words native runs vary on: boot not measured (")
    assert why in text
    # the figure is part of question 4's answer: unread, the question is not measured
    qs = G.standing_questions(current(), {"id": "S4"}, None, {}, ("x", None), ("y", True), text)
    (_, _, measured), = [q for q in qs if q[0] == "4"]
    assert measured is False
    assert G.verdict(4, [(True, "c", "[met] c")], qs).startswith(
        "FAIL: standing questions not measured: 4")


def test_m5_s_judged_words_name_the_mask_by_path_as_well_as_hash(tmp_path: Path) -> None:
    text = G.judged_words(judged_repo(tmp_path, dict(USED, mask="harness/replay/masks/x.h"),
                                      MASK))
    assert "describes another mask than the one" in text


def test_no_compared_replay_has_no_judged_words(tmp_path: Path) -> None:
    assert G.judged_words(tmp_path) is None


def test_question_6_at_a_finished_stage_needs_no_unit_rate() -> None:
    """A stage whose units were all done at its start has rate 0 and no projection;
    finished, it is answered from its hours per planned hour all the same (the fold
    review of fold2:r1), and with no hours spent it is not measured."""
    info = {"active_stage": "S4", "rate_per_hour": 0.0, "rate": "0.00 imports/h over S4",
            "units_remaining": 0, "spent_hours": 265.78}
    projected = ("not available: rate is 0.00 imports/h with 0 imports remaining", None)
    qs = G.standing_questions(current(), S4, S5, info, projected, ("y", True))
    (_, text, measured), = [q for q in qs if q[0] == "6"]
    assert measured is True and text == q6(0, 0.0)[0]
    info.update(rate_per_hour=None, rate="not measured (no wall hours spent on S4 yet)",
                spent_hours=0.0)
    qs = G.standing_questions(current(), S4, S5, info, ("not available", None), ("y", True))
    (_, text, measured), = [q for q in qs if q[0] == "6"]
    assert measured is False and text.startswith("not measured (rate not measured")
