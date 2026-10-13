#!/usr/bin/env python3
"""S3 whole-image compile: every lifted function must compile (design 5, M2).

One translation unit per function -- the grouping
`Documentation/receipts/s2-build-time-sample-v1.json` measured as the cheapest
to rebuild and the most parallel (1/TU) -- through the S2 corpus pipeline
unchanged: `tools/s2/corpus_run/internalize.py`'s `rewrite` folds remill's
collision-renamed missing-block stubs, then `zig cc -target x86_64-linux-musl
-O2 -w -c`.

The whole-image **link** is deliberately not attempted: S2's `internalize.py`
closed the name-collision blocker at 165 modules, and at 12 683 the collision
surface and the function table are different problems. What a link would face
is measured instead -- IR bytes, object bytes, externally-defined symbols
defined by more than one module after folding, and the function-table size --
and reported by `link_surface.py`.

With `--force`, a module whose latest lift or PC24 pass failed is recorded as not
compiled (`not_compiled`), so the object an earlier build left for it is never
staged as this build's.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/whole_image/compile_all.py [--jobs 16] [--tag map] [--force]
"""
from __future__ import annotations

import argparse
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

import spikelib as S  # tools/spike/spikelib.py, read-only  # noqa: E402

TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"

DEFINE = re.compile(r"^define\s+(?P<attrs>[^@\n]*)@(?P<name>[\w.$]+)\(", re.M)
DECLARE = re.compile(r"^declare\s+[^@\n]*@(?P<name>[\w.$]+)\(", re.M)
INTERNAL = ("internal", "private", "available_externally", "linkonce_odr")

_LL: Path | None = None
_FOLD: Path | None = None
_OBJ: Path | None = None


def _init(ll: str, fold: str, obj: str) -> None:
    global _LL, _FOLD, _OBJ
    _LL, _FOLD, _OBJ = Path(ll), Path(fold), Path(obj)
    _FOLD.mkdir(parents=True, exist_ok=True)
    _OBJ.mkdir(parents=True, exist_ok=True)


def symbols(text: str) -> tuple[list[str], list[str]]:
    """`(externally defined, declared)` -- the link surface of one module."""
    defined, internal = [], []
    for m in DEFINE.finditer(text):
        (internal if any(k in m.group("attrs") for k in INTERNAL) else defined).append(
            m.group("name"))
    declared = [m.group("name") for m in DECLARE.finditer(text)]
    return defined, declared


def one(job: tuple[int, str, str]) -> dict:
    import internalize  # tools/s2/corpus_run/internalize.py, read-only

    va, name, region = job
    src = _LL / f"sub_{va:08X}.pc24.ll"
    dst = _FOLD / f"sub_{va:08X}.pc24.ll"
    obj = _OBJ / f"ir_{va:08X}.o"
    rec = {"va": f"0x{va:08x}", "name": name, "region": region}
    t0 = time.time()
    try:
        text, st = internalize.rewrite(src.read_text(errors="replace"))
    except Exception as exc:
        rec.update(folded=False, compiled=False,
                   error_head=f"internalize: {type(exc).__name__}: {exc}"[:400])
        return rec
    dst.write_text(text)
    rec["fold_seconds"] = round(time.time() - t0, 3)
    rec["folded"] = True
    rec["fold"] = {k: st[k] for k in ("stub_defines_removed", "stub_declares_removed",
                                      "references_redirected")}
    rec["fold"]["kept_internal"] = len(st["stub_bodies_kept_internal"])
    rec["ir_bytes"] = len(text)
    defined, declared = symbols(text)
    rec["defines"] = defined
    rec["declares_sub"] = sorted({d for d in declared if d.startswith("sub_")})

    t1 = time.time()
    cmd = [str(S.ZIG), "cc"] + TARGET + [OPT, "-w", "-c", str(dst), "-o", str(obj)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=str(_OBJ))
        rc, err = p.returncode, p.stderr + p.stdout
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after 1200 s"
    rec["compile_seconds"] = round(time.time() - t1, 3)
    rec["compiled"] = rc == 0 and obj.exists()
    rec["object_bytes"] = obj.stat().st_size if obj.exists() else 0
    rec["error_head"] = "" if rec["compiled"] else S.error_head(err, 4)[:600]
    return rec


