#!/usr/bin/env python3
"""What a whole-image link would face. Measured, not attempted.

S2's `tools/s2/corpus_run/internalize.py` closed the name-collision blocker at
165 modules by folding remill's collision-renamed missing-block trampolines
(`sub_<hex>.<n>`) into one `__sa2_dispatch_stub`. At 12 683 modules the
collision surface and the function table are different problems and they belong
to the next slice, so this tool measures rather than links:

  * total IR bytes (raw, PC24-passed, folded) and total object bytes;
  * every externally-visible symbol the folded modules define, and how many are
    defined by more than one module -- the duplicate-symbol surface a link
    would hit;
  * every `sub_<hex>` a module calls that no module defines;
  * the function table's size (one row per lifted map entry);
  * `__remill_missing_block` call sites that **survive** the fold, which is the
    trap count the linked program would actually carry, as distinct from the
    raw per-module count that is dominated by direct-call trampolines.

Usage:
  .venv/bin/python tools/s3/whole_image/link_surface.py [--tag map] [--jobs 16]
"""
from __future__ import annotations

import argparse
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

MB_CALL = re.compile(r"\bcall [^\n]*@__remill_missing_block\b")
DISPATCH = re.compile(r"\bcall [^\n]*@__sa2_dispatch_stub\b")
ERR_CALL = re.compile(r"\bcall [^\n]*@__remill_error\b")

_FOLD: Path | None = None


def _init(fold: str) -> None:
    global _FOLD
    _FOLD = Path(fold)


def scan(va: int) -> tuple[str, int, int, int, int]:
    text = (_FOLD / f"sub_{va:08X}.pc24.ll").read_text(errors="replace")
    return (f"0x{va:08x}", len(text), len(MB_CALL.findall(text)),
            len(DISPATCH.findall(text)), len(ERR_CALL.findall(text)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="map")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    args = ap.parse_args()

    t0 = time.time()
    fmap = C.by_address()
    lift = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")
    build = C.read_jsonl(C.BUILD / f"{args.tag}.jsonl")

    raw_ll_bytes = sum(r.get("ll_bytes", 0) for r in lift.values() if r.get("lifted"))
    pc24_bytes = sum(r.get("pass", {}).get("out_bytes", 0) or 0
                     for r in lift.values() if r.get("lifted"))
    fold_bytes = sum(r.get("ir_bytes", 0) for r in build.values() if r.get("folded"))
    obj_bytes = sum(r.get("object_bytes", 0) for r in build.values() if r.get("compiled"))

    defined: collections.Counter = collections.Counter()
    owners: dict[str, list[str]] = collections.defaultdict(list)
    declared_sub: set[str] = set()
    for r in build.values():
        if not r.get("folded"):
            continue
        for name in r.get("defines", []):
            defined[name] += 1
            if len(owners[name]) < 6:
                owners[name].append(r["va"])
        declared_sub |= set(r.get("declares_sub", []))

    dups = {n: c for n, c in defined.items() if c > 1}
    defined_sub = {n for n in defined if n.startswith("sub_")}
    undefined_sub = sorted(declared_sub - defined_sub)

    fold_tot = collections.Counter()
    for r in build.values():
        for k, v in (r.get("fold") or {}).items():
            fold_tot[k] += v

    # residual missing blocks after the fold: the trap count a linked program carries
    vas = sorted(int(a, 16) for a, r in build.items() if r.get("folded"))
    resid = {"missing_block_calls": 0, "dispatch_stub_calls": 0, "remill_error_calls": 0}
    by_region = {r: dict(resid) | {"ir_bytes": 0, "modules": 0} for r in C.REGIONS}
    with cf.ProcessPoolExecutor(max_workers=args.jobs, initializer=_init,
                                initargs=(str(C.FOLD / args.tag),)) as ex:
        for key, nbytes, mb, disp, err in ex.map(scan, vas, chunksize=32):
            reg = fmap[int(key, 16)]["region"]
            by_region[reg]["modules"] += 1
            by_region[reg]["ir_bytes"] += nbytes
            by_region[reg]["missing_block_calls"] += mb
            by_region[reg]["dispatch_stub_calls"] += disp
            by_region[reg]["remill_error_calls"] += err
            resid["missing_block_calls"] += mb
            resid["dispatch_stub_calls"] += disp
            resid["remill_error_calls"] += err

    out = {
        "tag": args.tag,
        "note": "the whole-image link is not attempted in this slice; these are the "
                "numbers it would face",
        "modules": len(vas),
        "ir_bytes": {"raw_remill": raw_ll_bytes, "after_pc24_pass": pc24_bytes,
                     "after_fold": fold_bytes,
                     "after_fold_by_region": {r: by_region[r]["ir_bytes"] for r in C.REGIONS}},
        "object_bytes": {"total": obj_bytes,
                         "mean_per_module": round(obj_bytes / max(len(vas), 1), 1)},
        "fold": dict(fold_tot),
        "symbols": {
            "externally_defined": len(defined),
            "externally_defined_total_definitions": sum(defined.values()),
            "duplicate_names": len(dups),
            "duplicate_definitions": sum(dups.values()),
            "worst": [{"name": n, "definitions": c, "first_modules": owners[n]}
                      for n, c in sorted(dups.items(), key=lambda kv: -kv[1])[:40]],
            "sub_symbols_called_but_never_defined": len(undefined_sub),
            "sub_symbols_called_but_never_defined_first": undefined_sub[:40],
        },
        "function_table": {
            "rows": len(vas),
            "rule": "one row per lifted map entry: guest address -> lifted function "
                    "pointer (design 3.4, as tools/s2/wrap/gen_function_table.py builds it)",
            "bytes_at_8_per_row": len(vas) * 8,
            "bytes_at_16_per_row": len(vas) * 16,
        },
        "residual_after_fold": resid | {"by_region": by_region},
        "seconds": round(time.time() - t0, 2),
    }
    path = C.OUT / f"link_surface.{args.tag}.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"IR raw {raw_ll_bytes / 1e9:.2f} GB, pc24 {pc24_bytes / 1e9:.2f} GB, "
          f"folded {fold_bytes / 1e9:.2f} GB, objects {obj_bytes / 1e6:.1f} MB")
    print(f"symbols: {len(defined)} external names, {len(dups)} duplicated "
          f"({sum(dups.values())} definitions), {len(undefined_sub)} undefined sub_*")
    print(f"residual after fold: {resid['missing_block_calls']} missing-block calls, "
          f"{resid['dispatch_stub_calls']} dispatch-stub calls, "
          f"{resid['remill_error_calls']} remill_error calls")
    print(f"-> {path}  ({out['seconds']}s)")


if __name__ == "__main__":
    main()
