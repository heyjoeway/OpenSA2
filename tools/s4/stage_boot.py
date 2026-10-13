#!/usr/bin/env python3
"""M6's two lists: which stages the lifted build boots, and which stage replays reach their goal.

Subtitle: the stage run (tools/s4/stage_run.py) with the tick hook's stage
trace on, judged tick by tick against the predicate the stage-route decision
names; and every registered stage replay's kept goal-latch column, judged
against the native goal tick. Both are written as M6's input and a tracked
receipt, and this tool is the only writer of either list.

    source tools/env.sh
    .venv/bin/python tools/s4/stage_boot.py            # run every stage, print the verdicts
    .venv/bin/python tools/s4/stage_boot.py --write    # and write the receipt and M6's input

Booting
-------
Documentation/decisions/2026-09-23-S4-stage-route.md, item 5: M6's "booting"
is CurrentLevel at the stage, the player present, the in-stage mode. Read per
tick by harness/replay/wrap_tick_probe.c's stage trace (SA2_STAGE_TRACE):

  current_level  CurrentLevel (int16 at 0x01934B70) equals the stage's number
  game_mode      GameMode (0x0174B040) is GameMode_Level, 5
  game_state     GameState (int16 at 0x01934BE0) is GameStates_Ingame, 16 --
                 a strengthening of the decision's three: the level is loaded
                 and being played, not loading or restarting
  player         MainCharObj1[0] (0x01DEA6C0) is not null

A stage boots when all four hold from some tick to the last tick of the run,
for at least MIN_IN_STAGE ticks, while the in-game frame counter
(FrameCountIngame, 0x0174B03C) advances on every one of those ticks but the
first -- a game frozen in the level holds the four and stops that counter --
and the run ended at the harness's leash: the host's WM_QUIT at the poll
budget, with nothing injected and the guest not quitting on its own, as the
run's own pump record says. The trace must have one row per GameLoop return
the run counted. A stage that reaches the state and then crashes has not
booted; neither has one whose state flickers or freezes. The names and
values come from the Mod Loader's own headers (SA2ModLoader/include/
SA2Enums.h, SA2Variables.h).

What "booting" means here is scoped to S4's exit. The stage is entered through
the reproduced test spawn, and the harness itself writes CurrentLevel, so the
game has not chosen the stage. The user ruled on 2026-09-23 that "test spawn
does not count toward D1 at S6", keeping this count to G4, and ratified the
route itself (Documentation/decisions/2026-09-23-S4-stage-route.md).

Replay complete
---------------
M6's criterion_note: "a registered stage replay reaching the goal latch". A
stage replay is an entry in Documentation/replays.json with a `stage` block
(`name`, `level`). It completes when:

  * it has at least two native captures (the registered one and a repeat),
    each a run of its own, in a directory no other of its captures shares,
    and each carrying the input probe's end record, sa2-input-end.csv; all of
    them name one goal tick above zero with no missed input row --
    stage-route item 5's two replays that "set the goal latch on the same
    tick". One run listed twice is one replay, and is refused;
  * every file the verdict rests on is the one pinned for it, by sha256: each
    native tick capture and its end record as the registry pins them, and the
    lifted run's tick capture, goal-latch column (harness/stage_goal_probe.c,
    SA2_GOAL_LATCH) and stage trace as the tick-replay receipt
    (Documentation/receipts/s4-tick-replay-v1.json) records them;
  * each file sits in its run's directory: every end record beside the tick
    capture it belongs to, and the latch column and stage trace beside the
    lifted tick capture that M5 compares, so the goal tick and the rise come
    from the runs the replay is;
  * the first tick from 1 on whose latch byte (0x0174B002) is nonzero is that
    goal tick, and the latch rises there, from zero on the row before. The
    native probe takes the first OnFrame whose latch is nonzero. A lifted row t
    is the latch as GameLoop call t+1 found it, which is the latch tick t left
    unless something between the two calls moved it (the probe's comment
    names what that argument does not cover); a latch moved there lands the
    lifted rise a tick off, and this check fails rather than passes;
  * the latch column has a row per GameLoop call: as many rows as the stage
    trace, or one more when the run ended inside a call;
  * the stage trace shows CurrentLevel at the stage's level on the goal tick.

`replay_complete` lists the stage ids of the replays that complete. It is
derived from those verdicts and nothing else, and
Documentation/scripts/check_stage_results.py recomputes each verdict from the
same kept files, so a stage id typed into either list fails the gate.

The stage replay's lifted run is tools/s4/tick_replay.py's; this tool runs
nothing for it. Run it after `tick_replay.py --receipt`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import stage_run as SR  # noqa: E402
import tick_replay as TR  # noqa: E402
sys.path.append(str(HERE.parent / "oracle"))
import save_state as SS  # noqa: E402  the pinned saves every lifted run starts from

RECEIPT = ROOT / "Documentation" / "receipts" / "s4-stage-boot-v1.json"
RESULTS = ROOT / "private" / "generated" / "stage-results.json"
STAGE_LIST = ROOT / "Documentation" / "receipts" / "stage-list.json"
REACH = ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
REGISTRY_REL = "Documentation/replays.json"
TICK_REPLAY_REL = "Documentation/receipts/s4-tick-replay-v1.json"
GENERATED_BY = "tools/s4/stage_boot.py"

GAME_MODE_LEVEL = 5          # GameMode_Level (SA2Enums.h)
GAME_STATE_INGAME = 16       # GameStates_Ingame (SA2Enums.h)
TRACE_HEADER = ["tick", "current_level", "game_mode", "game_state", "ingame_frames", "player",
                "x", "y", "z"]
# Five seconds of the game's 60 Hz: long enough that reaching the state on the
# run's last ticks, or passing through it, is not booting.
MIN_IN_STAGE = 300

# harness/stage_goal_probe.c's column, and harness/replay/native_input_probe.c's
# end record, exactly as each writes them.
GOAL_LATCH = "0x0174B002"
GOAL_HEADER = ["tick", "goal_latch"]
GOAL_VALUE = re.compile(r"0x[0-9a-f]{2}\Z")
END_HEADER = ["goal_tick", "end_tick", "misses"]
COUNT = re.compile(r"[0-9]+\Z")

# What a stage run is built and run from, beside the reach build it shares.
PIPELINE = ["tools/s4/stage_boot.py", "tools/s4/stage_run.py", "tools/s4/loop_run.py",
            "tools/s4/link_s4.py", "tools/s4/reach_receipt.py", "harness/crt_probe.c",
            "harness/replay/tick_probe_core.h", "harness/replay/tick_fields.h",
            "harness/replay/tick_digest.h", *SR.HOOKS,
            # which saves each stage run starts from, and how the overlay is cleared
            "Documentation/save-state.json", "tools/oracle/save_state.py"]


def refuse(message: str) -> SystemExit:
    return SystemExit(f"STAGE_BOOT_REFUSED {message}")


def inputs(root: Path = ROOT) -> dict:
    reach = json.loads((root / REACH.relative_to(ROOT)).read_text())
    return {"reach_manifest_sha256": TR.digest(reach.get("manifest", {})),
            "runtime_sha256": TR.runtime_digest(root),
            "pipeline": {t: TR.must_sha256(root / t) for t in PIPELINE}}


def read_trace(path: Path) -> list[dict]:
    rows = list(csv.reader(path.open(newline="")))
    if not rows or rows[0] != TRACE_HEADER:
        raise refuse(f"{path}: not a stage trace (header {rows[:1]})")
    out = []
    for i, r in enumerate(rows[1:], 1):
        d = dict(zip(TRACE_HEADER, r))
        # A tick that is no number is refused like one out of sequence: a
        # refusal the gate reports, not a ValueError (the M6 review, N5).
        if not COUNT.match(d.get("tick") or "") or int(d["tick"]) != i:
            raise refuse(f"{path}: tick {d.get('tick')} on row {i}")
        out.append(d)
    return out


def leash(pump: dict) -> tuple[bool, str]:
    """Whether the run ended at the harness's leash, from its own pump record:
    the host posted WM_QUIT at the poll budget, nothing else was injected, and
    the guest did not quit on its own."""
    why = []
    if not pump.get("quit_by_host"):
        why.append("the host did not post the quit")
    if pump.get("quit_by_guest"):
        why.append("the guest quit on its own")
    if pump.get("injected"):
        why.append(f"{pump.get('injected')} message(s) injected")
    if pump.get("iterations") != pump.get("poll_limit"):
        why.append(f"{pump.get('iterations')} iterations, not the poll limit {pump.get('poll_limit')}")
    return not why, "; ".join(why) or "the host's WM_QUIT at the poll budget"


def judge(trace: list[dict], level: int, pump: dict) -> dict:
    """The first tick from which every predicate holds to the run's end, if any,
    and whether that is booting: held for MIN_IN_STAGE ticks with the in-game
    frame counter advancing on each, the trace one row per GameLoop return,
    and the run ended at the leash."""
    def holds(d: dict) -> bool:
        return (int(d["current_level"], 0) == level and int(d["game_mode"], 0) == GAME_MODE_LEVEL
                and int(d["game_state"], 0) == GAME_STATE_INGAME and int(d["player"], 0) != 0)
    first = None
    for d in reversed(trace):
        if not holds(d):
            break
        first = int(d["tick"])
    last = int(trace[-1]["tick"]) if trace else 0
    window = [int(d["ingame_frames"], 0) for d in trace[first - 1:]] if first else []
    stalled = sum(1 for a, b in zip(window, window[1:]) if b <= a)
    at_leash, how = leash(pump)
    # One row per GameLoop return: the loop returns once per empty poll.
    rows_match = last == pump.get("empty_polls")
    in_stage = (last - first + 1) if first else 0
    return {"ticks": last, "in_stage_from": first, "in_stage_ticks": in_stage,
            "ingame_frames_stalled": stalled, "ended_at_leash": at_leash, "ended": how,
            "trace_rows_match_run": rows_match,
            "booting": bool(first) and in_stage >= MIN_IN_STAGE and stalled == 0
                       and at_leash and rows_match}


# ------------------------------------------------------------ replay complete

def read_goal(path: Path) -> list[int]:
    """harness/stage_goal_probe.c's column: the latch byte of rows 0..N-1, in
    order. Anything the probe cannot have written is refused, not skipped: a
    tick out of sequence, a value that is not one byte in the probe's own
    spelling (its unreadable marker 0xffffffff included)."""
    rows = list(csv.reader(path.open(newline="")))
    if not rows or rows[0] != GOAL_HEADER:
        raise refuse(f"{path}: not a goal-latch column (header {rows[:1]})")
    out = []
    for i, r in enumerate(rows[1:]):
        if len(r) != 2 or r[0] != str(i):
            raise refuse(f"{path}: row {i + 1} is {r}, not tick {i} and one value")
        if not GOAL_VALUE.match(r[1]):
            raise refuse(f"{path}: tick {i} holds {r[1]!r}, which is not a byte the probe writes")
        out.append(int(r[1], 16))
    return out


def read_end(path: Path) -> dict:
    """A native run's sa2-input-end.csv (harness/replay/native_input_probe.c):
    one row of goal_tick, end_tick and misses, in decimal."""
    rows = list(csv.reader(path.open(newline="")))
    if len(rows) != 2 or rows[0] != END_HEADER or len(rows[1]) != len(END_HEADER) \
            or not all(COUNT.match(v) for v in rows[1]):
        raise refuse(f"{path}: not one row of {','.join(END_HEADER)}")
    return {k: int(v) for k, v in zip(END_HEADER, rows[1])}


# Stage-route item 5: two native replays of the recording, which set the goal
# latch on the same tick. One capture shows nothing about repeating. The count
# is of end records; that each is a separate run is evidence_problems' check,
# which every verdict passes first (goal_verdict).
MIN_NATIVE_ENDS = 2


def judge_goal(latch: list[int], trace: list[dict], level: int, ends: list[dict]) -> dict:
    """Whether a stage replay's lifted run reached its goal on the native goal tick.

    `ends` are the native captures' end records, the registered capture first,
    one per run (evidence_problems refuses a run listed twice before this is
    asked). The native goal tick is theirs when there are at least
    MIN_NATIVE_ENDS of them, every one names the same tick above zero, and no
    replay missed a recorded input row; otherwise there is none, and nothing
    completes.

    The lifted goal tick is the first row from 1 on whose latch is nonzero, as
    the native probe takes the first such OnFrame, and the latch must rise
    there: the row before it reads zero. Row 0 is the latch before the first
    tick, which no OnFrame reads, so a latch already set there and held is no
    rise, and does not complete even against a native goal tick of 1.

    The column has a row per GameLoop call and the trace one per return, so
    the column may have one row more: a run that ended inside its last call.
    That row is the state the trace's last tick left, so it still reads a tick
    the trace holds.

    Nothing here asks how the run ends after the goal: a run that reaches the
    goal and then traps has reached it. The replay's M5 row is where the rest
    of the run is held."""
    goals = [e["goal_tick"] for e in ends]
    agreed = goals[0] if len(goals) >= MIN_NATIVE_ENDS and goals[0] > 0 \
        and len(set(goals)) == 1 and not any(e["misses"] for e in ends) else None
    lifted = next((t for t, v in enumerate(latch) if t >= 1 and v), None)
    rises = lifted is not None and latch[lifted - 1] == 0
    rows_match = len(latch) - len(trace) in (0, 1)
    level_at = (int(trace[lifted - 1]["current_level"], 0)
                if lifted is not None and lifted <= len(trace) else None)
    return {"native_goal_ticks": goals, "native_misses": [e["misses"] for e in ends],
            "native_goal_tick": agreed, "lifted_goal_tick": lifted, "latch_rises": rises,
            "latch_rows": len(latch), "trace_rows": len(trace),
            "latch_rows_match_trace": rows_match, "level_at_goal": level_at,
            "complete": agreed is not None and lifted == agreed and rises and rows_match
                        and level_at == level}


def stage_replays(root: Path = ROOT) -> list[dict]:
    """The registered replays that are stage replays: those with a `stage` block."""
    registry = json.loads((root / REGISTRY_REL).read_text())
    return [e for e in registry.get("replays", []) if "stage" in e]


def goal_evidence(entry: dict, doc: dict | None) -> dict:
    """What one stage replay's verdict rests on, as the registry and the
    tick-replay receipt record it: the stage, the native tick captures and
    their end records, and the lifted tick capture with the goal-latch column
    and stage trace beside it, each file with its sha256."""
    level = int(entry["stage"]["level"])
    caps = [entry["native"]] + list(entry["native"].get("repeats", []))
    lifted = (doc or {}).get("lifted") or {}
    latch, trace = lifted.get("goal_latch") or {}, lifted.get("stage_trace") or {}
    return {"replay": entry["name"], "stage": entry["stage"].get("name"), "level": level,
            "stage_id": f"{level:02d}",
            "captures": [c.get("capture") for c in caps],
            "captures_sha256": [c.get("sha256") for c in caps],
            "ends": [{"file": (c.get("end") or {}).get("file"),
                      "sha256": (c.get("end") or {}).get("sha256")} for c in caps],
            "lifted_capture": lifted.get("capture"), "lifted_capture_sha256": lifted.get("sha256"),
            "latch": latch.get("path"), "latch_sha256": latch.get("sha256"),
            "trace": trace.get("path"), "trace_sha256": trace.get("sha256")}


def run_dir(path: str) -> str:
    """The directory of a run's file, lexically: "a/../a/x" is "a/x"'s run."""
    return os.path.normpath(os.path.dirname(path))


def beside(a, b) -> bool:
    """Both files named, and in one run's directory."""
    return bool(a) and bool(b) and run_dir(a) == run_dir(b)


