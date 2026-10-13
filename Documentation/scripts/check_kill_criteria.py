#!/usr/bin/env python3
"""A kill criterion that is met must be answered, and none may go untracked.

The defect this exists for
--------------------------

`Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md` section 1.4 kill
criterion 3 reads: *"Any trap on a mapped direct-call or jump-table target
(903 jump tables, 5817 targets, 118 functions with unresolved computed jumps
in the map) that the wrap cannot resolve within 8 agent wall-hours, pivot."*
S3's second slice measured **5813 of those 5817 targets as guaranteed traps**
and cited the criterion nowhere. The decision that adopted remill named the
condition under which remill stops being the answer; the stage met that
condition on its second slice; and the record stated the number without the
consequence. Nobody noticed for a whole stage, until a reviewer read the
decision record and the stage journal side by side
(`agents-journals/gates/2026-09-20-g3.md`, finding B1).

Every gate passed over it, because the kill criteria lived as prose in a
decision record and nothing read them.

What this gate does
-------------------

`Documentation/kill-criteria.json` is the ledger: one entry per criterion,
with its sources (the record and section that state it, quoted), what it is
evaluated against, its threshold and comparison, its status, and for
anything not `open` the record that resolved it. This gate:

1. **Evaluates** every criterion that can be evaluated automatically, against
   the committed `METRICS.md`, a receipt key path, or the budget block, and
   **fails when one is met**. For an `open` criterion that is "met and
   nothing answers it"; for a `met-and-resolved` one it is "the resolution
   stopped holding"; for a `retired` one it is "a criterion that was
   supposed to be unable to fire, fired".
2. **Sweeps the sources** for criteria the ledger does not list -- every
   numbered item under a decision record's "Kill criteria" heading, every
   `Kill/pivot:` paragraph in the design, and every budget-protocol bullet in
   the design that says "halt". A criterion stated in a
   record and absent from the ledger is exactly the hole that produced the
   incident, so it fails.
3. **Anchors** every ledger entry to its source: the quoted text must still
   appear in the named record under the named section. A criterion that was
   reworded or deleted out from under the ledger fails.
4. **Expires manual reviews.** A criterion with no automatic evaluation says
   `"kind": "manual"` and carries a `review` recording the gate at which a
   human last answered it. If the latest closed gate is newer than that
   review, and the criterion's precondition holds, the gate fails. This is
   the honest half: some of these cannot be measured, and a ledger that
   silently dropped them would be worse than none, but "a human must check
   this each gate" is only true if something notices when nobody did.

   Newer, older and beyond are by the execution order: a gate's place is its
   stage's place in stages.json's list (gate_order), not its number, because
   the user's order of 2026-10-07 closes G7 before G6. The latest closed gate
   is the one latest in that order among the gates a decision record's name
   carries (`-G<n>-`).

   A review dated at the latest closed gate is current. A review may also be
   dated at the gate under construction (`G<n>` while `S<n>` is active, the
   honest date for design 4.3's 100 % review), and then it must say
   `"before_closure": true` and expires when that gate closes: a review made
   before a gate closed cannot answer for its closing review. Nothing current
   under this rule outlives what the earlier rule ("dated exactly at the
   latest closed gate") allowed. A review dated beyond the gate under
   construction fails at once, since nothing would ever expire it.

   A criterion whose precondition is a budget percentage is answered at a
   budget: its review records `budget_hours`, and when stages.json no longer
   says that figure, the review is stale while the precondition holds. A
   re-budget moves the point the criterion applies from, so an answer given
   near the old 100 % does not carry to the new one.

   This applies to a `met-and-resolved` manual criterion too. A resolution a
   human wrote is only known to hold while a human keeps answering the
   criterion, and no metric can re-fire it the way the automatic branch
   re-fires a resolved automatic criterion. Only a `retired` criterion,
   whose condition can no longer arise, is exempt.

   A `met-and-resolved` manual criterion's review also names the METRICS.md
   row it was answered against and the value that row read
   (`"answered_against": {"row": ..., "value": ...}`). While the precondition
   holds, the gate fails when the field is missing or the row now reads
   anything else. A resolution was given for the divergence it classified; a
   different one must not inherit it unseen until the next gate closes.

   Any other review that names a row is held to it the same way. S5's 50 %
   review answered `design-S5-d3d9-surface` "not met" against an M7 row whose
   inputs had been stale since 2026-09-21, and the check above, written for
   resolutions only, could not see it (the user's ruling of 2026-10-04,
   `Documentation/decisions/2026-10-04-S5-user-rulings.md`). An `open` review
   that carries `answered_against` is therefore answered again whenever its row
   moves; an `open` review that names no row is unchanged.

   A review whose criterion compares a measurement with a projection may record
   the projection itself (`"projection": {"row": ..., "value": ...,
   "fixed_at_the_commit_adding": <path>}`), so that the re-answers the rule
   above forces cannot carry it away: `answered_against` follows the row, and
   the projection stays where it was taken. The gate holds `value` to the row as
   METRICS.md read it at the first commit that added `<path>` (the commit under
   construction while none has), so a projection moves only by a record the
   user makes, never by a re-answer (M7 kill re-answer, fold review, 2026-10-04).
5. **Requires every budget halt the design states to be automatic.** Design
   4.3's "At 150 % without exit criteria: automatic halt", and each stage's
   "At 150 %, halt.", must be evaluated by a `budget` criterion on the
   active stage (or that stage) with comparison `ge`, a threshold no higher
   than the design's, and no precondition. There is no exemption for any
   stage: the gate cannot tell which work a push carries, so a halt that
   admits one stage past it admits all of that stage's work.

   The one thing that moves the halt is the user's ruling for one stage
   (AGENTS.md rule 10 reserves a breach at 150 % to the user): a decision
   record named `<date>-S<n>-halt-<hours>h.md` whose quoted lines carry the
   figure (`metrics.halt_ruling`). While that stage is active, the halt entry
   at the design's 150 % is evaluated at the ruled hours as a percentage of
   the stage's budget instead, and fires there; a ruling below 150 % moves
   nothing. A later dated ruling supersedes an earlier one, which stays as
   history (2026-09-25, S4 to 300 h after 220 h); the date its name begins
   with must be a real day not after today, and a quoted question of the
   agent's counts only with the user's yes to it. The ledger entry keeps the
   design's threshold, so the check above still holds; a stricter halt,
   every criterion behind a budget precondition, and every other stage are
   untouched. A record so named that is not accepted, and two rulings for
   one stage dated the same day, fail the gate.

   Those halts are percentages of `budget_hours` in `Documentation/stages.json`,
   so a budget raised above the design's heading ("(budget: 80 h)") moves
   them as surely as a threshold edit would. A raised budget is the user's
   (AGENTS.md rule 10): it passes only with a decision record named
   `<date>-S<n>-budget-<hours>.md`, the figure in the name equal to the budget,
   that carries the figure in a quoted line. A lowered budget is a
   strengthening and needs nothing. Every stage id in stages.json must be a
   stage the design names, and every budget a positive finite number: a
   renamed stage or a negative budget would otherwise slip past every halt.
   The design must also still state design 4.3's halt: a sweep that finds no
   halt bullet there fails rather than enforcing nothing.

What the sweep can and cannot find
----------------------------------

It finds three shapes, and only three, because a fuzzy match over the words
"pivot" and "halt" would report every sentence that mentions one:

  * a numbered item under a heading matching `kill criteri` in any
    `Documentation/decisions/*.md`;
  * a `Kill/pivot:` paragraph in `Documentation/SA2_RECOMP_DESIGN.md`;
  * a budget-protocol bullet in the design (`- At **N %** ...`) that says
    `halt` -- design 4.3's 150 % halt, which used to be tracked only by
    convention, so deleting its entry removed the halt with no gate noticing.

A criterion stated in some fourth shape -- a decision that says "if X, we
stop" in running prose under an ordinary heading -- is invisible to the
sweep. The mitigation is a convention, not a check: state a kill criterion
under one of those shapes. `Documentation/IMPLEMENTATION_TIMELINE.md`
restates two of the design's criteria, prefixed `Kill/pivot (design):`, and
the sweep deliberately does not read it: a restatement pointing at the
design is not a new criterion, and requiring a ledger entry per restatement
would make the ledger a concordance instead of a register.

An entry may also carry a source the sweep does not enumerate. Such a
source is still anchored to its record; it simply has no site to claim.

What it deliberately does not do
--------------------------------

It does not time the windows. Two G1 criteria allow 8 agent wall-hours to
resolve a condition before the pivot fires, and the gate cannot know when
the clock started. It fires on the condition, on the day the condition
holds. That is the whole of finding B1: the clock ran for a stage with
nobody watching it, so the gate's job is to say the condition holds and let
a human start the clock.

It errs toward firing. Where a criterion's own precondition would make it
unable to fire again (G1 criterion 4's "at 100 % of the S2 budget", with S2
closed under budget), the ledger evaluates the condition without the
precondition and says so in the entry. A criterion evaluated as met is a
prompt for a human, not an automatic pivot.

Output ends with `marker_sha256=` over the sorted `id -> status -> condition`
map, so a change to what is tracked, or to a threshold, is visible in the
gate summary rather than only in a diff.

Usage: .venv/bin/python Documentation/scripts/check_kill_criteria.py
       [--root PATH] [--ledger PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import metrics as metrics_module
from decision_numbers import CitationError, resolve
from gitlog import project_root

LEDGER = "Documentation/kill-criteria.json"
SCHEMA = "sa2-recomp-kill-criteria/v1"
DESIGN = "Documentation/SA2_RECOMP_DESIGN.md"
DECISIONS = "Documentation/decisions"

STATUSES = ("open", "met-and-resolved", "retired")
RESOLVED_STATUSES = ("met-and-resolved", "retired")
EVALUATION_KINDS = ("metrics", "receipt", "budget", "manual")
VERDICTS = ("met", "not-met")

COMPARISONS: dict[str, Callable[[float, float], bool]] = {
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}
COMPARISON_TEXT = {"gt": ">", "ge": ">=", "lt": "<", "le": "<=", "eq": "==", "ne": "!="}

# A [[label]] citation marker in a decision record; prose quotes are matched
# with these removed, because check_decision_numbers.py owns them and a
# criterion's text should not have to carry them to be found.
MARKER = re.compile(r"\[\[[^\]\s]+\]\]")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
LIST_ITEM = re.compile(r"^(\d+)\.\s+(.*)$")
KILL_HEADING = re.compile(r"kill\s+criteri", re.IGNORECASE)
KILL_PIVOT = re.compile(r"^Kill/pivot:")
GATE_IN_DECISION = re.compile(r"-G(\d+)-")
# No leading zeros: "S04" would name no design stage and slip every per-stage check.
GATE_NAME = re.compile(r"G(0|[1-9]\d*)")
STAGE_NAME = re.compile(r"S(0|[1-9]\d*)")
STAGES = "Documentation/stages.json"
# Design 4.3's budget protocol is a bullet list, one bullet per threshold.
BUDGET_BULLET = re.compile(r"^-\s+At \*\*(\d+(?:\.\d+)?)\s*%\*\*")
# "halt", and the forms a rewording would reach for first ("halts", "halted").
HALT_WORD = re.compile(r"\bhalt(?:s|ed|ing)?\b", re.IGNORECASE)
# The stage paragraphs' own restatement: "At 150 %, halt." (the comma optional).
STAGE_HALT = re.compile(r"\bAt (\d+(?:\.\d+)?)\s*%,?\s*halt(?:s|ed)?\b")
STAGE_HEADING = re.compile(r"^#{1,6}\s+(S\d+)\.")
# A stage heading's own budget: "### S4. Runtime and headless boot (budget: 80 h)".
DESIGN_BUDGET = re.compile(r"\(budget:\s*(\d+(?:\.\d+)?)\s*h\)")
QUOTED = re.compile(r"^\s*>")
NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)")
RULING_NAME = re.compile(r"-(S\d+)-budget-(\d+(?:\.\d+)?)\.md$")


class LedgerError(Exception):
    """A ledger the gate cannot evaluate; the message names the cause."""


def normalise(text: str) -> str:
    """Whitespace-collapsed, marker-free text, for quoting prose out of prose."""
    return " ".join(MARKER.sub("", text).split())


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def _string(entry: dict, key: str, where: str, required: bool = True) -> str | None:
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{where}: {key!r} must be a non-empty string")
    return value


def load_ledger(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise LedgerError(f"{path}: {error}") from None
    except ValueError as error:
        raise LedgerError(f"{path}: not JSON: {error}") from None
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise LedgerError(f"{path}: schema is not {SCHEMA!r}")
    criteria = document.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise LedgerError(f"{path}: 'criteria' must be a non-empty list")
    seen: set[str] = set()
    for index, entry in enumerate(criteria):
        if not isinstance(entry, dict):
            raise LedgerError(f"{path}: criterion {index} is not an object")
        identifier = _string(entry, "id", f"{path}: criterion {index}")
        if identifier in seen:
            raise LedgerError(f"{path}: duplicate id {identifier!r}")
        seen.add(identifier)
        where = f"{path}: {identifier}"
        _string(entry, "title", where)
        _string(entry, "consequence", where)
        status = entry.get("status")
        if status not in STATUSES:
            raise LedgerError(f"{where}: status {status!r} is not one of {STATUSES}")
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources:
            raise LedgerError(f"{where}: 'sources' must be a non-empty list")
        for source in sources:
            if not isinstance(source, dict):
                raise LedgerError(f"{where}: a source is not an object")
            _string(source, "record", where)
            _string(source, "section", where)
            _string(source, "quote", where)
            if not isinstance(source.get("item"), int):
                raise LedgerError(f"{where}: source 'item' must be an integer")
        evaluation = entry.get("evaluation")
        if not isinstance(evaluation, dict):
            raise LedgerError(f"{where}: 'evaluation' must be an object")
        kind = evaluation.get("kind")
        if kind not in EVALUATION_KINDS:
            raise LedgerError(f"{where}: evaluation kind {kind!r} is not one of {EVALUATION_KINDS}")
        if kind == "manual":
            _string(evaluation, "why", where)
            _string(evaluation, "human_check", where)
        else:
            if evaluation.get("comparison") not in COMPARISONS:
                raise LedgerError(
                    f"{where}: comparison {evaluation.get('comparison')!r} is not one of"
                    f" {sorted(COMPARISONS)}")
            threshold = evaluation.get("threshold")
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise LedgerError(f"{where}: 'threshold' must be a number")
            _string(evaluation, "units", where)
        precondition = entry.get("precondition")
        if precondition is not None:
            if not isinstance(precondition, dict) or precondition.get("kind") != "budget":
                raise LedgerError(f"{where}: a precondition must be a budget object")
            _string(precondition, "stage", where)
            if precondition.get("comparison") not in COMPARISONS:
                raise LedgerError(f"{where}: precondition comparison is not a known comparison")
        if status in RESOLVED_STATUSES:
            resolved = entry.get("resolved_by")
            if not isinstance(resolved, dict):
                raise LedgerError(
                    f"{where}: status {status!r} requires 'resolved_by' naming the record"
                    " that answered it")
            _string(resolved, "record", where)
            _string(resolved, "what_it_decided", where)
        elif "resolved_by" in entry:
            raise LedgerError(f"{where}: status 'open' must not carry 'resolved_by'")
        review = entry.get("review")
        if review is not None:
            if not isinstance(review, dict):
                raise LedgerError(f"{where}: 'review' must be an object")
            _string(review, "at_gate", where)
            if not GATE_NAME.fullmatch(review["at_gate"]):
                raise LedgerError(f"{where}: review 'at_gate' {review['at_gate']!r} is not a"
                                  " gate name such as 'G3'; the review horizon compares gates"
                                  " by number")
            _string(review, "by", where)
            _string(review, "evidence", where)
            if not isinstance(review.get("before_closure", False), bool):
                raise LedgerError(f"{where}: review 'before_closure' must be true or false")
            answered = review.get("budget_hours")
            if answered is not None and (isinstance(answered, bool)
                                         or not isinstance(answered, (int, float))):
                raise LedgerError(f"{where}: review 'budget_hours' must be a number")
            against = review.get("answered_against")
            if against is not None:
                if not isinstance(against, dict):
                    raise LedgerError(f"{where}: review 'answered_against' must be an object"
                                      " naming a METRICS.md row and the value it read")
                _string(against, "row", f"{where}: review 'answered_against'")
                _string(against, "value", f"{where}: review 'answered_against'")
            projection = review.get("projection")
            if projection is not None:
                if not isinstance(projection, dict):
                    raise LedgerError(f"{where}: review 'projection' must be an object naming"
                                      " a METRICS.md row, the value it read and the path whose"
                                      " first commit fixes it")
                for key in ("row", "value", "fixed_at_the_commit_adding"):
                    _string(projection, key, f"{where}: review 'projection'")
            if review.get("verdict") not in VERDICTS:
                raise LedgerError(f"{where}: review verdict must be one of {VERDICTS}")
    return document


def condition_text(entry: dict) -> str:
    """One line naming what the criterion is measured against, for the marker."""
    evaluation = entry["evaluation"]
    kind = evaluation["kind"]
    if kind == "manual":
        return "manual"
    comparison = COMPARISON_TEXT[evaluation["comparison"]]
    threshold = evaluation["threshold"]
    if kind == "metrics":
        target = f"METRICS.md[{evaluation['value']['row']}]"
        if "denominator" in evaluation:
            target += f"/METRICS.md[{evaluation['denominator']['row']}]"
    elif kind == "receipt":
        target = f"{evaluation['receipt']}:{evaluation['path']}"
    else:
        target = f"budget[{evaluation['stage']}].{evaluation.get('measure', 'percent')}"
    return f"{target} {comparison} {threshold}"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

_NUMBER = r"\d+(?:\.\d+)?"


def extract(row_name: str, text: str, spec: dict) -> float:
    """Pull one number out of a METRICS.md row value, by an explicit rule."""
    if not isinstance(spec, dict):
        raise LedgerError(f"row {row_name!r}: the value spec must be an object")
    select = spec.get("select")
    if select == "percent":
        found = re.search(rf"\(({_NUMBER})\s*%\)", text)
        if not found:
            raise LedgerError(f"row {row_name!r}: no (NN.N%) in {text!r}")
        return float(found.group(1))
    if select == "int":
        found = re.search(r"\d+", text)
        if not found:
            raise LedgerError(f"row {row_name!r}: no integer in {text!r}")
        return float(found.group(0))
    if select == "ratio":
        keys = spec.get("keys") or ([spec["key"]] if isinstance(spec.get("key"), str) else None)
        if not keys:
            raise LedgerError(f"row {row_name!r}: a ratio needs 'key' or 'keys'")
        part = spec.get("part")
        if part not in ("numerator", "denominator"):
            raise LedgerError(f"row {row_name!r}: ratio 'part' must be numerator or denominator")
        total = 0.0
        for key in keys:
            found = re.search(rf"{re.escape(key)}\s+({_NUMBER})/({_NUMBER})", text)
            if not found:
                raise LedgerError(f"row {row_name!r}: no '{key} N/M' in {text!r}")
            total += float(found.group(1 if part == "numerator" else 2))
        return total
    raise LedgerError(f"row {row_name!r}: unknown select {select!r}"
                      " (known: percent, int, ratio)")


def metrics_rows(root: Path) -> dict[str, str]:
    path = root / metrics_module.METRICS_FILE
    if not path.is_file():
        raise LedgerError(f"{metrics_module.METRICS_FILE} is missing; run metrics.py --write")
    return metrics_module.parse_table(path.read_text(encoding="utf-8"))


def metrics_value(rows: dict[str, str], spec: dict) -> float:
    row = spec.get("row")
    if row not in rows:
        raise LedgerError(f"METRICS.md has no row {row!r} (rows: {', '.join(sorted(rows))})")
    text = rows[row]
    if "not measured" in text:
        raise LedgerError(f"METRICS.md row {row!r} reads {text!r}: the criterion cannot be"
                          " evaluated against a metric that is not measured")
    return extract(str(row), text, spec)


class Budget:
    """The budget block, read once and only if a criterion asks for it."""

    def __init__(self, root: Path):
        self.root = root
        self._info: dict | None = None

    def info(self) -> dict:
        if self._info is None:
            try:
                self._info = metrics_module.budget_info(self.root)
            except Exception as error:  # noqa: BLE001 - reported, never swallowed
                raise LedgerError(f"the budget block could not be computed: {error}") from None
        return self._info

    def percent(self, stage: str) -> tuple[float, str]:
        info = self.info()
        if stage == "active":
            if info.get("active_stage") is None:
                raise LedgerError("no stage is active, so 'active' has no budget percent")
            stage = info["active_stage"]
        if info.get("active_stage") == stage:
            value = info.get("percent")
            if value is None:
                raise LedgerError(f"stage {stage} declares no budget")
            return float(value), (f"{stage} active at {value} % of {info['budget_hours']} h"
                                  + self.reading(info))
        for closed in info.get("closed_stages", []):
            if closed.get("stage") == stage:
                return float(closed["percent"]), (f"{stage} closed at {closed['percent']} %"
                                                  + self.reading(closed))
        return 0.0, f"{stage} has not started (0 % of budget spent)"

    @staticmethod
    def reading(spend: dict) -> str:
        """For a stage counted from transcripts, the reading its percent is on, with the
        threshold, and the first-to-last reading beside it: the user's ruling of
        2026-10-04 asks for the threshold wherever the count is reported, and this note
        is where the kill gate reports it at every push. Empty for any other stage."""
        if spend.get("main_thread_rule") != metrics_module.TRANSCRIPTS:
            return ""
        beside = spend.get("first_to_last_percent")
        return (f" (active time, {metrics_module.threshold_text(spend)}; first to last "
                + ("n/a" if beside is None else f"{beside} %") + ")")

    def moved_halt(self, entry: dict) -> tuple[float, str] | None:
        """(percent, record) when `entry` is design 4.3's halt on the active stage and
        the user's ruling (`metrics.halt_ruling`) moved that stage's halt past 150 %.

        Only the design's halt moves: a budget criterion with comparison ge on the
        percent, at exactly the design's 150 % and with no precondition, evaluated
        on the active stage. A stricter halt, a budget criterion behind a
        precondition (none today; the tick-100 check is manual and never reaches
        this) and every closed stage keep their thresholds.
        """
        evaluation = entry["evaluation"]
        if (evaluation.get("kind") != "budget" or "precondition" in entry
                or evaluation.get("measure", "percent") != "percent"
                or evaluation.get("comparison") != "ge"
                or float(evaluation["threshold"]) != metrics_module.DESIGN_HALT_PERCENT):
            return None
        info = self.info()
        active = info.get("active_stage")
        if active is None or evaluation.get("stage") not in ("active", active):
            return None
        record, at = info.get("halt_ruling"), info.get("halt_percent")
        if record is None or at is None or at <= metrics_module.DESIGN_HALT_PERCENT:
            return None
        return float(at), record

    def continuation(self, entry: dict) -> dict | None:
        """Only the active design halt follows the authorized shared continuation."""
        evaluation = entry["evaluation"]
        if (evaluation.get("kind") != "budget" or "precondition" in entry
                or evaluation.get("measure", "percent") != "percent"
                or evaluation.get("comparison") != "ge"
                or float(evaluation["threshold"]) != metrics_module.DESIGN_HALT_PERCENT):
            return None
        info = self.info()
        if info.get("active_stage") is None or evaluation.get("stage") not in (
                "active", info["active_stage"]):
            return None
        return info.get("continuation")

    def hours(self, stage: str) -> float | None:
        """The budget in hours the percentages of `stage` are taken of."""
        info = self.info()
        if stage == "active":
            stage = info.get("active_stage")
        if info.get("active_stage") == stage:
            return info.get("budget_hours")
        for closed in info.get("closed_stages", []):
            if closed.get("stage") == stage:
                return closed.get("budget_hours")
        return None


class Result:
    def __init__(self, state: str, detail: str):
        self.state = state      # met | not-met | manual | skipped
        self.detail = detail

    @property
    def met(self) -> bool:
        return self.state == "met"


def evaluate(entry: dict, root: Path, rows: dict[str, str], budget: Budget) -> Result:
    evaluation = entry["evaluation"]
    kind = evaluation["kind"]
    if kind == "manual":
        return Result("manual", "no automatic evaluation: " + evaluation["why"].split(".")[0])
    comparison = evaluation["comparison"]
    threshold = float(evaluation["threshold"])
    if kind == "metrics":
        value = metrics_value(rows, evaluation["value"])
        shown = f"{value:g}"
        if "denominator" in evaluation:
            denominator = metrics_value(rows, evaluation["denominator"])
            if denominator == 0:
                raise LedgerError(f"{entry['id']}: the denominator row is zero")
            if evaluation.get("as") != "percent":
                raise LedgerError(f"{entry['id']}: a denominator needs \"as\": \"percent\"")
            shown = f"{value:g}/{denominator:g}"
            value = 100.0 * value / denominator
            shown = f"{value:.4g} ({shown})"
        source = f"METRICS.md row {evaluation['value']['row']!r}"
    elif kind == "receipt":
        receipt = root / evaluation["receipt"]
        if not receipt.is_file():
            raise LedgerError(f"{entry['id']}: receipt {evaluation['receipt']} is missing")
        try:
            raw = resolve(json.loads(receipt.read_text(encoding="utf-8")), evaluation["path"])
        except (ValueError, CitationError) as error:
            raise LedgerError(f"{entry['id']}: {evaluation['receipt']}: {error}") from None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise LedgerError(f"{entry['id']}: {evaluation['path']} is not a number ({raw!r})")
        value = float(raw)
        shown = f"{value:g}"
        source = f"{evaluation['receipt']} {evaluation['path']}"
    else:
        continuation = budget.continuation(entry)
        if continuation is not None:
            return Result("met" if continuation["due"] else "not-met",
                          metrics_module.continuation_text(continuation))
        value, note = budget.percent(evaluation["stage"])
        shown = f"{value:g}"
        source = f"budget: {note}"
        moved = budget.moved_halt(entry)
        if moved is not None:
            threshold, record = moved
            source += f"; halt moved to {threshold:g} % by the user's ruling {record}"
    fired = COMPARISONS[comparison](value, threshold)
    detail = (f"{shown} {COMPARISON_TEXT[comparison]} {threshold:g}"
              f" {evaluation['units']} [{source}]")
    return Result("met" if fired else "not-met", detail)


def precondition_holds(entry: dict, budget: Budget) -> tuple[bool, str]:
    precondition = entry.get("precondition")
    if precondition is None:
        return True, "no precondition"
    value, note = budget.percent(precondition["stage"])
    threshold = float(precondition["threshold"])
    holds = COMPARISONS[precondition["comparison"]](value, threshold)
    return holds, (f"{note}; precondition {value:g}"
                   f" {COMPARISON_TEXT[precondition['comparison']]} {threshold:g}"
                   f" is {'true' if holds else 'false'}")


# ---------------------------------------------------------------------------
# The sweep: criteria stated in the records
# ---------------------------------------------------------------------------

class Site:
    """A criterion as a record states it: where it is, and what it says."""

    def __init__(self, record: str, section: str, item: int, text: str):
        self.record = record
        self.section = section
        self.item = item
        self.text = normalise(text)

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.record, self.section, self.item)

    def __str__(self) -> str:
        return f"{self.record} :: {self.section} :: item {self.item}"


def sections(text: str) -> list[tuple[str, int, list[str]]]:
    """(heading line, level, body lines) for every heading in a markdown file."""
    out: list[tuple[str, int, list[str]]] = []
    current: tuple[str, int, list[str]] | None = None
    for line in text.splitlines():
        found = HEADING.match(line)
        if found:
            current = (line.strip(), len(found.group(1)), [])
            out.append(current)
        elif current is not None:
            current[2].append(line)
    return out


def numbered_items(body: list[str]) -> list[tuple[int, str]]:
    """Top-level ordered-list items.

    An item starts at an unindented `N. ` line and continues through indented
    or blank lines. It ends at the first unindented line that is not itself an
    item, so the paragraph that follows a list -- the G1 decision's
    toolchain-is-not-a-kill-criterion note, for one -- is not swallowed into
    the last criterion.
    """
    items: list[tuple[int, list[str]]] = []
    collecting: list[str] | None = None
    for line in body:
        found = LIST_ITEM.match(line)
        if found:
            collecting = [found.group(2)]
            items.append((len(items) + 1, collecting))
            continue
        if collecting is None:
            continue
        if not line.strip() or line[:1].isspace():
            collecting.append(line.strip())
            continue
        collecting = None
    return [(ordinal, " ".join(part for part in parts if part)) for ordinal, parts in items]


def decision_sites(root: Path) -> list[Site]:
    """Every numbered item under a decision record's "Kill criteria" heading."""
    found: list[Site] = []
    for path in sorted((root / DECISIONS).glob("*.md")):
        relative = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8")
        for heading, _level, body in sections(text):
            if not KILL_HEADING.search(heading):
                continue
            for ordinal, item in numbered_items(body):
                found.append(Site(relative, heading, ordinal, item))
    return found


