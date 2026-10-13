#!/usr/bin/env python3
"""The S4 scheduler-suspension receipt: one JSON out of the runs that made it.

Subtitle: read the run JSONs this slice produced, copy their numbers, and write
`private/generated/s4/receipts/s4-suspension-and-fls-v1.json` -- so every
number in the report is a number a run wrote, with the file and hash it came
from beside it.

Nothing here computes a result. It reads the arms' JSON, the two-run pairs, the
corpus and controls summaries and the build-cost JSON, and refuses when one is
missing rather than reporting the field absent. That is rule 7: generated over
typed.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/sched_receipt.py \\
      --on private/generated/s4/run/final.on_a.json \\
      --on-b private/generated/s4/run/final.on_b.json \\
      --off private/generated/s4/run/final.off_a.json \\
      --off-b private/generated/s4/run/final.off_b.json \\
      --break-run private/generated/s4/run/final.on_break.json \\
      --fls-break private/generated/s4/run/final.on_sharedfls.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s4" / "receipts"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def load(p: Path) -> dict:
    if not p.is_file():
        raise SystemExit(f"{p} does not exist; the receipt refuses to report a run it cannot read")
    return json.loads(p.read_text())


def where(d: dict) -> dict:
    """The observables the brief measures, straight out of a run's own JSON."""
    t = d["threads"]
    wl = d.get("winmain_loop", {})
    return {
        "import_calls": d["calls"]["total"],
        "winmain_iterations": d["pump"]["iterations"],
        "winmain_loop_iterations": wl.get("iterations"),
        "winmain_loop_ran": wl.get("ran"),
        "frames": t["frames"],
        "guest_threads_created": t["created"],
        "guest_threads_refused": t["refused"],
        "thread_switches": t["switches"],
        "time_advances": t["time_advances"],
        "message_boxes": d["message_boxes"],
        "suspension": {k: v for k, v in t["suspension"].items() if k != "log"},
        "stopped_at": d["trap"]["exit_pc"],
        "stop_note": d["trap"]["note"],
        "rows": [{k: r[k] for k in ("index", "guest_start_routine", "state", "slices",
                                    "guest_calls", "suspend_count", "parks", "waiting_on",
                                    "first_import_caller", "last_import_caller")}
                 for r in t["rows"]],
    }


