#!/usr/bin/env python3
"""Gate: M6's two lists describe the tree as it stands, and a tracked receipt backs each entry.

What this gate is for
---------------------
M6 is read from `private/generated/stage-results.json`, which
`tools/s4/stage_boot.py --write` writes with its receipt,
`Documentation/receipts/s4-stage-boot-v1.json`. The private file is not in
the tree, and a number that is not tied to the build it describes is the
failure that cost the reach a day on 2026-09-21. So, without running the
guest, this checks:

1. **Agreement.** The private file was written by tools/s4/stage_boot.py, and
   its `booting` and `replay_complete` are the receipt's exactly.
2. **Inputs.** The receipt's recorded inputs -- the reach build's manifest,
   the runtime and the stage pipeline -- equal the tree's now. A runtime byte
   that moved makes the receipt stale.
3. **Booting verdicts.** Each stage's verdict is recomputed with
   tools/s4/stage_boot.py's own predicate from its kept stage trace and kept
   run record, both pinned by sha256: the in-stage window, the in-game frame
   counter's stalls, the leash read from the run's own pump record, and the
   trace's row count against the run's. It must come out the same.
4. **Replay-complete verdicts.** The receipt holds one verdict per stage
   replay Documentation/replays.json registers, in its order. Each rests on
   the files the registry pins (the native tick captures and their
   sa2-input-end.csv) and the tick-replay receipt pins (the lifted tick
   capture, goal-latch column and stage trace), each beside the tick capture
   of the run it belongs to, the native captures each from a run of its own,
   and is recomputed from them with stage_boot.py's own judge_goal.
   `replay_complete` must be exactly the stage ids of the verdicts that
   complete, so a stage id typed into either list fails here.
5. **Identity.** Every stage is in `Documentation/receipts/stage-list.json`, its
   id is its level's, and its level is the one tools/s4/stage_run.py starts.

Every verdict is recomputed on every pass. A kept file that is missing,
unnamed or not what its pin says is a problem, whatever the verdict claims:
until 2026-09-23, one missing file switched every recompute off, and a receipt
claiming a completed replay with its evidence nulled passed
(agents-journals/s4/2026-09-23-m6-goal-latch.md).

Falsifications run on every pass, each aimed so that only the check it names
can catch it; some move a live input (a runtime byte, a trace byte, a run
record byte, the stage list) in a copied tree rather than the record. No stage
replay is registered until the Metal Harbor recording is, so the
replay-complete checks are also run against a copied tree holding one
registered stage replay made from synthetic files: its control must pass, and
each mutation of it must fail on the check it names. The falsifications that
mutate a kept stage trace run when the kept files exist; when they do not,
the live problems already fail the gate.

Re-running it:

    source tools/env.sh
    .venv/bin/python tools/s4/tick_replay.py --receipt   # a stage replay's lifted run
    .venv/bin/python tools/s4/stage_boot.py --write

Usage: .venv/bin/python Documentation/scripts/check_stage_results.py
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import stage_boot as SB  # noqa: E402

RAN = 0
VERDICT_KEYS = ("ticks", "in_stage_from", "in_stage_ticks", "ingame_frames_stalled",
                "ended_at_leash", "trace_rows_match_run", "booting")
GOAL_KEYS = ("native_goal_ticks", "native_misses", "native_goal_tick", "lifted_goal_tick",
             "latch_rises", "latch_rows", "trace_rows", "latch_rows_match_trace", "level_at_goal",
             "complete")
EVIDENCE_KEYS = ("replay", "stage", "level", "stage_id", "captures", "captures_sha256", "ends",
                 "lifted_capture", "lifted_capture_sha256", "latch", "latch_sha256", "trace",
                 "trace_sha256")


def replay_problems(receipt: dict, root: Path, ids: set[str]) -> list[str]:
    """Checks 4 and 5 for the stage replays: coverage, evidence, recomputed verdicts,
    identity, and replay_complete derived from the verdicts."""
    out = []
    recorded = receipt.get("replays", [])
    try:
        entries = SB.stage_replays(root)
    except (OSError, ValueError, KeyError) as e:
        return [f"{SB.REGISTRY_REL} cannot be read for its stage replays: {e}"]
    names = [e.get("name") for e in entries]
    if [r.get("replay") for r in recorded] != names:
        out.append(f"the receipt's stage replays {[r.get('replay') for r in recorded]} are not "
                   f"the registry's {names}, in its order; re-run tools/s4/stage_boot.py --write")
    tick_replay = root / SB.TICK_REPLAY_REL
    try:
        docs = ({d.get("name"): d for d in json.loads(tick_replay.read_text()).get("replays", [])}
                if tick_replay.is_file() else {})
    except ValueError as e:
        docs = {}
        out.append(f"{SB.TICK_REPLAY_REL} cannot be read: {e}")
    by_name = {e.get("name"): e for e in entries}
    for r in recorded:
        name = r.get("replay")
        if r.get("stage_id") not in ids:
            out.append(f"{name}: stage {r.get('stage_id')} is not in the stage list")
        level = r.get("level")
        if not isinstance(level, int) or r.get("stage_id") != f"{level:02d}":
            out.append(f"{name}: stage id {r.get('stage_id')} is not level {r.get('level')}'s")
        if SB.SR.STAGES.get(r.get("stage"), (None,))[0] != r.get("level"):
            out.append(f"{name}: level {r.get('level')} is not the one stage_run.py starts for "
                       f"{r.get('stage')!r}")
        entry = by_name.get(name)
        if entry is None:
            continue                    # the coverage problem above names it
        try:
            ev = SB.goal_evidence(entry, docs.get(name))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            out.append(f"{name}: its registry entry has no usable stage block: {e!r}")
            continue
        moved = [k for k in EVIDENCE_KEYS if r.get(k) != ev[k]]
        if moved:
            out.append(f"{name}: its recorded {', '.join(moved)} is not what the registry and the "
                       f"tick-replay receipt record, so the verdict does not rest on the "
                       f"registered replay's own runs; re-run tools/s4/stage_boot.py --write")
            continue
        strays = SB.evidence_problems(ev)
        if strays:
            out += [f"{name}: {p}" for p in strays]
            continue
        bad = [f"no {what} is recorded with a pin" if not rel or not pin
               else f"its {what} {rel} is missing or does not hash to its pin"
               for what, rel, pin in SB.pinned_files(ev)
               if not rel or not pin or SB.TR.sha256(root / rel) != pin]
        if bad:
            out.append(f"{name}: {'; '.join(bad)}")
            continue
        try:
            v = SB.goal_verdict(ev, root)
        except (SystemExit, ValueError, KeyError, IndexError) as e:
            # SystemExit is the readers' refusal; the others are a pinned file
            # whose values no reader can take (the M6 review, N5): a problem, not
            # a traceback.
            out.append(f"{name}: its verdict cannot be recomputed from the kept files: {e}")
            continue
        for k in GOAL_KEYS:
            if v[k] != r.get(k):
                out.append(f"{name}: recomputed {k} {v[k]} is not the recorded {r.get(k)}")
    derived = SB.completed(recorded) if all("complete" in r and "stage_id" in r
                                            for r in recorded) else None
    if receipt.get("replay_complete") != derived:
        out.append(f"the receipt's replay_complete {receipt.get('replay_complete')} is not what "
                   f"its stage-replay verdicts give ({derived}); nothing else may write it")
    return out


def problems(receipt: dict, results: dict, root: Path = ROOT,
             traces: dict[str, list[dict]] | None = None,
             pumps: dict[str, dict] | None = None) -> list[str]:
    out = []
    if results.get("generated_by") != SB.GENERATED_BY:
        out.append(f"M6's input was written by {results.get('generated_by')!r}, not "
                   f"{SB.GENERATED_BY}, the only writer of either list")
    if results.get("booting") != receipt.get("booting"):
        out.append(f"booting {results.get('booting')} is not the receipt's {receipt.get('booting')}")
    if results.get("replay_complete") != receipt.get("replay_complete"):
        out.append(f"replay_complete {results.get('replay_complete')} is not the receipt's "
                   f"{receipt.get('replay_complete')}")
    now = SB.inputs(root)
    if receipt.get("inputs") != now:
        moved = sorted(k for k in now if now[k] != (receipt.get("inputs") or {}).get(k))
        out.append(f"the receipt is stale: {moved} moved since it was written; re-run "
                   f"tools/s4/stage_boot.py --write")
    ids = set(json.loads((root / "Documentation/receipts/stage-list.json").read_text())["stages"])
    for st in receipt.get("stages", []):
        if st["stage_id"] not in ids:
            out.append(f"{st['stage']}: stage {st['stage_id']} is not in the stage list")
        if st["stage_id"] != f"{st['level']:02d}":
            out.append(f"{st['stage']}: stage id {st['stage_id']} is not level {st['level']}'s")
        if SB.SR.STAGES.get(st["stage"], (None,))[0] != st["level"]:
            out.append(f"{st['stage']}: level {st['level']} is not the one stage_run.py starts")
        # A kept file is read only once its pin is named and matches: TR.sha256
        # of a missing file is "", so an emptied pin over a deleted file would
        # otherwise match and the read would raise instead of report.
        kept = {}
        for key in ("trace", "run"):
            rel, pin = st.get(key), st.get(f"{key}_sha256")
            if not rel or not pin or SB.TR.sha256(root / rel) != pin:
                out.append(f"{st['stage']}: its kept {key} {rel} is unnamed, unpinned, missing or "
                           f"does not hash to the receipt's pin")
            else:
                kept[key] = root / rel
        if traces is not None and st["stage"] in traces:
            trace = traces[st["stage"]]
        elif "trace" in kept:
            try:
                trace = SB.read_trace(kept["trace"])
            except SystemExit as e:
                out.append(f"{st['stage']}: its kept trace cannot be read: {e}")
                continue
        else:
            continue
        if pumps is not None and st["stage"] in pumps:
            pump = pumps[st["stage"]]
        elif "run" in kept:
            pump = json.loads(kept["run"].read_text()).get("pump") or {}
        else:
            continue
        v = SB.judge(trace, st["level"], pump)
        for k in VERDICT_KEYS:
            if v[k] != st.get(k):
                out.append(f"{st['stage']}: recomputed {k} {v[k]} is not the recorded {st.get(k)}")
    booted = {st["stage_id"] for st in receipt.get("stages", []) if st.get("booting")}
    if set(receipt.get("booting", [])) != booted:
        out.append(f"the receipt's booting {receipt.get('booting')} is not its stages' {sorted(booted)}")
    out += replay_problems(receipt, root, ids)
    return out


def copied_tree(receipt: dict, with_private: bool) -> tempfile.TemporaryDirectory:
    """The files problems() reads, in a scratch root a falsification may change."""
    td = tempfile.TemporaryDirectory()
    t = Path(td.name)
    for rel in ["runtime", "Documentation/receipts", SB.REGISTRY_REL, *SB.PIPELINE]:
        src, dst = ROOT / rel, t / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)
    if with_private:
        for st in receipt["stages"]:
            for rel in (st["trace"], st["run"]):
                (t / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, t / rel)
    return td


# ------------------------------------------------- the synthetic stage replay

GOAL_NAME = "synthetic-goal"
INCOMPLETE = "recomputed complete False"
GOAL_DIR = "private/generated/replay/synthetic-goal"
GOAL_TICK, GOAL_TICKS, GOAL_LEVEL = 40, 60, 10


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def latch_bytes(rise: int | None = GOAL_TICK, rows: int = GOAL_TICKS,
                value: str = "0x01") -> bytes:
    """A goal-latch column in harness/stage_goal_probe.c's format, rows 0..rows-1,
    rising at `rise` (never when None)."""
    lines = ["tick,goal_latch"] + [
        f"{t},{value if rise is not None and t >= rise else '0x00'}" for t in range(rows)]
    return ("\n".join(lines) + "\n").encode()


def trace_bytes(level_at_goal: int = GOAL_LEVEL, rows: int = GOAL_TICKS) -> bytes:
    """A stage trace in wrap_tick_probe.c's format, in the stage on every tick."""
    lines = [",".join(SB.TRACE_HEADER)]
    for t in range(1, rows + 1):
        level = level_at_goal if t == GOAL_TICK else GOAL_LEVEL
        lines.append(f"{t},0x{level:04x},0x00000005,0x0010,0x{t:08x},0x10000000,"
                     "0x00000000,0x00000000,0x00000000")
    return ("\n".join(lines) + "\n").encode()


