"""The S4 exit predicates, exercised in both directions against fixture metrics.

`Documentation/gate-criteria.json`'s G4 criteria read the real `METRICS.md` and
are unmet today, which is correct for an open stage -- and it means
`check_criterion_clauses.py` *defers* their falsifiers rather than running
them, by its own documented design for a criterion whose control run fails. So
the passing direction is exercised here instead, against fixture text, or it
would not be exercised at all until the day S4 closed.

Why this file exists at all: on 2026-09-22 M5 read `0 replays` and M6 read
`booting 0/66`, both with status `measured (stage not started)`, and
`gate_report.py`'s 4.7 backstop only fails on `not measured` -- so G4's verdict
would have read PASS with zero replays and zero stages booting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage_exit as S  # noqa: E402

HEADER = "| Metric | Value | Denominator | Status |\n|---|---|---|---|\n"


def metrics(tmp_path: Path, m4: str, m5: str, m6: str) -> Path:
    p = tmp_path / "METRICS.md"
    p.write_text(HEADER
                 + f"| M4 imports | {m4} | x | measured |\n"
                 + f"| M5 replays | {m5} | x | measured |\n"
                 + f"| M6 stages | {m6} | x | measured |\n")
    return p


MET4 = "251/251 (100.0%); reachable on replays 118/118"
# A real residue record, so the met direction exercises the record lookup and
# not a string shape: Documentation/receipts/residues/pc53-double-rounding-v1.json.
MET5 = "mh_start no divergence/1800; garden 900/900 (residue pc53-double-rounding-v1: float_tail)"
MET6 = ("booting 1/66 (1.5%); replay complete 1/66 (1.5%); "
        "booting with replay complete 1/66 (1.5%)")


def test_all_three_met(tmp_path: Path) -> None:
    p = metrics(tmp_path, MET4, MET5, MET6)
    for key, f in S.PREDICATES.items():
        assert f(p).met, f"{key} should be met: {f(p).detail}"


@pytest.mark.parametrize("m4,m5,m6,unmet", [
    # M4: a ratio over an empty replay set is not 100 percent, it is nothing.
    ("251/251 (100.0%); reachable on replays 0/0", MET5, MET6, "M4"),
    ("251/251 (100.0%); reachable on replays 117/118", MET5, MET6, "M4"),
    ("251/251 (100.0%)", MET5, MET6, "M4"),
    # M5: no retained replay is not "every replay is clean".
    (MET4, "0 replays", MET6, "M5"),
    (MET4, "mh_start 42/1800", MET6, "M5"),          # divergence with no label
    (MET4, "not measured (input absent: x)", MET6, "M5"),
    # A class is not a label. `bug` is the comparer's default, and until
    # 2026-09-22 any `(class: field)` read as labelled, so this was met.
    (MET4, "boot 369/368 (bug: <lifted run ended>)", MET6, "M5"),
    (MET4, "garden 900/900 (residue: float_tail)", MET6, "M5"),   # the old shape
    (MET4, "garden 900/900 (residue no-such-record-v1: float_tail)", MET6, "M5"),
    (MET4, "mh_start no divergence/1800; boot 369/368 (bug: frame_count)", MET6, "M5"),
    # A part is matched whole. Each of these passed the substring test that
    # preceded it (2026-09-22 review).
    (MET4, "a 5/9 (bug: no divergence/9)", MET6, "M5"),
    (MET4, "x(residue pc53-double-rounding-v1: y) 5/9 (bug: f)", MET6, "M5"),
    (MET4, "a 5/9 (residue pc53-double-rounding-v1: f) and more", MET6, "M5"),
    (MET4, "a 5/9 (unrepeatable: native repeat)", MET6, "M5"),
    (MET4, "a 5/9 (residue ../receipts/s4-run-reach-v1: f)", MET6, "M5"),
    # A replay that judged no tick has shown nothing.
    (MET4, "boot no divergence/0", MET6, "M5"),
    (MET4, "mh_start no divergence/1800; boot no divergence/0", MET6, "M5"),
    # M6: a structural zero on either half.
    (MET4, MET5, "booting 0/66 (0.0%); replay complete 0/66 (0.0%); "
                 "booting with replay complete 0/66 (0.0%)", "M6"),
    (MET4, MET5, "booting 1/66 (1.5%); replay complete 0/66 (0.0%); "
                 "booting with replay complete 0/66 (0.0%)", "M6"),
    (MET4, MET5, "booting 0/66 (0.0%); replay complete 1/66 (1.5%); "
                 "booting with replay complete 0/66 (0.0%)", "M6"),
    # M6: a stage that boots and a replay that completes on another stage. The
    # criterion is one stage booting *with* a replay reaching its goal; until
    # 2026-09-23 the predicate read the two counts apart and this was met.
    (MET4, MET5, "booting 1/66 (1.5%); replay complete 1/66 (1.5%); "
                 "booting with replay complete 0/66 (0.0%)", "M6"),
    # The row as it read before the third count: no same-stage count, no pass.
    (MET4, MET5, "booting 1/66 (1.5%); replay complete 1/66 (1.5%)", "M6"),
    # A part is matched whole, as for M5.
    (MET4, MET5, "booting 1/66 junk; replay complete 1/66 (1.5%); "
                 "booting with replay complete 1/66 (1.5%)", "M6"),
    # "booting with replay complete" is not read as "booting".
    (MET4, MET5, "booting with replay complete 1/66 (1.5%); replay complete 1/66 (1.5%)", "M6"),
    # A same-stage count larger than either list cannot come from two lists.
    (MET4, MET5, "booting 1/66 (1.5%); replay complete 0/66 (0.0%); "
                 "booting with replay complete 1/66 (1.5%)", "M6"),
    # ... and larger than both, with both lists non-empty: only the guard
    # fails it (the M6 plumbing review, N2).
    (MET4, MET5, "booting 1/66 (1.5%); replay complete 1/66 (1.5%); "
                 "booting with replay complete 2/66 (3.0%)", "M6"),
    # A part prepended to a met row: the first part carrying a label is the
    # one read, whichever of the three it is.
    (MET4, MET5, "replay complete 0/66 (0.0%); " + MET6, "M6"),
    (MET4, MET5, "booting 0/66 (0.0%); " + MET6, "M6"),
    (MET4, MET5, "booting with replay complete 0/66 (0.0%); " + MET6, "M6"),
])
def test_unmet_shapes(tmp_path: Path, m4: str, m5: str, m6: str, unmet: str) -> None:
    p = metrics(tmp_path, m4, m5, m6)
    assert not S.PREDICATES[unmet](p).met, S.PREDICATES[unmet](p).detail


def m6_falsifiers() -> list[tuple[str, dict]]:
    """G4's M6 criterion's falsifiers, as Documentation/gate-criteria.json holds
    them, each named by its clause and place."""
    doc = json.loads((ROOT / "Documentation" / "gate-criteria.json").read_text())
    entry = next(e for e in doc["G4"] if e["criterion"].startswith("M6:"))
    return [(f"clause{c}-falsifier{k}", f) for c, clause in enumerate(entry["clauses"])
            for k, f in enumerate(clause.get("falsifiers", []))]


@pytest.mark.parametrize("where,falsifier", m6_falsifiers(), ids=[w for w, _ in m6_falsifiers()])
def test_every_m6_falsifier_turns_a_met_row_unmet(tmp_path: Path, where: str, falsifier: dict) -> None:
    """check_criterion_clauses.py defers these while M6 is unmet in the real
    METRICS.md, so they are run here against a met row, as that gate applies
    them: the named text must occur exactly as often as declared, and every
    occurrence is replaced. The clause note in gate-criteria.json says this
    test runs each one."""
    p = metrics(tmp_path, MET4, MET5, MET6)
    assert S.m6(p).met, S.m6(p).detail
    assert falsifier["file"] == "METRICS.md" and "absent" not in falsifier, where
    text = p.read_text()
    assert text.count(falsifier["replace"]) == falsifier.get("occurrences", 1), where
    p.write_text(text.replace(falsifier["replace"], falsifier["with"]))
    assert not S.m6(p).met, f"{where} leaves M6 met: {S.m6(p).detail}"


def test_every_m6_clause_carries_a_falsifier() -> None:
    doc = json.loads((ROOT / "Documentation" / "gate-criteria.json").read_text())
    entry = next(e for e in doc["G4"] if e["criterion"].startswith("M6:"))
    assert entry["clauses"] and all(c.get("falsifiers") for c in entry["clauses"])
    assert len(m6_falsifiers()) >= 4


def test_m6_reads_the_three_counts_in_any_order(tmp_path: Path) -> None:
    """The met direction does not depend on the order metrics.py writes the parts in."""
    p = metrics(tmp_path, MET4, MET5, "booting with replay complete 1/66 (1.5%); "
                                      "booting 2/66 (3.0%); replay complete 1/66 (1.5%)")
    assert S.m6(p).met, S.m6(p).detail


def test_a_residue_label_counts_only_while_its_record_exists(tmp_path: Path) -> None:
    """Both directions over one text: the same cell is met with the record on
    disk and unmet without it, so the lookup is what decides."""
    residues = tmp_path / "residues"
    residues.mkdir()
    p = metrics(tmp_path, MET4, "boot 12/368 (residue fsin-tail-v1: g_x)", MET6)
    assert not S.m5(p, residues).met
    (residues / "fsin-tail-v1.json").write_text("{}")
    assert S.m5(p, residues).met


def test_absent_rows_are_unmet(tmp_path: Path) -> None:
    p = tmp_path / "METRICS.md"
    p.write_text(HEADER)
    for key, f in S.PREDICATES.items():
        assert not f(p).met, f"{key} should be unmet when its row is absent"


def test_the_tree_as_it_stands_reads_each_exit_predicate_as_measured() -> None:
    """The predicates must say what the real file says, not only fixtures. Since U6
    landed on the re-take batch (unit IN, 2026-09-26) all three read met: M4, the
    imports the replays reach all implemented; M5, both retained replays judged to
    the ends of their captures with residues only, in the classes the user ruled;
    M6, a stage booting with a registered replay of it reaching its goal. Until then
    M5 read unmet, both replays first diverging on a bug. This names each, so a
    predicate whose reading changes is a change somebody sees, and the push that
    changes any changes this assertion deliberately. The predicates reading met do
    not close G4: its other criteria, the reach reproduction at a clean commit, and
    the three-model interrogation stand."""
    real = Path(__file__).resolve().parents[3] / "METRICS.md"
    got = {key: f(real) for key, f in S.PREDICATES.items()}
    assert {k: r.met for k, r in got.items()} == {"M4": True, "M5": True, "M6": True}, \
        {k: r.detail for k, r in got.items()}


# ------------------------------------------------ residue classes (2026-09-26)
#
# The user's direction of 2026-09-26, decisions 2 and 3
# (Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md): residue
# classes of words. M5's part names the classes a replay's words were seen to
# differ on, beside its first divergence or alone; alone, it is end-of-capture.

STEAM, TIMING = "host-services-steam-stats-v1", "host-timing-v1"
BOTH = f"{STEAM}, {TIMING}"


def test_a_replay_judged_to_its_end_with_residues_only_is_met(tmp_path: Path) -> None:
    """The real class records, read from the tree's residues directory."""
    p = metrics(tmp_path, MET4, f"boot residues only/1200 ({BOTH}); "
                                f"metalharbor residues only/15585 ({TIMING})", MET6)
    assert S.m5(p).met, S.m5(p).detail
    p = metrics(tmp_path, MET4, f"boot residues only/1200 ({BOTH}) from 380 (unrepeatable); "
                                f"metalharbor residues only/15585 ({TIMING}) from 4", MET6)
    assert S.m5(p).met, S.m5(p).detail