def native_run_problems(captures: list, ends: list[dict]) -> list[str]:
    """The native half of evidence_problems, which tools/s4/tick_replay.py asks
    of a stage entry before anything runs, so the two tools refuse the same
    entries: an end record that is not beside the tick capture it is registered
    with, and two native captures from one run's directory."""
    out = [f"native end record {k} ({e.get('file')}) is not beside its tick capture ({cap})"
           for k, (cap, e) in enumerate(zip(captures, ends))
           if not beside(e.get("file"), cap)]
    runs: dict[str, list[int]] = {}
    for k, cap in enumerate(captures):
        if cap:
            runs.setdefault(run_dir(cap), []).append(k)
    out += [f"native captures {' and '.join(map(str, ks))} are one run listed {len(ks)} times "
            f"({d}): each native replay must be a run of its own, in its own directory"
            for d, ks in runs.items() if len(ks) > 1]
    return out


def evidence_problems(ev: dict) -> list[str]:
    """Why the files a verdict reads are not the replay's own runs': an end
    record that is not beside the tick capture it is registered with, two
    native captures from one run's directory (native_run_problems), or a latch
    column or stage trace that is not beside the lifted tick capture. Every
    hash can match and the goal tick still come from another run, or the "two
    native replays" be one run listed twice."""
    out = native_run_problems(ev["captures"], ev["ends"])
    out += [f"the {what} ({rel}) is not beside the lifted tick capture ({ev['lifted_capture']})"
            for what, rel in (("goal-latch column", ev["latch"]), ("stage trace", ev["trace"]))
            if not beside(rel, ev["lifted_capture"])]
    return out


