#!/usr/bin/env python3
"""Break the Steam decision four ways and check that each break moves its own
clause of the verdict.

Subtitle: `thread_run.py --matrix` for `SA2_STEAM_BREAK`, with the control run
first and every expectation stated as a difference from it.

Why this is the whole point of the slice
----------------------------------------
Declining to implement Steam is easy to check: the guest walks past the wall or
it does not. *Not lying* is the half that keeps going unchecked, because the
guest's per-frame routine never asks a question whose answer could be a lie --
it calls `RequestCurrentStats` and ignores the return value. So a runtime that
fabricated every stat would produce a byte-identical run, and slice 8's
eleventh finding applies exactly: a claim whose falsification is invisible is
not yet a checked claim.

`harness/crt_probe.c` therefore asks the questions the guest does not, through
the same fabricated vtable, with a sentinel in the out-parameter. The breaks
here are what show each of those clauses can fail:

  refuse     ISteamUserStats answers nothing again      -> the wall comes back
  fabricate  the declining queries invent an answer     -> the sentinel dies
  storefail  StoreStats reports failure                 -> the no-op stops succeeding
  argcshort  every slot declares one dword less than it pops -> the frame drifts

A break that moves no clause fails this tool, and so does a break that moves a
clause which was already false in the control.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/steam_break.py --budget 400000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))

import common as C  # noqa: E402
import thread_run as T  # noqa: E402

OUT = C.GEN / "s4" / "steam"

# label, environment, the clause it must move, why
BREAKS = [
    ("refuse", "SA2_STEAM_BREAK=refuse", "guest_walked_past_the_steam_stats_wall",
     "ISteamUserStats refuses every slot again, so the guest stops inside its "
     "first frame at 0x0040e8c5"),
    ("fabricate", "SA2_STEAM_BREAK=fabricate",
     "steam_declined_without_writing_the_out_parameter",
     "a declined query invents an answer and writes over the probe's sentinel"),
    ("storefail", "SA2_STEAM_BREAK=storefail", "steam_noop_reported_success",
     "StoreStats reports failure, so the guest's dirty flag never clears"),
    ("argcshort", "SA2_STEAM_BREAK=argcshort",
     "steam_methods_popped_their_own_arguments",
     "every ISteamUserStats slot pops one dword less than it declares, which is "
     "the d3d9 defect that went unnoticed for six slices"),
]


def ending(run: dict) -> tuple:
    return (run.get("calls", {}).get("total"),
            run.get("trap", {}).get("note", "")[:160],
            run.get("winmain_loop", {}).get("iterations"),
            run.get("threads", {}).get("frames"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=400000,
                    help="SA2_IMPORT_BUDGET; a wall-clock bound cannot be used, "
                         "because it stops two runs of one binary at different calls")
    ap.add_argument("--probe-timeout", type=int, default=240)
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    T.EXTRA_ENV = f"SA2_IMPORT_BUDGET={args.budget}"

    rows = [("sbcontrol", "")] + [(l, e) for l, e, _, _ in BREAKS]
    runs = T.run_rows(rows, args.probe_timeout, build=not args.no_build)
    control = runs["sbcontrol"]
    cc = T.clauses(control)

    doc: dict = {
        "tool": "tools/s4/steam_break.py",
        "budget": args.budget,
        "control": {
            "calls": control.get("calls", {}).get("total"),
            "trap": control.get("trap", {}).get("note", "")[:200],
            "winmain_iterations": control.get("winmain_loop", {}).get("iterations"),
            "frames": control.get("threads", {}).get("frames"),
            "false_clauses": control.get("false_clauses", []),
            "steam": {k: v for k, v in control.get("interfaces", {}).items()
                      if k not in ("calls", "slots", "names", "declared")},
            "error": control.get("error"),
        },
        "breaks": [],
        "ok": True,
    }
    for label, env, clause, why in BREAKS:
        r = runs[label]
        delta = T.clause_delta(control, r)
        held = cc.get(clause, False)
        now = T.clauses(r).get(clause, None)
        moved = held and now is False
        doc["breaks"].append({
            "label": label, "env": env, "why": why, "clause": clause,
            "control_value": held, "broken_value": now, "moved": moved,
            "clause_delta": delta, "ending": ending(r),
            "control_ending": ending(control), "error": r.get("error"),
            "steam_probe": r.get("interfaces", {}).get("probe"),
        })
        doc["ok"] = doc["ok"] and moved

    (OUT / "breaks.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(f"control: calls={doc['control']['calls']} "
          f"winmain_iterations={doc['control']['winmain_iterations']} "
          f"frames={doc['control']['frames']} false={doc['control']['false_clauses']}")
    for b in doc["breaks"]:
        print(f"{b['label']:10} moved={str(b['moved']):5} clause={b['clause']} "
              f"became_false={b['clause_delta']['became_false']}")
    print("steam break matrix ok:", doc["ok"])
    if not doc["ok"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
