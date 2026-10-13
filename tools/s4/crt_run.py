#!/usr/bin/env python3
"""Drive the image's entry point, and close one missing-block defect per round.

Subtitle: `mb_edges.py` + `lift_extra.py` + `link_s4.py` + the probe, in a loop
that stops as soon as the run stops for a reason that is not a missing block.

Why a tool and not four commands
--------------------------------
Each round is the same four steps and the only thing that changes between them
is one address, which the previous round's probe wrote into its own JSON. Doing
that by hand is the step this project's rule 13 says becomes a tool before it is
repeated a third time -- and doing it by hand is also how a round gets run
against a stale generated table, which is the defect class the operating notes
record most often.

What a round does
-----------------
1. Read the last run's missing-block census. If it trapped on an address in no
   module, lift that address as its own module (`lift_extra.py`) and record why.
2. Regenerate the edge table, with the newly lifted blocks as named dispatch
   exceptions (`mb_edges.py --allow-from`).
3. Relink the driver, incrementally.
4. Run it and write the JSON.

It stops when the run stops for any other reason -- an unimplemented import, a
refusal, the guest's own exit, or success -- because those are decisions for a
person, not for a loop. Every round's JSON is kept under its own name, including
the failing ones: the run that gets deleted is always the one that failed.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/crt_run.py --rounds 6 --name crt.next+allarms
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
LINKDIR = C.GEN / "s4" / "link"
RUNDIR = C.GEN / "s4" / "run"
# An inode's mtime comes from the kernel's coarse cached clock; `time.time()`
# reads the fine one. Measured on this tree, a file written *after* the sample
# can carry an mtime up to 6 ms *earlier*, so a bare `mtime < started` rejects
# runs that did happen. `tools/s4/loop_run.py` removes the output first, which
# is strictly better; this loop keeps the previous round's JSON on purpose, so
# it gets the slack instead -- and the slack is named rather than tuned.
MTIME_SLACK_S = 0.05


def sh(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    print("$ " + " ".join(str(c) for c in cmd).replace(str(C.ROOT) + "/", ""), flush=True)
    p = subprocess.run([str(c) for c in cmd], cwd=str(C.ROOT), capture_output=True,
                       text=True, timeout=timeout)
    tail = (p.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print("  " + line, flush=True)
    if p.returncode and p.stderr:
        print("  ! " + p.stderr.strip().splitlines()[-1][:400], flush=True)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--lift-tag", default="next")
    ap.add_argument("--arms-tag", default="next")
    ap.add_argument("--name", default="crt.next+allarms")
    ap.add_argument("--main", default="harness/crt_probe.c")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--allow-target", action="append", default=[],
                    help="VA=reason, passed straight to tools/s4/mb_edges.py. A fall-through "
                         "into an address that is *already a module* is not a missing "
                         "module and must not be lifted again -- the second lift defines "
                         "sub_<va> twice and the link refuses -- so the reviewed per-address "
                         "exception is the closure, and the loop has to be able to carry it.")
    ap.add_argument("--stop-at", default=None)
    ap.add_argument("--require-stop", action="store_true")
    ap.add_argument("--probe-arg", action="append", default=[])
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--out", default=None, help="the run JSON name; default <name>.round<N>.json")
    args = ap.parse_args()

    RUNDIR.mkdir(parents=True, exist_ok=True)
    extra_state = LINKDIR / f"extra_functions.{args.tag}.json"
    history = []
    last_json = None

    for rnd in range(args.rounds):
        # 1. close the defect the previous round found, if it is one of the two
        #    shapes the same map defect takes: an address in no module reached
        #    by a branch (a missing block) or called (a function-table miss).
        if last_json and last_json.is_file():
            doc = json.loads(last_json.read_text())
            trapped = [e for e in doc.get("missing_block", {}).get("census", [])
                       if int(e.get("traps", 0)) > 0]
            # A function-table miss is the same map defect wherever it is
            # raised, and a *worker thread* that dies of one leaves the main
            # thread sleeping on a handshake that will never complete -- which
            # reads as a virtual-budget spin, a stop this loop does not close,
            # so the loop used to give up with the real defect sitting in a
            # thread row nobody looked at. Every thread's note is read now, and
            # all of a round's distinct misses are closed together rather than
            # one per relink.
            notes = [doc["trap"]["note"] or ""]
            notes += [r.get("trap_note") or ""
                      for r in doc.get("threads", {}).get("rows", [])]
            misses, seen = [], set()
            for n in notes:
                for a in re.findall(r"function table miss at (0x[0-9a-f]+)", n):
                    if a not in seen:
                        seen.add(a)
                        misses.append(a)
            if trapped:
                sh([PY, HERE / "lift_extra.py", "--tag", args.tag, "--lift-tag", args.lift_tag,
                    "--arms-tag", args.arms_tag, "--from-census", last_json])
            elif misses:
                for a in misses:
                    sh([PY, HERE / "lift_extra.py", "--tag", args.tag,
                        "--lift-tag", args.lift_tag, "--arms-tag", args.arms_tag,
                        "--entry", a, "--why",
                        f"called from lifted code in a run of {last_json.name} and in no "
                        f"module: the function map lumps it into the row that contains it"])
            else:
                print(f"round {rnd}: the last run stopped for a reason this loop does not "
                      f"close; stopping", flush=True)
                break

        round_started = time.time()

        # 2. the edge table, with the lifted blocks as named exceptions
        cmd = [PY, HERE / "mb_edges.py", "--tag", args.tag, "--lift-tag", args.lift_tag,
               "--arms-tag", args.arms_tag]
        if extra_state.is_file():
            cmd += ["--allow-from", extra_state]
        for a in args.allow_target:
            cmd += ["--allow-target", a]
        sh(cmd)

        # 3. link
        out = RUNDIR / (args.out or f"{args.name}.round{rnd}.json")
        link = [PY, HERE / "link_s4.py", "--tag", args.tag, "--main", args.main,
                "--name", args.name,
                "--extra-c", LINKDIR / f"mb_edges.{args.tag}.c"]
        objs = []
        if extra_state.is_file():
            st = json.loads(extra_state.read_text())
            objs = st.get("objects", [])
            if objs:
                link += ["--extra-c", LINKDIR / f"extra_functions.{args.tag}.c"]
                for o in objs:
                    link += ["--extra-o", o]
        link += ["--run", C.EXE, "--run=--json", "--run", out]
        if args.stop_at:
            link += ["--run=--stop-at", "--run", args.stop_at]
        if args.require_stop:
            link += ["--run=--require-stop"]
        for a in args.probe_arg:
            link += [f"--run={a}"]
        for e in args.env:
            link += ["--env", e]
        sh(link)
        # A failed link leaves the previous binary in place, and `link_s4.py`'s
        # run phase would happily run it: the probe would then report a run of
        # a build that does not exist. That happened once in this session --
        # four rounds reported the identical stop address while every relink was
        # failing on an undefined symbol -- so the link status is checked here
        # and the loop stops rather than reporting a stale run as a result.
        state_path = LINKDIR / f"main.{args.name}.json"
        if state_path.is_file():
            st = json.loads(state_path.read_text())
            # The compile has to be checked as well as the link, and it is the
            # case the original guard missed: a failed *compile* makes
            # `link_s4.py` refuse to link, which leaves the *previous*
            # successful link record in place with rc 0. The loop then read a
            # run JSON from the previous binary and reported it as this round's
            # result -- the same stale-report failure the link guard was
            # written for, one phase earlier. Both are checked now, and the
            # run JSON must also be newer than the round that claims it.
            if not st.get("compile_ok", True):
                bad = {k: v for k, v in st.get("compile", {}).items() if v.get("rc")}
                print("the compile failed; refusing to report a run of the previous binary: "
                      + ", ".join(bad) + "\n"
                      + "\n".join(v.get("stderr_tail", "")[-1500:] for v in bad.values()),
                      flush=True)
                break
            if st.get("link", {}).get("rc"):
                print("the link failed; refusing to report a run of the previous binary:\n"
                      + st["link"].get("stderr_tail", "")[-2000:], flush=True)
                break

        if not out.is_file():
            print(f"round {rnd}: the probe wrote no JSON; stopping", flush=True)
            break
        if out.stat().st_mtime < round_started - MTIME_SLACK_S:
            print(f"round {rnd}: {out.name} was not written by this round "
                  f"(mtime {out.stat().st_mtime} < {round_started}); refusing to report it",
                  flush=True)
            break
        doc = json.loads(out.read_text())
        note = doc["trap"]["note"]
        history.append({"round": rnd, "json": str(out), "ok": doc["ok"],
                        "exit_pc": doc["trap"]["exit_pc"], "note": note,
                        "import_calls": doc["calls"]["total"],
                        "extra_blocks": doc.get("extra_blocks_registered", 0)})
        print(f"== round {rnd}: ok={doc['ok']} calls={doc['calls']['total']} "
              f"stop={doc['trap']['exit_pc']} {note[:160]}", flush=True)
        last_json = out
        closable = ([e for e in doc.get("missing_block", {}).get("census", [])
                     if int(e.get("traps", 0)) > 0]
                    or any(re.search(r"function table miss at 0x", n or "")
                           for n in [doc["trap"]["note"]]
                           + [r.get("trap_note") for r in
                              doc.get("threads", {}).get("rows", [])]))
        if doc["ok"] or not closable:
            break
        # **A round that changes nothing is not a round.**
        #
        # `closable` asks "is this stop one this loop knows how to close", and
        # a `traps > 0` census row answers yes even when `lift_extra.py` then
        # refuses the address because it is *already a module* -- which is the
        # right refusal and leaves the loop with the same stop, the same
        # census, and the same refusal, forever. It span five rounds on
        # 0x00826f60 that way: a bound whose range no longer contains anything
        # has stopped bounding.
        #
        # The exit condition is the *count of extra modules*, which is the
        # thing a productive round moves. Same stop and no new module means the
        # closure this loop has is not the closure this address needs, and the
        # one it does need is the reviewed `--allow-target`, so the message
        # says so with the address already filled in.
        objs_now = len(json.loads(extra_state.read_text()).get("objects", [])) \
            if extra_state.is_file() else 0
        if len(history) >= 2 and history[-2]["note"] == note \
                and history[-2].get("extra_objects") == objs_now:
            print(f"round {rnd}: the same stop as the previous round and not one new "
                  f"module: this loop cannot close {doc['trap']['exit_pc']}. If it is an "
                  f"address that is already its own module, the closure is "
                  f"--allow-target {doc['trap']['exit_pc']}=<why>; otherwise it is a "
                  f"defect in lift_extra.py. Stopping rather than spinning.", flush=True)
            history[-1]["stalled_on"] = doc["trap"]["exit_pc"]
            break
        history[-1]["extra_objects"] = objs_now

    hist = RUNDIR / f"{args.name}.rounds.json"
    prev = json.loads(hist.read_text())["rounds"] if hist.is_file() else []
    hist.write_text(json.dumps({"written": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "rounds": prev + history}, indent=1) + "\n")
    print(f"-> {hist}")


if __name__ == "__main__":
    main()
