#!/usr/bin/env python3
r"""vm_kit: a one-command native-Windows replay check of the double-f80 Windows game, as a kit for
the user's own Windows VM, proved under Proton's Wine before it goes there.

Subtitle:

    source tools/env.sh
    .venv/bin/python tools/s8/vm_kit.py build          # private/generated/s8/vm-kit/sa2-double-check.zip
    tools/slot.sh .venv/bin/python tools/s8/vm_kit.py prove [--runs boot,metalharbor,release-boot]
    cp private/generated/s8/vm-kit/sa2-double-check.zip /home/user/VM-Shared/sa2-double-check.zip

and on the VM, in PowerShell (Z:\ is the shared folder):

    Expand-Archive -Force Z:\sa2-double-check.zip C:\sa2-kit
    py -3 C:\sa2-kit\run_check.py --steam "C:\Program Files (x86)\Steam" --copy-to Z:\

The question: the double-mode Windows game imports acos, asin, atan, atan2, pow and remainder
from the Universal CRT. Under Wine, whose CRT math is musl's, its boot and Metal Harbor captures
equal the registered ones. Does Microsoft's UCRT give the same?

`build` writes the kit folder (default private/generated/s8/vm-kit/kit, which must not exist yet)
and the zip of it:
  * bin\boot\sa2_main.exe and bin\metalharbor\sa2_main.exe: the win_link.py replay builds of the
    double objects (private/generated/s8/pc-double/win-boot, win-stage), each held to its
    link.record and to the runner its replay links (wine_run.py `linked_for`); bin\release\
    sa2-game.exe, the double release's game. Their imports are listed and must all be the
    system's (KERNEL32 and the Universal CRT): nothing else needs to be beside them.
  * game\sonic2app-unpacked.exe, held to tools/pins.json: the executable the registered replays
    load (the replay builds have no overlay table). It is the user's own game's file and goes to
    the user's own VM only, never into git.
  * per replay, what wine_run.py hands a run: tick_setup's environment for boot (loop_run's
    BASE_ENV with the write overlay in the run's folder, the reach configuration, the capture's
    device profile, the tick and word logs) and stage_setup's for Metal Harbor (stage_run.command's
    run spec: the stage, its input recording, poll budget and alarm, the observers), called here
    with a template folder and each host path turned into a token: {run} the run's scratch
    folder, {install} the game folder, {kit} the kit. The device profile and the input recording
    are copied into the kit, each held to its pin. Any other value holding a host path is refused.
  * the registered captures (Documentation/receipts/s4-tick-replay-v1.json's lifted runs, held
    to their sha256), for the first-difference report, and the stage run's registered goal
    latch, stage trace and applied input hashes.
  * install.files: every file the registered-equal Wine runs read from their installation
    (their run records' `files.resolutions`), with the clean Steam tree's sha256
    (Documentation/receipts/install-hashes-v1.json); install.absent, the paths they found
    absent. Left out, each for its reason: the Config files (the release writes its defaults
    into the overlay when the game folder has none, and the kit seeds the same), the save folder
    (the pinned save state, Documentation/save-state.json, is seeded; other saves are hidden by
    whiteouts) and a Mod Loader's *_orig original (an unmodded folder has none and the runtime
    maps the named file).
  * the overlay seed: Config\UserConfig.cfg and Config\Keyboard.cfg as the registered runs read
    them (private/install, the sizes their records show), and the pinned saves (empty files).
  * run_check.py (tools/s8/vm_kit_run_check.py), README.txt, and kit.json with every file's
    sha256.

`prove` extracts the zip into a fresh folder and runs run_check.py's own steps under Proton's
Wine, in bwrap: a new prefix whose dosdevices hold C: only (no Z:, so nothing reaches a Unix
path the VM would not have), the Steam game folder bound read only at the VM's
C:\Program Files (x86)\Steam\steamapps\common\Sonic Adventure 2, the kit at C:\sa2-kit, the
scratch at C:\sa2-runs, and Steam's tree read only on top. /usr/bin/python3 runs it, so the
proof also shows run_check.py needs nothing beyond the standard library. Writes
<out>/prove.record (JSON, not .json: the M7 census counts every *.json under private/generated).
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
import time
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VMK = ROOT / "private" / "generated" / "s8" / "vm-kit"
PCD = ROOT / "private" / "generated" / "s8" / "pc-double"
EVIDENCE = ROOT / "private" / "evidence" / "s8"
DEFAULTS = {"boot_exe": PCD / "win-boot" / "sa2_main.exe",
            "stage_exe": PCD / "win-stage" / "sa2_main.exe",
            "release_game": PCD / "release-windows" / "bin" / "sa2-game.exe",
            "read_sets": [EVIDENCE / "pc-double-wine-replay-boot" / "run.runrec",
                          EVIDENCE / "pc-double-wine-replay-mh" / "run.runrec"]}
RUN_CHECK = HERE / "vm_kit_run_check.py"
sys.path.insert(0, str(HERE))
import vm_kit_run_check as RC  # noqa: E402  find_ci: the runtime's case-blind lookup
INSTALL_TABLE = ROOT / "Documentation" / "receipts" / "install-hashes-v1.json"
SAVE_STATE = ROOT / "Documentation" / "save-state.json"
PINS = ROOT / "tools" / "pins.json"
STAGED_INSTALL = ROOT / "private" / "install"
KIT_SCHEMA = "sa2-s8-vm-kit/v1"
PROVE_SCHEMA = "sa2-s8-vm-kit-prove/v1"
KIT_ID = "sa2-double-check"
SELF = "tools/s8/vm_kit.py"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
# The run's own outputs a template run names (wine_run.py's tick and stage setups): each becomes
# {run}\<its path in the run folder>.
RUN_PATH_VARS = ("SA2_VFS_WRITE_ROOT", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS",
                 "SA2_STAGE_TRACE", "SA2_GOAL_LATCH", "SA2_INPUT_APPLIED")
USER_CONFIG = ("Config/UserConfig.cfg", "Config/Keyboard.cfg")
ORIG = re.compile(r"_orig\.[^/]*$", re.IGNORECASE)
SYSTEM_DLL = re.compile(r"^(kernel32\.dll|api-ms-win-crt-[a-z]+-l1-1-0\.dll)$", re.IGNORECASE)
# Each run's time-out on the VM, in seconds: wine_run.py's default for a tick replay, and the
# stage alarm plus room for a slower machine.
TICK_TIMEOUT = 1800
STAGE_SLACK = 600
# The VM's paths (run_check.py's defaults), which the proof maps.
VM_GAME = ("Program Files (x86)", "Steam", "steamapps", "common", "Sonic Adventure 2")
VM_STEAM = r"C:\Program Files (x86)\Steam"
BWRAP = "/usr/bin/bwrap"
SYSTEM_PYTHON = "/usr/bin/python3"


class Refused(SystemExit):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def win_rel(rel: str) -> str:
    return rel.replace("/", "\\")


def run_token(value: str, tdir: Path) -> str:
    """A path a template run was handed (relative to the repository, or absolute) as
    {run}\\<its path under the run's folder>; refused when it is not under it."""
    p = Path(value) if os.path.isabs(value) else ROOT / value
    p = Path(os.path.abspath(p))
    try:
        rel = p.relative_to(tdir)
    except ValueError:
        raise Refused(f"vm_kit: {value} is not under the template run folder {tdir}") from None
    return "{run}" + ("" if str(rel) == "." else "\\" + win_rel(rel.as_posix()))


