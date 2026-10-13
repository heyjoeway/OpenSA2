"""tools/s4/d3dx_table_check.py's verdicts, each shown able to fail.

The tool judges a switch-on run of the lifted build against the same build's
switch-off run: the D3DX import answered and never refused, one table per
call, no method on a table but QueryInterface, AddRef and Release and no call
on one but its final Release, the counts adding up, the tables released only
after the last frame, their headers native's, and the tick capture, the stage
trace and every other device count unmoved. It is a comparer, so its verdicts
are held here to a pair of made-up run records that passes every one, and
each of the twelve to a mutation that must fail it -- and exactly the
verdicts the test names with it, so a mutation that also trips another
verdict shows. Then the pieces that are not verdicts: a record that counts a
slot twice is refused; native's headers are the game's calls' on the main
thread, read from made-up trace lines and, where the captures are on disk,
from the two traced logs; the demo arm sets the switch in its own run spec,
so it keeps working once reach_receipt.ENV carries the switch; the
receipt is written only from three scenarios that all held, from one tree;
and each arm is the run its scenario stands for (P6's review): the boot arms
on the boot capture's device profile, every arm from the pinned saves and an
empty overlay, and held to the profile and the switches it was handed.
No build and no run: the records, the import tables and the captures are
written here.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import d3dx_table_check as K  # noqa: E402

POLLS = 5
NATIVE = [("0xfffe0300", 1, 136), ("0xffff0300", 4, 264)]
EXIT = "the guest called ExitProcess(0) -- it ended the process itself, this is not a missing stub"


def record(on: bool) -> dict:
    """A run record as harness/crt_probe.c writes one, cut to what the tool reads."""
    rows = [{"seq": i + 1, "caller": "0x0086722a", "bytecode": "0x04000000",
             "table": f"0x032000{0x50 + 0x20 * i:02x}", "version": v, "constants": c,
             "ctab_bytes": b, "refs": 0, "released_by": "0x0086604a", "released_seq": 900 + i}
            for i, (v, c, b) in enumerate(NATIVE)] if on else []
    impl = [{"interface": "IDirect3DDevice9", "method": "Present", "slot": 17, "calls": 5},
            {"interface": "IDirect3DTexture9", "method": "GetLevelCount", "slot": 13, "calls": 3}]
    impl += [{"interface": K.TABLE, "method": m, "slot": s, "calls": (2 if on and s == 2 else 0)}
             for s, m in enumerate(["QueryInterface", "AddRef", "Release", "GetBufferPointer",
                                    "GetBufferSize", "GetDesc", "GetConstantDesc",
                                    "GetSamplerIndex", "GetConstant", "GetConstantByName"])]
    built = len(rows)
    return {"pump": {"iterations": POLLS, "quit_by_host": 1, "quit_by_guest": 0, "injected": 0},
            "trap": {"status": 1, "note": EXIT},
            "calls": {"total": 1000},
            "d3d9": {"method_calls": 100 + built + (2 if on else 0),
                     "objects_built": 20 + built,
                     "lifetime": {"objects_destroyed": 10 + built},
                     "implemented": impl, "dynamic_surface": [],
                     "constant_tables": {"switch": on, "built": built, "destroyed": built,
                                         "qi_answered": 0, "qi_declined": 0, "rows_dropped": 0,
                                         "last_present_seq": 800, "rows": rows}}}


def arm(tmp: Path, name: str, on: bool) -> dict:
    d = tmp / name
    d.mkdir()
    ticks, trace = d / "lifted.csv", d / "stage-trace.csv"
    ticks.write_text("tick,frame_count\n" + "".join(f"{t},{t * 2}\n" for t in range(1, POLLS)))
    trace.write_text("tick,current_level\n" + "".join(f"{t},0x0000\n" for t in range(1, POLLS)))
    return {"arm": name, "break": "", "record": record(on), "binary_sha256": "ab" * 32,
            "files": {"dir": d, "ticks": ticks, "trace": trace},
            "imports": {"rows": [{"dll": "d3dx9_42.dll", "name": "D3DXGetShaderConstantTable",
                                  "calls": 2, "refused_calls": 0 if on else 2}]}}


@pytest.fixture
def pair(tmp_path):
    return arm(tmp_path, "off", False), arm(tmp_path, "on", True)


def failing(off: dict, on: dict) -> list[str]:
    return sorted(k for k, why in K.judge(off, on, POLLS, NATIVE, []).items() if why)


def test_a_consistent_pair_holds_every_verdict(pair):
    off, on = pair
    assert failing(off, on) == []
    assert sorted(K.judge(off, on, POLLS, NATIVE, [])) == sorted([
        "one_binary", "ran_to_the_leash", "import_row", "tables_built", "no_other_method",
        "only_final_releases", "counts_consistent", "released_after_the_last_frame",
        "headers_are_natives", "ticks_identical", "trace_identical", "nothing_else_moved"])


def edit_on(on: dict, f) -> dict:
    on = copy.deepcopy(on)
    f(on)
    return on


def set_slot(on: dict, slot: int, calls: int) -> None:
    for r in on["record"]["d3d9"]["implemented"]:
        if r["interface"] == K.TABLE and r["slot"] == slot:
            r["calls"] = calls


def addref_pair(on: dict) -> None:
    """An AddRef and a Release more on one table, mid-run: every count still
    adds up, and the device's method calls are two more, as they would be."""
    set_slot(on, 1, 1)
    set_slot(on, 2, 3)
    on["record"]["d3d9"]["method_calls"] += 2


