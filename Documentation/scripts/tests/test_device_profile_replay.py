"""The replay tools' use of the device profile: the tick replay, the reach run,
the stage run, the attract-demo run and the snapshot run each take the
capture's profile, run with every device switch on from one definition
(tools/s4/reach_receipt.py DEVICES, in ENV; profile_env for what a profile
adds), hold the lifted run to it and refuse a run that read another.

Split from test_device_profile.py on 2026-09-23 so the readers and the capture
side could ship before the replay tools do (Documentation/scripts/ship_subset.py);
the helpers are that file's. Every device and every capture here is made up.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_device_profile import (CHECKED, COUNTS, DP, GOOD, GOOD_SHA, GUEST,  # noqa: E402
                                 REGISTERED, ROOT, at, capture, entry)
from test_lifted_saves import devices_on  # noqa: E402  a record whose devices were on

DEVICES = ("SA2_DINPUT_DEVICE", "SA2_XAUDIO2_DEVICE", "SA2_DDRAW_DEVICE",
           "SA2_D3DX_CONSTANT_TABLE")
BREAKS = ("SA2_DINPUT_BREAK", "SA2_XAUDIO2_BREAK", "SA2_DDRAW_BREAK", "SA2_D3D9_BREAK",
          "SA2_DSOUND_BREAK", "SA2_CTYPE_BREAK", "SA2_LOCALE_BREAK", "SA2_ENV_BREAK",
          "SA2_STDIO_BREAK", "SA2_TIME_BREAK")
# The host keyboard's sources and log (runtime/sa2_dinput.h): tools/s5/play_live.py's only.
HOST_INPUT = ("SA2_INPUT_DI_SCRIPT", "SA2_INPUT_DI_SOURCE", "SA2_INPUT_DI_LOG")
ON = [f"{v}=1" for v in DEVICES]
DEFAULT_BLOCK = {"default": True, "source": "default", "sha256": ""}


def devices_in(spec: list[str]) -> list[str]:
    return [kv for kv in spec if kv.split("=")[0] in DEVICES]


@pytest.fixture(autouse=True)
def no_real_run(monkeypatch):
    """Nothing here may build or run. A test that gets past a refusal it expects
    (the refusal regressed) fails here instead of starting loop_run.py; the
    tests that read a command lay their own stand-in over this one."""
    def refuse(cmd, **kw):
        raise AssertionError(f"a test reached a real run: {cmd[:3]}")
    monkeypatch.setattr(subprocess, "run", refuse)


def lifted_paths(tmp_path: Path, monkeypatch):
    """tools/s4/tick_replay.py with its run directories under tmp_path, so a
    lifted run that got past its refusal writes nothing outside the test, and
    the pinned saves it starts from there (test_lifted_saves.pinned_tree)."""
    import tick_replay as T
    from test_lifted_saves import pinned_tree
    monkeypatch.setattr(T, "ROOT", tmp_path)
    monkeypatch.setattr(T, "OUT", tmp_path / "replay")
    monkeypatch.setattr(T, "RUNDIR", tmp_path / "run")
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    pinned_tree(tmp_path)
    return T


def reach_paths(tmp_path: Path, monkeypatch):
    """tools/s4/reach_receipt.py with its build checks stubbed (the restage and
    the fold-code stamp) and its run directories under tmp_path, for the same
    reason, and the pinned saves the run starts from there."""
    import reach_receipt as RR
    from test_lifted_saves import pinned_tree
    monkeypatch.setitem(sys.modules, "rebuild_image",
                        types.SimpleNamespace(staged_not_rebuilt=lambda: [],
                                              fold_code_problems=lambda: []))
    monkeypatch.setattr(RR.C, "ROOT", tmp_path)
    monkeypatch.setattr(RR.C, "GEN", tmp_path / "gen")
    (tmp_path / "gen" / "s4" / "run").mkdir(parents=True, exist_ok=True)
    pinned_tree(tmp_path)
    return RR


def reach_receipt(tmp_path: Path, profile, guest=None) -> Path:
    p = tmp_path / "reach.json"
    reach = {"iterations": 369, "import_calls": 165551, "frames_presented": 381, "stop": "s"}
    if profile is not None:
        reach["device_profile"] = profile
    if guest is not None:
        reach["guest_paths"] = guest
    p.write_text(json.dumps({"reach": reach}))
    return p


def test_the_tick_replay_compares_the_device_profile_before_the_reach(tmp_path):
    import tick_replay as T
    lifted = {"device_profile": REGISTERED,
              "reach": {"iterations": 1, "import_calls": 2, "frames_presented": 3, "stop": "x"}}
    with pytest.raises(SystemExit) as e:
        T.non_perturbation(lifted, reach_receipt(tmp_path, DP.DEFAULT), name="boot")
    assert str(e.value) == (
        "REPLAY_REFUSED the reach receipt was measured on device profile default and boot runs "
        f"on private/captures/c/sa2-device-profile.txt ({GOOD_SHA[:12]}), so the two are "
        "different runs before the hook is considered: re-run tools/s4/reach_receipt.py "
        "--replay boot --write")


def test_a_hooked_run_on_the_reach_s_profile_is_held_to_the_reach(tmp_path):
    import tick_replay as T
    same = {"device_profile": REGISTERED, "guest_paths": GUEST,
            "reach": {"iterations": 369, "import_calls": 165551, "frames_presented": 381,
                      "stop": "s"}}
    assert T.non_perturbation(same, reach_receipt(tmp_path, REGISTERED, GUEST))["equal"] is True
    moved = dict(same, reach=dict(same["reach"], import_calls=1))
    with pytest.raises(SystemExit, match="not the reach run"):
        T.non_perturbation(moved, reach_receipt(tmp_path, REGISTERED, GUEST))
    with pytest.raises(SystemExit, match="measured on device profile none recorded"):
        T.non_perturbation(same, reach_receipt(tmp_path, None, GUEST))


def test_a_hooked_run_is_held_to_a_reach_measured_in_its_directory(tmp_path):
    """The CRT parses the command line the runtime builds from the executable's
    path a character at a time, so the directory alone moves the import calls
    (708 in the boot, 2026-09-24): a reach measured in another directory, or
    before the directory was recorded, is another run."""
    import tick_replay as T
    same = {"device_profile": REGISTERED, "guest_paths": GUEST,
            "reach": {"iterations": 369, "import_calls": 165551, "frames_presented": 381,
                      "stop": "s"}}
    assert T.non_perturbation(same, reach_receipt(tmp_path, REGISTERED, GUEST))["equal"] is True
    with pytest.raises(SystemExit) as e:
        T.non_perturbation(same, reach_receipt(tmp_path, REGISTERED, DP.DEFAULT_GUEST),
                           name="boot")
    assert str(e.value).startswith(
        "REPLAY_REFUSED the reach receipt was measured in guest directory {'cwd': 'C:\\\\SA2'")
    assert str(e.value).endswith("re-run tools/s4/reach_receipt.py --replay boot --write")
    with pytest.raises(SystemExit, match="measured in guest directory None"):
        T.non_perturbation(same, reach_receipt(tmp_path, REGISTERED))
    for half in ("cwd", "exe_path"):           # each half alone (the fold review, W1)
        with pytest.raises(SystemExit, match="measured in guest directory"):
            T.non_perturbation(dict(same, guest_paths=dict(GUEST, **{half: "Z:\\x"})),
                               reach_receipt(tmp_path, REGISTERED, GUEST))
    older = {k: v for k, v in same.items() if k != "guest_paths"}
    with pytest.raises(SystemExit, match="runs in None"):
        T.non_perturbation(older, reach_receipt(tmp_path, REGISTERED, GUEST))
    # Neither side carrying a directory is refused too (the working-directory
    # review, W2(a)): the reach receipt is regenerated with it.
    with pytest.raises(SystemExit, match="measured in guest directory None"):
        T.non_perturbation(older, reach_receipt(tmp_path, REGISTERED))


def test_the_lifted_run_takes_its_directory_only_from_its_profile(tmp_path, monkeypatch):
    T = lifted_paths(tmp_path, monkeypatch)
    for var in ("SA2_GUEST_CWD", "SA2_GUEST_EXE_PATH"):
        with pytest.raises(SystemExit, match=rf"extra_env may not override .*{var}"):
            T.run_lifted("t", {var: "Z:\\x"}, device_profile=DP.DEFAULT)
    RR = reach_paths(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match=r"REACH_FAILED extra_env may not set "
                                         r"\['SA2_GUEST_CWD'\]"):
        RR.run("t", {"SA2_GUEST_CWD": "Z:\\x"})


def test_a_kept_lifted_capture_from_another_directory_is_not_compared(tmp_path, monkeypatch):
    """--no-run reuses a kept lifted capture only if it ran in its capture's
    directory."""
    import tick_replay as T
    reg = entry(REGISTERED, [])
    monkeypatch.setattr(T, "sha256", lambda p: "0" * 64)
    reg["native"]["sha256"] = "0" * 64
    monkeypatch.setattr(T, "native_probe_current", lambda e: {})
    monkeypatch.setattr(T, "native_oracle", lambda e: "vanilla")
    monkeypatch.setattr(T, "native_start", lambda e: None)
    monkeypatch.setattr(T, "read_capture", lambda p: (["tick"], []))
    monkeypatch.setattr(T, "poll_budget", lambda e: 0)
    monkeypatch.setattr(T, "native_device_profile", lambda e: CHECKED)
    monkeypatch.setattr(T, "native_repeats", lambda *a: {"judged_ticks": 0})
    monkeypatch.setattr(T, "OUT", tmp_path)
    before = T.inputs(reg)
    kept = {"inputs": before["lifted"], "capture": "c.csv", "sha256": "0" * 64,
            "device_profile": REGISTERED, "guest_paths": DP.DEFAULT_GUEST}
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot" / "result.json").write_text(json.dumps({"lifted": kept}))
    with pytest.raises(SystemExit, match=r"--no-run, and boot's lifted capture ran in guest "
                                         r"directory .*C:.*not its capture's"):
        T.replay(reg, run=False)


def test_the_lifted_run_takes_its_profile_only_as_device_profile(tmp_path, monkeypatch):
    T = lifted_paths(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match=r"extra_env may not override .*SA2_DEVICE_PROFILE"):
        T.run_lifted("t", {"SA2_DEVICE_PROFILE": "x"}, device_profile=DP.DEFAULT)
    for override in ("SA2_XINPUT_PAD", "SA2_GUEST_CPUS"):
        with pytest.raises(SystemExit, match=rf"extra_env may not override .*{override}"):
            T.run_lifted("t", {override: "1"}, device_profile=DP.DEFAULT)
    with pytest.raises(TypeError):
        T.run_lifted("t", {})                                   # no default to fall back on


def test_the_registry_s_profile_is_a_lifted_input():
    import tick_replay as T
    reg = entry(REGISTERED, [])
    reg["native"]["sha256"] = "0" * 64
    assert T.inputs(reg)["lifted"]["device_profile"] == REGISTERED


def test_the_reach_run_takes_its_profile_only_as_device_profile(tmp_path, monkeypatch):
    RR = reach_paths(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match=r"REACH_FAILED extra_env may not set "
                                         r"\['SA2_DEVICE_PROFILE'\]"):
        RR.run("t", {"SA2_DEVICE_PROFILE": "x"})
    with pytest.raises(SystemExit, match=r"REACH_FAILED extra_env may not set "
                                         r"\['SA2_IMPORT_BUDGET'\]"):
        RR.run("t", {"SA2_IMPORT_BUDGET": "1"})
    with pytest.raises(SystemExit, match=r"REACH_FAILED extra_env may not set "
                                         r"\['SA2_GUEST_CPUS', 'SA2_XINPUT_PAD'\]"):
        RR.run("t", {"SA2_GUEST_CPUS": "16", "SA2_XINPUT_PAD": "1"})


def test_a_reach_receipt_s_profile_is_read_back_for_its_reproduction(tmp_path):
    import reach_receipt as RR
    assert RR.recorded_device_profile(reach_receipt(tmp_path, DP.DEFAULT)) == DP.DEFAULT
    assert RR.recorded_device_profile(reach_receipt(tmp_path, None)) == DP.DEFAULT
    path = tmp_path / DP.FILE
    path.write_bytes(DP.dump(GOOD))
    # the capture's receipt beside its profile, which says where its game ran
    from test_device_profile import CAPTURE_CWD, LAUNCH
    (tmp_path / "receipt.json").write_text(json.dumps({"cwd": CAPTURE_CWD, "command": LAUNCH}))
    rec = {"path": str(path), "sha256": GOOD_SHA}
    assert RR.recorded_device_profile(reach_receipt(tmp_path, rec, GUEST)) == dict(
        rec, counts=COUNTS, guest=GUEST)
    # a reach measured before the directory was an input, or in another one, is
    # not reproduced in the capture's: it is refused and re-measured
    with pytest.raises(SystemExit, match=r"REACH_FAILED the reach receipt records guest "
                                         r"directory None and its device profile's capture"):
        RR.recorded_device_profile(reach_receipt(tmp_path, rec))
    with pytest.raises(SystemExit, match="REACH_FAILED the reach receipt records guest directory"):
        RR.recorded_device_profile(reach_receipt(tmp_path, rec, DP.DEFAULT_GUEST))
    path.write_bytes(DP.dump(GOOD) + b"\n")
    with pytest.raises(SystemExit, match="REACH_FAILED the receipt's device profile"):
        RR.recorded_device_profile(reach_receipt(tmp_path, rec, GUEST))


def stage_tree(tmp_path: Path, monkeypatch):
    """stage_run under tmp_path, with the pinned saves a lifted run starts from
    (test_lifted_saves.pinned_tree): its main() clears the overlay's saves
    under its ROOT before it runs anything."""
    import reach_receipt as RR
    import stage_run as SR
    from test_lifted_saves import pinned_tree
    for var in RR.DEVICE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(SR, "ROOT", tmp_path)
    monkeypatch.setattr(SR, "OUT", tmp_path / "replay")
    monkeypatch.setattr(SR, "RUNDIR", tmp_path / "run")
    pinned_tree(tmp_path)
    return SR


def spec_of(cmd: list[str]) -> list[str]:
    return cmd[cmd.index("--run") + 1].split("|")


def test_a_stage_run_reads_the_profile_it_is_handed_and_no_other(tmp_path, monkeypatch):
    SR = stage_tree(tmp_path, monkeypatch)
    cmd, _, _ = SR.command("metalharbor", device_profile=CHECKED)
    assert f"SA2_DEVICE_PROFILE={REGISTERED['path']}" in spec_of(cmd)
    cmd, _, _ = SR.command("metalharbor")
    assert not [kv for kv in spec_of(cmd) if kv.startswith("SA2_DEVICE_PROFILE=")]
    with pytest.raises(SystemExit, match="STAGE_RUN_REFUSED SA2_DEVICE_PROFILE in extra_env"):
        SR.command("metalharbor", extra_env={"SA2_DEVICE_PROFILE": "x"})
    monkeypatch.setenv(DP.ENV, "left/in/a/shell.txt")
    with pytest.raises(SystemExit, match="STAGE_RUN_REFUSED SA2_DEVICE_PROFILE is set in this "
                                         "shell"):
        SR.command("metalharbor")


@pytest.mark.parametrize("override", ["SA2_XINPUT_PAD", "SA2_GUEST_CPUS"])
def test_a_host_device_override_never_reaches_a_stage_run(tmp_path, monkeypatch, override):
    """stage_boot and reach_extend run this command with their own environment,
    so the command refuses the shell's override rather than trusting a strip."""
    SR = stage_tree(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match=f"STAGE_RUN_REFUSED {override} in extra_env"):
        SR.command("metalharbor", extra_env={override: "1"})
    monkeypatch.setenv(override, "1")
    with pytest.raises(SystemExit, match=f"STAGE_RUN_REFUSED {override} is set in this shell"):
        SR.command("metalharbor")


