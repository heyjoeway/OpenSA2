#!/usr/bin/env python3
"""Install and launch the Windows release by each of the installer's three routes, on a fresh host:
under Proton's Wine on this Linux machine (`--runner wine`), or natively on a Windows PC
(`--runner native`, the user's own run).

Subtitle: `.venv/bin/python host/launch/windows_routes.py --runner wine --out DIR` runs, for each
route, the release's `sa2-install.exe` (hash-checked, no manual step) and `sa2recomp.exe --selftest`
("launched"), each writing its record into DIR, with the summary `fresh-routes.runrec` (schema
`sa2-s7-fresh-routes/v1`, target `x86_64-windows`, its `runner` saying how it ran): the files
`tools/s7/installer_receipt.py --runs DIR` reads, as it reads host/launch/fresh_host_routes.py's.
Records are `.runrec`, never `.json` (the M7 census counts every .json under private/generated).

The routes (G7's installer clause, `Documentation/scripts/check_s7_exit.py` INSTALLER_ROUTES), the
same two commands each as on Linux, the paths Windows ones:

* **steam-autodiscovery**: `sa2-install.exe --source steam --yes`: the install found from the
  registry, with no path given.
* **folder**: `sa2-install.exe --source folder <the Steam game folder> --yes`.
* **zip**: `sa2-install.exe --source zip <a ZIP of the Steam game folder> --yes`; the archive is
  host/launch/fresh_host_routes.py's `make_zip` of the Steam install (on this machine, the Linux
  leg's archive when it is there, `--zip`).

`--runner wine` (Linux): each route runs in a bubblewrap sandbox of its own, as
fresh_host_routes.py's do, around Proton's Wine and a **new WINEPREFIX** made for that route
(`<out>/prefixes/<route>`, created empty, `wineboot --init`, nothing installed):

* HOME is an empty tmpfs at the real HOME's path, and the prefix is bound at `~/.wine`. Shared
  with the host, read only: `/usr`, `/etc` (Wine reads its fonts and locale there), `/sys`, the
  GPU's device nodes, Proton's files, and the X display's socket and authority file. Wine's Vulkan
  goes through its X11 driver, so the player needs a display to reach the GPU; it draws with SDL's
  offscreen driver and submits each frame without presenting it, so no window opens. Measured
  2026-10-11: with no display, host 2's device creation fails under Wine.
* Wine's Z: drive, its view of the whole Unix root, is removed from the new prefix, so the
  programs see only C: (the prefix), O: (`/out`, the records, read-write) and, for the Steam
  routes, S: (the Steam root, of which only the three paths the installer reads are bound, read
  only: `steamapps/libraryfolders.vdf`, `steamapps/appmanifest_213610.acf`,
  `steamapps/common/Sonic Adventure 2`). The release is bound read only at
  `C:/Program Files/sa2recomp`. For the zip route the archive is bound read only at
  `C:/users/<user>/Downloads/Sonic Adventure 2.zip` and the Steam folder is not visible.
* For steam-autodiscovery only, the registry values a Windows Steam install writes are written
  into the new prefix before the route (`setup`, which is the Steam install's stand-in and not the
  route's: the route's steps stay its two commands): HKCU\\Software\\Valve\\Steam `SteamPath`
  `s:/` (the client's spelling), and HKLM\\SOFTWARE\\Valve\\Steam `InstallPath` `S:\\` in the
  32-bit view (WOW6432Node), where Steam's 32-bit installer writes it.
* Before the steps the sandbox asserts it cannot see what fresh_host_routes.py's cannot (the
  checkout, `~/.steam`, Steam's config and the library's compatdata, shadercache and the rest,
  and for the zip route the Steam library itself), and that the prefix has no Z: drive; after
  them a write into the source has to fail as a read-only file system (`refused`).

`--runner native` (Windows, Python 3.10 or later): the user's own run (D3's "natively",
`Documentation/s7-windows-native-run.md`). There is no sandbox. "Fresh" is the installer's own
state: each route runs with LOCALAPPDATA set to a new, empty folder under --out, so its config,
manifest and data start empty, and with the real registry and Steam folder, read and never
written. The zip route's archive is made under --out from the Steam folder and marked read only,
so its write probe is `refused`; the Steam folder is the user's own and is not probed
(`not-probed: a native host's Steam folder is the user's`); the install record's manifests before
and after are the evidence that it was left alone.

`--hide-user-config` (`--runner wine`): fresh_host_routes.py's option. The Steam routes' sandboxes
mount an empty tmpfs, remounted read only, over the game's `Config/`, so the game folder is one
that was never launched (no `Config/UserConfig.cfg`; SA2's Launcher.exe writes it on its first
run) and the installer must write its default into the overlay. The Steam folder is never
written; the route's entry says so (`user_config_hidden`), and the sandbox stops with exit 94
unless the folder reads empty.

Each summary's route entry carries what fresh_host_routes.py's does (binds, what was asserted not
visible, the steps and their records' hashes, the write probe) and the summary carries `runner`
(kind `wine` or `native`, with Wine's version), `release_at` and `out_at` (the release's and the
records' folders as the programs saw them), so a Wine run never reads as a native one.

Usage:
  .venv/bin/python host/launch/windows_routes.py --runner wine --out DIR [--release DIR]
      [--steam DIR] [--zip FILE] [--routes steam-autodiscovery,folder,zip] [--frames 300]
  py -3 host\\launch\\windows_routes.py --runner native --out DIR --release DIR
      [--steam DIR] [--routes ...] [--frames 300]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve()
SCHEMA = "sa2-s7-fresh-routes/v1"
TARGET = "x86_64-windows"
APPID = "213610"
GAME = "Sonic Adventure 2"
ROUTES = ("steam-autodiscovery", "folder", "zip")
HIDDEN_STEAMAPPS = ("compatdata", "shadercache", "workshop", "downloading", "temp", "sourcemods")
GPU_NODES = ("/dev/dri", "/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-modeset", "/dev/nvidia-uvm",
             "/dev/nvidia-uvm-tools")
# Under Wine: where the programs see the release and the records (the receipt holds runs to these).
WINE_RELEASE_AT = "C:/Program Files/sa2recomp"
WINE_OUT_AT = "O:"
WINE_STEAM_DRIVE = "s:"
# Proton's Wine names every prefix's user profile this, whatever $USER is.
WINE_PROFILE = "steamuser"
# Where the sandbox holds Proton's files (read only): outside the Steam folder, so the zip route's
# sandbox has no Steam folder at all and the Steam routes' S: holds only the three paths.
PROTON_AT = Path("/opt/proton/files")
WINE_ENV = {"WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml=;winemenubuilder.exe=d"}
MAKER = "host/launch/fresh_host_routes.py make_zip (Python zipfile, deflate level 1)"
SHARED_WITH_HOST_WINE = ("/usr", "/etc", "/sys", "the GPU's device nodes", "Proton's files",
                         "the X display's socket and authority file (Wine's Vulkan; no window opens)")
NATIVE_HOME = "a new, empty LOCALAPPDATA"
NATIVE_UNPROBED = "not-probed: a native host's Steam folder is the user's"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def tilde(s: str, home: Path) -> str:
    return s.replace(str(home), "~")


def make_zip(game: Path, out: Path) -> dict:
    """fresh_host_routes.make_zip, the same archive: every file under `game.name/`, deflate 1."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    files = sorted(p for p in game.rglob("*") if p.is_file())
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for p in files:
            z.write(p, f"{game.name}/{p.relative_to(game).as_posix()}")
    os.replace(tmp, out)
    made = {"files": len(files), "bytes": out.stat().st_size, "sha256": sha256(out),
            "top": f"{game.name}/", "maker": MAKER, "made": now()}
    out.with_name(out.name + ".made").write_text(json.dumps(made, indent=1) + "\n")
    return made


