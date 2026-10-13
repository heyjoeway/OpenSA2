#!/usr/bin/env python3
"""A play session of the Windows release, driven with no person: boot, the title, the main menu
and into a stage, then the window's close; under Proton's Wine on this Linux machine
(`--runner wine`) or natively on a Windows PC (`--runner native`).

Subtitle: `.venv/bin/python host/launch/windows_play.py --runner wine --out DIR [--release DIR]`
installs the release by the folder route in a new WINEPREFIX (host/launch/windows_routes.py's
sandbox: bwrap, no Z: drive, the Steam game folder bound read only) and runs `sa2recomp.exe` in
play mode (no
--selftest): the game, host 2's player in its interactive mode over the session's named pipes, a
window on the desktop session, paced to real time, the sound on SDL's default device (Wine's
PulseAudio driver, the user's PulseAudio socket bound into the sandbox). The keys are the
player's own: `--player-arg` hands the player `--synthetic-keys` (Enter, SDL scancode 40, pressed
every --every seconds from --first, each held --hold; then the window's close at --close), with
the key and Present logs that mode needs and a --max-frames past the close, so every key goes
the way a person's does: the window's key event, the player's key sender, its pipe, the
launcher's hand-on (KeyForward), the game's keyboard pipe. Records are `.runrec`.

What it reads back (DIR/windows-play.runrec, schema sa2-s8-windows-play/v1): the launcher's
record (`ended_by` the window's close, the game stopped by its quit, the keys handed on), the
observer's table (`ticks.tsv`: each GameLoop return's GameMode and CurrentMenu, and each
keyboard poll that read the host's records), the game's applied-input log (`input.log`), the
player's report (Presents, pace, sound, host input), and the verdict: `title` (GameMode 14,
CurrentMenu 0), `main_menu` (CurrentMenu 1), `stage` (GameMode 5, GameMode_Level, after the main
menu), `keys_applied` (polls that read the player's records, and logged key runs holding Enter),
`presented` (the player's Presents), `closed_by_window` and `player_exit_0`. PASS needs all.

`--runner native` (Windows, Python 3.10 or later) runs the same two steps with no sandbox, as
windows_routes.py's native runner does: LOCALAPPDATA a new, empty folder under --out (so the
installer's config and data start empty), the real Steam folder read and never written, the
window on the user's desktop and the sound on the default device.

Usage:
  .venv/bin/python host/launch/windows_play.py --runner wine --out DIR [--release DIR] [--steam DIR]
      [--first 8] [--every 2.5] [--hold 0.15] [--last 80] [--close 110]
  py -3 host\\launch\\windows_play.py --runner native --out DIR --release DIR --steam DIR [...]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
SCHEMA = "sa2-s8-windows-play/v1"
GAME_MODE_LEVEL, GAME_MODE_ADVERTISE, MENU_TITLE, MENU_MAIN = 5, 14, 0, 1
SCANCODE_RETURN = 40                    # SDL_SCANCODE_RETURN; DIK 0x1C in the game's log
SESSION = "play-session"


def routes_module():
    spec = importlib.util.spec_from_file_location("windows_routes_for_play", HERE.with_name("windows_routes.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


WR = routes_module()


def key_script(first: float, every: float, hold: float, last: float, close: float) -> str:
    rows, t = [], first
    while t <= last:
        down = int(round(t * 1000))
        rows += [f"{down} {SCANCODE_RETURN} down", f"{down + int(round(hold * 1000))} {SCANCODE_RETURN} up"]
        t += every
    rows.append(f"{int(round(close * 1000))} 0 close")
    return "\n".join(rows) + "\n"


def play_steps(out_at: str, release_at: str, source: str, max_frames: int, snaps: str = "") -> list:
    """The install (the folder route's) and the play session, as the programs are given them;
    `snaps` (the player's --snap list) writes those frames as PNG under <out>/snaps."""
    session = f"{out_at}/{SESSION}"
    player = ["--synthetic-keys", f"{out_at}/keys.tsv", "--key-log", f"{out_at}/key-log.tsv",
              "--present-log", f"{out_at}/present-log.tsv", "--max-frames", str(max_frames)]
    if snaps:
        player += ["--snap", snaps, "--snap-dir", f"{out_at}/snaps"]
    play = [f"{release_at}/bin/sa2recomp.exe", "--out", session]
    for a in player:
        play += ["--player-arg", a]
    return [("install", [f"{release_at}/bin/sa2-install.exe", "--source", "folder", source, "--yes",
                         "--record", f"{out_at}/play-install.runrec"], 0),
            ("play", play, 0)]


def pulse_binds() -> list[str]:
    """The user's PulseAudio socket (PipeWire's, on this desktop), for Wine's sound driver."""
    run = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    sock = run / "pulse" / "native"
    if not sock.exists():
        return []
    return ["--ro-bind", str(sock.parent), str(sock.parent), "--setenv", "PULSE_SERVER", f"unix:{sock}",
            "--setenv", "XDG_RUNTIME_DIR", str(run)]


def read_ticks(path: Path) -> tuple[list, list]:
    rows, keys = [], []
    if not path.is_file():
        return rows, keys
    for line in path.read_text().splitlines():
        f = line.split("\t")
        if f[0] == "T" and len(f) >= 5 and f[1].isdigit():
            rows.append(tuple(int(x) for x in f[1:5]))
        elif f[0] == "K" and len(f) >= 6 and f[1].isdigit():
            keys.append(tuple(int(x) for x in f[1:6]))
    return rows, keys


def verdict(out: Path) -> dict:
    s = out / SESSION
    launch = json.loads((s / "launch.runrec").read_text()) if (s / "launch.runrec").is_file() else {}
    report = json.loads((s / "player-report.runrec").read_text()) if (s / "player-report.runrec").is_file() else {}
    rows, keys = read_ticks(s / "ticks.tsv")
    transitions = []
    for tick, presents, mode, menu in rows:
        if not transitions or transitions[-1][2:] != [mode, menu]:
            transitions.append([tick, presents, mode, menu])
    title = next((r for r in rows if r[2] == GAME_MODE_ADVERTISE and r[3] == MENU_TITLE), None)
    main = next((r for r in rows if r[2] == GAME_MODE_ADVERTISE and r[3] == MENU_MAIN), None)
    stage = next((r for r in rows if main and r[0] > main[0] and r[2] == GAME_MODE_LEVEL), None)
    log = (s / "input.log").read_text().splitlines() if (s / "input.log").is_file() else []
    enter_runs = [line for line in log if re.search(r"(^| |,)0x1C(,|$)", line.split(" ", 2)[-1] if " " in line else "")]
    pace = report.get("pace") or {}
    audio = report.get("audio") or {}
    host_input = report.get("host_input") or {}
    got = {
        "title": title is not None, "main_menu": main is not None, "stage": stage is not None,
        "keys_applied": bool(keys) and bool(enter_runs),
        "presented": (report.get("counts") or {}).get("presents", report.get("presents", 0)) or 0,
        "closed_by_window": launch.get("ended_by") == "the window was closed",
        "player_exit_0": (launch.get("player") or {}).get("exit") == 0,
    }
    ok = all(got[k] for k in ("title", "main_menu", "stage", "keys_applied", "closed_by_window",
                              "player_exit_0")) and got["presented"] > 0
    return {"pass": ok, **got,
            "title_at": list(title) if title else None, "main_menu_at": list(main) if main else None,
            "stage_at": list(stage) if stage else None, "rows_columns": "tick, presents, game_mode, current_menu",
            "transitions": transitions[:200], "polls_with_records": len(keys),
            "records_read": sum(k[4] for k in keys), "input_log_lines": len(log), "enter_runs": len(enter_runs),
            "launch": {k: launch.get(k) for k in ("mode", "paced", "sound", "ended_by", "seconds", "failures")} |
                      {"game_stopped_by": (launch.get("game") or {}).get("stopped_by"),
                       "player": launch.get("player"), "relay": launch.get("relay")},
            "player_report": {"complete": report.get("complete"), "exit_status": report.get("exit_status"),
                              "stop_reason": report.get("stop_reason"), "failures": report.get("failures"),
                              "pace": pace, "audio": {k: audio.get(k) for k in list(audio)[:40]},
                              "host_input": host_input, "end_request": report.get("end_request"),
                              "lifecycle": report.get("lifecycle")}}


def run_wine(a, out: Path, release: Path) -> tuple[list, dict, str, float]:
    home, steam = Path.home(), a.steam.resolve()
    proton = WR.proton_bin(steam)
    wine = proton / "wine"
    if not wine.is_file():
        raise SystemExit(f"WINDOWS_PLAY_FAIL no Proton Wine at {wine}")
    display, x_shared = WR.x_display()
    prefix = out / "prefix"
    prefix.mkdir()
    plan = WR.wine_plan("folder", home, steam, out / "unused.zip", prefix, 300)
    plan["steps"] = play_steps(WR.WINE_OUT_AT, WR.WINE_RELEASE_AT, f"S:/steamapps/common/{WR.GAME}", a.max_frames,
                               a.snap)
    script = WR.wine_script(home, plan, WR.PROTON_AT / "bin" / "wine")
    argv_ = WR.bwrap_argv(home, plan, release, out, os.environ.get("USER", "") or "user", proton, display,
                          x_shared, script)
    at = argv_.index("/bin/sh")
    sound = [] if a.no_sound_socket else pulse_binds()
    argv_[at:at] = sound
    t0 = time.time()
    p = subprocess.run(argv_, capture_output=True, text=True)
    log = p.stdout + ("\n--- stderr\n" + p.stderr if p.stderr else "")
    if "SANDBOX_CLEAN" not in p.stdout:
        log += "\nWINDOWS_PLAY: the sandbox was not clean\n"
    runner = {"kind": "wine", "wine": WR.tilde(str(wine), home), "sandbox": "bwrap", "prefix": "new, wineboot --init",
              "z_drive": "removed", "display": "the X display: the player's window opens on the desktop",
              "sound": "the user's PulseAudio socket" if sound else "none bound",
              "sandbox_clean": "SANDBOX_CLEAN" in p.stdout}
    return plan["steps"], runner, log, time.time() - t0


def run_native(a, out: Path, release: Path) -> tuple[list, dict, str, float]:
    if os.name != "nt":
        raise SystemExit("WINDOWS_PLAY_FAIL --runner native runs on Windows")
    if a.steam is None or not (a.steam / "steamapps" / "common" / WR.GAME).is_dir():
        raise SystemExit(f"WINDOWS_PLAY_FAIL --steam names no Steam folder holding steamapps/common/{WR.GAME}")
    local = out / "localappdata"
    local.mkdir()
    steps = play_steps(out.as_posix(), release.as_posix(), (a.steam.resolve() / "steamapps" / "common" / WR.GAME).as_posix(),
                       a.max_frames, a.snap)
    env = dict(os.environ, LOCALAPPDATA=str(local))
    t0, log = time.time(), []
    for name, argv, _ in steps:
        p = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(out))
        log.append(f"STEP {name}\n{p.stdout}\n--- stderr\n{p.stderr}\nEXIT {name} {p.returncode}")
    runner = {"kind": "native", "os": f"{sys.platform} {os.environ.get('OS', '')}".strip(),
              "windows_version": subprocess.run(["cmd", "/c", "ver"], capture_output=True, text=True).stdout.strip(),
              "localappdata": "a new, empty folder under --out", "sandbox_clean": True,
              "display": "the user's desktop", "sound": "the default playback device"}
    return steps, runner, "\n".join(log) + "\n", time.time() - t0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--runner", choices=("wine", "native"), default="wine")
    ap.add_argument("--out", type=Path, required=True, help="an empty folder for the records")
    ap.add_argument("--release", type=Path, default=None,
                    help="the Windows release (default, under Wine: private/generated/s7/release/windows-x86_64)")
    ap.add_argument("--steam", type=Path, default=None,
                    help="the Steam folder (default, under Wine: ~/.local/share/Steam)")
    ap.add_argument("--first", type=float, default=8.0, help="seconds after the first Present of the first Enter")
    ap.add_argument("--every", type=float, default=2.5)
    ap.add_argument("--hold", type=float, default=0.15)
    ap.add_argument("--last", type=float, default=80.0, help="no Enter after this")
    ap.add_argument("--close", type=float, default=110.0, help="the window's close, seconds after the first Present")
    ap.add_argument("--max-frames", type=int, default=1000000)
    ap.add_argument("--snap", default="", help="the player's --snap list (frames as PNG under OUT/snaps)")
    ap.add_argument("--no-sound-socket", action="store_true", help="(wine) do not bind the PulseAudio socket")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        print(f"WINDOWS_PLAY_FAIL {out} is not empty", file=sys.stderr)
        return 2
    if a.runner == "native" and a.release is None:
        print("WINDOWS_PLAY_FAIL --runner native needs --release", file=sys.stderr)
        return 2
    release = (a.release or WR.checkout_root() / "private/generated/s7/release/windows-x86_64").resolve()
    if not (release / "bin" / "sa2recomp.exe").is_file():
        print(f"WINDOWS_PLAY_FAIL no Windows release at {release}", file=sys.stderr)
        return 2
    if a.runner == "wine":
        a.steam = a.steam or Path.home() / ".local/share/Steam"
    (out / "keys.tsv").write_text(key_script(a.first, a.every, a.hold, a.last, a.close))
    steps, runner, log, seconds = (run_wine if a.runner == "wine" else run_native)(a, out, release)
    (out / "play.log").write_text(log)
    exits = {k: int(v) for k, v in re.findall(r"^EXIT (\S+) (\d+)$", log, re.M)}
    v = verdict(out)
    home = Path.home()
    entries = [{"name": n, "argv": [WR.tilde(x, home) for x in argv], "expected_exit": want, "exit": exits.get(n)}
               for n, argv, want in steps]
    ok = v["pass"] and all(s["exit"] == s["expected_exit"] for s in entries) and runner["sandbox_clean"]
    doc = {"schema": SCHEMA, "tool": "host/launch/windows_play.py", "tool_sha256": WR.sha256(HERE),
           "target": WR.TARGET, "runner": runner,
           "release": {"path": WR.tilde(str(release), home),
                       "manifest_sha256": WR.sha256(release / "release.manifest")
                       if (release / "release.manifest").is_file() else ""},
           "keys": {"script": "keys.tsv", "sha256": WR.sha256(out / "keys.tsv"), "first_s": a.first,
                    "every_s": a.every, "hold_s": a.hold, "last_s": a.last, "close_s": a.close,
                    "how": "the player's --synthetic-keys: SDL key events in its own window's queue"},
           "snaps": sorted(p.name for p in (out / "snaps").glob("*.png")) if (out / "snaps").is_dir() else [],
           "steps": entries, "seconds": round(seconds, 1), "log": "play.log", "verdict": v, "pass": ok}
    (out / "windows-play.runrec").write_text(json.dumps(doc, indent=1) + "\n")
    print(("WINDOWS_PLAY_OK " if ok else "WINDOWS_PLAY_FAIL ") + str(out / "windows-play.runrec") +
          f" title={v['title']} main_menu={v['main_menu']} stage={v['stage']} keys={v['keys_applied']} "
          f"presented={v['presented']} closed={v['closed_by_window']} player_exit_0={v['player_exit_0']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
