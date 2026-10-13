#!/usr/bin/env python3
"""Install and launch the release on a fresh Linux host by each of the installer's three routes.

Subtitle: `.venv/bin/python host/launch/fresh_host_routes.py --out DIR` runs one bubblewrap
sandbox per route, each a fresh host, and in it installs the game with the release's `sa2-install`
(hash-checked, no manual step) and runs `sa2recomp --selftest` ("launched"). Every step writes its
record into DIR, with the summary `fresh-routes.runrec` (`.runrec`, never `.json`: the M7 census
counts every `.json` under private/generated as a game run). tools/s7/installer_receipt.py reads
them.

The routes (G7's installer clause, `Documentation/scripts/check_s7_exit.py` INSTALLER_ROUTES):

* **steam-autodiscovery**: `sa2-install --source steam --yes`, the install found with no path given.
* **folder**: `sa2-install --source folder <the Steam game folder> --yes`.
* **zip**: `sa2-install --source zip <a ZIP of the Steam game folder> --yes`; the ZIP is made by
  `make_zip` from the Steam install, read only, into `--zip` (default: beside DIR), with the
  folder as its top directory, as a person zipping their game folder makes it.

What a sandbox holds (`binds`), each read only except `/out`: HOME is an empty tmpfs at the real
HOME's path (so the installer's config and data, the store included, start empty); `/usr`,
`/etc/ld.so.cache` and `/sys` (the dynamic linker and the Vulkan loader); the GPU's device nodes
(`/dev/dri`, `/dev/nvidia*`, whichever exist); the release directory at `/opt/sa2recomp`; and the
route's source at its own path: for steam-autodiscovery and folder the three Steam paths the
installer reads (`steamapps/libraryfolders.vdf`, `steamapps/appmanifest_213610.acf`,
`steamapps/common/Sonic Adventure 2`), for zip only the archive, at `~/Downloads`, and, read-write,
an empty new folder (`--zip-data`, default DIR's `zip-data`) at the installer's default data folder
`~/.local/share/sa2recomp`, so the 3 GB store the route extracts lands on disk rather than in the
tmpfs (which is memory) while every path the installer and launcher use stays its default. No
checkout, no `private/` tree, no Proton prefix (`compatdata`), no shader cache, no network, no
display (the selftest's player draws offscreen) and no sound device (SDL's dummy driver).

"Fresh" means no user state: `/usr`, `/etc/ld.so.cache`, `/sys` and the GPU's device nodes are the
build machine's own, read only (the summary's `shared_with_host` says so), so the host's libraries
and drivers are the ones a desktop running the release would have; it is not a bare distribution.

Before any step a sandbox asserts it cannot see what it must not (the checkout, `~/.steam`, the
library's `compatdata`, `shadercache`, `workshop`, `downloading`, `temp` and `config`, and, for the
zip route, the Steam library itself), lists HOME into its log, and stops with exit 90 if any is
visible. After the steps it probes a write into the source (a new file in the game folder, or an
append to the archive), which has to fail with "Read-only file system" (`refused`): a probe whose
folder is absent reads `no-such-folder`, and any other failure `failed-otherwise`, so a missing
bind never reads as a read-only one. The release's `release.manifest` is copied into DIR beside the
records, so the records keep the identity of the build that made them.

`--hide-user-config` makes the Steam routes' game folder one that was never launched: an empty
tmpfs, remounted read only, is mounted over its `Config/` folder in the sandbox, so the installer
finds no `Config/UserConfig.cfg` (SA2's Launcher.exe writes it on its first run; a Steam install
never launched has an empty `Config/`) and must write its default into the overlay. The Steam
folder itself is never written: the tmpfs exists only in the sandbox. The sandbox stops with exit
94 unless that folder reads empty, and the route's entry says so (`user_config_hidden`). The zip
route's archive is made from the Steam folder as it is, so the option leaves that route as it is.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def checkout_root(here: Path) -> Path:
    """The checkout holding this file: its own, or, for a snapshot of the sources kept under a
    checkout's private/, the checkout above it (whose tree a sandbox must not see either)."""
    for p in here.parents[1:]:
        if (p / "private" / "generated").is_dir() and (p / "tools" / "pins.json").is_file():
            return p
    return here.parents[2]


CHECKOUT = checkout_root(Path(__file__).resolve())
SCHEMA = "sa2-s7-fresh-routes/v1"
APPID = "213610"
GAME = "Sonic Adventure 2"
RELEASE_IN = "/opt/sa2recomp"
OUT_IN = "/out"
ROUTES = ("steam-autodiscovery", "folder", "zip")
HIDDEN_STEAMAPPS = ("compatdata", "shadercache", "workshop", "downloading", "temp", "sourcemods")
GPU_NODES = ("/dev/dri", "/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-modeset", "/dev/nvidia-uvm",
             "/dev/nvidia-uvm-tools")
