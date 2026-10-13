"""Every lifted replay starts from the pinned saves, as every native run does.

The user ruled on 2026-09-24 that the save file is a recorded input, restored
before every native and lifted replay. The lifted build reads its saves
through the write overlay first and the installation second
(runtime/sa2_files.c), so a save one lifted run wrote would be found by the
next: the replay of the user's first recording left SONIC2B__S01 there. Each
of the three replay runners -- the stage replay (tools/s4/stage_run.py), the
boot replay's lifted run (tools/s4/tick_replay.py run_lifted) and the attract
demo (tools/s4/demo_run.py) -- and the reach run the boot replay's hooked run is
held to (tools/s4/reach_receipt.py run) clears the overlay's save directory
before it starts the guest, refuses to start when the installation is not the
pin, and keeps what it cleared in its record (tools/oracle/save_state.py
reset_lifted, whose own tests are tools/oracle/test_save_state.py). The stage replay, the
attract demo and the boot replay (tools/s4/tick_replay.py native_start) also
refuse a native capture whose receipt does not show it started from the same
pin (save_state.native_start_problems). The reset also records what the
overlay still holds (`left`, tested here with the runners that read it), and
the reach run and the boot replay's hooked run refuse to start unless that is
nothing (save_state.require_empty).

No guest runs here: each runner's subprocess is replaced by one that records
what the overlay held when the guest would have started. Every tree is under
pytest's tmp_path; the tracked pin is copied there and read, never written.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.append(str(ROOT / "tools" / "oracle"))
import device_profile as DP  # noqa: E402
import save_state as SS  # noqa: E402

SAVE = Path("private/generated/s4/vfsroot/resource/gd_PC/SAVEDATA/SONIC2B__S01")


def pinned_tree(root: Path) -> None:
    """The tracked pin under `root`, and an installation at root/private/install
    holding its saves, which are empty files (Documentation/save-state.json),
    so every file can be made from its hash."""
    pin = SS.load()
    (root / "Documentation").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SS.PIN, root / "Documentation" / "save-state.json")
    saves = root / "private" / "install" / "resource" / "gd_PC" / "SAVEDATA"
    saves.mkdir(parents=True, exist_ok=True)
    for name, sha in pin.files.items():
        assert sha == SS.EMPTY, "the tracked pin holds a save with bytes; copy it here instead"
        (saves / name).write_bytes(b"")


def left_save(root: Path) -> None:
    """What the lifted replay of the user's first recording left behind."""
    (root / SAVE).parent.mkdir(parents=True)
    (root / SAVE).write_bytes(b"")


def unpin(root: Path) -> None:
    """The installation with a save the pin does not have."""
    (root / "private/install/resource/gd_PC/SAVEDATA/SONIC2B__S01").write_bytes(b"")


CLEARED = {"resource/gd_PC/SAVEDATA/SONIC2B__S01": SS.EMPTY, "resource/gd_PC/SAVEDATA": "<dir>",
           "resource/gd_PC": "<dir>", "resource": "<dir>"}
# What a run record says of its devices when every switch the reach
# configuration turns on took effect (tools/s4/reach_receipt.py SWITCH_RECORD):
# the reach run and the boot replay's hooked run refuse a record without it.
ON_BLOCKS = {"d3d9": {"device_enabled": True, "constant_tables": {"switch": True}},
             "dsound": {"device_enabled": True}, "dinput": {"device_enabled": True},
             "xaudio2": {"device_enabled": True}, "ddraw": {"device_enabled": True}}


def devices_on(record: dict) -> dict:
    """`record` with every device block ON_BLOCKS names merged into its own, and,
    unless it names one, the directory its device profile block stands for in
    its `virtual` block (what harness/crt_probe.c prints): the runtime's
    placeholders for the default, the made-up captures' (test_device_profile
    GUEST) for any other."""
    from test_device_profile import GUEST
    out = json.loads(json.dumps(record))
    for k, v in ON_BLOCKS.items():
        out[k] = {**json.loads(json.dumps(v)), **(out.get(k) or {})}
    block = out.get("device_profile")
    if "virtual" not in out and isinstance(block, dict):
        out["virtual"] = dict(DP.DEFAULT_GUEST if block.get("default") else GUEST)
    return out


