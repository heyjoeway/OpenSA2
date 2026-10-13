#!/usr/bin/env python3
r"""run_check: the double-f80 Windows game's registered replays, run natively on Windows.

Subtitle: extract the kit and run, in PowerShell,

    Expand-Archive -Force Z:\sa2-double-check.zip C:\sa2-kit
    py -3 C:\sa2-kit\run_check.py --steam "C:\Program Files (x86)\Steam" --copy-to Z:\

It prints DOUBLE_CHECK_OK when every replay's tick capture (lifted.csv) has the registered
sha256, and DOUBLE_CHECK_FAIL with the first differing tick and columns otherwise. Standard
library only (Python 3.8 or later); the kit is this file's folder. tools/s8/vm_kit.py builds the
kit and proves it under Proton's Wine before it reaches the VM.

What it does, in order:
  1. Checks every kit file against kit.json's sha256 (a damaged extract is refused, exit 2).
  2. Finds the game folder: --steam's steamapps\common\Sonic Adventure 2, or --steam itself
     when it is the game folder. It only reads it: nothing under it is opened for writing.
  3. Checks the files the registered runs read from the installation against the clean Steam
     tree's sha256s (kit.json `install.files`), the paths they found absent (`install.absent`),
     and the save folder. A difference is reported; the runs still go ahead.
  4. Makes a fresh scratch folder, C:\sa2-runs\double-check-<n> (n one above any there).
  5. For each replay: a folder <scratch>\<name> with its write overlay <name>\vfs seeded with the
     registered runs' Config files and save state (the release's own defaults: the game folder of
     a Steam install that never ran SA2's launcher has no Config files; a save the folder has
     beyond the pinned state is hidden by a whiteout marker, runtime/sa2_files.h); then the
     replay binary with the registered argv and environment (tools/s7/wine_run.py tick_setup and
     stage_setup, with Windows paths), every SA2_* variable of the shell removed first. The
     guest-side directory and executable names (SA2_GUEST_CWD, SA2_GUEST_EXE_PATH) stay the
     capture's own Z:\ strings: they are what the game is told, never a host path.
  6. Compares lifted.csv with the expected sha256; when it differs, the registered capture in
     the kit gives the first differing tick and the columns. Checks the run's own record
     (run.runrec): the device profile and guest names it was handed, every device switch on, no
     host keyboard, the executable kind. For Metal Harbor, also the goal latch, stage trace and
     applied input against the registered runs'.
  7. Writes <scratch>\summary.json and <scratch>\double-check-<n>-records.zip (the summary, each
     run's record and stderr; on a failed run also its captures), and copies the zip to
     --copy-to when given.

The replays: `boot` (bin\boot\sa2_main.exe) and `metalharbor` (bin\metalharbor\sa2_main.exe),
the win_link.py replay builds of the double objects, on the unpacked executable in the kit; and
`release-boot`, the release's own bin\sa2-game.exe on the boot replay with the game folder's
own sonic2app.exe (SA2_EXE_KIND=shipped, as the release runs it). The binaries import only
KERNEL32 and the Universal CRT, so no window opens and nothing else needs to be beside them.

`--wine WINE --wine-prefix PREFIX` is for proving the kit on Linux only (tools/s8/vm_kit.py
prove): the same steps, with C:\ being the prefix's drive_c for this script's own file access
and each binary started by Wine.

Exit status: 0 DOUBLE_CHECK_OK, 1 DOUBLE_CHECK_FAIL, 2 refused (kit damaged, no game folder).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PureWindowsPath

KIT_SCHEMA = "sa2-s8-vm-kit/v1"
SUMMARY_SCHEMA = "sa2-s8-vm-check/v1"
DEFAULT_STEAM = r"C:\Program Files (x86)\Steam"
DEFAULT_OUT = r"C:\sa2-runs"
GAME_DIR = ("steamapps", "common", "Sonic Adventure 2")
SCRATCH = "double-check-"
WHITEOUT = ".sa2-whiteout."          # runtime/sa2_files.c WHITEOUT_PREFIX
# Under Wine: the settings tools/s7/wine_run.py runs with (no debug channels, no Mono or Gecko,
# no menu entries), and no display.
WINE_ENV = {"WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml=;winemenubuilder.exe=d"}
DISPLAY_VARS = ("DISPLAY", "WAYLAND_DISPLAY")
# What a failed run's records zip carries beside the record and stderr.
FAILED_EXTRAS = ("lifted.csv", "goal-latch.csv", "stage-trace.csv", "input-applied.csv",
                 "imports.table", "lifted-words.bin")


class Refused(Exception):
    pass


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Host:
    """Where Windows paths live for this script's own file access. Natively a Windows path is
    itself; under Wine (`--wine`), C:\\ is the prefix's drive_c and every other drive is
    refused, so the proof cannot reach a file the VM would not have."""

    def __init__(self, wine: str | None = None, prefix: str | None = None):
        if (wine is None) != (prefix is None):
            raise Refused("--wine and --wine-prefix go together")
        self.wine = wine
        self.prefix = os.path.abspath(prefix) if prefix else None

    def local(self, win: str) -> str:
        if self.wine is None:
            return win
        p = PureWindowsPath(win)
        if p.drive.upper() != "C:" or not p.root:
            raise Refused(f"{win}: under Wine this check reaches C:\\ (the prefix) and nothing else")
        return os.path.join(self.prefix, "drive_c", *p.parts[1:])

    def windows(self, local: str) -> str:
        if self.wine is None:
            return str(PureWindowsPath(os.path.abspath(local)))
        drive_c = os.path.join(self.prefix, "drive_c")
        rel = os.path.relpath(os.path.abspath(local), drive_c)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            raise Refused(f"{local} is not under the prefix's drive_c")
        return str(PureWindowsPath("C:\\", *([] if rel == "." else rel.split(os.sep))))

    def environ(self) -> dict:
        """The shell's environment with every SA2_* variable removed: a run's settings are the
        kit's alone, as tools/s4/reach_receipt.py clean_environ holds a lifted run's."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SA2_")}
        if self.wine is not None:
            for k in DISPLAY_VARS:
                env.pop(k, None)
            env.update(WINE_ENV)
            env["WINEPREFIX"] = self.prefix
        return env

    def run(self, argv: list, env: dict, cwd: str, out_path: str, err_path: str,
            timeout: float) -> tuple:
        """Runs argv (argv[0] a Windows path) in the Windows folder `cwd`. Returns
        (rc or None when it was stopped at `timeout`, seconds)."""
        cmd = ([self.wine] if self.wine else []) + list(argv)
        if self.wine is None:
            cmd[0] = self.local(cmd[0])
        kw = {"start_new_session": True} if os.name != "nt" else {}
        t0 = time.monotonic()
        with open(out_path, "wb") as so, open(err_path, "wb") as se:
            proc = subprocess.Popen(cmd, cwd=self.local(cwd), env=env, stdout=so, stderr=se, **kw)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
                rc = None
            finally:
                if self.wine is not None:
                    server = os.path.join(os.path.dirname(self.wine), "wineserver")
                    subprocess.run([server, "-k"], env=env, capture_output=True, timeout=120)
        return rc, round(time.monotonic() - t0, 2)