DEFAULT_RELEASE = CHECKOUT / "private/generated/s7/release/linux-x86_64"
# What a sandbox shares with the build machine, read only (summary `shared_with_host`).
SHARED_WITH_HOST = ("/usr", "/etc/ld.so.cache", "/sys", "the GPU's device nodes")
MAKER = "host/launch/fresh_host_routes.py make_zip (Python zipfile, deflate level 1)"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tilde(s: str, home: Path) -> str:
    return s.replace(str(home), "~")


def make_zip(game: Path, out: Path) -> dict:
    """A ZIP of the game folder `game`, every file under the top directory `game.name/`, deflated,
    read only from the folder; written to `out` through a temporary name. Its identity."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    files = sorted(p for p in game.rglob("*") if p.is_file())
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for p in files:
            z.write(p, f"{game.name}/{p.relative_to(game).as_posix()}")
    os.replace(tmp, out)
    made = {"files": len(files), "bytes": out.stat().st_size, "sha256": sha256(out),
            "top": f"{game.name}/", "maker": MAKER, "made": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    # Beside the archive, so a later run that reuses it still says how it was made (`.made`, not
    # `.json`: the M7 census reads every .json under private/generated).
    out.with_name(out.name + ".made").write_text(json.dumps(made, indent=1) + "\n")
    return made


def steam_binds(steam: Path) -> list[tuple[Path, Path]]:
    apps = steam / "steamapps"
    return [(p, p) for p in (apps / "libraryfolders.vdf", apps / f"appmanifest_{APPID}.acf",
                             apps / "common" / GAME)]


def route_plan(route: str, home: Path, steam: Path, zip_path: Path, frames: int,
               data: Path | None = None, hide_user_config: bool = False) -> dict:
    """The route's binds (read only, and read-write), what it must not see, its steps (name,
    argv, expected exit) and the write probe's target. `data` is the zip route's on-disk data
    folder, bound at the installer's default. `hide_user_config` puts an empty read-only tmpfs
    over the Steam routes' Config/ (`empty_ro`)."""
    game = steam / "steamapps" / "common" / GAME
    hidden = sorted({str(ROOT), str(CHECKOUT)}) + [str(home / ".steam"), str(steam / "config")] + \
             [str(steam / "steamapps" / h) for h in HIDDEN_STEAMAPPS]
    rw_binds: list[tuple[Path, Path]] = []
    empty_ro: list[Path] = []
    if route == "zip":
        at = home / "Downloads" / f"{GAME}.zip"
        binds = [(zip_path, at)]
        if data is not None:
            rw_binds = [(data, home / ".local" / "share" / "sa2recomp")]
        install = ["--source", "zip", str(at), "--yes"]
        hidden.append(str(steam))
        probe = str(at)                      # the archive itself: an append must fail
    else:
        binds = steam_binds(steam)
        install = ["--source", "steam", "--yes"] if route == "steam-autodiscovery" else \
                  ["--source", "folder", str(game), "--yes"]
        probe = str(game / "probe")
        if hide_user_config:
            empty_ro = [game / "Config"]
    bin_ = f"{RELEASE_IN}/bin"
    steps = [
        ("install", [f"{bin_}/sa2-install", *install, "--record", f"{OUT_IN}/{route}-install.runrec"], 0),
        ("launch", [f"{bin_}/sa2recomp", "--selftest", "--frames", str(frames),
                    "--record", f"{OUT_IN}/{route}-launch.runrec", "--out", f"{OUT_IN}/{route}-session"], 0),
    ]
    return {"route": route, "binds": binds, "rw_binds": rw_binds, "hidden": hidden, "steps": steps,
            "probe": probe, "empty_ro": empty_ro}


def bwrap_argv(home: Path, binds: list[tuple[Path, Path]], release: Path, out: Path, user: str,
               script: str, rw_binds: list[tuple[Path, Path]] = (), empty_ro: list[Path] = ()) -> list[str]:
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
         "--setenv", "HOME", str(home), "--setenv", "PATH", "/usr/bin:/bin",
         "--setenv", "LANG", "C.UTF-8", "--setenv", "USER", user,
         "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
         "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin"]
    if Path("/etc/ld.so.cache").exists():
        a += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
    a += ["--ro-bind", "/sys", "/sys", "--proc", "/proc", "--dev", "/dev"]
    for node in GPU_NODES:
        if Path(node).exists():
            a += ["--dev-bind", node, node]
    a += ["--tmpfs", "/tmp", "--tmpfs", str(home)]
    for src, dst in binds:
        a += ["--ro-bind", str(src), str(dst)]
    for src, dst in rw_binds:
        a += ["--bind", str(src), str(dst)]
    # An empty folder in place of one inside a read-only bind (a game never launched has an empty
    # Config/): a tmpfs over it, then remounted read only, in the sandbox alone.
    for at in empty_ro:
        a += ["--tmpfs", str(at), "--remount-ro", str(at)]
    a += ["--ro-bind", str(release), RELEASE_IN, "--bind", str(out), OUT_IN, "/bin/sh", "-c", script]
    return a