def end_bytes(goal: int = GOAL_TICK, misses: int = 0) -> bytes:
    """A native run's sa2-input-end.csv, as native_input_probe.c writes it."""
    return f"goal_tick,end_tick,misses\n{goal},{goal + 300},{misses}\n".encode()


def capture_bytes(side: str) -> bytes:
    """A stand-in tick capture: nothing here reads a capture's columns, only its
    pin, so any bytes do, and these are synthetic."""
    return (f"tick,synthetic_{side}\n" + "".join(f"{t},{t}\n" for t in range(1, GOAL_TICKS + 1))
            ).encode()


def goal_scenario(t: Path) -> dict[str, str]:
    """One registered stage replay that reaches its goal, from synthetic files:
    the registry entry, the tick-replay receipt's record of its lifted run, and
    the seven files they pin. Returns the files' tree-relative paths by role."""
    files = {"latch": f"{GOAL_DIR}/goal-latch.csv", "trace": f"{GOAL_DIR}/stage-trace.csv",
             "lifted": f"{GOAL_DIR}/lifted.csv",
             "capture": f"{GOAL_DIR}/native-a/sa2-tick-capture.csv",
             "end": f"{GOAL_DIR}/native-a/sa2-input-end.csv",
             "repeat_capture": f"{GOAL_DIR}/native-b/sa2-tick-capture.csv",
             "repeat_end": f"{GOAL_DIR}/native-b/sa2-input-end.csv"}
    content = {"latch": latch_bytes(), "trace": trace_bytes(), "lifted": capture_bytes("lifted"),
               "capture": capture_bytes("native"), "end": end_bytes(),
               "repeat_capture": capture_bytes("native"), "repeat_end": end_bytes()}
    for role, rel in files.items():
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_bytes(content[role])
    registry = json.loads((t / SB.REGISTRY_REL).read_text())
    registry["replays"] = [{
        "name": GOAL_NAME, "stage": {"name": "metalharbor", "level": GOAL_LEVEL},
        "native": {"capture": files["capture"], "sha256": sha(content["capture"]),
                   "end": {"file": files["end"], "sha256": sha(content["end"])},
                   "repeats": [{"capture": files["repeat_capture"],
                                "sha256": sha(content["repeat_capture"]),
                                "end": {"file": files["repeat_end"],
                                        "sha256": sha(content["repeat_end"])}}]}}]
    (t / SB.REGISTRY_REL).write_text(json.dumps(registry, indent=1))
    (t / SB.TICK_REPLAY_REL).write_text(json.dumps({"id": "s4-tick-replay-v1", "replays": [
        {"name": GOAL_NAME, "lifted": {
            "capture": files["lifted"], "sha256": sha(content["lifted"]),
            "goal_latch": {"path": files["latch"], "sha256": sha(content["latch"])},
            "stage_trace": {"path": files["trace"], "sha256": sha(content["trace"])}}}]}, indent=1))
    return files


