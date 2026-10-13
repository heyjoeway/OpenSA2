#!/usr/bin/env python3
"""perf_f80: build the lifted image with the x87 legalised in a chosen f80 mode, for measuring.

Subtitle: `perf_f80.py build --target T --mode exact|double --out DIR` compiles the replay
link's 24,036 modules (tools/s7/cross_build.py's populations, each with its own tool's command,
the triple swapped) after the mode's pass (exact: tools/s7/f80_legalize.py; double:
tools/s8/f80_double.py), with the mode's shims beside them (tools/s7/f80_shims.c;
tools/s8/f80_shims_double.c), into DIR in cross_build's layout (build.jsonl, summary.json,
f80_shims.o, census.json); the summary's `deps` hashes every input of the pass and the shims
(`deps`), which tools/s7/release_build.py checks before it links a build. `perf_f80.py deps
--mode M` prints that map for this checkout. `perf_f80.py arm64-link --objects DIR --out OUT` links the replays'
aarch64 binaries from such a build with tools/s7/arm64_link.py, its shims the build's mode's,
for tools/s7/arm64_replays.py. An x86-64 double-mode build is the PC release's fast lifted
image: link it with tools/s8/perf_game.py `build --lifted DIR`.

    source tools/env.sh
    tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build --target x86_64-linux-musl \\
        --mode double --out private/generated/s8/perf-game/x86-dbl --jobs 4
    tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build --target x86_64-linux-musl \\
        --mode exact --out private/generated/s8/perf-game/x86-exact --jobs 4 \\
        [--shim-define SA2_F80_COUNT]
    .venv/bin/python tools/s8/perf_f80.py deps --mode double [--memfast]
    .venv/bin/python tools/s8/perf_game.py build --variant plain --lifted <DIR> --name <name>
    tools/slot.sh .venv/bin/python tools/s8/perf_f80.py arm64-link --objects <aarch64 DIR> \\
        --out <OUT> [--replay boot --replay metalharbor]

Why: an x86-64 build in the exact mode is the soft-f80 cost the arm64 port pays, measured on
the same machine as the native-x87 build (tools/s8/perf_game.py runs both on Metal Harbor), and
the double mode (tools/s8/f80_double.py) is the fast mode, verified on x86-64 natively and on
aarch64 under qemu by the registered replays.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import functools
import hashlib
import importlib
import json
import multiprocessing
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "spike"))
sys.path.insert(0, str(HERE))

MODES = ("exact", "double")
PASS = {"exact": ROOT / "tools" / "s7" / "f80_legalize.py", "double": HERE / "f80_double.py"}
SHIMS = {"exact": ROOT / "tools" / "s7" / "f80_shims.c", "double": HERE / "f80_shims_double.c"}
MEMFAST = HERE / "perf_memfast.py"
INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def _module_level(node: ast.AST):
    """The nodes a module runs when it is imported: all but function bodies."""
    for c in ast.iter_child_nodes(node):
        if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield c
            yield from _module_level(c)


def _imports(path: Path, found: list[Path]) -> None:
    """`path` and, transitively, every module under tools/ that it imports at module level, as
    this process resolved them (each is imported by then: the module that names it ran)."""
    if path in found:
        return
    found.append(path)
    tree = ast.parse(path.read_text(), str(path))
    for n in _module_level(tree):
        names = ([a.name for a in n.names] if isinstance(n, ast.Import) else
                 [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else [])
        for name in names:
            f = getattr(sys.modules.get(name), "__file__", None)
            if f and Path(f).resolve().is_relative_to(ROOT / "tools"):
                _imports(Path(f).resolve(), found)


def _includes(path: Path, found: list[Path]) -> None:
    """`path` and, transitively, every file it #includes by quote, beside the including file."""
    if path in found:
        return
    found.append(path)
    for name in INCLUDE.findall(path.read_text()):
        inc = (path.parent / name).resolve()
        if not inc.is_file() or not inc.is_relative_to(ROOT):
            raise SystemExit(f"perf_f80: {path.relative_to(ROOT)} includes \"{name}\", which is not "
                             "a file of this checkout beside it")
        _includes(inc, found)