def at_start(root: Path, seen: list):
    """A subprocess.run that records whether the overlay still held the save
    when the guest would have started, and does nothing else."""
    def run(cmd, **kw):
        seen.append((root / SAVE).exists() or (root / SAVE).parent.exists())
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


# ------------------------------------------------------------- the stage replay

def no_device_vars(monkeypatch) -> None:
    """No profile, host-device override, device switch or break from the shell:
    the runners refuse one (tools/s4/reach_receipt.py DEVICE_VARS)."""
    import reach_receipt as RR
    for var in RR.DEVICE_VARS:
        monkeypatch.delenv(var, raising=False)


def stage(tmp_path: Path, monkeypatch):
    import stage_run as SR
    no_device_vars(monkeypatch)
    monkeypatch.setattr(SR, "ROOT", tmp_path)
    monkeypatch.setattr(SR, "OUT", tmp_path / "replay")
    monkeypatch.setattr(SR, "RUNDIR", tmp_path / "run")
    pinned_tree(tmp_path)
    script = tmp_path / "synth" / "sa2-input.csv"
    script.parent.mkdir(parents=True)
    script.write_text("frame\n")
    monkeypatch.setattr(sys, "argv", ["stage_run.py", "--no-native", "--input", str(script)])
    return SR


def test_the_stage_replay_clears_the_overlay_s_saves_first_and_keeps_what_it_cleared(
        tmp_path, monkeypatch):
    SR = stage(tmp_path, monkeypatch)
    left_save(tmp_path)
    seen = []
    monkeypatch.setattr(SR.subprocess, "run", at_start(tmp_path, seen))
    assert SR.main() == 1                            # the fake run wrote no record
    assert seen == [False]
    saves = json.loads((tmp_path / "replay" / "stage-metalharbor-input" / "stage-run.json")
                       .read_text())["saves"]
    assert saves == {"pin": SS.load().record(), "installation": "private/install",
                     "installation_matches_pin": True, "overlay": "private/generated/s4/vfsroot",
                     "removed": CLEARED, "left": {}}