def pinned_files(ev: dict) -> list[tuple[str, str | None, str | None]]:
    """(what, path, pin) for every file a verdict rests on: the tick captures
    are read by nothing here, but a goal judged beside a capture that is not
    the registered one is judged on another run."""
    return ([(f"native tick capture {k}", c, s)
             for k, (c, s) in enumerate(zip(ev["captures"], ev["captures_sha256"]))]
            + [(f"native end record {k}", e["file"], e["sha256"]) for k, e in enumerate(ev["ends"])]
            + [("lifted tick capture", ev["lifted_capture"], ev["lifted_capture_sha256"]),
               ("goal-latch column", ev["latch"], ev["latch_sha256"]),
               ("stage trace", ev["trace"], ev["trace_sha256"])])


def goal_verdict(ev: dict, root: Path = ROOT) -> dict:
    """One stage replay's verdict from its pinned files; refused when a file is
    unnamed, missing, not what its pin says, or not its run's."""
    for problem in evidence_problems(ev):
        raise refuse(f"{ev['replay']}: {problem}")
    for what, rel, pin in pinned_files(ev):
        if not rel or not pin:
            raise refuse(f"{ev['replay']}: no {what} is recorded for it with a pin; register "
                         f"each native capture with its sha256 and end record, and run "
                         f"tools/s4/tick_replay.py --receipt")
        if TR.sha256(root / rel) != pin:
            raise refuse(f"{ev['replay']}: its {what} {rel} is missing or does not hash to its pin")
    v = judge_goal(read_goal(root / ev["latch"]), read_trace(root / ev["trace"]), ev["level"],
                   [read_end(root / e["file"]) for e in ev["ends"]])
    return {**ev, **v}


