#!/usr/bin/env python3
"""Break the window and the pump on purpose, and check that the probe notices.

Subtitle: one exact source substitution per round, a rebuild and a guest run
for each, the sources restored in a `finally`, and every failing JSON kept.

Why this exists
---------------
Six checks in this project have turned out unable to fail, and the last two
were found by deliberately breaking a stub and watching a probe still print
`"ok": true`. A new assertion therefore does not get believed because it
passed. It gets believed after it has been seen to fail against a runtime that
is wrong in exactly the way it is about -- and after the assertions it is *not*
about have been seen to stay true, because a break that turns everything false
shows only that the probe can fail.

One break per round, and not all at once as `fs_break.py` does them: every
clause of the loop verdict is downstream of the pump, so a single break that
empties the queue turns all of them false at once and says nothing about which
clause measures which fact.

  pump_always_empty          PeekMessageW reports "no messages" whatever the
                             queue holds. -> the four message clauses go false
                             while `loop_iterated` stays true, because the loop
                             still runs; the guest simply never sees anything.
  dispatch_skips_the_guest   DispatchMessageW posts the quit itself instead of
                             running the guest's window procedure. Every
                             *count* stays exactly as it was -- iterations,
                             messages, gets, the quit -- and only
                             `guest_wndproc_ran_and_posted_quit` can tell,
                             because only it looks at *who* called
                             PostQuitMessage. This is the round that matters.
  window_extents_invented    CreateWindowExW records a fixed 640x480 at the
                             origin instead of what the guest passed.
                             -> `guest_sized_its_window` goes false, and with
                             it `guest_built_its_window`.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/loop_break.py --name loop.break
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent
PY = C.ROOT / ".venv" / "bin" / "python"
RUNDIR = C.GEN / "s4" / "run"
LINKDIR = C.GEN / "s4" / "link"
FULLSCREEN_OVERLAY = "private/generated/s4/vfsroot-fullscreen"

# Nine harmless messages and then a close, so the guest's loop runs ten times
# and ends because its own window procedure asked it to.
INJECT = ",".join([f"{i}:0x200" for i in range(9)] + ["9:0x10"])

BREAKS = [
    {
        "label": "pump_always_empty",
        "file": "runtime/sa2_window.c",
        "old": """    const int have = queue_front(out);
    if (have) g_st.polls_message++; else g_st.polls_empty++;
    return have;""",
        "new": """    const int have = 0;            /* BREAK pump_always_empty */
    (void)queue_front(out);
    if (have) g_st.polls_message++; else g_st.polls_empty++;
    return have;""",
        "targets": ["loop_saw_a_message", "guest_wndproc_ran_and_posted_quit",
                    "guest_ended_the_loop", "loop_left_the_pump_on_wm_quit",
                    "loop_ended_after_the_guest_asked_to_quit"],
        # The blocking-GetMessageW pair runs after the loop and needs the queue
        # to be *empty* for its first half. A pump that never reports a message
        # leaves every injected one sitting in the queue, so that half is
        # served instead of refused. The break makes its precondition false
        # rather than breaking the thing it checks.
        "collateral": ["blocking_getmessage_refused_and_ready_served"],
    },
    {
        "label": "dispatch_skips_the_guest",
        "file": "runtime/sa2_imports.c",
        "old": """    sa2_window_note_dispatch(0);
    const uint32_t r = call_wndproc(s, w->wndproc, m.hwnd, m.msg, m.wparam, m.lparam);
    sa2_window_note_wndproc_returned();   /* after, so the count needs the guest */
    return r;""",
        "new": """    sa2_window_note_dispatch(0);
    /* BREAK dispatch_skips_the_guest: the runtime decides, not the guest. */
    if (m.msg == SA2_WM_CLOSE) sa2_window_post_quit(0, 0);
    return 0;""",
        "targets": ["guest_wndproc_ran_and_posted_quit"],
        # `loop_ended_after_the_guest_asked_to_quit` is the conjunction that
        # holds the clause this break targets, so it moves with it. Naming it
        # here is what keeps "everything else stayed true" a real statement:
        # every count this break leaves untouched -- iterations, messages,
        # gets, the delivered quit -- is identical to the control's, which is
        # the whole point of the round.
        "collateral": ["loop_ended_after_the_guest_asked_to_quit"],
    },
    {
        "label": "window_extents_invented",
        "file": "runtime/sa2_imports.c",
        "old": """    w.x = (int32_t)a[4];
    w.y = (int32_t)a[5];
    w.w = (int32_t)a[6];
    w.h = (int32_t)a[7];""",
        "new": """    w.x = 0; w.y = 0; w.w = 640; w.h = 480;   /* BREAK window_extents_invented */""",
        "targets": ["guest_sized_its_window", "guest_built_its_window"],
    },
]

# Assertions that legitimately move with a break because they are conjunctions
# over its targets, or because the broken run stops earlier than the deliberate
# stop. Naming them is what keeps "everything else stayed true" meaningful.
COLLATERAL = {"ended_where_intended", "reached_the_stop_address"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sh(cmd, timeout=3600):
    return subprocess.run([str(c) for c in cmd], cwd=str(C.ROOT), capture_output=True,
                          text=True, timeout=timeout)


def link_args(name: str) -> list:
    args = [PY, HERE / "link_s4.py", "--tag", "next+allarms",
            "--main", "harness/crt_probe.c", "--name", name,
            "--extra-c", LINKDIR / "mb_edges.next+allarms.c"]
    state = LINKDIR / "extra_functions.next+allarms.json"
    if state.is_file():
        objs = json.loads(state.read_text()).get("objects", [])
        if objs:
            args += ["--extra-c", LINKDIR / "extra_functions.next+allarms.c"]
            for o in objs:
                args += ["--extra-o", o]
    return args


def run_one(name: str, out: Path) -> dict:
    """One build-and-run under `name`. Every round reuses the same name so the
    incremental object cache survives: a fresh name recompiles the 2 MB
    generated function table, which turns a ninety-second break sweep into a
    half-hour one for no extra evidence."""
    # Removed before the run, not compared by mtime afterwards: see the note in
    # `loop_run.py`. An mtime from the kernel's coarse cached clock can precede
    # a `time.time()` sampled before the write by several milliseconds.
    if out.is_file():
        out.unlink()
    started = time.time()
    p = sh(link_args(name) + [
        "--run", C.EXE, "--run=--json", "--run", out,
        "--run=--drive-loop", "--run=--require-loop", "--run=--require-files",
        "--run=--require-stop", "--run=--stop-import", "--run", "Direct3DCreate9",
        "--env", "SA2_INSTALL=private/install",
        "--env", f"SA2_VFS_WRITE_ROOT={FULLSCREEN_OVERLAY}",
        "--env", f"SA2_PUMP_INJECT={INJECT}"])
    st_path = LINKDIR / f"main.{name}.json"
    st = json.loads(st_path.read_text()) if st_path.is_file() else {}
    rec = {"link_rc": p.returncode, "link_tail": (p.stdout or "")[-1200:],
           "compile_ok": st.get("compile_ok"), "link_phase_rc": st.get("link", {}).get("rc")}
    # A failed compile leaves the previous binary in place and `link_s4.py`
    # refuses to link but will still run it; a break reported against a binary
    # that does not contain the break is worse than no break at all.
    if not st.get("compile_ok"):
        rec["error"] = "the broken source did not compile"
        return rec
    if not out.is_file():
        rec["error"] = "the probe wrote no JSON for this round"
        return rec
    rec["seconds"] = round(time.time() - started, 2)
    doc = json.loads(out.read_text())
    rec["run_ok"] = doc.get("ok")
    rec["assert"] = doc.get("assert", {})
    rec["pump"] = {k: doc.get("pump", {}).get(k)
                   for k in ("iterations", "message_polls", "gets", "dispatches",
                             "guest_wndproc_calls", "quit_by_guest", "quit_by_host",
                             "quit_delivered", "postquit_caller")}
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="loop.break")
    args = ap.parse_args()
    RUNDIR.mkdir(parents=True, exist_ok=True)

    files = sorted({b["file"] for b in BREAKS})
    originals = {f: (C.ROOT / f).read_text() for f in files}
    before = {f: sha(C.ROOT / f) for f in files}

    result = {"written": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "inject": INJECT, "sha256_before": before, "rounds": []}

    # The control: the same binary and the same environment with nothing broken.
    # Without it, "the break made these go false" cannot be told from "these
    # were false anyway", which is the shape of half this project's findings.
    control_out = RUNDIR / f"{args.name}.control.json"
    control = run_one(args.name, control_out)
    control["label"] = "control"
    result["control"] = control

    try:
        for b in BREAKS:
            path = C.ROOT / b["file"]
            text = originals[b["file"]]
            if text.count(b["old"]) != 1:
                raise SystemExit(f"{b['file']}: the text break {b['label']} replaces occurs "
                                 f"{text.count(b['old'])} times, not once; the break would "
                                 f"not be the one it says it is")
            path.write_text(text.replace(b["old"], b["new"]))
            out = RUNDIR / f"{args.name}.{b['label']}.json"
            rec = run_one(args.name, out)
            rec["label"] = b["label"]
            rec["file"] = b["file"]
            rec["sha256_broken"] = sha(path)
            rec["targets"] = b["targets"]
            rec["collateral"] = sorted(COLLATERAL | set(b.get("collateral", [])))
            a = rec.get("assert", {})
            rec["targets_failed"] = {t: (a.get(t) is False) for t in b["targets"]}
            skip = set(b["targets"]) | COLLATERAL | set(b.get("collateral", []))
            untouched = {k: v for k, v in a.items() if k not in skip}
            rec["untouched_assertions_still_true"] = bool(untouched) and all(untouched.values())
            rec["untouched_that_moved"] = sorted(k for k, v in untouched.items() if not v)
            rec["ok"] = (rec.get("run_ok") is False
                         and all(rec["targets_failed"].values())
                         and rec["untouched_assertions_still_true"])
            result["rounds"].append(rec)
            path.write_text(originals[b["file"]])
    finally:
        for f in files:
            (C.ROOT / f).write_text(originals[f])
        result["sha256_after_restore"] = {f: sha(C.ROOT / f) for f in files}
        result["restored"] = result["sha256_after_restore"] == before

    result["ok"] = (result["restored"] and control.get("run_ok") is True
                    and bool(result["rounds"]) and all(r["ok"] for r in result["rounds"]))
    vf = RUNDIR / f"{args.name}.verdict.json"
    vf.write_text(json.dumps(result, indent=1) + "\n")
    print(f"control: ok={control.get('run_ok')} pump={control.get('pump')}")
    for r in result["rounds"]:
        for t, failed in r["targets_failed"].items():
            print(f"  [{r['label']}] {'FAILED as required' if failed else 'STILL PASSED (a hole)'}"
                  f"  {t}")
        print(f"  [{r['label']}] run_ok={r.get('run_ok')} "
              f"untouched_still_true={r.get('untouched_assertions_still_true')} "
              f"moved={r.get('untouched_that_moved')}")
    print(f"restored={result['restored']}")
    print(f"-> {vf}   ok={result['ok']}")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
