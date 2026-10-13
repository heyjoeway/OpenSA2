#!/usr/bin/env python3
"""perf_game: the lifted game's own CPU cost per tick on Metal Harbor, and where it goes.

Subtitle: `perf_game.py build` links a measurement build of Metal Harbor's registered replay,
`perf_game.py run` runs it with per-tick stamps (and optionally the CPU sampler, the replay's
observers, the recorder's stream drained by a fast reader), and `perf_game.py report` prints
ticks/s and ms/tick per phase and the profile split by source.

    source tools/env.sh
    .venv/bin/python tools/s8/perf_game.py build --variant plain        # no recorder
    .venv/bin/python tools/s8/perf_game.py build --variant rec          # with host/sa2_present_rec.c
    .venv/bin/python tools/s8/perf_game.py build --variant plain --lifted DIR --name x86f80
        # DIR: a tools/s7/cross_build.py (or tools/s8/perf_f80.py) output root (build.jsonl,
        # f80_shims.o) whose objects replace the x86-64 build's lifted objects (a soft-f80 or
        # double-mode build of x86-64, say); --shims OBJ takes another shims object (a counting
        # build); --scene boot builds the boot replay's hooks instead of Metal Harbor's
    tools/slot.sh .venv/bin/python tools/s8/perf_game.py run --build plain --label a [--prof]
        [--instrument] [--rec] [--max-ticks N] [--env VAR=VALUE ...]
    .venv/bin/python tools/s8/perf_game.py report RUN_DIR [RUN_DIR ...]

What it builds. Metal Harbor's replay binary as tools/s4/stage_run.py's HOOKS and WRAPS make it
(the test spawn, the recorded controller, the goal probe, the tick probe) with one change: the
tick probe's GameLoop wrap is renamed and wrapped by tools/s8/perf_probe.c, which stamps every
GameLoop return (SA2_PERF_TICKS) and carries the SIGPROF sampler (SA2_PROF_OUT). The runtime is
compiled from the tree with link_s4's command and the lifted objects are the staged x86-64 ones
unless --lifted names another set. Everything lands under private/generated/s8/perf-game/.

What it runs. The registered replay's environment (private/generated/s4/run/
replay-<scene>.matrix.json, its first row): the recording's input, the capture's device
profile, the pump and virtual-time budgets that end the run where the registered one ends. The
observers (SA2_REPLAY_TICKS, SA2_REPLAY_WORDS, SA2_STAGE_TRACE, SA2_GOAL_LATCH,
SA2_INPUT_APPLIED) are dropped unless --instrument keeps them, writing into the run directory;
then the tick capture is compared with the registered one byte for byte (`capture_equal`). With
--rec the recorder writes its stream (SA2_REC_TRACE, SA2_REC_AUDIO=1 as the product sets it)
into a FIFO that a reader thread drains as fast as it comes, counting bytes.

The runtime's clock is virtual (runtime/sa2_threads.c): a frame wait advances it rather than
sleeping, so a headless run's ticks/s is the game's own CPU cost, not 60 Hz.

What it reports. Ticks are split into phases by the guest's own state: `gameplay` is a tick at
which FrameCountIngame (0x0174B03C) advanced, everything else is `other` (boot, loading, the
test spawn's setup). Per phase: ticks, wall and CPU ms per tick (mean, p50, p95, p99) and
ticks/s. With samples (--prof), each sample is attributed to the source it fell in: `lifted`
(a sub_<VA> body), each runtime object (by the defined symbols of its .o), the f80 shims, and
`libc/compiler-rt` for what no object of the build defines; and the top functions overall.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import importlib.util
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GEN = ROOT / "private" / "generated"
PG = GEN / "s8" / "perf-game"
TAG = "next+allarms"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
SCENES = ("metalharbor", "boot")
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-tick-replay-v1.json"
PROBE = ROOT / "harness" / "replay" / "wrap_tick_probe.c"
PERF_PROBE = HERE / "perf_probe.c"
RECORDER = ROOT / "host" / "sa2_present_rec.c"
OBSERVERS = ("SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_STAGE_TRACE", "SA2_GOAL_LATCH",
             "SA2_INPUT_APPLIED")
SAMPLE = struct.Struct("<QQQQII")        # tools/s8/perf_probe.c: pc, sp[0], sp[1], fp[1], tick, tid
LEAVES = ("__remill_read_memory_", "__remill_write_memory_", "sa2_guest_read_slow",
          "sa2_guest_write_slow")


def literal(path: Path, name: str):
    import ast
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name
                                                for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{path} has no literal {name}")


def load_link():
    sys.path[:0] = [str(ROOT / "tools" / "s3" / "whole_image"), str(ROOT / "tools" / "spike"),
                    str(ROOT / "tools" / "s4")]
    os.environ.setdefault("ZIG_GLOBAL_CACHE_DIR", str(PG / "zig-global"))
    os.environ.setdefault("ZIG_LOCAL_CACHE_DIR", str(PG / "zig-local"))
    spec = importlib.util.spec_from_file_location("perf_link_s4", ROOT / "tools/s4/link_s4.py")
    link = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(link)
    (PG / "s3").mkdir(parents=True, exist_ok=True)
    if not (PG / "s3" / "link").exists():
        (PG / "s3" / "link").symlink_to(GEN / "s3" / "link")
    link.OUT = PG / "s4" / "link"
    return link


def lifted_linkdir(root: Path, name: str) -> tuple[Path, list[Path], list[Path]]:
    """A cross_build output's map and arm objects as a link directory (hard links), its extra
    objects in the link's order, and its shims object."""
    recs = [json.loads(l) for l in (root / "build.jsonl").read_text().splitlines() if l.strip()]
    want = {Path(r["object"]).name: root / r["object"] for r in recs
            if r.get("compiled") and r["population"] in ("map", "arms")}
    have_extra = {Path(r["object"]).name for r in recs
                  if r.get("compiled") and r["population"] == "extra"}
    linkroot = PG / "lifted" / name
    objdir = linkroot / TAG
    objdir.mkdir(parents=True, exist_ok=True)
    for t in objdir.glob("ir_*.o"):
        if t.name not in want:
            t.unlink()
    for oname, o in want.items():
        t = objdir / oname
        if t.exists() and not t.samefile(o):
            t.unlink()
        if not t.exists():
            os.link(o, t)
    extras = json.loads((GEN / "s4" / "link" / f"extra_functions.{TAG}.json").read_text())["objects"]
    missing = [Path(o).name for o in extras if Path(o).name not in have_extra]
    if missing:
        raise SystemExit(f"{len(missing)} extra objects the link needs are not in {root}")
    return linkroot, [root / "extra" / Path(o).name for o in extras], [root / "f80_shims.o"]


