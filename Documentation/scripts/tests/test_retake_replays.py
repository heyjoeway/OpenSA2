"""tools/oracle/retake_replays.py, driven with synthetic recordings and re-takes.

The re-take tool replays the user's recording again under the probes now in the
tree, so the native side a stage replay registers is current and its import log
covers the whole replay. Each case here is a way that could go wrong quietly: a
re-take that no longer follows the recorded run, re-takes that differ from each
other, an import log counted short of the replay or past it, a probe built other
than the tree builds it, import logs a registration would refuse, a recording
edited after its run or re-taken while its session may still replay it, a dry
run that shows one command and runs another.

Every check the verdict holds is made to fail on a constructed input, one at a
time (test_each_check_fails_by_name_on_its_own_constructed_input), and so is
every refusal before a run (test_a_recording_that_cannot_be_re_taken_stops_before_any_run).

Under option A (the user's ruling of 2026-09-24) every run starts from the
pinned saves and is launched with the recording's game environment, as
record_stage requires of every replay: the recording and each re-take record
both here as the runner does, a made-up pin stands for
Documentation/save-state.json, and a made-up environment for this shell's.

No runner, game or Wine process runs: record_stage.run, oracle_mode's two
checks, the device-profile reader, the executable's import table and its pin
are replaced. The probe builds in the receipts are hashed from the tree, as the
runner hashes them; every other value -- slot, import name, device profile,
executable hash, capture value, time -- is made up, none copied from the
executable or a capture.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import retake_replays as T  # noqa: E402

R = T.R

REC = "mh-record-t"
END, GOAL = 9, 4               # the recording ends on tick 9, so it captured ticks 1 to 8
LAST = END - 1
PROFILE = {"file": "sa2-device-profile.txt", "sha256": "1" * 64,
           "counts": {"gamectrl": 1, "xinput_connected": 0, "wmi_pnp": 3, "ddraw": 2}}
OTHER_PROFILE = dict(PROFILE, sha256="2" * 64)
EXE_PIN = "e" * 64             # the executable's pin in the replaced tools/pins.json
SLOTS = {0x00A00000: "one.dll!first", 0x00A00004: "one.dll!second", 0x00A00008: "two.dll!third"}
CALLS = {0x00A00000: 5, 0x00A00004: 0, 0x00A00008: 2}
TICK_HEADER = ["tick", "frame_count", "game_mode", "d_01af0000", "d_01b00000", "d_01760000"]
TRACE_HEADER = "tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z"
PLAYER = "0x0a0b0c0d"
VANILLA_CSV = ("va,length,loader_bytes,file_bytes,action\n"
               "0x00500100,5,e911111111,aabbccdd01,kept\n"
               "0x00500200,5,e922222222,aabbccdd02,restored\n"
               "0x00900010,4,33333333,44444444,iat-kept\n"
               "0x00500200,5,,,tick1-intact\n")
NO_RANGES = VANILLA_CSV.split("\n", 1)[0] + "\n"
INPUT_HEADER = "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old"
VERDICT = "replays-20260923-000000.json"
# The import probe's define before it counted each run to its end: a fixed tick.
FIXED_TICK = {"import_probe": ["TICK_LIMIT=599"]}
# Option A: the pinned start saves (made up), the saves a pickup would leave,
# and the game environment every run here is launched with unless a test says
# otherwise (record_stage's tests use the same shapes).
SAVES = {"SONIC2B__ALF": "a" * 64}
AFTER_PICKUP = {**SAVES, "SONIC2B__S01": "b" * 64}
GAME_ENV = {"HOME": "/home/someone", "DISPLAY": ":9", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"}
OTHER_ENV = {**GAME_ENV, "LANG": "en_GB.UTF-8"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_rows() -> list[list[int]]:
    """One controller update per tick from tick 0 to the recording's last, and a
    second on tick 5: the right arrow held from tick 3, a stick from tick 5."""
    rows = []
    for t in range(END):
        for o in ((0, 1) if t == 5 else (0,)):
            on = 0x400 if t >= 3 else 0
            rows.append([t, o, on, ~on & 0xFFFFFFFF, 0x400 if t == 3 else 0, 0, 0, 0, 0,
                         0xFF81 if t >= 5 else 0, 0, 0, 0x400 if t >= 4 else 0])
    return rows


def row_text(r: list[int]) -> str:
    return f"{r[0]},{r[1]}," + ",".join(f"0x{v:x}" for v in r[2:])


def built(input_defines, change: dict | None = None,
          defines: dict | None = None) -> list[dict]:
    """The receipt's built_probes for record_stage's probe set, from the tree,
    with record_stage.DEFINES unless `defines` stands in for them."""
    out = []
    for mod, source in R.PROBES + ((R.PROFILE_MOD, R.DP.PROBE),):
        path = ROOT / source
        entry = {"mod_id": mod, "source": str(path), "source_sha256": sha(path),
                 "headers_sha256": {h.name: sha(h) for h in sorted(path.parent.glob("*.h"))},
                 "defines": (list(input_defines) if mod == "input_probe"
                             else list((R.DEFINES if defines is None else defines)
                                       .get(mod, []))),
                 "dll_sha256": "d" * 64}
        entry.update((change or {}).get(mod, {}))
        out.append(entry)
    return out


def capture(name: str, *, mode: str, input_defines=None, goal: int = GOAL, end: int = END,
            misses: int = 0, rc: int = 0, elapsed: float = 15.0,
            profile: dict | None = PROFILE, builds: list[dict] | None = None,
            exe: str = EXE_PIN,
            tick_change: dict | None = None, trace_change: dict | None = None,
            trace_header: str = TRACE_HEADER, trace_ticks: int | None = None,
            player: str = PLAYER, vanilla_csv: str = VANILLA_CSV,
            applied: list[str] | None = None, applied_header: list[str] | None = None,
            import_at: int | None = None, calls: dict | None = None,
            import_rows: list[str] | None = None, collected: dict | None = None,
            verdict: bool = True, saves: dict | None = SAVES,
            saves_at_end: dict | None = None, environment: dict | None = GAME_ENV,
            tick_rows: int | None = None) -> dict:
    """A capture directory as the runner leaves one, and its receipt: the
    recording (mode "record") or a re-take of it (mode "replay"). Its import
    log is counted on its last captured tick, as record_stage's import probe
    counts it, unless `import_at` says otherwise. The receipt records each
    file's sha256 as the runner collected it, `collected` standing in for
    any; a recording also holds its session's verdict unless `verdict` is
    false. `saves` is what the runner records as the saves the run started
    from and `saves_at_end` what it ended with, and `environment` the game
    environment it was launched with; None leaves each record out. `tick_rows`
    shortens or lengthens the tick capture alone."""
    d = R.CAPTURES / name
    d.mkdir(parents=True)
    ticks = END - 1
    lines = [",".join(TICK_HEADER)]
    for t in range(1, (ticks if tick_rows is None else tick_rows) + 1):
        row = {"tick": str(t), "frame_count": f"0x{t:08x}", "game_mode": "0x00000005",
               "d_01af0000": "0xaaaaaaaa", "d_01b00000": "0xbbbbbbbb", "d_01760000": "0xcccccccc"}
        row.update({c: v for (k, c), v in (tick_change or {}).items() if k == t})
        lines.append(",".join(row[c] for c in TICK_HEADER))
    (d / "sa2-tick-capture.csv").write_text("\n".join(lines) + "\n")
    trace = [trace_header]
    for t in range(1, (ticks if trace_ticks is None else trace_ticks) + 1):
        row = {"tick": str(t), "current_level": "0x000a", "game_mode": "0x00000005",
               "game_state": "0x0010" if t >= 3 else "0x0007", "ingame_frames": f"0x{t:08x}",
               "player": player, "x": f"0x{0x1000 + t:08x}", "y": "0x22222222",
               "z": "0x33333333"}
        row.update({c: v for (k, c), v in (trace_change or {}).items() if k == t})
        trace.append(",".join(row[c] for c in TRACE_HEADER.split(",")))
    (d / "sa2-stage-trace.csv").write_text("\n".join(trace) + "\n")
    (d / "sa2-input-end.csv").write_text(f"goal_tick,end_tick,misses\n{goal},{end},{misses}\n")
    (d / "sa2-vanilla.csv").write_text(vanilla_csv)
    at = ticks if import_at is None else import_at
    body = import_rows if import_rows is not None else [
        f"0x{va:08x},{(calls or CALLS)[va]},1,{at}" for va in SLOTS]
    (d / "sa2-import-calls.csv").write_text("slot,calls,ours,tick\n" + "\n".join(body) + "\n")
    if mode == "record":
        (d / "sa2-input.csv").write_text(
            INPUT_HEADER + "\n" + "\n".join(row_text(r) for r in input_rows()) + "\n")
        files, defines = R.RECORD_CAPTURES, input_defines or ["INPUT_MODE=1"]
        if verdict:
            (d / VERDICT).write_text(json.dumps({"replays_agree": True}) + "\n")
    else:
        body = applied if applied is not None else [
            f"{r[0]},{r[1]},1," + row_text(r).split(",", 2)[2] for r in input_rows()]
        (d / "sa2-input-applied.csv").write_text(
            ",".join(applied_header or T.APPLIED_HEADER) + "\n" + "\n".join(body) + "\n")
        files, defines = R.REPLAY_CAPTURES, input_defines or ["INPUT_MODE=2"]
    arts = [{"file": f, "present": True,
             "check": {"ok": True, "problems": [],
                       "sha256": (collected or {}).get(f, sha(d / f))}} for f in files]
    arts.append({"file": "sa2-device-profile.txt", "present": True, "device_profile": True,
                 "check": {"ok": True, "problems": [], "sha256": (profile or {}).get("sha256")}})
    rec = {"inputs": {"built_probes": builds or built(defines),
                      "timeout_seconds": R.CAP, "executable_sha256": exe},
           "run": {"return_code": rc, "timed_out": rc == 124, "elapsed_seconds": elapsed},
           "installation_untouched": True, "artifacts": arts,
           "session": {"device_profile": profile, "host_game_controllers": []}}
    if environment is not None:
        rec["session"]["environment"] = R.RP.environment_record(environment)
        rec["session"]["game_environment"] = dict(environment)
    if saves is not None:
        rec["session"]["saves"] = {"at_start": saves}
    if saves_at_end is not None:
        rec["session"].setdefault("saves", {})["at_end"] = saves_at_end
    (d / "receipt.json").write_text(json.dumps(rec))
    return rec


def write_pin(path: Path, files: dict) -> Path:
    """A save-state pin as tools/oracle/save_state.py reads one, pinning `files`."""
    path.write_text(json.dumps({"schema": R.SS.SCHEMA, "directory": "resource/gd_PC/SAVEDATA",
                                "files": files, "digest": R.SS.digest(files),
                                "state": "a made-up start", "why": "a test's",
                                "restore": R.SS.RESTORE}))
    return path


@pytest.fixture
def world(tmp_path, monkeypatch) -> dict:
    """Captures and scripts under tmp_path; oracle_mode's vanilla check answering
    from the returned dict (capture name -> problems) and its mode comparison
    agreeing, the device-profile reader taking every capture, made-up import
    slots, and a tools/pins.json pinning the made-up executable. Their own tests
    are tools/oracle/test_oracle_mode.py, test_device_profile.py and
    test_native_imports.py. The pinned start saves are SAVES, and a run this
    process starts would get GAME_ENV. The stage's own mask is looked up under
    tmp_path, so whether the tree has one (harness/replay/masks/metalharbor.h
    since U6) changes nothing here; a test that wants one writes it there."""
    monkeypatch.setattr(R, "CAPTURES", tmp_path / "captures")
    monkeypatch.setattr(T, "MASKS", tmp_path / "masks")
    monkeypatch.setattr(R, "SCRIPTS", tmp_path / "scripts")
    monkeypatch.setattr(R, "PIN", write_pin(tmp_path / "save-state.json", SAVES))
    monkeypatch.setattr(R, "launch_environment", lambda: dict(GAME_ENV))
    (tmp_path / "captures").mkdir()
    problems: dict[str, list[str]] = {}
    monkeypatch.setattr(R.OM, "vanilla_problems", lambda d: list(problems.get(Path(d).name, [])))
    monkeypatch.setattr(R.OM, "require_same", lambda caps, *a, **k: {"mode": "vanilla"})
    monkeypatch.setattr(T.DP, "capture_profile",
                        lambda d, root=None: {"sha256": PROFILE["sha256"]})
    monkeypatch.setattr(T, "slot_table", lambda: dict(SLOTS))
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"executables": {"sonic2app-unpacked.exe": {"sha256": EXE_PIN}}}))
    monkeypatch.setattr(T.NI, "PINS", pins)
    # No recording session runs here, whatever runs on this machine
    # (test_a_live_recording_session_stops_the_re_takes holds the check).
    monkeypatch.setattr(T, "live_sessions", lambda: [])
    return problems


def fake_run(monkeypatch, specs) -> list[dict]:
    """record_stage.run, making each re-take with capture(); `specs` is capture()'s
    keyword arguments for every re-take, or a list of them, one per call, where
    an exception is raised instead."""
    calls = []

    def fake(name, input_defines, captures, timeout, lock_timeout=None):
        calls.append({"name": name, "input_defines": list(input_defines), "captures": captures,
                      "timeout": timeout, "lock_timeout": lock_timeout})
        spec = specs[len(calls) - 1] if isinstance(specs, list) else specs
        if isinstance(spec, BaseException):
            raise spec
        return capture(name, mode="replay", input_defines=input_defines, **spec)

    monkeypatch.setattr(R, "run", fake)
    return calls


def summary() -> dict:
    [path] = (R.CAPTURES / REC).glob("retakes-*.json")
    return json.loads(path.read_text())


def failed_checks() -> list[str]:
    return [k for k, v in summary()["checks"].items() if not v]


def no_runner(monkeypatch) -> None:
    def refuse(*a, **k):
        raise AssertionError("the runner ran")

    monkeypatch.setattr(R.subprocess, "run", refuse)
    monkeypatch.setattr(R, "run", refuse)


# ------------------------------------------------------------ the good case


def test_two_faithful_re_takes_pass_with_the_import_log_at_the_last_tick(world, monkeypatch,
                                                                         capsys):
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, {})
    assert T.main([REC]) == 0
    out = capsys.readouterr().out
    s = summary()
    assert s["verdict"] == "RETAKE_OK" and all(s["checks"].values())
    assert list(s["checks"]) == [
        "every_retake_ran", "runs_the_games_own_code", "built_as_the_tree_builds_now",
        "device_profile_reproducible", "same_oracle_mode_as_recording",
        "same_code_restored_as_recording", "same_probe_set_as_recording",
        "same_device_profile_as_recording", "start_saves_pinned",
        "same_game_environment_as_recording",
        "goal_on_recording_tick", "no_missed_rows", "every_recorded_row_applied",
        "trajectory_as_recorded", "named_columns_as_recorded", "import_log_at_last_tick",
        "import_logs_call_one_set",
        "stage_traces_identical", "tick_captures_identical", "import_logs_registrable"]
    assert s["reported"]["stage_route_item_5_met"] is True
    assert s["import_tick"] == LAST == 8
    header = R.SCRIPTS / f"{REC}.h"
    assert [c["input_defines"] for c in calls] == [
        ["INPUT_MODE=2", f'INPUT_SCRIPT="{header}"', f"TICK_LIMIT={END + 60}"]] * 2
    assert [(c["timeout"], c["lock_timeout"], c["captures"]) for c in calls] == [
        (R.CAP, R.LOCK_WAIT, R.REPLAY_CAPTURES)] * 2
    assert [c["name"].rsplit("-", 2)[0] for c in calls] == [f"{REC}-retake1", f"{REC}-retake2"]
    assert header.is_file() and "{5,1," in header.read_text()
    one, two = (c["name"] for c in calls)
    first = s["retakes"][one]
    assert first["import_log"]["counted_at_tick"] == 8 == first["import_log"]["tick_rows"]
    assert first["import_log"]["called"] == ["one.dll!first", "two.dll!third"]
    native = s["reported"]["registration"]["native"]
    assert (native["capture"], native["receipt"]) == (
        str(R.CAPTURES / one / "sa2-tick-capture.csv"), str(R.CAPTURES / one / "receipt.json"))
    assert [r["capture"] for r in native["repeats"]] == [
        str(R.CAPTURES / two / "sa2-tick-capture.csv")]
    assert native["imports"]["log"] == str(R.CAPTURES / one / "sa2-import-calls.csv")
    assert [r["log"] for r in native["imports"]["repeats"]] == [
        str(R.CAPTURES / two / "sa2-import-calls.csv")]
    assert s["moved_since_recording"] == [] and s["cap_warning"] is None
    assert (f"RETAKE_OK: 2 re-takes of {REC} are byte-identical to each other and follow the "
            "recorded run to the goal on tick 4; stage-route item 5 holds on them.") in out
    assert "WARNING" not in out


def test_a_recording_the_tree_s_probes_moved_away_from_is_re_taken_where_replay_only_refuses(
        world, monkeypatch, capsys):
    """The reason the tool exists: the tick probe changed after the user played,
    so record_stage --replay-only stops, and the re-takes, built from the tree,
    still pass and say what moved."""
    capture(REC, mode="record", builds=built(["INPUT_MODE=1"],
                                             {"tick_probe": {"source_sha256": "0" * 64}}))
    # A recording with its word log, so what stops --replay-only is the probe
    # that moved (one from before the word log stops earlier, as the next test).
    edit_receipt(REC, word_log={"ok": True, "stored_as": "sa2-word-log.bin.zst"})
    fake_run(monkeypatch, {})
    assert R.main(["--replay-only", REC]) == 1
    assert ("cannot be replayed with the tree as it is now. These files changed since it was "
            "recorded: harness/replay/native_tick_probe.c.") in capsys.readouterr().out
    assert T.main([REC]) == 0
    s = summary()
    assert s["verdict"] == "RETAKE_OK"
    assert s["moved_since_recording"] == ["harness/replay/native_tick_probe.c"]


def test_the_player_address_is_compared_with_the_recording_only_as_null_or_not(world,
                                                                               monkeypatch):
    """The heap may lie elsewhere under other probe DLLs; the trajectory may
    not. Between the re-takes, one configuration, the address is compared."""
    capture(REC, mode="record")
    fake_run(monkeypatch, {"player": "0x0a0b0f00"})
    assert T.main([REC]) == 0
    assert summary()["checks"]["trajectory_as_recorded"] is True


# ------------------------------------------------------------ every check fails


def vanilla_for_retakes(world, monkeypatch):
    monkeypatch.setattr(R.OM, "vanilla_problems",
                        lambda d: ["a tick1-rewritten row at 0x00500200"]
                        if "-retake" in Path(d).name else [])


def profile_refused(world, monkeypatch):
    def refuse(d, root=None):
        raise T.DP.Refusal(f"{Path(d).name} was taken with a native_device_profile.c that is "
                           "not the one in the tree")

    monkeypatch.setattr(T.DP, "capture_profile", refuse)


def modes_differ(world, monkeypatch):
    """oracle_mode.require_same answering from each capture's own mode, as the
    real one does, and refusing only captures whose modes differ: the recording
    in one mode, every re-take in another. The re-takes agree with each other,
    so a check that asked about a re-take alone, or about the re-takes
    together, would pass."""
    mode = {REC: "vanilla, one Mod Loader profile"}

    def require_same(caps, *a, **k):
        got = {mode.get(Path(c).name, "vanilla, another Mod Loader profile") for c in caps}
        if len(got) > 1:
            raise R.OM.ModeError("these captures were produced by different oracle modes")
        return {"mode": got.pop()}

    monkeypatch.setattr(R.OM, "require_same", require_same)


APPLIED_ALL = [f"{r[0]},{r[1]},1," + row_text(r).split(",", 2)[2] for r in input_rows()]
STOP = R.Stop("r2: the run was not accepted (exit 1).")
IMPORT_ROWS = [f"0x{va:08x},{CALLS[va]},1,{LAST}" for va in SLOTS]


@pytest.mark.parametrize("specs, patch, failed", [
    # the runner rejects the second re-take: nothing is compared across the series
    ([{}, STOP], None,
     ["every_retake_ran", "import_logs_call_one_set", "stage_traces_identical",
      "tick_captures_identical", "import_logs_registrable"]),
    ({}, vanilla_for_retakes, ["runs_the_games_own_code"]),
    ({"builds": built(["INPUT_MODE=2"], {"tick_probe": {"source_sha256": "0" * 64}})},
     None, ["built_as_the_tree_builds_now"]),
    # built with the import probe's old fixed tick, however the log reads
    ({"builds": built(["INPUT_MODE=2"], defines=FIXED_TICK)}, None,
     ["built_as_the_tree_builds_now"]),
    ({}, profile_refused, ["device_profile_reproducible"]),
    ({}, modes_differ, ["same_oracle_mode_as_recording"]),
    ({"vanilla_csv": VANILLA_CSV.replace(",restored", ",kept")}, None,
     ["same_code_restored_as_recording"]),
    ({"profile": OTHER_PROFILE}, None, ["same_device_profile_as_recording"]),
    # option A: a re-take from other saves, one that records none, one launched
    # with another game environment, one that records none
    ({"saves": AFTER_PICKUP}, None, ["start_saves_pinned"]),
    ({"saves": None}, None, ["start_saves_pinned"]),
    ({"environment": OTHER_ENV}, None, ["same_game_environment_as_recording"]),
    ({"environment": None}, None, ["same_game_environment_as_recording"]),
    ({"goal": 5}, None, ["goal_on_recording_tick"]),
    ({"misses": 2, "applied": APPLIED_ALL + ["9,0,0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0",
                                             "9,1,0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0"]},
     None, ["no_missed_rows"]),
    # the game made one call fewer on tick 8: a row skipped, no miss counted
    ({"applied": APPLIED_ALL[:-1]}, None, ["every_recorded_row_applied"]),
    # a value the recording does not hold was applied
    ({"applied": APPLIED_ALL[:3] + [APPLIED_ALL[3].replace("0x400", "0x800", 1)]
      + APPLIED_ALL[4:]}, None, ["every_recorded_row_applied"]),
    # tick 5's two calls took each other's rows: the same rows, another order
    ({"applied": APPLIED_ALL[:5] + [APPLIED_ALL[6], APPLIED_ALL[5]] + APPLIED_ALL[7:]}, None,
     ["every_recorded_row_applied"]),
    # every row as recorded, under a header the input probe does not write
    ({"applied_header": ["tick", "ord", "hit"] + R.FIELDS}, None,
     ["every_recorded_row_applied"]),
    ({"trace_change": {(6, "x"): "0x00002000"}}, None, ["trajectory_as_recorded"]),
    ({"player": "0x00000000"}, None, ["trajectory_as_recorded"]),
    # every row it has follows the recording, and it stops a row short
    ({"trace_ticks": LAST - 1}, None, ["trajectory_as_recorded"]),
    # every recorded row followed, and one more
    ({"trace_ticks": LAST + 1}, None, ["trajectory_as_recorded"]),
    # every row as recorded, under another header
    ({"trace_header": TRACE_HEADER.replace("ingame_frames", "frame_counter")}, None,
     ["trajectory_as_recorded"]),
    # both re-takes hold another game mode than the recording on tick 3: a named
    # column no probe's build decides (the re-take review, W1)
    ({"tick_change": {(3, "game_mode"): "0x00000006"}}, None, ["named_columns_as_recorded"]),
    # both re-takes a row short of the recording, every row they hold as recorded
    # and the import log on their own last tick
    ({"tick_rows": LAST - 1, "import_at": LAST - 1}, None, ["named_columns_as_recorded"]),
    ({"import_at": LAST - 1}, None, ["import_log_at_last_tick", "import_logs_registrable"]),
    # the log written on the exit tick, which the capture holds no row for: what
    # TICK_LIMIT = end_tick would write, since the import probe's OnFrame runs
    # before the input probe's ExitProcess
    ({"import_at": LAST + 1}, None, ["import_log_at_last_tick", "import_logs_registrable"]),
    # a slot missing: not a log a registration reads, so its set cannot be compared either
    ({"import_rows": IMPORT_ROWS[:-1]}, None,
     ["import_log_at_last_tick", "import_logs_call_one_set", "import_logs_registrable"]),
    # four fields, so the runner's capture check passes it, and a tick that is no number
    ({"import_rows": IMPORT_ROWS[:-1] + [IMPORT_ROWS[-1].rsplit(",", 1)[0] + ","]}, None,
     ["import_log_at_last_tick", "import_logs_call_one_set", "import_logs_registrable"]),
    ([{}, {"calls": {**CALLS, 0x00A00004: 1}}], None,
     ["import_logs_call_one_set", "import_logs_registrable"]),
    ([{}, {"player": "0x0a0b0f00"}], None, ["stage_traces_identical"]),
    ([{}, {"tick_change": {(3, "d_01b00000"): "0x12345678"}}], None,
     ["tick_captures_identical", "import_logs_registrable"]),
    # a run of another executable than the one tools/pins.json pins
    ({"exe": "0" * 64}, None, ["import_logs_registrable"]),
    # the import log on disk is not the file its run collected
    ({"collected": {"sa2-import-calls.csv": "0" * 64}}, None, ["import_logs_registrable"]),
], ids=["runner-rejects", "loader-code", "other-probe", "recorder-import-tick", "profile-refused",
        "other-mode", "other-code", "other-devices", "saves-other", "saves-none", "env-other",
        "env-none", "goal-late", "missed-rows", "row-skipped",
        "value-changed", "applied-order", "applied-header", "trajectory", "player-null",
        "trace-short", "trace-long", "trace-header", "named-column", "tick-rows-short",
        "import-short",
        "import-late",
        "import-slot-missing", "import-not-a-number", "import-sets", "traces-differ",
        "ticks-differ", "other-executable", "log-not-collected"])
def test_each_check_fails_by_name_on_its_own_constructed_input(world, monkeypatch, capsys,
                                                               specs, patch, failed):
    capture(REC, mode="record")
    if patch:
        patch(world, monkeypatch)
    fake_run(monkeypatch, specs)
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    s = summary()
    assert failed_checks() == failed
    assert s["verdict"] == "RETAKE_DISAGREE" and s["reasons"]
    assert f"RETAKE_DISAGREE: {', '.join(failed)}\n" in out
    assert "RETAKE_OK" not in out


@pytest.mark.parametrize("recorded, specs, failed", [
    # neither side restored a range, so nothing shows the same code ran
    ({"vanilla_csv": NO_RANGES}, {"vanilla_csv": NO_RANGES}, ["same_code_restored_as_recording"]),
    # a recording with no stage-trace rows gives no re-take anything to follow
    ({"trace_ticks": 0}, {"trace_ticks": 0}, ["trajectory_as_recorded"]),
], ids=["no-ranges-either-side", "no-trace-rows-either-side"])
def test_a_recording_that_holds_nothing_to_compare_passes_no_check_against_it(
        world, monkeypatch, recorded, specs, failed):
    """Equal because both are empty is not agreement."""
    capture(REC, mode="record", **recorded)
    fake_run(monkeypatch, specs)
    assert T.main([REC]) == 1
    assert failed_checks() == failed


@pytest.mark.parametrize("goals", [(GOAL, GOAL + 1), (0, 0)], ids=["goals-differ", "no-goal"])
def test_item_5_needs_one_goal_tick_beside_identical_tick_captures(world, monkeypatch, goals):
    """stage_route_item_5_met is item 5's two clauses on the fresh runs: they
    reproduce each other's tick captures, and set the goal latch on one tick.
    Here the first holds and the second does not."""
    capture(REC, mode="record")
    fake_run(monkeypatch, [{"goal": g} for g in goals])
    assert T.main([REC]) == 1
    s = summary()
    assert s["checks"]["tick_captures_identical"] is True
    assert s["reported"]["stage_route_item_5_met"] is False


def test_a_digest_difference_between_re_takes_is_named_and_item_5_is_not_met(world, monkeypatch,
                                                                            capsys):
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, [{}, {"tick_change": {(3, "d_01b00000"): "0x12345678",
                                                        (5, "d_01af0000"): "0x0"}}])
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    s = summary()
    one, two = (c["name"] for c in calls)
    assert s["reported"]["stage_route_item_5_met"] is False
    assert s["reported"]["digest_blocks_differ"] == {two: {"d_01b00000": 3, "d_01af0000": 5}}
    assert (f"  {two} differs from {one} in the digest blocks d_01af0000 from tick 5, "
            "d_01b00000 from tick 3\n") in out


def test_the_reasons_behind_failed_checks_are_printed(world, monkeypatch, capsys):
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, {"goal": 5, "misses": 1, "import_at": 7})
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    one = calls[0]["name"]
    log = R.CAPTURES / one / "sa2-import-calls.csv"
    assert f"  {one}: the goal latch rose at tick 5, and in {REC} at tick 4\n" in out
    assert f"  {one}: 1 input call(s) found no recorded row\n" in out
    assert (f"  {one}/sa2-import-calls.csv was counted at tick 7, and its tick capture holds 8 "
            "ticks\n") in out
    assert ("  native_imports.replay_calls refuses the re-takes' registration: "
            f"replay {REC}: {log} was counted at tick 7, and the replay judges 8 ticks\n") in out


def test_the_series_ends_at_the_first_rejected_run_and_the_summary_is_kept(world, monkeypatch,
                                                                           capsys):
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, [STOP, {}])
    assert T.main([REC, "--count", "3"]) == 1
    s = summary()
    assert len(calls) == 1 and s["count"] == 3
    assert s["runs"][0]["error"] == "r2: the run was not accepted (exit 1)."
    # no re-take ran, so nothing holds of the re-takes: no check is true of none
    assert failed_checks() == list(s["checks"])
    assert s["reported"]["registration"] is None


# ------------------------------------------------------------ refusals before any run


def edit_receipt(name: str, **fields) -> None:
    path = R.CAPTURES / name / "receipt.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **fields}))


def edit_session(name: str, **fields) -> None:
    """`name`'s receipt with each named session field replaced, or left out
    where the value is None."""
    path = R.CAPTURES / name / "receipt.json"
    rec = json.loads(path.read_text())
    for k, v in fields.items():
        if v is None:
            rec["session"].pop(k, None)
        else:
            rec["session"][k] = v
    path.write_text(json.dumps(rec))


def collect(name: str, file: str, sha256: str | None = "now") -> None:
    """Record in `name`'s receipt that its run collected `file` as it is now, or
    with `sha256`."""
    path = R.CAPTURES / name / "receipt.json"
    rec = json.loads(path.read_text())
    for a in rec["artifacts"]:
        if a["file"] == file:
            a["check"]["sha256"] = sha(R.CAPTURES / name / file) if sha256 == "now" else sha256
    path.write_text(json.dumps(rec))


def rewrite(name: str, file: str, text: str, *, collected: bool = True) -> None:
    """`file` in capture `name` written anew, and by default recorded as the file
    its run collected, so only what the text says is at fault."""
    (R.CAPTURES / name / file).write_text(text)
    if collected:
        collect(name, file)


@pytest.mark.parametrize("damage, words", [
    (lambda: edit_receipt(REC, run={"return_code": 1}), f"{REC} is not a whole capture: exit 1"),
    (lambda: edit_session(REC, device_profile=None), f"{REC} records no device profile"),
    (lambda: rewrite(REC, "sa2-input-end.csv", f"goal_tick,end_tick,misses\n0,{END},0\n"),
     "the goal latch never rose"),
    (lambda: rewrite(REC, "sa2-input-end.csv", f"goal_tick,end_tick,misses\n{GOAL},1,0\n"),
     "ended at tick 1, before any tick a replay captures"),
    (lambda: rewrite(REC, "sa2-input.csv",
                     INPUT_HEADER + "\n5,01,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0\n"),
     ":2: ord '01' is not decimal"),
    (lambda: (R.CAPTURES / REC / "sa2-input.csv").unlink(), f"{REC} holds no sa2-input.csv"),
    # input the parser takes, but not the input the user's run recorded
    (lambda: rewrite(REC, "sa2-input.csv", INPUT_HEADER + "\n" + row_text(input_rows()[0]) + "\n",
                     collected=False),
     f"{REC}/sa2-input.csv no longer hashes to what its run collected"),
    (lambda: (R.CAPTURES / REC / "sa2-stage-trace.csv").unlink(),
     f"{REC} holds no sa2-stage-trace.csv"),
    (lambda: collect(REC, "sa2-vanilla.csv", None),
     f"{REC}'s receipt records no sha256 for sa2-vanilla.csv"),
    # the session has not written its verdict: it may still be replaying the recording
    (lambda: (R.CAPTURES / REC / VERDICT).unlink(), f"{REC} holds no replays-*.json"),
    # option A: a recording from other saves, or whose receipt records none
    (lambda: edit_session(REC, saves={"at_start": AFTER_PICKUP}),
     f"{REC} did not start from the pinned saves"),
    (lambda: edit_session(REC, saves=None), "its receipt records none, and every re-take starts"),
    # option A: a recording whose receipt records no game environment
    (lambda: edit_session(REC, environment=None, game_environment=None),
     f"{REC}'s receipt records no game environment"),
], ids=["not-whole", "no-profile", "no-goal", "no-captured-tick", "bad-input", "no-input",
        "input-edited", "no-trace", "vanilla-log-unhashed", "no-session-verdict",
        "saves-other", "saves-none", "no-environment"])
def test_a_recording_that_cannot_be_re_taken_stops_before_any_run(world, monkeypatch, capsys,
                                                                   damage, words):
    capture(REC, mode="record")
    damage()
    no_runner(monkeypatch)
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    assert out.startswith(f"\nSTOPPED: {REC} cannot be re-taken:\n  - ")
    assert words in out
    assert out.count("\n  - ") == 1          # this reason alone
    assert not list((R.CAPTURES / REC).glob("retakes-*.json"))


def test_an_unknown_recording_and_a_single_re_take_are_refused(world, monkeypatch, capsys):
    no_runner(monkeypatch)
    assert T.main(["mh-record-none"]) == 1
    assert "no recording by that name" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        T.main([REC, "--count", "1"])
    assert e.value.code == 2


def test_a_recording_near_the_runner_s_cap_is_re_taken_with_a_warning(world, monkeypatch,
                                                                      capsys):
    """record_stage warns before its own replays, which take as long; a refusal
    would strand a recording whose session's replays fitted."""
    capture(REC, mode="record", elapsed=R.CAP - 10)
    no_runner(monkeypatch)
    warning = (f"WARNING: {REC} ran {R.CAP - 10} s of the runner's {R.CAP} s cap, and each "
               "re-take takes about as long; one that stops at the cap (exit 124) ends the "
               "series.")
    assert T.main([REC, "--dry-run"]) == 0
    assert warning in capsys.readouterr().out.splitlines()
    fake_run(monkeypatch, {})
    assert T.main([REC]) == 0
    assert warning in capsys.readouterr().out.splitlines()
    assert summary()["cap_warning"] == warning


