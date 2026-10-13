#!/usr/bin/env python3
"""Does any guest code read or write past the four words of a fabricated Direct3D object?

Subtitle: the Metal Harbor stage run under three object layouts and a control
that must differ, and everything the runs produce compared: the tick capture,
the stage trace, the input the run applied, the counts, the stop and the whole
run record.

    source tools/env.sh
    .venv/bin/python tools/s4/d3d9_stride_check.py [--input CSV --polls N --timeout S] [--write]

Why
---
`runtime/sa2_d3d9.h` shrank the object stride from 64 to 16 on 2026-09-23, to
fit four times the objects in the same region, because an object's guest bytes
are four words (vtable, tag, type, index). Nothing in the runtime reads more.
Whether the *guest* reads or writes more cannot be proven by reading the
runtime: SA2 calls through the vtable, but a read past +16 used to return 0
and now returns the next object's vtable pointer, and a write there now
replaces that pointer, silently either way (the rule-5 review of the
gameplay-walls slice).

The runs
--------
  stride16           ""           16 bytes apart: the next object's vtable pointer at +16
  stride64-sentinel  wideobj      64 bytes apart, a sentinel over words 4..15
  stride64-zero      wideobjzero  64 bytes apart, words 4..15 zero: the layout until 2026-09-23
  stride64-vtable    wideobjvt    the control: the sentinel, and word 0 overwritten by it

The second column is `SA2_D3D9_BREAK`, set on every run -- the shipping
layout's to the empty string, so a break exported in the caller's environment
cannot leak into the run meant to have none -- and each run record's
d3d9.object_stride and d3d9.object_fill must be the ones its run asked for.

If no guest code reads past +16, the three layouts are the same run: they
agree on every item of COMPARED, the last being the whole run record with each
object address rewritten as the object and offset it names at that run's
stride. The control must not agree with stride16: its vtable pointers are
bytes the guest certainly reads, so if overwriting them does not show, the
comparison cannot see what it claims to, and the check fails.

The shipping layout's run must cover the stage -- all the polls asked for,
ended by the host's WM_QUIT with nothing injected, one tick-capture and one
stage-trace row per GameLoop return, and GameState reaching GameStates_Ingame
(16) in its stage trace at least once -- or the check refuses and writes
nothing: two runs that stop at the same early trap agree about nothing. This
and the control come from the rule-5 review of this tool's first version
(2026-09-23), which could pass without reaching the stage and wrote a receipt
naming a commit that could not have produced it.

A wide layout's run is held to the same leash and row counts, but once
stride16 has covered the stage, a wide run that falls short of them is not
refused: it FAILED, and it is the strongest result this check gives -- the
sentinel read as a pointer, say, and the run trapping on it. It is reported
with the reasons it fell short and its trap note, and the other runs still
run, for the diagnosis below. Only stride16's trace is tested for GameState
16: a wide run that never reaches the stage has a trace that differs from
stride16's, so it FAILED too rather than being refused. The one short wide
run that is refused is one the harness's own wall-clock alarm stopped
(SA2_TRAP_TIMEOUT, status 4): that stop comes from the machine's load, not
from the guest, and says nothing about the layout (the rule-5 review of the
second fold, 2026-09-23). It is rerun with a larger --timeout.

A difference is not yet a read past +16
----------------------------------------
It needs diagnosis before it is attributed to one. The two wide layouts share
their addresses and differ only in words 4..15, so if they disagree with each
other, something read those words -- and if only the zero layout differs,
likely a read that tests them for zero, which the sentinel and the next
object's vtable pointer fail alike. If the wide layouts agree with each other
and not with stride16, it may still be a read past +16 whose effect does not
tell zero from the sentinel, or it may be anything the guest derives from an
object's address -- a hash, a sort, an alignment test -- which follows the
stride without reading past +16, or the wide pool's capacity, a quarter of the
shipping one's. And a run that differs once is run again before it is believed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import stage_boot as SB  # noqa: E402
import stage_run as SR  # noqa: E402
import tick_replay as TR  # noqa: E402

RECEIPT = ROOT / "Documentation" / "receipts" / "s4-d3d9-stride-v1.json"
OUT = ROOT / "private" / "generated" / "s4" / "d3d9-stride"

# runtime/sa2_d3d9.h: the pool's first block, SA2_D3D9_OBJ_FIRST_COUNT at
# SA2_D3D9_OBJ_STRIDE from SA2_D3D9_OBJ_BASE, the same 256 KiB whatever the
# stride. The vtables start at SA2_D3D9_VT_BASE, where the region ends, so a
# vtable pointer is never read as an object. The overflow block is the 16-byte
# layout's alone, past WIDE_CEILING, so no run this tool compares reaches it. The test file holds these, and the arms' strides and fills, to the
# runtime's own definitions.
OBJ_BASE = 0x03200000
OBJ_SPAN = 16384 * 16


class Arm(NamedTuple):
    label: str
    brk: str       # SA2_D3D9_BREAK, set even when empty (arm_env)
    stride: int    # the d3d9.object_stride its run record must report
    fill: int      # and d3d9.object_fill: runtime/sa2_d3d9.h's SA2_D3D9_FILL_*


ARMS = (Arm("stride16", "", 16, 0),
        Arm("stride64-sentinel", "wideobj", 64, 1),
        Arm("stride64-zero", "wideobjzero", 64, 2))
CONTROL = Arm("stride64-vtable", "wideobjvt", 64, 3)
# The most objects a wide layout holds. The pool never reuses a slot and every
# level load takes new ones (runtime/sa2_d3d9.h), so a run that builds more at
# the shipping stride cannot be reproduced at the wide one and is refused.
WIDE_CEILING = OBJ_SPAN // 64

# What the layouts must agree on, in the order it is reported. The receipt's
# answer is generated from this, so it names only what was compared: the first
# version typed "no read past +16 changed the run" over seven items. The
# input-applied log is compared when there is an input to apply.
COMPARED = {
    "ticks_sha256": "the tick capture",
    "trace_sha256": "the stage trace",
    "input_applied_sha256": "the input-applied log",
    "stop": "the stop",
    "iterations": "the pump's iterations",
    "import_calls": "the import calls",
    "d3d9_method_calls": "the Direct3D method calls",
    "d3d9_answers": "the Direct3D answers by class",
    "d3d9_frames": "the Direct3D frame counts",
    "objects_built": "the objects built",
    "record_sha256": "the whole run record with object addresses read as objects",
}

# Keys the whole-record comparison leaves out: those that differ by design,
# not run to run, each checked against what its run asked for instead
# (layout()). Developed against the two records the first version wrote on
# 2026-09-23, copied to private/generated/s4/d3d9-stride/first-version/ as
# stage-metalharbor-input.stride16.json and stage-metalharbor-input.stride64.json
# because this tool's stride16 run rewrites the first where loop_run wrote it,
# under private/generated/s4/run/. They agree: once object addresses were
# rewritten, the stride was the only key that differed, so the record carries
# no wall-clock time or timestamp, or two runs minutes apart would differ in
# it. Of the paths a run's environment names, only the stage trace's
# (SA2_STAGE_TRACE, arm_env) carries the label: every run writes the tick
# capture and the input-applied log to one place, and run_arm copies them out.
# The run record echoes none of the three.
LAYOUT_KEYS = frozenset({("d3d9", "object_stride"), ("d3d9", "object_fill")})

# What the runs are built and run from, pinned in the receipt's inputs.pipeline
# (check_receipt_pins.py reads it): the device runtime, the harness, the tick
# hook's headers, the stage run's tools, and this one.
PIPELINE = ["runtime/sa2_d3d9.c", "runtime/sa2_d3d9.h", "runtime/sa2_d3d9_m7_list.h",
            "harness/crt_probe.c", *SR.HOOKS,
            "harness/replay/tick_probe_core.h", "harness/replay/tick_fields.h",
            "harness/replay/tick_digest.h", "tools/s4/d3d9_stride_check.py",
            "tools/s4/stage_run.py", "tools/s4/stage_boot.py", "tools/s4/reach_receipt.py",
            "tools/s4/tick_replay.py", "tools/s4/loop_run.py", "tools/s4/link_s4.py"]

# ------------------------------------------------------------- normalising

_HEX = re.compile(r"(?<![0-9A-Za-z_])0[xX]([0-9A-Fa-f]+)(?![0-9A-Za-z_])")


def _text(s: str, stride: int) -> str:
    """`s` with each 0x token in the object region read as "obj[i]+0xr" at `stride`."""
    def one(m: re.Match) -> str:
        v = int(m.group(1), 16)
        if not OBJ_BASE <= v < OBJ_BASE + OBJ_SPAN:
            return m.group(0)
        i, r = divmod(v - OBJ_BASE, stride)
        return f"obj[{i}]+{r:#x}"
    return _HEX.sub(one, s)


def normalise(value, stride: int, drop: frozenset = LAYOUT_KEYS, path: tuple = ()):
    """`value` with every object address rewritten as the object and offset it
    names at `stride`, and the keys in `drop` (paths of dict keys) left out.

    An address is a 0x token -- a whole string, or a token inside a string or a
    key -- inside [OBJ_BASE, OBJ_BASE + OBJ_SPAN). harness/crt_probe.c prints
    every address as a "0x%08x" string, and in the first version's two records
    (LAYOUT_KEYS) every value in the region is such a token and no integer is.
    An integer is a count and is kept as it is, because counts come near the
    region: heap.live_bytes was 47,760,576 in those 1300-poll runs, and
    dsound.sound_buffers[].now_us reaches the region near 2,940 polls. Read as
    an object, the same count names a different one at each stride, and two
    runs that agree would differ.

    The rewrite makes the same object read the same at either stride. The
    offset is kept to tell an interior pointer from the object it points into:
    obj[3]+0x10, word 4 of a wide object, or obj[3]+0x4, its tag, is not
    obj[3]+0x0, and with the offset dropped a record naming either would read
    the same as one naming the object. Everything else is kept exactly. A 0x
    token in the region that is not an address reads differently at the two
    strides, which fails the check rather than passing it, and the differing
    path is printed for the diagnosis.
    """
    if isinstance(value, dict):
        return {_text(k, stride): normalise(v, stride, drop, path + (k,))
                for k, v in value.items() if path + (k,) not in drop}
    if isinstance(value, list):
        return [normalise(v, stride, drop, path + ("[]",)) for v in value]
    if isinstance(value, str):
        return _text(value, stride)
    return value


def _short(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True)
    return s[:12] if re.fullmatch(r"[0-9a-f]{64}", s) else (s if len(s) <= 80 else s[:77] + "...")


def where_different(a, b, limit: int = 8) -> list[str]:
    """Up to `limit` places two normalised records differ, with both values,
    for the diagnosis a difference needs. A value and its type both count:
    1 and true are the same to Python and not to the record."""
    out: list[str] = []

    def walk(x, y, at: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in [*x, *(k for k in y if k not in x)]:
                here = f"{at}.{k}" if at else k
                if k in x and k in y:
                    walk(x[k], y[k], here)
                else:
                    out.append(f"{here}: only in the {'first' if k in x else 'second'}")
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                out.append(f"{at}: {len(x)} entries, not {len(y)}")
            for i, (u, w) in enumerate(zip(x, y)):
                walk(u, w, f"{at}[{i}]")
        elif type(x) is not type(y) or x != y:
            out.append(f"{at}: {_short(x)} != {_short(y)}")

    walk(a, b, "")
    return out[:limit]

# -------------------------------------------------------------- judging


def state(record: dict, norm: dict, files: dict[str, str | None]) -> dict:
    """One run's value of each COMPARED item. The counts come from the record as
    written, since a count is never an address; the stop and the record's
    digest from the normalised one, since a trap note can name an object."""
    d3 = record.get("d3d9") or {}
    return {"ticks_sha256": files.get("ticks"), "trace_sha256": files.get("trace"),
            "input_applied_sha256": files.get("input_applied"),
            "stop": (norm.get("trap") or {}).get("note"),
            "iterations": (record.get("pump") or {}).get("iterations"),
            "import_calls": (record.get("calls") or {}).get("total"),
            "d3d9_method_calls": d3.get("method_calls"), "d3d9_answers": d3.get("answers"),
            "d3d9_frames": d3.get("frames"), "objects_built": d3.get("objects_built"),
            "record_sha256": TR.digest(norm)}


def layout(record: dict, arm: Arm) -> list[str]:
    """Why a run did not run `arm`'s layout; empty when it did. A run that
    reports another -- a break leaked from the environment, or one the runtime
    does not know, which traps before the layout is set -- compares nothing."""
    d3 = record.get("d3d9") or {}
    return [f"d3d9.{k} is {d3.get(k)}, not {want}"
            for k, want in (("object_stride", arm.stride), ("object_fill", arm.fill))
            if d3.get(k) != want]


def coverage(record: dict, polls: int, rows: dict[str, int | None], with_input: bool) -> list[str]:
    """Why a layout's run does not cover the stage; empty when it does.

    Covered is ended at the harness's leash -- every poll asked for, the host's
    WM_QUIT, nothing injected, the guest not quitting on its own, each read
    strictly, so a record missing a field does not pass -- with a tick capture
    and a stage trace of one row per GameLoop return, which at the leash is
    polls - 1 because the last poll returns the WM_QUIT; and, with an input, an
    input-applied log. The review's case: two runs that trapped at the same
    first frame agree on everything and cover nothing.
    """
    pump = record.get("pump") or {}
    why = []
    if pump.get("iterations") != polls:
        why.append(f"{pump.get('iterations')} iterations, not the {polls} polls asked for")
    if pump.get("quit_by_host") != 1:
        why.append("the host did not post the quit")
    if pump.get("quit_by_guest") != 0:
        why.append("the guest quit on its own, or the record does not say")
    if pump.get("injected") != 0:
        why.append(f"injected {pump.get('injected')} message(s), not 0")
    for kind, name in (("ticks", "tick-capture"), ("trace", "stage-trace")):
        n = rows.get(kind)
        if not n:
            why.append(f"no {name} file" if n is None else f"no {name} rows")
        elif n != polls - 1:
            why.append(f"{n} {name} rows, not one per GameLoop return ({polls - 1})")
    if with_input and not rows.get("input_applied"):
        why.append("no input-applied log, with an input to apply")
    return why


def level_loads(trace: list[dict]) -> int:
    """How many times GameState reached GameStates_Ingame in a stage trace
    (stage_boot.read_trace's rows): the first load and each restart after."""
    n, before = 0, None
    for d in trace:
        now = int(d["game_state"], 0)
        if now == SB.GAME_STATE_INGAME != before:
            n += 1
        before = now
    return n


def disagreements(states: list[dict], keys: list[str]) -> dict[str, list]:
    """Each of `keys` on which the states do not all agree, with every state's
    value. Values are compared as JSON, so 1 and true differ."""
    canon = [{k: json.dumps(s.get(k), sort_keys=True) for k in keys} for s in states]
    return {k: [s.get(k) for s in states] for k in keys
            if any(c[k] != canon[0][k] for c in canon[1:])}


def judge(layouts: list[dict], control: dict, keys: list[str],
          uncovered: dict[str, list[str]] | None = None) -> dict:
    """The verdict: every layout covered the stage (`uncovered` names those
    that did not, with coverage()'s reasons), the layouts agree on every item,
    and the control does not agree with the first layout on all of them. A
    control that agrees means the comparison cannot see a change to bytes the
    guest certainly reads, so its agreement about the layouts shows nothing."""
    differences = disagreements(layouts, keys)
    control_on = list(disagreements([layouts[0], control], keys))
    return {"ok": not uncovered and not differences and bool(control_on),
            "differences": differences, "control_differs_on": control_on}


def _list(items) -> str:
    items = list(items)
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)