def find_ci(root: str, rel: str) -> str | None:
    """`rel` (forward slashes) under the local folder `root`, each component matched exactly
    first and case-blind (ASCII) when that misses, as the runtime resolves a guest path
    (runtime/sa2_files.h, decision 4). None when it is not there."""
    cur = root
    for part in [p for p in rel.replace("\\", "/").split("/") if p]:
        nxt = os.path.join(cur, part)
        if not os.path.lexists(nxt):
            try:
                names = os.listdir(cur)
            except OSError:
                return None
            hits = [n for n in names if n.lower() == part.lower()]
            if len(hits) != 1:
                return None
            nxt = os.path.join(cur, hits[0])
        cur = nxt
    return cur if os.path.lexists(cur) else None


def clean_arg(text: str) -> str:
    """A folder argument as cmd.exe can hand it: `"C:\\Steam\\"` arrives as `C:\\Steam"`."""
    text = text.strip().strip('"').strip()
    while len(text) > 3 and text[-1] in "\\/":
        text = text[:-1]
    return text


def game_folder(host: Host, steam: str) -> str:
    """The game folder: the Steam root's steamapps\\common\\Sonic Adventure 2, or `steam` when
    it is the game folder itself."""
    base = PureWindowsPath(clean_arg(steam) or DEFAULT_STEAM)
    for cand in (base.joinpath(*GAME_DIR), base):
        local = host.local(str(cand))
        if find_ci(local, "sonic2app.exe") and find_ci(local, "resource/gd_PC"):
            return str(cand)
    raise Refused(f"no Sonic Adventure 2 folder at {base} (looked for {base.joinpath(*GAME_DIR)} "
                  f"and {base}, each holding sonic2app.exe and resource\\gd_PC)")