def zip_identity(zip_path: Path, made_now: dict | None, home: Path) -> dict:
    made_file = zip_path.with_name(zip_path.name + ".made")
    made = made_now or (json.loads(made_file.read_text()) if made_file.is_file() else None)
    got = {"path": tilde(str(zip_path), home), "sha256": sha256(zip_path),
           "bytes": zip_path.stat().st_size, "made_now": made_now is not None, "made": made}
    if made and made.get("sha256") != got["sha256"]:
        raise SystemExit(f"WINDOWS_ROUTES_FAIL {zip_path} is not the archive {made_file.name} describes")
    return got


def route_steps(route: str, release_at: str, out_at: str, source: str, frames: int) -> list:
    """The route's two commands as the programs are given them (name, argv, expected exit)."""
    install = ["--source", "steam", "--yes"] if route == "steam-autodiscovery" else \
              ["--source", "folder" if route == "folder" else "zip", source, "--yes"]
    return [("install", [f"{release_at}/bin/sa2-install.exe", *install,
                         "--record", f"{out_at}/{route}-install.runrec"], 0),
            ("launch", [f"{release_at}/bin/sa2recomp.exe", "--selftest", "--frames", str(frames),
                        "--record", f"{out_at}/{route}-launch.runrec", "--out", f"{out_at}/{route}-session"], 0)]


