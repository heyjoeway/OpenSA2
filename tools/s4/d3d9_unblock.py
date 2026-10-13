#!/usr/bin/env python3
"""Run the guest with the recording d3d9 device, lift whatever block it lands on, repeat.

Subtitle: `loop_run.py` and `lift_extra.py` in a bounded fixpoint, with every
round's trap, address and outcome written to one JSON file as it goes.

Why a tool
----------
Unblocking the guest past `CreateDevice` is the same three steps over and over:
run, read the trap for an address the whole-image lift produced no module for,
lift that address as its own module, rebuild. It had been done by hand four
times before this was written, which is past rule 13's threshold, and doing it
by hand loses the interesting part -- *which* addresses were needed and why is
the measurement, not a means to it.

The fixpoint is bounded in rounds and stops on any trap that is not a missing
block, because a trap that names an unimplemented method is a work item for the
device and not something to lift. It never lifts the same address twice: a
repeat means the lift did not take, and continuing would loop forever writing
the same row.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/d3d9_unblock.py --rounds 25
  .venv/bin/python tools/s4/d3d9_unblock.py --rounds 1 --no-lift   # just run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent
PY = C.ROOT / ".venv" / "bin" / "python"
RUNDIR = C.GEN / "s4" / "run"
OUTDIR = C.GEN / "s4" / "d3d9"

# The two trap notes that name an address the lift produced no module for. Any
# other note is a work item somewhere else and stops the fixpoint.
MISS = re.compile(r"function table miss at (0x[0-9a-f]{8})")
NOBLOCK = re.compile(r"no lifted block at (0x[0-9a-f]{8})")


def sh(cmd: list[str], timeout: int = 3000) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], cwd=str(C.ROOT),
                          capture_output=True, text=True, timeout=timeout)


def run_once(env_spec: str, label: str, probe_timeout: int, build: bool) -> dict:
    cmd = [PY, HERE / "loop_run.py", "--name", "loop.next+allarms",
           "--out-prefix", "d3d9unblock", "--run", f"{label}|{env_spec}",
           f"--probe-arg=--timeout", f"--probe-arg={probe_timeout}"]
    if not build:
        cmd.append("--no-build")
    out = RUNDIR / f"d3d9unblock.{label}.json"
    # Deleted *here* and not only inside loop_run. loop_run deletes it just
    # before each run, so a build that fails exits before the delete and leaves
    # the previous round's file in place -- and this tool read it as this
    # round's result for two rounds, reporting an unchanged trap as "the lift
    # did not take" when the truth was a duplicate-symbol link error nobody
    # looked at. Existence is the proof only if nothing older can be there.
    if out.is_file():
        out.unlink()
    p = sh(cmd)
    if p.returncode:
        return {"error": f"loop_run exited {p.returncode}: the build or the run refused",
                "stderr_tail": p.stderr[-1500:], "stdout_tail": p.stdout[-3000:]}
    if not out.is_file():
        return {"error": "the probe wrote no JSON", "stderr_tail": p.stderr[-1500:],
                "stdout_tail": p.stdout[-1500:]}
    return json.loads(out.read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=25)
    ap.add_argument("--env", default="SA2_D3D9_DEVICE=1")
    ap.add_argument("--label", default="dev")
    ap.add_argument("--probe-timeout", type=int, default=120)
    ap.add_argument("--no-lift", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (OUTDIR / f"unblock.{args.label}.json")
    state = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "env": args.env,
             "rounds": [], "lifted": [], "stopped_because": None}
    seen: set[str] = set()

    def save() -> None:
        state["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out_path.write_text(json.dumps(state, indent=1) + "\n")

    save()
    build = True
    for rnd in range(1, args.rounds + 1):
        t0 = time.time()
        d = run_once(args.env, args.label, args.probe_timeout, build)
        build = False
        row = {"round": rnd, "seconds": round(time.time() - t0, 1)}
        if "error" in d:
            row.update(d)
            state["rounds"].append(row)
            state["stopped_because"] = "the probe wrote no JSON"
            save()
            break
        trap = d.get("trap", {})
        note = trap.get("note", "")
        row.update(ok=d.get("ok"), calls=d["calls"]["total"], exit_pc=trap.get("exit_pc"),
                   note=note[:300],
                   d3d9_method_calls=d.get("d3d9", {}).get("method_calls"),
                   d3d9_distinct=d.get("d3d9", {}).get("distinct_methods"),
                   winmain_iterations=d.get("winmain_loop", {}).get("iterations"),
                   winmain_ran=d.get("winmain_loop", {}).get("ran"))
        m = MISS.search(note) or NOBLOCK.search(note)
        if not m:
            row["classified"] = "not a missing block: this is a work item elsewhere"
            state["rounds"].append(row)
            state["stopped_because"] = note[:300] or "the run ended without a missing block"
            save()
            break
        va = m.group(1)
        row["missing_at"] = va
        if args.no_lift:
            state["rounds"].append(row)
            state["stopped_because"] = "--no-lift"
            save()
            break
        if va in seen:
            row["classified"] = "the same address twice: the lift did not take"
            state["rounds"].append(row)
            state["stopped_because"] = f"{va} was lifted and is still missing"
            save()
            break
        seen.add(va)
        why = ("reached by the guest's renderer init once the recording d3d9 device "
               "answered CreateDevice; the whole-image lift produced no module for it")
        p = sh([PY, HERE / "lift_extra.py", "--tag", "next+allarms", "--lift-tag", "next",
                "--entry", va, "--why", why], timeout=3000)
        row["lift_rc"] = p.returncode
        row["lift_tail"] = (p.stdout or "").strip().splitlines()[-1:][:1]
        state["lifted"].append({"va": va, "round": rnd, "rc": p.returncode})
        state["rounds"].append(row)
        save()
        if p.returncode:
            state["stopped_because"] = f"lift_extra refused {va}"
            save()
            break
        build = True
    else:
        state["stopped_because"] = f"the round budget of {args.rounds} was spent"
        save()

    print(json.dumps({"rounds": len(state["rounds"]),
                      "lifted": [x["va"] for x in state["lifted"]],
                      "stopped_because": state["stopped_because"],
                      "last": state["rounds"][-1] if state["rounds"] else None}, indent=1))
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
