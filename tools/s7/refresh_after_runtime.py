#!/usr/bin/env python3
"""Refresh every receipt a runtime or harness change makes stale, in order, as one command.

Subtitle: the reach run, the registered tick replays, the stage boot, the NEW FILE hand-off,
M7's evidence and inputs, the DRM-unwrap measurement and M8's x86_64-linux receipt and
targets.json, each re-run by the command its own receipt records and each followed by the gate
that holds that receipt to the tree; the first failure stops the chain.

    source tools/env.sh
    .venv/bin/python tools/s7/refresh_after_runtime.py            # the whole chain
    .venv/bin/python tools/s7/refresh_after_runtime.py --list     # the steps, in order
    .venv/bin/python tools/s7/refresh_after_runtime.py --dry-run  # what would run
    .venv/bin/python tools/s7/refresh_after_runtime.py --from stage-boot   # resume there

Why a tool
----------
A change under runtime/ or to the harness moves the runtime digest or a pipeline hash that the
reach receipt, the tick-replay receipt, the stage-boot receipt, the NEW FILE receipt, M7's
evidence, the DRM-unwrap measurement and M8's target receipt each record, and every one of
them is then stale until it is re-run, in an order: the tick replays are held to the reach
run, the stage boot reads the replays, the hand-off check reads every registered run record,
M7's numerator reads the reach run and its evidence the tree's runtime, and the target receipt
reads the replay and reach receipts. The order was written in journal prose
(agents-journals/s5/2026-10-02-newfile-hang.md, "Shipping it") and repeated by hand after each
runtime change, which AGENTS.md rule 13 says becomes a tool.

What it does and does not do
----------------------------
* Each step's `run` is the command its receipt records (the receipt's `command` field, or the
  command its stale message names), run from the project root under `bash -c` with
  tools/env.sh sourced, so `source tools/env.sh && ...` commands run as recorded.
* Each step's `check` is the gate of Documentation/scripts/run_all_gates.py's list that holds
  that receipt to the tree. A step passes only when its run and its check both exit 0.
* A step with a `skip_if` names a file whose presence means its output already exists for this
  tree (M7's content-addressed re-recordings); it is then not re-run.
* The first step that fails stops the chain, its log is named, and the command that resumes at
  it is printed (`--from NAME`). Exit status 1 then, 0 when every step passed.
* It writes no receipt itself and refreshes no pin. Pins (gate-criteria.json evidence shapes,
  tools/pins.json) are refreshed by hand after the owning suite passes, replacing the exact
  known hash (AGENTS.md rule 8). It does not run reach_reproduce.py, which runs at stage gates
  and only as the last step before a commit (its docstring), nor `script tests` or ship.py.
* Each step starts only once `--min-available-gb` (default 8) GB is available, checked every
  minute: a replay's comparison alone holds several GB, and a step the kernel kills for memory
  is a receipt half written.
* Logs go to private/generated/s7/refresh-after-runtime/<UTC>/NN-<step>.log (never *.json:
  M7's census reads every *.json under private/generated as a possible run record), with
  chain.txt listing each step's exit and seconds.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ".venv/bin/python"
LOG_ROOT = "private/generated/s7/refresh-after-runtime"
ENV = "source tools/env.sh"


@dataclasses.dataclass(frozen=True)
class Step:
    name: str
    why: str
    run: tuple[str, ...] = ()        # shell commands, in order; none for a check-only step
    check: tuple[str, ...] = ()      # the gates that hold the step's output to the tree
    skip_if: str | None = None       # a path, relative to the root; the step is skipped if present


def gate(script: str, *args: str) -> str:
    return " ".join([PY, f"Documentation/scripts/{script}", *args])


def m7_rerecord(name: str) -> tuple[str, str]:
    """(the command that records M7's production run `name` again for this tree's recorder and
    runtime, the file whose presence means it has been), both from tools/s5/m7_synthetic.py's
    own definitions (rerecord_command, recording_key), so the chain records into the directory
    `m7_synthetic.py refresh` then finds by its key."""
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import m7_inputs as M  # noqa: E402
    import m7_synthetic as MS  # noqa: E402
    evidence = (ROOT / MS.EVIDENCE_ROOT).resolve()
    key = MS.recording_key(ROOT, M.tree_runtime_digest(ROOT))
    done = evidence / f"record-{key}" / name / "present-run.json"
    return MS.rerecord_command(evidence, key, name), str(done.relative_to(ROOT))


def steps() -> list[Step]:
    """The chain, in dependency order."""
    mh_cmd, mh_done = m7_rerecord("metalharbor")
    rel_cmd, rel_done = m7_rerecord("release")
    return [
        Step("reach", "the reach receipt: the boot run on the registered boot profile",
             run=(f"{PY} tools/s4/reach_receipt.py --replay boot --write",),
             check=(gate("check_run_reach.py"),)),
        Step("tick-replays", "every registered replay, held to the reach run",
             run=(f"{PY} tools/s4/tick_replay.py --receipt",),
             check=(gate("check_replay_results.py"), gate("check_replay_regression.py"))),
        Step("stage-boot", "the stage boot and stage results, which read the replays",
             run=(f"{PY} tools/s4/stage_boot.py --write",),
             check=(gate("check_stage_results.py"),)),
        Step("poll-handoff", "the NEW FILE run, and every gated run record's hand-off counters",
             run=(f"{PY} tools/s5/poll_handoff_check.py regression --write",),
             check=(gate("check_poll_handoff.py"),)),
        Step("m7-record-metalharbor", "M7's Metal Harbor recording at this tree's runtime",
             run=(mh_cmd,), skip_if=mh_done),
        Step("m7-record-release", "M7's release-route recording at this tree's runtime",
             run=(rel_cmd,), skip_if=rel_done),
        Step("m7-evidence", "M7's three-way evidence from those recordings, in both hosts",
             run=(f"{ENV} && {PY} tools/s5/m7_synthetic.py refresh",)),
        Step("m7-inputs", "M7's inputs and the run index",
             run=(f"{ENV} && {PY} tools/s4/m7_inputs.py",),
             check=(gate("check_m7_inputs.py"),)),
        Step("corpus", "the fixture corpus and its negative controls, which link the runtime",
             check=(gate("check_fixture_corpus.py"), gate("check_controls.py"))),
        Step("drm-unwrap", "the DRM-unwrap measurement, whose tool hashes include runtime "
             "files, and the overlay table the release build links, which cites it",
             run=(f"{ENV} && {PY} tools/s7/drm_unwrap.py", f"{PY} tools/s7/text_readset.py"),
             check=(f"{ENV} && {PY} tools/s7/drm_unwrap.py --check",
                    f"{PY} tools/s7/text_readset.py --check")),
        Step("target-x86_64-linux", "M8's reference receipt, which reads the reach and replay "
             "receipts", run=(f"{PY} tools/s7/target_receipt.py --target x86_64-linux",),
             check=(f"{PY} tools/s7/target_receipt.py --target x86_64-linux --check",)),
        Step("targets", "M8's input, targets.json, from the target receipts",
             run=(f"{PY} tools/s7/targets.py",),
             check=(f"{PY} tools/s7/targets.py --check", gate("metrics.py"))),
        Step("ledgers", "the ledger suites and every receipt and tool pin",
             check=(gate("check_ledger_suites.py"), gate("check_receipt_pins.py"),
                    gate("check_pins.py"))),
    ]


def available_gb(meminfo: Path = Path("/proc/meminfo")) -> float:
    """The memory the kernel says a new job can have (MemAvailable), in GB; infinite when the
    file does not say, so a host without /proc/meminfo never waits."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return float("inf")


