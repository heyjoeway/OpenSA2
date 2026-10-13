"""tools/s8/vm_kit.py and its VM-side script tools/s8/vm_kit_run_check.py.

The kit's run settings: wine_run's environment with each host path as a token ({run}, {install},
{kit}), and a value that still names a host path refused. The installation check: what the
registered runs read, with the Config files, the save folder and a Mod Loader's original left
out, each for its reason. The VM-side script end to end, under a stand-in Wine that writes a
capture and a run record: a good kit prints DOUBLE_CHECK_OK, seeds the overlay (the Config file,
the pinned save and a whiteout for the folder's extra save) and hands the run no SA2_* variable
of the shell; a bad capture prints DOUBLE_CHECK_FAIL with the first differing tick and column;
a damaged kit is refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s8"))
import vm_kit as VK  # noqa: E402
import vm_kit_run_check as RC  # noqa: E402

EMPTY = hashlib.sha256(b"").hexdigest()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_kit_env_turns_each_host_path_into_its_token():
    tdir = ROOT / "private" / "t" / "boot"
    env = {"SA2_VFS_WRITE_ROOT": "private/t/boot/vfs",
           "SA2_REPLAY_TICKS": "private/t/boot/lifted.csv",
           "SA2_INSTALL": "private/install",
           "SA2_DEVICE_PROFILE": "private/generated/oracle/captures/x/sa2-device-profile.txt",
           "SA2_INPUT_SCRIPT": str(ROOT / "private" / "generated" / "oracle" / "in.csv"),
           "SA2_GUEST_CWD": "Z:\\home\\user\\OpenSA2\\private\\generated\\oracle\\game",
           "SA2_GUEST_CMDLINE": "--level metalharbor --character sonic",
           "SA2_PUMP_POLLS": "1300"}
    copies = {"SA2_DEVICE_PROFILE": "replays/boot/sa2-device-profile.txt",
              "SA2_INPUT_SCRIPT": "replays/boot/sa2-input.csv"}
    assert VK.kit_env(env, tdir, copies) == {
        "SA2_VFS_WRITE_ROOT": "{run}\\vfs",
        "SA2_REPLAY_TICKS": "{run}\\lifted.csv",
        "SA2_INSTALL": "{install}",
        "SA2_DEVICE_PROFILE": "{kit}\\replays\\boot\\sa2-device-profile.txt",
        "SA2_INPUT_SCRIPT": "{kit}\\replays\\boot\\sa2-input.csv",
        "SA2_GUEST_CWD": "Z:\\home\\user\\OpenSA2\\private\\generated\\oracle\\game",
        "SA2_GUEST_CMDLINE": "--level metalharbor --character sonic",
        "SA2_PUMP_POLLS": "1300"}
    assert VK.kit_args(["--timeout", "3129", "--dump-imports", "private/t/boot/imports.table"],
                       tdir) == ["--timeout", "3129", "--dump-imports", "{run}\\imports.table"]


def test_kit_env_refuses_a_host_path_it_does_not_carry():
    tdir = ROOT / "private" / "t" / "boot"
    with pytest.raises(VK.Refused, match="SA2_SOMETHING"):
        VK.kit_env({"SA2_SOMETHING": "private/generated/x.bin"}, tdir, {})
    with pytest.raises(VK.Refused, match="not under the template run folder"):
        VK.kit_env({"SA2_REPLAY_TICKS": "private/elsewhere/lifted.csv"}, tdir, {})


def res(rel, status="ok", layer=0):
    return {"rel": rel, "status": status, "layer": layer}


def test_install_spec_keeps_what_the_runs_read_from_the_installation(tmp_path):
    (tmp_path / "resource" / "gd_PC" / "DLL" / "Win32").mkdir(parents=True)
    rec = tmp_path / "run.runrec"
    rec.write_text(json.dumps({"files": {"resolutions": [
        res("Config"), res("Config/UserConfig.cfg"), res("Config/StageSelectConfig.ini",
                                                         "not-found", -1),
        res("", "ok", 1),
        res("resource/shader/win32/obj.pak"),
        res("resource/gd_PC/DLL/Win32"),
        res("resource/gd_PC/DLL/Win32/Data_DLL.dll"),
        res("resource/gd_PC/DLL/Win32/Data_DLL_orig.dll"),
        res("resource/gd_PC/prs/forMovie.pak", "not-found", -1),
        res("resource/gd_PC/SAVEDATA/SONIC2B__S01", "not-found", -1),
        res("resource/gd_PC/SAVEDATA/SONIC2B__S01", "ok", 1)]}}))
    table = {"id": "install-hashes-v1", "files": [
        {"path": "resource/Shader/win32/obj.pak", "sha256": "aa" * 32},
        {"path": "resource/gd_PC/DLL/Win32/Data_DLL.dll", "sha256": "bb" * 32},
        {"path": "resource/gd_PC/unread.bin", "sha256": "cc" * 32}]}
    save_state = {"directory": "resource/gd_PC/SAVEDATA", "files": {"SONIC2B__ALF": EMPTY}}
    spec = VK.install_spec([rec], table, save_state, tmp_path)
    assert spec["files"] == {"resource/Shader/win32/obj.pak": "aa" * 32,
                             "resource/gd_PC/DLL/Win32/Data_DLL.dll": "bb" * 32}
    assert spec["absent"] == ["Config/StageSelectConfig.ini", "resource/gd_PC/prs/forMovie.pak"]
    assert spec["save_dir"] == "resource/gd_PC/SAVEDATA"
    assert spec["saves"] == {"SONIC2B__ALF": EMPTY}


def test_install_spec_refuses_a_read_file_no_table_holds(tmp_path):
    rec = tmp_path / "run.runrec"
    rec.write_text(json.dumps({"files": {"resolutions": [res("resource/gd_PC/mod.pak")]}}))
    save_state = {"directory": "resource/gd_PC/SAVEDATA", "files": {}}
    with pytest.raises(VK.Refused, match="resource/gd_PC/mod.pak"):
        VK.install_spec([rec], {"id": "t", "files": []}, save_state, tmp_path)
    rec.write_text(json.dumps({"files": {"resolutions": [res("Config/Mods.ini")]}}))
    with pytest.raises(VK.Refused, match="Config/Mods.ini"):
        VK.install_spec([rec], {"id": "t", "files": []}, save_state, tmp_path)


def test_wine_host_maps_c_and_nothing_else(tmp_path):
    h = RC.Host("/x/wine", str(tmp_path))
    assert h.local(r"C:\sa2-runs\double-check-1\boot") == str(
        tmp_path / "drive_c" / "sa2-runs" / "double-check-1" / "boot")
    assert h.windows(str(tmp_path / "drive_c" / "sa2-kit")) == r"C:\sa2-kit"
    with pytest.raises(RC.Refused):
        h.local(r"Z:\home\user")
    native = RC.Host()
    assert native.local(r"C:\sa2-kit") == r"C:\sa2-kit"


def test_clean_arg_takes_what_cmd_hands_a_quoted_folder():
    assert RC.clean_arg('C:\\Program Files (x86)\\Steam"') == "C:\\Program Files (x86)\\Steam"
    assert RC.clean_arg("C:\\Program Files (x86)\\Steam\\") == "C:\\Program Files (x86)\\Steam"
    assert RC.clean_arg("Z:\\") == "Z:\\"


def test_first_difference_names_the_tick_and_the_columns(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("tick,x,y,z\n100,1,2,3\n101,4,5,6\n102,7,8,9\n")
    b.write_text("tick,x,y,z\n100,1,2,3\n101,4,0,7\n102,7,8,9\n")
    assert RC.first_difference(str(a), str(b)) == {
        "row": 2, "tick": "101", "columns": ["y", "z"],
        "values": [{"column": "y", "registered": "5", "this_run": "0"},
                   {"column": "z", "registered": "6", "this_run": "7"}],
        "columns_differing": 2}
    b.write_text("tick,x,y,z\n100,1,2,3\n101,4,5,6\n")
    assert RC.first_difference(str(a), str(b)) == {
        "row": 3, "tick": "102", "columns": ["run length"], "ended": "this run",
        "columns_differing": 0}
    assert RC.first_difference(str(a), str(a)) is None


# A stand-in for Wine: runs no program, writes what a replay run writes, from the environment
# it was handed. The "binary" file's text picks the capture.
FAKE_WINE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
from pathlib import PureWindowsPath
prefix = os.environ["WINEPREFIX"]
def local(w):
    p = PureWindowsPath(w)
    assert p.drive.upper() == "C:", w
    return os.path.join(prefix, "drive_c", *p.parts[1:])
exe, args = sys.argv[1], sys.argv[2:]
mode = open(local(exe)).read().strip()
last = "2,5,6" if mode == "good" else "2,5,7"
with open(local(os.environ["SA2_REPLAY_TICKS"]), "w", newline="") as f:
    f.write("tick,a,b\n1,0,0\n" + last + "\n")
vfs = local(os.environ["SA2_VFS_WRITE_ROOT"])
seeded = sorted(os.path.relpath(os.path.join(d, n), vfs).replace(os.sep, "/")
                for d, _, ns in os.walk(vfs) for n in ns)
prof = os.environ["SA2_DEVICE_PROFILE"]
rec = {"device_profile": {"default": False, "source": prof,
                          "sha256": hashlib.sha256(open(local(prof), "rb").read()).hexdigest()},
       "virtual": {"cwd": os.environ["SA2_GUEST_CWD"], "exe_path": os.environ["SA2_GUEST_EXE_PATH"]},
       "d3d9": {"device_enabled": True, "frames": {"present": 2}},
       "exe": {"kind": os.environ.get("SA2_EXE_KIND", "unpacked"), "path": args[0]},
       "pump": {"iterations": 2, "quit_by_host": True, "poll_limit": 2},
       "seen_stray": "SA2_STRAY" in os.environ, "install": os.environ["SA2_INSTALL"],
       "overlay": seeded, "cwd": os.getcwd()}
with open(local(args[args.index("--json") + 1]), "w") as f:
    json.dump(rec, f)
'''

GOOD_ROWS = b"tick,a,b\n1,0,0\n2,5,6\n"
GAME = ("Program Files (x86)", "Steam", "steamapps", "common", "Sonic Adventure 2")


def make_kit(prefix: Path, mode: str) -> Path:
    kit = prefix / "drive_c" / "sa2-kit"
    files = {"bin/boot/sa2_main.exe": mode.encode() + b"\n",
             "replays/boot/sa2-device-profile.txt": b"profile\n",
             "replays/boot/registered-lifted.csv": GOOD_ROWS,
             "overlay/Config/UserConfig.cfg": b"cfg\n",
             "overlay/resource/gd_PC/SAVEDATA/SONIC2B__ALF": b""}
    for rel, data in files.items():
        (kit / rel).parent.mkdir(parents=True, exist_ok=True)
        (kit / rel).write_bytes(data)
    shutil.copyfile(ROOT / "tools" / "s8" / "vm_kit_run_check.py", kit / "run_check.py")
    run = {"name": "boot", "replay": "boot", "binary": "bin/boot/sa2_main.exe",
           "exe_kind": "unpacked", "timeout": 60, "wine_seconds": 1.0,
           "argv": ["{kit}\\bin\\boot\\sa2_main.exe", "{kit}\\game\\x.exe", "--json",
                    "{run}\\run.runrec"],
           "env": {"SA2_INSTALL": "{install}", "SA2_VFS_WRITE_ROOT": "{run}\\vfs",
                   "SA2_REPLAY_TICKS": "{run}\\lifted.csv",
                   "SA2_DEVICE_PROFILE": "{kit}\\replays\\boot\\sa2-device-profile.txt",
                   "SA2_GUEST_CWD": "Z:\\g", "SA2_GUEST_EXE_PATH": "Z:\\g\\s.exe",
                   "SA2_D3D9_DEVICE": "1"},
           "expect": {"capture_sha256": sha(GOOD_ROWS), "capture_ticks": 2,
                      "registered_capture": "replays/boot/registered-lifted.csv",
                      "outputs": {}, "device_profile_sha256": sha(b"profile\n"),
                      "guest": {"cwd": "Z:\\g", "exe_path": "Z:\\g\\s.exe"},
                      "switches": [["SA2_D3D9_DEVICE", ["d3d9", "device_enabled"]]]}}
    doc = {"schema": RC.KIT_SCHEMA, "id": "test-kit", "built": "now", "commit": "0" * 40,
           "question": "q", "overlay_seed": "overlay", "runs": [run],
           "files": {rel: {"bytes": len(d), "sha256": sha(d)} for rel, d in files.items()},
           "install": {"files": {"resource/gd_PC/a.bin": sha(b"asset")},
                       "absent": ["resource/gd_PC/missing.pak"],
                       "save_dir": "resource/gd_PC/SAVEDATA", "saves": {"SONIC2B__ALF": EMPTY},
                       "shipped_exe_sha256": sha(b"exe")}}
    (kit / "kit.json").write_text(json.dumps(doc))
    game = prefix / "drive_c" / Path(*GAME)
    (game / "resource" / "gd_PC" / "SAVEDATA").mkdir(parents=True, exist_ok=True)
    (game / "sonic2app.exe").write_bytes(b"exe")
    (game / "resource" / "gd_PC" / "A.BIN").write_bytes(b"asset")
    (game / "resource" / "gd_PC" / "SAVEDATA" / "SONIC2B__S01").write_bytes(b"played")
    (prefix / "drive_c" / "windows" / "system32").mkdir(parents=True, exist_ok=True)
    (prefix / "drive_c" / "windows" / "system32" / "ucrtbase.dll").write_bytes(b"ucrt")
    wine = prefix.parent / "fakewine" / "wine"
    wine.parent.mkdir(exist_ok=True)
    wine.write_text(FAKE_WINE)
    (wine.parent / "wineserver").write_text("#!/bin/sh\nexit 0\n")
    for p in (wine, wine.parent / "wineserver"):
        p.chmod(0o755)
    return kit


def run_check(prefix: Path, kit: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, SA2_STRAY="1")
    return subprocess.run([sys.executable, str(kit / "run_check.py"), "--steam",
                           r"C:\Program Files (x86)\Steam", "--wine",
                           str(prefix.parent / "fakewine" / "wine"), "--wine-prefix", str(prefix)],
                          env=env, capture_output=True, text=True, timeout=120)


def test_run_check_end_to_end_ok_then_fail(tmp_path):
    prefix = tmp_path / "prefix"
    kit = make_kit(prefix, "good")
    p = run_check(prefix, kit)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "DOUBLE_CHECK_OK boot" in p.stdout
    scratch = prefix / "drive_c" / "sa2-runs" / "double-check-1"
    s = json.loads((scratch / "summary.json").read_text())
    assert s["verdict"] == "DOUBLE_CHECK_OK" and s["install_ok"] is True
    assert s["install"]["saves_hidden"] == ["SONIC2B__S01"]
    assert s["host"]["ucrtbase"]["sha256"] == sha(b"ucrt")     # found case-blind
    assert s["runs"][0]["capture"]["equal"] is True and s["runs"][0]["problems"] == []
    rec = json.loads((scratch / "boot" / "run.runrec").read_text())
    assert rec["overlay"] == ["Config/UserConfig.cfg",
                              "resource/gd_PC/SAVEDATA/.sa2-whiteout.SONIC2B__S01",
                              "resource/gd_PC/SAVEDATA/SONIC2B__ALF"]
    assert rec["seen_stray"] is False
    assert rec["install"] == "C:\\" + "\\".join(GAME)
    assert rec["cwd"] == str(scratch / "boot")
    with zipfile.ZipFile(scratch / "double-check-1-records.zip") as z:
        assert sorted(z.namelist()) == ["boot/run.runrec", "boot/stderr.txt", "boot/stdout.txt",
                                        "summary.json"]

    kit = make_kit(prefix, "bad")             # the same kit folder, rewritten
    p = run_check(prefix, kit)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "DOUBLE_CHECK_FAIL boot" in p.stdout
    assert "first difference at tick 2 (row 2), columns b" in p.stdout
    scratch = prefix / "drive_c" / "sa2-runs" / "double-check-2"
    s = json.loads((scratch / "summary.json").read_text())
    assert s["runs"][0]["capture"]["first_difference"] == {
        "row": 2, "tick": "2", "columns": ["b"],
        "values": [{"column": "b", "registered": "6", "this_run": "7"}],
        "columns_differing": 1}
    with zipfile.ZipFile(scratch / "double-check-2-records.zip") as z:
        assert "boot/lifted.csv" in z.namelist()


def test_run_check_refuses_a_damaged_kit(tmp_path):
    prefix = tmp_path / "prefix"
    kit = make_kit(prefix, "good")
    (kit / "replays" / "boot" / "registered-lifted.csv").write_bytes(b"tick\n")
    p = run_check(prefix, kit)
    assert p.returncode == 2
    assert "DOUBLE_CHECK_REFUSED the kit is damaged" in p.stdout
    assert "replays/boot/registered-lifted.csv" in p.stdout
    assert not (prefix / "drive_c" / "sa2-runs").exists()