# Each mutation of the switch-on record, and exactly the verdicts it must fail.
MUTATIONS = {
    "one_binary": (lambda on: on.update(binary_sha256="cd" * 32), {"one_binary"}),
    # A refusal stopping the run: not the leash, and a stop off's run did not make.
    "ran_to_the_leash": (lambda on: on["record"]["trap"].update(
        note="ID3DXConstantTable::GetConstantByName called from 0x00867263 on 0x03200050"),
        {"ran_to_the_leash", "nothing_else_moved"}),
    "import_row": (lambda on: on["imports"]["rows"][0].update(refused_calls=1), {"import_row"}),
    "tables_built": (lambda on: on["record"]["d3d9"]["constant_tables"].update(rows_dropped=1),
                     {"tables_built"}),
    "counts_consistent": (lambda on: on["record"]["d3d9"]["constant_tables"]["rows"][0].update(
        released_by="0x00000000"), {"counts_consistent"}),
    "released_after_the_last_frame": (lambda on: on["record"]["d3d9"]["constant_tables"][
        "rows"][1].update(released_seq=700), {"released_after_the_last_frame"}),
    "headers_are_natives": (lambda on: on["record"]["d3d9"]["constant_tables"]["rows"][
        1].update(constants=5), {"headers_are_natives"}),
    # A call native was not measured making, whose counts all add up.
    "only_final_releases": (addref_pair, {"only_final_releases"}),
}


@pytest.mark.parametrize("verdict", sorted(MUTATIONS))
def test_each_mutation_fails_its_verdict(pair, verdict):
    off, on = pair
    mutate, expected = MUTATIONS[verdict]
    assert set(failing(off, edit_on(on, mutate))) == expected


def test_a_lookup_method_called_fails_no_other_method(pair):
    off, on = pair
    mutated = edit_on(on, lambda r: set_slot(r, 9, 1))
    # The extra call is also a device method call the tables do not account for.
    assert failing(off, mutated) == ["no_other_method", "nothing_else_moved"]


def test_a_declined_queryinterface_alone_fails_only_final_releases(pair):
    """The QueryInterface half on its own: no Release comes with it (fold review W1)."""
    off, on = pair
    def declined(r):
        set_slot(r, 0, 1)
        r["record"]["d3d9"]["constant_tables"]["qi_declined"] = 1
        r["record"]["d3d9"]["method_calls"] += 1
    assert failing(off, edit_on(on, declined)) == ["only_final_releases"]


