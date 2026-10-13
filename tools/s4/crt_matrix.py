#!/usr/bin/env python3
"""Run the CRT probe under several environments and keep every result.

Subtitle: the falsification column. One binary, one environment variable apart,
every run's JSON written to its own file -- including the runs that fail, which
are the ones that get deleted.

Why each row is here
--------------------
An assertion that cannot fail is not an assertion, and this project has caught
four of those. So every oracle the probe asserts on is moved by a variable and
the result is required to move with it:

  * `SA2_GUEST_CMDLINE` moves the command line the runtime publishes. WinMain
    compares its own parse of it against "-r" and stores the result at
    0x01933f6c, so the flag must be 1 for "-r" and 0 for anything else. A probe
    that had hard-coded either answer fails one of the two rows.
  * `SA2_VIRTUAL_EPOCH` moves the virtual clock, which is one of the five
    inputs to the security cookie. The cookie value must change and must still
    equal the probe's independent replay of the guest's arithmetic.
  * `SA2_MISSING_BLOCK_DISPATCH=0` puts the runtime back on the policy it
    shipped with, where every missing block traps. The run must stop at the
    entry point's own tail call, which is what shows the edge table is load
    bearing rather than decorative.
  * `SA2_IMPORT_BIND=0` is the previous slice's before column, kept.
  * `SA2_CPUID_EDX1` clears the SSE2 bit, which chooses a different body of
    lifted CRT code. Whatever happens is a measurement and not a pass/fail.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/crt_matrix.py --name crt.next+allarms
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

RUNDIR = C.GEN / "s4" / "run"
BIN = C.GEN / "s4" / "link" / "mains"

ROWS = [
    ("baseline", {}, "the run this slice reports"),
    ("cmdline_dash_r", {"SA2_GUEST_CMDLINE": "-r"},
     "WinMain's own comparison must flip to 1"),
    ("cmdline_other", {"SA2_GUEST_CMDLINE": "-nosound"},
     "and back to 0 for a tail that is not exactly -r"),
    ("epoch_moved", {"SA2_VIRTUAL_EPOCH": "0x1000000000000000"},
     "the security cookie must change and must still match the replay"),
    ("cwd_moved", {"SA2_GUEST_CWD": "D:\\elsewhere\\SA2X"},
     "the previous slice's path oracle, still moving"),
    ("mb_policy_trap", {"SA2_MISSING_BLOCK_DISPATCH": "0"},
     "every missing block traps: the run must stop at the entry point's tail call"),
    ("import_bind_off", {"SA2_IMPORT_BIND": "0"},
     "the previous slice's before column"),
    ("no_sse2", {"SA2_CPUID_EDX1": "0x03808111"},
     "CPUID stops reporting SSE2, so the CRT takes its other string routines"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="crt.next+allarms")
    ap.add_argument("--stop-import", default="PathFileExistsA")
    ap.add_argument("--out", default="crt.matrix.json")
    args = ap.parse_args()

    binary = BIN / args.name / "sa2_main"
    if not binary.is_file():
        raise SystemExit(f"no binary at {binary}; link it with tools/s4/link_s4.py first")
    RUNDIR.mkdir(parents=True, exist_ok=True)

    import os
    results = []
    for tag, env, why in ROWS:
        out = RUNDIR / f"{args.name}.{tag}.json"
        cmd = [str(binary), str(C.EXE), "--json", str(out),
               "--stop-import", args.stop_import]
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           env=dict(os.environ, **env))
        doc = json.loads(out.read_text()) if out.is_file() else None
        row = {"tag": tag, "env": env, "why": why, "rc": p.returncode,
               "seconds": round(time.time() - t0, 2), "json": str(out)}
        if doc:
            row.update(ok=doc["ok"], trap=doc["trap"]["note"][:200],
                       import_calls=doc["calls"]["total"],
                       cookie=doc["crt"]["cookie"]["after"],
                       cookie_matches=doc["crt"]["cookie"]["matches"],
                       reached_winmain=doc["winmain"]["reached"],
                       dash_r_flag=doc["winmain"]["dash_r_flag"],
                       dash_r_expected=doc["winmain"]["dash_r_expected"],
                       cmdline_parsed=doc["winmain"]["guest_parsed_the_command_line"],
                       heap_allocs=doc["heap"]["allocs"],
                       asserts=doc["assert"])
        results.append(row)
        print(f"{tag:18s} rc={p.returncode} ok={row.get('ok')} calls={row.get('import_calls')} "
              f"cookie={row.get('cookie')} dash_r={row.get('dash_r_flag')} "
              f"| {row.get('trap', '')[:90]}", flush=True)

    doc = {"schema": "sa2-recomp-measurement/v1", "id": "s4-crt-matrix",
           "question": "does every oracle the CRT probe asserts on move when the value "
                       "behind it moves?",
           "binary": str(binary), "binary_sha256": C.sha256(binary),
           "exe_sha256": C.sha256(C.EXE), "rows": results}
    path = RUNDIR / args.out
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