def kit_env(env: dict, tdir: Path, copies: dict) -> dict:
    """wine_run.py's run environment for the VM: the run's outputs under {run}, the installation
    as {install}, the device profile and input recording as the kit's copies (`copies`: variable
    -> the kit-relative path it was copied to), everything else as it is. A value that still
    names a host path is refused: the kit would hand the VM a Linux path."""
    out = {}
    for k, v in env.items():
        if k in RUN_PATH_VARS:
            out[k] = run_token(v, tdir)
        elif k == "SA2_INSTALL":
            out[k] = "{install}"
        elif k in copies:
            out[k] = "{kit}\\" + win_rel(copies[k])
        elif "/" in v or str(ROOT) in v:
            raise Refused(f"vm_kit: {k}={v!r} names a host path the kit does not carry")
        else:
            out[k] = v
    return out


def kit_args(args: list, tdir: Path) -> list:
    """The probe's arguments for the VM: a path under the template run folder as {run}\\..."""
    out = []
    for a in args:
        if "/" in a:
            out.append(run_token(a, tdir))
        else:
            out.append(a)
    return out


def install_spec(read_sets: list, table: dict, save_state: dict, staged: Path) -> dict:
    """What the registered-equal runs read from their installation, as the VM's folder must
    hold it: install.files (path -> the clean Steam tree's sha256) and install.absent."""
    tab = {e["path"].lower(): e for e in table["files"]}
    save_dir = save_state["directory"]
    files, absent, found = {}, set(), set()
    for rr in read_sets:
        d = json.loads(Path(rr).read_text(errors="replace"))
        for r in d["files"]["resolutions"]:
            rel, status, layer = r["rel"], r["status"], r["layer"]
            low = rel.lower()
            if not rel or layer == 1:
                continue                          # the overlay's answer, not the installation's
            if low == save_dir.lower() or low.startswith(save_dir.lower() + "/"):
                continue                          # the save state: seeded and whited out
            if low == "config" or (low.startswith("config/") and status == "ok"):
                if status == "ok" and rel not in USER_CONFIG and low != "config":
                    raise Refused(f"vm_kit: the run read {rel} from its Config folder, which the "
                                  f"kit does not seed")
                continue                          # the user's config: seeded
            if status == "not-found":
                absent.add(rel)
                continue
            if status != "ok":
                raise Refused(f"vm_kit: {rr}: {rel} resolved as {status}")
            found.add(low)
            if ORIG.search(rel):
                continue                          # a Mod Loader's original: none unmodded
            if low in tab:
                files[tab[low]["path"]] = tab[low]["sha256"]
            elif os.path.isdir(RC.find_ci(str(staged), rel) or "/nonexistent"):
                continue                          # a directory (the guest's spelling, case-blind)
            else:
                raise Refused(f"vm_kit: the run read {rel}, which the install table "
                              f"{table.get('id')} does not hold")
    clash = sorted(a for a in absent if a.lower() in found)
    if clash:
        raise Refused(f"vm_kit: found and also absent in the read sets: {clash}")
    return {"files": dict(sorted(files.items())), "absent": sorted(absent, key=str.lower),
            "save_dir": save_dir, "saves": dict(save_state["files"]),
            "table": {"id": table.get("id"), "sha256": sha256(INSTALL_TABLE)}}