def verify_kit(kit_local: str, kit: dict) -> list:
    """Every kit file against kit.json: [] when all are there with their sha256."""
    out = []
    for rel, want in sorted(kit["files"].items()):
        p = os.path.join(kit_local, *rel.split("/"))
        if not os.path.isfile(p):
            out.append(f"{rel}: missing")
        elif os.path.getsize(p) != want["bytes"] or sha256_file(p) != want["sha256"]:
            out.append(f"{rel}: not the kit's file (size or sha256 differs)")
    return out


def check_install(host: Host, game: str, spec: dict) -> dict:
    """The game folder against what the registered runs read from theirs: each file's sha256,
    each path they found absent, and the saves beside the pinned state."""
    root = host.local(game)
    missing, changed, present = [], [], []
    for rel, want in sorted(spec["files"].items()):
        p = find_ci(root, rel)
        if p is None or not os.path.isfile(p):
            missing.append(rel)
        elif sha256_file(p) != want:
            changed.append(rel)
    for rel in spec["absent"]:
        if find_ci(root, rel) is not None:
            present.append(rel)
    save_dir = find_ci(root, spec["save_dir"])
    saves = sorted(os.listdir(save_dir)) if save_dir and os.path.isdir(save_dir) else []
    pinned = {n.lower() for n in spec["saves"]}
    hidden = [n for n in saves if n.lower() not in pinned and not n.lower().startswith(WHITEOUT)]
    exe = find_ci(root, "sonic2app.exe")
    exe_sha = sha256_file(exe) if exe else None
    return {"game_folder": game, "files_checked": len(spec["files"]), "missing": missing,
            "changed": changed, "absent_checked": len(spec["absent"]), "present": present,
            "saves": saves, "saves_hidden": hidden,
            "sonic2app_exe": {"sha256": exe_sha, "shipped_pin": spec["shipped_exe_sha256"],
                              "matches": exe_sha == spec["shipped_exe_sha256"]},
            "ok": not (missing or changed or present)}


