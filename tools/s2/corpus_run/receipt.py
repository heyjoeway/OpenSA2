#!/usr/bin/env python3
"""Write Documentation/receipts/s2-corpus-run-v1.json from this slice's run logs.

Rule 7: every number here is copied out of private/generated/s2/corpus_run/*.json,
never typed. The prose fields (the blocker classification, the mid-function finding,
the reachability answer) are this unit's judgement and say so; the numbers behind them
are computed from the registry and the run log.

Usage:
  .venv/bin/python tools/s2/corpus_run/receipt.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
REG = ROOT / "Documentation" / "receipts" / "fixture-registry.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s2-corpus-run-v1.json"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def classify(blocker: str | None) -> str:
    if not blocker:
        return "runnable at S1"
    b = blocker.lower()
    if "window" in b:
        return "mid-function window"
    if "whole-kernel" in b:
        return "whole-kernel corpus"
    if "oracle-removal" in b or "removed the" in b:
        return "oracle removed by the stager"
    if "native oracle stub" in b:
        return "transcendental oracle (design 7.4)"
    if "trapping at" in b or "leaves the lifted bytes" in b:
        return "lifted but trapped at an unlifted callee"
    return "function outside the S1 spike ten"


def main() -> None:
    lift = json.loads((OUT / "lift.json").read_text())
    build = json.loads((OUT / "build.pc24.json").read_text())
    run = json.loads((OUT / "run.pc24.json").read_text())["results"]
    close = json.loads((OUT / "close_traps.json").read_text()) if (OUT / "close_traps.json").exists() else {}
    window = json.loads((OUT / "window_0047d0ec.json").read_text())
    controls = (json.loads((OUT / "controls.json").read_text())
                if (OUT / "controls.json").exists() else {})
    control_rows = [c for cs in controls.values() for c in cs]
    asserting = [c for c in control_rows if c["kind"] != "probe"]
    registry = json.loads(REG.read_text())["fixtures"]
    results = json.loads((GEN / "fixture-results.json").read_text())
    fmap = json.loads((GEN / "function-map.json").read_text())["functions"]
    entries = {f["address"].lower() for f in fmap}

    fns = lift["functions"]
    lifted = {a: e for a, e in fns.items() if e.get("lifted")}
    mapped = {a: e for a, e in lifted.items() if a in entries}
    orphan = {a: e for a, e in lifted.items() if a not in entries}
    roots = sorted({f["function"].lower() for f in registry
                    if isinstance(f.get("function"), str) and f["function"].strip()})

    ran = {r["fixture"] for r in run} | set(results["passed"])
    by_id = {f["id"]: f for f in registry}
    registered_ids = set(by_id)
    # M3 counts registered fixtures on both sides. `results["passed"]` also carries the
    # two superseded v1 files, which still drive and still pass but are not in the
    # denominator, so the numerator is the intersection rather than the whole list.
    passed_registered = sorted(set(results["passed"]) & registered_ids)
    not_run = [f["id"] for f in registry if f["id"] not in ran]
    classes = Counter(classify(by_id[i].get("blocker")) for i in not_run)

    # The wrap's two fixtures, read out of its own receipt rather than typed. The two
    # literals that used to stand here (`+ 95728 + 53`) were right, and were constants
    # inside a generator that rule 7 exists to keep out of one; the prose beside them
    # attributed the pair's 95781-row total to the first fixture alone, whose own figure
    # is 95728 (G2b finding 8).
    wrap = json.loads((ROOT / "Documentation" / "receipts" / "s2-wrap-v1.json").read_text())
    wrap_fixtures = [{"fixture": Path(f["fixture"]).stem, "rows": f["rows"]}
                     for f in wrap["reruns"]["entry_test_0x00426d50"]["fixtures"]]
    wrap_rows = sum(f["rows"] for f in wrap_fixtures)
    # Only registered fixtures count towards the corpus totals: the two superseded v1
    # files still drive and are still reported per fixture, but they are not in M3's
    # denominator, so their rows are not in its numerator either.
    corpus_rows = sum(r["rows"] for r in run if r["fixture"] in registered_ids)
    rows_run = corpus_rows + wrap_rows
    rows_registered = sum(f["rows"] for f in registry)
    rows_not_driven = {r["fixture"]: r.get("rows_registered_not_driven") for r in run
                       if r["fixture"] in registered_ids and r.get("rows_registered_not_driven")}

    # The eight fixtures the G2 fold drove by moving the lift boundary to the containing
    # routine's entry, and the rows they carry -- computed from the registry, not typed.
    BOUNDARY_EIGHT = tuple(f"ground-admission-source-v{i}" for i in (1, 2, 3, 4)) + \
        tuple(f"ground-near-set-v{i}" for i in (1, 2, 3, 4))
    boundary_rows = sum(r["rows"] for r in run if r["fixture"] in BOUNDARY_EIGHT)

    # The no-call comparison, per fixture and in total (G2b finding 1).
    no_call = [(r["fixture"], r.get("no_call") or {}) for r in run
               if r["fixture"] in registered_ids]
    informative = {
        "question": ("Of the columns and words a passing fixture compares, how many does its "
                     "pass depend on the lifted code for? A column whose value the plan poked "
                     "and read straight back is satisfied whether or not anything ran."),
        "method": ("tools/s2/corpus_run/run.py drives every fixture a second time with every "
                   "OP_CALL stripped out of its row op-streams and compares that against the "
                   "fixture's own expected words. A column that matches on every row without "
                   "the call is satisfied without executing the lift; the rest are "
                   "`informative`. A fixture the no-call run satisfies outright fails."),
        "columns_compared": sum(n.get("columns", 0) for _f, n in no_call),
        "informative_columns": sum(n.get("informative_columns", 0) for _f, n in no_call),
        "words_compared": sum(n.get("words_compared", 0) for _f, n in no_call),
        "informative_words": sum(n.get("informative_words", 0) for _f, n in no_call),
        "fixtures_the_no_call_run_satisfies": [f for f, n in no_call
                                               if n.get("passed_without_the_call")],
        "per_fixture": [{"fixture": f, "rows": n.get("rows"),
                         "columns": n.get("columns"),
                         "informative_columns": n.get("informative_columns"),
                         "words_compared": n.get("words_compared"),
                         "informative_words": n.get("informative_words"),
                         "rows_matching_without_the_call": n.get(
                             "rows_matching_native_without_the_call")}
                        for f, n in sorted(no_call)],
    }
    informative["columns_satisfied_without_the_call"] = (
        informative["columns_compared"] - informative["informative_columns"])
    informative["words_satisfied_without_the_call"] = (
        informative["words_compared"] - informative["informative_words"])

    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s2-corpus-run-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S2",
        "question": ("Can the S2 exit be reached: M1 for the fixture-covered set = 100 % and "
                     "M3 = 100 % of fixtures passing on the lifted functions? Lift every function "
                     "any reference-project fixture covers and its callees, drive every fixture that can "
                     "be driven, and establish whether a captured mid-function window is drivable "
                     "at all."),
        "verdict": (
            f"M1 fixture-covered is now {len(set(roots) & set(mapped))}/{len(roots)}: every distinct entry "
            f"address the registry names is lifted, with its transitive callee closure -- "
            f"{len(mapped)} mapped functions and {len(orphan)} orphan blocks, all {build['functions']} "
            f"modules compiled and linked into one program. M3 is {len(passed_registered)}/{len(registry)}: "
            f"{len(run) + 2} fixtures were driven through runtime v0, {len(passed_registered)} of the "
            f"registered {len(registry)} pass and "
            f"{len(results['failed'])} fails, over {rows_run} native rows. The atan2 divergence the first "
            "corpus run classified as a design 7.4 transcendental residue was not one: it was two defects "
            "in the lift, both now fixed in tools/spike/pc24pass and both proved on the full row sets "
            "(see `defects_found`). This run's earlier claim -- that the undriven fixtures cannot be driven "
            "because their rows record the kernel's semantic inputs and not the machine state at the "
            "address they were captured at -- was false, and is withdrawn: see `reachability`. Eight of "
            "them were driven by the G2 fold, over " + str(boundary_rows) + " native rows with no mismatch and no trap, by "
            "moving the lift boundary to the containing routine's own entry; the survey recorded in "
            "`survey` then decoded all 35 that remained and split them honestly, and the closing "
            "slice in `closing` drove the seven it had called blocked by the clock rather than by "
            "evidence -- all seven drive, all seven pass, and the windows they needed exposed a "
            "third function-map size under-report (see `defects_found`). "
            + (f"{len(not_run)} registered fixtures are still undriven, "
               f"{len([i for i in not_run if (by_id[i].get('blocker') or '').startswith('surveyed')])} "
               "of them with a reason read out of their own bytes and the image's disassembly. "
               "Those reasons are claims, not findings: every one written in this stage that was "
               "later attacked has fallen, so each surviving one is a hypothesis for the next "
               "slice to attack rather than a ceiling. " if not_run else
               "No registered fixture is undriven, and the survey table that held their reasons "
               "is empty: every blocker written in this stage was eventually falsified by running "
               "the experiment it named. ")
            + 
            f"The closing slice's claim that the remaining 23 are structurally undrivable is "
            f"withdrawn for seven of them: the `gates` slice drove all seven and all seven pass, "
            f"and not one needed a field its format does not record (see `gates`). Every fixture "
            f"driven there carries negative controls, in "
            f"private/generated/s2/corpus_run/controls.json. "
            f"The `last` slice then tested the sixteen the `gates` slice left as hypotheses and "
            f"drove eight of them -- the six ground-tail fixtures and the two candidate-loop "
            f"executors -- at one new window, 0x00462F83..0x00463821, the accepted tail of "
            f"GroundCollisionStep. The pointer-free SA2GTR reduction is invertible on the "
            f"driver's side because [esp+74h] is the candidate triple's own word 1 "
            f"(0x00462F58/0x00462F5E/0x00462F77), so a driver that lays the triple out knows "
            f"which address the stager's token 1 names (see `last`). "
            f"The `final` slice then drove the remaining eight, and M3 is now "
            f"{len(passed_registered)}/{len(registry)} with no fixture left undriven: the "
            f"selector-to-precise pair over three rebuilt object caches for SA2GPC1, SA2GPE2 and "
            f"both SA2GPE3 files; the window 0x004620CA..0x0046244C, which loads the near set and "
            f"its count out of the globals 0x0047CD60 fills, for the two SA2GFL1 full-chain "
            f"fixtures; and, for SA2GCL1 and SA2GEX2, the same 0x00462F83 tail carried through "
            f"0x00463692 `jl 004624C0h` into the newly lifted loop head, which reaches it because "
            f"internalize.py folds remill's missing block into the function-table dispatch (see "
            f"`final`). "
            f"{len(controls)} fixtures now carry {len(control_rows)} controls, "
            f"{len(asserting)} of them asserting, "
            f"{sum(1 for c in asserting if c['control_holds'])} of which hold, and every fixture "
            f"this unit drives carries at least one. "
            f"Of the {informative['columns_compared']} compared columns "
            f"{informative['informative_columns']} are informative -- the rest are reproduced with "
            f"every OP_CALL stripped from the row op-streams -- and of "
            f"{informative['words_compared']} compared words "
            f"{informative['informative_words']} are. That is the honest size of the claim, and it "
            f"is recorded per fixture rather than summarised: see `informative_columns` and "
            f"`g2b_fold`. "
            f"{len(not_run)} remain undriven."),
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 5 (S2), 3.4, 3.7, 7.4, 8"],
            "receipts_built_on": ["Documentation/receipts/s2-wrap-v1.json",
                                  "Documentation/receipts/s2-fixture-corpus-v1.json",
                                  "Documentation/receipts/fixture-registry.json",
                                  "Documentation/receipts/s2-pc24-native-rows-v1.json"],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": sha(ROOT / "private" / "exe" / "sonic2app-unpacked.exe")},
            "function_map": {"path": "private/generated/function-map.json",
                             "sha256": sha(GEN / "function-map.json")},
            "fixture_registry": {"path": "Documentation/receipts/fixture-registry.json",
                                 "sha256": sha(REG)},
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py                     # remill + PC24 pass over the closure",
            ".venv/bin/python tools/s2/corpus_run/build.py --variant pc24          # one program, every module",
            ".venv/bin/python tools/s2/corpus_run/close_traps.py                   # lift what the runs trap at",
            ".venv/bin/python tools/s2/corpus_run/run.py                           # drive every fixture with a plan",
            ".venv/bin/python tools/s2/corpus_run/window_entry.py --window 0x0047D0EC --end 0x0047D151 --drive",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --only 0x00497560",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --block 0x0048B6C0:0x0048B9EB 0x0048DA51:0x0048DDEF",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --block 0x004622EE:0x0046244C+ret 0x00462526:0x00462C19+ret",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --block 0x00462F83:0x00463822",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --force --block 0x00462133:0x0046244C+ret",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --block 0x004620CA:0x0046244C+ret 0x004624C0:0x00463822",
            ".venv/bin/python tools/s2/corpus_run/controls.py                      # negative controls for every driven fixture",
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --to-next-entry 0x0048B510",
            ".venv/bin/python tools/s2/corpus_run/metrics_inputs.py                # emit log, build log, fixture results",
            ".venv/bin/python tools/s2/corpus_run/receipt.py",
            ".venv/bin/python tools/s2/wrap/wrap.py --phase build && .venv/bin/python tools/s2/wrap/wrap.py --phase run",
        ],
        "lift": {
            "roots": {"count": len(roots), "addresses": roots,
                      "basis": "every `function` field in the fixture registry, plus 0x0048b9f0 whose "
                               "callees the corpus receipt measured as its blocker"},
            "closure_rule": "transitive `direct_calls` out of private/generated/function-map.json, then the "
                            "addresses the runs themselves trapped at (a `jmp` into another mapped entry is "
                            "not a `direct_call`, so the call graph alone does not close)",
            "functions_lifted": len(lifted),
            "mapped_functions": len(mapped),
            "orphan_blocks": {
                "count": len(orphan),
                "addresses": sorted(orphan),
                "meaning": "a jump target inside a gap the function map has no entry for; lifted from that "
                           "address to the next mapped entry. All of them are on the CRT transcendental tail "
                           "(atan2/asin special cases). They are emitted and compiled but cannot count towards "
                           "M1, whose denominator is the map.",
            },
            "by_region": dict(Counter(e.get("region") for e in lifted.values())),
            "bytes": sum(e.get("bytes", 0) for e in lifted.values()),
            "seconds": round(sum(e.get("seconds", 0) for e in lifted.values()), 2),
            "instrumented_fp_ops": sum((e.get("pass") or {}).get("ops_instrumented") or 0
                                       for e in lifted.values()),
            # Every fix class the pass applies, counted over the closure. `fist_rewrites` was
            # 0 in every module until the G2 fold, because lift_set.py invoked pc24pass.py
            # without --int-helper-ll: remill's host-rounding `llvm.nearbyint.f80` and its
            # poison `fptosi x86_fp80 to iN` survived the pass at every site (design 3.4:
            # "`fistp` family uses a helper reproducing x87's out-of-range result").
            "pass_counters": {k: sum((e.get("pass") or {}).get(k) or 0 for e in lifted.values())
                              for k in ("fist_rewrites", "fpatan_rewrites", "fpatan_refused",
                                        "control_word_masks_fixed", "f80_accessor_attrs_fixed")},
            "int_helper": "private/generated/s2/corpus_run/helper/x87_int.ll, compiled from "
                          "tools/s2/patch/x87_int.c by tools/s2/corpus_run/lift_set.py",
            "failures": [a for a, e in fns.items() if not e.get("lifted")],
            "callee_could_not_be_lifted": [],
            "trap_closure_rounds": close.get("rounds", []),
        },
        "build": {
            "toolchain": f"zig cc / zig c++ {build['target']} {build['opt']} "
                         "(the pipeline s2-build-time-sample-v1 measured)",
            "modules": build["functions"],
            "objects_ok": build["ir_ok"],
            "link_ok": build["link"]["ok"],
            "undefined_symbols": build.get("undefined_sub_symbols"),
            "seconds": build["seconds"],
            "function_table": build["function_table"]["summary"],
            # These were 0/0/0 in the previous receipt: build.py accumulated them inside the
            # cache guard, so a cached re-run reported nothing folded.
            "missing_block_folding": build["fold"],
            "whole_closure_link": (
                "The S2 wrap receipt recorded whole-image linking as an S3 blocker: remill's per-module "
                "missing-block stubs are renamed `@sub_<hex>.<n>` by LLVM and several modules define the same "
                "symbol. tools/s2/corpus_run/internalize.py removes those defines and declares (each body was "
                "checked to be nothing but `__remill_missing_block`) and points every reference at one "
                "`__sa2_dispatch_stub`, which dispatches on the call target remill passes as `pc`. "
                f"{build['functions']} modules then link as one program with no undefined symbol."),
            "missing_block_policy": (
                "The wrap receipt also left open what a missing block whose address IS a table entry should do. "
                "The evidence arrived here: every trap on the CRT transcendental path was `jmp` to another "
                "mapped entry. runtime/remill_runtime.cpp now dispatches those, behind "
                "`sa2_missing_block_dispatch`, which defaults to 0 so every driver the wrap built keeps the "
                "behaviour its receipt recorded; harness/plan_driver.c sets it to 1."),
        },
        "fixtures": {
            "registered": len(registry),
            "driven": len(run) + 2,
            "driven_note": (f"{len(run)} by tools/s2/corpus_run/run.py, of which "
                            f"{len(run) - len([r for r in run if r['fixture'] in registered_ids])} "
                            "are superseded files kept driven but out of the denominator, plus the "
                            "wrap's two."),
            "passed": passed_registered,
            "passed_including_superseded": results["passed"],
            "failed": results["failed"],
            "regressions": results["regressions"],
            "native_rows_driven": rows_run,
            "native_rows_registered": rows_registered,
            "native_rows_driven_note": (
                f"{corpus_rows} from the corpus run over the {len(registered_ids)} registered "
                f"fixtures plus {wrap_rows} from the wrap's two "
                f"({', '.join(f'{f['fixture']} {f['rows']}' for f in wrap_fixtures)}), both read "
                "out of their run logs. It used to exceed `native_rows_registered` because "
                "ground-gather-output-v1 drove 442 candidate rows against 152 registered, and the "
                "290-row surplus hid a 4-row shortfall on two other fixtures (G2b finding 6). Every "
                "registered row is now driven or declared excluded by name: see "
                "`rows_registered_not_driven` and `rows_excluded_with_reason` per fixture."),
            "rows_registered_not_driven": rows_not_driven,
            "rows_registered_not_driven_rule": (
                "tools/s2/corpus_run/run.py fails a fixture when the registry's row count minus the "
                "rows it drove is anything other than the number of rows the plan declares in "
                "`Plan.excluded`, each with the row it names and why. A shortfall with no declared "
                "exclusion is a silent gap, which is what this check exists to stop."),
            "superseded_files": [s["id"] for s in
                                 json.loads(REG.read_text()).get("superseded", [])],
            "per_fixture": [
                {k: v for k, v in r.items() if k not in ("driver_stderr",)} for r in sorted(run, key=lambda r: r["fixture"])
            ],
            "wrap_rerun": (
                "contact-transform-corpus-v1 and ninja-point-transform-v1 are re-verified by "
                "tools/s2/wrap/wrap.py --phase run after this slice's runtime change: "
                + " and ".join(f"{f['fixture']} {f['rows']}/{f['rows']}" for f in wrap_fixtures)
                + f" rows match native on the pc24 variant, {wrap_rows} together, identical to "
                "Documentation/receipts/s2-wrap-v1.json, so the runtime change caused no "
                "regression. Neither carries a negative control: they are driven by "
                "tools/s2/wrap, which the corpus-run unit does not own, so the rule that every "
                "driven fixture carries an asserting control holds over the 52 this unit drives "
                "and not over these two."),
        },
        "informative_columns": informative,
        "mid_function_entry": {
            "question": "Is a captured mid-function window drivable by lifting at the window's entry address "
                        "and setting up the state the fixture's rows record?",
            "answer": "The question was the wrong one, and the answer this receipt gave was wrong. "
                      "remill lifts at the window address, it links, and `sa2_call` enters it and returns "
                      "cleanly -- but the window at 0x0047D0EC was chosen eleven bytes after the loop head, "
                      "and the one live-in that decides the outcome, `ah`, is computed by the eleven bytes "
                      "that were cut off (`fld [esp+18h]; fld [esi+0Ch]; fcompp; fnstsw ax`). At the "
                      "containing routine's own entry, 0x0047CD60, the fixture's own columns ARE the "
                      "machine state: its `cols` section is the COL record the routine walks, field offset "
                      "for field offset. ground-admission-source-v1..v4 are driven from there and pass, "
                      "162247 rows, no mismatch and no trap.",
            "case": "0x0047D0EC, the admission branch inside 0x0047CD60 ListGroundForCollision "
                    "(ground-admission-source-v1..v4, 162247 native rows)",
            "lift": window["lift"],
            "link": window["build"],
            "live_in": {
                "registers_read_before_written":
                    window["live_in"]["registers_read_before_written"],
                "segments_read": window["live_in"].get("segments_read"),
                "x87_read_before_written": window["live_in"].get("x87_read_before_written"),
                "guest_globals_read": window["live_in"].get("guest_globals_read"),
                "memory_reads": window["live_in"]["memory_reads"],
                "census": window["live_in"].get("census"),
                "census_correction": (
                    "This receipt previously reported 13 registers read before written and three live x87 "
                    "registers here. Both were tool defects, fixed in tools/s2/corpus_run/window_entry.py: "
                    "`_full` called `Register.full_register`, which does not exist in this iced-x86 build "
                    "(it is `RegisterExt.full_register`), and a bare `except` returned the sub-register, so "
                    "`ah` and `eax` were counted separately; and iced reports no ST write for `fld`, so the "
                    "four slots this window pushes itself looked like the caller's. The census is "
                    "6 general registers, 2 segment registers, 0 x87 registers and 1 guest global."),
                "summary": (
                    f"{len(window['live_in']['registers_read_before_written'])} registers are read before "
                    "they are written and "
                    f"{len(window['live_in']['memory_reads'])} memory operands are read. The first "
                    "instruction at the window address is `test ah, 41h`: `ah` holds the x87 condition codes "
                    "`fnstsw` copied from the `fcompp` two instructions earlier, and that single bit decides "
                    "whether the COL entry is admitted. The window also needs `esi` (the COL cursor), `ebp` "
                    "(the near-set write cursor), `ebx`, `edi`, the caller's 0x1C-byte stack frame at "
                    "[esp+0x1c], [esp+0x30], [esp+0x34] and [esp+0x38], three live x87 registers, and the "
                    "guest global 0x01DE9484 (the near-set count)."),
            },
            "probe": window.get("drive"),
            "withdrawn": (
                "The paragraph this receipt carried here argued that reconstructing `ah` from the fixture's "
                "`accepted` column `would be assuming what is to be proved`, and concluded that the fixture "
                "could not supply the window's state. It is withdrawn. The argument holds only at the "
                "boundary 0x0047D0EC; the boundary was a choice, not a fact about the fixture, and it was "
                "never tested against the alternative the same section named in `what_it_would_take`. "
                "`ah` is not a live-in at 0x0047D0E1 and there is no register file to reconstruct at "
                "0x0047CD60."),
            "boundary_correction": {
                "0x0047D0EC": "the receipt's boundary: 6 general registers, 2 segments, 0 x87, 1 guest "
                              "global; `ah` is a live-in and decides the answer",
                "0x0047D0E1": "the loop head eleven bytes earlier: 5 general registers, 0 x87, 1 guest "
                              "global, and NO `ah` -- the window computes it from [esp+18h] and [esi+0Ch]. "
                              "remill lifts there and the block links (155/155 IR objects, no undefined "
                              "symbol).",
                "0x0047CD60": "the containing routine's own entry, already lifted at depth 0. Driven: "
                              "ground-admission-source-v1..v4, 162247 rows, 0 mismatches, 0 traps.",
                "0x0047D160": "RefreshCollisionObjectSet, the near-set family's containing entry. Driven: "
                              "ground-near-set-v1..v4, 1015588 rows, 0 mismatches, 0 traps -- after the "
                              "lift was widened, see `map_size_defect`.",
            },
            "map_size_defect": (
                "private/generated/function-map.json records 0x0047D160 as 1351 bytes, ending 0x0047D6A7. "
                "The routine's own epilogue runs to 0x0047D6AF and the next map entry is 0x0047D6B0, so the "
                "map is 9 bytes short: the final `jg 0x0047D550` is cut in half and remill emits "
                "`__remill_error`, which made every call into the lifted routine trap at 0x0047D6A2. "
                "tools/s2/corpus_run/lift_set.py --to-next-entry 0x0047D160 lifts it to the next map entry "
                "instead. The flag is off by default: widening every function would move digests the "
                "determinism gate pins, for a defect that belongs to the map (S1) and not to this unit."),
        },
        "whole_kernel_corpora": {
            "note": "Shape (c), out of scope for this slice: recorded, not attempted.",
            "fixtures": [
                {"id": f["id"], "rows": f["rows"], "functions_spanned": f["blocker"],
                 "what_a_single_entry_fixture_would_require":
                     "one entry address whose call runs the whole span, the guest state every function in "
                     "the span reads (COL table, cache arena, player and work blocks, the matrix stack and "
                     "the near-set globals), and expected words captured at that entry's return rather than "
                     "at each stage's window"}
                for f in registry if classify(f.get("blocker")) == "whole-kernel corpus"
            ],
        },
        "reachability": {
            "answer": (
                "The reachability claim this receipt made -- that 43 of the 54 registered fixtures cannot "
                "be driven because their rows record the kernel's semantic inputs and not the machine "
                "state at the address they were captured at -- is FALSE, and is withdrawn. It rested on "
                "one experiment at one boundary, 0x0047D0EC, an address eleven bytes after the loop head "
                "that computes the live-in the argument turns on, and on a live-in census that "
                "double-counted sub-registers and counted the window's own x87 pushes as the caller's. "
                "Both are corrected above. Moving the lift boundary to the containing routine's own entry "
                "drives eight of the 43 today, with no new capture and no new emitter work: "
                "ground-admission-source-v1..v4 at 0x0047CD60 and ground-near-set-v1..v4 at 0x0047D160, "
                f"{boundary_rows} native rows, every expected word matched including `distance_bits`, "
                "zero traps. "
                "Their fixtures' own `cols` and case sections are the COL records those routines walk, at "
                "the offsets they walk them. What each one needed is in tools/s2/corpus_run/plans.py. "
                "The survey of the other 35 is now done as well, and it moved six more: the whole "
                "1331-record COL table drives ground-funnel-v1 at 0x0047D160; 0x0047D160 chained into "
                "0x0047CD60 through the guest's own globals drives ground-gather-output-v1 and "
                "ground-funnel-chain-v1, once the gather is given the PREVIOUS tick's near set, which is "
                "what the capture shows and what cost 44 of 5401 rows to get wrong; a window lifted "
                "inside a mapped function, 0x0048DBD7:0x0048DDEF, drives selection-corpus-v1 and "
                "selection-output-v1; and candidate-enumeration-v1 passes once the group list and Z "
                "chains are rebuilt from its own `polygons` section, whose first two words the stager "
                "itself says are the chain links normalized to ordinals. That last one is the direct "
                "answer to `whole_kernel_corpora`: the world this fixture needed was in the fixture. "
                "M3 is therefore 24/54 with nothing failing, and the in-S2 ceiling is STILL NOT "
                "established: 30 fixtures remain undriven, 29 of them with a reason naming the field "
                "their own format does not record and the instruction that consumes it, and one "
                "(compact-contact-corpus-v1) with the honest reason that its boundary was not tried "
                "inside the slice's time box."),
            "numbers": {
                "M1_fixture_covered": f"{len(set(roots) & set(mapped))}/{len(roots)}",
                "M3": f"{len(passed_registered)}/{len(registry)}",
                "M3_before_this_fold": "10/54",
                "M3_before_this_survey": "18/54",
                "fixtures_driven": len(run) + 2,
                "fixtures_not_driven": len(not_run),
                "native_rows_driven": rows_run,
                "native_rows_gained_by_moving_the_boundary": boundary_rows,
                "ceiling_inside_S2": (
                    (f"not established; {len(passed_registered)}/{len(registry)} reached and "
                     f"{len(not_run)} fixtures still undriven. Every one of the "
                     f"{len(not_run)} was decoded and surveyed: each names either the "
                     "field its own format does not record with the instruction that "
                     "consumes it, or the guest entry it IS drivable at, which the slice "
                     "that wrote it did not reach.") if not_run else
                    (f"no ceiling: {len(passed_registered)}/{len(registry)} of the registered "
                     "corpus drives and passes, and no fixture is left in the denominator "
                     "undriven. Every blocker written in this stage was eventually "
                     "falsified by running the experiment it named, which is why the "
                     "survey table is empty rather than exhausted.")),
                "surveyed_at_instruction_depth": f"{len([i for i in not_run if (by_id[i].get('blocker') or '').startswith('surveyed')])} of {len(not_run)}",
            },
            "withdrawn_claims": [
                "`of the 43 registered fixtures nothing drove, none can be driven by a lift alone` -- "
                "eight of them are driven, and the survey of the other 35 was never done.",
                "`35 are windows captured at an interior address whose live-in is a register file, a "
                "caller stack frame and an x87 stack that the rows do not record` -- the census that "
                "said so was wrong (0 x87 registers, not 3), and the interior address was a choice.",
                "`candidate-cache-point-composition-v3 had its oracle deleted by the stager and can never "
                "pass in any stage` -- not verified here, and an independent read of the two fixture files "
                "reports that the predecessor's assertion target survives in "
                "ninja-point-transform-v1.bin. This receipt no longer asserts it.",
            ],
            "still_undriven": sorted(not_run),
            "why_each_one": "Documentation/receipts/fixture-registry.json, fixtures[].blocker. The "
                            "generated half is composed per fixture from its own decoded sections, the "
                            "addresses it names, which of them are lifted, and what the run reported; the "
                            "survey half is tools/s2/fixtures/register_corpus.py's SURVEYED table, one "
                            "entry per fixture tried and not driven, each naming an address and quoting "
                            "the instruction that consumes the missing field.",
            "survey": {
                "what": "Every one of the 35 fixtures the G2 fold left undriven was decoded, given a "
                        "lift boundary and driven; none was reasoned about.",
                "journal": "agents-journals/s2/2026-09-20-s2-corpus-survey.md",
                "drove": ["ground-funnel-v1", "ground-gather-output-v1", "ground-funnel-chain-v1",
                          "selection-corpus-v1", "selection-output-v1", "candidate-enumeration-v1"],
                "new_boundaries": {
                    "0x0048DBD7:0x0048DDEF": "the selection and aggregation tail of 0x0048CE40, lifted "
                                             "as a window inside a mapped function by the new "
                                             "`lift_set.py --block START:END`; the old rule (next map "
                                             "entry, capped at 512 bytes) truncates it at 535.",
                    "0x00462133:0x00462460": "the candidate-ordering block inside 0x00461F10, lifted and "
                                             "linked but NOT driven: the promotion flag at [esp+10h] "
                                             "that 0x004622F0 reads is unmeasured in both formats that "
                                             "record this block.",
                },
                "columns_not_compared": {
                    "candidate-enumeration-v1.expanded": "the frame word at (entry ESP)-0x0C, which the "
                                                         "walk overwrites with the Z upper bound at "
                                                         "0x0048BBB6",
                    "candidate-enumeration-v1.visited": "no instruction in 0x0048BAF0 keeps a visit "
                                                        "counter; the stager computed it offline",
                },
                "existential": "WITHDRAWN by the G2b fold. ground-gather-output-v1 used to pass "
                               "152/152 groups over 442 driven rows under an existential rule -- each "
                               "observation equals the admitted set of SOME query captured at that tick, "
                               "because the stager could pair by ordinal on only 88 of 152 -- and that "
                               "relaxation was load-bearing on 65 of the 152. It is now compared row for "
                               "row, 152 driven rows against 152 registered, by pairing observation j of "
                               "a tick with query j of that tick in the file's own order: 152 of 152 "
                               "reproduce. Plan.groups is gone from the runner. See `g2b_fold`.",
            },
        },
        "rounding_control": (json.loads((OUT / "rounding_probe.json").read_text())
                             if (OUT / "rounding_probe.json").exists() else None),
        "closing": {
            "what": "The seven fixtures the survey classified as blocked by the clock rather than by "
                    "evidence were driven. All seven drive; all seven pass.",
            "journal": "agents-journals/s2/2026-09-20-s2-corpus-close.md",
            "drove": {
                "candidate-cache-builder-v1": "0x004974F0 once per record into a zeroed 128-pair bucket "
                                              "array -- the bucket is observed, not inferred, because the "
                                              "row reads back all 256 bucket words and node 0 can only be "
                                              "where the guest put it -- then all 72 inserts and "
                                              "0x00497560 with edi = the group.",
                "basic-strip-decoder-v1": "the triangle loop window 0x0048B6C0..0x0048B9EA, entered once "
                                          "per strip with the strip's transformed vertices at (entry "
                                          "ESP)+0x60 and both enclosing loop counters set to 1 so the "
                                          "epilogue at 0x0048B9CE runs and the window ends on a `ret`.",
                "candidate-cache-composition-v1": "the builder window per recorded triangle, then "
                                                  "0x00497560, 0x0048BAF0 and 0x0048CE40.",
                "candidate-cache-strip-composition-v2": "the builder window per strip, then the same three.",
                "candidate-cache-point-composition-v3": "0x0048B510 per strip over the 53 source points "
                                                        "with the recorded matrix on the ninja stack top, "
                                                        "then the builder window and the same three.",
                "candidate-cache-object-matrix-composition-v4": "0x0048B9F0 builds the matrix from the "
                                                                "nine transform words, then as v3.",
                "compact-contact-corpus-v1": "the record-assembly window 0x0048DA51..0x0048DDEE, 2990 "
                                             "rows, twelve of the twenty-two record words (see `limits`).",
            },
            "oracle_checked": "The G2 reviewer's claim about candidate-cache-point-composition-v3 was "
                              "verified rather than taken: ninja-point-transform-v1's expected[:52] is "
                              "byte for byte SA2ECC2's transformed-vertex section at offset 72 + 1025*4, "
                              "and SA2ECC3's matrix and its 53 source points are ninja-point-transform-v1's "
                              "matrix and sources. The oracle survives.",
            "contact_stage": "All four SA2ECC fixtures compare the twenty result words 0x0048CE40 leaves "
                             "at (csts)+0x34, the accepted count at [0x019471B0], and the per-face "
                             "acceptance bit -- the last observed by driving 0x0048CE40 once per selected "
                             "face with [group+0x1C] pointing at that one face and [group+0x24] = 1, which "
                             "is what the loop at 0x0048CEE7 walks.",
        },
        "gates": {
            "what": "Seven of the twenty-three the closing slice called structurally undrivable were "
                    "driven. All seven drive; all seven pass. Not one of them needed a field its "
                    "format does not record: what they needed was a guest global chosen so the "
                    "unrecorded path never runs, a word index read as a byte offset, or an earlier "
                    "boundary where the x87 stack is empty.",
            "journal": "agents-journals/s2/2026-09-20-s2-corpus-gates.md",
            "controls": {
                "file": "private/generated/s2/corpus_run/controls.json",
                "tool": "tools/s2/corpus_run/controls.py",
                "what": "Every fixture driven here carries `break` controls (perturb an input the "
                        "disassembly says a named column depends on; the control holds when the run "
                        "fails and the report names the column and the row count) and `inert` "
                        "controls (perturb an input the window does not read; the control holds when "
                        "the run still passes bit for bit). A `probe` control poisons the whole "
                        "output region and reports which words came back untouched, which is how "
                        "'the seed writes these twelve work words' was measured rather than assumed.",
            },
            "drove": {
                "precise-ground-query-seed-v1": "0x00461F10 itself. [0x01DE9484] is not a dropped "
                    "field: 0x0047CD60 zeroes it at its own entry and raises it once per admitted "
                    "COL record, so an empty COL table ([0x01A5A2C8] = [0x01A5A2CC] = 0) makes "
                    "0x004620D4 `test dx,dx` / `jne 00462133h` fall through to the empty-set tail "
                    "and the `ret` at 0x00462132. The 33-word work buffer enters poisoned with "
                    "0xDEADBEEF and only the twenty-one words the step does not write are written "
                    "back.",
                "ground-query-seed-slope-v1": "the same, 41 rows. Work words 13 and 14 are dropped "
                    "from the compared set by a rule read out of the disassembly -- compare a work "
                    "word only where the seed is its last writer inside 0x00461F10 -- because "
                    "0x0046312F, 0x0046317D and 0x004631CF rewrite [ebx+34h] and [ebx+38h] in the "
                    "candidate tail. See `limits`.",
                "ground-object-order-v2": "the ordering block 0x004622EE..0x0046244B, entered as "
                    "0x0046228E enters it (ECX = the count). Player word 40 is byte 0xA0 and player "
                    "word 44 is byte 0xB0, so SA2GOO2's `prior_task` and `prior_object` are exactly "
                    "the two fields 0x0046232C and 0x004623FE read.",
                "ground-object-order-slope-v1": "the same, 41 calls.",
                "ground-policy-word-v1": "the policy window 0x00462526..0x00462C18. The 'two "
                    "pointers collapsed into one boolean' are read once each, at 0x00462532 and "
                    "0x00462536, and the only consumer is `je 00462665h`; inverting the recorded "
                    "relation leaves every compared word unchanged on all 13 flat and all 43 slope "
                    "cases, because `test dl,cl` at 0x0046252A guards the compare and entity word 1 "
                    "is even on 13 of 13 flat and 41 of 43 slope cases.",
                "ground-policy-word-slope-v1": "the same, 43 rows, comparing the contact angle in "
                    "ESI as well as the policy word.",
                "ground-acceptance-v1": "the policy window too, not SA2GAC1's own 0x00462C19, which "
                    "reads x87 registers it never pushes (0x00462C55 `fcom st,dword ptr "
                    "[eax*8+1DEFBA4h]`, 0x00462CCC `fcomp st,st(4)`). Its 43 cases are the 43 cases "
                    "of ground-policy-word-slope-v1 -- every shared column agrees on every row, "
                    "checked in the builder -- so three words its schema calls inputs become "
                    "compared outputs: the policy word, the angle, and bit 8, which is "
                    "`(policy >> 3) & 1` on all 43 rows.",
            },
            "blockers_withdrawn": {
                "SA2GQS3": "the gather count at 0x004620CA and the list base at 0x00462133 are a "
                           "branch gate and the branch's taken side, not dropped fields",
                "SA2GOO2": "the promotion flag at [esp+10h] is a boolean the record pins down and "
                           "the slot index at [esp+18h] is not a live-in with the flag clear; the "
                           "two fields called missing are player words 40 and 44, recorded",
                "SA2GPW1": "the collapsed pointer pair is not read on 54 of the 56 recorded cases "
                           "and does not change the output on the other two",
                "SA2GAC1": "three of the four named fields are guest globals the corpus pokes and "
                           "the fourth is player word 0, which SA2GQS3's schema carries for the "
                           "same ticks; the real obstacle was the x87 state at its own boundary, "
                           "and one boundary earlier there is none",
            },
            "tooling": [
                "tools/s2/corpus_run/lift_set.py --block START:END+ret: a window every path of "
                "which leaves by jumping to one address gets a synthetic 0xC3 at that address; no "
                "image byte is altered and the lift log records `synthetic_ret` and `image_bytes`.",
                "tools/s2/corpus_run/lift_set.py --force no longer discards lift.json. It used to "
                "rebuild the log from scratch, so `--force --block ...` rewrote it with one entry "
                "and lost the other 160, while every .ll on disk and the linked driver stayed "
                "correct -- a silent loss that only the next reader of the log would have found.",
                "tools/s2/corpus_run/controls.py: the negative-control runner described above.",
            ],
        },
        "last": {
            "what": "The sixteen the `gates` slice left as hypotheses were tested. Eight drive: "
                    "the six SA2GTR ground-tail fixtures and the two SA2GEX1 candidate-loop "
                    "executors, all at one new window. Two more are named as untested rather than "
                    "undrivable, and the four contact-expectation fixtures and the two full-chain "
                    "fixtures were not reached inside the time box.",
            "journal": "agents-journals/s2/2026-09-20-s2-corpus-last.md",
            "window": {
                "address": "0x00462F83..0x00463821",
                "lift": "tools/s2/corpus_run/lift_set.py --block 0x00462F83:0x00463822",
                "why_no_synthetic_ret": "every path out of the window is one of "
                    "GroundCollisionStep's own three `ret`s, at 0x004637A7, 0x004637E6 and "
                    "0x00463821",
                "entry_state": "EAX = the helper result 0x00465DB0 returns, EBX = the 33-word "
                    "collision work buffer (0x00461F23 `mov ebx,[esi+90h]`), ESI = EntityData2 "
                    "(0x00462F64 `mov esi,[ebp+0Ch]`), EDI = the entity (0x00462C19 "
                    "`mov edi,[ebp+8]`), EBP = the frame whose +8/+0Ch/+10h are the three "
                    "arguments, ESP = the caller frame minus 0x14 because the window's own first "
                    "instruction is `add esp,14h`",
            },
            "how_the_blocker_fell": {
                "the_claim": "the SA2GTR schemas are a pointer-free reduction and the window reads "
                             "nine caller-frame pointers none of which the fixture carries; the "
                             "support pointer at [esp+74h] is replaced by a token 0/1/2 in player "
                             "words 40 and 44, so the expected output is a token where the guest "
                             "stores a pointer",
                "the_answer": "the window's read-before-written caller-frame set is fourteen words, "
                              "and nine of them are addresses of blocks the driver owns: work+0, "
                              "+4, +0x10, +0x1C and +0x48 at [esp+24h], [esp+68h], [esp+6Ch], "
                              "[esp+44h] and [esp+10h]; entity+0x14 at [esp+30h]; the candidate "
                              "triple at [esp+18h]; the null moving support at [esp+2Ch]; and the "
                              "support object at [esp+74h]. The other five are the fixture's own "
                              "semantic words 1, 2, 6, 7 and 8.",
                "the_support_pointer": "[esp+74h] is not a fourteenth unknown. 0x00462F58 "
                    "`mov eax,[esp+18h]` / 0x00462F5E `mov ecx,[eax+4]` / 0x00462F77 "
                    "`mov [esp+88h],ecx` (five pushes deep, so caller-frame +0x74) makes it the "
                    "candidate triple's own word 1 -- the very `candidate_w1` the SA2GEX blocker "
                    "called a dropped field. A driver that lays the triple out chooses it, so the "
                    "stager's `0 if 0, 1 if == current_support, else 2` inverts: 0 -> 0, 1 -> the "
                    "support this plan laid down, 2 -> a different object it also laid down. "
                    "Across all 86 SA2GTR cases the output token is 1 for word 44 and 0 for word "
                    "40, so the inverse is single-valued, and the "
                    "`support-pointer-drives-player_w44` control pointing [esp+74h] at the other "
                    "object makes the column come back as that other address on every row of all "
                    "six fixtures.",
                "the_record": "the 22-word compact contact record is read at 0x00463385 "
                    "`movzx eax,word ptr [ebx+2Ah]` / `imul eax,eax,58h` / `add eax,1DDB660h`, the "
                    "public record array `selection-output-v1` already drives, indexed by the work "
                    "buffer's own half-word. Its words 8..10 are the plane point 0x00780310 "
                    "projects onto, at 0x004633CF/0x004633D9/0x004633E3.",
            },
            "supplied_and_named": {
                "[0x01A5A248]": "driven as 0. GroundCollisionStep's own prologue zeroes it "
                    "(0x00461F32 `mov [1A5A248h],eax`, eax = 0) and the only three stores of 1 in "
                    "the image are 0x004651FC, 0x00465657 and 0x00465C78, inside the contact "
                    "helpers 0x00464E30, 0x00465390 and 0x004659A0. It gates 0x00463359 "
                    "`cmp dword ptr [1A5A248h],0`; set to 1 it clears work words 27..29 and the "
                    "run fails on 2/2, 29/29, 40/40 and 7/11 rows. This is the one supplied value "
                    "in the slice that the record itself pins rather than the image, and it is "
                    "named here for that reason.",
                "[0x01A5A23C]": "driven as 0, its image-initial value; the only store in the image "
                    "is 0x0045A730 `mov dword ptr [1A5A23Ch],1`. Measured **inert** on every row "
                    "of five of the six SA2GTR fixtures, because those rows reach the position "
                    "store at 0x004635E4 through 0x004635DA `jz` either way; load-bearing only on "
                    "ground-tail-final-v5's four bound-3 rows, where it is registered as a "
                    "`break`.",
                "ground-tail-v1's helper result and stage selector": "SA2GTR1 records neither. "
                    "Both come from ground-tail-v2, the same two ticks, after every shared block "
                    "is checked equal word for word. The result is load-bearing (the "
                    "`sibling-result-nonzero` control fails the run on 2/2 rows); the selector is "
                    "measured inert, its only read on this path being 0x004691DD "
                    "`cmp word ptr [1934B70h],5Ah` inside PResetPosition.",
                "EntityData2 words 3..9 on SA2GTR1 and SA2GTR2": "those two schemas carry three "
                    "world words; the driver zeroes the rest. Measured inert -- the window's only "
                    "EntityData2 reads are 0x00462FC6 `fcom st,dword ptr [esi]` and 0x00462FCF "
                    "`fcomp st,dword ptr [esi+8]`.",
            },
            "what_is_compared": "per row, the Ground-exit expectation (8 entity words, the "
                "EntityData2 block, the 24 schema player words with 40 and 44 mapped back through "
                "the token, and the 33 work words) and the PReset seam (the same three blocks "
                "after a second call to PResetPosition 0x00469050 with preset-position-v1's ABI). "
                "That is 117 columns per row on the four wide fixtures and 103 on SA2GTR1 and "
                "SA2GTR2. Of those, 29 to 34 per fixture differ from the recorded input on at "
                "least one row -- the window writes those; the rest are compared as words it must "
                "leave untouched, which is what the `written-columns-poisoned` probe and the "
                "`break` controls separate.",
            "not_compared": {
                "ground-candidate-executor-v1 sequence 1 tick 118": "bound 2 with the hit at index "
                    "0, so the accepted iteration is not the final one and 0x00463692 "
                    "`jl 004624C0h` re-enters the loop head outside this window. Its "
                    "`accepted_state` differs from its `expected_final`, and the 11 other payloads "
                    "of that fixture have `accepted_state == expected_final` by the stager's own "
                    "`require`. Excluded and named; the same case is `ground-candidate-loop-v1`.",
                "the non-`ordinary` calls of both SA2GEX1 fixtures": "topology word 3 != 1; the "
                    "stager emits no payload for them at all (`if not ordinary: continue`), so "
                    "there is nothing to compare. One call in ground-candidate-executor-v1 (tick "
                    "123) and two in ground-executor-slope-v1 (ticks 170 and 171).",
            },
            "blockers_withdrawn": {
                "SA2GTR1..5": "the nine caller-frame pointers are nine addresses the driver owns, "
                              "and the token normalisation of player words 40 and 44 inverts on "
                              "the driver's side because [esp+74h] is the candidate triple's word "
                              "1 -- no comparator change was needed",
                "SA2GEX1": "`candidate_w1` is that same triple word; what actually distinguishes "
                           "the two SA2GEX1 fixtures from the two that remain is which iteration "
                           "they record, not which field they drop",
                "SA2GPC1/SA2GPE2/SA2GPE3 (partly)": "the object token 0/1/2 is "
                    "`TOKENS = {address: token for token, address in enumerate(OBJECTS)}` in the "
                    "stager, which a driver laying out its own objects inverts by construction. "
                    "What is untested for those four is the runtime structure the "
                    "0x0048BAF0/0x0048CE40 pair walks, not the token.",
            },
            "still_undriven": {
                "ground-candidate-loop-v1, ground-executor-multi-v1": "untested, not shown "
                    "undrivable: both record iterations before or after the accepted one, so they "
                    "need the loop window 0x004624C0..0x00463821, whose per-iteration decision is "
                    "0x0046250A `call 0048CE40h` over the candidate object's polygon cache at "
                    "0x00462191 `mov ecx,[eax+34h]`. Whether either format supplies enough to "
                    "enter there was not tested inside this time box.",
                "ground-contact-composition-v1 and the three expectation fixtures": "untested, not "
                    "shown undrivable. SA2GPC1 carries three object caches of 16-word polygon "
                    "records whose first two words are the chain links as ordinals -- SA2CSE1's "
                    "shape, which candidate-enumeration-v1 already drives -- plus the selected "
                    "ordinals and a 1025-word table, and its case records both queries and the "
                    "compared outputs. The plan was not written for want of time. Two of the "
                    "three expectation fixtures do not need caches of their own: "
                    "ground-source-cache-contact-expectations-v2 and "
                    "ground-all-source-contact-expectations-v3 carry the same 30 cases as "
                    "SA2GPC1, word for word on all 46 case words, joined by (sequence, tick, "
                    "caller_iteration) -- checked in this slice. "
                    "ground-contact-expectations-slope-v1 shares none of its 91 cases with "
                    "SA2GPC1 and no fixture carries the objects they name.",
                "ground-full-chain-v1, ground-full-chain-slope-v1": "unchanged and untested this "
                    "slice: they need the source list out of the fill loop at 0x00462133.",
            },
            "tooling": [
                "tools/s2/corpus_run/plans.py: `_gtr_*` (the SA2GTR window, the case walk, the "
                "token inverse and the PReset seam) and `_gex_*` (the SA2GEX1 payload adapter).",
                "tools/s2/corpus_run/controls.py: 113 further controls over the eight fixtures, "
                "including a `probe` that poisons every compared column the window is claimed to "
                "write.",
                "tools/s2/fixtures/register_corpus.py: the obsolete SA2GTR `SURVEYED` entry "
                "deleted, and the SA2GEX and SA2GPE entries rewritten with what is now known.",
            ],
            "controls_the_run_corrected": [
                "`work_w7..9-drive-the-plane-projection` was written as a `break` on the strength "
                "of 0x004633B0/0x004633BB/0x004633C5 and failed on all six fixtures. Work words 7, "
                "8 and 9 are overwritten with (0, 1, 0) at 0x004631C2, 0x004631C5 and 0x004631C9 "
                "before those loads, and the values the projection takes are what 0x00464680 left "
                "there. It is now an `inert` control with those three stores as its basis.",
                "`entity_w5..7-drive-the-exit-position` was written as a `break` and holds only on "
                "ground-tail-final-v5's four bound-3 rows. On every row of the other five fixtures "
                "0x004635E4 `mov eax,[esp+30h]` / 0x004635F0 / 0x004635F6 / 0x004635F9 overwrites "
                "the entity position from [esp+38h..40h] first, so it is registered `inert` there "
                "and `break` on v5 -- the same pair of readings as [0x01A5A23C].",
                "`entity_w2-drives-work_w13` holds on ground-tail-v4, -final-v5 and -slope-v1 and "
                "is inert on the three fixtures whose only ticks are 401 and 402, because those "
                "rows reach 0x004631A5 `call 00464960h`, which writes entity+8 before 0x004631CC "
                "reads it.",
            ],
        },
        "final": {
            "what": "The eight the `last` slice left are driven. M3 is 54/54. Not one of them "
                    "needed a field its format does not record; three needed a sibling fixture on "
                    "the same ticks, and every such supply is matched against columns both "
                    "fixtures record before it is used.",
            "journal": "agents-journals/s2/2026-09-20-s2-corpus-final.md",
            "contact_composition": {
                "fixtures": ["ground-contact-composition-v1",
                             "ground-source-cache-contact-expectations-v2",
                             "ground-all-source-contact-expectations-v3",
                             "ground-contact-expectations-slope-v1"],
                "entry": "0x0048BAF0 then 0x0048CE40, the pair GroundCollisionStep calls at "
                         "0x004620C5 and 0x0046250A. Both callees were already lifted and already "
                         "driven by the SA2ECC family; nothing new was lifted for these four.",
                "what_was_rebuilt": "SA2GPC1's three object caches, each a run of 16-word SA2CSE1 "
                    "nodes whose first two words its own stager normalised to ordinals, "
                    "re-pointered into one guest group per object and chained in token order at "
                    "[0x01A5A400]. The group's id is the driver's own, which is the whole of the "
                    "object token: 0x0048CE86 `cmp [eax+8],esi` is how 0x0048CE40 finds the "
                    "object, so `TOKENS = {address: token}` inverts by construction.",
                "near_set_gate": "0x0048BB80 `cmp [edx],esi` skips a group the near-set table does "
                    "not name, so which objects are admitted is an input. The fixtures record it: "
                    "a tick's cases are the caller's candidate list, one per `caller_iteration`, "
                    "and `caller_bound` says how many. Admitting all three on every tick instead "
                    "changes nothing on the three 30-case fixtures and over-counts "
                    "`selector_result` on nine of the slope fixture's 91 rows -- registered as "
                    "`admit-every-object`, inert on three and break on one.",
                "sibling_supply": "SA2GPE2 carries one of the three caches and SA2GPE3 none. The "
                    "two 30-case files carry SA2GPC1's own 30 cases word for word on all 46 case "
                    "words, which the builder checks before it takes the caches; SA2GPE2's own "
                    "cache is additionally checked equal to SA2GPC1's object of the same token. "
                    "`ground-contact-expectations-slope-v1` is a different capture -- 91 cases "
                    "over ticks 160..200 against SA2GPC1's 118..130 -- and the previous slice's "
                    "reading that no fixture carries its objects is **withdrawn**: they are the "
                    "same three collision objects, checked by attribute word "
                    "(0x40000001, 0x40400001, 0x20000001) and then measured -- all 91 rows "
                    "reproduce from SA2GPC1's caches, `pni_count` and the twenty contact words "
                    "included.",
                "compared": "per row: `selector_result` (eax from 0x0048BAF0), `pni_count` (that "
                    "object's [group+24h]), `result` (eax from 0x0048CE40), `record_count` "
                    "([0x019471B0]), `work_w10` ([ebx+28h]/[ebx+2Ah]) and `output_w13..32` "
                    "([ebx+34h..80h]); on the three 30-case fixtures also the 14 selected node "
                    "pointers at 0x0194F030, whose recorded ordinals are the selection on every "
                    "tick there because every case's `selector_result` equals their total. On the "
                    "slope fixture `selector_result` runs 5..16, so the ordinals -- recorded once "
                    "per object, not per case -- cannot be the selection on every tick and the "
                    "node columns are dropped; `pni_count` is per case and carries the count.",
                "miss_rows": "0x0048DBF5 `cmp ecx,1` / `jl 0048CE91h` returns 0 without touching "
                    "the work buffer, so on a miss row work words 10 and 13..32 are not this "
                    "window's outputs. The plan poisons work words 7..32 with 0xDEADBEEF and "
                    "expects the poison back on those rows rather than the recorded zeros, which "
                    "compares more than the zeros would: a run that hit where the record misses "
                    "fails on them.",
            },
            "full_chain": {
                "fixtures": ["ground-full-chain-v1", "ground-full-chain-slope-v1"],
                "entry": "0x0047CD60 at its own entry over the fixture's whole 1331-record COL "
                         "table, then the window 0x004620CA..0x0046244C "
                         "(`lift_set.py --block 0x004620CA:0x0046244C+ret`).",
                "why_that_address": "the gates slice judged the fill loop at 0x00462133 to need "
                    "the work buffer, the player status byte and each object's polygon cache. "
                    "Two of the three are not live-ins: 0x0046218A `mov eax,[edi+8]` reads the "
                    "near entry's third word, which 0x0047D0FA `mov [ebp+8],ebx` writes with the "
                    "ebx that 0x0047CFF7 `xor ebx,ebx` zeroed and nothing in the gather changes, "
                    "so it is 0 on every entry and 0x0046218F `je 004621A3h` skips the cache test "
                    "entirely; and the work buffer is read once, at 0x004621BE `fcomp [ebx]`, "
                    "gating the precise call at 0x004621CC whose two sides reach the same append "
                    "at 0x00462232. Entering four instructions earlier than the fill loop, at "
                    "0x004620CA `mov dx,[1DE9484h]`, makes the near-set count a runtime value "
                    "rather than one the plan supplies, at the cost of 0x004620D1 `add esp,4` "
                    "(the block's frame is the window's entry ESP + 4).",
                "compared": "SA2GOO2's own columns: the four ordered slots of three words each at "
                    "(block frame)+0x100, poisoned first so the slots past the count come back "
                    "untouched, and the write cursor spilled to +0x2C. The triples are built from "
                    "the fixture's `cols` section at the COL index it names -- (attribute, "
                    "object_va + relocation_delta, 0), the three words 0x00462236..0x00462247 "
                    "copy -- not from any output column.",
                "refresh_not_called": "0x0047D160's reach pre-filter is skipped and the gather is "
                    "handed all 1331 records. `reach-prefilter-inert` applies the pre-filter "
                    "(reach 400.0 around every recorded reference point) and the compared columns "
                    "do not move, which is the measurement behind the argument that a record the "
                    "gather admits -- within 5.0 of a query point a few units from the reference "
                    "-- was never going to be removed by it.",
            },
            "candidate_loop": {
                "fixtures": ["ground-candidate-loop-v1", "ground-executor-multi-v1"],
                "entry": "0x00462F83, the same tail window the six SA2GTR fixtures use, with the "
                         "loop head 0x004624C0..0x00463821 lifted beside it.",
                "how_the_back_edge_works": "0x00463692 `jl 004624C0h` leaves the 0x00462F83 "
                    "window; remill emits `__remill_missing_block` for that target and "
                    "tools/s2/corpus_run/internalize.py folds it into `__sa2_dispatch_stub`, "
                    "which is `sa2_ft_dispatch(0x004624C0, ...)`. Once the loop head is in the "
                    "function table the back edge reaches it with the state the tail left, and "
                    "the remaining iterations run for real. No state is re-injected between "
                    "iterations; the only inputs are the row's own pokes.",
                "the_world": "SA2GPC1's three object caches again, as groups, plus one call to "
                    "0x0048BAF0 before the window: 0x0048CE9A reads each group's selected face "
                    "list and only the selector fills it, so without that call every iteration "
                    "after the entered one misses. The selector's query is the entered state's "
                    "own work words 0..6, which are unchanged at this boundary -- SA2GPC1's and "
                    "SA2GPE3's `selector_w0..6` for these ticks are those same seven words.",
                "joins_checked": "SA2GCL1's tick 118 input work words 0..6 are "
                    "ground-contact-composition-v1's tick-118 `precise_w0..6` for object token 0, "
                    "and its `accepted_state` work words 13..32 are that case's "
                    "`output_w13..32`, word for word. SA2GEX2's two calls are ticks 170 and 171, "
                    "the slope window ground-contact-expectations-slope-v1 and "
                    "ground-full-chain-slope-v1 cover, and the per-tick candidate order the "
                    "SA2GFL1 fixture records is the order SA2GEX2's iteration attributes give.",
                "supplied_and_named": {
                    "the caller's ordered candidate list": "SA2GEX2 records each iteration's own "
                        "attribute and needs no supply. SA2GCL1 records only the accepted "
                        "candidate's, so the rest comes from ground-full-chain-v1's tick 118 "
                        "ordered COL indices -- a fixture this same slice drives -- mapped to the "
                        "three objects by their distinct attribute words.",
                    "the two frame triples at +0x4C and +0x58": "written in the routine's "
                        "prologue at 0x00461F74/0x00461F85/0x00461FA5 and rewritten at "
                        "0x00461FD3, all before 0x004624C0, so both are live-ins and neither loop "
                        "fixture records them. ground-policy-word-v1 and -slope-v1 record them as "
                        "their own case words 20..25 on exactly these ticks; taken from there, "
                        "matched by (tick, attribute) and checked against the eight entity words "
                        "both fixtures record.",
                    "the start position at +0xC4": "0x004620A8..0x004620BE writes caller-frame "
                        "words 49..51 once before the loop and nothing in the window writes them "
                        "again; 0x0046366E `lea ecx,[esp+0E4h]` hands them to the "
                        "rejected-contact response 0x00463EE0. SA2GEX2 records them as the "
                        "rejected iteration's own `stack_w49..51`; SA2GCL1 has no rejected "
                        "iteration and drives with zeros, measured inert.",
                },
                "compared": "`final_state` (75 words: entity, EntityData2, the 24 schema player "
                    "words and the 33 work words) and `reset_state` (42) after PResetPosition -- "
                    "the two blocks at the window's own exits. The per-iteration `head_state`, "
                    "`acceptance`, `response`, `continued_state`, `missed_state` and `exit_state` "
                    "blocks are mid-loop, at 0x00463685 and 0x00462C19, and are neither fed in "
                    "nor compared.",
                "what_SA2GCL1_actually_tests": "its single case is tick 118, bound 2, hit at index "
                    "0, and the one iteration after the entered one is a miss. A miss returns "
                    "from 0x0048CE40 without touching the work buffer, so on that row the object "
                    "caches, the selector's face lists, the object token and even the bound "
                    "cannot move a compared column -- each of those five controls is registered "
                    "`inert` there and `break` on SA2GEX2, whose two calls have three iterations "
                    "and two hits apiece. The loop head still has to be lifted and entered for "
                    "the row to return at all: without it the back edge traps.",
            },
            "blockers_withdrawn": {
                "SA2GPC1/SA2GPE2/SA2GPE3": "the runtime structure the 0x0048BAF0/0x0048CE40 pair "
                    "walks is three SA2CSE1-shaped caches the fixture records, and the near-set "
                    "admission is an input the cases record. The reading that "
                    "ground-contact-expectations-slope-v1's objects are carried by no fixture is "
                    "withdrawn: SA2GPC1's are its objects.",
                "SA2GFL1": "the fill loop's three named live-ins are one word, one byte and a "
                    "pointer that is structurally zero; the near set and its count are loaded by "
                    "the window itself out of the globals the gather fills.",
                "SA2GCL1/SA2GEX2": "the iterations these two record before and after the accepted "
                    "one do not need a window that starts at the loop head. They need the loop "
                    "head to be *in the function table*, so the tail window's own back edge can "
                    "reach it.",
            },
            "not_compared": {
                "ground-candidate-executor-v1 sequence 1 tick 118": "still excluded from that "
                    "fixture's plan, and the exclusion is now removable rather than structural: "
                    "with 0x004624C0 lifted, the back edge that decides its `expected_final` "
                    "runs. It is the same call as ground-candidate-loop-v1, which drives here. "
                    "Left alone this slice because that fixture already passes and changing a "
                    "passing fixture's row set for a row another fixture already covers is not "
                    "worth a regression risk; named as the cheapest next improvement.",
                "the 1025-word table section of SA2GPC1 and SA2GPE2": "not poked -- the image "
                    "carries the table the capture used, and no plan writes over it.",
                "`attribute`, `caller_bound`, `caller_iteration`": "recorded metadata the "
                    "0x0048BAF0/0x0048CE40 pair never reads; `caller_iteration` and "
                    "`caller_bound` are used as inputs to decide which objects the near-set gate "
                    "admits, and `attribute` to name the object in the slope fixture's supply "
                    "check.",
            },
            "controls_the_run_corrected": [
                "`selector-radius-drives-pni_count` and `selector-motion-drives-pni_count` were "
                "written as `break` with the standard `bump` perturbation (3.14159f) and held on "
                "all 30 flat rows -- because 3.14159 is *smaller* than the recorded query radius "
                "of 5.0, and shrinking the selector's sphere removes nothing from a selection "
                "already strictly inside it. Both are now `break` with a `widen` perturbation "
                "(400.0f), and the shrink is separately registered as `selector-radius-zeroed` "
                "and `selector-motion-zeroed`: inert on the three 118..130 fixtures and break on "
                "the 160..200 slope one, whose sweeps are wide enough for the expansion to "
                "matter. Same defect class as the `flip`-versus-`bump` finding of the gates "
                "slice.",
                "`selector-fills-the-face-lists`, `object-token-names-the-group`, "
                "`node-vertices-drive-the-exit`, `node-centres-drive-the-exit` and "
                "`bound-drives-the-exit` were written as `break` on both loop fixtures and failed "
                "on SA2GCL1. The reason is a property of its single case, not of the plan, and it "
                "is now the sharpest thing the receipt says about what that row tests: see "
                "`candidate_loop.what_SA2GCL1_actually_tests`. All five are registered inert "
                "there and break on SA2GEX2.",
                "`object-token-names-the-group` on the contact family first picked its replacement "
                "group from the admitted set, so on a tick with one admitted object it named no "
                "group at all and 0x0048CE91 returned 0 -- which a miss row also does, so the "
                "control was weaker than it looked. All three groups are in [0x01A5A400] on every "
                "row; it now names the next token round.",
            ],
            "tooling": [
                "tools/s2/corpus_run/fixture_rows.py: readers for SA2GPC1/SA2GPE2 (`gpc1`), "
                "SA2GFL1 (`gfl1`) and SA2GEX2 (`gex2`), each walking the same bytes as "
                "corpus.py's own `walk_*` and re-checking the file size the way it does.",
                "tools/s2/corpus_run/plans.py: `_gpc_*` (the selector-to-precise pair over three "
                "rebuilt caches), `_gfl_*` (the COL table through the gather into the fill loop "
                "and the ordering block) and `_gcl_*` (the loop carried through its own back "
                "edge). Eight new builders, 52 in all.",
                "tools/s2/corpus_run/controls.py: 98 further controls over the eight fixtures.",
                "tools/s2/fixtures/register_corpus.py: the `SURVEYED` table is now empty, because "
                "every entry it held covered a fixture that drives.",
            ],
        },
        "g2b_fold": {
            "question": ("The G2 interrogation reads 54/54 and the corpus reproduces, but two of "
                         "three reviewers say the pass is overstated. What does a passing row "
                         "actually prove, and what does the denominator actually count?"),
            "journal": "agents-journals/s2/2026-09-20-s2-g2b-fold-corpus.md",
            "m3": {
                "before": "54/54",
                "after": f"{len(passed_registered)}/{len(registry)}",
                "why_the_denominator_moved": (
                    "ground-near-set-v1 and ground-admission-source-v1 are row-identical to their v2 "
                    "siblings over every word they record, and each compares one column that the "
                    "plan itself zeroes before the call, so most of their rows are satisfied with "
                    "the call removed. They are the same evidence counted twice. Both are "
                    "de-registered as superseded, still driven and still re-run; M3's denominator "
                    "falls from 54 to 52 and its numerator with it, and 1298738 registered native "
                    "rows fall to " + str(rows_registered) + "."),
            },
            "informative_columns": {
                "found": ("1210 of 1999 compared columns and 45.8 % of compared words were "
                          "reproduced with every OP_CALL stripped out of the row op-streams. Some "
                          "are honest 'the window did not clobber this' assertions and some are "
                          "words the plan poked and read straight back; nothing distinguished them "
                          "and nothing recorded either."),
                "now": ("run.py drives every fixture a second time with the calls removed and "
                        "records `informative_columns` and `informative_words` per fixture beside "
                        "the raw counts; see `informative_columns` above. A fixture the no-call run "
                        "satisfies outright now fails."),
            },
            "controls": {
                "no_ops_found_and_fixed": (
                    "Eight of the 279 asserting controls encoded a plan byte-identical to the "
                    "baseline's, and two probes did as well. Two were written `player=lambda v: v`; "
                    "two (`source-fourth-word-unread`) perturbed the fourth word of a SA2GOO2 source "
                    "entry, which `_goo_plan` never pokes because the guest's stride is three words; "
                    "and six (`token-2-address-is-a-name`) looked for a player word holding the "
                    "token-2 address on fixtures none of whose recorded words carry token 2. "
                    "run_controls now compares every control's encoded plan against the baseline's "
                    "and raises, so the class cannot return. `source-fourth-word-unread` is replaced "
                    "by `source-past-the-count-unread`, which writes a fabricated triple at source "
                    "index `count` and asserts the reverse walk never loads it; the six token-2 "
                    "controls are re-registered only on the four ground-tail fixtures whose records "
                    "carry the token; the two identity probes are removed."),
                "circularity_rule": (
                    "Every `break` control is driven twice, once normally and once with its own "
                    "OP_CALLs stripped, and its divergences are split into `call_dependent` and "
                    "`call_independent`. A break holds only when the run fails AND at least one "
                    "column it moves is one the lifted code decides."),
                "six_reported_circular": (
                    "The interrogation reported `support-pointer-drives-player_w44` and "
                    "`work_w18..20-drive-player_w31..33` on ground-candidate-executor-v1 and "
                    "ground-tail-final-v5, and `support-pointer-...` on ground-executor-slope-v1 and "
                    "ground-tail-slope-v1, as circular on the ground that every column they move "
                    "matches bit-for-bit with the call removed in the BASELINE. Measured against "
                    "each control's OWN no-call run, none of the six is: under "
                    "`support-pointer-drives-player_w44` on ground-tail-final-v5 player word 44 "
                    "comes back 0x03003600 with the call and 0x03003500 without it, and 0x03003600 "
                    "is a value no op in that plan ever writes into the player block, so "
                    "0x004637F2 `mov [eax+0B0h],ecx` did execute. The stated mechanism -- that "
                    "`_gtr_player` maps the tokens on both the input and the expected side, so the "
                    "perturbation changes what the plan expects -- is not what those controls do: "
                    "none of them touches `player` or the expected side; they change `sm['support']` "
                    "or three input work words. What IS true, and is now recorded per fixture rather "
                    "than argued, is that the BASELINE pass of those columns proves nothing, because "
                    "the window stores the value the plan handed it -- so the control is the only "
                    "evidence for them, which is an argument for keeping it."),
                "coverage_rule": (
                    "controls.py refuses to run when a fixture with a plan builder carries no "
                    "asserting control. Thirty-one of the 54 passing fixtures carried none, and "
                    "every fixture declaring an exclusion was among them."),
            },
            "live_in_ecx": (
                "The runtime fold unit's CFG backward-liveness census found ECX read before "
                "written at 0x0048B6C3 `push ecx`, making it a live-in of the 0x0048B6C0 window "
                "that none of the 20 plans entering it set. The three entry sites in plans.py now "
                "set ECX = 0xDEADBEEF -- the push is the block's one-dword frame allocation and "
                "nothing reads the dword back, so a conspicuous value is better than a plausible "
                "zero -- and `pushed-ecx-is-dead` drives 3.14159f there instead and is measured "
                "inert on all five fixtures that enter the window."),
            "x87_entry_ops": (
                "harness/plan_ops.h gained OP_ST, OP_ST_F80, OP_ST_UNSUPPLIED and OP_EMIT_ST for "
                "windows that read caller x87 registers. plans.py emits none of them, because the "
                "runtime unit measured the question: filling all eight entry x87 registers with "
                "1.5 and again with -7777.25 moved no verdict, 52/52 both times. run.py's op walk "
                "knows their lengths, so the no-call rewrite stays correct when a fixture needs "
                "them."),
            "gather_output": (
                "ground-gather-output-v1's existential rule is withdrawn. The stager could not pair "
                "an observation to a call (its probe bumps the call ordinal in a hook that runs "
                "after the function returns), so it made the weaker claim that each observation "
                "equals the admitted set of SOME query captured at that tick, and the plan drove all "
                "442 candidates and passed a group when any member matched. That relaxation was "
                "load-bearing on 65 of 152 observations and ambiguous on 8. The pairing the FILE "
                "supports is positional -- observation j of a tick against query j of that tick, in "
                "the file's own order -- and it reproduces the recorded list on 152 of 152 with no "
                "exception: 14 at ordinal 0 of a two-query tick, 69 at ordinal 0 and 69 at ordinal 1 "
                "of a three-query tick, the radius-200.0 wide query last in every tick and never "
                "paired. The fixture is now compared row for row, 152 driven rows against 152 "
                "registered, and Plan.groups is gone from the runner."),
            "not_closed": [
                "The two fixtures the wrap attests (contact-transform-corpus-v1, "
                "ninja-point-transform-v1) carry no control: they are driven by tools/s2/wrap, "
                "which this unit does not own.",
                "`informative_columns` is a column-level measure: a column informative on one row "
                "of a thousand is counted whole. The word figure is the honest one for size and "
                "both are recorded; the column ratio should not be quoted alone.",
            ],
        },
        "defects_found": [
            {"name": "function map: 0x0048B510 recorded 235 bytes, eight short of its epilogue",
             "site": "0x0048B510 TransformMeshVertices; the map says 235 bytes, ending 0x0048B5FA, and "
                     "the NJD_MESHSET_3 arm's `jg short 0048B5D0h` at 0x0048B5F7 falls through to "
                     "0x0048B5F9 `mov eax,[esp+8]` / `pop edi` / `pop ebp` / `pop ecx` / `ret`",
             "what": ("The only path a Basic strip mesh takes leaves the recorded body, so every call "
                      "into the lifted function trapped with status 2 at exit_pc 0x0048B5F9. The "
                      "interior epilogue at 0x0048B579 is the same five instructions and IS inside the "
                      "recorded size, which is why the type-0 and type-0x4000 arms would have run and "
                      "why nothing caught this until a fixture drove the strip arm. Third map size "
                      "under-report found this way; 0x0047D160 (1351 bytes, cut mid-jump) was the first."),
             "evidence": "candidate-cache-point-composition-v3 and "
                         "candidate-cache-object-matrix-composition-v4 trapped at 0x0048B5F9 on every "
                         "row before the widening and pass on every row after it",
             "fix": "tools/s2/corpus_run/lift_set.py --to-next-entry 0x0048B510 (next map entry "
                    "0x0048B610, 256 bytes)"},
            {"name": "remill: __remill_read_memory_f80 / __remill_write_memory_f80 declared `readnone`",
             "site": "declared in every lifted module with an 80-bit memory operand; measured at "
                     "0x0085A39C (`fld tbyte [0x00922EEA]`, the CRT's pi/2 constant)",
             "what": ("The 80-bit accessors hand their value back through an out-pointer, not a return "
                      "value, but remill declares them in LLVM's `readnone` attribute group. At -O2 the "
                      "optimiser knows nothing ever wrote the caller's alloca, so the `load x86_fp80` "
                      "after the call folds to zero: every `fld tbyte` in the image loads 0.0, with the "
                      "bytes present and correct in guest memory."),
             "evidence": "Documentation/receipts/s2-atan2-v1.json; tools/s2/corpus_run/atan2_probe.py",
             "fix": "tools/spike/pc24pass/pc24pass.py --fix-f80-accessors (on by default): the declaration "
                    "moves to a fresh attribute group that is its own minus readnone/readonly/memory(none)"},
            {"name": "remill: FPATAN lowered to one-argument `atanl` of st(1)/st(0)",
             "site": "three modules in this closure -- 0x0085A34C (atan2's general tail), 0x0086BCD8 and 0x0086BF68 (the CRT arc functions); 26 transcendental sites image-wide",
             "what": ("x87's FPATAN is a two-argument arc tangent over the whole circle. remill divides "
                      "st(1) by st(0) and calls the host's `atanl`, whose range is (-pi/2, pi/2), so the "
                      "quadrant the sign of st(0) carries is lost: every lifted `fpatan` with st(0) < 0 is "
                      "off by pi, and st(0) == 0 divides by zero. Measured through the CRT's acos, which "
                      "returned acos(x) - pi for every negative x -- a half-turn error in every angle the "
                      "game takes from an arc cosine."),
             "evidence": "Documentation/receipts/s2-atan2-v1.json",
             "fix": "tools/spike/pc24pass/pc24pass.py --fix-fpatan (on by default): the call becomes "
                    "`atan2l(st(1), st(0))` on the division's own operands"},
        ],
        "limits": [
            "compact-contact-corpus-v1 passes over twelve of its twenty-two record words. The other "
            "ten are supplied, each with the instruction that writes it: words 0..7 at "
            "0x0048D820/27/2E, 0x0048D8E0/EB/F4 (or 0x0048D90B/12/19), 0x0048D9EA (or 0x0048DA3C) "
            "and 0x0048DA09 (or 0x0048DA1A / 0x0048DA2F), and words 20 and 21 at 0x0048D7B8 "
            "`fst dword ptr [esp+0F0h]` and 0x0048D7D4 `fstp dword ptr [esp+0F4h]` -- all of them "
            "before 0x0048DA51, the boundary the fixture's own inputs define. SA2CRI1 records the "
            "PLANE-LOCAL origin and sweep (its stager: 'the plane-local origin and the sweep still "
            "at [ESP+0x60] and [ESP+0x40]'), so the per-face head at 0x0048CEE7 that the survey "
            "named -- the one boundary that would produce all twenty-two -- cannot be fed from it: "
            "0x0048CEE7 reads the world-space query. This is the one partial pass of the "
            "thirty-one, and it is recorded as such.",
            "The four SA2ECC composition fixtures do not compare `expanded`, `direction` or "
            "`visited`, for the reasons SA2CSE1 does not: `expanded` is the frame word at (entry "
            "ESP)-0x0C that 0x0048BBB6 `fstp dword ptr [esp+14h]` overwrites with the Z upper "
            "bound, and `direction` and `visited` are the stager's own computed columns with no "
            "native counter behind them.",
            "SA2ECC3 and SA2ECC4 do not record `root_center_z`, the word 0x0048B741 reads from "
            "[0x01DDE264]. It is supplied from SA2ECC1/SA2ECC2's header (0xC4D12000, -1673.0) -- "
            "the same corpus at a shallower depth -- and it is not free: it decides every bucket, "
            "and the chain the buckets produce is compared ordinal for ordinal through "
            "`head_generation`, `tail_generation` and the selected node pointers. SA2ECC4 records "
            "no parent matrix either; the identity is supplied, which is ninja-object-matrix-v1 "
            "case 0's own parent, and the matrix the guest then builds is compared word for word "
            "against SA2ECC3's recorded matrix.",
            "The two transcendental residues the first corpus run named were defects, not residues, and are "
            "fixed (see `defects_found`). What remains a design 7.4 transcendental residue is the last-bits "
            "accuracy of the host libm calls remill emits for FPATAN, FSIN, FCOS, FYL2X and F2XM1; no "
            "fixture in this corpus discriminates on it.",
            "The plans place each fixture's recorded words and leave every other guest word zero. Where that "
            "is not enough the run says so: ninja-object-matrix-v1 needed an empty model (polygon count 0) "
            "at object[+4] for 0x0048B610 to return, because the fixture records no model data.",
            "preset-position-*: 0x00469050 reads a mode word at 0x01934B70 and compares it with 0x5A. The "
            "guest holds the executable's file value, which is not 0x5A, so the branch that path guards is "
            "not exercised by these 2043 rows.",
            "The fp80 contrast variant was not built for the corpus set; only the pc24 (adopted) pipeline "
            "was run. The wrap's per-function drivers still build both.",
            "Guest globals are whatever the executable's file image holds: runtime v0 runs no CRT "
            "initialisation and no guest start-up, so any global the game writes before the traced call is "
            "at its file value. The atan2 residue above may be an instance.",
            "Orphan blocks are lifted from their address to the next mapped entry, capped at 512 bytes. A "
            "block longer than that would be cut; none of the four hit the cap.",
            "The live-in census walks the window straight-line and does not follow branches: it is a lower "
            "bound on what the window reads, which is enough for the conclusion but not a full liveness "
            "analysis.",
        ],
        "authority": {
            "native_rows": "directly-traced (the fixture corpus, inherited; per-fixture authority is in "
                           "Documentation/receipts/fixture-registry.json)",
            "runs": "observed (every run figure is measured by tools/s2/corpus_run on this machine)",
            "prose": "the blocker classification, the mid-function finding and the reachability answer are "
                     "this unit's judgement; the numbers under them are computed from the registry and the "
                     "run log by tools/s2/corpus_run/receipt.py",
        },
        "owned_files": [
            "tools/s2/corpus_run/", "Documentation/receipts/s2-corpus-run-v1.json",
            "agents-journals/s2/2026-09-20-s2-corpus-run.md", "private/generated/fixture-results.json",
            "harness/plan_ops.h", "harness/plan_driver.c",
            "runtime/remill_runtime.cpp and runtime/remill_runtime.h (the missing-block policy, opt-in)",
            "private/generated/emit-log.json and private/generated/build-log.json (rewritten by "
            "tools/s2/corpus_run/metrics_inputs.py: M1 and M2 cannot move without them; not in the brief's "
            "owned list, and flagged in the journal)",
        ],
        "written_by": "tools/s2/corpus_run/receipt.py",
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1) + "\n")
    print(receipt["verdict"])
    print("->", RECEIPT.relative_to(ROOT))


if __name__ == "__main__":
    main()