def route_script(home: Path, plan: dict) -> str:
    q = shlex.quote
    lines = ["set -u", "seen=0"]
    for h in plan["hidden"]:
        lines.append(f"if [ -e {q(h)} ]; then echo \"VISIBLE {h}\"; seen=1; fi")
    lines.append(f"if [ -n \"$(find {q(str(home))} -name pfx -print -quit 2>/dev/null)\" ]; then "
                 f"echo 'VISIBLE a pfx folder under HOME'; seen=1; fi")
    lines.append(f"echo LISTING; find {q(str(home))} -maxdepth 6 | sort")
    lines.append("[ \"$seen\" = 0 ] || { echo 'SANDBOX_LEAK'; exit 90; }")
    for at in plan.get("empty_ro", []):
        lines.append(f"[ -d {q(str(at))} ] && [ -z \"$(ls -A {q(str(at))})\" ] || "
                     f"{{ echo 'NOT_EMPTY {at}'; exit 94; }}")
        lines.append(f"echo {q('EMPTY ' + str(at))}")
    lines.append("echo SANDBOX_CLEAN")
    for name, argv, _ in plan["steps"]:
        lines.append(f"echo {q('STEP ' + name)}")
        lines.append(" ".join(map(q, argv)) + f"; echo \"EXIT {name} $?\"")
    # An append to the archive, or a new file in the game folder: either is a write into the
    # route's source, and the read-only bind must refuse it as a read-only file system. A folder
    # that is not there (a bind missing) is no refusal.
    probe = plan["probe"]
    lines.append(f"if [ ! -d {q(str(Path(probe).parent))} ]; then echo 'WRITE_PROBE no-such-folder'; "
                 f"elif ( : >> {q(probe)} ) 2>/tmp/write-probe.err; then echo 'WRITE_PROBE wrote'; "
                 f"elif grep -q 'Read-only file system' /tmp/write-probe.err; then echo 'WRITE_PROBE refused'; "
                 f"else echo 'WRITE_PROBE failed-otherwise'; cat /tmp/write-probe.err; fi")
    lines.append(f"echo LISTING_AFTER; find {q(str(home))} -maxdepth 3 | sort")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, required=True, help="an empty folder for the records")
    ap.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    ap.add_argument("--steam", type=Path, default=Path.home() / ".local/share/Steam")
    ap.add_argument("--zip", type=Path, default=None,
                    help="the route's ZIP; made from the Steam install when absent (default: "
                         "<out>/../sonic-adventure-2.zip)")
    ap.add_argument("--zip-data", type=Path, default=None,
                    help="the zip route's data folder, new and on disk (default <out>/zip-data)")
    ap.add_argument("--routes", default=",".join(ROUTES))
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--hide-user-config", action="store_true",
                    help="an empty read-only tmpfs over the Steam game's Config/ in the Steam routes' "
                         "sandboxes: a game folder never launched, with no UserConfig.cfg")
    a = ap.parse_args(argv)
    home = Path.home()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        print(f"FRESH_ROUTES_FAIL {out} is not empty", file=sys.stderr)
        return 2
    release = a.release.resolve()
    manifest = release / "release.manifest"
    if not manifest.is_file():
        print(f"FRESH_ROUTES_FAIL no release at {release} (tools/s7/release_build.py)", file=sys.stderr)
        return 2
    routes = [r for r in a.routes.split(",") if r]
    if [r for r in routes if r not in ROUTES]:
        print(f"FRESH_ROUTES_FAIL the routes are {', '.join(ROUTES)}", file=sys.stderr)
        return 2
    zip_path = (a.zip or out.parent / "sonic-adventure-2.zip").resolve()
    zip_made = None
    if "zip" in routes and not zip_path.is_file():
        print(f"making {zip_path} from the Steam install", flush=True)
        zip_made = make_zip(a.steam / "steamapps" / "common" / GAME, zip_path)
    user = os.environ.get("USER", "")
    data = (a.zip_data or out / "zip-data").resolve()
    # The build's identity beside the records (text, as the release writes it).
    shutil.copyfile(manifest, out / "release.manifest")
    summary = {"schema": SCHEMA, "tool": "host/launch/fresh_host_routes.py",
               "tool_sha256": sha256(Path(__file__)), "target": "x86_64-linux",
               "release": {"path": tilde(str(release), home), "manifest_sha256": sha256(manifest),
                           "manifest_copy": "release.manifest"},
               "shared_with_host": list(SHARED_WITH_HOST),
               "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "routes": []}
    if "zip" in routes:
        made = zip_path.with_name(zip_path.name + ".made")
        summary["zip"] = {"path": tilde(str(zip_path), home), "sha256": sha256(zip_path),
                          "bytes": zip_path.stat().st_size, "made_now": zip_made is not None,
                          "made": zip_made or (json.loads(made.read_text()) if made.is_file() else None)}
        if summary["zip"]["made"] and summary["zip"]["made"].get("sha256") != summary["zip"]["sha256"]:
            print(f"FRESH_ROUTES_FAIL {zip_path} is not the archive {made.name} describes", file=sys.stderr)
            return 2
    ok = True
    for route in routes:
        plan = route_plan(route, home, a.steam, zip_path, a.frames, data, a.hide_user_config)
        missing = [str(s) for s, _ in plan["binds"] if not s.exists()]
        if missing:
            print(f"FRESH_ROUTES_FAIL {route}: not here: {missing}", file=sys.stderr)
            return 2
        for src, _ in plan["rw_binds"]:
            src.mkdir(parents=True)          # empty, and new: a fresh host's data folder
        script = route_script(home, plan)
        t0 = time.time()
        p = subprocess.run(bwrap_argv(home, plan["binds"], release, out, user, script,
                                      plan["rw_binds"], plan["empty_ro"]),
                           capture_output=True, text=True)
        log = p.stdout + ("\n--- stderr\n" + p.stderr if p.stderr else "")
        (out / f"{route}.log").write_text(log)
        exits = dict(re.findall(r"^EXIT (\S+) (\d+)$", p.stdout, re.M))
        clean = "SANDBOX_CLEAN" in p.stdout
        steps = []
        for name, args, want in plan["steps"]:
            got = int(exits[name]) if name in exits else None
            rec = out / f"{route}-{name}.runrec"
            steps.append({"name": name, "argv": [tilde(x, home) for x in args], "expected_exit": want,
                          "exit": got, "record": rec.name,
                          "record_sha256": sha256(rec) if rec.exists() else "",
                          "pass": got == want and rec.exists()})
        probe = re.search(r"^WRITE_PROBE (\S+)$", p.stdout, re.M)
        probe_v = probe.group(1) if probe else ""
        hidden_ok = all(f"EMPTY {at}" in p.stdout.splitlines() for at in plan["empty_ro"])
        passed = clean and hidden_ok and p.returncode == 0 and all(x["pass"] for x in steps) and \
            probe_v == "refused"
        ok &= passed
        extra = {}
        if plan["empty_ro"]:
            extra["user_config_hidden"] = [
                {"at": tilde(str(at), home), "how": "an empty tmpfs remounted read only over the game's "
                 "Config/, in the sandbox alone", "read_empty": f"EMPTY {at}" in p.stdout.splitlines()}
                for at in plan["empty_ro"]]
        summary["routes"].append({
            "route": route,
            "binds_read_only": [{"source": tilde(str(s), home), "at": tilde(str(d), home)}
                                for s, d in plan["binds"]] +
                               [{"source": tilde(str(release), home), "at": RELEASE_IN}],
            "binds_read_write": [{"source": tilde(str(s), home), "at": tilde(str(d), home),
                                  "empty_at_start": True} for s, d in plan["rw_binds"]] +
                                [{"source": tilde(str(out), home), "at": OUT_IN, "holds": "the records"}],
            "asserted_not_visible": [tilde(h, home) for h in plan["hidden"]] + ["any pfx folder under ~"],
            "home": "an empty tmpfs", "sandbox_clean": clean, "steps": steps, "write_probe": probe_v,
            "seconds": round(time.time() - t0, 1), "log": f"{route}.log",
            "log_sha256": sha256(out / f"{route}.log"), **extra, "pass": passed})
        print(f"{route}: {'PASS' if passed else 'FAIL'} " +
              " ".join(f"{x['name']}={x['exit']}" for x in steps) + f" probe={probe_v} "
              + ("user-config=hidden " if plan["empty_ro"] else "") +
              f"({time.time() - t0:.0f} s)", flush=True)
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["pass"] = ok
    (out / "fresh-routes.runrec").write_text(json.dumps(summary, indent=1) + "\n")
    print(("FRESH_ROUTES_OK " if ok else "FRESH_ROUTES_FAIL ") + str(out / "fresh-routes.runrec"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
