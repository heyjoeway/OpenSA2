#!/usr/bin/env python3
"""Run sa2-install on a fresh host built from the real installs, in a bubblewrap sandbox.

Subtitle: `.venv/bin/python host/install/fresh_host_run.py --binary B --out DIR` runs two
sessions, each in its own sandbox, and writes every step's `.runrec`, each session's log and the
summary `fresh-host.runrec` into DIR (a `.runrec`, never `.json`: the M7 census counts every
`.json` under private/generated as a game run).

The sessions (`SESSIONS` below; each step's expected exit code is part of the check):

* **steam**: the Steam install, found by discovery. `--source steam --yes`, then `--verify`
  (the quick check), then `--source folder <the Steam game folder> --yes`, then a write probe
  into the game folder, which has to fail.
* **modloader**: the SA2 Mod Loader tree (`private/install`), which has to be accepted through
  Data_DLL_orig.dll; then `--source steam`, which has to find nothing (exit 4).

What a sandbox holds (`binds`): HOME is an empty tmpfs at the real HOME's path. The steam session
adds, read-only, exactly three things from the Steam library, at their own paths:
`steamapps/libraryfolders.vdf`, `steamapps/appmanifest_213610.acf` and
`steamapps/common/Sonic Adventure 2`. The modloader session adds only the Mod Loader tree,
read-only, at `~/Games/Sonic Adventure 2`. Both add `/usr` and `/etc/ld.so.cache` read-only (the
dynamic linker), the binary at `/opt/sa2/sa2-install`, and DIR at `/out`. No checkout, no
`private/` tree, no Proton prefix (`compatdata`), no shader cache, no network, no display.

Before any step, a session asserts inside the sandbox that it cannot see what it must not: the
checkout, `~/.steam`, the Steam library's `compatdata`, `shadercache`, `workshop`,
`downloading`, `temp` and `config`, and any `pfx` folder under HOME. It then lists HOME to depth
six into its log. A session that sees any of them stops with exit 90 and runs no step.

Nothing outside DIR is written: every bind but `/out` is read-only, and the write probe shows it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPID = "213610"
GAME = "Sonic Adventure 2"
BIN_IN = "/opt/sa2/sa2-install"
OUT_IN = "/out"
SCHEMA = "sa2-s7-fresh-host/v1"
HIDDEN_STEAMAPPS = ("compatdata", "shadercache", "workshop", "downloading", "temp", "sourcemods")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def steam_binds(steam: Path) -> list[tuple[Path, Path]]:
    """(source, target) read-only binds of the steam session: the three Steam paths, each at
    its own path (the library's vdf names it by that path)."""
    apps = steam / "steamapps"
    paths = [apps / "libraryfolders.vdf", apps / f"appmanifest_{APPID}.acf", apps / "common" / GAME]
    return [(p, p) for p in paths]


def modloader_binds(home: Path, tree: Path) -> list[tuple[Path, Path]]:
    return [(tree.resolve(), home / "Games" / GAME)]


def bwrap_argv(home: Path, binds: list[tuple[Path, Path]], binary: Path, out: Path, user: str,
               script: str) -> list[str]:
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
         "--setenv", "HOME", str(home), "--setenv", "PATH", "/usr/bin:/bin",
         "--setenv", "LANG", "C.UTF-8", "--setenv", "USER", user,
         "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
         "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin"]
    if Path("/etc/ld.so.cache").exists():
        a += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
    a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", str(home)]
    for src, dst in binds:
        a += ["--ro-bind", str(src), str(dst)]
    a += ["--ro-bind", str(binary), BIN_IN, "--bind", str(out), OUT_IN, "/bin/sh", "-c", script]
    return a


def session_script(home: Path, hidden: list[str], steps: list[tuple[str, list[str], int]],
                   probe: str | None) -> str:
    q = shlex.quote
    lines = ["set -u", "seen=0"]
    for h in hidden:
        lines.append(f"if [ -e {q(h)} ]; then echo \"VISIBLE {h}\"; seen=1; fi")
    lines.append(f"if [ -n \"$(find {q(str(home))} -name pfx -print -quit 2>/dev/null)\" ]; then "
                 f"echo 'VISIBLE a pfx folder under HOME'; seen=1; fi")
    lines.append(f"echo LISTING; find {q(str(home))} -maxdepth 6 | sort")
    lines.append("[ \"$seen\" = 0 ] || { echo 'SANDBOX_LEAK'; exit 90; }")
    lines.append("echo SANDBOX_CLEAN")
    for name, args, _ in steps:
        lines.append(f"echo {q('STEP ' + name)}")
        lines.append(" ".join([BIN_IN, *map(q, args)]) + "; echo \"EXIT " + name + " $?\"")
    if probe:
        lines.append(f"if touch {q(probe)} 2>/dev/null; then echo 'WRITE_PROBE wrote'; "
                     f"else echo 'WRITE_PROBE refused'; fi")
    return "\n".join(lines) + "\n"