@pytest.mark.parametrize("m5", [
    # a divergence beside residue classes is the divergence
    f"boot 383/1200 (bug: d_01c00000) + residues ({BOTH})",
    f"boot 122/1200 (unrepeatable: native repeat) + residues ({STEAM})",
    # residues only over no tick shows nothing, as `no divergence/0` does not
    f"boot residues only/0 ({STEAM})",
    # every class named must be a class record on disk
    "boot residues only/1200 (no-such-class-v1)",
    f"boot residues only/1200 ({STEAM}, no-such-class-v1)",
    # a numeric residue record is not a class of words
    "boot residues only/1200 (pc53-double-rounding-v1)",
    # a class of words is never a cell's label: its words leave the comparison
    f"boot 383/1200 (residue {TIMING}: d_01c00000)",
    # the part is matched whole
    f"boot residues only/1200 ({BOTH}) and more",
    f"boot residues only/1200 ({STEAM},{TIMING})",
    "boot residues only/1200 ()",
    # a `from` tick's class is a window's end, never another word (finding 4)
    f"boot residues only/1200 ({STEAM}) from 4 (bug)",
    f"boot residues only/1200 ({STEAM}) from 4 (residue)",
    f"boot residues only/1200 ({STEAM}) (unrepeatable)",
])
def test_residue_class_shapes_that_are_not_met(tmp_path: Path, m5: str) -> None:
    p = metrics(tmp_path, MET4, f"mh_start no divergence/1800; {m5}", MET6)
    assert not S.m5(p).met, S.m5(p).detail


