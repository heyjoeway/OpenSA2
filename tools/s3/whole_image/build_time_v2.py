#!/usr/bin/env python3
"""G1 kill criterion 1, re-measured on the image that actually ships.

Subtitle: a serial (`--jobs 1`) stratified sample over **both** populations of
the shipping build -- the 12 683 map-function modules and the 5 711 jump-table
arm traces -- projected to the whole image, plus a direct whole-image serial
run that the projection is checked against.

Why this tool exists
--------------------

`Documentation/receipts/s3-whole-image-lift-v1.json` publishes
`timings.serial_sample.projected_minutes_total = 33.352`, 55.6 % of the
criterion's 60-minute threshold. That figure is a projection of a build nobody
links, in two separate ways:

  * it samples **only the function map**. S3 then seeded every jump-table arm
    as its own lifted trace (`Documentation/decisions/2026-09-20-G3-jump-table-arms.md`),
    so the shipping build is 18 394 modules, not 12 683. Arms are 31 % of the
    modules and are shaped differently -- shorter, entered mid-function, more
    exits per decoded instruction -- so the map's per-instruction rate is not
    theirs to lend.
  * it was lifted on the **`map` bound** (`to_next_entry: false`), and the
    adopted bound is `--to-next-entry` (that receipt's own `to_next_entry`
    section decides it). The wider bound decodes more per module, and its
    16-job wall is 11 % above the `map` arm's.

Scaling 33.352 by IR volume or by module count is not a measurement, and the
number decides whether the project abandons remill. So it is measured.

Stratification, and why
-----------------------

The population is the shipping build's 18 394 modules: 12 683 map entries
(69 %) and 5 711 arm traces (31 %). They are sampled and scaled **as two
strata**, 1-in-16 systematic in address order within each, and projected
separately before being added:

  * 1-in-16 in both keeps the sample's mix at the population's 69/31, and
    makes the map stratum *exactly* the 793 entries the existing receipt
    sampled -- so the two figures are comparable line for line;
  * systematic in address order, not random, because that is what the existing
    sample did and because address order in this image is close to build order,
    which spreads CRT, CRI and game code through the sample rather than
    clustering it;
  * scaled **separately**, because the two strata have no common size
    variable. The map's scaling variable is the analyzer's static instruction
    count per entry, which arm traces do not have a row for; the arms' is
    remill's own decoded-instruction marker per trace, which the map modules'
    log does not record. Adding a map instruction to an arm marker to get one
    denominator would be a ratio spanning two universes -- the defect the G3
    second interrogation already caught in this project's coverage metric
    (commit development_snapshot). Two strata, two ratios, one sum.

What is measured, and what is refused
-------------------------------------

Every phase wall here is a **tool wall at `--jobs 1`**: one worker, no
contention. Summing per-function walls from a 16-job run is the artefact the
G2 interrogation named (two reviewers derived 53.4 min that way against a
30.15 min direct measurement); this tool never reads a 16-job per-function
sum, and the one place it reports per-record seconds -- splitting the arm
stratum's single tool wall into its lift+pass and fold+compile halves -- reads
them from a **1-job** run, where there is no contention to inflate them, and
uses them only as a ratio of that run's own directly measured wall.

Usage
-----

    source tools/env.sh
    .venv/bin/python tools/s3/whole_image/build_time_v2.py sample
    .venv/bin/python tools/s3/whole_image/build_time_v2.py full
    .venv/bin/python tools/s3/whole_image/build_time_v2.py receipt

`sample` and `full` both resume: the underlying tools skip modules already
done under their tag, so a killed run is restarted with the same command.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "arms"))
import common as C  # noqa: E402

ARMS_OUT = C.GEN / "s3" / "arms"
OUT = C.GEN / "s3" / "build_time_v2"
PY = str(C.ROOT / ".venv" / "bin" / "python")

SAMPLE_TAG = "btv2sample"
FULL_TAG = "btv2full"
STRIDE = 16
THRESHOLD_MINUTES = 60.0

SHIPPING_MAP_TAG = "next"       # the arm of the whole-image build that ships
SHIPPING_ARMS_LOG = ARMS_OUT / "arms.next.jsonl"
SHIPPING_ARMLIST = ARMS_OUT / "armlist.next.json"


# --------------------------------------------------------------------------
# peak memory over the whole process tree
# --------------------------------------------------------------------------
def _tree_memory_class():
    """`lift_arms_image.TreeMemory`, imported rather than re-written.

    It is the sampler the arm slice already uses: `/proc`-walked RSS summed
    over this process and every descendant every two seconds, because
    `ru_maxrss` is the single worst child and says nothing about a pool.
    """
    import lift_arms_image
    return lift_arms_image.TreeMemory


# --------------------------------------------------------------------------
# populations
# --------------------------------------------------------------------------
def map_population() -> tuple[list[int], dict[int, int]]:
    """`(addresses in order, address -> analyzer instruction count)`."""
    fm = C.by_address()
    order = sorted(fm)
    return order, {a: int(fm[a].get("instructions") or 0) for a in order}


def arm_population() -> tuple[list[dict], dict[str, dict]]:
    """`(the shipping work list in order, arm -> its shipping build record)`.

    The record supplies `pc_markers_in_define`, remill's own count of decoded
    instructions plus exit blocks in the trace's define -- the arm stratum's
    size variable. It is an output of the shipping build rather than a static
    analyzer field, which `limits` says out loud.
    """
    work = json.loads(SHIPPING_ARMLIST.read_text())["work"]
    recs = C.read_jsonl(SHIPPING_ARMS_LOG)
    return work, recs


def sample_indices(n: int) -> list[int]:
    return list(range(0, n, STRIDE))


# --------------------------------------------------------------------------
# running a phase
# --------------------------------------------------------------------------
def run(cmd: list[str], label: str) -> dict:
    """Run one phase to completion, timing it and sampling the process tree."""
    TreeMemory = _tree_memory_class()
    mem = TreeMemory()
    mem.start()
    t0 = time.time()
    p = subprocess.run(cmd, cwd=str(C.ROOT))
    wall = time.time() - t0
    mem.stop()
    rec = {
        "label": label,
        "command": " ".join(cmd),
        "returncode": p.returncode,
        "wall_seconds": round(wall, 2),
        "peak_process_tree_kb": mem.peak_tree_kb,
        "worst_single_process_kb": mem.peak_single_kb,
        "samples": mem.samples,
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if p.returncode != 0:
        raise SystemExit(f"{label} failed with rc={p.returncode}: {' '.join(cmd)}")
    return rec


def record_phase(name: str, rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "phases.jsonl").open("a") as fh:
        fh.write(json.dumps({**rec, "phase": name}, sort_keys=True) + "\n")
    (OUT / f"{name}.json").write_text(json.dumps(rec, indent=1) + "\n")
    print(f"[{name}] {rec['wall_seconds']} s, tree peak "
          f"{rec['peak_process_tree_kb'] / 1e6:.2f} GB", flush=True)


PHASE_NAMES = ("sample_map_lift", "sample_map_compile", "sample_arms",
               "full_map_lift", "full_map_compile", "full_arms",
               "parallel_map_lift", "parallel_map_compile", "parallel_arms")


def phases() -> dict[str, dict]:
    """The phase records, by name -- and nothing else in the directory.

    Globbing `*.json` here swept the sample's own armlist into the receipt and
    put 357 arm rows inside a timing block. The phase names are the phases.
    """
    out: dict[str, dict] = {}
    for name in PHASE_NAMES:
        path = OUT / f"{name}.json"
        if path.is_file():
            rec = json.loads(path.read_text())
            rec["command"] = shorten_only(rec.get("command", ""))
            out[name] = rec
    return out


ONLY_REPLACEMENT = ("--only <793 addresses: every 16th map entry in address order, "
                    "listed in private/generated/s3/build_time_v2/"
                    "sample_map_addresses.json, which this receipt pins>")


def shorten_only(command: str) -> str:
    """The sample lift's 793 `--only` addresses, by reference.

    `lift_all.py` takes the sample on its command line, which put thirteen
    kilobytes of hex into the receipt and buried everything else. The list is
    written to `sample_map_addresses.json` by this tool, that file is hashed in
    `inputs`, and the command names it. Nothing is lost: the command is still
    reproducible, and now the receipt is still readable.
    """
    head, sep, _ = command.partition("--only ")
    return f"{head}{ONLY_REPLACEMENT}" if sep else command


# --------------------------------------------------------------------------
# the two runs
# --------------------------------------------------------------------------
def write_sample_armlist(dest: Path) -> dict:
    doc = json.loads(SHIPPING_ARMLIST.read_text())
    work = doc["work"]
    picked = [work[i] for i in sample_indices(len(work))]
    out = {
        "tag": SAMPLE_TAG,
        "derived_from": str(SHIPPING_ARMLIST.relative_to(C.ROOT)),
        "stride": STRIDE,
        "population": len(work),
        "work": picked,
        # lift_arms_image.py refuses an armlist that did not pass its own link
        # assertion. This one is a subset of a list that passed, taken without
        # reordering, so it inherits the assertion rather than claiming a new
        # one -- and says which.
        "assertion": {**doc["assertion"],
                      "inherited_from": str(SHIPPING_ARMLIST.relative_to(C.ROOT)),
                      "note": "a 1-in-16 subset of a work list that passed its own "
                              "assertion; no arm was added, reordered or re-anchored"},
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1) + "\n")
    return out


def do_sample() -> None:
    order, _ = map_population()
    picked = [order[i] for i in sample_indices(len(order))]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sample_map_addresses.json").write_text(json.dumps(
        {"stride": STRIDE, "population": len(order), "sample": len(picked),
         "addresses": [f"0x{a:08x}" for a in picked]}, indent=1) + "\n")
    armlist = OUT / f"armlist.{SAMPLE_TAG}.json"
    doc = write_sample_armlist(armlist)
    print(f"sample: {len(picked)} map entries, {len(doc['work'])} arm traces", flush=True)

    record_phase("sample_map_lift", run(
        [PY, "tools/s3/whole_image/lift_all.py", "--jobs", "1", "--tag", SAMPLE_TAG,
         "--to-next-entry", "--only", *[f"0x{a:08x}" for a in picked]],
        "sample map lift+pass, --jobs 1, --to-next-entry"))
    record_phase("sample_map_compile", run(
        [PY, "tools/s3/whole_image/compile_all.py", "--jobs", "1", "--tag", SAMPLE_TAG],
        "sample map fold+compile, --jobs 1"))
    record_phase("sample_arms", run(
        [PY, "tools/s3/arms/lift_arms_image.py", "--jobs", "1", "--tag", SAMPLE_TAG,
         "--armlist", str(armlist)],
        "sample arm traces lift+pass+fold+compile, --jobs 1"))


def do_full() -> None:
    record_phase("full_map_lift", run(
        [PY, "tools/s3/whole_image/lift_all.py", "--jobs", "1", "--tag", FULL_TAG,
         "--to-next-entry"],
        "whole-image map lift+pass, --jobs 1, --to-next-entry"))
    record_phase("full_map_compile", run(
        [PY, "tools/s3/whole_image/compile_all.py", "--jobs", "1", "--tag", FULL_TAG],
        "whole-image map fold+compile, --jobs 1"))
    record_phase("full_arms", run(
        [PY, "tools/s3/arms/lift_arms_image.py", "--jobs", "1", "--tag", FULL_TAG,
         "--armlist", str(SHIPPING_ARMLIST)],
        "whole-image arm traces lift+pass+fold+compile, --jobs 1"))


def do_parallel() -> None:
    """The 16-job whole-build wall: the thing a person actually waits for.

    A different question from the criterion's, measured because the receipt
    should not make the reader guess it. Runs under its own tags so it cannot
    resume off the serial run's output.
    """
    tag = "btv2par"
    record_phase("parallel_map_lift", run(
        [PY, "tools/s3/whole_image/lift_all.py", "--jobs", "16", "--tag", tag,
         "--to-next-entry"], "whole-image map lift+pass, --jobs 16, --to-next-entry"))
    record_phase("parallel_map_compile", run(
        [PY, "tools/s3/whole_image/compile_all.py", "--jobs", "16", "--tag", tag],
        "whole-image map fold+compile, --jobs 16"))
    record_phase("parallel_arms", run(
        [PY, "tools/s3/arms/lift_arms_image.py", "--jobs", "16", "--tag", tag,
         "--armlist", str(SHIPPING_ARMLIST)],
        "whole-image arm traces lift+pass+fold+compile, --jobs 16"))


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------
def tool_wall(path: Path) -> dict:
    """A tool's own wall file -- the measurement, not the driver's stopwatch.

    The superseded receipt's serial figures are these files' `wall_seconds`, so
    reading the same files is what makes the two comparable. The driver's own
    stopwatch is kept in `phases` beside them and includes interpreter startup.
    """
    if not path.exists():
        raise SystemExit(f"missing wall file {path}; run the phase first")
    return json.loads(path.read_text())


def arm_phase_split(tag: str) -> dict:
    """The lift+pass / fold+compile split of an arm run's single wall.

    `lift_arms_image.py` reports one wall for a pipeline the map's half runs as
    two tools. At `--jobs 1` the per-record seconds are uncontended, so their
    ratio divides that run's directly measured wall. The seconds are a share,
    never a total: the wall stays the measurement.

    `lift_arms.fold_and_compile` times the compile and not the fold, so the
    fold sits in the residual between the wall and the summed per-record
    seconds, together with IR writing and pool overhead. Multiplying the wall
    by a share allocates that residual in proportion, which is the assumption
    made here and stated in `limits`; the residual is reported so its size is
    visible rather than argued.
    """
    recs = C.read_jsonl(ARMS_OUT / f"arms.{tag}.jsonl")
    wall = tool_wall(ARMS_OUT / f"arms.{tag}.wall.json")["wall_seconds"]
    lift = sum(r.get("lift_seconds", 0) + (r.get("pass_seconds") or 0)
               for r in recs.values())
    comp = sum(r.get("compile_seconds") or 0 for r in recs.values())
    total = lift + comp
    return {"records": len(recs),
            "tool_wall_seconds": wall,
            "per_record_lift_and_pass_seconds": round(lift, 2),
            "per_record_compile_seconds": round(comp, 2),
            "per_record_total_seconds": round(total, 2),
            "residual_seconds_fold_and_overhead": round(wall - total, 2),
            "residual_percent_of_wall": round(100 * (wall - total) / wall, 2) if wall else None,
            "share_lift_and_pass": round(lift / total, 5) if total else None,
            "share_fold_and_compile": round(comp / total, 5) if total else None}


def alternative_scalings(map_sample: list[int], arm_sample: list[str],
                         order: list[int], work: list[dict],
                         map_seconds: float, arm_seconds: float) -> dict:
    """The same two walls scaled by two other size variables.

    A ratio estimator is only as good as the assumption that cost tracks the
    variable chosen. Scaling the identical measured walls by module count and
    by IR lines -- both known for the whole population from the shipping build
    -- says how much of the answer is the measurement and how much is the
    choice of denominator. These are a sensitivity check, not the headline.
    """
    map_ship = C.read_jsonl(C.LIFT / f"{SHIPPING_MAP_TAG}.jsonl")
    arm_ship = C.read_jsonl(SHIPPING_ARMS_LOG)
    map_keys = {f"0x{a:08x}" for a in map_sample}
    map_ll_pop = sum(r.get("ll_lines", 0) for r in map_ship.values())
    map_ll_sam = sum(r.get("ll_lines", 0) for k, r in map_ship.items() if k in map_keys)
    arm_keys = set(arm_sample)
    arm_ll_pop = sum(arm_ship[w["arm"]].get("ll_lines", 0) for w in work)
    arm_ll_sam = sum(arm_ship[a].get("ll_lines", 0) for a in arm_keys)

    def total(k_map: float, k_arm: float) -> float:
        return (map_seconds * k_map + arm_seconds * k_arm) / 60

    by_count = total(len(order) / len(map_sample), len(work) / len(arm_sample))
    by_ir = total(map_ll_pop / map_ll_sam, arm_ll_pop / arm_ll_sam)
    return {
        "by_module_count": {
            "map_scale": round(len(order) / len(map_sample), 4),
            "arm_scale": round(len(work) / len(arm_sample), 4),
            "projected_minutes_total": round(by_count, 3),
            "percent_of_60_minute_threshold": round(100 * by_count / THRESHOLD_MINUTES, 2)},
        "by_ir_lines_of_the_shipping_build": {
            "map_scale": round(map_ll_pop / map_ll_sam, 4),
            "arm_scale": round(arm_ll_pop / arm_ll_sam, 4),
            "map_population_ir_lines": map_ll_pop, "map_sample_ir_lines": map_ll_sam,
            "arm_population_ir_lines": arm_ll_pop, "arm_sample_ir_lines": arm_ll_sam,
            "projected_minutes_total": round(by_ir, 3),
            "percent_of_60_minute_threshold": round(100 * by_ir / THRESHOLD_MINUTES, 2)},
        "why": "the headline scales by instruction count because that is the variable "
               "the superseded receipt scaled by; these two say what the answer would "
               "have been under a different denominator, so the reader can see the "
               "estimate is the measurement and not the choice",
    }


def projection() -> dict:
    ph = phases()
    order, ins = map_population()
    work, shipping_arms = arm_population()

    map_sample = [order[i] for i in sample_indices(len(order))]
    map_sample_ins = sum(ins[a] for a in map_sample)
    map_total_ins = sum(ins.values())

    arm_sample = [work[i]["arm"] for i in sample_indices(len(work))]
    markers = {a: int(shipping_arms[a]["pc_markers_in_define"]) for a in shipping_arms}
    arm_sample_markers = sum(markers[a] for a in arm_sample)
    arm_total_markers = sum(markers[w["arm"]] for w in work)

    k_map = map_total_ins / map_sample_ins
    k_arm = arm_total_markers / arm_sample_markers

    split = arm_phase_split(SAMPLE_TAG)
    arm_wall = split["tool_wall_seconds"]
    map_lift = tool_wall(C.LIFT / f"{SAMPLE_TAG}.wall.json")["wall_seconds"]
    map_comp = tool_wall(C.BUILD / f"{SAMPLE_TAG}.wall.json")["wall_seconds"]

    lift_s = map_lift * k_map + arm_wall * split["share_lift_and_pass"] * k_arm
    comp_s = map_comp * k_map + arm_wall * split["share_fold_and_compile"] * k_arm
    total_s = lift_s + comp_s

    alt = alternative_scalings(map_sample, arm_sample, order, work,
                               map_lift + map_comp, arm_wall)

    return {
        "alternative_scalings": alt,
        "method": "two strata, 1-in-16 systematic in address order within each, each "
                  "scaled to its own population by its own size variable, then added; "
                  "every phase wall is a --jobs 1 tool wall",
        "strata": {
            "map_modules": {
                "population": len(order), "sample": len(map_sample),
                "size_variable": "function-map `instructions` (the analyzer's static "
                                 "instruction count per entry), the variable "
                                 "Documentation/receipts/s3-whole-image-lift-v1.json "
                                 "scaled by",
                "population_size": map_total_ins, "sample_size": map_sample_ins,
                "scale": round(k_map, 4),
                "lift_and_pass_wall_seconds": map_lift,
                "fold_and_compile_wall_seconds": map_comp,
                "projected_minutes_lift_and_pass": round(map_lift * k_map / 60, 3),
                "projected_minutes_fold_and_compile": round(map_comp * k_map / 60, 3),
            },
            "arm_traces": {
                "population": len(work), "sample": len(arm_sample),
                "size_variable": "remill's `store i32 %k, ptr %PC` markers in the "
                                 "trace's own define (decoded instructions plus exit "
                                 "blocks), from the shipping arm build's log; arm "
                                 "traces have no function-map row and so no static "
                                 "instruction count",
                "population_size": arm_total_markers, "sample_size": arm_sample_markers,
                "scale": round(k_arm, 4),
                "wall_seconds": arm_wall,
                "phase_split": split,
                "projected_minutes_lift_and_pass": round(
                    arm_wall * split["share_lift_and_pass"] * k_arm / 60, 3),
                "projected_minutes_fold_and_compile": round(
                    arm_wall * split["share_fold_and_compile"] * k_arm / 60, 3),
            },
        },
        "projected_minutes_lift_and_pass": round(lift_s / 60, 3),
        "projected_minutes_fold_and_compile": round(comp_s / 60, 3),
        "projected_minutes_total": round(total_s / 60, 3),
        "percent_of_60_minute_threshold": round(100 * total_s / 60 / THRESHOLD_MINUTES, 2),
        "fires": total_s / 60 > THRESHOLD_MINUTES,
    }


def direct_serial() -> dict | None:
    ph = phases()
    need = ("full_map_lift", "full_map_compile", "full_arms")
    if not all(k in ph for k in need):
        return None
    split = arm_phase_split(FULL_TAG)
    arm_wall = split["tool_wall_seconds"]
    map_lift = tool_wall(C.LIFT / f"{FULL_TAG}.wall.json")["wall_seconds"]
    map_comp = tool_wall(C.BUILD / f"{FULL_TAG}.wall.json")["wall_seconds"]
    lift_s = map_lift + arm_wall * split["share_lift_and_pass"]
    comp_s = map_comp + arm_wall * split["share_fold_and_compile"]
    total_s = lift_s + comp_s
    return {
        "what": "no projection: every one of the 18 394 shipping modules lifted, passed, "
                "folded and compiled at --jobs 1, and the three tool walls added",
        "map_lift_and_pass_wall_seconds": map_lift,
        "map_fold_and_compile_wall_seconds": map_comp,
        "arm_wall_seconds": arm_wall,
        "arm_phase_split": split,
        "minutes_lift_and_pass": round(lift_s / 60, 3),
        "minutes_fold_and_compile": round(comp_s / 60, 3),
        "minutes_total": round(total_s / 60, 3),
        "percent_of_60_minute_threshold": round(100 * total_s / 60 / THRESHOLD_MINUTES, 2),
        "fires": total_s / 60 > THRESHOLD_MINUTES,
    }


def parallel_wall() -> dict | None:
    ph = phases()
    need = ("parallel_map_lift", "parallel_map_compile", "parallel_arms")
    if not all(k in ph for k in need):
        return None
    tag = "btv2par"
    map_lift = tool_wall(C.LIFT / f"{tag}.wall.json")["wall_seconds"]
    map_comp = tool_wall(C.BUILD / f"{tag}.wall.json")["wall_seconds"]
    arm_wall = tool_wall(ARMS_OUT / f"arms.{tag}.wall.json")["wall_seconds"]
    total = map_lift + map_comp + arm_wall
    return {
        "what": "the same 18 394 modules at --jobs 16 on 8 physical cores: what a person "
                "waits for, which is NOT what the criterion asks about",
        "map_lift_and_pass_wall_seconds": map_lift,
        "map_fold_and_compile_wall_seconds": map_comp,
        "arm_wall_seconds": arm_wall,
        "minutes_total": round(total / 60, 3),
        "percent_of_60_minute_threshold": round(100 * total / 60 / THRESHOLD_MINUTES, 2),
        "speedup_over_serial_direct": None,
    }


def counts() -> dict:
    """Module counts, from the logs rather than from the prose."""
    order, _ = map_population()
    work, _ = arm_population()
    return {"map_modules": len(order), "arm_trace_modules": len(work),
            "shipping_modules": len(order) + len(work),
            "arm_share_percent": round(100 * len(work) / (len(order) + len(work)), 2)}


# --------------------------------------------------------------------------
# the receipt
# --------------------------------------------------------------------------
PIPELINE = (
    "tools/s3/whole_image/build_time_v2.py",
    "tools/s3/whole_image/lift_all.py",
    "tools/s3/whole_image/compile_all.py",
    "tools/s3/whole_image/common.py",
    "tools/s3/arms/lift_arms_image.py",
    "tools/s3/arms/lift_arms.py",
    "tools/s3/arms/armlist.py",
    "tools/spike/pc24pass/pc24pass.py",
    "tools/s2/corpus_run/internalize.py",
)


def verdict(proj: dict, direct: dict | None) -> str:
    best = direct if direct else proj
    minutes = best["minutes_total"] if direct else best["projected_minutes_total"]
    basis = ("a direct whole-image serial run" if direct
             else "a 1-in-16 stratified serial sample")
    fired = "FIRES" if minutes > THRESHOLD_MINUTES else "does not fire"
    return (
        f"G1 kill criterion 1 {fired}. The shipping image -- {counts()['map_modules']} "
        f"map-function modules and {counts()['arm_trace_modules']} jump-table arm traces, "
        f"{counts()['shipping_modules']} modules -- lifts, passes, folds and compiles in "
        f"{minutes:.3f} wall minutes at --jobs 1 on this machine, measured by {basis}; "
        f"that is {100 * minutes / THRESHOLD_MINUTES:.2f} % of the criterion's 60-minute "
        f"threshold. The 1-in-16 stratified projection reads "
        f"{proj['projected_minutes_total']:.3f} minutes "
        f"({proj['percent_of_60_minute_threshold']:.2f} %), against which the direct run "
        f"is the check. Documentation/receipts/s3-whole-image-lift-v1.json's "
        f"timings.serial_sample published 33.352 minutes (55.59 %); that figure is a "
        f"projection of the map-bound, arms-free build of 12 683 modules and is superseded "
        f"for this criterion by this receipt. It is not withdrawn: it remains a correct "
        f"measurement of the build it measured.")


def do_receipt(out: Path) -> None:
    proj = projection()
    direct = direct_serial()
    par = parallel_wall()
    if direct and par:
        par["speedup_over_serial_direct"] = round(
            direct["minutes_total"] / par["minutes_total"], 2)

    falsification = None
    if direct:
        err = (proj["projected_minutes_total"] - direct["minutes_total"]) / direct["minutes_total"]
        falsification = {
            "what": "the 1-in-16 stratified projection against the directly measured "
                    "whole-image serial run of the same pipeline on the same machine",
            "projected_minutes_total": proj["projected_minutes_total"],
            "measured_minutes_total": direct["minutes_total"],
            "relative_error_percent": round(100 * err, 2),
            "would_have_failed_if": "the projection and the direct run disagreed about "
                                    "whether the total is above or below 60 minutes, or "
                                    "differed by more than 15 %",
            "passed": abs(err) <= 0.15 and (
                (proj["projected_minutes_total"] > THRESHOLD_MINUTES)
                == (direct["minutes_total"] > THRESHOLD_MINUTES)),
        }

    ph = phases()

    def peak(prefix: str) -> int:
        return max([p.get("peak_process_tree_kb", 0)
                    for k, p in ph.items() if k.startswith(prefix)] or [0])

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s3-build-time-v2",
        "date": time.strftime("%Y-%m-%d"),
        "stage": "S3 (re-measured during S4; the criterion is not stage-bound)",
        "question": "G1 kill criterion 1: does the projected full-image lift+compile "
                    "exceed 60 wall minutes on this machine? Re-measured on the image "
                    "that ships, which is 18 394 modules including the jump-table arm "
                    "traces S3 seeded, rather than the 12 683-module arms-free build the "
                    "published projection measured.",
        "supersedes": {
            "receipt": "Documentation/receipts/s3-whole-image-lift-v1.json",
            "path": "timings.serial_sample",
            "for": "G1 kill criterion 1 only",
            "why": "that block projects the `map`-bound, arms-free build of 12 683 "
                   "modules: a build nobody links. It is a correct measurement of what "
                   "it measured and is not edited or withdrawn; this receipt is what the "
                   "criterion is evaluated against from here.",
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s3/whole_image/build_time_v2.py sample",
            ".venv/bin/python tools/s3/whole_image/build_time_v2.py full",
            ".venv/bin/python tools/s3/whole_image/build_time_v2.py parallel",
            ".venv/bin/python tools/s3/whole_image/build_time_v2.py receipt",
        ],
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 5 (S3), section 3.4"],
            "criterion": {
                "record": "Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md",
                "section": "### 1.4 Kill criteria (each fires a pivot to the custom emitter)",
                "item": 1,
                "quote": "S2 lifts and compiles a 500-function sample drawn from the map, "
                         "stratified by size; if the projected full-image lift+compile "
                         "exceeds 60 wall minutes on this machine, pivot.",
                "threshold_minutes": THRESHOLD_MINUTES,
                "threshold_is_the_users": "the threshold is not this receipt's to move; "
                                          "this receipt changes only which build it is "
                                          "measured against",
            },
            "records_built_on": [
                "Documentation/receipts/s3-whole-image-lift-v1.json",
                "Documentation/receipts/s2-build-time-sample-v1.json",
                "Documentation/decisions/2026-09-20-G3-jump-table-arms.md",
                "agents-journals/gates/2026-09-20-g2.md",
            ],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": C.sha256(C.EXE)},
            "function_map": {"path": "private/generated/function-map.json",
                             "sha256": C.sha256(C.MAP),
                             "functions": len(C.functions())},
            "sample_map_addresses": {
                "path": "private/generated/s3/build_time_v2/sample_map_addresses.json",
                "sha256": C.sha256(OUT / "sample_map_addresses.json"),
                "addresses": 793,
                "rule": "every 16th entry of the function map in address order",
            },
            "sample_arm_work_list": {
                "path": "private/generated/s3/build_time_v2/armlist.btv2sample.json",
                "sha256": C.sha256(OUT / f"armlist.{SAMPLE_TAG}.json"),
                "traces": 357,
                "rule": "every 16th row of the shipping arm work list in its own order",
            },
            "arm_work_list": {
                "path": str(SHIPPING_ARMLIST.relative_to(C.ROOT)),
                "sha256": C.sha256(SHIPPING_ARMLIST),
                "traces": len(arm_population()[0]),
            },
            "helpers": {
                "pc_round.ll": {"path": "private/generated/spike/pc24pass/pc_round.ll",
                                "sha256": C.sha256(C.HELPER_LL)},
                "x87_int.ll": {"path": "private/generated/s2/corpus_run/helper/x87_int.ll",
                               "sha256": C.sha256(C.INT_HELPER_LL)},
            },
            "pipeline": {p: C.sha256(C.ROOT / p) for p in PIPELINE},
        },
        "machine": {"nproc": os.cpu_count(), "physical_cores": 8,
                    "note": "16 hardware threads over 8 physical cores; every serial "
                            "figure here used one of them"},
        "population": counts(),
        # The one block the ledger reads. It carries the best measurement
        # available -- the direct whole-image serial run when there is one, the
        # stratified projection otherwise -- and says which, so a check pointed
        # here never has to choose between two keys that mean different things.
        "kill_criterion_1": {
            "minutes_total": (direct["minutes_total"] if direct
                              else proj["projected_minutes_total"]),
            "percent_of_60_minute_threshold": (
                direct["percent_of_60_minute_threshold"] if direct
                else proj["percent_of_60_minute_threshold"]),
            "threshold_minutes": THRESHOLD_MINUTES,
            "fires": (direct["fires"] if direct else proj["fires"]),
            "basis": ("direct whole-image serial run at --jobs 1, no projection"
                      if direct else
                      "1-in-16 stratified serial sample projected to the whole image"),
            "jobs": 1,
            "covers": "the shipping build: map-function modules and jump-table arm traces",
        },
        "serial_projection_stratified": proj,
        "serial_direct_whole_image": direct,
        "parallel_whole_build_16_jobs": par,
        "which_number_the_criterion_asks_about": {
            "answer": "the serial one",
            "why": "the criterion says 'wall minutes on this machine' and names no job "
                   "count. A wall that depends on how many cores the operator gave it is "
                   "not a property of the build, and the 16-job wall would let the "
                   "criterion be cleared by buying cores rather than by the emitter being "
                   "cheap -- which is the thing the criterion is there to test. "
                   "Documentation/receipts/s3-whole-image-lift-v1.json chose serial for "
                   "the same reason and this receipt agrees with it. The 16-job wall is "
                   "reported beside it because it is what a person actually waits for, "
                   "and it answers a different question.",
        },
        "falsification": falsification,
        "peak_memory": {
            "serial_direct_whole_image_gb": round(peak("full_") / 1e6, 2),
            "serial_sample_gb": round(peak("sample_") / 1e6, 2),
            "parallel_16_jobs_gb": round(peak("parallel_") / 1e6, 2),
            "peak_process_tree_kb": {"serial_direct": peak("full_"),
                                     "serial_sample": peak("sample_"),
                                     "parallel_16_jobs": peak("parallel_")},
            "how": "RSS summed over the driver and every descendant, sampled every 2 s, "
                   "and taken as the maximum over each group's phases. The groups are "
                   "reported apart because one number over all of them would be the "
                   "16-job run's, and would be read as the serial measurement's.",
        },
        "phases": ph,
        "limits": [
            "The arm stratum's size variable is remill's own PC-store marker count per "
            "trace, read from the shipping arm build's log. It is an output of a previous "
            "run of this same pipeline, not an independent static count, so it is exact "
            "for this image and would have to be re-measured if the arm work list "
            "changed. The map stratum's variable is the analyzer's static instruction "
            "count, which is the variable the superseded projection used; the two are "
            "never added.",
            "The two strata are scaled separately and summed. That is correct for a total "
            "and it is not a per-instruction rate: this receipt publishes no single "
            "instructions-per-second figure spanning both populations, because there is "
            "no denominator both populations share.",
            "Ratio estimation assumes cost is roughly proportional to the size variable "
            "within a stratum. The direct whole-image serial run is the check on that "
            "assumption and is reported as `falsification`; where the direct run exists, "
            "it, not the projection, is the measurement.",
            "A 1-in-16 systematic sample in address order is not a random sample. If cost "
            "per instruction were periodic with period 16 in address order the estimate "
            "would be biased; nothing suggests it is, and the direct run bounds the error "
            "either way.",
            "The whole-image link is still not attempted (the superseded receipt's own "
            "limit). These figures are lift, pass, fold and compile to objects. A link "
            "step would add to them, and the criterion's words are 'lift+compile'. "
            "Documentation/receipts/s2-build-time-sample-v1.json's projection did link "
            "its 500-module sample, so its 30.15 minutes and these figures do not cover "
            "quite the same span; the difference runs in this receipt's favour and is "
            "not claimed as margin.",
            "The arm stratum's single tool wall is split into lift+pass and fold+compile "
            "by the ratio of its own uncontended per-record seconds. "
            "`lift_arms.fold_and_compile` does not time the fold, so that ratio's "
            "denominator omits it and the residual -- reported under "
            "`serial_projection_stratified.strata.arm_traces.phase_split` -- is "
            "allocated in proportion. The split moves the two halves, never the total.",
            "Both runs used a warm page cache for the executable and the toolchain, and "
            "wrote to the same NVMe filesystem. A cold-cache first build would be slower "
            "by the cost of reading the toolchain once.",
            "`--jobs 1` leaves 15 hardware threads idle; the serial figure is therefore "
            "not this machine's best clock frequency behaviour under load, and if "
            "anything flatters the build slightly relative to a loaded machine.",
        ],
        "verdict": verdict(proj, direct),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"-> {out}")
    print(doc["verdict"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("sample", "full", "parallel", "receipt", "show"))
    ap.add_argument("--out", type=Path,
                    default=C.ROOT / "Documentation" / "receipts" / "s3-build-time-v2.json")
    args = ap.parse_args()
    if args.action == "sample":
        do_sample()
    elif args.action == "full":
        do_full()
    elif args.action == "parallel":
        do_parallel()
    elif args.action == "show":
        print(json.dumps({"population": counts(), "projection": projection(),
                          "direct": direct_serial(), "parallel": parallel_wall()},
                         indent=1))
    else:
        do_receipt(args.out)


if __name__ == "__main__":
    main()