def budget_halt_bullets(body: list[str]) -> list[str]:
    """The budget-protocol bullets (`- At **N %** ...`) that say `halt`.

    A bullet runs from its `- ` line through the indented lines under it; the
    next bullet, a blank line or an unindented line ends it.
    """
    bullets: list[list[str]] = []
    collecting: list[str] | None = None
    for line in body:
        if BUDGET_BULLET.match(line):
            collecting = [line]
            bullets.append(collecting)
            continue
        if collecting is not None and line.strip() and line[:1].isspace():
            collecting.append(line)
            continue
        collecting = None
    return [text for text in (" ".join(parts) for parts in bullets) if HALT_WORD.search(text)]


def design_sites(root: Path) -> list[Site]:
    """Every `Kill/pivot:` paragraph and budget-halt bullet, under its own heading.

    Within a heading the `Kill/pivot:` paragraphs are numbered first and the
    halt bullets after them, so the two shapes never share an item number.
    """
    path = root / DESIGN
    if not path.is_file():
        return []
    record = str(path.relative_to(root))
    found: list[Site] = []
    for heading, _level, body in sections(path.read_text(encoding="utf-8")):
        ordinal = 0
        collecting: list[str] | None = None
        for line in [*body, ""]:
            if KILL_PIVOT.match(line):
                ordinal += 1
                collecting = [line]
                continue
            if collecting is None:
                continue
            if line.strip():
                collecting.append(line)
                continue
            found.append(Site(record, heading, ordinal, " ".join(collecting)))
            collecting = None
        for bullet in budget_halt_bullets(body):
            ordinal += 1
            found.append(Site(record, heading, ordinal, bullet))
    return found