def cmd_build(a) -> int:
    link = load_link()
    name = a.name or a.variant
    gen = GEN / "s4" / "link"
    functions = json.loads((gen / f"extra_functions.{TAG}.json").read_text())
    extra_o = [Path(o) for o in functions.get("objects") or []]
    base = [gen / f"mb_edges.{TAG}.c"] + ([gen / f"extra_functions.{TAG}.c"] if extra_o else [])
    if a.scene == "boot":
        hooks = [ROOT / literal(ROOT / "tools/s4/tick_replay.py", "HOOK")]
        wraps = [literal(ROOT / "tools/s4/tick_replay.py", "WRAP")]
    else:
        hooks = [ROOT / h for h in literal(ROOT / "tools/s4/stage_run.py", "HOOKS")]
        wraps = list(literal(ROOT / "tools/s4/stage_run.py", "WRAPS"))
    unit = PG / "units" / name / "perf_observer.c"
    unit.parent.mkdir(parents=True, exist_ok=True)
    text = ("/* Generated by tools/s8/perf_game.py; do not edit. */\n#define _GNU_SOURCE\n"
            "#define __wrap_sub_433ee0 sa2_perf_probe_gameloop\n"
            f'#include "{PROBE}"\n#undef __wrap_sub_433ee0\n#include "{PERF_PROBE}"\n')
    if not unit.is_file() or unit.read_text() != text:
        unit.write_text(text)
    os.utime(unit)                     # link_s4's freshness rule: the includes are elsewhere
    hooks = [unit if h == PROBE else h for h in hooks]
    if a.variant == "rec":
        hooks.append(RECORDER)
        sys.path.insert(0, str(ROOT / "tools" / "s5"))
        import objcheck as OC  # noqa: PLC0415
        wraps += list(OC.WRAPS)
    extra = base + hooks
    shims: list[Path] = []
    if a.lifted:
        linkroot, extra_o, shims = lifted_linkdir(Path(a.lifted).resolve(), name)
        link.C.BUILD = linkroot
        if a.shims:
            shims = [Path(a.shims).resolve()]
    state = {"name": f"perf-{name}", "tag": TAG}
    t0 = time.time()
    link.phase_compile(state, f"perf-{name}", TAG, ROOT / "harness" / "crt_probe.c", a.rebuild,
                       extra)
    if not state.get("compile_ok"):
        print(json.dumps({k: v.get("stderr_tail", "")[-1500:] for k, v in state["compile"].items()
                          if v["rc"]}, indent=1))
        return 1
    link.phase_link(state, f"perf-{name}", TAG, [], extra, extra_o + shims, wraps)
    if state["link"]["rc"]:
        return 1
    binary = link.OUT / "mains" / f"perf-{name}" / "sa2_main"
    info = {"name": name, "variant": a.variant, "scene": a.scene, "lifted": a.lifted or "staged",
            "shims": [str(x) for x in shims],
            "binary": str(binary), "sha256": state["link"].get("sha256"), "hooks": [str(h) for h in hooks],
            "wraps": wraps, "objdir": str(link.OUT / "mains" / f"perf-{name}" / "obj"),
            "seconds": round(time.time() - t0, 1)}
    (PG / "builds").mkdir(parents=True, exist_ok=True)
    (PG / "builds" / f"{name}.json").write_text(json.dumps(info, indent=1) + "\n")
    print(json.dumps(info, indent=1))
    return 0