def step_entries(out: Path, route: str, steps: list, exits: dict, home: Path) -> list:
    entries = []
    for name, argv, want in steps:
        got = exits.get(name)
        rec = out / f"{route}-{name}.runrec"
        entries.append({"name": name, "argv": [tilde(x, home) for x in argv], "expected_exit": want,
                        "exit": got, "record": rec.name, "record_sha256": sha256(rec) if rec.exists() else "",
                        "pass": got == want and rec.exists()})
    return entries


# ---------------------------------------------------------------- under Proton's Wine (Linux)

def proton_bin(steam: Path) -> Path:
    return steam / "steamapps" / "common" / "Proton - Experimental" / "files" / "bin"


def wine_plan(route: str, home: Path, steam: Path, zip_path: Path, prefix: Path, frames: int,
              hide_user_config: bool = False) -> dict:
    """The route's binds (read only and read-write, as (source, at in the sandbox, as Windows
    names it)), what it must not see, the setup, its steps and the write probe's target.
    `hide_user_config` puts an empty read-only tmpfs over the Steam routes' Config/ (`empty_ro`)."""
    apps = steam / "steamapps"
    game = apps / "common" / GAME
    wine_prefix = home / ".wine"
    hidden = sorted({str(checkout_root())}) + [str(home / ".steam"), str(steam / "config")] + \
             [str(apps / h) for h in HIDDEN_STEAMAPPS]
    downloads = wine_prefix / "drive_c" / "users" / WINE_PROFILE / "Downloads"
    if route == "zip":
        at = downloads / f"{GAME}.zip"
        binds = [(zip_path, at, f"C:/users/{WINE_PROFILE}/Downloads/{GAME}.zip")]
        hidden.append(str(apps))
        source = f"C:/users/{WINE_PROFILE}/Downloads/{GAME}.zip"
        probe = str(at)
        drives = {}
        empty_ro = []
    else:
        binds = [(p, p, f"S:/{p.relative_to(steam).as_posix()}")
                 for p in (apps / "libraryfolders.vdf", apps / f"appmanifest_{APPID}.acf", game)]
        source = f"S:/steamapps/common/{GAME}"
        probe = str(game / "probe")
        drives = {WINE_STEAM_DRIVE: str(steam)}
        empty_ro = [game / "Config"] if hide_user_config else []
    registry = []
    if route == "steam-autodiscovery":
        registry = [["HKCU\\Software\\Valve\\Steam", "SteamPath", "s:/", "64"],
                    ["HKLM\\SOFTWARE\\Valve\\Steam", "InstallPath", "S:\\", "32"]]
    mounts = [wine_prefix / "drive_c" / WINE_RELEASE_AT.split("/", 1)[1]] + \
             [at for _s, at, _w in binds if wine_prefix in at.parents]
    return {"route": route, "binds": binds, "hidden": hidden, "drives": dict(drives, **{"o:": "/out"}),
            "mounts_in_prefix": mounts,
            "registry": registry, "probe": probe, "prefix": prefix, "wine_prefix": wine_prefix,
            "empty_ro": empty_ro,
            "steps": route_steps(route, WINE_RELEASE_AT, WINE_OUT_AT, source, frames)}