def goal_verdicts(root: Path = ROOT) -> list[dict]:
    """Every registered stage replay's verdict, in registry order."""
    entries = stage_replays(root)
    if not entries:
        return []
    receipt = root / TICK_REPLAY_REL
    docs = ({d.get("name"): d for d in json.loads(receipt.read_text()).get("replays", [])}
            if receipt.is_file() else {})
    out = []
    for e in entries:
        # A registered entry tick_replay.py recorded no run of: tick_replay
        # refused it or was not run. Named first, so the refusal says what to run
        # rather than which file is missing (the M6 review, W3).
        if e.get("name") not in docs:
            raise refuse(f"{e.get('name')}: {TICK_REPLAY_REL} records no run of it; run "
                         f"tools/s4/tick_replay.py --receipt. If tick_replay refuses the entry, "
                         f"take it back out of {REGISTRY_REL} and bring the refusal to the user")
        try:
            ev = goal_evidence(e, docs.get(e.get("name")))
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise refuse(f"{e.get('name')}: its registry entry has no usable stage block or "
                         f"native side: {err!r}") from None
        out.append(goal_verdict(ev, root))
    return out


def completed(replays: list[dict]) -> list[str]:
    """M6's replay_complete: the stage ids of the stage replays that complete."""
    return sorted({r["stage_id"] for r in replays if r["complete"]})