def deps(mode: str, memfast: bool = False) -> dict[str, str]:
    """{checkout-relative path: sha256} of every input of the mode's pass and shims, as the
    summary records them (`deps`): the pass module (and, with `memfast`, tools/s8/perf_memfast.py)
    with every tools/ module it imports at module level, transitively; the shims source with
    every file it #includes by quote, transitively. Imported here first, so the modules resolve
    as the build's own imports do."""
    found: list[Path] = []
    for p in (PASS[mode],) + ((MEMFAST,) if memfast else ()):
        mod = importlib.import_module(p.stem)
        if Path(mod.__file__).resolve() != p:
            raise SystemExit(f"perf_f80: {p.stem} came from {mod.__file__}, not {p}")
        _imports(p, found)
    _includes(SHIMS[mode], found)
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in found}


def _pass(mode: str):
    """The mode's legalize(text) -> (text, stats)."""
    import f80_double as D  # noqa: PLC0415
    import f80_legalize as F  # noqa: PLC0415
    if not hasattr(F, "_legalize_exact"):
        F._legalize_exact = F.legalize
    return D.legalize if mode == "double" else F._legalize_exact


def _legalize_memfast(text: str, mode: str):
    import perf_memfast as M  # noqa: PLC0415
    out, st = _pass(mode)(text)
    out, mf = M.memfast(out)
    st["memfast"] = mf
    return out, st


def _init(mode: str, memfast: bool = False) -> None:
    """Each compile worker runs cross_build.one with the mode's pass in place of its own."""
    import f80_legalize as F  # noqa: PLC0415
    exact = _pass(mode)     # first: _pass keeps the exact pass before F.legalize is replaced
    F.legalize = functools.partial(_legalize_memfast, mode=mode) if memfast else exact


def build_shims(out: Path, triple: str, mode: str, defines: list[str]) -> dict:
    """The mode's shims with cross_build's command for the map population (cross_build's own
    build_shims compiles tools/s7/f80_shims.c only)."""
    import cross_build as XB  # noqa: PLC0415
    import common as C  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    source = SHIMS[mode]
    obj = out / "f80_shims.o"
    cmd = XB.compile_command("map", triple, source, obj)
    at = cmd.index(str(source))
    cmd[at:at] = defines
    if obj.exists():
        obj.unlink()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"the shims did not compile for {triple}:\n{p.stderr[-3000:]}")
    return {"object": str(obj), "source": str(source.relative_to(ROOT)), "defines": defines,
            "source_sha256": C.sha256(source), "object_sha256": C.sha256(obj)}


