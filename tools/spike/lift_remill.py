#!/usr/bin/env python3
"""Lift the ten spike functions with remill v5.0.7 (remill-lift-14) and compile the IR.

remill-lift-14 takes raw bytes (`--bytes <hex> --address VA --arch x86 --os
windows`) and writes LLVM 14 textual IR. The release binary hardcodes its
semantics directory as /usr/local/share/remill/14/semantics (no flag, no env
var), so each run happens inside an unprivileged user namespace
(`unshare -Urm`) that bind-mounts private/lifters/remill/usr/local over
/usr/local for that process only: user space, no sudo, nothing written outside
the repository. Lifted = rc 0 and a `define ... @sub_<va>(` in the IR. Compile
per tools/spike/spikelib.COMPILERS["ir"]. Results:
private/generated/spike/remill/results.json.

Usage: .venv/bin/python tools/spike/lift_remill.py
"""
from __future__ import annotations

import json
import re
import subprocess
import time

import spikelib as S

PREFIX = S.ROOT / "private" / "lifters" / "remill" / "usr" / "local"
OUT = S.ROOT / "private" / "generated" / "spike" / "remill"


def body_lines(text: str, name: str) -> int:
    m = re.search(rf"^define [^\n]*@{name}\([^)]*\)[^\n{{]*\{{\n(.*?)^\}}", text, re.S | re.M)
    return m.group(1).count("\n") if m else 0


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    already = any(json.loads(l).get("lifter") == "remill" and json.loads(l).get("event") == "first_compilable_output"
                  for l in S.TIMELINE.read_text().splitlines() if l.strip())
    results = []
    for fn in S.functions():
        va = int(fn["va"], 16)
        hexbytes = (S.ROOT / fn["bin"]).read_bytes().hex() if not fn["bin"].startswith("/") else open(fn["bin"], "rb").read().hex()
        ll = OUT / f"sub_{va:08X}.ll"
        inner = (f"mount --bind {PREFIX} /usr/local && /usr/local/bin/remill-lift-14 --arch x86 --os windows "
                 f"--address {va:#x} --bytes {hexbytes} --ir_out {ll}")
        cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        secs = round(time.time() - t0, 2)
        (OUT / f"sub_{va:08X}.log").write_text(proc.stdout + proc.stderr)
        text = ll.read_text(errors="replace") if ll.exists() else ""
        name = f"sub_{va:x}"
        lifted = proc.returncode == 0 and re.search(rf"^define [^\n]*@{name}\(", text, re.M) is not None
        entry = {
            "va": fn["va"], "name": fn["name"], "bytes": fn["bytes"],
            "command": f"unshare -Urm --propagation unchanged sh -c '{inner.replace(hexbytes, '<' + str(fn['bytes']) + ' bytes hex>')}'",
            "rc": proc.returncode, "seconds": secs, "lifted": lifted,
            "lift_error_head": "" if lifted else S.error_head(proc.stderr + proc.stdout),
            "ll_lines": S.line_count(ll) if ll.exists() else 0,
            "ll_function_body_lines": body_lines(text, name),
            "missing_blocks": len(re.findall(r"__remill_missing_block", text)),
            "compile": {},
        }
        if ll.exists():
            entry["compile"]["ir"] = S.compile_file("ir", ll)
        ok = [k for k, v in entry["compile"].items() if v["ok"]]
        if ok and not already:
            S.timeline("remill", "first_compilable_output", f"sub_{va:08X}: {', '.join(ok)} compiled")
            already = True
        print(f"{fn['va']} lifted={lifted} {secs}s LL={entry['ll_lines']}({entry['ll_function_body_lines']}) compiled={ok}")
        results.append(entry)
    version = subprocess.run([str(PREFIX / "bin" / "remill-lift-14"), "--version"], capture_output=True, text=True)
    S.write_results(OUT / "results.json", "remill", results,
                    {"version": (version.stdout + version.stderr).strip()[:300], "package": "remill-5.0.7.c0f90b9-x86_64.deb"})
    S.timeline("remill", "ten_functions_done", f"{sum(r['lifted'] for r in results)}/10 lifted; "
               f"IR {sum(r['compile'].get('ir', {}).get('ok', False) for r in results)}/10 compiled")


if __name__ == "__main__":
    main()