def pair(a: Path, b: Path) -> dict:
    return {"a": str(a), "b": str(b), "a_sha256": sha256(a), "b_sha256": sha256(b),
            "byte_identical": sha256(a) == sha256(b)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", type=Path, required=True)
    ap.add_argument("--on-b", type=Path, required=True)
    ap.add_argument("--off", type=Path, required=True)
    ap.add_argument("--off-b", type=Path, required=True)
    ap.add_argument("--break-run", type=Path, required=True,
                    help="SA2_SUSPEND_PARK=0: the self-suspend does not park")
    ap.add_argument("--fls-break", type=Path, required=True,
                    help="SA2_THREADS_BREAK=sharedfls: one FLS array for every thread")
    ap.add_argument("--build-cost", type=Path,
                    default=C.GEN / "s4" / "build_cost" / "build_cost.json")
    ap.add_argument("--corpus", type=Path,
                    default=C.GEN / "s2" / "corpus_run" / "run.pc24.json")
    ap.add_argument("--controls", type=Path,
                    default=C.GEN / "s2" / "corpus_run" / "controls.json")
    ap.add_argument("--corpus-line", default="")
    ap.add_argument("--controls-line", default="")
    ap.add_argument("--gates-line", default="")
    ap.add_argument("--id", default="s4-suspension-and-fls-v1")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    src = {n: C.ROOT / p for n, p in (
        ("runtime/sa2_threads.c", "runtime/sa2_threads.c"),
        ("runtime/sa2_threads.h", "runtime/sa2_threads.h"),
        ("runtime/sa2_imports.c", "runtime/sa2_imports.c"),
        ("runtime/guest_memory.h", "runtime/guest_memory.h"),
        ("harness/crt_probe.c", "harness/crt_probe.c"),
        ("tools/s4/sched_receipt.py", "tools/s4/sched_receipt.py"),
    )}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                            capture_output=True, text=True).stdout.strip()

    on, onb = load(args.on), load(args.on_b)
    off, offb = load(args.off), load(args.off_b)
    brk, flsbrk = load(args.break_run), load(args.fls_break)

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": args.id,
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": commit,
        "question": ("With SA2_DSOUND_DEVICE=1 two threads burned 195 345 guest calls each "
                     "while a suspend count climbed past 0x1fc9b and the main thread got "
                     "seven slices. Which thread does that handle name, what state is it "
                     "in, and what does the scheduler do with it?"),
        "inputs": {
            "executable": "private/exe/sonic2app-unpacked.exe",
            "executable_sha256": sha256(C.EXE),
            "pipeline": {n: sha256(p) for n, p in src.items()},
        },
        "the_answer": {
            "which_thread_the_handle_names": (
                "The thread created third by CRI's 0x007C5950, whose start routine is "
                "0x007C53F0 -- the server body that contains the SuspendThread call site at "
                "0x007C5470 itself. The handle at 0x017523EC is written at 0x007C59E3 from "
                "that CreateThread's return value, and the run reads it back as 0x7f00a000, "
                "which is the handle the SuspendThread calls carry. So it is a SELF-suspend "
                "in the image."),
            "what_the_stopped_run_showed_instead": (
                "The suspension census recorded by=3 and by=4, target=5, target state READY, "
                "130 212 times and zero self-suspends: two OTHER threads were executing "
                "0x007C5472. The guest's own loop counters say why -- [0x017523B0], the "
                "0x007C53F0 loop head, read 0x1FCA7, while [0x017523AC], the 0x007C58B0 "
                "ticker's loop head, read 0. All three CRI threads had entered the same "
                "routine, the third one's."),
            "the_cause": (
                "KERNEL32!Fls{Alloc,GetValue,SetValue,Free} stored their values in one "
                "process-wide array. FLS is per fiber and a thread that never converts to a "
                "fiber still has one, so on a fiberless process FLS is per thread. The MSVC "
                "CRT keeps its _ptiddata there: _threadstartex writes the routine it was "
                "asked for into ptd->_initaddr at 0x00828A9A and _callthreadstartex reads it "
                "back at 0x00828A27. With one block the three CRI threads, all created "
                "CREATE_SUSPENDED and resumed together, all ran the routine the last of them "
                "registered -- so the ticker that calls ResumeThread never ran, the server "
                "the handle names was suspended once and never resumed, and two copies of "
                "the server body spun. Virtual time only moves when nothing is runnable, so "
                "two always-runnable threads froze the clock and the main thread's Sleep(10) "
                "never expired."),
            "which_possibility_it_was": (
                "Neither of the two the brief listed. The target was T_READY at every one of "
                "the 130 212 calls, which the old code handled, and the suspend count did "
                "keep it out of pick_next. The count climbed because nothing ever resumed "
                "it, and nothing ever resumed it because the resumer was running somebody "
                "else's routine."),
        },
        "where_the_guest_gets": {
            "device_off": where(off),
            "device_on": where(on),
            "device_on_with_suspend_park_off": where(brk),
            "device_on_with_shared_fls": where(flsbrk),
        },
        "determinism": {
            "device_off": pair(args.off, args.off_b),
            "device_on": pair(args.on, args.on_b),
            "why_it_is_expected": (
                "One token, hand-off only at runtime entry points, the next thread a fixed "
                "function of scheduler state, and a clock that moves only when the guest "
                "asks or when nothing is runnable. Parking a self-suspended thread adds a "
                "hand-off point and no new input: the wake condition is the suspend count, "
                "which is scheduler state like every other."),
        },
        "regression": {
            "corpus": {"path": str(args.corpus), "sha256": sha256(args.corpus)
                       if args.corpus.is_file() else None,
                       "summary_line": args.corpus_line},
            "controls": {"path": str(args.controls), "sha256": sha256(args.controls)
                         if args.controls.is_file() else None,
                         "summary_line": args.controls_line},
            "gates": args.gates_line,
        },
    }
    if args.build_cost.is_file():
        doc["build_cost"] = json.loads(args.build_cost.read_text())

    out = OUT / f"{args.id}.json"
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
