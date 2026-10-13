#!/usr/bin/env python3
"""Drive the guest with threads on: close the missing-block loop, prove the run
is deterministic, and break every clause of the verdict on purpose.

Subtitle: `loop_run.py` plus `lift_extra.py` in a fixpoint, then two identical
runs compared tick by tick, then one run per named break with an expectation
that is checked rather than printed.

Why a tool and not three command lines
--------------------------------------
Turning threads on moved the run from one guest thread to three, and each new
thread walks into bytes the analyzer's function map does not contain -- the
five unmapped CRI thread entry points G1 section 3 names. Closing that is a
loop: run, read the trap, lift the address, relink, run again. It was done by
hand three times before this file existed, which is past rule 13's threshold,
and the hand version has a specific way of going wrong: `link_s4.py` leaves the
previous binary in place when a compile fails, so a run after a failed relift
reports the *old* binary's trap and the loop looks converged.

The three modes
---------------
``--iterate N``   run, lift whatever address the run could not dispatch, relink,
                  repeat. Every iteration's JSON is kept, including the failing
                  ones, and the tool refuses to call the loop converged unless
                  the last iteration actually ran the binary it just built.

``--matrix``      the control, a second identical run, and one run per break.
                  Each break carries the clause it is supposed to move, and a
                  break that does not move its clause fails this tool. The
                  control is run first and every expectation is a difference
                  from it, because a comparison whose operands can both be
                  empty is not a comparison. A break whose clause only a
                  replay's own configuration exercises (REPLAY_BREAKS) is
                  judged against a second control on that configuration.

``--determinism`` two runs of the same binary under the same environment,
                  compared field by field and tick by tick. This is the
                  evidence for the risk register's "threads with real
                  concurrency -> non-deterministic replays", and the `free`
                  break in ``--matrix`` is what shows the comparison can fail.

The verdict names the clause that changed
-----------------------------------------
Slice 7's fourth finding was that a conjunction with many false clauses hides a
newly-false one as effectively as no check at all. `crt_probe` now emits every
clause by name; this tool diffs a run's clauses against the control's and
reports the ones that *changed*, so "false" and "false, and this is the clause
that stopped being true" are different outputs.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/thread_run.py --iterate 8
  .venv/bin/python tools/s4/thread_run.py --matrix
  .venv/bin/python tools/s4/thread_run.py --determinism
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
OUTDIR = C.GEN / "s4" / "threads"
RECEIPTS = C.GEN / "s4" / "receipts"
NAME = "loop.next+allarms"
BASE = "SA2_D3D9_DEVICE=1"

# A trap note that names an address the program could not dispatch to. Both
# shapes are the same defect (the map does not contain those bytes) and both are
# fixed by lifting the address as its own module.
MISS = re.compile(r"function table miss at 0x([0-9a-fA-F]{8})")
NOT_RUNNABLE = re.compile(r"no function registered for 0x([0-9a-fA-F]{8})")


# Appended to BASE for every row of every mode. Slice 9 needs it: with the
# Steam wall gone the guest no longer stops at 15 882 calls but polls forever,
# so a wall-clock timeout would be the bound -- and a wall-clock alarm stops two
# runs of the same binary at *different* points, which is exactly the bound a
# determinism measurement cannot use. `--extra-env SA2_IMPORT_BUDGET=400000`
# stops both runs at the same call.
EXTRA_ENV = ""


def run_rows(rows: list[tuple[str, str]], probe_timeout: int, build: bool,
             extra_probe_args: list[str] | None = None) -> dict[str, dict]:
    """One build, one run per row. Returns {label: run json}. A row whose JSON
    is missing or older than this invocation is reported as an error rather
    than as the previous invocation's result."""
    t0 = time.time()
    cmd = [PY, HERE / "loop_run.py", "--name", NAME, "--out-prefix", "thr",
           "--timeout", "1800",
           "--probe-arg=--timeout", f"--probe-arg={probe_timeout}"]
    for a in extra_probe_args or []:
        cmd.append(f"--probe-arg={a}")
    for label, env in rows:
        full = BASE + (f"|{EXTRA_ENV}" if EXTRA_ENV else "") + (f"|{env}" if env else "")
        cmd += ["--run", f"{label}|{full}"]
    if not build:
        cmd.append("--no-build")
    p = subprocess.run([str(x) for x in cmd], cwd=str(C.ROOT), capture_output=True,
                       text=True, timeout=7200)
    out: dict[str, dict] = {}
    for label, _ in rows:
        f = RUNDIR / f"thr.{label}.json"
        if not f.is_file() or f.stat().st_mtime < t0:
            out[label] = {"error": "no run JSON from this invocation",
                          "loop_run_rc": p.returncode,
                          "stdout_tail": p.stdout[-3000:]}
            continue
        try:
            out[label] = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            out[label] = {"error": f"unparseable run JSON: {e}"}
    return out