def sessions(home: Path, steam: Path, tree: Path) -> list[dict]:
    game = steam / "steamapps/common" / GAME
    ml = home / "Games" / GAME
    common_hidden = [str(ROOT), str(home / ".steam")]
    return [
        {"name": "steam", "binds": steam_binds(steam),
         "hidden": common_hidden + [str(steam / "steamapps" / h) for h in HIDDEN_STEAMAPPS] +
                   [str(steam / "config"), str(tree.resolve())],
         "steps": [("steam", ["--source", "steam", "--yes", "--record", f"{OUT_IN}/steam.runrec"], 0),
                   ("verify-quick", ["--verify", "--record", f"{OUT_IN}/verify-quick.runrec"], 0),
                   ("folder-steam", ["--source", "folder", str(game), "--yes",
                                     "--record", f"{OUT_IN}/folder-steam.runrec"], 0)],
         "probe": str(game / "probe")},
        {"name": "modloader", "binds": modloader_binds(home, tree),
         "hidden": common_hidden + [str(steam)],
         "steps": [("folder-modloader", ["--source", "folder", str(ml), "--yes",
                                         "--record", f"{OUT_IN}/folder-modloader.runrec"], 0),
                   ("steam-none", ["--source", "steam", "--record", f"{OUT_IN}/steam-none.runrec"], 4)],
         "probe": str(ml / "probe")},
    ]


def tilde(s: str, home: Path) -> str:
    return s.replace(str(home), "~")


def shown(src: Path, given: Path, home: Path) -> str:
    """How a bind's source is recorded: the Mod Loader tree as given (private/install, a link),
    not its target, whose folder names are the user's own; the rest home-relative."""
    if src == given.resolve() and given.resolve() != given.absolute():
        g = given.absolute()
        return g.relative_to(ROOT).as_posix() if g.is_relative_to(ROOT) else tilde(str(g), home)
    return tilde(str(src), home)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--binary", type=Path, required=True, help="the sa2-install binary to run")
    ap.add_argument("--out", type=Path, required=True, help="an empty folder for the records")
    ap.add_argument("--steam", type=Path, default=Path.home() / ".local/share/Steam")
    ap.add_argument("--modloader", type=Path, default=ROOT / "private/install")
    ap.add_argument("--only", choices=["steam", "modloader"])
    a = ap.parse_args()
    home = Path.home()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        print(f"FRESH_HOST_FAIL {out} is not empty", file=sys.stderr)
        return 2
    binary = a.binary.resolve()
    user = os.environ.get("USER", "")
    summary = {"schema": SCHEMA, "tool": "host/install/fresh_host_run.py",
               "tool_sha256": sha256(Path(__file__)), "binary_sha256": sha256(binary),
               "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sessions": []}
    ok = True
    for s in sessions(home, a.steam, a.modloader):
        if a.only and s["name"] != a.only:
            continue
        missing = [str(src) for src, _ in s["binds"] if not src.exists()]
        if missing:
            print(f"FRESH_HOST_FAIL {s['name']}: not here: {missing}", file=sys.stderr)
            return 2
        script = session_script(home, s["hidden"], s["steps"], s["probe"])
        argv = bwrap_argv(home, s["binds"], binary, out, user, script)
        p = subprocess.run(argv, capture_output=True, text=True)
        log = p.stdout + ("\n--- stderr\n" + p.stderr if p.stderr else "")
        (out / f"{s['name']}.log").write_text(log)
        exits = dict(re.findall(r"^EXIT (\S+) (\d+)$", p.stdout, re.M))
        clean = "SANDBOX_CLEAN" in p.stdout
        steps = []
        for name, args, want in s["steps"]:
            got = int(exits[name]) if name in exits else None
            rec = out / f"{name}.runrec"
            steps.append({"name": name, "argv": [tilde(x, home) for x in args], "expected_exit": want,
                          "exit": got, "record_sha256": sha256(rec) if rec.exists() else "",
                          "pass": got == want and rec.exists()})
        probe = re.search(r"^WRITE_PROBE (\S+)$", p.stdout, re.M)
        probe_v = probe.group(1) if probe else ""
        passed = clean and p.returncode == 0 and all(x["pass"] for x in steps) and probe_v == "refused"
        ok &= passed
        summary["sessions"].append({
            "name": s["name"],
            "binds_read_only": [{"source": shown(src, a.modloader, home), "at": tilde(str(dst), home)}
                                for src, dst in s["binds"]],
            "asserted_not_visible": [tilde(h, home) for h in s["hidden"]] + ["any pfx folder under ~"],
            "sandbox_clean": clean, "steps": steps, "write_probe": probe_v,
            "log_sha256": sha256(out / f"{s['name']}.log"), "pass": passed})
        print(f"{s['name']}: {'PASS' if passed else 'FAIL'} " +
              " ".join(f"{x['name']}={x['exit']}" for x in steps) + f" probe={probe_v}")
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["pass"] = ok
    (out / "fresh-host.runrec").write_text(json.dumps(summary, indent=1) + "\n")
    print(("FRESH_HOST_OK " if ok else "FRESH_HOST_FAIL ") + str(out / "fresh-host.runrec"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
