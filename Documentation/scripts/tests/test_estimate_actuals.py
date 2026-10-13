"""tools/s4/estimate_actuals.py: the 110-hour review's actual-against-estimate table.

The table files each unit under an estimate row by its label and adds the main
thread's commit spacing. These tests hold the parts a misfile or a typed number
would break: every label lands in exactly one row, work outside the estimate
has a row of its own, the total is the rows' sum, and a unit that ended exactly
at the estimate counts before it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))

import estimate_actuals as E  # noqa: E402
import gitlog  # noqa: E402


def test_the_finish_plan_units_land_in_their_rows() -> None:
    cases = {
        "workflow sa2-g4-units-wave1: build:U4": "devices",
        "workflow sa2-p4-rebase: rebase:P4": "devices",
        "review: U4 r1 trap rule (gate batch P5)": "devices",
        "workflow sa2-g4-units-wave1: fold:U7": "vanilla",
        "workflow sa2-g4-units-wave1: review:U12": "recording",
        "workflow sa2-recorder-import-log-review: fold:recorder": "recording",
        # the clock measurement is the drift the per-replay masks answer
        "workflow sa2-g4-units-wave1: build:U11": "m5",
        "workflow sa2-u10-d3dx: build:U10-d3dx": "m4",
        "workflow sa2-g4-units-wave1: fold:U1": "gate",
        "workflow sa2-interrogate-g4-100: interrogate:g4-100:A-fable": "gate",
        # the display fix and the save isolation were not in the estimate
        "workflow sa2-session-instrument-review: review-A:display": "outside",
        "workflow sa2-session-instrument-review: fold:session": "outside",
        "measure: time as a recorded input, the proposal (the user's third ruling)": "outside",
    }
    assert {label: E.classify(label) for label in cases} == cases


def test_a_label_no_rule_matches_is_outside_not_dropped() -> None:
    assert E.classify("something nobody planned") == E.OUTSIDE[0]


def test_every_row_key_is_reachable_and_the_total_is_the_rows_sum() -> None:
    keys = {key for key, *_ in E.ROWS} | {E.OUTSIDE[0]}
    assert {key for key, _ in E.RULES} <= keys
    assert (sum(r[2] for r in E.ROWS), sum(r[3] for r in E.ROWS)) == (44, 68)
    assert E.TYPED_TOTAL != (44, 68)  # the decision's typed total, printed beside the sum


def test_a_unit_that_ended_at_the_estimate_counts_before_it() -> None:
    at = E.stamp(E.ESTIMATE_AT)
    iso = lambda t: t.isoformat().replace("+00:00", "Z")  # noqa: E731
    units = [{"ended": iso(at), "hours": 1.0}, {"ended": iso(at + timedelta(seconds=1)),
                                                "hours": 2.0}]
    done, after = E.split(units, at)
    assert [u["hours"] for u in done] == [1.0] and [u["hours"] for u in after] == [2.0]


def test_gaps_count_only_the_ones_over_the_session_gap() -> None:
    t0 = datetime(2026, 9, 23, tzinfo=timezone.utc)
    commit = lambda h: gitlog.Commit("x", t0 + timedelta(hours=h), "s")  # noqa: E731
    got = E.gaps([commit(0), commit(1), commit(1 + gitlog.SESSION_GAP_HOURS + 0.5), commit(20)])
    assert got[0] == 2 and abs(got[1] - (gitlog.SESSION_GAP_HOURS + 0.5 + 20 - 3.5)) < 1e-9
