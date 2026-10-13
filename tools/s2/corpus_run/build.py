#!/usr/bin/env python3
"""S2 corpus run, phase `build`: one program holding every lifted fixture-covered function.

`tools/s2/wrap/wrap.py` links one driver per function, which is right for eleven
functions with no overlapping IR and wrong here: the fixture-covered closure is 147
functions that call each other, so they go into one program with one function table.
That needs remill's per-module missing-block stubs folded first
(`tools/s2/corpus_run/internalize.py`).

Compiler and flags are `Documentation/receipts/s2-build-time-sample-v1.json`'s pipeline,
unchanged: `zig cc -target x86_64-linux-musl -O2`.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/build.py [--variant pc24|fp80] [--force] [--jobs N]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pefile
from iced_x86 import Decoder, FlowControl, Formatter, FormatterSyntax

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools" / "spike"))
import spikelib as S  # noqa: E402

GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
LIFT = OUT / "lift"
FOLD = OUT / "fold"
BUILD = OUT / "build"
GENC = OUT / "gen"
MAP = GEN / "function-map.json"
RUNTIME = ROOT / "runtime"
HARNESS = ROOT / "harness"
REMILL_INC = ROOT / "private" / "lifters" / "remill" / "usr" / "local" / "include"
GEN_TABLE = ROOT / "tools" / "s2" / "wrap" / "gen_function_table.py"

TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"
RUNTIME_C = ["guest_memory.c", "function_table_impl.c", "sa2_trap.c", "sa2_call.c"]
RUNTIME_CXX = ["remill_runtime.cpp"]
HARNESS_C = ["plan_driver.c"]


EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"

# runtime/remill_runtime.h's SA2_MB_EDGE_*.
EDGE_UNKNOWN, EDGE_JMP, EDGE_JCC, EDGE_FALL, EDGE_CALL = 0, 1, 2, 3, 4
EDGE_NAMES = {EDGE_UNKNOWN: "unknown", EDGE_JMP: "jmp", EDGE_JCC: "jcc-taken",
              EDGE_FALL: "fall-through", EDGE_CALL: "call-return"}

# Addresses the reaching-instruction policy dispatches although the edge is not a `jmp`.
# Each needs a reason, the reason is compiled into the driver and printed with the census,
# and anything not named here traps. This list is the whole of what the withdrawn
# target-membership rule used to do silently for every function-table entry.
DISPATCH_EXCEPTIONS = {
    0x004624C0: (
        "the loop head of GroundCollisionStep's candidate loop. `jl 004624C0h` at "
        "0x00463692 is the loop's back edge, taken out of the 0x00462F83 tail window, "
        "which starts after the head and so has no block for it. remill always calls "
        "__remill_missing_block in tail position -- `%x = tail call ...` then `ret ptr "
        "%x` -- so dispatching it does not push a guest frame and is a jump to that "
        "address, which is what the back edge is. The target is the entry of its own "
        "lifted module (lift_set.py --block 0x004624C0:0x00463822), so the jump lands "
        "where the guest lands. Two fixtures depend on it: ground-candidate-loop-v1 and "
        "ground-executor-multi-v1, 1 dispatch per row each."
    ),
}


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def lifted_range(va: int, entry: dict, fmap: dict) -> tuple[int, int, int]:
    """`(start, block_end, decode_end)` for the module at `va`.

    `entry["bytes"]` is the blob handed to remill, so `[start, block_end)` is exactly the
    range remill has blocks for -- the range an edge has to leave to become a missing
    block. `decode_end` is the last *image* byte of that blob: a `--block START:END+ret`
    window's blob ends in one synthetic 0xC3 that is not an image byte, and decoding the
    image there would read a different instruction and invent an edge.
    """
    blob = int(entry.get("bytes") or 0)
    if not blob and va in fmap:
        blob = int(fmap[va]["size"])
    image_bytes = entry.get("image_bytes")
    decode = int(image_bytes) if image_bytes is not None else blob
    return va, va + blob, va + decode


def reaching_edges(image: bytes, ranges: dict[int, tuple[int, int]]) -> tuple[list[dict], list[dict]]:
    """Every control-flow edge that leaves a lifted range, by the instruction that takes it.

    This is the static half of the missing-block policy (runtime/remill_runtime.h). remill
    lifts one byte range per module; an edge to an address outside that range has no block,
    so the lifted code calls `__remill_missing_block` with the target. Which instruction
    reaches it is decided here, from the image, rather than guessed at run time from whether
    the target happens to be a function-table entry -- the rule the G2 interrogation
    falsified.

    Only the edges that *become* missing blocks are recorded: `jmp`, a conditional branch's
    taken edge and its fall-through, the fall-through of a trailing non-branch, and the
    return fall-through of a trailing `call`. A direct `call rel32` to an out-of-range
    target is not one of them -- remill emits a `call @sub_<target>` there, which
    `internalize.py` folds into `__sa2_dispatch_stub`, a different path.

    When two ranges reach the same address by different kinds the stricter one wins, so a
    target that any non-`jmp` edge reaches never dispatches. `conflicts` records every such
    address, because a silent merge is how the original rule stayed wrong.
    """
    fmt = Formatter(FormatterSyntax.INTEL)
    found: dict[int, list[dict]] = {}

    def add(target: int, source: int, kind: int, text: str, owner: int) -> None:
        found.setdefault(target, []).append(
            {"target": f"0x{target:08x}", "source": f"0x{source:08x}", "kind": kind,
             "kind_name": EDGE_NAMES[kind], "insn": text, "module": f"0x{owner:08x}"})

    for va, (start, end, decode_end) in sorted(ranges.items()):
        off = start - 0x400000
        if off < 0 or decode_end - 0x400000 > len(image) or end <= start:
            continue
        decoder = Decoder(32, image[off:decode_end - 0x400000], ip=start)
        for ins in decoder:
            if ins.is_invalid:
                continue
            nxt = ins.next_ip
            fc = ins.flow_control
            if fc == FlowControl.UNCONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if t and not (start <= t < end):
                    add(t, ins.ip, EDGE_JMP, fmt.format(ins), va)
            elif fc == FlowControl.CONDITIONAL_BRANCH:
                t = ins.near_branch_target
                if t and not (start <= t < end):
                    add(t, ins.ip, EDGE_JCC, fmt.format(ins), va)
                if not (start <= nxt < end):
                    add(nxt, ins.ip, EDGE_FALL, fmt.format(ins), va)
            elif fc == FlowControl.CALL:
                if not (start <= nxt < end):
                    add(nxt, ins.ip, EDGE_CALL, fmt.format(ins), va)
            elif fc in (FlowControl.RETURN, FlowControl.INDIRECT_BRANCH,
                        FlowControl.EXCEPTION, FlowControl.INTERRUPT):
                pass
            else:
                if not (start <= nxt < end):
                    add(nxt, ins.ip, EDGE_FALL, fmt.format(ins), va)

    edges, conflicts = [], []
    for target in sorted(found):
        seen = found[target]
        kinds = {e["kind"] for e in seen}
        # The strictest kind wins: only an address every recorded edge reaches by `jmp`
        # may dispatch. UNKNOWN is not among `seen`, so `min` over the non-jmp kinds.
        kind = EDGE_JMP if kinds == {EDGE_JMP} else min(k for k in kinds if k != EDGE_JMP)
        pick = next(e for e in seen if e["kind"] == kind)
        why = DISPATCH_EXCEPTIONS.get(target)
        edges.append({"target": target, "source": int(pick["source"], 16), "kind": kind,
                      "kind_name": EDGE_NAMES[kind], "insn": pick["insn"],
                      "module": pick["module"], "edges": seen,
                      "allow": bool(why and kind != EDGE_JMP), "why": why})
        if len(kinds) > 1:
            conflicts.append({"target": f"0x{target:08x}",
                              "kinds": sorted(EDGE_NAMES[k] for k in kinds),
                              "resolved_to": EDGE_NAMES[kind], "reached_by": seen})
    return edges, conflicts


def emit_edge_table(edges: list[dict], path: Path, variant: str) -> None:
    lines = [
        "/* GENERATED by tools/s2/corpus_run/build.py -- do not edit, never committed.",
        " *",
        " * The missing-block reaching-instruction table (runtime/remill_runtime.h). One row",
        " * per guest address a lifted module can reach with no block for it, carrying the",
        " * instruction that reaches it. The runtime dispatches only a `jmp` edge.",
        " */",
        "#include <stddef.h>",
        '#include "remill_runtime.h"',
        "",
        f"static const sa2_mb_edge kEdges[] = {{  /* variant {variant}, {len(edges)} edges */",
    ]
    for e in edges:
        why = ("\n     " + json.dumps(e["why"])) if e.get("why") else "NULL"
        lines.append(f"    {{0x{e['target']:08x}u, 0x{e['source']:08x}u, {e['kind']}, "
                     f"{1 if e.get('allow') else 0}, {why}}},"
                     f"  /* {e['kind_name']}: {e['insn']} in {e['module']} */")
    lines += [
        "};",
        "",
        "void sa2_mb_register_generated(void) {",
        f"    sa2_mb_set_edges(kEdges, {len(edges)}u);",
        "}",
        "",
    ]
    path.write_text("\n".join(lines))


def cc(args, out: Path, cwd: Path) -> dict:
    cmd = [str(S.ZIG)] + args + ["-o", str(out)]
    p = sh(cmd, cwd=cwd)
    return {"ok": p.returncode == 0,
            "command": " ".join(cmd).replace(str(ROOT) + "/", ""),
            "error_head": S.error_head(p.stderr or p.stdout) if p.returncode else ""}


def fold_one(src: Path, dst: Path, force: bool) -> tuple[dict, bool]:
    """Fold the lifted module `src` into `dst` (`internalize.rewrite`): its statistics,
    and whether `dst` was written.

    The statistics are the fold's whether or not `dst` is written: they describe the
    folded modules, not this run's cache misses. Accumulating them only on a write made a
    cached re-run report zero of everything, which is the number that reached the
    receipt (G2 finding 8).

    `dst` is written with `force`, when it is missing or older than `src`, and when its
    text is not what the current fold code makes. The last is the S5 case: the speed
    passes changed the fold without touching any lifted file, so a cache kept by mtime
    alone kept every old fold and object, and the fixtures ran the old code. A written
    fold is newer than its object, so `compile_ir` compiles it again."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import internalize  # noqa: E402
    text, st = internalize.rewrite(src.read_text(errors="replace"))
    if (force or not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime
            or text != dst.read_text(errors="replace")):
        dst.write_text(text)
        return st, True
    return st, False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="pc24", choices=("pc24", "fp80"))
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    variant = args.variant
    suffix = ".pc24.ll" if variant == "pc24" else ".ll"

    for d in (FOLD, BUILD, GENC):
        d.mkdir(parents=True, exist_ok=True)
    lift = json.loads((OUT / "lift.json").read_text())
    vas = sorted(int(a, 16) for a, e in lift["functions"].items() if e.get("lifted"))

    log = {"variant": variant, "target": " ".join(TARGET), "opt": OPT,
           "functions": len(vas), "fold": {}, "runtime": {}, "harness": {}, "ir": {}}
    t0 = time.time()

    # 1. fold the per-module missing-block stubs
    totals = {"stub_defines_removed": 0, "stub_declares_removed": 0, "references_redirected": 0}
    unexpected, kept, deleted_unexpected = [], [], []
    folded_now = 0
    for va in vas:
        st, written = fold_one(LIFT / f"sub_{va:08X}{suffix}", FOLD / f"sub_{va:08X}.{variant}.ll",
                               args.force)
        folded_now += written
        for k in totals:
            totals[k] += st[k]
        unexpected += st["unexpected_stub_bodies"]
        kept += st["stub_bodies_kept_internal"]
        deleted_unexpected += st["deleted_unexpected"]
    log["fold"] = dict(totals, unexpected_stub_bodies=sorted(set(unexpected)),
                       stub_bodies_kept_internal=sorted(set(kept)),
                       deleted_unexpected=sorted(set(deleted_unexpected)),
                       modules=len(vas), rewritten_this_run=folded_now,
                       tool="tools/s2/corpus_run/internalize.py")
    # G2 finding 4: `internalize` used to append a non-trampoline body to
    # `unexpected_stub_bodies` and delete it anyway. It now keeps such a body with
    # `internal` linkage, so `deleted_unexpected` is empty unless someone restores the
    # old path -- and then the build stops rather than linking a program from which a
    # real lifted block was silently removed.
    if deleted_unexpected:
        js = OUT / f"build.{variant}.json"
        js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")
        raise SystemExit("build: internalize deleted "
                         f"{len(set(deleted_unexpected))} stub body/bodies that are not "
                         f"missing-block trampolines: {sorted(set(deleted_unexpected))}")

    # 1b. the plain `sub_<hex>` symbols the folded modules call but none defines: a
    # `call` into a function the closure missed (a jump target's own callee, say).
    # The link would fail on them; naming them here lets close_traps.py lift them.
    defined, declared = set(), set()
    for va in vas:
        text = (FOLD / f"sub_{va:08X}.{variant}.ll").read_text(errors="replace")
        defined |= set(re.findall(r"^define\s[^@\n]*@(sub_[0-9a-fA-F]+)\(", text, re.M))
        declared |= set(re.findall(r"^declare\s[^@\n]*@(sub_[0-9a-fA-F]+)\(", text, re.M))
    log["undefined_sub_symbols"] = sorted(f"0x{int(s[4:], 16):08x}" for s in declared - defined)

    # 2. runtime and harness objects
    cflags = ["cc"] + TARGET + [OPT, "-w", "-c", "-I", str(RUNTIME), "-I", str(HARNESS)]
    for src in RUNTIME_C:
        log["runtime"][src] = cc(cflags + [str(RUNTIME / src)], BUILD / (Path(src).stem + ".o"), BUILD)
    for src in RUNTIME_CXX:
        log["runtime"][src] = cc(["c++"] + TARGET + [OPT, "-w", "-std=c++17", "-c", "-I", str(RUNTIME),
                                                     "-I", str(REMILL_INC), str(RUNTIME / src)],
                                 BUILD / (Path(src).stem + ".o"), BUILD)
    for src in HARNESS_C:
        log["harness"][src] = cc(cflags + [str(HARNESS / src)], BUILD / (Path(src).stem + ".o"), BUILD)

    # 3. every lifted module
    def compile_ir(va: int):
        ll = FOLD / f"sub_{va:08X}.{variant}.ll"
        o = BUILD / f"ir_{variant}_{va:08X}.o"
        if not args.force and o.exists() and o.stat().st_mtime > ll.stat().st_mtime:
            return va, {"ok": True, "cached": True}
        return va, cc(["cc"] + TARGET + [OPT, "-w", "-c", str(ll)], o, BUILD)

    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for va, r in ex.map(compile_ir, vas):
            log["ir"][f"0x{va:08x}"] = r

    # 4. the function table over all of them
    gen_c = GENC / f"function_table_corpus_{variant}.c"
    gen_js = GENC / f"function_table_corpus_{variant}.json"
    cmd = [sys.executable, str(GEN_TABLE), "--map", str(MAP), "--out", str(gen_c), "--json", str(gen_js),
           "--ll"] + [str(FOLD / f"sub_{va:08X}.{variant}.ll") for va in vas]
    p = sh(cmd)
    log["function_table"] = {"rc": p.returncode, "error_head": S.error_head(p.stderr) if p.returncode else "",
                             "summary": json.loads(gen_js.read_text()) if gen_js.exists() else None,
                             "generator": "tools/s2/wrap/gen_function_table.py"}
    if log["function_table"]["summary"]:
        log["function_table"]["summary"].pop("addresses", None)
        log["function_table"]["summary"].pop("ll", None)
    gen_o = BUILD / f"function_table_corpus_{variant}.o"
    log["function_table"]["compile"] = cc(cflags + [str(gen_c)], gen_o, BUILD)

    # 4b. the missing-block reaching-instruction table, over the same lifted ranges the
    # modules were produced from. This is the corpus's load path: without it every
    # missing block is UNKNOWN and traps.
    fmap = {int(f["address"], 16): f for f in json.loads(MAP.read_text())["functions"]}
    ranges = {va: lifted_range(va, lift["functions"][f"0x{va:08x}"], fmap) for va in vas}
    image = bytes(pefile.PE(str(EXE), fast_load=True).get_memory_mapped_image())
    edges, conflicts = reaching_edges(image, ranges)
    edge_c = GENC / f"missing_block_edges_{variant}.c"
    emit_edge_table(edges, edge_c, variant)
    by_kind: dict[str, int] = {}
    for e in edges:
        by_kind[e["kind_name"]] = by_kind.get(e["kind_name"], 0) + 1
    log["missing_block_edges"] = {
        "count": len(edges), "by_kind": dict(sorted(by_kind.items())),
        "dispatchable": sum(1 for e in edges if e["kind"] == EDGE_JMP or e.get("allow")),
        "named_exceptions": {f"0x{e['target']:08x}": e["why"] for e in edges if e.get("allow")},
        "conflicts": conflicts,
        "generated": str(edge_c.relative_to(ROOT)),
        "policy": "runtime/remill_runtime.h SA2_MB_REACHED: only a `jmp` edge dispatches",
        "edges": [{k: v for k, v in e.items() if k != "edges"} | {"target": f"0x{e['target']:08x}",
                                                                  "source": f"0x{e['source']:08x}"}
                  for e in edges],
    }
    edge_o = BUILD / f"missing_block_edges_{variant}.o"
    log["missing_block_edges"]["compile"] = cc(cflags + [str(edge_c)], edge_o, BUILD)

    # 4c. A failed compile used to leave the previous object on disk and the link then
    # succeeded against it: the edge table was built from a stale object of a different
    # struct layout and the driver trapped on addresses the current table dispatches.
    # Every object this build produces is checked before anything is linked.
    failed = [f"{name}: {r['error_head'][:400]}"
              for name, r in list(log["runtime"].items()) + list(log["harness"].items())
              + [("function_table", log["function_table"]["compile"]),
                 ("missing_block_edges", log["missing_block_edges"]["compile"])]
              if not r.get("ok")]
    failed += [f"{va}: {r['error_head'][:200]}" for va, r in log["ir"].items() if not r.get("ok")]
    if failed:
        js = OUT / f"build.{variant}.json"
        js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")
        print("build: compile failures, nothing linked:")
        for line in failed:
            print("  " + line)
        raise SystemExit(1)

    # 5. link
    objs = [str(BUILD / (Path(s).stem + ".o")) for s in RUNTIME_C + RUNTIME_CXX + HARNESS_C]
    objs += [str(gen_o), str(edge_o)] + [str(BUILD / f"ir_{variant}_{va:08X}.o") for va in vas]
    binary = BUILD / f"corpus_driver_{variant}"
    link = cc(["c++"] + TARGET + [OPT] + objs, binary, BUILD)
    link["binary"] = str(binary.relative_to(ROOT)) if link["ok"] else None
    link["command"] = link["command"][:200] + f" ... [{len(objs)} objects]"
    link["missing_symbols"] = sorted(set(re.findall(r"undefined symbol: (\S+)", link["error_head"])))
    log["link"] = link
    log["ir_ok"] = sum(1 for r in log["ir"].values() if r["ok"])
    log["seconds"] = round(time.time() - t0, 2)

    js = OUT / f"build.{variant}.json"
    js.write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")
    print(f"{variant}: folded {totals['stub_defines_removed']} stub defines, "
          f"{log['ir_ok']}/{len(vas)} IR objects, "
          f"{len(edges)} missing-block edges ({log['missing_block_edges']['dispatchable']} dispatchable), "
          f"link ok={link['ok']} in {log['seconds']}s")
    if not link["ok"]:
        print(link["error_head"][:3000])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