def seed_overlay(kit_local: str, kit: dict, vfs_local: str, hidden: list) -> list:
    """The run's write overlay before it starts: the kit's seed (the Config files and pinned
    saves) and a whiteout marker for each save the game folder has beyond the pinned state.
    Returns the overlay files written, relative, with forward slashes."""
    seed = os.path.join(kit_local, *kit["overlay_seed"].split("/"))
    written = []
    for dirpath, _dirs, files in os.walk(seed):
        for name in sorted(files):
            src = os.path.join(dirpath, name)
            rel = os.path.relpath(src, seed).replace(os.sep, "/")
            dst = os.path.join(vfs_local, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            written.append(rel)
    save_dir = kit["install"]["save_dir"]
    for name in hidden:
        rel = f"{save_dir}/{WHITEOUT}{name}"
        dst = os.path.join(vfs_local, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").close()
        written.append(rel)
    return sorted(written)


def substitute(text: str, tokens: dict) -> str:
    for k, v in tokens.items():
        text = text.replace(k, v)
    return text


def first_difference(registered: str, got: str) -> dict | None:
    """The first row where two tick captures differ: its tick, the differing columns (at most
    eight, with both values) and how many differ; a header or a length difference says so.
    None when every row is equal."""
    with open(registered, newline="") as fa, open(got, newline="") as fb:
        ra, rb = csv.reader(fa), csv.reader(fb)
        ha, hb = next(ra, []), next(rb, [])
        if ha != hb:
            cols = [a for a, b in itertools.zip_longest(ha, hb) if a != b]
            return {"row": 0, "tick": None, "columns": ["header"] + [str(c) for c in cols[:7]],
                    "columns_differing": len(cols)}
        tick_col = ha.index("tick") if "tick" in ha else None
        for row, (x, y) in enumerate(itertools.zip_longest(ra, rb), start=1):
            if x == y:
                continue
            if x is None or y is None:
                have = y if x is None else x
                return {"row": row, "tick": have[tick_col] if tick_col is not None else row,
                        "columns": ["run length"],
                        "ended": "registered" if x is None else "this run",
                        "columns_differing": 0}
            n = max(len(x), len(y))
            cells = [(ha[i] if i < len(ha) else f"#{i}", x[i] if i < len(x) else None,
                      y[i] if i < len(y) else None) for i in range(n)]
            diff = [(c, a, b) for c, a, b in cells if a != b]
            return {"row": row, "tick": x[tick_col] if tick_col is not None else row,
                    "columns": [c for c, _, _ in diff[:8]],
                    "values": [{"column": c, "registered": a, "this_run": b}
                               for c, a, b in diff[:8]],
                    "columns_differing": len(diff)}
    return None


def dig(d, path):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def record_problems(rec: dict, run: dict, env: dict) -> list:
    """The run's own record against what it was handed (tools/s4/device_profile.py check_run,
    reach_receipt.switch_problems, wine_run.exe_problems)."""
    out = []
    exp = run["expect"]
    prof = rec.get("device_profile") if isinstance(rec.get("device_profile"), dict) else {}
    if prof.get("default") or prof.get("sha256") != exp["device_profile_sha256"]:
        out.append(f"device profile: the run read {prof.get('source')!r} "
                   f"({str(prof.get('sha256'))[:12]}), and was handed one hashing "
                   f"{exp['device_profile_sha256'][:12]}")
    elif prof.get("source") != env.get("SA2_DEVICE_PROFILE"):
        out.append(f"device profile: the run read {prof.get('source')!r}, and was handed "
                   f"{env.get('SA2_DEVICE_PROFILE')!r}")
    virt = rec.get("virtual") if isinstance(rec.get("virtual"), dict) else {}
    for k, want in sorted(exp["guest"].items()):
        if virt.get(k) != want:
            out.append(f"guest {k}: the run answered {virt.get(k)!r}, the capture's is {want!r}")
    for var, path in exp["switches"]:
        if dig(rec, path) is not True:
            out.append(f"{var}=1 was set and the run's record reads {'.'.join(path)} = "
                       f"{dig(rec, path)!r}")
    if isinstance(rec.get("dinput"), dict) and "host_input" in rec["dinput"]:
        out.append("the run's record reads dinput.host_input: a host keyboard answered")
    kind = dig(rec, ["exe", "kind"])
    if kind != run["exe_kind"]:
        refusal = dig(rec, ["exe", "overlay", "refusal"])
        out.append(f"asked for the {run['exe_kind']} executable; the run record says the loader "
                   f"mapped {kind!r}" + (f" ({refusal})" if refusal else ""))
    return out


def reach(rec: dict) -> dict:
    """tools/s4/tick_replay.py reach_of and stop_note, from the run's own record."""
    pump = rec.get("pump") or {}
    note = (rec.get("trap") or {}).get("note", "")
    return {"iterations": pump.get("iterations", 0),
            "import_calls": (rec.get("calls") or {}).get("total", 0),
            "frames_presented": ((rec.get("d3d9") or {}).get("frames") or {}).get("present", 0),
            "quit_by_host": pump.get("quit_by_host"), "poll_limit": pump.get("poll_limit"),
            "stop": note[:300]}


def run_one(host: Host, kit_local: str, kit_win: str, kit: dict, run: dict, scratch: str,
            game: str, install: dict, scale: float) -> dict:
    name = run["name"]
    rdir = str(PureWindowsPath(scratch, name))
    rlocal = host.local(rdir)
    os.makedirs(rlocal)
    tokens = {"{kit}": kit_win, "{run}": rdir, "{install}": game}
    seeded = seed_overlay(kit_local, kit, host.local(rdir + "\\vfs"), install["saves_hidden"])
    argv = [substitute(a, tokens) for a in run["argv"]]
    handed = {k: substitute(v, tokens) for k, v in run["env"].items()}
    env = host.environ()
    env.update(handed)
    print(f"run {name}: {' '.join(argv[:2])} ... (registered under Wine in "
          f"{run.get('wine_seconds')} s)", flush=True)
    rc, seconds = host.run(argv, env, rdir, os.path.join(rlocal, "stdout.txt"),
                           os.path.join(rlocal, "stderr.txt"), run["timeout"] * scale)
    res = {"name": name, "replay": run["replay"], "binary": run["binary"], "rc": rc,
           "timed_out": rc is None, "seconds": seconds, "wine_seconds": run.get("wine_seconds"),
           "argv": argv, "env": handed, "overlay_seeded": seeded, "problems": []}
    problems = res["problems"]
    if rc is None:
        problems.append(f"stopped at the time-out of {run['timeout'] * scale:.0f} s")
    elif rc != 0:
        problems.append(f"exit status {rc}")
    recpath = os.path.join(rlocal, "run.runrec")
    rec = {}
    if os.path.isfile(recpath):
        try:
            with open(recpath, encoding="utf-8", errors="replace") as f:
                rec = json.load(f)
        except ValueError as e:
            problems.append(f"run.runrec is not JSON: {e}")
    else:
        problems.append("the run wrote no run.runrec")
    if rec:
        problems += record_problems(rec, run, handed)
        res["reach"] = reach(rec)
        res["exe"] = {k: dig(rec, ["exe", k]) for k in ("kind", "path")}
    exp = run["expect"]
    cap = os.path.join(rlocal, "lifted.csv")
    if os.path.isfile(cap):
        got = sha256_file(cap)
        with open(cap, "rb") as f:
            ticks = max(sum(1 for _ in f) - 1, 0)
        res["capture"] = {"sha256": got, "expected_sha256": exp["capture_sha256"],
                          "equal": got == exp["capture_sha256"], "ticks": ticks,
                          "expected_ticks": exp["capture_ticks"]}
        if got != exp["capture_sha256"]:
            reg = os.path.join(kit_local, *exp["registered_capture"].split("/"))
            res["capture"]["first_difference"] = first_difference(reg, cap)
    else:
        res["capture"] = {"sha256": None, "expected_sha256": exp["capture_sha256"],
                          "equal": False}
        problems.append("the run wrote no lifted.csv")
    outs = {}
    for fname, want in sorted(exp.get("outputs", {}).items()):
        p = os.path.join(rlocal, fname)
        got = sha256_file(p) if os.path.isfile(p) else None
        outs[fname] = {"sha256": got, "expected_sha256": want, "equal": got == want}
        if got != want:
            problems.append(f"{fname} " + ("is missing" if got is None else
                                           "differs from the registered run's"))
    if outs:
        res["outputs"] = outs
    words = os.path.join(rlocal, "lifted-words.bin")
    if os.path.isfile(words):
        res["words"] = {"bytes": os.path.getsize(words), "sha256": sha256_file(words),
                        "note": "the word log carries the host's clock and block addresses; "
                                "it is kept, not compared"}
    if run["exe_kind"] == "shipped" and not install["sonic2app_exe"]["matches"]:
        problems.append("the game folder's sonic2app.exe is not the shipped build the release's "
                        "overlay table is for")
    res["ok"] = bool(res["capture"]["equal"] and not problems)
    return res


def host_info(host: Host) -> dict:
    info = {"platform": platform.platform(), "version": platform.version(),
            "machine": platform.machine(), "processor": platform.processor(),
            "cpus": os.cpu_count(), "python": sys.version.split()[0],
            "runner": "wine" if host.wine else "native",
            "window": "none: the binaries import only KERNEL32 and the Universal CRT"}
    if host.wine:
        info["wine"] = host.wine
        info["prefix"] = host.prefix
    ucrt = find_ci(host.local("C:\\"), "Windows/System32/ucrtbase.dll")
    if ucrt and os.path.isfile(ucrt):
        info["ucrtbase"] = {"path": r"C:\Windows\System32\ucrtbase.dll",
                            "bytes": os.path.getsize(ucrt), "sha256": sha256_file(ucrt),
                            "version": file_version(ucrt)}
    return info


def file_version(path: str) -> str | None:
    """A Windows file's version resource (VS_FIXEDFILEINFO), natively; None elsewhere."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr, n = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)):
            return None
        fixed = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms, ls = fixed[2], fixed[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001  the version is a note; the hash identifies the file
        return None


def fresh_scratch(host: Host, out_root: str) -> tuple:
    """C:\\sa2-runs\\double-check-<n>, n one above any number there, made here."""
    local = host.local(out_root)
    os.makedirs(local, exist_ok=True)
    taken = [int(n[len(SCRATCH):]) for n in os.listdir(local)
             if n.lower().startswith(SCRATCH) and n[len(SCRATCH):].isdigit()]
    n = max(taken, default=0) + 1
    path = str(PureWindowsPath(out_root, f"{SCRATCH}{n}"))
    os.makedirs(host.local(path))
    return path, n


def inside(child: str, parent: str) -> bool:
    c = str(PureWindowsPath(child)).lower().rstrip("\\") + "\\"
    p = str(PureWindowsPath(parent)).lower().rstrip("\\") + "\\"
    return c.startswith(p)


def write_records(host: Host, scratch: str, n: int, summary: dict, runs: list) -> str:
    """<scratch>\\double-check-<n>-records.zip: the summary, each run's record and stderr (and
    stdout when it is not the record again), and a failed run's captures."""
    zpath = host.local(str(PureWindowsPath(scratch, f"{SCRATCH}{n}-records.zip")))
    slocal = host.local(scratch)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(slocal, "summary.json"), "summary.json")
        for res in runs:
            rdir = os.path.join(slocal, res["name"])
            names = ["run.runrec", "stderr.txt", "stdout.txt"]
            if not res["ok"]:
                names += list(FAILED_EXTRAS)
            rec = os.path.join(rdir, "run.runrec")
            for name in names:
                p = os.path.join(rdir, name)
                if not os.path.isfile(p):
                    continue
                if name == "stdout.txt" and os.path.isfile(rec) and \
                        os.path.getsize(p) == os.path.getsize(rec) and \
                        sha256_file(p) == sha256_file(rec):
                    continue
                z.write(p, f"{res['name']}/{name}")
    return zpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The double-f80 Windows game's registered "
                                             "replays, run natively, against their captures.")
    ap.add_argument("--steam", default=DEFAULT_STEAM,
                    help=f"the Steam folder, or the game folder itself (default {DEFAULT_STEAM})")
    ap.add_argument("--out-root", default=DEFAULT_OUT,
                    help=f"where the fresh scratch folder goes (default {DEFAULT_OUT})")
    ap.add_argument("--runs", default=None,
                    help="a comma-separated subset of the kit's runs (default all)")
    ap.add_argument("--copy-to", default=None,
                    help="also copy the records zip into this folder (Z:\\ is the shared one)")
    ap.add_argument("--timeout-scale", type=float, default=1.0,
                    help="multiply every run's time-out (a slow machine)")
    ap.add_argument("--wine", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--wine-prefix", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    t0 = time.time()
    try:
        host = Host(a.wine, a.wine_prefix)
        kit_local = os.path.dirname(os.path.abspath(__file__))
        kit_win = host.windows(kit_local)
        with open(os.path.join(kit_local, "kit.json"), encoding="utf-8") as f:
            kit = json.load(f)
        if kit.get("schema") != KIT_SCHEMA:
            raise Refused(f"kit.json's schema is {kit.get('schema')!r}, not {KIT_SCHEMA}")
        print(f"kit {kit['id']} built {kit['built']} at {kit['commit'][:12]}; checking its "
              f"{len(kit['files'])} files", flush=True)
        damaged = verify_kit(kit_local, kit)
        if damaged:
            raise Refused("the kit is damaged; extract it again: " + "; ".join(damaged))
        game = game_folder(host, a.steam)
        steam_root = str(PureWindowsPath(clean_arg(a.steam) or DEFAULT_STEAM))
        out_root = str(PureWindowsPath(clean_arg(a.out_root)))
        for holder in (game, steam_root):
            if inside(out_root, holder):
                raise Refused(f"--out-root {out_root} is inside {holder}: the runs never write "
                              f"there")
        wanted = [r["name"] for r in kit["runs"]]
        if a.runs:
            asked = [x.strip() for x in a.runs.split(",") if x.strip()]
            unknown = sorted(set(asked) - set(wanted))
            if unknown:
                raise Refused(f"--runs {unknown}: the kit's runs are {wanted}")
            wanted = [w for w in wanted if w in asked]
    except Refused as e:
        print(f"DOUBLE_CHECK_REFUSED {e}", flush=True)
        return 2
    print(f"game folder {game} (read only); checking the files the registered runs read",
          flush=True)
    install = check_install(host, game, kit["install"])
    print(f"install: {install['files_checked']} files, {len(install['missing'])} missing, "
          f"{len(install['changed'])} changed; {len(install['present'])} of "
          f"{install['absent_checked']} absent paths present; saves {install['saves']} "
          f"(hidden from the runs: {install['saves_hidden']}); sonic2app.exe "
          f"{'is' if install['sonic2app_exe']['matches'] else 'is NOT'} the shipped pin",
          flush=True)
    scratch, n = fresh_scratch(host, out_root)
    print(f"scratch {scratch}", flush=True)
    summary = {"schema": SUMMARY_SCHEMA, "kit": {k: kit.get(k) for k in
                                                 ("id", "built", "commit", "question")},
               "kit_json_sha256": sha256_file(os.path.join(kit_local, "kit.json")),
               "kit_at": kit_win, "host": host_info(host), "install": install,
               "scratch": scratch, "runs": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    spath = host.local(str(PureWindowsPath(scratch, "summary.json")))

    def save():
        with open(spath, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1, sort_keys=True)
            f.write("\n")

    save()
    for run in [r for r in kit["runs"] if r["name"] in wanted]:
        res = run_one(host, kit_local, kit_win, kit, run, scratch, game, install,
                      a.timeout_scale)
        summary["runs"].append(res)
        save()
        cap = res["capture"]
        line = (f"  {res['name']}: rc {res['rc']} in {res['seconds']} s; capture "
                f"{'EQUAL' if cap['equal'] else 'DIFFERS'} ({str(cap.get('sha256'))[:12]}, "
                f"registered {cap['expected_sha256'][:12]})")
        fd = cap.get("first_difference")
        if fd:
            line += (f"; first difference at tick {fd['tick']} (row {fd['row']}), columns "
                     f"{', '.join(fd['columns'])}")
        if res["problems"]:
            line += f"; problems: {'; '.join(res['problems'])}"
        print(line, flush=True)
    ok = bool(summary["runs"]) and all(r["ok"] for r in summary["runs"])
    summary["ok"] = ok
    summary["install_ok"] = install["ok"]
    summary["verdict"] = "DOUBLE_CHECK_OK" if ok else "DOUBLE_CHECK_FAIL"
    summary["seconds"] = round(time.time() - t0, 2)
    summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save()
    zpath = write_records(host, scratch, n, summary, summary["runs"])
    print(f"summary {host.windows(spath) if host.wine else spath}", flush=True)
    print(f"records {host.windows(zpath) if host.wine else zpath}", flush=True)
    if a.copy_to:
        dest = os.path.join(host.local(clean_arg(a.copy_to)), os.path.basename(zpath))
        try:
            shutil.copyfile(zpath, dest)
            print(f"copied the records to {dest}", flush=True)
        except OSError as e:
            print(f"could not copy the records to {dest}: {e}", flush=True)
    if ok:
        print(f"DOUBLE_CHECK_OK {', '.join(r['name'] for r in summary['runs'])}: every capture "
              f"has its registered sha256", flush=True)
        return 0
    bad = [r["name"] for r in summary["runs"] if not r["ok"]]
    print(f"DOUBLE_CHECK_FAIL {', '.join(bad)}" +
          ("" if install["ok"] else " (the game folder differs from the registered runs' "
                                    "installation; see install in the summary)"), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
