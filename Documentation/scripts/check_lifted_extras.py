#!/usr/bin/env python3
"""Gate: the tracked lifted-extras record matches the generated state.

`tools/s4/lift_extra.py` lifts an address the whole-image lift produced no
module for. Each row's `why` says how it came in:
- a driven run reached it (`tools/s4/reach_extend.py`, a probe's census, or a
  hand `--entry` naming the run), which is evidence from running the game;
- it is a dispatch target on a dispatch-surface list (`--from-list`);
- it is a code pointer in a run of them in `.rdata` (the sweep);
- it is an arm of a switch anywhere in the lifted image (`--seed-arms`).
The last three are read from the executable.

It lived only in `private/generated/s4/link/extra_functions.<tag>.json`, which
is gitignored, so the set of blocks the guest can execute was not in the tree.
That is the same shape as the dispatch exceptions, which cost a day on
2026-09-21 when a regeneration dropped one and the driven run fell from 369
WinMain iterations to 1 with every gate passing.

`Documentation/lifted-extras.json` is a **projection**, not a copy: the
generated file carries `lift_seconds`, `compile_seconds` and absolute object
paths, and tracking a stopwatch would change the record's hash on every
regeneration. A staleness gate that fires for no semantic reason is one people
learn to re-run past, which is the failure this record exists to prevent.

Five checks:

1. the tracked record re-projects from the generated state exactly, when that
   state is present;
2. every row has a reason, and a row whose reason was *recovered* after the
   fact says where it was recovered from;
3. the backfill does not grow. `lift_extra.py` now refuses an `--entry`
   without a `--why`, so a new reasonless row cannot be created; a backfill
   entry for an address outside the two historical ones means somebody
   reintroduced the hole;
4. every arm of every switch in the lifted image -- the map's modules and the
   record's rows -- has a row, when the executable, the map, the lift log and
   S3's arm log are here to read. The G3 decision gives every jump-table arm a
   trace and a row. S3 seeded them from the analyzer's records, which have
   none for a function the map does not list, none for a table the analyzer
   did not recover (`AddRings` has three), and can leave out a default arm the
   table still routes to. A worker thread died at one, 0x0081F363, before
   `tools/s4/switches.py` read them all. A switch it cannot read fails here
   too, rather than being taken as having no arms. Only a row that compiled
   and was not rejected counts, since only those are registered; and when the
   inputs are not here the check says so on a `note:` line, which
   `run_all_gates.py` shows as degraded, rather than passing in silence;
5. every instruction remill cannot lift that lifted code reaches has a row at
   the instruction after it (`tools/s4/emulated.py`'s census). remill ends the
   trace there with hypercall 256 and `__remill_error`, the runtime executes the
   instruction (`runtime/sa2_emulate.cpp`) and continues through the function
   table, so a site with no row after it is a trap one instruction late. Every
   site S3's census listed must be among them. That the seven forms are all
   remill could not lift is not assumed: `test_emulate.py` holds every lifted
   module's hypercall-256 count in remill's own IR to the sites the census
   reaches, with the one data table lifted as code named.

Usage: .venv/bin/python Documentation/scripts/check_lifted_extras.py
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

TRACKED = ROOT / "Documentation" / "lifted-extras.json"
BACKFILL = ROOT / "Documentation" / "lifted-extras-backfill.json"

# The two rows that predate lift_extra.py requiring a reason. The list is
# closed: the tool refuses to make another, so a third address here is a
# regression in the tool, not a new historical fact.
HISTORICAL = {"0x007c5910", "0x007ebb90"}


def problems(tracked_path: Path | None = None) -> list[str]:
    out: list[str] = []
    path = tracked_path or TRACKED
    if not path.exists():
        return [f"{path.relative_to(ROOT)} does not exist; the set of blocks the "
                "guest can execute is not in the tree"]
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"the record is not JSON: {e}"]
    if doc.get("schema") != "sa2-recomp-lifted-extras/v1":
        out.append(f"schema is {doc.get('schema')!r}, expected "
                   "'sa2-recomp-lifted-extras/v1'")

    rows = doc.get("rows", [])
    if not rows:
        out.append("the record has no rows, so it asserts that no block was ever "
                   "lifted, which the build contradicts")
    seen = set()
    for r in rows:
        va = str(r.get("va", "?"))
        if va in seen:
            out.append(f"{va} is listed twice")
        seen.add(va)
        if not str(r.get("why", "")).strip():
            out.append(f"{va} has no reason")
        if r.get("why_recovered") is not None and not str(r["why_recovered"]).strip():
            out.append(f"{va} is marked as recovered but does not say from where")

    counts = doc.get("counts", {})
    if counts.get("rows") != len(rows):
        out.append(f"counts.rows is {counts.get('rows')} and there are {len(rows)} rows")

    if BACKFILL.exists():
        extra = {f"0x{int(b['va'], 0):08x}"
                 for b in json.loads(BACKFILL.read_text()).get("rows", [])}
        for va in sorted(extra - HISTORICAL):
            out.append(f"{va} is a new backfill entry. lift_extra.py refuses an "
                       "--entry without a --why, so a reasonless row cannot be "
                       "created any more; this one means that refusal was removed.")
        for va in sorted(HISTORICAL - extra):
            out.append(f"{va} lost its backfilled reason, which was recovered from "
                       "run evidence and cannot be recovered again if those runs go")

    arms = unregistered_arms(rows)
    if arms:
        out += arms
    cont = unlifted_continuations(rows)
    if cont:
        out += cont

    # Re-project and compare, when the generated state is present.
    try:
        import track_extra_functions as T                # noqa: PLC0415
        fresh = T.project(doc.get("tag", "next+allarms"))
    except SystemExit:
        return out                                       # not built here; nothing to compare
    except Exception as e:                               # noqa: BLE001
        return out + [f"the projection could not be recomputed: {e!r}"]

    if fresh.get("rows") != rows:
        a = {r["va"] for r in fresh.get("rows", [])}
        b = seen
        for va in sorted(a - b)[:5]:
            out.append(f"{va} is lifted in the build and not in the record; re-run "
                       "tools/s4/track_extra_functions.py --write")
        for va in sorted(b - a)[:5]:
            out.append(f"{va} is in the record and not lifted in the build")
        if a == b:
            out.append("the record and the build name the same addresses but a row "
                       "differs in its detail; re-run "
                       "tools/s4/track_extra_functions.py --write")
    return out


# Why check 4 last returned None, for main() to print: a check that skips
# silently prints the same OK as one that ran.
SKIPPED_WHY = ""
_INPUTS: dict = {}
_CENSUS: dict = {}


def _census_inputs():
    """The executable, the map's ranges, the analyzer's tables and S3's arms,
    read once per process: the falsifications call check 4 again for every
    mutated record."""
    global SKIPPED_WHY
    if _INPUTS:
        return _INPUTS
    gen = ROOT / "private" / "generated"
    fmap = gen / "function-map.json"
    arms_log = gen / "s3" / "arms" / "arms.next.jsonl"
    if not fmap.is_file() or not arms_log.is_file():
        SKIPPED_WHY = f"{fmap.name} or {arms_log.name} is not here"
        return None
    try:
        import common as C                               # noqa: PLC0415
        import image as IM                               # noqa: PLC0415
        import switches as SW                            # noqa: PLC0415
        img = IM.Image()
        functions = C.by_address()
    except (SystemExit, OSError) as e:
        SKIPPED_WHY = f"the executable or the map could not be read ({e})"
        return None
    lift_log = C.LIFT / "next.jsonl"
    if not lift_log.is_file():
        SKIPPED_WHY = f"{lift_log.name} is not here"
        return None
    _INPUTS.update(
        SW=SW, img=img,
        ranges=[(int(d["va"], 16), int(d["end"], 16)) for d in C.read_jsonl(lift_log).values()],
        recorded=SW.recorded_tables(functions),
        base=set(functions) | {int(json.loads(line)["entry"], 16)
                               for line in arms_log.read_text().splitlines() if line.strip()})
    return _INPUTS


def unregistered_arms(rows: list[dict]) -> list[str] | None:
    """Check 4: the switches in the lifted image whose arms have no row -- the
    map's modules and the record's rows, read by `tools/s4/switches.py`'s
    census. A row counts only if it compiled and was not rejected, because
    only those are registered. None, with SKIPPED_WHY set, when the inputs are
    not here to read."""
    inp = _census_inputs()
    if inp is None:
        return None
    good = [r for r in rows if r.get("compiled") and not r.get("rejected")]
    key = frozenset((int(r["va"], 16), int(r["end"], 16)) for r in good)
    if key not in _CENSUS:
        ranges = inp["ranges"] + sorted(key)
        _CENSUS[key] = inp["SW"].census(inp["img"], ranges, inp["recorded"])
    registered = inp["base"] | {int(str(r.get("va", "0")), 16) for r in good}
    out = []
    for sw in sorted(_CENSUS[key].values(), key=lambda s: s.dispatch):
        if sw.problem:
            out.append(f"the switch at {sw.dispatch:#010x} could not be read ({sw.problem}); "
                       "tools/s4/switches.py says why")
            continue
        missing = [a for a in sw.arms if a not in registered]
        if missing:
            out.append(f"{len(missing)} arm(s) of the switch at {sw.dispatch:#010x} have no "
                       f"row, first {missing[0]:#010x}; the G3 decision gives every arm one "
                       "-- run tools/s4/lift_extra.py --seed-arms")
    return out


_EMULATED: dict = {}


def unlifted_continuations(rows: list[dict]) -> list[str] | None:
    """Check 5: sites whose continuation has no row, and S3 sites the census
    no longer reaches. None, with SKIPPED_WHY set, when the inputs are not here."""
    inp = _census_inputs()
    if inp is None:
        return None
    import common as C                                   # noqa: PLC0415
    import emulated as EM                                # noqa: PLC0415
    good = [r for r in rows if r.get("compiled") and not r.get("rejected")]
    key = frozenset((r["va"], r.get("anchor"), r["end"]) for r in good)
    if key not in _EMULATED:
        _EMULATED[key] = EM.census(inp["img"], C, good)
    found = _EMULATED[key]
    registered = inp["base"] | {int(str(r["va"]), 16) for r in good}
    out = []
    for site in found.values():
        if site.next_ip not in registered:
            out.append(f"the continuation after {site.kind} at {site.ip:#010x} has no row at "
                       f"{site.next_ip:#010x}; run tools/s4/lift_extra.py --seed-continuations")
    for va in sorted(EM.s3_sites(ROOT) - set(found)):
        out.append(f"S3's census listed {va:#010x} and lifted code no longer reaches it")
    return out


# How many falsifications ran, set by falsifications() from the cases it
# actually built. It was a literal in the summary line, which is the same
# defect this project catalogues as "a counter incremented before the thing it
# counts": adding a case without touching the literal would have printed a
# number that was simply wrong, and a gate that misreports its own rigour is
# worse than one that does not report it.
RAN = 0


def falsifications() -> list[str]:
    if not TRACKED.exists():
        return ["the record does not exist, so no mutation can be shown to fail"]
    doc = json.loads(TRACKED.read_text())
    if not doc.get("rows"):
        return ["the record has no rows; the falsifications cannot run"]

    def mutate(name, f):
        d = copy.deepcopy(doc)
        f(d)
        return name, d

    cases = [
        mutate("a lifted block dropped from the record",
               lambda d: d["rows"].pop(0)),
        mutate("a reason emptied",
               lambda d: d["rows"][0].__setitem__("why", "")),
        mutate("a recovered reason with no provenance",
               lambda d: d["rows"][0].__setitem__("why_recovered", "")),
        mutate("a row's extent changed",
               lambda d: d["rows"][0].__setitem__("bytes", 1)),
        mutate("a duplicate row",
               lambda d: d["rows"].append(copy.deepcopy(d["rows"][0]))),
        mutate("the count disagreeing with the rows",
               lambda d: d["counts"].__setitem__("rows", 1)),
        mutate("the schema changed",
               lambda d: d.__setitem__("schema", "something-else/v1")),
    ]
    global RAN
    RAN = len(cases)

    survived = []
    with tempfile.TemporaryDirectory() as td:
        for name, d in cases:
            p = Path(td) / "extras.json"
            p.write_text(json.dumps(d))
            if not problems(p):
                survived.append(name)

    # Check 4, through problems() so the wiring is tested too, where it can run.
    # The arm the run died at must be named by its switch -- a message only
    # check 4 writes -- both when its row is gone and when its row exists but
    # did not compile, since only a compiled row is registered.
    arm = "0x0081f363"
    if any(r.get("va") == arm for r in doc["rows"]) and unregistered_arms([]) is not None:
        for name, f in (
                ("an arm of a switch left without a row",
                 lambda d: d.__setitem__("rows", [r for r in d["rows"] if r.get("va") != arm])),
                ("an arm whose row did not compile counted as registered",
                 lambda d: [r.__setitem__("compiled", False) for r in d["rows"]
                            if r.get("va") == arm])):
            d = copy.deepcopy(doc)
            f(d)
            RAN += 1
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "extras.json"
                p.write_text(json.dumps(d))
                if not any("0x0081c256" in m for m in problems(p)):
                    survived.append(name)

    # Check 5 the same way: the continuation after the cvtps2pi the decoder
    # thread died on, removed, must be named by its site.
    cont = "0x0080d42d"
    if any(r.get("va") == cont for r in doc["rows"]) and unlifted_continuations([]) is not None:
        d = copy.deepcopy(doc)
        d["rows"] = [r for r in d["rows"] if r.get("va") != cont]
        RAN += 1
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "extras.json"
            p.write_text(json.dumps(d))
            if not any("cvtps2pi at 0x0080d42a" in m for m in problems(p)):
                survived.append("an emulated instruction left without its continuation")
    return [f"falsification survived: {s} -- the gate accepted a record it "
            "should have rejected" for s in survived]


def main() -> int:
    failed = falsifications()
    if failed:
        for f in failed:
            print(f"  {f}")
        print(f"LIFTED_EXTRAS_FAILED falsifications={len(failed)}")
        return 1
    probs = problems()
    if probs:
        for p in probs[:12]:
            print(f"  {p}")
        print(f"LIFTED_EXTRAS_FAILED problems={len(probs)}")
        return 1
    doc = json.loads(TRACKED.read_text())
    c = doc["counts"]
    back = len(json.loads(BACKFILL.read_text())["rows"]) if BACKFILL.exists() else 0
    if unregistered_arms(doc.get("rows", [])) is None:
        print(f"note: check 4 (every switch arm has a row) skipped: {SKIPPED_WHY}")
    print(f"LIFTED_EXTRAS_OK rows={c['rows']} lifted={c['lifted']} "
          f"compiled={c['compiled']} guest_bytes={c['guest_bytes']} "
          f"backfilled={back} falsifications={RAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
