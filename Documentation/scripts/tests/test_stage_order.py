"""The stage order: stages.json's list, not the ids, is the order the stages run in.

The defect this exists for
--------------------------
The user's order of 2026-10-07 (Documentation/decisions/2026-10-07-release-order-and-installer.md)
runs G5, then portability through G7, then breadth through G6. metrics.stage_bounds
already started each stage at the gate of the entry before it in the list, but
gate_report.py took "the next stage" and "the previous gate" by id, S(n+1) and S(n-1),
and check_kill_criteria.py took "newer" and "older" gates by number. Its three-model
interrogation (2026-10-10) found each reading on a scratch copy: G5's question 6 priced S6
("no fixed ceiling: consistent") with S7 listed after S5; G7's previous review read S6,
which has no gate commit; and after G7 closed a review dated G6 read stale while one
dated G7 stayed current through G6's closure.

These tests hold the real list to the user's order, the design's entry gates to the
list, and the kill gate's dating to the list's order.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_kill_criteria as K  # noqa: E402
import metrics as M  # noqa: E402

ROOT = SCRIPTS.parents[1]
DESIGN = ROOT / "Documentation/SA2_RECOMP_DESIGN.md"
STAGE_HEADING = re.compile(r"^### (S\d+)\. ")
# The entry gate as written, or as a dated amendment rewrites it: the last such line in
# the stage's section is the one in force.
ENTRY = re.compile(r"^Entry(?:, as amended \d{4}-\d{2}-\d{2})?: (.*)$")


def test_the_real_list_is_the_users_order() -> None:
    """S7 follows S5 and S6 follows S7; S8 is last. Each stage's neighbours are read from
    the list, so G5 opens S7, G7 opens S6 and G6 opens S8."""
    stages = M.load_stages(ROOT)
    assert [s["id"] for s in stages] == ["S0", "S1", "S2", "S3", "S4", "S5", "S7", "S6", "S8"]
    following = {s["id"]: (M.next_stage(stages, s["id"]) or {}).get("id") for s in stages}
    assert following == {"S0": "S1", "S1": "S2", "S2": "S3", "S3": "S4", "S4": "S5",
                         "S5": "S7", "S7": "S6", "S6": "S8", "S8": None}
    preceding = {s["id"]: (M.previous_stage(stages, s["id"]) or {}).get("id") for s in stages}
    assert preceding == {"S0": None, "S1": "S0", "S2": "S1", "S3": "S2", "S4": "S3",
                         "S5": "S4", "S7": "S5", "S6": "S7", "S8": "S6"}
    assert M.next_stage(stages, "S9") is None and M.previous_stage(stages, "S9") is None


def design_entries(text: str) -> dict[str, str]:
    """Stage id -> the entry line in force in its design section (the last Entry line)."""
    found: dict[str, str] = {}
    stage = None
    for line in text.splitlines():
        heading = STAGE_HEADING.match(line)
        if heading:
            stage = heading.group(1)
            continue
        if line.startswith("## "):
            stage = None
            continue
        entry = ENTRY.match(line)
        if stage and entry:
            found[stage] = entry.group(1)
    return found


def test_each_design_entry_names_the_gate_of_the_stage_listed_before_it() -> None:
    """The design's entry gates and the list say the same order: a stage's entry in force
    (a dated "Entry, as amended" line where one was added, else the original "Entry:")
    names the gate of the stage before it in stages.json. A reorder that changes one and
    not the other fails here. S0 enters on the document's adoption; S8's section has no
    Entry line."""
    stages = M.load_stages(ROOT)
    entries = design_entries(DESIGN.read_text(encoding="utf-8"))
    assert sorted(entries) == ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    assert entries["S0"] == "this document adopted."
    for stage in stages:
        if stage["id"] not in entries or stage["id"] == "S0":
            continue
        before = M.previous_stage(stages, stage["id"])
        assert entries[stage["id"]] == f"G{before['id'][1:]}.", (stage["id"], entries)


def test_an_entry_amended_away_from_the_list_fails() -> None:
    """The check above can fail: the same parse over a design whose S7 amendment names G6
    disagrees with the list, which puts S5 before S7."""
    text = DESIGN.read_text(encoding="utf-8")
    moved = text.replace("Entry, as amended 2026-10-10: G5.", "Entry, as amended 2026-10-10: G6.")
    assert moved != text
    before = M.previous_stage(M.load_stages(ROOT), "S7")
    assert design_entries(text)["S7"] == f"G{before['id'][1:]}." == "G5."
    assert design_entries(moved)["S7"] == "G6."


def write_stages(root: Path, ids: list[str], closed: set[str]) -> None:
    """A stage file in `ids`' order: S4 and earlier by commit spacing, S5 on from
    transcripts; the stages in `closed` carry a gate commit."""
    stages = []
    for ident in ids:
        stage = {"id": ident, "title": ident, "budget_hours": 10,
                 "start_commit": "abc1234" if ident == ids[0] else None,
                 "gate_commit": "def5678" if ident in closed else None}
        if int(ident[1:]) >= 5:
            stage["main_thread"] = "transcripts"
            stage["start_commit"] = None
        stages.append(stage)
    path = root / "Documentation/stages.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "sa2-recomp-stages/v1", "idle_gap_hours": 2.0,
                                "stages": stages}), encoding="utf-8")


def test_load_stages_refuses_an_id_listed_twice(tmp_path: Path) -> None:
    """An id listed twice has two places in the order, and which follows its gate would
    depend on which one a reader found first."""
    write_stages(tmp_path, ["S4", "S5", "S7", "S5"], set())
    with pytest.raises(M.MalformedInput, match=r"stage ids listed more than once: \['S5'\]"):
        M.load_stages(tmp_path)


def decision(root: Path, name: str) -> None:
    path = root / "Documentation/decisions" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# a record\n", encoding="utf-8")


def test_the_kill_gate_dates_reviews_in_the_list_s_order(tmp_path: Path) -> None:
    """G5, then G7, then G6. With S6 active after G7 closed, a review dated G6 before its
    closure is current (by number it read stale: G6 < G7); once G6 has closed it is the
    horizon, so a review dated G7 is stale (by number G7 stayed the horizon and the G7
    review current through G6's closure)."""
    write_stages(tmp_path, ["S4", "S5", "S7", "S6", "S8"], {"S4", "S5", "S7"})
    place = K.gate_order(tmp_path)
    assert [g for g in sorted(["G8", "G6", "G7", "G5", "G4"], key=place)] == [
        "G4", "G5", "G7", "G6", "G8"]
    decision(tmp_path, "2026-10-10-G5-closed.md")
    decision(tmp_path, "2026-10-20-G7-closed.md")
    assert K.latest_closed_gate(tmp_path) == "G7"
    assert K.active_gate(tmp_path) == "G6"
    early_g6 = {"at_gate": "G6", "before_closure": True}
    assert K.review_timing(early_g6, "G7", "G6", place) is None
    assert K.review_timing(early_g6, "G7", "G6") == (
        "last reviewed at G6, and the latest closed gate is G7")      # the numbers' reading
    assert K.review_timing({"at_gate": "G7"}, "G7", "G6", place) is None
    # A review dated at a gate after the one under construction never expires: refused.
    assert "a gate beyond the one under construction" in K.review_timing(
        {"at_gate": "G8"}, "G7", "G6", place)
    # G6 closes: it is the horizon, though G7 is the higher number.
    decision(tmp_path, "2026-11-01-G6-closed.md")
    write_stages(tmp_path, ["S4", "S5", "S7", "S6", "S8"], {"S4", "S5", "S7", "S6"})
    place = K.gate_order(tmp_path)
    assert K.latest_closed_gate(tmp_path) == "G6"
    assert K.review_timing({"at_gate": "G7"}, "G6", "G8", place) == (
        "last reviewed at G7, and the latest closed gate is G6")
    assert K.review_timing({"at_gate": "G6"}, "G6", "G8", place) is None
    assert K.review_timing(early_g6, "G6", "G8", place) == (
        "last reviewed at G6 before that gate closed, and the latest closed gate is G6")


def test_a_gate_the_list_does_not_hold_follows_the_listed_gate_below_it(tmp_path: Path) -> None:
    """A miniature project lists a few stages: an unlisted gate sits after the latest-placed
    listed gate with a lower number, so a review dated G5 with S3 and S4 listed is beyond
    G4, as by number; among unlisted gates the numbers decide."""
    write_stages(tmp_path, ["S3", "S4"], {"S3"})
    place = K.gate_order(tmp_path)
    assert sorted(["G5", "G4", "G1", "G3", "G2"], key=place) == ["G1", "G2", "G3", "G4", "G5"]
    assert "a gate beyond the one under construction (G4)" in K.review_timing(
        {"at_gate": "G5"}, "G3", "G4", place)
    write_stages(tmp_path, ["S4", "S5", "S7", "S6"], {"S4"})
    place = K.gate_order(tmp_path)
    assert sorted(["G9", "G6", "G8", "G7", "G5", "G1"], key=place) == [
        "G1", "G5", "G7", "G6", "G8", "G9"]
    decision(tmp_path, "2026-10-10-G9-misnamed.md")
    decision(tmp_path, "2026-10-10-G7-closed.md")
    assert K.latest_closed_gate(tmp_path) == "G9"


def test_without_a_stage_file_the_order_is_the_numbers(tmp_path: Path) -> None:
    """A miniature project with no stages.json keeps the numbers' order, as before."""
    decision(tmp_path, "2026-09-20-G3-x.md")
    decision(tmp_path, "2026-09-19-G1-y.md")
    assert K.gate_order(tmp_path) is K.gate_number
    assert K.latest_closed_gate(tmp_path) == "G3"
