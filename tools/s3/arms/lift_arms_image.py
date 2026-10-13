#!/usr/bin/env python3
"""The arm-seeding fix at image scale: lift, pass, fold and compile every arm.

Subtitle: `tools/s3/arms/lift_arms.py` over one owner's six arms, run over
every arm in `armlist.py`'s work list, through the identical pipeline every
other whole-image module goes through.

Nothing about the per-trace pipeline is new. `lift_arms.lift_one` and
`lift_arms.fold_and_compile` are imported and called unchanged -- remill under
`unshare -Urm`, `pc24pass.instrument` with both helpers, `internalize.rewrite`,
`zig cc -target x86_64-linux-musl -O2 -w -c`. What this adds is only what scale
forces, and it is the same set `lift_all.py` and `compile_all.py` added for the
same reason:

  * a process pool (`--jobs`), with the memory image read once per worker;
  * append-only JSONL, one record per trace, flushed as it lands, so a kill
    loses one trace rather than the run -- and so a rerun resumes;
  * a wall file, and a peak-memory sampler over the whole process tree, because
    16 concurrent remill invocations are the memory question and
    `ru_maxrss` only answers it for the single worst child.

The record is shaped so that `tools/s3/whole_image/gen_table.py` can read a
concatenation of this log and the whole-image build log: `va`, `compiled` and
`defines` mean exactly what they mean there, `defines` being the module's
*externally* defined symbols after the fold, via `compile_all.symbols`.

The whole-image **link is not attempted here**. The runtime is being changed
concurrently by another unit; this slice produces objects and a table input.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/armlist.py --tag next --jobs 16
  .venv/bin/python tools/s3/arms/lift_arms_image.py --tag next --jobs 16
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import resource
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import lift_arms as L  # noqa: E402
import compile_all as CA  # symbols(): external vs internal defines  # noqa: E402

OUT = C.GEN / "s3" / "arms"

_IMAGE: bytes | None = None
_DIRS: tuple[Path, Path, Path] | None = None


def _init(lldir: str, folddir: str, objdir: str) -> None:
    global _IMAGE, _DIRS
    _IMAGE = C.memory_image()
    _DIRS = (Path(lldir), Path(folddir), Path(objdir))


def one(job: dict) -> dict:
    lldir, folddir, objdir = _DIRS
    arm = int(job["arm"], 16)
    lo, hi = int(job["lo"], 16), int(job["hi"], 16)
    rec = L.lift_one(lo, hi, arm, lldir, _IMAGE)
    rec["va"] = job["arm"]
    rec["arm"] = job["arm"]
    rec["owner"] = job["owner"]
    rec["region"] = job["region"]
    rec["name"] = f"arm_{arm:08x}_of_{job['owner_name']}"
    rec["anchor"] = [job["lo"], job["hi"]]
    if not rec["lifted"] or not rec.get("pass", {}).get("ok"):
        rec["compiled"] = False
        rec["defines"] = []
        return rec
    # the object is deleted before the compile, so a failed compile cannot
    # leave a stale one behind for a later link to pick up -- the mistake
    # `link_arms.py` made in the six-arm slice
    obj = objdir / f"ir_{arm:08X}.o"
    if obj.exists():
        obj.unlink()
    rec.update(L.fold_and_compile(arm, lldir, folddir, objdir))
    folded = (folddir / f"sub_{arm:08X}.pc24.ll").read_text(errors="replace")
    defined, declared = CA.symbols(folded)
    rec["defines"] = defined
    rec["declares_sub"] = sorted({d for d in declared if d.startswith("sub_")})
    return rec


class TreeMemory(threading.Thread):
    """Peak resident memory of this process and every descendant.

    `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` is the high-water mark of
    the single worst child, which for a 16-way pool understates the machine's
    actual load by more than an order of magnitude. Both are reported.
    """

    def __init__(self, interval: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_tree_kb = 0
        self.peak_single_kb = 0
        self.samples = 0
        self._stop = threading.Event()

    def _snapshot(self) -> tuple[int, int]:
        me = os.getpid()
        children: dict[int, list[int]] = {}
        rss: dict[int, int] = {}
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                with open(f"/proc/{pid}/stat") as fh:
                    fields = fh.read().rsplit(") ", 1)[1].split()
                ppid = int(fields[1])
                with open(f"/proc/{pid}/statm") as fh:
                    rss[pid] = int(fh.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
            except (OSError, IndexError, ValueError):
                continue
            children.setdefault(ppid, []).append(pid)
        total = single = 0
        stack = [me]
        while stack:
            pid = stack.pop()
            r = rss.get(pid, 0)
            total += r
            single = max(single, r)
            stack += children.get(pid, [])
        return total, single

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                total, single = self._snapshot()
            except OSError:
                continue
            self.samples += 1
            self.peak_tree_kb = max(self.peak_tree_kb, total)
            self.peak_single_kb = max(self.peak_single_kb, single)

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--armlist", type=Path, default=None)
    args = ap.parse_args()

    listing = args.armlist or (OUT / f"armlist.{args.tag}.json")
    doc = json.loads(listing.read_text())
    if not doc.get("assertion", {}).get("ok"):
        sys.exit(f"REFUSED: {listing} did not pass its own link assertion; "
                 f"run tools/s3/arms/armlist.py first")

    lldir = OUT / "lift" / args.tag
    folddir = OUT / "fold" / args.tag
    objdir = OUT / "build" / args.tag
    for d in (lldir, folddir, objdir):
        d.mkdir(parents=True, exist_ok=True)
    log = OUT / f"arms.{args.tag}.jsonl"
    done = {} if args.force else C.read_jsonl(log)

    jobs = [row for row in doc["work"]
            if args.force or not done.get(row["arm"], {}).get("compiled")]
    resumed = len(doc["work"]) - len(jobs)
    if args.limit:
        jobs = jobs[:args.limit]

    print(f"tag={args.tag} jobs={args.jobs} traces={len(doc['work'])} "
          f"already_compiled={resumed} todo={len(jobs)}", flush=True)

    mem = TreeMemory()
    mem.start()
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.time()
    n = lifted = compiled = 0
    with log.open("a") as fh, cf.ProcessPoolExecutor(
            max_workers=args.jobs, initializer=_init,
            initargs=(str(lldir), str(folddir), str(objdir))) as ex:
        for rec in ex.map(one, jobs, chunksize=4):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()                      # one trace per line, as it lands
            n += 1
            lifted += bool(rec.get("lifted"))
            compiled += bool(rec.get("compiled"))
            if n % 200 == 0:
                el = time.time() - t0
                print(f"  {n}/{len(jobs)} lifted={lifted} compiled={compiled} "
                      f"{el:.0f}s ({n / el:.1f}/s, eta "
                      f"{(len(jobs) - n) / max(n / el, 1e-9) / 60:.1f} min)", flush=True)
    wall = time.time() - t0
    mem.stop()
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    walldoc = {
        "tag": args.tag, "jobs": args.jobs,
        "traces_attempted": n, "lifted": lifted, "compiled": compiled,
        "wall_seconds": round(wall, 2),
        "traces_per_second": round(n / wall, 3) if wall else None,
        "nproc": os.cpu_count(),
        "peak_memory": {
            "process_tree_peak_kb": mem.peak_tree_kb,
            "worst_single_process_kb": mem.peak_single_kb,
            "ru_maxrss_children_kb": after,
            "ru_maxrss_children_before_kb": before,
            "sampler_interval_seconds": mem.interval,
            "samples": mem.samples,
            "note": "the tree peak sums RSS over this process and every descendant "
                    "every 2 s; ru_maxrss is the single worst child and is reported "
                    "beside it because it is what the other S3 tools record",
        },
        "pipeline": "remill-lift-14 --entry_address <arm> | pc24pass.instrument "
                    "(--helper-ll and --int-helper-ll) | internalize.rewrite | "
                    "zig cc -target x86_64-linux-musl -O2 -w -c",
    }
    # Every run is appended to the history; the headline file is replaced only by
    # a run that did work. A resume with nothing to do otherwise overwrites the
    # real run's wall and peak memory with zeroes, which is a number that is
    # present and means something else -- the failure mode the operating notes
    # say the gates are worst at catching. It happened once here before this
    # guard existed.
    with (OUT / f"arms.{args.tag}.wall.jsonl").open("a") as fh:
        fh.write(json.dumps(dict(walldoc, when=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                             time.gmtime())),
                            sort_keys=True) + "\n")
    if n:
        (OUT / f"arms.{args.tag}.wall.json").write_text(json.dumps(walldoc, indent=1) + "\n")
    else:
        print("no traces ran: the wall file is left as the last real run's", flush=True)
    print(f"done {n} traces, {lifted} lifted, {compiled} compiled, {wall:.1f}s wall "
          f"at {args.jobs} jobs; tree peak {mem.peak_tree_kb / 1e6:.2f} GB")
    print(f"-> {log}")


if __name__ == "__main__":
    main()