def build(a) -> int:
    import cross_build as XB  # noqa: PLC0415
    import f80_double as D  # noqa: PLC0415
    import f80_legalize as F  # noqa: PLC0415
    import common as C  # noqa: PLC0415
    out = Path(a.out).resolve()
    inputs = deps(a.mode, a.memfast)     # before the pass runs: the files it was built from
    pops = XB.populations()
    chosen = [p for p in a.populations.split(",") if p]
    for p in chosen:
        (out / p).mkdir(parents=True, exist_ok=True)
    (out / "tmp").mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in chosen:
        mods = pops[p][: a.limit] if a.limit else pops[p]
        jobs += [(p, va, str(src), str(out), a.target, True, False) for va, src in mods]
    print(f"target={a.target} mode={a.mode} out={out} modules={len(jobs)} jobs={a.jobs}", flush=True)
    log = out / "build.jsonl"
    t0 = time.time()
    n = ok = 0
    ctx = multiprocessing.get_context("fork")
    with log.open("w") as fh, cf.ProcessPoolExecutor(a.jobs, mp_context=ctx, initializer=_init,
                                                     initargs=(a.mode, a.memfast)) as ex:
        for rec in ex.map(XB.one, jobs, chunksize=4):
            rec["f80_mode"] = a.mode
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            ok += bool(rec.get("compiled"))
            if n % 2000 == 0:
                print(f"  {n}/{len(jobs)} compiled={ok} {time.time() - t0:.0f}s", flush=True)
    wall = round(time.time() - t0, 1)
    try:
        (out / "tmp").rmdir()
    except OSError:
        pass
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    shims = build_shims(out, a.target, a.mode, [f"-D{d}" for d in a.shim_define])
    objs = [out / r["object"] for r in recs if r.get("compiled")]
    names = (D.shim_names if a.mode == "double" else F.shim_names)(out / "f80_shims.o")
    cen = F.census(objs, names, a.jobs)
    (out / "census.json").write_text(json.dumps(cen, indent=1) + "\n")
    summary = {"schema": "sa2-s7-cross-build/v2", "generated_by": "tools/s8/perf_f80.py",
               "target": a.target, "variant": f"{a.target}+f80-{a.mode}", "legalized": True,
               "f80_mode": a.mode, "memfast": a.memfast, "out": str(out), "jobs": a.jobs,
               "wall_seconds": wall,
               "pass": str(PASS[a.mode].relative_to(ROOT)),
               "pass_sha256": C.sha256(PASS[a.mode]), "shims": shims, "deps": inputs,
               "modules": len(recs), "compiled": sum(bool(r.get("compiled")) for r in recs),
               "refused": {}, "errors": {},
               "census": {k: cen[k] for k in ("objects", "ok", "classes", "disallowed")}}
    for r in recs:
        if "refused" in r:
            summary["refused"][r["refused"][:160]] = summary["refused"].get(r["refused"][:160], 0) + 1
        elif not r.get("compiled"):
            k = (r.get("error_head") or "?").splitlines()[0][:200]
            summary["errors"][k] = summary["errors"].get(k, 0) + 1
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("target", "f80_mode", "modules", "compiled",
                                              "wall_seconds", "refused", "errors", "census")},
                     indent=1)[:6000])
    return 0 if summary["compiled"] == summary["modules"] and cen["ok"] else 1


def arm64_link(a) -> int:
    import arm64_link as AL  # noqa: PLC0415
    summary = json.loads((Path(a.objects) / "summary.json").read_text())
    mode = summary.get("f80_mode", "exact")
    AL.SHIMS = str(SHIMS[mode].relative_to(ROOT))       # arm64_link compiles src / SHIMS
    ns = argparse.Namespace(cmd="link", out=a.out, objects=a.objects, triple=a.triple,
                            src=str(ROOT), gen=str(ROOT / "private" / "generated"),
                            replay=a.replay, jobs=a.jobs, reference=False)
    return AL.link(ns)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--target", required=True)
    b.add_argument("--mode", choices=MODES, required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--jobs", type=int, default=4)
    b.add_argument("--populations", default="map,arms,extra")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--shim-define", action="append", default=[])
    b.add_argument("--memfast", action="store_true",
                   help="also tools/s8/perf_memfast.py: the guest-memory fast path for the "
                        "functions State localization left alone (a measurement)")
    d = sub.add_parser("deps", help="the inputs a build's summary records (`deps`), as JSON")
    d.add_argument("--mode", choices=MODES, required=True)
    d.add_argument("--memfast", action="store_true")
    k = sub.add_parser("arm64-link")
    k.add_argument("--objects", required=True)
    k.add_argument("--out", required=True)
    k.add_argument("--triple", default="aarch64-linux-musl")
    k.add_argument("--replay", action="append", default=[])
    k.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    if a.cmd == "deps":
        print(json.dumps(deps(a.mode, a.memfast), indent=1))
        return 0
    return build(a) if a.cmd == "build" else arm64_link(a)


if __name__ == "__main__":
    sys.exit(main())
