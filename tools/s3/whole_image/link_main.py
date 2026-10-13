#!/usr/bin/env python3
"""Link any driver `main` against a whole-image tag, and run it.

Subtitle: one command to put `harness/seh_probe.c` or `tools/s3/arms/arm_probe.c`
on top of a tag's 18 051 lifted objects, instead of a zig command line nobody
can retype.

`link_all.py` links exactly one main, `link_probe.c`, and `link_arms.py` links
exactly one other, `arm_probe.c`, with the object list and the runtime file list
written out twice. Driving guest code needs a third and a fourth, so the third
time this was about to be copied it became a tool (AGENTS.md rule 13).

What it does not do is choose anything. The tag, the driver, the objects to
leave off the link line and the arguments to run with are all named on the
command line, because every one of them changes what the run means:

  --drop ir_00406520.o   leaves one lifted object off, so a driver's own weak
                         definition of that `sub_` binds instead. This is how
                         `arm_probe.c` intercepts a *direct* call, which the
                         function table cannot: remill lifts `call <imm>` to
                         `call @sub_<target>` and the linker binds it.

Each phase checkpoints to `private/generated/s3/link/main.<name>.json` as it
lands, so a kill loses at most the phase in flight.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/whole_image/link_main.py --tag next+arms \\
      --main harness/seh_probe.c --name seh.next+arms \\
      --run private/exe/sonic2app-unpacked.exe
  .venv/bin/python tools/s3/whole_image/link_main.py --tag next+arms \\
      --main tools/s3/arms/arm_probe.c --name armprobe.next+arms \\
      --drop ir_00406520.o --drop ir_004026E0.o \\
      --run private/exe/sonic2app-unpacked.exe --run <oracle.txt> --run --no-arm-rows
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import link_all as L  # noqa: E402

import spikelib as S  # tools/spike/spikelib.py, read-only  # noqa: E402

OUT = C.GEN / "s3" / "link"
RUNTIME = C.ROOT / "runtime"
TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"
RUNTIME_C = ["guest_memory.c", "function_table_impl.c", "sa2_trap.c", "sa2_call.c"]
RUNTIME_CXX = ["remill_runtime.cpp"]


def phase_compile(state: dict, name: str, tag: str, main: Path) -> None:
    bdir = OUT / "mains" / name / "obj"
    bdir.mkdir(parents=True, exist_ok=True)
    for stale in bdir.glob("*.o"):      # a failed compile must not leave a stale
        stale.unlink()                  # object for the link to reuse (slice 3)
    zig = str(S.ZIG)
    cflags = [zig, "cc"] + TARGET + [OPT, "-w", "-c", "-I", str(RUNTIME),
                                     "-I", str(C.REMILL_INC)]
    log = {}
    for src in RUNTIME_C:
        o = bdir / (Path(src).stem + ".o")
        log[src] = L.run(cflags + [str(RUNTIME / src), "-o", str(o)])
    for src in RUNTIME_CXX:
        o = bdir / (Path(src).stem + ".o")
        log[src] = L.run([zig, "c++"] + TARGET + [OPT, "-w", "-std=c++17", "-c",
                         "-I", str(RUNTIME), "-I", str(C.REMILL_INC),
                         str(RUNTIME / src), "-o", str(o)])
    table_c = OUT / tag / "function_table_whole.c"
    if not table_c.is_file():
        raise SystemExit(f"no generated table for tag {tag}: {table_c}\n"
                         f"run link_all.py --tag {tag} --phase table first")
    for src, path in (("function_table_whole.c", table_c), ("main.c", main)):
        o = bdir / (Path(src).stem + ".o")
        log[src] = L.run(cflags + [str(path), "-o", str(o)], timeout=7200)
    for v in log.values():
        v.pop("stdout", None)
    state["compile"] = log
    state["compile_ok"] = all(v["rc"] == 0 for v in log.values())
    print("compile: " + ", ".join(f"{k} rc={v['rc']}" for k, v in log.items()), flush=True)
    for k, v in log.items():
        if v["rc"]:
            print(f"--- {k} ---\n{v['stderr_tail'][-3000:]}", flush=True)


def phase_link(state: dict, name: str, tag: str, drop: list[str]) -> None:
    if not state.get("compile_ok"):
        raise SystemExit("refusing to link: the compile phase failed")
    bdir = (OUT / "mains" / name / "obj").resolve()
    objdir = (C.BUILD / tag).resolve()
    binary = (OUT / "mains" / name / "sa2_main").resolve()
    dropped = set(drop)
    objs = sorted(p.name for p in objdir.glob("ir_*.o") if p.name not in dropped)
    absent = sorted(d for d in dropped if not (objdir / d).is_file())
    if absent:
        raise SystemExit(f"--drop names objects that are not in {objdir}: {absent}. "
                         f"Dropping a name that is not there would silently link the "
                         f"whole image and look like the intended experiment.")
    base = [str(bdir / (Path(s).stem + ".o"))
            for s in RUNTIME_C + RUNTIME_CXX + ["function_table_whole.c", "main.c"]]
    cmd = [str(S.ZIG), "c++"] + TARGET + [OPT] + base + objs + ["-o", str(binary)]
    print(f"link {name}: {len(objs)} lifted objects (dropped {sorted(dropped) or 'none'})"
          f" + {len(base)} runtime/driver objects", flush=True)
    r = L.run(cmd, cwd=objdir)
    r.pop("stdout", None)
    r.update(objects_lifted=len(objs), objects_dropped=sorted(dropped),
             objects_runtime=len(base), argv_bytes=sum(len(a) + 1 for a in cmd),
             object_bytes_in=sum((objdir / o).stat().st_size for o in objs))
    if binary.exists() and r["rc"] == 0:
        r["binary"] = str(binary.relative_to(C.ROOT))
        r["binary_bytes"] = binary.stat().st_size
        r["sections"] = L.sections(binary)
        r["sha256"] = C.sha256(binary)
    state["link"] = r
    print(f"link {name}: rc={r['rc']} {r['seconds']}s peak_rss={r['peak_child_rss_mb']} MB "
          f"binary={r.get('binary_bytes', 0) / 1e6:.1f} MB", flush=True)
    if r["rc"] != 0:
        print(r["stderr_tail"][-4000:], flush=True)


def phase_run(state: dict, name: str, argv: list[str], env: dict[str, str]) -> None:
    binary = (OUT / "mains" / name / "sa2_main").resolve()
    if not binary.exists():
        state["run"] = {"ok": False, "why": "no binary"}
        return
    t0 = time.time()
    p = subprocess.run([str(binary)] + argv, capture_output=True, text=True,
                       timeout=1800, env=dict(os.environ, **env))
    r = {"rc": p.returncode, "seconds": round(time.time() - t0, 2), "argv": argv,
         "env": env, "stderr_tail": p.stderr[-6000:]}
    try:
        r["result"] = json.loads(p.stdout)
    except json.JSONDecodeError:
        r["result"] = None
        r["stdout_tail"] = p.stdout[-20000:]
    state["run"] = r
    print(f"run {name}: rc={r['rc']} {r['seconds']}s", flush=True)
    print(json.dumps(r.get("result") or {"stdout_tail": r.get("stdout_tail", "")[-1500:]},
                     indent=1)[:4000], flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--main", type=Path, required=True)
    ap.add_argument("--name", required=True, help="output name; also the json's name")
    ap.add_argument("--drop", action="append", default=[],
                    help="a lifted object to leave off the link line, by file name")
    ap.add_argument("--run", action="append", default=[],
                    help="one argument for the run phase; repeat for more")
    ap.add_argument("--env", action="append", default=[],
                    help="NAME=VALUE for the run phase; repeat for more")
    ap.add_argument("--phase", default="all", choices=["all", "compile", "link", "run"])
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"main.{args.name}.json"
    state = json.loads(path.read_text()) if path.is_file() else {}
    state.update(name=args.name, tag=args.tag, main=str(args.main),
                 machine={"nproc": os.cpu_count(),
                          "toolchain": f"zig cc/c++ {' '.join(TARGET)} {OPT}"})
    env = dict(kv.split("=", 1) for kv in args.env)
    runners = {
        "compile": lambda: phase_compile(state, args.name, args.tag,
                                         (C.ROOT / args.main).resolve()),
        "link": lambda: phase_link(state, args.name, args.tag, args.drop),
        "run": lambda: phase_run(state, args.name, args.run, env),
    }
    for ph in (["compile", "link", "run"] if args.phase == "all" else [args.phase]):
        state["phase_in_flight"] = ph
        path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
        runners[ph]()
        state["phase_in_flight"] = None
        path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