def answer(keys: list[str], labels: list[str], control: str, control_on: list[str]) -> str:
    """The receipt's answer, generated from what was compared rather than typed."""
    return (f"No read past +16 changed {_list(COMPARED[k] for k in keys)}: "
            f"{_list(labels)} agree on each. The control, {control}, differs on "
            f"{_list(COMPARED[k] for k in control_on)}.")


def limits(built: int, loads: int) -> list[str]:
    """The first version's two limits, then the two numbers that bound this run."""
    return ["Only the code this run reaches: the stage with this input, for these "
            "polls. A read past +16 on a path the run does not take is not seen.",
            "A read whose value does not reach compared state -- a digest word, a "
            "field, the trace, a count or the stop -- is not seen.",
            f"Only runs that fit the wide layouts, which hold {WIDE_CEILING} objects to the "
            f"shipping layout's {OBJ_SPAN // ARMS[0].stride}; this run built {built}. The pool "
            f"never reuses a slot, so a longer run is past what this check can compare.",
            f"Only the objects of the {loads} level load(s) this run made (GameState reaching "
            f"{SB.GAME_STATE_INGAME} in the stage trace). A read past +16 in objects a later "
            f"load builds is not seen."]

# ------------------------------------------------------------------ files


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rows_of(path: Path) -> int | None:
    """The data rows under a CSV's header; None when there is no file."""
    return max(0, len(path.read_text().splitlines()) - 1) if path.is_file() else None