def checkout_root() -> Path:
    for p in HERE.parents[1:]:
        if (p / "private" / "generated").is_dir() and (p / "tools" / "pins.json").is_file():
            return p
    return HERE.parents[2]


def wine_script(home: Path, plan: dict, wine: Path) -> str:
    """The sandbox's script: the visibility checks, the prefix made, Z: removed and the route's
    drives made, the registry written, the steps, the write probe."""
    q = shlex.quote
    wp = plan["wine_prefix"]
    wineserver = str(wine.parent / "wineserver")
    lines = ["set -u", "seen=0"]
    for h in plan["hidden"]:
        lines.append(f"if [ -e {q(h)} ]; then echo \"VISIBLE {h}\"; seen=1; fi")
    lines.append(f"if [ -n \"$(find {q(str(home))} -name pfx -print -quit 2>/dev/null)\" ]; then "
                 f"echo 'VISIBLE a pfx folder under HOME'; seen=1; fi")
    lines.append("[ \"$seen\" = 0 ] || { echo 'SANDBOX_LEAK'; exit 90; }")
    for at in plan.get("empty_ro", []):
        lines.append(f"[ -d {q(str(at))} ] && [ -z \"$(ls -A {q(str(at))})\" ] || "
                     f"{{ echo 'NOT_EMPTY {at}'; exit 94; }}")
        lines.append(f"echo {q('EMPTY ' + str(at))}")
    # New: no file in the prefix but the binds' (bwrap made their folders for them).
    prune = " -o ".join(f"-path {q(str(at))}" for at in plan["mounts_in_prefix"])
    lines.append(f"left=$(find {q(str(wp))} -mindepth 1 \\( {prune} \\) -prune -o -type f -print -quit); "
                 f"[ -z \"$left\" ] || {{ echo \"PREFIX_NOT_NEW $left\"; exit 91; }}")
    # The prefix, made with no display (wineboot opens no window then), then its drives.
    lines.append("echo SETUP prefix")
    lines.append(f"env -u DISPLAY -u WAYLAND_DISPLAY {q(str(wine))} wineboot --init >/dev/null 2>&1; "
                 f"echo \"SETUP_EXIT prefix $?\"; {q(wineserver)} -w")
    lines.append(f"[ -d {q(str(wp / 'drive_c' / 'users' / WINE_PROFILE / 'AppData'))} ] || "
                 f"{{ echo 'NO_PROFILE {WINE_PROFILE}'; exit 93; }}")
    dd = wp / "dosdevices"
    lines.append(f"if [ -L {q(str(dd / 'z:'))} ]; then unlink {q(str(dd / 'z:'))}; fi")
    for letter, target in plan["drives"].items():
        lines.append(f"ln -sfn {q(target)} {q(str(dd / letter))}")
    lines.append(f"if [ -e {q(str(dd / 'z:'))} ]; then echo 'VISIBLE the Z: drive'; exit 92; fi")
    for key, name, value, view in plan["registry"]:
        lines.append(f"env -u DISPLAY -u WAYLAND_DISPLAY {q(str(wine))} reg add {q(key)} /v {q(name)} "
                     f"/t REG_SZ /d {q(value)} /f /reg:{view} >/dev/null 2>&1; "
                     f"echo \"SETUP_EXIT registry:{name} $?\"")
    lines.append(f"{q(wineserver)} -w")
    lines.append(f"echo LISTING; find {q(str(home))} -maxdepth 2 | sort; ls -la {q(str(dd))}")
    lines.append("echo SANDBOX_CLEAN")
    for name, argv, _ in plan["steps"]:
        lines.append(f"echo {q('STEP ' + name)}")
        lines.append(f"{q(str(wine))} " + " ".join(map(q, argv)) + f"; echo \"EXIT {name} $?\"")
    lines.append(f"{q(wineserver)} -w")
    probe = plan["probe"]
    lines.append(f"if [ ! -d {q(str(Path(probe).parent))} ]; then echo 'WRITE_PROBE no-such-folder'; "
                 f"elif ( : >> {q(probe)} ) 2>/tmp/write-probe.err; then echo 'WRITE_PROBE wrote'; "
                 f"elif grep -q 'Read-only file system' /tmp/write-probe.err; then echo 'WRITE_PROBE refused'; "
                 f"else echo 'WRITE_PROBE failed-otherwise'; cat /tmp/write-probe.err; fi")
    return "\n".join(lines) + "\n"