# ------------------------------------------------------------------ running


def drain(path: Path, counted: dict, stop: threading.Event) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        import fcntl  # noqa: PLC0415
        fcntl.fcntl(fd, 1031, 1 << 20)          # F_SETPIPE_SZ: a 1 MB pipe, as a player's reader
    except OSError:
        pass
    try:
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            counted["bytes"] += len(b)
    finally:
        os.close(fd)


def cmd_run(a) -> int:
    info = json.loads((PG / "builds" / f"{a.build}.json").read_text())
    out = PG / "runs" / a.label
    if out.exists():
        shutil.rmtree(out)
    (out / "vfsroot").mkdir(parents=True)
    sys.path[:0] = [str(ROOT / "tools" / "s4")]
    import reach_receipt  # noqa: PLC0415  clean_environ only
    env = dict(reach_receipt.clean_environ())
    env.update({"SA2_INSTALL": "private/install", "SA2_VFS_WRITE_ROOT": str(out / "vfsroot"),
                "SDL_AUDIO_DRIVER": "dummy"})
    scene = info.get("scene", "metalharbor")
    matrix = GEN / "s4" / "run" / f"replay-{scene}.matrix.json"
    row_env = dict(json.loads(matrix.read_text())["rows"][0]["env"])
    for var in OBSERVERS:
        if var in row_env:
            if a.instrument:
                row_env[var] = str(out / ("lifted.csv" if var == "SA2_REPLAY_TICKS"
                                          else var.lower()))
            else:
                del row_env[var]
    env.update(row_env)
    env["SA2_PERF_TICKS"] = str(out / "ticks.txt")
    if a.prof:
        env["SA2_PROF_OUT"] = str(out)
        if a.prof_us:
            env["SA2_PROF_US"] = str(a.prof_us)
    counted = {"bytes": 0}
    reader = None
    if a.rec:
        if info["variant"] != "rec":
            raise SystemExit(f"--rec needs a build with the recorder; {a.build} is {info['variant']}")
        fifo = out / "stream.fifo"
        os.mkfifo(fifo)
        env.update({"SA2_REC_TRACE": str(fifo), "SA2_REC_AUDIO": "1"})
        reader = threading.Thread(target=drain, args=(fifo, counted, threading.Event()), daemon=True)
        reader.start()
    for kv in a.env or []:
        k, v = kv.split("=", 1)
        env[k] = v
    argv = [info["binary"], str(EXE), "--json", str(out / "run.runrec")]
    if scene == "metalharbor":
        argv += ["--timeout", "3600"]
    (out / "command.json").write_text(json.dumps({"argv": argv, "env": {k: v for k, v in env.items()
                                                  if k.startswith("SA2_")}, "build": info},
                                                 indent=1) + "\n")
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=open(out / "stdout.txt", "w"),
                            stderr=open(out / "stderr.txt", "w"), start_new_session=True)
    ticks_file = out / "ticks.txt"
    cut = False
    while proc.poll() is None:
        time.sleep(1.0)
        if a.max_seconds and time.monotonic() - t0 > a.max_seconds:
            cut = True
        elif a.max_ticks and ticks_file.is_file():
            try:
                with open(ticks_file, "rb") as f:
                    f.seek(max(0, ticks_file.stat().st_size - 200))
                    last = f.read().splitlines()[-1].split()
                if last and last[0].isdigit() and int(last[0]) >= a.max_ticks:
                    cut = True
            except (OSError, IndexError):
                pass
        if cut:
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            break
    wall = time.monotonic() - t0
    if reader is not None:
        reader.join(timeout=30)
    res = {"label": a.label, "build": a.build, "rc": proc.returncode, "cut": cut,
           "wall_seconds": round(wall, 2), "instrument": a.instrument, "rec": a.rec,
           "prof": a.prof, "stream_bytes": counted["bytes"]}
    if a.instrument and (out / "lifted.csv").is_file():
        receipt = json.loads(RECEIPT.read_text())
        reg = ROOT / next(r for r in receipt["replays"] if r["name"] == scene)["lifted"]["capture"]
        got, want = (out / "lifted.csv").read_bytes(), reg.read_bytes()
        rows = got[:got.rfind(b"\n") + 1]
        res["capture_rows"] = rows.count(b"\n") - 1
        res["capture_equal"] = got == want if not cut else want[:len(rows)] == rows
    (out / "result.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    print(report(out))
    return 0


# ------------------------------------------------------------------ reporting


def read_ticks(out: Path) -> list[tuple[int, int, int, int, int, int]]:
    rows = []
    for line in (out / "ticks.txt").read_text().splitlines()[1:]:
        f = line.split()
        if len(f) == 6:
            rows.append(tuple(int(x) for x in f))
    return rows


def q(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def phases(rows) -> dict:
    """Per tick deltas, split into gameplay (FrameCountIngame advanced) and other."""
    by = collections.defaultdict(lambda: {"wall": [], "cpu": [], "ticks": []})
    for prev, cur in zip(rows, rows[1:]):
        kind = "gameplay" if cur[5] != prev[5] and cur[5] != 0xFFFFFFFF else "other"
        by[kind]["wall"].append((cur[1] - prev[1]) / 1e6)
        by[kind]["cpu"].append((cur[2] - prev[2]) / 1e6)
        by[kind]["ticks"].append(cur[0])
    res = {}
    for kind, d in by.items():
        n = len(d["wall"])
        wall, cpu = sum(d["wall"]), sum(d["cpu"])
        res[kind] = {"ticks": n, "first_tick": min(d["ticks"]), "last_tick": max(d["ticks"]),
                     "wall_ms_mean": round(wall / n, 3), "cpu_ms_mean": round(cpu / n, 3),
                     "ticks_per_s": round(n / (wall / 1e3), 1) if wall else None,
                     "wall_ms_p50": round(q(d["wall"], .5), 3), "wall_ms_p95": round(q(d["wall"], .95), 3),
                     "wall_ms_p99": round(q(d["wall"], .99), 3), "wall_ms_max": round(max(d["wall"]), 3)}
    return res


def symbols(binary: str) -> tuple[list[int], list[str]]:
    r = subprocess.run(["nm", "--defined-only", "-n", binary], capture_output=True, text=True)
    addrs, names = [], []
    for line in r.stdout.splitlines():
        p = line.split()
        if len(p) == 3 and p[1] in "tTwW":
            addrs.append(int(p[0], 16))
            names.append(p[2])
    return addrs, names


def object_of(objdir: Path) -> dict[str, str]:
    owner = {}
    for o in sorted(objdir.glob("*.o")):
        r = subprocess.run(["nm", "--defined-only", str(o)], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) == 3 and p[1] in "tTwW":
                owner.setdefault(p[2], o.stem)
    return owner


def category(name: str, owner: dict[str, str]) -> str:
    if name.startswith("sub_") or name.startswith("__real_sub_"):
        return "lifted"
    if name.startswith(("sa2_lf80_", "sa2_x87_")) or name in ("unpk", "pk"):
        return "f80_shims"
    o = owner.get(name)
    if o:
        return o
    return "libc/compiler-rt"


def profile(out: Path, binary: str, objdir: Path, gameplay: set[int] | None) -> str:
    files = sorted(out.glob("prof-*.bin"))
    fmt = SAMPLE
    if files and files[0].stat().st_size % SAMPLE.size and not files[0].stat().st_size % 24:
        fmt = struct.Struct("<QQII")    # the second format: pc, sp[0], tick, tid
    if not files:                       # the first format: pc, tick, tid
        files, fmt = sorted(out.glob("samples-*.bin")), struct.Struct("<QII")
    if not files:
        return ""
    raw = files[0].read_bytes()
    addrs, names = symbols(binary)
    owner = object_of(objdir)
    cats, funcs, n = collections.Counter(), collections.Counter(), 0
    callers = collections.Counter()

    def name_of(pc: int) -> str:
        k = bisect.bisect_right(addrs, pc) - 1
        return names[k] if k >= 0 else "?"
    for i in range(0, len(raw) - fmt.size + 1, fmt.size):
        rec = fmt.unpack_from(raw, i)
        if fmt is SAMPLE:
            pc, tick = rec[0], rec[4]
        elif fmt.size == 24:
            pc, tick = rec[0], rec[2]
        else:
            pc, tick = rec[0], rec[1]
        if pc == 0:
            break
        if gameplay is not None and tick not in gameplay:
            continue
        n += 1
        name = name_of(pc)
        cats[category(name, owner)] += 1
        funcs[name] += 1
        if name.startswith(LEAVES) and fmt is SAMPLE:
            k = bisect.bisect_right(addrs, pc) - 1
            off = pc - addrs[k] if k >= 0 else 0
            # the return address is sp[0] at entry and at the `ret`, sp[1] after the `push rbp`
            # and at the `pop rbp`, fp[1] in between: the first that names a lifted function
            order = (1, 2, 3) if off == 0 else (2, 1, 3) if off == 1 else (3, 1, 2)
            got = next((name_of(rec[j]) for j in order if rec[j]
                        and name_of(rec[j]).startswith("sub_")), "?")
            callers[got] += 1
    if not n:
        return ""
    lines = [f"  samples {n} ({'gameplay ticks' if gameplay is not None else 'all ticks'})",
             "  by source:"]
    lines += [f"    {100 * v / n:5.1f}%  {k}" for k, v in cats.most_common(16)]
    lines.append("  top functions:")
    lines += [f"    {100 * v / n:5.1f}%  {k} [{category(k, owner)}]" for k, v in funcs.most_common(30)]
    if callers:
        lines.append("  callers of the memory intrinsics (the word at the stack pointer):")
        lines += [f"    {100 * v / n:5.1f}%  {k}" for k, v in callers.most_common(12)]
    hit = [v for k, v in funcs.most_common() if category(k, owner) == "lifted"]
    lines.append(f"  lifted functions hit: {len(hit)}; the top 10 hold {100 * sum(hit[:10]) / n:.1f}%, "
                 f"the top 100 {100 * sum(hit[:100]) / n:.1f}% of all samples")
    return "\n".join(lines)


def report(out: Path) -> str:
    out = Path(out)
    rows = read_ticks(out)
    res = json.loads((out / "result.json").read_text()) if (out / "result.json").is_file() else {}
    lines = [f"== {out.name}: {len(rows)} ticks, {json.dumps({k: res.get(k) for k in ('build', 'instrument', 'rec', 'prof', 'wall_seconds', 'stream_bytes', 'capture_equal', 'capture_rows')})}"]
    ph = phases(rows)
    for kind in ("gameplay", "other"):
        if kind in ph:
            lines.append(f"  {kind}: {json.dumps(ph[kind])}")
    if res.get("stream_bytes") and "gameplay" in ph:
        lines.append(f"  stream: {res['stream_bytes'] / max(1, len(rows)) / 1e6:.3f} MB per tick")
    cmd = json.loads((out / "command.json").read_text())
    gp = None
    if rows:
        gp = {cur[0] for prev, cur in zip(rows, rows[1:]) if cur[5] != prev[5]}
    prof = profile(out, cmd["build"]["binary"], Path(cmd["build"]["objdir"]), gp)
    if prof:
        lines.append(prof)
    return "\n".join(lines)


def cmd_report(a) -> int:
    for d in a.dirs:
        print(report(Path(d)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--variant", choices=("plain", "rec"), default="plain")
    b.add_argument("--scene", choices=SCENES, default="metalharbor")
    b.add_argument("--name")
    b.add_argument("--lifted", help="a cross_build output root whose objects replace the staged ones")
    b.add_argument("--rebuild", action="store_true")
    b.add_argument("--shims", help="with --lifted: this f80_shims.o in place of the build's (a "
                                   "counting build of the shims, say)")
    r = sub.add_parser("run")
    r.add_argument("--build", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--instrument", action="store_true")
    r.add_argument("--rec", action="store_true")
    r.add_argument("--prof", action="store_true")
    r.add_argument("--prof-us", type=int)
    r.add_argument("--max-ticks", type=int)
    r.add_argument("--max-seconds", type=float)
    r.add_argument("--env", action="append")
    p = sub.add_parser("report")
    p.add_argument("dirs", nargs="+")
    a = ap.parse_args(argv)
    return {"build": cmd_build, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