def scenario_docs(t: Path) -> tuple[dict, dict]:
    """The receipt and M6 input stage_boot.py's own writers make for the
    synthetic tree: no stage runs, the synthetic replay's verdict."""
    replays = SB.goal_verdicts(t)
    rec = SB.receipt_doc(SB.inputs(t), [], replays, "0" * 40, False)
    return rec, SB.results_doc(rec["booting"], rec["replay_complete"])


def repin(doc, rel: str, new: str):
    """`doc` (any record) with the pin of the file `rel` set to `new`: the
    sha256 beside a "file", "path" or "capture" naming it, and a verdict's
    latch_sha256, trace_sha256, lifted_capture_sha256 or captures_sha256 entry
    when the matching name is `rel`. By name, not by value, since two runs'
    files can hash alike."""
    if isinstance(doc, list):
        return [repin(x, rel, new) for x in doc]
    if not isinstance(doc, dict):
        return doc
    out = {k: repin(v, rel, new) for k, v in doc.items()}
    if rel in (out.get("file"), out.get("path"), out.get("capture")):
        out["sha256"] = new
    for key in ("latch", "trace", "lifted_capture"):
        if out.get(key) == rel:
            out[f"{key}_sha256"] = new
    if isinstance(out.get("captures"), list) and isinstance(out.get("captures_sha256"), list):
        out["captures_sha256"] = [new if c == rel else s
                                  for c, s in zip(out["captures"], out["captures_sha256"])]
    return out


