#!/usr/bin/env python3
"""Behaviour tests for check_kill_criteria.py.

The gate exists because G1 kill criterion 3 was met on S3's second slice --
5813 of 5817 jump-table arms were guaranteed traps -- and the record stated
the number without the consequence, invisibly to every gate for a whole
stage (`agents-journals/gates/2026-09-20-g3.md`, finding B1).

So the first thing proved here is that the shipped evaluation spec for that
criterion, taken verbatim out of `Documentation/kill-criteria.json`, fires on
the `Dispatch` row as S3 slice 2 would have written it. A gate that has not
been shown to fail has not been shown to be a gate; this project has been
caught by that three times.

Every test runs the real script as a subprocess against a miniature project
under tmp_path, except the one that runs it against the live repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
GATE = SCRIPTS / "check_kill_criteria.py"
LEDGER = ROOT / "Documentation" / "kill-criteria.json"
SCHEMA = "sa2-recomp-kill-criteria/v1"

# The criterion, quoted out of the decision record it lives in.
CRITERION_3 = (
    "3. **Control flow.** Any trap on a mapped direct-call or jump-table target\n"
    "   (903 jump tables, 5817 targets, 118 functions with unresolved computed\n"
    "   jumps in the map) that the wrap cannot resolve within 8 agent\n"
    "   wall-hours, pivot. A trap on an unmapped target is the analyzer's\n"
    "   defect and does not count.\n")

# The Dispatch row as it stood when S3 slice 2 measured the arms, and as it
# stands today. metrics.py's dispatch() writes both shapes.
DISPATCH_SLICE_2 = ("6017 unresolved of 19456 distinct addresses;"
                    " far_outside_the_image 5/5, import_iat_value 250/250,"
                    " jump_table_arm 5813/5817, map_entry 0/12683,"
                    " missing_block_target 56/559, rdata_vtable_entry_unmapped 755/755;"
                    " controls positive-miss 0 negative-hit 0 (measured on next)")
DISPATCH_TODAY = ("1062 unresolved of 19456 distinct addresses;"
                  " far_outside_the_image 5/5, import_iat_value 250/250,"
                  " jump_table_arm 0/5715, map_entry 0/12683,"
                  " missing_block_target 56/559, rdata_vtable_entry_unmapped 755/755;"
                  " controls positive-miss 0 negative-hit 0 (measured on next+allarms)")

SECTION = "### 1.4 Kill criteria (each fires a pivot to the custom emitter)"
RECORD = "Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md"
RESOLVER = "Documentation/decisions/2026-09-20-G3-jump-table-arms.md"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GATE), "--root", str(root), *args],
                          cwd=ROOT, capture_output=True, text=True)


def shipped_entry(identifier: str, horizon: str | None = "G3") -> dict:
    """One entry out of the ledger this repository ships, verbatim but for its review's
    gate dating, which is set to `horizon` (None: left verbatim).

    The miniature projects close G3 (G3_RECORD) and keep S4 active, while the shipped
    review's dating moves at every gate close: dated G3 during S4, G4 with
    before_closure true for G4's report of record, then G4 as the commit that closed G4
    answered it (the tick-100 entry's review_dating). Dating it at the miniature's
    horizon keeps what these tests hold, the shipped review's budget, row and verdict,
    independent of where the real ledger's horizon stands; the dating rules have tests
    of their own (test_a_review_made_before_its_gate_closes_is_current and those after
    it), and test_the_shipped_reviews_are_current_on_the_repository_s_own_horizon holds
    the shipped dating to the repository as it stands.
    """
    document = json.loads(LEDGER.read_text(encoding="utf-8"))
    for entry in document["criteria"]:
        if entry["id"] == identifier:
            if horizon is not None and "review" in entry:
                review = dict(entry["review"], at_gate=horizon)
                review.pop("before_closure", None)
                entry = dict(entry, review=review)
            return entry
    raise AssertionError(f"{identifier} is not in {LEDGER}")


# The M5 row as METRICS.md read it at development_snapshot, when the user first answered the
# tick-100 criterion.
M5_AT_EDF5E24 = "boot 4/1200 (bug: d_01750000)"
# A row the vanilla re-registration could plausibly leave: a new divergence,
# before tick 100, in another column. Constructed; no run produced it.
M5_MOVED = "boot 57/1200 (bug: d_01760000)"


# The row the shipped tick-100 review names in answered_against: the row the
# user's latest answer was given against ("record it", 2026-09-24).
M5_SHIPPED = shipped_entry("design-S4-replay-tick-100")["review"]["answered_against"]["value"]


def metrics_file(dispatch: str = DISPATCH_TODAY, m3: str = "52/52 (100.0%); failed 0",
                 traps: str = "611 missing-block sites in game region across 470 addresses",
                 m5: str = M5_SHIPPED) -> str:
    rows = [
        ("M1 emitted", "game 8581/8581 (100.0%); game-region instructions decoded into a"
                       " module 968324/971553 (99.7%)"),
        ("M3 fixtures", m3),
        ("M5 replays", m5),
        ("Traps", traps),
        ("Dispatch", dispatch),
    ]
    head = ("# Metrics\n\n## Metrics\n\n| Metric | Value | Denominator source | Status |\n"
            "|---|---|---|---|\n")
    return head + "".join(f"| {name} | {value} | source | measured |\n" for name, value in rows)


def project(tmp_path: Path, criteria: list[dict], *, records: dict[str, str] | None = None,
            metrics: str | None = None) -> Path:
    """A miniature project: a decisions directory, METRICS.md and the ledger.

    `.git` exists because every gate locates its project with
    `gitlog.project_root`, which refuses a directory that is not a repository.
    Nothing here reads the git log: no test uses a budget-kind criterion.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "Documentation" / "decisions").mkdir(parents=True)
    for relative, text in (records or {RECORD: (
            "# G1 decision\n\n" + SECTION + "\n\n" + CRITERION_3)}).items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "METRICS.md").write_text(metrics or metrics_file(), encoding="utf-8")
    ledger = tmp_path / "Documentation" / "kill-criteria.json"
    ledger.write_text(json.dumps({"schema": SCHEMA, "note": "test", "criteria": criteria}),
                      encoding="utf-8")
    return ledger


