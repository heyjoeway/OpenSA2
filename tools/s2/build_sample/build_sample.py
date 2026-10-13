#!/usr/bin/env python3
"""S2 build-time sample: lift, pass and compile 500 map functions; project the full image; kill criterion 1.

Decision 2026-09-19-G1 section 1.4, criterion 1: "S2 lifts and compiles a
500-function sample drawn from the map, stratified by size; if the projected
full-image lift+compile exceeds 60 wall minutes on this machine, pivot." The
projection scales by instructions (section 1.3), not by function count.

Phases (each resumable: its JSON under OUT is reused unless --force):

  sample       500 functions from private/generated/function-map.json, seeded
               (SEED), stratified by instruction-count decile (rank deciles, 50
               per decile) and, inside each decile, proportional to region
               (largest remainder). Written to OUT/sample.json.
  lift         remill-lift-14 per function from the unpacked image at its VA
               with the map's `size` bytes, the spike's invocation
               (`unshare -Urm` with the semantics bind mount,
               tools/spike/lift_remill.py), NPROC lifts in parallel, each child
               measured with wait4 (wall, cpu, peak RSS). A second lift of every
               function into OUT/lift2 gives the remill determinism result, and the
               whole set is lifted again at each other job count in JOB_SCAN (1 and 8)
               so the projection carries a measured serial wall rather than a sum of
               per-function walls taken under 16-way contention.
  pass         tools/spike/pc24pass/pc24pass.instrument over every lifted .ll
               with a helper IR built here by the S1 command (clang 20
               -O1 -S -emit-llvm of pc_round.c), NPROC processes, and again at
               each other job count in JOB_SCAN.
  tu25         the 25-functions-per-TU grouping: raw .ll files linked with
               remill-llvm-link-14 -opaque-pointers -S (LLVM 14 parses LLVM 14
               IR; it cannot parse the clang-20 helper the pass splices in, so
               the link comes first) and the pass run once over the linked
               module.
  compile      each TU with `zig cc -target x86_64-linux-musl -O2 -w -c` (the
               S1 harness target), N parallel jobs, a fresh zig cache per
               compile (zig cc caches -c objects, so a warm cache measures
               nothing), wait4 per compile. Runs: {1/TU, 25/TU} x {N=16, N=8}
               at -O2, plus -O1 at N=16 for both groupings. What the -O flag
               reaches clang as, and whether the spliced helper is inlined,
               are measured (opt_flag_check, helper_inlining), not assumed. Runs at
               N=1 as well, for the serial column.
  link         the stage no receipt here had timed: the callee stubs are deduplicated
               across all 500 modules (500 modules otherwise define the same
               `sub_<callee>.<n>` and ld.lld stops at the first duplicate), the pass and
               the compiler are run over the deduplicated set, and every object is linked
               into one relocatable object; a program link is run too and recorded with
               the __remill_* undefined-symbol error it fails on.
  memory       peak RSS per compile, measured one compile at a time from a fresh
               probe process: wait4's ru_maxrss carries the high-water mark Linux
               copies into a child from its parent, so the compile runs' own RSS
               column is an upper bound that moves with this driver's footprint.
  incremental  after the full build: three functions re-lifted and re-passed
               alone, their TU recompiled in place for both groupings, every
               object re-hashed: the rebuilt object must equal its previous
               hash (emitter and compiler determinism) and every other object
               must be untouched. Wall per grouping recorded.
  receipt      Documentation/receipts/s2-build-time-sample-v1.json, schema
               sa2-recomp-receipt/v1: counts, hashes, timings, the projection
               and the kill-criterion verdict. No IR text.

Everything lifted or compiled lives under private/generated/s2/build_sample/
(ignored). Nothing under tools/spike is modified; its modules are imported.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/build_sample/build_sample.py [--phase PHASE] [--force] [--jobs N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import platform
import random
import re
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPIKE = ROOT / "tools" / "spike"
sys.path.insert(0, str(SPIKE))
sys.path.insert(0, str(SPIKE / "pc24pass"))
import pc24pass  # noqa: E402  (tools/spike/pc24pass/pc24pass.py, read-only)
import spikelib as S  # noqa: E402  (tools/spike/spikelib.py, read-only)

MAP = ROOT / "private" / "generated" / "function-map.json"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
REMILL = ROOT / "private" / "lifters" / "remill" / "usr" / "local"
NUMBERS_MD = ROOT / "Documentation" / "decisions" / "2026-09-19-G1-numbers.md"
S1_HELPER_LL = ROOT / "private" / "generated" / "spike" / "pc24pass" / "pc_round.ll"
OUT = ROOT / "private" / "generated" / "s2" / "build_sample"
RECEIPT = ROOT / "Documentation" / "receipts" / "s2-build-time-sample-v1.json"
PINS = json.loads((ROOT / "tools" / "pins.json").read_text())
ZIG = str(S.ZIG)
TARGET = ["-target", "x86_64-linux-musl"]
SEED = 20260920
SAMPLE_N = 500
DECILES = 10
TU25 = 25
KILL_MINUTES = 60
NPROC = os.cpu_count() or 1
REGIONS = ("game", "crt", "cri", "other")
RUNS = [  # (grouping, jobs, opt); the first of each grouping at -O2/N=16 is the incremental check's reference build
    ("g1", 16, "O2"), ("g25", 16, "O2"),
    ("g1", 8, "O2"), ("g25", 8, "O2"),
    ("g1", 1, "O2"), ("g25", 1, "O2"),
    ("g1", 16, "O1"), ("g25", 16, "O1"),
]
REFERENCE_RUNS = {"g1": "g1_N16_O2", "g25": "g25_N16_O2"}
JOB_SCAN = (1, 8, 16)   # every phase's wall is measured at each of these job counts; the projection has a column per count


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n")


def load(path: Path):
    return json.loads(path.read_text())


def cmdline(cmd: list[str]) -> str:
    return " ".join(c if " " not in c else repr(c) for c in cmd)


def run_measured(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None,
                 log: Path | None = None, timeout: float = 900) -> dict:
    """Run one child; wait4 gives its own rusage: wall, cpu (user+sys, reaped descendants included), peak RSS."""
    logf = open(log, "wb") if log else subprocess.DEVNULL
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        _, status, ru = os.wait4(p.pid, 0)
    finally:
        timer.cancel()
    wall = time.perf_counter() - t0
    p.returncode = os.waitstatus_to_exitcode(status)   # already reaped; keeps Popen from waiting again
    if log:
        logf.close()
    head = ""
    if p.returncode != 0 and log:
        head = S.error_head(log.read_text(errors="replace"))
    return {"rc": p.returncode, "wall_s": round(wall, 4), "cpu_s": round(ru.ru_utime + ru.ru_stime, 4),
            "maxrss_kb": ru.ru_maxrss, "error_head": head}


def children_cpu() -> float:
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def pstats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "sum": round(sum(s), 4), "min": s[0], "median": statistics.median(s),
            "p90": s[min(len(s) - 1, int(len(s) * 0.9))], "max": s[-1], "mean": round(sum(s) / len(s), 4)}


# ---------------------------------------------------------------- sample
def mapped_instructions_from_numbers() -> int | None:
    """`mapped-instructions` from the G1 numbers table (generated by decision_numbers.py from the receipts)."""
    if not NUMBERS_MD.exists():
        return None
    m = re.search(r"^\|\s*`mapped-instructions`\s*\|\s*(\d+)\s*\|", NUMBERS_MD.read_text(), re.M)
    return int(m.group(1)) if m else None


def phase_sample(force: bool) -> dict:
    path = OUT / "sample.json"
    if path.exists() and not force:
        return load(path)
    fmap = load(MAP)
    fns = fmap["functions"]
    ordered = sorted(fns, key=lambda f: (f["instructions"], int(f["address"], 16)))
    n = len(ordered)
    for rank, f in enumerate(ordered):
        f["decile"] = rank * DECILES // n
    rng = random.Random(SEED)
    per_decile = SAMPLE_N // DECILES
    strata = []
    chosen = []
    for d in range(DECILES):
        members = [f for f in ordered if f["decile"] == d]
        by_region = {r: [f for f in members if f["region"] == r] for r in REGIONS}
        exact = {r: per_decile * len(by_region[r]) / len(members) for r in REGIONS}
        quota = {r: int(exact[r]) for r in REGIONS}
        for r in sorted(REGIONS, key=lambda r: exact[r] - quota[r], reverse=True)[: per_decile - sum(quota.values())]:
            quota[r] += 1
        picked = []
        for r in REGIONS:
            picked.extend(rng.sample(sorted(by_region[r], key=lambda f: int(f["address"], 16)), quota[r]))
        strata.append({"decile": d, "population": len(members),
                       "instructions_min": min(f["instructions"] for f in members),
                       "instructions_max": max(f["instructions"] for f in members),
                       "population_by_region": {r: len(by_region[r]) for r in REGIONS},
                       "quota_by_region": quota, "picked": len(picked),
                       "picked_instructions": sum(f["instructions"] for f in picked)})
        chosen.extend(picked)
    chosen.sort(key=lambda f: int(f["address"], 16))
    rows = [{"address": f["address"], "size": f["size"], "instructions": f["instructions"], "region": f["region"],
             "decile": f["decile"], "name": f["name"], "thunk": f["thunk"], "seeded": f["seeded"],
             "direct_calls": len(f["direct_calls"]), "jump_tables": len(f["jump_tables"])} for f in chosen]
    map_sum = sum(f["instructions"] for f in fns)
    numbers = mapped_instructions_from_numbers()
    sample = {
        "written": now(), "seed": SEED, "n": len(rows), "deciles": DECILES, "per_decile": per_decile,
        "method": "functions ranked by (instructions, address) into 10 equal-count deciles; 50 per decile; inside a decile the "
                  "50 are split across regions by largest remainder on the decile's region counts; random.Random(seed).sample "
                  "per region over the address-sorted members; the 500 listed in address order",
        "map": {"path": rel(MAP), "sha256": sha(MAP), "functions": len(fns), "instructions_sum": map_sum,
                "by_region": {r: sum(1 for f in fns if f["region"] == r) for r in REGIONS},
                "instructions_by_region": {r: sum(f["instructions"] for f in fns if f["region"] == r) for r in REGIONS}},
        "mapped_instructions": {"value": numbers if numbers is not None else map_sum,
                                "source": f"{rel(NUMBERS_MD)} row `mapped-instructions` (decision 1.3 denominator)" if numbers is not None
                                else f"sum of `instructions` over {rel(MAP)} (numbers table absent)",
                                "map_instructions_sum": map_sum},
        "strata": strata,
        "by_region": {r: sum(1 for f in rows if f["region"] == r) for r in REGIONS},
        "instructions_sum": sum(f["instructions"] for f in rows),
        "bytes_sum": sum(f["size"] for f in rows),
        "functions": rows,
    }
    sample["scale_factor"] = round(sample["mapped_instructions"]["value"] / sample["instructions_sum"], 6)
    dump(path, sample)
    return sample


# ---------------------------------------------------------------- lift
def load_image() -> tuple[bytes, int]:
    import pefile
    pe = pefile.PE(str(EXE), fast_load=True)
    return pe.get_memory_mapped_image(), pe.OPTIONAL_HEADER.ImageBase


def lift_one(args: tuple) -> dict:
    hexbytes, address, out_dir = args
    va = int(address, 16)
    out_dir = Path(out_dir)
    ll = out_dir / f"sub_{va:08X}.ll"
    log = out_dir / f"sub_{va:08X}.log"
    inner = (f"mount --bind {REMILL} /usr/local && /usr/local/bin/remill-lift-14 --arch x86 --os windows "
             f"--address {va:#x} --bytes {hexbytes} --ir_out {ll}")
    r = run_measured(["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner], log=log, timeout=600)
    text = ll.read_text(errors="replace") if ll.exists() else ""
    lifted = r["rc"] == 0 and re.search(rf"^define [^\n]*@sub_{va:x}\(", text, re.M) is not None
    row = {"address": address, "rc": r["rc"], "lifted": lifted,
           "lift_wall_s": r["wall_s"], "lift_cpu_s": r["cpu_s"], "lift_maxrss_kb": r["maxrss_kb"],
           "error_head": "" if lifted else (r["error_head"] or S.error_head(log.read_text(errors="replace"))),
           "ll_lines": text.count("\n"), "ll_bytes": len(text.encode()) if text else 0,
           "ll_sha256": sha(ll) if ll.exists() else None,
           "defines": len(re.findall(r"^define ", text, re.M)),
           "missing_block_calls": len(re.findall(r"^\s+(?:%[\w.]+ = )?(?:tail |musttail )?call [^\n]*@__remill_missing_block\(", text, re.M)),
           "fp80_ops": len(re.findall(r"= f(?:add|sub|mul|div) (?:[a-z]+ )*x86_fp80 ", text))}
    if lifted:
        log.unlink(missing_ok=True)
    return row


def lift_all(sample: dict, out_dir: Path, jobs: int, lift_cmd_template: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    img, base = load_image()
    work = []
    for f in sample["functions"]:
        va = int(f["address"], 16)
        work.append((img[va - base: va - base + f["size"]].hex(), f["address"], str(out_dir)))
    cpu0 = children_cpu()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(jobs) as ex:
        rows = list(ex.map(lift_one, work))
    wall = time.perf_counter() - t0
    return {"jobs": jobs, "phase_wall_s": round(wall, 3), "children_cpu_s": round(children_cpu() - cpu0, 3),
            "command_template": lift_cmd_template, "rows": rows}


def lift_job_scan(sample: dict, reference_rows: list[dict], jobs_list: list[int]) -> list[dict]:
    """The same 500 lifts again at other job counts, so the projection has a measured serial column.

    A per-function wall summed over 500 functions measured 16-way is not a serial wall: those 500 lifts
    contended for 8 physical cores. Each scan lifts into its own directory, checks every .ll against the
    reference lift's hash (free determinism evidence at another job count) and deletes the directory."""
    by_addr = {r["address"]: r["ll_sha256"] for r in reference_rows}
    out = []
    for j in jobs_list:
        d = OUT / f"lift_n{j}"
        print(f"  lift job scan N={j} ...", flush=True)
        run = lift_all(sample, d, j, "")
        identical = sum(1 for r in run["rows"]
                        if r["ll_sha256"] is not None and r["ll_sha256"] == by_addr.get(r["address"]))
        out.append({"jobs": j, "phase_wall_s": run["phase_wall_s"], "children_cpu_s": run["children_cpu_s"],
                    "lifted": sum(1 for r in run["rows"] if r["lifted"]),
                    "per_function_wall_s": pstats([r["lift_wall_s"] for r in run["rows"]]),
                    "identical_to_reference_lift": identical})
        shutil.rmtree(d, ignore_errors=True)
    return out


