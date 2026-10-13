#!/usr/bin/env python3
"""A person's first play of a release on a fresh host, driven with no person: no installation and
no sa2-install step, so `sa2recomp` in play mode must install the game itself and then play.

Subtitle: `.venv/bin/python host/launch/first_run_play.py --runner linux|wine --scenario S --out DIR
[--release DIR]` runs the release's launcher once in a sandbox whose HOME starts empty (Linux:
host/launch/fresh_host_routes.py's bubblewrap sandbox; Windows: Proton's Wine in a new WINEPREFIX,
host/launch/windows_routes.py's), with nothing installed, and reads back what it did
(DIR/first-run-play.runrec, schema sa2-s8-first-run-play/v1; records are `.runrec`, never `.json`,
which the M7 census reads as a game run).

The scenarios (--scenario):

* **steam**: Steam's copy is there (Linux: the three Steam paths the installer reads, bound read
  only where they are; Wine: S: and the registry values a Steam install writes, as
  windows_routes.py's steam-autodiscovery route has them). The launcher must install it by the
  installer's Steam route and play: the title, the main menu and into a stage, by the player's
  own `--synthetic-keys` (host/launch/windows_play.py's key script: Enter on schedule, then the
  window's close), every key through the window's queue, the player's key sender and the game's
  keyboard, as a person's go.
* **picker** (Linux): no Steam at all, and the game folder bound read only at
  `~/Games/Sonic Adventure 2`. SDL3's dialogs go to zenity on a desktop with no portal; a zenity
  stand-in on PATH (it logs every call into DIR/zenity.log and answers: the message box's
  "Choose folder", the picker the folder above the game's, as a person might pick it) stands in
  for the person, and DISPLAY names a display that is no X server's, so SDL finds no other way.
  The launcher must ask, install that folder by the folder route and play as above.
* **picker** (Wine): no Steam (S: holds the game folder, the registry nothing). SDL's dialogs are
  Windows' own and wait for a person, and nothing here sends input to any window: the run checks
  that the launcher asked (its line, and a top-level window titled "Sonic Adventure 2" on the X
  display, read with xprop), then stops it after --dialog-wait seconds.
* **no-picker** (Linux): no Steam, no zenity (the sandbox's /usr/bin/zenity is an empty file), no
  portal, a DISPLAY with no server: no dialog can be shown, and the launcher must end with 4 and
  say what to run instead.
* **no-display** (Linux): as no-picker, with DISPLAY and WAYLAND_DISPLAY unset.
* **console** (Wine): the steam scenario started the way a double-click starts a console
  program, in a console of its own (`start /wait`), whose output nothing captures: the launcher
  must give that console up when the game starts (`console` in its record: "released: ...") and
  keep what it said in the session's launcher.log.

Linux plays with SDL's offscreen driver (`--video-driver offscreen`: host 2 submits each frame
without presenting it; no window opens, no display is used) unless `--display`, which binds the X
display's socket and opens the player's window on the desktop. Wine needs the X display (its
Vulkan goes through its X11 driver), so the player's window opens there. With a display, the
titles of the windows that open meanwhile are read with xprop every second (`windows_opened`:
the player's window, a dialog); nothing is sent to any window. The sound is SDL's dummy
driver throughout (SDL_AUDIO_DRIVER=dummy): no sound plays.

What the record holds: the scenario, the sandbox's binds, the launcher's exit code, the
launcher's record (`first_run`: each installer step's route and exit; what was asked and chosen;
`console`), the config the first run wrote (source.kind), the installer records copied out of the
sandbox (DIR/first-run/), and, for the play scenarios, windows_play.py's verdict of the session
(title, main menu, a stage after it, keys applied, the window's close, the player's exit 0).

Usage:
  .venv/bin/python host/launch/first_run_play.py --runner linux --scenario steam --out DIR [--release DIR]
  .venv/bin/python host/launch/first_run_play.py --runner wine --scenario steam --out DIR [--release DIR]
      [--steam DIR] [--first 8] [--every 2.5] [--hold 0.15] [--last 80] [--close 110]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve()
SCHEMA = "sa2-s8-first-run-play/v1"
GAME = "Sonic Adventure 2"
SCENARIOS = {"linux": ("steam", "picker", "no-picker", "no-display"), "wine": ("steam", "picker", "console")}
PLAYS = ("steam", "picker", "console")
SESSION = "play-session"
FAKEBIN_AT = "/opt/fakebin"
NO_X = ":99"                                   # a display no X server serves in the sandbox
DIALOG_TITLE = "Sonic Adventure 2"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FHR = load("fresh_host_routes_for_first_run", HERE.with_name("fresh_host_routes.py"))
WR = load("windows_routes_for_first_run", HERE.with_name("windows_routes.py"))
WP = load("windows_play_for_first_run", HERE.with_name("windows_play.py"))

# zenity's stand-in (the picker scenario): logs each call and answers as $FAKE_ZENITY says.
FAKE_ZENITY = r"""#!/usr/bin/python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_ZENITY_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
cfg = json.loads(os.environ.get("FAKE_ZENITY", "{}"))
if a[:1] == ["--version"]:
    print("4.1.90")
    sys.exit(0)