@pytest.mark.parametrize("rec,part", [
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
      "class": None}, "boot no divergence/1200"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
      "class": None, "residues": []}, "boot no divergence/1200"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
      "class": "residue", "residues": [STEAM, TIMING]}, f"boot residues only/1200 ({BOTH})"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": 383, "field": "d_01c00000",
      "class": "bug", "residues": [STEAM, TIMING]},
     f"boot 383/1200 (bug: d_01c00000) + residues ({BOTH})"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": 383, "field": "d_01c00000",
      "class": "bug"}, "boot 383/1200 (bug: d_01c00000)"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": 383, "field": "d_01c00000",
      "class": "bug", "residues": [STEAM, TIMING], "residues_from": 4},
     f"boot 383/1200 (bug: d_01c00000) + residues ({BOTH}) from 4"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
      "class": "residue", "residues": [STEAM], "residues_from": 4},
     f"boot residues only/1200 ({STEAM}) from 4"),
    # a `from` tick that is a window's end carries its class (the fold review of
    # fold:rc, finding 4), so it never reads as a lifted difference
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
      "class": "residue", "residues": [STEAM], "residues_from": 380,
      "residues_from_class": "unrepeatable"},
     f"boot residues only/1200 ({STEAM}) from 380 (unrepeatable)"),
    ({"name": "boot", "ticks": 1200, "first_divergent_tick": 900, "field": "d_01c00000",
      "class": "bug", "residues": [STEAM, TIMING], "residues_from": 600,
      "residues_from_class": "bounded"},
     f"boot 900/1200 (bug: d_01c00000) + residues ({BOTH}) from 600 (bounded)"),
    ({"name": "garden", "ticks": 900, "first_divergent_tick": 900, "field": "float_tail",
      "class": "residue pc53-double-rounding-v1"},
     "garden 900/900 (residue pc53-double-rounding-v1: float_tail)"),
])
def test_m5_part_is_the_one_rendering_part_reads_back(rec: dict, part: str) -> None:
    """metrics.py writes each replay's part with m5_part, and PART reads every
    shape it writes back whole, so the row and the predicate cannot drift."""
    assert S.m5_part(rec) == part
    m = S.PART.match(part)
    assert m and m.group("name") == rec["name"]
    if rec.get("residues") and rec["first_divergent_tick"] is None:
        assert m.group("rids").split(", ") == rec["residues"]
    elif rec.get("residues"):
        assert m.group("sids").split(", ") == rec["residues"]