def test_the_stage_replay_does_not_start_off_the_pinned_saves(tmp_path, monkeypatch, capsys):
    SR = stage(tmp_path, monkeypatch)
    left_save(tmp_path)
    unpin(tmp_path)
    monkeypatch.setattr(SR.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    assert SR.main() == 1
    out = capsys.readouterr().out
    assert out.startswith("STAGE_RUN_REFUSED the saves in ")
    assert "extra SONIC2B__S01 (e3b0c44298fc)" in out and SS.RESTORE in out
    assert (tmp_path / SAVE).is_file()               # nothing cleared before the refusal


def test_the_stage_replay_refuses_a_capture_that_started_from_other_saves(tmp_path,
                                                                          monkeypatch):
    """The lifted run starts from the pin, so the native capture it stands for
    must have too, by its receipt (the save-state review of 2026-09-24, N5)."""
    SR = stage(tmp_path, monkeypatch)
    monkeypatch.setattr(SR.TR, "native_vanilla_problems", lambda native, root=None: [])
    monkeypatch.setattr(SR.DP, "capture_profile", lambda native, root: DP.DEFAULT)
    native = tmp_path / "captures" / "rec"
    native.mkdir(parents=True)
    started = {"SONIC2B__ALF": SS.EMPTY, "SONIC2B__S01": SS.EMPTY}
    (native / "receipt.json").write_text(json.dumps({"session": {"saves": {"at_start": started}}}))
    profile, problems = SR.native_side(native)
    assert profile is None
    assert problems == [f"captures/rec started from saves {SS.digest(started)[:12]}, and a "
                        f"lifted replay of it starts from the pin {SS.load().digest[:12]} "
                        f"(Documentation/save-state.json, {SS.load().state})"]
    started.pop("SONIC2B__S01")
    (native / "receipt.json").write_text(json.dumps({"session": {"saves": {"at_start": started}}}))
    assert SR.native_side(native) == (DP.DEFAULT, [])


# ------------------------------------------------------- the boot replay's run

def tick(tmp_path: Path, monkeypatch):
    import tick_replay as T
    no_device_vars(monkeypatch)
    monkeypatch.setattr(T, "ROOT", tmp_path)
    monkeypatch.setattr(T, "OUT", tmp_path / "replay")
    monkeypatch.setattr(T, "RUNDIR", tmp_path / "run")
    pinned_tree(tmp_path)
    return T


def test_the_boot_replay_s_lifted_run_clears_the_overlay_s_saves_first(tmp_path, monkeypatch):
    """The whole path: the run's record, matrix and tick capture written by a
    fake guest, and the result carrying what was cleared."""
    T = tick(tmp_path, monkeypatch)
    left_save(tmp_path)
    seen = []

    def run(cmd, **kw):
        seen.append((tmp_path / SAVE).parent.exists())
        prefix = cmd[cmd.index("--out-prefix") + 1]
        (tmp_path / "run").mkdir(parents=True, exist_ok=True)
        (tmp_path / "run" / f"{prefix}.ticks.json").write_text(json.dumps(devices_on(
            {"device_profile": {"default": True}, "pump": {"iterations": 3},
             "calls": {"total": 9}, "d3d9": {"frames": {"present": 2}}, "trap": {"note": "t"}})))
        (tmp_path / "run" / f"{prefix}.matrix.json").write_text(json.dumps({"sha256": "b" * 64}))
        (tmp_path / "replay" / "boot" / "lifted.csv").write_text("tick,a\n1,0x1\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(T.subprocess, "run", run)
    lifted = T.run_lifted("boot", device_profile=DP.DEFAULT)
    assert seen == [False]
    assert lifted["saves"]["removed"] == CLEARED
    assert lifted["saves"]["pin"] == SS.load().record()
    assert lifted["capture"] == "replay/boot/lifted.csv"


def test_the_boot_replay_s_lifted_run_does_not_start_off_the_pinned_saves(tmp_path,
                                                                          monkeypatch):
    T = tick(tmp_path, monkeypatch)
    unpin(tmp_path)
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit) as e:
        T.run_lifted("boot", device_profile=DP.DEFAULT)
    assert str(e.value.code).startswith("REPLAY_REFUSED the saves in ")


def test_the_pin_and_its_tool_are_what_a_lifted_capture_depends_on():
    """A lifted capture taken from other saves is stale, as one taken on
    another device profile is."""
    import tick_replay as T
    assert {"Documentation/save-state.json", "tools/oracle/save_state.py"} <= set(
        T.LIFTED_PIPELINE)


# ------------------------------------------------------------ the attract demo

def demo(tmp_path: Path, monkeypatch):
    import demo_run as D
    no_device_vars(monkeypatch)
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(D, "OUT", tmp_path / "replay" / "attract-demo")
    monkeypatch.setattr(D.SR, "RUNDIR", tmp_path / "run")
    monkeypatch.setattr(D, "native_side", lambda native: (DP.DEFAULT, []))
    monkeypatch.setattr(sys, "argv", ["demo_run.py", "--native", str(tmp_path / "native")])
    pinned_tree(tmp_path)
    return D


def test_the_attract_demo_clears_the_overlay_s_saves_first_and_keeps_what_it_cleared(
        tmp_path, monkeypatch):
    D = demo(tmp_path, monkeypatch)
    left_save(tmp_path)
    seen = []
    monkeypatch.setattr(D.subprocess, "run", at_start(tmp_path, seen))
    assert D.main() == 1                             # the fake run wrote no record
    assert seen == [False]
    saves = json.loads((tmp_path / "replay" / "attract-demo" / "demo-run.json")
                       .read_text())["saves"]
    assert saves["removed"] == CLEARED and saves["installation_matches_pin"] is True


def test_the_attract_demo_refuses_a_capture_that_started_from_other_saves(tmp_path,
                                                                          monkeypatch):
    import demo_run as D
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(D.OM, "vanilla_problems", lambda native, root=None: [])
    monkeypatch.setattr(D.DP, "capture_profile", lambda native, root: DP.DEFAULT)
    pinned_tree(tmp_path)
    native = tmp_path / "captures" / "demo"
    native.mkdir(parents=True)
    (native / "receipt.json").write_text(json.dumps({"session": {}}))
    profile, [problem] = D.native_side(native)
    assert profile is None
    assert problem.startswith("captures/demo's receipt records no start saves")
    (native / "receipt.json").write_text(json.dumps(
        {"session": {"saves": {"at_start": SS.load().files}}}))
    assert D.native_side(native) == (DP.DEFAULT, [])


def test_the_attract_demo_does_not_start_off_the_pinned_saves(tmp_path, monkeypatch, capsys):
    D = demo(tmp_path, monkeypatch)
    unpin(tmp_path)
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    assert D.main() == 1
    assert capsys.readouterr().out.startswith("DEMO_RUN_REFUSED the saves in ")


# ---------------------------------------------------------------- the reach run

def reach(tmp_path: Path, monkeypatch):
    import reach_receipt as RR
    no_device_vars(monkeypatch)
    monkeypatch.setitem(sys.modules, "rebuild_image",
                        types.SimpleNamespace(staged_not_rebuilt=lambda: [],
                                              fold_code_problems=lambda: []))
    monkeypatch.setattr(RR.C, "ROOT", tmp_path)
    monkeypatch.setattr(RR.C, "GEN", tmp_path / "private" / "generated")
    pinned_tree(tmp_path)
    return RR


def test_the_reach_run_clears_the_overlay_s_saves_first_and_keeps_what_it_cleared(
        tmp_path, monkeypatch):
    """The boot replay's hooked run is held to the reach receipt field for field
    (tools/s4/tick_replay.py non_perturbation) and starts from the pin, so the
    reach run does too: a save directory left in the overlay moved the reach's
    import calls on 2026-09-24, because the runtime lists the overlay when the
    game searches a directory the overlay holds."""
    RR = reach(tmp_path, monkeypatch)
    left_save(tmp_path)
    seen = []

    def run(cmd, **kw):
        seen.append((tmp_path / SAVE).parent.exists())
        name = cmd[cmd.index("--name") + 1]
        out = tmp_path / "private" / "generated" / "s4" / "run"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.a.json").write_text(json.dumps(devices_on(
            {"device_profile": {"default": True, "source": "default", "sha256": ""},
             "pump": {"iterations": 3, "driven": False}, "calls": {"total": 9},
             "d3d9": {"frames": {"present": 2}}, "trap": {"note": "t"}})))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(RR.subprocess, "run", run)
    got = RR.run("t")
    assert seen == [False]
    assert got["saves"] == {"pin": SS.load().record(), "installation": "private/install",
                            "installation_matches_pin": True,
                            "overlay": "private/generated/s4/vfsroot", "removed": CLEARED,
                            "left": {}}