if "--question" in a:
    buttons = [a[i + 1] for i, x in enumerate(a[:-1]) if x == "--extra-button"]
    want = cfg.get("button")
    print(want if want in buttons else buttons[0] if buttons else "")
    sys.exit(1)
if "--file-selection" in a:
    got = cfg.get("folder" if "--directory" in a else "file", "")
    if not got:
        sys.exit(1)
    print(got)
    sys.exit(0)
sys.exit(255)
"""


def player_args(out_at: str, max_frames: int) -> list[str]:
    """The player's synthetic keys, with the logs that mode needs (windows_play.py's)."""
    p = ["--synthetic-keys", f"{out_at}/keys.tsv", "--key-log", f"{out_at}/key-log.tsv",
         "--present-log", f"{out_at}/present-log.tsv", "--max-frames", str(max_frames)]
    return [x for a in p for x in ("--player-arg", a)]


def linux_plan(scenario: str, home: Path, steam: Path, release_at: str, out_at: str, max_frames: int,
               display: bool) -> dict:
    """The sandbox's read-only binds, extra environment, what it must not see, and the launch."""
    game = steam / "steamapps" / "common" / GAME
    hidden = sorted({str(FHR.ROOT), str(FHR.CHECKOUT)}) + [str(home / ".steam"), str(steam / "config")] + \
        [str(steam / "steamapps" / h) for h in FHR.HIDDEN_STEAMAPPS]
    env = {"SDL_AUDIO_DRIVER": "dummy"}
    binds: list[tuple[Path, Path]] = []
    devnull_over: list[str] = []
    if scenario == "steam":
        binds = FHR.steam_binds(steam)
    else:
        hidden.append(str(steam))                       # no Steam at all
        if scenario == "picker":
            binds = [(game, home / "Games" / GAME)]
        if scenario == "no-picker":
            devnull_over = ["/usr/bin/zenity"]
        if scenario in ("picker", "no-picker"):
            env.update(DISPLAY=NO_X, DBUS_SESSION_BUS_ADDRESS="unix:path=/tmp/no-session-bus")
    if scenario == "picker":
        env.update(FAKE_ZENITY=json.dumps({"button": "Choose folder", "folder": str(home / "Games")}),
                   FAKE_ZENITY_LOG=f"{out_at}/zenity.log")
    launch = [f"{release_at}/bin/sa2recomp", "--out", f"{out_at}/{SESSION}"]
    if not display:
        launch += ["--video-driver", "offscreen"]
    if scenario in PLAYS:
        launch += player_args(out_at, max_frames)
    return {"scenario": scenario, "binds": binds, "env": env, "hidden": hidden, "launch": launch,
            "devnull_over": devnull_over, "fakebin": scenario == "picker",
            "expect_exit": 0 if scenario in PLAYS else 4}


