#!/usr/bin/env python3
"""arm64_link: link the registered replays' binaries for aarch64-linux-musl.

Subtitle: tools/s4/link_s4.py's link, as tools/s4/tick_replay.py's runs build it through
tools/s4/loop_run.py and tools/s4/stage_run.py, for another triple: the runtime and the harness
compiled from a source tree for that triple, the lifted image's three populations from
tools/s7/cross_build.py's legalised objects, and tools/s7/f80_shims.c, the soft-x87 the
legalised objects call.

What each replay links is read from the tools that link it on x86-64, never typed here:

  runtime   link_s4.py's RUNTIME_C and RUNTIME_CXX, its -O2 and its PRODUCTION_WRAPS, read
            from the source tree's own link_s4.py (so a snapshot links what it holds);
  hooks     a replay's hooks and wraps: tick_replay.HOOK and WRAP for the boot entry,
            stage_run.HOOKS and WRAPS for a stage entry; the main is reach_receipt.MAIN;
  generated loop_run.link_args's --extra-c inputs, in its order: the edge table, the extras'
            C, then the hooks, each compiled as link_s4 names it (`extra<k>_<stem>`);
  lifted    the map and arm objects build.jsonl's records name (hard-linked into one
            directory, as the staged link directory is, and linked from it in name order),
            then the extras in extra_functions.next+allarms.json's order, then the shims.

Two flags differ from the x86-64 link, and both are recorded. `-ffp-contract=off` is passed to
every runtime and harness compile for a non-x86 triple: the x86-64 build targets the x86-64
baseline, which has no fused multiply-add, so its C arithmetic rounds each operation; AArch64
always has one, and clang's default contraction would fuse `a*b+c` in the runtime's own C and
round once where x86-64 rounds twice. The lifted objects are cross_build's, compiled from IR
that carries no `contract` flag and no fmuladd, so contraction cannot reach them; the link
record counts the fused multiply-add instructions in every object group to show it
(`fma_words`). And the shims are compiled with -DSA2_F80_COUNT -DSA2_F80_SITES: the measuring
build counts each transcendental call by call site (tools/s7/f80_shims.c), so that the replay
that is compared is the run that is counted. Neither define touches a value the shims compute.

The shims are named sa2_lf80_* (and sa2_x87_*), never sa2_f80_*. That prefix is the runtime's:
runtime/cpu_state.h exports sa2_f80_from_f64, sa2_f80_to_f64 and sa2_f80_to_f32 on its own
sa2_f80 type, and the first aarch64 link of the shims beside the runtime failed on those three
names as duplicate symbols (2026-10-11), so tools/s7/f80_legalize.py's calls were renamed.

Nothing is written outside --out. The link record and the build census are JSON written under
names that do not end in `.json` (`.rec`): tools/s4/m7_inputs.py counts every *.json under
private/generated as a possible game-run record.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s7/arm64_link.py link --src <tree or snapshot> --out <dir> \\
      [--replay boot] [--replay metalharbor] [--jobs 4]
  .venv/bin/python tools/s7/arm64_link.py census --out <dir> [--census-out PATH]
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

def _root() -> Path:
    """The repository: the nearest directory above this file holding AGENTS.md and
    tools/pins.json (this file's grandparent in the tree; a snapshot's run finds the tree)."""
    for p in Path(__file__).resolve().parents:
        if (p / "AGENTS.md").is_file() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"no repository above {__file__}")


ROOT = _root()
SELF = "tools/s7/arm64_link.py"
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
sys.path.insert(0, str(ROOT / "tools" / "s7"))
# This file's neighbours first (a snapshot's copies, when this is a snapshot's), then the
# tree's tools/s7 for the ones a snapshot does not hold (target_receipt).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402  tools/s3/whole_image/common.py, read-only

TRIPLE = "aarch64-linux-musl"
TARGET = "arm64-linux"
GEN = ROOT / "private" / "generated"
OBJECTS = GEN / "s7" / "targets" / TRIPLE
STAGED = C.BUILD / "next+allarms"
TABLE = "s3/link/{tag}/function_table_whole.c"
EDGES = "s4/link/mb_edges.{tag}.c"
EXTRA_C = "s4/link/extra_functions.{tag}.c"
EXTRA_STATE = "s4/link/extra_functions.{tag}.json"
SHIMS = "tools/s7/f80_shims.c"
PASSES = ("tools/s7/f80_legalize.py", SHIMS)
SHIM_DEFINES = ("-DSA2_F80_COUNT", "-DSA2_F80_SITES")
FP_FLAGS = ("-ffp-contract=off",)
LINK_REC = "link.rec"
CENSUS_SCHEMA = "sa2-s7-build-census/v1"
CENSUS_TRACKED = "Documentation/receipts/s7-arm64-linux-build-v1.json"
ZIG = ROOT / json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]["zig"]["path"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    path = Path(path)
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def x86(triple: str) -> bool:
    return triple.split("-")[0] in ("x86_64", "x86", "i386", "i686")


def link_s4_lists(src: Path) -> dict:
    """RUNTIME_C, RUNTIME_CXX, OPT and PRODUCTION_WRAPS as the source tree's link_s4.py says."""
    tree = ast.parse((src / "tools" / "s4" / "link_s4.py").read_text())
    want = ("RUNTIME_C", "RUNTIME_CXX", "OPT", "PRODUCTION_WRAPS")
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in want:
            found[node.targets[0].id] = ast.literal_eval(node.value)
    missing = [w for w in want if w not in found]
    if missing:
        raise SystemExit(f"{src}/tools/s4/link_s4.py does not define {missing} as literals")
    return found