@contextlib.contextmanager
def rewritten(t: Path, rel: str, data: bytes, records: tuple[str, ...]):
    """`rel` holding `data` for the duration, and each record named in
    `records` ("registry", "tick_replay") repinned to match; the caller repins
    the stage-boot receipt with repin(). A case repins every record it is not
    aimed at, so only the check it names can catch it."""
    paths = {"registry": SB.REGISTRY_REL, "tick_replay": SB.TICK_REPLAY_REL}
    saved = {p: (t / p).read_bytes() for p in (rel, *paths.values())}
    (t / rel).write_bytes(data)
    for which in records:
        p = t / paths[which]
        p.write_text(json.dumps(repin(json.loads(p.read_text()), rel, sha(data)), indent=1))
    try:
        yield sha(data)
    finally:
        for p, b in saved.items():
            (t / p).write_bytes(b)


@contextlib.contextmanager
def edited(path: Path, change):
    """`path` changed for the duration: replaced by `change` when it is text,
    else parsed as JSON, handed to `change` to edit in place, and written back."""
    saved = path.read_bytes()
    if isinstance(change, str):
        path.write_text(change)
    else:
        doc = json.loads(saved)
        change(doc)
        path.write_text(json.dumps(doc))
    try:
        yield
    finally:
        path.write_bytes(saved)