def sha_of(path: Path) -> str | None:
    """The file's sha256, or None when there is no file, so the receipt tells a
    missing file from an empty one, which has a digest of its own. Two missing
    files still agree -- disagreements() reads both as null -- so it is not the
    digest that keeps a missing file from passing but coverage(), which asks
    every layout for its tick capture, its stage trace and, with an input, its
    input-applied log."""
    return TR.sha256(path) if path.is_file() else None


def first_line(a: Path, b: Path) -> int | None:
    """The first line two text files differ on, the header being line 1."""
    x = a.read_text().splitlines() if a.is_file() else []
    y = b.read_text().splitlines() if b.is_file() else []
    for n, (u, w) in enumerate(zip(x, y), 1):
        if u != w:
            return n
    return None if len(x) == len(y) else min(len(x), len(y)) + 1


def inputs(input_csv: Path | None, root: Path = ROOT) -> dict:
    """What the runs are built and run from. The recording is private, so it is
    pinned beside the pipeline and not in it: check_receipt_pins.py requires
    every pipeline path to be a file of this repository."""
    return {"runtime_sha256": TR.runtime_digest(root),
            "pipeline": {t: TR.must_sha256(root / t) for t in PIPELINE},
            "input_csv": ({"path": str(input_csv), "sha256": TR.must_sha256(input_csv)}
                          if input_csv else None)}