# ------------------------------------------------------------ the dry run


def test_the_dry_run_prints_the_commands_the_runs_would_make_and_runs_nothing(world, monkeypatch,
                                                                              capsys):
    capture(REC, mode="record")
    no_runner(monkeypatch)
    before = sorted(p.relative_to(R.CAPTURES.parent) for p in R.CAPTURES.parent.rglob("*"))
    assert T.main([REC, "--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert sorted(p.relative_to(R.CAPTURES.parent) for p in R.CAPTURES.parent.rglob("*")) == before
    shown = [line.split(": ", 1)[1] for line in out if line.startswith("  re-take ")]
    names = [shlex.split(c)[shlex.split(c).index("--name") + 1] for c in shown]
    assert [n.rsplit("-", 2)[0] for n in names] == [f"{REC}-retake1", f"{REC}-retake2"]
    assert shown == [shlex.join(R.command(n, T.input_defines(REC, {"goal_tick": GOAL,
                                                                    "end_tick": END,
                                                                    "misses": 0}),
                                          R.REPLAY_CAPTURES, R.CAP, R.LOCK_WAIT))
                     for n in names]
    assert all('--define=import_probe:INPUT_END_DLL="input_probe.dll"' in shlex.split(c)
               and not any(a.startswith("--define=import_probe:TICK_LIMIT")
                           for a in shlex.split(c))
               for c in shown)
    assert (f"import log: counted on each re-take's last captured tick, {LAST} for one that "
            "ends where the recording did") in out
    assert not any("WARNING" in line for line in out)


def test_the_dry_run_names_what_would_stop_a_real_run_and_exits_1(world, monkeypatch, capsys):
    capture(REC, mode="record")
    rewrite(REC, "sa2-input-end.csv", f"goal_tick,end_tick,misses\n0,{END},0\n")
    no_runner(monkeypatch)
    assert T.main([REC, "--dry-run", "--count", "3"]) == 1
    out = capsys.readouterr().out
    assert out.count("\n  re-take ") == 3
    assert out.endswith("A real run stops before any re-take:\n"
                        f"  - {REC}: the goal latch never rose, so this recording does not "
                        "reach the goal\n")


# ------------------------------------------------------------ record_stage's runner call


def test_the_runner_call_is_record_stage_s_own_and_counts_the_log_at_the_run_s_end(
        world, monkeypatch):
    """The re-takes run record_stage.run, whose command is record_stage.command:
    the import probe built with DEFINES, which count the log on the run's last
    captured tick (test_native_import_end.py), and no fixed tick."""
    cmd = R.command("n", ["INPUT_MODE=2"], R.REPLAY_CAPTURES, R.CAP, R.LOCK_WAIT)
    assert [a for a in cmd if a.startswith("--define=import_probe:")] == [
        '--define=import_probe:INPUT_END_DLL="input_probe.dll"']
    seen = []

    def fake(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(R.subprocess, "run", fake)
    with pytest.raises(SystemExit):
        R.run("n", ["INPUT_MODE=2"], R.REPLAY_CAPTURES, R.CAP, lock_timeout=R.LOCK_WAIT)
    assert seen == [cmd]


# ------------------------------------------------------------ option A, beyond the checks


def test_a_recording_from_a_shell_with_another_game_environment_stops_before_any_run(
        world, monkeypatch, capsys):
    """Every re-take would be refused, so none runs; the variables are named,
    never their values (record_stage.check_recording's rule)."""
    capture(REC, mode="record")
    monkeypatch.setattr(R, "launch_environment", lambda: {**OTHER_ENV, "EXTRA": "x"})
    no_runner(monkeypatch)
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    assert (f"{REC} was recorded with another game environment than a run from this shell "
            "would get (EXTRA, LANG differ).") in out
    assert "en_GB" not in out and "C.UTF-8" not in out
    assert out.count("\n  - ") == 1
    assert T.main([REC, "--dry-run"]) == 1
    assert "(EXTRA, LANG differ)" in capsys.readouterr().out


def test_an_unreadable_pin_refuses_the_recording_and_fails_the_verdict(world, monkeypatch,
                                                                        capsys):
    capture(REC, mode="record")
    no_runner(monkeypatch)
    R.PIN.write_text("{")
    assert T.main([REC]) == 1
    assert "STOPPED" in capsys.readouterr().out
    reps = [{"name": f"{REC}-retake1-x", "error": None}]
    capture(reps[0]["name"], mode="replay")
    reps[0]["end"] = R.end_of(reps[0]["name"])
    reps[0].update(R.facts(R.receipt(reps[0]["name"])))
    got = T.judge(REC, R.end_of(REC), reps, 1)
    assert got["checks"]["start_saves_pinned"] is False
    assert any("cannot be read" in r for r in got["reasons"])


def test_a_recording_whose_probe_set_the_tree_no_longer_builds_cannot_be_re_taken_as_it(
        world, monkeypatch):
    """A probe taken from another source file since the recording: the
    re-takes are what the tree builds, and they are another configuration than
    the recording's, which no re-take of it can repair. A moved define or
    header is what a re-take follows, and passes (the next test)."""
    capture(REC, mode="record", builds=built(
        ["INPUT_MODE=1"], {"stage_trace": {"source": "/x/harness/replay/native_stage_trace2.c"}}))
    fake_run(monkeypatch, {})
    assert T.main([REC]) == 1
    assert failed_checks() == ["same_probe_set_as_recording"]
    assert any("loaded another probe set" in r for r in summary()["reasons"])


def test_a_probe_whose_define_or_header_moved_since_the_recording_is_followed(world, monkeypatch):
    """The reason re-takes exist: a stage digest mask moves the tick probe's
    inputs, and the re-takes are built as the tree builds them now."""
    capture(REC, mode="record", builds=built(
        ["INPUT_MODE=1"], {"tick_probe": {"defines": ["TICK_LIMIT=5"],
                                          "headers_sha256": {"tick_digest.h": "0" * 64}}}))
    fake_run(monkeypatch, {})
    assert T.main([REC]) == 0
    assert summary()["checks"]["same_probe_set_as_recording"] is True


def test_the_saves_each_run_ended_with_are_reported_not_judged(world, monkeypatch):
    capture(REC, mode="record", saves_at_end=AFTER_PICKUP)
    calls = fake_run(monkeypatch, [{"saves_at_end": AFTER_PICKUP}, {"saves_at_end": SAVES}])
    assert T.main([REC]) == 0
    one, two = (c["name"] for c in calls)
    assert summary()["reported"]["saves_at_end"] == {REC: AFTER_PICKUP, one: AFTER_PICKUP,
                                                      two: SAVES}


def judged(recording_kw: dict, retake_kw: dict) -> dict:
    """judge() itself over a recording and two re-takes made with capture()'s
    keyword arguments: what check_recording refuses first is still checked."""
    capture(REC, mode="record", **recording_kw)
    reps = []
    for k in range(2):
        n = f"{REC}-retake{k + 1}-x"
        capture(n, mode="replay", **retake_kw)
        reps.append({"name": n, "error": None, "end": R.end_of(n), **R.facts(R.receipt(n))})
    return T.judge(REC, R.end_of(REC), reps, 2)["checks"]


def test_the_verdict_holds_the_recording_itself_to_the_pin(world):
    """Re-takes on the pin cannot show a recording off it started there."""
    checks = judged({"saves": AFTER_PICKUP}, {})
    assert [k for k, v in checks.items() if not v] == ["start_saves_pinned"]


def test_no_game_environment_on_either_side_is_not_one_environment(world):
    """Equal because both are missing is not the same environment."""
    checks = judged({"environment": None}, {"environment": None})
    assert [k for k, v in checks.items() if not v] == ["same_game_environment_as_recording"]


def test_a_reordered_probe_set_is_another_configuration(world, monkeypatch):
    """The re-take review's N1: the same probes from the same sources, loaded in
    another order, are not the recording's probe set."""
    order = built(["INPUT_MODE=1"])
    i = next(k for k, b in enumerate(order) if b["mod_id"] == "input_probe")
    j = next(k for k, b in enumerate(order) if b["mod_id"] == "tick_probe")
    order[i], order[j] = order[j], order[i]
    capture(REC, mode="record", builds=order)
    fake_run(monkeypatch, {})
    assert T.main([REC]) == 1
    assert failed_checks() == ["same_probe_set_as_recording"]


def test_a_newer_probe_s_re_take_is_given_the_device_check_s_own_reasons(world):
    """RT's note on this tool, folded by unit IN (2026-09-26): the reasons behind
    same_device_profile_as_recording are record_stage.device_differences', the
    check's own. A re-take whose profile adds only sections the recording does
    not hold (a newer probe's) passes the check with no device-profile reason,
    where a whole-sha256 comparison gave it one; one whose lines of a section
    the recording holds differ fails it with the section named; and one whose
    file no longer holds its recorded sha256 fails it with that said."""
    old = b"sa2-device-profile 1\ngamectrl 1 00000001\n"
    new = old + b"env 1 PATH=C:\\windows\n"
    other = b"sa2-device-profile 1\ngamectrl 1 00000002\nenv 1 PATH=C:\\windows\n"
    capture(REC, mode="record", profile=dict(PROFILE, sha256=hashlib.sha256(old).hexdigest()))
    (R.CAPTURES / REC / "sa2-device-profile.txt").write_bytes(old)

    def retake(n: str, text: bytes) -> str:
        capture(n, mode="replay", profile=dict(PROFILE, sha256=hashlib.sha256(text).hexdigest()))
        (R.CAPTURES / n / "sa2-device-profile.txt").write_bytes(text)
        return n

    def judged(names: list[str]) -> dict:
        reps = [{"name": n, "error": None, "end": R.end_of(n), **R.facts(R.receipt(n))}
                for n in names]
        return T.judge(REC, R.end_of(REC), reps, len(reps))

    def device_reasons(got: dict) -> list[str]:
        return [r for r in got["reasons"] if "device profile" in r]

    a, b = retake(f"{REC}-retake1-a", new), retake(f"{REC}-retake2-a", new)
    got = judged([a, b])
    assert got["checks"]["same_device_profile_as_recording"] is True
    assert device_reasons(got) == []

    c = retake(f"{REC}-retake2-c", other)
    got = judged([a, c])
    assert got["checks"]["same_device_profile_as_recording"] is False
    assert device_reasons(got) == [f"{c}'s device profile differs from the recording's in a "
                                   "section the recording holds, first in 'gamectrl'"]

    (R.CAPTURES / b / "sa2-device-profile.txt").write_bytes(other)
    got = judged([a, b])
    assert got["checks"]["same_device_profile_as_recording"] is False
    assert device_reasons(got) == [
        f"{b} recorded device profile {hashlib.sha256(new).hexdigest()[:12]}, and the "
        f"recording {hashlib.sha256(old).hexdigest()[:12]}, and {b}'s own file no longer "
        "holds it"]


def test_the_recording_s_profile_is_read_from_the_recording_s_own_capture(world):
    """The instrument review of unit IN, finding 4: the device check and its reasons
    read the recording's profile from the recording's own capture (`recording`), not
    from any capture that holds a file of that name and sha256. With the recording's
    file gone and the same bytes left in another capture, both say so; a check that
    searched every capture passed."""
    old = b"sa2-device-profile 1\ngamectrl 1 00000001\n"
    new = old + b"env 1 PATH=C:\\windows\n"
    capture(REC, mode="record", profile=dict(PROFILE, sha256=hashlib.sha256(old).hexdigest()))
    names = [f"{REC}-retake1-a", f"{REC}-retake2-a"]
    for n in names:
        capture(n, mode="replay", profile=dict(PROFILE, sha256=hashlib.sha256(new).hexdigest()))
        (R.CAPTURES / n / "sa2-device-profile.txt").write_bytes(new)
    elsewhere = R.CAPTURES / "another-capture-with-the-same-profile"
    elsewhere.mkdir()
    (elsewhere / "sa2-device-profile.txt").write_bytes(old)
    (R.CAPTURES / REC / "sa2-device-profile.txt").unlink(missing_ok=True)
    reps = [{"name": n, "error": None, "end": R.end_of(n), **R.facts(R.receipt(n))}
            for n in names]
    got = T.judge(REC, R.end_of(REC), reps, len(reps))
    assert got["checks"]["same_device_profile_as_recording"] is False
    assert [r for r in got["reasons"] if "device profile" in r] == [
        f"no capture holds the recording's device profile ({hashlib.sha256(old).hexdigest()[:12]}"
        "), so no replay's can be compared with it"]
    (R.CAPTURES / REC / "sa2-device-profile.txt").write_bytes(old)
    got = T.judge(REC, R.end_of(REC), reps, len(reps))
    assert got["checks"]["same_device_profile_as_recording"] is True


def test_the_recording_s_own_saves_and_environment_are_given_as_reasons(world):
    """The re-take review's N2: the reason lines behind the recording's own
    start_saves_pinned and same_game_environment_as_recording."""
    capture(REC, mode="record", saves=AFTER_PICKUP, environment=None)
    reps = []
    for k in range(2):
        n = f"{REC}-retake{k + 1}-x"
        capture(n, mode="replay")
        reps.append({"name": n, "error": None, "end": R.end_of(n), **R.facts(R.receipt(n))})
    got = T.judge(REC, R.end_of(REC), reps, 2)
    assert f"{REC} did not start from the pinned saves (its receipt records " \
           f"{AFTER_PICKUP})" in got["reasons"]
    assert f"{REC}'s receipt records no game environment" in got["reasons"]


def test_a_recording_whose_vanilla_probe_was_an_experiment_build_is_not_re_taken(
        world, monkeypatch, capsys):
    """The re-take review's N3: record_stage refuses such a recording with
    RecordAgain; a re-take under today's probe would read it RETAKE_OK."""
    capture(REC, mode="record", builds=built(["INPUT_MODE=1"],
                                             {"vanilla_probe": {"defines": ["EXPERIMENT=1"]}}))
    no_runner(monkeypatch)
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    assert "vanilla probe was not built as the vanilla oracle builds it" in out
    assert out.count("\n  - ") == 1


def test_a_live_recording_session_stops_the_re_takes(world, monkeypatch, capsys):
    """The re-take review's N6: any recording's session, before the series and
    between its runs, since a re-take holds the oracle lock for a whole replay."""
    capture(REC, mode="record")
    monkeypatch.setattr(T, "live_sessions", lambda: [4242])
    no_runner(monkeypatch)
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    assert "a recording session is running (record_stage.py, pid 4242)" in out
    assert not list((R.CAPTURES / REC).glob("retakes-*.json"))
    # One that starts after the first re-take: the series ends, nothing more runs.
    answers = iter([[], [], [4243]])       # check_recording, re-take 1, re-take 2
    monkeypatch.setattr(T, "live_sessions", lambda: next(answers))
    calls = fake_run(monkeypatch, {})
    assert T.main([REC]) == 1
    assert len(calls) == 1
    assert "every_retake_ran" in failed_checks()
    assert any("pid 4243" in r for r in summary()["reasons"])


def test_the_session_finder_reads_each_process_s_argv(tmp_path, monkeypatch):
    """The M6 fold review's nit 5: the /proc scan itself, over made-up processes."""
    for pid, argv in ((101, ["/workspace/OpenSA2/.venv/bin/python",
                             "tools/oracle/record_stage.py"]),
                      (102, ["bash", "-c", "pgrep -f record_stage.py"]),
                      (103, ["python3", "tools/oracle/retake_replays.py", "r"])):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (tmp_path / "self").mkdir()
    monkeypatch.setattr(T, "PROC", tmp_path)
    assert T.live_sessions() == [101]


def test_a_recording_with_no_vanilla_probe_is_not_re_taken(world, monkeypatch, capsys):
    """The M6 fold review's nit 4: the other half of the vanilla-build check."""
    capture(REC, mode="record", builds=[b for b in built(["INPUT_MODE=1"])
                                        if b["mod_id"] != "vanilla_probe"])
    no_runner(monkeypatch)
    assert T.main([REC]) == 1
    assert "vanilla probe was not built as the vanilla oracle builds it (0 build(s)" in \
        capsys.readouterr().out


def test_the_session_finder_is_the_recorder_s_pgrep_pattern():
    assert T.RECORDER.match("/workspace/OpenSA2/.venv/bin/python tools/oracle/record_stage.py")
    assert T.RECORDER.match("python3 /x/record_stage.py --replay-only r")
    assert not T.RECORDER.match("bash -c pgrep -f record_stage.py")
    assert not T.RECORDER.match("python3 tools/oracle/retake_replays.py r")


# ------------------------------------------------------------ the word log


def old_mask(builds: list[dict]) -> list[dict]:
    """`builds` as the runner records them when tick_digest.h beside the probes
    was another: every probe records every header beside it."""
    for b in builds:
        b["headers_sha256"] = {**b["headers_sha256"], "tick_digest.h": "0" * 64}
    return builds


def test_a_recording_taken_before_the_word_log_is_re_taken_with_it(world, monkeypatch):
    """The user's recording ran the tick probe that computed its digests in
    the game, with no define and another mask. The re-takes run the word log's
    probe, which the tree builds now: the moved probe is followed, and named."""
    capture(REC, mode="record", builds=old_mask(built(
        ["INPUT_MODE=1"], {"tick_probe": {"source_sha256": "0" * 64, "defines": []}})))
    calls = fake_run(monkeypatch, {})
    assert T.main([REC]) == 0
    s = summary()
    assert s["verdict"] == "RETAKE_OK"
    assert s["moved_since_recording"] == ["harness/replay/native_tick_probe.c",
                                          "harness/replay/tick_digest.h",
                                          "tools/oracle/record_stage.py"]
    assert len(calls) == 2


def test_a_mask_that_moved_between_re_takes_is_one_configuration(world, monkeypatch):
    """A new mask between two re-takes moves no probe input (the digests are
    the host's), so both are built as the tree builds now and compare."""
    capture(REC, mode="record")
    fake_run(monkeypatch, [{}, {"builds": old_mask(built(["INPUT_MODE=2"]))}])
    assert T.main([REC]) == 0
    assert summary()["checks"]["built_as_the_tree_builds_now"] is True


def test_each_re_take_collects_the_word_log_before_the_double_dash(world):
    capture(REC, mode="record")
    for cmd in T.commands(REC, R.end_of(REC), T.retake_names(REC, "s", 2)):
        args = R.RP.arg_parser().parse_args(cmd[2:])
        assert R.RP.game_arguments(args) == ["--level", "metalharbor", "--character", "sonic"]
        assert args.binary_capture == ["sa2-word-log.bin"]
        assert [d for d in args.define if d.startswith("tick_probe:")] == [
            'tick_probe:INPUT_END_DLL="input_probe.dll"']


def test_each_re_take_s_word_log_is_reported_with_the_machine_before_it(world, monkeypatch):
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, {})
    real = T.retake

    def retake(recording, end, names, *rest, **kw):
        out = real(recording, end, names, *rest, **kw)
        for n in names:
            (R.CAPTURES / n / "sa2-word-log.bin.zst").write_bytes(b"log")
            edit_receipt(n, word_log={"stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64,
                                      "ticks": LAST, "end_reason": "input_end", "ok": True})
            edit_session(n, idle_before_run={"seconds": 95.0},
                         load_average={"at_launch": [0.5, 0.4, 0.3]})
        return out

    monkeypatch.setattr(T, "retake", retake)
    assert T.main([REC]) == 0
    got = summary()["retakes"][calls[0]["name"]]["word_log"]
    assert got == {"stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64, "ticks": LAST,
                   "end_reason": "input_end", "ok": True,
                   "file": str(R.CAPTURES / calls[0]["name"] / "sa2-word-log.bin.zst"),
                   "file_sha256": hashlib.sha256(b"log").hexdigest(),
                   "idle_before_run_s": 95.0, "load_average_at_launch": [0.5, 0.4, 0.3]}


# ------------------------------------------------ the reviews of 2026-09-25


def test_under_the_stage_s_mask_the_re_takes_are_compared_as_their_logs_give_them(
        world, monkeypatch, tmp_path, capsys):
    """W1: the stored captures carry the tree's mask; with the stage's own
    (--mask, or harness/replay/masks/<level>.h), tick_captures_identical is
    judged on what each re-take's kept log gives with it, and the summary
    names the mask. Here the stored captures differ in a block the stage's
    mask leaves out, and the logs under it agree."""
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, [{}, {"tick_change": {(3, "d_01b00000"): "0x12345678"}}])
    mask = tmp_path / "metalharbor.h"
    mask.write_text("/* a stage mask */\n")
    asked = []

    def from_log(capture_dir, m=None):
        asked.append((Path(capture_dir).name, m))
        return "tick,frame_count,d_01760000\n1,0x00000001,0x00000002\n"

    monkeypatch.setattr(R.RP, "capture_from_log", from_log)
    # native_imports.replay_calls still holds a registration to the stored
    # captures (U2's MANIFEST asks its owner to take the mask too), so that
    # check alone fails.
    assert T.main([REC, "--mask", str(mask)]) == 1
    s = summary()
    assert failed_checks() == ["import_logs_registrable"]
    assert s["checks"]["tick_captures_identical"] is True
    under = s["reported"]["judged_under_mask"]
    assert (under["mask"], under["sha256"]) == (str(mask), R.sha256(mask))
    # A header given by hand is judged under but cannot meet item 5 (the U2 fold
    # review, W1): only the stage's own mask under harness/replay/masks/ can.
    assert under["source"] == "given"
    assert s["reported"]["stage_route_item_5_met"] is False
    assert asked == [(c["name"], mask) for c in calls]
    assert f"tick captures judged under {mask} (" in capsys.readouterr().out
    # The stage's own mask by its path, when it exists and --mask is not given.
    monkeypatch.setattr(T, "MASKS", tmp_path)
    assert T.stage_mask() == mask
    assert T.stage_mask(tmp_path / "other.h") == tmp_path / "other.h"
    monkeypatch.setattr(T, "MASKS", tmp_path / "none")
    assert T.stage_mask() is None


def test_under_a_stage_mask_a_difference_is_named_with_the_mask(world, monkeypatch, tmp_path,
                                                                capsys):
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, [{}, {}])
    mask = tmp_path / "metalharbor.h"
    mask.write_text("/* a stage mask */\n")
    texts = iter(["tick,frame_count,d_01760000\n1,0x1,0x2\n2,0x2,0x2\n",
                  "tick,frame_count,d_01760000\n1,0x1,0x2\n2,0x2,0x9\n"])
    monkeypatch.setattr(R.RP, "capture_from_log", lambda d, m=None: next(texts))
    assert T.main([REC, "--mask", str(mask)]) == 1
    one, two = (c["name"] for c in calls)
    assert "tick_captures_identical" in failed_checks()
    assert summary()["reported"]["digest_blocks_differ_under_mask"] == {two: {"d_01760000": 2}}
    assert (f"  {two} differs from {one} under {mask} in the digest blocks d_01760000 from "
            "tick 2\n") in capsys.readouterr().out


