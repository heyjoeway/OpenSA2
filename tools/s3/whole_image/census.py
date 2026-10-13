#!/usr/bin/env python3
"""S3 trap census and map-defect census, over the whole function map.

**Traps.** Design 5's S3 exit asks for *"the trap count recorded and every trap
opcode listed"*, for traps meaning "unsupported instruction". The adopted
remill + PC24 pipeline has no such concept: remill decodes a function's bytes
by recursive descent and every control-flow edge that leaves the lifted byte
range becomes `__remill_missing_block`. S2 redefined the Traps metric against
exactly those sites (`Documentation/S3_INPUTS.md` section 6) and the field was
never written, so the metric reads "not measured". This writes it.

The reaching-instruction analysis is `tools/s2/corpus_run/build.py`'s
`reaching_edges`, imported read-only and unchanged -- the same machinery that
decides the runtime's dispatch, so the census and the dispatch policy cannot
drift. `build.py` merges edges by target across modules because the runtime
dispatches on a guest address; this tool also groups the raw edges by the
module that takes them, because design 5 wants the count per function and per
region, and classifies every target as another map entry, an address inside
another entry, or an address inside no entry at all.

`reaching_edges` sweeps each body linearly. A body with an embedded jump table
or inline data desynchronises a linear sweep and invents edges, so every
number this tool reports as a *defect* comes from an independent recursive
descent from the function's entry (`walk`), which follows only real control
flow and seeds the map's own jump-table targets. Both are reported; where they
disagree the linear figure is the upper bound.

**Map defects.** `Documentation/S3_INPUTS.md` section 1 records three functions
whose recorded `size` is short, each found by driving a fixture into the
truncated tail. They are a sample, not special cases. Detected mechanically:

  A `straddle`     a reachable instruction crosses `entry+size`: the lift ends
                   mid-instruction and remill emits `__remill_error`.
  B `ends_at_end`  a reachable edge targets exactly `entry+size`: the lift ends
                   in a missing block at the function's own last byte.
  C `gap_target`   a reachable edge leaves the body for an address inside no
                   map entry: code that a whole-image lift never emits, so the
                   trap is guaranteed however many functions are lifted.

A and B are the rule the slice was given. C is the shape of the third recorded
defect (`0x008287C0`, whose 26 unmapped bytes at `0x008287DC` are inside no
entry), which A and B cannot see because that body ends cleanly on a `ret`.

Usage:
  .venv/bin/python tools/s3/whole_image/census.py [--tag map] [--out FILE]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

from iced_x86 import Decoder, FlowControl, Formatter, FormatterSyntax  # noqa: E402

import build as S2BUILD  # tools/s2/corpus_run/build.py, read-only  # noqa: E402

FMT = Formatter(FormatterSyntax.INTEL)
STOP = (FlowControl.RETURN, FlowControl.INTERRUPT, FlowControl.EXCEPTION)


def walk(image: bytes, va: int, size: int, jump_tables: list[dict]) -> dict:
    """Recursive descent over one recorded body. Returns reachable facts only."""
    end = va + size
    off = va - C.IMAGE_BASE
    window = image[off:end - C.IMAGE_BASE + 16]     # +16: see a straddling instruction whole
    dec = Decoder(32, window, ip=va)
    seen: set[int] = set()
    work = [va]
    for jt in jump_tables or []:
        for t in jt.get("targets", []):
            t = int(t, 16)
            if va <= t < end:
                work.append(t)
    mnem: collections.Counter = collections.Counter()
    out_edges: list[dict] = []
    straddles: list[dict] = []
    invalid: list[dict] = []
    indirect = 0

    def leave(target: int, ins, kind: str) -> None:
        out_edges.append({"target": target, "source": ins.ip, "kind": kind,
                          "insn": FMT.format(ins)})

    while work:
        ip = work.pop()
        while va <= ip < end:
            if ip in seen:
                break
            seen.add(ip)
            dec.position = ip - va
            dec.ip = ip
            ins = dec.decode()
            if ins.is_invalid:
                invalid.append({"ip": f"0x{ip:08x}",
                                "bytes": window[ip - va:ip - va + 4].hex()})
                break
            if ins.next_ip > end:
                straddles.append({"ip": f"0x{ip:08x}", "len": ins.len,
                                  "next_ip": f"0x{ins.next_ip:08x}",
                                  "insn": FMT.format(ins), "over_by": ins.next_ip - end})
                break
            mnem[str(ins.mnemonic).rsplit(".", 1)[-1].lower()] += 1
            fc = ins.flow_control
            if fc in STOP:
                break
            if fc == FlowControl.INDIRECT_BRANCH:
                indirect += 1
                break
            if fc == FlowControl.UNCONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if va <= t < end:
                    ip = t
                    continue
                leave(t, ins, "jmp")
                break
            if fc == FlowControl.CONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if va <= t < end:
                    work.append(t)
                else:
                    leave(t, ins, "jcc-taken")
                if ins.next_ip >= end:
                    leave(ins.next_ip, ins, "fall-through")
                    break
                ip = ins.next_ip
                continue
            # CALL / INDIRECT_CALL / NEXT: control resumes after the instruction
            kind = "call-return" if fc in (FlowControl.CALL, FlowControl.INDIRECT_CALL) \
                else "fall-through"
            if ins.next_ip >= end:
                leave(ins.next_ip, ins, kind)
                break
            ip = ins.next_ip
    return {"reachable_instructions": sum(mnem.values()), "mnemonics": dict(mnem),
            "out_edges": out_edges, "straddles": straddles, "invalid": invalid,
            "indirect_branches": indirect}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="map")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    t0 = time.time()
    fmap = C.by_address()
    starts = sorted(fmap)
    ends = [va + fmap[va]["size"] for va in starts]
    image = C.memory_image()
    log = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")

    def classify(target: int) -> str:
        if target in fmap:
            return "map_entry"
        i = bisect.bisect_right(starts, target) - 1
        if i >= 0 and starts[i] <= target < ends[i]:
            return "inside_entry"
        return "gap"

    # ---- 1. S2's linear reaching-instruction analysis, unchanged, over every lifted body
    ranges = {}
    for va, fn in fmap.items():
        rec = log.get(f"0x{va:08x}")
        if rec and rec.get("lifted"):
            blob = int(rec.get("bytes") or fn["size"])
            ranges[va] = (va, va + blob, va + blob)
    lin_edges, lin_conflicts = S2BUILD.reaching_edges(image, ranges)
    lin_sites = sum(len(e["edges"]) for e in lin_edges)
    print(f"linear (build.py reaching_edges): {lin_sites} sites, {len(lin_edges)} targets, "
          f"{len(lin_conflicts)} conflicts, {time.time() - t0:.1f}s", flush=True)

    # ---- 2. recursive descent, per function: traps, opcodes and map defects
    blank = lambda: {"functions": 0, "lifted": 0, "reachable_instructions": 0,
                     "mapped_instructions": 0, "functions_with_traps": 0,
                     "trap_sites": 0, "straddle": 0, "ends_at_end": 0,
                     "gap_target": 0, "invalid_sites": 0, "indirect_branches": 0}
    by_region = {r: blank() for r in C.REGIONS}
    opcode = {r: collections.Counter() for r in C.REGIONS}
    kinds = {r: collections.Counter() for r in C.REGIONS}
    target_class = {r: collections.Counter() for r in C.REGIONS}
    trap_rows, straddle_rows, ends_rows, gap_rows, invalid_rows = [], [], [], [], []

    for va in starts:
        fn = fmap[va]
        r = fn["region"]
        b = by_region[r]
        b["functions"] += 1
        b["mapped_instructions"] += int(fn.get("instructions") or 0)
        key = f"0x{va:08x}"
        rec = log.get(key)
        if not (rec and rec.get("lifted")):
            continue
        b["lifted"] += 1
        size = int(rec.get("bytes") or fn["size"])
        w = walk(image, va, size, fn.get("jump_tables"))
        b["reachable_instructions"] += w["reachable_instructions"]
        b["indirect_branches"] += w["indirect_branches"]
        b["invalid_sites"] += len(w["invalid"])
        if w["invalid"]:
            invalid_rows.append({"va": key, "name": fn["name"], "region": r,
                                 "count": len(w["invalid"]), "first": w["invalid"][:4]})
        end = va + size
        row = {"va": key, "name": fn["name"], "region": r, "map_size": fn["size"],
               "map_end": f"0x{end:08x}", "remill_error_calls": rec.get("remill_error_calls", 0)}
        if w["straddles"]:
            b["straddle"] += 1
            straddle_rows.append(row | w["straddles"][0] | {"count": len(w["straddles"])})
        hit_end = [e for e in w["out_edges"] if e["target"] == end]
        if hit_end:
            b["ends_at_end"] += 1
            ends_rows.append(row | {"insn": hit_end[0]["insn"],
                                    "source": f"0x{hit_end[0]['source']:08x}",
                                    "kind": hit_end[0]["kind"]})
        gaps = [e for e in w["out_edges"] if classify(e["target"]) == "gap"]
        if gaps:
            b["gap_target"] += 1
            gap_rows.append(row | {"targets": sorted({f"0x{e['target']:08x}" for e in gaps})[:6],
                                   "insn": gaps[0]["insn"],
                                   "source": f"0x{gaps[0]['source']:08x}",
                                   "count": len(gaps)})
        if w["out_edges"]:
            b["functions_with_traps"] += 1
            b["trap_sites"] += len(w["out_edges"])
            for e in w["out_edges"]:
                opcode[r][e["insn"].split()[0].lower()] += 1
                kinds[r][e["kind"]] += 1
                target_class[r][classify(e["target"])] += 1
            trap_rows.append({"va": key, "name": fn["name"], "region": r,
                              "sites": len(w["out_edges"]),
                              "targets": sorted({f"0x{e['target']:08x}"
                                                 for e in w["out_edges"]})[:8]})
    for r in C.REGIONS:
        opcode[r] = {k: v for k, v in opcode[r].items() if v}

    # ---- 3. IR-side counts, as lifted (raw modules, before the fold)
    ir = {r: {"missing_block_calls": 0, "remill_error_calls": 0,
              "functions_with_remill_error": 0, "ll_bytes": 0, "ll_lines": 0}
          for r in C.REGIONS}
    for rec in log.values():
        if not rec.get("lifted"):
            continue
        r = rec["region"]
        ir[r]["missing_block_calls"] += rec.get("missing_block_calls", 0)
        ir[r]["remill_error_calls"] += rec.get("remill_error_calls", 0)
        ir[r]["functions_with_remill_error"] += bool(rec.get("remill_error_calls"))
        ir[r]["ll_bytes"] += rec.get("ll_bytes", 0)
        ir[r]["ll_lines"] += rec.get("ll_lines", 0)

    # ---- 4. map gaps: what --to-next-entry would pull in
    gap_bytes = 0
    gap_count = 0
    big_gaps = []
    overlaps = []
    for i, va in enumerate(starts[:-1]):
        e, nxt = va + fmap[va]["size"], starts[i + 1]
        if nxt > e:
            gap_count += 1
            gap_bytes += nxt - e
            if nxt - e >= 32:
                big_gaps.append({"after": f"0x{va:08x}", "from": f"0x{e:08x}",
                                 "to": f"0x{nxt:08x}", "bytes": nxt - e,
                                 "region": fmap[va]["region"]})
        elif nxt < e:
            overlaps.append({"entry": f"0x{va:08x}", "end": f"0x{e:08x}",
                             "next_entry": f"0x{nxt:08x}", "overlap": e - nxt})

    game = by_region["game"]
    ratio = game["trap_sites"] / game["mapped_instructions"] if game["mapped_instructions"] else 0.0
    ir_ratio = ir["game"]["missing_block_calls"] / game["mapped_instructions"]
    verdict = {
        "criterion": "design 5 S3 kill/pivot: if the trap count in the game region "
                     "exceeds 2 % of instructions, the opcode histogram was wrong and it "
                     "must be re-measured before continuing",
        "metric": "missing-block sites (S2's Traps redefinition): control-flow edges "
                  "that leave a lifted body, counted per edge per module",
        "game_trap_sites": game["trap_sites"],
        "game_mapped_instructions": game["mapped_instructions"],
        "percent": round(ratio * 100, 4),
        "threshold_percent": 2.0,
        "fires": ratio > 0.02,
        "game_remill_error_calls": ir["game"]["remill_error_calls"],
        "combined_with_remill_error": {
            "what": "__remill_error is the closest thing this pipeline has to design 5's "
                    "'unsupported instruction': remill emits it where it could not decode "
                    "or lift the bytes at a block terminator. Counted here beside the "
                    "missing-block sites so the strictest reading of the criterion is "
                    "also measured.",
            "sites": game["trap_sites"] + ir["game"]["remill_error_calls"],
            "percent": round(100 * (game["trap_sites"] + ir["game"]["remill_error_calls"])
                             / game["mapped_instructions"], 4),
            "fires": (game["trap_sites"] + ir["game"]["remill_error_calls"])
                     / game["mapped_instructions"] > 0.02,
        },
        "also_measured": {
            "raw_ir_missing_block_calls_game": ir["game"]["missing_block_calls"],
            "raw_ir_percent": round(ir_ratio * 100, 4),
            "note": "the raw per-module IR count is dominated by remill's "
                    "collision-renamed direct-call trampolines (`sub_<hex>.<n>`), which "
                    "tools/s2/corpus_run/internalize.py folds into __sa2_dispatch_stub "
                    "and which are therefore not traps; see link_surface.py for the "
                    "count that survives the fold",
        },
    }

    out = {
        "tag": args.tag,
        "traps": {
            "definition": "missing-block sites: a control-flow edge that leaves the "
                          "lifted byte range of its module, so remill has no block for "
                          "the target and the lifted code calls __remill_missing_block",
            "method": "recursive descent from each entry (this tool's `walk`), seeded "
                      "with the map's own jump-table targets; cross-checked against "
                      "tools/s2/corpus_run/build.py reaching_edges (linear sweep)",
            "by_region": by_region,
            "by_kind": {r: dict(sorted(kinds[r].items())) for r in C.REGIONS},
            "target_class": {r: dict(sorted(target_class[r].items())) for r in C.REGIONS},
            "opcode_histogram": {r: dict(sorted(opcode[r].items(), key=lambda kv: -kv[1]))
                                 for r in C.REGIONS},
            "linear_cross_check": {"sites": lin_sites, "distinct_targets": len(lin_edges),
                                   "conflicts": len(lin_conflicts)},
            "ir": ir,
            "worst_functions": sorted(trap_rows, key=lambda x: -x["sites"])[:25],
            # Every trap row, not only the worst, because design 5 (S3) exits on
            # "the trap count recorded and every trap opcode listed" and because
            # Documentation/scripts/s3_metrics_inputs.py feeds the Traps metric
            # from these rows. Without them the metric can only see the raw
            # pre-fold IR count, which is about thirty times larger: those are
            # direct-call trampolines the fold resolves, not traps.
            "functions": sorted(trap_rows, key=lambda x: x["va"]),
            "kill_criterion": verdict,
        },
        "map_defects": {
            "rule": {
                "straddle": "a reachable instruction crosses entry+size: the lift ends "
                            "mid-instruction (remill emits __remill_error)",
                "ends_at_end": "a reachable edge targets exactly entry+size: the lift "
                               "ends in a missing block at the function's own last byte",
                "gap_target": "a reachable edge leaves the body for an address inside no "
                              "map entry: code a whole-image lift never emits",
            },
            "counts": {k: sum(by_region[r][k] for r in C.REGIONS)
                       for k in ("straddle", "ends_at_end", "gap_target")},
            "candidates": len({x["va"] for x in straddle_rows} | {x["va"] for x in ends_rows}),
            "by_region": {r: {k: by_region[r][k]
                              for k in ("straddle", "ends_at_end", "gap_target")}
                          for r in C.REGIONS},
            "straddles": straddle_rows,
            "ends_at_end": ends_rows,
            "gap_targets": gap_rows[:400],
            "gap_targets_total": len(gap_rows),
            "undecodable": {"functions": len(invalid_rows),
                            "sites": sum(x["count"] for x in invalid_rows),
                            "rows": invalid_rows[:40]},
        },
        "map_gaps": {
            "question": "what --to-next-entry would hand remill in addition",
            "gaps": gap_count, "bytes": gap_bytes,
            "gaps_32_bytes_or_more": len(big_gaps),
            "bytes_in_those": sum(g["bytes"] for g in big_gaps),
            "largest": sorted(big_gaps, key=lambda g: -g["bytes"])[:25],
            "overlapping_entries": overlaps,
        },
        "seconds": round(time.time() - t0, 2),
    }
    path = args.out or (C.OUT / f"census.{args.tag}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"traps: game {game['trap_sites']} sites / {game['mapped_instructions']} "
          f"mapped instructions = {verdict['percent']}% (fires={verdict['fires']})")
    print(f"map defects: straddle {out['map_defects']['counts']['straddle']}, "
          f"ends_at_end {out['map_defects']['counts']['ends_at_end']}, "
          f"gap_target {out['map_defects']['counts']['gap_target']}")
    print(f"map gaps: {gap_count} gaps, {gap_bytes} bytes, "
          f"{len(big_gaps)} of 32 bytes or more")
    print(f"-> {path}  ({out['seconds']}s)")


if __name__ == "__main__":
    main()