def diff_summary(a: Path, b: Path) -> dict:
    la, lb = a.read_text(errors="replace").splitlines(), b.read_text(errors="replace").splitlines()
    first = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
    ops = [t for t in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes() if t[0] != "equal"]
    return {"lines_a": len(la), "lines_b": len(lb), "first_differing_line": first + 1,
            "changed_hunks": len(ops), "lines_removed": sum(t[2] - t[1] for t in ops), "lines_added": sum(t[4] - t[3] for t in ops)}


def phase_lift(sample: dict, force: bool, jobs: int) -> dict:
    path = OUT / "lift.json"
    if path.exists() and not force:
        return load(path)
    template = ("unshare -Urm --propagation unchanged sh -c 'mount --bind private/lifters/remill/usr/local /usr/local && "
                "/usr/local/bin/remill-lift-14 --arch x86 --os windows --address <VA> --bytes <size bytes hex> --ir_out sub_<VA>.ll'")
    first = lift_all(sample, OUT / "lift", jobs, template)
    second = lift_all(sample, OUT / "lift2", jobs, template)
    by_addr = {r["address"]: r for r in second["rows"]}
    mismatches = []
    for r in first["rows"]:
        r2 = by_addr[r["address"]]
        r["relift_identical"] = r["ll_sha256"] is not None and r["ll_sha256"] == r2["ll_sha256"]
        if not r["relift_identical"] and r["lifted"] and r2["lifted"]:
            va = int(r["address"], 16)
            mismatches.append({"address": r["address"], **diff_summary(OUT / "lift" / f"sub_{va:08X}.ll", OUT / "lift2" / f"sub_{va:08X}.ll")})
    scan = lift_job_scan(sample, first["rows"], [j for j in JOB_SCAN if j != jobs])
    version = subprocess.run([str(REMILL / "bin" / "remill-lift-14"), "--version"], capture_output=True, text=True)
    result = {"written": now(), "jobs": jobs, "command_template": template, "job_scan": scan,
              "remill_version": (version.stdout + version.stderr).strip()[:200],
              "phase_wall_s": first["phase_wall_s"], "children_cpu_s": first["children_cpu_s"],
              "second_lift": {"phase_wall_s": second["phase_wall_s"], "children_cpu_s": second["children_cpu_s"],
                              "identical": sum(1 for r in first["rows"] if r["relift_identical"]),
                              "differing": len(mismatches), "differing_summaries": mismatches[:20]},
              "rows": first["rows"]}
    dump(path, result)
    return result


# ---------------------------------------------------------------- pass
def build_helper() -> dict:
    hdir = OUT / "helper"
    hdir.mkdir(parents=True, exist_ok=True)
    helper_ll = hdir / "pc_round.ll"
    cmd = [ZIG, "cc"] + TARGET + ["-O1", "-g0", "-S", "-emit-llvm", "-fno-discard-value-names",
                                  str(SPIKE / "pc24pass" / "pc_round.c"), "-o", str(helper_ll)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=hdir)
    if p.returncode:
        raise SystemExit(f"helper IR compile failed: {p.stderr}")
    return {"helper_ll": str(helper_ll), "helper_ll_sha256": sha(helper_ll), "command": cmdline(cmd).replace(str(ROOT) + "/", ""),
            "source_sha256": sha(SPIKE / "pc24pass" / "pc_round.c"),
            "equals_s1_helper_ll": sha(S1_HELPER_LL) == sha(helper_ll) if S1_HELPER_LL.exists() else None}


def pass_one(args: tuple) -> dict:
    src, dst, helper_ll = (Path(a) for a in args)
    helper = helper_ll.read_text()
    text = src.read_text(errors="replace")
    t0 = time.perf_counter()
    out, stats = pc24pass.instrument(text, helper)
    secs = time.perf_counter() - t0
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out)
    return {"pass_wall_s": round(secs, 4), "ops_instrumented": stats["ops_instrumented"],
            "by_op": stats.get("by_op", {}), "by_semantic": stats.get("by_semantic", {}),
            "skipped_by_semantic": stats.get("skipped_by_semantic", {}),
            "skipped_non_lifted_defines": stats.get("skipped_non_lifted", 0),
            "pass_ll_lines": out.count("\n"), "pass_ll_sha256": hashlib.sha256(out.encode()).hexdigest()}


def pass_all(sources: list[Path], dst_dir: Path, helper_ll: str, jobs: int) -> tuple[float, list[dict]]:
    work = [(str(s), str(dst_dir / s.name), helper_ll) for s in sources]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(jobs) as ex:
        results = list(ex.map(pass_one, work, chunksize=4))
    wall = time.perf_counter() - t0
    rows = [{"address": "0x" + Path(src).stem[4:], **res} for (src, _, _), res in zip(work, results)]
    return round(wall, 3), rows


def pass_job_scan(sources: list[Path], reference_rows: list[dict], helper_ll: str, jobs_list: list[int]) -> list[dict]:
    """The pass over the same 500 files at other job counts; the output is hashed against the reference run
    and then deleted. Same reason as lift_job_scan: a summed per-file wall is not a serial wall."""
    by_addr = {r["address"]: r["pass_ll_sha256"] for r in reference_rows}
    out = []
    for j in jobs_list:
        d = OUT / f"pass_n{j}"
        d.mkdir(parents=True, exist_ok=True)
        wall, rows = pass_all(sources, d, helper_ll, j)
        out.append({"jobs": j, "phase_wall_s": wall, "files": len(rows),
                    "per_function_wall_s": pstats([r["pass_wall_s"] for r in rows]),
                    "identical_to_reference_pass": sum(1 for r in rows if r["pass_ll_sha256"] == by_addr.get(r["address"]))})
        shutil.rmtree(d, ignore_errors=True)
    return out