def control_flow_entry(status: str = "open", **overrides) -> dict:
    """The shipped G1-3 entry, with its shipped evaluation spec."""
    entry = {
        "id": "G1-3-control-flow",
        "title": "Control flow",
        "consequence": "pivot to the custom emitter",
        "status": status,
        # The miniature record below carries this criterion alone, so it is
        # item 1 there; in the real decision it is item 3.
        "sources": [{"record": RECORD, "section": SECTION, "item": 1,
                     "quote": "Any trap on a mapped direct-call or jump-table target"}],
        "evaluation": shipped_entry("G1-3-control-flow")["evaluation"],
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# The incident, reproduced
# ---------------------------------------------------------------------------

def test_the_g3_incident_fires_the_gate(tmp_path: Path) -> None:
    """5813 of 5817 arms trapped: the criterion was met and nothing said so."""
    ledger = project(tmp_path, [control_flow_entry()],
                     metrics=metrics_file(dispatch=DISPATCH_SLICE_2))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: G1-3-control-flow" in done.stderr
    assert "5813" in done.stderr and "5817" in done.stderr  # what B1 taught, quoted
    assert "pivot to the custom emitter" in done.stderr


def test_the_same_criterion_passes_on_todays_row(tmp_path: Path) -> None:
    """The wrap resolved it, so the same spec must not fire on the current row."""
    ledger = project(tmp_path, [control_flow_entry()])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "kill criteria OK" in done.stdout


# ---------------------------------------------------------------------------
# A criterion in a record that the ledger does not list
# ---------------------------------------------------------------------------

def test_a_criterion_missing_from_the_ledger_fails(tmp_path: Path) -> None:
    record = ("# G1 decision\n\n" + SECTION + "\n\n" + CRITERION_3 + "\n"
              "4. **Build time.** If the build exceeds 60 wall minutes, pivot.\n")
    ledger = project(tmp_path, [control_flow_entry()], records={RECORD: record})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is stated in a record and is not in the ledger" in done.stderr
    assert "item 2" in done.stderr
    assert "Build time" in done.stderr


def test_a_design_kill_pivot_paragraph_must_be_tracked(tmp_path: Path) -> None:
    """The sweep covers the design's own Kill/pivot paragraphs, not just decisions."""
    ledger = project(tmp_path, [control_flow_entry()])
    design = tmp_path / "Documentation" / "SA2_RECOMP_DESIGN.md"
    design.write_text("## 5. Staged plan\n\n### S3. Whole-image lift\n\n"
                      "Kill/pivot: if trap count in the game region exceeds 2 % of\n"
                      "instructions, the histogram was wrong.\n", encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is not in the ledger" in done.stderr
    assert "S3. Whole-image lift" in done.stderr


# ---------------------------------------------------------------------------
# Drift between the ledger and the record
# ---------------------------------------------------------------------------

def test_a_reworded_criterion_breaks_the_anchor(tmp_path: Path) -> None:
    record = ("# G1 decision\n\n" + SECTION + "\n\n"
              "3. **Control flow.** Any trap the wrap cannot resolve, pivot.\n")
    ledger = project(tmp_path, [control_flow_entry()], records={RECORD: record})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "reworded or removed out from under the ledger" in done.stderr


def test_a_missing_section_breaks_the_anchor(tmp_path: Path) -> None:
    ledger = project(tmp_path, [control_flow_entry()],
                     records={RECORD: "# G1 decision\n\n## 2. Something else\n\nprose\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "anchored to a heading that no longer exists" in done.stderr


def test_two_entries_cannot_claim_one_criterion(tmp_path: Path) -> None:
    second = control_flow_entry()
    second["id"] = "duplicate"
    ledger = project(tmp_path, [control_flow_entry(), second])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "two ledger entries claim the same criterion" in done.stderr


# ---------------------------------------------------------------------------
# Status and resolution
# ---------------------------------------------------------------------------

def test_a_resolved_criterion_that_fires_again_fails(tmp_path: Path) -> None:
    entry = control_flow_entry(
        status="met-and-resolved",
        resolved_by={"record": RESOLVER, "what_it_decided": "the wrap resolves it"})
    ledger = project(tmp_path, [entry], records={
        RECORD: "# G1\n\n" + SECTION + "\n\n" + CRITERION_3,
        RESOLVER: "# G3 decision\n\nthe wrap resolves G1 kill criterion 3\n"},
        metrics=metrics_file(dispatch=DISPATCH_SLICE_2))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "A RESOLVED KILL CRITERION FIRES AGAIN" in done.stderr


def test_a_resolved_criterion_needs_a_record_that_exists(tmp_path: Path) -> None:
    entry = control_flow_entry(
        status="met-and-resolved",
        resolved_by={"record": "Documentation/decisions/nothing-here.md",
                     "what_it_decided": "it was fine"})
    ledger = project(tmp_path, [entry])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "names a resolving record that does not exist" in done.stderr


def test_status_must_be_known(tmp_path: Path) -> None:
    ledger = project(tmp_path, [control_flow_entry(status="fine, probably")])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is not one of" in done.stderr


# ---------------------------------------------------------------------------
# The manual half: a criterion no metric can evaluate
# ---------------------------------------------------------------------------

def manual_entry(**overrides) -> dict:
    entry = {
        "id": "G1-2-semantic-gap",
        "title": "Semantic gap",
        "consequence": "pivot to the custom emitter",
        "status": "open",
        "sources": [{"record": RECORD, "section": SECTION, "item": 1,
                     "quote": "Any trap on a mapped direct-call or jump-table target"}],
        "evaluation": {"kind": "manual", "why": "no metric carries a fixture class",
                       "human_check": "list the classes and say which are EXACT"},
        "review": {"at_gate": "G1", "verdict": "not-met", "by": "a human",
                   "evidence": "every class has a passing fixture"},
    }
    entry.update(overrides)
    return entry


def test_a_manual_criterion_is_representable_and_passes(tmp_path: Path) -> None:
    """Reviewed at the latest closed gate, verdict not-met: the gate passes."""
    ledger = project(tmp_path, [manual_entry()])
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "manual/reviewed not-met" in done.stdout
    assert "1 manual and needing a human" in done.stdout


def test_a_stale_manual_review_fails(tmp_path: Path) -> None:
    """G3 closed and nobody answered the criterion: silence is not a pass."""
    ledger = project(tmp_path, [manual_entry()], records={
        RECORD: "# G1\n\n" + SECTION + "\n\n" + CRITERION_3,
        RESOLVER: "# G3 decision\n\nprose\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "last reviewed at G1, and the latest closed gate is G3" in done.stderr
    assert "list the classes and say which are EXACT" in done.stderr


def test_a_manual_criterion_reviewed_as_met_fails(tmp_path: Path) -> None:
    ledger = project(tmp_path, [manual_entry(
        review={"at_gate": "G1", "verdict": "met", "by": "a human",
                "evidence": "fsqrt has been failing for two days"})])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED (by review)" in done.stderr


def test_a_manual_criterion_with_no_review_fails(tmp_path: Path) -> None:
    entry = manual_entry()
    del entry["review"]
    ledger = project(tmp_path, [entry])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "no automatic evaluation and no recorded review" in done.stderr


# ---------------------------------------------------------------------------
# A check that cannot find its number must fail, not pass
# ---------------------------------------------------------------------------

def test_a_missing_metrics_row_fails(tmp_path: Path) -> None:
    entry = control_flow_entry()
    entry["evaluation"] = dict(entry["evaluation"], value={
        "row": "Dispatch", "select": "ratio", "keys": ["no_such_group"], "part": "numerator"})
    ledger = project(tmp_path, [entry])
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "no 'no_such_group N/M'" in done.stderr


def test_an_unmeasured_row_fails(tmp_path: Path) -> None:
    ledger = project(tmp_path, [control_flow_entry()],
                     metrics=metrics_file(dispatch="not measured (input absent)"))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "cannot be evaluated against a metric that is not measured" in done.stderr


def test_the_trap_ceiling_fires_when_traps_pass_two_percent(tmp_path: Path) -> None:
    """The design's own S3 criterion, with the shipped spec, on a doctored row."""
    entry = shipped_entry("design-S3-trap-ceiling")
    entry = dict(entry, sources=[{"record": RECORD, "section": SECTION, "item": 1,
                                  "quote": "Any trap on a mapped direct-call"}])
    ledger = project(tmp_path, [entry], metrics=metrics_file(
        traps="30000 missing-block sites in game region across 470 addresses"))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-S3-trap-ceiling" in done.stderr
    assert "3.088" in done.stderr  # 30000 / 971553


# ---------------------------------------------------------------------------
# The review horizon: a review fails only when it is older than the latest
# closed gate, or dated beyond the gate under construction
# ---------------------------------------------------------------------------

G3_RECORD = "Documentation/decisions/2026-09-20-G3-jump-table-arms.md"


def stages_file(root: Path, active: str = "S4") -> None:
    """A stages.json whose first stage without a gate commit is `active`. Stages from S5
    on carry "main_thread": "transcripts", as metrics.TRANSCRIPTS_FROM requires, and the
    file states the idle-gap threshold they are counted at, as metrics.load_stages
    requires (the user's ruling of 2026-10-04)."""
    number = int(active[1:])
    counted = {"main_thread": "transcripts"}
    stages = [{"id": f"S{n}", "title": f"stage {n}", "budget_hours": 80,
               "start_commit": None, "gate_commit": f"gate{n}", **(counted if n >= 5 else {})}
              for n in range(number)]
    stages.append({"id": active, "title": "active", "budget_hours": 80,
                   "start_commit": None, "gate_commit": None,
                   **(counted if number >= 5 else {})})
    path = root / "Documentation" / "stages.json"
    path.write_text(json.dumps({"schema": "sa2-recomp-stages/v1", "idle_gap_hours": 2.0,
                                "stages": stages}), encoding="utf-8")


def horizon_project(tmp_path: Path, review: dict, *, status: str = "open",
                    active: str | None = "S4", **extra) -> Path:
    """G3 closed (a -G3- decision exists); `active` is the stage under construction."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    entry = manual_entry(review=review, status=status, **extra)
    ledger = project(tmp_path, [entry], records={
        RECORD: "# G1\n\n" + SECTION + "\n\n" + CRITERION_3,
        G3_RECORD: "# G3 decision\n\nprose\n"})
    if active:
        stages_file(tmp_path, active)
    return ledger


def review_at(gate: str, verdict: str = "not-met", **extra) -> dict:
    return {"at_gate": gate, "verdict": verdict, "by": "a human", "evidence": "looked",
            **extra}


def test_a_review_made_before_its_gate_closes_is_current(tmp_path: Path) -> None:
    """Line 651's old rule failed any review not dated exactly at the horizon.

    A review made during S4 (design 4.3's 100 % review) is honestly dated G4
    while G3 is the latest closed gate. It passes when it says so.
    """
    ledger = horizon_project(tmp_path, review_at("G4", before_closure=True))
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "gate under construction: G4" in done.stdout


def test_a_review_dated_at_an_open_gate_must_say_before_closure(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G4"))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'the gate still under construction, without "before_closure": true' in done.stderr


def test_a_before_closure_review_expires_when_its_gate_closes(tmp_path: Path) -> None:
    """It cannot answer for the closing review: no looser than a review dated G3."""
    ledger = horizon_project(tmp_path, review_at("G4", before_closure=True), active="S5")
    (tmp_path / "Documentation/decisions/2026-10-01-G4-closure.md").write_text("# G4\n")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "last reviewed at G4 before that gate closed, and the latest closed gate is G4" \
        in done.stderr


def test_the_closing_review_is_current_after_its_gate_closes(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G4"), active="S5")
    (tmp_path / "Documentation/decisions/2026-10-01-G4-closure.md").write_text("# G4\n")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_review_older_than_the_horizon_still_fails(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G2"))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "last reviewed at G2, and the latest closed gate is G3" in done.stderr


def test_a_review_dated_beyond_the_gate_under_construction_fails(tmp_path: Path) -> None:
    """Without the upper bound, 'G9' would stay current until G9 closed."""
    ledger = horizon_project(tmp_path, review_at("G5"))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "reviewed at G5, a gate beyond the one under construction (G4)" in done.stderr


def test_a_review_newer_than_the_horizon_with_no_stage_file_fails(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G4"), active=None)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "names no gate under construction" in done.stderr


def test_a_review_at_a_gate_closed_only_in_stages_json_fails(tmp_path: Path) -> None:
    """S4 has its gate_commit and S5 is active, but no -G4- record exists yet.

    The review is dated G4: neither the latest closed gate (G3, by the decision
    records) nor the gate under construction (G5). Flagged or not, nothing
    would ever expire it, so it fails.
    """
    for flag in (True, False):
        where = tmp_path / str(flag)
        ledger = horizon_project(where, review_at("G4", before_closure=flag), active="S5")
        done = run(where, "--ledger", str(ledger))
        assert done.returncode == 1, (flag, done.stdout + done.stderr)
        assert ("reviewed at G4, which is neither the latest closed gate (G3) nor the gate"
                " under construction (G5)") in done.stderr, flag


def test_an_unparseable_review_gate_fails(tmp_path: Path) -> None:
    for gate in ("G3b", "G03"):
        ledger = horizon_project(tmp_path / gate, review_at(gate))
        done = run(tmp_path / gate, "--ledger", str(ledger))
        assert done.returncode == 1, done.stdout + done.stderr
        assert "is not a gate name such as 'G3'" in done.stderr


def test_a_before_closure_flag_that_is_not_a_boolean_is_refused(tmp_path: Path) -> None:
    """'yes' is truthy: accepted, it would pass as the flag without being one."""
    ledger = horizon_project(tmp_path, review_at("G4", before_closure="yes"))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "review 'before_closure' must be true or false" in done.stderr


def test_a_review_budget_that_is_not_a_number_is_refused(tmp_path: Path) -> None:
    for name, figure in (("text", "110"), ("bool", True)):
        ledger = horizon_project(tmp_path / name, review_at("G3", budget_hours=figure))
        done = run(tmp_path / name, "--ledger", str(ledger))
        assert done.returncode == 1, (name, done.stdout + done.stderr)
        assert "review 'budget_hours' must be a number" in done.stderr, name


def test_an_unreadable_stages_file_fails(tmp_path: Path) -> None:
    """The gate under construction dates reviews; a stages.json it cannot read is a failure."""
    ledger = horizon_project(tmp_path, review_at("G3"))
    (tmp_path / "Documentation" / "stages.json").write_text("{not json", encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "Documentation/stages.json: the active stage could not be read" in done.stderr


# ---------------------------------------------------------------------------
# A resolved manual criterion keeps being answered; a retired one does not
# ---------------------------------------------------------------------------

RESOLUTION = {"record": G3_RECORD, "what_it_decided": "the class is the oracle's"}
AGAINST = {"row": "M5 replays", "value": M5_SHIPPED}


def test_a_resolved_manual_criterion_with_a_current_review_passes(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G3", "met", answered_against=AGAINST),
                             status="met-and-resolved", resolved_by=RESOLUTION)
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "manual/reviewed met" in done.stdout
    assert "1 met and resolved" in done.stdout


def test_a_resolved_manual_criterion_whose_review_expired_fails(tmp_path: Path) -> None:
    """Before 2026-09-23 a resolved manual entry was never looked at again."""
    ledger = horizon_project(tmp_path, review_at("G2", "met", answered_against=AGAINST),
                             status="met-and-resolved", resolved_by=RESOLUTION)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "last reviewed at G2, and the latest closed gate is G3" in done.stderr


def test_a_resolved_manual_review_fails_when_its_row_moves(tmp_path: Path) -> None:
    """Review W1: the oracle-fault answer must not cover a divergence it never saw.

    The review is current by date; only the row it was answered against moved.
    """
    ledger = horizon_project(tmp_path, review_at("G3", "met", answered_against=AGAINST),
                             status="met-and-resolved", resolved_by=RESOLUTION)
    (tmp_path / "METRICS.md").write_text(metrics_file(m5=M5_MOVED), encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert (f"G1-2-semantic-gap: the review was answered against METRICS.md row 'M5 replays'"
            f" = '{M5_SHIPPED}', and it now reads '{M5_MOVED}'") in done.stderr


def test_a_resolved_manual_review_that_names_no_row_fails(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G3", "met"), status="met-and-resolved",
                             resolved_by=RESOLUTION)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("G1-2-semantic-gap: status 'met-and-resolved', and its review does not name the"
            " METRICS.md row it was answered against") in done.stderr


def test_a_resolved_manual_review_against_a_missing_row_fails(tmp_path: Path) -> None:
    ledger = horizon_project(
        tmp_path, review_at("G3", "met", answered_against={"row": "M9 nothing", "value": "x"}),
        status="met-and-resolved", resolved_by=RESOLUTION)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "row 'M9 nothing', and METRICS.md has no such row" in done.stderr


def test_a_resolved_manual_review_with_no_metrics_file_fails(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G3", "met", answered_against=AGAINST),
                             status="met-and-resolved", resolved_by=RESOLUTION)
    (tmp_path / "METRICS.md").unlink()
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "row 'M5 replays', which cannot be read: METRICS.md is missing" in done.stderr


def test_a_malformed_answered_against_is_refused(tmp_path: Path) -> None:
    for name, against, said in (
            ("text", "M5 replays", "review 'answered_against' must be an object"),
            ("novalue", {"row": "M5 replays"},
             "review 'answered_against': 'value' must be a non-empty string"),
            ("emptyrow", {"row": " ", "value": M5_AT_EDF5E24},
             "review 'answered_against': 'row' must be a non-empty string")):
        ledger = horizon_project(tmp_path / name,
                                 review_at("G3", "met", answered_against=against),
                                 status="met-and-resolved", resolved_by=RESOLUTION)
        done = run(tmp_path / name, "--ledger", str(ledger))
        assert done.returncode == 1, (name, done.stdout + done.stderr)
        assert said in done.stderr, (name, done.stderr)


def test_an_open_manual_review_needs_no_row(tmp_path: Path) -> None:
    """The tie is for a resolution; an open criterion's answer is its own evidence."""
    ledger = horizon_project(tmp_path, review_at("G3"))
    (tmp_path / "METRICS.md").write_text(metrics_file(m5=M5_MOVED), encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


def test_an_open_manual_review_that_names_a_row_fails_when_the_row_moves(tmp_path: Path) -> None:
    """S5's 50 % review answered design-S5-d3d9-surface against a stale M7 row, and only a
    resolution's row was ever compared. An open review that names a row is held to it."""
    ledger = horizon_project(tmp_path, review_at("G3", answered_against=AGAINST))
    (tmp_path / "METRICS.md").write_text(metrics_file(m5=M5_MOVED), encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert (f"G1-2-semantic-gap: the review was answered against METRICS.md row 'M5 replays'"
            f" = '{M5_SHIPPED}', and it now reads '{M5_MOVED}'. The answer was given for that"
            f" evidence") in done.stderr


def test_an_open_manual_review_whose_row_is_unchanged_passes(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G3", answered_against=AGAINST))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_resolved_manual_criterion_with_no_review_fails(tmp_path: Path) -> None:
    entry = manual_entry(status="met-and-resolved", resolved_by=RESOLUTION)
    del entry["review"]
    ledger = project(tmp_path, [entry], records={
        RECORD: "# G1\n\n" + SECTION + "\n\n" + CRITERION_3, G3_RECORD: "# G3\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "status 'met-and-resolved' and no recorded review" in done.stderr


def test_a_retired_manual_criterion_needs_no_review(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G1"), status="retired",
                             resolved_by=RESOLUTION)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------
# The budget: a git repository whose spend is set by its unit ledger, the
# design's own 4.3 and S4 text, and the shipped entries verbatim
# ---------------------------------------------------------------------------

T0 = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
DESIGN = ROOT / "Documentation" / "SA2_RECOMP_DESIGN.md"
TICK_100_RECORD = "Documentation/decisions/2026-09-23-S4-tick-100-oracle-fault.md"
BUDGET_RECORD = "Documentation/decisions/2026-09-23-S4-budget-110.md"


def design_excerpt(*headings: str) -> str:
    """The named `###` sections of the real design, verbatim, under a title."""
    lines = DESIGN.read_text(encoding="utf-8").splitlines()
    out = ["# SA2 Recompiled: design document", ""]
    for heading in headings:
        start = next(i for i, line in enumerate(lines) if line.startswith(heading))
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith(("## ", "### "))), len(lines))
        out += lines[start:end]
    return "\n".join(out) + "\n"


HALT_SECTION = "### 4.3 Budget breach protocol"
S4_SECTION = "### S4. Runtime and headless boot"


def git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    return subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def budget_repo(tmp_path: Path, criteria: list[dict], *, percent: float,
                active: str = "S4", budget: float = 80, design: str | None = None,
                records: dict[str, str] | None = None) -> Path:
    """A repository whose active stage `active` reads `percent` % of `budget` h.

    One commit, charged 0.25 h as a session start; one recorded unit supplies
    the rest. When S5 is active, S4 is closed at that commit, and S5, counted from
    the session transcripts (metrics.TRANSCRIPTS_FROM), is charged the same quarter
    hour from a session ledger window that starts at the gate.
    """
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "kill@example.invalid")
    git(tmp_path, "config", "user.name", "Kill Criteria Test")
    (tmp_path / "log.txt").write_text("first\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "first", when=T0)
    first = git(tmp_path, "rev-parse", "HEAD")
    stages = [{"id": "S4", "title": "Runtime and headless boot", "budget_hours": budget,
               "start_commit": first, "gate_commit": None}]
    docs = tmp_path / "Documentation"
    (docs / "decisions").mkdir(parents=True)
    if active == "S5":
        stages[0]["budget_hours"] = 80
        stages[0]["gate_commit"] = first
        stages.append({"id": "S5", "title": "Presentation", "budget_hours": budget,
                       "start_commit": None, "gate_commit": None,
                       "main_thread": "transcripts"})
        window = [T0.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                  (T0 + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")]
        (docs / "agent-sessions.json").write_text(json.dumps({
            "schema": "sa2-recomp-agent-sessions/v1", "sessions": [
                {"id": "main", "source": "main.jsonl", "windows": [window], "gaps": [],
                 "hours": 0.25}]}), encoding="utf-8")
    units = round(percent / 100 * budget - 0.25, 4)
    (docs / "stages.json").write_text(json.dumps(
        {"schema": "sa2-recomp-stages/v1", "idle_gap_hours": 2.0, "stages": stages}),
        encoding="utf-8")
    (docs / "agent-units.json").write_text(json.dumps({"units": [
        {"id": "u1", "stage": active, "started": "2026-09-23T10:00:00Z",
         "ended": "2026-09-23T11:00:00Z", "hours": units}]}), encoding="utf-8")
    (docs / "SA2_RECOMP_DESIGN.md").write_text(
        design if design is not None else design_excerpt(HALT_SECTION, S4_SECTION),
        encoding="utf-8")
    for relative, text in {G3_RECORD: "# G3 decision\n\nprose\n",
                           TICK_100_RECORD: "# the tick-100 ruling\n", **(records or {})}.items():
        (tmp_path / relative).write_text(text, encoding="utf-8")
    (tmp_path / "METRICS.md").write_text(metrics_file(), encoding="utf-8")
    ledger = docs / "kill-criteria.json"
    ledger.write_text(json.dumps({"schema": SCHEMA, "note": "test", "criteria": criteria}),
                      encoding="utf-8")
    return ledger


def budget_ledger(**halt_overrides) -> list[dict]:
    """The shipped 150 % halt and the shipped tick-100 entry, verbatim."""
    halt = dict(shipped_entry("design-4.3-budget-halt"), **halt_overrides)
    return [halt, shipped_entry("design-S4-replay-tick-100")]


def test_s4_at_151_percent_halts(tmp_path: Path) -> None:
    """The user's ruling: at 150 % S4 work stops. No exemption, no progress clause."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=151)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "151 >= 150" in done.stderr and "S4 active at 151.0 %" in done.stderr


def test_s4_at_exactly_150_percent_halts(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=150)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr


def test_s4_just_under_150_percent_passes(tmp_path: Path) -> None:
    """Under the shipped 110 h budget, whose tick-100 review was given again there."""
    ruling = {BUDGET_RECORD: "> Budget ruling: S4's budget is now 110 agent-hours.\n"}
    ledger = budget_repo(tmp_path, budget_ledger(), percent=149.9, budget=110, records=ruling)
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "149.9 >= 150" in done.stdout


S5_SECTION = "### S5. Presentation"


def test_another_stage_at_151_percent_halts(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger() + [shipped_entry("design-S5-d3d9-surface")],
                         percent=151, active="S5", budget=120,
                         design=design_excerpt(HALT_SECTION, S4_SECTION, S5_SECTION))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "S5 active at 151.0 %" in done.stderr


def test_resolving_the_halt_does_not_let_s4_past_it(tmp_path: Path) -> None:
    """A resolution record is not permission: a resolved automatic criterion fires again."""
    ledger = budget_repo(tmp_path, budget_ledger(
        status="met-and-resolved",
        resolved_by={"record": G3_RECORD, "what_it_decided": "S4 may continue"}),
        percent=151)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "A RESOLVED KILL CRITERION FIRES AGAIN: design-4.3-budget-halt" in done.stderr


def test_the_raised_budget_moves_the_halt_and_nothing_else(tmp_path: Path) -> None:
    """S4 re-budgeted to 110 h by the user: 151 % of 110 h halts, 149.9 % does not."""
    ruling = {BUDGET_RECORD: "# S4 re-budgeted\n\n> Budget ruling: S4's budget is now 110"
                             " agent-hours.\n"}
    ledger = budget_repo(tmp_path, budget_ledger(), percent=151, budget=110, records=ruling)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4 active at 151.0 % of 110 h" in done.stderr


def test_the_tick_100_resolution_passes_past_100_percent(tmp_path: Path) -> None:
    """Q1 option A: met, the oracle's, resolved; as shipped since 2026-09-24, answered
    again as not met against the vanilla registration's row.

    The shipped review was given again at the 110 h budget on 2026-09-24
    (Documentation/decisions/2026-09-24-S4-110h-review.md), so it passes at
    100.4 % of 110 h with METRICS.md's M5 row unchanged. The answer given at
    80 h is the control below: stale at 110 h.
    """
    ruling = {BUDGET_RECORD: "> Budget ruling: S4's budget is now 110 agent-hours.\n"}
    ledger = budget_repo(tmp_path, budget_ledger(), percent=100.4, budget=110, records=ruling)
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "manual/reviewed not-met" in done.stdout
    assert "precondition 100.4 >= 100 is true" in done.stdout


def test_the_tick_100_resolution_does_not_cover_a_new_divergence(tmp_path: Path) -> None:
    """Review W1, on the shipped entry: M5 moves to another pre-tick-100 bug and the gate fails.

    Without the tie, the oracle-fault answer would stand for the new divergence
    until G4 closed.
    """
    ledger = budget_repo(tmp_path, budget_ledger(), percent=100.4)
    (tmp_path / "METRICS.md").write_text(metrics_file(m5=M5_MOVED), encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert (f"design-S4-replay-tick-100: the review was answered against METRICS.md row"
            f" 'M5 replays' = '{M5_SHIPPED}', and it now reads '{M5_MOVED}'") in done.stderr


def test_the_unanswered_tick_100_entry_fails_past_100_percent(tmp_path: Path) -> None:
    """The control: HEAD's entry (open, no review) is what blocked every push."""
    unanswered = shipped_entry("design-S4-replay-tick-100")
    for key in ("resolved_by", "review", "met_when_it_fired", "review_dated_g3_because",
                "review_given_again"):
        unanswered.pop(key, None)
    unanswered["status"] = "open"
    ledger = budget_repo(tmp_path, [shipped_entry("design-4.3-budget-halt"), unanswered],
                         percent=100.4)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("design-S4-replay-tick-100: no automatic evaluation and no recorded review"
            in done.stderr)


def test_the_tick_100_review_is_given_again_at_the_new_budget(tmp_path: Path) -> None:
    """Answered at 80 h; after the re-budget to 110 h it is stale from 110 h.

    The shipped entry has been given again at 110 h, so the control is the
    entry as the 80 h answer left it.
    """
    ruling = {BUDGET_RECORD: "> Budget ruling: S4's budget is now 110 agent-hours.\n"}
    halt, tick = budget_ledger()
    tick = dict(tick, review=dict(tick["review"], budget_hours=80))
    ledger = budget_repo(tmp_path, [halt, tick], percent=100.4, budget=110, records=ruling)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("design-S4-replay-tick-100: its precondition is a percentage of a 110 h budget,"
            " and the review answered it at a 80 h budget") in done.stderr


def test_the_tick_100_review_given_at_the_new_budget_passes(tmp_path: Path) -> None:
    ruling = {BUDGET_RECORD: "> Budget ruling: S4's budget is now 110 agent-hours.\n"}
    halt, tick = budget_ledger()
    tick = dict(tick, review=dict(tick["review"], budget_hours=110))
    ledger = budget_repo(tmp_path, [halt, tick], percent=100.4, budget=110, records=ruling)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_tick_100_review_below_the_new_100_percent_is_not_asked(tmp_path: Path) -> None:
    ruling = {BUDGET_RECORD: "> Budget ruling: S4's budget is now 110 agent-hours.\n"}
    ledger = budget_repo(tmp_path, budget_ledger(), percent=78, budget=110, records=ruling)
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "precondition 78 >= 100 is false" in done.stdout


def test_the_tick_100_resolution_expires_when_g4_closes(tmp_path: Path) -> None:
    """Dated G3, the review cannot answer for G4's closure (a review dated G4 before
    that gate closed cannot either: test_a_before_closure_review_expires_when_its_gate_closes)."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=100.4, records={
        "Documentation/decisions/2026-10-01-G4-closure.md": "# G4\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("design-S4-replay-tick-100: last reviewed at G3, and the latest closed gate is G4"
            in done.stderr)


# ---------------------------------------------------------------------------
# Every halt the design states is enforced by an automatic budget criterion
# ---------------------------------------------------------------------------

def test_deleting_the_halt_entry_fails(tmp_path: Path) -> None:
    """Before 2026-09-23 the 4.3 halt was tracked by convention: deleting it passed."""
    ledger = budget_repo(tmp_path, [shipped_entry("design-S4-replay-tick-100")], percent=151)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is stated in a record and is not in the ledger" in done.stderr
    assert "4.3 Budget breach protocol :: item 1" in done.stderr
    assert "the design halts at 150 % and no criterion enforces it" in done.stderr


def narrowed(**changes) -> list[dict]:
    halt = shipped_entry("design-4.3-budget-halt")
    evaluation = dict(halt["evaluation"])
    evaluation.update({k: v for k, v in changes.items() if k != "precondition"})
    halt = dict(halt, evaluation=evaluation)
    if "precondition" in changes:
        halt["precondition"] = changes["precondition"]
    return [halt, shipped_entry("design-S4-replay-tick-100")]


def test_a_halt_scoped_to_another_stage_fails(tmp_path: Path) -> None:
    """An S4 exemption by scoping the halt to other stages is refused at any spend."""
    ledger = budget_repo(tmp_path, narrowed(stage="S5"), percent=10)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "the design halts at 150 % and no criterion enforces it" in done.stderr
    assert "S4. Runtime and headless boot" in done.stderr


def test_a_raised_halt_threshold_fails(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, narrowed(threshold=151), percent=10)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "the design halts at 150 % and no criterion enforces it" in done.stderr


def test_a_halt_behind_a_precondition_fails(tmp_path: Path) -> None:
    """A 'slice rule holds' style condition would ride in as a precondition."""
    ledger = budget_repo(tmp_path, narrowed(precondition={
        "kind": "budget", "stage": "S5", "comparison": "ge", "threshold": 0}), percent=10)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "the design halts at 150 % and no criterion enforces it" in done.stderr


def test_a_halt_made_manual_fails(tmp_path: Path) -> None:
    halt = dict(shipped_entry("design-4.3-budget-halt"), evaluation={
        "kind": "manual", "why": "a human decides", "human_check": "look at the budget"},
        review={"at_gate": "G3", "verdict": "not-met", "by": "a human", "evidence": "fine"})
    ledger = budget_repo(tmp_path, [halt, shipped_entry("design-S4-replay-tick-100")],
                         percent=10)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "the design halts at 150 % and no criterion enforces it" in done.stderr


def test_a_stricter_halt_threshold_is_accepted(tmp_path: Path) -> None:
    """Strengthening is the agent's own: halting earlier than the design is allowed."""
    ledger = budget_repo(tmp_path, narrowed(threshold=140), percent=10)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------
# A budget above the design's is the user's, by a named ruling
# ---------------------------------------------------------------------------

def test_a_raised_budget_with_no_ruling_fails(tmp_path: Path) -> None:
    """Raising budget_hours outruns the halt as surely as raising its threshold."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4's budget is 110 h and the design gives it 80 h" in done.stderr


def test_a_raised_budget_with_the_users_ruling_passes(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110, records={
        BUDGET_RECORD: "# S4 re-budgeted\n\n> Budget ruling: S4's budget is now 110"
                       " agent-hours. The 100% review falls at\n> 110 and the 150% stop at"
                       " 165.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_ruling_that_does_not_quote_the_figure_fails(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110, records={
        BUDGET_RECORD: "# S4 re-budgeted\n\nThe agent set the budget to 110 h.\n\n> fine\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4's budget is 110 h and the design gives it 80 h" in done.stderr


def test_a_ruling_for_another_figure_fails(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=120, records={
        BUDGET_RECORD: "# S4 re-budgeted\n\n> S4's budget is now 110 agent-hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4's budget is 120 h and the design gives it 80 h" in done.stderr


def test_a_ruling_figure_at_the_end_of_a_sentence_is_found(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110, records={
        BUDGET_RECORD: "> S4's budget is now 110.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_ruling_is_matched_to_the_figure_exactly(tmp_path: Path) -> None:
    """A 110 h ruling does not cover 110.00004 h, however the figure is printed."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110.00004, records={
        BUDGET_RECORD: "> S4's budget is now 110 agent-hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "and the design gives it 80 h" in done.stderr


def test_a_ruling_whose_quote_names_another_figure_fails(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=120, records={
        "Documentation/decisions/2026-09-23-S4-budget-120.md":
            "> S4's budget is now 110 agent-hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4's budget is 120 h and the design gives it 80 h" in done.stderr


def test_the_110_ruling_does_not_cover_its_own_150_percent_figure(tmp_path: Path) -> None:
    """Review W2: the figure in the file name has to be the budget, not just a quoted number.

    The real ruling quotes 165 (where its 150 % falls) and "45-70" beside 110.
    Without the name check, stages.json could say 165 h with only the 110 h
    ruling on disk, and every halt would move with it.
    """
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=165, records={
        BUDGET_RECORD: "# S4 re-budgeted\n\n> Budget ruling: S4's budget is now 110"
                       " agent-hours. The 100% review falls at\n> 110 and the 150% stop at"
                       " 165.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4's budget is 165 h and the design gives it 80 h" in done.stderr


def test_a_ruling_named_for_another_stage_does_not_cover_this_one(tmp_path: Path) -> None:
    """The file matches S4's pattern, but the name's own stage is S14's."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110, records={
        "Documentation/decisions/2026-09-23-S4-budget-S14-budget-110.md":
            "> S14's budget is now 110 agent-hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4's budget is 110 h and the design gives it 80 h" in done.stderr


def test_a_budget_that_is_not_positive_and_finite_fails(tmp_path: Path) -> None:
    """A negative budget reads as a negative percentage, which no halt reaches."""
    for figure in (-80, 0, float("nan"), float("inf")):
        where = tmp_path / str(figure)
        where.mkdir()
        ledger = budget_repo(where, budget_ledger(), percent=10, budget=80)
        stages_path = where / "Documentation" / "stages.json"
        stages = json.loads(stages_path.read_text(encoding="utf-8"))
        stages["stages"][0]["budget_hours"] = figure
        stages_path.write_text(json.dumps(stages), encoding="utf-8")
        done = run(where, "--ledger", str(ledger))
        assert done.returncode == 1, (figure, done.stdout + done.stderr)
        assert "is not a positive finite number of hours" in done.stderr, figure


def test_a_stage_with_no_budget_where_the_design_gives_one_fails(tmp_path: Path) -> None:
    """A null budget has no percentage, so no halt could ever be measured against it."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=80)
    stages_path = tmp_path / "Documentation" / "stages.json"
    stages = json.loads(stages_path.read_text(encoding="utf-8"))
    stages["stages"][0]["budget_hours"] = None
    stages_path.write_text(json.dumps(stages), encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4 declares no budget, and the design gives it 80 h" in done.stderr


def test_a_stage_the_design_does_not_name_fails(tmp_path: Path) -> None:
    """Renamed, S4 would slip every per-stage check and the budget check with it."""
    for name, said in (("S04", "active stage id 'S04' is not S<n>"),
                       ("S9", "stage id 'S9' is not a stage the design names")):
        where = tmp_path / name
        where.mkdir()
        ledger = budget_repo(where, budget_ledger(), percent=10, budget=1000)
        stages_path = where / "Documentation" / "stages.json"
        stages = json.loads(stages_path.read_text(encoding="utf-8"))
        stages["stages"][0]["id"] = name
        stages_path.write_text(json.dumps(stages), encoding="utf-8")
        done = run(where, "--ledger", str(ledger))
        assert done.returncode == 1, (name, done.stdout + done.stderr)
        assert said in done.stderr, done.stderr


def test_a_design_whose_halt_was_reworded_away_fails(tmp_path: Path) -> None:
    """If the sweep cannot see design 4.3's halt, the gate says so instead of enforcing nothing."""
    design = (design_excerpt(HALT_SECTION, S4_SECTION)
              .replace("automatic halt.", "automatic stop.").replace("At 150 %, halt.",
                                                                     "At 150 %, stop."))
    ledger = budget_repo(tmp_path, [shipped_entry("design-S4-replay-tick-100")], percent=250,
                         design=design)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "states no budget halt for every stage" in done.stderr


def test_a_reworded_4_3_halt_is_still_swept(tmp_path: Path) -> None:
    """Review N5: 'halted' is still a halt, so the sweep names the 4.3 bullet itself.

    The halt entry is left out; the gate must say which halt went unenforced
    rather than only that none was found.
    """
    design = design_excerpt(HALT_SECTION, S4_SECTION).replace(
        "automatic halt.", "the stage is automatically halted.")
    assert "automatically halted" in design
    ledger = budget_repo(tmp_path, [shipped_entry("design-S4-replay-tick-100")], percent=10,
                         design=design)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "4.3 Budget breach protocol :: item 1" in done.stderr
    assert "states no budget halt for every stage" not in done.stderr


def test_a_stage_halt_without_its_comma_is_still_enforced(tmp_path: Path) -> None:
    """Review N5: 'At 120 % halt.' in S4's paragraph is a halt at 120 %, comma or not.

    The shipped 150 % entry does not enforce it, so the gate fails.
    """
    design = design_excerpt(HALT_SECTION, S4_SECTION).replace("fault. At 150 %, halt.",
                                                              "fault. At 120 % halt.")
    assert "At 120 % halt." in design
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, design=design)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "the design halts at 120 % and no criterion enforces it" in done.stderr


def test_a_lowered_budget_needs_no_ruling(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=60)
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------
# The user's ruling moves one stage's halt, and nothing else
# ---------------------------------------------------------------------------

HALT_RECORD = "Documentation/decisions/2026-09-24-S4-halt-220h.md"
HALT_QUOTE = ("> Confirming 200%: S4 may run to 220 hours without budget-review stops.\n"
              "> That waives the pauses only.\n")
AT_110 = {BUDGET_RECORD: "> Budget ruling: S4's budget is now 110 agent-hours.\n"}


def test_the_users_halt_ruling_lets_s4_past_150_percent(tmp_path: Path) -> None:
    """2026-09-24: S4 may run to 220 h of its 110 h budget. At 199.9 % it runs on."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=199.9, budget=110,
                         records={**AT_110, HALT_RECORD: "# S4 to 220 h\n\n" + HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert ("199.9 >= 200 % of the active stage's budget" in done.stdout
            and f"halt moved to 200 % by the user's ruling {HALT_RECORD}" in done.stdout)


def test_the_moved_halt_fires_at_the_ruled_hours(tmp_path: Path) -> None:
    """At exactly 220 h (200 % of 110 h) the halt fires, as the design's does at 150 %."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=200, budget=110,
                         records={**AT_110, HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "200 >= 200" in done.stderr


def test_a_halt_ruling_for_another_stage_does_not_move_this_one(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=151, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-S5-halt-220h.md": HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "151 >= 150" in done.stderr


def test_a_halt_ruling_does_not_follow_the_stage_that_comes_next(tmp_path: Path) -> None:
    """S4's ruling, S5 active at 151 %: S5 halts at the design's 150 %."""
    ledger = budget_repo(tmp_path, budget_ledger() + [shipped_entry("design-S5-d3d9-surface")],
                         percent=151, active="S5", budget=120,
                         design=design_excerpt(HALT_SECTION, S4_SECTION, S5_SECTION),
                         records={HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S5 active at 151.0 %" in done.stderr and "151 >= 150" in done.stderr


def test_a_halt_ruling_that_does_not_quote_its_hours_moves_nothing_and_fails(
        tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=160, budget=110, records={
        **AT_110, HALT_RECORD: "# S4 to 220 h\n\nThe agent let S4 run to 220 h.\n\n> fine\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert ("2026-09-24-S4-halt-220h.md: names a halt at 220 h for S4, and no quoted passage"
            in done.stderr)


def test_a_halt_ruling_whose_quote_names_other_hours_moves_nothing(tmp_path: Path) -> None:
    """A 300 h name over the user's 220 h quote: the name alone cannot move the halt."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=160, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-S4-halt-300h.md": HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr


def test_two_halt_rulings_dated_the_same_day_fail(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=100.4, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE,
        "Documentation/decisions/2026-09-24-S4-halt-200h.md": "> S4 may run to 200 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4 has 2 halt rulings dated 2026-09-24" in done.stderr
    assert "Traceback" not in done.stderr
    assert "kill criteria FAIL: Documentation/decisions" in done.stderr


LATER = "Documentation/decisions/2026-09-25-S4-halt-300h.md"
LATER_QUOTE = ("> we're making history here. 300 hours.\n\n"
               "> Asked: Can I record it as: \"S4 may run to 300 hours\", on the same terms?\n"
               "> Answered: Yes, same terms.\n")


def test_a_later_halt_ruling_supersedes_an_earlier_one(tmp_path: Path) -> None:
    """2026-09-25: the user moved S4's halt from 220 h to 300 h. The latest dated
    ruling stands; the earlier one is history, not a problem."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=250, budget=110,
                         records={**AT_110, HALT_RECORD: HALT_QUOTE, LATER: LATER_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"the user's ruling {LATER}" in done.stdout
    # At the later ruling's hours it fires (300 h of 110 h is 272.7 %); with only
    # the earlier ruling, 250 % would already have halted.
    (tmp_path / "at").mkdir()
    at = budget_repo(tmp_path / "at", budget_ledger(), percent=272.8, budget=110,
                     records={**AT_110, HALT_RECORD: HALT_QUOTE, LATER: LATER_QUOTE})
    done = run(tmp_path / "at", "--ledger", str(at))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr


D = "Documentation/decisions/"   # the halt review's tests, 2026-09-25


def test_a_later_ruling_that_lowers_the_halt_stands(tmp_path: Path) -> None:
    """220 h (09-24), then 180 h (09-25): the later, lower ruling stands. 180 h of 110 h
    is 163.6 %, so 170 % halts; ordered by hours rather than by date, it would not."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=170, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE,
        D + "2026-09-25-S4-halt-180h.md": "> S4 may run to 180 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "170 >= 163.6" in done.stderr


def test_two_rulings_on_the_latest_day_fail_after_an_earlier_one(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=100.4, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE,
        D + "2026-09-25-S4-halt-250h.md": "> S4 may run to 250 hours.\n",
        D + "2026-09-25-S4-halt-400h.md": "> S4 may run to 400 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4 has 2 halt rulings dated 2026-09-25" in done.stderr


def test_a_later_record_that_is_not_a_ruling_fails_beside_one_that_is(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=190, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE, D + "2026-09-25-S4-halt-500h.md": "> fine\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "2026-09-25-S4-halt-500h.md: names a halt at 500 h for S4" in done.stderr
    assert "KILL CRITERION MET" not in done.stderr        # the halt stays at 220 h


def test_a_ruling_dated_after_today_is_not_a_ruling(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=260, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE, LATER: LATER_QUOTE,
        D + "2099-01-01-S4-halt-1000h.md": "> S4 may run to 1000 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "2099-01-01-S4-halt-1000h.md: names a halt at 1000 h for S4, and its name's date" \
           " 2099-01-01 is after today (UTC)" in done.stderr


def test_a_ruling_whose_date_is_not_a_day_is_not_a_ruling(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=280, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE, LATER: LATER_QUOTE,
        D + "2026-13-45-S4-halt-1000h.md": "> S4 may run to 1000 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "date 2026-13-45 is not a day" in done.stderr


def test_a_question_the_user_declined_is_not_a_ruling(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=210, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE, D + "2026-09-25-S4-halt-400h.md":
            '> Asked: Can I record it as: "S4 may run to 400 hours"?\n> Answered: No.\n'})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "210 >= 200" in done.stderr


def test_a_halt_record_with_words_between_its_date_and_stage_is_not_a_ruling(
        tmp_path: Path) -> None:
    """The halt review's N4: the whole name is read, not its end alone."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=160, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-draft-S4-halt-220h.md": HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr


def test_a_question_with_no_answer_is_not_a_ruling(tmp_path: Path) -> None:
    """The fold review's W3: the agent's question alone moves nothing."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=210, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE,
        D + "2026-09-25-S4-halt-400h.md": '> Asked: Can I record it as: "S4 may run to 400 hours"?\n'})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "210 >= 200" in done.stderr


def test_a_yes_to_one_question_does_not_carry_a_no_to_another(tmp_path: Path) -> None:
    """The fold review's N3: each question is read with its own answer."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=210, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE,
        D + "2026-09-25-S4-halt-400h.md": (
            '> Asked: Can I record it as: "S4 may run to 400 hours"?\n> Answered: No.\n'
            '> Asked: Resume now?\n> Answered: Yes.\n')})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "210 >= 200" in done.stderr


def test_two_rulings_on_an_earlier_day_fail_too(tmp_path: Path) -> None:
    """The fold review's W2: one day has one ruling on every day, not only the
    day that stands."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=100.4, budget=110, records={
        **AT_110, HALT_RECORD: HALT_QUOTE,
        D + "2026-09-20-S4-halt-250h.md": "> S4 may run to 250 hours.\n",
        D + "2026-09-20-S4-halt-400h.md": "> S4 may run to 400 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "S4 has 2 halt rulings dated 2026-09-20" in done.stderr


def test_a_ruling_dated_today_is_a_ruling(tmp_path: Path) -> None:
    """The fold review's N4: today (UTC) is not after today."""
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    ledger = budget_repo(tmp_path, budget_ledger(), percent=210, budget=110, records={
        **AT_110, D + f"{today}-S4-halt-240h.md": "> S4 may run to 240 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{today}-S4-halt-240h.md" in done.stdout


def test_a_halt_record_whose_name_carries_no_date_is_not_a_ruling(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=160, budget=110, records={
        **AT_110, "Documentation/decisions/draft-S4-halt-220h.md": HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "draft-S4-halt-220h.md: names a halt at 220 h for S4" in done.stderr


def test_a_halt_ruling_below_150_percent_moves_nothing(tmp_path: Path) -> None:
    """A ruling cannot lower the halt below the design's either: 150 h of 110 h is 136 %."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=151, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-S4-halt-150h.md":
            "> S4 may run to 150 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "151 >= 150" in done.stderr and "halt moved" not in done.stdout + done.stderr


def test_a_halt_ruling_leaves_the_tick_100_precondition_alone(tmp_path: Path) -> None:
    """The ruling waives the pauses only: the tick-100 check still applies from 100 %."""
    halt, tick = budget_ledger()
    tick = dict(tick, review=dict(tick["review"], budget_hours=80))
    ledger = budget_repo(tmp_path, [halt, tick], percent=160, budget=110,
                         records={**AT_110, HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("design-S4-replay-tick-100: its precondition is a percentage of a 110 h budget,"
            " and the review answered it at a 80 h budget") in done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" not in done.stderr


def test_a_halt_ruling_given_in_percent_moves_nothing(tmp_path: Path) -> None:
    """The halt review's W1: the user's first words were "200 percent". A record named
    for 300 h over a quote in percent must not move the halt (it would be 273 %)."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=250, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-S4-halt-300h.md":
            "> i am authorizing S4 to 300 percent. no pauses until then.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "250 >= 150" in done.stderr


def test_a_halt_ruling_quoting_another_stage_does_not_move_this_one(tmp_path: Path) -> None:
    """S4's quote filed under S5's name: S5 still halts at 150 %."""
    ledger = budget_repo(tmp_path, budget_ledger() + [shipped_entry("design-S5-d3d9-surface")],
                         percent=170, active="S5", budget=120,
                         design=design_excerpt(HALT_SECTION, S4_SECTION, S5_SECTION),
                         records={"Documentation/decisions/2026-09-24-S5-halt-220h.md":
                                  HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "170 >= 150" in done.stderr


def test_the_stage_and_the_hours_must_share_one_passage(tmp_path: Path) -> None:
    """The fold review's W1: "S4 ... 200 percent" in one passage and "220 hours" in
    another, as the real record's passages are laid out, is not a ruling for 220 h."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=160, budget=110, records={
        **AT_110, HALT_RECORD: "> i am authorizing S4 to 200 percent.\n\n"
                               "> The review took 220 hours of reading.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "160 >= 150" in done.stderr


def test_a_halt_ruling_for_a_stage_the_design_does_not_name_fails(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=10, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-S9-halt-300h.md":
            "> S9 may run to 300 hours.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "names a halt for S9, which is not a stage the design names" in done.stderr


def test_the_moved_halt_is_rounded_to_the_nearest_not_up(tmp_path: Path) -> None:
    """220 h of a 75 h budget is 293.33 %: spend at 293.3 % must fire."""
    ledger = budget_repo(tmp_path, budget_ledger(), percent=293.3, budget=75,
                         records={HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "293.3 >= 293.3" in done.stderr


def test_a_date_is_not_a_halt_ruling(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, budget_ledger(), percent=160, budget=110, records={
        **AT_110, "Documentation/decisions/2026-09-24-S4-halt-2026h.md":
            "> 2026-09-24: noted for S4.\n"})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "160 >= 150" in done.stderr


def test_a_halt_ruling_does_not_move_a_halt_behind_a_precondition(tmp_path: Path) -> None:
    """The halt reviews' W3/W5: the precondition guard, shown able to fail."""
    ledger = budget_repo(tmp_path, narrowed(precondition={
        "kind": "budget", "stage": "S4", "comparison": "ge", "threshold": 100}),
        percent=160, budget=110, records={**AT_110, HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "KILL CRITERION MET AND UNRESOLVED: design-4.3-budget-halt" in done.stderr
    assert "160 >= 150" in done.stderr and "halt moved" not in done.stdout + done.stderr


def test_a_halt_ruling_does_not_move_a_gt_halt(tmp_path: Path) -> None:
    ledger = budget_repo(tmp_path, narrowed(comparison="gt"), percent=160, budget=110,
                         records={**AT_110, HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "160 > 150" in done.stderr


def test_a_halt_ruling_does_not_move_a_closed_stages_halt(tmp_path: Path) -> None:
    """A halt scoped to S4 after S4 closed at 160 %, with S5's ruling on disk: S4's
    entry is evaluated at 150 %, not moved by the active stage's ruling."""
    ledger = budget_repo(tmp_path, narrowed(stage="S4"), percent=100, active="S5", budget=120,
                         design=design_excerpt(HALT_SECTION, S4_SECTION, S5_SECTION),
                         records={"Documentation/decisions/2026-09-24-S5-halt-240h.md":
                                  "> S5 may run to 240 hours.\n"})
    units = tmp_path / "Documentation" / "agent-units.json"
    doc = json.loads(units.read_text(encoding="utf-8"))
    doc["units"].append({"id": "u0", "stage": "S4", "started": "2026-09-23T09:10:00Z",
                         "ended": "2026-09-23T09:20:00Z", "hours": 127.75})
    units.write_text(json.dumps(doc), encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger), "--verbose")
    halt = [line for line in (done.stdout + done.stderr).splitlines()
            if "design-4.3-budget-halt" in line and ">=" in line][0]
    assert "160 >= 150" in halt and "halt moved" not in halt


def test_the_moved_halt_shows_in_the_summary_and_the_marker(tmp_path: Path) -> None:
    """The halt reviews' W1/W3: without --verbose the move is a note, and the marker moves."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ruled = budget_repo(tmp_path / "a", budget_ledger(), percent=160, budget=110,
                        records={**AT_110, HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path / "a", "--ledger", str(ruled))
    assert done.returncode == 0, done.stdout + done.stderr
    assert (f"note: design-4.3-budget-halt evaluated at 200 % (ledger 150), the user's"
            f" ruling {HALT_RECORD}") in done.stdout
    plain = budget_repo(tmp_path / "b", budget_ledger(), percent=100.4, budget=110,
                        records=AT_110)
    other = run(tmp_path / "b", "--ledger", str(plain))
    assert other.returncode == 0, other.stdout + other.stderr
    marker = lambda out: out.rsplit("marker_sha256=", 1)[1].split()[0]
    assert marker(done.stdout) != marker(other.stdout)


def test_a_halt_ruling_does_not_move_a_stricter_halt(tmp_path: Path) -> None:
    """A halt entry stricter than the design's (140 %) is the agent's strengthening; it stays."""
    ledger = budget_repo(tmp_path, narrowed(threshold=140), percent=141, budget=110,
                         records={**AT_110, HALT_RECORD: HALT_QUOTE})
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "141 >= 140" in done.stderr


def test_the_shipped_halt_ruling_moves_s4_to_300_hours() -> None:
    """The real records, read the way the gate reads them: 300 h (2026-09-25)
    supersedes 220 h (2026-09-24), which stays as history."""
    sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
    import metrics
    hours, path = metrics.halt_ruling(ROOT, "S4")
    assert (hours, path.name) == (300.0, "2026-09-25-S4-halt-300h.md")
    assert [p.name for _, p in metrics.halt_rulings(ROOT, "S4")] == [
        "2026-09-24-S4-halt-220h.md", "2026-09-25-S4-halt-300h.md"]
    assert metrics.halt_ruling(ROOT, "S5") is None


# ---------------------------------------------------------------------------
# The live repository
# ---------------------------------------------------------------------------

def test_the_real_ledger_passes_the_real_tree() -> None:
    done = run(ROOT, "--verbose")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "kill criteria OK" in done.stdout


def test_every_criterion_the_records_state_is_tracked() -> None:
    """The sweep over the real records finds nothing the ledger misses."""
    done = run(ROOT)
    assert "is not in the ledger" not in done.stderr, done.stderr
    assert "sites swept: " in done.stdout


def test_the_four_g1_criteria_are_all_in_the_ledger() -> None:
    document = json.loads(LEDGER.read_text(encoding="utf-8"))
    items = sorted(source["item"]
                   for entry in document["criteria"]
                   for source in entry["sources"]
                   if source["record"] == RECORD and source["section"] == SECTION)
    assert items == [1, 2, 3, 4]


def test_criterion_three_is_recorded_as_met_and_resolved() -> None:
    entry = shipped_entry("G1-3-control-flow")
    assert entry["status"] == "met-and-resolved"
    assert entry["resolved_by"]["record"] == RESOLVER


def test_the_shipped_reviews_are_current_on_the_repository_s_own_horizon() -> None:
    """Every shipped manual review is current where this repository stands, and expires
    at the next gate close: the dating the miniature projects above set aside."""
    sys.path.insert(0, str(SCRIPTS))
    import check_kill_criteria as K
    horizon, current = K.latest_closed_gate(ROOT), K.active_gate(ROOT)
    document = json.loads(LEDGER.read_text(encoding="utf-8"))
    reviewed = [e for e in document["criteria"]
                if e["evaluation"]["kind"] == "manual" and e["status"] != "retired"
                and "review" in e]
    assert {e["id"] for e in reviewed} >= {"design-S4-replay-tick-100", "G1-2-semantic-gap"}
    # The next gate to close: the one under construction, unless it already has a decision
    # record (its closing review's records land before stages.json moves on, as G5's did on
    # 2026-10-10), and then the gate after it.
    newest = max((g for g in (horizon, current) if g), key=K.gate_number)
    later = f"G{K.gate_number(newest) + 1}"
    closes = current if current and (horizon is None
                                     or K.gate_number(current) > K.gate_number(horizon)) else later
    for entry in reviewed:
        review = entry["review"]
        assert K.review_timing(review, horizon, current) is None, (entry["id"], horizon, current)
        assert K.review_timing(review, closes, later) is not None, (entry["id"], closes, later)


def test_the_g1_2_review_states_the_fxam_class_s_hours_as_units_only() -> None:
    """G4's second interrogation, Reviewer D's finding 3: the FXAM class's hours are the
    units' alone, by label; the main thread's share is said to be not isolable; no S2
    fixture discriminates the class; and the verdict follows from the criterion's text."""
    review = shipped_entry("G1-2-semantic-gap", horizon=None)["review"]
    assert review["verdict"] == "not-met"
    units = json.loads((ROOT / "Documentation/agent-units.json").read_text())["units"]
    labels = ("build:fx", "review:fx-pass", "review:fx-gate", "fold:fx", "review:fx-fold")
    hours = {label: [u["hours"] for u in units if u["label"].endswith(": " + label)]
             for label in labels}
    assert all(len(found) == 1 for found in hours.values()), hours
    for label, (value,) in hours.items():
        assert f"{label} {value:g} h" in review["evidence"], label
    assert f"{round(sum(v for (v,) in hours.values()), 4):g} h in all" in review["evidence"]
    for said in ("The main thread's share is not isolable", "No S2 fixture discriminates it",
                 "check_emitter_determinism.py", "s2-atan2-v1.json",
                 "pc53-double-rounding-v1.json"):
        assert said in review["evidence"], said


def test_the_tick_100_criterion_is_recorded_as_the_users_resolution() -> None:
    """Q1 option A as ruled: met, the oracle's, resolved by adopting the vanilla oracle.

    Answered again on 2026-09-24 against the vanilla re-registration's row, at
    the user's direction ("For the tick-100 check: record it"): not met, the
    boot replay passing tick 100 and first diverging at tick 380. Answered
    again on 2026-09-26 at the U6 landing against the row with both replays and
    the residue classes: not met, both passing tick 100 with residues only; and
    again on 2026-09-27 when the boot named the host-thread-race class: not met.
    """
    entry = shipped_entry("design-S4-replay-tick-100", horizon=None)
    assert entry["status"] == "met-and-resolved"
    assert entry["resolved_by"]["record"] == TICK_100_RECORD
    # answered at G4 (unit CL, 2026-09-28): before the report of record with
    # before_closure, then by the commit that closed G4; and again at G5's closing
    # review (2026-10-10), against the same row
    assert (entry["review"]["at_gate"], entry["review"]["verdict"]) == ("G5", "not-met")
    assert "G4's closing review of 2026-09-29" in entry["review"]["by"]
    assert entry["review"]["answered_against"] == {
        "row": "M5 replays",
        "value": "boot residues only/1200 (host-services-steam-stats-v1, host-timing-v1, "
                 "host-thread-race-sofdec-v1) from 4; metalharbor residues only/15585 "
                 "(host-services-steam-stats-v1, host-timing-v1) from 4"}
    assert "under the user's direction of 2026-09-24" in entry["review"]["by"]
    assert "Documentation/decisions/2026-09-24-S4-halt-220h.md" in entry["review"]["by"]
    assert "record it" in (ROOT / "Documentation/decisions/2026-09-24-S4-halt-220h.md"
                           ).read_text(encoding="utf-8")
    record = (ROOT / TICK_100_RECORD).read_text(encoding="utf-8")
    assert "oracle fault, continue. Record it as met and" in record
    assert "Documentation/decisions/2026-09-23-S4-vanilla-oracle.md" in record


def test_the_video_checkpoint_is_sixty_hours_of_s5s_budget() -> None:
    """The user's ruling of 2026-09-29: no frame of the game on screen through DXVK Native
    within 60 hours of S5 opening stops S5 for a review with the user
    (Documentation/decisions/2026-09-29-S5-budget-400.md). The ledger holds it behind a
    budget precondition in percent, so the percentage must stay 60 hours of whatever
    budget stages.json gives S5: a re-budget that left it alone would move the checkpoint.
    It stays open and manual until a receipt of a frame retires it."""
    entry = shipped_entry("S5-video-checkpoint-60h", horizon=None)
    stages = json.loads((ROOT / "Documentation/stages.json").read_text(encoding="utf-8"))
    budget = next(s for s in stages["stages"] if s["id"] == "S5")["budget_hours"]
    precondition = entry["precondition"]
    assert (precondition["kind"], precondition["stage"], precondition["comparison"]) == (
        "budget", "S5", "ge")
    assert abs(precondition["threshold"] * budget / 100.0 - 60.0) < 1e-9, (
        precondition["threshold"], budget)
    assert entry["evaluation"]["kind"] == "manual"
    if entry["status"] == "open":
        assert "review" not in entry or entry["review"]["verdict"] == "not-met"
    else:
        assert entry["status"] == "retired", entry["status"]
        assert (ROOT / entry["resolved_by"]["record"]).is_file()
    record = (ROOT / entry["sources"][0]["record"]).read_text(encoding="utf-8")
    assert "within 60 hours of S5 opening, stop" in record


def test_the_video_checkpoint_is_answered_only_by_a_live_run() -> None:
    """The checkpoint reads as met only by a live run: the lifted game running, its d3d9
    calls reaching DXVK Native through the recording device's COM objects while it runs,
    and its frames on screen in an SDL3 window. A frame the player draws from a stream
    file recorded earlier does not answer it. That is the stricter reading of the user's
    words, which is the agent's to adopt (AGENTS.md rule 10); an edit that let a file
    replay answer it would loosen a kill criterion, which is the user's alone, so the
    human check is held to both halves here (agents-journals/s5/2026-09-29-dxvk-native.md,
    "The 60-hour checkpoint is met only by a live run"). The live run is named in the
    first 200 characters, which is what the kill gate prints when a review is stale."""
    entry = shipped_entry("S5-video-checkpoint-60h", horizon=None)
    human_check = entry["evaluation"]["human_check"]
    assert "a receipt under Documentation/receipts/ of a live run: the lifted game running, " \
           "its d3d9 calls reaching DXVK Native through the recording device's COM objects " \
           "while it runs" in human_check[:200]
    assert "Only a live run answers the checkpoint. A frame the player draws by replaying a " \
           "stream file recorded earlier does not" in human_check
    assert "If there is no receipt of a live run when S5 reaches 60 hours, the criterion is " \
           "met" in human_check
    assert "from a live run" in entry["title"]


# A projection a review compares against (the M7 kill re-answer's fold, 2026-10-04): fixed at the
# first commit that adds a named path, and carried forward unchanged by the re-answers a moving
# row forces.
PROJECTION = {"row": "M5 replays", "value": M5_SHIPPED, "fixed_at_the_commit_adding": "fixed.txt"}


def set_review(ledger: Path, review: dict) -> None:
    document = json.loads(ledger.read_text(encoding="utf-8"))
    document["criteria"][0]["review"] = review
    ledger.write_text(json.dumps(document), encoding="utf-8")


def test_a_projection_is_the_row_the_commit_that_takes_it_reads(tmp_path: Path) -> None:
    """While no commit adds the path, the commit under construction takes the projection, so
    it is held to METRICS.md as that commit will read it."""
    ledger = horizon_project(tmp_path, review_at("G3", answered_against=AGAINST,
                                                 projection=PROJECTION))
    (tmp_path / "fixed.txt").write_text("x\n", encoding="utf-8")
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr
    (tmp_path / "METRICS.md").write_text(metrics_file(m5=M5_MOVED), encoding="utf-8")
    set_review(ledger, review_at("G3", answered_against={"row": "M5 replays", "value": M5_MOVED},
                                 projection=PROJECTION))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert (f"G1-2-semantic-gap: the review's projection records METRICS.md row 'M5 replays' ="
            f" '{M5_SHIPPED}', and that row read '{M5_MOVED}' at the commit under construction,"
            f" which adds fixed.txt. A projection is fixed where it was taken") in done.stderr


def test_a_projection_stays_where_it_was_taken_when_the_row_moves(tmp_path: Path) -> None:
    """The re-answer a moved row forces follows the row in answered_against and carries the
    projection forward; a re-answer that moves the projection with it fails."""
    ledger = horizon_project(tmp_path, review_at("G3", answered_against=AGAINST,
                                                 projection=PROJECTION))
    (tmp_path / "fixed.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / ".git").rmdir()
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "kill@example.invalid")
    git(tmp_path, "config", "user.name", "Kill Criteria Test")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "takes the projection")
    taken = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "METRICS.md").write_text(metrics_file(m5=M5_MOVED), encoding="utf-8")
    set_review(ledger, review_at("G3", answered_against={"row": "M5 replays", "value": M5_MOVED},
                                 projection=PROJECTION))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 0, done.stdout + done.stderr
    set_review(ledger, review_at("G3", answered_against={"row": "M5 replays", "value": M5_MOVED},
                                 projection={**PROJECTION, "value": M5_MOVED}))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert (f"the review's projection records METRICS.md row 'M5 replays' = '{M5_MOVED}', and"
            f" that row read '{M5_SHIPPED}' at {taken[:12]}, the commit that added fixed.txt"
            ) in done.stderr


def test_a_projection_names_a_path_the_tree_holds(tmp_path: Path) -> None:
    ledger = horizon_project(tmp_path, review_at("G3", answered_against=AGAINST,
                                                 projection=PROJECTION))
    done = run(tmp_path, "--ledger", str(ledger))
    assert done.returncode == 1, done.stdout + done.stderr
    assert ("the review's projection is fixed at the commit adding fixed.txt, which the tree"
            " does not hold") in done.stderr


def test_a_malformed_projection_is_refused(tmp_path: Path) -> None:
    for name, projection, said in (
            ("text", "M5 replays", "review 'projection' must be an object"),
            ("nopath", {"row": "M5 replays", "value": M5_SHIPPED},
             "review 'projection': 'fixed_at_the_commit_adding' must be a non-empty string")):
        ledger = horizon_project(tmp_path / name, review_at("G3", answered_against=AGAINST,
                                                            projection=projection))
        done = run(tmp_path / name, "--ledger", str(ledger))
        assert done.returncode == 1, (name, done.stdout + done.stderr)
        assert said in done.stderr, (name, done.stderr)


def test_the_shipped_m7_review_keeps_its_projection_apart_from_its_row() -> None:
    """design-S5-d3d9-surface: the user's ruling 1 of 2026-10-04 is quoted, the projection is
    its own field fixed at the commit that adds the M7 staleness gate, and the evidence says the
    row's numerator is the user's three-way numerator as tools/s4/m7_inputs.py measures it, and the
    device's alone, an upper bound, only while that block reads not measured."""
    entry = shipped_entry("design-S5-d3d9-surface", horizon=None)
    review = entry["review"]
    assert review["verdict"] == "not-met" and entry["status"] == "open"
    assert review["projection"]["row"] == review["answered_against"]["row"] == "M7 device"
    assert review["projection"]["fixed_at_the_commit_adding"] == \
        "Documentation/scripts/check_m7_inputs.py"
    for words in ('"no split. The surface did not grow; M7 was stale."', "review.projection",
                  "carries the projection forward unchanged",
                  "measures into the receipt's three_way_numerator",
                  "while that block reads not measured, the row's numerator is what the recording "
                  "device answers, an upper bound",
                  "Documentation/decisions/2026-10-04-S5-user-rulings-2.md",
                  "flagged for the user and for G5's interrogation"):
        assert words in review["evidence"], words
    assert "a larger surface at the closing review meets this criterion\" was the agent's " \
           "reading" in review["evidence"]