def linux_script(home: Path, plan: dict) -> str:
    q = shlex.quote
    lines = ["set -u", "seen=0"]
    for h in plan["hidden"]:
        lines.append(f"if [ -e {q(h)} ]; then echo \"VISIBLE {h}\"; seen=1; fi")
    for d in (home / ".config" / "sa2recomp", home / ".local" / "share" / "sa2recomp"):
        lines.append(f"if [ -e {q(str(d))} ]; then echo 'VISIBLE an installation at {d}'; seen=1; fi")
    lines.append("[ \"$seen\" = 0 ] || { echo 'SANDBOX_LEAK'; exit 90; }")
    lines.append(f"echo LISTING; find {q(str(home))} -maxdepth 6 | sort")
    lines.append("echo SANDBOX_CLEAN")
    lines.append("echo STEP play")
    lines.append(" ".join(map(q, plan["launch"])) + "; echo \"EXIT play $?\"")
    data = home / ".local" / "share" / "sa2recomp"
    lines.append(f"[ -d {q(str(data / 'first-run'))} ] && cp -r {q(str(data / 'first-run'))} /out/first-run")
    cfg = home / ".config" / "sa2recomp" / "sa2recomp.cfg"
    lines.append(f"[ -f {q(str(cfg))} ] && cp {q(str(cfg))} /out/sa2recomp.cfg")
    lines.append(f"echo LISTING_AFTER; find {q(str(home))} -maxdepth 4 | sort")
    return "\n".join(lines) + "\n"


def linux_argv(home: Path, plan: dict, release: Path, out: Path, script: str, display: bool) -> list[str]:
    argv = FHR.bwrap_argv(home, plan["binds"], release, out, os.environ.get("USER", "") or "user", script)
    at = argv.index("/bin/sh")
    extra: list[str] = []
    for k, v in plan["env"].items():
        extra += ["--setenv", k, v]
    if plan["fakebin"]:
        extra += ["--ro-bind", str(out / "fakebin"), FAKEBIN_AT]
        i = argv.index("PATH")
        argv[i + 1] = f"{FAKEBIN_AT}:{argv[i + 1]}"
    for p in plan["devnull_over"]:
        extra += ["--ro-bind", "/dev/null", p]
    if display:
        disp, shared = WR.x_display()
        extra += ["--setenv", "DISPLAY", disp]
        if os.environ.get("XAUTHORITY"):
            extra += ["--setenv", "XAUTHORITY", os.environ["XAUTHORITY"]]
        for p in shared:
            extra += ["--ro-bind", p, p]
    argv[at:at] = extra
    return argv


def run_linux(a, out: Path, release: Path) -> dict:
    home, steam = Path.home(), a.steam.resolve()
    plan = linux_plan(a.scenario, home, steam, FHR.RELEASE_IN, FHR.OUT_IN, a.max_frames, a.display)
    missing = [str(s) for s, _ in plan["binds"] if not s.exists()]
    if missing:
        raise SystemExit(f"FIRST_RUN_PLAY_FAIL not here: {missing}")
    if plan["fakebin"]:
        (out / "fakebin").mkdir()
        (out / "fakebin" / "zenity").write_text(FAKE_ZENITY)
        (out / "fakebin" / "zenity").chmod(0o755)
    script = linux_script(home, plan)
    t0 = time.time()
    stdout, stderr, rc, windows = run_watching(linux_argv(home, plan, release, out, script, a.display),
                                               WR.x_display()[0] if a.display else None)
    log = stdout + ("\n--- stderr\n" + stderr if stderr else "")
    return {"log": log, "seconds": time.time() - t0, "returncode": rc, "expect_exit": plan["expect_exit"],
            "windows": windows,
            "sandbox": {"kind": "bwrap", "home": "an empty tmpfs",
                        "binds_read_only": [{"source": WR.tilde(str(s), home), "at": WR.tilde(str(d), home)}
                                            for s, d in plan["binds"]] +
                                           [{"source": WR.tilde(str(release), home), "at": FHR.RELEASE_IN}],
                        "env": plan["env"], "zenity": "a stand-in on PATH" if plan["fakebin"] else
                        ("none: an empty file over /usr/bin/zenity" if plan["devnull_over"] else "the host's, if any"),
                        "asserted_not_visible": [WR.tilde(h, home) for h in plan["hidden"]] +
                                                ["~/.config/sa2recomp", "~/.local/share/sa2recomp"],
                        "display": "the X display: the player's window on the desktop" if a.display else
                        "none for the player (SDL's offscreen driver)"},
            "launch": [WR.tilde(x, home) for x in plan["launch"]]}


def x_window_names(display: str) -> dict[str, str]:
    """{id: WM_NAME} of the X display's top-level windows (_NET_CLIENT_LIST): read, never touched."""
    env = dict(os.environ, DISPLAY=display)
    try:
        got = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"], capture_output=True, text=True, env=env).stdout
    except OSError:
        return {}
    return {wid: "" for wid in re.findall(r"0x[0-9a-f]+", got)}