def test_a_re_take_whose_digests_are_another_mask_s_is_given_that_reason(world, monkeypatch,
                                                                        capsys):
    """N2: across two masks no digest block is named as differing, and the
    reason is the masks."""
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, [{}, {"tick_change": {(3, "d_01b00000"): "0x12345678"}}])
    real = T.retake

    def retake(recording, end, names, *rest, **kw):
        out = real(recording, end, names, *rest, **kw)
        for n, mask in zip(names, ("1" * 64, "2" * 64)):
            rec = json.loads((R.CAPTURES / n / "receipt.json").read_text())
            for a in rec["artifacts"]:
                if a["file"] == "sa2-tick-capture.csv":
                    a["built_from_log"] = {"digest_inputs": {"harness/replay/tick_digest.h": mask}}
            (R.CAPTURES / n / "receipt.json").write_text(json.dumps(rec))
        return out

    monkeypatch.setattr(T, "retake", retake)
    assert T.main([REC]) == 1
    out = capsys.readouterr().out
    one, two = (c["name"] for c in calls)
    s = summary()
    assert s["reported"]["masks_same"] == {two: False}
    assert s["reported"]["digest_blocks_differ"] == {two: {}}
    assert (f"  {two}'s digest columns were built with another mask than {one}'s, so none "
            "compares (not rebuilt: a run kept no word log)\n") in out
    assert "in the digest blocks" not in out