def budget_halts(sites: list[Site]) -> list[tuple[Site, float, str]]:
    """(site, percent, stage) for every halt on the budget the design states.

    Design 4.3's bullet applies to whichever stage is active ("active"); a
    stage paragraph's "At N %, halt." applies to its own stage.
    """
    out: list[tuple[Site, float, str]] = []
    for site in sites:
        if site.record != DESIGN:
            continue
        bullet = BUDGET_BULLET.match(site.text)
        if bullet:
            out.append((site, float(bullet.group(1)), "active"))
            continue
        stage = STAGE_HEADING.match(site.section)
        for found in STAGE_HALT.finditer(site.text):
            out.append((site, float(found.group(1)), stage.group(1) if stage else "active"))
    return out


def enforces_halt(entry: dict, percent: float, stage: str) -> bool:
    """Does this entry fail the gate whenever `stage` (or any stage) reaches `percent`?"""
    evaluation = entry["evaluation"]
    if evaluation["kind"] != "budget" or "precondition" in entry:
        return False
    if evaluation.get("measure", "percent") != "percent":
        return False
    if evaluation.get("stage") not in ({"active"} if stage == "active" else {"active", stage}):
        return False
    return evaluation["comparison"] == "ge" and float(evaluation["threshold"]) <= percent


def latest_closed_gate(root: Path, place: Callable[[str], Any] | None = None) -> str | None:
    """The newest gate with a decision record: the review horizon for manual entries.

    Newest is latest in `place`, the execution order (gate_order), not the highest
    number: under the user's order of 2026-10-07 G6 closes after G7, so once both
    have records the horizon is G6."""
    place = place or gate_order(root)
    gates = {f"G{found.group(1)}"
             for path in (root / DECISIONS).glob("*.md")
             for found in [GATE_IN_DECISION.search(path.name)] if found}
    return max(gates, key=place) if gates else None