def x_display() -> tuple[str, list[str]]:
    """DISPLAY, and the paths the sandbox must share for it: the X socket folder and the authority
    file. Refused when there is no X display: Wine's Vulkan needs one."""
    display = os.environ.get("DISPLAY", "")
    if not display:
        raise SystemExit("WINDOWS_ROUTES_FAIL no DISPLAY: Wine's Vulkan goes through its X11 driver")
    shared = ["/tmp/.X11-unix"]
    auth = os.environ.get("XAUTHORITY", "")
    if auth and Path(auth).is_file():
        shared.append(auth)
    return display, shared


def bwrap_argv(home: Path, plan: dict, release: Path, out: Path, user: str, proton: Path,
               display: str, x_shared: list[str], script: str) -> list[str]:
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "USER": user,
           "WINEPREFIX": str(plan["wine_prefix"]), "DISPLAY": display, **WINE_ENV}
    if os.environ.get("XAUTHORITY"):
        env["XAUTHORITY"] = os.environ["XAUTHORITY"]
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for k, v in env.items():
        a += ["--setenv", k, v]
    a += ["--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
          "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
          "--ro-bind", "/etc", "/etc", "--ro-bind", "/sys", "/sys", "--proc", "/proc", "--dev", "/dev"]
    for node in GPU_NODES:
        if Path(node).exists():
            a += ["--dev-bind", node, node]
    a += ["--tmpfs", "/tmp", "--tmpfs", str(home)]
    for p in x_shared:
        a += ["--ro-bind", p, p]
    a += ["--ro-bind", str(proton.parent), str(PROTON_AT)]
    a += ["--bind", str(plan["prefix"]), str(plan["wine_prefix"])]
    rel_at = WINE_RELEASE_AT.split("/", 1)[1]
    a += ["--ro-bind", str(release), str(plan["wine_prefix"] / "drive_c" / rel_at)]
    for src, at, _win in plan["binds"]:
        a += ["--ro-bind", str(src), str(at)]
    # A game never launched: an empty folder in place of its Config/, in the sandbox alone.
    for at in plan.get("empty_ro", []):
        a += ["--tmpfs", str(at), "--remount-ro", str(at)]
    a += ["--bind", str(out), "/out", "/bin/sh", "-c", script]
    return a