def test_an_addref_on_a_table_never_released_fails_only_final_releases(pair):
    """The AddRef half with the Release count still balanced (fold review W1)."""
    off, on = pair
    def kept(r):
        d3 = r["record"]["d3d9"]
        set_slot(r, 1, 1); set_slot(r, 2, 1)
        d3["constant_tables"]["destroyed"] = 1
        d3["constant_tables"]["rows"][1].update(refs=2, released_by="0x00000000", released_seq=0)
        d3["lifetime"]["objects_destroyed"] -= 1
    assert failing(off, edit_on(on, kept)) == ["only_final_releases"]


def test_a_queryinterface_and_its_release_fail_only_final_releases(pair):
    off, on = pair

    def qi_pair(r: dict) -> None:
        set_slot(r, 0, 1)
        set_slot(r, 2, 3)
        r["record"]["d3d9"]["constant_tables"]["qi_answered"] = 1
        r["record"]["d3d9"]["method_calls"] += 2
    assert failing(off, edit_on(on, qi_pair)) == ["only_final_releases"]


def test_a_release_that_keeps_its_table_fails_the_counts_and_only_final_releases(pair):
    """SA2_D3D9_BREAK=tablekeep's shape: every Release made, none destroyed."""
    off, on = pair

    def kept(r: dict) -> None:
        ct = r["record"]["d3d9"]["constant_tables"]
        ct["destroyed"] = 0
        for row in ct["rows"]:
            row.update(released_by="0x00000000", released_seq=0)
        r["record"]["d3d9"]["lifetime"]["objects_destroyed"] -= len(ct["rows"])
    assert failing(off, edit_on(on, kept)) == ["counts_consistent", "only_final_releases"]


def test_a_record_that_counts_a_slot_twice_is_refused(pair):
    off, on = pair
    mutated = edit_on(on, lambda r: r["record"]["d3d9"]["dynamic_surface"].append(
        {"interface": K.TABLE, "method": "Release", "slot": 2, "calls": 3}))
    with pytest.raises(SystemExit, match="Release \\(slot 2\\) 2 and 3 times"):
        K.judge(off, mutated, POLLS, NATIVE, [])
    # The same count twice is the record as harness/crt_probe.c writes it.
    same = edit_on(on, lambda r: r["record"]["d3d9"]["dynamic_surface"].append(
        {"interface": K.TABLE, "method": "Release", "slot": 2, "calls": 2}))
    assert failing(off, same) == []


def test_an_unnamed_slot_is_counted_too(pair):
    off, on = pair
    mutated = edit_on(on, lambda r: r["record"]["d3d9"]["dynamic_surface"].append(
        {"interface": K.TABLE, "method": "<unnamed slot>", "slot": 40, "calls": 1}))
    assert failing(off, mutated) == ["no_other_method", "nothing_else_moved"]


def test_a_moved_tick_fails_the_tick_capture_and_nothing_else(pair):
    off, on = pair
    text = on["files"]["ticks"].read_text().replace("3,6\n", "3,7\n")
    on["files"]["ticks"].write_text(text)
    assert failing(off, on) == ["ticks_identical"]


def test_a_missing_row_fails_the_stage_trace(pair):
    off, on = pair
    lines = on["files"]["trace"].read_text().splitlines(keepends=True)
    on["files"]["trace"].write_text("".join(lines[:-1]))
    assert failing(off, on) == ["trace_identical"]


def test_another_method_moving_fails_nothing_else_moved(pair):
    off, on = pair
    mutated = edit_on(on, lambda r: r["record"]["d3d9"]["implemented"][1].update(calls=4))
    assert failing(off, mutated) == ["nothing_else_moved"]


def test_a_refused_off_arm_count_must_be_every_call(pair):
    off, on = pair
    off = copy.deepcopy(off)
    off["imports"]["rows"][0]["refused_calls"] = 1
    assert failing(off, on) == ["import_row"]