def gate_number(gate: str) -> int:
    return int(GATE_NAME.fullmatch(gate).group(1))


def gate_order(root: Path) -> Callable[[str], Any]:
    """A gate's place in the execution order: G<n> sits where S<n> sits in stages.json's
    list (metrics.next_stage), which is not the number order once S7 is listed before S6
    (the user's order of 2026-10-07, Documentation/decisions/2026-10-07-release-order-and-installer.md).

    A gate whose stage the list does not hold (a miniature project lists a few stages)
    sits after the latest-placed listed gate with a lower number, and ahead of the next
    listed gate; among such gates, by number. The repository's list holds every stage
    the design names, so there every gate is placed by the list alone. Without
    stages.json the order is the gate numbers'. Places compare with each other only.
    """
    if not (root / STAGES).is_file():
        return gate_number
    try:
        stages = metrics_module.load_stages(root)
    except Exception as error:  # noqa: BLE001 - reported, never swallowed
        raise LedgerError(f"{STAGES}: the stage order could not be read: {error}") from None
    places = {f"G{found.group(1)}": index for index, stage in enumerate(stages)
              for found in [STAGE_NAME.fullmatch(str(stage["id"]))] if found}

    def place(gate: str) -> tuple[int, int]:
        if gate in places:
            return places[gate], 0
        number = gate_number(gate)
        below = [where for listed, where in places.items() if gate_number(listed) < number]
        return (max(below) if below else -1), number + 1

    return place


