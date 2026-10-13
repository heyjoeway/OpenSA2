#!/usr/bin/env python3
"""What the lifted image does with jump tables, indirect calls and EH.

Design 5 (S3) lists *"jump tables, indirect call table, thunks, SEH stubs per
the G1 strategy"* as stage work, and nothing had measured the lifted image
against it. The analyzer's map records the edges; this asks what survived the
lift.

The question that matters for a jump table is not how many arms the analyzer
found, it is whether the lifted program can reach them. Two independent
answers are computed for every arm:

  * **is it lifted?** remill decodes by recursive descent from the entry and
    stops at an indirect branch, so an arm reachable *only* through the table
    is never decoded and no block exists for it anywhere. This is decided by
    running `census.walk` twice per function, once seeded with the map's
    jump-table targets and once not, and it is confirmed at the IR level: for
    every arm whose first instruction carries a distinctive 32-bit immediate,
    the owning module is searched for that immediate.
  * **can the dispatcher reach it?** an indirect branch lifts to
    `__remill_jump`, which `runtime/remill_runtime.cpp` sends to
    `sa2_ft_dispatch`, and the function table holds one row per *map entry*.
    An arm that is not itself a map entry is a guaranteed dispatch miss.

Indirect calls are counted the same way, and the EH surface is checked against
the G1 decision (`Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md`
section 2, step 1: "registration and unregistration are plain guest-memory
writes to FS:[0], lifted as such").

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/whole_image/indirect.py [--tag next] [--jobs 16]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import census as CENSUS  # noqa: E402

from iced_x86 import Decoder, Formatter, FormatterSyntax, OpKind  # noqa: E402

OUT = C.GEN / "s3" / "link"

JUMP = re.compile(r"call ptr @__remill_jump\(")
FSBASE_USE = re.compile(r"load i32, ptr %FSBASE")
CALL = re.compile(r"call ptr @__remill_function_call\(")
MISSING = re.compile(r"call ptr @__remill_missing_block\(")
FMT = Formatter(FormatterSyntax.INTEL)

_LL: Path | None = None


def _init(ll: str) -> None:
    global _LL
    _LL = Path(ll)


def ir_counts(va: int) -> tuple[str, int, int, int, int]:
    text = (_LL / f"sub_{va:08X}.ll").read_text(errors="replace")
    return (f"0x{va:08x}", len(JUMP.findall(text)), len(CALL.findall(text)),
            len(MISSING.findall(text)), len(FSBASE_USE.findall(text)))


def immediate_probe(job: tuple[int, list[str], str]) -> dict:
    """Is an arm's own code in the owning module?

    Decodes the first instructions at each arm, takes a 32-bit immediate or
    displacement distinctive enough to search for, and looks for it in the
    module's IR as LLVM prints it (a signed decimal). A hit means remill lifted
    the arm; a miss means it did not. Arms with no distinctive operand are
    skipped rather than guessed at.
    """
    va, targets, path = job
    text = Path(path).read_text(errors="replace")
    checked = hits = 0
    misses: list[str] = []
    global _IMG
    for t in targets:
        ta = int(t, 16)
        off = ta - C.IMAGE_BASE
        dec = Decoder(32, _IMG[off:off + 24], ip=ta)
        value = None
        for ins in dec:
            if ins.is_invalid:
                break
            for i in range(ins.op_count):
                kind = ins.op_kind(i)
                v = None
                if kind == OpKind.IMMEDIATE32:
                    v = ins.immediate32
                elif kind == OpKind.MEMORY:
                    v = ins.memory_displacement
                if v and v > 0xFFFF:
                    value = v
                    break
            if value is not None:
                break
        if value is None:
            continue
        checked += 1
        signed = value - (1 << 32) if value >= (1 << 31) else value
        if re.search(rf"(?<![0-9]){signed}(?![0-9])", text):
            hits += 1
        else:
            misses.append(t)
    return {"va": f"0x{va:08x}", "checked": checked, "found_in_ir": hits,
            "absent_from_ir": misses[:8], "absent": checked - hits}


_IMG: bytes = b""


def _init_img(ll: str) -> None:
    global _IMG, _LL
    _LL = Path(ll)
    _IMG = C.memory_image()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--probe-limit", type=int, default=400,
                    help="functions to confirm at the IR level (0 = all)")
    args = ap.parse_args()
    t0 = time.time()

    fmap = C.by_address()
    starts = sorted(fmap)
    ends = [va + fmap[va]["size"] for va in starts]
    image = C.memory_image()
    lift = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")
    entries = set(starts)

    def classify(target: int) -> str:
        if target in entries:
            return "map_entry"
        i = bisect.bisect_right(starts, target) - 1
        if i >= 0 and starts[i] <= target < ends[i]:
            return "inside_entry"
        return "gap"

    # ---- 1. the map's own indirect surface
    surface = {r: collections.Counter() for r in C.REGIONS}
    for va, fn in fmap.items():
        s = surface[fn["region"]]
        s["functions"] += 1
        s["jump_tables"] += len(fn.get("jump_tables") or [])
        s["jump_table_targets"] += sum(len(jt.get("targets", []))
                                       for jt in fn.get("jump_tables") or [])
        s["functions_with_jump_tables"] += bool(fn.get("jump_tables"))
        s["indirect_call_sites"] += int(fn.get("indirect_call_sites") or 0)
        s["functions_with_indirect_calls"] += bool(fn.get("indirect_call_sites"))
        s["unresolved_indirect_jumps"] += int(fn.get("unresolved_indirect_jumps") or 0)
        s["functions_with_unresolved_jumps"] += bool(fn.get("unresolved_indirect_jumps"))
        s["fs0_sites"] += int(fn.get("fs0_sites") or 0)
        s["functions_with_fs0"] += bool(fn.get("fs0_sites"))
        s["import_call_sites"] += len(fn.get("import_calls") or [])

    # ---- 2. every jump-table arm: lifted? dispatchable?
    target_class: collections.Counter = collections.Counter()
    arm_rows: list[dict] = []
    lifted_arms = unlifted_arms = 0
    instructions_only_via_tables = 0
    # Per region as well as image-wide, because design 5's S3 exit is stated
    # over the game region and the S3 interrogation's third finding is that
    # M1's 100 % needs an instruction-level companion in the same region to
    # stop reading as "the game's code is present".
    only_via_tables_by_region = {r: 0 for r in C.REGIONS}
    reach_seeded = reach_plain = 0
    per_region_arm = {r: collections.Counter() for r in C.REGIONS}
    for va in starts:
        fn = fmap[va]
        jts = fn.get("jump_tables") or []
        if not jts:
            continue
        rec = lift.get(f"0x{va:08x}")
        if not (rec and rec.get("lifted")):
            continue
        size = int(rec.get("bytes") or fn["size"])
        seeded = CENSUS.walk(image, va, size, jts)
        plain = CENSUS.walk(image, va, size, [])
        # `walk` returns counts, not the reachable set, so the membership question
        # ("is this arm decoded at all?") needs the set as well.
        reachable_plain = _reachable_set(image, va, size, [])
        reach_seeded += seeded["reachable_instructions"]
        reach_plain += plain["reachable_instructions"]
        behind_tables = seeded["reachable_instructions"] - plain["reachable_instructions"]
        instructions_only_via_tables += behind_tables
        r = fn["region"]
        only_via_tables_by_region[r] = only_via_tables_by_region.get(r, 0) + behind_tables
        for jt in jts:
            for t in jt.get("targets", []):
                ta = int(t, 16)
                cls = classify(ta)
                target_class[cls] += 1
                per_region_arm[r][f"target_{cls}"] += 1
                if ta in reachable_plain:
                    lifted_arms += 1
                    per_region_arm[r]["lifted_by_direct_flow"] += 1
                else:
                    unlifted_arms += 1
                    per_region_arm[r]["only_reachable_through_the_table"] += 1
        arm_rows.append({
            "va": f"0x{va:08x}", "name": fn["name"], "region": r,
            "tables": len(jts),
            "targets": sum(len(jt.get("targets", [])) for jt in jts),
            "targets_lifted_by_direct_flow": sum(
                1 for jt in jts for t in jt.get("targets", [])
                if int(t, 16) in reachable_plain),
            # The IR confirmation asks only about arms this walk says are not
            # lifted: an arm the direct flow also reaches proves nothing.
            "unlifted_targets": [t for jt in jts for t in jt.get("targets", [])
                                 if int(t, 16) not in reachable_plain],
            "instructions_only_via_tables": (seeded["reachable_instructions"]
                                             - plain["reachable_instructions"]),
        })
    print(f"jump tables: {sum(r['tables'] for r in arm_rows)} tables, "
          f"{lifted_arms + unlifted_arms} arms, {unlifted_arms} reachable only "
          f"through the table, {instructions_only_via_tables} instructions behind them "
          f"({time.time() - t0:.1f}s)", flush=True)

    # ---- 2b. the volume check: does the IR look like the arms were lifted?
    #
    # The reachability walk says an arm reachable only through the table is not
    # decoded. That is an argument about remill's algorithm, so it is checked
    # against remill's output by volume: IR lines per reachable instruction is
    # nearly constant across the image, so a jump-table function's IR should
    # match whichever instruction count is really lifted.
    lines_no_jt = insns_no_jt = 0
    lines_jt = direct_jt = seeded_jt = 0
    n_jt = 0
    for va in starts:
        fn = fmap[va]
        rec = lift.get(f"0x{va:08x}")
        if not (rec and rec.get("lifted") and rec.get("ll_lines")):
            continue
        size = int(rec.get("bytes") or fn["size"])
        jts = fn.get("jump_tables") or []
        plain = CENSUS.walk(image, va, size, [])["reachable_instructions"]
        if not jts:
            lines_no_jt += rec["ll_lines"]
            insns_no_jt += plain
            continue
        seeded = CENSUS.walk(image, va, size, jts)["reachable_instructions"]
        if seeded <= plain:
            continue
        n_jt += 1
        lines_jt += rec["ll_lines"]
        direct_jt += plain
        seeded_jt += seeded
    per_insn = lines_no_jt / insns_no_jt if insns_no_jt else 0.0
    volume = {
        "question": "does a jump-table function's IR carry the arms' instructions?",
        "ir_lines_per_reachable_instruction_without_jump_tables": round(per_insn, 3),
        "functions_measured_without_jump_tables": len(starts) - n_jt,
        "functions_whose_tables_add_reachable_code": n_jt,
        "their_ir_lines": lines_jt,
        "predicted_from_direct_flow_only": round(direct_jt * per_insn),
        "predicted_if_the_arms_were_lifted": round(seeded_jt * per_insn),
        "ratio_to_direct_flow_prediction": round(lines_jt / (direct_jt * per_insn), 3)
        if direct_jt else None,
        "ratio_to_lifted_arms_prediction": round(lines_jt / (seeded_jt * per_insn), 3)
        if seeded_jt else None,
    }
    print(f"IR volume: jump-table functions carry {volume['ratio_to_direct_flow_prediction']} "
          f"x the IR the direct-flow instruction count predicts and "
          f"{volume['ratio_to_lifted_arms_prediction']} x what lifting the arms would "
          f"predict", flush=True)

    # ---- 2c. what the "decoded coverage" figure means once the arms are counted
    # The census seeds its walk with the map's jump-table targets, so its
    # reachable-instruction figure counts code remill never decoded. Reported
    # here beside it, because slice 1's 99.6 % coverage reads as a lift result
    # and is really a map result.
    census_path = C.OUT / f"census.{args.tag}.json"
    coverage = None
    if census_path.is_file():
        by_region = json.loads(census_path.read_text())["traps"]["by_region"]
        seeded_total = sum(b["reachable_instructions"] for b in by_region.values())
        mapped_total = sum(b["mapped_instructions"] for b in by_region.values())
        coverage = {
            "mapped_instructions": mapped_total,
            "census_reachable_with_jump_table_seeds": seeded_total,
            "percent_with_seeds": round(100 * seeded_total / mapped_total, 2),
            "instructions_only_reachable_through_a_table": instructions_only_via_tables,
            "decoded_by_remill_lower_bound": seeded_total - instructions_only_via_tables,
            "percent_decoded": round(100 * (seeded_total - instructions_only_via_tables)
                                     / mapped_total, 2),
            "note": "the census seeds the map's jump-table targets, so its coverage "
                    "figure includes instructions no module contains; subtracting them "
                    "is a lower bound on what remill decoded",
            "by_region": {r: {
                "mapped_instructions": by_region[r]["mapped_instructions"],
                "census_reachable_with_jump_table_seeds": by_region[r]["reachable_instructions"],
                "instructions_only_reachable_through_a_table": only_via_tables_by_region[r],
                "decoded_by_remill_lower_bound": (by_region[r]["reachable_instructions"]
                                                  - only_via_tables_by_region[r]),
                "percent_decoded": round(
                    100 * (by_region[r]["reachable_instructions"]
                           - only_via_tables_by_region[r])
                    / by_region[r]["mapped_instructions"], 2)
                if by_region[r]["mapped_instructions"] else None,
            } for r in by_region},
        }
        print(f"decoded coverage: {coverage['percent_decoded']} % against the census's "
              f"{coverage['percent_with_seeds']} %", flush=True)

    out = {
        "tag": args.tag,
        "decoded_coverage": coverage,
        "map_surface": {r: dict(surface[r]) for r in C.REGIONS},
        "map_surface_total": dict(sum(surface.values(), collections.Counter())),
        "ir_volume_check": volume,
        "jump_table_arms": {
            "arms": lifted_arms + unlifted_arms,
            "lifted_by_direct_flow": lifted_arms,
            "only_reachable_through_the_table": unlifted_arms,
            "target_class": dict(target_class),
            "dispatchable": {
                "rule": "an indirect branch lifts to __remill_jump, which dispatches "
                        "through the function table; the table holds one row per map "
                        "entry, so an arm that is not a map entry is a guaranteed miss",
                "arms_that_are_map_entries": target_class.get("map_entry", 0),
                "arms_inside_some_entry": target_class.get("inside_entry", 0),
                "arms_in_no_entry": target_class.get("gap", 0),
            },
            "instructions_reachable_only_through_a_table": instructions_only_via_tables,
            "reachable_instructions_with_seeds": reach_seeded,
            "reachable_instructions_without_seeds": reach_plain,
            "by_region": {r: dict(per_region_arm[r]) for r in C.REGIONS},
            "worst_functions": sorted(arm_rows, key=lambda x: -x["targets"])[:25],
        },
        "seconds": round(time.time() - t0, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"indirect.{args.tag}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True) + "\n")

    # ---- 3. confirm at the IR level (checkpointed: the file above is already written)
    probe_jobs = [(int(row["va"], 16), row["unlifted_targets"],
                   str(C.LIFT / args.tag / f"sub_{int(row['va'], 16):08X}.ll"))
                  for row in sorted(arm_rows, key=lambda x: x["va"])
                  if row["unlifted_targets"]]
    if args.probe_limit:
        probe_jobs = probe_jobs[:args.probe_limit]
    checked = found = 0
    absent_rows = []
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init_img,
                                initargs=(str(C.LIFT / args.tag),)) as ex:
        for res in ex.map(immediate_probe, probe_jobs, chunksize=4):
            checked += res["checked"]
            found += res["found_in_ir"]
            if res["absent"]:
                absent_rows.append(res)
    out["ir_confirmation"] = {
        "method": "for every arm the walk says is reachable only through the table, "
                  "and whose first instructions carry a 32-bit immediate or a large "
                  "displacement, the owning module's IR is searched for that constant. "
                  "An absence is the arm's code being absent from the module; a hit is "
                  "either a lifted arm or the same constant used elsewhere in the "
                  "function, so the hits are an upper bound on disagreement.",
        "functions_probed": len(probe_jobs),
        "arms_with_a_distinctive_constant": checked,
        "found_in_the_module": found,
        "absent_from_the_module": checked - found,
        "examples": absent_rows[:10],
    }
    print(f"IR confirmation: {found} of {checked} arms' constants found in the "
          f"owning module", flush=True)

    # ---- 4. what the lifted IR actually contains
    vas = sorted(int(a, 16) for a, r in lift.items() if r.get("lifted"))
    ir = {r: collections.Counter() for r in C.REGIONS}
    fs_disagreements: list[dict] = []
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init,
                                initargs=(str(C.LIFT / args.tag),)) as ex:
        for key, njump, ncall, nmiss, nfs in ex.map(ir_counts, vas, chunksize=32):
            fn = fmap[int(key, 16)]
            r = fn["region"]
            ir[r]["remill_jump_calls"] += njump
            ir[r]["remill_function_call_calls"] += ncall
            ir[r]["missing_block_calls"] += nmiss
            ir[r]["functions_with_remill_jump"] += bool(njump)
            ir[r]["fs_base_loads"] += nfs
            ir[r]["functions_with_fs_base_loads"] += bool(nfs)
            if bool(nfs) != bool(int(fn.get("fs0_sites") or 0)):
                fs_disagreements.append({"va": key, "region": r,
                                         "map_fs0_sites": int(fn.get("fs0_sites") or 0),
                                         "ir_fs_base_loads": nfs})
    out["lifted_ir"] = {
        "note": "__remill_jump is an indirect branch that could not be resolved to a "
                "block: every jump-table site and every unresolved computed jump. "
                "__remill_function_call is every call, direct or indirect; the direct "
                "ones resolve to a lifted define at link time.",
        "by_region": {r: dict(ir[r]) for r in C.REGIONS},
        "total_remill_jump_calls": sum(ir[r]["remill_jump_calls"] for r in C.REGIONS),
    }
    # ---- 5. the G1 exception-handling strategy, against the lifted image
    #
    # Step 1 of the decision is "registration and unregistration are plain
    # guest-memory writes to FS:[0], lifted as such (each guest thread gets a TEB
    # region so FS:[0] resolves)", and "the runtime's RaiseException returns
    # normally for the SetThreadName code 0x406D1388 ... and traps for every other
    # code". Both halves are checkable here.
    import struct
    iat = []
    for im in C.function_map().get("imports", []):
        a = int(im["iat"], 16)
        v = struct.unpack_from("<I", image, a - C.IMAGE_BASE)[0]
        iat.append((im["symbol"], a, v))
    out["exception_handling"] = {
        "decision": "Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md section 2 "
                    "step 1",
        "registration_sites_in_the_map": {r: surface[r]["fs0_sites"] for r in C.REGIONS},
        "functions_with_registration": {r: surface[r]["functions_with_fs0"]
                                        for r in C.REGIONS},
        "lifted_as_a_guest_memory_access": {
            "test": "the lifted module loads %FSBASE out of the remill State and adds it "
                    "to the displacement, so FS:[0] is an ordinary __remill_read/"
                    "write_memory_32 on FSBASE + 0 -- which is what the decision asks for",
            "fs_base_loads": {r: ir[r]["fs_base_loads"] for r in C.REGIONS},
            "functions_disagreeing_with_the_map": len(fs_disagreements),
            "disagreements": fs_disagreements[:20],
        },
        "imports": {
            "entries": len(iat),
            "iat_values_dispatchable_as_loaded": sum(1 for _s, _a, v in iat
                                                     if v in entries),
            "why": "the IAT is loaded from the file and nothing binds it, so an import "
                   "call reads the file's name-table pointer and dispatches on it; the "
                   "function table has no row for any of those addresses",
            "raise_exception": [{"symbol": s, "iat": f"0x{a:08x}",
                                 "value_as_loaded": f"0x{v:08x}"}
                                for s, a, v in iat
                                if "RaiseException" in s or "RtlUnwind" in s],
        },
        "import_call_sites": {r: surface[r]["import_call_sites"] for r in C.REGIONS},
    }

    out["seconds"] = round(time.time() - t0, 2)
    (OUT / f"indirect.{args.tag}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"__remill_jump sites in the lifted IR: "
          f"{out['lifted_ir']['total_remill_jump_calls']}", flush=True)
    print(f"-> {OUT / f'indirect.{args.tag}.json'}  ({out['seconds']}s)")


def _reachable_set(image: bytes, va: int, size: int, jump_tables: list) -> set[int]:
    """The addresses `census.walk` decodes, as a set.

    `walk` returns counts; the arm question needs membership. Same algorithm,
    same stopping rules -- it is the shape of remill's own recursive descent,
    which is why an address outside this set has no lifted block.
    """
    end = va + size
    window = image[va - C.IMAGE_BASE:end - C.IMAGE_BASE + 16]
    dec = Decoder(32, window, ip=va)
    seen: set[int] = set()
    work = [va]
    for jt in jump_tables or []:
        work += [int(t, 16) for t in jt.get("targets", []) if va <= int(t, 16) < end]
    from iced_x86 import FlowControl
    while work:
        ip = work.pop()
        while va <= ip < end:
            if ip in seen:
                break
            seen.add(ip)
            dec.position = ip - va
            dec.ip = ip
            ins = dec.decode()
            if ins.is_invalid or ins.next_ip > end:
                break
            fc = ins.flow_control
            if fc in CENSUS.STOP or fc == FlowControl.INDIRECT_BRANCH:
                break
            if fc == FlowControl.UNCONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if not (va <= t < end):
                    break
                ip = t
                continue
            if fc == FlowControl.CONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if va <= t < end:
                    work.append(t)
            ip = ins.next_ip
    return seen


if __name__ == "__main__":
    main()
