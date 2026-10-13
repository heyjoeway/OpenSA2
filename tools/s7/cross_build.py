#!/usr/bin/env python3
"""cross_build: compile the lifted image's three populations for another target triple.

Subtitle: the compile step of tools/s3/whole_image/compile_all.py (and of the arms' and the
extras' tools, which use the same command), with the triple swapped, the x86_fp80 operations
legalized for a non-x86 target (tools/s7/f80_legalize.py), and every object written under
private/generated/s7/targets/<triple>/ so that no x86-64 object is ever replaced.

The inputs are read, never written: the folded IR the x86-64 build compiled, for exactly the
objects the replay link reads.

  map    tools/s3/whole_image/compile_all.py's log (build/next.jsonl), the compiled records,
         folded IR fold/next/sub_<VA>.pc24.ll
  arms   tools/s3/arms' log (s3/arms/arms.next.jsonl), the compiled records,
         folded IR s3/arms/fold/next/sub_<VA>.pc24.ll
  extra  tools/s4/lift_extra.py's object list (s4/link/extra_functions.next+allarms.json),
         folded IR s4/lift/fold/sub_<VA>.pc24.ll

The map and arm object names together must be the staged link directory's
(build/next+allarms, tools/s3/arms/stage_merged_build.py), or the build refuses: a cross build
of another set of modules than the link's would prove nothing about the link. Names are not
enough, so each module is also checked against the record its build wrote: for the map and
the arms, the folded IR's size and the staged object's size equal the log's, and for every
population the folded IR is not newer than the object built from it. A refolded tree that
nothing recompiled fails here.

Each population is compiled with its own tool's command, read from that tool's source (the
`cmd = ...` list of compile_all.py's `one`, lift_arms.py's `fold_and_compile` and
lift_extra.py's `fold_and_compile`, with `TARGET` and `OPT` resolved from the same file), with
only the triple replaced. A flag added there reaches every target, and a command this reader
cannot resolve refuses the build. What the reading cannot see (a flag set some other way) is
caught by Documentation/scripts/tests/test_f80_legalize.py, which compiles sampled modules of
each population with this command for the x86-64 triple and requires the staged objects'
bytes.

The output directory holds exactly this build: an `ir_*.o` in a population directory that
this build did not compile (left by an earlier build of another module set) is removed, every
record in build.jsonl names its object, and the census reads the objects the records name.

Legalization is on for a target whose long double is not the x87's (any non-x86 triple) and
off for x86 ones; `--legalize on` forces it for an x86 triple, which is how the pass is proved
behaviour-neutral on this machine: the output then goes to `<triple>+f80/`. With legalization
on, tools/s7/f80_shims.c is compiled beside the objects (`f80_shims.o`) and the census of
tools/s7/f80_legalize.py is taken over every object (`census.json`).

Usage:
  source tools/env.sh
  .venv/bin/python tools/s7/cross_build.py --target aarch64-linux-musl [--jobs 16]
  .venv/bin/python tools/s7/cross_build.py --target x86_64-linux-musl --legalize on \\
      --out-root private/generated/s7/wave2/f80/build
Outputs: <root>/<population>/ir_<VA>.o, <root>/build.jsonl (one record per module),
<root>/summary.json, and with legalization <root>/f80_shims.o and <root>/census.json.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import functools
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _root() -> Path:
    """The repository: the nearest directory above this file holding AGENTS.md and
    tools/pins.json. In the tree that is this file's grandparent; a copy of this file in a
    scratch snapshot under the tree finds the tree, so it reads and names the same paths."""
    for p in Path(__file__).resolve().parents:
        if (p / "AGENTS.md").is_file() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"no repository above {__file__}")


ROOT = _root()
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402  tools/s3/whole_image/common.py, read-only
import compile_all as CA  # noqa: E402  tools/s3/whole_image/compile_all.py, read-only
import f80_legalize as F  # noqa: E402

TARGETS = C.GEN / "s7" / "targets"
ARMS = C.GEN / "s3" / "arms"
EXTRA_STATE = C.GEN / "s4" / "link" / "extra_functions.next+allarms.json"
STAGED = C.BUILD / "next+allarms"
TAG = "next"


def x86(triple: str) -> bool:
    return triple.split("-")[0] in ("x86_64", "i386", "i486", "i586", "i686", "x86")


# Each population's compile, as its own tool writes it: (source file, function).
COMPILERS = {
    "map": (ROOT / "tools" / "s3" / "whole_image" / "compile_all.py", "one"),
    "arms": (ROOT / "tools" / "s3" / "arms" / "lift_arms.py", "fold_and_compile"),
    "extra": (ROOT / "tools" / "s4" / "lift_extra.py", "fold_and_compile"),
}


def _module_constants(tree: ast.Module) -> dict[str, object]:
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


@functools.lru_cache(maxsize=None)
def command_template(pop: str) -> tuple[str, ...]:
    """The population's compile command as its tool writes it, with `{zig}`, `{triple}`,
    `{ir}` and `{object}` for what varies. Refuses a command it cannot resolve."""
    path, fn = COMPILERS[pop]
    tree = ast.parse(path.read_text())
    consts = _module_constants(tree)
    func = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn), None)
    if func is None:
        raise SystemExit(f"{path.name} has no function {fn}")
    cmds = [n.value for n in ast.walk(func) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "cmd" for t in n.targets)]
    if len(cmds) != 1:
        raise SystemExit(f"{path.name}:{fn} assigns cmd {len(cmds)} times; expected once")

    def parts(node: ast.AST) -> list:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return parts(node.left) + parts(node.right)
        if isinstance(node, ast.List):
            return list(node.elts)
        if isinstance(node, ast.Name) and isinstance(consts.get(node.id), list):
            return [ast.Constant(v) for v in consts[node.id]]
        raise SystemExit(f"{path.name}:{fn}: cannot read {ast.unparse(node)} in its command")

    out: list[str] = []
    for el in parts(cmds[0]):
        if isinstance(el, ast.Constant) and isinstance(el.value, str):
            out.append(el.value)
        elif isinstance(el, ast.Name) and isinstance(consts.get(el.id), str):
            out.append(consts[el.id])
        elif (isinstance(el, ast.Call) and isinstance(el.func, ast.Name) and el.func.id == "str"
              and len(el.args) == 1):
            arg = ast.unparse(el.args[0])
            slot = {"S.ZIG": "{zig}", "dst": "{ir}", "obj": "{object}"}.get(arg)
            if slot is None:
                raise SystemExit(f"{path.name}:{fn}: cannot read str({arg}) in its command")
            out.append(slot)
        else:
            raise SystemExit(f"{path.name}:{fn}: cannot read {ast.unparse(el)} in its command")
    if out[:2] != ["{zig}", "cc"] or out.count("-target") != 1 or "{ir}" not in out \
            or "{object}" not in out:
        raise SystemExit(f"{path.name}:{fn}: not a 'zig cc -target <triple> ...' command: {out}")
    i = out.index("-target")
    out[i + 1] = "{triple}"
    return tuple(out)


def compile_command(pop: str, triple: str, ir: Path, obj: Path) -> list[str]:
    """The population's own compile command for one module, with the triple replaced."""
    fill = {"{zig}": str(CA.S.ZIG), "{triple}": triple, "{ir}": str(ir), "{object}": str(obj)}
    return [fill.get(t, t) for t in command_template(pop)]


