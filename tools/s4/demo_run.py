#!/usr/bin/env python3
"""Run the lifted build from boot through the title screen's attract demo.

Subtitle: the reach configuration with the tick hook and the stage trace,
started with no test spawn and no input, run for as many ticks as the native
capture holds, then the stage trace compared row by row with the native one
and the first divergence printed.

    source tools/env.sh
    .venv/bin/python tools/s4/demo_run.py [--native <capture directory>] [--polls N]

Why
---
Left idle, the title screen plays demos the game drives with input of its
own: City Escape, then Hidden Base 2P. Native runs of it agree on the stage
trace in every column compared across builds
(agents-journals/s4/2026-09-23-attract-demo.md), so it is a gameplay replay
that needs no recording and no one at the keyboard. This is its lifted side.

The run stands for the capture, so it reads the capture's device profile
(tools/s4/device_profile.py capture_profile) and runs with every device switch
the profile needs on (tools/s4/reach_receipt.py DEVICES, in the reach
configuration; profile_env, the one definition every run tool takes, adds the
profile's path), and the capture must be the vanilla
oracle's, both as tools/s4/stage_run.py requires of a stage run. A device the
runtime does not have linked is reported, not silently approximated: the run's
record says which devices were configured, and the result carries it.

The stage trace compares level, mode, state, in-game frames and position
exactly, and the player only as null or not: the player is a heap address,
which native runs share and the lifted build does not
(harness/replay/native_stage_trace.c). The tick capture's comparison is
tools/s4/stage_run.py's, printed beside it.

The run starts from the pinned saves, as every native one does (the user's
ruling of 2026-09-24): the installation must be the pin and the write
overlay's save directory is cleared first (tools/oracle/save_state.py
reset_lifted), and demo-run.json keeps what was cleared (`saves`).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "oracle"))

import device_profile as DP  # noqa: E402
import oracle_mode as OM  # noqa: E402
import reach_receipt as RR  # noqa: E402
import save_state as SS  # noqa: E402  the pinned saves every lifted run starts from
import stage_run as SR  # noqa: E402

ROOT = SR.ROOT
NAME = "attract-demo"
OUT = SR.OUT / NAME
# The default native side: the attract demo taken under the display addendum's
# option A (the vanilla probe with BeginScene kept alone, the fixed launch
# environment, the pinned saves), 2026-09-24. attract-demo-v1c, the default
# before it, is refused by native_side: an older vanilla probe, another KEEP
# table, and no start saves. v2a holds 3100 ticks, which end in the title
# before the City Escape demo starts (v1c's trace reaches it at tick 4469), so
# a run against it compares boot and the title only; a longer capture
# (--define tick_probe:TICK_LIMIT=12000 and stage_trace likewise, as v1c had)
# brings the two demos' gameplay back. attract-demo-v3a (the re-take batch of
# 2026-09-26, private/generated/oracle/batches/retake-batch-20260926-205224.json)
# is v2a's command with the word log, taken with the locale take's device-profile
# probe, whose profile the runtime now reads; native_side refuses v2a, taken with
# the probe before it, and accepts v3a. Its stage trace is v2a's byte for byte:
# 3100 ticks, boot and the title only.
DEFAULT_NATIVE = ROOT / "private/generated/oracle/captures/attract-demo-v3a"
COLUMNS = ("current_level", "game_mode", "game_state", "ingame_frames", "x", "y", "z")


def command(polls: int, profile: dict | str, timeout: int, *, name: str = NAME,
            label: str = "a", out: Path | None = None,
            extra_env: dict[str, str] | None = None) -> tuple[list[str], Path, Path, Path]:
    """The loop_run command, its run JSON, its tick capture and its stage trace.

    The defaults are this tool's own run. Another tool that runs the same demo
    under other settings names its own build (`name`, which is also the run
    record's prefix), its run (`label`) and the directory its tick capture and
    stage trace go to (`out`, under the repository), so its runs are kept apart
    from this one's and from each other; `extra_env` adds settings of its own,
    and may name neither a variable the device profile owns, nor a device switch
    (RR.DEVICES), nor one this command sets, since laid over one it would run a
    demo other than the one `polls`, `profile` and `out` describe, nor the host
    keyboard's sources and log (RR.HOST_INPUT_VARS)."""
    try:
        profile_env = RR.profile_env(profile)
    except DP.Refusal as e:
        raise SystemExit(f"DEMO_RUN_REFUSED {e}") from None
    # The host keyboard (RR.HOST_INPUT_VARS) is tools/s5/play_live.py's: the demo plays
    # with no input at all.
    owned = sorted(set(extra_env or {}) & (set(DP.OWNED) | set(profile_env) | set(RR.DEVICES)
                                           | set(RR.HOST_INPUT_VARS)))
    if owned:
        raise SystemExit(f"DEMO_RUN_REFUSED {', '.join(owned)} in extra_env: the device set is "
                         "the capture's checked profile and the reach configuration's devices, "
                         "with no host keyboard")
    for var in RR.DEVICE_VARS:
        if var in os.environ:
            raise SystemExit(f"DEMO_RUN_REFUSED {var} is set in this shell; unset it, since a "
                             "run's devices are the capture's it stands for")
    out = OUT if out is None else out
    ticks, trace = out / "lifted.csv", out / "stage-trace.csv"
    env = dict(RR.ENV, SA2_IMPORT_BUDGET=str(max(8_000_000, polls * 5000)),
               SA2_PUMP_POLLS=str(polls), SA2_VIRTUAL_BUDGET_MS=str(max(30000, polls * 20)),
               SA2_REPLAY_TICKS=str(ticks.relative_to(ROOT)),
               SA2_STAGE_TRACE=str(trace.relative_to(ROOT)))
    env.update(profile_env)
    clash = sorted(set(extra_env or {}) & set(env))
    if clash:
        raise SystemExit(f"DEMO_RUN_REFUSED {', '.join(clash)} in extra_env: this command sets "
                         "it itself")
    env.update(extra_env or {})
    out.mkdir(parents=True, exist_ok=True)
    spec = "|".join([label] + [f"{k}={v}" for k, v in env.items()])
    cmd = [SR.PY, str(HERE / "loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN, "--name", name,
           "--out-prefix", name, "--run", spec]
    for h in SR.HOOKS:
        cmd += ["--hook", h]
    for w in SR.WRAPS:
        cmd += ["--wrap", w]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", f"--probe-arg={timeout}", "--timeout", str(timeout + 120)]
    return cmd, SR.RUNDIR / f"{name}.{label}.json", ticks, trace


def native_side(native: Path) -> tuple[dict | None, list[str]]:
    """The checked device profile of the capture the run stands for, and every
    reason it cannot stand for one: not the vanilla oracle's as it stands in the
    tree (tools/oracle/oracle_mode.py vanilla_problems), not started from the
    pinned saves the lifted run starts from (tools/oracle/save_state.py
    native_start_problems), or no profile the runtime can reproduce
    (tools/s4/device_profile.py capture_profile)."""
    problems = list(OM.vanilla_problems(native, root=ROOT))
    problems += SS.native_start_problems(native, ROOT)
    try:
        profile = DP.capture_profile(native, ROOT)
    except DP.Refusal as e:
        return None, problems + [str(e)]
    return (profile if not problems else None), problems


def read_trace(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def compare_traces(native: list[dict], lifted: list[dict]) -> dict:
    """The first row where the two traces disagree, by tick, in the compared
    columns and in whether the player is null; how many native rows were
    compared, the differing one included; and how far the lifted trace got in
    the native one's level sequence.

    The comparison walks the native rows in order and stops at the first
    difference or at the first native tick the lifted trace lacks, so no
    divergence says nothing until `compared_rows` says how far it looked: a
    lifted trace a tick out of step, or one sharing no tick, compares none.
    A tick either trace holds twice is named too (`repeated_tick_native`,
    `repeated_tick_lifted`): the lifted row compared for it is the last, so
    an earlier one is never read."""
    by_tick = {r["tick"]: r for r in lifted}
    first, compared = None, 0
    for n in native:
        lft = by_tick.get(n["tick"])
        if lft is None:
            break
        compared += 1
        diff = [c for c in COLUMNS if n[c] != lft[c]]
        if (int(n["player"], 16) == 0) != (int(lft["player"], 16) == 0):
            diff.append("player (null or not)")
        if diff:
            first = {"tick": int(n["tick"]), "columns": diff,
                     "native": {c: n[c] for c in COLUMNS},
                     "lifted": {c: lft[c] for c in COLUMNS}}
            break

    def levels(rows):
        seq = []
        for r in rows:
            if int(r["game_state"], 16) == 0x10 and (not seq or seq[-1] != r["current_level"]):
                seq.append(r["current_level"])
        return seq

    def repeated(rows):
        seen = set()
        for r in rows:
            if r["tick"] in seen:
                return int(r["tick"])
            seen.add(r["tick"])
        return None

    return {"native_rows": len(native), "lifted_rows": len(lifted), "compared_rows": compared,
            "repeated_tick_native": repeated(native), "repeated_tick_lifted": repeated(lifted),
            "first_divergence": first,
            "levels_played_native": levels(native), "levels_played_lifted": levels(lifted)}


def devices_configured(record: dict) -> dict:
    """Whether each recording device was on in a run record; a block the record
    lacks is a device this build does not link."""
    return {k: (record[k].get("device_enabled") if isinstance(record.get(k), dict)
                else "not linked")
            for k in ("dinput", "xaudio2", "ddraw")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--native", type=Path, default=DEFAULT_NATIVE)
    ap.add_argument("--polls", type=int, default=None,
                    help="the poll budget; default the native trace's length plus a margin")
    ap.add_argument("--timeout", type=int, default=3000,
                    help="the probe's wall-clock alarm around WinMain, in seconds")
    args = ap.parse_args()
    native = args.native if args.native.is_absolute() else ROOT / args.native
    profile, problems = native_side(native)
    if problems:
        print(f"DEMO_RUN_REFUSED {DP.rel(native, ROOT)} cannot stand for this run:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    ntrace = read_trace(native / "sa2-stage-trace.csv")
    polls = args.polls or len(ntrace) + 300
    cmd, run_json, ticks, trace = command(polls, profile, args.timeout)
    try:
        saves = SS.reset_lifted(cmd, ROOT)
    except SS.SaveStateError as e:
        print(f"DEMO_RUN_REFUSED {e.code}")
        return 1
    for p in (ticks, trace, run_json):
        p.unlink(missing_ok=True)
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=args.timeout + 600, env=RR.clean_environ())
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        p = subprocess.CompletedProcess(cmd, -1, out, f"timed out: {e}")
    stop, read, devices, guest = "", None, {}, None
    if run_json.is_file():
        d = json.loads(run_json.read_text())
        stop = ((d.get("trap") or {}).get("note") or "")[:600]
        try:
            read = DP.check_run(d, profile)
        except DP.Refusal as e:
            read = f"refused: {e}"
        try:
            guest = DP.run_guest(d)
        except DP.Refusal as e:
            guest = f"refused: {e}"
        devices = devices_configured(d)
    result = {"native": DP.rel(native, ROOT), "run_json": str(run_json.relative_to(ROOT)),
              "polls": polls, "stop": stop, "saves": saves, "device_profile": DP.recorded(profile),
              "device_profile_read": read, "guest_paths": guest,
              "devices_configured": devices,
              "vs_native_trace": compare_traces(ntrace, read_trace(trace)),
              "vs_native_ticks": SR.compare(native / "sa2-tick-capture.csv", ticks)}
    (OUT / "demo-run.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    if not run_json.is_file():
        print(f"loop_run exited {p.returncode} and wrote no run record")
        print(p.stdout[-2000:], p.stderr[-2000:])
        return 1
    if read != DP.recorded(profile):
        # A run on another device set than the capture's is not this capture's
        # replay, whatever its trace reads (P6's review, N5), as
        # tools/s4/stage_run.py says.
        print(f"DEMO_RUN_REFUSED the run did not read the device profile it was handed: {read}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