# ----------------------------------------------------------------- the runs

def stage_ids() -> list[str]:
    return [str(s) for s in json.loads(STAGE_LIST.read_text())["stages"]]


def run_stage(stage: str) -> dict:
    """One stage run with the stage trace on; the verdict and what it rests on."""
    level = SR.STAGES[stage][0]
    work = SR.OUT / f"stage-{stage}"
    # Its own name: `stage_run.py --trace` writes stage-trace.csv, and the pinned
    # trace must not be the one a traced stage walk overwrites (rule-5 review).
    trace = work / "stage-boot.trace.csv"
    trace.unlink(missing_ok=True)
    cmd, run_json, ticks = SR.command(stage, extra_env={"SA2_STAGE_TRACE": str(trace.relative_to(ROOT))})
    run_json.unlink(missing_ok=True)
    ticks.unlink(missing_ok=True)          # a capture left by an earlier run must not be pinned
    # From the pinned saves and an overlay that holds nothing else, as every
    # native run starts: a replay of a recording that passes the Light Dash
    # pickup leaves the game's save in the shared overlay, and a stage boot
    # measured over it would start from another state than the pin (the user's
    # ruling of 2026-09-24 that the save file is a recorded input).
    try:
        saves = SS.reset_lifted(cmd, ROOT)
        SS.require_empty(saves)
    except SS.SaveStateError as e:
        raise refuse(f"{stage}: {e.code}") from None
    # Without a device variable left in this shell, as every replay run: the
    # run's devices are the reach configuration's (tools/s4/reach_receipt.py).
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=3600,
                       env=SR.RR.clean_environ())
    if not run_json.is_file() or not trace.is_file():
        raise refuse(f"{stage}: the run wrote no record or no trace "
                     f"(loop_run exited {p.returncode})\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    # Kept beside the trace under names only this tool writes: the stage walk
    # rewrites the run record and the capture, and the gate re-reads both.
    kept_run, kept_ticks = work / "stage-boot.run.json", work / "stage-boot.ticks.csv"
    kept_run.write_bytes(run_json.read_bytes())
    kept_ticks.write_bytes(ticks.read_bytes() if ticks.is_file() else b"")
    d = json.loads(kept_run.read_text())
    pump = d.get("pump") or {}
    verdict = judge(read_trace(trace), level, pump)
    return {"stage": stage, "level": level, "stage_id": f"{level:02d}",
            "trace": str(trace.relative_to(ROOT)), "trace_sha256": TR.sha256(trace),
            "run": str(kept_run.relative_to(ROOT)), "run_sha256": TR.sha256(kept_run),
            "ticks_capture": str(kept_ticks.relative_to(ROOT)), "ticks_sha256": TR.sha256(kept_ticks),
            "stop": ((d.get("trap") or {}).get("note") or "")[:300],
            "iterations": pump.get("iterations"), "saves": saves, **verdict}


def results_doc(booting: list[str], replay_complete: list[str]) -> dict:
    """M6's input, private/generated/stage-results.json."""
    return {"generated_by": GENERATED_BY, "booting": booting, "replay_complete": replay_complete}


def receipt_doc(before: dict, stages: list[dict], replays: list[dict], head: str,
                dirty: bool) -> dict:
    """The tracked receipt, Documentation/receipts/s4-stage-boot-v1.json."""
    return {
        "schema": "sa2-recomp-receipt/v1", "id": "s4-stage-boot-v1",
        "question": "Does the lifted build enter each stage it is started in and stay in it, "
                    "and does each registered stage replay reach its goal on the native goal tick?",
        "generated_by": GENERATED_BY, "command": ".venv/bin/python tools/s4/stage_boot.py --write",
        "commit": head, "uncommitted_changes": dirty,
        "predicate": {"game_mode": GAME_MODE_LEVEL, "game_state": GAME_STATE_INGAME,
                      "current_level": "the stage's number", "player": "MainCharObj1[0] != 0",
                      "ingame_frames": "FrameCountIngame advances on every in-stage tick but the first",
                      "holds": f"from its first tick to the run's last, at least {MIN_IN_STAGE} ticks",
                      "run_ends": "at the leash, from the run's pump record",
                      "scope": "S4's exit (G4); test spawn does not count toward D1 at S6 (the user, 2026-09-23)"},
        "goal_predicate": {"latch": f"the byte at {GOAL_LATCH}, per GameLoop call "
                                    "(harness/stage_goal_probe.c)",
                           "native": f"at least {MIN_NATIVE_ENDS} registered native captures, each "
                                     "a run of its own in its own directory, whose "
                                     "sa2-input-end.csv files name one goal tick above zero, with "
                                     "no missed input row",
                           "lifted": "the first tick from 1 on whose latch is nonzero is that tick, "
                                     "the latch rises there from zero, and CurrentLevel is the "
                                     "stage's level on it",
                           "evidence": f"by sha256, each native tick capture and end record as "
                                       f"{REGISTRY_REL} pins them, and the lifted tick capture, "
                                       f"goal-latch column and stage trace as {TICK_REPLAY_REL} "
                                       "records them; each file beside its own run's tick capture"},
        "inputs": before, "stages": stages, "booting": sorted(r["stage_id"] for r in stages
                                                             if r["booting"]),
        "replays": replays, "replay_complete": completed(replays)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    ids = stage_ids()
    before = inputs()
    replays = goal_verdicts()
    results = [run_stage(st) for st in sorted(SR.STAGES)]
    if inputs() != before or goal_verdicts() != replays:
        raise refuse("an input moved while the stages ran")
    for r in results + replays:
        if r["stage_id"] not in ids:
            raise refuse(f"{r['stage']}: stage {r['stage_id']} is not in "
                         f"{STAGE_LIST.relative_to(ROOT)}")
    for r in results:
        print(f"STAGE_BOOT {r['stage']} ({r['stage_id']}): booting={r['booting']} "
              f"in-stage from tick {r['in_stage_from']} for {r['in_stage_ticks']} of "
              f"{r['ticks']} ticks (at least {MIN_IN_STAGE}); in-game frames stalled on "
              f"{r['ingame_frames_stalled']}; ended: {r['ended']}; trace rows match the run: "
              f"{r['trace_rows_match_run']}")
    for r in replays:
        print(f"STAGE_GOAL {r['replay']} ({r['stage_id']}): complete={r['complete']} native goal "
              f"tick {r['native_goal_tick']} (captures {r['native_goal_ticks']}), lifted "
              f"{r['lifted_goal_tick']}, level on it {r['level_at_goal']}; latch rows "
              f"{r['latch_rows']} against {r['trace_rows']} trace rows")
    if not a.write:
        return 0
    def git(*args: str) -> str:
        # A git that fails prints nothing, which read unchecked is commit ""
        # and a clean tree (the rule-5 review of the second fold, 2026-09-23).
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
        if p.returncode:
            raise SystemExit(f"git {' '.join(args)} exited {p.returncode}: "
                             f"{p.stderr.strip()[-300:]}")
        return p.stdout.strip()
    doc = receipt_doc(before, results, replays, git("rev-parse", "HEAD"),
                      bool(git("status", "--porcelain")))
    RESULTS.write_text(json.dumps(results_doc(doc["booting"], doc["replay_complete"]),
                                  indent=1, sort_keys=True) + "\n")
    RECEIPT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"STAGE_BOOT wrote {RESULTS.relative_to(ROOT)} and {RECEIPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