def goal_falsifications(receipt: dict, aimed) -> list[str]:
    """The replay-complete checks against the synthetic stage replay, each run
    through `aimed` (falsifications()'s expect). Returns the control's
    problems, which must be none: a mutation that fails a tree whose control
    already fails shows nothing."""
    with copied_tree(receipt, False) as td:
        t = Path(td)
        files = goal_scenario(t)
        rec, res = scenario_docs(t)
        control = problems(rec, res, root=t)
        if control or rec["replay_complete"] != [f"{GOAL_LEVEL:02d}"]:
            return control or [f"the synthetic replay does not complete: {rec['replays']}"]

        def expect(*args, **kw) -> None:
            """Every case's unmutated record is the control, which has no problem."""
            aimed(*args, before=set(), **kw)

        # (name, file, new bytes, records whose pin follows it, the receipt's too, the problem)
        every = ("registry", "tick_replay")
        cases = [
            # The evidence moved under a recorded verdict, every pin following
            # it: only the recompute can see these.
            # Each also names `complete`: the evidence key alone would let a
            # `complete` that ignores it pass (the M6 review, N4).
            ("a latch that never rises", files["latch"], latch_bytes(None), every, True,
             ("recomputed lifted_goal_tick None", INCOMPLETE)),
            ("a latch that rises a tick late", files["latch"], latch_bytes(GOAL_TICK + 1), every,
             True, (f"recomputed lifted_goal_tick {GOAL_TICK + 1}", INCOMPLETE)),
            ("a latch that rises a tick early", files["latch"], latch_bytes(GOAL_TICK - 1), every,
             True, (f"recomputed lifted_goal_tick {GOAL_TICK - 1}", INCOMPLETE)),
            ("the native replays set the goal on different ticks", files["repeat_end"],
             end_bytes(GOAL_TICK + 1), every, True, ("recomputed native_goal_tick None", INCOMPLETE)),
            ("a native replay missed recorded input rows", files["repeat_end"],
             end_bytes(misses=3), every, True, ("recomputed native_goal_tick None", INCOMPLETE)),
            ("the goal tick is not in the stage", files["trace"], trace_bytes(GOAL_LEVEL + 1),
             every, True, (f"recomputed level_at_goal {GOAL_LEVEL + 1}", INCOMPLETE)),
            ("the latch column misses GameLoop calls", files["latch"],
             latch_bytes(rows=GOAL_TICKS - 5), every, True,
             ("recomputed latch_rows_match_trace False", INCOMPLETE)),
            # One row short: the column may hold one row more than the trace,
            # never one fewer (the M6 review, N3).
            ("the latch column is one GameLoop call short", files["latch"],
             latch_bytes(rows=GOAL_TICKS - 1), every, True,
             ("recomputed latch_rows_match_trace False", INCOMPLETE)),
            ("the latch column has calls the trace never saw", files["latch"],
             latch_bytes(rows=GOAL_TICKS + 2), every, True,
             ("recomputed latch_rows_match_trace False", INCOMPLETE)),
            ("the latch column holds the probe's unreadable marker", files["latch"],
             latch_bytes(value="0xffffffff"), every, True, "cannot be recomputed"),
            ("the latch column skips a tick", files["latch"],
             latch_bytes().replace(b"\n7,0x00\n", b"\n"), every, True, "cannot be recomputed"),
            # A value no reader takes, pinned: a problem line, not a traceback
            # (the M6 review, N5).
            ("the stage trace holds a level that is no number on the goal tick", files["trace"],
             trace_bytes().replace(f"\n{GOAL_TICK},0x{GOAL_LEVEL:04x},".encode(),
                                   f"\n{GOAL_TICK},0xzz,".encode()), every, True,
             "cannot be recomputed"),
            # A pin that does not follow.
            ("the latch column moved and nothing was repinned", files["latch"],
             latch_bytes() + b"0,0x00\n", (), False,
             f"its goal-latch column {files['latch']} is missing or does not hash to its pin"),
            ("the lifted tick capture moved and nothing was repinned", files["lifted"],
             capture_bytes("lifted") + b"61,61\n", (), False,
             f"its lifted tick capture {files['lifted']} is missing or does not hash to its pin"),
            ("a native tick capture moved and nothing was repinned", files["repeat_capture"],
             capture_bytes("native") + b"61,61\n", (), False,
             f"its native tick capture 1 {files['repeat_capture']} is missing or does not hash "
             f"to its pin"),
            ("the tick-replay receipt records another lifted run", files["latch"],
             latch_bytes(GOAL_TICK + 1), ("tick_replay",), False, "is not what the registry and"),
            ("the tick-replay receipt records another lifted tick capture", files["lifted"],
             capture_bytes("lifted") + b"61,61\n", ("tick_replay",), False,
             "is not what the registry and"),
            ("the registry pins another native end record", files["end"],
             end_bytes().replace(b"\n", b"\r\n"), ("registry",), False,
             "is not what the registry and"),
            ("the registry pins another native tick capture", files["capture"],
             capture_bytes("native") + b"61,61\n", ("registry",), False,
             "is not what the registry and"),
        ]
        for name, rel, data, records, own, want in cases:
            with rewritten(t, rel, data, records) as new:
                expect(name, want, repin(rec, rel, new) if own else rec, res, root=t)

        registry, tick_replay = t / SB.REGISTRY_REL, t / SB.TICK_REPLAY_REL
        stage_list = t / "Documentation/receipts/stage-list.json"
        # Evidence gone from under a verdict that claims completion.
        for role, what in (("latch", "goal-latch column"), ("repeat_capture", "native tick capture 1"),
                           ("lifted", "lifted tick capture")):
            with rewritten(t, files[role], b"", ()):
                (t / files[role]).unlink()
                expect(f"a kept {what} deleted under a claimed completion",
                       f"its {what} {files[role]} is missing or does not hash to its pin", rec, res,
                       root=t)

        def as_written(tree: Path) -> dict:
            """The receipt stage_boot.py's writer would make for `tree` were its
            evidence checks skipped: every recorded key agrees with the registry
            and the tick-replay receipt, so only evidence_problems can object."""
            entry = SB.stage_replays(tree)[0]
            doc = {d["name"]: d for d in json.loads((tree / SB.TICK_REPLAY_REL).read_text())
                   ["replays"]}[GOAL_NAME]
            ev = SB.goal_evidence(entry, doc)
            v = SB.judge_goal(SB.read_goal(tree / ev["latch"]), SB.read_trace(tree / ev["trace"]),
                              ev["level"], [SB.read_end(tree / e["file"]) for e in ev["ends"]])
            out = copy.deepcopy(rec)
            out["replays"][0] = {**ev, **v}
            out["replay_complete"] = SB.completed(out["replays"])
            return out

        # One native run counted as two (stage-route item 5 asks for two
        # replays): the registered capture listed again as its repeat, and a
        # repeat listed twice. Every pin and verdict key agrees.
        with edited(registry, lambda d: d["replays"][0]["native"]["repeats"].__setitem__(
                0, {k: d["replays"][0]["native"][k] for k in ("capture", "sha256", "end")})):
            twice = as_written(t)
            expect("one native run listed twice", "are one run listed 2 times", twice,
                   dict(res, replay_complete=twice["replay_complete"]), root=t)
        with edited(registry, lambda d: d["replays"][0]["native"]["repeats"].append(
                copy.deepcopy(d["replays"][0]["native"]["repeats"][0]))):
            thrice = as_written(t)
            expect("a repeat listed twice", "are one run listed 2 times", thrice,
                   dict(res, replay_complete=thrice["replay_complete"]), root=t)
        nulled = copy.deepcopy(rec)
        nulled["replays"][0].update(latch=None, latch_sha256=None)
        with edited(tick_replay, lambda d: d["replays"][0]["lifted"].pop("goal_latch")):
            expect("no latch column recorded under a claimed completion",
                   "goal-latch column (None) is not beside", nulled, res, root=t)
        # Evidence from another run: every pin consistent, the file elsewhere.
        for what, key, place, want in (
                ("a native end record from another run's directory", "end", registry,
                 "is not beside its tick capture"),
                ("a latch column from another run's directory", "latch", tick_replay,
                 "is not beside the lifted tick capture")):
            stray = f"{GOAL_DIR}/native-c/{Path(files[key]).name}"
            (t / stray).parent.mkdir(parents=True, exist_ok=True)
            (t / stray).write_bytes((t / files[key]).read_bytes())
            moved_to = place.read_text().replace(f'"{files[key]}"', f'"{stray}"')
            elsewhere = json.loads(json.dumps(rec).replace(f'"{files[key]}"', f'"{stray}"'))
            with edited(place, moved_to):
                expect(what, want, elsewhere, res, root=t)
        with edited(tick_replay, "{ not json"):
            expect("an unreadable tick-replay receipt", "cannot be read", rec, res, root=t)
        with edited(registry, lambda d: d["replays"][0]["stage"].pop("level")):
            expect("a stage block with no level", "has no usable stage block", rec, res, root=t)
        # One native capture: stage-route item 5 asks for two replays.
        single = copy.deepcopy(rec)
        single["replays"][0].update(captures=single["replays"][0]["captures"][:1],
                                    captures_sha256=single["replays"][0]["captures_sha256"][:1],
                                    ends=single["replays"][0]["ends"][:1],
                                    native_goal_ticks=[GOAL_TICK], native_misses=[0])
        with edited(registry, lambda d: d["replays"][0]["native"].__setitem__("repeats", [])):
            expect("a completion on one native capture", "recomputed native_goal_tick None",
                   single, res, root=t)
        # A latch set before the first tick and held, against native goal ticks
        # of 1: the same first nonzero row, and no rise.
        with rewritten(t, files["end"], end_bytes(1), every) as e1, \
                rewritten(t, files["repeat_end"], end_bytes(1), every) as e2, \
                rewritten(t, files["latch"], latch_bytes(0), every) as l0:
            held = repin(repin(repin(rec, files["end"], e1), files["repeat_end"], e2),
                         files["latch"], l0)
            held["replays"][0].update(SB.goal_verdicts(t)[0], complete=True)
            expect("a latch held from before the first tick", "recomputed complete False", held,
                   res, root=t)
        # Identity: a stage id the stage list does not hold, and a stage whose
        # level is not the one stage_run.py starts.
        with edited(stage_list, lambda d: d.__setitem__(
                "stages", [x for x in d["stages"] if x != f"{GOAL_LEVEL:02d}"])):
            expect("a stage replay's stage dropped from the stage list", "is not in the stage list",
                   rec, res, root=t)
        moved = copy.deepcopy(rec)
        moved["replays"][0].update(level=GOAL_LEVEL + 1, stage_id=f"{GOAL_LEVEL + 1:02d}")
        moved["replay_complete"] = [f"{GOAL_LEVEL + 1:02d}"]
        with edited(registry, lambda d: d["replays"][0]["stage"].__setitem__("level",
                                                                              GOAL_LEVEL + 1)):
            expect("a stage replay's level that stage_run.py does not start for its stage",
                   "is not the one stage_run.py starts", moved,
                   dict(res, replay_complete=moved["replay_complete"]), root=t)

        # The records themselves, over unmoved evidence.
        typed = copy.deepcopy(rec)
        typed["replay_complete"] = ["10", "13"]
        expect("a stage id typed into the receipt's replay_complete",
               "is not what its stage-replay verdicts give", typed,
               dict(res, replay_complete=["10", "13"]), root=t)
        expect("a stage id typed into M6's input", "is not the receipt's", rec,
               dict(res, replay_complete=["10", "13"]), root=t)
        claimed = copy.deepcopy(rec)
        claimed["replays"][0]["complete"] = False
        expect("a verdict edited to not complete", "recomputed complete True", claimed, res,
               root=t)
        wrong_id = copy.deepcopy(rec)
        wrong_id["replays"][0]["stage_id"] = "11"
        wrong_id["replay_complete"] = ["11"]
        expect("a stage id that is not its level's", "is not level", wrong_id,
               dict(res, replay_complete=["11"]), root=t)
        extra = copy.deepcopy(rec)
        extra["replays"].append(dict(rec["replays"][0], replay="unregistered"))
        expect("a verdict for a replay the registry does not hold", "are not the registry's",
               extra, res, root=t)
        dropped = copy.deepcopy(rec)
        dropped["replays"], dropped["replay_complete"] = [], []
        expect("a registered stage replay with no verdict", "are not the registry's", dropped,
               dict(res, replay_complete=[]), root=t)
        other = dict(res)
        other.pop("generated_by")
        expect("M6's input written by another tool", "the only writer", rec, other,
               root=t)
    return []