def replay_plan(name: str) -> dict:
    """The main, the hooks and the wraps a registered replay's x86-64 link uses, from the tools
    that link it: a stage entry through stage_run.command, the boot entry through run_lifted."""
    import reach_receipt as RR  # noqa: PLC0415
    import stage_run as SR  # noqa: PLC0415
    import tick_replay as T  # noqa: PLC0415
    entries = {e["name"]: e for e in json.loads(T.REGISTRY.read_text())["replays"]}
    if name not in entries:
        raise SystemExit(f"{name} is not a registered replay ({sorted(entries)})")
    entry = entries[name]
    if T.is_stage(entry):
        hooks, wraps = list(SR.HOOKS), list(SR.WRAPS)
    else:
        hooks, wraps = [T.HOOK], [T.WRAP]
    return {"name": name, "stage": T.is_stage(entry), "tag": RR.TAG, "main": RR.MAIN,
            "hooks": hooks, "wraps": wraps}


def fma_words(obj: Path) -> int:
    """AArch64 floating-point data-processing (3 source) instructions in an object's code:
    FMADD, FMSUB, FNMADD, FNMSUB (encoding 0x1F000000 under mask 0xFF000000, any size), counted
    over every executable section. An ELF64 little-endian object or executable."""
    data = obj.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise SystemExit(f"{obj} is not an ELF64 little-endian file")
    shoff = int.from_bytes(data[0x28:0x30], "little")
    shentsize = int.from_bytes(data[0x3A:0x3C], "little")
    shnum = int.from_bytes(data[0x3C:0x3E], "little")
    n = 0
    for i in range(shnum):
        sh = data[shoff + i * shentsize: shoff + (i + 1) * shentsize]
        sh_type = int.from_bytes(sh[4:8], "little")
        flags = int.from_bytes(sh[8:16], "little")
        if sh_type != 1 or not flags & 0x4:          # PROGBITS and SHF_EXECINSTR
            continue
        off = int.from_bytes(sh[24:32], "little")
        size = int.from_bytes(sh[32:40], "little")
        for k in range(off, off + size - 3, 4):
            if data[k + 3] == 0x1F:                  # bits 31..24 of the little-endian word
                n += 1
    return n


# AArch64 relocation types of a branch to a symbol: BL (a call, which returns to the next
# instruction) and B (a tail call, which returns to the caller's caller).
R_AARCH64_JUMP26, R_AARCH64_CALL26 = 282, 283
TRANSCENDENTAL_SHIMS = tuple(f"sa2_lf80_{n}" for n in ("sin", "cos", "tan", "atan", "atan2",
                                                       "exp2", "log2", "exp", "log", "log10",
                                                       "pow", "asin", "acos"))