def pe_imports(path: Path) -> list:
    import pefile  # noqa: PLC0415  the venv's
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
    names += [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
    pe.close()
    return sorted(set(names), key=str.lower)


def put(kit: Path, rel: str, src: Path, want: str | None = None) -> str:
    """Copies `src` into the kit at `rel`; `want`, when given, is the sha256 it must have."""
    got = sha256(src)
    if want is not None and got != want:
        raise Refused(f"vm_kit: {src} hashes to {got[:12]}, not its pin {want[:12]}")
    dst = kit / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return got


README = r"""SA2 double-f80 native check ({id}, built {built} at {commit})

The question: do the double-mode Windows game's boot and Metal Harbor replays give the registered
captures natively (Microsoft's UCRT math), as they do under Wine? Three runs: boot and Metal
Harbor on the replay builds, and boot on the release's own sa2-game.exe with the Steam copy's
sonic2app.exe.

1. Extract this zip to C:\sa2-kit. In PowerShell (Z: is the shared folder, MAP-WINDOWS-Z.cmd):
       Expand-Archive -Force Z:\sa2-double-check.zip C:\sa2-kit
2. Run:
       py -3 C:\sa2-kit\run_check.py --steam "C:\Program Files (x86)\Steam" --copy-to Z:\

It reads the Steam copy of Sonic Adventure 2 and never writes to it. Each run gets a fresh
folder under C:\sa2-runs\double-check-<n>. No window opens. The runs take a few minutes:
boot about {boot_s} s, Metal Harbor about {mh_s} s under Wine on the Linux host.

The last line is DOUBLE_CHECK_OK, or DOUBLE_CHECK_FAIL with the first differing tick and
columns. The summary is C:\sa2-runs\double-check-<n>\summary.json, and
double-check-<n>-records.zip (copied to Z:\ with --copy-to) holds what to bring back.
"""


def build(a) -> int:
    sys.path[:0] = [str(ROOT / "tools" / "s7"), str(ROOT / "tools" / "s4")]
    import link_s4 as LS  # noqa: PLC0415  read-only: PRODUCTION_WRAPS
    import loop_run as LR  # noqa: PLC0415
    import reach_receipt as RR  # noqa: PLC0415
    import stage_run as SR  # noqa: PLC0415
    import tick_replay as TR  # noqa: PLC0415
    import wine_run as WR  # noqa: PLC0415  read-only: its setups are called, never main

    kit = Path(os.path.abspath(a.kit))
    zpath = Path(os.path.abspath(a.zip))
    for p, what in ((kit, "--kit"), (zpath, "--zip")):
        if not p.is_relative_to(ROOT / "private"):
            raise Refused(f"vm_kit: {what} {p} is not under private/")
    if kit.exists():
        raise Refused(f"vm_kit: {kit} exists; every kit is built into a new folder "
                      f"(tools/rmtree.py removes an old one)")
    template = kit.parent / (kit.name + ".template")
    pins = json.loads(PINS.read_text())["executables"]
    reg = json.loads(TR.REGISTRY.read_text())
    entries = {e["name"]: e for e in (reg["replays"] if isinstance(reg, dict) else reg)}
    table = json.loads(INSTALL_TABLE.read_text())
    save_state = json.loads(SAVE_STATE.read_text())
    bad_saves = {n: s for n, s in save_state["files"].items() if s != EMPTY_SHA}
    if bad_saves:
        raise Refused(f"vm_kit: the pinned save state holds non-empty saves {sorted(bad_saves)}, "
                      f"which the kit would have to carry")
    for rr in a.read_set:
        cap = Path(rr).parent / "lifted.csv"
        name = "boot" if "boot" in Path(rr).parent.name else "metalharbor"
        want = WR.registered(TR, name)["lifted"]["sha256"]
        if not cap.is_file() or sha256(cap) != want:
            raise Refused(f"vm_kit: the read set {rr} is not from a run whose capture is the "
                          f"registered {name} one ({want[:12]})")
    spec = install_spec(a.read_set, table, save_state, STAGED_INSTALL)
    spec["shipped_exe_sha256"] = pins["sonic2app.exe"]["sha256"]

    kit.mkdir(parents=True)
    unpacked, _ = LR.exe_argv("unpacked")
    shipped, shipped_env = LR.exe_argv("shipped")
    put(kit, "game/sonic2app-unpacked.exe", unpacked, pins["sonic2app-unpacked.exe"]["sha256"])
    runs, binaries = [], {}
    wine_seconds = {}
    for name, rec in (("boot", EVIDENCE / "pc-double-wine-replay-boot" / "wine-run.record"),
                      ("metalharbor", EVIDENCE / "pc-double-wine-replay-mh" / "wine-run.record")):
        if rec.is_file():
            wine_seconds[name] = (json.loads(rec.read_text()).get("run") or {}).get("seconds")
    plan = (("boot", "boot", Path(a.boot_exe), "bin/boot/sa2_main.exe", "unpacked"),
            ("metalharbor", "metalharbor", Path(a.stage_exe), "bin/metalharbor/sa2_main.exe",
             "unpacked"),
            ("release-boot", "boot", Path(a.release_game), "bin/release/sa2-game.exe",
             "shipped"))
    for name, replay, exe, exe_rel, kind in plan:
        entry = entries[replay]
        stage = TR.is_stage(entry)
        reg_run = WR.registered(TR, replay)
        profile = TR.native_device_profile(entry)
        tdir = template / name
        if stage:
            setup = WR.stage_setup(TR, RR, LR, SR, entry, tdir, profile)
        else:
            setup = WR.tick_setup(TR, RR, LR, entry, tdir, profile)
        if kind == "unpacked":
            problems = WR.linked_for(exe, setup["hooks"], setup["wraps"], LS.PRODUCTION_WRAPS)
            if problems:
                raise Refused(f"vm_kit: {exe} is not linked for {replay}'s runner: "
                              f"{'; '.join(problems)}")
            guest_exe = "{kit}\\game\\sonic2app-unpacked.exe"
            env = dict(setup["env"])
        else:
            head = json.loads((exe.parents[1] / "release.manifest").read_text()
                              .splitlines()[0][2:])
            if ((head.get("game") or {}).get("f80") or {}).get("mode") != "double":
                raise Refused(f"vm_kit: {exe} is not a double-f80 release's game")
            guest_exe = "{install}\\" + shipped.name
            # tools/s8/pc_double.py boot: the tick setup, the kind, SDL's dummy audio.
            env = dict(setup["env"], **shipped_env, SDL_AUDIO_DRIVER="dummy")
        copies = {}
        prof_rel = f"replays/{replay}/sa2-device-profile.txt"
        put(kit, prof_rel, ROOT / profile["path"], profile["sha256"])
        copies["SA2_DEVICE_PROFILE"] = prof_rel
        if "SA2_INPUT_SCRIPT" in env:
            in_rel = f"replays/{replay}/sa2-input.csv"
            put(kit, in_rel, Path(env["SA2_INPUT_SCRIPT"]), entry["input"]["sha256"])
            copies["SA2_INPUT_SCRIPT"] = in_rel
        cap_rel = f"replays/{replay}/registered-lifted.csv"
        lifted = reg_run["lifted"]
        put(kit, cap_rel, ROOT / lifted["capture"], lifted["sha256"])
        bsha = put(kit, exe_rel, exe)
        imports = pe_imports(exe)
        foreign = [d for d in imports if not SYSTEM_DLL.match(d)]
        if foreign:
            raise Refused(f"vm_kit: {exe} imports {foreign}, which the kit does not carry")
        binaries[exe_rel] = {"source": str(exe.relative_to(ROOT)), "sha256": bsha,
                             "imports": imports}
        outputs = {}
        if stage:
            outputs = {"goal-latch.csv": lifted["goal_latch"]["sha256"],
                       "stage-trace.csv": lifted["stage_trace"]["sha256"],
                       "input-applied.csv": lifted["input_applied"]["sha256"]}
        switches = [[var, list(path)] for var, path in RR.SWITCH_RECORD.items()
                    if {**RR.ENV, **setup["profile_env"]}.get(var) == "1"]
        ticks = max((ROOT / lifted["capture"]).read_bytes().count(b"\n") - 1, 0)
        runs.append({
            "name": name, "replay": replay, "binary": exe_rel, "exe_kind": kind,
            "argv": ["{kit}\\" + win_rel(exe_rel), guest_exe, "--json", "{run}\\run.runrec"]
                    + kit_args(setup["probe"], tdir),
            "env": kit_env(env, tdir, copies),
            "timeout": (setup["alarm"] + STAGE_SLACK) if stage else TICK_TIMEOUT,
            "wine_seconds": wine_seconds.get(replay),
            "expect": {"capture_sha256": lifted["sha256"], "capture_ticks": ticks,
                       "registered_capture": cap_rel, "outputs": outputs,
                       "device_profile_sha256": profile["sha256"],
                       "guest": TR.DP.guest_record(profile), "switches": switches},
            "wine_run": {"hooks": setup["hooks"], "wraps": setup["wraps"]}})
    seed = "overlay"
    for rel in USER_CONFIG:
        put(kit, f"{seed}/{rel}", STAGED_INSTALL / rel)
    for save in save_state["files"]:
        dst = kit / seed / spec["save_dir"] / save
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"")
    shutil.copyfile(RUN_CHECK, kit / "run_check.py")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                          text=True).stdout.strip()
    built = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    (kit / "README.txt").write_text(README.format(
        id=KIT_ID, built=built, commit=head[:12], boot_s=wine_seconds.get("boot"),
        mh_s=wine_seconds.get("metalharbor")).replace("\n", "\r\n"))
    files = {}
    for p in sorted(kit.rglob("*")):
        if p.is_file():
            rel = p.relative_to(kit).as_posix()
            files[rel] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    doc = {"schema": KIT_SCHEMA, "id": KIT_ID, "generated_by": SELF, "commit": head,
           "built": built,
           "question": "Do the double-f80 Windows game's boot and Metal Harbor replays, run "
                       "natively on Windows (Microsoft's UCRT math), give the registered "
                       "captures, as they do under Wine?",
           "files": files, "binaries": binaries, "runs": runs, "install": spec,
           "overlay_seed": seed,
           "inputs": {"tools": {f: sha256(ROOT / f) for f in (
               SELF, "tools/s8/vm_kit_run_check.py", "tools/s7/wine_run.py",
               "tools/s4/stage_run.py", "tools/s4/loop_run.py", "tools/s4/reach_receipt.py")},
               "read_sets": {str(Path(r).resolve().relative_to(ROOT)): sha256(Path(r))
                             for r in a.read_set},
               "registry_sha256": sha256(TR.REGISTRY),
               "receipt_sha256": sha256(TR.RECEIPT)}}
    (kit / "kit.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    zpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = zpath.with_name(zpath.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(kit.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo(p.relative_to(kit).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with open(p, "rb") as src, z.open(info, "w") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    tmp.replace(zpath)
    digest = sha256(zpath)
    print(f"VM_KIT_BUILT {zpath} {zpath.stat().st_size} bytes sha256 {digest}; {len(files)} "
          f"files; runs {[r['name'] for r in runs]}; install check {len(spec['files'])} files, "
          f"{len(spec['absent'])} absent paths")
    return 0


def extract(zpath: Path, dest: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise Refused(f"vm_kit: {zpath} names {info.filename}, outside the kit")
        z.extractall(dest)


def prove(a) -> int:
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import wine_run as WR  # noqa: PLC0415  read-only: WINE, STEAM, WINE_ENV

    zpath = Path(os.path.abspath(a.zip))
    if a.out is None:
        taken = [int(p.name.split("-", 1)[1]) for p in VMK.glob("prove-*")
                 if p.name.split("-", 1)[1].isdigit()]
        out = VMK / f"prove-{max(taken, default=0) + 1}"
    else:
        out = Path(os.path.abspath(a.out))
    if not out.is_relative_to(ROOT / "private"):
        raise Refused(f"vm_kit: --out {out} is not under private/")
    if out.exists() and any(out.iterdir()):
        raise Refused(f"vm_kit: {out} is not empty; every proof starts from a fresh folder")
    game = WR.STEAM / "steamapps" / "common" / "Sonic Adventure 2"
    for tool in (WR.WINE, WR.WINESERVER, Path(BWRAP), Path(SYSTEM_PYTHON), game / "sonic2app.exe"):
        if not Path(tool).is_file():
            raise Refused(f"vm_kit: {tool} is not there")
    kit = out / "kit"
    prefix = out / "prefix"
    extract(zpath, kit)
    # The prefix's drives before Wine first starts: C: alone. Wine adds C: and Z: only to a
    # dosdevices folder it creates itself.
    (prefix / "drive_c").mkdir(parents=True)
    (prefix / "dosdevices").mkdir()
    os.symlink("../drive_c", prefix / "dosdevices" / "c:")
    game_at = prefix / "drive_c" / Path(*VM_GAME)
    kit_at = prefix / "drive_c" / "sa2-kit"
    game_at.mkdir(parents=True)
    kit_at.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SA2_") and k not in WR.DISPLAY_VARS}
    env.update(WR.WINE_ENV, WINEPREFIX=str(prefix), SDL_AUDIO_DRIVER="dummy")
    sandbox = [BWRAP, "--dev-bind", "/", "/", "--ro-bind", str(WR.STEAM), str(WR.STEAM),
               "--ro-bind", str(game), str(game_at), "--ro-bind", str(kit), str(kit_at),
               "--die-with-parent", "--chdir", str(prefix / "drive_c"), "--"]
    t0 = time.time()
    init = subprocess.run(sandbox + [str(WR.WINE), "wineboot", "-i"], env=env,
                          capture_output=True, text=True, timeout=600)
    subprocess.run(sandbox + [str(WR.WINESERVER), "-w"], env=env, capture_output=True, timeout=600)
    # Drive letters only: Wine also lists serial and parallel ports there (com1, lpt1).
    drives = sorted(p.name for p in (prefix / "dosdevices").iterdir()
                    if re.fullmatch(r"[a-z]:", p.name.lower()))
    record: dict = {"schema": PROVE_SCHEMA, "tool": SELF, "tool_sha256": sha256(Path(__file__)),
                    "zip": {"path": str(zpath), "sha256": sha256(zpath)},
                    "wine": str(WR.WINE), "prefix": str(prefix), "drives": drives,
                    "wineboot_rc": init.returncode, "game_folder": str(game),
                    "game_at": "C:\\" + "\\".join(VM_GAME), "kit_at": "C:\\sa2-kit",
                    "python": SYSTEM_PYTHON, "sandbox": sandbox}
    rec_path = out / "prove.record"
    if [d.lower() for d in drives] != ["c:"]:
        record["problem"] = f"the prefix has drives {drives}, not C: alone"
        rec_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        raise Refused(f"vm_kit: {record['problem']}")
    v = subprocess.run(sandbox + [str(WR.WINE), "--version"], env=env, capture_output=True,
                       text=True, timeout=120)
    record["wine_version"] = v.stdout.strip()
    cmd = [SYSTEM_PYTHON, "-u", str(kit_at / "run_check.py"), "--steam", VM_STEAM,
           "--wine", str(WR.WINE), "--wine-prefix", str(prefix)]
    if a.runs:
        cmd += ["--runs", a.runs]
    record["run_check_argv"] = cmd[2:]
    log = out / "prove.log"
    with open(log, "w") as fh:
        p = subprocess.run(sandbox + cmd, env=env, stdout=fh, stderr=subprocess.STDOUT,
                           timeout=a.timeout)
    record["rc"] = p.returncode
    record["seconds"] = round(time.time() - t0, 2)
    text = log.read_text()
    print(text, end="")
    summaries = sorted((prefix / "drive_c" / "sa2-runs").glob("double-check-*/summary.json"))
    if summaries:
        s = json.loads(summaries[-1].read_text())
        record["summary"] = {"path": str(summaries[-1]), "sha256": sha256(summaries[-1]),
                             "verdict": s.get("verdict"), "install_ok": s.get("install_ok"),
                             "runs": {r["name"]: {"ok": r["ok"], "seconds": r["seconds"],
                                                  "capture": r["capture"].get("sha256"),
                                                  "problems": r["problems"]}
                                      for r in s.get("runs", [])}}
    record["ok"] = p.returncode == 0 and (record.get("summary") or {}).get(
        "verdict") == "DOUBLE_CHECK_OK"
    rec_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(("VM_KIT_PROVED " if record["ok"] else "VM_KIT_PROVE_FAIL ") +
          f"{rec_path} rc={p.returncode} seconds={record['seconds']} "
          f"zip={record['zip']['sha256'][:12]}")
    return 0 if record["ok"] else 1


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="the kit folder and its zip")
    b.add_argument("--kit", type=Path, default=VMK / "kit", help="a new folder under private/")
    b.add_argument("--zip", type=Path, default=VMK / f"{KIT_ID}.zip")
    b.add_argument("--boot-exe", type=Path, default=DEFAULTS["boot_exe"])
    b.add_argument("--stage-exe", type=Path, default=DEFAULTS["stage_exe"])
    b.add_argument("--release-game", type=Path, default=DEFAULTS["release_game"])
    b.add_argument("--read-set", type=Path, nargs="+", default=DEFAULTS["read_sets"],
                   help="run records of registered-equal runs: what the installation must hold")
    p = sub.add_parser("prove", help="run_check.py's steps under Proton's Wine")
    p.add_argument("--zip", type=Path, default=VMK / f"{KIT_ID}.zip")
    p.add_argument("--out", type=Path, default=None,
                   help="a fresh folder under private/ (default the next prove-<n>)")
    p.add_argument("--runs", default=None)
    p.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else prove(a)


if __name__ == "__main__":
    sys.exit(main())