def test_the_summary_names_the_probes_whose_build_moved(world, monkeypatch):
    """N3: moved_since_recording names files, record_stage.py among them for
    any define; moved_probes names the probes."""
    capture(REC, mode="record", builds=built(["INPUT_MODE=1"],
                                             {"tick_probe": {"source_sha256": "0" * 64}}))
    fake_run(monkeypatch, {})
    assert T.main([REC]) == 0
    assert summary()["moved_probes"] == ["tick_probe"]


def test_each_re_take_waits_for_the_idle_time_asked(world, monkeypatch, capsys):
    """N4: with --min-idle, no re-take launches before the oracle has been idle
    that long by the runner's measure, and the series ends after --max-wait."""
    capture(REC, mode="record")
    calls = fake_run(monkeypatch, {})
    monkeypatch.setattr(T, "POLL", 0.0)
    monkeypatch.setattr(R.RP, "idle_before_run", lambda: {"seconds": 5.0})
    assert T.main([REC, "--min-idle", "90", "--max-wait", "0"]) == 1
    assert calls == []
    assert summary()["runs"][0]["error"] == ("the oracle had been idle 5.0 s, not 90 s, after "
                                             "waiting 0 s")
    idle = iter([10.0, 95.0, 10.0, 91.0])
    monkeypatch.setattr(R.RP, "idle_before_run", lambda: {"seconds": next(idle)})
    for p in (R.CAPTURES / REC).glob("retakes-*.json"):
        p.unlink()
    assert T.main([REC, "--min-idle", "90", "--max-wait", "60"]) == 0
    assert len(calls) == 2
    s = summary()
    assert (s["min_idle"], s["min_idle_met"]) == (90.0, {c["name"]: False for c in calls})