def shim_branches(obj: Path) -> dict[str, dict[str, int]]:
    """Each transcendental shim an AArch64 object branches to, with its calls (BL) and its tail
    calls (B), read from the object's relocations. tools/s7/f80_shims.c counts a call by its
    return address, so a tail call would be counted at the caller's caller: the link records
    these so a reader can hold the count to none."""
    data = obj.read_bytes()
    shoff = int.from_bytes(data[0x28:0x30], "little")
    shentsize = int.from_bytes(data[0x3A:0x3C], "little")
    shnum = int.from_bytes(data[0x3C:0x3E], "little")
    secs = [data[shoff + i * shentsize: shoff + (i + 1) * shentsize] for i in range(shnum)]
    out: dict[str, dict[str, int]] = {}
    for sh in secs:
        if int.from_bytes(sh[4:8], "little") != 4:          # SHT_RELA
            continue
        symtab = secs[int.from_bytes(sh[40:44], "little")]
        sym_off = int.from_bytes(symtab[24:32], "little")
        strtab = secs[int.from_bytes(symtab[40:44], "little")]
        str_off = int.from_bytes(strtab[24:32], "little")
        off, size = int.from_bytes(sh[24:32], "little"), int.from_bytes(sh[32:40], "little")
        for k in range(off, off + size, 24):
            info = int.from_bytes(data[k + 8:k + 16], "little")
            kind, sym = info & 0xFFFFFFFF, info >> 32
            if kind not in (R_AARCH64_JUMP26, R_AARCH64_CALL26):
                continue
            at = sym_off + sym * 24
            name_at = str_off + int.from_bytes(data[at:at + 4], "little")
            name = data[name_at:data.index(b"\0", name_at)].decode()
            if name in TRANSCENDENTAL_SHIMS:
                row = out.setdefault(name, {"call": 0, "tail": 0})
                row["call" if kind == R_AARCH64_CALL26 else "tail"] += 1
    return out


def compile_one(job: tuple) -> dict:
    name, cmd, obj = job
    t0 = time.time()
    if obj.exists():
        obj.unlink()                    # a failed compile must not leave a stale object
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    rec = {"rc": p.returncode, "seconds": round(time.time() - t0, 2)}
    if p.returncode:
        rec["stderr_tail"] = p.stderr[-3000:]
    else:
        rec["object_sha256"] = sha256(obj)
    return {name: rec}


def compile_base(src: Path, gen: Path, out: Path, triple: str, plan: dict, lists: dict,
                 jobs: int) -> tuple[list[Path], dict]:
    """Compile the runtime, the generated C, the hooks and the main for `triple` into
    out/obj/<replay>/, as link_s4's phase_compile names them; (objects in link order, log)."""
    tag = plan["tag"]
    objdir = out / "obj" / plan["name"]
    objdir.mkdir(parents=True, exist_ok=True)
    fp = [] if x86(triple) else list(FP_FLAGS)
    inc = ["-I", str(src / "runtime"), "-I", str(C.REMILL_INC)]
    cflags = [str(ZIG), "cc", "-target", triple, lists["OPT"], "-w", "-c"] + inc + fp
    cxxflags = [str(ZIG), "c++", "-target", triple, lists["OPT"], "-w", "-std=c++17", "-c"] \
        + inc + fp
    table = gen / TABLE.format(tag=tag)
    extra_c = [gen / EDGES.format(tag=tag)]
    state = json.loads((gen / EXTRA_STATE.format(tag=tag)).read_text())
    if state.get("objects"):
        extra_c.append(gen / EXTRA_C.format(tag=tag))
    extra_c += [src / h for h in plan["hooks"]]
    sources = [(s, src / "runtime" / s, cflags) for s in lists["RUNTIME_C"]]
    sources += [(s, src / "runtime" / s, cxxflags) for s in lists["RUNTIME_CXX"]]
    sources.append(("function_table_whole.c", table, cflags))
    sources += [(f"extra{k}_{x.stem}.c", x, cflags) for k, x in enumerate(extra_c)]
    sources.append(("main.c", src / plan["main"], cflags))
    work, log = [], {}
    for name, path, flags in sources:
        if not path.is_file():
            raise SystemExit(f"{path} is not there ({name})")
        obj = objdir / (Path(name).stem + ".o")
        work.append((name, flags + [str(path), "-o", str(obj)], obj))
        log[name] = {"source": rel(path), "source_sha256": sha256(path)}
    with cf.ThreadPoolExecutor(max(1, jobs)) as ex:
        for done in ex.map(compile_one, work):
            for k, v in done.items():
                log[k].update(v)
    bad = {k: v for k, v in log.items() if v.get("rc")}
    if bad:
        raise SystemExit("compile failed: " + "; ".join(
            f"{k}: {v.get('stderr_tail', '')[-600:]}" for k, v in bad.items()))
    return [obj for _, _, obj in work], {"flags_c": cflags[1:], "flags_cxx": cxxflags[1:],
                                         "objects": log}