def active_gate(root: Path) -> str | None:
    """The gate under construction: G<n> for the active stage S<n>, from stages.json.

    None when the file is absent (a miniature project) or no stage is active.
    """
    if not (root / STAGES).is_file():
        return None
    try:
        stage = metrics_module.active_stage(metrics_module.load_stages(root))
    except Exception as error:  # noqa: BLE001 - reported, never swallowed
        raise LedgerError(f"{STAGES}: the active stage could not be read: {error}") from None
    if stage is None:
        return None
    found = STAGE_NAME.fullmatch(str(stage["id"]))
    if not found:
        raise LedgerError(f"{STAGES}: active stage id {stage['id']!r} is not S<n>")
    return f"G{found.group(1)}"


def design_budgets(root: Path) -> dict[str, float | None]:
    """Stage id -> its design heading's budget in hours, or None ("no fixed ceiling").

    Every `### S<n>.` heading is a design stage, with or without a figure.
    """
    path = root / DESIGN
    if not path.is_file():
        return {}
    found: dict[str, float | None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stage = STAGE_HEADING.match(line)
        if stage:
            budget = DESIGN_BUDGET.search(line)
            found[stage.group(1)] = float(budget.group(1)) if budget else None
    return found


def budget_ruling(root: Path, stage: str, hours: float) -> Path | None:
    """The user's record for a budget above the design's.

    Named `<date>-<stage>-budget-<hours>.md`, the figure in the name equal to
    `hours` exactly, and the same figure inside a quoted line (`> ...`): the
    project's records quote the user's own words in block quotes, and a budget
    above the design's is a threshold only the user may move (AGENTS.md rule
    10). This is a drift check, not a forgery check: it catches a budget
    edited in stages.json with no ruling behind it, the way a halt gets
    outrun.
    """
    for path in sorted((root / DECISIONS).glob(f"*-{stage}-budget-*.md")):
        named = RULING_NAME.search(path.name)
        if not named or named.group(1) != stage or float(named.group(2)) != hours:
            continue
        quoted = [line for line in path.read_text(encoding="utf-8").splitlines()
                  if QUOTED.match(line)]
        if any(float(number) == hours for line in quoted for number in NUMBER.findall(line)):
            return path
    return None


def budget_problems(root: Path) -> list[str]:
    """Every stage's budget is the design's, lower, or raised by the user's ruling.

    Every budget halt is a percentage of `budget_hours` in stages.json, so a
    raised figure moves the halt as surely as a changed threshold does. So
    does a figure that is not a positive finite number (a negative budget
    reads as a negative percentage, which no halt reaches), and so does a
    stage id the design does not name, which no per-stage check would see.
    Lowering a budget is a strengthening and needs nothing.
    """
    stated = design_budgets(root)
    if not stated or not (root / STAGES).is_file():
        return []
    try:
        stages = metrics_module.load_stages(root)
    except Exception as error:  # noqa: BLE001 - reported, never swallowed
        return [f"{STAGES}: cannot be read: {error}"]
    out: list[str] = []
    for stage in stages:
        identifier = str(stage.get("id"))
        if not STAGE_NAME.fullmatch(identifier) or identifier not in stated:
            out.append(f"{STAGES}: stage id {identifier!r} is not a stage the design names"
                       f" ({', '.join(sorted(stated))}); a stage the design does not name"
                       f" has no budget any halt can be checked against.")
            continue
        design = stated[identifier]
        hours = stage.get("budget_hours")
        if hours is None:
            if design is not None:
                out.append(
                    f"{STAGES}: {identifier} declares no budget, and the design gives it"
                    f" {design:g} h. With no budget no halt can be measured.")
            continue
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) \
                or not math.isfinite(hours) or hours <= 0:
            out.append(f"{STAGES}: {identifier} budget_hours {hours!r} is not a positive"
                       f" finite number of hours, so no percentage of it can reach a halt.")
            continue
        if design is not None and float(hours) <= design:
            continue
        if budget_ruling(root, identifier, float(hours)) is None:
            given = "no fixed ceiling" if design is None else f"{design:g} h"
            out.append(
                f"{STAGES}: {identifier}'s budget is {float(hours):g} h and the design gives it"
                f" {given}. A budget above the design's moves every halt measured"
                f" against it, so it is the user's (AGENTS.md rule 10): name the ruling"
                f" {DECISIONS}/<date>-{identifier}-budget-{float(hours):g}.md, with the"
                f" figure in the user's quoted words (a '> ' line).")
    out += halt_ruling_problems(root, stated)
    return out