def wait_for_memory(step: str, min_gb: float, poll: float = 60.0, available=available_gb,
                    sleep=time.sleep) -> None:
    """Hold step `step` until at least `min_gb` GB is available: the replays and recordings each
    hold several GB, and on a machine other jobs share a step started short of memory is one
    the kernel kills. 0 never waits."""
    while min_gb > 0 and (have := available()) < min_gb:
        print(f"REFRESH {step}: waiting, {have:.1f} GB available, {min_gb:g} GB wanted",
              flush=True)
        sleep(poll)


def run_chain(chain: list[Step], root: Path, log_dir: Path, start: str | None = None,
              only: list[str] | None = None, dry_run: bool = False,
              shell_prefix: str = "set -o pipefail; ", min_available_gb: float = 0.0,
              available=available_gb, sleep=time.sleep) -> int:
    """Run `chain` from step `start` (or the first), or only the steps `only` names, in order,
    under `root`, logging into `log_dir`, each step once `min_available_gb` GB is available
    (wait_for_memory). 0 when every step run passed; 1 at the first step whose command or
    check failed, after printing how to resume at it."""
    names = [s.name for s in chain]
    for asked in [start] + list(only or []):
        if asked is not None and asked not in names:
            print(f"REFRESH_REFUSED no step {asked!r}; the steps are {names}")
            return 2
    begin = names.index(start) if start else 0
    todo = [s for s in chain[begin:] if not only or s.name in only]
    if not dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)
    table = []
    for n, step in enumerate(todo, 1):
        if step.skip_if and (root / step.skip_if).exists():
            print(f"REFRESH {step.name}: skipped, {step.skip_if} is there", flush=True)
            table.append(f"{step.name}\tskipped\t0")
            continue
        commands = list(step.run) + list(step.check)
        if dry_run:
            for c in commands:
                print(f"REFRESH {step.name}: would run {c}")
            continue
        log = log_dir / f"{names.index(step.name) + 1:02d}-{step.name}.log"
        wait_for_memory(step.name, min_available_gb, available=available, sleep=sleep)
        print(f"REFRESH {step.name}: {step.why}", flush=True)
        t0 = time.monotonic()
        rc = 0
        with log.open("w", encoding="utf-8") as out:
            for c in commands:
                out.write(f"$ {c}\n")
                out.flush()
                rc = subprocess.run(["bash", "-c", shell_prefix + c], cwd=root, stdout=out,
                                    stderr=subprocess.STDOUT).returncode
                out.write(f"# exit {rc}\n")
                out.flush()
                if rc:
                    break
        seconds = round(time.monotonic() - t0, 1)
        table.append(f"{step.name}\t{rc}\t{seconds}")
        (log_dir / "chain.txt").write_text("\n".join(table) + "\n", encoding="utf-8")
        if rc:
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
            print(f"REFRESH_FAILED {step.name} (exit {rc}, {seconds} s); log {log}", flush=True)
            for line in tail:
                print(f"  {line[:300]}")
            print(f"resume with: {PY} tools/s7/refresh_after_runtime.py --from "
                  f"{shlex.quote(step.name)}")
            return 1
        print(f"REFRESH {step.name}: ok ({seconds} s)", flush=True)
    if not dry_run:
        print(f"REFRESH_OK steps={len(todo)} logs={log_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", default=None, metavar="STEP",
                    help="start at this step (resume after a failure)")
    ap.add_argument("--only", action="append", default=[], metavar="STEP",
                    help="run only this step; repeatable")
    ap.add_argument("--list", action="store_true", help="print the steps and exit")
    ap.add_argument("--dry-run", action="store_true", help="print each command and run nothing")
    ap.add_argument("--min-available-gb", type=float, default=8.0, metavar="GB",
                    help="start each step only once this much memory is available, checking "
                         "every minute (default 8; 0 never waits)")
    a = ap.parse_args(argv)
    chain = steps()
    if a.list:
        for n, s in enumerate(chain, 1):
            print(f"{n:2d}. {s.name}: {s.why}")
            for c in s.run:
                print(f"      run   {c}")
            for c in s.check:
                print(f"      check {c}")
            if s.skip_if:
                print(f"      skipped when {s.skip_if} exists")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return run_chain(chain, ROOT, ROOT / LOG_ROOT / stamp, start=a.start, only=a.only,
                     dry_run=a.dry_run, min_available_gb=a.min_available_gb)


if __name__ == "__main__":
    raise SystemExit(main())
