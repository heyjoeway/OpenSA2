#!/usr/bin/env python3
"""pc_double: the PC releases in the double f80 mode, checked: the release's own game on the
registered boot replay, the gameplay cost against the native-x87 game, and the Windows zip.

Subtitle: the double mode (tools/s8/f80_double.py) is the PC releases' default (the user's
decision of 2026-10-12, `Documentation/decisions/2026-10-12-S7-pc-double-default.md`):
tools/s7/release_build.py links tools/s8/perf_f80.py's double objects with no flag, into the
release's default directory, and `--f80 native` builds the exact native-x87 game. The objects,
then the releases:

    source tools/env.sh
    tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build --target x86_64-linux-musl \\
        --mode double --out private/generated/s8/pc-double/linux-obj --jobs 4
    tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build --target x86_64-windows-gnu \\
        --mode double --out private/generated/s8/pc-double/windows-obj --jobs 4
    tools/slot.sh .venv/bin/python tools/s7/release_build.py
    tools/slot.sh .venv/bin/python tools/s7/release_build.py --target x86_64-windows

and then this tool's three commands. `boot` stays the gate on each release's game: the registered
replays and every gate measure the native-x87 build, so a release's game, in whichever mode, is
held to the registered boot replay before it is handed over.

    tools/slot.sh .venv/bin/python tools/s8/pc_double.py boot --release DIR --out OUT \\
        [--runner linux|wine] [--exe-kind unpacked|shipped] [--prefix PREFIX]
    tools/slot.sh .venv/bin/python tools/s8/pc_double.py perf build|run|report ...
    .venv/bin/python tools/s8/pc_double.py zip --release DIR --out ZIP [--copy-to PATH]

`boot` runs the release's own game binary (bin/sa2-game, or bin/sa2-game.exe under Proton's
Wine) with the registered boot replay's settings, as tools/s7/wine_run.py builds them for a tick
entry (`tick_setup`: loop_run's BASE_ENV with the write overlay in OUT, the reach configuration,
the capture's device profile, the tick and word logs in OUT), and compares its tick capture with
the registered one byte for byte. The release's game links the tick probe unchanged inside its
launch observer (release_build.py `observer_source`), so it writes the capture a boot replay
writes; with SA2_LAUNCH_TICKS and SA2_REC_TRACE unset its observer and recorder write nothing.
wine_run.py itself refuses a binary that win_link.py did not link for the boot runner, which the
release's is not, so this calls wine_run's own pieces (the bwrap and Wine environment, the
bounded run, the run-record checks). The run's own record must show the device profile, every
switch of the configuration, and the executable kind asked for (`--exe-kind shipped`: the
player's sonic2app.exe through the overlay table, as the release runs it). Writes
OUT/pc-double-boot.record (JSON; not .json, which the M7 census reads as a run record).

`perf` is tools/s8/perf_game.py with its directory moved to private/generated/s8/pc-double/perf,
so its Metal Harbor and boot builds (`build --lifted DIR` for the double mode, none for the
native-x87 one) and runs land beside the releases.

`zip` is tools/s7/release_zip.py's `pack` for a release directory and zip path other than its
own (a native-x87 build beside the default double one, say), and an optional copy (in place, then
hashed, as release_zip's --copy-to); the default release's zip is release_zip.py's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PCD = ROOT / "private" / "generated" / "s8" / "pc-double"
SCHEMA = "sa2-s8-pc-double-boot/v1"
GAMES = {"linux": "bin/sa2-game", "wine": "bin/sa2-game.exe"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest_head(release: Path) -> dict:
    line = (release / "release.manifest").read_text().splitlines()[0]
    return json.loads(line[2:])


def boot(a) -> int:
    sys.path[:0] = [str(ROOT / "tools" / "s7"), str(ROOT / "tools" / "s4")]
    import wine_run as WR  # noqa: PLC0415  read-only: its helpers are called, never main
    import loop_run as LR  # noqa: PLC0415
    import reach_receipt as RR  # noqa: PLC0415
    import tick_replay as TR  # noqa: PLC0415
    release, out = a.release.resolve(), Path(os.path.abspath(a.out))
    game = release / GAMES[a.runner]
    if not game.is_file():
        raise SystemExit(f"pc_double: no game at {game}")
    if not out.is_relative_to(ROOT / "private"):
        raise SystemExit(f"pc_double: --out {out} is not under private/")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"pc_double: {out} is not empty; every run starts from a fresh directory")
    out.mkdir(parents=True, exist_ok=True)
    reg = json.loads(TR.REGISTRY.read_text())
    entry = next(e for e in (reg["replays"] if isinstance(reg, dict) else reg) if e.get("name") == "boot")
    want = WR.registered(TR, "boot")
    profile = TR.native_device_profile(entry)
    setup = WR.tick_setup(TR, RR, LR, entry, out, profile)
    game_exe, kind_env = LR.exe_argv(a.exe_kind)
    extra = dict(setup["env"], **kind_env)
    run_json = out / WR.RUN_RECORD
    if a.runner == "wine":
        prefix = Path(os.path.abspath(a.prefix))
        if not prefix.is_relative_to(ROOT / "private") or "compatdata" in prefix.parts:
            raise SystemExit(f"pc_double: --prefix {prefix} is not a scratch prefix under private/")
        prefix.mkdir(parents=True, exist_ok=True)
        env = WR.wine_environ(RR.clean_environ(), prefix)
        argv = WR.wrapped([str(WR.WINE), str(game), str(game_exe), "--json", WR.rel(run_json)]
                          + setup["probe"], prefix)
    else:
        prefix = None
        env = dict(RR.clean_environ())
        argv = [str(game), str(game_exe), "--json", WR.rel(run_json)] + setup["probe"]
    env.pop(LR.EXE_KIND_VAR, None)
    env.update(extra)
    env["SDL_AUDIO_DRIVER"] = "dummy"
    t0 = time.monotonic()
    try:
        rc, _cut = WR.run_bounded(argv, env, out, a.timeout, None)
    finally:
        if prefix is not None:
            subprocess.run(WR.wrapped([str(WR.WINESERVER), "-k"], prefix), env=env,
                           capture_output=True, timeout=120)
    seconds = round(time.monotonic() - t0, 2)
    problems: list[str] = []
    cap = out / "lifted.csv"
    if rc is None or not run_json.is_file() or not cap.is_file():
        problems.append(f"the run did not complete: rc {rc}, run record {run_json.is_file()}, "
                        f"capture {cap.is_file()}")
        d = {}
    else:
        d = json.loads(run_json.read_text())
        try:
            TR.DP.check_run(d, profile)
        except TR.DP.Refusal as e:
            problems.append(f"device profile: {e}")
        problems += RR.switch_problems(d, {**RR.ENV, **setup["profile_env"]})
        problems += WR.exe_problems(d, a.exe_kind)
    got = cap.read_bytes() if cap.is_file() else b""
    reg_cap = (ROOT / want["lifted"]["capture"]).read_bytes()
    ticks = max(got.count(b"\n") - 1, 0)
    head = manifest_head(release)
    rec = {"schema": SCHEMA, "tool": "tools/s8/pc_double.py", "tool_sha256": sha256(Path(__file__)),
           "runner": a.runner, "exe_kind": a.exe_kind,
           "release": {"path": str(release), "manifest_sha256": sha256(release / "release.manifest"),
                       "target": head.get("target"), "f80": (head.get("game") or {}).get("f80")},
           "game": {"path": str(game), "sha256": sha256(game)},
           "argv": argv, "env": extra, "rc": rc, "seconds": seconds, "ticks": ticks,
           "ms_per_tick": round(1000 * seconds / ticks, 3) if ticks else None,
           "registered": {"capture": want["lifted"]["capture"], "sha256": want["lifted"]["sha256"]},
           "capture": {"path": str(cap), "sha256": sha256(cap) if cap.is_file() else None},
           "capture_equal": got == reg_cap, "problems": problems,
           "run_exe": d.get("exe"), "stop": TR.stop_note(d) if d else None}
    rec["ok"] = bool(rec["capture_equal"] and not problems and rc == 0)
    (out / "pc-double-boot.record").write_text(json.dumps(rec, indent=1, sort_keys=True, default=str) + "\n")
    print(("PC_DOUBLE_BOOT_OK " if rec["ok"] else "PC_DOUBLE_BOOT_FAIL ")
          + f"{out / 'pc-double-boot.record'} {a.runner}/{a.exe_kind} f80={(rec['release']['f80'] or {}).get('mode', 'native-x87')} "
          f"capture_equal={rec['capture_equal']} ticks={ticks} seconds={seconds} rc={rc}"
          + (f" problems={problems}" if problems else ""))
    return 0 if rec["ok"] else 1


def perf(argv: list[str]) -> int:
    sys.path.insert(0, str(HERE))
    import perf_game as PG  # noqa: PLC0415
    PG.PG = PCD / "perf"
    return PG.main(argv)


def zip_release(a) -> int:
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import release_zip as RZ  # noqa: PLC0415
    release, out = a.release.resolve(), Path(os.path.abspath(a.out))
    if not out.is_relative_to(ROOT / "private"):
        raise SystemExit(f"pc_double: --out {out} is not under private/")
    if out.resolve() == RZ.OUT.resolve() or release == RZ.RELEASE.resolve():
        raise SystemExit("pc_double: zip is for another release and zip than release_zip.py's own")
    out.parent.mkdir(parents=True, exist_ok=True)
    digest = RZ.pack(release, out)
    player = sha256(release / "bin" / "sa2_player_gpu.exe")
    game = sha256(release / "bin" / "sa2-game.exe")
    print(f"PC_DOUBLE_ZIP_OK {out} {out.stat().st_size} bytes sha256 {digest} player {player} game {game}")
    if a.copy_to:
        shutil.copyfile(out, a.copy_to)          # in place, as release_zip's --copy-to
        if sha256(a.copy_to) != digest:
            raise SystemExit(f"pc_double: {a.copy_to} does not hash as {out}")
        print(f"copied to {a.copy_to}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["perf"]:
        return perf(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("boot", help="the release's game on the registered boot replay")
    b.add_argument("--release", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True, help="a fresh directory under private/")
    b.add_argument("--runner", choices=tuple(GAMES), default="linux")
    b.add_argument("--exe-kind", choices=("unpacked", "shipped"), default="unpacked")
    b.add_argument("--prefix", type=Path, default=PCD / "wine-prefix",
                   help="(wine) the scratch WINEPREFIX")
    b.add_argument("--timeout", type=int, default=1800)
    sub.add_parser("perf", help="tools/s8/perf_game.py, its directory under pc-double/perf")
    z = sub.add_parser("zip", help="tools/s7/release_zip.py's pack for this release")
    z.add_argument("--release", type=Path, required=True)
    z.add_argument("--out", type=Path, required=True)
    z.add_argument("--copy-to", type=Path)
    a = ap.parse_args(argv)
    return boot(a) if a.cmd == "boot" else zip_release(a)


if __name__ == "__main__":
    sys.exit(main())
