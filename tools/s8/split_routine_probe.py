#!/usr/bin/env python3
"""Link and run harness/split_routine_probe.c: routines the function map split in two, entered
through the edge between their halves.

Subtitle: the falsifier of three of the 2026-10-18 dispatch exceptions (0x007059af, issue #16's
Pyramid Cave stop; 0x006b165f, Death Chamber's; 0x0082c189, the CRT's x87 domain handler), as a
command.

    .venv/bin/python tools/s8/split_routine_probe.py [--edges TABLE.c] [--f80-objects DIR] [--out DIR]

The probe is linked exactly as the Linux game is (tools/s7/release_build.py's game step: the double
f80 build's lifted objects, the runtime, the generated function and edge tables, the overlay table),
with the probe as the main in harness/crt_probe.c's place, so it runs the lifted code the release
runs under the edge table the release links (private/generated/s4/link/mb_edges.next+allarms.c,
from tools/s4/mb_edges.py). It prints SPLIT_ROUTINE_PROBE_OK when FUN_007059a0 and FUN_006b1650
agree with their model over 203 tables each and _CIatan2 and _CIfmod return the handler's NaN and
errno EDOM for the operands their class tables send to it, with no trap and every edge crossed, and
exits 1 otherwise.

`--edges` links another generated table in its place (its .json beside it), which is how the
falsification runs: with a table generated before the exceptions, every call that crosses one of
the three edges stops at its address with status 1 (`reached by jcc-taken` or `fall-through`).
release_build.py refuses such a table for a release; this probe takes it on purpose.

`--misdispatch` falsifies the x87 leg's oracle instead of the table: 0x0082c189 is pointed at the
handler's other tail, 0x0082c190, which returns the same NaN (the head loaded it) but skips the
domain-error flag. The run must then fail on every crossing atan2/fmod call, and only on those;
this command prints MISDISPATCH_CAUGHT and exits 0 when it does.

Work and output: private/generated/s8/split-probe/ (gitignored), or --out.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import release_build as rb  # noqa: E402

PROBE = ROOT / "harness" / "split_routine_probe.c"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
BASE = ROOT / "private" / "generated" / "s8" / "split-probe"
OVERLAY = ROOT / "private" / "generated" / "s7" / "exe-overlay" / "sa2_exe_overlay_table.c"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--edges", type=Path, default=None,
                    help="a generated mb_edges.<tag>.c to link instead of the default one")
    ap.add_argument("--f80-objects", type=Path, default=rb.F80_OBJECTS["x86_64-linux"],
                    help="the double f80 build's objects (default: release_build.py's)")
    ap.add_argument("--out", type=Path, default=BASE)
    ap.add_argument("--misdispatch", action="store_true",
                    help="point 0x0082c189 at 0x0082c190 and require the x87 leg to catch it")
    args = ap.parse_args()
    # Absolute, not resolved: in a worktree whose private/ is a link, a resolved path leaves the
    # tree and link_s4's records (release_build.py main says the same).
    base = Path(os.path.abspath(args.out))
    work, out = base / "work", base / "out"
    (work / "overlay").mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    if not OVERLAY.is_file():
        raise SystemExit(f"no overlay table at {OVERLAY.relative_to(ROOT)}: "
                         "run tools/s7/release_build.py --only tables")
    shutil.copy2(OVERLAY, work / "overlay" / OVERLAY.name)
    original = rb.source_modules

    def with_probe(src: Path) -> dict:
        m = original(src)
        m["RR"].MAIN = PROBE
        return m

    rb.source_modules = with_probe
    if args.edges:
        edges = args.edges.resolve()
        if not edges.is_file():
            raise SystemExit(f"--edges {edges} is not there")
        linked = rb.link_inputs
        rb.link_inputs = lambda: linked(edges, check=False)
    f80 = args.f80_objects.resolve()
    log = base / "link.log"
    with log.open("w") as fh:
        stdout, sys.stdout = sys.stdout, fh
        try:
            rb.step_game(ROOT, work, out, f80, rb.f80_build(f80, rb.F80_TRIPLES["x86_64-linux"], "double"))
        finally:
            sys.stdout = stdout
    binary = out / "bin" / rb.GAME
    env = dict(os.environ, SPLIT_PROBE_MISDISPATCH="1" if args.misdispatch else "0")
    run = subprocess.run([str(binary), str(EXE)], capture_output=True, text=True, timeout=300,
                         env=env)
    (base / "run.txt").write_text(run.stdout + run.stderr)
    lines = run.stdout.splitlines()
    for line in lines:
        if line.startswith(("edge ", "SPLIT_ROUTINE_PROBE", "errno at", "misdispatch")) or \
                "DIFFERS" in line or "crossed into" in line:
            print(line)
    if args.misdispatch:
        x87 = [ln for ln in lines if ln.startswith(("atan2(", "fmod("))]
        crossing = [ln for ln in x87 if " crossed=0 " not in ln]
        wrong = [ln for ln in x87 if (" crossed=0 " in ln) == ("DIFFERS" in ln)]
        others = [ln for ln in lines if "DIFFERS" in ln and ln not in x87]
        if run.returncode and crossing and not wrong and not others and \
                any(ln.startswith("misdispatch:") for ln in lines):
            print(f"MISDISPATCH_CAUGHT {len(crossing)} crossing x87 calls failed, "
                  f"{len(x87) - len(crossing)} others passed")
            return 0
        print(f"MISDISPATCH_MISSED: the x87 leg did not tell 0x0082c190 from 0x0082c189 "
              f"({len(wrong)} lines disagree, {len(others)} other failures); see {base / 'run.txt'}")
        return 1
    if not lines or not lines[-1].startswith("SPLIT_ROUTINE_PROBE_OK") or run.returncode:
        print(f"see {base / 'run.txt'} and {log}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
