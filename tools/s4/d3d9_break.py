#!/usr/bin/env python3
"""Break the recording d3d9 device on purpose, and check that each break fails.

Subtitle: one build, one control run and one run per named break, with an
expectation per break that is checked rather than printed.

Why this exists
---------------
Seven checks in this project have turned out unable to fail, and the fix each
time was the same: run the thing with the claim deliberately falsified and
look. This drives that for the d3d9 device.

Each row is a break, an expectation, and the field the expectation reads. A
break that does *not* change what it is supposed to change fails this tool, so
"the device is doing the work" is a result rather than an assumption. The
control is run first and every expectation is expressed as a difference from
it, because a comparison whose operands can both be empty is not a comparison.

The breaks
----------
* ``argc`` reinstates the exact defect the previous slice's survey table had:
  ``IDirect3D9::CheckDeviceMultiSampleType`` declared five stack arguments
  instead of six. A ``__stdcall`` COM method pops ``this`` plus its arguments,
  so a count one dword short leaves four bytes of the caller's own arguments on
  the caller's stack. The guest calls that method twice before ``CreateDevice``,
  so by then the frame is eight bytes out and ``CreateDevice``'s ``DeviceType``
  argument comes from the wrong slot. **The expectation is not "something
  changed": it is that the recorded DeviceType stops being 1 (D3DDEVTYPE_HAL)
  and becomes a pointer into the guest heap** -- which is the value the S4
  slice-5 journal records, 0x02801b38, produced by this very defect.

* ``out`` stops every method with an out-parameter from writing through it,
  while still answering S_OK. This is the difference between "the call was
  recorded and answered" and "the call was answered *with something the guest
  can use*", and a device that passed both would not be a device.

* ``guestheap`` puts a locked resource's backing store back where it was before
  S4 slice 7: bumped from ``SA2_GUEST_HEAP_BASE``, which is also the base of the
  arena the guest's own ``HeapAlloc`` allocates from -- a second allocator on
  that range. (Since 2026-09-22 the store *is* in that arena, but taken from the
  heap's own allocator, one block with a header each; the break still bypasses
  the allocator, which is the defect.) The expectation is not
  "something changed": it is that the heap *walk* stops agreeing with the
  allocator's own live-block count, which is the reading that was `false` for
  two slices while nobody looked at it.

* ``noimpl`` runs no handler at all and answers S_OK to everything, which is
  the previous slice's survey. The expectation is that the run collapses back
  to roughly what the survey reached, because if it does not then the guest was
  never depending on the answers.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/d3d9_break.py
  .venv/bin/python tools/s4/d3d9_break.py --no-build
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

HERE = Path(__file__).resolve().parent
PY = C.ROOT / ".venv" / "bin" / "python"
RUNDIR = C.GEN / "s4" / "run"
OUTDIR = C.GEN / "s4" / "d3d9"
BASE = "SA2_D3D9_DEVICE=1"


def run(label: str, env_extra: str, build: bool, probe_timeout: int) -> dict:
    out = RUNDIR / f"d3d9break.{label}.json"
    if out.is_file():
        out.unlink()
    spec = f"{label}|{BASE}" + (f"|{env_extra}" if env_extra else "")
    cmd = [PY, HERE / "loop_run.py", "--name", "loop.next+allarms",
           "--out-prefix", "d3d9break", "--run", spec,
           "--probe-arg=--timeout", f"--probe-arg={probe_timeout}"]
    if not build:
        cmd.append("--no-build")
    p = subprocess.run([str(x) for x in cmd], cwd=str(C.ROOT), capture_output=True,
                       text=True, timeout=2400)
    if p.returncode or not out.is_file():
        return {"error": f"loop_run exited {p.returncode}", "stdout_tail": p.stdout[-2000:]}
    return json.loads(out.read_text())


def createdevice_devicetype(d: dict) -> str | None:
    """The DeviceType argument of the one IDirect3D9::CreateDevice call."""
    for c in d.get("d3d9", {}).get("calls", []):
        if c["interface"] == "IDirect3D9" and c["method"] == "CreateDevice":
            return c["args"][1] if len(c["args"]) > 1 else None
    return None


def summarise(d: dict) -> dict:
    x = d.get("d3d9", {})
    h = d.get("heap", {})
    if "consistent" not in h:
        raise SystemExit("the probe's heap section has no `consistent` field; this tool reads "
                         "the field the runtime writes and refuses to guess a key name")
    return {"ok": d.get("ok"), "import_calls": d["calls"]["total"],
            "method_calls": x.get("method_calls"), "distinct": x.get("distinct_methods"),
            "objects_built": x.get("objects_built"),
            "createdevice_devicetype": createdevice_devicetype(d),
            "heap_consistent": h["consistent"],
            "heap_walk_inuse": h.get("walk_inuse"), "heap_live_blocks": h.get("live_blocks"),
            "trap": d.get("trap", {}).get("note", "")[:200],
            "winmain_iterations": d.get("winmain_loop", {}).get("iterations")}


def heap_bounds(d: dict) -> tuple[int, int]:
    """The guest heap arena the *runtime reports*, not a constant typed here.

    The arena moved in S4 slice 7 and the `argc` expectation below had its old
    bounds written into it as literals; a range that no longer contains the
    arena would have made that check unfalsifiable rather than failing."""
    lo, hi = d["heap"]["arena"]
    return int(lo, 16), int(hi, 16)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--probe-timeout", type=int, default=180)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (OUTDIR / "breaks.json")
    doc: dict = {"schema": "sa2-recomp-measurement/v1", "id": "s4-d3d9-breaks",
                 "question": "does each claim the recording d3d9 device makes fail when "
                             "the thing it claims is deliberately broken?",
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": []}

    def save() -> None:
        doc["written"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out_path.write_text(json.dumps(doc, indent=1) + "\n")

    save()
    control = run("control", "", not args.no_build, args.probe_timeout)
    if "error" in control:
        doc["control"] = control
        doc["verdict"] = "the control run did not produce JSON; nothing below was checked"
        save()
        print(json.dumps(doc["control"], indent=1))
        raise SystemExit(2)
    doc["control"] = summarise(control)
    save()

    cdt = doc["control"]["createdevice_devicetype"]
    heap_lo, heap_hi = heap_bounds(control)
    checks = [
        {
            "break": "argc",
            "env": "SA2_D3D9_BREAK=argc",
            "claim": "every __stdcall COM method pops this plus its own declared "
                     "arguments, so the caller's frame does not drift",
            "expect": "CreateDevice's DeviceType argument stops reading "
                      f"{cdt} (D3DDEVTYPE_HAL), reads a guest heap pointer instead, and "
                      "the device's own argument-sanity guard refuses the call by name",
            "test": lambda c, b: (b["createdevice_devicetype"] != c["createdevice_devicetype"]
                                  and b["createdevice_devicetype"] is not None
                                  and heap_lo <= int(b["createdevice_devicetype"], 16) < heap_hi
                                  and "DeviceType" in (b["trap"] or "")
                                  and "frame has drifted" in (b["trap"] or "")),
        },
        {
            "break": "out",
            "env": "SA2_D3D9_BREAK=out",
            "claim": "a method with an out-parameter fills it with something the guest "
                     "can use, not merely S_OK",
            "expect": "the run stops earlier and fewer distinct methods are reached",
            "test": lambda c, b: (b["import_calls"] < c["import_calls"]
                                  and b["distinct"] < c["distinct"]),
        },
        {
            "break": "guestheap",
            "env": "SA2_D3D9_BREAK=guestheap",
            "claim": "a locked resource's backing store is taken from the guest heap's own "
                     "allocator (since 2026-09-22; before that, from a separate runtime arena), "
                     "never from a second allocator laid over the guest's range, so it cannot "
                     "erase the objects the guest built with malloc",
            "expect": "a second, bump allocator on the guest heap's range makes the heap walk "
                      "disagree with the allocator's own count, and the run stops earlier",
            "test": lambda c, b: (c["heap_consistent"] is True
                                  and b["heap_consistent"] is False
                                  and b["import_calls"] < c["import_calls"]),
        },
        {
            "break": "noimpl",
            "env": "SA2_D3D9_BREAK=noimpl",
            "claim": "the guest depends on the answers, so removing every handler "
                     "collapses the run to what the previous slice's survey reached",
            "expect": "no object is fabricated and the distinct method count falls to "
                      "the survey's handful",
            "test": lambda c, b: (b["objects_built"] <= 1 and b["distinct"] < c["distinct"]
                                  and b["import_calls"] < c["import_calls"]),
        },
    ]

    failed = []
    for ch in checks:
        d = run(ch["break"], ch["env"], False, args.probe_timeout)
        row = {"break": ch["break"], "env": ch["env"], "claim": ch["claim"],
               "expect": ch["expect"]}
        if "error" in d:
            row.update(d, broke_as_expected=False,
                       why="the broken run produced no JSON, so nothing was compared")
        else:
            row["observed"] = summarise(d)
            try:
                row["broke_as_expected"] = bool(ch["test"](doc["control"], row["observed"]))
            except Exception as e:                       # noqa: BLE001
                row["broke_as_expected"] = False
                row["why"] = f"the expectation could not be evaluated: {e!r}"
        if not row["broke_as_expected"]:
            failed.append(ch["break"])
        doc["rows"].append(row)
        save()

    doc["breaks_that_did_not_break"] = failed
    doc["verdict"] = ("every break changed what it claims to change"
                      if not failed else
                      "these breaks changed nothing, so the claim beside each is unchecked: "
                      + ", ".join(failed))
    save()
    print(json.dumps({"control": doc["control"],
                      "rows": [{k: r.get(k) for k in
                                ("break", "broke_as_expected", "observed")} for r in doc["rows"]],
                      "verdict": doc["verdict"]}, indent=1))
    print(f"-> {out_path}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
