#!/usr/bin/env python3
"""S3 whole-image link: one program from all 12 683 lifted objects.

Slice 1 measured the surface a link would face and did not attempt it: 0
duplicate external symbols, 0 `sub_<hex>` called and never defined, 259 MB of
objects, a 12 683-row function table. This links it.

What the link adds that the surface could not show is the linker's own limits.
Three of them are real at this size and none exists at S2's 165 modules:

  * the argument list -- 12 683 absolute object paths do not fit in one
    `execve`, so the link runs with its working directory *inside* the object
    directory and names its inputs relatively;
  * peak resident memory of the linker, measured rather than assumed;
  * whether anything the fold's renaming missed collides once every module is
    on one command line.

Phases, each checkpointed to `private/generated/s3/link/link.<tag>.json` as it
finishes, so a kill loses at most the phase in flight:

  table    tools/s3/whole_image/gen_table.py -> the generated function table
  compile  the runtime, the table and tools/s3/whole_image/link_probe.c
  link     zig c++ over every object; wall time, peak RSS, sections
  arms     every jump-table arm address the analyzer's map lists, each with the
           answer this build is predicted to give for it -> arms.<tag>.txt/.json
  run      the probe binary: load factor, insertion and lookup cost from the
           runtime's own counters, resolution, and what the linked table
           answers for every one of those arms

Usage:
  source tools/env.sh
  # the whole-image build: only the 4 map-entry arms have a symbol
  .venv/bin/python tools/s3/whole_image/link_all.py --tag next --arms-expect map-only
  # the arms-seeded build: every compiled arm trace has one too
  .venv/bin/python tools/s3/whole_image/link_all.py --tag next+arms \
      --arms-expect seeded --arms-lift-tag next
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import resource
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import gen_table  # noqa: E402

import spikelib as S  # tools/spike/spikelib.py, read-only  # noqa: E402

OUT = C.GEN / "s3" / "link"
RUNTIME = C.ROOT / "runtime"
TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"

RUNTIME_C = ["guest_memory.c", "function_table_impl.c", "sa2_trap.c", "sa2_call.c"]
RUNTIME_CXX = ["remill_runtime.cpp"]


def sections(path: Path) -> dict:
    """Section sizes of an ELF64 file, without depending on a host binutils."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return {}
    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    def sh(i: int) -> tuple:
        return struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * e_shentsize)
    stroff = sh(e_shstrndx)[4]
    out = {}
    for i in range(e_shnum):
        name_off, _t, _f, _a, _off, size, *_ = sh(i)
        end = data.index(b"\0", stroff + name_off)
        name = data[stroff + name_off:end].decode()
        if name and size:
            out[name] = size
    return out