def kept_files_present(receipt: dict) -> bool:
    """Whether every kept stage trace and run record the receipt names is on
    disk, so the falsifications that mutate them can read them."""
    return all(p and (ROOT / p).is_file() for st in receipt.get("stages", [])
               for p in (st.get("trace"), st.get("run")))


def falsifications(receipt: dict, results: dict) -> list[str]:
    global RAN
    survived = []
    have_private = kept_files_present(receipt)
    # A mutation is caught only by a problem its unmutated record does not
    # already have: against a live record that already fails, a check aimed at
    # that failure would otherwise read as caught while showing nothing
    # (check_replay_results.py's rule, from the gate fold's verification, N2).
    live = set(problems(receipt, results))

    def expect(name: str, want: str | tuple[str, ...], rec: dict, res: dict,
               before: set[str] | None = None, **kw) -> None:
        """The falsification must fail, and fail on the check it is aimed at,
        with a problem the unmutated record (`before`, the live one by default)
        does not have; with several `want`s, a new problem naming each."""
        global RAN
        RAN += 1
        got = problems(rec, res, **kw)
        new = [p for p in got if p not in (live if before is None else before)]
        for w in (want,) if isinstance(want, str) else want:
            if not any(w in p for p in new):
                already = " (the unmutated record already has it)" if any(w in p for p in got) else ""
                survived.append(f"{name} (wanted a new problem naming {w!r}{already}, got {got})")

    extra = copy.deepcopy(results)
    extra["booting"] = sorted(set(extra["booting"]) | {"99"})
    expect("a booting stage the receipt does not hold", "is not the receipt's", receipt, extra)

    # A completed replay typed in: a stage id no verdict of the live record
    # completes, so the list moves whatever the record already holds. Since
    # Metal Harbor's replay completes (U6, 2026-09-25), typing "10" moved nothing
    # and both of these survived.
    typed_done = next(i for i in ("10", "13", "11", "12", "09", "99")
                      if i not in receipt.get("replay_complete", []))
    typed = copy.deepcopy(results)
    typed["replay_complete"] = sorted(set(results.get("replay_complete", [])) | {typed_done})
    expect("a typed completed replay", "is not the receipt's", receipt, typed)
    both = copy.deepcopy(receipt)
    both["replay_complete"] = typed["replay_complete"]
    expect("a completed replay typed into both lists", "is not what its stage-replay verdicts give",
           both, typed)
    # A booting stage typed into both lists: the receipt agrees with M6's input,
    # and only its own stage verdicts can show the id is not theirs. Since M6
    # counts a stage in both lists, this check carries M6 (the M6 review, W1).
    typed_boot = next(i for i in ("13", "11", "12", "09", "99") if i not in receipt["booting"])
    b = copy.deepcopy(receipt)
    b["booting"] = sorted(set(b["booting"]) | {typed_boot})
    expect("a booting stage typed into both lists", "is not its stages'", b,
           dict(results, booting=b["booting"]))

    for st in receipt["stages"]:
        # An id that is in the stage list but is not this level's.
        other = next(i for i in ("11", "12", "09") if i != st["stage_id"])
        rec = copy.deepcopy(receipt)
        res = copy.deepcopy(results)
        for s2 in rec["stages"]:
            if s2["stage"] == st["stage"]:
                s2["stage_id"] = other
        swap = lambda xs: sorted(other if x == st["stage_id"] else x for x in xs)  # noqa: E731
        rec["booting"], res["booting"] = swap(rec["booting"]), swap(res["booting"])
        expect(f"{st['stage']}: an id that is not its level's", "is not level", rec, res)

    # Live inputs moved in a copied tree.
    with copied_tree(receipt, have_private) as td:
        t = Path(td)
        victim = sorted((t / "runtime").glob("*.c"))[0]
        victim.write_bytes(victim.read_bytes() + b"\n")
        expect("a runtime byte moved", "the receipt is stale", receipt, results, root=t)
    with copied_tree(receipt, have_private) as td:
        t = Path(td)
        sl = t / "Documentation/receipts/stage-list.json"
        doc = json.loads(sl.read_text())
        doc["stages"] = [x for x in doc["stages"] if x not in {s["stage_id"] for s in receipt["stages"]}]
        sl.write_text(json.dumps(doc))
        expect("the stage dropped from the stage list", "is not in the stage list",
               receipt, results, root=t)

    control = goal_falsifications(receipt, expect)
    if control:
        survived.append(f"the synthetic stage replay's control does not pass, so no replay-complete "
                        f"falsification shows anything: {control}")
    if not have_private:
        return survived

    with copied_tree(receipt, True) as td:
        t = Path(td)
        for st in receipt["stages"]:
            p = t / st["trace"]
            p.write_bytes(p.read_bytes() + b"\n")
        expect("a trace byte moved", "does not hash to the receipt's pin", receipt, results, root=t)
    with copied_tree(receipt, True) as td:
        t = Path(td)
        for st in receipt["stages"]:
            (t / st["trace"]).unlink()
        expect("a kept trace deleted under its verdict", "does not hash to the receipt's pin",
               receipt, results, root=t)
    # A missing file hashes to "" (TR.sha256), so an emptied pin over a deleted
    # file must still be a problem, and not a read that raises.
    for key in ("trace", "run"):
        with copied_tree(receipt, True) as td:
            t = Path(td)
            emptied = copy.deepcopy(receipt)
            for st in emptied["stages"]:
                (t / st[key]).unlink()
                st[f"{key}_sha256"] = ""
            expect(f"a kept {key} deleted and its pin emptied", f"its kept {key} ", emptied,
                   results, root=t)
    with copied_tree(receipt, True) as td:
        t = Path(td)
        for st in receipt["stages"]:
            p = t / st["run"]
            p.write_bytes(p.read_bytes() + b"\n")
        expect("a run record byte moved", "does not hash to the receipt's pin", receipt, results,
               root=t)

    for st in receipt["stages"]:
        trace = SB.read_trace(ROOT / st["trace"])
        pump = json.loads((ROOT / st["run"]).read_text()).get("pump") or {}
        name = st["stage"]
        if st["booting"]:
            gone = copy.deepcopy(trace)
            gone[-1]["player"] = "0x00000000"
            expect(f"{name}: the player gone at the last tick", "recomputed booting",
                   receipt, results, traces={name: gone})
            left = copy.deepcopy(trace)
            left[-1]["game_state"] = "0x0000"
            expect(f"{name}: out of the in-stage state at the last tick", "recomputed booting",
                   receipt, results, traces={name: left})
            frozen = copy.deepcopy(trace)
            frozen[-1]["ingame_frames"] = frozen[-2]["ingame_frames"]
            expect(f"{name}: the in-game frame counter stalled", "recomputed ingame_frames_stalled",
                   receipt, results, traces={name: frozen})
            short = copy.deepcopy(trace)[:st["in_stage_from"] + SB.MIN_IN_STAGE - 2]
            expect(f"{name}: fewer in-stage ticks than the minimum", "recomputed booting",
                   receipt, results, traces={name: short}, pumps={name: dict(pump, empty_polls=len(short))})
        else:
            # A stage that does not boot must be caught the other way round.
            made = copy.deepcopy(trace)
            for d in made:
                d.update(current_level=f"{st['level']:#06x}", game_mode="0x00000005",
                         game_state="0x0010", player="0x10000000")
            for i, d in enumerate(made):
                d["ingame_frames"] = f"{i:#010x}"
            expect(f"{name}: made to boot", "recomputed booting", receipt, results,
                   traces={name: made}, pumps={name: dict(pump, quit_by_host=1, quit_by_guest=0,
                                                            injected=0,
                                                            iterations=pump.get("poll_limit"))})
        injected = dict(pump, injected=1)
        expect(f"{name}: a message injected into the run", "recomputed ended_at_leash",
               receipt, results, pumps={name: injected})
        dropped = copy.deepcopy(trace)[:-1]
        expect(f"{name}: a trace row missing", "recomputed", receipt, results,
               traces={name: dropped})
    return survived