def test_headers_against_no_native_fail(pair):
    off, on = pair
    assert "headers_are_natives" in sorted(k for k, why in
                                           K.judge(off, on, POLLS, [], []).items() if why)


# ------------------------------------------------------- native's headers

# Made-up lines in the shape of Wine's +d3dx trace on one thread.
CALL = "0024:trace:d3dx:D3DXGetShaderConstantTable (0A000000, 0B000000): Forwarded to " \
       "D3DXGetShaderConstantTableEx"
EX = "0024:trace:d3dx:D3DXGetShaderConstantTableEx byte_code 0A000000, flags 0, constant_table " \
     "0B000000."
SIZE = "0024:trace:d3dx:D3DXFindShaderComment Returning comment data at 0A5C0010 with size {}"
HEAD = ('0024:trace:d3dx:D3DXGetShaderConstantTableEx Creator "made up", Version 0x{:x}, '
        'Constants {}, Target "vs_3_0".')


def numbered(lines: list[str]) -> list[tuple[int, str]]:
    return list(enumerate(lines, 1))


def test_each_game_call_has_the_header_after_it_and_another_callers_is_left_out():
    lines = [CALL, EX, SIZE.format(136), HEAD.format(0xFFFE0300, 1),
             # the Ex function on the same thread with no call of the game's before it
             EX, SIZE.format(64), HEAD.format(0xFFFE0300, 0),
             CALL, EX, SIZE.format(264), HEAD.format(0xFFFF0300, 4)]
    assert K.call_headers(numbered(lines)) == (
        [("0xfffe0300", 1, 136), ("0xffff0300", 4, 264)], [])


def test_a_call_without_its_header_or_size_is_a_problem():
    heads, problems = K.call_headers(numbered([CALL, EX, CALL, EX, SIZE.format(136),
                                               HEAD.format(0xFFFE0300, 1), CALL]))
    assert heads == [("0xfffe0300", 1, 136)]
    assert problems == ["line 1: a call with no table header before the next call",
                        "line 7: a call with no table header after it"]
    heads, problems = K.call_headers(numbered([CALL, EX, HEAD.format(0xFFFE0300, 1)]))
    assert heads == []
    assert problems == ["line 3: the header of the call at line 1 has no comment size before it"]


def test_the_native_headers_are_the_game_calls_in_both_traced_logs():
    """The outcome's two traced logs, where they are on disk: 26 calls on the
    main thread, each with its header, the same in both."""
    rec = K.NO.load(K.NO.RECEIPT)
    out = next(o for o in rec["outcomes"] if o["id"] == K.OUTCOME)
    logs = sorted({c["log"] for c in out["counts"]})
    if not all((K.ROOT / log).is_file() for log in logs):
        pytest.skip(f"the traced logs are not on disk: {logs}")
    heads, problems = K.native_headers()
    assert problems == []
    assert len(heads) == 26


# ------------------------------------------------- the demo arm's switch

def test_the_run_spec_override_replaces_one_setting_and_keeps_the_rest():
    cmd = ["py", "loop_run.py", "--run", "off|A=1|SA2_D3DX_CONSTANT_TABLE=1|B=x=y", "--hook", "h"]
    assert K.override_run_env(cmd, {"SA2_D3DX_CONSTANT_TABLE": "0"}) == [
        "py", "loop_run.py", "--run", "off|A=1|SA2_D3DX_CONSTANT_TABLE=0|B=x=y", "--hook", "h"]


def test_the_run_spec_override_refuses_what_it_cannot_write_back():
    with pytest.raises(SystemExit, match="2 runs"):
        K.override_run_env(["--run", "a|A=1", "--run", "b|A=1"], {"A": "0"})
    with pytest.raises(SystemExit, match="separator"):
        K.override_run_env(["--run", "a|A=1"], {"A": "0|B=1"})