def lifted_objects(objects: Path, gen: Path, tag: str, linkdir: Path) -> dict:
    """The cross build's objects as the link takes them: the map and arm objects build.jsonl's
    compiled records name, hard-linked into `linkdir` (whose every other ir_*.o is removed),
    which must be the staged reference link directory's names exactly; the extras in the x86-64
    link's order; nothing failed."""
    log = objects / "build.jsonl"
    recs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    failed = [r.get("va") for r in recs if r.get("compiled") is not True]
    if failed:
        raise SystemExit(f"{len(failed)} modules of {log} did not compile (first {failed[:3]})")
    whole = {Path(r["object"]).name: objects / r["object"] for r in recs
             if r["population"] in ("map", "arms")}
    staged = {p.name for p in STAGED.glob("ir_*.o")}
    if set(whole) != staged:
        raise SystemExit(f"the cross build's map and arm objects are not {rel(STAGED)}'s: "
                         f"{len(set(whole) - staged)} extra, {len(staged - set(whole))} missing")
    linkdir.mkdir(parents=True, exist_ok=True)
    for t in linkdir.glob("ir_*.o"):
        if t.name not in whole:
            t.unlink()
    for name, obj in whole.items():
        t = linkdir / name
        if t.exists() and not t.samefile(obj):
            t.unlink()
        if not t.exists():
            os.link(obj, t)
    extras_have = {Path(r["object"]).name: objects / r["object"] for r in recs
                   if r["population"] == "extra"}
    order = [Path(o).name for o in json.loads((gen / EXTRA_STATE.format(tag=tag)).read_text())
             ["objects"]]
    missing = [o for o in order if o not in extras_have]
    if missing:
        raise SystemExit(f"{len(missing)} extras the link lists are not in {log} "
                         f"(first {missing[:3]})")
    # The modules the cross build found transcendental calls in, by its own record.
    transc = [objects / r["object"] for r in recs if r.get("transcendental")]
    return {"log": rel(log), "log_sha256": sha256(log), "whole": sorted(whole),
            "extras": [extras_have[o] for o in order],
            "records": len(recs), "transcendental_modules": transc}


def reference_objects(gen: Path, tag: str) -> dict:
    """The x86-64 link's own lifted objects, as link_s4.py takes them: every ir_*.o of the
    staged link directory, linked from it in name order, and the extras' objects at the paths
    extra_functions.next+allarms.json lists. The reference run of the cross-target comparison
    is linked from these, from the same sources as the arm64 binaries."""
    order = json.loads((gen / EXTRA_STATE.format(tag=tag)).read_text())["objects"]
    extras = [Path(o) if Path(o).is_absolute() else ROOT / o for o in order]
    missing = [str(x) for x in extras if not x.is_file()]
    if missing:
        raise SystemExit(f"{len(missing)} extras the link lists are not there (first {missing[:3]})")
    return {"log": None, "log_sha256": None, "whole": sorted(p.name for p in STAGED.glob("ir_*.o")),
            "extras": extras, "records": None, "dir": STAGED}


def build_shims(src: Path, out: Path, triple: str) -> dict:
    """tools/s7/f80_shims.c from the source tree, compiled with cross_build's own command for
    the map population (its triple swapped) and the measuring defines."""
    import cross_build as XB  # noqa: PLC0415  the tree's, for its command template only
    source = src / SHIMS
    obj = out / "f80_shims.o"
    cmd = XB.compile_command("map", triple, source, obj)
    at = cmd.index(str(source))
    cmd[at:at] = list(SHIM_DEFINES)
    if obj.exists():
        obj.unlink()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"the shims did not compile for {triple}:\n{p.stderr[-3000:]}")
    return {"object": rel(obj), "object_sha256": sha256(obj), "source": rel(source),
            "source_sha256": sha256(source), "defines": list(SHIM_DEFINES), "command": cmd[1:]}


