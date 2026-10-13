#!/usr/bin/env python3
"""Build and drive the guest file system's unit probe, with and without folding.

Subtitle: `zig cc harness/vfs_probe.c runtime/sa2_files.c`, run twice, plus the
one cross-run comparison a single run cannot make.

Why a tool and not two commands
-------------------------------
The probe's own verdict says the mapping table is right. It cannot say that the
case-insensitive fallback is *load bearing*, because it adapts its expectations
to `SA2_VFS_NO_CASE_FOLD` -- a probe that passes either way has not measured the
mechanism. That comparison lives here: the folding cases must answer `ok` with
folding on and `not-found` with it off, and this refuses if they do not.

Every run's JSON is kept, including a failing one, because the run that gets
deleted is always the one that failed (operating notes, S3).

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/vfs_check.py [--out-dir private/generated/s4/vfs]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

import spikelib as S  # noqa: E402

TARGET = ["-target", "x86_64-linux-musl"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--install", default=None, help="the host read root; default $SA2_INSTALL")
    args = ap.parse_args()

    out = Path(args.out_dir) if args.out_dir else (C.GEN / "s4" / "vfs")
    out.mkdir(parents=True, exist_ok=True)
    binary = out / "vfs_probe"
    cmd = [str(S.ZIG), "cc", *TARGET, "-O2", "-Wall", "-Wextra", "-Werror",
           "-I", str(C.ROOT / "runtime"),
           str(C.ROOT / "harness" / "vfs_probe.c"), str(C.ROOT / "runtime" / "sa2_files.c"),
           "-o", str(binary)]
    print("$ zig cc harness/vfs_probe.c runtime/sa2_files.c", flush=True)
    p = subprocess.run(cmd, cwd=str(C.ROOT), capture_output=True, text=True, timeout=600)
    if p.returncode:
        print(p.stderr[-6000:])
        raise SystemExit("vfs_probe did not compile")

    install = args.install or os.environ.get("SA2_INSTALL") or str(C.ROOT / "private" / "install")
    write_root = out / "overlay"
    runs = {}
    for name, extra in (("fold", {}), ("nofold", {"SA2_VFS_NO_CASE_FOLD": "1"})):
        jf = out / f"vfs.{name}.json"
        env = dict(os.environ, SA2_INSTALL=install, SA2_VFS_WRITE_ROOT=str(write_root), **extra)
        r = subprocess.run([str(binary), "--json", str(jf)], cwd=str(C.ROOT),
                           capture_output=True, text=True, timeout=600, env=env)
        doc = json.loads(jf.read_text()) if jf.is_file() else {"ok": False, "why": "no json"}
        runs[name] = {"rc": r.returncode, "json": str(jf.relative_to(C.ROOT)), "ok": doc.get("ok"),
                      "tally": doc.get("tally"), "stderr_tail": r.stderr[-2000:]}
        print(f"{name}: rc={r.returncode} ok={doc.get('ok')} tally={doc.get('tally')}", flush=True)

    # The cross-run comparison. Both operands are named cases, so neither side
    # can be empty: a table that stopped emitting cases fails here.
    a = json.loads((out / "vfs.fold.json").read_text())
    b = json.loads((out / "vfs.nofold.json").read_text())
    folding_cases = [c["guest"] for c in a["cases"]
                     if c["got"] == "ok" and c["host_leaf"] and c["rel"]
                     and c["host_leaf"] != c["rel"].split("/")[-1]]
    moved = []
    for g in folding_cases:
        ca = next(c for c in a["cases"] if c["guest"] == g)
        cb = next(c for c in b["cases"] if c["guest"] == g)
        moved.append({"guest": g, "with_fold": ca["got"], "without_fold": cb["got"],
                      "moved": ca["got"] == "ok" and cb["got"] == "not-found"})
    fold_is_load_bearing = bool(moved) and all(m["moved"] for m in moved)

    verdict = {
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "binary": str(binary.relative_to(C.ROOT)),
        "install_read_root": a.get("read_root"),
        "write_root": a.get("write_root"),
        "runs": runs,
        "case_fold_is_load_bearing": fold_is_load_bearing,
        "case_fold_evidence": moved,
        "ok": bool(runs["fold"]["ok"]) and bool(runs["nofold"]["ok"]) and fold_is_load_bearing,
    }
    vf = out / "vfs.verdict.json"
    vf.write_text(json.dumps(verdict, indent=1) + "\n")
    print(f"-> {vf}   ok={verdict['ok']} fold_load_bearing={fold_is_load_bearing}")
    raise SystemExit(0 if verdict["ok"] else 1)


if __name__ == "__main__":
    main()
