#!/usr/bin/env python3
"""Run the reach configuration until it stops on something that is not a missed function.

Subtitle: `loop_run.py` and `lift_extra.py` in a loop, with the reason for
every lift generated from evidence rather than typed, and a record of every
round kept.

Why this exists
---------------
Once the data DLL mapped (2026-09-22) the run stopped, one round at a time, on
function-table misses at small functions the analyzer's map never listed:
callbacks an initialiser registers by pushing their address, sitting in the
int3 padding after a neighbour's recorded extent. Each is the same three
steps -- read the stop, look at the address, lift it with a reason -- and rule
13 says a step repeated a third time is a tool. The previous way to get this
wrong is recorded in `Documentation/lifted-extras-backfill.json`: two blocks
were lifted with no reason at all, which is a decision about what the guest may
execute that nothing explains.

What it will lift, and what it will not
---------------------------------------
Only a stop whose note is `function table miss at 0x........ (region <name>`,
where <name> is an executable section of the image -- the main thread's, or
failing that a guest worker thread's, since a worker that dies on a miss leaves
the main thread stopped somewhere else -- at an address that

  * decodes to a `ret` or an unconditional `jmp` within MAX_BODY instructions
    without meeting int3 first,
  * is not already a function-map entry or a lifted extra, and
  * either follows int3 padding or a `ret`, or is the first byte of its
    section -- a function boundary -- or is the
    operand of some instruction (`tools/s4/xrefs.py` finds it), or is the value
    of a relocated dword in a data section: a vtable slot.

Anything else ends the loop and is reported: a miss in the middle of a function
is a different defect (a wrong extent, or a lift that stopped short) and wants a
person. So does the same address twice, which means a lift did not take.

The reason recorded with each lift names the run's JSON, the round, the
instruction count to the first `ret`, the map entry whose recorded extent the
function follows, and the sites that reference it.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/reach_extend.py --name dll.next+allarms [--max-rounds 12]

Writes private/generated/s4/run/reach_extend.<name>.json and prints lines
prefixed EXTEND.
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402
import image as IM  # noqa: E402
import reach_receipt as RR  # noqa: E402
from iced_x86 import Decoder, FlowControl  # noqa: E402

PY = C.ROOT / ".venv" / "bin" / "python"
RUNDIR = C.GEN / "s4" / "run"
EXTRAS = C.GEN / "s4" / "link" / f"extra_functions.{RR.TAG}.json"
# Any region, then checked against the image's executable sections: the first
# version matched `.text` by name, and so refused 0x00871000, the first byte of
# PSFD00 -- the image's other code section, where the CRI decoder's routines
# are -- although every other rule held for it.
MISS = re.compile(r"^function table miss at (0x[0-9a-f]{8}) \(region ([^,]+),")
MAX_BODY = 2000     # a guard against running off into data, not a size limit: the
                    # first round-12 refusal was a real 224-instruction function


def run(name: str, stage: str | None = None) -> dict:
    if stage:
        # The stage configuration (tools/s4/stage_run.py): the reach's, with the
        # test spawn's wraps and settings, so the walls stage loading and play
        # hit are walked by the same loop as the boot's.
        import stage_run as SR
        cmd, out, _ = SR.command(stage)
        out.unlink(missing_ok=True)      # a failed build must not leave last round's record
        p = subprocess.run(cmd, cwd=str(C.ROOT), capture_output=True, text=True, timeout=3600)
        if p.returncode != 0:
            raise SystemExit(f"EXTEND_FAILED loop_run exited {p.returncode}\n{p.stdout[-2000:]}\n"
                             f"{p.stderr[-1500:]}")
        if not out.is_file():
            raise SystemExit(f"EXTEND_FAILED the stage run wrote no JSON\n{p.stdout[-2000:]}")
        return json.loads(out.read_text())
    spec = "|".join(["a"] + [f"{k}={v}" for k, v in RR.ENV.items()])
    cmd = [str(PY), str(HERE / "loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN,
           "--name", name, "--run", spec]
    for a in RR.PROBE_ARGS:
        cmd.append(f"--probe-arg={a}")          # the `=` form; see reach_receipt.run
    out = RUNDIR / f"{name}.a.json"
    out.unlink(missing_ok=True)
    p = subprocess.run(cmd, cwd=str(C.ROOT), capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        raise SystemExit(f"EXTEND_FAILED loop_run exited {p.returncode}\n{p.stdout[-2000:]}\n{p.stderr[-1500:]}")
    if not out.is_file():
        raise SystemExit(f"EXTEND_FAILED the run wrote no JSON\n{p.stdout[-2000:]}")
    return json.loads(out.read_text())


def body(img: "IM.Image", va: int) -> tuple[int, str]:
    """Instructions to the body's first end, or 0 and why not.

    A body ends at a `ret` or an unconditional `jmp`: a tail call, as
    0x00670830 is, or a thunk that is nothing else, as 0x00852C3B is (one
    `jmp`, an exception-handler thunk). int3 before either is data being
    decoded as code, and refuses. The first version ended only at a `jmp`
    followed by padding, so it counted 0x00852C3B as 32 instructions by
    decoding on into the next function, and recorded that in the lift's reason.
    """
    sec = img.section_of(va)
    if sec is None or not sec.executable:
        return 0, "not in an executable section"
    data = img._section_bytes[sec.name]
    for k, ins in enumerate(Decoder(32, data[va - sec.va:], ip=va), 1):
        if k > MAX_BODY:
            return 0, f"no ret or jmp within {MAX_BODY} instructions"
        if ins.flow_control == FlowControl.INTERRUPT:
            return 0, f"int3 at {ins.ip:#010x} before any ret or jmp"
        if ins.flow_control in (FlowControl.RETURN, FlowControl.UNCONDITIONAL_BRANCH):
            return k, ""
    return 0, "the section ends first"


def data_refs(img: "IM.Image", va: int) -> list[str]:
    """Relocated dwords in a data section whose value is `va`: a vtable slot or a
    pointer table, which is how 0x00860860 is reached (from .rdata 0x00900214)
    with a jump table rather than padding in front of it. A relocation is what
    makes a dword a pointer rather than a coincidence of bytes."""
    out = []
    for at in sorted(img.relocated):
        sec = img.section_of(at)
        if sec is None or sec.executable:
            continue
        rva = at - img.base
        if img.pe.get_dword_at_rva(rva) == va:
            out.append(f"{at:#010x} {sec.name.strip()}")
    return out


def xref_sites(va: int) -> list[str]:
    p = subprocess.run([str(PY), str(HERE / "xrefs.py"), f"{va:#010x}"], cwd=str(C.ROOT),
                       capture_output=True, text=True, timeout=600)
    return [" ".join(line.split()[:2]) for line in p.stdout.splitlines()
            if line.startswith("   0x")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="the loop_run build name to drive")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--stage", default=None,
                    help="drive tools/s4/stage_run.py's configuration for this stage instead of "
                         "the reach configuration (the --name is then only the record's name)")
    a = ap.parse_args()

    img = IM.Image()
    fmap = json.loads((C.GEN / "function-map.json").read_text())["functions"]
    entries = sorted((int(f["address"], 16) if isinstance(f["address"], str) else f["address"],
                      f.get("name", ""), f.get("size", 0)) for f in fmap)
    starts = [e[0] for e in entries]
    out = RUNDIR / f"reach_extend.{a.name}.json"
    # Every invocation is kept: the first version rewrote this file on each run,
    # and a second invocation would have erased the rounds that justify the
    # lifts the first one made.
    history = json.loads(out.read_text()) if out.is_file() else {"name": a.name, "invocations": []}
    history.setdefault("invocations", [])
    record = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "rounds": []}
    history["invocations"].append(record)
    seen: set[int] = set()
    verdict = "max rounds reached"
    # The edge table once, before round 1: a previous invocation whose lift
    # succeeded and whose regeneration failed left it out of date, and round 1
    # would stop on an edge "reached by unknown" (the rule-5 review, 2026-09-23).
    e = subprocess.run([str(PY), str(HERE / "mb_edges.py"), "--tag", RR.TAG,
                        "--lift-tag", "next", "--arms-tag", "next", "--allow-from",
                        str(EXTRAS)], cwd=str(C.ROOT), capture_output=True, text=True,
                       timeout=1800)
    if e.returncode:
        raise SystemExit(f"EXTEND_FAILED mb_edges.py before round 1: {e.stderr.strip()[-300:]}")
    for k in range(1, a.max_rounds + 1):
        d = run(a.name, a.stage)
        note = (d.get("trap") or {}).get("note", "")
        rnd = {"round": k, "iterations": (d.get("pump") or {}).get("iterations"),
               "import_calls": (d.get("calls") or {}).get("total"), "stop": note[:300]}
        record["rounds"].append(rnd)
        out.write_text(json.dumps(history, indent=1) + "\n")
        print(f"EXTEND round {k}: iterations={rnd['iterations']} calls={rnd['import_calls']} "
              f"stop={note[:120]}", flush=True)
        m = MISS.match(note)
        on_thread = ""
        if not m:
            # A worker that died on a miss shows only as its trap note; the main
            # thread then stops somewhere else, often a lock the worker held
            # (2026-09-22: thread 3924 died at 0x007CBD00 holding 0x0175486C,
            # and the main thread's note named a deadlock).
            for row in (d.get("threads") or {}).get("rows", []):
                wm = MISS.match(row.get("trap_note") or "")
                if wm:
                    m = wm
                    on_thread = (f" on guest thread {row.get('index')} (start "
                                 f"{row.get('guest_start_routine')}), after which the main "
                                 f"thread stopped with: {note[:140]}")
                    break
        if not m:
            verdict = "the run stopped on something other than a missed function"
            break
        va = int(m.group(1), 16)
        rnd["on_thread"] = on_thread or None
        region = m.group(2).strip()
        if region not in {x.name.strip() for x in img.sections if x.executable}:
            verdict = (f"{va:#010x} missed in region {region}, which is not an executable "
                       f"section of the image")
            break
        extras = {int(r["va"], 16) for r in json.loads(EXTRAS.read_text()).get("rows", [])} \
            if EXTRAS.is_file() else set()
        if va in seen or va in extras or va in set(starts):
            verdict = f"{va:#010x} is already lifted or was lifted this loop, and still missed"
            break
        seen.add(va)
        n, why_not = body(img, va)
        sec = img.section_of(va)
        before = img._section_bytes[sec.name][va - sec.va - 1] if sec and va > sec.va else None
        starts_section = sec is not None and va == sec.va
        boundary = before in (0xCC, 0xC3) or starts_section
        sites = xref_sites(va) + data_refs(img, va)
        i = bisect.bisect_right(starts, va) - 1
        owner = entries[i] if i >= 0 else None
        if owner and img.section_of(owner[0]) is not sec:
            owner = None                  # the entry before it is in another section
        rnd.update(address=f"{va:#010x}", body_instructions=n, follows_boundary=boundary,
                   referenced_by=sites,
                   after=(f"{owner[1]} at {owner[0]:#010x}, recorded size {owner[2]}"
                          if owner else f"no map entry before it in {sec.name.strip()}"
                          if sec else None))
        if not n or not (boundary or sites):
            rnd["refused"] = why_not or "neither after a function boundary nor referenced"
            out.write_text(json.dumps(history, indent=1) + "\n")
            verdict = f"{va:#010x} does not look like a function entry: {rnd['refused']}"
            break
        where = ("the first byte of section " + sec.name.strip() if starts_section
                 else "after int3 padding or a ret" if boundary else "mid-gap")
        follows = f"following {rnd['after']}" if owner else rnd["after"]
        run_file = f"stage-{a.stage}.a.json" if a.stage else f"{a.name}.a.json"
        why = (f"function-table miss at {va:#010x}{on_thread} in private/generated/s4/run/{run_file} "
               f"(round {k} of tools/s4/reach_extend.py): {n} instructions to its first ret or jmp, "
               f"{where}, {follows}; referenced by {', '.join(sites) or 'no instruction operand'}")
        # --seed-arms: a function lifted here may hold a switch, whose arms the
        # G3 decision gives rows too; S3's arms count as registered. And
        # --seed-continuations: it may hold an instruction remill cannot lift,
        # after which the runtime continues.
        p = subprocess.run([str(PY), str(HERE / "lift_extra.py"), "--tag", RR.TAG,
                            "--lift-tag", "next", "--arms-tag", "next", "--seed-arms",
                            "--seed-continuations", "--entry", f"{va:#010x}", "--why", why],
                           cwd=str(C.ROOT), capture_output=True, text=True, timeout=1800)
        rnd["lifted"] = p.returncode == 0
        rnd["why"] = why
        out.write_text(json.dumps(history, indent=1) + "\n")
        if p.returncode:
            verdict = f"lift_extra.py refused {va:#010x}: {p.stderr.strip()[-300:]}"
            break
        # The new extra's own edges -- a `jmp` it makes to a map function -- are in
        # the edge table only once mb_edges decodes it; until 2026-09-23 this loop
        # never regenerated the table, so the next round stopped on its edge
        # "reached by unknown".
        e = subprocess.run([str(PY), str(HERE / "mb_edges.py"), "--tag", RR.TAG,
                            "--lift-tag", "next", "--arms-tag", "next", "--allow-from",
                            str(EXTRAS)], cwd=str(C.ROOT), capture_output=True, text=True,
                           timeout=1800)
        if e.returncode:
            verdict = f"mb_edges.py failed after lifting {va:#010x}: {e.stderr.strip()[-300:]}"
            break
        print(f"EXTEND lifted {va:#010x} ({n} instructions)", flush=True)
    record["verdict"] = verdict
    out.write_text(json.dumps(history, indent=1) + "\n")
    print(f"EXTEND done: {verdict} -> {out.relative_to(C.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