def not_compiled(lifted: dict) -> dict:
    """The build record of a module whose latest lift or PC24 pass failed (`lifted`, its
    record in the lift log): there is no IR to fold, so it is not compiled."""
    if lifted.get("lifted"):
        why = f"the PC24 pass refused it: {(lifted.get('pass') or {}).get('error_head', '')}"
    else:
        why = f"remill did not lift it: {lifted.get('error_head', '')}"
    return {"va": lifted["va"], "name": lifted.get("name", ""),
            "region": lifted.get("region", ""), "folded": False, "compiled": False,
            "error_head": f"not compiled, its latest lift failed: {why}"[:600]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--tag", default="map")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    lift_log = C.read_jsonl(C.LIFT / f"{args.tag}.jsonl")
    log = C.BUILD / f"{args.tag}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    done = {} if args.force else C.read_jsonl(log)

    jobs = [(int(r["va"], 16), r["name"], r["region"])
            for r in sorted(lift_log.values(), key=lambda x: x["va"])
            if r.get("lifted") and r.get("pass", {}).get("ok")
            and (args.force or not done.get(r["va"], {}).get("compiled"))]
    if args.limit:
        jobs = jobs[:args.limit]
    # A forced rebuild gives a module whose latest lift or pass failed a record saying it
    # was not compiled. Without one, the record of its last good build stayed the latest
    # in the log, so tools/s3/arms/stage_merged_build.py staged that build's object, made
    # by the fold code of its day, beside objects the current code made; with one, the
    # stager finds the old object's link a stray and refuses (the review of the S5 speed
    # fold, SHOULD-FIX 1, which tools/s4/rebuild_image.py also checks for itself).
    refused = [not_compiled(r) for r in sorted(lift_log.values(), key=lambda x: x["va"])
               if not (r.get("lifted") and r.get("pass", {}).get("ok"))] if args.force else []

    print(f"tag={args.tag} jobs={args.jobs} modules={len(jobs)}", flush=True)
    if refused:
        print(f"  {len(refused)} module(s) not compiled, their latest lift failed: "
              f"{[r['va'] for r in refused[:5]]}", flush=True)
    t0 = time.time()
    n = ok = 0
    with log.open("a") as fh, cf.ProcessPoolExecutor(
            max_workers=args.jobs, initializer=_init,
            initargs=(str(C.LIFT / args.tag), str(C.FOLD / args.tag),
                      str(C.BUILD / args.tag))) as ex:
        for rec in refused:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        for rec in ex.map(one, jobs, chunksize=2):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            ok += bool(rec.get("compiled"))
            if n % 200 == 0:
                fh.flush()
                el = time.time() - t0
                print(f"  {n}/{len(jobs)} compiled={ok} {el:.0f}s "
                      f"({n / el:.1f}/s, eta {(len(jobs) - n) / max(n / el, 1e-9) / 60:.1f} min)",
                      flush=True)
    wall = time.time() - t0
    print(f"done {n} modules, {ok} compiled, {wall:.1f}s wall at {args.jobs} jobs")
    C.write_wall(
        C.BUILD / f"{args.tag}.wall.json",
        {"tag": args.tag, "jobs": args.jobs, "attempted": n, "compiled": ok,
         "wall_seconds": round(wall, 2), "nproc": os.cpu_count(),
         "toolchain": f"zig cc {' '.join(TARGET)} {OPT} -w -c (one TU per function)"},
        did_work=n > 0)


if __name__ == "__main__":
    main()
