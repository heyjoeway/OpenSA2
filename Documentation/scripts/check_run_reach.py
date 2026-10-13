#!/usr/bin/env python3
"""Gate: the recorded run reach is current, and it has not silently fallen.

What this gate is for
---------------------
"369 WinMain iterations and 378 frames" steered S4 from slice 12 onward. On
2026-09-21 it became 1 iteration and **all 23 gates passed**, because the
run's reach was neither recorded nor checked. The cause was a generated build
input: `mb_edges.<tag>.c` lost a named dispatch exception that had only ever
existed as a `--allow-target` argument on a command line, so the fall-through
into 0x00826f60 stopped dispatching and the guest stopped in CRI's colour
conversion.

This gate is the cheap half of the answer. It does **not** re-run the guest on
every push -- that is minutes, and a gate nobody can afford to run is a gate
that gets skipped. It checks three things that cost milliseconds:

1. **Staleness.** `Documentation/receipts/s4-run-reach-v1.json` records the
   build inputs that produced its number. If any of them differs from the
   inputs as they stand now, the receipt describes a build that no longer
   exists, and it must be re-run before the push passes. This is what would
   have caught the regression: the edge table's hash changed, so the receipt
   would have gone stale in the same commit.
2. **Direction.** A reach lower than the last recorded one fails unless the
   receipt carries a `decrease_reason`. Going backwards can be right -- a
   correctness fix that stops the guest earlier is still a fix -- but it is
   never right *silently*.
3. **Fold code.** The objects the reach links were folded by the fold code in
   the tree and compiled with its compile commands: the stamp
   `tools/s4/rebuild_image.py` writes beside them (the manifest's
   `generated.fold_code`) must equal the tree's fold code now
   (`rebuild_image.fold_code`). Staleness alone cannot say this: the objects
   are digested by name and size, and re-running the reach after an edit to
   the fold code records the new code over the old objects. Only a rebuild
   clears it (review of the S5 speed change, SHOULD-FIX 3).

A full reproduction from a clean clone belongs at stage gates, not here.

Re-running it
-------------

    source tools/env.sh
    .venv/bin/python tools/s4/reach_receipt.py --name reach --replay boot --write

Falsification runs on every pass.

Usage: .venv/bin/python Documentation/scripts/check_run_reach.py
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

RECEIPT = ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
HISTORY = ROOT / "Documentation" / "run-reach-history.json"
IMPORT_STATUS = ROOT / "private" / "generated" / "import-status.json"

# The fields whose difference means "this receipt describes a different build".
# Nested as (section, key); an empty key means the whole section.
WATCHED = [
    ("", "tag"),
    ("", "main"),
    ("", "exe_sha256"),
    ("", "runtime_sha256"),
    ("generated", "mb_edges_c_sha256"),
    ("generated", "lifted_extras_sha256"),
    ("generated", "lifted_module_digest"),
    ("generated", "lifted_module_count"),
    ("generated", "whole_image_module_digest"),
    ("generated", "fold_code"),
    ("tracked", ""),
    ("", "environment"),
    ("", "probe_args"),
    ("", "pipeline"),
]


def problems(receipt_path: Path | None = None,
             live: dict | None = None, status_doc: dict | None = None,
             fold_now: dict | None = None) -> list[str]:
    """Every reason the reach receipt does not describe the tree, as a sentence. `live`
    is the tree's manifest (default `reach_receipt.manifest()`), `status_doc` M4's
    import status, and `fold_now` the tree's fold code (default
    `rebuild_image.fold_code()`)."""
    out: list[str] = []
    path = receipt_path or RECEIPT
    if not path.exists():
        return [f"{path.relative_to(ROOT)} does not exist; the run's reach is "
                "unrecorded, which is the state that let it fall from 369 "
                "iterations to 1 unnoticed"]
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"the reach receipt is not JSON: {e}"]
    if doc.get("id") != "s4-run-reach-v1":
        out.append(f"receipt id is {doc.get('id')!r}, expected 's4-run-reach-v1'")

    recorded = doc.get("manifest") or {}
    reach = doc.get("reach") or {}
    for field in ("iterations", "import_calls", "frames_presented"):
        if not isinstance(reach.get(field), int):
            out.append(f"reach.{field} is {reach.get(field)!r}, not a number")

    if live is None:
        try:
            import reach_receipt  # noqa: E402
            live = reach_receipt.manifest()
        except SystemExit as e:
            return out + [f"the manifest could not be computed: {e}"]
        except Exception as e:                       # noqa: BLE001
            return out + [f"the manifest could not be computed: {e!r}"]

    for section, key in WATCHED:
        a = recorded.get(section, {}) if section else recorded
        b = live.get(section, {}) if section else live
        if key:
            if a.get(key) != b.get(key):
                out.append(
                    f"the reach receipt is stale: {(section + '.' if section else '') + key} "
                    f"was {a.get(key)!r} when the run was measured and is {b.get(key)!r} now. "
                    "Re-run tools/s4/reach_receipt.py --replay boot --write before pushing.")
        elif a != b:
            out.append(f"the reach receipt is stale: the {section!r} section differs "
                       "from the current inputs. Re-run tools/s4/reach_receipt.py --replay boot --write.")

    # The fold code that made the objects (their stamp, as the manifest read it) is the
    # tree's. A reach re-run cannot clear this; a rebuild can (module docstring, 3).
    try:
        import rebuild_image  # noqa: E402
        now = rebuild_image.fold_code() if fold_now is None else fold_now
        out.extend(rebuild_image.fold_code_problems(
            (live.get("generated") or {}).get("fold_code") or {}, now))
    except Exception as e:                           # noqa: BLE001
        out.append(f"the fold code could not be compared with the objects' stamp: {e!r}")

    # M4's numerator is the import table this run dumped, and nothing else.
    # It was a table a probe dumped on 2026-09-20, which read 122 implemented
    # while a 2026-09-21 dump already read 151, and no gate compared them.
    table = reach.get("import_table") or {}
    status = status_doc if status_doc is not None else (
        json.loads(IMPORT_STATUS.read_text()) if IMPORT_STATUS.is_file() else None)
    if not table.get("path") or not table.get("sha256"):
        out.append("the reach receipt records no import table, so M4 has no current source. "
                   "Re-run tools/s4/reach_receipt.py --replay boot --write.")
    elif status is None:
        out.append(f"{IMPORT_STATUS.relative_to(ROOT)} is absent; run tools/s4/import_status.py")
    elif (status.get("source"), status.get("source_sha256")) != (table["path"], table["sha256"]):
        out.append(f"M4 is read from {status.get('source')!r} and the reach run dumped "
                   f"{table['path']!r}: run tools/s4/import_status.py after the reach receipt")

    # Direction. The history is a tracked list of the reaches accepted so far.
    if HISTORY.exists() and isinstance(reach.get("iterations"), int):
        hist = json.loads(HISTORY.read_text()).get("accepted", [])
        best = max((h.get("iterations", 0) for h in hist), default=0)
        if reach["iterations"] < best and not str(doc.get("decrease_reason", "")).strip():
            out.append(
                f"reach fell from {best} iterations to {reach['iterations']} with no "
                "`decrease_reason` in the receipt. A decrease can be correct -- a fix "
                "that stops the guest earlier is still a fix -- but never silently.")
    return out


# How many falsifications ran, set by falsifications() from the cases it
# actually built. It was a literal in the summary line, which is the same
# defect this project catalogues as "a counter incremented before the thing it
# counts": adding a case without touching the literal would have printed a
# number that was simply wrong, and a gate that misreports its own rigour is
# worse than one that does not report it.
RAN = 0


def falsifications() -> list[str]:
    if not RECEIPT.exists():
        return ["the reach receipt does not exist, so no mutation of it can be "
                "shown to fail; the falsifications cannot run"]
    doc = json.loads(RECEIPT.read_text())
    try:
        import reach_receipt  # noqa: E402
        import rebuild_image  # noqa: E402
        live = reach_receipt.manifest()
        tree = rebuild_image.fold_code()
    except Exception as e:                            # noqa: BLE001
        return [f"the manifest or the tree's fold code could not be computed, so the "
                f"falsifications cannot run: {e!r}"]

    def mutate(name, f):
        d = copy.deepcopy(doc)
        f(d)
        return name, d

    cases = [
        mutate("the edge table's hash changed",
               lambda d: d["manifest"]["generated"].__setitem__(
                   "mb_edges_c_sha256", "0" * 64)),
        mutate("the lifted-extras record changed",
               lambda d: d["manifest"]["generated"].__setitem__(
                   "lifted_extras_sha256", "0" * 64)),
        mutate("the lifted module set changed",
               lambda d: d["manifest"]["generated"].__setitem__(
                   "lifted_module_digest", "0" * 64)),
        mutate("the tracked exception record changed",
               lambda d: d["manifest"]["tracked"].__setitem__(
                   "Documentation/dispatch-exceptions.json", "0" * 64)),
        mutate("the whole-image module set changed",
               lambda d: d["manifest"]["generated"].__setitem__(
                   "whole_image_module_digest", "0" * 64)),
        mutate("the tag changed",
               lambda d: d["manifest"].__setitem__("tag", "next+arms")),
        mutate("the driver changed",
               lambda d: d["manifest"].__setitem__("main", "harness/import_probe.c")),
        mutate("a runtime source changed",
               lambda d: d["manifest"].__setitem__("runtime_sha256", "0" * 64)),
        mutate("M4 would read another import table",
               lambda d: d["reach"]["import_table"].__setitem__("sha256", "0" * 64)),
        mutate("the executable changed",
               lambda d: d["manifest"].__setitem__("exe_sha256", "0" * 64)),
        mutate("the environment changed",
               lambda d: d["manifest"]["environment"].__setitem__(
                   "SA2_IMPORT_BUDGET", "1")),
        # A value the receipt does not hold. This mutation set [] until
        # 2026-09-22, when [] became the true value, and a mutation that
        # equals the recorded value falsifies nothing.
        mutate("the probe arguments changed",
               lambda d: d["manifest"].__setitem__(
                   "probe_args", list(d["manifest"].get("probe_args") or []) + ["--drive-loop"])),
        mutate("a pipeline tool changed",
               lambda d: d["manifest"]["pipeline"].__setitem__(
                   "tools/s4/mb_edges.py", "0" * 64)),
        mutate("the fold code changed (a pipeline pin)",
               lambda d: d["manifest"]["pipeline"].__setitem__(
                   "tools/s2/corpus_run/speed_rewrite.py", "0" * 64)),
        mutate("the objects were rebuilt by other fold code since the run",
               lambda d: d["manifest"]["generated"].__setitem__(
                   "fold_code", {"tools/s2/corpus_run/speed_rewrite.py": "0" * 64})),
        mutate("the reach is not a number",
               lambda d: d["reach"].__setitem__("iterations", "lots")),
    ]
    # The receipt and the manifest agree, and the objects' stamp is not the tree's fold
    # code: only check 3 can see it, so it must name the fold code.
    other = {rel: "0" * 64 for rel in (live.get("generated") or {}).get("fold_code") or
             {"tools/s2/corpus_run/speed_rewrite.py": ""}}
    stamped = copy.deepcopy(doc)
    stamped.setdefault("manifest", {}).setdefault("generated", {})["fold_code"] = other
    stamped_live = copy.deepcopy(live)
    stamped_live.setdefault("generated", {})["fold_code"] = other
    # The same with only one compile command other than the tree's, every file the
    # tree's: the stamp holds each tool's command as data (rebuild_image COMPILE_STEPS).
    step = rebuild_image.COMPILE_STEPS[0]
    recompiled = dict(tree, **{step: f"{tree.get(step)} -O0"})
    compiled = copy.deepcopy(doc)
    compiled.setdefault("manifest", {}).setdefault("generated", {})["fold_code"] = recompiled
    compiled_live = copy.deepcopy(live)
    compiled_live.setdefault("generated", {})["fold_code"] = recompiled
    specific = [("the objects were folded by fold code that is not the tree's",
                 stamped, stamped_live),
                ("the objects were compiled with a command that is not the tree's",
                 compiled, compiled_live)]
    global RAN
    RAN = len(cases) + len(specific)

    survived = []
    with tempfile.TemporaryDirectory() as td:
        for name, d in cases:
            p = Path(td) / "receipt.json"
            p.write_text(json.dumps(d))
            if not problems(p, live):
                survived.append(name)
        for name, d, stand_in in specific:
            p = Path(td) / "receipt.json"
            p.write_text(json.dumps(d))
            if not any("tools/s4/rebuild_image.py" in x for x in problems(p, stand_in)):
                survived.append(name)
    return [f"falsification survived: {s} -- the gate accepted a receipt it "
            "should have called stale" for s in survived]


def main() -> int:
    failed = falsifications()
    if failed:
        for f in failed:
            print(f"  {f}")
        print(f"RUN_REACH_FAILED falsifications={len(failed)}")
        return 1
    probs = problems()
    if probs:
        for p in probs:
            print(f"  {p}")
        print(f"RUN_REACH_FAILED problems={len(probs)}")
        return 1
    r = json.loads(RECEIPT.read_text())["reach"]
    print(f"RUN_REACH_OK iterations={r['iterations']} import_calls={r['import_calls']} "
          f"frames={r['frames_presented']} stale=0 falsifications={RAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
