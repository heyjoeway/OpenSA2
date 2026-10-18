#!/usr/bin/env python3
"""What does S4's runtime addition cost the build?

Subtitle: compile and link the runtime with and without `sa2_imports.c`, three
times each, and write the seconds to JSON -- because G1 kill criterion 1 fires
a pivot away from remill if the whole-image serial build passes 60 minutes and
it currently stands at 50.380, with 9.62 minutes of headroom
(`Documentation/receipts/s3-build-time-v2.json`).

The answer is expected to be "nothing", and "expected" is the reason to
measure it. Two facts have to be established separately:

  1. **The criterion's denominator does not contain this code.**
     `tools/s3/whole_image/build_time_v2.py` times exactly three commands --
     `lift_all.py`, `compile_all.py` and `arms/run_image.py` -- over the 18 394
     lifted modules. No runtime source is compiled in any of them and no link
     happens in any of them. This tool re-reads that file and reports the
     commands it found rather than asserting it from memory, so a later change
     to the criterion's phases shows up here as a different list.
  2. **What the addition does cost** is the compile of one more ~600-line C
     file and one more object on the link line, measured rather than assumed.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/build_cost.py --tag next+allarms [--repeats 3]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

import spikelib as S  # noqa: E402
import tweak_sources as TS  # tools/s4/tweak_sources.py  # noqa: E402

RUNTIME = C.ROOT / "runtime"
OUT = C.GEN / "s4" / "build_cost"
TARGET = ["-target", "x86_64-linux-musl"]
OPT = "-O2"
S3_RUNTIME_C = ["guest_memory.c", "function_table_impl.c", "sa2_trap.c", "sa2_call.c"]


def s4_added_c() -> list[str]:
    """Every runtime source S4 adds, read from the tool that links them.

    This was a hand-kept list. It read `["sa2_imports.c"]` for a slice after
    `sa2_files.c` joined the build and reported that one file's seconds as the
    whole addition -- a number true of a build nobody makes, which is the same
    defect the build-time kill criterion itself had. It is now derived from
    `link_s4.py`'s own `RUNTIME_C`, so a file added there cannot be missing
    here, and a parse that finds nothing refuses instead of reporting zero. The play tweaks'
    files (runtime/tweaks/*.c), which link_s4.py adds to RUNTIME_C after its literal, are
    added here the same way (tools/s4/tweak_sources.py)."""
    src = (C.ROOT / "tools" / "s4" / "link_s4.py").read_text()
    m = re.search(r"RUNTIME_C = \[(.*?)\]", src, re.S)
    if not m:
        raise SystemExit("tools/s4/link_s4.py has no RUNTIME_C list to read")
    names = re.findall(r'"([\w.]+\.c)"', m.group(1))
    added = [n for n in names if n not in S3_RUNTIME_C]
    if not added:
        raise SystemExit("link_s4.py's RUNTIME_C adds nothing to S3's list; that cannot be right")
    return added + TS.sources(RUNTIME)


S4_ADDED_C = s4_added_c()
def runtime_cxx() -> list[str]:
    """The C++ runtime sources, from link_s4.py's RUNTIME_CXX, for the reason
    s4_added_c reads RUNTIME_C: a hand-kept ["remill_runtime.cpp"] left
    sa2_emulate.cpp out of a link the weak reference let succeed."""
    src = (C.ROOT / "tools" / "s4" / "link_s4.py").read_text()
    m = re.search(r"RUNTIME_CXX = \[(.*?)\]", src, re.S)
    names = re.findall(r'"([\w.]+\.cpp)"', m.group(1)) if m else []
    if "remill_runtime.cpp" not in names:
        raise SystemExit("tools/s4/link_s4.py's RUNTIME_CXX does not name remill_runtime.cpp")
    return names


RUNTIME_CXX = runtime_cxx()


def production_wraps() -> list[str]:
    """The lifted functions the runtime wraps in every build, from link_s4.py's
    PRODUCTION_WRAPS, read for the reason s4_added_c reads RUNTIME_C: the link
    with S4's sources compiles runtime/sa2_poll_handoff.c, whose strong
    `__real_sub_427ee0` reference fails a link that omits the flag. The play tweaks' wraps
    follow, as link_s4.py adds them after its literal (tools/s4/tweak_sources.py)."""
    src = (C.ROOT / "tools" / "s4" / "link_s4.py").read_text()
    m = re.search(r"PRODUCTION_WRAPS = \[(.*?)\]", src, re.S)
    if not m:
        raise SystemExit("tools/s4/link_s4.py has no PRODUCTION_WRAPS list to read")
    listed = re.findall(r'"(sub_[0-9a-f]+)"', m.group(1))
    return listed + TS.wraps(RUNTIME, listed)


PRODUCTION_WRAPS = production_wraps()
DRIVER = "harness/seh_probe.c"   # an S3 driver that needs no import symbol, so it
                                 # links with and without the new object


# The cache discipline, which took two wrong measurements to get right.
#
# `zig cc` has two caches and they fail a naive measurement in opposite
# directions. Draft 1 used the ambient cache and reported 0.005 s to compile a
# 600-line C file and 0.004 s for `remill_runtime.cpp`: those are hash lookups
# in the **result** cache, not compiles. Draft 2 gave every invocation a fresh
# cache and reported 6.4 s to link the image: that is real work, but most of it
# is zig rebuilding its own musl and compiler-rt artifacts in the **toolchain**
# cache, which a developer pays once and never again.
#
# So: one private cache directory for the whole tool, warmed once before
# anything is timed, and every timed input made unique so the result cache
# always misses. Measured that way, relinking the image with one object added
# is 0.307 s against 0.281 s for the same inputs again -- which also says the
# S3 receipt's 0.2 s link is a real link and not the cache artefact it looked
# like at 6.4 s. Both wrong numbers are kept in `cache_discipline` below,
# because a tool that only reports the right one cannot be checked.
CACHE_ENV: dict[str, str] = {}


def timed(cmd: list[str], cwd: Path | None = None) -> float:
    env = dict(os.environ, **CACHE_ENV)
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, env=env)
    dt = time.perf_counter() - t0
    if p.returncode != 0:
        raise SystemExit(f"failed: {' '.join(cmd[:6])} ...\n{p.stderr[-3000:]}")
    return dt


def unique_copy(src: Path, tag: str) -> Path:
    """A byte-different copy of `src`, so the result cache cannot answer."""
    dst = Path(CACHE_ENV["SA2_SCRATCH"]) / f"{tag}_{src.name}"
    dst.write_text(src.read_text() + f"\n/* cache-buster {tag} */\n")
    return dst


def criterion_phases() -> tuple[list[str], list[str]]:
    """What `build_time_v2.py` times, and whether any of it touches `runtime/`.

    Claim 1 of this tool -- that the kill criterion's 50.380 minutes contain no
    runtime compilation -- is checked rather than asserted: the tool names the
    scripts it hands to its `run()` timer, and each of those is searched for a
    reference to any file under `runtime/`. A hit would mean the claim is false
    and this tool should say so instead of printing a reassuring sentence.
    """
    src = (C.ROOT / "tools" / "s3" / "whole_image" / "build_time_v2.py").read_text()
    timed = sorted({m for m in re.findall(r'"(tools/[\w/]+\.py)"', src)})
    names = [Path(f).name for f in S3_RUNTIME_C + S4_ADDED_C + RUNTIME_CXX] + ["runtime/"]
    touching = []
    for tool in timed:
        text = (C.ROOT / tool).read_text() if (C.ROOT / tool).is_file() else ""
        hits = sorted({n for n in names if n in text})
        if hits:
            touching.append(f"{tool}: {', '.join(hits)}")
    return timed, touching


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"build_cost.{args.tag}.json"
    cache = tempfile.mkdtemp(prefix="sa2-buildcost-")
    CACHE_ENV.update(ZIG_GLOBAL_CACHE_DIR=str(Path(cache) / "g"),
                     ZIG_LOCAL_CACHE_DIR=str(Path(cache) / "l"),
                     SA2_SCRATCH=str(Path(cache) / "src"))
    Path(CACHE_ENV["SA2_SCRATCH"]).mkdir(parents=True, exist_ok=True)
    _timed_tools, _touching = criterion_phases()
    zig = str(S.ZIG)
    cflags = [zig, "cc"] + TARGET + [OPT, "-w", "-c", "-I", str(RUNTIME), "-I", str(C.REMILL_INC)]
    cxxflags = [zig, "c++"] + TARGET + [OPT, "-w", "-std=c++17", "-c",
                                        "-I", str(RUNTIME), "-I", str(C.REMILL_INC)]
    bdir = OUT / "obj"
    bdir.mkdir(parents=True, exist_ok=True)

    # Warm the toolchain cache before anything is timed: the first invocation
    # in a fresh cache builds musl and compiler-rt, which is a cost nobody pays
    # per build and which swamped draft 2's figures.
    warm_seconds = round(timed(cflags + [str(RUNTIME / "sa2_trap.c"),
                                         "-o", str(bdir / "_warm.o")]), 3)

    doc: dict = {
        "schema": "sa2-recomp-measurement/v1",
        "tag": args.tag,
        "repeats": args.repeats,
        "toolchain": f"zig cc/c++ {' '.join(TARGET)} {OPT}",
        "criterion": {
            "id": "G1-1-build-time",
            "receipt": "Documentation/receipts/s3-build-time-v2.json",
            "tools_timed_by_build_time_v2": _timed_tools,
            "runtime_sources_in_those_phases": _touching,
        },
        "cache_discipline": {
            "toolchain_warm_seconds": warm_seconds,
            "withdrawn_draft_1": "ambient cache: 0.005 s per runtime source, 0.004 s for "
                                 "remill_runtime.cpp -- result-cache hits, not compiles",
            "withdrawn_draft_2": "a fresh cache per invocation: 6.416 s and 6.530 s to link, "
                                 "dominated by zig rebuilding musl and compiler-rt; the 0.114 s "
                                 "difference between them is noise on a 6.4 s base",
            "method": "one private cache for the whole run, warmed once before anything is "
                      "timed, and every timed input made byte-unique so the result cache misses",
        },
        "compile_seconds": {},
        "link_seconds": {},
    }
    path.write_text(json.dumps(doc, indent=1) + "\n")   # checkpoint before any work

    # 1. compile each runtime source, fresh, `repeats` times
    for src in S3_RUNTIME_C + S4_ADDED_C + RUNTIME_CXX:
        flags = cxxflags if src.endswith(".cpp") else cflags
        o = bdir / (Path(src).stem + ".o")
        runs = []
        for i in range(args.repeats):
            if o.exists():
                o.unlink()
            uniq = unique_copy(RUNTIME / src, f"c{i}")
            runs.append(round(timed(flags + [str(uniq), "-o", str(o)]), 3))
        cached = round(timed(flags + [str(RUNTIME / src), "-o", str(o)]), 3)
        cached = round(timed(flags + [str(RUNTIME / src), "-o", str(o)]), 3)
        doc["compile_seconds"][src] = {
            "runs": runs, "min": min(runs), "mean": round(statistics.mean(runs), 3),
            "result_cache_hit_seconds": cached,
            "object_bytes": o.stat().st_size,
            "added_by_s4": src in S4_ADDED_C,
        }
        path.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"compile {src}: min {min(runs)}s, object {o.stat().st_size} bytes", flush=True)

    # 2. link the same driver with and without the new object
    table_c = C.GEN / "s3" / "link" / args.tag / "function_table_whole.c"
    for src, path_ in (("function_table_whole.c", table_c), ("driver.c", C.ROOT / DRIVER)):
        o = bdir / (Path(src).stem + ".o")
        if not o.exists():
            timed(cflags + [str(path_), "-o", str(o)])
    objdir = (C.BUILD / args.tag).resolve()
    objs = sorted(p.name for p in objdir.glob("ir_*.o"))
    doc["link_objects_lifted"] = len(objs)
    for label, extra in (("without_sa2_imports", []), ("with_sa2_imports", S4_ADDED_C)):
        base = [str(bdir / (Path(s).stem + ".o"))
                for s in S3_RUNTIME_C + extra + RUNTIME_CXX
                + ["function_table_whole.c", "driver.c"]]
        # The runtime's own wraps go with the runtime source that defines them.
        wraps = [f"-Wl,-wrap={w}" for w in PRODUCTION_WRAPS] if extra else []
        runs = []
        for i in range(args.repeats):
            # A different output path each time, so no run can be answered from
            # a previous one's result.
            binary = OUT / f"sa2_{label}_{i}"
            if binary.exists():
                binary.unlink()
            runs.append(round(timed([zig, "c++"] + TARGET + [OPT] + wraps + base + objs
                                    + ["-o", str(binary.resolve())], cwd=objdir), 3))
        doc["link_seconds"][label] = {
            "runs": runs, "min": min(runs), "mean": round(statistics.mean(runs), 3),
            "runtime_objects": len(base), "binary_bytes": binary.stat().st_size,
        }
        path.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"link {label}: min {min(runs)}s, binary {binary.stat().st_size} bytes", flush=True)

    a = doc["link_seconds"]["without_sa2_imports"]
    b = doc["link_seconds"]["with_sa2_imports"]
    doc["cleanup"] = shutil.rmtree(cache, ignore_errors=True) or "cache removed"
    doc["delta"] = {
        # Every file S4 adds, not just the first one. This read
        # `doc["compile_seconds"]["sa2_imports.c"]["min"]` while S4_ADDED_C had
        # one entry, and went on reporting that one file's 0.362 s after
        # `sa2_files.c` joined the list -- a number that was true of a build
        # nobody was making any more, which is the same defect the build-time
        # kill criterion itself had.
        "compile_seconds_added": round(
            sum(doc["compile_seconds"][n]["min"] for n in S4_ADDED_C), 3),
        "compile_seconds_added_by_file": {
            n: doc["compile_seconds"][n]["min"] for n in S4_ADDED_C},
        "link_seconds_added": round(b["min"] - a["min"], 3),
        "binary_bytes_added": b["binary_bytes"] - a["binary_bytes"],
        "criterion_minutes_added": 0.0,
        "why_zero": ("build_time_v2.py times " + ", ".join(_timed_tools) + " over the 18 394 "
                     "lifted modules. None of them references any file under runtime/ "
                     + ("-- checked, not asserted." if not _touching
                        else "-- EXCEPT: " + "; ".join(_touching) + ", so this claim is FALSE.")
                     + " No second of the figures above is inside the 50.380 minutes the "
                       "criterion measures."),
        "claim_1_holds": not _touching,
    }
    # 3. the part of the criterion this stage *does* spend: every address
    #    `tools/s4/lift_extra.py` lifts is one more module in exactly the phase
    #    `build_time_v2.py` times. The per-module serial rate is the one
    #    measured in the CRT slice's receipt rather than re-measured here, and
    #    the receipt is named so the arithmetic can be checked against it.
    crt = json.loads((C.ROOT / "Documentation" / "receipts"
                      / "s4-crt-startup-v1.json").read_text())
    eb = crt["build_time"]["extra_block_lift"]
    state = C.GEN / "s4" / "link" / f"extra_functions.{args.tag}.json"
    now_modules = len(json.loads(state.read_text())["objects"]) if state.is_file() else 0
    per_module_s = (eb["lift_seconds_serial"] + eb["compile_seconds_serial"]) / eb["modules"]
    minutes_now = now_modules * per_module_s / 60.0
    total = eb["projected_total_minutes"] - eb["minutes_serial"] + minutes_now
    doc["extra_blocks"] = {
        "measured_in": "Documentation/receipts/s4-crt-startup-v1.json",
        "measured_modules": eb["modules"],
        "measured_minutes_serial": eb["minutes_serial"],
        "per_module_seconds_serial": round(per_module_s, 4),
        "modules_now": now_modules,
        "modules_added_by_this_slice": now_modules - eb["modules"],
        "minutes_serial_now": round(minutes_now, 3),
        "minutes_added_by_this_slice": round(minutes_now - eb["minutes_serial"], 4),
        "baseline_minutes": crt["build_time"]["baseline_minutes"],
        "projected_total_minutes": round(total, 3),
        "projected_percent_of_threshold": round(100.0 * total / 60.0, 2),
        "headroom_minutes": round(60.0 - total, 3),
    }
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc["delta"], indent=1))
    print(json.dumps(doc["extra_blocks"], indent=1))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