def test_the_demo_arm_sets_the_switch_itself_once_reach_env_carries_it(monkeypatch, tmp_path):
    """After the switch-on push reach_receipt.ENV sets SA2_D3DX_CONSTANT_TABLE,
    and demo_run.command refuses an extra setting that names one of its own; so
    the switch must not reach it as extra_env, and the off arm must still run
    with the switch off."""
    seen = {}

    def fake_command(polls, profile, timeout, *, name, label, out, extra_env):
        seen["extra"] = dict(extra_env)
        env = dict(K.RR.ENV, SA2_D3DX_CONSTANT_TABLE="1", **extra_env)
        return (["py", "loop_run.py", "--name", name, "--run",
                 "|".join([label] + [f"{k}={v}" for k, v in env.items()])], None, None, None)

    monkeypatch.setattr(K.D, "command", fake_command)
    a = type("A", (), {"out": K.ROOT / "private" / "generated" / "s4" / "d3dx-table-test",
                       "build_suffix": "", "demo_polls": 5, "demo_profile": "default",
                       "timeout": 60})()
    for arm, want in (("off", "0"), ("on", "1")):
        cmd = K.command("demo", arm, "", a, build=True)
        assert "SA2_D3DX_CONSTANT_TABLE" not in seen["extra"]
        spec = dict(kv.split("=", 1) for kv in cmd[cmd.index("--run") + 1].split("|")[1:])
        assert spec["SA2_D3DX_CONSTANT_TABLE"] == want
        assert spec["SA2_D3D9_BREAK"] == ""


# ------------------------------------------------------------ the receipt

def results(verdict_holds: bool = True, runtime: str = "r" * 64) -> dict:
    v = {k: {"holds": True, "why": []} for k in ("one_binary", "ticks_identical")}
    v["ticks_identical"]["holds"] = verdict_holds
    return {s: {"scenario": s, "build": K.BUILDS[s], "polls": 5,
                "inputs": {"runtime_sha256": runtime, "pipeline": {"tools/s4/x.py": "p" * 64},
                           "native": {s: "n" * 64}},
                "arms": {}, "against_native": {}, "verdicts": {"on": v}}
            for s in K.BUILDS}


ARGS = type("A", (), {"arms": ["off", "on"], "brk": ["tablekeep"]})()


def test_the_receipt_keeps_the_shared_inputs_once_and_each_scenarios_own():
    got = K.receipt(results(), ARGS, {"commit": "c" * 40, "uncommitted_changes": False})
    assert got["id"] == "s4-d3dx-table-v1"
    assert got["inputs"] == {"runtime_sha256": "r" * 64, "pipeline": {"tools/s4/x.py": "p" * 64}}
    assert got["scenarios"]["demo"]["inputs"] == {"native": {"demo": "n" * 64}}
    assert got["command"].endswith("--write --break tablekeep")
    assert got["answer"].startswith("Yes, in every scenario: one_binary; ticks_identical hold")


def test_the_receipt_is_refused_unless_everything_held():
    with pytest.raises(SystemExit, match="boot: ticks_identical"):
        K.receipt(results(verdict_holds=False), ARGS, {})
    partial = results()
    del partial["mh"]
    with pytest.raises(SystemExit, match="missing \\['mh'\\]"):
        K.receipt(partial, ARGS, {})
    moved = results()
    moved["demo"]["inputs"]["runtime_sha256"] = "s" * 64
    with pytest.raises(SystemExit, match="different trees"):
        K.receipt(moved, ARGS, {})


# ------------------------------- the runs a scenario stands for (P6's review, W2 and N5)
#
# The boot scenario is the boot replay's run, so it runs on the boot capture's
# device profile; every arm starts from the pinned saves and an empty overlay,
# runs without the shell's device variables, and is refused unless its record
# shows the profile it was handed and every switch its row turns on.

