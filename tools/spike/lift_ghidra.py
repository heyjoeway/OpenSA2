#!/usr/bin/env python3
"""Lifter (4) "ghidra-decompiler": export Ghidra's decompiled C for the ten spike functions.

Ghidra is a decompiler, not a lifter (design: route D territory); its
time-to-first-compilable-output is the baseline the other lifters compare
against. Runs the predecessor's already-analysed project
(private/predecessor-work/tools/ghidra/project/SA2Native.gpr, program
sonic2app-unpacked.exe) read-only through analyzeHeadless with the tracked
exporter Documentation/scripts/ExportSA2EvidencePacket.java, which writes
<ADDR>-<name>.c / .asm.txt / .json per target into the output directory. The
decompiled C is then compile-tested per tools/spike/spikelib.COMPILERS
(c_strict, c_gnu89, gcc). Results: private/generated/spike/ghidra/results.json.

Usage: .venv/bin/python tools/spike/lift_ghidra.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time

import spikelib as S

TOOLS = S.ROOT / "private" / "predecessor-tools"
GHIDRA = TOOLS / "ghidra" / "ghidra_12.1.3_PUBLIC"
JAVA_HOME = TOOLS / "temurin21" / "jdk-21.0.12.1+1"
PROJECT_DIR = S.ROOT / "private" / "predecessor-work" / "tools" / "ghidra" / "project"
PROGRAM = "sonic2app-unpacked.exe"
EXPORTER = S.ROOT / "Documentation" / "scripts" / "ExportSA2EvidencePacket.java"
EXE = S.ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
OUT = S.ROOT / "private" / "generated" / "spike" / "ghidra"


def body_lines(text: str) -> int:
    """Lines between the function's opening `{` line and its closing `}` line."""
    lines = text.splitlines()
    opens = [i for i, l in enumerate(lines) if l.strip() == "{"]
    closes = [i for i, l in enumerate(lines) if l.strip() == "}"]
    return closes[-1] - opens[0] - 1 if opens and closes else 0


def label(fn: dict) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", fn["name"]) or "spike"


def main() -> None:
    import sys
    OUT.mkdir(parents=True, exist_ok=True)
    fns = S.functions()
    if "--recount-only" in sys.argv:
        data = json.loads((OUT / "results.json").read_text())
        for entry in data["results"]:
            path = OUT / (entry["target"].replace("=", "-") + ".c")
            entry["c_function_body_lines"] = body_lines(path.read_text(errors="replace")) if path.exists() else 0
        (OUT / "results.json").write_text(json.dumps(data, indent=2) + "\n")
        print({e["va"]: e["c_function_body_lines"] for e in data["results"]})
        return
    targets = [f"{int(fn['va'], 16):08X}={label(fn)}" for fn in fns]
    md5 = hashlib.md5(EXE.read_bytes()).hexdigest()
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = f"{JAVA_HOME / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(S.ROOT / "private" / "generated" / "spike" / "xdg-config")
    cmd = [str(GHIDRA / "support" / "analyzeHeadless"), str(PROJECT_DIR), "SA2Native",
           "-process", PROGRAM, "-noanalysis", "-readOnly",
           "-scriptPath", str(EXPORTER.parent),
           "-scriptlog", str(OUT / "ghidra-script.log"), "-log", str(OUT / "ghidra.log"),
           "-postScript", EXPORTER.name, str(OUT), md5, *targets]
    S.timeline("ghidra-decompiler", "headless_start", "analyzeHeadless -process sonic2app-unpacked.exe -noanalysis -readOnly -postScript ExportSA2EvidencePacket.java, ten targets")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
    secs = round(time.time() - t0, 1)
    (OUT / "headless.stdout").write_text(proc.stdout + proc.stderr)
    S.timeline("ghidra-decompiler", "headless_done", f"rc={proc.returncode} in {secs}s")
    already = any(json.loads(l).get("lifter") == "ghidra-decompiler" and json.loads(l).get("event") == "first_compilable_output"
                  for l in S.TIMELINE.read_text().splitlines() if l.strip())
    results = []
    for fn, target in zip(fns, targets):
        stem = OUT / target.replace("=", "-")
        c_path = stem.with_suffix(".c")
        text = c_path.read_text(errors="replace") if c_path.exists() else ""
        lifted = c_path.exists() and c_path.stat().st_size > 0
        entry = {
            "va": fn["va"], "name": fn["name"], "target": target,
            "lifted": lifted,
            "lift_error_head": "" if lifted else S.error_head(proc.stderr + proc.stdout),
            "c_lines": S.line_count(c_path) if lifted else 0,
            "c_function_body_lines": body_lines(text),
            "compile": {},
        }
        if lifted:
            for kind in ("c_strict", "c_gnu89", "gcc"):
                entry["compile"][kind] = S.compile_file(kind, c_path)
        ok = [k for k, v in entry["compile"].items() if v["ok"]]
        if ok and not already:
            S.timeline("ghidra-decompiler", "first_compilable_output", f"{target}: {', '.join(ok)} compiled")
            already = True
        print(f"{fn['va']} lifted={lifted} C={entry['c_lines']} compiled={ok}"
              + ("" if ok or not lifted else " | " + entry["compile"]["c_strict"]["error_head"].splitlines()[0][:120]))
        results.append(entry)
    S.write_results(OUT / "results.json", "ghidra-decompiler", results,
                    {"version": "Ghidra 12.1.3 PUBLIC (tools/pins.json)", "headless_seconds": secs, "headless_rc": proc.returncode,
                     "command": " ".join(cmd)})
    S.timeline("ghidra-decompiler", "ten_functions_done", f"{sum(r['lifted'] for r in results)}/10 exported; "
               f"C strict {sum(r['compile'].get('c_strict', {}).get('ok', False) for r in results)}/10, "
               f"C gnu89 {sum(r['compile'].get('c_gnu89', {}).get('ok', False) for r in results)}/10")


if __name__ == "__main__":
    main()