def va_of(name: str) -> int:
    return int(name.split("_")[1].split(".")[0], 16)


def populations() -> dict[str, list[tuple[int, Path]]]:
    """(va, folded IR) for every object the replay link reads, by population, checked
    against the staged link directory."""
    whole = C.read_jsonl(C.BUILD / f"{TAG}.jsonl")
    arms = C.read_jsonl(ARMS / f"arms.{TAG}.jsonl")
    pops: dict[str, list[tuple[int, Path]]] = {
        "map": sorted((int(r["va"], 16), C.FOLD / TAG / f"sub_{int(r['va'], 16):08X}.pc24.ll")
                      for r in whole.values() if r.get("compiled")),
        "arms": sorted((int(r["va"], 16), ARMS / "fold" / TAG / f"sub_{int(r['va'], 16):08X}.pc24.ll")
                       for r in arms.values() if r.get("compiled")),
    }
    objs = json.loads(EXTRA_STATE.read_text())["objects"]
    pops["extra"] = sorted((va_of(Path(o).name), C.GEN / "s4" / "lift" / "fold"
                            / f"sub_{va_of(Path(o).name):08X}.pc24.ll") for o in objs)
    staged = {p.name for p in STAGED.glob("ir_*.o")}
    ours = {f"ir_{va:08X}.o" for va, _ in pops["map"] + pops["arms"]}
    if staged != ours:
        raise SystemExit(f"the map and arm logs name {len(ours)} objects and the staged link "
                         f"directory {STAGED} holds {len(staged)}; they differ by "
                         f"{sorted(staged ^ ours)[:5]}")
    missing = [str(p) for pop in pops.values() for _, p in pop if not p.is_file()]
    if missing:
        raise SystemExit(f"{len(missing)} folded modules are not on disk: {missing[:3]}")
    stale = []
    irs = {pop: dict(mods) for pop, mods in pops.items()}
    for pop, recs in (("map", whole), ("arms", arms)):
        for r in recs.values():
            if not r.get("compiled"):
                continue
            va = int(r["va"], 16)
            ir = irs[pop][va]
            obj = STAGED / f"ir_{va:08X}.o"
            if ir.stat().st_size != r["ir_bytes"] or obj.stat().st_size != r["object_bytes"]:
                stale.append(f"{pop} {ir.name}: the sizes are not its build record's")
            elif ir.stat().st_mtime > obj.stat().st_mtime:
                stale.append(f"{pop} {ir.name}: newer than the staged object")
    for o in objs:
        va = va_of(Path(o).name)
        ir = irs["extra"][va]
        if ir.stat().st_mtime > Path(o).stat().st_mtime:
            stale.append(f"extra {ir.name}: newer than {o}")
    if stale:
        raise SystemExit(f"{len(stale)} folded modules are not what the link's objects were "
                         f"built from: {stale[:3]}")
    return pops


