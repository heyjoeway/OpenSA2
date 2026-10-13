#!/usr/bin/env python3
"""host2_win_run: host 2's Windows player on the registered Metal Harbor stream, under Proton's Wine.

Subtitle: the x86_64-windows target's host evidence. The Windows player that
tools/s7/host2_win_build.py builds replays the stream that G5's host comparison replayed
(`Documentation/receipts/s5-host-compare-v1.json`, its `stream_sha256`), with its SDL window
created hidden and every frame read back before Present, under Proton's Wine in a scratch
WINEPREFIX inside bwrap with Steam's tree bound read-only. No input is sent. Its frames table
is then compared row by row with the x86_64-linux comparison's first host, in the columns G5's
predicate compares (`check_s5_exit.COMPARED`), and the evidence
`Documentation/receipts/s7-x86_64-windows-host-v1.json` (schema `sa2-s7-target-host/v1`,
`Documentation/s7-green-means.md`, `host`) is written from a run that exited zero. The verdict
is tools/s7/target_receipt.py's, recomputed from the evidence; the comparison here is printed
so a run is read at once.

The y4m the player writes (about 22 GB for Metal Harbor) is deleted once the run ends; its
times table, beside it, is the frames table the evidence cites by sha256.

  .venv/bin/python tools/s7/host2_win_run.py                  # run, compare, write the evidence
  .venv/bin/python tools/s7/host2_win_run.py --evidence RUN   # write it from a finished run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))

import check_s5_exit as S5  # noqa: E402
import host2_win_build as HB  # noqa: E402
import wine_run as WR  # noqa: E402

SELF = "tools/s7/host2_win_run.py"
TARGET = "x86_64-windows"
REPLAY = "metalharbor"
SCHEMA = "sa2-s7-target-host/v1"
EVIDENCE = ROOT / "Documentation" / "receipts" / f"s7-{TARGET}-host-v1.json"
HOST_COMPARE = ROOT / "Documentation" / "receipts" / "s5-host-compare-v1.json"
STREAM = ROOT / "private" / "generated" / "s5" / "pushC" / "record" / "trace" / "metalharbor.sa2s"
PLAYER = ROOT / "private" / "generated" / "s7" / "host2-win" / "bin" / "sa2_player_gpu.exe"
RUNS = ROOT / "private" / "generated" / "s7" / "host2win-run"
PREFIX = RUNS / "prefix"
WINE = WR.PROTON / "wine"
MODE = "--hidden"
PLAYER_ENV = {"SDL_AUDIO_DRIVER": "dummy", "SDL_NO_SIGNAL_HANDLERS": "1",
              "VK_LOADER_LAYERS_DISABLE": "~implicit~"}
TIMEOUT_S = 3600


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def winpath(path: Path) -> str:
    return "Z:" + str(path.resolve())


def wrapped(argv: list[str], cwd: Path) -> list[str]:
    """wine_run's bwrap (Steam's tree read-only), working in the run's directory: the player
    writes its log beside its cwd, and refuses a path in the working tree outside private/."""
    w = WR.wrapped(argv, PREFIX)
    w[w.index("--chdir") + 1] = str(cwd)
    return w


def reference() -> tuple[str, str, Path]:
    """The x86_64-linux comparison's stream sha256, and its first host's name and frames."""
    doc = json.loads(HOST_COMPARE.read_text())
    frames = {h["host"]: h["frames"] for h in doc["hosts"]}
    first = sorted(frames)[0]
    return doc["stream_sha256"], first, ROOT / frames[first]["path"]


def compare(mine: Path, theirs: Path) -> dict:
    a = S5.table(mine, S5.TIMES_HEADER, "the Windows frames")
    b = S5.table(theirs, S5.TIMES_HEADER, "the x86_64-linux frames")
    for x, y in zip(a, b):
        for column in S5.COMPARED:
            if x[column] != y[column]:
                return {"rows": [len(a), len(b)], "equal": False,
                        "first_difference": {"frame": y["frame"], "column": column,
                                             "windows": x[column], "linux": y[column]}}
    return {"rows": [len(a), len(b)], "equal": len(a) == len(b), "first_difference": None}


def run(player: Path, out: Path) -> dict:
    out.mkdir(parents=True)
    player = player.resolve()                # the player runs in `out`
    ran = sha256(player)                     # before the run: a rebuild may replace the file
    HB.wait_for_memory("host 2 under Wine")
    env = WR.wine_environ(os.environ, PREFIX)
    for var in WR.DISPLAY_VARS:              # the player opens a (hidden) window
        if var in os.environ:
            env[var] = os.environ[var]
    env.update(PLAYER_ENV)
    env.update(SA2_PLAY_OUT=winpath(out / "play-out"),
               SA2_D3D9GPU_REPORT=winpath(out / "d3d9gpu.runrec"))
    argv = [str(WINE), str(player), winpath(STREAM), "--stage", "3",
            "--frames-out", winpath(out / "frames.y4m"),
            "--report", winpath(out / "report.runrec"), MODE]
    start = time.monotonic()
    with (out / "player.out").open("w") as so, (out / "player.err").open("w") as se:
        try:
            rc = subprocess.run(wrapped(argv, out), env=env, stdout=so, stderr=se,
                                timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    seconds = round(time.monotonic() - start, 1)
    subprocess.run([str(WR.PROTON / "wineserver"), "-k"], env=env, capture_output=True)
    version = subprocess.run(wrapped([str(WINE), "--version"], out), env=env,
                             capture_output=True, text=True, timeout=120).stdout.strip()
    y4m = out / "frames.y4m"
    if y4m.is_file():
        y4m.unlink()                         # ~22 GB; its times table is the evidence
    facts = {"exit": rc, "seconds": seconds, "wine_version": version, "argv": argv[1:],
             "player": {"path": rel(player) if player.is_relative_to(ROOT) else str(player),
                        "sha256": ran}}
    if sha256(player) != ran:
        facts["exit"] = f"the player was replaced during the run (it ran {ran})"
    (out / "run.json").write_text(json.dumps(facts, indent=1) + "\n")
    return facts


def evidence(out: Path) -> int:
    facts = json.loads((out / "run.json").read_text())
    stream_sha, first, theirs = reference()
    times = out / "frames.y4m.times.tsv"
    if facts["exit"] != 0 or not times.is_file():
        print(f"HOST2_WIN_RUN_FAILED: the player exited {facts['exit']}; no evidence written "
              f"(see {rel(out / 'player.out')})")
        return 1
    result = compare(times, theirs)
    print(f"host 2 under Wine: exit {facts['exit']}, {facts['seconds']} s; frames "
          f"{result['rows'][0]} vs {first} {result['rows'][1]}; equal {result['equal']}; "
          f"first difference {result['first_difference']}")
    doc = {
        "schema": SCHEMA,
        "generated_by": SELF,
        "written": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": TARGET,
        "replay": REPLAY,
        "stream_sha256": stream_sha,
        "runner": {"kind": "wine", "wine": str(WINE), "wine_version": facts["wine_version"],
                   "mode": f"{MODE}: the SDL window created hidden; every frame read back "
                           f"before Present; no input sent"},
        "binary_sha256": facts["player"]["sha256"],
        "binary": facts["player"]["path"],
        "frames": {"path": rel(times), "sha256": sha256(times)},
        "report": {"path": rel(out / "report.runrec"), "sha256": sha256(out / "report.runrec")},
        "exit": facts["exit"],
        "seconds": facts["seconds"],
        "compared_with": first,
        "compare": result,
    }
    EVIDENCE.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"{'HOST2_WIN_RUN_OK' if result['equal'] else 'HOST2_WIN_RUN_DIFFERS'}: "
          f"wrote {rel(EVIDENCE)}")
    return 0 if result["equal"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--player", type=Path, default=PLAYER)
    ap.add_argument("--out", type=Path, help="the run's directory (default: a new one under "
                    f"{rel(RUNS)})")
    ap.add_argument("--evidence", type=Path, metavar="RUN",
                    help="write the evidence from a finished run's directory; run nothing")
    args = ap.parse_args(argv)
    if args.evidence:
        return evidence(args.evidence)
    stream_sha, _, _ = reference()
    if sha256(STREAM) != stream_sha:
        print(f"{rel(STREAM)} is not the stream the x86_64-linux comparison replayed")
        return 2
    out = args.out or RUNS / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if out.exists():
        print(f"{out} exists")
        return 2
    run(args.player, out)
    return evidence(out)


if __name__ == "__main__":
    sys.exit(main())
