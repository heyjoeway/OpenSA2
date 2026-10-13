#!/usr/bin/env python3
"""Lift the blocks the whole-image lift could not reach, as modules of their own.

Subtitle: one remill invocation, the PC24 pass and the fold, exactly as
`tools/s3/whole_image/{lift_all,compile_all}.py` do them, for a handful of
addresses named on the command line instead of for the whole map.

The problem it exists for
------------------------
remill lifts what is *reachable from the entry address it is given*. The
whole-image lift gives it one entry per function-map row, so a basic block the
map's entry cannot reach is in no module at all -- 129 638 instructions, 11.1 %
of the map, by S3's census. Most of those are jump-table arms, which
`tools/s3/arms` now seeds. The rest are the other half of the same defect: the
analyzer's map splits a function, and the bytes after the split are handed to a
module whose entry cannot reach them.

`__calloc_impl` is the case that stops CRT startup. The map has

    0x007b99ac __calloc_impl      ends at 0x007b9aa8
    0x007b9aa8 FUN_007b9aa8       ends at 0x007b9aca

but 0x007b9aa8 is `_unlock(4)`, a five-instruction block that returns at
0x007b9ab0, and 0x007b9ac2 is `__calloc_impl`'s shared epilogue:

    007b9a74  jne short 007B9AC2h      ; the success path of every calloc
    ...
    007b9ac2  mov eax,ebx
    007b9ac4  call 007ACC39h           ; __SEH_epilog
    007b9ac9  ret

The bytes were given to remill for module 0x007b9aa8, and remill never reached
them, because nothing in that module branches there. So the guest's `calloc`
can allocate and cannot return.

What this does
--------------
Lift the unreached address as its own module and register a function-table row
for it, so the missing-block dispatch resolves. That is the third of the three
options S3's journal listed for the same defect class -- "give each function a
block table" -- applied to the addresses a run actually reaches, rather than to
all of them.

It is a *local* correction, not a fix for the map: the map is still wrong and
`Documentation/S3_INPUTS.md` section 1 still owes the real repair. The addresses
this tool is given are recorded with the run that reached them, so the list is
evidence of which map rows are wrong and not a growing pile of magic numbers.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/lift_extra.py --tag next+allarms --lift-tag next \\
      --entry 0x007b9ac2 --why "the shared epilogue of __calloc_impl"
  # or, from a probe's missing-block census:
  .venv/bin/python tools/s4/lift_extra.py --tag next+allarms --lift-tag next \\
      --from-census private/generated/s4/run/crt.iter.json
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s2" / "corpus_run"))
import common as C  # noqa: E402

import spikelib as S  # noqa: E402

OUT = C.GEN / "s4" / "lift"
LINKDIR = C.GEN / "s4" / "link"
TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"
MISSING_BLOCK_CALL = re.compile(r"\bcall [^\n]*@__remill_missing_block\b")


def module_ranges(lift_tag: str, arms_tag: str | None) -> list[tuple[int, int, str]]:
    rows = []
    for line in (C.LIFT / f"{lift_tag}.jsonl").read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            rows.append((int(d["va"], 16), int(d["end"], 16), d.get("name", "")))
    if arms_tag:
        p = C.GEN / "s3" / "arms" / f"arms.{arms_tag}.jsonl"
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    lo, hi = (int(x, 16) for x in d["anchor"])
                    rows.append((int(d["entry"], 16), hi, "arm"))
    return sorted(rows)


def containing(rows, va: int) -> tuple[int, int, str] | None:
    best = None
    for start, end, name in rows:
        if start <= va < end and (best is None or end - start < best[1] - best[0]):
            best = (start, end, name)
    return best


def lift_one(va: int, end: int, image: bytes, lldir: Path, anchor: int | None = None) -> dict:
    """Lift `va` over the bytes up to `end`. With an `anchor`, the bytes start
    there and remill is seeded with `--entry_address va`, as S3 lifts its arms
    (`tools/s3/arms/lift_arms.py`): an arm's own branches go back to its switch's
    loop head, before the arm, and bytes that start at the arm cannot hold it."""
    import pc24pass  # tools/spike/pc24pass, read-only

    lo = va if anchor is None else anchor
    raw = image[lo - C.IMAGE_BASE:end - C.IMAGE_BASE]
    ll = lldir / f"sub_{va:08X}.ll"
    seed = "" if anchor is None else f" --entry_address {va:#x}"
    inner = (f"mount --bind {C.REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 "
             f"--arch x86 --os windows --address {lo:#x}{seed} --bytes {raw.hex()} --ir_out {ll}")
    cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        rc, err = p.returncode, p.stderr + p.stdout
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after 900 s"
    text = ll.read_text(errors="replace") if ll.exists() else ""
    lifted = rc == 0 and re.search(rf"^define [^\n]*@sub_{va:x}\(", text, re.M) is not None
    rec = {"va": f"0x{va:08x}", "end": f"0x{end:08x}", "bytes": len(raw),
           **({"anchor": f"0x{anchor:08x}"} if anchor is not None else {}),
           "lift_seconds": round(time.time() - t0, 3), "rc": rc, "lifted": lifted,
           "missing_block_calls": len(MISSING_BLOCK_CALL.findall(text)),
           "error_head": "" if lifted else "\n".join(err.strip().splitlines()[:3])}
    if not lifted:
        return rec
    helper = C.HELPER_LL.read_text()
    int_helper = C.INT_HELPER_LL.read_text()
    out, st = pc24pass.instrument(text, helper, int_helper, True, True, True)
    (lldir / f"sub_{va:08X}.pc24.ll").write_text(out)
    rec["pass"] = {"ops_instrumented": st.get("ops_instrumented"),
                   "fist_rewrites": st.get("fist_rewrites"),
                   "fld_quiet_fixed": st.get("fld_quiet_fixed"),
                   "fld_quiet_refused": st.get("fld_quiet_refused")}
    if st.get("fld_quiet_refused"):
        # The module would keep remill's quieting of every FLD m32/m64 while IE is set
        # (pc24pass.py, "FLD"). An exception, not SystemExit: rebuild_image.py calls this
        # in a process pool, which hands an exception back and loses a worker's SystemExit.
        raise RuntimeError(f"the PC24 pass refused the FLD quieting fix in extra 0x{va:08x}: "
                           f"{st['fld_quiet_refused_sites'][:3]}")
    return rec


DECLARE_SUB = re.compile(r"^declare[^\n]*@(sub_[0-9a-fA-F]+)\(", re.M)


def fold_and_compile(va: int, lldir: Path, folddir: Path, objdir: Path) -> dict:
    import internalize  # tools/s2/corpus_run/internalize.py, read-only

    src = lldir / f"sub_{va:08X}.pc24.ll"
    dst = folddir / f"sub_{va:08X}.pc24.ll"
    obj = objdir / f"ir_{va:08X}.o"
    text, st = internalize.rewrite(src.read_text(errors="replace"))
    dst.write_text(text)
    cmd = [str(S.ZIG), "cc"] + TARGET + [OPT, "-w", "-c", str(dst), "-o", str(obj)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=str(objdir))
    return {"folded": True,
            "declares_sub": sorted(set(DECLARE_SUB.findall(text))),
            "fold": {k: st[k] for k in ("stub_defines_removed", "stub_declares_removed",
                                        "references_redirected")},
            "compiled": p.returncode == 0 and obj.exists(),
            "compile_seconds": round(time.time() - t0, 3),
            "object": str(obj), "object_bytes": obj.stat().st_size if obj.exists() else 0,
            "error_head": "" if p.returncode == 0 else (p.stderr + p.stdout)[:600]}


def emit_registry(rows: list[dict], path: Path, tag: str) -> None:
    out = [
        "/* GENERATED by tools/s4/lift_extra.py -- do not edit, never committed.",
        " *",
        " * Function-table rows for blocks the whole-image lift could not reach from any",
        " * map entry, lifted as modules of their own. Registered AFTER the generated",
        " * table, so a row here wins the address (function_table.h: a later",
        f" * registration wins). Tag: {tag}.",
        " */",
        '#include "function_table.h"',
        "",
    ]
    for r in rows:
        out.append(f"extern void *sub_{int(r['va'], 16):x}(void *, unsigned, void *);")
    out += ["", "int sa2_s4_extra_register(void) {", "    int n = 0;"]
    for r in rows:
        va = int(r["va"], 16)
        why = json.dumps(r.get("why") or "")
        out.append(f"    /* {why} */")
        out.append(f"    n += sa2_ft_add(0x{va:08x}u, (sa2_lifted_fn)sub_{va:x}, "
                   f"\"blk_{va:x}\", SA2_FN_LIFTED) >= 0 ? 1 : 0;")
    out += ["    return n;", "}", ""]
    path.write_text("\n".join(out))


def seed_arms(state: dict, jobs: list, have: set, entries: set, lift_tag: str) -> list:
    """Jobs for every jump-table arm in the lifted image that has no row yet.

    The G3 decision gives every arm its own trace and function-table row. S3
    seeded arms from the analyzer's jump-table records, and those are
    incomplete in three ways `tools/s4/switches.py` found: a function the map
    does not list has none (0x0081C0E0's 86-arm table cost a worker thread at
    0x0081F363); a map function the analyzer under-sized or whose table it did
    not recover has none (`AddRings`, `LoadTitleModule`, 0x00823410); and a
    recorded table can leave out a default arm the index table still routes
    through it (0x0043658D). The census covers the map's modules, the extras
    and the extras this call adds. Each arm is anchored on the map range that
    contains it, as S3 anchors its arms, and what could not be read is kept in
    the state.
    """
    import image as IM
    import switches as SW

    img = IM.Image()
    map_rows = module_ranges(lift_tag, None)
    good = [r for r in state["rows"] if r.get("compiled") and not r.get("rejected")]
    failed = {int(r["va"], 16): r for r in state["rows"]
              if not (r.get("compiled") and not r.get("rejected"))}
    ranges = [(va, end) for va, end, _n in map_rows]
    ranges += [(int(r["va"], 16), int(r["end"], 16)) for r in good]
    ranges += [(va, end) for va, end, _o, _w, _a in jobs]
    recorded = SW.recorded_tables(C.by_address())
    found = SW.census(img, ranges, recorded)
    # Registered means a row the registry will emit: a row that did not compile,
    # or was rejected, gives the arm no function-table row, and is a problem to
    # look at rather than a thing to retry quietly.
    registered = set(entries) | {int(r["va"], 16) for r in good}
    problems, seeded = [], {}
    for sw in found.values():
        if sw.problem:
            problems.append({"dispatch": f"0x{sw.dispatch:08x}", "problem": sw.problem})
            continue
        for arm in sw.arms:
            if arm in registered or arm in seeded:
                continue
            if arm in failed:
                problems.append({"dispatch": f"0x{sw.dispatch:08x}", "arm": f"0x{arm:08x}",
                                 "problem": "has a row that did not compile or was rejected: "
                                            + str(failed[arm].get("rejected_why")
                                                  or failed[arm].get("error_head") or "")[:200]})
                continue
            own = containing(map_rows, arm)
            head = containing(map_rows, sw.dispatch)
            if own is None:
                problems.append({"dispatch": f"0x{sw.dispatch:08x}", "arm": f"0x{arm:08x}",
                                 "problem": "in no map module's range, so nothing anchors it"})
                continue
            # From the earlier of the arm's range and its switch's, so an arm in a
            # range after its dispatch still has the switch head in its bytes.
            anchor = (head[0], own[1], head[2]) if head and head[0] < own[0] else own
            seeded[arm] = (anchor, sw)
    jobs_out = []
    for arm, (anchor, sw) in sorted(seeded.items()):
        bound = {
            "check": f"its bound check admits {sw.cases} key values"
                     + (f" through the byte index table at 0x{sw.index_table:08x}"
                        if sw.index_table is not None else ""),
            "layout": f"it has an index table and no immediate bound check, so the table is "
                      f"read by its layout, up to its index table at 0x{sw.index_table or 0:08x}",
            "relocations": f"no bound check within {SW.LOOK_BACK} instructions, so the table is "
                           f"its run of relocated pointers into the range, up to the next table",
            "analyzer": "the table is the analyzer's record",
        }[sw.bound_from]
        analyzer = ("the analyzer's record of this table does not list this arm"
                    if sw.dispatch in recorded else "the analyzer recorded no table here")
        why = (f"an arm of the {sw.entries}-entry jump table at 0x{sw.table:08x}, dispatched "
               f"by the jmp at 0x{sw.dispatch:08x}; {bound}; {analyzer}. The G3 decision "
               f"gives every arm a trace and a row, and S3 seeded arms only from the "
               f"analyzer's records. Anchored on {anchor[2] or 'the map range'} "
               f"0x{anchor[0]:08x}..0x{anchor[1]:08x} as S3's arms are "
               f"(tools/s4/switches.py, lift_extra.py --seed-arms)")
        jobs_out.append((arm, anchor[1], anchor, why, anchor[0]))
    all_arms = {a for sw in found.values() for a in sw.arms}
    state["arm_seeding"] = {
        "dispatches": len(found),
        "bounds": {b: sum(1 for sw in found.values() if sw.bound_from == b)
                   for b in ("check", "layout", "relocations", "analyzer")},
        "arms": len(all_arms),
        "already_registered": len(all_arms & registered),
        "seeded": len(jobs_out),
        "problems": problems,
    }
    print(f"arm seeding: {len(found)} dispatches in {len(ranges)} module ranges, "
          f"{len(all_arms)} distinct arms, {state['arm_seeding']['already_registered']} "
          f"already registered, {len(jobs_out)} to lift, {len(problems)} problem(s)", flush=True)
    return jobs_out


def seed_continuations(state: dict, jobs: list, have: set, entries: set, lift_tag: str) -> list:
    """Jobs for the address after every instruction remill cannot lift.

    remill ends a trace at `paddusb`, `paddusw`, `cvtps2pi`, `fnstenv`, `fldenv`,
    `fnsave` and `frstor` with sync hypercall 256 and `__remill_error` at the
    next instruction; `runtime/sa2_emulate.cpp` executes the instruction and the
    runtime continues at that address through the function table. So each site
    lifted code can reach (`tools/s4/emulated.py`) needs an entry there,
    anchored on the map range that holds it so the continuation's own branches
    back into its loop stay in its trace.
    """
    import image as IM
    import emulated as EM

    map_rows = module_ranges(lift_tag, None)
    good = [r for r in state["rows"] if r.get("compiled") and not r.get("rejected")]
    failed = {int(r["va"], 16): r for r in state["rows"]
              if not (r.get("compiled") and not r.get("rejected"))}
    pending = [{"va": f"0x{va:08x}", "end": f"0x{end:08x}",
                **({"anchor": f"0x{a:08x}"} if a is not None else {})}
               for va, end, _o, _w, a in jobs]
    found = EM.census(IM.Image(), C, good + pending)
    registered = set(entries) | {int(r["va"], 16) for r in good} | \
        {va for va, _e, _o, _w, _a in jobs}
    problems, out = [], []
    for site in found.values():
        nxt = site.next_ip
        if nxt in registered:
            continue
        if nxt in failed:
            problems.append({"site": f"0x{site.ip:08x}", "continuation": f"0x{nxt:08x}",
                             "problem": "has a row that did not compile or was rejected"})
            continue
        anchor = containing(map_rows, nxt)
        if anchor is None:
            problems.append({"site": f"0x{site.ip:08x}", "continuation": f"0x{nxt:08x}",
                             "problem": "in no map module's range, so nothing anchors it"})
            continue
        why = (f"the continuation after {site.kind} at 0x{site.ip:08x}, which remill cannot "
               f"lift: its trace ends there with sync hypercall 256 and __remill_error at "
               f"this address, and the runtime executes the instruction "
               f"(runtime/sa2_emulate.cpp) and continues here. Anchored on "
               f"{anchor[2] or 'the map range'} 0x{anchor[0]:08x}..0x{anchor[1]:08x} "
               f"(tools/s4/emulated.py, lift_extra.py --seed-continuations)")
        out.append((nxt, anchor[1], anchor, why, anchor[0]))
        registered.add(nxt)
    state["continuation_seeding"] = {
        "sites": len(found),
        "by_kind": {k: sum(1 for x in found.values() if x.kind == k)
                    for k in sorted({x.kind for x in found.values()})},
        "seeded": len(out),
        "problems": problems,
    }
    print(f"continuation seeding: {len(found)} sites lifted code reaches, {len(out)} "
          f"continuations to lift, {len(problems)} problem(s)", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--lift-tag", required=True)
    ap.add_argument("--arms-tag", default=None)
    ap.add_argument("--entry", action="append", default=[])
    ap.add_argument("--why", action="append", default=[])
    ap.add_argument("--from-census", type=Path, default=None,
                    help="a crt_probe JSON; every missing-block target it trapped on is added")
    ap.add_argument("--end", default=None, help="override the range end for a single --entry")
    ap.add_argument("--drop", action="append", default=[],
                    help="remove the row and object for this address and stop registering "
                         "it. A block lifted as its own module and later found to be the "
                         "interior of a *wider* module is a mistake to undo, not to keep: "
                         "both objects define the same sub_<va> symbol and the link "
                         "refuses. Recorded in the state as `dropped` with the reason.")
    ap.add_argument("--drop-why", default="")
    ap.add_argument("--relift", action="store_true",
                    help="drop any existing row for the named --entry addresses first. "
                         "Needed when the first lift used the map's end and the map's end "
                         "is wrong: a module whose range stops one instruction before a "
                         "fall-through leaves that edge as SA2_MB_EDGE_FALL, which policy "
                         "2 refuses to dispatch whatever the target is -- and re-running "
                         "without this flag silently keeps the short module.")
    ap.add_argument("--from-list", type=Path, default=None,
                    help="tools/s3/whole_image/dispatch_surface.py's <hex> <group> table")
    ap.add_argument("--group", action="append", default=[],
                    help="which groups of --from-list to lift, e.g. rdata_vtable_entry_unmapped")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--sweep-min-run", type=int, default=3,
                    help="a swept dword counts as a code pointer only when it is part of a "
                         "run of at least this many consecutive aligned dwords that are all "
                         "code pointers -- a pointer array, rather than a coincidence")
    ap.add_argument("--seed-arms", action="store_true",
                    help="also lift every jump-table arm in the lifted image -- the map's "
                         "modules, the extras and the ones this call adds -- that is not yet "
                         "a module entry (tools/s4/switches.py finds them; give --arms-tag "
                         "so S3's arms count as registered)")
    ap.add_argument("--seed-continuations", action="store_true",
                    help="also lift an entry after every instruction remill cannot lift, "
                         "where the runtime continues once it has executed it "
                         "(tools/s4/emulated.py finds them; give --arms-tag)")
    ap.add_argument("--reseed", default="",
                    help="with --seed-arms: drop every seeded arm first and seed again; the "
                         "value is the reason, recorded in the state")
    ap.add_argument("--sweep-section", action="append", default=[],
                    help="lift every aligned dword in this section whose value is an address "
                         "inside an executable section and is not already a module entry "
                         "(e.g. .rdata: the static-initialiser and vtable pointer arrays)")
    args = ap.parse_args()

    for d in (OUT / "ll", OUT / "fold", OUT / "obj"):
        d.mkdir(parents=True, exist_ok=True)
    LINKDIR.mkdir(parents=True, exist_ok=True)

    state_path = LINKDIR / f"extra_functions.{args.tag}.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"rows": []}
    have = {r["va"] for r in state["rows"]}

    wants: list[tuple[int, str]] = []
    for k, e in enumerate(args.entry):
        # **A reason is required, not optional.** This read
        # `args.why[k] if k < len(args.why) else ""` and two blocks were lifted
        # with an empty reason because nobody passed a matching --why. Both
        # reasons were recoverable, from a thread-start receipt and a run's
        # exit_pc, but only because the runs happened to survive; the record
        # itself had lost them. Lifting a block changes what the guest can
        # execute, and rule 14 asks the same of it as of a dispatch exception.
        if k >= len(args.why) or not str(args.why[k]).strip():
            raise SystemExit(f"--entry {e} has no matching --why. A block lifted for "
                             "no recorded reason is the thing rule 14 forbids, and "
                             "the reason is cheapest to write now.")
        wants.append((int(e, 0), args.why[k]))
    if args.from_census:
        doc = json.loads(args.from_census.read_text())
        for e in doc.get("missing_block", {}).get("census", []):
            if int(e.get("traps", 0)) > 0:
                wants.append((int(e["target"], 16),
                              f"reached by {e['kind']} from {e['source']} in a run of "
                              f"{args.from_census.name}, and in no module"))
    if args.drop:
        gone = {f"0x{int(e, 0):08x}" for e in args.drop}
        present = {r["va"] for r in state["rows"]} & gone
        for r in state["rows"]:
            if r["va"] in gone:
                o = r.get("object")
                if o and Path(o).is_file():
                    Path(o).unlink()
        state.setdefault("dropped", []).extend(
            [{"va": v, "why": args.drop_why} for v in sorted(present)])
        state["rows"] = [r for r in state["rows"] if r["va"] not in gone]
        have = {r["va"] for r in state["rows"]}
        missing_from_state = sorted(gone - present)
        if missing_from_state:
            raise SystemExit("--drop names addresses this state has no row for: "
                             + ", ".join(missing_from_state))

    if args.relift:
        drop = {f"0x{int(e, 0):08x}" for e in args.entry}
        kept = [r for r in state["rows"] if r["va"] not in drop]
        state["relifted"] = sorted(drop & {r["va"] for r in state["rows"]})
        state["rows"] = kept
        have = {r["va"] for r in kept}

    rows = module_ranges(args.lift_tag, args.arms_tag)
    entries = {va for va, _end, _n in rows}
    if args.from_list:
        groups = set(args.group)
        for line in args.from_list.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2 or (groups and parts[1] not in groups):
                continue
            va = int(parts[0], 16)
            # An address that is already a module entry has a `sub_<va>` symbol
            # in the build; lifting it again would define that symbol twice and
            # the link would refuse. Those are already dispatchable anyway.
            if va in entries:
                continue
            # A third column onward is the row's own evidence, when the list
            # carries it (tools/s4/address_taken.py does); the generic reason
            # is for the S3 tables, which have none.
            detail = " ".join(parts[2:]) if len(parts) > 2 else (
                "a dispatch target the whole-image lift produced no module for")
            wants.append((va, f"{parts[1]} in {args.from_list.name}: {detail}"))
    # An address that is already a module entry has a `sub_<va>` symbol in the
    # build, so lifting it again defines that symbol twice and the link
    # refuses -- four thousand objects later, with nothing pointing at the
    # cause. `--from-list` has always skipped those; `--from-census` and
    # `--entry` did not, and the hole was found by a census row for
    # 0x00826F60, a *function-table entry reached by a fall-through*. That is
    # not a missing module at all: the module exists and the edge policy
    # refuses to dispatch a fall-through, which `mb_edges.py --allow-target`
    # is the reviewed mechanism for. Lifting it produced a duplicate symbol
    # and a link error instead.
    if not args.relift:
        skipped = [(va, why) for va, why in wants if va in entries]
        if skipped:
            state.setdefault("skipped_already_a_module", []).extend(
                {"va": f"0x{va:08x}", "why": why,
                 "reason": "already a module entry in the whole-image lift; lifting it "
                           "again would define sub_%08x twice. If a run cannot reach it, "
                           "the edge policy is what refuses, and "
                           "tools/s4/mb_edges.py --allow-target is the reviewed fix."
                           % va}
                for va, why in skipped)
            for va, _ in skipped:
                print(f"skip {va:#010x}: already a module entry; "
                      f"see mb_edges.py --allow-target", flush=True)
        wants = [(va, why) for va, why in wants if va not in entries]

    if args.sweep_section:
        import pefile
        pe = pefile.PE(str(C.EXE), fast_load=True)
        base = pe.OPTIONAL_HEADER.ImageBase
        img = C.memory_image()
        execs = [(base + x.VirtualAddress, base + x.VirtualAddress + x.Misc_VirtualSize)
                 for x in pe.sections if x.Characteristics & 0x20000000]
        from iced_x86 import Decoder
        boundaries: dict[int, set[int]] = {}

        def is_boundary(v: int) -> bool:
            """Decoded forward from the entry of the module that contains it, does an
            instruction start exactly at `v`? An aligned dword in .rdata whose value
            happens to land inside `.text` is not necessarily a code pointer, and
            lifting from the middle of an instruction produces a module that calls
            addresses that do not exist -- which is a link error four thousand
            objects later, not a finding. This is `tools/s4/image.py`'s anchored-decode
            predicate, which the import-surface work already established as the one
            that separates a real reference from a coincidence."""
            own = containing(rows, v)
            if own is None:
                return False
            key = own[0]
            if key not in boundaries:
                blk = img[own[0] - base:own[1] - base]
                boundaries[key] = {i.ip for i in Decoder(32, blk, ip=own[0]) if not i.is_invalid}
            return v in boundaries[key]

        # A dword is a code pointer only if it is *in a pointer array*.
        #
        # The boundary test alone is close to a coin flip: a linear decode of a
        # few-hundred-byte function starts an instruction at a large fraction of
        # its byte offsets, so any two-byte pair out of a UTF-16 string in
        # .rdata has a good chance of passing. A review of the first version of
        # this sweep found 1026 accepted rows of which 351 came from dwords
        # inside ASCII text -- 0x0089da3c holds 0x00410020, which is the
        # `" " "A"` of "SONIC ADVENTURE" -- and they cost 2.4 of the 3.9 minutes
        # the whole extra lift added to a build-time kill criterion with 9.6
        # minutes of headroom.
        #
        # Static-initialiser tables and vtables are *runs* of consecutive
        # aligned code pointers; string bytes are isolated hits. So the
        # predicate is run length, which is a property of the data layout
        # rather than of how forgiving x86 decoding is.
        skipped_not_boundary = skipped_isolated = 0
        for sec in pe.sections:
            nm = sec.Name.rstrip(b"\0").decode("ascii", "replace")
            if nm not in args.sweep_section:
                continue
            lo = base + sec.VirtualAddress
            hi = lo + sec.Misc_VirtualSize
            addrs = list(range((lo + 3) & ~3, hi - 3, 4))
            ok: list[bool] = []
            vals: list[int] = []
            for va in addrs:
                v = int.from_bytes(img[va - base:va - base + 4], "little")
                vals.append(v)
                good = any(a <= v < b for a, b in execs) and (v in entries or is_boundary(v))
                if any(a <= v < b for a, b in execs) and v not in entries and not good:
                    skipped_not_boundary += 1
                ok.append(good)
            i = 0
            while i < len(addrs):
                if not ok[i]:
                    i += 1
                    continue
                j = i
                while j < len(addrs) and ok[j]:
                    j += 1
                run = j - i
                for k in range(i, j):
                    if vals[k] in entries:
                        continue
                    if run < args.sweep_min_run:
                        skipped_isolated += 1
                        continue
                    wants.append((vals[k],
                                  f"an aligned dword in {nm} at 0x{addrs[k]:08x}, one of a run "
                                  f"of {run} consecutive code pointers, holding an address the "
                                  f"whole-image lift produced no module for"))
                i = j
        state["sweep_skipped_not_an_instruction_boundary"] = skipped_not_boundary
        state["sweep_skipped_not_in_a_pointer_run"] = skipped_isolated
        state["sweep_min_run"] = args.sweep_min_run
        print(f"sweep: {len(wants)} candidates; {skipped_not_boundary} dwords rejected as not "
              f"an instruction boundary, {skipped_isolated} more as not part of a run of "
              f"{args.sweep_min_run} consecutive code pointers")
    if not wants:
        if not args.drop and not args.seed_arms and not args.seed_continuations:
            raise SystemExit("nothing to lift: give --entry, --from-census, --from-list, "
                             "--sweep-section, --seed-arms or --seed-continuations")

    image = C.memory_image()
    jobs = []
    for va, why in wants:
        key = f"0x{va:08x}"
        if key in have:
            continue
        have.add(key)
        if args.end and len(wants) == 1:
            end, owner = int(args.end, 0), (va, int(args.end, 0), "(--end)")
        else:
            owner = containing(rows, va)
            if owner is None:
                # An address in no module's byte range has nothing to bound the
                # lift with. Skipping it is a finding, not a failure: it is
                # recorded and the run that reaches it will still trap by name.
                state["rows"].append({"va": key, "lifted": False, "compiled": False,
                                      "why": why, "bytes": 0, "missing_block_calls": 0,
                                      "error_head": "in no lifted module's byte range, so "
                                                    "there is no end to bound the lift with"})
                continue
            end = owner[1]
        jobs.append((va, end, owner, why, None))

    if args.seed_arms:
        if not args.arms_tag:
            raise SystemExit("--seed-arms needs --arms-tag: without S3's arms counted as "
                             "registered, every one of them would be seeded again, and each "
                             "would define a sub_ symbol S3's objects already define")
        if args.reseed:
            # Every seeded arm again, with the census and reasons as they now are.
            # Recorded as a count and a reason, not as 1208 `dropped` rows.
            # Arms only: continuations are anchored too, and --seed-continuations
            # owns them.
            gone = [r for r in state["rows"] if str(r.get("why", "")).startswith("an arm of the")]
            for r in gone:
                o = r.get("object")
                if o and Path(o).is_file():
                    Path(o).unlink()
            state["rows"] = [r for r in state["rows"] if r not in gone]
            have = {r["va"] for r in state["rows"]}
            state["reseeded"] = {"rows": len(gone), "why": args.reseed}
        jobs += seed_arms(state, jobs, have, entries, args.lift_tag)
    if args.seed_continuations:
        if not args.arms_tag:
            raise SystemExit("--seed-continuations needs --arms-tag, for the reason "
                             "--seed-arms does")
        # Until a pass seeds nothing: a continuation's own range is part of the
        # next pass's census.
        passes = []
        while True:
            more = seed_continuations(state, jobs, have, entries, args.lift_tag)
            passes.append(state["continuation_seeding"])
            if not more:
                break
            jobs += more
        state["continuation_seeding"] = dict(passes[0], passes=len(passes),
                                             seeded=sum(x["seeded"] for x in passes),
                                             problems=[q for x in passes for q in x["problems"]])

    def do(job):
        va, end, owner, why, anchor = job
        rec = lift_one(va, end, image, OUT / "ll", anchor)
        rec["why"] = why
        rec["owner"] = {"start": f"0x{owner[0]:08x}" if isinstance(owner[0], int) else owner[0],
                        "end": f"0x{end:08x}", "name": owner[2]}
        if rec["lifted"]:
            rec.update(fold_and_compile(va, OUT / "ll", OUT / "fold", OUT / "obj"))
        return rec

    added = []
    if args.jobs > 1 and len(jobs) > 1:
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            for rec in ex.map(do, jobs):
                state["rows"].append(rec)
                added.append(rec)
                if not rec.get("compiled"):
                    print(f"{rec['va']}: lifted={rec['lifted']} compiled={rec.get('compiled')} "
                          f"{rec.get('error_head', '')[:160]}")
    else:
        for job in jobs:
            rec = do(job)
            state["rows"].append(rec)
            added.append(rec)
            print(f"{rec['va']}: lifted={rec['lifted']} compiled={rec.get('compiled')} "
                  f"bytes={rec['bytes']} missing_block_calls={rec['missing_block_calls']} "
                  f"{rec.get('error_head', '')[:200]}")

    state["rows"].sort(key=lambda r: int(r["va"], 16))

    # A module may declare `sub_X` for a call target that nothing defines -- a
    # garbage lift, or a target that is itself in no module. Linking it fails
    # with an undefined symbol thousands of objects later, so the rejection
    # happens here, with a reason, and iterates: rejecting one module can orphan
    # the next.
    defined = {f"sub_{va:x}" for va, _e, _n in module_ranges(args.lift_tag, args.arms_tag)}
    rejected = []
    while True:
        avail = defined | {f"sub_{int(r['va'], 16):x}" for r in state["rows"]
                           if r.get("compiled") and not r.get("rejected")}
        changed = False
        for r in state["rows"]:
            if not r.get("compiled") or r.get("rejected"):
                continue
            missing = [d for d in r.get("declares_sub", []) if d not in avail]
            if missing:
                r["rejected"] = True
                r["rejected_why"] = ("declares " + ", ".join(missing[:4])
                                     + ", which no module in this build defines")
                rejected.append(r["va"])
                changed = True
        if not changed:
            break
    state["rejected"] = len(rejected)
    state["rejected_head"] = rejected[:20]
    good = [r for r in state["rows"] if r.get("compiled") and not r.get("rejected")]
    emit_registry(good, LINKDIR / f"extra_functions.{args.tag}.c", args.tag)
    state.update(tag=args.tag, lift_tag=args.lift_tag, arms_tag=args.arms_tag,
                 registered=len(good), attempted=len(state["rows"]),
                 objects=[r["object"] for r in good],
                 registry_c=str(LINKDIR / f"extra_functions.{args.tag}.c"))
    state_path.write_text(json.dumps(state, indent=1) + "\n")
    print(f"{len(added)} new, {len(good)} of {len(state['rows'])} registered")
    print(f"-> {state['registry_c']}\n-> {state_path}")
    for r in good:
        print(f"   link object: {r['object']}")


if __name__ == "__main__":
    main()