def phase_pass(lift: dict, force: bool, jobs: int) -> dict:
    path = OUT / "pass.json"
    if path.exists() and not force:
        return load(path)
    helper = build_helper()
    sources = [OUT / "lift" / f"sub_{int(r['address'], 16):08X}.ll" for r in lift["rows"] if r["lifted"]]
    (OUT / "pass").mkdir(parents=True, exist_ok=True)
    wall, rows = pass_all(sources, OUT / "pass", helper["helper_ll"], jobs)
    scan = pass_job_scan(sources, rows, helper["helper_ll"], [j for j in JOB_SCAN if j != jobs])
    result = {"written": now(), "jobs": jobs, "phase_wall_s": wall, "helper": helper, "job_scan": scan,
              "pass_sha256": sha(SPIKE / "pc24pass" / "pc24pass.py"),
              "command": f".venv/bin/python -c 'pc24pass.instrument(text, helper_ll)' per file (tools/spike/pc24pass/pc24pass.py), {jobs} processes",
              "rows": rows}
    dump(path, result)
    return result


# ---------------------------------------------------------------- tu25
RE_DEFINE_BLOCK = re.compile(r"^define ([^\n@]*)@([\w.]+)\(([^\n]*?)\)([^\n{]*)\{\n.*?^\}\n", re.S | re.M)


def dedup_stubs(members: list[str], src_dir: Path, dedup_dir: Path) -> tuple[list[str], int]:
    """remill's --bytes lift defines an external stub `sub_<callee>.<n>` (body: __remill_missing_block) for every
    call site; two modules that call the same callee both define the same name with the same body, and LLVM 14's
    llvm-link refuses the duplicate. Keep the first module's define, turn the later ones into declares.
    Returns the rewritten file list and the number of stub defines dropped."""
    if dedup_dir.exists():
        shutil.rmtree(dedup_dir)
    dedup_dir.mkdir(parents=True)
    seen: set[str] = set()
    dropped = 0
    out = []
    for a in members:
        name = f"sub_{int(a, 16):08X}.ll"
        text = (src_dir / name).read_text(errors="replace")

        def repl(m: re.Match) -> str:
            nonlocal dropped
            if m.group(2) in seen:
                dropped += 1
                params = re.sub(r" %[\w.\"]+", "", m.group(3))
                return f"declare {m.group(1).strip()} @{m.group(2)}({params})\n"
            seen.add(m.group(2))
            return m.group(0)

        (dedup_dir / name).write_text(RE_DEFINE_BLOCK.sub(repl, text))
        out.append(str(dedup_dir / name))
    return out, dropped


def link_tu(members: list[str], src_dir: Path, tu_ll: Path, log: Path) -> dict:
    t0 = time.perf_counter()
    files, dropped = dedup_stubs(members, src_dir, tu_ll.parent / f"{tu_ll.stem}.dedup")
    t_dedup = time.perf_counter() - t0
    cmd = [str(REMILL / "bin" / "remill-llvm-link-14"), "-opaque-pointers", "-S"] + files + ["-o", str(tu_ll)]
    r = run_measured(cmd, log=log)
    text = tu_ll.read_text(errors="replace") if r["rc"] == 0 and tu_ll.exists() else ""
    if r["rc"] == 0:
        log.unlink(missing_ok=True)
    defines = re.findall(r"^define [^\n]*@([\w.]+)\(", text, re.M)
    return {"link_rc": r["rc"], "dedup_wall_s": round(t_dedup, 4), "stub_defines_dropped": dropped,
            "link_wall_s": round(r["wall_s"] + t_dedup, 4), "llvm_link_wall_s": r["wall_s"], "link_cpu_s": r["cpu_s"], "link_maxrss_kb": r["maxrss_kb"],
            "link_error_head": r["error_head"], "linked_ll_lines": text.count("\n"),
            "defines": len(defines), "callee_stub_defines": sum(1 for d in defines if re.search(r"\.\d+$", d))}


def pass_tu(tu_ll: Path, tu_pass: Path, helper_ll: Path) -> dict:
    res = pass_one((str(tu_ll), str(tu_pass), str(helper_ll)))
    return {"pass_wall_s": res["pass_wall_s"], "ops_instrumented": res["ops_instrumented"],
            "pass_ll_lines": res["pass_ll_lines"], "pass_ll_sha256": res["pass_ll_sha256"]}


def phase_tu25(lift: dict, pass_: dict, force: bool, jobs: int) -> dict:
    path = OUT / "tu25.json"
    if path.exists() and not force:
        return load(path)
    lifted = [r["address"] for r in lift["rows"] if r["lifted"]]
    groups = [lifted[i:i + TU25] for i in range(0, len(lifted), TU25)]
    tdir = OUT / "tu25"
    tdir.mkdir(parents=True, exist_ok=True)
    helper_ll = Path(pass_["helper"]["helper_ll"])

    def one(i_members):
        i, members = i_members
        tu_ll, tu_pass = tdir / f"tu_{i:02d}.ll", tdir / f"tu_{i:02d}.pass.ll"
        row = {"tu": i, "members": members, **link_tu(members, OUT / "lift", tu_ll, tdir / f"tu_{i:02d}.link.log")}
        if row["link_rc"] == 0:
            row.update(pass_tu(tu_ll, tu_pass, helper_ll))
        return row

    t0 = time.perf_counter()
    with ThreadPoolExecutor(jobs) as ex:
        rows = list(ex.map(one, enumerate(groups)))
    wall = time.perf_counter() - t0
    result = {"written": now(), "jobs": jobs, "functions_per_tu": TU25, "tus": len(groups), "phase_wall_s": round(wall, 3),
              "grouping": "lifted functions in address order, consecutive chunks of 25 (arbitrary; not a call-graph grouping)",
              "link_command": "dedup_stubs (python: a callee stub `sub_<callee>.<n>` defined by an earlier member becomes a declare) then "
                              "private/lifters/remill/usr/local/bin/remill-llvm-link-14 -opaque-pointers -S <25 deduplicated sub_*.ll> -o tu_NN.ll",
              "pass_command": "pc24pass.instrument over tu_NN.ll -> tu_NN.pass.ll (one helper per TU)",
              "rows": rows}
    dump(path, result)
    return result


# ---------------------------------------------------------------- compile
def compile_cmd(opt: str, src: Path, obj: Path) -> list[str]:
    return [ZIG, "cc"] + TARGET + [f"-{opt}", "-w", "-c", str(src), "-o", str(obj)]


def compile_one(args: tuple) -> dict:
    src, obj, opt, log = Path(args[0]), Path(args[1]), args[2], Path(args[3])
    scratch = OUT / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cache = tempfile.mkdtemp(prefix="zigcache.", dir=scratch)
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=cache, ZIG_LOCAL_CACHE_DIR=cache)
    try:
        r = run_measured(compile_cmd(opt, src, obj), cwd=scratch, env=env, log=log)
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    ok = r["rc"] == 0 and obj.exists()
    if ok:
        log.unlink(missing_ok=True)
    return {"tu": src.stem.replace(".pass", ""), "ok": ok, "rc": r["rc"], "wall_s": r["wall_s"], "cpu_s": r["cpu_s"],
            "maxrss_kb": r["maxrss_kb"], "ll_bytes": src.stat().st_size, "obj_bytes": obj.stat().st_size if ok else 0,
            "obj_sha256": sha(obj) if ok else None, "error_head": r["error_head"]}


def tu_sources(grouping: str, lift: dict, tu25: dict) -> list[Path]:
    if grouping == "g1":
        return [OUT / "pass" / f"sub_{int(r['address'], 16):08X}.ll" for r in lift["rows"] if r["lifted"]]
    return [OUT / "tu25" / f"tu_{r['tu']:02d}.pass.ll" for r in tu25["rows"] if r["link_rc"] == 0]


def run_compile(name: str, grouping: str, jobs: int, opt: str, sources: list[Path]) -> dict:
    rdir = OUT / "compile" / name
    if rdir.exists():
        shutil.rmtree(rdir)
    (rdir / "obj").mkdir(parents=True)
    (rdir / "log").mkdir()
    sources = sorted(sources, key=lambda p: p.stat().st_size, reverse=True)   # largest first: better packing under N jobs
    work = [(str(s), str(rdir / "obj" / (s.stem.replace(".pass", "") + ".o")), opt, str(rdir / "log" / (s.stem + ".log"))) for s in sources]
    cpu0 = children_cpu()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(jobs) as ex:
        rows = list(ex.map(compile_one, work))
    wall = time.perf_counter() - t0
    ok = [r for r in rows if r["ok"]]
    rss = [r["maxrss_kb"] for r in ok]
    return {"name": name, "grouping": grouping, "jobs": jobs, "opt": opt,
            "command": cmdline(compile_cmd(opt, Path("<tu.ll>"), Path("<tu.o>"))).replace(str(ROOT) + "/", "") +
                       "  (ZIG_GLOBAL_CACHE_DIR=ZIG_LOCAL_CACHE_DIR=<fresh temp dir per compile>, cwd private/generated/s2/build_sample/scratch)",
            "tus": len(rows), "ok": len(ok), "failed": len(rows) - len(ok),
            "phase_wall_s": round(wall, 3), "children_cpu_s": round(children_cpu() - cpu0, 3),
            "cpu_sum_s": round(sum(r["cpu_s"] for r in rows), 3), "wall_sum_s": round(sum(r["wall_s"] for r in rows), 3),
            "maxrss_kb": {"max": max(rss) if rss else 0, "median": statistics.median(rss) if rss else 0,
                          "sum_of_top_N": sum(sorted(rss, reverse=True)[:jobs]) if rss else 0},
            "obj_bytes_total": sum(r["obj_bytes"] for r in ok), "ll_bytes_total": sum(r["ll_bytes"] for r in rows),
            "per_tu_wall_s": pstats([r["wall_s"] for r in ok]),
            "failures": [{"tu": r["tu"], "rc": r["rc"], "error_head": r["error_head"]} for r in rows if not r["ok"]],
            "rows": rows}


