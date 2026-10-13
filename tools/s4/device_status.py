#!/usr/bin/env python3
"""What the recording d3d9 device implements, and what it answered in a run.

Subtitle: M7's numerator side, generated from the runtime's own tables through
a probe run rather than counted by hand.

Two things, kept apart on purpose
---------------------------------
**What the runtime implements** is a property of `runtime/sa2_d3d9.c`: for each
(interface, slot) it names, does calling it return, or does it refuse? That is
the numerator M7's definition asks for and it does not depend on any run.

**What a run answered** is a property of one execution: which methods the guest
actually called, how many times, and -- the part that matters for a later stage
-- how each answer was arrived at. An answer derived from something the guest
supplied is a different kind of thing from a capability this runtime invented,
and a file that reported only "implemented: 47" would lose that difference
entirely.

Both come out of the probe's JSON, because the probe reads the runtime's own
tables through `sa2_d3d9_method_*`. Nothing here is typed from the source, so
this file cannot drift from the device the way a hand-kept list would.

Which run
---------
By default the run is the reach run, `reach.run_json` in
`Documentation/receipts/s4-run-reach-v1.json`: the run M4's numerator is tied
to, which `check_run_reach.py` holds fresh for the tree. Until 2026-10-04 the
default was a control probe of 2026-09-21, and M7 read a runtime two weeks old
while every gate passed (`Documentation/decisions/2026-10-04-S5-user-rulings.md`,
ruling 1).

The file records the run's sha256, `runtime/sa2_d3d9.c`'s when it was written,
and the runtime digest the run was measured on: the reach receipt's
`manifest.runtime_sha256` when the run is the reach run (`inputs.run_runtime_sha256`,
None for any other probe run). That digest is the run's, not the tree's at the
moment the file was written; a status written after the runtime moved, from a
run made before, would otherwise carry the new digest beside the old run, and
`Documentation/scripts/check_m7_inputs.py` could not tell.

`tools/s4/m7_inputs.py` calls `write()` first, so one command regenerates M7,
and the gate calls `build()` to re-derive the file from the run and compare it
with what was written.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/device_status.py
  .venv/bin/python tools/s4/device_status.py --run private/generated/s4/run/<probe>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

REACH_REL = "Documentation/receipts/s4-run-reach-v1.json"
REACH = C.ROOT / REACH_REL
DEFAULT_OUT = C.GEN / "s4" / "device-status.json"


def reach_run() -> Path:
    """The reach run the reach receipt names (`reach.run_json`)."""
    if not REACH.is_file():
        raise SystemExit(f"{REACH_REL} is absent; run "
                         "tools/s4/reach_receipt.py --name reach --replay boot --write first")
    rel = (json.loads(REACH.read_text()).get("reach") or {}).get("run_json")
    if not rel:
        raise SystemExit(f"{REACH_REL} names no reach.run_json")
    return C.ROOT / rel


def run_runtime(run: Path, root: Path) -> tuple[str | None, str | None]:
    """(the runtime digest `run` was measured on, where that is recorded): the reach
    receipt's `manifest.runtime_sha256` when `run` is the reach run it names, else
    (None, None). No other probe run records the runtime it ran."""
    reach = root / REACH_REL
    if not reach.is_file():
        return None, None
    doc = json.loads(reach.read_text())
    named = (doc.get("reach") or {}).get("run_json")
    if not named or (root / named).absolute() != run.absolute():
        return None, None
    return (doc.get("manifest") or {}).get("runtime_sha256"), \
        f"{REACH_REL} manifest.runtime_sha256"


def build(run: Path, root: Path = C.ROOT) -> dict:
    """The device status of `run` in the tree at `root`: what `write()` writes, but for
    the time it was written."""
    if not run.is_file():
        raise SystemExit(f"no probe run at {run}; run tools/s4/reach_receipt.py --name reach "
                         "--replay boot --write (or tools/s4/d3d9_break.py) first")
    d = json.loads(run.read_text())
    x = d.get("d3d9")
    if not x or "implemented" not in x:
        raise SystemExit(f"{run} has no d3d9 section with an `implemented` table; it was "
                         f"written by a probe older than this tool")

    rows = x["implemented"]
    by_iface: dict[str, dict] = {}
    for r in rows:
        b = by_iface.setdefault(r["interface"], {"named": 0, "implemented": 0, "refuses": 0,
                                                 "out_param_unfilled": 0, "capability": 0})
        b["named"] += 1
        if r["implemented"]:
            b["implemented"] += 1
        else:
            b["refuses"] += 1
            if "out" in (r.get("flags") or ""):
                b["out_param_unfilled"] += 1
        if "cap" in (r.get("flags") or ""):
            b["capability"] += 1

    named = len(rows)
    implemented = sum(1 for r in rows if r["implemented"])
    refuses = named - implemented
    capability = sum(1 for r in rows if "cap" in (r.get("flags") or ""))
    cap_impl = sum(1 for r in rows if r["implemented"] and "cap" in (r.get("flags") or ""))

    surface = x.get("dynamic_surface", [])
    answers = x.get("answers", {})
    rel = run.absolute().relative_to(root.absolute()).as_posix()
    run_digest, run_digest_from = run_runtime(run, root)

    return {
        "schema": "sa2-recomp-measurement/v1",
        "id": "s4-device-status",
        "question": "which d3d9 methods does this runtime's recording device implement, "
                    "and which did one run of the guest actually call?",
        "inputs": {
            "d3d9_c_at_write": {"path": "runtime/sa2_d3d9.c",
                                "sha256": C.sha256(root / "runtime" / "sa2_d3d9.c")},
            "run_runtime_sha256": run_digest,
            "run_runtime_from": run_digest_from,
            "probe_run": {"path": rel, "sha256": C.sha256(run)},
            "binary_sha256": None,
        },
        "commands": ["source tools/env.sh",
                     ".venv/bin/python tools/s4/reach_receipt.py --name reach --replay boot --write",
                     f".venv/bin/python tools/s4/device_status.py --run {rel}"],
        "what_implemented_means": (
            "A named (interface, slot) is *implemented* when calling it returns to the "
            "guest. It is *refused* when the call is recorded and then raises a trap "
            "naming the interface, the method, the caller and the arguments. A slot the "
            "table does not name is neither: it refuses without a name and without a "
            "guessed argument count, because popping a guessed count corrupts the "
            "caller's frame rather than answering wrongly."),
        "what_implemented_does_not_mean": (
            "It does not mean the answer is right. This is a no-op device: it draws "
            "nothing, presents nothing and stores nothing a renderer would store. Of the "
            f"{implemented} implemented methods, {cap_impl} answer a capability query with "
            "a value this runtime invented, and those are flagged `cap` in the table "
            "below and counted separately in `answers.invented` for the run. A later "
            "stage that reads this file as a renderer's coverage would be reading it "
            "wrongly."),
        "implements": {
            "named_methods": named,
            "implemented": implemented,
            "refuses": refuses,
            "capability_methods": capability,
            "capability_methods_answered": cap_impl,
            "by_interface": by_iface,
        },
        "run": {
            "device_enabled": x.get("device_enabled"),
            "strict_caps": x.get("strict_caps"),
            "shader_model": x.get("shader_model"),
            "method_calls": x.get("method_calls"),
            "distinct_methods": x.get("distinct_methods"),
            "objects_built": x.get("objects_built"),
            "guest_bytes_allocated": x.get("guest_bytes_allocated"),
            "answers": answers,
            "answer_classes_mean": {
                "refused": "recorded, then trapped by name",
                "no-op-success": "recorded; there was nothing to answer and S_OK came back",
                "from-guest": "answered from a value the guest itself supplied",
                "invented": "answered with a value this runtime invented",
                "fabricated-object": "answered by fabricating a COM object in guest memory",
                "d3d9-default-never-set": "a Get with no matching Set; D3D9's documented "
                                          "default, which is a weaker claim than from-guest",
            },
            "caps_fields_written": x.get("caps_fields_written", []),
            "dynamic_surface": surface,
            "trap": d.get("trap", {}).get("note"),
            "winmain_loop": d.get("winmain_loop", {}),
        },
        "table": rows,
    }


def write(run: Path, out: Path = DEFAULT_OUT, root: Path = C.ROOT) -> dict:
    """Write the device status of `run` to `out` and return it."""
    doc = {**build(run, root), "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    imp, ran = doc["implements"], doc["run"]
    print(f"named {imp['named_methods']}, implemented {imp['implemented']}, refuses "
          f"{imp['refuses']}, capability-answered {imp['capability_methods_answered']}; the "
          f"run called {ran['distinct_methods']} distinct methods in {ran['method_calls']} calls")
    print(f"-> {out}")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=None,
                    help="a probe run; default the reach run the reach receipt names")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    write(args.run.absolute() if args.run else reach_run(), args.out)


if __name__ == "__main__":
    main()
