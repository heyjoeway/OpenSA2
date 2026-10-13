#!/usr/bin/env python3
"""G1 kill criterion 1 as the user redefined it: the whole image at four jobs.

Subtitle: the whole shipping image -- the map's modules, S3's jump-table arm
traces and S4's lifted extras -- lifted, passed, folded and compiled at
`--jobs 4`, the wall clock around each phase added, every object required to
be byte-identical to the one the link uses; and the relink after one object
changes, timed.

    source tools/env.sh
    .venv/bin/python tools/s4/build_time_jobs.py            # measure and print
    .venv/bin/python tools/s4/build_time_jobs.py --write    # and write the receipt

Why
---
The user decided on 2026-09-23 (`Documentation/decisions/2026-09-23-G1-1-build-time-jobs4.md`):
the criterion is "full-image lift+compile wall time at --jobs 4, measured
directly, threshold 60 minutes". G1's text is "60 wall minutes on this
machine" with no job count; the serial reading was a later strengthening that
measured a build nobody waits for, and was steering work away from lifting.
Four jobs is stricter than the literal original and portable to a modest
machine. The serial figure (`Documentation/receipts/s4-build-time-v1.json`)
and the relink time are kept as tracked METRICS.md rows, not triggers.

What is measured
----------------
Four phases, run one after another, each at `--jobs 4` into a fresh tag so
nothing is reused (`--force` as well, so a leftover of the same tag cannot be
skipped): the map's lift and pass (`--to-next-entry`, the adopted bound), the
map's fold and compile, the arm traces end to end, and the extras end to end.
The criterion's figure is the sum of the four walls: the build as a sequence,
at the job count the user chose. It proves it measured the build that ships
by comparing every object with the one the link uses: the map's against
`whole_image/build/next`, the arms' against `arms/build/next`, and each
extra's against the object its row records.

The relink is the reach driver's link, reconstructed from the reach build's
own checkpoint and first shown to reproduce the reach binary byte for byte,
then run three times with one small object added whose content changes each
time. zig caches a link whose inputs are unchanged -- the reach checkpoint's
0.29 s link was such a hit -- so an unchanged relink is not a relink. The
compile of the changed object is not included: this is the link.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402
import build_time_v2 as BT  # noqa: E402
import build_time_extras as BTE  # noqa: E402
import lift_extra as LE  # noqa: E402
import link_s4 as LS  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
TAG = "bt4"
JOBS = 4
THRESHOLD = 60.0
ARMLIST = C.GEN / "s3" / "arms" / "armlist.next.json"
MAP_BUILD = C.GEN / "s3" / "whole_image" / "build"
ARM_BUILD = C.GEN / "s3" / "arms" / "build"
SHIPPED = "next"
REACH_STATE = LS.OUT / "main.reach.json"
SERIAL = ROOT / "Documentation" / "receipts" / "s4-build-time-v1.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-build-time-v2.json"
DECISION = "Documentation/decisions/2026-09-23-G1-1-build-time-jobs4.md"
PIPELINE = (
    "tools/s4/build_time_jobs.py",
    "tools/s4/build_time_extras.py",
    "tools/s4/lift_extra.py",
    "tools/s4/link_s4.py",
    "tools/s3/whole_image/lift_all.py",
    "tools/s3/whole_image/compile_all.py",
    "tools/s3/arms/lift_arms_image.py",
    "tools/spike/pc24pass/pc24pass.py",
    "tools/s2/corpus_run/internalize.py",
    "tools/s2/corpus_run/speed_rewrite.py",
    "tools/s2/corpus_run/state_local.py",
    "Documentation/lifted-extras.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identical(ours: Path, theirs: Path) -> dict:
    """Every object under `ours` against the same name under `theirs`."""
    names = sorted(p.name for p in ours.glob("*.o"))
    shipped = sorted(p.name for p in theirs.glob("*.o"))
    differ = [n for n in names if not (theirs / n).is_file()
              or sha256(ours / n) != sha256(theirs / n)]
    return {"built": len(names), "shipped": len(shipped),
            "missing_from_build": sorted(set(shipped) - set(names))[:10],
            "extra_in_build": sorted(set(names) - set(shipped))[:10],
            "differ": differ[:10], "differ_count": len(differ),
            "ok": names == shipped and not differ}


# The extras are lifted in worker processes that inherit the memory image by
# fork rather than by pickling it, one image per worker.
_IMAGE = None


def _extra(job: tuple[dict, str]) -> dict:
    r, work = job
    dirs = [Path(work) / d for d in ("ll", "fold", "obj")]
    va, end = int(r["va"], 16), int(r["end"], 16)
    anchor = int(r["anchor"], 16) if r.get("anchor") else None
    rec = LE.lift_one(va, end, _IMAGE, dirs[0], anchor)
    if rec.get("lifted"):
        rec.update(LE.fold_and_compile(va, dirs[0], dirs[1], dirs[2]))
    if not rec.get("compiled"):
        return {"va": r["va"], "failed": True}
    same = Path(r["object"]).is_file() and sha256(Path(rec["object"])) == sha256(Path(r["object"]))
    return {"va": r["va"], "failed": False, "identical": same}


def extras_phase(jobs: int) -> tuple[dict, dict]:
    global _IMAGE
    state = json.loads(BTE.STATE.read_text())
    rows = [r for r in state["rows"] if r.get("compiled") and not r.get("rejected")]
    _IMAGE = C.memory_image()
    work = Path(tempfile.mkdtemp(prefix="sa2-extras-jobs-"))
    for d in ("ll", "fold", "obj"):
        (work / d).mkdir()
    t0 = time.time()
    try:
        with multiprocessing.get_context("fork").Pool(jobs) as pool:
            out = pool.map(_extra, [(r, str(work)) for r in rows], chunksize=4)
        wall = time.time() - t0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failed = [o["va"] for o in out if o["failed"]]
    differ = [o["va"] for o in out if not o["failed"] and not o["identical"]]
    phase = {"label": f"lifted extras lift+pass+fold+compile, --jobs {jobs}",
             "command": f"tools/s4/build_time_jobs.py (a fork pool of {jobs} over "
                        "tools/s4/lift_extra.py lift_one and fold_and_compile)",
             "wall_seconds": round(wall, 2), "returncode": 0 if not failed else 1,
             "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ident = {"built": len(rows) - len(failed), "shipped": len(rows), "failed": failed[:10],
             "differ": differ[:10], "differ_count": len(differ),
             "ok": not failed and not differ}
    return phase, ident


def relink(runs: int = 3) -> dict:
    """The reach driver's link, reproduced, then relinked with one object changed."""
    st = json.loads(REACH_STATE.read_text())
    name, tag = st["name"], st["tag"]
    bdir = (LS.OUT / "mains" / name / "obj").resolve()
    objdir = (C.BUILD / tag).resolve()
    objs = sorted(p.name for p in objdir.glob("ir_*.o"))
    extra = [Path(x) for x in st.get("extra_c", [])]
    names = (LS.RUNTIME_C + LS.RUNTIME_CXX + ["function_table_whole.c"]
             + [f"extra{k}_{x.stem}.c" for k, x in enumerate(extra)] + ["main.c"])
    base = [str(bdir / (Path(s).stem + ".o")) for s in names] + list(st.get("extra_o", []))
    wraps = [f"-Wl,-wrap={w}" for w in (st["link"].get("wrapped") or [])]
    if st["link"].get("objects_lifted") != len(objs) or st["link"].get("objects_dropped"):
        raise SystemExit("the reach checkpoint's link does not match the objects on disk; "
                         "re-run tools/s4/reach_receipt.py first")

    def cmd(out: Path, more: list[str]) -> list[str]:
        return [str(LS.S.ZIG), "c++"] + LS.TARGET + [LS.OPT] + wraps + base + more + objs + \
            ["-o", str(out)]

    with tempfile.TemporaryDirectory(prefix="sa2-relink-") as td:
        tmp = Path(td)
        same = tmp / "reach"
        p = subprocess.run(cmd(same, []), cwd=objdir, capture_output=True, text=True)
        if p.returncode or sha256(same) != st["link"]["sha256"]:
            raise SystemExit("the reconstructed link does not reproduce the reach binary "
                             f"(rc={p.returncode}); the relink figure would time another link")
        walls = []
        for i in range(runs):
            src = tmp / f"probe{i}.c"
            src.write_text(f"int sa2_relink_probe_{time.time_ns()} = {i};\n")
            obj = tmp / f"probe{i}.o"
            subprocess.run([str(LS.S.ZIG), "cc"] + LS.TARGET + [LS.OPT, "-c", str(src), "-o",
                           str(obj)], check=True, capture_output=True)
            t0 = time.time()
            p = subprocess.run(cmd(tmp / f"out{i}", [str(obj)]), cwd=objdir,
                               capture_output=True, text=True)
            walls.append(round(time.time() - t0, 2))
            if p.returncode:
                raise SystemExit(f"relink {i} failed: {p.stderr[-2000:]}")
    return {"what": "the reach driver's link with one small object added whose content changes "
                    "each run, so zig's link cache cannot answer it; the compile of that object "
                    "is not included",
            "reproduces_reach_binary": st["link"]["sha256"],
            "objects_lifted": len(objs), "objects_runtime": len(base) + 1,
            "runs_seconds": walls, "seconds": statistics.median(walls)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--jobs", type=int, default=JOBS,
                    help="the criterion is defined at 4; another value measures, never writes")
    args = ap.parse_args()
    if args.write and args.jobs != JOBS:
        raise SystemExit(f"the criterion is defined at --jobs {JOBS}; a receipt at "
                         f"--jobs {args.jobs} would measure something else")
    BTE.unwind_on_sigterm()
    for d in (MAP_BUILD / TAG, ARM_BUILD / TAG):
        shutil.rmtree(d, ignore_errors=True)
    j = str(args.jobs)
    with BTE.cold_compiler_cache():
        phases = timed_phases(args.jobs)
    extras_ident = phases.pop("_extras_ident")
    ident = {"map": identical(MAP_BUILD / TAG, MAP_BUILD / SHIPPED),
             "arms": identical(ARM_BUILD / TAG, ARM_BUILD / SHIPPED),
             "extras": extras_ident}
    total = round(sum(p["wall_seconds"] for p in phases.values()) / 60.0, 3)
    rel = relink()                        # the link, against zig's own cache: see relink()
    return finish(args, j, phases, ident, total, rel)