def undispatched(run: dict) -> list[str]:
    """Every address this run could not dispatch to, main thread and workers."""
    notes = [run.get("trap", {}).get("note", "")]
    notes += [t.get("trap_note", "") for t in run.get("threads", {}).get("rows", [])]
    found: list[str] = []
    for n in notes:
        for rx in (MISS, NOT_RUNNABLE):
            for m in rx.finditer(n or ""):
                a = "0x" + m.group(1).lower()
                if a not in found:
                    found.append(a)
    return found


def clauses(run: dict) -> dict[str, bool]:
    return {c["name"]: bool(c["value"]) for c in run.get("clauses", [])}


def clause_delta(control: dict, other: dict) -> dict[str, list[str]]:
    a, b = clauses(control), clauses(other)
    return {
        "became_false": sorted(k for k in a if a[k] and k in b and not b[k]),
        "became_true": sorted(k for k in a if not a[k] and k in b and b[k]),
        "missing": sorted(k for k in a if k not in b),
    }


def lift(entries: list[str], why: str) -> dict:
    cmd = [PY, HERE / "lift_extra.py", "--tag", "next+allarms", "--lift-tag", "next"]
    for e in entries:
        cmd += ["--entry", e, "--why", why]
    p = subprocess.run([str(x) for x in cmd], cwd=str(C.ROOT), capture_output=True,
                       text=True, timeout=3600)
    return {"entries": entries, "rc": p.returncode, "stderr_tail": p.stderr[-2000:]}


def iterate(n: int, probe_timeout: int, env: str = "") -> dict:
    """Run, lift what could not be dispatched, relink, repeat."""
    state: dict = {"mode": "iterate", "max_iterations": n, "env": env, "iterations": []}
    seen: set[str] = set()
    for i in range(n):
        runs = run_rows([("iter", env)], probe_timeout, build=True)
        run = runs["iter"]
        miss = [a for a in undispatched(run) if a not in seen]
        row = {
            "i": i,
            "calls": run.get("calls", {}).get("total"),
            "trap": run.get("trap", {}).get("note", "")[:200],
            "threads_created": run.get("threads", {}).get("created"),
            "undispatched": undispatched(run),
            "new_to_lift": miss,
            "false_clauses": run.get("false_clauses", []),
            "winmain_iterations": run.get("winmain_loop", {}).get("iterations"),
        }
        (OUTDIR / f"iter.{i}.json").write_text(json.dumps(run, indent=1))
        state["iterations"].append(row)
        (OUTDIR / "iterate.json").write_text(json.dumps(state, indent=1))
        print(f"[{i}] calls={row['calls']} threads={row['threads_created']} "
              f"winmain_iters={row['winmain_iterations']} new={miss} "
              f"trap={row['trap'][:110]}", flush=True)
        if not miss:
            state["converged"] = True
            state["why"] = ("the run dispatched every address it reached"
                            if not row["undispatched"]
                            else "every address it could not dispatch had already been lifted")
            break
        seen.update(miss)
        row["lift"] = lift(miss, "reached by a guest thread with G1's thread strategy on; "
                                 "bytes the analyzer function map does not contain")
        if row["lift"]["rc"]:
            state["converged"] = False
            state["why"] = f"lift_extra refused: {row['lift']['stderr_tail'][-300:]}"
            break
    state.setdefault("converged", False)
    state.setdefault("why", "the iteration budget ran out")
    (OUTDIR / "iterate.json").write_text(json.dumps(state, indent=1))
    return state


