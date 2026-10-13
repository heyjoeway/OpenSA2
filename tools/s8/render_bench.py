#!/usr/bin/env python3
"""render_bench: host 2's player speed on Metal Harbor, Linux and Windows-under-Wine, in ms/frame.

Subtitle: one command that runs the GPU player (host/d3d9gpu/build.sh's sa2_player_gpu, and
tools/s7/host2_win_build.py's sa2_player_gpu.exe under Proton's Wine) on the registered Metal
Harbor stream for the first --frames frames, unpaced and with its window hidden, and prints each
build's player time per frame: the "wall" of the player's own "stage 3:" line (the frames from
the first record to the last Present, so neither a device's start nor Wine's prefix setup is
counted) divided by its presents.

  .venv/bin/python tools/s8/render_bench.py                       # both default builds
  .venv/bin/python tools/s8/render_bench.py --linux A --linux B   # several builds, in order
  .venv/bin/python tools/s8/render_bench.py --windows W.exe --no-linux
  .venv/bin/python tools/s8/render_bench.py --check-frames ...    # and every frame's hash
  .venv/bin/python tools/s8/render_bench.py --env LD_PRELOAD=P.so --env SA2_PROF_OUT=D ...
                                                                  # a profiler in each player

--check-frames also reads every frame back (--frames-out) and holds each run's times table, in
the columns G5's predicate compares (Documentation/scripts/check_s5_exit.py COMPARED), to the
first --frames rows of the x86_64-linux host comparison's first host
(Documentation/receipts/s5-host-compare-v1.json); the y4m is deleted when the run ends (about
1.4 MB a frame), its times table kept. Reading back costs time: a speed is read from a run
without it.

Every run goes through tools/slot.sh (one of three machine-wide slots, 8 GB free). A Windows
run is wrapped as tools/s7/host2_win_run.py wraps it: bwrap with Steam's tree read-only, a
scratch WINEPREFIX under the output directory (removed, by tools/rmtree.py, when the bench ends),
the player's working directory outside the repository. Outputs go under --out (default private/generated/s8/render/bench/<UTC time>):
one directory per run with the player's stdout, stderr and report, and bench.json, the runs
with their binaries' sha256 and ms/frame. Prints RENDER_BENCH_OK, or RENDER_BENCH_FAILED and
exits 1 when a run fails or (with --check-frames) a frame differs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))

STREAM = ROOT / "private" / "generated" / "s5" / "pushC" / "record" / "trace" / "metalharbor.sa2s"
LINUX = ROOT / "private" / "generated" / "s5" / "play" / "bin" / "sa2_player_gpu"
WINDOWS = ROOT / "private" / "generated" / "s7" / "host2-win" / "bin" / "sa2_player_gpu.exe"
OUT = ROOT / "private" / "generated" / "s8" / "render" / "bench"
SLOT = ROOT / "tools" / "slot.sh"
HOST_COMPARE = ROOT / "Documentation" / "receipts" / "s5-host-compare-v1.json"
PLAYER_ENV = {"SDL_AUDIO_DRIVER": "dummy", "SDL_NO_SIGNAL_HANDLERS": "1",
              "VK_LOADER_LAYERS_DISABLE": "~implicit~"}
STAGE3 = re.compile(r"^stage 3: presents (\d+) .*?, draws (\d+), wall ([0-9.]+) s", re.M)
TIMEOUT_S = 3600


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def winpath(path: Path) -> str:
    return "Z:" + str(path.resolve())


def parse_stage3(text: str) -> dict | None:
    """The player's "stage 3:" line: presents, draws and wall seconds, and ms per frame."""
    m = STAGE3.search(text)
    if not m:
        return None
    presents, draws, wall = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return {"presents": presents, "draws": draws, "wall_s": wall,
            "ms_per_frame": round(1000.0 * wall / presents, 3) if presents else None}


def compare_rows(mine: Path, rows: int) -> dict:
    """The run's times table against the first `rows` rows of the host comparison's first host,
    in check_s5_exit's COMPARED columns (tools/s7/host2_win_run.py compare(), over a prefix)."""
    import check_s5_exit as S5
    doc = json.loads(HOST_COMPARE.read_text())
    frames = {h["host"]: h["frames"] for h in doc["hosts"]}
    first = sorted(frames)[0]
    a = S5.table(mine, S5.TIMES_HEADER, "the run's frames")
    b = S5.table(ROOT / frames[first]["path"], S5.TIMES_HEADER, f"{first}'s frames")[:rows]
    for x, y in zip(a, b):
        for column in S5.COMPARED:
            if x[column] != y[column]:
                return {"rows": [len(a), len(b)], "equal": False, "compared_with": first,
                        "first_difference": {"frame": y["frame"], "column": column,
                                             "run": x[column], "reference": y[column]}}
    return {"rows": [len(a), len(b)], "equal": len(a) == len(b), "compared_with": first,
            "first_difference": None}


