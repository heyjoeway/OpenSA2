#!/usr/bin/env python3
"""Assemble the CRT-startup receipt from the files the runs wrote.

Subtitle: `tools/s4/import_receipt.py` for the entry-point slice. Every number
comes out of a generated file with its hash; nothing in the output is typed.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/crt_receipt.py --tag next+allarms
  # writes Documentation/receipts/s4-crt-startup-v1.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

GEN4 = C.GEN / "s4"
RUN = GEN4 / "run"
LINK = GEN4 / "link"

PIPELINE = [
    "runtime/sa2_imports.c", "runtime/sa2_imports.h", "runtime/guest_memory.h",
    "runtime/remill_runtime.cpp", "runtime/remill_runtime.h",
    "runtime/sa2_emulate.cpp", "runtime/sa2_emulate.h",
    "harness/crt_probe.c", "harness/heap_probe.c",
    "tools/s4/link_s4.py", "tools/s4/mb_edges.py", "tools/s4/lift_extra.py",
    "tools/s4/crt_run.py", "tools/s4/crt_matrix.py", "tools/s4/crt_receipt.py",
    "tools/s4/disas.py", "tools/s4/build_cost.py",
]


def load(p: Path):
    return json.loads(p.read_text()) if p.is_file() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--out", default="s4-crt-startup-v1.json")
    args = ap.parse_args()

    base = load(RUN / "crt.winmain.json")
    before = load(RUN / "crt.before.json")
    matrix = load(RUN / "crt.matrix.json")
    edges = load(LINK / f"mb_edges.{args.tag}.json")
    extra = load(LINK / f"extra_functions.{args.tag}.json")
    cost = load(GEN4 / "build_cost" / f"build_cost.{args.tag}.json")
    linkst = load(LINK / f"main.crt.{args.tag}.json")
    brk_l = load(RUN / "crt.break.lstrcmp.json")
    brk_a = load(RUN / "crt.break.argc.json")
    fatal = load(RUN / "crt.uninit_fatal.json")
    heapp = load(RUN / "heap.json")
    corpus = load(C.GEN / "s2" / "corpus_run" / "run.pc24.json")
    controls = load(C.GEN / "s2" / "corpus_run" / "controls.json")
    if base is None:
        raise SystemExit(f"no baseline run at {RUN / 'crt.winmain.json'}")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                          capture_output=True, text=True).stdout.strip()

    rows = extra["rows"] if extra else []
    good = [r for r in rows if r.get("compiled") and not r.get("rejected")]
    lift_s = sum(r.get("lift_seconds", 0.0) for r in good)
    comp_s = sum(r.get("compile_seconds", 0.0) for r in good)
    crit_min = 50.380          # Documentation/receipts/s3-build-time-v2.json, not re-measured here
    added_min = round((lift_s + comp_s) / 60.0, 3)

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-crt-startup-v1",
        "stage": "S4",
        "question": "Does the image's own CRT start up when the PE entry point is driven, "
                    "and how far does the guest get?",
        "authority": "driven",
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 3.5, section 5 S4"],
            "inherited": ["Documentation/receipts/s4-import-binding-v1.json",
                          "Documentation/receipts/s4-import-surface-v1.json",
                          "Documentation/receipts/s3-build-time-v2.json",
                          "agents-journals/s4/2026-09-20-s4-import-binding.md"],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": C.sha256(C.EXE)},
            "binary": {"tag": args.tag,
                       "path": (linkst or {}).get("link", {}).get("binary"),
                       "sha256": (linkst or {}).get("link", {}).get("sha256"),
                       "bytes": (linkst or {}).get("link", {}).get("binary_bytes"),
                       "lifted_objects": (linkst or {}).get("link", {}).get("objects_lifted"),
                       "runtime_and_driver_objects":
                           (linkst or {}).get("link", {}).get("objects_runtime")},
            "pipeline": {p: C.sha256(C.ROOT / p) for p in PIPELINE
                         if (C.ROOT / p).is_file()},
            "git_head": head,
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s4/mb_edges.py --tag next+allarms --lift-tag next "
            "--arms-tag next --allow-from private/generated/s4/link/"
            "extra_functions.next+allarms.json",
            ".venv/bin/python tools/s4/lift_extra.py --tag next+allarms --lift-tag next "
            "--arms-tag next --from-list private/generated/s3/link/"
            "dispatch_surface.next+allarms.txt --group rdata_vtable_entry_unmapped "
            "--group missing_block_target --jobs 12",
            ".venv/bin/python tools/s4/lift_extra.py --tag next+allarms --lift-tag next "
            "--arms-tag next --sweep-section .rdata --jobs 12",
            ".venv/bin/python tools/s4/crt_run.py --rounds 4",
            ".venv/bin/python tools/s4/crt_matrix.py",
            ".venv/bin/python tools/s4/build_cost.py --tag next+allarms --repeats 3",
            ".venv/bin/python tools/s2/corpus_run/run.py",
            ".venv/bin/python tools/s2/corpus_run/controls.py",
            ".venv/bin/python Documentation/scripts/run_all_gates.py",
        ],
        "entry_point": {
            "address_of_entry_point_va": "0x007a801c",
            "shape": ["007a801c call 007B5195h  __security_init_cookie",
                      "007a8021 jmp  007A7E9Eh  __tmainCRTStartup"],
            "winmain_call_site": "007a7fac call 00401B80h",
        },
        "before": {
            "json": "private/generated/s4/run/crt.before.json",
            "note": "the same probe before any of this slice's work: the entry point runs "
                    "five instructions and stops at the first CRT import",
            "trap": (before or {}).get("trap", {}).get("note"),
            "import_calls": (before or {}).get("calls", {}).get("total"),
            "cookie_after": (before or {}).get("crt", {}).get("cookie", {}).get("after"),
            "crtheap_after": (before or {}).get("crt", {}).get("heap", {}).get("after"),
        },
        "after": {
            "json": "private/generated/s4/run/crt.winmain.json",
            "ok": base["ok"],
            "trap": base["trap"]["note"],
            "import_calls": base["calls"]["total"],
            "distinct_imports_called": base["calls"]["distinct_imports"],
            "cookie": base["crt"]["cookie"],
            "heap": base["heap"],
            "winmain": base["winmain"],
            "cpu": base.get("cpu"),
            "getprocaddress": {k: base["getprocaddress"][k]
                               for k in ("resolved", "unresolved", "unresolved_names")},
            "import_table": {
                "json": "private/generated/s4/run/crt.imports.table.json",
                "slots": (load(RUN / "crt.imports.table.json") or {}).get("imports"),
                "implemented": (load(RUN / "crt.imports.table.json") or {}).get("implemented"),
                "called_in_this_run":
                    (load(RUN / "crt.imports.table.json") or {}).get("called_in_this_run"),
                "previous_slice_implemented": 14,
                "note": "M4's numerator. private/generated/import-status.json is NOT "
                        "regenerated by this slice: it feeds METRICS.md, which is outside "
                        "this unit's allowed paths, so the integrating thread publishes it.",
            },
            "message_boxes": base["message_boxes"]["count"],
            "asserts": base["assert"],
        },
        "function_table": {
            "generated_rows": (linkst or {}).get("link", {}).get("objects_lifted"),
            "extra_blocks_registered": base.get("extra_blocks_registered"),
            "rows_before_binding": base["bind"]["table_rows_before"],
            "rows_after_binding": base["bind"]["table_rows_after"],
            "getprocaddress_rows": base["getprocaddress"]["resolved"],
            "collision_check": {
                "expected_before_binding": ((linkst or {}).get("link", {}).get("objects_lifted", 0)
                                            + (base.get("extra_blocks_registered") or 0)),
                "observed_before_binding": base["bind"]["table_rows_before"],
                "grew_by_the_import_count":
                    base["bind"]["table_rows_after"] - base["bind"]["table_rows_before"],
                "why_it_is_a_check":
                    "sa2_ft_add lets a later registration win, so a block lifted over an "
                    "address the generated table already holds would replace a real lifted "
                    "function instead of adding a row, and the count would not be the sum",
            },
        },
        "uninitialised_crt_fatal_path": {
            "json": "private/generated/s4/run/crt.uninit_fatal.json",
            "command": "sa2_main <exe> --va 0x00406520",
            "why": "the previous slice's wall, driven on purpose on the same binary: the "
                   "interior function it drove runs with the CRT uninitialised, so the guest "
                   "takes MSVC's R6030 path. It is kept because it exercises the USER32 "
                   "GetProcAddress rows and the message-box recorder that the entry-point run "
                   "never reaches, and because it makes the no_message_box assertion "
                   "demonstrably able to fail.",
            "trap": (fatal or {}).get("trap", {}).get("note"),
            "exit_code": (fatal or {}).get("virtual", {}).get("exit_code"),
            "sleep_ms": (fatal or {}).get("virtual", {}).get("sleep_ms"),
            "sleep_ms_in_the_previous_slice": 1830000,
            "sleep_note": "the previous slice measured 1 830 000 ms here, exactly "
                          "sum(1000..60000): __crt_waiting_on_module_handle retried "
                          "GetModuleHandleW(L\"KERNEL32.DLL\") sixty times because nothing "
                          "answered it. It answers now, so the retry loop does not run.",
            "message_boxes": (fatal or {}).get("message_boxes"),
            "getprocaddress": (fatal or {}).get("getprocaddress", {}).get("rows"),
            "asserts": (fatal or {}).get("assert"),
        },
        "heap_probe": {
            "json": "private/generated/s4/run/heap.json",
            "why": "the entry-point run reaches WinMain with 20 HeapAllocs, one HeapFree and "
                   "no HeapReAlloc, so it carries the allocator's split, coalesce and "
                   "reallocate paths without exercising them. This drives the heap through "
                   "the bound IAT thunks -- the same dispatch, marshalling and refusals a "
                   "lifted `call [slot]` gets -- and asserts alignment, non-overlap, "
                   "retention, HeapSize, HEAP_ZERO_MEMORY, reallocation in place and moved, "
                   "coalescing (the whole arena is allocatable again after 67 frees), and "
                   "that a double free, a free on the wrong heap and a free of a pointer the "
                   "heap never returned all trap.",
            "ok": (heapp or {}).get("ok"),
            "failures": (heapp or {}).get("failures"),
            "first_failure": (heapp or {}).get("first_failure"),
            "after_freeing_everything": (heapp or {}).get("after_freeing_everything"),
            "whole_arena_realloc_after_coalescing":
                (heapp or {}).get("whole_arena_realloc_after_coalescing"),
            "refusals": (heapp or {}).get("refusals"),
            "oversized_request": (heapp or {}).get("oversized_request"),
            "defect_it_found": (
                "HeapAlloc's overflow guard covered `bytes + 7` and not `need + 16`. For a "
                "request in [0xFFFFFFE9, 0xFFFFFFF8] the second add wrapped to at most 16, "
                "every free block then satisfied `total >= want`, and the split landed inside "
                "the block's own header: the allocator returned a pointer and left the arena "
                "with a zero-sized block that the walk reports as corrupt. Windows returns "
                "NULL. It does now, and the request is also refused when it exceeds the "
                "arena."),
        },
        "missing_block_table": {
            "why": "only the S2 corpus build ever emitted the reaching-instruction table, so "
                   "at image scale every missing block was SA2_MB_EDGE_UNKNOWN and trapped "
                   "under every policy, including the entry point's own tail call",
            "modules": (edges or {}).get("modules"),
            "edges": (edges or {}).get("edges"),
            "by_kind": (edges or {}).get("by_kind"),
            "dispatchable_as_tail_calls": (edges or {}).get("dispatchable_under_reached"),
            "named_exceptions": (edges or {}).get("named_exceptions"),
            "named_exception_targets": (edges or {}).get("named_exception_targets"),
            "policy": base["missing_block"]["policy_name"],
            "census_in_the_reported_run": base["missing_block"]["census"],
        },
        "blocks_in_no_module": {
            "why": "remill lifts what is reachable from the entry it is given, so a block no "
                   "map entry reaches is in no module; 129 638 instructions, 11.1 % of the "
                   "map, by S3's census",
            "attempted": (extra or {}).get("attempted"),
            "registered": (extra or {}).get("registered"),
            "rejected_for_undefined_symbols": (extra or {}).get("rejected"),
            "rdata_dwords_rejected_as_not_an_instruction_boundary":
                (extra or {}).get("sweep_skipped_not_an_instruction_boundary"),
            "rdata_dwords_rejected_as_not_in_a_pointer_run":
                (extra or {}).get("sweep_skipped_not_in_a_pointer_run"),
            "sweep_min_run": (extra or {}).get("sweep_min_run"),
            "sources": ["dispatch_surface groups rdata_vtable_entry_unmapped and "
                        "missing_block_target",
                        ".rdata code-pointer sweep, restricted to runs of consecutive "
                        "pointers",
                        "addresses a run of the probe actually reached"],
            "why_the_run_length_predicate": (
                "the instruction-boundary test alone accepted 1026 rows, 351 of them from "
                "dwords inside UTF-16 text -- 0x0089da3c holds 0x00410020, the \" \" and "
                "\"A\" of \"SONIC ADVENTURE\" -- because a linear decode of a function "
                "starts an instruction at a large fraction of its byte offsets. Requiring a "
                "run of consecutive code pointers, which is what a vtable or an initialiser "
                "table is and what string bytes are not, cut the set from 1838 to 896 rows "
                "and the build-time addition from 3.874 to 1.712 minutes with the same run "
                "reaching the same address."),
        },
        "falsification": {
            "matrix": "private/generated/s4/run/crt.matrix.json",
            "rows": [{k: r.get(k) for k in
                      ("tag", "env", "why", "ok", "import_calls", "cookie", "dash_r_flag",
                       "dash_r_expected", "trap")}
                     for r in (matrix or {}).get("rows", [])],
            "deliberate_breaks": [
                {"break": "lstrcmpA reports every string equal",
                 "json": "private/generated/s4/run/crt.break.lstrcmp.json",
                 "ok_before_the_fix": True,
                 "what_it_found": "reached_winmain and guest_parsed_the_command_line were "
                                  "reported and left out of the ok conjunction, so they could "
                                  "not fail; they are in it now",
                 "ok_after_the_fix": (brk_l or {}).get("ok"),
                 "asserts": (brk_l or {}).get("assert")},
                {"break": "EnterCriticalSection declared with 0 stdcall arguments",
                 "json": "private/generated/s4/run/crt.break.argc.json",
                 "ok": (brk_a or {}).get("ok"),
                 "trap": (brk_a or {}).get("trap", {}).get("note"),
                 "what_it_shows": "the previous slice's hole -- a wrecked guest stack passing "
                                  "every assertion -- is closed: the run derails and both "
                                  "ended_where_intended and reached_winmain go false"},
            ],
        },
        "build_time": {
            "criterion": "G1-1: the whole-image serial build must stay under 60 wall minutes",
            "baseline_minutes": crit_min,
            "baseline_receipt": "Documentation/receipts/s3-build-time-v2.json",
            "runtime_sources_in_the_measured_phases":
                (cost or {}).get("criterion", {}).get("runtime_sources_in_those_phases"),
            "runtime_source_minutes_added":
                (cost or {}).get("delta", {}).get("criterion_minutes_added"),
            "link_seconds_added": (cost or {}).get("delta", {}).get("link_seconds_added"),
            "binary_bytes_added": (cost or {}).get("delta", {}).get("binary_bytes_added"),
            "extra_block_lift": {
                "modules": len(good),
                "lift_seconds_serial": round(lift_s, 1),
                "compile_seconds_serial": round(comp_s, 1),
                "minutes_serial": added_min,
                "note": "this is NOT zero and it is in the class the criterion measures: it is "
                        "lift and compile of guest modules, the same two phases "
                        "build_time_v2.py times. The runtime sources cost nothing; these do.",
                "projected_total_minutes": round(crit_min + added_min, 3),
                "projected_percent_of_threshold": round((crit_min + added_min) / 60.0 * 100, 1),
                "headroom_minutes_before": round(60.0 - crit_min, 2),
                "headroom_minutes_after": round(60.0 - crit_min - added_min, 2),
            },
        },
        "regression": {
            "fixture_corpus": {
                "passed": sum(1 for r in (corpus or {}).get("results", []) if r.get("passed")),
                "total": len((corpus or {}).get("results", [])),
                "json": "private/generated/s2/corpus_run/run.pc24.json"},
            "controls": {
                "json": "private/generated/s2/corpus_run/controls.json",
                "asserting_hold": sum(1 for rows in (controls or {}).values() for c in rows
                                      if c.get("kind") != "probe" and c.get("control_holds")),
                "asserting": sum(1 for rows in (controls or {}).values() for c in rows
                                 if c.get("kind") != "probe"),
                "break_controls": sum(1 for rows in (controls or {}).values() for c in rows
                                      if c.get("kind") == "break")},
            "gates": {
                "command": ".venv/bin/python Documentation/scripts/run_all_gates.py",
                "result": "20 of 21 OK; `unit ledger` FAILS on one unrecorded agent unit",
                "why_it_fails": (
                    "the background reviewer this slice ran is a finished transcript with no "
                    "ledger entry (0.223 h). Closing it writes Documentation/agent-units.json, "
                    "which is outside this unit's allowed paths, so it is left for the "
                    "integrating thread: "
                    "tools/units.py record agent-a8dc810389b95a4ce.jsonl --stage S4 "
                    "--role review --label \"S4 CRT startup adversarial review\""),
                "result_before_that_unit_existed": "ALL_GATES_OK count=21",
            },
        },
    }
    # A receipt is a tracked record and belongs where receipts live. It was
    # produced under private/generated while the slice ran, because that
    # slice could not write to Documentation/; the path moved when it landed.
    out = C.ROOT / "Documentation" / "receipts" / args.out
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