def run_wine(a, out: Path, release: Path, routes: list[str]) -> int:
    home = Path.home()
    steam = a.steam.resolve()
    proton = proton_bin(steam)
    wine = proton / "wine"
    if not wine.is_file():
        print(f"WINDOWS_ROUTES_FAIL no Proton Wine at {wine}", file=sys.stderr)
        return 2
    display, x_shared = x_display()
    user = os.environ.get("USER", "") or "user"
    version = subprocess.run([str(wine), "--version"], capture_output=True, text=True,
                             env={**os.environ, "WINEPREFIX": str(out / "prefixes" / "version-probe")}).stdout.strip()
    zip_path = (a.zip or checkout_root() / "private/generated/s7/wave3/launch/zip/sonic-adventure-2.zip").resolve()
    zip_made = None
    if "zip" in routes and not zip_path.is_file():
        print(f"making {zip_path} from the Steam install", flush=True)
        zip_made = make_zip(steam / "steamapps" / "common" / GAME, zip_path)
    summary = base_summary(out, release, home)
    summary["runner"] = {"kind": "wine", "wine": tilde(str(wine), home), "wine_version": version,
                         "prefix": "a new WINEPREFIX per route, made by wineboot --init, nothing installed",
                         "z_drive": "removed", "sandbox": "bwrap", "display": "the X display, for Wine's "
                         "Vulkan; SDL's offscreen driver, no window"}
    summary["release_at"], summary["out_at"] = WINE_RELEASE_AT, WINE_OUT_AT
    summary["shared_with_host"] = list(SHARED_WITH_HOST_WINE)
    if "zip" in routes:
        summary["zip"] = zip_identity(zip_path, zip_made, home)
    ok = True
    for route in routes:
        prefix = out / "prefixes" / route
        prefix.mkdir(parents=True)                # new and empty: a fresh host
        plan = wine_plan(route, home, steam, zip_path, prefix, a.frames, a.hide_user_config)
        missing = [str(s) for s, _, _ in plan["binds"] if not s.exists()]
        if missing:
            print(f"WINDOWS_ROUTES_FAIL {route}: not here: {missing}", file=sys.stderr)
            return 2
        script = wine_script(home, plan, PROTON_AT / "bin" / "wine")
        t0 = time.time()
        p = subprocess.run(bwrap_argv(home, plan, release, out, user, proton, display, x_shared, script),
                           capture_output=True, text=True)
        log = p.stdout + ("\n--- stderr\n" + p.stderr if p.stderr else "")
        (out / f"{route}.log").write_text(log)
        exits = {k: int(v) for k, v in re.findall(r"^EXIT (\S+) (\d+)$", p.stdout, re.M)}
        setup = [{"name": k, "exit": int(v)} for k, v in re.findall(r"^SETUP_EXIT (\S+) (\d+)$", p.stdout, re.M)]
        clean = "SANDBOX_CLEAN" in p.stdout
        steps = step_entries(out, route, plan["steps"], exits, home)
        probe = re.search(r"^WRITE_PROBE (\S+)$", p.stdout, re.M)
        probe_v = probe.group(1) if probe else ""
        setup_ok = bool(setup) and all(s["exit"] == 0 for s in setup)
        hidden_ok = all(f"EMPTY {at}" in p.stdout.splitlines() for at in plan["empty_ro"])
        passed = clean and setup_ok and hidden_ok and p.returncode == 0 and all(x["pass"] for x in steps) \
            and probe_v == "refused"
        ok &= passed
        extra = {}
        if plan["empty_ro"]:
            extra["user_config_hidden"] = [
                {"at": tilde(str(at), home), "how": "an empty tmpfs remounted read only over the game's "
                 "Config/, in the sandbox alone", "read_empty": f"EMPTY {at}" in p.stdout.splitlines()}
                for at in plan["empty_ro"]]
        summary["routes"].append({
            "route": route,
            "binds_read_only": [{"source": tilde(str(s), home), "at": tilde(str(at), home), "windows": win}
                                for s, at, win in plan["binds"]] +
                               [{"source": tilde(str(release), home), "at": "~/.wine/drive_c/" +
                                 WINE_RELEASE_AT.split("/", 1)[1], "windows": WINE_RELEASE_AT}],
            "binds_read_write": [{"source": tilde(str(prefix), home), "at": "~/.wine", "holds": "the new WINEPREFIX",
                                  "empty_at_start": True},
                                 {"source": tilde(str(out), home), "at": "/out", "windows": WINE_OUT_AT,
                                  "holds": "the records"}],
            "drives": {k.upper(): tilde(v, home) for k, v in plan["drives"].items()},
            "registry": [{"key": k, "value": n, "data": d, "view": f"{v}-bit"} for k, n, d, v in plan["registry"]],
            "setup": setup,
            "asserted_not_visible": [tilde(h, home) for h in plan["hidden"]] +
                                    ["any pfx folder under ~", "the Z: drive"],
            "home": "an empty tmpfs", "sandbox_clean": clean, "steps": steps, "write_probe": probe_v,
            "seconds": round(time.time() - t0, 1), "log": f"{route}.log",
            "log_sha256": sha256(out / f"{route}.log"), **extra, "pass": passed})
        print(f"{route}: {'PASS' if passed else 'FAIL'} " + " ".join(f"{x['name']}={x['exit']}" for x in steps) +
              f" setup={'ok' if setup_ok else setup} probe={probe_v} " +
              ("user-config=hidden " if plan["empty_ro"] else "") + f"({time.time() - t0:.0f} s)", flush=True)
    return finish(out, summary, ok)


