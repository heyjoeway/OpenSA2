#!/usr/bin/env python3
"""Lift the ten spike functions with RetDec v5.0 and try to compile each output.

Per function: `retdec-decompiler <unpacked exe> --select-ranges VA-END
--select-decode-only --backend-no-time-varying-info -o sub_VA.c`, which writes
sub_VA.c and sub_VA.ll (plus .bc/.dsm/.config.json, removed after the run:
the .dsm alone is 190 MB). Lifted = rc 0 and a `function_<va>` definition in
the C. Compiles per tools/spike/spikelib.COMPILERS. Results go to
private/generated/spike/retdec/results.json; the first compile success appends
a timeline event once (idempotent: it checks the timeline for an earlier one).

Usage: .venv/bin/python tools/spike/lift_retdec.py [--only 0x77fc30]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import spikelib as S

RETDEC = S.ROOT / "private" / "lifters" / "retdec" / "bin" / "retdec-decompiler"
EXE = S.ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
OUT = S.ROOT / "private" / "generated" / "spike" / "retdec"


def body_lines(text: str, name: str) -> int:
    """Lines inside the braces of the C definition `T name(...) {` or the IR `define ... @name(...) ... {`."""
    m = re.search(rf"^(?:define )?[^\n]*[ @]{name}\([^)]*\)[^\n{{]*\{{\n(.*?)^\}}", text, re.S | re.M)
    return m.group(1).count("\n") if m else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=None)
    ap.add_argument("--recount-only", action="store_true", help="re-read existing outputs; no lift, no compile, no timeline")
    args = ap.parse_args()
    if args.recount_only:
        data = json.loads((OUT / "results.json").read_text())
        for entry in data["results"]:
            stem = OUT / f"sub_{int(entry['va'], 16):08X}"
            name = f"function_{int(entry['va'], 16):x}"
            for suffix, key in ((".c", "c_function_body_lines"), (".ll", "ll_function_body_lines")):
                path = stem.with_suffix(suffix)
                entry[key] = body_lines(path.read_text(errors="replace"), name) if path.exists() else 0
        (OUT / "results.json").write_text(json.dumps(data, indent=2) + "\n")
        print({e["va"]: (e["c_function_body_lines"], e["ll_function_body_lines"]) for e in data["results"]})
        return
    OUT.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(RETDEC), "--version"], capture_output=True, text=True).stdout.strip()
    already = any(json.loads(l).get("lifter") == "retdec" and json.loads(l).get("event") == "first_compilable_output"
                  for l in S.TIMELINE.read_text().splitlines() if l.strip())
    results = []
    for fn in S.functions():
        va = int(fn["va"], 16)
        if args.only and int(args.only, 16) != va:
            continue
        end = va + fn["bytes"]
        stem = OUT / f"sub_{va:08X}"
        cmd = [str(RETDEC), str(EXE), "--select-ranges", f"{va:#x}-{end:#x}", "--select-decode-only",
               "--backend-no-time-varying-info", "-o", f"{stem}.c"]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        secs = round(time.time() - t0, 1)
        (stem.with_suffix(".log")).write_text(proc.stdout + proc.stderr)
        for junk in (".dsm", ".bc", ".config.json"):
            p = Path(str(stem) + junk)
            if p.exists():
                p.unlink()
        c_path, ll_path = stem.with_suffix(".c"), stem.with_suffix(".ll")
        name = f"function_{va:x}"
        c_text = c_path.read_text(errors="replace") if c_path.exists() else ""
        defined = re.search(rf"^\w[^\n]*\b{name}\(", c_text, re.M) is not None
        lifted = proc.returncode == 0 and defined
        entry = {
            "va": fn["va"], "name": fn["name"], "range": f"{va:#x}-{end:#x}",
            "command": " ".join(cmd), "rc": proc.returncode, "seconds": secs,
            "lifted": lifted,
            "lift_error_head": "" if lifted else (S.error_head(proc.stderr + proc.stdout) if proc.returncode else f"no {name} definition in C output"),
            "c_lines": S.line_count(c_path) if c_path.exists() else 0,
            "c_function_body_lines": body_lines(c_text, name),
            "ll_lines": S.line_count(ll_path) if ll_path.exists() else 0,
            "ll_function_body_lines": body_lines(ll_path.read_text(errors="replace"), name) if ll_path.exists() else 0,
            "compile": {},
        }
        if c_path.exists():
            for kind in ("c_strict", "c_gnu89", "gcc"):
                entry["compile"][kind] = S.compile_file(kind, c_path)
        if ll_path.exists():
            entry["compile"]["ir"] = S.compile_file("ir", ll_path)
        ok = [k for k, v in entry["compile"].items() if v["ok"]]
        if ok and not already:
            S.timeline("retdec", "first_compilable_output", f"sub_{va:08X}: {', '.join(ok)} compiled")
            already = True
        print(f"{fn['va']} lifted={lifted} {secs}s C={entry['c_lines']}({entry['c_function_body_lines']}) "
              f"LL={entry['ll_lines']}({entry['ll_function_body_lines']}) compiled={ok}")
        results.append(entry)
    S.write_results(OUT / "results.json", "retdec", results, {"version": version, "exe": str(EXE)})
    S.timeline("retdec", "ten_functions_done", f"{sum(r['lifted'] for r in results)}/10 lifted; "
               f"C strict {sum(r['compile'].get('c_strict', {}).get('ok', False) for r in results)}/10, "
               f"C gnu89 {sum(r['compile'].get('c_gnu89', {}).get('ok', False) for r in results)}/10, "
               f"IR {sum(r['compile'].get('ir', {}).get('ok', False) for r in results)}/10")


if __name__ == "__main__":
    main()