# label -> (env, the clause it must move, extra expectation on the run)
BREAKS: list[tuple[str, str, str, str]] = [
    ("refuse", "SA2_THREADS_BREAK=refuse", "guest_created_a_thread",
     "CreateThread refuses, as the single-threaded runtime did"),
    ("notimer", "SA2_THREADS_BREAK=notimer", "guest_armed_the_periodic_timer",
     "timeSetEvent refuses, so the guest tears the sound object down"),
    ("nopulse", "SA2_THREADS_BREAK=nopulse", "a_pulse_released_a_waiting_thread",
     "the timer ticks and its pulse releases nobody"),
    ("tinystack", "SA2_THREADS_BREAK=tinystack", "every_thread_stayed_in_its_stack_slot",
     "a worker gets 4 KiB of guest stack instead of 1 MiB"),
]
# Breaks whose expectation is "the run differs from the control", because the
# thing they break has no single clause of its own. Stated as such rather than
# attached to a clause it does not really move.
DIFF_BREAKS: list[tuple[str, str, str]] = [
    ("sharedtls", "SA2_THREADS_BREAK=sharedtls",
     "every thread reads thread 0's TLS slots, so the CRT's per-thread block is shared"),
]
BREAKS.append(("sharedteb", "SA2_THREADS_BREAK=sharedteb",
               "every_thread_saw_only_its_own_seh_chain",
               "every thread points FS at thread 0's TEB, so the SEH chains are shared"))
# Breaks whose clause only a replay's own configuration exercises, each judged
# against a control on that configuration, `control-boot`, never `control`.
#
# `noflsexit` was first added to BREAKS, and the matrix refused it: on the rows
# above the AsynDo threads (0x008653d0) that end never make a CRT per-thread
# block, so no ended thread could hold one, and the clause held with the break
# as without it (2026-09-26, the refcount fold). On the boot replay's
# configuration -- tools/s4/reach_receipt.py ENV on the boot capture's device
# profile, the run that found the defect -- they do. That configuration is the
# row's own environment, so it wins over --extra-env on every variable it sets.
REPLAY = "boot"
REPLAY_BREAKS: list[tuple[str, str, str, str]] = [
    ("noflsexit", "SA2_THREADS_BREAK=noflsexit", "every_ended_thread_released_its_crt_block",
     "a thread that ends runs no FLS callbacks, so a CRT block made lazily is never freed"),
]
# What each replay break's control must show beyond its clause (see
# released_by_callback); judged inside judge() against the same control.
EXERCISED: dict = {}


def released_by_callback(control: dict, broken: dict) -> dict:
    """noflsexit's clause reads each ended worker's CRT slot, and run_fls_exit
    clears the slot whether or not a callback ran, so the clause alone is true
    on a control whose callbacks never ran. The break is exercised only when
    the threads it leaves holding a CRT block (same index, same start routine)
    each ran an FLS callback on the control; the plain `control`, where no
    worker that ended ever made a block, fails it too."""
    c = {t.get("index"): t for t in control.get("threads", {}).get("rows", [])}
    held = [t for t in broken.get("threads", {}).get("rows", [])
            if t.get("state") == "finished" and int(t.get("crt_ptd") or "0", 16)]
    ran = [t["index"] for t in held
           if c.get(t["index"], {}).get("start_va") == t.get("start_va")
           and c.get(t["index"], {}).get("fls_exit_callbacks", 0) >= 1]
    return {"held_under_break": len(held), "released_by_a_callback_on_control": len(ran),
            "ok": len(ran) == len(held)}


EXERCISED["noflsexit"] = released_by_callback


def replay_env(name: str = REPLAY) -> str:
    """A registered replay's reach configuration as one row's environment:
    reach_receipt.py ENV plus profile_env on the replay's device profile, the
    environment its lifted run and the reach run both take."""
    import reach_receipt as RR  # noqa: E402  (tools/s4, beside this file)
    entry = next((e for e in json.loads(RR.REGISTRY.read_text())["replays"]
                  if e["name"] == name), None)
    if entry is None:
        raise SystemExit(f"{name} is not a registered replay")
    env = {**RR.ENV, **RR.profile_env(RR.DP.replay_profile(entry, C.ROOT))}
    return "|".join(f"{k}={v}" for k, v in env.items())


