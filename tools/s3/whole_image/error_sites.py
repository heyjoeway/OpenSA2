#!/usr/bin/env python3
"""Separate remill's two `__remill_error` populations, and name the opcodes.

Design 5 asks for *"unsupported instructions emit a trap ... their count is a
metric"* and for every trap opcode to be listed. The count S2 redefined the
metric against is the missing-block site (`census.py`). `__remill_error` is a
different and stricter thing, and in this pipeline it has **two** sources that
a plain `grep -c` conflates:

  * a **decode failure**: remill could not decode or has no semantics for the
    bytes at some address, so it sets `hyper_call = kInvalidInstruction` (32)
    and ends the block with

        %N = tail call ptr @__remill_error(ptr %state, i32 %PC, ptr %MEM)
        ret ptr %N

    -- a bare `ptr %state` argument, because the call is emitted by the lifter
    itself. This is design 5's "unsupported instruction" trap.
  * a **guest fault path** inside an inlined semantics function: `DIV`/`IDIV`
    raise #DE on a zero divisor or a quotient overflow and call
    `__remill_error` with clang's full attribute list
    (`ptr noundef nonnull align 16 dereferenceable(3504) %state`). That is
    correct lifted behaviour, not a trap for an unsupported instruction.

This tool counts both, and for every decode failure decides whether the
function-map's recorded extent already explains it (a body cut mid-instruction,
which `census.py`'s `walk` finds independently). Whatever is left is an
instruction remill cannot lift although iced-x86 decodes it, and those are
found exactly, by lifting each reachable instruction of the unexplained
functions on its own and keeping the ones that come back as a decode failure.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/whole_image/error_sites.py [--tag map] [--jobs 16]
                                                       [--no-probe]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import census as CENSUS  # noqa: E402

from iced_x86 import Decoder, Formatter, FormatterSyntax  # noqa: E402

DECODE_FAIL = re.compile(r"tail call ptr @__remill_error\(ptr %state,")
ANY_ERROR = re.compile(r"call ptr @__remill_error\(")

_LL: Path | None = None


def _init(ll: str) -> None:
    global _LL
    _LL = Path(ll)


def scan(va: int) -> tuple[str, int, int]:
    text = (_LL / f"sub_{va:08X}.ll").read_text(errors="replace")
    return f"0x{va:08x}", len(DECODE_FAIL.findall(text)), len(ANY_ERROR.findall(text))


def probe(job: tuple[int, bytes]) -> tuple[str, bool]:
    """Lift one instruction on its own; True when remill emits a decode failure.

    One `0xC3` is appended so the block has a clean terminator: without it the
    fall-through leaves the one-instruction range and remill's end-of-range
    handling, rather than the instruction, decides what the module ends in.
    """
    va, raw = job
    raw = raw + b"\xc3"
    inner = (f"mount --bind {C.REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 "
             f"--arch x86 --os windows --address {va:#x} --bytes {raw.hex()} --ir_out /dev/stdout")
    cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return f"0x{va:08x}", True
    return f"0x{va:08x}", bool(DECODE_FAIL.search(p.stdout))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="map")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--no-probe", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    fmap = C.by_address()
    lift = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")
    image = C.memory_image()
    vas = sorted(int(a, 16) for a, r in lift.items() if r.get("lifted"))

    per: dict[str, tuple[int, int]] = {}
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init,
                                initargs=(str(C.LIFT / args.tag),)) as ex:
        for key, nfail, nall in ex.map(scan, vas, chunksize=32):
            per[key] = (nfail, nall)

    by_region = {r: {"decode_failure_sites": 0, "decode_failure_functions": 0,
                     "semantics_fault_sites": 0, "functions": 0} for r in C.REGIONS}
    unexplained, explained = [], []
    for va in vas:
        key = f"0x{va:08x}"
        nfail, nall = per[key]
        r = fmap[va]["region"]
        by_region[r]["functions"] += 1
        by_region[r]["decode_failure_sites"] += nfail
        by_region[r]["semantics_fault_sites"] += nall - nfail
        if not nfail:
            continue
        by_region[r]["decode_failure_functions"] += 1
        size = int(lift[key].get("bytes") or fmap[va]["size"])
        w = CENSUS.walk(image, va, size, fmap[va].get("jump_tables"))
        row = {"va": key, "name": fmap[va]["name"], "region": r, "sites": nfail,
               "map_size": fmap[va]["size"], "lifted_bytes": size}
        if w["straddles"] or w["invalid"]:
            row["explained_by"] = "straddle" if w["straddles"] else "undecodable byte"
            row["where"] = (w["straddles"] or w["invalid"])[0]
            explained.append(row)
        else:
            unexplained.append(row)

    out = {"tag": args.tag, "by_region": by_region,
           "decode_failure_sites": sum(b["decode_failure_sites"] for b in by_region.values()),
           "semantics_fault_sites": sum(b["semantics_fault_sites"] for b in by_region.values()),
           "explained_by_map_extent": {"functions": len(explained), "first": explained[:20]},
           "unexplained": {"functions": len(unexplained), "rows": unexplained[:60]}}

    if unexplained and not args.no_probe:
        fmt = Formatter(FormatterSyntax.INTEL)
        jobs, meta = [], {}
        for row in unexplained:
            va = int(row["va"], 16)
            size = row["lifted_bytes"]
            w = CENSUS.walk(image, va, size, fmap[va].get("jump_tables"))
            dec = Decoder(32, image[va - C.IMAGE_BASE:va - C.IMAGE_BASE + size], ip=va)
            for ins in dec:
                if ins.is_invalid or ins.len == 0:
                    continue
                raw = image[ins.ip - C.IMAGE_BASE:ins.ip - C.IMAGE_BASE + ins.len]
                jobs.append((ins.ip, raw))
                text = fmt.format(ins)
                meta[f"0x{ins.ip:08x}"] = (text.split()[0].lower(), text, raw.hex(),
                                           row["va"], row["region"])
            _ = w
        print(f"probing {len(jobs)} instructions from {len(unexplained)} functions",
              flush=True)
        hits = []
        with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
            for key, bad in ex.map(probe, jobs, chunksize=8):
                if bad:
                    m, text, hexb, owner, reg = meta[key]
                    hits.append({"address": key, "mnemonic": m, "insn": text,
                                 "bytes": hexb, "function": owner, "region": reg})
        hist = collections.Counter(h["mnemonic"] for h in hits)
        out["unsupported_instructions"] = {
            "method": "each reachable instruction of an unexplained function lifted on "
                      "its own with remill-lift-14; kept when the module comes back as a "
                      "decode failure",
            "probed": len(jobs), "sites": len(hits),
            "opcode_histogram": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
            "by_region": dict(collections.Counter(h["region"] for h in hits)),
            "sites_listed": sorted(hits, key=lambda h: h["address"])[:200],
        }

    out["seconds"] = round(time.time() - t0, 2)
    path = C.OUT / f"error_sites.{args.tag}.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"decode failures {out['decode_failure_sites']} sites "
          f"({len(explained)} functions explained by the map's extent, "
          f"{len(unexplained)} not); semantics fault paths "
          f"{out['semantics_fault_sites']} sites")
    if "unsupported_instructions" in out:
        print("unsupported opcodes: "
              + json.dumps(out["unsupported_instructions"]["opcode_histogram"]))
    print(f"-> {path}  ({out['seconds']}s)")


if __name__ == "__main__":
    main()