def halt_ruling_problems(root: Path, stated: dict[str, float | None]) -> list[str]:
    """A record named as a moved halt that `metrics.halt_ruling` would not accept.

    A misnamed or unquoted ruling moves nothing, so the halt stays at 150 %;
    this names it so the mistake is seen rather than discovered at the halt.
    """
    out: list[str] = []
    stages: set[str] = set()
    for path in sorted((root / DECISIONS).glob("*-halt-*h.md")):
        named = metrics_module.HALT_RULING_NAME.search(path.name)
        if not named:
            continue
        stage = named.group(1)
        stages.add(stage)
        if stage not in stated:
            out.append(f"{DECISIONS}/{path.name}: names a halt for {stage}, which is not a stage"
                       f" the design names")
    for stage in sorted(stages):
        try:
            metrics_module.halt_ruling(root, stage)
        except metrics_module.MalformedInput as error:
            out.append(str(error))
            continue
        # An earlier ruling a later one superseded is history, not a problem; a
        # record named as a ruling that is not one is.
        accepted = {p for _, p in metrics_module.halt_rulings(root, stage)}
        for path in sorted((root / DECISIONS).glob(f"*-{stage}-halt-*h.md")):
            named = metrics_module.HALT_RULING_NAME.search(path.name)
            if named and named.group(1) == stage and path not in accepted:
                why = (metrics_module.halt_ruling_date_problem(path.name)
                       or (None if metrics_module.HALT_RULING_WHOLE.fullmatch(path.name)
                           else "its name is not <date>-<stage>-halt-<hours>h.md")
                       or (f"no quoted passage of the user's ('> ' lines), or question of the"
                           f" agent's the user answered yes, names {stage} and says"
                           f" {float(named.group(2)):g} hours"))
                out.append(
                    f"{DECISIONS}/{path.name}: names a halt at {float(named.group(2)):g} h for"
                    f" {stage}, and {why}; it moves nothing, and the halt stays where the"
                    f" latest accepted ruling, or the design's 150 %, puts it.")
    return out


def review_timing(review: dict, horizon: str | None, current: str | None,
                  place: Callable[[str], Any] = gate_number) -> str | None:
    """Why a review is not current, or None when it is.

    Earlier and later are by `place`, the execution order (gate_order; the run passes
    the repository's). With the gate numbers' order, a review dated G6 while G6 is
    under construction after G7 closed read stale, and a review dated G7 stayed
    current through G6's closure (the interrogation of the 2026-10-07 order).

    HEAD's rule until 2026-09-23 was "dated exactly at the latest closed gate",
    and that is kept. What it refused was a review made during a stage and
    dated at the gate under construction, which is the honest date for design
    4.3's 100 % review. Such a review must say `"before_closure": true`, and
    it then expires when that gate closes, exactly as a review dated at the
    previous gate would: it can never stand in for the gate's own closing
    review. So nothing current under this rule outlives what HEAD's rule
    allowed. A review dated beyond the gate under construction is refused
    outright, since nothing would ever expire it.
    """
    reviewed = place(review["at_gate"])
    early = review.get("before_closure", False)
    if horizon is not None:
        closed = place(horizon)
        if reviewed < closed or (reviewed == closed and early):
            return (f"last reviewed at {review['at_gate']}"
                    + (" before that gate closed" if early and reviewed == closed else "")
                    + f", and the latest closed gate is {horizon}")
        if reviewed == closed:
            return None
    elif current is None or reviewed < place(current):
        return None  # no gate has closed, so nothing can have expired it
    if current is None:
        return (f"reviewed at {review['at_gate']}, newer than the latest closed gate"
                f" {horizon}, and {STAGES} names no gate under construction to date it at")
    if reviewed == place(current):
        if early:
            return None
        return (f"reviewed at {review['at_gate']}, the gate still under construction, without"
                f" \"before_closure\": true. A review made before its gate closes has to say"
                f" so, because it cannot answer for the closing review and expires when"
                f" {review['at_gate']} closes")
    if reviewed > place(current):
        return (f"reviewed at {review['at_gate']}, a gate beyond the one under construction"
                f" ({current}); nothing would ever expire it")
    return (f"reviewed at {review['at_gate']}, which is neither the latest closed gate"
            f" ({horizon}) nor the gate under construction ({current})")


def evidence_findings(identifier: str, review: dict, rows: dict[str, str],
                      rows_error: str | None) -> list[str]:
    """A manual review still matches the row it was answered against.

    The resolution was given for the divergence it classified. Without this, a
    different divergence -- a runtime one uncovered before tick 100 by a new
    oracle, say -- would inherit the resolution until the next gate closed. An
    open review that names a row is held the same way: its answer was given for
    that row too.
    """
    against = review.get("answered_against")
    if against is None:
        return [f"{identifier}: status 'met-and-resolved', and its review does not name the"
                f" METRICS.md row it was answered against (review.answered_against ="
                f" {{\"row\": ..., \"value\": ...}}). A resolution is for the evidence it"
                f" classified; without the row a different divergence inherits it unseen."]
    row, value = against["row"], against["value"]
    if rows_error:
        return [f"{identifier}: the review was answered against METRICS.md row {row!r},"
                f" which cannot be read: {rows_error}"]
    if row not in rows:
        return [f"{identifier}: the review was answered against METRICS.md row {row!r}, and"
                f" METRICS.md has no such row"]
    if rows[row] != value:
        return [f"{identifier}: the review was answered against METRICS.md row {row!r} ="
                f" {value!r}, and it now reads {rows[row]!r}. The answer was given for that"
                f" evidence: answer the criterion again against the current row"
                f" (review.answered_against.value)."]
    return []


def first_commit_adding(root: Path, relative: str) -> str | None:
    """The oldest commit that added `relative`, or None (none has, or `root` has no git
    history: the commit under construction is then the one that adds it)."""
    try:
        done = subprocess.run(["git", "-C", str(root), "log", "--diff-filter=A",
                               "--format=%H", "--", relative],
                              capture_output=True, text=True, check=False)
    except OSError:
        return None
    found = done.stdout.split() if done.returncode == 0 else []
    return found[-1] if found else None