def x_window_name(wid: str, display: str) -> str:
    got = subprocess.run(["xprop", "-id", wid, "WM_NAME"], capture_output=True, text=True,
                         env=dict(os.environ, DISPLAY=display)).stdout
    m = re.search(r'= "(.*)"\s*$', got)
    return m.group(1) if m else ""


def run_watching(argv: list[str], display: str | None) -> tuple[str, str, int, list[dict]]:
    """Runs `argv`; with a display, the windows that open on it meanwhile (the player's, a dialog,
    a console): each one's X id, title, and the seconds after the start it was first and last
    seen, read with xprop every second; nothing is sent to them. An id that goes and comes back,
    or comes back with another title, is another window (the X server hands a closed client's
    ids to the next)."""
    if not display:
        p = subprocess.run(argv, capture_output=True, text=True)
        return p.stdout, p.stderr, p.returncode, []
    before = set(x_window_names(display))
    lives: list[dict] = []
    open_: dict[str, dict] = {}                     # id: its life, while it is listed
    t0 = time.time()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out: dict[str, str] = {}
    readers = [threading.Thread(target=lambda k=k, f=f: out.__setitem__(k, f.read()))
               for k, f in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for t in readers:
        t.start()
    while proc.poll() is None:
        now = round(time.time() - t0, 1)
        listed = set(x_window_names(display)) - before
        for wid in list(open_):
            if wid not in listed:
                del open_[wid]
        for wid in listed:
            name = x_window_name(wid, display)
            if not name:                            # a window not yet named is asked again
                continue
            life = open_.get(wid)
            if life and life["name"] == name:
                life["last_s"] = now
            else:
                open_[wid] = {"id": wid, "name": name, "first_s": now, "last_s": now}
                lives.append(open_[wid])
        time.sleep(1)
    for t in readers:
        t.join()
    return out.get("stdout", ""), out.get("stderr", ""), proc.returncode, lives


def run_wine(a, out: Path, release: Path) -> dict:
    home, steam = Path.home(), a.steam.resolve()
    proton = WR.proton_bin(steam)
    if not (proton / "wine").is_file():
        raise SystemExit(f"FIRST_RUN_PLAY_FAIL no Proton Wine at {proton / 'wine'}")
    display, x_shared = WR.x_display()
    prefix = out / "prefix"
    prefix.mkdir()
    route = "steam-autodiscovery" if a.scenario in ("steam", "console") else "folder"
    plan = WR.wine_plan(route, home, steam, out / "unused.zip", prefix, 300)
    launch = [f"{WR.WINE_RELEASE_AT}/bin/sa2recomp.exe", "--out", f"{WR.WINE_OUT_AT}/{SESSION}"]
    if a.scenario in PLAYS:
        launch += player_args(WR.WINE_OUT_AT, a.max_frames)
    plan["steps"] = [("play", launch, 0)]
    wine = WR.PROTON_AT / "bin" / "wine"
    script = WR.wine_script(home, plan, wine)
    q = shlex.quote
    play_line = f"{q(str(wine))} " + " ".join(map(q, launch)) + "; echo \"EXIT play $?\""
    if a.scenario == "console":
        # As a double-click starts it: a console of its own, its output on that console alone.
        script = script.replace(play_line, f"{q(str(wine))} start /wait " + " ".join(map(q, launch)) +
                                "; echo \"EXIT play $?\"")
    elif a.scenario == "picker":
        # Windows' own dialogs wait for a person; nothing here answers them.
        script = script.replace(play_line, f"timeout -s KILL {int(a.dialog_wait)} {q(str(wine))} " +
                                " ".join(map(q, launch)) + f"; echo \"EXIT play $?\"; {q(str(wine.parent / 'wineserver'))} -k")
    assert "EXIT play" in script
    local = f"{plan['wine_prefix']}/drive_c/users/{WR.WINE_PROFILE}/AppData/Local/sa2recomp"
    script += (f"[ -d {q(local + '/first-run')} ] && cp -r {q(local + '/first-run')} /out/first-run\n"
               f"[ -f {q(local + '/sa2recomp.cfg')} ] && cp {q(local + '/sa2recomp.cfg')} /out/sa2recomp.cfg\n")
    argv = WR.bwrap_argv(home, plan, release, out, os.environ.get("USER", "") or "user", proton, display,
                         x_shared, script)
    at = argv.index("/bin/sh")
    argv[at:at] = ["--setenv", "SDL_AUDIO_DRIVER", "dummy"]
    t0 = time.time()
    stdout, stderr, rc, windows = run_watching(argv, display)
    log = stdout + ("\n--- stderr\n" + stderr if stderr else "")
    return {"log": log, "seconds": time.time() - t0, "returncode": rc, "expect_exit": None if a.scenario == "picker" else 0,
            "windows": windows,
            "sandbox": {"kind": "bwrap around Proton's Wine", "prefix": "new, wineboot --init", "z_drive": "removed",
                        "drives": {k.upper(): WR.tilde(v, home) for k, v in plan["drives"].items()},
                        "registry": [{"key": k, "value": n, "data": d} for k, n, d, _ in plan["registry"]],
                        "binds_read_only": [{"source": WR.tilde(str(s), home), "windows": w} for s, _, w in plan["binds"]],
                        "display": "the X display: the player's window on the desktop", "sound": "SDL's dummy driver",
                        "started": "start /wait (a console of its own)" if a.scenario == "console" else
                                   "wine from a shell, its output captured"},
            "launch": [WR.tilde(x, home) for x in launch]}


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def verdict(a, out: Path, ran: dict) -> dict:
    exits = {k: int(v) for k, v in re.findall(r"^EXIT (\S+) (\d+)$", ran["log"], re.M)}
    launch = read_json(out / SESSION / "launch.runrec")
    cfg = (out / "sa2recomp.cfg").read_text() if (out / "sa2recomp.cfg").is_file() else ""
    kind = re.search(r"^source\.kind=(\S+)$", cfg, re.M)
    records = sorted(str(p.relative_to(out)) for p in (out / "first-run").rglob("*.runrec")) \
        if (out / "first-run").is_dir() else []
    installs = [read_json(out / r) for r in records]
    got = {"sandbox_clean": "SANDBOX_CLEAN" in ran["log"], "exit": exits.get("play"),
           "windows_opened": sorted({w["name"] for w in ran.get("windows", [])}),
           "windows": ran.get("windows", []),
           "config_source_kind": kind.group(1) if kind else None, "installer_records": records,
           "installer_exits": [(r.get("route"), (r.get("exit") or {}).get("code")) for r in installs],
           "first_run": launch.get("first_run"), "console": launch.get("console")}
    want_kind = {"steam": "steam", "console": "steam", "picker": "folder"}.get(a.scenario)
    ok = got["sandbox_clean"]
    if a.scenario in PLAYS and not (a.runner == "wine" and a.scenario == "picker"):
        play = WP.verdict(out)
        got["play"] = play
        ok &= got["exit"] == 0 and got["config_source_kind"] == want_kind and bool((launch.get("first_run") or {}).get("ran"))
        ok &= all(play[k] for k in ("title", "main_menu", "keys_applied", "closed_by_window", "player_exit_0"))
        if a.scenario == "console":
            # Informational: the console's window (Wine titles it with the program's path) and the
            # player's, with when each was seen; the console's should go when the game starts.
            wins = ran.get("windows", [])
            got["console_windows"] = [w for w in wins if w["name"].lower().endswith("sa2recomp.exe")]
            got["player_windows"] = [w for w in wins if w["name"] == DIALOG_TITLE]
            ok &= str(got["console"]).startswith("released")
            got["launcher_log"] = (out / SESSION / "launcher.log").read_text() \
                if (out / SESSION / "launcher.log").is_file() else ""
            ok &= got["launcher_log"].startswith("sa2recomp: no installation yet")
        if a.scenario == "picker":
            calls = [json.loads(x) for x in (out / "zenity.log").read_text().splitlines()] \
                if (out / "zenity.log").is_file() else []
            got["zenity_calls"] = [c for c in calls if c[:1] != ["--version"]]
            ok &= any("--question" in c for c in got["zenity_calls"]) and \
                any("--directory" in c for c in got["zenity_calls"])
    elif a.scenario == "picker":                         # Wine: the question was reached
        asked = "Asking for the game's folder" in ran["log"]
        dialogs = [w["id"] for w in ran.get("windows", []) if w["name"] == DIALOG_TITLE]
        got.update(asked=asked, dialog_windows=dialogs)
        ok &= asked and got["installer_exits"][:1] == [("steam-autodiscovery", 4)] and not cfg
    else:                                                # no picker: what to run, and 4
        said = re.findall(r"^sa2recomp: .*$", ran["log"], re.M)
        got["said"] = said
        ok &= got["exit"] == 4 and not cfg and any("No folder picker could be shown" in s for s in said) and \
            "--source folder" in ran["log"]
    got["pass"] = bool(ok)
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--runner", choices=tuple(SCENARIOS), default="linux")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", type=Path, required=True, help="an empty folder for the records")
    ap.add_argument("--release", type=Path, default=None,
                    help="the release (default private/generated/s7/release/<target>)")
    ap.add_argument("--steam", type=Path, default=Path.home() / ".local/share/Steam")
    ap.add_argument("--display", action="store_true", help="(linux) the player's window on the X display")
    ap.add_argument("--dialog-wait", type=float, default=60, help="(wine picker) seconds before the launcher is stopped")
    ap.add_argument("--first", type=float, default=8.0)
    ap.add_argument("--every", type=float, default=2.5)
    ap.add_argument("--hold", type=float, default=0.15)
    ap.add_argument("--last", type=float, default=80.0)
    ap.add_argument("--close", type=float, default=110.0)
    ap.add_argument("--max-frames", type=int, default=1000000)
    a = ap.parse_args(argv)
    if a.scenario not in SCENARIOS[a.runner]:
        print(f"FIRST_RUN_PLAY_FAIL the {a.runner} scenarios are {', '.join(SCENARIOS[a.runner])}", file=sys.stderr)
        return 2
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        print(f"FIRST_RUN_PLAY_FAIL {out} is not empty", file=sys.stderr)
        return 2
    target = "linux-x86_64" if a.runner == "linux" else "windows-x86_64"
    release = (a.release or FHR.CHECKOUT / "private/generated/s7/release" / target).resolve()
    if not (release / "release.manifest").is_file():
        print(f"FIRST_RUN_PLAY_FAIL no release at {release}", file=sys.stderr)
        return 2
    (out / "keys.tsv").write_text(WP.key_script(a.first, a.every, a.hold, a.last, a.close))
    shutil.copyfile(release / "release.manifest", out / "release.manifest")
    ran = (run_linux if a.runner == "linux" else run_wine)(a, out, release)
    (out / "first-run-play.log").write_text(ran["log"])
    v = verdict(a, out, ran)
    home = Path.home()
    doc = {"schema": SCHEMA, "tool": "host/launch/first_run_play.py", "tool_sha256": WR.sha256(HERE),
           "runner": a.runner, "scenario": a.scenario,
           "release": {"path": WR.tilde(str(release), home), "manifest_sha256": WR.sha256(release / "release.manifest"),
                       "manifest_copy": "release.manifest"},
           "sandbox": ran["sandbox"], "launch": ran["launch"], "prior_install_step": None,
           "keys": {"script": "keys.tsv", "sha256": WR.sha256(out / "keys.tsv"), "first_s": a.first,
                    "every_s": a.every, "hold_s": a.hold, "last_s": a.last, "close_s": a.close},
           "seconds": round(ran["seconds"], 1), "log": "first-run-play.log", "verdict": v, "pass": v["pass"]}
    (out / "first-run-play.runrec").write_text(json.dumps(doc, indent=1) + "\n")
    play = v.get("play") or {}
    print(("FIRST_RUN_PLAY_OK " if v["pass"] else "FIRST_RUN_PLAY_FAIL ") + str(out / "first-run-play.runrec") +
          f" {a.runner}/{a.scenario} exit={v['exit']} source={v['config_source_kind']} installs={v['installer_exits']}" +
          (f" title={play.get('title')} main_menu={play.get('main_menu')} stage={play.get('stage')} "
           f"keys={play.get('keys_applied')} closed={play.get('closed_by_window')}" if play else "") +
          (f" console={v['console']!r}" if a.scenario == "console" else ""))
    return 0 if v["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