PROFILE = {"path": "private/captures/c/sa2-device-profile.txt", "sha256": "a" * 64,
           "counts": {}, "guest": {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"}}


def arm_args(tmp_path: Path, boot_profile=K.DP.DEFAULT):
    return type("A", (), {"out": tmp_path / "out", "build_suffix": "", "timeout": 60,
                          "boot_profile": boot_profile, "demo_profile": None, "mh_polls": 5,
                          "input": tmp_path / "sa2-input.csv"})()


def spec(cmd: list[str]) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in cmd[cmd.index("--run") + 1].split("|")[1:])


def test_the_boot_arms_run_on_the_boot_capture_s_profile_with_every_device_on(
        monkeypatch, tmp_path):
    monkeypatch.setattr(K, "ROOT", tmp_path)
    for arm, want in (("off", "0"), ("on", "1")):
        got = spec(K.command("boot", arm, "", arm_args(tmp_path, PROFILE), build=True))
        assert got["SA2_DEVICE_PROFILE"] == PROFILE["path"]
        assert got["SA2_D3DX_CONSTANT_TABLE"] == want
        assert {k: got[k] for k in K.RR.DEVICES if k != "SA2_D3DX_CONSTANT_TABLE"} == {
            k: "1" for k in K.RR.DEVICES if k != "SA2_D3DX_CONSTANT_TABLE"}
    assert "SA2_DEVICE_PROFILE" not in spec(K.command("boot", "on", "", arm_args(tmp_path),
                                                      build=True))


def arm_run(tmp_path: Path, monkeypatch, record: dict):
    """run_arm under a pinned tree at tmp_path, with loop_run replaced by one
    that writes `record`, and a matrix whose row is the arm's own settings."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_lifted_saves import pinned_tree
    pinned_tree(tmp_path)
    monkeypatch.setattr(K, "ROOT", tmp_path)
    monkeypatch.setattr(K.SR, "RUNDIR", tmp_path / "run")
    (tmp_path / "run").mkdir(exist_ok=True)
    for var in K.RR.DEVICE_VARS:
        monkeypatch.delenv(var, raising=False)
    seen = []

    def fake(cmd, **kw):
        seen.append(kw["env"])
        name = cmd[cmd.index("--name") + 1]
        label, *kvs = cmd[cmd.index("--run") + 1].split("|")
        (tmp_path / "run" / f"{name}.{label}.json").write_text(json.dumps(record))
        (tmp_path / "run" / f"{name}.matrix.json").write_text(json.dumps(
            {"sha256": "b" * 64,
             "rows": [{"label": label, "env": dict(kv.split("=", 1) for kv in kvs)}]}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(K.subprocess, "run", fake)
    return seen


def test_an_arm_holds_its_record_to_the_profile_and_the_switches_it_was_handed(
        tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_lifted_saves import devices_on
    rec = devices_on({"device_profile": {"default": True, "source": "default", "sha256": ""}})
    seen = arm_run(tmp_path, monkeypatch, rec)
    monkeypatch.setenv("SA2_D3D9_BREAK", "tablekeep")        # a shell's break never reaches it
    got = K.run_arm("mh", "on", "", arm_args(tmp_path), build=False)
    assert got["device_profile_read"] == "default"
    assert got["saves"]["left"] == {} and got["saves"]["installation_matches_pin"] is True
    assert "SA2_D3D9_BREAK" not in seen[0]
    # the off arm turns the table off itself and is held to the other switches
    rec["d3d9"]["constant_tables"]["switch"] = False
    arm_run(tmp_path, monkeypatch, rec)
    assert K.run_arm("mh", "off", "", arm_args(tmp_path), build=False)["arm"] == "off"
    with pytest.raises(SystemExit, match=r"D3DX_TABLE_REFUSED mh/on: its devices are not its "
                                         r"configuration's: SA2_D3DX_CONSTANT_TABLE=1"):
        K.run_arm("mh", "on", "", arm_args(tmp_path), build=False)
    rec["d3d9"]["constant_tables"]["switch"] = True
    rec["ddraw"]["device_enabled"] = False
    arm_run(tmp_path, monkeypatch, rec)
    with pytest.raises(SystemExit, match=r"mh/off: its devices are not its configuration's: "
                                         r"SA2_DDRAW_DEVICE=1"):
        K.run_arm("mh", "off", "", arm_args(tmp_path), build=False)
    rec["ddraw"]["device_enabled"] = True
    arm_run(tmp_path, monkeypatch, rec)
    with pytest.raises(SystemExit, match=r"D3DX_TABLE_REFUSED boot/on: the run is not the one "
                                         r"asked for: the run read device profile default"):
        K.run_arm("boot", "on", "", arm_args(tmp_path, PROFILE), build=False)


def test_an_arm_does_not_start_off_the_pinned_saves_or_from_a_used_overlay(tmp_path,
                                                                           monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_lifted_saves import devices_on
    arm_run(tmp_path, monkeypatch, devices_on({}))
    kept = tmp_path / "private/generated/s4/vfsroot/Config/UserConfig.cfg"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"cfg")
    monkeypatch.setattr(K.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit, match=r"D3DX_TABLE_REFUSED mh/on: the write overlay "
                                         r"private/generated/s4/vfsroot still holds Config"):
        K.run_arm("mh", "on", "", arm_args(tmp_path), build=False)
    kept.unlink()
    (tmp_path / "private/install/resource/gd_PC/SAVEDATA/SONIC2B__S01").write_bytes(b"")
    with pytest.raises(SystemExit, match=r"D3DX_TABLE_REFUSED mh/on: the saves in "):
        K.run_arm("mh", "on", "", arm_args(tmp_path), build=False)


@pytest.mark.skipif(not (K.ROOT / "Documentation" / "replays.json").is_file(),
                    reason="no replay registry")
def test_the_boot_scenario_runs_on_the_registered_boot_capture_s_profile():
    """boot_profile, the step main takes for the boot scenario, on the real registry: the
    boot scenario gets the boot replay's capture profile, not the default (the P6 fold
    review, W3). main's own call to it is not exercised here (the mask review, N3)."""
    reg = json.loads((K.ROOT / "Documentation" / "replays.json").read_text())
    boot = [e for e in reg["replays"] if e["name"] == "boot"]
    if not boot or not boot[0]["native"].get("device_profile"):
        pytest.skip("the registered boot capture records no device profile")
    got = K.boot_profile(["boot"], boot)
    assert got == K.TR.native_device_profile(boot[0]) and got != K.DP.DEFAULT
    assert K.boot_profile(["mh"], boot) == K.DP.DEFAULT
    with pytest.raises(SystemExit, match="no replay named boot"):
        K.boot_profile(["boot"], [])