def ending(run: dict) -> tuple:
    return (run.get("calls", {}).get("total"),
            run.get("trap", {}).get("note", "")[:160],
            run.get("threads", {}).get("created"),
            run.get("winmain_loop", {}).get("iterations"))


VOLATILE = ()  # nothing in the probe's JSON is wall-clock derived; see the receipt


def diff_runs(a: dict, b: dict) -> dict:
    """Field-by-field difference between two run documents."""
    diffs: list[str] = []

    def walk(x, y, path):
        if type(x) is not type(y):
            diffs.append(f"{path}: {type(x).__name__} vs {type(y).__name__}")
            return
        if isinstance(x, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    diffs.append(f"{path}/{k}: present in only one run")
                else:
                    walk(x[k], y[k], f"{path}/{k}")
        elif isinstance(x, list):
            if len(x) != len(y):
                diffs.append(f"{path}: {len(x)} vs {len(y)} entries")
            for i, (p, q) in enumerate(zip(x, y)):
                walk(p, q, f"{path}[{i}]")
        elif x != y:
            diffs.append(f"{path}: {x!r} vs {y!r}")
        if len(diffs) > 200:
            raise StopIteration

    try:
        walk(a, b, "")
    except StopIteration:
        diffs.append("... (truncated at 200)")
    return {"identical": not diffs, "differences": diffs}


def determinism(probe_timeout: int, build: bool, env: str = "") -> dict:
    runs = run_rows([("det1", env), ("det2", env)], probe_timeout, build=build)
    a, b = runs["det1"], runs["det2"]
    d = diff_runs(a, b)
    ta = a.get("threads", {}).get("ticks", [])
    tb = b.get("threads", {}).get("ticks", [])
    d["ticks_a"] = len(ta)
    d["ticks_b"] = len(tb)
    d["ticks_identical"] = ta == tb
    # A tick comparison whose operands can both be empty is not a comparison.
    d["ticks_compared_something"] = len(ta) > 0 and len(ta) == len(tb)
    d["ending_a"] = ending(a)
    d["ending_b"] = ending(b)
    d["env"] = env
    d["extra_env"] = EXTRA_ENV
    d["verdict"] = bool(d["identical"] and d["ticks_identical"]
                        and d["ticks_compared_something"])
    return d


def summary(control: dict) -> dict:
    return {
        "calls": control.get("calls", {}).get("total"),
        "trap": control.get("trap", {}).get("note", "")[:200],
        "threads": {k: v for k, v in control.get("threads", {}).items()
                    if k not in ("rows", "ticks")},
        "false_clauses": control.get("false_clauses", []),
        "winmain_iterations": control.get("winmain_loop", {}).get("iterations"),
    }


def judge(control: dict, control_label: str, r: dict, label: str, env: str,
          clause: str, why: str, exercised=None) -> dict:
    """A break moved its clause: true on the control, false on the break, and
    (for a replay break) the control exercised what the break removes."""
    held_before = clauses(control).get(clause, False)
    now = clauses(r).get(clause, None)
    out = {
        "label": label, "env": env, "why": why, "clause": clause, "control": control_label,
        "control_value": held_before, "broken_value": now,
        "moved": held_before and now is False,
        "clause_delta": clause_delta(control, r),
        "ending": ending(r), "error": r.get("error"),
    }
    if exercised is not None:
        out["exercised"] = exercised(control, r)
        out["moved"] = bool(out["moved"] and out["exercised"]["ok"])
    return out


def matrix(probe_timeout: int) -> dict:
    renv = replay_env()
    rcontrol = f"control-{REPLAY}"
    rows = [("control", "")] + [(l, e) for l, e, _, _ in BREAKS] \
         + [(l, e) for l, e, _ in DIFF_BREAKS] + [("free", "SA2_THREADS_BREAK=free")] \
         + [(rcontrol, renv)] + [(l, f"{renv}|{e}") for l, e, _, _ in REPLAY_BREAKS]
    runs = run_rows(rows, probe_timeout, build=True)
    control = runs["control"]
    (OUTDIR / "control.json").write_text(json.dumps(control, indent=1))
    out: dict = {"mode": "matrix", "control": summary(control),
                 rcontrol: summary(runs[rcontrol]), "breaks": [], "ok": True}

    for label, env, clause, why in BREAKS:
        out["breaks"].append(judge(control, "control", runs[label], label, env, clause, why))
        out["ok"] = out["ok"] and out["breaks"][-1]["moved"]
    for label, env, clause, why in REPLAY_BREAKS:
        row = judge(runs[rcontrol], rcontrol, runs[label], label,
                    f"{rcontrol}'s environment|{env}", clause, why, EXERCISED[label])
        # The clause must not fire on live workers, which hold their blocks
        # legitimately: the plain control has three.
        row["holds_on_control"] = clauses(control).get(clause) is True
        row["moved"] = bool(row["moved"] and row["holds_on_control"])
        out["breaks"].append(row)
        out["ok"] = out["ok"] and row["moved"]

    for label, env, why in DIFF_BREAKS:
        r = runs[label]
        # The whole document, not only the ending.
        #
        # `ending()` was the test, and an import-call budget flattens it: both
        # runs stop at the same call with the same note, so `sharedtls` was
        # reported as "did not move" against a run it differs from in 88
        # fields, one of them a guest-visible GetProcAddress count of 67
        # against 64. A bound whose range no longer contains the difference has
        # stopped bounding, which is the same defect class as slice 8's. The
        # document comparison is safe to use precisely because `--determinism`
        # shows the document is a function of the input.
        doc = diff_runs(control, r)
        differs = not doc["identical"]
        out["breaks"].append({
            "label": label, "env": env, "why": why, "clause": None,
            "differs_from_control": differs, "moved": differs,
            "ending_alone_differs": ending(r) != ending(control),
            "document_differences": len(doc["differences"]),
            "first_differences": doc["differences"][:8],
            "clause_delta": clause_delta(control, r),
            "ending": ending(r), "control_ending": ending(control),
            "error": r.get("error"),
        })
        out["ok"] = out["ok"] and differs

    (OUTDIR / "matrix.json").write_text(json.dumps(out, indent=1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterate", type=int, default=0)
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--determinism", action="store_true")
    ap.add_argument("--free-determinism", action="store_true",
                    help="the same two-run comparison with SA2_THREADS_BREAK=free, "
                         "which is how the comparison is shown able to fail")
    ap.add_argument("--det-env", default="",
                    help="extra environment for the determinism runs, e.g. "
                         "SA2_STEAM_SURVEY=100000 to get a run long enough to compare")
    ap.add_argument("--tag", default="", help="suffix for this determinism run's output file")
    ap.add_argument("--extra-env", default="",
                    help="appended to every row's environment in every mode, e.g. "
                         "SA2_IMPORT_BUDGET=400000")
    ap.add_argument("--probe-timeout", type=int, default=240)
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()
    global EXTRA_ENV
    EXTRA_ENV = args.extra_env
    OUTDIR.mkdir(parents=True, exist_ok=True)
    RECEIPTS.mkdir(parents=True, exist_ok=True)

    if args.iterate:
        st = iterate(args.iterate, args.probe_timeout, args.det_env)
        print(json.dumps({k: v for k, v in st.items() if k != "iterations"}, indent=1))
    if args.determinism:
        d = determinism(args.probe_timeout, build=not args.no_build, env=args.det_env)
        (OUTDIR / f"determinism{args.tag}.json").write_text(json.dumps(d, indent=1))
        print("determinism:", json.dumps({k: v for k, v in d.items()
                                          if k != "differences"}, indent=1))
        print("first differences:", d["differences"][:10])
    if args.free_determinism:
        env = "SA2_THREADS_BREAK=free" + (f"|{args.det_env}" if args.det_env else "")
        d = determinism(args.probe_timeout, build=not args.no_build, env=env)
        (OUTDIR / f"determinism.free{args.tag}.json").write_text(json.dumps(d, indent=1))
        print("determinism under SA2_THREADS_BREAK=free:",
              json.dumps({k: v for k, v in d.items() if k != "differences"}, indent=1))
        print("first differences:", d["differences"][:10])
    if args.matrix:
        m = matrix(args.probe_timeout)
        for b in m["breaks"]:
            print(f"{b['label']:11} moved={b['moved']!s:5} clause={b['clause']} "
                  f"delta={b['clause_delta']['became_false']}")
        print("matrix ok:", m["ok"])


if __name__ == "__main__":
    main()
