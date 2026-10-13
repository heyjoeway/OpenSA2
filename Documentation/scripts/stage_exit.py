#!/usr/bin/env python3
"""Design section 5's S4 exit criteria, read off METRICS.md.

Why these are code rather than three inline one-liners in
`gate-criteria.json`: they are the three sentences that decide whether S4 is
finished, they need testing against a metrics file that says "met" (which
`METRICS.md` does not yet say), and a predicate nobody can test is the shape
of defect this project keeps finding.

The design's text, quoted:

    Exit: M4 >= the set reachable on the replays (every import hit by a replay
    is implemented; unreachable ones may remain stubs); M5 on every retained
    replay: first divergent tick is either end-of-capture or a labelled
    residue; M6 >= 1 stage booting with a replay reaching the goal.

Each predicate is **unmet on a structural zero**, and that is the point. On
2026-09-22 M5 read `0 replays` and M6 read `booting 0/66`, both with status
`measured (stage not started)` rather than `not measured` -- so
`gate_report.py`'s section 4.7 backstop, which only fails on `not measured`,
would have answered its standing questions and let G4's verdict read PASS with
no replays and no stage booting. "No replays yet" is not "every replay is
clean"; a criterion that cannot tell them apart is not a criterion.

Usage: imported by G4's criteria in `Documentation/gate-criteria.json`, and
tested in `Documentation/scripts/tests/test_stage_exit.py` against fixture
metrics text in both directions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    met: bool
    detail: str


def row(text: str, metric: str) -> str:
    """The value cell of one METRICS.md row, or "" when the row is absent."""
    m = re.search(rf"^\|\s*{re.escape(metric)}\s*\|([^|]*)\|", text, re.M)
    return m.group(1).strip() if m else ""


def read(path: str | Path) -> str:
    return Path(path).read_text()


def m4(path: str | Path = "METRICS.md") -> Result:
    """Every import a retained replay reaches is implemented.

    `reachable on replays a/b` with b == 0 is unmet, not met: a ratio over an
    empty set is the saturated-denominator failure S3 already had once.
    """
    v = row(read(path), "M4 imports")
    m = re.search(r"reachable on replays (\d+)/(\d+)", v)
    if not m:
        return Result(False, f"{v or 'row absent'} (no replay-reachable ratio)")
    hit, total = int(m.group(1)), int(m.group(2))
    if total == 0:
        return Result(False, f"{v} (no replay has been run, so the set is empty)")
    return Result(hit == total, v)


RESIDUES = Path(__file__).resolve().parents[1] / "receipts" / "residues"
# A residue class of words (tools/s4/tick_replay.py, the user's direction of
# 2026-09-26): only a record of this schema may be named as one.
CLASS_SCHEMA = "sa2-residue-class/v1"

# One replay in M5's cell, exactly as metrics.py renders it (`m5_part`): one of
#   <name> no divergence/<ticks>
#   <name> residues only/<ticks> (<id>, <id>) from <tick>
#   <name> <tick>/<ticks> (<class>: <field>)
#   <name> <tick>/<ticks> (<class>: <field>) + residues (<id>, <id>) from <tick>
# where `from <tick>` is the literal first divergence the classes absorbed (the
# comparer's `unclassed` tick), so design 8's "first divergent tick" and 4.7's
# question 4 stay answerable from the row, not only its first non-residue tick.
# A plain `from <tick>` is a lifted difference (with no class, a bug there); when
# the reading with no class is the end of a window instead, its class follows the
# tick, `from <tick> (unrepeatable)` or `from <tick> (bounded)`, so a native
# repeat or the lifted run's poll budget never reads as a lifted difference (the
# fold review of fold:rc, finding 4).
# Matched whole. A substring test let a replay whose name contained
# `no divergence/` pass with a bug in it (2026-09-22 review).
# A replay that judged no tick has shown nothing, so `no divergence/0` is not
# end-of-capture: the clean form must count at least one tick (2026-09-22,
# when the boot replay's cell first read `no divergence/1200`), and so must
# the residue-only form. The residues a replay names are the residue classes
# whose words it was seen to differ on (2026-09-26): beside a divergence they
# change nothing about it, and alone they are end-of-capture with residues only.
ID = r"[A-Za-z0-9._-]+"
IDS = rf"{ID}(?:, {ID})*"
FROM_CLASSES = ("unrepeatable", "bounded")       # a `from` tick that is not a lifted difference
FROM = rf" from \d+(?: \((?:{'|'.join(FROM_CLASSES)})\))?"
PART = re.compile(rf"(?P<name>{ID}) (?:no divergence/(?P<ticks>\d+)"
                  rf"|residues only/(?P<rticks>\d+) \((?P<rids>{IDS})\)(?:{FROM})?"
                  rf"|\d+/\d+ \((?P<cls>[^:()]+): [^()]+\)"
                  rf"(?: \+ residues \((?P<sids>{IDS})\)(?:{FROM})?)?)\Z")


def m5_part(rec: dict) -> str:
    """One replay's part of M5's cell, from its record (tools/s4/tick_replay.py
    m5_record): the one rendering, which metrics.py writes and `PART` reads."""
    name, ticks, first = rec["name"], rec["ticks"], rec["first_divergent_tick"]
    seen = ", ".join(rec.get("residues") or [])
    frm, how = rec.get("residues_from"), rec.get("residues_from_class")
    tail = (f" from {frm}" + (f" ({how})" if how else "")) if seen and frm is not None else ""
    if first is None:
        if rec.get("class") == "residue":
            return f"{name} residues only/{ticks} ({seen}){tail}"
        return f"{name} no divergence/{ticks}"
    return (f"{name} {first}/{ticks} ({rec['class']}: {rec['field']})"
            + (f" + residues ({seen}){tail}" if seen else ""))


def residue_class(rid: str, residues: Path = RESIDUES) -> bool:
    """Whether `rid` names a residue class of words on disk: a record of
    CLASS_SCHEMA whose id is its name."""
    path = residues / f"{rid}.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("schema") == CLASS_SCHEMA and doc.get("id") == rid


def residue_label(part: str, residues: Path = RESIDUES) -> str | None:
    """The residue record a replay's divergence names, if it names one that exists.

    A divergence is labelled when its class reads `residue <id>` and
    `Documentation/receipts/residues/<id>.json` is on disk. Until 2026-09-22
    this accepted any `(class: field)`, so a divergence classed `bug` -- the
    comparer's default -- read as labelled, and the first replay published
    with a bug in it would have met this criterion. "Labelled residue" is
    design 7.4's vocabulary, and a residue in this project is a record with
    evidence, not a word in a cell. A residue class of words (CLASS_SCHEMA)
    is never a cell's label: its words leave the comparison and are named
    beside it (`residues_only`), so a cell typed `residue <class>` is refused.
    """
    m = PART.match(part)
    cls = re.fullmatch(r"residue ([A-Za-z0-9._-]+)", (m.group("cls") or "") if m else "")
    if not cls or not (residues / f"{cls.group(1)}.json").is_file() \
            or residue_class(cls.group(1), residues):
        return None
    return cls.group(1)


def residues_only(part: str, residues: Path = RESIDUES) -> bool:
    """A replay judged to its end whose only differences are residue classes
    of words: `residues only/<ticks>` over at least one tick, each class a
    record of CLASS_SCHEMA on disk. Held to the recorded result by M5's check
    (tools/s4/tick_replay.py residue_problems), not here."""
    m = PART.match(part)
    if not m or m.group("rticks") is None or int(m.group("rticks")) < 1:
        return False
    return all(residue_class(r, residues) for r in m.group("rids").split(", "))


def m5(path: str | Path = "METRICS.md", residues: Path = RESIDUES) -> Result:
    """Every retained replay diverges only at end-of-capture or on a labelled
    residue, and there is at least one retained replay. End-of-capture with
    residue classes alone is end-of-capture (`residues_only`); a divergence
    beside residue classes is the divergence."""
    v = row(read(path), "M5 replays")
    if not v or re.match(r"^\s*0 replays\s*$", v) or v.startswith("not measured"):
        return Result(False, f"{v or 'row absent'} (no retained replay)")
    parts = [p.strip() for p in v.split(";") if p.strip()]
    clean = [p for p in parts if (m := PART.match(p)) and m.group("ticks") is not None
             and int(m.group("ticks")) > 0]
    bad = [p for p in parts if p not in clean and not residues_only(p, residues)
           and not residue_label(p, residues)]
    if bad:
        return Result(False, f"{v} (not end-of-capture and no recorded residue: {'; '.join(bad)})")
    return Result(True, v)


# The three counts in M6's cell, as metrics.py renders them: the stages
# booting, the stages with a completed replay, and the stages in both lists.
# Longest label first, so "booting with replay complete" is never read as
# "booting". Each part is matched whole, as metrics.py's ratio() prints it;
# the first part carrying a label is that label's.
M6_PART = re.compile(r"(booting with replay complete|replay complete|booting) (\d+)/(\d+)"
                     r"(?: \([0-9.]+%\))?\Z")
M6_LABELS = ("booting", "replay complete", "booting with replay complete")


def m6(path: str | Path = "METRICS.md") -> Result:
    """At least one stage boots, and a replay reaching its goal ran on that same stage.

    The criterion is "a stage booting *with* a replay reaching its goal". Until
    2026-09-23 this read the two counts apart, so a row whose booting stage
    and completed replay were different stages read as met
    (agents-journals/s4/2026-09-23-m6-goal-latch.md). It is met now only on
    the third count, the stages in both lists, which cannot exceed either
    list's.
    """
    v = row(read(path), "M6 stages")
    counts: dict[str, int] = {}
    for part in v.split(";"):
        m = M6_PART.match(part.strip())
        if m and m.group(1) not in counts:
            counts[m.group(1)] = int(m.group(2))
    if set(counts) != set(M6_LABELS):
        return Result(False, f"{v or 'row absent'} (no booting, replay-complete and same-stage "
                             f"ratios)")
    booting, complete, both = (counts[k] for k in M6_LABELS)
    if both > min(booting, complete):
        return Result(False, f"{v} (more stages in both lists than in one of them)")
    return Result(booting >= 1 and complete >= 1 and both >= 1, v)


PREDICATES = {"M4": m4, "M5": m5, "M6": m6}