def phase_compile(lift: dict, tu25: dict, force: bool) -> dict:
    path = OUT / "compile.json"
    if path.exists() and not force:
        return load(path)
    runs = {}
    for grouping, jobs, opt in RUNS:
        name = f"{grouping}_N{jobs}_{opt}"
        print(f"compile {name} ...", flush=True)
        runs[name] = run_compile(name, grouping, jobs, opt, tu_sources(grouping, lift, tu25))
        print(f"  {runs[name]['ok']}/{runs[name]['tus']} ok, wall {runs[name]['phase_wall_s']} s, cpu {runs[name]['cpu_sum_s']} s, "
              f"max rss {runs[name]['maxrss_kb']['max']} kB", flush=True)
    # compiler determinism for free: the N=8 run compiled the same TUs with the same flags as the N=16 run
    det = {}
    for grouping in ("g1", "g25"):
        a = {r["tu"]: r["obj_sha256"] for r in runs[f"{grouping}_N16_O2"]["rows"] if r["ok"]}
        b = {r["tu"]: r["obj_sha256"] for r in runs[f"{grouping}_N8_O2"]["rows"] if r["ok"]}
        det[grouping] = {"compared": len(set(a) & set(b)), "identical": sum(1 for k in a if k in b and a[k] == b[k]),
                         "differing": sorted(k for k in a if k in b and a[k] != b[k])[:20]}
    result = {"written": now(), "runs": runs, "object_determinism_N16_vs_N8": det}
    dump(path, result)
    return result


def opt_flag_check(lift: dict) -> dict:
    """What `zig cc -O<n>` does to a .ll input: the cc1 -O level from `-v`, and the object at O0..O3 for the largest
    lifted function (the -O1 vs -O2 comparison in RUNS is only meaningful if the objects differ)."""
    biggest = max((OUT / "pass" / f"sub_{int(r['address'], 16):08X}.ll" for r in lift["rows"] if r["lifted"]), key=lambda p: p.stat().st_size)
    scratch = OUT / "scratch"
    levels = {}
    for opt in ("O0", "O1", "O2", "O3", "Os"):
        cache = tempfile.mkdtemp(prefix="zigcache.", dir=scratch)
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=cache, ZIG_LOCAL_CACHE_DIR=cache)
        obj = Path(cache) / "o.o"
        p = subprocess.run(compile_cmd(opt, biggest, obj) + ["-v"], cwd=scratch, env=env, capture_output=True, text=True)
        cc1 = next((l for l in (p.stderr + p.stdout).splitlines() if "-cc1" in l), "")
        m = re.search(r"\s(-O[0-9s])(?:\s|$)", cc1)
        levels[opt] = {"cc1_opt": m.group(1) if m else None, "rc": p.returncode,
                       "obj_bytes": obj.stat().st_size if obj.exists() else 0, "obj_sha256": sha(obj) if obj.exists() else None}
        shutil.rmtree(cache, ignore_errors=True)
    return {"tu": biggest.name, "ll_bytes": biggest.stat().st_size, "levels": levels,
            "O1_O2_O3_objects_identical": len({levels[o]["obj_sha256"] for o in ("O1", "O2", "O3")}) == 1,
            "reading": "zig cc (0.15.2) rewrites -O1, -O2 and -O3 to the same clang -cc1 optimisation level for this input, so every "
                       "compile run here is that level; the RUNS -O1 rows duplicate the -O2 rows and are kept as the measurement of that fact"
                       if len({levels[o]["obj_sha256"] for o in ("O1", "O2", "O3")}) == 1 else "the three levels produce different objects"}


def helper_inlining(pass_: dict) -> dict:
    """Whether __sa2_pc_round survives as a symbol in the g1 reference objects (an internal function that was inlined
    at every call site is deleted), and how many call instructions still target it."""
    obj_dir = OUT / "compile" / REFERENCE_RUNS["g1"] / "obj"
    with_ops = [r for r in pass_["rows"] if r["ops_instrumented"]]
    symbol_kept, calls, checked = 0, 0, 0
    for r in with_ops:
        obj = obj_dir / f"sub_{int(r['address'], 16):08X}.o"
        if not obj.exists():
            continue
        checked += 1
        nm = subprocess.run(["nm", str(obj)], capture_output=True, text=True).stdout
        if re.search(r"\b[tT] __sa2_pc_round", nm):
            symbol_kept += 1
        dis = subprocess.run(["objdump", "-d", "--no-show-raw-insn", str(obj)], capture_output=True, text=True).stdout
        calls += len(re.findall(r"\bcall\b[^\n]*__sa2_pc_round", dis))
    return {"objects_with_instrumented_ops": checked, "objects_keeping_helper_symbol": symbol_kept,
            "call_instructions_to_helper": calls, "instrumented_ops": sum(r["ops_instrumented"] for r in with_ops),
            "reading": "the helper is spliced as `define internal`; clang inlines it where its cost model allows and keeps one out-of-line copy "
                       "per object where it does not; call_instructions_to_helper against instrumented_ops is the measured inlining ratio"}


# ---------------------------------------------------------------- incremental
# ---------------------------------------------------------------- memory
# `wait4`'s ru_maxrss for a child is the maximum of that child's own peak and the high-water mark it
# inherited from the process that forked it (Linux copies mm->hiwater_rss into the child). This driver
# holds 500 lifted modules' text in flight across three lift passes, so its own high-water mark is
# several hundred MB and every compile it forks reports at least that. The timing runs therefore carry
# an upper bound, and the real per-compile peak is measured here instead: each compile is run by a
# fresh, small python process that forks exactly one child and reports its rusage.
MEM_PROBE = (
    "import os,sys,json,subprocess;"
    "cmd=json.loads(sys.argv[1]);"
    "p=subprocess.Popen(cmd,cwd=sys.argv[2],env=dict(os.environ,ZIG_GLOBAL_CACHE_DIR=sys.argv[3],ZIG_LOCAL_CACHE_DIR=sys.argv[3]),"
    "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT);"
    "_,st,ru=os.wait4(p.pid,0);"
    "print(json.dumps({'rc':os.waitstatus_to_exitcode(st),'maxrss_kb':ru.ru_maxrss,"
    "'cpu_s':round(ru.ru_utime+ru.ru_stime,4),'floor_kb':int(open('/proc/self/status').read().split('VmHWM:')[1].split()[0])}))"
)


def measure_memory_one(src: Path, opt: str) -> dict:
    scratch = OUT / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cache = tempfile.mkdtemp(prefix="zigmem.", dir=scratch)
    obj = Path(cache) / (src.stem.replace(".pass", "") + ".o")
    try:
        p = subprocess.run([sys.executable, "-c", MEM_PROBE, json.dumps(compile_cmd(opt, src, obj)), str(scratch), cache],
                           capture_output=True, text=True, timeout=900)
        row = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {"rc": p.returncode, "maxrss_kb": 0}
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    return {"tu": src.stem.replace(".pass", ""), "ll_bytes": src.stat().st_size, **row}