def test_an_arm_with_no_matrix_row_is_refused(tmp_path, monkeypatch):
    """The mask review's N2 (its own probe): loop_run's matrix with no row for the arm.

    Before the fold an absent row read as no switches, so an "on" arm whose run had
    the switch off passed as long as the record said off too."""
    from test_lifted_saves import devices_on
    rec = devices_on({"device_profile": {"default": True, "source": "default", "sha256": ""}})
    rec["d3d9"]["constant_tables"]["switch"] = False      # a run whose switch is OFF
    arm_run(tmp_path, monkeypatch, rec)

    def fake(cmd, **kw):
        name = cmd[cmd.index("--name") + 1]
        label = cmd[cmd.index("--run") + 1].split("|")[0]
        (tmp_path / "run" / f"{name}.{label}.json").write_text(json.dumps(rec))
        (tmp_path / "run" / f"{name}.matrix.json").write_text(json.dumps(
            {"sha256": "b" * 64, "rows": [{"label": "someone-else", "env": {}}]}))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(K.subprocess, "run", fake)
    with pytest.raises(SystemExit, match=r"D3DX_TABLE_REFUSED mh/on: loop_run's matrix has no "
                                         r"row for this arm"):
        K.run_arm("mh", "on", "", arm_args(tmp_path), build=False)
