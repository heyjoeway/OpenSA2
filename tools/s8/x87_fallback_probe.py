#!/usr/bin/env python3
"""Link and run harness/x87_fallback_probe.c: the CRT's x87 tan, sin and cos through the edges
Documentation/dispatch-exceptions.json allows.

Subtitle: the falsifier of the three call-return exceptions (0x00859ac8, 0x00859648,
0x00829008), as a command.

    .venv/bin/python tools/s8/x87_fallback_probe.py

The probe is linked exactly as the Linux game is (tools/s7/release_build.py's game step: the
double f80 build's lifted objects, the runtime, the generated function and edge tables, the
overlay table), with the probe as the main in harness/crt_probe.c's place, so it runs the
lifted code the release runs under the edge table the release links
(private/generated/s4/link/mb_edges.next+allarms.c, from tools/s4/mb_edges.py). It prints
X87_FALLBACK_PROBE_OK when all 60 calls (tan, sin and cos of ten arguments, finite, huge, NaN
and infinite, under the control words 0x027F and 0x007F) return the host libm's value with no
trap, and exits 1 otherwise. With an exception removed and the table regenerated, every call
to that function traps at its address.

Work and output: private/generated/s8/x87-probe/ (gitignored).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import release_build as rb  # noqa: E402

PROBE = ROOT / "harness" / "x87_fallback_probe.c"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
BASE = ROOT / "private" / "generated" / "s8" / "x87-probe"
OVERLAY = ROOT / "private" / "generated" / "s7" / "exe-overlay" / "sa2_exe_overlay_table.c"


def main() -> int:
    work, out = BASE / "work", BASE / "out"
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
    f80 = rb.F80_OBJECTS["x86_64-linux"].resolve()
    log = BASE / "link.log"
    with log.open("w") as fh:
        stdout, sys.stdout = sys.stdout, fh
        try:
            rb.step_game(ROOT, work, out, f80, rb.f80_build(f80, rb.F80_TRIPLES["x86_64-linux"], "double"))
        finally:
            sys.stdout = stdout
    binary = out / "bin" / rb.GAME
    run = subprocess.run([str(binary), str(EXE)], capture_output=True, text=True, timeout=300)
    (BASE / "run.txt").write_text(run.stdout)
    lines = run.stdout.splitlines()
    for line in lines:
        if "DIFFERS" in line or line.startswith("X87_FALLBACK_PROBE"):
            print(line)
    if not lines or not lines[-1].startswith("X87_FALLBACK_PROBE_OK") or run.returncode:
        print(f"see {(BASE / 'run.txt').relative_to(ROOT)} and {log.relative_to(ROOT)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