def one(job: tuple[str, int, str, str, str, bool, bool]) -> dict:
    pop, va, src, outdir, triple, legal, keep_ir = job
    src_p = Path(src)
    obj = Path(outdir) / pop / f"ir_{va:08X}.o"
    rec = {"population": pop, "va": f"0x{va:08x}", "ir": str(src_p.relative_to(ROOT)),
           "object": f"{pop}/{obj.name}", "legalized": legal}
    text = src_p.read_text(errors="replace")
    rec["ir_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    t0 = time.time()
    if obj.exists():
        obj.unlink()            # a failed compile must not leave a stale object
    tmp = None
    if legal:
        try:
            text, st = F.legalize(text)
        except F.Refused as e:
            rec.update(compiled=False, refused=str(e)[:600])
            return rec
        rec["rewrites"] = st.get("rewrites", 0)
        rec["transcendental"] = st.get("transcendental", {})
        if keep_ir:
            ir = Path(outdir) / "ir" / pop / f"sub_{va:08X}.f80.ll"
            ir.parent.mkdir(parents=True, exist_ok=True)
        else:
            fd, name = tempfile.mkstemp(suffix=".f80.ll", dir=str(Path(outdir) / "tmp"))
            os.close(fd)
            ir = tmp = Path(name)
        ir.write_text(text)
    else:
        ir = src_p
    rec["legalize_seconds"] = round(time.time() - t0, 3)
    t1 = time.time()
    try:
        p = subprocess.run(compile_command(pop, triple, ir, obj), capture_output=True, text=True,
                           timeout=1200, cwd=str(Path(outdir) / pop))
        rc, err = p.returncode, p.stderr + p.stdout
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after 1200 s"
    finally:
        if tmp is not None:
            tmp.unlink()
    rec["compile_seconds"] = round(time.time() - t1, 3)
    rec["compiled"] = rc == 0 and obj.exists()
    rec["object_bytes"] = obj.stat().st_size if obj.exists() else 0
    rec["error_head"] = "" if rec["compiled"] else CA.S.error_head(err, 4)[:600]
    return rec


def build_shims(outdir: Path, triple: str, defines: list[str]) -> dict:
    obj = outdir / "f80_shims.o"
    cmd = compile_command("map", triple, F.SHIMS_C, obj)
    cmd[cmd.index(str(F.SHIMS_C)):cmd.index(str(F.SHIMS_C))] = defines
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"the shims did not compile for {triple}:\n{p.stderr[-3000:]}")
    return {"object": str(obj.relative_to(ROOT) if obj.is_relative_to(ROOT) else obj),
            "defines": defines,
            "source_sha256": C.sha256(F.SHIMS_C), "object_sha256": C.sha256(obj)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="a zig target triple")
    ap.add_argument("--legalize", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--populations", default="map,arms,extra")
    ap.add_argument("--out-root", default=None,
                    help="default: private/generated/s7/targets/<triple>[+f80]")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--limit", type=int, default=0, help="first N modules of each population")
    ap.add_argument("--keep-ir", action="store_true", help="keep the legalized IR (GBs)")
    ap.add_argument("--shim-define", action="append", default=[],
                    help="a -D for tools/s7/f80_shims.c, e.g. SA2_F80_TRANSC_X87 (x86 only)")
    args = ap.parse_args()

    legal = {"on": True, "off": False, "auto": not x86(args.target)}[args.legalize]
    variant = args.target + ("+f80" if legal and x86(args.target) else
                             "+nof80" if not legal and not x86(args.target) else "")
    outdir = Path(args.out_root).resolve() if args.out_root else TARGETS / variant
    pops = populations()
    chosen = [p for p in args.populations.split(",") if p]
    for p in chosen:
        if p not in pops:
            raise SystemExit(f"unknown population {p}; known: {sorted(pops)}")
        (outdir / p).mkdir(parents=True, exist_ok=True)
    (outdir / "tmp").mkdir(parents=True, exist_ok=True)
    jobs = []
    for p in chosen:
        mods = pops[p][: args.limit] if args.limit else pops[p]
        jobs += [(p, va, str(src), str(outdir), args.target, legal, args.keep_ir)
                 for va, src in mods]
    print(f"target={args.target} legalize={legal} out={outdir} modules={len(jobs)} "
          f"jobs={args.jobs}", flush=True)
    log = outdir / "build.jsonl"
    t0 = time.time()
    n = ok = 0
    with log.open("w") as fh, cf.ProcessPoolExecutor(args.jobs) as ex:
        for rec in ex.map(one, jobs, chunksize=4):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            ok += bool(rec.get("compiled"))
            if n % 2000 == 0:
                el = time.time() - t0
                print(f"  {n}/{len(jobs)} compiled={ok} {el:.0f}s", flush=True)
    wall = round(time.time() - t0, 1)
    (outdir / "tmp").rmdir()            # empty: each worker removes its own temporary IR
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    built = {r["object"] for r in recs}
    stale = [o for p in chosen for o in sorted((outdir / p).glob("ir_*.o"))
             if f"{p}/{o.name}" not in built]
    for o in stale:
        o.unlink()                      # not this build's: an earlier build of other modules
    summary: dict = {
        "schema": "sa2-s7-cross-build/v2", "target": args.target, "variant": variant,
        "legalized": legal, "out": str(outdir), "jobs": args.jobs, "wall_seconds": wall,
        "commands": {p: list(command_template(p)) for p in chosen},
        "pass_sha256": C.sha256(Path(F.__file__)),
        "stale_objects_removed": len(stale),
        "by_population": {}, "errors": {}, "refused": {},
    }
    for p in chosen:
        rs = [r for r in recs if r["population"] == p]
        summary["by_population"][p] = {
            "modules": len(rs), "compiled": sum(bool(r.get("compiled")) for r in rs),
            "refused": sum("refused" in r for r in rs),
            "rewrites": sum(r.get("rewrites", 0) for r in rs),
            "with_transcendental": sum(bool(r.get("transcendental")) for r in rs)}
    for r in recs:
        if "refused" in r:
            summary["refused"][r["refused"][:160]] = summary["refused"].get(r["refused"][:160], 0) + 1
        elif not r.get("compiled"):
            k = (r.get("error_head") or "").splitlines()[0][:200] if r.get("error_head") else "?"
            summary["errors"][k] = summary["errors"].get(k, 0) + 1
    summary["modules"] = len(recs)
    summary["compiled"] = sum(bool(r.get("compiled")) for r in recs)
    if legal:
        summary["shims"] = build_shims(outdir, args.target, [f"-D{d}" for d in args.shim_define])
        objs = [outdir / r["object"] for r in recs if r.get("compiled")]
        cen = F.census(objs, F.shim_names(outdir / "f80_shims.o"), args.jobs)
        (outdir / "census.json").write_text(json.dumps(cen, indent=1) + "\n")
        summary["census"] = {k: cen[k] for k in ("objects", "ok", "classes", "disallowed")}
        summary["census"]["shim_symbols"] = sorted(s for s, e in cen["symbols"].items()
                                                   if e["class"] == "shim")
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("target", "variant", "modules", "compiled",
                                              "wall_seconds", "by_population", "errors",
                                              "refused")}, indent=1))
    if legal:
        print(json.dumps(summary["census"], indent=1))
    if summary["compiled"] != summary["modules"] or (legal and not summary["census"]["ok"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
