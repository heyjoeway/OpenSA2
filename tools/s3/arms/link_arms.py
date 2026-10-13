#!/usr/bin/env python3
"""Link the whole image plus the re-lifted jump-table arms, and drive it.

Subtitle: the 12 683 whole-image objects, the runtime, the six arm traces and
`tools/s3/arms/arm_probe.c`, into one binary that calls guest code.

Phases, each checkpointed to `private/generated/s3/arms/link.<tag>.json` as it
lands, so a kill loses at most the phase in flight:

  compile  the runtime, the generated whole-image function table and arm_probe.c
  link     zig c++ over every whole-image object plus the arm objects
  run      the probe, with the arm rows and again without them (the control)

Nothing under `runtime/`, `harness/`, `tools/s2/` or `tools/s3/whole_image/` is
written: the runtime sources are compiled from their own tree into this tool's
output directory, and the generated table is the one
`tools/s3/whole_image/link_all.py --phase table` already wrote.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/link_arms.py --tag fun4015f0 [--phase all]
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402
import link_all as L  # read-only: `sections`, and the phase shape  # noqa: E402

import spikelib as S  # tools/spike/spikelib.py, read-only  # noqa: E402

OUT = C.GEN / "s3" / "arms"
WHOLE = C.GEN / "s3" / "link"           # slice 2's output, read only
RUNTIME = C.ROOT / "runtime"
TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"
RUNTIME_C = ["guest_memory.c", "function_table_impl.c", "sa2_trap.c", "sa2_call.c"]
RUNTIME_CXX = ["remill_runtime.cpp"]


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 7200) -> dict:
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(cwd) if cwd else None)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", f"timeout after {timeout} s"
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {"rc": rc, "seconds": round(time.time() - t0, 2),
            "peak_child_rss_mb": round(max(after, before) / 1024.0, 1),
            "stdout": out, "stderr_tail": err[-8000:]}


def checkpoint(state: dict, tag: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"link.{tag}.json").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def phase_compile(state: dict, tag: str, whole_tag: str) -> None:
    bdir = OUT / "link" / tag / "obj"
    bdir.mkdir(parents=True, exist_ok=True)
    for stale in bdir.glob("*.o"):        # a failed compile must not leave a
        stale.unlink()                    # stale object for the link to reuse
    zig = str(S.ZIG)
    cflags = [zig, "cc"] + TARGET + [OPT, "-w", "-c", "-I", str(RUNTIME)]
    log = {}
    for src in RUNTIME_C:
        o = bdir / (Path(src).stem + ".o")
        log[src] = run(cflags + [str(RUNTIME / src), "-o", str(o)])
        log[src]["object_bytes"] = o.stat().st_size if o.exists() else 0
    for src in RUNTIME_CXX:
        o = bdir / (Path(src).stem + ".o")
        log[src] = run([zig, "c++"] + TARGET + [OPT, "-w", "-std=c++17", "-c",
                       "-I", str(RUNTIME), "-I", str(C.REMILL_INC),
                       str(RUNTIME / src), "-o", str(o)])
        log[src]["object_bytes"] = o.stat().st_size if o.exists() else 0
    table_c = WHOLE / whole_tag / "function_table_whole.c"
    for src, path in (("function_table_whole.c", table_c),
                      ("arm_probe.c", HERE / "arm_probe.c")):
        o = bdir / (Path(src).stem + ".o")
        log[src] = run(cflags + [str(path), "-o", str(o)], timeout=7200)
        log[src]["object_bytes"] = o.stat().st_size if o.exists() else 0
    state["compile"] = {k: {kk: vv for kk, vv in v.items() if kk != "stdout"}
                        for k, v in log.items()}
    state["compile_ok"] = all(v["rc"] == 0 for v in log.values())
    for k, v in log.items():
        print(f"compile {k}: rc={v['rc']} {v['seconds']}s", flush=True)
        if v["rc"] != 0:
            print(v["stderr_tail"][-3000:], flush=True)


# The witness call `call 0x004026e0` is direct, so the function table cannot
# intercept it (see arm_probe.c). One binary links the lifted object and runs
# the real FUN_004026e0; the other leaves that single object off so arm_probe's
# weak `sub_4026e0` binds and the witness stub records EBX. Everything else
# about the two links is identical.
# The selector call has the same shape and the same problem: `call 0x00406520`
# is direct, and the real FUN_00406520 opens with an SEH prologue that reads
# `FS:[0]`, which faults because nothing sets `fs_base`.
WITNESS_OBJECT = "ir_004026E0.o"
SELECTOR_OBJECT = "ir_00406520.o"

LINKS = {
    "full": [],                                        # all 12 683 lifted objects
    "selector_thunk": [SELECTOR_OBJECT],               # + the real witness
    "both_thunks": [SELECTOR_OBJECT, WITNESS_OBJECT],
}


def one_link(state: dict, tag: str, whole_tag: str, name: str, drop: list[str]) -> None:
    bdir = (OUT / "link" / tag / "obj").resolve()
    objdir = (C.BUILD / whole_tag).resolve()          # the 12 683 whole-image objects
    armdir = (OUT / "build" / tag).resolve()          # the arm objects
    binary = (OUT / "link" / tag / f"sa2_arms_{name}").resolve()
    objs = sorted(p.name for p in objdir.glob("ir_*.o") if p.name not in drop)
    arms = sorted(str(p) for p in armdir.glob("ir_*.o"))
    base = [str(bdir / (Path(s).stem + ".o"))
            for s in RUNTIME_C + RUNTIME_CXX + ["function_table_whole.c", "arm_probe.c"]]
    cmd = [str(S.ZIG), "c++"] + TARGET + [OPT] + base + arms + objs + ["-o", str(binary)]
    print(f"link {name}: {len(objs)} whole-image (dropped {drop or 'none'}) + "
          f"{len(arms)} arm + {len(base)} runtime objects", flush=True)
    r = run(cmd, cwd=objdir)
    r.pop("stdout", None)
    r.update(objects_whole_image=len(objs), objects_dropped=drop,
             objects_arms=len(arms), objects_runtime=len(base),
             argv_bytes=sum(len(a) + 1 for a in cmd))
    if binary.exists() and r["rc"] == 0:
        r["binary"] = str(binary.relative_to(C.ROOT))
        r["binary_bytes"] = binary.stat().st_size
        r["sections"] = L.sections(binary)
        r["sha256"] = C.sha256(binary)
    state.setdefault("link", {})[name] = r
    print(f"link {name}: rc={r['rc']} {r['seconds']}s peak_rss={r['peak_child_rss_mb']} MB "
          f"binary={r.get('binary_bytes', 0) / 1e6:.1f} MB", flush=True)
    if r["rc"] != 0:
        print(r["stderr_tail"][-4000:], flush=True)


def phase_link(state: dict, tag: str, whole_tag: str) -> None:
    if not state.get("compile_ok"):
        bad = [k for k, v in (state.get("compile") or {}).items() if v.get("rc")]
        raise SystemExit(f"refusing to link: the compile phase failed for {bad}")
    for name, drop in LINKS.items():
        one_link(state, tag, whole_tag, name, drop)
        checkpoint(state, tag)


# (binary, arm rows, missing-block policy). The last two are the controls: the
# same binary without the six rows the recommendation adds, and the same run
# under the shipped missing-block policy.
RUNS = [
    ("A.full.arms",            "full",           [],                "1"),
    ("B.selector_thunk.arms",  "selector_thunk", [],                "1"),
    ("C.both_thunks.arms",     "both_thunks",    [],                "1"),
    ("D.both_thunks.NO_arms",  "both_thunks",    ["--no-arm-rows"], "1"),
    ("E.both_thunks.arms.mbtrap", "both_thunks", [],                "0"),
]


def phase_run(state: dict, tag: str) -> None:
    oracle = OUT / f"oracle.{tag}.txt"
    for key, binname, extra, policy in RUNS:
        binary = (OUT / "link" / tag / f"sa2_arms_{binname}").resolve()
        if not binary.exists():
            state.setdefault("probe", {})[key] = {"ok": False, "why": f"no binary {binary}"}
            continue
        env = dict(os.environ, SA2_MISSING_BLOCK_DISPATCH=policy)
        t0 = time.time()
        p = subprocess.run([str(binary), str(C.EXE), str(oracle)] + extra,
                           capture_output=True, text=True, timeout=900, env=env)
        r = {"rc": p.returncode, "seconds": round(time.time() - t0, 2),
             "binary": binname, "argv_extra": extra,
             "SA2_MISSING_BLOCK_DISPATCH": policy,
             "stderr_tail": p.stderr[-4000:]}
        try:
            r["result"] = json.loads(p.stdout)
        except json.JSONDecodeError:
            r["result"] = None
            r["stdout_tail"] = p.stdout[-6000:]
        state.setdefault("probe", {})[key] = r
        checkpoint(state, tag)
        res = r.get("result") or {}
        print(f"{key}: rc={r['rc']} policy={res.get('missing_block_policy')} "
              f"{json.dumps(res.get('summary', {}))}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--whole-tag", default="next")
    ap.add_argument("--phase", default="all",
                    choices=["all", "compile", "link", "run"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"link.{args.tag}.json"
    state = json.loads(path.read_text()) if path.is_file() else {}
    state["tag"] = args.tag
    state["whole_tag"] = args.whole_tag
    state["machine"] = {"nproc": os.cpu_count(),
                        "toolchain": f"zig cc/c++ {' '.join(TARGET)} {OPT}"}
    phases = ["compile", "link", "run"] if args.phase == "all" else [args.phase]
    for ph in phases:
        state["phase_in_flight"] = ph
        checkpoint(state, args.tag)
        {"compile": lambda s, t: phase_compile(s, t, args.whole_tag),
         "link": lambda s, t: phase_link(s, t, args.whole_tag),
         "run": phase_run}[ph](state, args.tag)
        state["phase_in_flight"] = None
        checkpoint(state, args.tag)
    print(f"-> {path}")


if __name__ == "__main__":
    main()