def run_one(kind: str, player: Path, out: Path, frames: int, check: bool, extra_env: dict | None = None) -> dict:
    out.mkdir(parents=True)
    player = player.resolve()
    ran = sha256(player)
    y4m = out / "frames.y4m"
    cwd = Path(tempfile.mkdtemp(prefix="render-bench-"))   # outside the repository
    env = dict(os.environ)
    if kind == "linux":
        argv = [str(player), str(STREAM), "--stage", "3", "--hidden", "--max-frames", str(frames),
                "--report", str(out / "report.runrec")]
        if check:
            argv += ["--frames-out", str(y4m)]
        env.update(PLAYER_ENV)
        env["SA2_PLAY_OUT"] = str(out / "play-out")
        cmd = [str(SLOT)] + argv
    else:
        import wine_run as WR
        prefix = out.parent / "wine-prefix"
        prefix.mkdir(exist_ok=True)
        env = WR.wine_environ(os.environ, prefix)
        for var in WR.DISPLAY_VARS:              # the player opens a (hidden) window
            if var in os.environ:
                env[var] = os.environ[var]
        env.update(PLAYER_ENV)
        env.update(SA2_PLAY_OUT=winpath(out / "play-out"))
        argv = [str(WR.PROTON / "wine"), str(player), winpath(STREAM), "--stage", "3", "--hidden",
                "--max-frames", str(frames), "--report", winpath(out / "report.runrec")]
        if check:
            argv += ["--frames-out", winpath(y4m)]
        wrapped = WR.wrapped(argv, prefix)
        wrapped[wrapped.index("--chdir") + 1] = str(cwd)
        cmd = [str(SLOT)] + wrapped
    env.update(extra_env or {})
    start = time.monotonic()
    with (out / "player.out").open("w") as so, (out / "player.err").open("w") as se:
        try:
            rc = subprocess.run(cmd, env=env, cwd=cwd, stdout=so, stderr=se, timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    seconds = round(time.monotonic() - start, 1)
    if kind == "windows":
        import wine_run as WR
        subprocess.run([str(WR.PROTON / "wineserver"), "-k"], env=env, capture_output=True)
    shutil.rmtree(cwd, ignore_errors=True)    # a fresh mkdtemp directory of this run's own
    if y4m.is_file():
        y4m.unlink()                          # ~1.4 MB a frame; the times table is kept
    stage3 = parse_stage3((out / "player.out").read_text(errors="replace"))
    result = {"kind": kind, "player": str(player), "sha256": ran, "exit": rc, "seconds": seconds,
              "dir": str(out), "stage3": stage3}
    if check and rc == 0:
        times = out / "frames.y4m.times.tsv"
        result["frames"] = compare_rows(times, frames) if times.is_file() else {"equal": False, "error": "no times table"}
    if sha256(player) != ran:
        result["exit"] = f"the player was replaced during the run (it ran {ran})"
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--linux", type=Path, action="append", help=f"a Linux build (default {LINUX.relative_to(ROOT)})")
    ap.add_argument("--windows", type=Path, action="append", help=f"a Windows build (default {WINDOWS.relative_to(ROOT)})")
    ap.add_argument("--no-linux", action="store_true")
    ap.add_argument("--no-windows", action="store_true")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--repeat", type=int, default=1, help="runs per build (the report keeps each)")
    ap.add_argument("--check-frames", action="store_true", help="read every frame back and compare its hash")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                    help="set in each player's environment (a profiler's LD_PRELOAD, say)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    extra_env = dict(e.split("=", 1) for e in args.env)
    if not STREAM.is_file():
        print(f"RENDER_BENCH_FAILED: no stream at {STREAM}")
        return 1
    builds = []
    if not args.no_linux:
        builds += [("linux", p) for p in (args.linux or [LINUX])]
    if not args.no_windows:
        builds += [("windows", p) for p in (args.windows or [WINDOWS])]
    out = (args.out or OUT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    if out.exists() and any(out.iterdir()):
        print(f"RENDER_BENCH_FAILED: {out} exists and is not empty")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    results, ok = [], True
    for n, (kind, player) in enumerate(builds):
        for r in range(args.repeat):
            res = run_one(kind, player, out / f"{n}-{kind}-{r}", args.frames, args.check_frames, extra_env)
            results.append(res)
            s = res["stage3"]
            line = (f"{kind:7s} {res['sha256'][:12]} {player}: exit {res['exit']}, "
                    + (f"{s['presents']} frames, {s['draws']} draws, {s['wall_s']} s, {s['ms_per_frame']} ms/frame"
                       if s else "no stage 3 line"))
            if "frames" in res:
                f = res["frames"]
                line += f"; frames equal {f.get('equal')} {f.get('rows')} {f.get('first_difference') or ''}"
                ok &= bool(f.get("equal"))
            print(line, flush=True)
            ok &= res["exit"] == 0 and s is not None and s["presents"] == args.frames
    (out / "bench.json").write_text(json.dumps({"stream": str(STREAM), "frames": args.frames,
                                                "check_frames": args.check_frames, "runs": results}, indent=1) + "\n")
    prefix = out / "wine-prefix"             # the Windows runs' scratch prefix (about 600 MB)
    if prefix.is_dir():
        subprocess.run([sys.executable, str(ROOT / "tools" / "rmtree.py"), str(prefix)], check=False)
    print(f"{'RENDER_BENCH_OK' if ok else 'RENDER_BENCH_FAILED'}: {out / 'bench.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