def checkpoint(state: dict, tag: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"link.{tag}.json").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def _link_nofile(cmd: list[str]) -> dict | None:
    """Reserve one descriptor per explicit object plus bounded linker headroom.

    Only Zig cc/c++ object links are covered. Compile/preprocess/assembly commands
    and source-only links keep their existing process limits and result shape.
    """
    if (len(cmd) < 2 or Path(cmd[0]).name != "zig" or cmd[1] not in ("cc", "c++")
            or any(flag in cmd[2:] for flag in ("-c", "--compile", "-S", "-E", "-fsyntax-only"))):
        return None
    objects = 0
    output = False
    for arg in cmd[2:]:
        if output:
            output = False
        elif arg == "-o":
            output = True
        elif not arg.startswith("-") and arg.endswith(".o"):
            objects += 1
    if not objects:
        return None
    headroom = 256
    needed = objects + headroom
    limit = {"object_inputs": objects, "headroom": headroom, "needed": needed,
             "soft_before": None, "soft_effective": None, "hard": None,
             "hard_effective": None, "infinity": resource.RLIM_INFINITY,
             "raised": False, "error": None}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        limit.update(soft_before=soft, soft_effective=soft, hard=hard, hard_effective=hard)
        if soft != resource.RLIM_INFINITY and soft < needed:
            if hard != resource.RLIM_INFINITY and hard < needed:
                limit["error"] = f"need {needed} descriptors but the unchanged hard limit is {hard}"
                return limit
            resource.setrlimit(resource.RLIMIT_NOFILE, (needed, hard))
            limit.update(soft_effective=None, hard_effective=None, raised=True)
            effective, effective_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            limit.update(soft_effective=effective, hard_effective=effective_hard,
                         raised=effective != soft)
            if effective_hard != hard or (effective != resource.RLIM_INFINITY and effective < needed):
                limit["error"] = "NOFILE readback did not preserve the hard limit and satisfy the link"
    except (OSError, ValueError, OverflowError) as error:
        limit["error"] = f"{type(error).__name__}: {error}"
    return limit


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 7200) -> dict:
    """Run one command, reporting wall time and the peak RSS of the child."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.time()
    limit = _link_nofile(cmd)
    if limit is not None and limit["error"] is not None:
        rc, out, err = -1, "", f"link descriptor limit refused: {limit['error']}"
    else:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               cwd=str(cwd) if cwd else None)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = -1, "", f"timeout after {timeout} s"
    wall = time.time() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    result = {"rc": rc, "seconds": round(wall, 2),
              # ru_maxrss is the high-water mark over all children reaped so far;
              # it only rises, so a rise during this call is this child's peak.
              "peak_child_rss_mb": round(max(after, before) / 1024.0, 1),
              "peak_rose_mb": round(max(0, after - before) / 1024.0, 1),
              "stdout": out[-4000:], "stderr_tail": err[-8000:]}
    if limit is not None:
        result["link_nofile"] = limit
    return result


def phase_table(state: dict, tag: str) -> None:
    out_c = OUT / tag / "function_table_whole.c"
    rows, census = gen_table.rows(tag)
    gen_table.emit(rows, out_c, tag)
    census["out"] = str(out_c.relative_to(C.ROOT))
    census["out_bytes"] = out_c.stat().st_size
    state["table_generation"] = census
    print(f"table: {census['rows']} rows, {len(census['duplicate_symbols'])} duplicate "
          f"symbols, {census['out_bytes'] / 1e6:.1f} MB of C", flush=True)


def phase_compile(state: dict, tag: str) -> None:
    bdir = OUT / tag / "obj"
    bdir.mkdir(parents=True, exist_ok=True)
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
    for src, path in (("function_table_whole.c", OUT / tag / "function_table_whole.c"),
                      ("link_probe.c", Path(__file__).resolve().parent / "link_probe.c")):
        o = bdir / (Path(src).stem + ".o")
        log[src] = run(cflags + [str(path), "-o", str(o)], timeout=7200)
        log[src]["object_bytes"] = o.stat().st_size if o.exists() else 0
    state["compile"] = {k: {kk: vv for kk, vv in v.items() if kk != "stdout"}
                        for k, v in log.items()}
    state["compile_ok"] = all(v["rc"] == 0 for v in log.values())
    print("compile: " + ", ".join(f"{k} rc={v['rc']} {v['seconds']}s"
                                  for k, v in log.items()), flush=True)


def phase_link(state: dict, tag: str) -> None:
    """Link every lifted object with the runtime.

    The objects are named relatively from inside their own directory: 12 683
    absolute paths are about 900 kB of argv, which is within this host's 2 MB
    `ARG_MAX` but not within every host's, and the relative form is 170 kB.
    """
    bdir = (OUT / tag / "obj").resolve()
    objdir = (C.BUILD / tag).resolve()
    binary = (OUT / tag / "sa2_whole").resolve()
    objs = sorted(p.name for p in objdir.glob("ir_*.o"))
    base = [str(bdir / (Path(s).stem + ".o"))
            for s in RUNTIME_C + RUNTIME_CXX + ["function_table_whole.c", "link_probe.c"]]
    cmd = ([str(S.ZIG), "c++"] + TARGET + [OPT] + base + objs + ["-o", str(binary)])
    argv_bytes = sum(len(a) + 1 for a in cmd)
    print(f"link: {len(objs)} lifted objects + {len(base)} runtime objects, "
          f"argv {argv_bytes / 1e3:.0f} kB, cwd {objdir}", flush=True)
    r = run(cmd, cwd=objdir)
    r["objects_lifted"] = len(objs)
    r["objects_runtime"] = len(base)
    r["argv_bytes"] = argv_bytes
    r["object_bytes_in"] = sum((objdir / o).stat().st_size for o in objs) + \
        sum(Path(b).stat().st_size for b in base if Path(b).exists())
    r["command"] = (f"{S.ZIG} c++ {' '.join(TARGET)} {OPT} <{len(base)} runtime objects> "
                    f"ir_*.o ({len(objs)} files, cwd {objdir.relative_to(C.ROOT)}) -o "
                    f"{binary.relative_to(C.ROOT)}")
    if binary.exists() and r["rc"] == 0:
        r["binary"] = str(binary.relative_to(C.ROOT))
        r["binary_bytes"] = binary.stat().st_size
        r["sections"] = sections(binary)
        r["sha256"] = C.sha256(binary)
    state["link"] = r
    print(f"link: rc={r['rc']} {r['seconds']}s peak_rss={r['peak_child_rss_mb']} MB "
          f"binary={r.get('binary_bytes', 0) / 1e6:.1f} MB", flush=True)
    if r["rc"] != 0:
        print(r["stderr_tail"][-3000:], flush=True)


def seeded_arms(lift_tag: str) -> set[int]:
    """The arm addresses `tools/s3/arms/run_image.py` lifted and compiled.

    This is the *second* independent source the per-arm prediction needs, and
    it is deliberately not the function table: it is the arm-lift log, one
    record per seeded trace, written before any table existed. An arm that has
    a compiled trace gets a `sub_<va>` define, so `gen_table.py` gives it a row
    and the linked table must resolve it. An arm that has no trace and is not a
    map entry has no symbol anywhere and must miss.
    """
    path = C.GEN / "s3" / "arms" / f"arms.{lift_tag}.jsonl"
    if not path.is_file():
        return set()
    return {int(r["va"], 16) for r in C.read_jsonl(path).values() if r.get("compiled")}


def arm_rows(tag: str) -> tuple[list[dict], dict]:
    """Every jump-table arm address in the map, in the order the map lists them.

    Same source and same rule as `tools/s3/whole_image/indirect.py` section 2:
    for each map entry that has jump tables *and* was lifted on this arm, every
    target of every table, one row per occurrence, so the total is the 5 817 the
    indirect census reports and not the distinct count. `class` is the map's own
    classification of the target, and `map_entry` is the only class the
    generated function table can hold a row for -- which is a prediction the
    link probe then puts to the linked program.
    """
    fmap = C.by_address()
    starts = sorted(fmap)
    ends = [va + fmap[va]["size"] for va in starts]
    entries = set(starts)
    lift = C.read_jsonl(C.LIFT / f"{tag}.jsonl")

    def classify(target: int) -> str:
        if target in entries:
            return "map_entry"
        i = bisect.bisect_right(starts, target) - 1
        if i >= 0 and starts[i] <= target < ends[i]:
            return "inside_entry"
        return "gap"

    rows: list[dict] = []
    for va in starts:
        fn = fmap[va]
        jts = fn.get("jump_tables") or []
        if not jts:
            continue
        rec = lift.get(f"0x{va:08x}")
        if not (rec and rec.get("lifted")):
            continue
        for ji, jt in enumerate(jts):
            for t in jt.get("targets", []):
                ta = int(t, 16)
                rows.append({"arm": f"0x{ta:08x}", "owner": f"0x{va:08x}",
                             "owner_name": fn["name"], "region": fn["region"],
                             "table": ji, "class": classify(ta)})
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    meta = {
        "tag": tag,
        "arm_occurrences": len(rows),
        "distinct_arms": len({r["arm"] for r in rows}),
        "by_class": by_class,
        "predicted_hits": by_class.get("map_entry", 0),
        "predicted_hit_addresses": sorted({r["arm"] for r in rows
                                           if r["class"] == "map_entry"}),
        "rule": "the generated table holds one row per map entry, so the map says an "
                "arm hits exactly when it is itself a map entry. The link probe asks "
                "the linked table instead of trusting this.",
    }
    return rows, meta


def phase_arms(state: dict, tag: str, expect: str, lift_tag: str) -> None:
    """Write the arm control's input: one row per arm occurrence, each carrying
    what *this* build is predicted to answer for it.

    Until the arms were seeded there was one prediction for every build -- "an
    arm hits exactly when it is itself a map entry", which is 4 -- and the probe
    compared one total against another. That is no longer true of every build,
    so the premise is now stated on the command line and checked per address:

      --arms-expect map-only  the binary links the 12 683 whole-image objects
                              only, so only the 4 map-entry arms have a symbol
      --arms-expect seeded    the binary also links the arm traces, so every
                              compiled arm trace has a row as well

    There is no default. A default here is exactly the failure the operating
    notes describe: a decision nobody has to make, so nobody notices when it is
    the wrong one, and the probe would then compare a real answer against a
    prediction for a different binary and call it agreement.
    """
    rows, meta = arm_rows(lift_tag)
    seeded = seeded_arms(lift_tag) if expect == "seeded" else set()
    map_entry = {int(a, 16) for a in meta["predicted_hit_addresses"]}
    predict = map_entry | seeded
    for r in rows:
        r["predicted"] = "hit" if int(r["arm"], 16) in predict else "miss"
    hits = sum(1 for r in rows if r["predicted"] == "hit")
    meta.update({
        "expect": expect,
        "lift_tag": lift_tag,
        "seeded_arm_traces_compiled": len(seeded),
        "seeded_arm_traces_that_are_arms_of_this_map": len(seeded & {
            int(r["arm"], 16) for r in rows}),
        "predicted_hits": hits,
        "predicted_hits_because_map_entry": len(map_entry),
        "predicted_hits_because_seeded": len(predict) - len(map_entry),
        "distinct_arms_predicted_to_hit": len(predict & {int(r["arm"], 16) for r in rows}),
        "distinct_arms_predicted_to_miss": meta["distinct_arms"] - len(
            predict & {int(r["arm"], 16) for r in rows}),
        "rule": "an arm resolves exactly when some object defines sub_<arm>: it is "
                "either a map entry (the whole-image build) or a compiled arm trace "
                "(the seeded build). Both sources are read here; the linked table is "
                "asked separately, and the probe fails on any address they disagree on.",
    })
    (OUT / f"arms.{tag}.json").write_text(
        json.dumps({"meta": meta, "arms": rows}, indent=1, sort_keys=True) + "\n")
    # The probe reads the flat form, format v2: a header of
    # "<occurrences> <predicted hits> v2" and then "<address> <0|1>" per line,
    # in map order, duplicate occurrences included.
    (OUT / f"arms.{tag}.txt").write_text(
        f"{len(rows)} {hits} v2\n"
        + "".join(f"{r['arm']} {1 if r['predicted'] == 'hit' else 0}\n" for r in rows))
    state["arms"] = meta
    print(f"arms: {meta['arm_occurrences']} occurrences, {meta['distinct_arms']} "
          f"distinct, expect={expect}, {hits} predicted hits "
          f"({meta['predicted_hits_because_map_entry']} map entries + "
          f"{meta['predicted_hits_because_seeded']} seeded traces) {meta['by_class']}",
          flush=True)


def phase_run(state: dict, tag: str, expect: str, lift_tag: str) -> None:
    binary = (OUT / tag / "sa2_whole").resolve()
    if not binary.exists():
        state["probe"] = {"ok": False, "why": "no binary"}
        return
    arms = OUT / f"arms.{tag}.txt"
    if not arms.is_file():        # `--phase run` alone still gets its control
        phase_arms(state, tag, expect, lift_tag)
    r = run([str(binary), str(C.EXE), str(arms)], timeout=1800)
    try:
        r["result"] = json.loads(r["stdout"])
    except json.JSONDecodeError:
        r["result"] = None
    state["probe"] = r
    print("probe: " + json.dumps(r.get("result") or {"rc": r["rc"],
                                                     "stderr": r["stderr_tail"][-400:]}),
          flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--phase", default="all",
                    choices=["all", "table", "compile", "link", "arms", "run"])
    ap.add_argument("--arms-expect", required=True, choices=["map-only", "seeded"],
                    help="what this build is predicted to resolve an arm with: "
                         "map-only = whole-image objects alone, seeded = plus the "
                         "arm traces. No default on purpose; see phase_arms.")
    ap.add_argument("--arms-lift-tag", default=None,
                    help="the tag whose lift log and arm-lift log the prediction is "
                         "read from (default: --tag). A merged build tag such as "
                         "`next+arms` has no lift log of its own.")
    args = ap.parse_args()
    lift_tag = args.arms_lift_tag or args.tag
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"link.{args.tag}.json"
    state = json.loads(path.read_text()) if path.is_file() else {}
    state["tag"] = args.tag
    state["machine"] = {"nproc": os.cpu_count(),
                        "toolchain": f"zig cc/c++ {' '.join(TARGET)} {OPT}"}
    phases = (["table", "compile", "link", "arms", "run"] if args.phase == "all"
              else [args.phase])
    runners = {
        "table": lambda: phase_table(state, args.tag),
        "compile": lambda: phase_compile(state, args.tag),
        "link": lambda: phase_link(state, args.tag),
        "arms": lambda: phase_arms(state, args.tag, args.arms_expect, lift_tag),
        "run": lambda: phase_run(state, args.tag, args.arms_expect, lift_tag),
    }
    for ph in phases:
        state["phase_in_flight"] = ph
        checkpoint(state, args.tag)
        runners[ph]()
        state["phase_in_flight"] = None
        checkpoint(state, args.tag)
    print(f"-> {path}")


if __name__ == "__main__":
    main()