def main() -> int:
    if not SB.RECEIPT.is_file():
        print(f"STAGE_RESULTS_FAILED {SB.RECEIPT.relative_to(ROOT)} is absent")
        return 1
    receipt = json.loads(SB.RECEIPT.read_text())
    if not SB.RESULTS.is_file():
        print(f"STAGE_RESULTS_FAILED {SB.RESULTS.relative_to(ROOT)} is absent; M6 would read "
              f"nothing the receipt holds")
        return 1
    results = json.loads(SB.RESULTS.read_text())
    # Both, always: when the live record already fails, a falsification can
    # survive for that reason alone, and printing only the survivors would hide
    # the problems that made them survive.
    survived = falsifications(receipt, results)
    probs = problems(receipt, results)
    for s in survived:
        print(f"falsification survived: {s}")
    for p in probs:
        print(p)
    if survived or probs:
        print(f"STAGE_RESULTS_FAILED falsifications={len(survived)} problems={len(probs)}")
        return 1
    print(f"STAGE_RESULTS_OK stages={len(receipt.get('stages', []))} "
          f"booting={len(receipt.get('booting', []))} "
          f"stage_replays={len(receipt.get('replays', []))} "
          f"replay_complete={len(receipt.get('replay_complete', []))} falsifications={RAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