def phase_memory(lift: dict, tu25: dict, force: bool) -> dict:
    """Peak RSS per compile, measured one compile at a time from a fresh probe process."""
    path = OUT / "memory.json"
    if path.exists() and not force:
        return load(path)
    g1 = sorted(tu_sources("g1", lift, tu25), key=lambda p: p.stat().st_size, reverse=True)
    g25 = sorted(tu_sources("g25", lift, tu25), key=lambda p: p.stat().st_size, reverse=True)
    sets = {"g1": g1[:20] + g1[len(g1) // 2: len(g1) // 2 + 10], "g25": g25}
    out: dict = {"written": now(), "opt": "O2", "method": "one compile per fresh probe process, serially, fresh zig cache each",
                 "probe": "python -c <MEM_PROBE> (tools/s2/build_sample/build_sample.py): fork one child, wait4, report its rusage",
                 "selection": {"g1": "the 20 largest lifted modules plus 10 from the middle of the size order", "g25": "every TU"},
                 "by_grouping": {}}
    for grouping, sources in sets.items():
        rows = [measure_memory_one(s, "O2") for s in sources]
        ok = [r for r in rows if r.get("rc") == 0]
        rss = [r["maxrss_kb"] for r in ok]
        floor = [r.get("floor_kb", 0) for r in ok]
        out["by_grouping"][grouping] = {
            "compiles": len(rows), "ok": len(ok),
            "maxrss_kb": pstats(rss), "probe_floor_kb": pstats(floor),
            "max_mb": round(max(rss) / 1024, 1) if rss else 0,
            "median_mb": round(statistics.median(rss) / 1024, 1) if rss else 0,
            "rows": rows}
    dump(path, out)
    return out


# ---------------------------------------------------------------- link
def link_command(kind: str, objs: list[Path], out: Path) -> list[str]:
    extra = ["-r"] if kind == "relocatable" else []
    return [ZIG, "cc"] + TARGET + extra + ["-o", str(out)] + [str(o) for o in objs]


def phase_link(lift: dict, pass_: dict, force: bool, jobs: int) -> dict:
    """The link stage no receipt in this project had ever timed.

    The 1/TU objects as compiled cannot be linked at all: remill's `--bytes` lift defines a callee stub
    `sub_<callee>.<n>` in every module that calls that callee, so 500 modules hold duplicate definitions and
    `ld.lld` stops at the first one. The stage is therefore measured over the pipeline a real build would run:
    lift, deduplicate the stubs across the whole set (the 25/TU grouping already does this inside a TU), pass,
    compile, then link. Two links are measured: a relocatable link of every object into one object (what a
    whole-image link costs before the wrap exists) and a program link (which fails on the unresolved
    `__remill_*` runtime, recorded with its error rather than asserted)."""
    path = OUT / "link.json"
    if path.exists() and not force:
        return load(path)
    ldir = OUT / "link"
    if ldir.exists():
        shutil.rmtree(ldir)
    ldir.mkdir(parents=True)
    members = [r["address"] for r in lift["rows"] if r["lifted"]]

    t0 = time.perf_counter()
    files, dropped = dedup_stubs(members, OUT / "lift", ldir / "dedup")
    dedup_wall = time.perf_counter() - t0

    (ldir / "pass").mkdir()
    pass_wall, pass_rows = pass_all([Path(f) for f in files], ldir / "pass", pass_["helper"]["helper_ll"], jobs)

    (ldir / "obj").mkdir()
    (ldir / "log").mkdir()
    work = [(str(ldir / "pass" / Path(f).name), str(ldir / "obj" / (Path(f).stem + ".o")), "O2",
             str(ldir / "log" / (Path(f).stem + ".log"))) for f in files]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(16) as ex:
        crows = list(ex.map(compile_one, work))
    compile_wall = time.perf_counter() - t0
    objs = sorted((ldir / "obj").glob("*.o"))

    links = {}
    for kind, out_name in (("relocatable", "sample_all.o"), ("program", "sample_program")):
        out = ldir / out_name
        r = run_measured(link_command(kind, objs, out), cwd=ldir, log=ldir / f"{kind}.log", timeout=1800)
        links[kind] = {"ok": r["rc"] == 0 and out.exists(), "rc": r["rc"], "wall_s": r["wall_s"], "cpu_s": r["cpu_s"],
                       "maxrss_kb": r["maxrss_kb"], "output_bytes": out.stat().st_size if out.exists() else 0,
                       "error_head": r["error_head"],
                       "output": f"<{out_name}, under private/generated/s2/build_sample/link/, deleted after measurement>",
                       "command": cmdline(link_command(kind, [Path("<500 objects>")], Path(f"<{out_name}>"))).replace(str(ROOT) + "/", "")}
    result = {"written": now(), "grouping": "g1 (one function per translation unit), the objects the best projection uses",
              "modules": len(files), "objects": len(objs),
              "objects_ok": sum(1 for r in crows if r["ok"]), "object_bytes_total": sum(r["obj_bytes"] for r in crows if r["ok"]),
              "stub_defines_dropped": dropped,
              "dedup_wall_s": round(dedup_wall, 3), "dedup_jobs": 1,
              "pass_wall_s": pass_wall, "pass_jobs": jobs,
              "pass_ops_instrumented": sum(r["ops_instrumented"] for r in pass_rows),
              "compile_wall_s": round(compile_wall, 3), "compile_jobs": 16,
              "links": links,
              "note": "the relocatable link is one `ld.lld` process: its wall does not change with the job count, "
                      "so the projection carries the same link figure in every N column",
              "dedup_command": "dedup_stubs over all 500 lifted modules in address order (a callee stub `sub_<callee>.<n>` "
                               "defined by an earlier module becomes a declare); the 25/TU grouping runs the same rule inside a TU"}
    for sub in ("dedup", "pass", "obj"):
        shutil.rmtree(ldir / sub, ignore_errors=True)
    for name in ("sample_all.o", "sample_program"):
        (ldir / name).unlink(missing_ok=True)
    dump(path, result)
    return result


def manifest(obj_dir: Path) -> dict:
    return {p.name: sha(p) for p in sorted(obj_dir.glob("*.o"))}


def phase_incremental(sample: dict, lift: dict, pass_: dict, tu25: dict, compile_: dict, force: bool) -> dict:
    path = OUT / "incremental.json"
    if path.exists() and not force:
        return load(path)
    lifted = [r for r in lift["rows"] if r["lifted"]]
    instr = {f["address"]: f["instructions"] for f in sample["functions"]}
    by_size = sorted(lifted, key=lambda r: (instr[r["address"]], r["address"]))
    n = len(by_size)
    picks = [by_size[n // 10], by_size[n // 2], by_size[9 * n // 10]]
    tu_of = {a: r["tu"] for r in tu25["rows"] if r["link_rc"] == 0 and r.get("pass_ll_sha256") for a in r["members"]}
    helper_ll = Path(pass_["helper"]["helper_ll"])
    img, base = load_image()
    idir = OUT / "incremental"
    if idir.exists():
        shutil.rmtree(idir)
    idir.mkdir(parents=True)
    obj_dirs = {g: OUT / "compile" / REFERENCE_RUNS[g] / "obj" for g in ("g1", "g25")}
    before = {g: manifest(d) for g, d in obj_dirs.items()}
    checks = []
    for r in picks:
        a = r["address"]
        va = int(a, 16)
        size = next(f["size"] for f in sample["functions"] if f["address"] == a)
        entry = {"address": a, "instructions": instr[a], "size": size, "tu25": tu_of.get(a)}
        # re-lift alone
        t0 = time.perf_counter()
        lr = lift_one((img[va - base: va - base + size].hex(), a, str(idir)))
        t_lift = time.perf_counter() - t0
        new_ll = idir / f"sub_{va:08X}.ll"
        entry["relift"] = {"wall_s": round(t_lift, 4), "lifted": lr["lifted"], "ll_sha256": lr["ll_sha256"],
                           "identical_to_first_lift": lr["ll_sha256"] == r["ll_sha256"]}
        if not entry["relift"]["identical_to_first_lift"] and lr["lifted"]:
            entry["relift"]["diff"] = diff_summary(OUT / "lift" / f"sub_{va:08X}.ll", new_ll)
        # re-pass alone
        new_pass = idir / f"sub_{va:08X}.pass.ll"
        pr = pass_one((str(new_ll), str(new_pass), str(helper_ll)))
        old_pass_sha = next(x["pass_ll_sha256"] for x in pass_["rows"] if x["address"] == a)
        entry["repass"] = {"wall_s": pr["pass_wall_s"], "pass_ll_sha256": pr["pass_ll_sha256"], "identical_to_first_pass": pr["pass_ll_sha256"] == old_pass_sha}
        # g1: recompile only this function's TU, in place
        obj = obj_dirs["g1"] / f"sub_{va:08X}.o"
        old = before["g1"].get(obj.name)
        cr = compile_one((str(new_pass), str(obj), "O2", str(idir / f"sub_{va:08X}.g1.log")))
        entry["g1"] = {"tu": obj.name, "compile_wall_s": cr["wall_s"], "ok": cr["ok"], "obj_sha256_before": old, "obj_sha256_after": cr["obj_sha256"],
                       "object_identical": cr["obj_sha256"] == old,
                       "incremental_wall_s": round(t_lift + pr["pass_wall_s"] + cr["wall_s"], 4),
                       "steps": "relift + pass + compile (fresh zig cache)"}
        # g25: relink this function's TU with the new .ll, pass, recompile in place
        if a in tu_of:
            i = tu_of[a]
            members = next(x["members"] for x in tu25["rows"] if x["tu"] == i)
            src_dir = idir / f"tu_{i:02d}.src"
            src_dir.mkdir()
            for m in members:   # the TU's other 24 members from the first lift, this one from the re-lift
                mv = int(m, 16)
                os.symlink((new_ll if m == a else OUT / "lift" / f"sub_{mv:08X}.ll"), src_dir / f"sub_{mv:08X}.ll")
            tu_ll, tu_pass = idir / f"tu_{i:02d}.ll", idir / f"tu_{i:02d}.pass.ll"
            lk = link_tu(members, src_dir, tu_ll, idir / f"tu_{i:02d}.link.log")
            pt = pass_tu(tu_ll, tu_pass, helper_ll) if lk["link_rc"] == 0 else {"pass_wall_s": 0, "pass_ll_sha256": None}
            old_tu_sha = next(x["pass_ll_sha256"] for x in tu25["rows"] if x["tu"] == i)
            obj = obj_dirs["g25"] / f"tu_{i:02d}.o"
            old = before["g25"].get(obj.name)
            cr = compile_one((str(tu_pass), str(obj), "O2", str(idir / f"tu_{i:02d}.g25.log")))
            entry["g25"] = {"tu": obj.name, "link_wall_s": lk["link_wall_s"], "link_ok": lk["link_rc"] == 0, "pass_wall_s": pt["pass_wall_s"],
                            "tu_pass_ll_identical": pt["pass_ll_sha256"] == old_tu_sha,
                            "compile_wall_s": cr["wall_s"], "ok": cr["ok"], "obj_sha256_before": old, "obj_sha256_after": cr["obj_sha256"],
                            "object_identical": cr["obj_sha256"] == old,
                            "incremental_wall_s": round(t_lift + lk["link_wall_s"] + pt["pass_wall_s"] + cr["wall_s"], 4),
                            "steps": "relift + relink 25 + pass + compile (fresh zig cache)"}
        checks.append(entry)
    after = {g: manifest(d) for g, d in obj_dirs.items()}
    rebuilt = {"g1": {f"sub_{int(c['address'], 16):08X}.o" for c in checks}, "g25": {c["g25"]["tu"] for c in checks if "g25" in c}}
    others = {}
    for g in ("g1", "g25"):
        keys = set(before[g]) | set(after[g])
        changed = sorted(k for k in keys if before[g].get(k) != after[g].get(k))
        others[g] = {"objects": len(before[g]), "rebuilt": sorted(rebuilt[g]), "changed": changed,
                     "changed_outside_rebuilt": sorted(set(changed) - rebuilt[g]),
                     "all_other_objects_unchanged": not (set(changed) - rebuilt[g]),
                     "rebuilt_objects_identical": all(before[g].get(k) == after[g].get(k) for k in rebuilt[g])}
    result = {"written": now(), "picked": "lifted sample sorted by instruction count: the 10th, 50th and 90th percentile functions",
              "reference_runs": REFERENCE_RUNS, "checks": checks, "manifest": others,
              "incremental_wall_s": {g: {c["address"]: c[g]["incremental_wall_s"] for c in checks if g in c} for g in ("g1", "g25")}}
    dump(path, result)
    return result


# ---------------------------------------------------------------- receipt
def machine() -> dict:
    mem = next((int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")), 0)
    cpuinfo = Path("/proc/cpuinfo").read_text().splitlines()
    cpu = next((l.split(":", 1)[1].strip() for l in cpuinfo if l.startswith("model name")), "")
    cores = next((int(l.split(":", 1)[1]) for l in cpuinfo if l.startswith("cpu cores")), 0)
    siblings = next((int(l.split(":", 1)[1]) for l in cpuinfo if l.startswith("siblings")), 0)
    smt = bool(cores and siblings > cores)
    return {"nproc": NPROC, "cpu_model": cpu, "cpu_cores": cores, "threads_per_core": (siblings // cores) if cores else 0,
            "smt": smt,
            "smt_note": (f"nproc counts SMT threads: {cores} physical cores with {siblings // cores if cores else 0} threads each. "
                         "N=16 is 16 threads on 8 cores, so the CPU-second columns below are not machine-independent: the same "
                         "byte-identical objects cost more CPU seconds at N=16 than at N=8 because the threads share cores. "
                         "Compare wall time at a stated job count, not CPU seconds." if smt else "no SMT"),
            "ram_kb": mem, "ram_gb": round(mem / 1048576, 1),
            "kernel": platform.release(), "python": platform.python_version(),
            "zig": PINS["tools"]["zig"]["version"], "clang": PINS["tools"]["zig"]["clang"], "zig_path": PINS["tools"]["zig"]["path"],
            "remill": {k: PINS["tools"]["remill"][k] for k in ("version", "commit", "tarball_sha256", "path")}}


def wall_by_jobs(phase: dict) -> dict[int, float]:
    """{job count: measured phase wall} for a phase that recorded a job scan beside its own run."""
    out = {int(phase["jobs"]): phase["phase_wall_s"]}
    for s in phase.get("job_scan", []):
        out[int(s["jobs"])] = s["phase_wall_s"]
    return out


def ghidra_prerequisite() -> dict:
    """The analyzer run the projection does not contain, with the figure the S1 receipt measured."""
    path = ROOT / "Documentation" / "receipts" / "s1-function-map-v1.json"
    out = {"what": "The projection starts from private/generated/function-map.json. Producing that map is a Ghidra "
                   "headless analysis of the whole image, which is a prerequisite of every lift and is not part of "
                   "any figure in this receipt; it is a whole-image cost already paid once, not a per-function cost "
                   "that scales with the sample.",
           "receipt": "Documentation/receipts/s1-function-map-v1.json",
           "keys": ["outputs.ghidra_analysis_seconds", "outputs.analysis_wall_seconds"]}
    if path.is_file():
        o = json.loads(path.read_text()).get("outputs", {})
        out["ghidra_analysis_seconds"] = o.get("ghidra_analysis_seconds")
        out["analysis_wall_seconds"] = o.get("analysis_wall_seconds")
        if isinstance(out.get("analysis_wall_seconds"), (int, float)):
            out["analysis_wall_minutes"] = round(out["analysis_wall_seconds"] / 60, 3)
    out["bounded"] = "measured once on this image on this machine; it is not re-measured here and no gate re-runs it"
    return out


def projection(sample: dict, lift: dict, pass_: dict, tu25: dict, compile_: dict, link: dict) -> dict:
    k = sample["scale_factor"]
    lifted_rows = [r for r in lift["rows"] if r["lifted"]]
    sample_instr = sample["instructions_sum"]
    lifted_instr = sum(f["instructions"] for f in sample["functions"] if any(r["address"] == f["address"] for r in lifted_rows))
    tu25_link_wall = sum(r["link_wall_s"] for r in tu25["rows"])   # measured with `jobs` parallel links; phase wall is the honest one
    lift_by_jobs = wall_by_jobs(lift)
    pass_by_jobs = wall_by_jobs(pass_)
    link_ok = link["links"]["relocatable"]["ok"]
    link_wall = link["links"]["relocatable"]["wall_s"] if link_ok else None
    link_min = round(link_wall * k / 60, 3) if link_ok else None
    stages = {
        "lift": {"sample_wall_s": lift["phase_wall_s"], "jobs": lift["jobs"], "full_image_min": round(lift["phase_wall_s"] * k / 60, 3),
                 "by_jobs": {str(j): {"sample_wall_s": w, "full_image_min": round(w * k / 60, 3)} for j, w in sorted(lift_by_jobs.items())}},
        "pass": {"sample_wall_s": pass_["phase_wall_s"], "jobs": pass_["jobs"], "full_image_min": round(pass_["phase_wall_s"] * k / 60, 3),
                 "by_jobs": {str(j): {"sample_wall_s": w, "full_image_min": round(w * k / 60, 3)} for j, w in sorted(pass_by_jobs.items())}},
        "tu25_link_and_pass": {"sample_wall_s": tu25["phase_wall_s"], "jobs": tu25["jobs"], "link_wall_sum_s": round(tu25_link_wall, 3),
                               "full_image_min": round(tu25["phase_wall_s"] * k / 60, 3),
                               "measured_at_one_job_count": "only at 16 jobs; the N=8 and N=1 columns below carry the 16-job figure "
                                                            "for this stage and are therefore optimistic for the 25/TU grouping"},
        "link": {"sample_wall_s": link_wall, "jobs": 1, "full_image_min": link_min,
                 "what": "one relocatable link (`zig cc -r`) of all 500 objects into a single object, after the callee stubs are "
                         "deduplicated across the whole set; it is one ld.lld process, so its wall does not change with the job count",
                 "ok": link_ok, "objects": link["objects"], "output_bytes": link["links"]["relocatable"]["output_bytes"],
                 "program_link": {"ok": link["links"]["program"]["ok"], "rc": link["links"]["program"]["rc"],
                                  "error_head": link["links"]["program"]["error_head"]},
                 "scaling_caveat": "linker cost is not linear in instruction count (symbol resolution and relocation counts grow "
                                   "faster than text); the x24.805 scaling of a 500-object link is the weakest projection here"},
    }
    table = []
    for name, run in compile_["runs"].items():
        front = stages["lift"]["full_image_min"] + stages["pass"]["full_image_min"] + (stages["tu25_link_and_pass"]["full_image_min"] if run["grouping"] == "g25" else 0)
        comp = run["phase_wall_s"] * k / 60
        table.append({"run": name, "grouping": run["grouping"], "jobs": run["jobs"], "opt": run["opt"],
                      "sample_compile_wall_s": run["phase_wall_s"], "sample_compile_cpu_s": run["cpu_sum_s"],
                      "full_image_compile_min": round(comp, 3), "full_image_compile_cpu_min": round(run["cpu_sum_s"] * k / 60, 3),
                      "full_image_front_end_min": round(front, 3),
                      "full_image_lift_pass_compile_min": round(front + comp, 3),
                      "full_image_link_min": link_min,
                      "full_image_lift_pass_compile_link_min": round(front + comp + (link_min or 0), 3),
                      "full_image_objects_mb": round(run["obj_bytes_total"] * k / 1048576, 1),
                      "full_image_ir_gb": round(run["ll_bytes_total"] * k / 1073741824, 2),
                      "exceeds_60_min": (front + comp + (link_min or 0)) > KILL_MINUTES})
    o2 = [t for t in table if t["opt"] == "O2"]
    worst = max(o2, key=lambda t: t["full_image_lift_pass_compile_link_min"])
    best = min(o2, key=lambda t: t["full_image_lift_pass_compile_link_min"])
    fires = any(t["exceeds_60_min"] for t in o2)

    # One row per job count: the margin is a job-count figure, so the receipt states it as one.
    by_jobs = []
    for n in sorted(set(lift_by_jobs) & set(pass_by_jobs)):
        for grouping in ("g1", "g25"):
            run = compile_["runs"].get(f"{grouping}_N{n}_O2")
            if run is None:
                continue
            lift_min = round(lift_by_jobs[n] * k / 60, 3)
            pass_min = round(pass_by_jobs[n] * k / 60, 3)
            tu_min = stages["tu25_link_and_pass"]["full_image_min"] if grouping == "g25" else 0.0
            comp_min = round(run["phase_wall_s"] * k / 60, 3)
            total = round(lift_min + pass_min + tu_min + comp_min + (link_min or 0), 3)
            by_jobs.append({"jobs": n, "grouping": grouping, "run": run["name"],
                            "lift_min": lift_min, "pass_min": pass_min, "tu25_link_and_pass_min": round(tu_min, 3),
                            "compile_min": comp_min, "link_min": link_min, "total_min": total,
                            "percent_of_threshold": round(total / KILL_MINUTES * 100, 1),
                            "margin_min": round(KILL_MINUTES - total, 3),
                            "exceeds_60_min": total > KILL_MINUTES})
    serial = [r for r in by_jobs if r["jobs"] == 1]
    serial_worst = max(serial, key=lambda r: r["total_min"]) if serial else None
    serial_best = min(serial, key=lambda r: r["total_min"]) if serial else None
    par = [r for r in by_jobs if r["jobs"] == max(lift_by_jobs)]
    par_worst = max(par, key=lambda r: r["total_min"]) if par else None
    par_best = min(par, key=lambda r: r["total_min"]) if par else None
    fires_at_any = any(r["exceeds_60_min"] for r in by_jobs)

    verdict = (
        f"kill criterion 1 {'FIRES' if (fires or fires_at_any) else 'does not fire'}, and the margin is a job-count figure: "
        f"at {par_best['jobs']} parallel jobs the projected full-image lift+pass+compile+link at -O2 is "
        f"{par_best['total_min']} min ({par_best['run']}, {par_best['percent_of_threshold']} % of the 60-minute threshold) to "
        f"{par_worst['total_min']} min ({par_worst['run']}, {par_worst['percent_of_threshold']} %); "
        f"serially, at 1 job, it is {serial_best['total_min']} min ({serial_best['run']}, "
        f"{serial_best['percent_of_threshold']} %) to {serial_worst['total_min']} min ({serial_worst['run']}, "
        f"{serial_worst['percent_of_threshold']} %). Every figure scales the sample's {sample_instr} instructions "
        f"({len(lifted_rows)}/{sample['n']} lifted) to {sample['mapped_instructions']['value']} mapped instructions "
        f"(x{k:.3f}). The threshold is a wall-clock one and this machine has {machine()['cpu_cores']} cores with "
        f"{machine()['threads_per_core']} threads each, so a reader quoting one number must quote its job count with it."
        if serial and par else
        f"kill criterion 1 {'FIRES' if fires else 'does not fire'}: {best['full_image_lift_pass_compile_link_min']} min "
        f"(best: {best['run']}) to {worst['full_image_lift_pass_compile_link_min']} min (worst: {worst['run']})")
    return {"method": "full_image_minutes = sample_phase_wall_seconds x (mapped_instructions / sample_instructions) / 60; lift, pass and compile "
                      "are per function or per TU and were measured with the stated parallelism, so each projected stage keeps its parallelism; "
                      "decision 2026-09-19-G1 section 1.3 scales by instructions. Every phase wall is measured at each job count in "
                      f"{list(JOB_SCAN)} (the 25/TU link-and-pass stage excepted, measured at 16), so the N=1 column is a measured serial "
                      "wall and not a sum of per-function walls taken under 16-way contention.",
            "mapped_instructions": sample["mapped_instructions"], "sample_instructions": sample_instr,
            "sample_instructions_lifted": lifted_instr, "scale_factor": k, "stages": stages, "table": table,
            "by_jobs": by_jobs,
            "prerequisite_not_in_the_projection": ghidra_prerequisite(),
            "kill_criterion_1": {"threshold_wall_minutes": KILL_MINUTES, "fires": fires or fires_at_any,
                                 "best": best["run"], "worst": worst["run"],
                                 "best_min": best["full_image_lift_pass_compile_link_min"],
                                 "worst_min": worst["full_image_lift_pass_compile_link_min"],
                                 "parallel_jobs": par_best["jobs"] if par else None,
                                 "parallel_best_min": par_best["total_min"] if par else None,
                                 "parallel_worst_min": par_worst["total_min"] if par else None,
                                 "parallel_worst_percent_of_threshold": par_worst["percent_of_threshold"] if par else None,
                                 "serial_jobs": 1,
                                 "serial_best_min": serial_best["total_min"] if serial else None,
                                 "serial_worst_min": serial_worst["total_min"] if serial else None,
                                 "serial_worst_percent_of_threshold": serial_worst["percent_of_threshold"] if serial else None,
                                 "verdict": verdict}}


def phase_receipt(sample: dict, lift: dict, pass_: dict, tu25: dict, compile_: dict, link: dict, memory: dict, incremental: dict) -> dict:
    rows = {f["address"]: dict(f) for f in sample["functions"]}
    for r in lift["rows"]:
        rows[r["address"]].update({k: r[k] for k in ("lifted", "rc", "lift_wall_s", "lift_cpu_s", "lift_maxrss_kb", "ll_lines", "ll_sha256",
                                                     "defines", "missing_block_calls", "fp80_ops", "relift_identical")})
        if r["error_head"]:
            rows[r["address"]]["lift_error_head"] = r["error_head"]
    for r in pass_["rows"]:
        rows[r["address"]].update({"pass_wall_s": r["pass_wall_s"], "ops_instrumented": r["ops_instrumented"], "pass_ll_lines": r["pass_ll_lines"],
                                   "pass_ll_sha256": r["pass_ll_sha256"], "skipped_by_semantic": sum(r["skipped_by_semantic"].values())})
    for r in tu25["rows"]:
        for a in r["members"]:
            rows[a]["tu25"] = r["tu"]
    for r in compile_["runs"]["g1_N16_O2"]["rows"]:
        a = "0x" + r["tu"][4:]
        rows[a].update({"g1_O2_compile_wall_s": r["wall_s"], "g1_O2_maxrss_kb": r["maxrss_kb"], "g1_O2_obj_bytes": r["obj_bytes"], "g1_O2_obj_sha256": r["obj_sha256"]})
    lifted = [r for r in lift["rows"] if r["lifted"]]
    failed = [r for r in lift["rows"] if not r["lifted"]]
    proj = projection(sample, lift, pass_, tu25, compile_, link)
    by_sem = {}
    for r in pass_["rows"]:
        for k, v in r["by_semantic"].items():
            by_sem[k] = by_sem.get(k, 0) + v
    skipped = {}
    for r in pass_["rows"]:
        for k, v in r["skipped_by_semantic"].items():
            skipped[k] = skipped.get(k, 0) + v
    ll_lines = sum(r["ll_lines"] for r in lifted)
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s2-build-time-sample-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S2",
        "question": "Kill criterion 1 (decision 2026-09-19-G1 section 1.4): lift and compile a 500-function sample of the map, stratified by size, "
                    "through the inlined, optimised bitcode pipeline with per-TU parallelism; does the projected full-image lift+compile exceed "
                    "60 wall minutes on this machine? Also: does per-function incremental re-lift work, and is the lift byte-deterministic?",
        "inputs": {
            "exe": rel(EXE), "exe_sha256": sha(EXE),
            "map": sample["map"],
            "tool": {"path": rel(Path(__file__)), "sha256": sha(Path(__file__))},
            "pass": {"source": "tools/spike/pc24pass/pc24pass.py", "sha256": pass_["pass_sha256"],
                     "helper_source": "tools/spike/pc24pass/pc_round.c", "helper_source_sha256": pass_["helper"]["source_sha256"],
                     "helper_ll_sha256": pass_["helper"]["helper_ll_sha256"], "helper_ll_command": pass_["helper"]["command"],
                     "helper_ll_equals_s1_build": pass_["helper"]["equals_s1_helper_ll"]},
            "remill_version_line": lift["remill_version"],
            "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        },
        "machine": machine(),
        "command": "source tools/env.sh && .venv/bin/python tools/s2/build_sample/build_sample.py",
        "commands": {
            "lift": lift["command_template"], "pass": pass_["command"],
            "tu25_link": tu25["link_command"], "tu25_pass": tu25["pass_command"],
            "compile": {name: run["command"] for name, run in compile_["runs"].items()},
        },
        "sample": {k: sample[k] for k in ("seed", "n", "deciles", "per_decile", "method", "mapped_instructions", "scale_factor", "strata",
                                            "by_region", "instructions_sum", "bytes_sum")},
        "lift": {
            "jobs": lift["jobs"], "phase_wall_s": lift["phase_wall_s"], "children_cpu_s": lift["children_cpu_s"],
            "functions": len(lift["rows"]), "lifted": len(lifted), "failed": len(failed),
            "failures": [{"address": r["address"], "rc": r["rc"], "error_head": r["error_head"]} for r in failed],
            "per_function_wall_s": pstats([r["lift_wall_s"] for r in lift["rows"]]),
            "per_function_maxrss_kb": pstats([r["lift_maxrss_kb"] for r in lift["rows"]]),
            "job_scan": lift.get("job_scan", []),
            "job_scan_note": "the same 500 lifts re-measured at each job count; per_function_wall_s.sum above is a sum of "
                             "walls taken under the reference run's parallelism and is not the serial wall",
            "ir_lines_total": ll_lines, "ir_bytes_total": sum(r["ll_bytes"] for r in lifted),
            "ir_lines_per_instruction": round(ll_lines / max(1, proj["sample_instructions_lifted"]), 2),
            "ir_lines_per_instruction_s1_ten_functions": 85.7,
            "missing_block_calls_total": sum(r["missing_block_calls"] for r in lifted),
            "functions_with_missing_block_calls": sum(1 for r in lifted if r["missing_block_calls"]),
            "defines_total": sum(r["defines"] for r in lifted),
            "fp80_ops_total": sum(r["fp80_ops"] for r in lifted),
            "determinism": {"method": "every function lifted twice (lift/ and lift2/), sha256 of the .ll compared",
                            "second_lift_wall_s": lift["second_lift"]["phase_wall_s"],
                            "identical": lift["second_lift"]["identical"], "differing": lift["second_lift"]["differing"],
                            "differing_summaries": lift["second_lift"]["differing_summaries"],
                            "byte_deterministic": lift["second_lift"]["differing"] == 0 and lift["second_lift"]["identical"] == len(lifted)},
        },
        "pass": {
            "jobs": pass_["jobs"], "phase_wall_s": pass_["phase_wall_s"], "files": len(pass_["rows"]),
            "per_function_wall_s": pstats([r["pass_wall_s"] for r in pass_["rows"]]),
            "ops_instrumented_total": sum(r["ops_instrumented"] for r in pass_["rows"]),
            "functions_with_instrumented_ops": sum(1 for r in pass_["rows"] if r["ops_instrumented"]),
            "by_semantic": by_sem, "skipped_by_semantic": skipped,
            "ir_lines_after": sum(r["pass_ll_lines"] for r in pass_["rows"]),
            "job_scan": pass_.get("job_scan", []),
        },
        "link": link,
        "memory": memory,
        "tu25": {k: tu25[k] for k in ("functions_per_tu", "tus", "jobs", "phase_wall_s", "grouping")} | {
            "link_ok": sum(1 for r in tu25["rows"] if r["link_rc"] == 0),
            "link_failures": [{"tu": r["tu"], "error_head": r["link_error_head"]} for r in tu25["rows"] if r["link_rc"] != 0],
            "per_tu_link_wall_s": pstats([r["link_wall_s"] for r in tu25["rows"]]),
            "per_tu_link_maxrss_kb": pstats([r["link_maxrss_kb"] for r in tu25["rows"]]),
            "per_tu_pass_wall_s": pstats([r["pass_wall_s"] for r in tu25["rows"] if "pass_wall_s" in r]),
            "callee_stub_defines_total": sum(r["callee_stub_defines"] for r in tu25["rows"]),
            "stub_defines_dropped_total": sum(r["stub_defines_dropped"] for r in tu25["rows"]),
            "ops_instrumented_total": sum(r.get("ops_instrumented", 0) for r in tu25["rows"]),
            "tus_table": [{k: r[k] for k in ("tu", "dedup_wall_s", "stub_defines_dropped", "link_wall_s", "link_maxrss_kb", "linked_ll_lines", "defines", "callee_stub_defines") if k in r}
                          | {"members": len(r["members"]), "pass_wall_s": r.get("pass_wall_s"), "pass_ll_lines": r.get("pass_ll_lines"), "pass_ll_sha256": r.get("pass_ll_sha256")}
                          for r in tu25["rows"]],
        },
        "compile": {
            "runs": {name: {k: run[k] for k in ("grouping", "jobs", "opt", "command", "tus", "ok", "failed", "phase_wall_s", "children_cpu_s", "cpu_sum_s",
                                                 "wall_sum_s", "maxrss_kb", "obj_bytes_total", "ll_bytes_total", "per_tu_wall_s", "failures")}
                     for name, run in compile_["runs"].items()},
            "table": [{"run": name, "grouping": run["grouping"], "N": run["jobs"], "opt": run["opt"], "tus": run["tus"], "ok": run["ok"],
                       "total_wall_s": run["phase_wall_s"], "cpu_sum_s": run["cpu_sum_s"],
                       "max_rss_kb_upper_bound": run["maxrss_kb"]["max"], "obj_bytes": run["obj_bytes_total"]}
                      for name, run in compile_["runs"].items()],
            "rss_columns_are_upper_bounds": "every maxrss_kb in compile.runs and per_function is wait4's ru_maxrss for a child "
                                            "this driver forked, and Linux gives a forked child its parent's high-water mark: "
                                            "this driver's own peak (it holds 500 lifted modules in flight) is the floor under "
                                            "every one of them. The real per-compile peak is receipt key `memory`, measured one "
                                            "compile at a time from a fresh probe process.",
            "g25_tus_table": [{k: r[k] for k in ("tu", "ok", "wall_s", "cpu_s", "maxrss_kb", "ll_bytes", "obj_bytes", "obj_sha256")}
                              for r in sorted(compile_["runs"]["g25_N16_O2"]["rows"], key=lambda r: r["tu"])],
            "object_determinism_N16_vs_N8": compile_["object_determinism_N16_vs_N8"],
            "opt_flag_check": opt_flag_check(lift),
            "helper_inlining": helper_inlining(pass_),
            "O1_vs_O2_N16": {g: {"O2_wall_s": compile_["runs"][f"{g}_N16_O2"]["phase_wall_s"], "O1_wall_s": compile_["runs"][f"{g}_N16_O1"]["phase_wall_s"],
                                 "O2_cpu_s": compile_["runs"][f"{g}_N16_O2"]["cpu_sum_s"], "O1_cpu_s": compile_["runs"][f"{g}_N16_O1"]["cpu_sum_s"],
                                 "O2_obj_bytes": compile_["runs"][f"{g}_N16_O2"]["obj_bytes_total"], "O1_obj_bytes": compile_["runs"][f"{g}_N16_O1"]["obj_bytes_total"]}
                             for g in ("g1", "g25")},
        },
        "projection": proj,
        "kill_criterion_1": proj["kill_criterion_1"],
        "incremental": incremental,
        "determinism": {
            "remill_lift": {"byte_deterministic": lift["second_lift"]["differing"] == 0 and lift["second_lift"]["identical"] == len(lifted),
                            "functions_compared": len(lifted), "identical": lift["second_lift"]["identical"], "differing": lift["second_lift"]["differing"]},
            "pass": {"method": "pure text, no timestamps (tools/spike/pc24pass); the incremental checks re-ran it on re-lifted IR",
                     "incremental_repass_identical": all(c["repass"]["identical_to_first_pass"] for c in incremental["checks"])},
            "compile": {"N16_vs_N8_objects": compile_["object_determinism_N16_vs_N8"],
                        "incremental_rebuilt_objects_identical": {g: incremental["manifest"][g]["rebuilt_objects_identical"] for g in ("g1", "g25")}},
        },
        "per_function": [rows[a] for a in sorted(rows, key=lambda a: int(a, 16))],
        "authority": "measured (every timing, count and hash is read from this run's outputs under private/generated/s2/build_sample/); "
                     "the projection is an arithmetic scaling of measured sample walls, stated with its factor",
        "limits": [
            "The TU grouping is arbitrary (address-order chunks of 25 lifted functions); a call-graph or file-based grouping would change per-TU size and inlining but not the volume.",
            "Every call out of a function's bytes ends in __remill_missing_block and every return in __remill_function_return: these objects are not linkable "
            "into a program until the S2 wrap supplies the __remill_* runtime and a dispatcher; the sample measures lift and compile cost, not the wrap.",
            "The lift gives remill the map's full `size` bytes per function (the S1 spike lifted to the first ret); a function whose bytes remill decodes only partly "
            "still counts as lifted when its define exists; missing-block and define counts are recorded per function.",
            "The 25/TU grouping links raw remill IR with remill's own LLVM 14 llvm-link (-opaque-pointers) and runs the pass once per TU, because LLVM 14 cannot "
            "parse the clang-20 helper IR the pass splices in; the 1/TU grouping runs the pass per function. The two groupings therefore carry different pass "
            "walls, both recorded.",
            "The compile target is x86_64-linux-musl (the S1 harness target); the lifted IR carries an i386-windows-msvc triple and datalayout, which clang overrides "
            "with a warning (-w). A 32-bit target or a different host ABI would change code size and possibly compile time.",
            "zig cc caches -c objects by content; every compile here runs with a fresh, empty ZIG_GLOBAL_CACHE_DIR/ZIG_LOCAL_CACHE_DIR so the walls are real compiles; "
            "peak RSS is the child's ru_maxrss from wait4 (zig runs clang in-process).",
            "zig cc's -O flag is not clang's: compile.opt_flag_check records what -O0/-O1/-O2/-O3/-Os reach clang -cc1 as and whether the objects differ; "
            "the -O1 rows of the compile table are read against that check. compile.helper_inlining records whether the spliced __sa2_pc_round was "
            "inlined (symbol kept, call instructions left) rather than assuming it.",
            "The projection scales sample wall time by instruction count at each measured job count (1, 8 and 16); projection.by_jobs carries one row "
            "per count, because a wall-clock threshold has no meaning without the job count that produced it. The 25/TU link-and-pass stage is "
            "measured at 16 jobs only and carried unchanged into the N=8 and N=1 rows, which makes those rows optimistic for that grouping.",
            "The link stage is measured (receipt key `link`) but only in one form: a relocatable link of all 500 objects into one object, after the "
            "callee stubs are deduplicated across the whole set. The program link is run and fails on the unresolved __remill_* runtime; its error "
            "is recorded rather than asserted. Linker cost is not linear in instruction count, so the x24.805 scaling of the link is the weakest "
            "projection in this receipt; the final program link, with the wrap's dispatcher and runtime, has still never been run at any size.",
            "The projection does not contain the analyzer: producing private/generated/function-map.json is a Ghidra headless run of the whole image, "
            "measured once in Documentation/receipts/s1-function-map-v1.json and cited under projection.prerequisite_not_in_the_projection. It is a "
            "one-off whole-image cost, it does not scale with the sample, and nothing re-measures it.",
            "nproc is 16 SMT threads on 8 physical cores (machine.smt_note). The CPU-second columns are therefore not machine-independent and not "
            "comparable across job counts: the same byte-identical objects burn more CPU seconds at N=16 than at N=8. Wall time at a stated job "
            "count is the comparable figure.",
            "The projection does not model I/O, the wrap, or functions the analyzer has not mapped.",
            "Incremental re-lift is measured on three functions and simulates an emitter change by re-lifting the function; a real emitter change would also "
            "re-run the pass on every function (pass phase wall above), unless the change is per-function.",
            "No wrap, no runtime, no fixture run: nothing here says the compiled objects are correct, only how long they take and how much memory they need.",
            f"Sampling is uniform inside each decile, so the top decile ({sample['strata'][-1]['instructions_min']}-{sample['strata'][-1]['instructions_max']} "
            f"instructions) is represented by functions up to {max(f['instructions'] for f in sample['functions'])} instructions; per-TU compile wall grows with "
            "IR size (per_function rows), so the projection's tail (the few thousand-instruction functions) is its least certain part.",
        ],
        "written_by": rel(Path(__file__)),
        "written": now(),
    }
    dump(RECEIPT, receipt)
    return receipt


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=["all", "sample", "lift", "pass", "tu25", "compile", "link", "memory", "incremental", "receipt"], default="all")
    ap.add_argument("--force", action="store_true", help="redo the named phase (and, with all, every phase) even if its JSON exists")
    ap.add_argument("--jobs", type=int, default=NPROC, help="parallel lifts/passes/links (compile runs use their own N)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    want = lambda p: a.phase in ("all", p)  # noqa: E731
    force = lambda p: a.force and want(p)  # noqa: E731
    phases = ["sample", "lift", "pass", "tu25", "compile", "link", "memory", "incremental"]
    if a.force and a.phase != "receipt":   # a redone phase invalidates everything downstream of it
        start = 0 if a.phase == "all" else phases.index(a.phase)
        for p in phases[start:]:
            (OUT / f"{p}.json").unlink(missing_ok=True)

    sample = phase_sample(force("sample"))
    print(f"sample: {sample['n']} functions, {sample['instructions_sum']} instructions, by region {sample['by_region']}, "
          f"scale x{sample['scale_factor']} to {sample['mapped_instructions']['value']}", flush=True)
    if a.phase == "sample":
        return
    lift = phase_lift(sample, force("lift"), a.jobs)
    print(f"lift: {sum(r['lifted'] for r in lift['rows'])}/{len(lift['rows'])} lifted, wall {lift['phase_wall_s']} s with {lift['jobs']} jobs, "
          f"cpu {lift['children_cpu_s']} s; second lift identical {lift['second_lift']['identical']}, differing {lift['second_lift']['differing']}", flush=True)
    if a.phase == "lift":
        return
    pass_ = phase_pass(lift, force("pass"), a.jobs)
    print(f"pass: {len(pass_['rows'])} files, wall {pass_['phase_wall_s']} s, ops {sum(r['ops_instrumented'] for r in pass_['rows'])}", flush=True)
    if a.phase == "pass":
        return
    tu25 = phase_tu25(lift, pass_, force("tu25"), a.jobs)
    print(f"tu25: {tu25['tus']} TUs, link ok {sum(1 for r in tu25['rows'] if r['link_rc'] == 0)}, wall {tu25['phase_wall_s']} s", flush=True)
    if a.phase == "tu25":
        return
    compile_ = phase_compile(lift, tu25, force("compile"))
    if a.phase == "compile":
        return
    link = phase_link(lift, pass_, force("link"), a.jobs)
    print(f"link: {link['objects']} objects, {link['stub_defines_dropped']} stub defines deduplicated; relocatable link "
          f"{link['links']['relocatable']['wall_s']} s ok={link['links']['relocatable']['ok']}; program link "
          f"ok={link['links']['program']['ok']} rc={link['links']['program']['rc']}", flush=True)
    if a.phase == "link":
        return
    memory = phase_memory(lift, tu25, force("memory"))
    print(f"memory: g1 max {memory['by_grouping']['g1']['max_mb']} MB median {memory['by_grouping']['g1']['median_mb']} MB; "
          f"g25 max {memory['by_grouping']['g25']['max_mb']} MB median {memory['by_grouping']['g25']['median_mb']} MB "
          f"(probe floor {memory['by_grouping']['g1']['probe_floor_kb']['max']} kB)", flush=True)
    if a.phase == "memory":
        return
    incremental = phase_incremental(sample, lift, pass_, tu25, compile_, force("incremental"))
    for c in incremental["checks"]:
        print(f"incremental {c['address']}: relift identical {c['relift']['identical_to_first_lift']}, g1 object identical {c['g1']['object_identical']} "
              f"({c['g1']['incremental_wall_s']} s), g25 object identical {c.get('g25', {}).get('object_identical')} ({c.get('g25', {}).get('incremental_wall_s')} s)", flush=True)
    if a.phase == "incremental":
        return
    receipt = phase_receipt(sample, lift, pass_, tu25, compile_, link, memory, incremental)
    print(receipt["kill_criterion_1"]["verdict"])
    for t in receipt["projection"]["table"]:
        print(f"  {t['run']:12s} compile {t['full_image_compile_min']:8.2f} min  front-end {t['full_image_front_end_min']:6.2f} min  "
              f"total {t['full_image_lift_pass_compile_link_min']:8.2f} min  objects {t['full_image_objects_mb']} MB")
    for r in receipt["projection"]["by_jobs"]:
        print(f"  N={r['jobs']:<3d} {r['grouping']:4s} lift {r['lift_min']:7.2f}  pass {r['pass_min']:6.2f}  compile {r['compile_min']:7.2f}  "
              f"link {r['link_min']:5.2f}  total {r['total_min']:7.2f} min  = {r['percent_of_threshold']:5.1f} % of 60")
    print(f"receipt: {rel(RECEIPT)}")


if __name__ == "__main__":
    main()