def timed_phases(jobs: int) -> dict:
    """The four timed phases, in the build's order; run inside a cold compiler cache."""
    j = str(jobs)
    phases = {
        "map_lift": BT.run([PY, "tools/s3/whole_image/lift_all.py", "--jobs", j, "--tag", TAG,
                            "--to-next-entry", "--force"],
                           f"whole-image map lift+pass, --jobs {j}, --to-next-entry"),
        "map_compile": BT.run([PY, "tools/s3/whole_image/compile_all.py", "--jobs", j,
                               "--tag", TAG, "--force"],
                              f"whole-image map fold+compile, --jobs {j}"),
        "arms": BT.run([PY, "tools/s3/arms/lift_arms_image.py", "--jobs", j, "--tag", TAG,
                        "--armlist", str(ARMLIST), "--force"],
                       f"whole-image arm traces lift+pass+fold+compile, --jobs {j}"),
    }
    phases["extras"], phases["_extras_ident"] = extras_phase(jobs)
    return phases


def finish(args, j: str, phases: dict, ident: dict, total: float, rel: dict) -> int:
    doc = json.loads(SERIAL.read_text())
    # The serial figure is copied into this receipt, so it must be the one the
    # current tool measured cold, not one left from before: a chained run whose
    # serial half failed would otherwise carry the old figure into METRICS.md.
    pinned = (doc.get("inputs") or {}).get("pipeline", {}).get("tools/s4/build_time_extras.py")
    if pinned != sha256(ROOT / "tools/s4/build_time_extras.py") or \
            BTE.COLD_CACHE not in doc.get("method", ""):
        raise SystemExit(f"{SERIAL.relative_to(ROOT)} was not written by the current "
                         f"build_time_extras.py with a cold compiler cache; run it first")
    serial = doc["kill_criterion_1"]
    ok = all(i["ok"] for i in ident.values())
    print(f"--jobs {j}: " + ", ".join(f"{k} {v['wall_seconds']} s" for k, v in phases.items())
          + f" = {total} min of {THRESHOLD}; objects identical to the build's: "
          + ", ".join(f"{k} {'yes' if v['ok'] else 'NO'}" for k, v in ident.items())
          + f"; relink {rel['seconds']} s (runs {rel['runs_seconds']}); "
            f"serial (tracked) {serial['minutes_total']} min")
    if not ok:
        print(json.dumps(ident, indent=1))
        return 1
    if not args.write:
        return 0
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-build-time-v2",
        "question": "Does the full-image lift+compile at --jobs 4, measured directly, exceed G1 "
                    "kill criterion 1's 60 wall minutes (the user's decision of 2026-09-23)?",
        "generated_by": "tools/s4/build_time_jobs.py",
        "commit": head,
        "command": ".venv/bin/python tools/s4/build_time_jobs.py --write",
        "decision": DECISION,
        "machine": {"nproc": multiprocessing.cpu_count()},
        "inputs": {
            "pipeline": {p: sha256(ROOT / p) for p in PIPELINE},
            "armlist": {"path": str(ARMLIST.relative_to(ROOT)), "sha256": sha256(ARMLIST)},
            "extras_state": str(BTE.STATE.relative_to(ROOT)),
            "serial_receipt": {"path": str(SERIAL.relative_to(ROOT)), "sha256": sha256(SERIAL)},
        },
        "phases": phases,
        "objects_identical_to_the_build": ident,
        "kill_criterion_1": {
            "basis": f"a direct whole-image run at --jobs {JOBS}, the four phase walls added",
            "covers": "the shipping build: map-function modules, S3's jump-table arm traces and "
                      "S4's lifted extras",
            "jobs": JOBS,
            "minutes_total": total,
            "percent_of_60_minute_threshold": round(100.0 * total / THRESHOLD, 2),
            "threshold_minutes": THRESHOLD,
            "fires": total > THRESHOLD,
        },
        "tracked_not_triggers": {
            "serial_minutes": serial["minutes_total"],
            "serial_source": str(SERIAL.relative_to(ROOT)) + " kill_criterion_1.minutes_total",
            "relink": rel,
        },
        "limits": [
            "The four phases run one after another; the build tools do not overlap them, so "
            "neither does this.",
            "A warm page cache, as in S3's measurements; no link in the criterion's figure, as "
            "there. The relink is reported beside it, not added to it.",
            "The compiler cache is cold: " + BTE.COLD_CACHE + ". The relink alone runs against "
            "zig's own cache, which is what makes it a relink.",
            "The machine has more than four hardware threads, so --jobs 4 here is four workers "
            "without contention from each other for cores; a four-core machine would be slower.",
        ],
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"wrote {RECEIPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