def test_the_reach_run_does_not_start_off_the_pinned_saves(tmp_path, monkeypatch):
    RR = reach(tmp_path, monkeypatch)
    left_save(tmp_path)
    unpin(tmp_path)
    monkeypatch.setattr(RR.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit) as e:
        RR.run("t")
    assert str(e.value.code).startswith("REACH_FAILED the saves in ")
    assert (tmp_path / SAVE).is_file()               # nothing cleared before the refusal


def test_the_pin_and_its_tool_are_in_the_reach_manifest():
    """A reach receipt measured from other saves is stale, as one measured with
    another runtime is (Documentation/scripts/check_run_reach.py)."""
    import reach_receipt as RR
    assert {"Documentation/save-state.json", "tools/oracle/save_state.py"} <= set(RR.TOOLS)


# ------------------------------------------------------ P6's review fold
#
# W3: the guest reads the overlay first and lists it when it searches a
# directory the overlay holds -- the game's own directory always -- so what the
# overlay holds beside its saves is part of every lifted run's input. The reset
# records it (`left`), and the two runs compared field for field, the reach run
# and the boot replay's hooked run, refuse to start unless it is nothing.
# W1: the boot replay holds its capture and every repeat to the pinned saves,
# as the stage replay and the attract demo hold theirs. N5: the attract demo
# fails when its run did not read the profile it was handed.