def link(args: argparse.Namespace) -> int:
    src = Path(args.src).resolve()
    gen = Path(args.gen).resolve()
    out = Path(args.out).resolve()
    objects = Path(args.objects).resolve()
    triple = args.triple
    out.mkdir(parents=True, exist_ok=True)
    lists = link_s4_lists(src)
    if args.reference and not x86(triple):
        raise SystemExit("--reference links the x86-64 objects: pass an x86 --triple")
    shims = None if args.reference else build_shims(src, out, triple)
    names = args.replay or [e["name"] for e in json.loads(
        (ROOT / "Documentation" / "replays.json").read_text())["replays"]]
    status = 0
    for name in names:
        plan = replay_plan(name)
        lifted = (reference_objects(gen, plan["tag"]) if args.reference else
                  lifted_objects(objects, gen, plan["tag"], out / "linkdir"))
        linkdir = lifted.get("dir") or out / "linkdir"
        base, compiled = compile_base(src, gen, out, triple, plan, lists, args.jobs)
        wrapped = list(lists["PRODUCTION_WRAPS"]) + plan["wraps"]
        binary = out / "bin" / name / "sa2_main"
        binary.parent.mkdir(parents=True, exist_ok=True)
        if binary.exists():
            binary.unlink()
        cmd = ([str(ZIG), "c++", "-target", triple, lists["OPT"]]
               + [f"-Wl,-wrap={w}" for w in wrapped]
               + [str(o) for o in base] + [str(x) for x in lifted["extras"]]
               + ([str(ROOT / shims["object"])] if shims else []) + lifted["whole"]
               + ["-o", str(binary)])
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(linkdir), capture_output=True, text=True,
                           timeout=7200)
        rec = {"schema": "sa2-s7-arm64-link/v1", "generated_by": SELF, "replay": name,
               "triple": triple, "target": TARGET, "src": rel(src), "plan": plan,
               "link_s4": {k: lists[k] for k in ("RUNTIME_C", "RUNTIME_CXX", "OPT",
                                                  "PRODUCTION_WRAPS")},
               "fp_flags": [] if x86(triple) else list(FP_FLAGS),
               "compile": compiled, "shims": shims, "wrapped": wrapped,
               "reference": bool(args.reference),
               "lifted": {"log": lifted["log"], "log_sha256": lifted["log_sha256"],
                          "dir": rel(linkdir),
                          "records": lifted["records"], "whole_image": len(lifted["whole"]),
                          "extras": len(lifted["extras"])},
               "returncode": p.returncode, "seconds": round(time.time() - t0, 2),
               "stderr_tail": p.stderr[-3000:]}
        if p.returncode == 0 and binary.is_file():
            rec["binary"] = rel(binary)
            rec["binary_sha256"] = sha256(binary)
            rec["binary_bytes"] = binary.stat().st_size
            if not x86(triple):
                rec["fma_words"] = {
                    "binary": fma_words(binary),
                    "runtime_and_harness": sum(fma_words(o) for o in base),
                    "shims": fma_words(ROOT / shims["object"]) if shims else None,
                    "lifted_extras": sum(fma_words(x) for x in lifted["extras"]),
                    "lifted_whole_image": sum(fma_words(linkdir / o)
                                              for o in lifted["whole"])}
                sites: dict[str, dict[str, int]] = {}
                for obj in lifted.get("transcendental_modules") or []:
                    for name, row in shim_branches(obj).items():
                        got = sites.setdefault(name, {"call": 0, "tail": 0})
                        got["call"] += row["call"]
                        got["tail"] += row["tail"]
                rec["transcendental_branches"] = {
                    "modules": len(lifted.get("transcendental_modules") or []),
                    "by_shim": dict(sorted(sites.items())),
                    "calls": sum(r["call"] for r in sites.values()),
                    "tail_calls": sum(r["tail"] for r in sites.values())}
        else:
            status = 1
        (binary.parent / LINK_REC).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: rec.get(k) for k in ("replay", "returncode", "seconds", "binary",
                                                   "binary_sha256", "fma_words")}), flush=True)
        if p.returncode:
            print(p.stderr[-3000:], file=sys.stderr)
    return status