# ---------------------------------------------------------------- natively on Windows

def run_native(a, out: Path, release: Path, routes: list[str]) -> int:
    """The user's own run: no sandbox; each route with a new, empty LOCALAPPDATA under --out."""
    if os.name != "nt":
        print("WINDOWS_ROUTES_FAIL --runner native runs on Windows", file=sys.stderr)
        return 2
    home = Path.home()
    steam = a.steam.resolve() if a.steam else None
    rel_at, out_at = release.as_posix(), out.as_posix()
    # The launcher writes a path under the user's profile as "~/..." in its records, and the
    # receipt reads the records' folder and the release by the absolute paths given here.
    inside = [str(p) for p in (out, release) if p == home or home in p.parents]
    if inside:
        print(f"WINDOWS_ROUTES_FAIL {inside} is inside the user profile {home}; put --out and --release "
              "outside it (for example C:\\sa2-runs and C:\\sa2recomp)", file=sys.stderr)
        return 2
    zip_made, zip_path = None, None
    if "zip" in routes or "folder" in routes:
        if steam is None or not (steam / "steamapps" / "common" / GAME).is_dir():
            print("WINDOWS_ROUTES_FAIL the folder and zip routes need --steam, the Steam folder holding "
                  f"steamapps/common/{GAME}", file=sys.stderr)
            return 2
    if "zip" in routes:
        zip_path = a.zip.resolve() if a.zip else out / f"{GAME}.zip"
        if not zip_path.is_file():
            print(f"making {zip_path} from the Steam install", flush=True)
            zip_made = make_zip(steam / "steamapps" / "common" / GAME, zip_path)
        os.chmod(zip_path, stat.S_IREAD)          # read only: the write probe must be refused
    summary = base_summary(out, release, home)
    summary["runner"] = {"kind": "native", "os": f"{sys.platform} {os.environ.get('OS', '')}".strip(),
                         "windows_version": subprocess.run(["cmd", "/c", "ver"], capture_output=True,
                                                           text=True).stdout.strip(),
                         "machine": os.environ.get("PROCESSOR_ARCHITECTURE", "")}
    summary["release_at"], summary["out_at"] = rel_at, out_at
    summary["shared_with_host"] = ["the whole Windows host: a native run has no sandbox"]
    if zip_path is not None:
        summary["zip"] = zip_identity(zip_path, zip_made, home)
    ok = True
    for route in routes:
        local = out / "localappdata" / route
        local.mkdir(parents=True)                 # new and empty: the installer's state starts empty
        source = "" if route == "steam-autodiscovery" else \
                 (steam / "steamapps" / "common" / GAME).as_posix() if route == "folder" else zip_path.as_posix()
        steps = route_steps(route, rel_at, out_at, source, a.frames)
        env = dict(os.environ, LOCALAPPDATA=str(local))
        t0 = time.time()
        exits, log = {}, []
        for name, argv, _ in steps:
            p = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(out))
            exits[name] = p.returncode
            log.append(f"STEP {name}\n{p.stdout}\n--- stderr\n{p.stderr}\nEXIT {name} {p.returncode}")
        probe_v = NATIVE_UNPROBED
        if route == "zip":
            try:
                with open(zip_path, "ab"):
                    probe_v = "wrote"
            except PermissionError:
                probe_v = "refused"
            except OSError as e:
                probe_v = f"failed-otherwise: {e}"
        (out / f"{route}.log").write_text("\n".join(log) + "\n")
        entries = step_entries(out, route, steps, exits, home)
        want_probe = "refused" if route == "zip" else NATIVE_UNPROBED
        passed = all(x["pass"] for x in entries) and probe_v == want_probe
        ok &= passed
        summary["routes"].append({
            "route": route, "binds_read_only": [], "binds_read_write": [
                {"source": tilde(str(local), home), "at": "%LOCALAPPDATA%", "holds": "the installer's config and data",
                 "empty_at_start": True}],
            "asserted_not_visible": [], "home": NATIVE_HOME, "sandbox_clean": True, "steps": entries,
            "write_probe": probe_v, "seconds": round(time.time() - t0, 1), "log": f"{route}.log",
            "log_sha256": sha256(out / f"{route}.log"), "pass": passed})
        print(f"{route}: {'PASS' if passed else 'FAIL'} " + " ".join(f"{x['name']}={x['exit']}" for x in entries) +
              f" probe={probe_v} ({time.time() - t0:.0f} s)", flush=True)
    return finish(out, summary, ok)