# ------------------------------------------------------------ the re-judge (U6 fold, W2)

def test_a_rejudge_judges_series_already_taken_under_the_stage_mask_and_runs_nothing(
        world, monkeypatch, tmp_path, capsys):
    """--rejudge SUMMARY...: every stage's mask is chosen from its re-takes, so
    item 5 under it is judged after them. Two series are read back from their
    summaries, judged together under the stage's own mask, nothing is launched,
    a shell with another game environment does not stop it (it launches nothing),
    and the receipt names the summaries, the mask, every word log and the verdict."""
    capture(REC, mode="record")
    fake_run(monkeypatch, {})
    assert T.session(REC, 2, "20260925-220509") == 0
    assert T.session(REC, 2, "20260925-222033") == 0
    both = sorted((R.CAPTURES / REC).glob("retakes-*.json"))
    assert len(both) == 2
    masks = tmp_path / "masks"
    masks.mkdir(exist_ok=True)
    (masks / "metalharbor.h").write_text("/* the stage's own mask */\n")
    monkeypatch.setattr(R.RP, "capture_from_log",
                        lambda d, m=None: "tick,frame_count,d_01760000\n1,0x1,0x2\n")
    no_runner(monkeypatch)
    monkeypatch.setattr(R, "launch_environment", lambda: {**OTHER_ENV, "EXTRA": "x"})
    receipt = tmp_path / "item5.json"
    got = T.main([REC, "--rejudge", *[p.name for p in both], "--receipt", str(receipt)])
    out = capsys.readouterr().out
    rec = json.loads(receipt.read_text())
    assert rec["schema"] == T.REJUDGE_SCHEMA and rec["count"] == 4
    assert [r["summary"] for r in rec["rejudged_from"]] == [str(p) for p in both]
    assert rec["judged_under_mask"]["source"] == "stage"
    assert rec["mask"]["sha256"] == R.sha256(masks / "metalharbor.h")
    assert sorted(rec["word_logs"]) == sorted(
        r for s in rec["rejudged_from"] for r in s["runs"])
    assert rec["checks"]["tick_captures_identical"] is True
    assert rec["stage_route_item_5_met"] is True and got == 0
    assert "item 5 under" in out and "met" in out
    # A summary that is not a series of this recording's re-takes is refused.
    assert T.main([REC, "--rejudge", Path(rec["summary"]["path"]).name]) == 1
    assert "is not a series of re-takes" in capsys.readouterr().out
    # The same series twice names every run twice: refused.
    assert T.main([REC, "--rejudge", both[0].name, both[0].name]) == 1
    assert "named twice" in capsys.readouterr().out
    # A series with a run that did not run to the end, or of another recording: refused.
    doc = json.loads(both[1].read_text())
    doc["runs"][0]["error"] = "stopped"
    (both[1].parent / "retakes-bad.json").write_text(json.dumps(doc))
    assert T.main([REC, "--rejudge", "retakes-bad.json"]) == 1
    assert "did not run to the end" in capsys.readouterr().out
    doc = dict(json.loads(both[1].read_text()), recording="mh-record-other")
    (both[1].parent / "retakes-other.json").write_text(json.dumps(doc))
    assert T.main([REC, "--rejudge", "retakes-other.json"]) == 1
    assert "is not a series of re-takes" in capsys.readouterr().out
    # Captures that differ under the mask: item 5 not met, and the command says so by its exit.
    n = iter(range(1000))
    monkeypatch.setattr(R.RP, "capture_from_log",
                        lambda d, m=None: f"tick,frame_count,d_01760000\n1,0x1,{next(n):#x}\n")
    assert T.main([REC, "--rejudge", *[p.name for p in both]]) == 1
    capsys.readouterr()
    # Without the stage's own mask there is nothing to judge item 5 under.
    (masks / "metalharbor.h").unlink()
    assert T.main([REC, "--rejudge", both[0].name]) == 1
    assert "no stage mask" in capsys.readouterr().out