def census(args: argparse.Namespace) -> int:
    """The arm64 target's build census (Documentation/s7-green-means.md, `build`), from the
    cross build's log and this tool's link records: written to --census-out, or beside the
    links. Its lifted inputs are the reach manifest's, as tools/s7/target_receipt.py reads the
    reference's, since the cross build compiled the reference's folded IR."""
    import target_receipt as TR  # noqa: PLC0415
    out = Path(args.out).resolve()
    objects = Path(args.objects).resolve()
    manifest = json.loads((ROOT / TR.REACH).read_text())
    manifest = manifest.get("manifest", manifest)
    names = [e["name"] for e in json.loads((ROOT / TR.REGISTRY).read_text())["replays"]]
    log = objects / "build.jsonl"
    # The target passes the census names are the tree's files, so the objects and the shims
    # the links used must have been made by exactly those: the cross build's summary records the
    # pass it ran, and each link record the shims source it compiled.
    summary = json.loads((objects / "summary.json").read_text())
    passes = {p: sha256(ROOT / p) for p in PASSES}
    if summary.get("target") != args.triple or summary.get("legalized") is not True:
        raise SystemExit(f"{objects}/summary.json is not a legalised {args.triple} build")
    if summary.get("pass_sha256") != passes[PASSES[0]]:
        raise SystemExit(f"{objects} was built by another {PASSES[0]} "
                         f"({summary.get('pass_sha256')}): build it again with the tree's")
    binaries, rc = {}, 0
    for name in names:
        recp = out / "bin" / name / LINK_REC
        if not recp.is_file():
            raise SystemExit(f"{recp} is absent: link {name} first")
        rec = json.loads(recp.read_text())
        rc = rc or rec.get("returncode", 1)
        binary = ROOT / rec.get("binary", "")
        if rec.get("returncode") != 0 or not binary.is_file() \
                or sha256(binary) != rec.get("binary_sha256"):
            raise SystemExit(f"{recp}: the link did not succeed or its binary moved")
        if (rec.get("lifted") or {}).get("log_sha256") != sha256(log):
            raise SystemExit(f"{recp} was linked from another {log}")
        if (rec.get("shims") or {}).get("source_sha256") != passes[SHIMS]:
            raise SystemExit(f"{recp} linked shims compiled from another {SHIMS}")
        binaries[name] = rec["binary_sha256"]
    doc = {"schema": CENSUS_SCHEMA, "generated_by": SELF, "target": TARGET, "triple": args.triple,
           "lifted_inputs": TR.lifted_inputs(manifest),
           "target_passes": passes,
           "log": {"path": rel(log), "sha256": sha256(log)},
           "link": {"returncode": rc, "binaries": binaries},
           "written": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    dest = Path(args.census_out).resolve() if args.census_out else out / "build-census.rec"
    dest.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"-> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("link", "census"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True, help="where the objects, binaries and records go")
        p.add_argument("--objects", default=str(OBJECTS), help="tools/s7/cross_build.py's output")
        p.add_argument("--triple", default=TRIPLE)
    lk = sub.choices["link"]
    lk.add_argument("--src", default=str(ROOT), help="the tree (or a snapshot of it) to compile")
    lk.add_argument("--gen", default=str(GEN), help="where the generated link inputs are")
    lk.add_argument("--replay", action="append", default=[], help="default: every registered")
    lk.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 4))
    lk.add_argument("--reference", action="store_true",
                    help="link the x86-64 link's own objects (with --triple x86_64-linux-musl), "
                         "the cross-target comparison's reference")
    sub.choices["census"].add_argument("--census-out", default=None,
                                       help=f"e.g. {CENSUS_TRACKED}; default beside the links")
    args = ap.parse_args(argv)
    return link(args) if args.cmd == "link" else census(args)


if __name__ == "__main__":
    sys.exit(main())