# ---------------------------------------------------------------- the summary

def base_summary(out: Path, release: Path, home: Path) -> dict:
    manifest = release / "release.manifest"
    shutil.copyfile(manifest, out / "release.manifest")
    return {"schema": SCHEMA, "tool": "host/launch/windows_routes.py", "tool_sha256": sha256(HERE),
            "target": TARGET,
            "release": {"path": tilde(str(release), home), "manifest_sha256": sha256(manifest),
                        "manifest_copy": "release.manifest"},
            "started": now(), "routes": []}


def finish(out: Path, summary: dict, ok: bool) -> int:
    summary["finished"] = now()
    summary["pass"] = ok
    (out / "fresh-routes.runrec").write_text(json.dumps(summary, indent=1) + "\n")
    print(("WINDOWS_ROUTES_OK " if ok else "WINDOWS_ROUTES_FAIL ") + str(out / "fresh-routes.runrec"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--runner", choices=("wine", "native"), required=True)
    ap.add_argument("--out", type=Path, required=True, help="an empty folder for the records")
    ap.add_argument("--release", type=Path, default=None,
                    help="the Windows release (default, under Wine: private/generated/s7/release/windows-x86_64)")
    ap.add_argument("--steam", type=Path, default=None,
                    help="the Steam folder (default, under Wine: ~/.local/share/Steam)")
    ap.add_argument("--zip", type=Path, default=None, help="the zip route's archive (made when absent)")
    ap.add_argument("--routes", default=",".join(ROUTES))
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--hide-user-config", action="store_true",
                    help="(--runner wine) an empty read-only tmpfs over the Steam game's Config/ in the "
                         "Steam routes' sandboxes: a game folder never launched, with no UserConfig.cfg")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        print(f"WINDOWS_ROUTES_FAIL {out} is not empty", file=sys.stderr)
        return 2
    release = (a.release or checkout_root() / "private/generated/s7/release/windows-x86_64").resolve()
    if not (release / "release.manifest").is_file() or not (release / "bin" / "sa2recomp.exe").is_file():
        print(f"WINDOWS_ROUTES_FAIL no Windows release at {release} (tools/s7/release_build.py "
              "--target x86_64-windows)", file=sys.stderr)
        return 2
    routes = [r for r in a.routes.split(",") if r]
    if [r for r in routes if r not in ROUTES]:
        print(f"WINDOWS_ROUTES_FAIL the routes are {', '.join(ROUTES)}", file=sys.stderr)
        return 2
    if a.runner == "wine":
        a.steam = a.steam or Path.home() / ".local/share/Steam"
        return run_wine(a, out, release, routes)
    if a.hide_user_config:
        print("WINDOWS_ROUTES_FAIL --hide-user-config is the Wine runner's: a native run uses the Steam "
              "folder as it is", file=sys.stderr)
        return 2
    return run_native(a, out, release, routes)


if __name__ == "__main__":
    raise SystemExit(main())