def provenance() -> dict:
    """The commit the runs start from and whether the tree differs from it,
    from the two git commands tools/s4/stage_boot.py uses; the pinned inputs
    say which of the files the runs depend on differed, and how. A git that
    fails is refused: it prints nothing, which read unchecked is commit "" and
    a clean tree."""
    def git(*args: str) -> str:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
        if p.returncode:
            raise SystemExit(f"D3D9_STRIDE_REFUSED git {' '.join(args)} exited {p.returncode}: "
                             f"{p.stderr.strip()[-300:]}")
        return p.stdout.strip()
    return {"commit": git("rev-parse", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain"))}


def command(a) -> str:
    """The command that reproduces this check, from its parsed arguments. The
    alarm is named even at its default, so the receipt says what its runs had
    whatever the default becomes."""
    return " ".join([".venv/bin/python tools/s4/d3d9_stride_check.py"]
                    + ([f"--input {a.input}"] if a.input else [])
                    + [f"--polls {a.polls}", f"--timeout {a.timeout}", "--write"])

# ------------------------------------------------------------------- runs


def arm_env(arm: Arm, trace: Path) -> dict[str, str]:
    """What one run adds to the stage run's environment. SA2_D3D9_BREAK is set
    even when it is empty: loop_run lays a run's variables over os.environ, so
    left unset, a break the caller exported would run in the shipping layout's
    place."""
    return {"SA2_STAGE_TRACE": rel(trace), "SA2_D3D9_BREAK": arm.brk}


def run_arm(arm: Arm, a) -> dict:
    """One stage run under `arm`'s layout: its run record, the files it wrote,
    each copied to a name of its own, and the sha256 of the binary it ran."""
    trace = OUT / f"{arm.label}.trace.csv"
    trace.unlink(missing_ok=True)
    cmd, run_json, ticks = SR.command("metalharbor", a.polls, label=arm.label,
                                      input_script=a.input, extra_env=arm_env(arm, trace),
                                      timeout=a.timeout)
    # stage_run.command's SA2_INPUT_APPLIED, and loop_run's summary beside the
    # run records, which holds the binary's digest: every run writes both to
    # the same place, so each is read or copied before the next run replaces it.
    applied = ticks.parent / "input-applied.csv"
    matrix = run_json.parent / f"{cmd[cmd.index('--out-prefix') + 1]}.matrix.json"
    for f in (run_json, ticks, applied, matrix):
        f.unlink(missing_ok=True)       # a file an earlier run left must not read as this one's
    # loop_run builds, then runs under its own bound, which stage_run.command
    # sets 120 s past the probe's alarm, or past 3600 s when the alarm is 0 and
    # there is none. This bound is the same base with 1800 s for the build.
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=(3600 if a.timeout == 0 else a.timeout) + 1800)
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label}: loop_run ran past {e.timeout} s")
    if not run_json.is_file():
        raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label}: no run record (loop_run exited "
                         f"{p.returncode})\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    files = {"trace": trace}
    for kind, src in (("ticks", ticks), ("input_applied", applied)):
        files[kind] = kept = OUT / f"{arm.label}.{kind.replace('_', '-')}.csv"
        kept.unlink(missing_ok=True)
        if src.is_file():
            kept.write_bytes(src.read_bytes())
    m = json.loads(matrix.read_text()) if matrix.is_file() else {}
    return {"record": json.loads(run_json.read_text()), "run_json": run_json, "files": files,
            "binary_sha256": m.get("sha256")}


def measure(arm: Arm, got: dict) -> dict:
    """One run as check() reads it: the rows and digests of the files it wrote,
    its record normalised at its own stride, and its COMPARED state."""
    norm = normalise(got["record"], arm.stride)
    shas = {k: sha_of(p) for k, p in got["files"].items()}
    return {**got, "arm": arm, "norm": norm,
            "rows": {k: rows_of(p) for k, p in got["files"].items()},
            "state": state(got["record"], norm, shas)}


def summary(m: dict) -> dict:
    """One run as the receipt records it."""
    d3 = m["record"].get("d3d9") or {}
    return {"label": m["arm"].label, "break": m["arm"].brk,
            "object_stride": d3.get("object_stride"), "object_fill": d3.get("object_fill"),
            "run": rel(m["run_json"]), "run_sha256": sha_of(m["run_json"]),
            "binary_sha256": m["binary_sha256"], "rows": m["rows"],
            "files": {k: rel(p) if p.is_file() else None for k, p in m["files"].items()},
            **m["state"]}


# runtime/sa2_trap.h: SA2_TRAP_TIMEOUT, the call exceeded its wall-clock budget.
TRAP_TIMEOUT = 4


def check(a, run: Callable[[Arm, argparse.Namespace], dict] = run_arm) -> int:
    """Run the layouts and the control, refuse what cannot be compared, judge
    the rest, and with --write and a pass, write the receipt. `run` is run_arm
    except in the tests."""
    keys = [k for k in COMPARED if a.input or k != "input_applied_sha256"]
    before, origin = inputs(a.input), provenance()
    layouts: list[dict] = []
    uncovered: dict[str, list[str]] = {}
    built = loads = 0
    for arm in ARMS:
        m = measure(arm, run(arm, a))
        why = layout(m["record"], arm)
        short = coverage(m["record"], a.polls, m["rows"], bool(a.input))
        # A run that did not run its layout compares nothing, whichever it is.
        # stride16's coverage is what the others are held to, so without it
        # the check refuses; a wide layout that stops where stride16 went on is
        # a result, and FAILED below.
        if why or (short and not layouts):
            raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label}: {'; '.join(why + short)}")
        if short and (m["record"].get("trap") or {}).get("status") == TRAP_TIMEOUT:
            raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label}: stopped by the harness's "
                             f"wall-clock alarm ({'; '.join(short)}), which says nothing about "
                             f"the layout; rerun with a larger --timeout")
        if short:
            uncovered[arm.label] = short
        if not layouts:                   # the shipping layout bounds what the others can do
            built = m["state"]["objects_built"]
            loads = level_loads(SB.read_trace(m["files"]["trace"]))
            if not isinstance(built, int) or built <= 0:
                raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label}: {built} objects built, so "
                                 f"no layout was used")
            if built > WIDE_CEILING:
                raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label} built {built} objects and the "
                                 f"wide layouts hold {WIDE_CEILING}: no run this long can be "
                                 f"made at both strides")
            if not loads:
                raise SystemExit(f"D3D9_STRIDE_REFUSED {arm.label}: GameState never reached "
                                 f"{SB.GAME_STATE_INGAME} in its stage trace, so no stage was "
                                 f"compared")
        layouts.append(m)
    control = measure(CONTROL, run(CONTROL, a))
    why = layout(control["record"], CONTROL)
    if why:
        raise SystemExit(f"D3D9_STRIDE_REFUSED {CONTROL.label}: {'; '.join(why)}; a control "
                         f"that did not run its layout shows nothing")
    if inputs(a.input) != before:
        raise SystemExit("D3D9_STRIDE_REFUSED an input moved while the runs ran")
    binaries = [m["binary_sha256"] for m in (*layouts, control)]
    if None in binaries or len(set(binaries)) != 1:
        raise SystemExit(f"D3D9_STRIDE_REFUSED the runs were not one binary: {binaries}")

    v = judge([m["state"] for m in layouts], control["state"], keys, uncovered)
    base, labels = layouts[0], [m["arm"].label for m in layouts]
    print(f"D3D9_STRIDE {'OK' if v['ok'] else 'FAILED'} {_list(labels)} "
          f"{'differ' if v['differences'] else 'agree'} over {len(keys)} compared items: "
          f"{base['rows']['ticks']} ticks, {built} objects (the wide layouts hold "
          f"{WIDE_CEILING}), {loads} level load(s); the control {CONTROL.label} differs on "
          f"{', '.join(v['control_differs_on']) or 'nothing'}")
    for m in layouts:
        if m["arm"].label in uncovered:
            note = (m["record"].get("trap") or {}).get("note") or "none recorded"
            print(f"  {m['arm'].label} did not cover the stage {base['arm'].label} covered "
                  f"({'; '.join(uncovered[m['arm'].label])}); its stop: {str(note)[:600]}")
    for k, values in v["differences"].items():
        print(f"  {k} ({COMPARED[k]}): "
              + "; ".join(f"{label}={_short(x)}" for label, x in zip(labels, values)))
    for m in layouts[1:]:
        for kind, f in m["files"].items():
            line = first_line(base["files"][kind], f)
            if line:
                print(f"  {m['arm'].label}: its {kind} file first differs from "
                      f"{base['arm'].label}'s at line {line}")
        if m["state"]["record_sha256"] != base["state"]["record_sha256"]:
            for w in where_different(base["norm"], m["norm"]):
                print(f"  {m['arm'].label} against {base['arm'].label}: {w}")
    if v["differences"] or uncovered:
        print("  a difference needs diagnosis before it is attributed to a read past +16; "
              "this tool's docstring says how the layouts tell the causes apart")
    if not v["control_differs_on"]:
        print(f"  the control agrees with {base['arm'].label}: this comparison cannot see a "
              f"change to vtable pointers the guest reads, so its agreement shows nothing")
    if a.write and v["ok"]:
        RECEIPT.write_text(json.dumps({
            "schema": "sa2-recomp-receipt/v1", "id": "s4-d3d9-stride-v1",
            "question": "Does any guest code read past the four words of a fabricated "
                        "Direct3D object, which the 16-byte object stride would answer with "
                        "the next object's vtable pointer?",
            "generated_by": "tools/s4/d3d9_stride_check.py", "command": command(a),
            **origin, "inputs": before, "polls": a.polls,
            "layouts": [summary(m) for m in layouts],
            "control": {**summary(control), "differs_on": v["control_differs_on"]},
            "compared": {k: COMPARED[k] for k in keys},
            "answer": answer(keys, labels, CONTROL.label, v["control_differs_on"]),
            "objects_built": built, "wide_object_ceiling": WIDE_CEILING, "level_loads": loads,
            "limits": limits(built, loads)}, indent=1, sort_keys=True) + "\n")
        print(f"D3D9_STRIDE wrote {rel(RECEIPT)}")
    return 0 if v["ok"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=None)
    ap.add_argument("--polls", type=int, default=1300)
    ap.add_argument("--timeout", type=int, default=600,
                    help="the probe's wall-clock alarm around the guest's WinMain call, in "
                         "seconds, 0 for none; the probe's own default, 120 s, surrounds runs "
                         "that take about 105 s on this shared machine")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    return check(a)


if __name__ == "__main__":
    raise SystemExit(main())
