#!/usr/bin/env python3
"""Does the d3d9 lifetime model play the attract demo, and do its breaks show what it says?

Subtitle: the lifted build from boot through the title screen's attract demo
under the Direct3D 9 lifetime model and under each of its three lifetime
breaks, one after another, each compared with the native capture and with the
model's own run; one short run under the fakepixels break for the texture
byte count; and six verdicts that can each fail.

    source tools/env.sh
    .venv/bin/python tools/s4/d3d9_life_check.py [--native DIR] [--polls N] [--timeout S] [--write]

Why
---
runtime/sa2_d3d9.c frees a resource's backing store at its true final release
under Direct3D 9's lifetime rules (the backing store's comment there), and
keeps three breaks that put back what it replaced. Until this tool, their
runs were made by hand under names of their own and read in a private scratch
file, and the runtime's comments cited that file's numbers.

Freeing a file texture's store also took away the bytes harness/crt_probe.c's
texture clause counts, so sa2_d3d9.c counts them at the release instead. The
clause itself cannot show that count works: under fakepixels it fails on the
break's own flag before it reads a byte. The fakepixels run below reads the
count alone.

The runs
--------
  model       ""          the Direct3D 9 model
  neverfree   neverfree   the old model: a fresh surface for every GetSurfaceLevel,
                          each with its own count and store, and no store freed
  ownzero     ownzero     the old model, each store freed at its own object's zero
  keepall     keepall     the Direct3D 9 model, nothing freed at a final release
  fakepixels  fakepixels  the Direct3D 9 model, every file texture's store filled
                          by this runtime at creation; run for FAKEPIXELS.polls
                          polls rather than the demo

Each is tools/s4/demo_run.py's command, under the link name `d3d9-life-check`
and the run's label, against the same native capture and its device profile,
with SA2_D3D9_BREAK set on every run -- the model's to the empty string, so a
break exported in the caller's shell cannot run in its place -- and its tick
capture and stage trace kept under private/generated/s4/d3d9-life/<label>/.
The runs are sequential.

The verdicts
------------
  model_finishes                   the model runs every poll to the host's quit
                                   and the game's own ExitProcess(0), with no
                                   call on a destroyed object, no release past
                                   zero, a consistent guest heap, and the stage
                                   trace compared with native on every native
                                   row and matching it
  neverfree_stops_at_the_arena     the old model stops on the guest heap arena
                                   having no room for a backing store
  model_equals_neverfree_before_the_wall
                                   the model's tick capture and stage trace
                                   equal neverfree's, byte for byte, on every
                                   row neverfree ran
  ownzero_matches_the_model        ownzero's tick capture equals the model's on
                                   every row ownzero ran, and so does its stage
                                   trace with the player read as null or not
                                   (a heap address, which moves with the heap)
  keepall_matches_the_model        keepall runs every poll the model ran to the
                                   same stop, and matches the model as ownzero
                                   must, on every one of the model's rows
  fakepixels_counted_after_release every file texture the fakepixels run's byte
                                   count reads (never locked, never copied into)
                                   holds bytes, each whose store went back to
                                   the guest heap at its final release included,
                                   and at least one did

"Every row" is counted rather than assumed. Each comparison says how many rows
it compared, and a verdict holds only when that is every row it names: two
traces a tick out of step, or sharing no tick, compare nothing, and a tick
either file holds twice is refused rather than compared.

The receipt is written only when all six hold.

What is refused, and so compares nothing: a run with no record, a run the
harness's wall-clock alarm stopped (the machine's load, not the model: rerun
with a larger --timeout), a run whose loop_run row does not carry the break
it was given, a run whose record read another device profile, a run whose
d3d9 counts say another model ran (level surfaces are the Direct3D 9 model's
alone; neverfree frees nothing; only fakepixels fills a texture), runs that
were not one binary, and an input that moved while they ran.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import demo_run as D  # noqa: E402
import device_profile as DP  # noqa: E402
import stage_run as SR  # noqa: E402
import tick_replay as TR  # noqa: E402

RECEIPT = ROOT / "Documentation" / "receipts" / "s4-d3d9-life-v1.json"
OUT = ROOT / "private" / "generated" / "s4" / "d3d9-life"
NAME = "d3d9-life-check"


class Arm(NamedTuple):
    label: str
    brk: str          # SA2_D3D9_BREAK, set even when empty
    d3d9_model: bool  # the Direct3D 9 model's level surfaces, not the old model's
    polls: int = 0    # its own poll budget; 0 for the check's (--polls)


MODEL = Arm("model", "", True)
NEVERFREE = Arm("neverfree", "neverfree", False)
OWNZERO = Arm("ownzero", "ownzero", False)
KEEPALL = Arm("keepall", "keepall", True)
# fakepixels' verdict reads the file textures' rows, not the demo, so its run
# is only as long as the game takes to create and release some of them. A
# budget too short to see a release fails the verdict rather than passing it.
FAKEPIXELS = Arm("fakepixels", "fakepixels", True, 1500)
BREAKS = (NEVERFREE, OWNZERO, KEEPALL)        # the lifetime breaks, each compared with the model
ARMS = (MODEL, *BREAKS, FAKEPIXELS)

# runtime/sa2_d3d9.c obj_alloc's note when the guest heap cannot hold a store.
ARENA_NOTE = "the guest heap arena has no room for"
# harness: the guest ending the process itself, with status 0.
EXIT_NOTE = "the guest called ExitProcess(0)"
# runtime/sa2_trap.h: SA2_TRAP_TIMEOUT, the call exceeded its wall-clock budget.
TRAP_TIMEOUT = 4
# harness/crt_probe.c's texture clause, recorded for each run beside the verdicts.
TEXTURE_CLAUSE = "no_texture_surface_holds_bytes_this_runtime_wrote"

CLAIMS = {
    "model_finishes":
        "the model runs every poll to the host's quit and the game's own ExitProcess(0), "
        "with no call on a destroyed object, no release past zero, a consistent guest heap, "
        "and the stage trace compared with the native capture on every native row and "
        "matching it",
    "neverfree_stops_at_the_arena":
        "neverfree, the old model, stops on the guest heap arena having no room for a "
        "backing store",
    "model_equals_neverfree_before_the_wall":
        "the model's tick capture and stage trace equal neverfree's on every row neverfree ran",
    "ownzero_matches_the_model":
        "ownzero's tick capture equals the model's on every row ownzero ran, and so does its "
        "stage trace with the player read as null or not",
    "keepall_matches_the_model":
        "keepall, the model with nothing freed at a final release, runs every poll the model "
        "ran to the same stop, and its tick capture equals the model's on every row, and so "
        "does its stage trace with the player read as null or not",
    "fakepixels_counted_after_release":
        "under fakepixels, every file texture the probe's byte count reads (never locked, "
        "never copied into) holds non-zero bytes, each whose store went back to the guest "
        "heap at its final release included, and at least one did",
}

# What the runs are built and run from, pinned in the receipt's inputs.pipeline
# (check_receipt_pins.py reads it). The native capture is private, so it is
# pinned beside the pipeline and not in it.
PIPELINE = ["runtime/sa2_d3d9.c", "runtime/sa2_d3d9.h", "runtime/sa2_d3d9_m7_list.h",
            "harness/crt_probe.c", *SR.HOOKS,
            "harness/replay/tick_probe_core.h", "harness/replay/tick_fields.h",
            "harness/replay/tick_digest.h", "tools/s4/d3d9_life_check.py",
            "tools/s4/demo_run.py", "tools/s4/stage_run.py", "tools/s4/device_profile.py",
            "tools/oracle/oracle_mode.py", "tools/s4/reach_receipt.py",
            "tools/s4/tick_replay.py", "tools/s4/loop_run.py", "tools/s4/link_s4.py"]

# ------------------------------------------------------------- comparing


def _repeated(lines: list[str]) -> int | str | None:
    """The first tick a CSV body holds twice, or None."""
    seen: set[str] = set()
    for line in lines:
        t = line.split(",", 1)[0]
        if t in seen:
            return int(t) if t.isdigit() else t
        seen.add(t)
    return None


def same_rows(a: Path, b: Path) -> dict:
    """Two CSV files row for row, by the tick in their first column: how many
    rows each has, how many distinct ticks both have, the first of those (in
    a's order) whose rows differ in any byte, and the first tick each file
    holds twice. Different headers, or a tick either file holds twice,
    compare nothing: a repeated tick has no one row to compare."""
    x = a.read_text().splitlines() if a.is_file() else []
    y = b.read_text().splitlines() if b.is_file() else []
    got = {"rows_a": max(0, len(x) - 1), "rows_b": max(0, len(y) - 1),
           "headers_match": bool(x) and bool(y) and x[0] == y[0],
           "repeated_tick_a": _repeated(x[1:]), "repeated_tick_b": _repeated(y[1:]),
           "shared_rows": 0, "first_difference": None}
    if not _comparable(got):
        return got
    by_tick = {line.split(",", 1)[0]: line for line in y[1:]}
    for line in x[1:]:
        other = by_tick.get(line.split(",", 1)[0])
        if other is None:
            continue
        got["shared_rows"] += 1
        if other != line and got["first_difference"] is None:
            got["first_difference"] = int(line.split(",", 1)[0])
    return got


def _comparable(cmp: dict) -> bool:
    """Whether same_rows compared anything: the headers match and no tick repeats."""
    return bool(cmp["headers_match"]) and cmp.get("repeated_tick_a") is None and \
        cmp.get("repeated_tick_b") is None


def _rows(cmp: dict, what: str, whose: str) -> list[str]:
    """Why a same_rows(model's, `whose`) comparison is not agreement on every
    shared row."""
    if not cmp["headers_match"]:
        return [f"{what}: the headers differ, or a file is missing"]
    if not _comparable(cmp):
        return [f"{what}: {who} holds tick {cmp[k]} twice"
                for k, who in (("repeated_tick_a", "the model's"), ("repeated_tick_b", whose))
                if cmp.get(k) is not None]
    if not cmp["shared_rows"]:
        return [f"{what}: no row is shared"]
    if cmp["first_difference"] is not None:
        return [f"{what}: {whose} first differs at tick {cmp['first_difference']}"]
    return []


def _trace_repeats(trace: dict, native: str, lifted: str) -> list[str]:
    """Why a demo_run.compare_traces result read a tick either trace holds twice."""
    return [f"the stage trace: {who} holds tick {trace[k]} twice"
            for k, who in (("repeated_tick_native", native), ("repeated_tick_lifted", lifted))
            if trace.get(k) is not None]

# -------------------------------------------------------------- judging


def ran_its_model(arm: Arm, record: dict, env: dict) -> list[str]:
    """Why a run did not run `arm`; empty when it did. The break is read from
    loop_run's row for the run, because the run record does not echo it, and
    the counts say which model ran: only the Direct3D 9 model builds a level
    surface, neverfree frees no store, and only fakepixels fills a texture."""
    why = []
    if env.get("SA2_D3D9_BREAK") != arm.brk:
        why.append(f"its loop_run row sets SA2_D3D9_BREAK to {env.get('SA2_D3D9_BREAK')!r}, "
                   f"not {arm.brk!r}")
    tex = (record.get("d3d9") or {}).get("textures_from_files")
    filled = tex.get("with_decoded_pixels") if isinstance(tex, dict) else None
    if filled is None:
        why.append("its record has no d3d9.textures_from_files.with_decoded_pixels")
    elif arm == FAKEPIXELS and not filled:
        why.append("no texture filled by this runtime, where fakepixels fills every file texture")
    elif arm != FAKEPIXELS and filled:
        why.append(f"{filled} textures filled by this runtime, which only fakepixels does")
    life = (record.get("d3d9") or {}).get("lifetime")
    if not isinstance(life, dict):
        return why + ["its record has no d3d9.lifetime block"]
    levels = life.get("level_surfaces")
    if arm.d3d9_model and not levels:
        why.append(f"{levels} level surfaces: the Direct3D 9 model's GetSurfaceLevel never ran")
    if not arm.d3d9_model and levels != 0:
        why.append(f"{levels} level surfaces, which only the Direct3D 9 model builds")
    if arm == NEVERFREE and life.get("guest_bytes_freed") != 0:
        why.append(f"{life.get('guest_bytes_freed')} bytes freed, where neverfree frees none")
    # ownzero frees each store at its own object's count of zero; one that freed
    # nothing is neverfree by another name, and its match would show nothing
    # (the review of the second fold, W1).
    if arm == OWNZERO and not life.get("guest_bytes_freed"):
        why.append(f"{life.get('guest_bytes_freed')} bytes freed, where ownzero frees each "
                   "store at its object's own zero")
    return why


def finishes(record: dict, polls: int) -> list[str]:
    """Why a run did not run every poll to the host's quit and the game's own
    ExitProcess(0) with nothing injected; empty when it did."""
    pump, note = record.get("pump") or {}, (record.get("trap") or {}).get("note") or ""
    why = []
    if pump.get("iterations") != polls:
        why.append(f"{pump.get('iterations')} iterations, not the {polls} polls asked for")
    if pump.get("quit_by_host") != 1 or pump.get("quit_by_guest") != 0:
        why.append("the loop did not end on the host's quit")
    if pump.get("injected") != 0:
        why.append(f"injected {pump.get('injected')} message(s), not 0")
    if not note.startswith(EXIT_NOTE):
        why.append(f"its stop is not the game's own ExitProcess(0): {note[:300]}")
    return why


def model_finishes(record: dict, polls: int, vs_native: dict) -> list[str]:
    """`vs_native` is demo_run.compare_traces(native rows, the model's rows)."""
    life = (record.get("d3d9") or {}).get("lifetime") or {}
    heap = record.get("heap") or {}
    why = finishes(record, polls)
    for k in ("calls_on_destroyed", "releases_past_zero"):
        if life.get(k) != 0:
            why.append(f"{k} is {life.get(k)}, the first from {life.get(k + '_first_caller')}")
    if heap.get("consistent") is not True:
        why.append("the guest heap walk is not consistent")
    repeats = _trace_repeats(vs_native, "native's", "the model's")
    why += repeats
    first = vs_native.get("first_divergence")
    if first:
        why.append(f"the stage trace first differs from native at tick {first['tick']} in "
                   f"{', '.join(first['columns'])}")
    n, c = vs_native.get("native_rows"), vs_native.get("compared_rows")
    if not n:
        why.append("the stage trace: native's has no rows, so none was compared")
    elif c != n and not first and not repeats:
        why.append(f"the stage trace compared {c} of native's {n} rows (the model has "
                   f"{vs_native.get('lifted_rows')}), so not every native row was compared")
    return why


def stops_at_the_arena(record: dict) -> list[str]:
    note = (record.get("trap") or {}).get("note") or ""
    return [] if ARENA_NOTE in note else [f"its stop is not the arena: {note[:300]}"]


def equals_before_the_wall(ticks: dict, trace: dict) -> list[str]:
    """`ticks` and `trace` are same_rows(model's, neverfree's)."""
    why = _rows(ticks, "the tick capture", "neverfree's") + \
        _rows(trace, "the stage trace", "neverfree's")
    for what, cmp in (("tick capture", ticks), ("stage trace", trace)):
        if _comparable(cmp) and cmp["shared_rows"] and cmp["shared_rows"] != cmp["rows_b"]:
            why.append(f"the model's {what} has {cmp['shared_rows']} of neverfree's "
                       f"{cmp['rows_b']} rows")
    return why


def matches_the_model(label: str, ticks: dict, trace: dict) -> list[str]:
    """`ticks` is same_rows(model's, the arm's); `trace` is
    demo_run.compare_traces(model's rows, the arm's rows). Every row the arm
    ran is compared or the verdict fails: each of its tick capture's rows
    shared with the model's, and each of its stage trace's rows reached by the
    walk, which stops at the first model tick the arm lacks."""
    why = _rows(ticks, "the tick capture", f"{label}'s")
    if _comparable(ticks) and ticks["shared_rows"] and ticks["shared_rows"] != ticks["rows_b"]:
        why.append(f"the tick capture: {ticks['shared_rows']} of {label}'s {ticks['rows_b']} "
                   "rows are the model's")
    repeats = _trace_repeats(trace, "the model's", f"{label}'s")
    why += repeats
    first = trace.get("first_divergence")
    if not trace.get("lifted_rows") or not trace.get("native_rows"):
        why.append("the stage trace: a trace is empty")
    elif trace.get("compared_rows") != trace["lifted_rows"] and not first and not repeats:
        why.append(f"the stage trace: {trace.get('compared_rows')} of {label}'s "
                   f"{trace['lifted_rows']} rows were compared")
    if first:
        why.append(f"the stage trace: {label}'s first differs at tick {first['tick']} in "
                   f"{', '.join(first['columns'])}")
    return why


def keepall_matches(record: dict, polls: int, ticks: dict, trace: dict,
                    model_record: dict) -> list[str]:
    """keepall is the model with nothing freed at a final release, and
    runtime/sa2_d3d9.c says it shows what moved the wall; a keepall stopped at
    neverfree's wall would still match the model on every row the two share.
    So it must run what the model ran: every poll, to the same stop, and every
    one of the model's rows. `ticks` and `trace` are as matches_the_model's.
    And it must free less than the model: both give back a store a lock grew
    past, so the difference is the frees at a final release, which keepall
    exists not to make; a keepall that freed as much as the model would be the
    model under another name (the review of the second fold, W1)."""
    why = [f"keepall: {w}" for w in finishes(record, polls)]
    kept = ((record.get("d3d9") or {}).get("lifetime") or {}).get("guest_bytes_freed")
    freed = ((model_record.get("d3d9") or {}).get("lifetime") or {}).get("guest_bytes_freed")
    if not isinstance(kept, int) or not isinstance(freed, int) or not kept < freed:
        why.append(f"keepall freed {kept} bytes and the model {freed}: keepall must free less, "
                   "or nothing shows it kept a store the model frees at a final release")
    why += matches_the_model("keepall", ticks, trace)
    if _comparable(ticks) and ticks["rows_b"] != ticks["rows_a"]:
        why.append(f"the tick capture: keepall has {ticks['rows_b']} rows to the model's "
                   f"{ticks['rows_a']}")
    if trace.get("lifted_rows") != trace.get("native_rows"):
        why.append(f"the stage trace: keepall has {trace.get('lifted_rows')} rows to the "
                   f"model's {trace.get('native_rows')}")
    return why


def _surface(row: dict) -> int | None:
    try:
        return int(str(row.get("surface")), 16)
    except ValueError:
        return None


def counted_after_release(record: dict) -> list[str]:
    """`record` is the fakepixels run's. fakepixels fills every file texture's
    store at creation (runtime/sa2_d3d9.c), so each row harness/crt_probe.c's
    byte count reads -- answered, never locked, never copied into -- must
    count non-zero bytes. A row whose store went back to the guest heap at its
    final release reads surface 0, and counts them only through the count
    sa2_d3d9.c takes at that release; without it the row reads zero, and the
    texture clause's byte count passes over every released texture. At least
    one such row must be read, or the release was not exercised."""
    tex = (record.get("d3d9") or {}).get("textures_from_files") or {}
    rows = [r for r in tex.get("rows") or []
            if not r.get("refused") and not r.get("locks") and r.get("guest_copied") is not True]
    released = [r for r in rows if _surface(r) == 0]
    empty = [r for r in rows if not r.get("surface_nonzero_bytes")]
    why = []
    if not rows:
        why.append("no texture row is one the byte count reads")
    elif not released:
        why.append(f"none of the {len(rows)} rows the byte count reads was released, so no "
                   "count taken at a final release was read")
    if empty:
        gone = sum(1 for r in empty if _surface(r) == 0)
        why.append(f"{len(empty)} of the {len(rows)} rows the byte count reads count no "
                   f"non-zero byte, {gone} of them released; the first is row "
                   f"{empty[0].get('seq')}")
    return why


def judge(runs: dict[str, dict], polls: int) -> dict[str, list[str]]:
    """Each verdict and why it does not hold (empty when it does). `runs` is
    measure()'s output by label."""
    m, n, k = runs["model"], runs["neverfree"], runs["keepall"]
    return {
        "model_finishes": model_finishes(m["record"], polls, m["vs_native_trace"]),
        "neverfree_stops_at_the_arena": stops_at_the_arena(n["record"]),
        "model_equals_neverfree_before_the_wall":
            equals_before_the_wall(n["vs_model"]["ticks"], n["vs_model"]["trace"]),
        "ownzero_matches_the_model":
            matches_the_model("ownzero", runs["ownzero"]["vs_model"]["ticks"],
                              runs["ownzero"]["vs_model"]["trace"]),
        "keepall_matches_the_model":
            keepall_matches(k["record"], polls, k["vs_model"]["ticks"], k["vs_model"]["trace"],
                            m["record"]),
        "fakepixels_counted_after_release": counted_after_release(runs["fakepixels"]["record"]),
    }


def answer(verdicts: dict[str, list[str]]) -> str:
    """The receipt's answer, generated from the verdicts rather than typed."""
    held = [CLAIMS[k] for k, why in verdicts.items() if not why]
    return "Each holds: " + "; ".join(held) + "." if len(held) == len(verdicts) else \
        "Not every verdict holds."

# ------------------------------------------------------------------ files


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sha_of(path: Path) -> str | None:
    return TR.sha256(path) if path.is_file() else None


def inputs(native: Path, root: Path = ROOT) -> dict:
    return {"runtime_sha256": TR.runtime_digest(root),
            "pipeline": {t: TR.must_sha256(root / t) for t in PIPELINE},
            "native": {"path": DP.rel(native, root),
                       "stage_trace_sha256": TR.must_sha256(native / "sa2-stage-trace.csv"),
                       "tick_capture_sha256": TR.must_sha256(native / "sa2-tick-capture.csv")}}


def provenance() -> dict:
    """The commit the runs start from and whether the tree differs from it; a
    git that fails is refused rather than read as commit "" and a clean tree.
    Neither may take the index lock: `git status` refreshes the index when it
    can, which is a write."""
    def git(*args: str) -> str:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        if p.returncode:
            raise SystemExit(f"D3D9_LIFE_REFUSED git {' '.join(args)} exited {p.returncode}: "
                             f"{p.stderr.strip()[-300:]}")
        return p.stdout.strip()
    return {"commit": git("rev-parse", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain"))}


def command(a, native: Path) -> str:
    return (f".venv/bin/python tools/s4/d3d9_life_check.py --native {DP.rel(native, ROOT)} "
            f"--polls {a.polls} --timeout {a.timeout} --write")

# ------------------------------------------------------------------- runs


def arm_env(arm: Arm) -> dict[str, str]:
    """What one run adds to demo_run's settings. SA2_D3D9_BREAK is set even when
    empty: loop_run lays a run's variables over os.environ, so left unset, a
    break the caller exported would run in the model's place."""
    return {"SA2_D3D9_BREAK": arm.brk}


def run_arm(arm: Arm, a, profile: dict | str) -> dict:
    """One demo run under `arm`: its record, its files, loop_run's row for it
    (the settings it ran with) and the digest of the binary it ran."""
    cmd, run_json, ticks, trace = D.command(arm.polls or a.polls, profile, a.timeout, name=NAME,
                                            label=arm.label, out=OUT / arm.label,
                                            extra_env=arm_env(arm))
    matrix = SR.RUNDIR / f"{NAME}.matrix.json"
    for f in (run_json, ticks, trace, matrix):
        f.unlink(missing_ok=True)       # a file an earlier run left must not read as this one's
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=a.timeout + 1800, env=DP.clean_environ())
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"D3D9_LIFE_REFUSED {arm.label}: loop_run ran past {e.timeout} s")
    if not run_json.is_file():
        raise SystemExit(f"D3D9_LIFE_REFUSED {arm.label}: no run record (loop_run exited "
                         f"{p.returncode})\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    m = json.loads(matrix.read_text()) if matrix.is_file() else {}
    row = next((r for r in m.get("rows", []) if r.get("label") == arm.label), {})
    return {"record": json.loads(run_json.read_text()), "run_json": run_json,
            "files": {"ticks": ticks, "trace": trace}, "env": row.get("env") or {},
            "binary_sha256": m.get("sha256")}


def measure(arm: Arm, got: dict, native: Path, ntrace: list[dict]) -> dict:
    """One run as judge() and the receipt read it."""
    return {**got, "arm": arm,
            "vs_native_trace": D.compare_traces(ntrace, D.read_trace(got["files"]["trace"])),
            "vs_native_ticks": SR.compare(native / "sa2-tick-capture.csv", got["files"]["ticks"])}


def against_model(model: dict, other: dict) -> dict:
    """The comparisons judge() reads between the model's run and a lifetime
    break's: the tick capture byte for byte, and the stage trace byte for byte
    against neverfree's and with the player as null or not against the
    others'."""
    mt, ot = model["files"], other["files"]
    trace = (same_rows(mt["trace"], ot["trace"]) if other["arm"] == NEVERFREE
             else D.compare_traces(D.read_trace(mt["trace"]), D.read_trace(ot["trace"])))
    return {"ticks": same_rows(mt["ticks"], ot["ticks"]), "trace": trace}


def summary(m: dict, polls: int) -> dict:
    """One run as the receipt records it; `polls` is the check's budget."""
    rec = m["record"]
    d3, tex = rec.get("d3d9") or {}, (rec.get("d3d9") or {}).get("textures_from_files") or {}
    pump, heap, trap = rec.get("pump") or {}, rec.get("heap") or {}, rec.get("trap") or {}
    clause = next((c.get("value") for c in rec.get("clauses") or []
                   if c.get("name") == TEXTURE_CLAUSE), None)
    out = {"label": m["arm"].label, "break": m["arm"].brk, "polls": m["arm"].polls or polls,
           "run": rel(m["run_json"]), "run_sha256": sha_of(m["run_json"]),
           "binary_sha256": m["binary_sha256"],
           "files": {k: rel(p) if p.is_file() else None for k, p in m["files"].items()},
           "ticks_sha256": sha_of(m["files"]["ticks"]), "trace_sha256": sha_of(m["files"]["trace"]),
           "stop": (trap.get("note") or "")[:600], "trap_status": trap.get("status"),
           "pump": {k: pump.get(k) for k in ("iterations", "quit_by_host", "quit_by_guest",
                                             "injected")},
           "d3d9": {"objects_built": d3.get("objects_built"),
                    "guest_bytes_allocated": d3.get("guest_bytes_allocated"),
                    "lifetime": d3.get("lifetime")},
           "heap": {k: heap.get(k) for k in ("peak_bytes", "consistent", "failures")},
           "texture_clause": {TEXTURE_CLAUSE: clause,
                              **{k: tex.get(k) for k in ("answered", "with_decoded_pixels",
                                                         "rows_counted",
                                                         "unlocked_surfaces_holding_bytes",
                                                         "guest_copied_rows_left_out")}},
           "devices_configured": D.devices_configured(rec),
           "vs_native_trace": m["vs_native_trace"], "vs_native_ticks": m["vs_native_ticks"]}
    if "vs_model" in m:
        out["vs_model"] = m["vs_model"]
    return out


def limits(runs: dict[str, dict]) -> list[str]:
    return ["One run of each against this native capture: from boot through the attract demo, "
            f"and for fakepixels its first {FAKEPIXELS.polls} polls. A lifetime the demo does "
            "not exercise is not seen.",
            "neverfree is compared with the model up to its stop, and ownzero on every row it "
            "ran; each run's stop is recorded beside it.",
            "The tick capture against native is recorded for each run and judged by none of "
            "the verdicts; the stage trace against native is judged for the model only.",
            "fakepixels is judged on its texture rows alone: its textures hold bytes no other "
            "run's do, so neither its tick capture nor its stage trace is compared with the "
            "model's.",
            "Which recording devices each run had on is recorded (devices_configured); a "
            "device a build does not link is not reproduced from the device profile."]


def check(a, run: Callable[[Arm, argparse.Namespace, dict | str], dict] = run_arm) -> int:
    """Run the model and its breaks, refuse what cannot be compared, judge the
    rest, and with --write and every verdict holding, write the receipt. `run`
    is run_arm except in the tests."""
    native = a.native if a.native.is_absolute() else ROOT / a.native
    profile, problems = D.native_side(native)
    if problems:
        raise SystemExit(f"D3D9_LIFE_REFUSED {DP.rel(native, ROOT)} cannot stand for these "
                         f"runs: {'; '.join(problems)}")
    ntrace = D.read_trace(native / "sa2-stage-trace.csv")
    if not ntrace:
        raise SystemExit(f"D3D9_LIFE_REFUSED {DP.rel(native, ROOT)} has no stage trace")
    a.polls = a.polls or len(ntrace) + 300
    before, origin = inputs(native), provenance()
    runs: dict[str, dict] = {}
    for arm in ARMS:
        m = measure(arm, run(arm, a, profile), native, ntrace)
        rec = m["record"]
        if (rec.get("trap") or {}).get("status") == TRAP_TIMEOUT:
            raise SystemExit(f"D3D9_LIFE_REFUSED {arm.label}: stopped by the harness's "
                             f"wall-clock alarm, which says nothing about the model; rerun with "
                             f"a larger --timeout")
        why = ran_its_model(arm, rec, m["env"])
        try:
            DP.check_run(rec, profile)
        except DP.Refusal as e:
            why.append(str(e))
        if why:
            raise SystemExit(f"D3D9_LIFE_REFUSED {arm.label}: {'; '.join(why)}")
        runs[arm.label] = m
    for arm in BREAKS:
        runs[arm.label]["vs_model"] = against_model(runs["model"], runs[arm.label])
    if inputs(native) != before:
        raise SystemExit("D3D9_LIFE_REFUSED an input moved while the runs ran")
    binaries = [runs[arm.label]["binary_sha256"] for arm in ARMS]
    if None in binaries or len(set(binaries)) != 1:
        raise SystemExit(f"D3D9_LIFE_REFUSED the runs were not one binary: {binaries}")

    verdicts = judge(runs, a.polls)
    ok = not any(verdicts.values())
    print(f"D3D9_LIFE {'OK' if ok else 'FAILED'} against {DP.rel(native, ROOT)}, {a.polls} polls")
    for arm in ARMS:
        r = runs[arm.label]
        print(f"  {arm.label}: {r['vs_native_trace']['lifted_rows']} trace rows, stop: "
              f"{str((r['record'].get('trap') or {}).get('note'))[:160]}")
    for k, why in verdicts.items():
        print(f"  {k}: {'holds' if not why else 'FAILS: ' + '; '.join(why)}")
    if a.write and ok:
        RECEIPT.write_text(json.dumps({
            "schema": "sa2-recomp-receipt/v1", "id": "s4-d3d9-life-v1",
            "question": "Does the d3d9 device's Direct3D 9 lifetime model play the attract "
                        "demo from boot with nothing compared moving, do its three breaks "
                        "(neverfree, ownzero, keepall) show what runtime/sa2_d3d9.c says "
                        "they show, and is a file texture's byte count still read once its "
                        "store is freed?",
            "generated_by": "tools/s4/d3d9_life_check.py", "command": command(a, native),
            **origin, "inputs": before, "polls": a.polls, "native_rows": len(ntrace),
            "device_profile": DP.recorded(profile),
            # the directory every arm answered: DP.check_run held each to it
            "guest_paths": DP.guest_record(profile),
            "runs": [summary(runs[arm.label], a.polls) for arm in ARMS],
            "verdicts": {k: {"claim": CLAIMS[k], "holds": not why} for k, why in verdicts.items()},
            "answer": answer(verdicts), "limits": limits(runs)}, indent=1, sort_keys=True) + "\n")
        print(f"D3D9_LIFE wrote {rel(RECEIPT)}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--native", type=Path, default=D.DEFAULT_NATIVE)
    ap.add_argument("--polls", type=int, default=None,
                    help="the poll budget of every run but fakepixels; default the native "
                         "trace's length plus 300, as tools/s4/demo_run.py's")
    ap.add_argument("--timeout", type=int, default=3000,
                    help="the probe's wall-clock alarm around WinMain, in seconds, per run")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    return check(a)


if __name__ == "__main__":
    raise SystemExit(main())