def projection_findings(identifier: str, review: dict, root: Path, rows: dict[str, str],
                        rows_error: str | None) -> list[str]:
    """A review's projection still reads what METRICS.md read where it was taken.

    `answered_against` follows the row at every re-answer; a projection must not, or the
    comparison a criterion promises against it is with whatever the row read last.
    """
    projection = review.get("projection")
    if projection is None:
        return []
    row, value = projection["row"], projection["value"]
    path = projection["fixed_at_the_commit_adding"]
    if not (root / path).exists():
        return [f"{identifier}: the review's projection is fixed at the commit adding {path},"
                f" which the tree does not hold"]
    commit = first_commit_adding(root, path)
    if commit is None:
        if rows_error:
            return [f"{identifier}: the review's projection is METRICS.md row {row!r} as the"
                    f" commit under construction reads it, which cannot be read: {rows_error}"]
        where, reads = f"the commit under construction, which adds {path}", rows.get(row)
    else:
        try:
            shown = subprocess.run(["git", "-C", str(root), "show",
                                    f"{commit}:{metrics_module.METRICS_FILE}"],
                                   capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            return [f"{identifier}: the review's projection is METRICS.md row {row!r} at"
                    f" {commit[:12]}, which cannot be read: {error}"]
        where = f"{commit[:12]}, the commit that added {path}"
        reads = metrics_module.parse_table(shown).get(row)
    if reads != value:
        return [f"{identifier}: the review's projection records METRICS.md row {row!r} ="
                f" {value!r}, and that row read {reads!r} at {where}. A projection is fixed"
                f" where it was taken: a re-answer carries it forward unchanged, and only the"
                f" user moves it (AGENTS.md rule 10)."]
    return []


def manual_findings(entry: dict, horizon: str | None, current: str | None,
                    budget_hours: float | None, rows: dict[str, str],
                    rows_error: str | None, root: Path | None = None,
                    place: Callable[[str], Any] = gate_number) -> list[str]:
    """What a manual criterion whose precondition holds still owes a human.

    `horizon` is the latest closed gate, `current` the gate under construction,
    `budget_hours` the budget the criterion's budget precondition is a
    percentage of (None when it has none), `rows` METRICS.md's rows (and
    `rows_error` why they could not be read), `place` the gates' execution order
    (gate_order).
    """
    identifier, status = entry["id"], entry["status"]
    review = entry.get("review")
    if review is None:
        if status == "open":
            return [f"{identifier}: no automatic evaluation and no recorded review."
                    f" A criterion a human must check is only checked if the ledger"
                    f" records who checked it, when, and what they found."]
        return [f"{identifier}: status {status!r} and no recorded review. A resolved"
                f" criterion no metric can evaluate holds only while a human keeps"
                f" answering it; record the review that found it met, and re-answer it"
                f" at every gate while its precondition holds."]
    out: list[str] = []
    if review["verdict"] == "met" and status == "open":
        # load_ledger refuses resolved_by on an open entry, so this is "met
        # and nothing answers it".
        out.append(
            f"KILL CRITERION MET AND UNRESOLVED (by review): {identifier}\n"
            + "\n".join(teach_b1(entry, Result(
                "met", f"reviewed at {review['at_gate']}: {review['evidence'][:160]}"))))
    stale = review_timing(review, horizon, current, place)
    if stale:
        out.append(
            f"{identifier}: {stale}. A criterion with no automatic evaluation has to be"
            f" answered by a human at every gate."
            f" What to check: {entry['evaluation']['human_check'][:200]}")
    if budget_hours is not None and review.get("budget_hours") != budget_hours:
        answered = review.get("budget_hours")
        out.append(
            f"{identifier}: its precondition is a percentage of a {budget_hours:g} h budget,"
            f" and the review answered it "
            + ("without recording the budget" if answered is None
               else f"at a {answered:g} h budget")
            + ". A re-budget moves the point the criterion applies from, so the review"
            f" has to be given again there (review.budget_hours = {budget_hours:g}).")
    if status == "met-and-resolved" or review.get("answered_against") is not None:
        out.extend(evidence_findings(identifier, review, rows, rows_error))
    if root is not None:
        out.extend(projection_findings(identifier, review, root, rows, rows_error))
    return out


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def teach_b1(entry: dict, result: Result) -> list[str]:
    """The failure message says what the incident taught, not just that it failed."""
    return [
        f"  measured: {result.detail}",
        f"  the record's consequence: {entry['consequence']}",
        "  This is the class of agents-journals/gates/2026-09-20-g3.md finding B1:",
        "  G1 kill criterion 3 was met on S3's second slice -- 5813 of 5817 jump-table",
        "  arms were guaranteed traps -- and the record stated the number without the",
        "  consequence. It was invisible to every gate for a whole stage. A criterion",
        "  that is met is answered by a decision record that says so (and by naming",
        "  that record in resolved_by), or by firing the pivot the criterion names.",
        "  Stating the number is not answering it.",
    ]


def run(root: Path, ledger_path: Path, verbose: bool) -> int:
    document = load_ledger(ledger_path)
    criteria = document["criteria"]
    failures: list[str] = []
    moved_halts: dict[str, tuple[float, str]] = {}
    continuations: dict[str, dict] = {}
    lines: list[str] = []

    rows: dict[str, str] = {}
    rows_error: str | None = None
    try:
        rows = metrics_rows(root)
    except LedgerError as error:
        rows_error = str(error)
    budget = Budget(root)

    # 1. Anchor every source, and collect what the ledger claims.
    sites = decision_sites(root) + design_sites(root)
    by_key = {site.key: site for site in sites}
    claimed: dict[tuple[str, str, int], str] = {}
    for entry in criteria:
        for source in entry["sources"]:
            key = (source["record"], source["section"], source["item"])
            if key in claimed:
                failures.append(
                    f"two ledger entries claim the same criterion: {claimed[key]} and"
                    f" {entry['id']} both name {source['record']} :: {source['section']}"
                    f" :: item {source['item']}")
            claimed[key] = entry["id"]
            path = root / source["record"]
            if not path.is_file():
                failures.append(f"{entry['id']}: source record {source['record']} is missing")
                continue
            body = None
            for heading, _level, section_body in sections(path.read_text(encoding="utf-8")):
                if heading == source["section"]:
                    body = "\n".join(section_body)
                    break
            if body is None:
                failures.append(
                    f"{entry['id']}: {source['record']} has no section"
                    f" {source['section']!r}; the ledger is anchored to a heading that"
                    " no longer exists")
                continue
            # Anchor to the claimed item when the sweep found one -- so an entry
            # that quotes criterion 1 while claiming criterion 3 is caught -- and
            # to the section otherwise, for a criterion the sweep does not
            # enumerate (design 4.3's halt rule is prose, not a `Kill/pivot:`
            # paragraph).
            site = by_key.get(key)
            where = f"item {source['item']}" if site else f"section {source['section']!r}"
            haystack = normalise(site.text) if site else normalise(body)
            if normalise(source["quote"]) not in haystack:
                failures.append(
                    f"{entry['id']}: the quoted text is not in {source['record']} at"
                    f" {where}. The criterion was reworded or removed out"
                    f" from under the ledger. Quote: {normalise(source['quote'])[:120]!r}")
        if entry["status"] in RESOLVED_STATUSES:
            record = entry["resolved_by"]["record"]
            if not (root / record).is_file():
                failures.append(
                    f"{entry['id']}: status {entry['status']!r} names a resolving record"
                    f" that does not exist: {record}")

    # 2. Sweep the records for criteria the ledger does not list.
    for site in sites:
        if site.key in claimed:
            continue
        failures.append(
            f"a kill criterion is stated in a record and is not in the ledger:\n"
            f"  {site}\n"
            f"  text: {site.text[:220]}\n"
            f"  An untracked criterion is the hole finding B1 went through: the record\n"
            f"  names a condition that stops the project, and no gate can see whether it\n"
            f"  holds. Add it to {LEDGER}, with an evaluation or an honest \"manual\".")

    # 3. Every halt the design puts on the budget is evaluated automatically,
    #    for every stage alike -- and the design still states design 4.3's, so
    #    rewording it away cannot leave the sweep enforcing nothing.
    halts = budget_halts(sites)
    if (root / DESIGN).is_file() and not any(stage == "active" for _, _, stage in halts):
        failures.append(
            f"{DESIGN} states no budget halt for every stage: no budget-protocol bullet"
            f" ('- At **N %** ...') that says 'halt' was found. Design 4.3's 150 % halt is"
            f" the one AGENTS.md rule 4 names; if it was reworded, the sweep must be taught"
            f" the new wording in the same commit, or the halt goes unenforced.")
    for site, percent, stage in halts:
        if any(enforces_halt(entry, percent, stage) for entry in criteria):
            continue
        scope = "the active stage" if stage == "active" else f"'active' or {stage!r}"
        failures.append(
            f"the design halts at {percent:g} % and no criterion enforces it:\n"
            f"  {site}\n"
            f"  text: {site.text[:220]}\n"
            f"  Design 4.3 calls this halt automatic. It is enforced only by a \"budget\"\n"
            f"  criterion on {scope} with comparison \"ge\", a threshold no higher than\n"
            f"  {percent:g} and no precondition. The ledger has no exemption for any stage:\n"
            f"  going past the halt is the user's to allow, by a new budget or a halt ruling\n"
            f"  (<date>-S<n>-halt-<hours>h.md), not the gate's.")

    # 4. ... and the budget those halts are a percentage of is the design's, or
    #    one the user set by a named ruling.
    failures.extend(budget_problems(root))

    # 5. Evaluate.
    current = active_gate(root)
    place = gate_order(root)
    horizon = latest_closed_gate(root, place)
    for entry in sorted(criteria, key=lambda e: e["id"]):
        identifier, status = entry["id"], entry["status"]
        kind = entry["evaluation"]["kind"]
        try:
            holds, precondition_note = precondition_holds(entry, budget)
        except LedgerError as error:
            failures.append(f"{identifier}: {error}")
            lines.append(f"  [{status:16s}] {identifier:28s} ERROR     {error}")
            continue

        if kind == "manual":
            review = entry.get("review")
            if status != "retired" and holds:
                precondition = entry.get("precondition")
                hours = None
                if precondition and precondition.get("kind") == "budget":
                    try:
                        hours = budget.hours(precondition["stage"])
                    except LedgerError as error:
                        failures.append(f"{identifier}: {error}")
                failures.extend(manual_findings(entry, horizon, current,
                                                None if hours is None else float(hours),
                                                rows, rows_error, root, place))
            state = ("manual/reviewed " + review["verdict"]) if review else "manual"
            if not holds:
                state = "manual/n-a"
            lines.append(f"  [{status:16s}] {identifier:28s} {state:22s} {precondition_note}")
            continue

        if rows_error and kind == "metrics":
            failures.append(f"{identifier}: {rows_error}")
            lines.append(f"  [{status:16s}] {identifier:28s} ERROR                  {rows_error}")
            continue
        try:
            result = evaluate(entry, root, rows, budget)
            moved = budget.moved_halt(entry) if kind == "budget" else None
            continuation = budget.continuation(entry) if kind == "budget" else None
        except LedgerError as error:
            failures.append(f"{identifier}: {error}")
            lines.append(f"  [{status:16s}] {identifier:28s} ERROR                  {error}")
            continue
        if moved is not None:
            moved_halts[identifier] = moved
        if continuation is not None:
            continuations[identifier] = continuation

        if not holds and result.met:
            lines.append(f"  [{status:16s}] {identifier:28s} {'met/precondition-false':22s}"
                         f" {result.detail} -- {precondition_note}")
            failures.append(
                f"{identifier}: the condition holds but its precondition does not"
                f" ({precondition_note}). That combination is a judgement call, not a"
                f" measurement; a human decides. {result.detail}")
            continue
        lines.append(f"  [{status:16s}] {identifier:28s} {result.state:22s} {result.detail}")
        if not result.met:
            continue
        if status == "open":
            failures.append(f"KILL CRITERION MET AND UNRESOLVED: {identifier}"
                            f" -- {entry['title']}\n" + "\n".join(teach_b1(entry, result)))
        elif status == "met-and-resolved":
            failures.append(
                f"A RESOLVED KILL CRITERION FIRES AGAIN: {identifier} -- {entry['title']}\n"
                f"  measured: {result.detail}\n"
                f"  resolved by: {entry['resolved_by']['record']}\n"
                f"  That record says: {entry['resolved_by']['what_it_decided'][:200]}\n"
                f"  The resolution stopped holding, so the criterion is open again:"
                f" either the work regressed or the decision was wrong.")
        else:
            failures.append(
                f"A RETIRED KILL CRITERION FIRES: {identifier} -- {entry['title']}\n"
                f"  measured: {result.detail}\n"
                f"  retired by: {entry['resolved_by']['record']}\n"
                f"  A retired criterion is one whose condition can no longer arise. It"
                f" arose. The retirement was wrong.")

    if verbose or failures:
        for line in lines:
            print(line)

    # A halt the user's ruling moved is evaluated at another threshold than its
    # ledger text says, so the marker and the summary carry the move (the halt
    # review, 2026-09-24): a threshold change stays visible in the gate summary.
    marker = hashlib.sha256(
        json.dumps({e["id"]: [e["status"], condition_text(e)]
                    + ([f"moved to {moved_halts[e['id']][0]:g} by {moved_halts[e['id']][1]}"]
                       if e["id"] in moved_halts else [])
                    + ([f"{continuations[e['id']]['comparison']} {metrics_module.continuation_hours_text(continuations[e['id']]['hours'])} h"
                        f" for {budget.info()['active_stage']} by {continuations[e['id']]['record']}"
                        f" sha256 {continuations[e['id']]['record_sha256']}"]
                       if e["id"] in continuations else [])
                    for e in criteria},
                   sort_keys=True).encode()).hexdigest()[:16]
    for identifier, (at, record) in sorted(moved_halts.items()):
        print(f"note: {identifier} evaluated at {at:g} % (ledger"
              f" {metrics_module.DESIGN_HALT_PERCENT:g}), the user's ruling {record}")
    for identifier, continuation in sorted(continuations.items()):
        print(f"note: {identifier} evaluated at binding active time > {metrics_module.continuation_hours_text(continuation['hours'])} h"
              f" (ledger {metrics_module.DESIGN_HALT_PERCENT:g} %),"
              f" the user's continuation {continuation['record']}")
    automatic = sum(1 for e in criteria if e["evaluation"]["kind"] != "manual")
    # A retired manual criterion needs nobody: its stage is closed and its
    # inputs are frozen. Counting it under "needs a human" would overstate
    # the standing work by exactly the entries that have none. A resolved
    # one still does (section 4 of the docstring).
    manual = sum(1 for e in criteria
                 if e["evaluation"]["kind"] == "manual" and e["status"] != "retired")
    resolved = sum(1 for e in criteria if e["status"] == "met-and-resolved")
    from_decisions = sum(1 for site in sites if site.record != DESIGN)
    print(f"sites swept: {len(sites)} ({from_decisions} decision item(s),"
          f" {len(sites) - from_decisions} design site(s));"
          f" latest closed gate: {horizon}; gate under construction: {current}")

    if failures:
        for failure in failures:
            print(f"kill criteria FAIL: {failure}", file=sys.stderr)
        print(f"kill criteria FAIL: {len(failures)} finding(s) over {len(criteria)}"
              f" tracked criteria; marker_sha256={marker}", file=sys.stderr)
        return 1
    print(f"kill criteria OK: {len(criteria)} tracked, {automatic} evaluated automatically,"
          f" {manual} manual and needing a human, {resolved} met and resolved,"
          f" 0 firing; marker_sha256={marker}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--ledger", type=Path, default=None,
                        help=f"the ledger (default: {LEDGER} under the root)")
    parser.add_argument("--verbose", action="store_true",
                        help="print every criterion's line even when the gate passes")
    arguments = parser.parse_args(argv)
    root = project_root(arguments.root)
    ledger_path = arguments.ledger or (root / LEDGER)
    if not ledger_path.is_file():
        print(f"kill criteria MISSING: {ledger_path}", file=sys.stderr)
        return 1
    try:
        return run(root, ledger_path, arguments.verbose)
    except LedgerError as error:
        print(f"kill criteria FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
