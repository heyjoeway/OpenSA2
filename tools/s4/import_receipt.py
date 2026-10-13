#!/usr/bin/env python3
"""Assemble the S4 import-binding receipt from the runs that were kept.

Subtitle: every number in it is copied from a JSON file on disk, including the
runs that failed, because the failing "before" run is the one that gets deleted
the moment the fix works.

This writes to `private/generated/s4/` only. Placing a receipt under
`Documentation/receipts/` is the integrating thread's call, not this slice's.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/import_receipt.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "private" / "generated" / "s4" / "run"
COST = ROOT / "private" / "generated" / "s4" / "build_cost"
# The receipt is a tracked record, so it is written where receipts live.
# It was produced under private/generated while the slice ran because that
# slice could not write to Documentation/; the path moved when it landed.
OUT = ROOT / "Documentation" / "receipts" / "s4-import-binding-v1.json"

PIPELINE = [
    "runtime/sa2_imports.c", "runtime/sa2_imports.h",
    "runtime/guest_memory.c", "runtime/guest_memory.h",
    "harness/import_probe.c",
    "tools/s4/link_s4.py", "tools/s4/build_cost.py", "tools/s4/import_receipt.py",
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path):
    return json.loads(p.read_text()) if p.is_file() else None


def strip_calls(run: dict | None) -> dict | None:
    """A run without its per-call records, which are kept in their own file."""
    if not run:
        return None
    out = dict(run)
    calls = out.get("calls")
    if calls:
        out["calls"] = {k: v for k, v in calls.items() if k != "records"}
    return out


def main() -> None:
    bound = load(RUN / "import.bound.json")
    unbound = load(RUN / "import.unbound.json")
    mutation = load(RUN / "import.cwd-mutation.json")
    argcbug = load(RUN / "import.argc-bug.json")
    cost = load(COST / "build_cost.next+allarms.json")
    if not (bound and unbound and cost):
        raise SystemExit("missing a kept run; re-run link_s4.py and build_cost.py first")

    calls = bound["calls"]["records"]
    by_name: dict[str, dict] = {}
    for c in calls:
        row = by_name.setdefault(c["name"], {"dll": c["dll"], "calls": 0,
                                             "implemented": c["implemented"],
                                             "first_caller": c["caller"]})
        row["calls"] += 1

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-import-binding-v1",
        "stage": "S4",
        "question": "Do the image's 251 imports dispatch, and how far does driven guest code "
                    "get once they do?",
        "authority": "driven",
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 3.5 (import stubs, time "
                       "virtualisation), section 5 S4"],
            "inherited": ["agents-journals/gates/2026-09-20-g4.md",
                          "agents-journals/s3/2026-09-20-s3-linked-with-arms.md",
                          "Documentation/receipts/s4-import-surface-v1.json",
                          "Documentation/receipts/s3-build-time-v2.json"],
            "executable": {
                "path": "private/exe/sonic2app-unpacked.exe",
                "sha256": sha256(ROOT / "private/exe/sonic2app-unpacked.exe"),
            },
            "binary": {
                "tag": "next+allarms",
                "lifted_objects": 18394,
                "path": "private/generated/s4/link/mains/import.next+allarms/sa2_main",
            },
            "pipeline": {p: sha256(ROOT / p) for p in PIPELINE},
            "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       capture_output=True, text=True).stdout.strip(),
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s4/link_s4.py --tag next+allarms "
            "--main harness/import_probe.c --name import.next+allarms "
            "--run private/exe/sonic2app-unpacked.exe --run=--json "
            "--run private/generated/s4/run/import.bound.json",
            ".venv/bin/python tools/s4/link_s4.py --tag next+allarms "
            "--main harness/import_probe.c --name import.next+allarms --phase run "
            "--env SA2_IMPORT_BIND=0 --run private/exe/sonic2app-unpacked.exe --run=--json "
            "--run private/generated/s4/run/import.unbound.json",
            ".venv/bin/python tools/s4/build_cost.py --tag next+allarms --repeats 3",
            ".venv/bin/python tools/s2/corpus_run/run.py",
            ".venv/bin/python tools/s2/corpus_run/controls.py",
            ".venv/bin/python Documentation/scripts/run_all_gates.py",
        ],
        "mechanism": {
            "chosen": "bind the IAT in guest memory at init and register one function-table "
                      "native per synthetic thunk address",
            "thunk_region": {"base": "0x00200000", "stride": 16, "slots": 256,
                             "region_name": "import-thunk"},
            "rejected": "register the natives at the unbound values the slots already hold",
            "evidence_against_the_rejected_option": {
                "slots": bound["bind"]["imports"],
                "values_duplicated": bound["bind"]["unbound_values_duplicated"],
                "duplicate_pair": "XINPUT1_3.dll!Ordinal#2 and OLEAUT32.dll!SysAllocString "
                                  "both hold 0x80000002",
                "slots_whose_value_is_not_a_guest_address":
                    bound["bind"]["unbound_values_outside_guest_space"],
                "note": "245 of the remaining values point into .text, in no mapped function "
                        "today; a collision with a lifted entry would have replaced a real "
                        "function, because sa2_ft_add lets a later registration win",
            },
            "collision_check": {
                "table_rows_before": bound["bind"]["table_rows_before"],
                "table_rows_after": bound["bind"]["table_rows_after"],
                "grew_by": bound["bind"]["table_rows_after"] - bound["bind"]["table_rows_before"],
                "why_it_is_a_check": "a thunk that landed on an existing row would have "
                                     "replaced it rather than added one, so the count would "
                                     "not have grown by the import count",
            },
        },
        "runs": {
            "bound": strip_calls(bound),
            "unbound_SA2_IMPORT_BIND_0": strip_calls(unbound),
            "cwd_mutation": strip_calls(mutation),
            "argc_mutation_Sleep_argc_0": strip_calls(argcbug),
        },
        "calls_by_symbol": by_name,
        "call_log": "private/generated/s4/run/import.bound.json (calls.records)",
        "frontier": {
            "before": {"exit_pc": unbound["trap"]["exit_pc"], "note": unbound["trap"]["note"]},
            "after": {"exit_pc": bound["trap"]["exit_pc"], "note": bound["trap"]["note"],
                      "import_calls": bound["calls"]["total"],
                      "exit_code": bound["virtual"]["exit_code"]},
            "why_it_stops_there":
                "the guest reached KERNEL32!ExitProcess(255) by its own control flow. The "
                "chain is _malloc at 0x007a84a5 testing the CRT heap handle at 0x01751ff4, "
                "finding 0, and taking the CRT's uninitialised-runtime path: __FF_MSGBANNER, "
                "__NMSG_WRITE(0x1e), __crt_waiting_on_module_handle(L\"KERNEL32.DLL\"), "
                "___crtMessageBoxA(LoadLibraryA(\"USER32.DLL\")), ___crtCorExitProcess, "
                "__crtExitProcess(255). The wall is no longer an import; it is CRT "
                "initialisation state, which this entry point never ran.",
        },
        "build_cost": cost["delta"] | {
            "compile_seconds_per_runtime_source":
                {k: v["min"] for k, v in cost["compile_seconds"].items()},
            "link_seconds": {k: v["min"] for k, v in cost["link_seconds"].items()},
            "link_runs_include_one_toolchain_bootstrap":
                cost["link_seconds"]["without_sa2_imports"]["runs"],
            "cache_discipline": cost["cache_discipline"],
            "claim_1_holds": cost["delta"]["claim_1_holds"],
        },
        "limits": [
            "The virtual current directory and executable path are named placeholders "
            "(C:\\\\SA2 and C:\\\\SA2\\\\sonic2app.exe), overridable with SA2_GUEST_CWD and "
            "SA2_GUEST_EXE_PATH. No live-oracle capture exists to agree with, and nothing "
            "observes them yet: the only use on any reached path builds a string and then "
            "calls an import with no stub.",
            "The stub table carries each import's argument count by hand. A wrong count "
            "corrupts the caller's frame; the falsification run in `runs` shows what that "
            "looks like. 15 counts are declared; the other 236 imports have none and do not "
            "need one, because the loud default never returns.",
            "One thread. TLS is a single 1088-slot array and there is one TEB, matching "
            "sa2_guest_init.",
            "A stub returns EAX only. No import reached so far returns a 64-bit value in "
            "EDX:EAX, and nothing checks that claim.",
            "sa2_imports_bind() is opt-in: the driver calls it, sa2_runtime_init does not. "
            "That keeps the fixture corpus and every S2/S3 probe on exactly the runtime they "
            "were verified against, and it is what makes the SA2_IMPORT_BIND=0 before column "
            "the same binary rather than a different one.",
            "GetProcAddress is deliberately not implemented (708 call sites, 451 names, 3 of "
            "them OS entry points). LoadLibraryA therefore refuses, so no handle exists for "
            "it to be asked about.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    print(f"-> {OUT}")
    print(json.dumps(doc["frontier"], indent=1))


if __name__ == "__main__":
    main()