def test_a_synthetic_recording_replays_on_the_default_and_is_compared_with_nothing(
        tmp_path, monkeypatch):
    """`--no-native --input`: tools/s4/synth_input.py's recordings stand for no
    capture (the 2026-09-23 review found the pair refused at argparse)."""
    SR = stage_tree(tmp_path, monkeypatch)
    script = tmp_path / "synth" / "forward-jump" / "sa2-input.csv"
    script.parent.mkdir(parents=True)
    script.write_text("frame\n")
    ran = []
    monkeypatch.setattr(SR.subprocess, "run", lambda cmd, **kw: ran.append((cmd, kw["env"]))
                        or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(sys, "argv", ["stage_run.py", "--no-native", "--input", str(script)])
    assert SR.main() == 1                      # the fake run wrote no record
    [(cmd, env)] = ran
    spec = spec_of(cmd)
    assert f"SA2_INPUT_SCRIPT={script}" in spec
    assert not [kv for kv in spec if kv.split("=")[0] in DP.OWNED]
    assert devices_in(spec) == ON                  # the reach configuration's, once each
    assert not [k for k in DP.OWNED + DEVICES if k in env]
    result = json.loads((tmp_path / "replay" / "stage-metalharbor-input" / "stage-run.json")
                        .read_text())
    assert (result["native"], result["vs_native"], result["device_profile"]) == (None, None,
                                                                                 "default")
    monkeypatch.setattr(sys, "argv", ["stage_run.py", "--no-native", "--native", str(script)])
    with pytest.raises(SystemExit):
        SR.main()                              # a capture with no capture is argparse's error


# ------------------------------------------------------------ the recording devices
#
# Each recording device refuses a capture's profile at configuration while it
# is off, so a run that stands for a capture needs all three on. They were on
# only in tools/s4/demo_run.py, from a map of its own; the stage run and the
# tick replay's lifted run were refused at configuration on any capture's
# profile (the XAudio2 slice's review, 2026-09-23). The reach configuration has
# switched them on for every run, with the D3DX constant table, since the
# before/after comparison on the default profile of 2026-09-24.

def test_the_reach_configuration_switches_every_device_on_from_one_definition(monkeypatch):
    import reach_receipt as RR
    assert tuple(RR.DEVICES) == DEVICES and set(RR.DEVICES.values()) == {"1"}
    assert RR.DEVICE_BREAKS == BREAKS
    assert RR.HOST_INPUT_VARS == HOST_INPUT
    assert set(RR.DEVICE_VARS) == set(DP.OWNED) | set(DEVICES) | set(BREAKS) | set(HOST_INPUT)
    assert {k: RR.ENV[k] for k in DEVICES} == dict(RR.DEVICES)
    # ENV has them on for every run, so a capture's profile adds only its own
    # path, and nothing is set twice.
    assert RR.profile_env(DP.DEFAULT) == {}
    # A capture's profile adds its path and the directory its game ran in.
    capture_vars = {"SA2_DEVICE_PROFILE": REGISTERED["path"], "SA2_GUEST_CWD": GUEST["cwd"],
                    "SA2_GUEST_EXE_PATH": GUEST["exe_path"]}
    assert RR.profile_env(CHECKED) == capture_vars
    # profile_env stays the one place a device the reach configuration leaves
    # off is switched on for a capture's profile.
    monkeypatch.setattr(RR, "ENV", {k: v for k, v in RR.ENV.items() if k not in RR.DEVICES})
    assert RR.profile_env(CHECKED) == dict(RR.DEVICES, **capture_vars)
    assert RR.profile_env(DP.DEFAULT) == {}


def test_a_stage_run_has_every_device_on_once_on_either_profile(tmp_path, monkeypatch):
    SR = stage_tree(tmp_path, monkeypatch)
    assert devices_in(spec_of(SR.command("metalharbor", device_profile=CHECKED)[0])) == ON
    assert devices_in(spec_of(SR.command("metalharbor")[0])) == ON


def lifted_command(tmp_path, monkeypatch, profile) -> tuple[list[str], dict]:
    """The command and environment tools/s4/tick_replay.py's lifted run would
    start; the run itself is refused, since nothing may build here."""
    T = lifted_paths(tmp_path, monkeypatch)
    ran = []
    monkeypatch.setattr(T.subprocess, "run", lambda cmd, **kw: ran.append((cmd, kw["env"]))
                        or subprocess.CompletedProcess(cmd, 1, "", ""))
    with pytest.raises(SystemExit, match="the lifted run did not complete"):
        T.run_lifted("t", device_profile=profile)
    [(cmd, env)] = ran
    return cmd, env


def test_the_tick_replay_s_lifted_run_on_a_capture_s_profile_has_every_device_on(
        tmp_path, monkeypatch):
    for var in DEVICES:
        monkeypatch.setenv(var, "0")           # a shell's switch never reaches the run
    cmd, env = lifted_command(tmp_path, monkeypatch, CHECKED)
    spec = spec_of(cmd)
    assert devices_in(spec) == ON
    assert f"SA2_DEVICE_PROFILE={REGISTERED['path']}" in spec
    assert not [k for k in DEVICES if k in env]


def test_the_tick_replay_s_lifted_run_on_the_default_has_the_reach_s_devices(
        tmp_path, monkeypatch):
    cmd, env = lifted_command(tmp_path / "d", monkeypatch, DP.DEFAULT)
    spec = spec_of(cmd)
    assert devices_in(spec) == ON
    assert not [kv for kv in spec if kv.startswith(f"{DP.ENV}=")]


def reach_command(tmp_path, monkeypatch, *args, **kw) -> tuple[list[str], dict]:
    """The command and environment tools/s4/reach_receipt.py's run would start,
    with the build check stubbed and the run refused, so nothing builds here."""
    RR = reach_paths(tmp_path, monkeypatch)
    ran = []
    monkeypatch.setattr(RR.subprocess, "run", lambda cmd, **k: ran.append((cmd, k["env"]))
                        or subprocess.CompletedProcess(cmd, 1, "", ""))
    with pytest.raises(SystemExit, match="REACH_FAILED loop_run exited 1"):
        RR.run("t", *args, **kw)
    [(cmd, env)] = ran
    return cmd, env


def test_the_reach_run_on_a_capture_s_profile_has_every_device_on(tmp_path, monkeypatch):
    """The reach run a replay's hooked run is held to takes the same definition,
    so the two are configured alike."""
    monkeypatch.setenv("SA2_DDRAW_DEVICE", "0")
    cmd, env = reach_command(tmp_path, monkeypatch, device_profile=CHECKED)
    assert devices_in(spec_of(cmd)) == ON
    assert "SA2_DDRAW_DEVICE" not in env


def test_the_reach_record_names_the_device_switches_its_run_had_beside_env(
        tmp_path, monkeypatch):
    """The manifest's environment is ENV, which switches every device on, so a
    run on either profile records none beside it; were ENV to leave one off, a
    capture's profile would switch it on, and the record would name it."""
    RR = reach_paths(tmp_path, monkeypatch)
    run = tmp_path / "gen" / "s4" / "run"
    record = devices_on({"pump": {"iterations": 1, "driven": False}, "calls": {"total": 2},
                         "d3d9": {"frames": {"present": 3}}, "trap": {"note": "x"}})

    def fake(cmd, **kw):
        name = cmd[cmd.index("--name") + 1]
        default = not [kv for kv in spec_of(cmd) if kv.startswith("SA2_DEVICE_PROFILE=")]
        block = ({"default": True, "source": "default", "sha256": ""} if default else
                 {"default": False, "source": REGISTERED["path"], "sha256": GOOD_SHA})
        (run / f"{name}.a.json").write_text(json.dumps(devices_on(dict(
            {k: v for k, v in record.items() if k != "virtual"}, device_profile=block))))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(RR.subprocess, "run", fake)
    assert RR.run("t", device_profile=CHECKED)["device_switches"] == {}
    assert RR.run("d")["device_switches"] == {}
    monkeypatch.setattr(RR, "ENV", {k: v for k, v in RR.ENV.items() if k != "SA2_DDRAW_DEVICE"})
    assert RR.run("c", device_profile=CHECKED)["device_switches"] == {"SA2_DDRAW_DEVICE": "1"}
    assert RR.run("p", {"SA2_DDRAW_DEVICE": "1", "SA2_DINPUT_BREAK": "argc"})[
        "device_switches"] == {"SA2_DDRAW_DEVICE": "1"}


@pytest.mark.parametrize("profile", ["default", "capture"])
def test_no_run_turns_a_device_off(tmp_path, monkeypatch, profile):
    """Every device is on in the reach configuration, on the default profile as
    on a capture's, and no caller's extra settings may turn one off: not the
    reach run's, the tick replay's, the stage run's or the attract demo's."""
    import demo_run as D
    import reach_receipt as RR
    import tick_replay as T
    prof = DP.DEFAULT if profile == "default" else CHECKED
    reach_paths(tmp_path / "r", monkeypatch)
    for var in DEVICES:
        with pytest.raises(SystemExit, match=rf"REACH_FAILED extra_env may not set "
                                             rf"\['{var}'\]"):
            RR.run("t", {var: "0"}, device_profile=prof)
    lifted_paths(tmp_path / "l", monkeypatch)
    for var in DEVICES:
        with pytest.raises(SystemExit, match=rf"extra_env may not override .*{var}"):
            T.run_lifted("t", {var: "0"}, device_profile=prof)
    SR = stage_tree(tmp_path / "s", monkeypatch)
    for var in DEVICES:
        with pytest.raises(SystemExit, match=f"STAGE_RUN_REFUSED {var} in extra_env"):
            SR.command("metalharbor", extra_env={var: "0"}, device_profile=prof)
    monkeypatch.setattr(D, "ROOT", tmp_path)
    for var in DEVICES:
        with pytest.raises(SystemExit, match=f"DEMO_RUN_REFUSED {var} in extra_env"):
            D.command(100, prof, 60, out=tmp_path / "d", extra_env={var: "0"})


@pytest.mark.parametrize("var", DEVICES + BREAKS + HOST_INPUT)
def test_a_device_switch_left_in_a_shell_never_reaches_a_run(tmp_path, monkeypatch, var):
    """stage_boot, reach_extend and d3d9_stride_check run the stage command with
    their own environment, so the command refuses the shell's switch, break or
    host keyboard; the tools that start a run themselves strip it."""
    import reach_receipt as RR
    SR = stage_tree(tmp_path, monkeypatch)
    monkeypatch.setenv(var, "1")
    with pytest.raises(SystemExit, match=f"STAGE_RUN_REFUSED {var} is set in this shell"):
        SR.command("metalharbor")
    assert var not in RR.clean_environ()


@pytest.mark.parametrize("var", HOST_INPUT)
def test_the_host_keyboard_never_reaches_a_run_through_extra_env(tmp_path, monkeypatch, var):
    """A caller's extra settings could still hand a registered run the host keyboard,
    which only tools/s5/play_live.py sets (the speed fold's review, NIT 3): the reach
    run, the stage command and the attract demo's refuse it before anything runs."""
    import demo_run as D
    RR = reach_paths(tmp_path / "r", monkeypatch)
    with pytest.raises(SystemExit, match=rf"REACH_FAILED extra_env may not set \['{var}'\]: "):
        RR.run("t", {var: "x"})
    SR = stage_tree(tmp_path / "s", monkeypatch)
    with pytest.raises(SystemExit, match=f"STAGE_RUN_REFUSED {var} in extra_env: the device "):
        SR.command("metalharbor", extra_env={var: "x"})
    monkeypatch.setattr(D, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match=f"DEMO_RUN_REFUSED {var} in extra_env: the device "):
        D.command(100, DP.DEFAULT, 60, out=tmp_path / "d", extra_env={var: "x"})


def test_a_break_a_run_names_itself_still_reaches_it(tmp_path, monkeypatch):
    """A falsification names its break in its own settings; only the shell's
    is stripped or refused."""
    monkeypatch.setenv("SA2_XAUDIO2_BREAK", "argc")
    cmd, env = reach_command(tmp_path / "r", monkeypatch, {"SA2_DINPUT_BREAK": "argc"})
    assert "SA2_DINPUT_BREAK=argc" in spec_of(cmd)
    assert not [kv for kv in spec_of(cmd) if kv.startswith("SA2_XAUDIO2_BREAK=")]
    assert "SA2_XAUDIO2_BREAK" not in env
    monkeypatch.delenv("SA2_XAUDIO2_BREAK")
    SR = stage_tree(tmp_path / "s", monkeypatch)
    cmd, _, _ = SR.command("metalharbor", extra_env={"SA2_DDRAW_BREAK": "argc"})
    assert "SA2_DDRAW_BREAK=argc" in spec_of(cmd)


def test_the_attract_demo_run_takes_the_same_definition(tmp_path, monkeypatch):
    """tools/s4/demo_run.py kept its own DEVICES map; it now has none, and its
    runs have the reach configuration's devices on either profile."""
    import demo_run as D
    import reach_receipt as RR
    for var in RR.DEVICE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(D, "ROOT", tmp_path)
    assert not hasattr(D, "DEVICES")
    capture_spec = spec_of(D.command(100, CHECKED, 60, out=tmp_path / "c")[0])
    default_spec = spec_of(D.command(100, DP.DEFAULT, 60, out=tmp_path / "d")[0])
    assert devices_in(capture_spec) == devices_in(default_spec) == ON
    assert f"SA2_DEVICE_PROFILE={REGISTERED['path']}" in capture_spec
    with pytest.raises(SystemExit, match="DEMO_RUN_REFUSED SA2_DDRAW_DEVICE in extra_env"):
        D.command(100, CHECKED, 60, out=tmp_path / "c", extra_env={"SA2_DDRAW_DEVICE": "0"})
    monkeypatch.setenv("SA2_DINPUT_DEVICE", "1")
    with pytest.raises(SystemExit, match="DEMO_RUN_REFUSED SA2_DINPUT_DEVICE is set in this "
                                         "shell"):
        D.command(100, DP.DEFAULT, 60, out=tmp_path / "d")


# ------------------------------------------------------------ the native side

VANILLA_SOURCE = """/* a stand-in for harness/replay/native_vanilla_probe.c */
#ifndef RESTORE_IAT
#define RESTORE_IAT 1
#endif
static const uint32_t KEEP[][2] = {
    {0x00500100u, 0x00500105u},
};
"""
VANILLA_LOG = ("va,length,loader_bytes,file_bytes,action\n"
               "0x00500100,5,e9a1a2a3a4,a1b2c3d4e5,kept\n"
               "0x00500200,5,e9b1b2b3b4,b2c3d4e5f6,restored\n"
               "0x00900010,4,f1e2d3c4,c4d3e2f1,iat-restored\n"
               "0x00500200,5,,,tick1-intact\n")


def test_the_default_capture_is_refused_for_every_reason_with_the_command_that_retakes_it(
        tmp_path, monkeypatch, capsys):
    """The default stage capture was taken with a superseded vanilla probe, no
    device profile and no record of the saves it started from: all three are
    named, in tools/oracle/oracle_mode.py's words, tools/s4/device_profile.py's
    and tools/oracle/save_state.py's, with the command that re-captures it, and
    nothing is built or run (fold-profile's spec L5)."""
    import hashlib
    SR = stage_tree(tmp_path, monkeypatch)
    probe = tmp_path / "harness" / "replay" / "native_vanilla_probe.c"
    probe.parent.mkdir(parents=True)
    probe.write_text(VANILLA_SOURCE)
    old = tmp_path / "private" / "captures" / "stage-default"
    old.mkdir(parents=True)
    (old / "sa2-vanilla.csv").write_text(VANILLA_LOG)
    mods = ["vanilla_probe", "tick_probe"]
    (old / "receipt.json").write_text(json.dumps({
        "session": {},
        "artifacts": [{"file": "sa2-vanilla.csv", "check": {
            "sha256": hashlib.sha256(VANILLA_LOG.encode()).hexdigest()}}],
        "inputs": {"built_probes": [
            {"mod_id": m, "source": f"/elsewhere/harness/replay/native_{m}.c",
             "source_sha256": "0" * 64, "defines": []} for m in mods],
            "enabled_mods": mods, "game_args": ["--level", "metalharbor"],
            "modloader_profile": {"Graphics": {"SkipIntro": False}, "EnabledMods": mods}}}))
    monkeypatch.setattr(SR, "DEFAULT_NATIVE", old)
    monkeypatch.setattr(SR.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    monkeypatch.setattr(sys, "argv", ["stage_run.py"])
    assert SR.main() == 1
    out = capsys.readouterr().out
    assert out.startswith("STAGE_RUN_REFUSED private/captures/stage-default cannot stand for "
                          "this run:\n")
    now = hashlib.sha256(VANILLA_SOURCE.encode()).hexdigest()
    assert ("  - stage-default was taken with a native_vanilla_probe.c that is not the one in "
            f"the tree (000000000000 against {now[:12]}); re-capture it\n" in out)
    assert ("  - private/captures/stage-default's receipt records no device profile: re-capture "
            "it with the profile probe (tools/oracle/run_oracle_probe.py --device-profile)\n"
            in out)
    assert ("  - private/captures/stage-default's receipt records no start saves "
            "(session.saves.at_start), so nothing shows it started from the pinned saves a "
            "lifted replay of it starts from (Documentation/save-state.json, before the Light "
            "Dash pickup); re-capture it\n" in out)
    assert f"  re-capture it: {SR.RECAPTURE}\n" in out
    assert SR.RECAPTURE.index("--build vanilla_probe=") < SR.RECAPTURE.index("--build tick_probe=")
    assert " --device-profile " in SR.RECAPTURE
    assert f"--name {SR.RECAPTURE_NAME} " in SR.RECAPTURE
    [then] = [line for line in out.splitlines() if line.startswith("  then pass --native ")]
    assert then.endswith(f"/{SR.RECAPTURE_NAME}, or make it DEFAULT_NATIVE in "
                         "tools/s4/stage_run.py")


def test_the_default_stage_capture_stands_for_a_run_or_is_refused_until_retaken():
    """Spec L5 against the tree: the registered default, mh-spawn-vanilla-v2a,
    is refused until RECAPTURE has run and DEFAULT_NATIVE names the new capture,
    which must then stand for a run."""
    import stage_run as SR
    if not (SR.DEFAULT_NATIVE / "receipt.json").is_file():
        pytest.skip(f"the default stage capture is not present: {SR.DEFAULT_NATIVE}")
    profile, problems = SR.native_side(SR.DEFAULT_NATIVE)
    if SR.DEFAULT_NATIVE.name == "mh-spawn-vanilla-v2a":
        assert profile is None
        assert any("receipt records no device profile" in p for p in problems)
        assert any("not the one in the tree" in p for p in problems)
    else:
        assert (problems, profile is None) == ([], False)


def test_a_capture_whose_profile_probe_did_not_load_last_is_refused_by_the_replay_tools(
        tmp_path, monkeypatch):
    """The verify-profile review's second LOW finding, held on the replay path:
    DP.capture_profile requires the profile probe last in the build list and in
    the loader's order, and the tick replay and the stage run both ask it."""
    import stage_run as SR
    import tick_replay as T
    capture(tmp_path, "c", DP.dump(GOOD), profile_first=True)
    monkeypatch.setattr(T, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match=r"REPLAY_REFUSED .*somewhere other than last"):
        T.native_device_profile(entry(REGISTERED, []))
    monkeypatch.setattr(SR, "ROOT", tmp_path)
    profile, problems = SR.native_side(tmp_path / "private" / "captures" / "c")
    assert profile is None and any("somewhere other than last" in p for p in problems)


def test_the_snapshot_run_stands_for_the_boot_capture_and_reads_its_profile(tmp_path,
                                                                            monkeypatch):
    """tools/s4/snapshot_fields.py's lifted run is the boot replay's with
    snapshots on, so it is handed the boot capture's profile, never the default."""
    import snapshot_fields as SF
    import tick_replay as T
    registry = tmp_path / "replays.json"
    registry.write_text(json.dumps({"replays": [{"name": "other"}, {"name": "boot", "n": 1}]}))
    monkeypatch.setattr(SF, "ROOT", tmp_path)
    monkeypatch.setattr(T, "REGISTRY", registry)
    monkeypatch.setattr(T, "OUT", tmp_path / "replay")
    monkeypatch.setattr(T, "native_device_profile",
                        lambda e: {"path": "p", "sha256": "s", "of": e["name"]})
    handed = []

    def run_lifted(name, extra, *, device_profile):
        handed.append((name, sorted(extra), device_profile))
        (tmp_path / extra["SA2_REPLAY_SNAPSHOTS"]).write_bytes(b"")
        return {"reach": {}}
    monkeypatch.setattr(T, "run_lifted", run_lifted)
    SF.run_lifted("snap")
    assert handed == [("snap", ["SA2_REPLAY_SNAPSHOTS", "SA2_REPLAY_SNAP_EVERY",
                                "SA2_REPLAY_SNAP_LIMIT"],
                       {"path": "p", "sha256": "s", "of": "boot"})]
    registry.write_text(json.dumps({"replays": [{"name": "other"}]}))
    with pytest.raises(SystemExit, match="registers no replay named boot"):
        SF.run_lifted("snap")


# ------------------------------------------------------------ P6's review fold
#
# The read-back that proves a lifted run read the profile it was handed, held
# at each runner that makes one (W2); a device the reach configuration leaves
# off still switched on for a capture's profile by every runner (N1); a run
# record that shows a switch off refused (N4); and a stage run's extra_env held
# to what the command sets itself (N2). Every device and capture here is made
# up.

RECORD = {"pump": {"iterations": 1, "driven": False}, "calls": {"total": 2},
          "d3d9": {"frames": {"present": 3}}, "trap": {"note": "x"}}


def test_the_tick_replay_refuses_a_lifted_run_whose_record_names_another_profile(
        tmp_path, monkeypatch):
    """Without DP.check_run in run_lifted, the whole suite passed (W2)."""
    T = lifted_paths(tmp_path, monkeypatch)

    def fake(cmd, **kw):
        prefix = cmd[cmd.index("--out-prefix") + 1]
        ticks = [kv for kv in spec_of(cmd) if kv.startswith("SA2_REPLAY_TICKS=")][0]
        out = tmp_path / ticks.split("=", 1)[1]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("tick,x\n1,0\n")
        (T.RUNDIR / f"{prefix}.ticks.json").write_text(
            json.dumps(devices_on(dict(RECORD, device_profile=DEFAULT_BLOCK))))
        (T.RUNDIR / f"{prefix}.matrix.json").write_text(json.dumps({"sha256": "0" * 64}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(T.subprocess, "run", fake)
    with pytest.raises(SystemExit, match=r"the lifted run is not the one asked for: the run read "
                                         r"device profile default and was handed "):
        T.run_lifted("t", device_profile=CHECKED)


def test_the_reach_run_refuses_a_record_that_names_another_profile(tmp_path, monkeypatch):
    """Without DP.check_run in reach_receipt.run, the whole suite passed (W2)."""
    RR = reach_paths(tmp_path, monkeypatch)
    run = tmp_path / "gen" / "s4" / "run"

    def fake(cmd, **kw):
        name = cmd[cmd.index("--name") + 1]
        (run / f"{name}.a.json").write_text(
            json.dumps(devices_on(dict(RECORD, device_profile=DEFAULT_BLOCK))))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(RR.subprocess, "run", fake)
    with pytest.raises(SystemExit, match=r"REACH_FAILED the run read device profile default and "
                                         r"was handed "):
        RR.run("t", device_profile=CHECKED)


def test_a_device_the_reach_configuration_leaves_off_is_on_for_a_capture_s_profile_in_every_runner(
        tmp_path, monkeypatch):
    """RR.profile_env is the one definition of what a capture's profile
    switches on beside ENV. With SA2_DDRAW_DEVICE dropped from ENV, every runner
    still sets it on a capture's profile and none sets it on the default.
    Replacing RR.profile_env with DP.run_env in the tick replay, the stage run
    or the attract demo passed every test until this one (N1)."""
    import demo_run as D
    import reach_receipt as RR
    monkeypatch.setattr(RR, "ENV", {k: v for k, v in RR.ENV.items() if k != "SA2_DDRAW_DEVICE"})
    want = "SA2_DDRAW_DEVICE=1"
    assert want in spec_of(lifted_command(tmp_path / "l", monkeypatch, CHECKED)[0])
    assert want not in spec_of(lifted_command(tmp_path / "ld", monkeypatch, DP.DEFAULT)[0])
    assert want in spec_of(reach_command(tmp_path / "r", monkeypatch,
                                         device_profile=CHECKED)[0])
    assert want not in spec_of(reach_command(tmp_path / "rd", monkeypatch)[0])
    SR = stage_tree(tmp_path / "s", monkeypatch)
    assert want in spec_of(SR.command("metalharbor", device_profile=CHECKED)[0])
    assert want not in spec_of(SR.command("metalharbor")[0])
    monkeypatch.setattr(D, "ROOT", tmp_path)
    assert want in spec_of(D.command(100, CHECKED, 60, out=tmp_path / "c")[0])
    assert want not in spec_of(D.command(100, DP.DEFAULT, 60, out=tmp_path / "d")[0])


def test_every_switch_the_reach_configuration_turns_on_has_a_record_field():
    import reach_receipt as RR
    assert set(RR.SWITCH_RECORD) == {k for k in RR.ENV if k.endswith("_DEVICE")} | set(DEVICES)
    assert {k: RR.ENV[k] for k in RR.SWITCH_RECORD} == {k: "1" for k in RR.SWITCH_RECORD}
    assert RR.switch_problems(devices_on({}), RR.ENV) == []
    assert len(RR.switch_problems({}, RR.ENV)) == len(RR.SWITCH_RECORD)


@pytest.mark.parametrize("var", ["SA2_D3D9_DEVICE", "SA2_DSOUND_DEVICE", *DEVICES])
def test_a_record_that_shows_a_switch_off_is_named_and_only_it(var):
    import reach_receipt as RR
    rec = devices_on({})
    *path, last = RR.SWITCH_RECORD[var]
    node = rec
    for k in path:
        node = node[k]
    node[last] = False
    assert [p.split("=", 1)[0] for p in RR.switch_problems(rec, RR.ENV)] == [var]
    # an arm that turns the switch off itself (tools/s4/d3dx_table_check.py's off
    # arm) is held only to the switches its row turns on
    assert RR.switch_problems(rec, dict(RR.ENV, **{var: "0"})) == []


def test_the_reach_run_and_the_hooked_run_refuse_a_record_whose_table_was_off(
        tmp_path, monkeypatch):
    """The runtime runs a capture's profile with the D3DX table off rather than
    refusing it, and the reach fields cannot tell (N4): the record's switch is
    read instead."""
    rec = devices_on(dict(RECORD, device_profile=DEFAULT_BLOCK))
    rec["d3d9"]["constant_tables"]["switch"] = False
    why = (r"SA2_D3DX_CONSTANT_TABLE=1 was set and the run's record reads "
           r"d3d9\.constant_tables\.switch = False")
    RR = reach_paths(tmp_path / "r", monkeypatch)
    run = tmp_path / "r" / "gen" / "s4" / "run"

    def reach_fake(cmd, **kw):
        (run / f"{cmd[cmd.index('--name') + 1]}.a.json").write_text(json.dumps(rec))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(RR.subprocess, "run", reach_fake)
    with pytest.raises(SystemExit, match=r"REACH_FAILED the run's devices are not its "
                                         r"configuration's: " + why):
        RR.run("t")
    T = lifted_paths(tmp_path / "l", monkeypatch)

    def lifted_fake(cmd, **kw):
        prefix = cmd[cmd.index("--out-prefix") + 1]
        ticks = [kv for kv in spec_of(cmd) if kv.startswith("SA2_REPLAY_TICKS=")][0]
        out = tmp_path / "l" / ticks.split("=", 1)[1]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("tick,x\n1,0\n")
        (T.RUNDIR / f"{prefix}.ticks.json").write_text(json.dumps(rec))
        (T.RUNDIR / f"{prefix}.matrix.json").write_text(json.dumps({"sha256": "0" * 64}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(T.subprocess, "run", lifted_fake)
    with pytest.raises(SystemExit, match=r"its devices are not its configuration's: " + why):
        T.run_lifted("t", device_profile=DP.DEFAULT)


HOST_BLOCK = {"source": "private/generated/s5/live/input.fifo", "answers_from_host": 7,
              "polls": 9}
HOST_WHY = ("the run's record reads dinput.host_input (source "
            "'private/generated/s5/live/input.fifo'): a host keyboard answered its DirectInput "
            "device, which only tools/s5/play_live.py sets")


def test_a_record_whose_keyboard_was_the_host_s_is_named():
    """harness/crt_probe.c prints dinput.host_input only when a host keyboard source was
    set, so a record carrying it is a run whose DirectInput device was not its
    configuration's, whatever its switches read (the speed fold's review, NIT 3)."""
    import reach_receipt as RR
    rec = devices_on({"dinput": {"host_input": HOST_BLOCK}})
    assert RR.switch_problems(rec, RR.ENV) == [HOST_WHY]
    assert RR.switch_problems(rec, {}) == [HOST_WHY]
    assert RR.switch_problems(devices_on({"dinput": {"host": 0}}), RR.ENV) == []


def test_the_reach_run_and_the_hooked_run_refuse_a_record_whose_keyboard_was_the_host_s(
        tmp_path, monkeypatch):
    rec = devices_on(dict(RECORD, device_profile=DEFAULT_BLOCK,
                          dinput={"host_input": HOST_BLOCK}))
    why = re.escape(HOST_WHY)
    RR = reach_paths(tmp_path / "r", monkeypatch)
    run = tmp_path / "r" / "gen" / "s4" / "run"

    def reach_fake(cmd, **kw):
        (run / f"{cmd[cmd.index('--name') + 1]}.a.json").write_text(json.dumps(rec))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(RR.subprocess, "run", reach_fake)
    with pytest.raises(SystemExit, match=r"REACH_FAILED the run's devices are not its "
                                         r"configuration's: " + why):
        RR.run("t")
    T = lifted_paths(tmp_path / "l", monkeypatch)

    def lifted_fake(cmd, **kw):
        prefix = cmd[cmd.index("--out-prefix") + 1]
        ticks = [kv for kv in spec_of(cmd) if kv.startswith("SA2_REPLAY_TICKS=")][0]
        out = tmp_path / "l" / ticks.split("=", 1)[1]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("tick,x\n1,0\n")
        (T.RUNDIR / f"{prefix}.ticks.json").write_text(json.dumps(rec))
        (T.RUNDIR / f"{prefix}.matrix.json").write_text(json.dumps({"sha256": "0" * 64}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(T.subprocess, "run", lifted_fake)
    with pytest.raises(SystemExit, match=r"its devices are not its configuration's: " + why):
        T.run_lifted("t", device_profile=DP.DEFAULT)


@pytest.mark.parametrize("var", ["SA2_D3D9_DEVICE", "SA2_DSOUND_DEVICE", "SA2_PUMP_POLLS",
                                 "SA2_REPLAY_TICKS", "SA2_VIRTUAL_BUDGET_MS"])
def test_a_stage_run_s_extra_env_sets_nothing_the_command_sets(tmp_path, monkeypatch, var):
    """demo_run.command refused this and stage_run.command did not: laid over
    the reach configuration, SA2_D3D9_DEVICE=0 turned off the device the D3DX
    table's answer needs (N2)."""
    SR = stage_tree(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match=f"STAGE_RUN_REFUSED {var} in extra_env: this command "
                                         "sets it itself"):
        SR.command("metalharbor", extra_env={var: "0"})


def test_a_stage_run_with_a_recording_refuses_extra_env_naming_its_input_settings(
        tmp_path, monkeypatch):
    SR = stage_tree(tmp_path, monkeypatch)
    script = tmp_path / "rec" / "sa2-input.csv"
    with pytest.raises(SystemExit, match="STAGE_RUN_REFUSED SA2_INPUT_APPLIED in extra_env"):
        SR.command("metalharbor", input_script=script, extra_env={"SA2_INPUT_APPLIED": "x"})
    cmd, _, _ = SR.command("metalharbor", extra_env={"SA2_INPUT_APPLIED": "x"})
    assert "SA2_INPUT_APPLIED=x" in spec_of(cmd)      # no recording: the command sets none
