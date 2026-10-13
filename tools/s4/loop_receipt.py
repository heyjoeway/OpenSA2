#!/usr/bin/env python3
"""Assemble this slice's receipt from the files the probes wrote.

Subtitle: no number in it is typed. Every figure is read out of a generated
JSON under `private/generated/s4/`, and every input file is hashed as it is at
the moment the receipt is written, because a receipt is a historical record
(`check_receipt_pins.py` compares it against the commit that wrote it, not
against HEAD).

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/loop_receipt.py \\
      --matrix loop2 --controls-matrix loop2ctl --survey d3d9survey
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

RUNDIR = C.GEN / "s4" / "run"
OUT = C.GEN / "s4" / "receipts"

PIPELINE = [
    "runtime/sa2_window.c", "runtime/sa2_window.h",
    "runtime/sa2_iface.c", "runtime/sa2_iface.h",
    "runtime/sa2_imports.c", "runtime/sa2_imports.h",
    "runtime/sa2_files.c", "runtime/sa2_files.h",
    "harness/crt_probe.c",
    "tools/s4/link_s4.py", "tools/s4/loop_run.py", "tools/s4/loop_break.py",
    "tools/s4/build_cost.py", "tools/s4/lift_extra.py", "tools/s4/mb_edges.py",
    "tools/s4/install_guard.py", "tools/s4/vfs_overlay.py", "tools/s4/loop_receipt.py",
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path):
    return json.loads(p.read_text()) if p.is_file() else None


# Both of these read the *field the owning tool writes*, and both refuse when
# the field is not there. The first drafts guessed at the key names -- `pass`,
# `ok`, `holds` -- and, because `dict.get` answers `None` for a key that does
# not exist, reported 0/52 for a corpus that passed 52 and 98/98 for controls
# whose own tool prints 374/374. A summary that counts a key nobody writes is
# not a summary; it is a comparison whose operands can both be empty, which is
# the failure this project keeps finding. Refusing is what makes it visible.
def corpus_summary(doc) -> str:
    if not doc:
        return "not run"
    rows = doc.get("results", [])
    if not rows or "rc" not in rows[0]:
        raise SystemExit("run.pc24.json rows have no 'rc'; this summary would count nothing")
    return f"{sum(1 for r in rows if r['rc'] == 0)}/{len(rows)}"


def controls_summary(doc) -> str:
    """The same arithmetic `tools/s2/corpus_run/controls.py` prints, not a new
    one: every row whose `kind` is not "probe" is asserting, and a break control
    is circular when it moves no column the lift decides. The first draft here
    invented its own classification -- break rows excluded from the denominator
    -- and reported 98/98 where the owning tool reports 374/374. A receipt whose
    denominator differs from the tool's is a second measurement claiming to be
    the first."""
    if not doc:
        return "not run"
    total = held = circular = 0
    saw_field = False
    for rows in doc.values():
        for r in rows:
            if "control_holds" not in r or "kind" not in r:
                continue
            saw_field = True
            if r["kind"] == "probe":
                continue
            total += 1
            held += 1 if r["control_holds"] else 0
            if r["kind"] == "break" and not r.get("moves_a_column_the_lift_decides"):
                circular += 1
    if not saw_field:
        raise SystemExit("controls.json rows have no 'control_holds'; this summary counts nothing")
    return (f"{held}/{total} asserting controls hold over {len(doc)} fixtures; "
            f"{circular} break controls move nothing the lift decides")


def row_summary(doc: dict) -> dict:
    p = doc.get("pump", {})
    w = doc.get("window", {})
    return {
        "ok": doc.get("ok"),
        "import_calls": doc.get("calls", {}).get("total"),
        "distinct_imports": doc.get("calls", {}).get("distinct_imports"),
        "stopped_at": doc.get("trap", {}).get("note"),
        "iterations": p.get("iterations"),
        "empty_polls": p.get("empty_polls"),
        "message_polls": p.get("message_polls"),
        "gets": p.get("gets"),
        "translates": p.get("translates"),
        "dispatches": p.get("dispatches"),
        "guest_wndproc_calls": p.get("guest_wndproc_calls"),
        "defwndproc_calls": p.get("defwndproc_calls"),
        "quit_by_guest": p.get("quit_by_guest"),
        "quit_by_host": p.get("quit_by_host"),
        "quit_delivered": p.get("quit_delivered"),
        "postquit_caller": p.get("postquit_caller"),
        "blocking_get_refused": p.get("blocking_get_refused"),
        "loop_exit_status": p.get("status"),
        "loop_exit_note": p.get("note"),
        "window": w.get("window"),
        "window_expected": w.get("expected"),
        "asserts_failed": sorted(k for k, v in doc.get("assert", {}).items() if not v),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default="loop2")
    ap.add_argument("--controls-matrix", default="loop2ctl")
    ap.add_argument("--survey", default="d3d9survey")
    ap.add_argument("--break-name", default="loop.break")
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--gates-log", default="private/generated/s4/run/gates3.log")
    ap.add_argument("--id", default="s4-window-and-pump-v1")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                          capture_output=True, text=True).stdout.strip()
    link_state = load(C.GEN / "s4" / "link" / f"main.{args.matrix and 'loop.next+allarms'}.json")
    build = load(C.GEN / "s4" / "build_cost" / f"build_cost.{args.tag}.json")
    guard = load(C.GEN / "s4" / "guard" / "install.compare.before-after-loop.json")
    brk = load(RUNDIR / f"{args.break_name}.verdict.json")
    extra = load(C.GEN / "s4" / "link" / f"extra_functions.{args.tag}.json")
    corpus = load(C.GEN / "s2" / "corpus_run" / "run.pc24.json")
    controls = load(C.GEN / "s2" / "corpus_run" / "controls.json")
    gates_line = ""
    gl = C.ROOT / args.gates_log
    if gl.is_file():
        for line in gl.read_text().splitlines():
            if line.startswith("ALL_GATES_OK") or line.startswith("GATES"):
                gates_line = line.strip()

    rows = {}
    for prefix in (args.matrix, args.controls_matrix, args.survey):
        m = load(RUNDIR / f"{prefix}.matrix.json")
        if not m:
            continue
        for r in m["rows"]:
            doc = load(C.ROOT / r["json"])
            if doc:
                rows[r["label"]] = {"env": r["env"], "json": r["json"], **row_summary(doc)}

    survey_doc = None
    sm = load(RUNDIR / f"{args.survey}.matrix.json")
    if sm and sm["rows"]:
        survey_doc = load(C.ROOT / sm["rows"][0]["json"])

    d3d9 = {}
    if survey_doc:
        i = survey_doc["interfaces"]
        d3d9 = {
            "how": "SA2_D3D9_SURVEY=400 SA2_PRESENT_SURVEY=400: Direct3DCreate9 returns a "
                   "fabricated IDirect3D9 whose methods record their arguments, return 0 and "
                   "write no out-parameter, and unimplemented presentation imports do the "
                   "same. Every row below is marked with how it was answered. This is a "
                   "survey of what the game asks for, not a run of the game.",
            "default_behaviour": "Direct3DCreate9 refuses by name; see the 'refuse' row.",
            "interface_methods": [c for c in i["calls"] if c["iface"] == "IDirect3D9"],
            "presentation_imports": survey_doc["presentation_survey_calls"],
            "surveyed_method_calls": i["surveyed"],
            "surveyed_import_calls": i["presentation_surveyed"],
            "ended_at": survey_doc["trap"]["note"],
            "static_surface": {
                "d3d9.dll": ["Direct3DCreate9"],
                "note": "the 11 d3dx9_42 imports are in the IAT and none was reached",
            },
        }

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": args.id,
        "stage": "S4",
        "date": time.strftime("%Y-%m-%d"),
        "question": "With window creation stubbed and the message pump under host control, "
                    "how many iterations of the game's own main loop execute, what does the "
                    "guest do in them, and exactly where does presentation begin?",
        "authority": "driven",
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 3.5, section 5 S4 and S5"],
            "inherited": [
                "Documentation/receipts/s4-guest-filesystem-v1.json",
                "Documentation/receipts/s4-crt-startup-v1.json",
                "Documentation/receipts/s3-build-time-v2.json",
            ],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": C.sha256(Path(C.EXE))},
            "binary": {
                "tag": args.tag,
                "path": str((C.GEN / "s4" / "link" / "mains" / "loop.next+allarms"
                             / "sa2_main").relative_to(C.ROOT)),
                "sha256": (link_state or {}).get("link", {}).get("sha256"),
                "bytes": (link_state or {}).get("link", {}).get("binary_bytes"),
                "lifted_objects": (link_state or {}).get("link", {}).get("objects_lifted"),
                "extra_blocks": len((extra or {}).get("objects", [])),
                "runtime_and_driver_objects": (link_state or {}).get("link", {})
                                              .get("objects_runtime"),
            },
            "pipeline": {f: sha(C.ROOT / f) for f in PIPELINE if (C.ROOT / f).is_file()},
            "git_head": head,
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s4/lift_extra.py --tag next+allarms --lift-tag next "
            "--arms-tag next --entry 0x00401da4 --why \"WinMain's own message loop head\"",
            ".venv/bin/python tools/s4/loop_run.py --out-prefix loop2 "
            "--probe-arg=--drive-loop --probe-arg=--require-files --probe-arg=--require-stop "
            "--probe-arg=--require-loop --probe-arg=--stop-import "
            "--probe-arg=Direct3DCreate9 --run '<label>|<VAR=VALUE>...'",
            ".venv/bin/python tools/s4/loop_break.py --name loop.break",
            ".venv/bin/python tools/s4/build_cost.py --tag next+allarms --repeats 3",
            ".venv/bin/python tools/s4/install_guard.py --snapshot after-loop --compare before",
            ".venv/bin/python tools/s2/corpus_run/run.py",
            ".venv/bin/python tools/s2/corpus_run/controls.py",
            ".venv/bin/python Documentation/scripts/run_all_gates.py",
        ],
        "where_the_loop_lives": {
            "winmain": "0x00401b80",
            "window_creation": "0x004019d0, called from 0x00401d40",
            "initialisation": "0x004010d0, called from 0x00401d49; WinMain jumps to its own "
                              "exit at 0x00401e48 when it returns false",
            "loop_head": "0x00401da4",
            "loop_exit": "0x00401e07, reached when GetMessageW answers FALSE",
            "finding": "the main loop is *behind* the presentation boundary, not in front of "
                       "it: FUN_004010d0 reaches IDirect3D9::CreateDevice, so WinMain never "
                       "pumps in this build. 0x00401da4 is lifted as its own module "
                       "(tools/s4/lift_extra.py) and driven as a mid-function window entry "
                       "with the two registers WinMain loads at 0x00401d98 and 0x00401d9e.",
        },
        "runs": rows,
        "d3d9": d3d9,
        "break": {
            "verdict": str((RUNDIR / f"{args.break_name}.verdict.json").relative_to(C.ROOT)),
            "ok": (brk or {}).get("ok"),
            "restored": (brk or {}).get("restored"),
            "control_pump": (brk or {}).get("control", {}).get("pump"),
            "rounds": [{"label": r["label"], "file": r["file"], "targets": r["targets"],
                        "targets_failed": r["targets_failed"],
                        "untouched_assertions_still_true": r["untouched_assertions_still_true"],
                        "pump": r["pump"], "ok": r["ok"]}
                       for r in (brk or {}).get("rounds", [])],
        },
        "build_time": {
            "criterion": "G1-1-build-time, 60 minute threshold",
            "runtime_source_minutes_added": (build or {}).get("delta", {})
                                            .get("criterion_minutes_added"),
            "compile_seconds_added_by_file": (build or {}).get("delta", {})
                                             .get("compile_seconds_added_by_file"),
            "link_seconds_added": (build or {}).get("delta", {}).get("link_seconds_added"),
            "extra_block_lift": (build or {}).get("extra_blocks"),
        },
        "installation_untouched": guard,
        "regression": {
            # Counted from the generated files rather than from the tools'
            # printed summaries, because a summary line is prose and a count
            # over the rows is the measurement.
            "fixture_corpus": corpus_summary(corpus),
            "controls": controls_summary(controls),
            "gates": gates_line,
        },
    }
    # A receipt is a tracked record and belongs with the other receipts. It
    # was produced under private/generated while the slice ran, because that
    # slice could not write to Documentation/; the path moved when it landed.
    out = C.ROOT / "Documentation" / "receipts" / f"{args.id}.json"
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