KEPT = Path("private/generated/s4/vfsroot/Config/UserConfig.cfg")


def left_beside(root: Path) -> None:
    """A save and a file beside it, as tools/s4/vfs_overlay.py leaves one."""
    left_save(root)
    (root / KEPT).parent.mkdir(parents=True)
    (root / KEPT).write_bytes(b"cfg")


LEFT = {"Config": "<dir>", "Config/UserConfig.cfg": hashlib.sha256(b"cfg").hexdigest()}


def test_the_reset_records_what_the_overlay_still_holds(tmp_path):
    pinned_tree(tmp_path)
    left_beside(tmp_path)
    cmd = ["loop_run.py", "--run", "a|SA2_PUMP_POLLS=1"]
    record = SS.reset_lifted(cmd, tmp_path)
    assert record["removed"] == CLEARED
    assert record["left"] == LEFT
    with pytest.raises(SS.SaveStateError, match=r"the write overlay private/generated/s4/vfsroot "
                                                r"still holds Config, Config/UserConfig\.cfg"):
        SS.require_empty(record)
    (tmp_path / KEPT).unlink()
    (tmp_path / KEPT).parent.rmdir()
    assert SS.reset_lifted(cmd, tmp_path)["left"] == {}
    SS.require_empty(SS.reset_lifted(cmd, tmp_path))
    with pytest.raises(SS.SaveStateError, match="recorded nothing about what the write overlay"):
        SS.require_empty({"removed": {}})


def test_the_reach_run_refuses_an_overlay_that_holds_anything_but_its_saves(tmp_path,
                                                                           monkeypatch):
    RR = reach(tmp_path, monkeypatch)
    left_beside(tmp_path)
    monkeypatch.setattr(RR.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit) as e:
        RR.run("t")
    assert str(e.value.code).startswith("REACH_FAILED the write overlay private/generated/s4/"
                                        "vfsroot still holds Config, Config/UserConfig.cfg")
    assert not (tmp_path / SAVE).exists() and (tmp_path / KEPT).is_file()


def test_the_boot_replay_s_lifted_run_refuses_an_overlay_that_holds_anything_but_its_saves(
        tmp_path, monkeypatch):
    T = tick(tmp_path, monkeypatch)
    left_beside(tmp_path)
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit) as e:
        T.run_lifted("boot", device_profile=DP.DEFAULT)
    assert str(e.value.code).startswith("REPLAY_REFUSED the write overlay private/generated/s4/"
                                        "vfsroot still holds Config")


def test_the_stage_replay_records_what_the_overlay_held_and_still_runs(tmp_path, monkeypatch):
    """A stage replay may be run with a file put in the overlay on purpose
    (tools/s4/vfs_overlay.py); it records it rather than refusing."""
    SR = stage(tmp_path, monkeypatch)
    left_beside(tmp_path)
    seen = []
    monkeypatch.setattr(SR.subprocess, "run", at_start(tmp_path, seen))
    assert SR.main() == 1                            # the fake run wrote no record
    assert seen == [False]
    saves = json.loads((tmp_path / "replay" / "stage-metalharbor-input" / "stage-run.json")
                       .read_text())["saves"]
    assert saves["left"] == LEFT


