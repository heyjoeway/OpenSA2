"""Shared helpers for the S1 lifter-spike drivers (tools/spike/lift_*.py).

Compile policy, the same for every lifter so the receipt's columns compare:
  c_strict : `zig cc -c -w -o /dev/null FILE.c`      (clang 20, default C dialect)
  c_gnu89  : `zig cc -std=gnu89 -c -w -o /dev/null`   (implicit declarations tolerated)
  gcc      : `gcc -c -w -o /dev/null FILE.c`          (gcc 14.2, default dialect)
  ir       : `zig cc -target x86-linux-musl -c -o /dev/null FILE.ll`
"first compilable output" in the receipt means one of these returned 0 for any
output of the lifter; the timeline event names which.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMELINE = ROOT / "tools" / "spike" / "timeline.jsonl"
FUNCTIONS = ROOT / "private" / "generated" / "spike" / "functions.json"
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"

COMPILERS = {
    "c_strict": [str(ZIG), "cc", "-c", "-w", "-o", "/dev/null"],
    "c_gnu89": [str(ZIG), "cc", "-std=gnu89", "-c", "-w", "-o", "/dev/null"],
    "gcc": ["gcc", "-c", "-w", "-o", "/dev/null"],
    "ir": [str(ZIG), "cc", "-target", "x86-linux-musl", "-c", "-o", "/dev/null"],
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timeline(lifter: str, event: str, detail: str) -> None:
    with TIMELINE.open("a") as fh:
        fh.write(json.dumps({"ts": now(), "lifter": lifter, "event": event, "detail": detail}) + "\n")


def functions() -> list[dict]:
    return json.loads(FUNCTIONS.read_text())["functions"]


def error_head(text: str, lines: int = 3) -> str:
    return "\n".join(text.strip().splitlines()[:lines])


def compile_file(kind: str, path: Path) -> dict:
    """Run one compiler over one output file; returns {ok, command, rc, error_head}."""
    cmd = COMPILERS[kind] + [str(path)]
    # zig cc 0.15.2 writes <stem>.o into the cwd on a successful `-c -o /dev/null`
    # (observed 2026-09-20: twelve objects appeared in the repository root and
    # were deleted), so every compile runs in a scratch directory outside it.
    scratch = Path(os.environ.get("SA2_SPIKE_SCRATCH", "/tmp")) / "sa2-spike-compile"
    scratch.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=scratch)
    return {
        "ok": proc.returncode == 0,
        "command": " ".join(cmd),
        "rc": proc.returncode,
        "error_head": "" if proc.returncode == 0 else error_head(proc.stderr or proc.stdout),
        "warnings": len([l for l in proc.stderr.splitlines() if "warning:" in l]),
    }


def line_count(path: Path) -> int:
    return sum(1 for _ in path.open("rb"))


def write_results(path: Path, lifter: str, results: list[dict], extra: dict | None = None) -> None:
    payload = {"lifter": lifter, "written": now(), "results": results}
    payload.update(extra or {})
    path.write_text(json.dumps(payload, indent=2) + "\n")