def boot_capture(root: Path, name: str, at_start) -> dict:
    d = root / "private" / "captures" / name
    d.mkdir(parents=True)
    session = {} if at_start is None else {"saves": {"at_start": at_start}}
    (d / "receipt.json").write_text(json.dumps({"session": session}))
    return {"capture": f"private/captures/{name}/t.csv",
            "receipt": f"private/captures/{name}/receipt.json"}


def boot_entry(root: Path, repeat_start) -> dict:
    pin = SS.load().files
    native = dict(boot_capture(root, "a", pin), repeats=[boot_capture(root, "b", repeat_start)])
    return {"name": "boot", "native": native}


def test_a_boot_capture_and_repeat_that_started_from_the_pin_are_accepted(tmp_path):
    import tick_replay as T
    pinned_tree(tmp_path)
    assert T.native_start(boot_entry(tmp_path, SS.load().files), tmp_path) is None


@pytest.mark.parametrize("start,why", [
    (None, "records no start saves"),
    ({"SONIC2B__S01": SS.EMPTY}, "started from saves"),
])
def test_a_boot_repeat_that_did_not_start_from_the_pin_is_refused(tmp_path, start, why):
    import tick_replay as T
    pinned_tree(tmp_path)
    with pytest.raises(SystemExit, match=why):
        T.native_start(boot_entry(tmp_path, start), tmp_path)


def test_a_boot_capture_without_its_receipt_is_refused_not_passed(tmp_path):
    """native_start_problems leaves a missing receipt to its caller's check;
    native_start is that check, so it refuses one rather than pass it."""
    import tick_replay as T
    pinned_tree(tmp_path)
    entry = boot_entry(tmp_path, SS.load().files)
    (tmp_path / entry["native"]["repeats"][0]["receipt"]).unlink()
    with pytest.raises(SystemExit, match="has no oracle receipt"):
        T.native_start(entry, tmp_path)
    entry["native"].pop("receipt")
    with pytest.raises(SystemExit, match="has no oracle receipt"):
        T.native_start(entry, tmp_path)


def test_the_attract_demo_fails_a_run_that_did_not_read_its_profile(tmp_path, monkeypatch,
                                                                    capsys):
    D = demo(tmp_path, monkeypatch)
    from test_device_profile import GUEST
    monkeypatch.setattr(D, "native_side", lambda native: (
        {"path": "private/captures/c/sa2-device-profile.txt", "sha256": "a" * 64,
         "counts": {}, "guest": GUEST}, []))

    def run(cmd, **kw):
        name = cmd[cmd.index("--name") + 1]
        (tmp_path / "run").mkdir(parents=True, exist_ok=True)
        (tmp_path / "run" / f"{name}.a.json").write_text(json.dumps(
            {"device_profile": {"default": True, "source": "default", "sha256": ""}}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(D.subprocess, "run", run)
    assert D.main() == 1
    assert "DEMO_RUN_REFUSED the run did not read the device profile it was handed" in (
        capsys.readouterr().out)


def test_the_boot_replay_asks_it_before_anything_runs(tmp_path, monkeypatch):
    """replay() holds the registered captures to the pin before it reads a
    tick, builds or runs: removing the call passed every other test."""
    T = tick(tmp_path, monkeypatch)
    entry = boot_entry(tmp_path, {"SONIC2B__S01": SS.EMPTY})
    cap = tmp_path / entry["native"]["capture"]
    cap.write_text("tick,x\n1,0x0\n")
    entry["native"]["sha256"] = T.sha256(cap)
    monkeypatch.setattr(T, "native_probe_current", lambda e: {})
    monkeypatch.setattr(T, "native_oracle", lambda e: "vanilla")
    monkeypatch.setattr(T, "read_capture", lambda p: pytest.fail("nothing may be read"))
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit, match=r"REPLAY_REFUSED boot: private/captures/b started "
                                         r"from saves"):
        T.replay(entry, run=True)
