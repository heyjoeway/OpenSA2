"""tools/oracle/record_stage.py, driven with synthetic receipts and captures.

The recorder is the one command the user runs for the play session M6 needs,
and each case here is a way that session could be lost or misjudged: a whole
run thrown away for a teardown exit, a rejection that does not say why, a
verdict that passes without comparing anything, a digest allowance applied
without saying so, a recording copied into C with a leading zero, pacing timed
on the runner's clock instead of the game's, a failure after the user played
that leaves no way back to the recording, a run that started from other saves
than the pinned ones, a replay launched with another game environment.

No runner, game or Wine process runs: the runner's subprocess or the tool's
own `run` is replaced where a flow needs one. The probe builds in the receipts
are hashed from the tree, as the runner hashes them; every other value --
device, address, byte, capture value and elapsed time -- is made up, none
copied from the executable or a capture.
"""

from __future__ import annotations

import builtins
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import record_stage as R  # noqa: E402

PROFILE = {"file": "sa2-device-profile.txt", "sha256": "1" * 64,
           "counts": {"gamectrl": 1, "xinput_connected": 0, "wmi_pnp": 3, "ddraw": 2}}
OTHER_PROFILE = dict(PROFILE, sha256="2" * 64)
TICK_HEADER = ["tick", "frame_count", "game_mode", "d_01750000", "d_01af0000", "d_01b00000",
               "d_01b10000", "d_01760000"]
TRACE_HEADER = "tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z"
VANILLA_CSV = ("va,length,loader_bytes,file_bytes,action\n"
               "0x00500100,5,e911111111,aabbccdd01,kept\n"
               "0x00500200,5,e922222222,aabbccdd02,restored\n"
               "0x00900010,4,33333333,44444444,iat-kept\n"
               "0x00500200,5,,,tick1-intact\n")
INPUT_HEADER = "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old\n"
SAVES = {"SONIC2B__ALF": "a" * 64}
# The environment the runner launches the game with (run_oracle_probe
# game_environment): every capture here was launched with GAME_ENV unless a
# test says otherwise, and a run this process starts would get it too.
GAME_ENV = {"HOME": "/home/someone", "DISPLAY": ":9", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"}
OTHER_ENV = {**GAME_ENV, "LANG": "en_GB.UTF-8"}
ROW = "5,1,0x400,0xfffffbff,0x400,0x0,0x0,0x0,0x0,0xff81,0x0,0x0,0x0"
# The import-address-table slots the import logs here name, standing for the
# executable's: made-up addresses and names, read through native_imports.read_at.
SLOTS = {0x00900000: "x.dll!a", 0x00900004: "x.dll!b"}
TREE_BUILDS = ["vanilla_probe=harness/replay/native_vanilla_probe.c",
               "import_probe=harness/replay/native_import_probe.c",
               "input_probe=harness/replay/native_input_probe.c",
               "tick_probe=harness/replay/native_tick_probe.c",
               "stage_trace=harness/replay/native_stage_trace.c"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def built(input_defines=("INPUT_MODE=1",), change: dict | None = None) -> list[dict]:
    """The receipt's built_probes for this tool's probe set, from the tree."""
    out = []
    for mod, source in R.PROBES + ((R.PROFILE_MOD, R.DP.PROBE),):
        path = ROOT / source
        entry = {"mod_id": mod, "source": str(path), "source_sha256": sha(path),
                 "headers_sha256": {h.name: sha(h) for h in sorted(path.parent.glob("*.h"))},
                 "defines": list(input_defines) if mod == "input_probe"
                 else list(R.DEFINES.get(mod, [])),
                 "dll_sha256": "d" * 64}
        entry.update((change or {}).get(mod, {}))
        out.append(entry)
    return out


def capture(name: str, *, mode: str = "record", ticks: int = 6, goal: int = 4,
            end: int | None = None, misses: int = 0, rc: int = 0, elapsed: float = 15.0,
            tick_change: dict | None = None, tick_header: list[str] | None = None,
            player: str = "0x0a0b0c0d", builds: list[dict] | None = None,
            profile: dict | None = PROFILE, input_defines=None,
            vanilla_csv: str = VANILLA_CSV, import_at: int | None = None,
            timer: int = 5, saves: dict | None = SAVES, saves_at_end: dict | None = None,
            environment: dict | None = GAME_ENV, word_log: bool = True) -> dict:
    """A capture directory as the runner leaves one, and its receipt. Its
    import log is counted at `import_at`, by default the last captured tick,
    as the import probe built with DEFINES writes it, and its first slot
    (R.TIMER here, the `vanilla` fixture's) counts `timer` calls. `saves` is
    what the runner records as the saves the run started from, and
    `saves_at_end` what it ended with; None leaves the record out, as a
    receipt from before 2026-09-23 does. `environment` is the game
    environment the run was launched with, recorded as the runner records
    it; None leaves it out, as a receipt from before 2026-09-24 does.
    `word_log` records the runner's word log, as every receipt since
    2026-09-25 does; False leaves it out, as a recording from before it."""
    d = R.CAPTURES / name
    d.mkdir(parents=True)
    header = tick_header or TICK_HEADER
    with (d / "sa2-tick-capture.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for t in range(1, ticks + 1):
            row = {c: "aa" for c in header}
            row.update(tick=str(t), frame_count=str(t), game_mode="0x5")
            for (tick, column), value in (tick_change or {}).items():
                if tick == t:
                    row[column] = value
            w.writerow([row[c] for c in header])
    (d / "sa2-stage-trace.csv").write_text(TRACE_HEADER + "\n" + "".join(
        f"{t},0x000a,0x00000005,{'0x0010' if t >= 3 else '0x0007'},0x{t:08x},{player},"
        "0x11111111,0x22222222,0x33333333\n" for t in range(1, ticks + 1)))
    (d / "sa2-input-end.csv").write_text(
        f"goal_tick,end_tick,misses\n{goal},{ticks + 1 if end is None else end},{misses}\n")
    (d / "sa2-vanilla.csv").write_text(vanilla_csv)
    at = ticks if import_at is None else import_at
    (d / "sa2-import-calls.csv").write_text(
        f"slot,calls,ours,tick\n0x00900000,{timer},1,{at}\n0x00900004,0,1,{at}\n")
    if mode == "record":
        (d / "sa2-input.csv").write_text(INPUT_HEADER + "0,0,0x0,0xffffffff,0x0,0x0,0x0,0x0,"
                                         "0x0,0x0,0x0,0x0,0x0\n" + ROW + "\n")
        files = R.RECORD_CAPTURES
        defines = input_defines or ["INPUT_MODE=1"]
    else:
        (d / "sa2-input-applied.csv").write_text("tick,ord,matched\n1,0,1\n")
        files = R.REPLAY_CAPTURES
        defines = input_defines or ["INPUT_MODE=2"]
    arts = [{"file": f, "present": True, "check": {"ok": True, "problems": []}} for f in files]
    arts.append({"file": "sa2-device-profile.txt", "present": True, "device_profile": True,
                 "check": {"ok": True, "problems": [], "sha256": (profile or {}).get("sha256")}})
    rec = {"inputs": {"built_probes": builds or built(defines), "timeout_seconds": R.CAP},
           "run": {"return_code": rc, "timed_out": rc == 124, "elapsed_seconds": elapsed},
           "installation_untouched": True, "artifacts": arts,
           "session": {"device_profile": profile}}
    if environment is not None:
        rec["session"]["environment"] = R.RP.environment_record(environment)
        rec["session"]["game_environment"] = dict(environment)
    if saves is not None:
        rec["session"]["saves"] = {"at_start": saves}
    if saves_at_end is not None:
        rec["session"].setdefault("saves", {})["at_end"] = saves_at_end
    if word_log:
        rec["word_log"] = {"ok": True, "stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64,
                           "ticks": ticks, "end_reason": "input_end"}
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
def vanilla(tmp_path, monkeypatch) -> dict:
    """Captures and scripts under tmp_path, and oracle_mode's vanilla check
    and mode comparison answering from this dict (capture name -> problems)
    instead of the files, whose own tests are tools/oracle/test_oracle_mode.py.
    The pinned start state is SAVES, and a run this process starts would get
    GAME_ENV."""
    monkeypatch.setattr(R, "CAPTURES", tmp_path / "captures")
    monkeypatch.setattr(R, "SCRIPTS", tmp_path / "scripts")
    monkeypatch.setattr(R, "TIMER", "x.dll!a")
    monkeypatch.setattr(R, "PIN", write_pin(tmp_path / "save-state.json", SAVES))
    monkeypatch.setattr(R, "launch_environment", lambda: dict(GAME_ENV))
    (tmp_path / "captures").mkdir()
    problems: dict[str, list[str]] = {}
    monkeypatch.setattr(R.OM, "vanilla_problems",
                        lambda d: list(problems.get(Path(d).name, [])))
    monkeypatch.setattr(R.OM, "require_same", lambda caps, *a, **k: {"mode": "vanilla"})
    monkeypatch.setattr(R.NI, "slots", lambda exe=None: SLOTS)
    return problems


def fake_runner(monkeypatch, returncode: int, stdout: str, stderr: str = "",
                write: tuple[str, dict] | None = None) -> list[list[str]]:
    """The runner's subprocess, answering as given and writing `write`'s receipt."""
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        if write:
            d = R.CAPTURES / write[0]
            d.mkdir(parents=True, exist_ok=True)
            (d / "receipt.json").write_text(json.dumps(write[1]))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(R.subprocess, "run", fake)
    return calls


def fake_run(monkeypatch, **per_mode) -> list[tuple]:
    """The tool's own `run`, making each capture with capture(); per_mode maps
    "record" or "replay" to capture()'s keyword arguments, or to an exception
    to raise after the capture's directory exists, or to a list of either, one
    per call in that mode."""
    calls = []
    made: dict[str, int] = {}

    def fake(name, input_defines, captures, timeout, lock_timeout=None):
        calls.append((name, list(input_defines), timeout, lock_timeout))
        mode = "replay" if "INPUT_MODE=2" in input_defines else "record"
        spec = per_mode.get(mode, {})
        if isinstance(spec, list):
            spec = spec[made.get(mode, 0)]
        made[mode] = made.get(mode, 0) + 1
        if isinstance(spec, BaseException):
            (R.CAPTURES / name).mkdir(parents=True)
            raise spec
        return capture(name, mode=mode, input_defines=input_defines, **spec)

    monkeypatch.setattr(R, "run", fake)
    return calls


# ------------------------------------------------------------ accepting runs


def test_a_teardown_exit_with_every_capture_whole_is_accepted_and_nothing_less_is():
    whole = {"run": {"return_code": 247}, "installation_untouched": True,
             "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                            "check": {"ok": True, "problems": []}}]}
    assert R.accepted(whole) == (True, [])
    assert R.accepted({**whole, "artifacts": [{"file": "sa2-tick-capture.csv",
                                                "present": False}]}) == (
        False, ["sa2-tick-capture.csv missing"])
    assert R.accepted({**whole, "run": {"return_code": 1}}) == (False, ["exit 1"])
    assert R.accepted({**whole, "installation_untouched": False}) == (
        False, ["the installation was written"])
    assert R.accepted({**whole, "artifacts": []}) == (False, ["no artifacts"])


def test_no_capture_that_wrote_the_installation_is_whole_the_first_recording_included():
    """The user's first recording took Metal Harbor's light dash upgrade, and
    the game saved into the staged installation (2026-09-23). That receipt was
    excused by name until the user ruled on 2026-09-24 that the saves are a
    recorded input and asked for a re-record: now it is refused like any other
    capture that wrote the installation, and the likely causes say so. The same
    receipt with the installation untouched is whole, so the refusal is the
    write's and nothing else's."""
    save = "created file: resource/gd_PC/SAVEDATA/SONIC2B__S01"
    first = {"name": "mh-record-20260923-212428", "run": {"return_code": 0},
             "installation_untouched": False, "installation_differences": [save],
             "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                            "check": {"ok": True, "problems": []}}]}
    refused = (False, ["the installation was written"])
    assert R.accepted(first) == refused
    assert not hasattr(R, "PRE_ISOLATION_SAVES") and not hasattr(R, "pre_isolation_save")
    for other in (["modified file: resource/gd_PC/SAVEDATA/SONIC2B__ALF a -> b"],
                  ["created dir: resource/gd_PC/SAVEDATA/sub"], [], None):
        assert R.accepted({**first, "installation_differences": other}) == refused, other
    assert R.accepted({**first, "installation_untouched": None}) == refused
    assert R.accepted({**first, "installation_untouched": True,
                       "installation_differences": []}) == (True, [])
    assert "the game wrote into its installation: stop, and tell the agent" in R.causes(first)


def test_a_run_the_runner_found_off_the_pinned_saves_is_not_whole():
    """The runner refuses a run whose saves at start were not the pin
    (session.saves.matches_pin), and the recorder must not take it back as a
    teardown exit or for its import log. A receipt from before the field is
    judged by start_saves_pinned instead."""
    whole = {"run": {"return_code": 247}, "installation_untouched": True,
             "session": {"saves": {"matches_pin": False}},
             "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                            "check": {"ok": True, "problems": []}}]}
    assert R.accepted(whole) == (False, ["the run did not start from the pinned saves"])
    assert R.accepted({**whole, "session": {"saves": {"matches_pin": True}}}) == (True, [])
    assert R.accepted({**whole, "session": {"saves": {}}}) == (True, [])


def test_a_run_that_wrote_the_mirror_outside_its_save_directory_is_not_whole():
    """The runner refuses such a run, and the recorder must not take it back
    as a teardown exit or for its import log: a write there carries into every
    later run (tools/oracle/mirror_install.py save_path_problems)."""
    whole = {"run": {"return_code": 247}, "installation_untouched": True,
             "mirror_path_differences": ["created resource/gd_PC/NEWFILE"],
             "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                            "check": {"ok": True, "problems": []}}]}
    assert R.accepted(whole) == (False, ["the mirror was written outside its save directory"])
    assert R.accepted({**whole, "mirror_path_differences": []}) == (True, [])
    assert R.causes(whole) == ["the game wrote into the mirror outside its save directory "
                               "(MIRROR WRITTEN above): stop, and tell the agent"]


def test_the_runner_s_verdict_is_read_wherever_it_falls_in_its_output(vanilla, monkeypatch,
                                                                       capsys):
    """A note printed ahead of the verdict once made a whole replay read as
    rejected (2026-09-23). The verdict is the SA2_ORACLE_CAPTURE_ line."""
    rec = {"run": {"return_code": 0}, "installation_untouched": True,
           "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                          "check": {"ok": True, "problems": []}}]}
    fake_runner(monkeypatch, 0, "  what a previous run did to the mirror's saves, undone: "
                "['created SONIC2B__S01']\nSA2_ORACLE_CAPTURE_OK name=r exit=0\n",
                write=("r", rec))
    assert R.run("r", ["INPUT_MODE=2"], R.REPLAY_CAPTURES, 480) == rec
    assert capsys.readouterr().out.splitlines() == ["SA2_ORACLE_CAPTURE_OK name=r exit=0"]
    fake_runner(monkeypatch, 0, "a note\nno verdict at all\n", write=("r2", rec))
    with pytest.raises(SystemExit) as e:
        R.run("r2", ["INPUT_MODE=2"], R.REPLAY_CAPTURES, 480)
    assert e.value.code.startswith("r2: the run was not accepted (the runner said so).")


def test_a_run_the_runner_rejects_only_for_exit_247_is_kept_with_one_probe_set(
        vanilla, monkeypatch, capsys):
    rec = {"run": {"return_code": 247, "elapsed_seconds": 15.2}, "installation_untouched": True,
           "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                          "check": {"ok": True, "problems": []}}]}
    calls = fake_runner(monkeypatch, 1, "SA2_ORACLE_CAPTURE_PROBLEM name=r exit=247 problems=0\n"
                        "  ok sa2-tick-capture.csv rows=599 columns=7 sha256=ab\n",
                        write=("r", rec))
    assert R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480) == rec
    assert "NOTE: r exited 247 at shutdown with every capture whole; accepted as a teardown " \
           "exit." in capsys.readouterr().out
    cmd = calls[0]
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--build"] == TREE_BUILDS
    assert [a for a in cmd if a.startswith("--define=")] == [
        '--define=import_probe:INPUT_END_DLL="input_probe.dll"',
        '--define=tick_probe:INPUT_END_DLL="input_probe.dll"',
        "--define=input_probe:INPUT_MODE=1"]
    assert "--device-profile" in cmd and "--lock-timeout" not in cmd

    calls = fake_runner(monkeypatch, 0, "SA2_ORACLE_CAPTURE_OK name=r2\n", write=(
        "r2", dict(rec, run={"return_code": 0})))
    R.run("r2", ["INPUT_MODE=2"], R.REPLAY_CAPTURES, 480, lock_timeout=R.LOCK_WAIT)
    assert calls[0][calls[0].index("--lock-timeout") + 1] == "900"


def test_exit_247_is_accepted_only_where_the_runner_s_verdict_names_it(vanilla, monkeypatch):
    """A receipt with exit 247 and whole captures, and a runner that died
    before its verdict: nothing says the runner judged this run, so it is not
    taken as a teardown exit."""
    rec = {"run": {"return_code": 247}, "installation_untouched": True,
           "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                          "check": {"ok": True, "problems": []}}]}
    fake_runner(monkeypatch, 1, "", "Traceback (most recent call last): ...", write=("r", rec))
    with pytest.raises(SystemExit) as e:
        R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480)
    assert e.value.code.startswith("r: the run was not accepted (the runner said so).")


def test_a_name_already_captured_is_refused_before_the_runner_runs(vanilla, monkeypatch):
    """The runner refuses to overwrite a capture, and the old receipt beside
    it must never be read as the new run's."""
    (R.CAPTURES / "r").mkdir()
    calls = fake_runner(monkeypatch, 0, "SA2_ORACLE_CAPTURE_OK name=r\n")
    with pytest.raises(SystemExit) as e:
        R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480)
    assert e.value.code == "r: a capture by that name already exists; run this again"
    assert calls == []


def test_a_rejected_run_says_what_the_runner_found_and_the_likely_causes(vanilla, monkeypatch):
    rec = {"inputs": {"timeout_seconds": 480}, "run": {"return_code": 124},
           "installation_untouched": True,
           "artifacts": [{"file": "sa2-vanilla.csv", "present": True,
                          "check": {"ok": True, "problems": []}},
                         {"file": "sa2-input-end.csv", "present": False},
                         {"file": "sa2-device-profile.txt", "present": True, "device_profile": True,
                          "check": {"ok": False, "problems": ["line 8: a pad"]}}]}
    stdout = ("SA2_ORACLE_CAPTURE_PROBLEM name=r exit=124 problems=2\n"
              "  ok sa2-vanilla.csv rows=23 columns=5 sha256=ab\n"
              "  MISSING sa2-input-end.csv\n"
              "  REJECTED sa2-device-profile.txt counts=None sha256=cd\n"
              "      line 8: a pad\n")
    fake_runner(monkeypatch, 1, stdout, write=("r", rec))
    with pytest.raises(SystemExit) as e:
        R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480)
    assert e.value.code == (
        "r: the run was not accepted (sa2-input-end.csv missing; sa2-device-profile.txt "
        "rejected: line 8: a pad).\n"
        "SA2_ORACLE_CAPTURE_PROBLEM name=r exit=124 problems=2\n"
        "  MISSING sa2-input-end.csv\n"
        "  REJECTED sa2-device-profile.txt counts=None sha256=cd\n"
        "      line 8: a pad\n"
        "Likely causes:\n"
        "  - exit 124: the run hit its 480 s cap before the goal\n"
        "  - no sa2-input-end.csv: the run never reached its end (the goal, or its tick limit)\n"
        "  - the device check refused this machine's devices (the reason is above): unplug any "
        "gamepad and keep the keyboard's receiver plugged in")


def test_a_run_that_never_got_the_oracle_says_who_to_stop(vanilla, monkeypatch):
    fake_runner(monkeypatch, 1, "", "another oracle run holds /x/oracle-probe.lock: {}")
    with pytest.raises(SystemExit) as e:
        R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480)
    assert e.value.code.endswith(
        "Likely cause:\n  - another run holds the oracle: stop any agent capturing the game, "
        "then run this again")


# ------------------------------------------------------------ comparing runs


def test_identical_runs_compare_identical(vanilla):
    capture("a")
    capture("b")
    got = R.compare_to("a", "b")
    assert got == {"headers_same": True, "rows_same": True, "columns_same": True,
                   "masks_same": True, "digests": "as stored",
                   "tick_rows": [6, 6], "trace_rows": [6, 6], "trace_same": True,
                   "named_columns_same": True, "named_columns_differ": {},
                   "digest_blocks_differ": {}, "tick_captures_identical": True,
                   "differences_only_in_natively_unstable_blocks": True}


def test_the_unstable_blocks_pass_by_name_with_their_first_ticks_and_nothing_else_does(
        vanilla):
    capture("a")
    capture("unstable", tick_change={(3, "d_01b00000"): "ff", (5, "d_01af0000"): "ee",
                                     (6, "d_01b00000"): "ee"})
    got = R.compare_to("a", "unstable")
    assert (got["tick_captures_identical"], got["differences_only_in_natively_unstable_blocks"],
            got["named_columns_same"]) == (False, True, True)
    assert got["digest_blocks_differ"] == {"d_01b00000": 3, "d_01af0000": 5}
    # CRI's file table's block, allowed on 2026-09-24 with the user's yes (the recorder
    # review's W3: a behaviour test, not a check of the constant).
    capture("xeci", tick_change={(2, "d_01750000"): "ff"})
    got = R.compare_to("a", "xeci")
    assert (got["differences_only_in_natively_unstable_blocks"],
            got["digest_blocks_differ"]) == (True, {"d_01750000": 2})
    capture("stable", tick_change={(2, "d_01760000"): "ff"})
    got = R.compare_to("a", "stable")
    assert got["differences_only_in_natively_unstable_blocks"] is False
    assert got["digest_blocks_differ"] == {"d_01760000": 2}
    capture("named", tick_change={(4, "frame_count"): "99", (5, "d_01b00000"): "ff"})
    got = R.compare_to("a", "named")
    assert (got["named_columns_same"], got["differences_only_in_natively_unstable_blocks"],
            got["named_columns_differ"]) == (False, False, {"frame_count": 4})


@pytest.mark.parametrize("other, broken", [
    ({"ticks": 4}, "rows_same"),
    ({"tick_header": TICK_HEADER[:-1] + ["d_01770000"]}, "columns_same"),
    ({"builds": built(change={"tick_probe": {"source_sha256": "0" * 64}})}, "headers_same"),
    ({"builds": built(change={"tick_probe": {"headers_sha256": {}}})}, "headers_same"),
    ({"builds": built(change={"import_probe": {"defines": ["TICK_LIMIT=600"]}})},
     "headers_same"),
])
def test_no_tick_verdict_passes_without_comparing_like_with_like(vanilla, other, broken):
    """The review's vacuous cases: a shorter capture, another field list, a
    probe built otherwise. Each must fail every tick verdict, not pass them."""
    capture("a")
    capture("b", **other)
    got = R.compare_to("a", "b")
    assert got[broken] is False
    assert (got["tick_captures_identical"], got["named_columns_same"],
            got["differences_only_in_natively_unstable_blocks"]) == (False, False, False)


def test_the_stage_trace_compares_whole_lines_player_included(vanilla):
    capture("a")
    capture("b", player="0x0a0b0c0e")
    assert R.compare_to("a", "b")["trace_same"] is False
    capture("c", ticks=7)
    got = R.compare_to("a", "c")
    assert (got["trace_same"], got["trace_rows"]) == (False, [6, 7])


# ------------------------------------------------------------ the script


def test_a_recording_is_read_as_the_lifted_replay_reads_it(tmp_path):
    rec = tmp_path / "sa2-input.csv"
    rec.write_text(INPUT_HEADER + "4294967295,0,0xffffffff,0x0,0x0,0x0,0xffff,0x0,0x0,0x0,0x0,"
                   "0x0,0xffffffff\n")
    R.script(rec, tmp_path / "s.h")
    assert "{4294967295,0,4294967295,0,0,0,65535,0,0,0,0,0,4294967295}" in (
        tmp_path / "s.h").read_text()


@pytest.mark.parametrize("rows, words", [
    ("010,1" + ROW[3:], ":2: tick '010' is not decimal with no leading zero within 32 bits, "
                        "as the lifted replay's parser requires"),
    ("5,01" + ROW[3:], ":2: ord '01' is not decimal"),
    ("+5" + ROW[1:], ":2: tick '+5' is not decimal"),
    (" 5" + ROW[1:], ":2: tick ' 5' is not decimal"),
    ("4294967296" + ROW[1:], ":2: tick '4294967296' is not decimal"),
    ("5,1,0x0400" + ROW[9:], ":2: on '0x0400' is not 0x and lower-case hex with no leading "
                             "zero within 4 bytes"),
    ("5,1,0X400" + ROW[9:], ":2: on '0X400' is not 0x"),
    ("5,1,400" + ROW[9:], ":2: on '400' is not 0x"),
    ("5,1,0x400,0xfffffbff,0x400,0x0,0x10000,0x0,0x0,0xff81,0x0,0x0,0x0",
     ":2: r '0x10000' is not 0x and lower-case hex with no leading zero within 2 bytes"),
    (ROW + ",0x0", ":2: 14 columns, not 13"),
    (ROW + "\n" + ROW, ":3: rows out of (tick, ord) order"),
])
def test_a_row_the_lifted_replay_would_refuse_is_refused_here_too(tmp_path, rows, words):
    rec = tmp_path / "sa2-input.csv"
    rec.write_text(INPUT_HEADER + rows + "\n")
    with pytest.raises(SystemExit) as e:
        R.script(rec, tmp_path / "s.h")
    assert words in e.value.code
    assert not (tmp_path / "s.h").exists()


def test_a_file_that_is_not_the_recorder_s_is_refused(tmp_path):
    rec = tmp_path / "sa2-input.csv"
    rec.write_text("tick,ord,matched\n1,0,1\n")
    with pytest.raises(SystemExit) as e:
        R.script(rec, tmp_path / "s.h")
    assert e.value.code == (f"{rec}: the header is not "
                            "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old")


# ------------------------------------------------------------ the preflight


def test_the_preflight_times_the_game_and_replays_its_own_recording(vanilla, monkeypatch):
    calls = fake_run(monkeypatch, record={"goal": 0, "end": 7, "elapsed": 15.0},
                     replay={"goal": 0, "end": 7, "elapsed": 15.3})
    pre = R.preflight("t")
    assert pre["checks"] == dict.fromkeys(
        ["paced", "playable", "runs_the_games_own_code", "same_code_restored",
         "same_device_profile", "same_oracle_mode", "no_missed_rows", "same_probes",
         "tick_rows_same", "stage_trace_same", "named_columns_same",
         "differences_only_in_natively_unstable_blocks", "import_log_at_last_tick",
         "start_saves_pinned", "same_game_environment"], True)
    assert (pre["elapsed_seconds"], pre["end_ticks"]) == (15.0, [7, 7])
    assert pre["game_environment"] == R.RP.environment_record(GAME_ENV)["sha256"]
    assert pre["start_saves"] == {"pin": {"file": str(R.PIN), "digest": R.SS.digest(SAVES),
                                          "state": "a made-up start"},
                                  "mh-preflight-t": SAVES, "mh-preflight-t-replay": SAVES}
    assert (pre["elapsed_seconds_each"], pre["pace_of_replay"]) == ([15.0, 15.3], 1.0)
    assert pre["import_logs"] == {n: {"file": "sa2-import-calls.csv", "counted_at_tick": 6,
                                      "last_captured_tick": 6, "whole_run": True}
                                  for n in ("mh-preflight-t", "mh-preflight-t-replay")}
    assert calls == [
        ("mh-preflight-t", ["INPUT_MODE=1", "TICK_LIMIT=600"], 200, None),
        ("mh-preflight-t-replay",
         ["INPUT_MODE=2", f'INPUT_SCRIPT="{R.SCRIPTS / "mh-preflight-t.h"}"', "TICK_LIMIT=7"],
         200, None)]


def test_an_unpaced_game_fails_the_preflight_however_long_the_runner_took(vanilla, monkeypatch):
    """Pace is the game's own time for 600 ticks, and 9.0 s (90 % of 10 s) is
    the least that passes. The unpaced game measured about half of that
    (module docstring); the runner's time around it is not what is read."""
    fake_run(monkeypatch, record={"goal": 0, "end": 7, "elapsed": 9.0},
             replay={"goal": 0, "end": 7})
    assert R.preflight("t")["checks"]["paced"] is True
    fake_run(monkeypatch, record={"goal": 0, "end": 7, "elapsed": 8.9},
             replay={"goal": 0, "end": 7})
    with pytest.raises(SystemExit) as e:
        R.preflight("u")
    assert e.value.code.startswith("PREFLIGHT_FAILED (paced), and nothing was recorded.")


@pytest.mark.parametrize("record, replay, paced", [
    (16.5, 15.0, True),
    (16.6, 15.0, False),        # fell behind: the limiter is a floor
    (26.4, 15.0, False),        # mh-preflight-20260923-212312, the desktop in use
    (15.0, 19.9, False),        # its replay: the second pass is held to it too
])
def test_a_game_that_fell_behind_fails_the_preflight(vanilla, monkeypatch, record, replay, paced):
    """The frame limiter holds each frame to at least 1/60 s and never makes up
    a slow one, so a run can fall behind unnoticed by the game: 16.5 s is the
    most that passes, for either pass (PACED_SECONDS)."""
    fake_run(monkeypatch, record={"goal": 0, "end": 7, "elapsed": record},
             replay={"goal": 0, "end": 7, "elapsed": replay})
    if paced:
        assert R.preflight("t")["checks"]["paced"] is True
        return
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith("PREFLIGHT_FAILED (paced), and nothing was recorded.")


@pytest.mark.parametrize("record, replay", [
    (SAVES, {**SAVES, "SONIC2B__S01": "b" * 64}),     # the pickup's save left in place
    ({"SONIC2B__ALF": "b" * 64}, {"SONIC2B__ALF": "b" * 64}),   # alike, and not the pin
    (None, None),                                     # nothing recorded is not a match
])
def test_a_preflight_whose_passes_did_not_start_from_the_pinned_saves_fails(
        vanilla, monkeypatch, record, replay):
    """Both passes must start from the pin, not merely from the same saves."""
    fake_run(monkeypatch, record={"goal": 0, "end": 7, "saves": record},
             replay={"goal": 0, "end": 7, "saves": replay})
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith("PREFLIGHT_FAILED (start_saves_pinned), and nothing was "
                                   "recorded.")


def test_a_preflight_whose_passes_had_other_game_environments_fails(vanilla, monkeypatch):
    fake_run(monkeypatch, record={"goal": 0, "end": 7},
             replay={"goal": 0, "end": 7, "environment": OTHER_ENV})
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith("PREFLIGHT_FAILED (same_game_environment), and nothing "
                                   "was recorded.")
    fake_run(monkeypatch, record={"goal": 0, "end": 7, "environment": None},
             replay={"goal": 0, "end": 7, "environment": None})
    with pytest.raises(SystemExit) as e:          # nothing recorded is not the same
        R.preflight("u")
    assert e.value.code.startswith("PREFLIGHT_FAILED (same_game_environment)")


def test_a_pin_that_cannot_be_read_stops_the_preflight_before_any_run(vanilla, monkeypatch):
    R.PIN.write_text(R.PIN.read_text().replace('"digest": "', '"digest": "0'))
    calls = fake_run(monkeypatch)
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert calls == []
    assert e.value.code.startswith("PREFLIGHT_FAILED, and nothing was recorded.\n")
    assert "its digest" in e.value.code


def test_a_run_the_runner_refused_for_unpinned_saves_says_how_to_restore_them(
        vanilla, monkeypatch):
    """The runner stops before building anything when the staged installation's
    saves are not the pin, with no receipt; the likely cause names the command."""
    pin = R.SS.load(R.PIN)
    fake_runner(monkeypatch, 1, "", R.SS.refusal(Path("private/install"), pin,
                                                 ["extra SONIC2B__S01 (e3b0c44298fc)"]))
    with pytest.raises(R.Stop) as e:
        R.run("mh-preflight-t", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 200)
    assert e.value.code.startswith("mh-preflight-t: the run was not accepted (the run left no "
                                   "receipt).")
    assert ("  - the staged installation's saves are not the pinned start state: run "
            f"`{R.SS.RESTORE}` from {R.ROOT}, then this again") in e.value.code


@pytest.mark.parametrize("replay, failed", [
    ({"goal": 0, "end": 7, "tick_change": {(4, "frame_count"): "99"}},
     "named_columns_same, differences_only_in_natively_unstable_blocks"),
    ({"goal": 0, "end": 7, "profile": OTHER_PROFILE}, "same_device_profile"),
    ({"goal": 0, "end": 7, "misses": 3}, "no_missed_rows"),
    ({"goal": 0, "end": 7, "player": "0x0"}, "stage_trace_same"),
    ({"goal": 0, "end": 7, "vanilla_csv": VANILLA_CSV.replace(",restored", ",kept")},
     "same_code_restored"),
    ({"goal": 0, "end": 7, "import_at": 5}, "import_log_at_last_tick"),
])
def test_the_preflight_holds_its_replay_to_the_session_s_verdict(vanilla, monkeypatch, replay,
                                                                  failed):
    fake_run(monkeypatch, record={"goal": 0, "end": 7}, replay=replay)
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith(f"PREFLIGHT_FAILED ({failed}), and nothing was recorded.")


def test_a_preflight_that_runs_loader_code_fails(vanilla, monkeypatch):
    vanilla["mh-preflight-t"] = ["0x00500100 kept is not a KEEP range"]
    fake_run(monkeypatch, record={"goal": 0, "end": 7}, replay={"goal": 0, "end": 7})
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith("PREFLIGHT_FAILED (runs_the_games_own_code)")


def test_a_preflight_with_no_vanilla_rows_has_not_shown_the_same_code_restored(
        vanilla, monkeypatch):
    """Two empty logs are equal, and show nothing restored."""
    fake_run(monkeypatch, record={"goal": 0, "end": 7, "vanilla_csv": ""},
             replay={"goal": 0, "end": 7, "vanilla_csv": ""})
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith("PREFLIGHT_FAILED (same_code_restored)")


def test_preflight_only_runs_both_passes_prints_the_verdict_and_never_prompts(
        vanilla, monkeypatch, capsys):
    def no_prompt(prompt=""):
        raise AssertionError("prompted")

    monkeypatch.setattr(builtins, "input", no_prompt)
    calls = fake_run(monkeypatch, record={"goal": 0, "end": 7}, replay={"goal": 0, "end": 7})
    assert R.main(["--preflight-only"]) == 0
    out = capsys.readouterr().out
    assert [c[0].startswith("mh-preflight-") for c in calls] == [True, True]
    assert out.splitlines()[-1].startswith('PREFLIGHT_OK {"paced": true,')
    assert "Stop every agent" not in out


def test_preflight_only_fails_with_exit_1_and_names_the_check(vanilla, monkeypatch, capsys):
    fake_run(monkeypatch, record={"goal": 0, "end": 7}, replay={"goal": 0, "end": 7, "misses": 1})
    assert R.main(["--preflight-only"]) == 1
    out = capsys.readouterr().out
    assert "STOPPED: PREFLIGHT_FAILED (no_missed_rows), and nothing was recorded." in out
    assert "PREFLIGHT_OK" not in out


def test_preflight_only_and_replay_only_are_not_taken_together():
    with pytest.raises(SystemExit) as e:
        R.main(["--preflight-only", "--replay-only", "mh-record-t"])
    assert e.value.code == 2


# ------------------------------------------------------------ the session


def summary_of(name: str) -> dict:
    [path] = (R.CAPTURES / name).glob("replays-*.json")
    return json.loads(path.read_text())


def test_replays_identical_to_each_other_pass_as_ok_and_say_what_was_allowed(
        vanilla, monkeypatch, capsys):
    """Both replays differ from the recording in one unstable block, the same
    way, so they reproduce each other's tick captures: stage-route item 5
    holds, and the allowance used against the recording is still printed."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay={"tick_change": {(3, "d_01b00000"): "ff"}})
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    r1, r2 = (r["name"] for r in s["replays"])
    assert all(s["checks"].values()) and s["replays_agree"] is True
    assert (s["stage_route_item_5_met"], s["passed_by_allowance"]) == (True, False)
    assert s["reported"]["tick_captures_identical"] == {r1: False, r2: False}
    assert s["reported"]["replays_tick_captures_identical_to_each_other"] is True
    assert s["reported"]["natively_unstable_blocks_differ"] == {r1: {"d_01b00000": 3},
                                                                r2: {"d_01b00000": 3}}
    assert s["allowance"] == R.ALLOWANCE
    assert "RECORD_STAGE_OK: both replays reached the goal on tick 4 and match your run." in out
    assert "RECORD_STAGE_OK_BY_ALLOWANCE" not in out and R.ITEM_5_NOT_MET not in out
    assert f"  Not byte-identical: in {r1} the digest blocks d_01b00000 from tick 3 differ." in out
    assert ("  Why: three pairs of idle native runs differed, under the mask before 2026-09-24, in\n"
            "    exactly the last three blocks:\n"
            "    mh-recordcfg-check-v2a vs mh-preflight-20260923-090333,\n"
            "    mh-spawn-v1a vs mh-spawn-v1b,\n"
            "    mh-snap-vanilla-v1c vs mh-snap-vanilla-v1d.\n"
            "    Once the mask compared CRI's file table (2026-09-24), an idle pair differed in\n"
            "    d_01750000 too, on 32 scattered ticks, as CRI's read status does when a read is\n"
            "    still in flight at a tick's end in one run only:\n"
            "    mh-preflight-20260924-201834 vs mh-preflight-20260924-201834-replay.\n"
            "    Its file and event handles are Wine's numbers, which that pair did not show\n"
            "    varying and a replay can (agents-journals/s4/2026-09-24-tick-380-xeci.md).\n"
            "  Caveat: it allows whole 64 KiB blocks, which also hold game words (about 13 in\n"
            "    d_01af0000, 5 in d_01b10000), so a gameplay difference in those words would "
            "pass.\n"
            "    In d_01750000 it hides all 34 compared words: entry 0 of CRI's file table (27:\n") in out
    # The file-table block is allowed (2026-09-24, the user's yes after a preflight
    # failed on it) and a block outside the list is still not.
    assert "d_01750000" in R.NATIVE_UNSTABLE and "d_01760000" not in R.NATIVE_UNSTABLE
    assert "as they do between any two native runs" not in out


def test_replays_that_differ_from_each_other_pass_only_by_the_allowance_and_say_so(
        vanilla, monkeypatch, capsys):
    """Stage-route item 5's literal clause is false: each replay passes the
    allowance against the recording, but not in the same way, so the replays'
    tick captures differ. The session is kept (exit 0) and the distinction is
    printed and written."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay=[{"tick_change": {(3, "d_01b00000"): "ff"}},
                                  {"tick_change": {(5, "d_01af0000"): "ee"}}])
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    assert all(s["checks"].values()) and s["replays_agree"] is True
    assert s["reported"]["replays_tick_captures_identical_to_each_other"] is False
    assert (s["stage_route_item_5_met"], s["passed_by_allowance"]) == (False, True)
    assert s["reported"]["stage_route_item_5_met"] is False
    assert "RECORD_STAGE_OK:" not in out
    assert ("RECORD_STAGE_OK_BY_ALLOWANCE: both replays reached the goal on tick 4; they match "
            "your run only by the allowance below.\n"
            "  stage-route item 5 (replays reproduce each other's tick captures) is not met; the "
            "allowance for the natively unstable blocks stands until the stage has its own "
            "digest mask, and whether item 5 may pass by it is the user's ruling\n") in out


@pytest.mark.parametrize("goals, met", [([5, 5], True), ([4, 5], False), ([0, 0], False)])
def test_item_5_also_needs_both_replays_to_set_the_goal_latch_on_one_tick(
        vanilla, monkeypatch, goals, met):
    """Item 5 is about the two replays, not the recording: goal ticks equal to
    each other and set, whatever the recording's (tick 4 here)."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay=[{"goal": g} for g in goals])
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    s = summary_of("mh-record-t")
    assert s["reported"]["replays_tick_captures_identical_to_each_other"] is True
    assert (s["stage_route_item_5_met"], s["passed_by_allowance"]) == (met, False)


def test_byte_identical_replays_pass_as_ok_with_no_allowance_printed(vanilla, monkeypatch,
                                                                      capsys):
    capture("mh-record-t")
    fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    assert (s["stage_route_item_5_met"], s["passed_by_allowance"]) == (True, False)
    assert "RECORD_STAGE_OK: " in out and "Caveat:" not in out and "Not byte-identical" not in out


# A capture compare_to cannot line up with the recording fails every
# tick-capture verdict with it, not only the check that names why.
UNCOMPARED = ["named_columns_as_recorded", "differences_only_in_natively_unstable_blocks"]


@pytest.mark.parametrize("replay, failed", [
    ({"goal": 5}, ["goal_on_recording_tick"]),
    ({"tick_change": {(2, "d_01760000"): "ff"}},
     ["differences_only_in_natively_unstable_blocks"]),
    ({"ticks": 5}, ["tick_rows_as_recorded", "stage_trace_as_recorded"] + UNCOMPARED),
    ({"profile": OTHER_PROFILE}, ["same_device_profile_as_recording"]),
    ({"builds": built(["INPUT_MODE=2"], {"tick_probe": {"source_sha256": "0" * 64}})},
     ["same_probes_as_recording"] + UNCOMPARED),
    ({"vanilla_csv": VANILLA_CSV.replace(",restored", ",kept")}, ["same_code_restored"]),
])
def test_a_replay_that_disagrees_fails_by_name_and_leaves_the_way_back(vanilla, monkeypatch,
                                                                        capsys, replay, failed):
    capture("mh-record-t")
    fake_run(monkeypatch, replay=replay)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    assert [k for k, v in s["checks"].items() if not v] == failed
    assert s["passed_by_allowance"] is False
    assert f"RECORD_STAGE_REPLAYS_DISAGREE: {', '.join(failed)}\n" in out
    assert "RECORD_STAGE_OK" not in out
    assert R.resume_command("mh-record-t") in out


def test_a_replay_slower_than_its_recording_is_named_not_failed(vanilla, monkeypatch, capsys):
    """The pace is a floor, so a replay can run slower than its recording and
    still reproduce it: the ratio of CRI's timer calls over the same ticks is
    reported for each replay, and one off by more than 1 % is named. 1.045 is
    replay1 of the user's recording (22224 calls against 21264)."""
    capture("mh-record-t", timer=21264)
    fake_run(monkeypatch, replay=[{"timer": 22224}, {"timer": 21260}])
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    r1, r2 = (r["name"] for r in s["replays"])
    assert s["reported"]["pace_against_recording"] == {r1: 1.0451, r2: 0.9998}
    assert (s["reported"]["pace_off_by_more_than_tolerance"], s["reported"]["pace_unknown"]) == (
        [r1], [])
    assert all(s["checks"].values())
    assert (f"  WARNING: {r1} ran 4.5 % longer than the recording by CRI's timer calls. The "
            "pace is a floor, not a lock, and state the game drives from real time follows it "
            "(Documentation/risks.json, Determinism).") in out
    assert f"WARNING: {r2}" not in out


def test_a_pace_over_different_spans_is_unknown_not_a_ratio(vanilla, monkeypatch, capsys):
    """Logs counted at different ticks cover different spans, so they give no
    ratio. A replay whose timer was never called gives 0, which is off and
    named like any other."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay=[{"import_at": 5}, {"timer": 0}])
    R.main(["--replay-only", "mh-record-t"])
    s = summary_of("mh-record-t")
    r1, r2 = (r["name"] for r in s["replays"])
    assert s["reported"]["pace_against_recording"] == {r1: None, r2: 0.0}
    assert (s["reported"]["pace_unknown"], s["reported"]["pace_off_by_more_than_tolerance"]) == (
        [r1], [r2])
    assert f"  WARNING: the pace of {r1} against the recording is unknown" in \
        capsys.readouterr().out


def judged_directly(name: str, stamp: str = "s") -> tuple[dict, list[dict]]:
    """The recording `name`, replayed twice through the tool's replay() and
    judged by judge() on its own, with nothing before it (check_recording) to
    refuse the recording first; the checks and the replays. The replays are
    named `name`-replayN-`stamp`."""
    end = R.end_of(name)
    R.script(R.CAPTURES / name / "sa2-input.csv", R.SCRIPTS / f"{name}.h")
    reps = R.replay(name, stamp, end)
    assert [r["error"] for r in reps] == [None, None]
    return R.judge(name, end, reps)["checks"], reps


def test_every_replay_must_start_from_the_pinned_saves(vanilla, monkeypatch):
    """A save left in place changes what the stage offers (the user's ruling of
    2026-09-24 makes the saves a recorded input), and only a receipt's own
    record shows where a run started: a replay whose receipt records no start
    saves fails as one that started elsewhere does, whatever an install-before
    snapshot beside it says (the save-state review of 2026-09-24, W3)."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay=[{}, {"saves": {**SAVES, "SONIC2B__S01": "b" * 64}}])
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    assert [k for k, v in summary_of("mh-record-t")["checks"].items() if not v] == [
        "start_saves_pinned"]

    capture("mh-record-u")
    snapshots = R.CAPTURES.parent / "snapshots"      # where the runner writes them, beside
    snapshots.mkdir()                                # its captures, each showing the pin
    for name in ("mh-record-u", "mh-record-u-replay1-s", "mh-record-u-replay2-s"):
        (snapshots / f"{name}-install-before.json").write_text(json.dumps({"files": {
            "resource/gd_PC/SAVEDATA/SONIC2B__ALF": "a" * 64}}))
    fake_run(monkeypatch, replay=[{}, {"saves": None}])
    checks, reps = judged_directly("mh-record-u")
    assert [k for k, v in checks.items() if not v] == ["start_saves_pinned"]
    assert R.start_saves(reps[1]["name"]) is None and not hasattr(R, "SNAPSHOTS")


@pytest.mark.parametrize("saves", [None, {"SONIC2B__ALF": "b" * 64},
                                   {**SAVES, "SONIC2B__S01": "b" * 64}])
def test_a_recording_that_did_not_start_from_the_pinned_saves_is_recorded_again(
        vanilla, monkeypatch, capsys, saves):
    """No replay can start from what it started from, so none is spent on it:
    nothing recorded, other bytes, or a save left in place."""
    capture("mh-record-t", saves=saves)
    calls = fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert calls == []
    began = "its receipt records none" if saves is None else f"it started from {saves}"
    assert (f"STOPPED: mh-record-t did not start from the pinned saves ({R.PIN}, a made-up "
            f"start): {began}, and every replay starts from the pin.") in out
    assert "record again" in out and "--replay-only" not in out


def test_the_verdict_holds_the_recording_itself_and_reads_no_pin_as_no_match(
        vanilla, monkeypatch):
    """judge() on its own, as a caller other than --replay-only would use it,
    with nothing before it to refuse the recording first: a recording off the
    pin fails start_saves_pinned though its replays are on it; a recording and
    replays that all record no game environment are not the same environment;
    and a pin that cannot be read matches nothing (the probe review of
    2026-09-24, nit 4; the save-state review, N7)."""
    def judged(name: str, **recording) -> dict:
        capture(name, **recording)
        return judged_directly(name)[0]

    fake_run(monkeypatch)
    checks = judged("mh-record-a")
    assert all(checks.values()), checks                 # the control: every check holds
    fake_run(monkeypatch)
    checks = judged("mh-record-b", saves={**SAVES, "SONIC2B__S01": "b" * 64})
    assert [k for k, v in checks.items() if not v] == ["start_saves_pinned"]
    fake_run(monkeypatch, replay={"environment": None})
    checks = judged("mh-record-c", environment=None)
    assert [k for k, v in checks.items() if not v] == ["same_game_environment_as_recording"]
    R.PIN.write_text("{")
    fake_run(monkeypatch)
    checks = judged("mh-record-d")
    assert [k for k, v in checks.items() if not v] == ["start_saves_pinned"]


def test_a_replay_launched_with_another_game_environment_fails_the_verdict(
        vanilla, monkeypatch):
    """Native heap addresses follow the environment the game was launched
    with (agents-journals/s4/2026-09-23-recording-session.md), so a replay
    launched with another shows nothing about its recording's."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay=[{}, {"environment": OTHER_ENV}])
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    assert [k for k, v in summary_of("mh-record-t")["checks"].items() if not v] == [
        "same_game_environment_as_recording"]
    capture("mh-record-u")
    fake_run(monkeypatch, replay={"environment": None})
    assert R.main(["--replay-only", "mh-record-u"]) == 1       # none recorded is not the same
    assert [k for k, v in summary_of("mh-record-u")["checks"].items() if not v] == [
        "same_game_environment_as_recording"]


def test_replay_only_from_a_shell_that_would_launch_another_environment_stops_first(
        vanilla, monkeypatch, capsys):
    """Every replay would be refused, so none runs. The variables are named,
    never their values, and the way back is printed: the shell is at fault,
    not the recording."""
    capture("mh-record-t")
    monkeypatch.setattr(R, "launch_environment", lambda: {**OTHER_ENV, "EXTRA": "x"})
    calls = fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert calls == []
    assert ("STOPPED: mh-record-t was recorded with another game environment than a run from "
            "this shell would get (EXTRA, LANG differ).") in out
    assert "en_GB" not in out and "C.UTF-8" not in out
    assert f"    {R.resume_command('mh-record-t')}\n" in out


def test_a_recording_with_no_game_environment_is_recorded_again(vanilla, monkeypatch, capsys):
    capture("mh-record-t", environment=None)
    calls = fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert calls == []
    assert ("STOPPED: mh-record-t's receipt records no game environment, so no replay can be "
            "shown to have been launched with the same one.") in out
    assert "record again" in out


def test_the_saves_each_run_ended_with_are_reported_not_judged(vanilla, monkeypatch):
    """The pickup writes a save; whether each replay wrote the recording's is
    reported beside the verdict, which it does not decide."""
    after = {**SAVES, "SONIC2B__S01": "c" * 64}
    capture("mh-record-t", saves_at_end=after)
    fake_run(monkeypatch, replay=[{"saves_at_end": after}, {"saves_at_end": SAVES}])
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    s = summary_of("mh-record-t")
    r1, r2 = (r["name"] for r in s["replays"])
    assert s["reported"]["saves_at_end"] == {"mh-record-t": after, r1: after, r2: SAVES}
    assert s["reported"]["replays_end_with_the_recording_s_saves"] is False
    capture("mh-record-u", saves_at_end=after)
    fake_run(monkeypatch, replay={"saves_at_end": after})
    assert R.main(["--replay-only", "mh-record-u"]) == 0
    assert summary_of("mh-record-u")["reported"]["replays_end_with_the_recording_s_saves"] is True


def test_a_recording_with_no_vanilla_rows_has_not_shown_the_same_code_restored(
        vanilla, monkeypatch):
    capture("mh-record-t", vanilla_csv="")
    fake_run(monkeypatch, replay={"vanilla_csv": ""})
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    assert summary_of("mh-record-t")["checks"]["same_code_restored"] is False


def test_teardown_exits_are_named_in_the_summary(vanilla, monkeypatch):
    capture("mh-record-t", rc=247)
    fake_run(monkeypatch, replay=[{"rc": 0}, {"rc": 247}])
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    s = summary_of("mh-record-t")
    r1, r2 = (r["name"] for r in s["replays"])
    assert s["reported"]["teardown_exits"] == ["mh-record-t", r2]
    assert (s["recording_run"]["teardown_exit"], [r["teardown_exit"] for r in s["replays"]]) == (
        True, [False, True])


def test_a_replay_that_ran_loader_code_is_reported_and_the_summary_still_written(
        vanilla, monkeypatch, capsys):
    capture("mh-record-t")
    calls = fake_run(monkeypatch)
    vanilla_for_replays = ["a tick1-rewritten row at 0x00500200"]

    def problems(d):
        return vanilla_for_replays if "-replay" in Path(d).name else []

    monkeypatch.setattr(R.OM, "vanilla_problems", problems)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    s = summary_of("mh-record-t")
    assert s["checks"]["runs_the_games_own_code"] is False
    assert [r["vanilla_problems"] for r in s["replays"]] == [vanilla_for_replays] * 2
    assert [c[3] for c in calls] == [900, 900]


def test_a_vanilla_check_that_refuses_outright_becomes_a_reason_not_a_crash(
        vanilla, monkeypatch, capsys):
    """oracle_mode raises ModeError (a SystemExit) where it cannot read a mode
    at all. For a replay that must become the replay's reason, and the
    summary must still be written."""
    capture("mh-record-t")
    fake_run(monkeypatch)

    def refuse(d):
        if "-replay" in Path(d).name:
            raise R.OM.ModeError(f"{Path(d).name}: no receipt.json, so its oracle mode is unknown")
        return []

    monkeypatch.setattr(R.OM, "vanilla_problems", refuse)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    s = summary_of("mh-record-t")
    assert [r["vanilla_problems"] for r in s["replays"]] == [
        [f"{r['name']}: no receipt.json, so its oracle mode is unknown"] for r in s["replays"]]
    assert s["checks"]["runs_the_games_own_code"] is False
    assert "RECORD_STAGE_REPLAYS_DISAGREE: runs_the_games_own_code\n" in capsys.readouterr().out


def test_a_replay_from_another_oracle_mode_fails_the_verdict(vanilla, monkeypatch):
    capture("mh-record-t")
    fake_run(monkeypatch)

    def modes(caps, *a, **k):
        if any("-replay" in Path(c).name for c in caps):
            raise R.OM.ModeError("the recording and its replay were produced by different "
                                 "oracle modes")
        return {"mode": "vanilla"}

    monkeypatch.setattr(R.OM, "require_same", modes)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    assert summary_of("mh-record-t")["checks"]["same_oracle_mode_as_recording"] is False


def test_a_replay_the_runner_rejects_ends_the_series_and_keeps_the_recording(
        vanilla, monkeypatch, capsys):
    capture("mh-record-t")
    calls = fake_run(monkeypatch, replay=R.Stop("r: the run was not accepted (exit 1)."))
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    assert len(calls) == 1 and s["checks"]["every_replay_ran"] is False
    assert s["replays"][0]["error"] == "r: the run was not accepted (exit 1)."
    assert (f"    cd {R.ROOT} && source tools/env.sh && .venv/bin/python "
            "tools/oracle/record_stage.py --replay-only mh-record-t") in out


def edit_receipt(name: str, **fields) -> None:
    path = R.CAPTURES / name / "receipt.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **fields}))


@pytest.mark.parametrize("damage, why", [
    ({"run": {"return_code": 1}}, "exit 1"),
    ({"installation_untouched": None}, "the installation was written"),
    ({"artifacts": [{"file": "sa2-input-end.csv", "present": False}]},
     "sa2-input-end.csv missing"),
])
def test_replay_only_refuses_a_recording_that_is_not_whole_and_says_record_again(
        vanilla, monkeypatch, capsys, damage, why):
    capture("mh-record-t")
    edit_receipt("mh-record-t", **damage)
    calls = fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert calls == []
    assert f"STOPPED: mh-record-t is not a whole capture: {why}\n" in out
    assert f"This recording cannot be replayed, so record again:\n    {R.record_command()}\n" in out
    assert "--replay-only" not in out


@pytest.mark.parametrize("change, files", [
    ({"tick_probe": {"source_sha256": "0" * 64}}, "harness/replay/native_tick_probe.c"),
    ({"tick_probe": {"headers_sha256": {"tick_fields.h": "0" * 64}}}, None),
    ({"import_probe": {"defines": ["TICK_LIMIT=600"]}}, "tools/oracle/record_stage.py"),
])
def test_replay_only_names_the_files_that_moved_since_the_recording_and_keeps_the_way_back(
        vanilla, monkeypatch, capsys, change, files):
    """An agent's edit between the recording and its replays stops the
    session. The files are named, and putting them back is what makes the
    printed command work, so the command is printed."""
    builds = built(change=change)
    if files is None:       # the field list the tick probe includes, recorded as another
        tree = built()[0]["headers_sha256"]
        for b in builds:
            if b["mod_id"] == "tick_probe":
                b["headers_sha256"] = {**tree, "tick_fields.h": "0" * 64}
        files = "harness/replay/tick_fields.h"
    capture("mh-record-t", builds=builds)
    calls = fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert calls == []
    assert (f"STOPPED: mh-record-t cannot be replayed with the tree as it is now. These files "
            f"changed since it was recorded: {files}. Put them back as they were, and the command "
            f"below works.\n") in out
    assert f"    {R.resume_command('mh-record-t')}\n" in out


def test_replay_only_refuses_a_recording_that_ran_loader_code_or_missed_the_goal(
        vanilla, monkeypatch, capsys):
    capture("mh-record-t")
    vanilla["mh-record-t"] = ["no sa2-vanilla.csv"]
    fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert "STOPPED: mh-record-t did not run the game's own code: no sa2-vanilla.csv" in out
    assert "record again" in out and "--replay-only" not in out
    capture("mh-record-u", goal=0)
    assert R.main(["--replay-only", "mh-record-u"]) == 1
    out = capsys.readouterr().out
    assert "the goal latch never rose" in out
    assert "record again" in out and "--replay-only" not in out


def session_with(monkeypatch, recording) -> tuple[list[str], str]:
    """The session end to end with its prompt answered: the preflight passes,
    and `recording(name)` makes the recording's run."""
    calls = []

    def fake(name, input_defines, captures, timeout, lock_timeout=None):
        calls.append(name)
        if name.startswith("mh-preflight-"):
            mode = "replay" if "INPUT_MODE=2" in input_defines else "record"
            return capture(name, mode=mode, goal=0, end=7, input_defines=input_defines)
        return recording(name)

    monkeypatch.setattr(R, "run", fake)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert R.main([]) == 1
    assert calls[-1].startswith("mh-record-") and len(calls) == 3
    return calls, calls[-1]


def test_a_whole_recording_the_runner_did_not_pass_prints_the_way_back(
        vanilla, monkeypatch, capsys):
    """The receipt shows a whole capture, which --replay-only accepts, so its
    command is printed."""
    def recording(name):
        capture(name)
        raise R.Stop(f"{name}: the run was not accepted (the runner said so).")

    _, name = session_with(monkeypatch, recording)
    out = capsys.readouterr().out
    assert "Stop every agent working in this repository until the replays finish." in out
    assert f"STOPPED: {name}: the run was not accepted (the runner said so)." in out
    assert f"    {R.resume_command(name)}" in out


@pytest.mark.parametrize("receipt", [False, True])
def test_a_recording_the_tool_rejected_says_record_again_not_replay(
        vanilla, monkeypatch, capsys, receipt):
    """No receipt, or one that is not a whole capture: --replay-only would
    refuse it, so its command is not printed."""
    def recording(name):
        if receipt:
            capture(name, rc=124)
            edit_receipt(name, artifacts=[{"file": "sa2-input-end.csv", "present": False}])
        else:
            (R.CAPTURES / name).mkdir(parents=True)
        raise R.Stop(f"{name}: the run was not accepted (sa2-input-end.csv missing).")

    _, name = session_with(monkeypatch, recording)
    out = capsys.readouterr().out
    assert f"STOPPED: {name}: the run was not accepted (sa2-input-end.csv missing)." in out
    assert f"This recording cannot be replayed, so record again:\n    {R.record_command()}\n" in out
    assert "--replay-only" not in out


def test_a_run_near_the_cap_is_warned_about():
    """A replay can take longer than its recording (the slowest measured, 4.5 %
    longer), so the warning comes where a replay that slow would be within
    15 s of the cap."""
    assert R.cap_warning({"run": {"elapsed_seconds": 442.8}}) is None
    assert R.cap_warning({"run": {"elapsed_seconds": 443.0}}) == (
        "WARNING: your run took 443 s of the 480 s cap, and a replay can take longer than the "
        "run it replays. If a replay stops with exit 124, record again and finish sooner.")


# ------------------------------------------------------------ the import log


def test_the_import_probe_is_told_to_find_the_input_probe_by_the_dll_the_runner_builds():
    """The runner names each probe's DLL after its mod id; the import probe
    asks that DLL for the tick the run ends on, so the define must name the
    input probe's mod as the tool builds it."""
    assert dict(R.PROBES)[R.INPUT_MOD] == R.PROBE
    assert R.DEFINES == {"import_probe": ['INPUT_END_DLL="input_probe.dll"'],
                         "tick_probe": ['INPUT_END_DLL="input_probe.dll"']}
    for mod in ("import_probe", "tick_probe"):
        cmd = [a for a in R.tree_builds() if a[0] == mod]
        assert cmd[0][4] == ['INPUT_END_DLL="input_probe.dll"']


@pytest.mark.parametrize("pass_, log", [
    ("mh-preflight-t", "slot,calls,ours,tick\n0x00900000,5,1,5\n0x00900004,0,1,5\n"),
    ("mh-preflight-t-replay", "slot,calls,ours,tick\nrefused,0,0,0\ninput_probe.dll is not "
                              "loaded or does not export SA2InputProbeEnd; the tick the run "
                              "ends on is unknown"),
    ("mh-preflight-t", "slot,calls,ours,tick\n0x00900000,5,1,6\n"),
])
def test_a_preflight_pass_whose_log_misses_its_last_tick_stops_the_session(
        vanilla, monkeypatch, pass_, log):
    """Counted a tick early, refused by the probe, or cut short: each leaves
    the log short of the run, and the preflight says so before anyone plays."""
    def fake(name, input_defines, captures, timeout, lock_timeout=None):
        mode = "replay" if "INPUT_MODE=2" in input_defines else "record"
        rec = capture(name, mode=mode, goal=0, end=7, input_defines=input_defines)
        if name == pass_:
            (R.CAPTURES / name / "sa2-import-calls.csv").write_text(log)
        return rec

    monkeypatch.setattr(R, "run", fake)
    with pytest.raises(SystemExit) as e:
        R.preflight("t")
    assert e.value.code.startswith("PREFLIGHT_FAILED (import_log_at_last_tick), and nothing "
                                   "was recorded.")


def test_the_session_reports_what_each_import_log_covers_without_judging_it(
        vanilla, monkeypatch, capsys):
    """The recording's log covers its whole run; one replay's was counted short.
    Both are written and printed, and the verdict is the replays' alone."""
    capture("mh-record-t")
    fake_run(monkeypatch, replay=[{}, {"import_at": 4}])
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    out = capsys.readouterr().out
    s = summary_of("mh-record-t")
    r1, r2 = (r["name"] for r in s["replays"])
    assert s["import_logs"] == {
        "mh-record-t": {"file": "sa2-import-calls.csv", "counted_at_tick": 6,
                        "last_captured_tick": 6, "whole_run": True},
        r1: {"file": "sa2-import-calls.csv", "counted_at_tick": 6, "last_captured_tick": 6,
             "whole_run": True},
        r2: {"file": "sa2-import-calls.csv", "counted_at_tick": 4, "last_captured_tick": 6,
             "whole_run": False}}
    assert ("  import log of mh-record-t: counted at tick 6, the run's last captured tick "
            "(every tick the capture holds)\n") in out
    assert (f"  WARNING: the import log of {r2} was counted at tick 4 but the run's last "
            "captured tick is 6: it stops before the run ends\n") in out
    assert "RECORD_STAGE_OK: both replays reached the goal on tick 4" in out


@pytest.mark.parametrize("at, last, why", [
    (4, 6, "it stops before the run ends"),
    (7, 6, "it counts past the capture"),
    (0, 0, "the run has no tick capture"),
])
def test_a_log_that_is_not_the_whole_run_says_which_way_it_misses(at, last, why):
    log = {"file": "sa2-import-calls.csv", "counted_at_tick": at, "last_captured_tick": last,
           "whole_run": False}
    assert R.coverage({"r": log}) == [
        f"  WARNING: the import log of r was counted at tick {at} but the run's last captured "
        f"tick is {last}: {why}"]


def test_a_log_counted_at_tick_0_beside_no_tick_capture_is_not_the_whole_run(vanilla):
    """Tick 0 equals the row count of a missing capture; that agreement is
    vacuous, and whole_run must not take it (a TICK_LIMIT build that detaches
    before its first frame writes such a log)."""
    capture("z")
    (R.CAPTURES / "z" / "sa2-tick-capture.csv").unlink()
    (R.CAPTURES / "z" / "sa2-import-calls.csv").write_text(
        "slot,calls,ours,tick\n0x00900000,5,1,0\n0x00900004,0,1,0\n")
    assert R.import_log("z") == {"file": "sa2-import-calls.csv", "counted_at_tick": 0,
                                 "last_captured_tick": 0, "whole_run": False}


def test_an_import_log_the_reader_refuses_is_reported_with_the_reader_s_reason(vanilla):
    capture("a")
    (R.CAPTURES / "a" / "sa2-import-calls.csv").write_text(
        "slot,calls,ours,tick\n0x00900000,5,1,6\n0x00900008,5,1,6\n")
    got = R.import_log("a")
    assert (got["counted_at_tick"], got["whole_run"]) == (None, False)
    assert got["refused"].endswith("slot 0x00900008 is not one of the executable's")
    assert R.coverage({"a": got}) == [
        f"  WARNING: the import log of a was not read: {got['refused']}"]
    (R.CAPTURES / "a" / "sa2-import-calls.csv").unlink()
    assert R.import_log("a")["refused"] == "no sa2-import-calls.csv"


def test_a_run_is_kept_when_its_import_log_is_all_that_is_wrong_and_only_then():
    """The import log is reported, never a reason to throw a run away; every
    other artifact still is."""
    whole = {"run": {"return_code": 0}, "installation_untouched": True,
             "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                            "check": {"ok": True, "problems": []}},
                           {"file": "sa2-import-calls.csv", "present": False}]}
    assert R.accepted(whole) == (True, [])
    assert R.import_log_problem(whole) == "sa2-import-calls.csv missing"
    torn = dict(whole, artifacts=[whole["artifacts"][0], {
        "file": "sa2-import-calls.csv", "present": True,
        "check": {"ok": False, "problems": ["fewer than 251 rows"]}}])
    assert R.accepted(torn) == (True, [])
    assert R.import_log_problem(torn) == "sa2-import-calls.csv rejected: fewer than 251 rows"
    both = dict(whole, artifacts=[{"file": "sa2-tick-capture.csv", "present": False},
                                  whole["artifacts"][1]])
    assert R.accepted(both) == (False, ["sa2-tick-capture.csv missing"])
    assert R.import_log_problem({"artifacts": whole["artifacts"][:1]}) is None
    assert R.import_log_problem({"artifacts": [{"file": "sa2-import-calls.csv",
                                                "present": True}]}) == \
        "sa2-import-calls.csv rejected: "
    assert R.accepted(dict(whole, artifacts=whole["artifacts"][1:])) == (False, ["no artifacts"])


def test_a_teardown_exit_is_not_excused_when_the_oracle_mode_was_refused(vanilla, monkeypatch):
    rec = {"run": {"return_code": 247}, "installation_untouched": True,
           "session": {"oracle": {"refused": "the vanilla probe is built at position [1]"}},
           "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                          "check": {"ok": True, "problems": []}}]}
    fake_runner(monkeypatch, 1, "SA2_ORACLE_CAPTURE_PROBLEM name=r exit=247 problems=0\n"
                "  ORACLE MODE REFUSED: the vanilla probe is built at position [1]\n",
                write=("r", rec))
    with pytest.raises(SystemExit) as e:
        R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480)
    assert e.value.code.startswith("r: the run was not accepted (the runner said so).")


@pytest.mark.parametrize("extra, kept", [
    ({}, True),
    ({"session": {"oracle": {"refused": "the vanilla probe is built at position [1]"}}}, False),
    ({"artifacts": [{"file": "sa2-import-calls.csv", "present": False},
                    {"file": "sa2-stage-trace.csv", "present": False}]}, False),
    ({"artifacts": [{"file": "sa2-import-calls.csv", "present": False}]}, False),
])
def test_the_runner_s_objection_to_the_import_log_alone_does_not_lose_the_run(
        vanilla, monkeypatch, capsys, extra, kept):
    rec = {"run": {"return_code": 0, "elapsed_seconds": 15.0}, "installation_untouched": True,
           "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                          "check": {"ok": True, "problems": []}},
                         {"file": "sa2-import-calls.csv", "present": False}], **extra}
    fake_runner(monkeypatch, 1, "SA2_ORACLE_CAPTURE_PROBLEM name=r exit=0 problems=1\n"
                "  MISSING sa2-import-calls.csv\n", write=("r", rec))
    if kept:
        assert R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480) == rec
        assert ("NOTE: r is kept although its import log is not usable (sa2-import-calls.csv "
                "missing); the verdict reports what the log covers.") in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as e:
            R.run("r", ["INPUT_MODE=1"], R.RECORD_CAPTURES, 480)
        assert e.value.code.startswith("r: the run was not accepted (")


def test_a_recording_whose_import_log_never_came_is_replayed_not_thrown_away(
        vanilla, monkeypatch, capsys):
    """The goal-ended write is the one path the preflight cannot run. If it
    fails, the recording and its replays go on, and the verdict says the log
    is missing instead of asking the user to play again."""
    capture("mh-record-t")
    (R.CAPTURES / "mh-record-t" / "sa2-import-calls.csv").unlink()
    edit_receipt("mh-record-t", artifacts=[
        *json.loads((R.CAPTURES / "mh-record-t" / "receipt.json").read_text())["artifacts"][:1],
        {"file": "sa2-import-calls.csv", "present": False}])
    fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    out = capsys.readouterr().out
    assert "record again" not in out
    assert ("  WARNING: the import log of mh-record-t was not read: no sa2-import-calls.csv\n"
            in out)
    assert summary_of("mh-record-t")["import_logs"]["mh-record-t"]["whole_run"] is False


def test_a_reader_that_fails_outright_is_a_refusal_not_a_lost_summary(vanilla, monkeypatch):
    capture("a")

    def broken(exe=None):
        raise OSError("sonic2app-unpacked.exe: no such file")

    monkeypatch.setattr(R.NI, "slots", broken)
    got = R.import_log("a")
    assert (got["counted_at_tick"], got["whole_run"]) == (None, False)
    assert got["refused"] == ("the log could not be read: "
                              "OSError('sonic2app-unpacked.exe: no such file')")


# ------------------------------------------------------------ the word log


@pytest.mark.parametrize("input_defines", [["INPUT_MODE=1"],
                                           ["INPUT_MODE=1", f"TICK_LIMIT={R.PREFLIGHT_TICKS}"],
                                           ["INPUT_MODE=2", 'INPUT_SCRIPT="s.h"', "TICK_LIMIT=9"]])
def test_no_runner_flag_follows_the_double_dash_and_every_run_collects_the_word_log(
        input_defines):
    """Everything after `--` reaches the game, and the judge's run B put the
    log's flag there: the game got two more arguments and its heap moved. The
    runner's own parser reads each command of the recording, the preflight
    and the replays, and the game gets the test spawn and nothing else."""
    cmd = R.command("n", input_defines, R.REPLAY_CAPTURES, R.CAP)
    args = R.RP.arg_parser().parse_args(cmd[2:])
    assert R.RP.game_arguments(args) == ["--level", "metalharbor", "--character", "sonic"]
    assert cmd[cmd.index("--"):] == R.SPAWN
    assert args.binary_capture == ["sa2-word-log.bin"]
    assert R.RP.word_log_refusal([(m, Path(s)) for m, s in R.PROBES], args.capture,
                                 args.binary_capture) is None


def mask_moved(builds: list[dict], header: str) -> list[dict]:
    """`builds` as the runner records them after `header` beside the probes
    changed: every probe records every header beside it."""
    for b in builds:
        b["headers_sha256"] = {**b["headers_sha256"], header: "0" * 64}
    return builds


def test_a_new_mask_is_not_a_new_configuration_and_a_new_field_list_is(vanilla):
    """The digest columns are the host's since the word log, so the mask is no
    probe's input and a run taken under another is one configuration with the
    tree's; the field list the tick probe includes still is an input."""
    capture("a")
    capture("mask", builds=mask_moved(built(["INPUT_MODE=1"]), "tick_digest.h"))
    capture("fields", builds=mask_moved(built(["INPUT_MODE=1"]), "tick_fields.h"))
    assert R.compare_to("a", "mask")["headers_same"] is True
    assert R.changed_files("mask") == []
    assert R.compare_to("a", "fields")["headers_same"] is False
    assert R.changed_files("fields") == ["harness/replay/tick_fields.h"]


def test_a_build_the_tree_cannot_vouch_for_is_compared_on_every_header_it_recorded(vanilla):
    """Two runs of a tick probe the tree no longer has: what that source
    included is unknown, so a mask difference between them still separates
    them, as it did before the word log."""
    old = {"tick_probe": {"source_sha256": "0" * 64}}
    capture("a", builds=built(change=old))
    capture("b", builds=mask_moved(built(change=old), "tick_digest.h"))
    capture("c", builds=built(change=old))
    assert R.compare_to("a", "b")["headers_same"] is False
    assert R.compare_to("a", "c")["headers_same"] is True


def test_the_input_probe_s_script_is_no_header_and_its_other_includes_would_be():
    """The input probe includes its script by macro (INPUT_SCRIPT): the
    recording's input, never a header beside it, so the probe includes none."""
    assert R.included_headers(ROOT / R.PROBE) == {}
    assert R.included_headers(ROOT / R.TICK) == {
        h: sha(ROOT / "harness/replay" / h)
        for h in ("tick_fields.h", "tick_probe_core.h", "word_log_core.h")}


def test_the_summary_keeps_what_the_runner_recorded_of_the_word_log(vanilla):
    rec = capture("a", word_log=False)
    assert (R.facts(rec)["word_log"], R.facts(rec)["idle_before_run_s"]) == (None, None)
    rec["word_log"] = {"stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64, "ticks": 6,
                       "end_reason": "input_end", "ok": True, "cost": {"scan_ms": {}}}
    rec["session"].update(idle_before_run={"seconds": 91.5},
                          load_average={"at_launch": [0.5, 0.4, 0.3]})
    got = R.facts(rec)
    assert got["word_log"] == {"stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64,
                               "ticks": 6, "end_reason": "input_end", "ok": True}
    assert (got["idle_before_run_s"], got["load_average_at_launch"]) == (91.5, [0.5, 0.4, 0.3])


def built_from_log(name: str, mask_sha256: str) -> None:
    """`name`'s tick capture recorded as the runner builds it from the word log,
    its digest columns under the mask with `mask_sha256`."""
    path = R.CAPTURES / name / "receipt.json"
    rec = json.loads(path.read_text())
    for a in rec["artifacts"]:
        if a["file"] == "sa2-tick-capture.csv":
            a["built_from_log"] = {"digest_inputs": {"harness/replay/tick_digest.h": mask_sha256,
                                                     "tools/s4/wordlog.c": "7" * 64}}
    path.write_text(json.dumps(rec))


def test_digests_built_under_two_masks_pass_no_digest_verdict_and_the_named_ones_still_compare(
        vanilla):
    """A mask is no probe's input, so two runs whose captures the runner built
    under two masks are one configuration; their digest columns hash other
    words, so no digest verdict can pass on them, while the named columns
    still compare."""
    capture("a")
    capture("b")
    capture("c", tick_change={(4, "frame_count"): "99"})
    for name, mask in (("a", "1" * 64), ("b", "2" * 64), ("c", "2" * 64)):
        built_from_log(name, mask)
    got = R.compare_to("a", "b")
    assert (got["headers_same"], got["masks_same"], got["named_columns_same"],
            got["tick_captures_identical"],
            got["differences_only_in_natively_unstable_blocks"]) == (True, False, True, False,
                                                                    False)
    assert R.compare_to("a", "c")["named_columns_differ"] == {"frame_count": 4}
    built_from_log("b", "1" * 64)
    got = R.compare_to("a", "b")
    assert (got["masks_same"], got["tick_captures_identical"]) == (True, True)


# ------------------------------------------------ the reviews of 2026-09-25


@pytest.fixture
def kept_log(tmp_path) -> Path:
    """A word log of the capture() runs' six ticks, compressed as the runner
    keeps it (tools/oracle/test_oracle_tools.py synthetic_word_log)."""
    from test_oracle_tools import synthetic_word_log
    (tmp_path / "log").mkdir()
    raw = synthetic_word_log(tmp_path / "log" / "sa2-word-log.bin", 6)
    subprocess.run(["zstd", "-3", "-q", "--rm", str(raw)], check=True)
    return tmp_path / "log" / "sa2-word-log.bin.zst"


def keep_log(name: str, zst: Path) -> None:
    """`name` as the runner leaves a run since the word log: the log kept, and
    its artifact collected and accepted."""
    d = R.CAPTURES / name
    (d / zst.name).write_bytes(zst.read_bytes())
    path = d / "receipt.json"
    rec = json.loads(path.read_text())
    rec["artifacts"].append({"file": zst.name, "present": True, "binary": True, "word_log": True,
                             "check": {"ok": True, "problems": [],
                                       "sha256": R.sha256(d / zst.name)}})
    path.write_text(json.dumps(rec))


def test_across_two_masks_both_captures_are_rebuilt_from_their_logs_and_compare(
        vanilla, kept_log):
    """W2: with both logs kept, a mask that moved between two runs costs no
    digest verdict: both captures are rebuilt with the tree's mask."""
    capture("a")
    capture("b", tick_change={(3, "d_01760000"): "0x0bad0bad"})
    built_from_log("a", "1" * 64)
    built_from_log("b", "2" * 64)
    assert R.compare_to("a", "b")["masks_same"] is False       # no logs: nothing to rebuild
    keep_log("a", kept_log)
    keep_log("b", kept_log)
    got = R.compare_to("a", "b")
    assert (got["masks_same"], got["tick_captures_identical"], got["named_columns_same"],
            got["differences_only_in_natively_unstable_blocks"]) == (True, True, True, True)
    assert got["digests"].startswith("rebuilt from both word logs with harness/replay/"
                                     "tick_digest.h (")


def test_replay_only_after_a_mask_move_replays_and_passes_on_the_rebuilt_captures(
        vanilla, kept_log, monkeypatch, capsys):
    """W2, the reviewer's demonstration inverted: a recording whose capture
    the runner built under one mask, replayed after the tree's mask moved."""
    capture("mh-record-t")
    built_from_log("mh-record-t", "1" * 64)
    keep_log("mh-record-t", kept_log)
    calls = fake_run(monkeypatch)
    real = R.replay

    def replay(recording, stamp, end, n=2):
        out = real(recording, stamp, end, n)
        for r in out:
            built_from_log(r["name"], "2" * 64)
            keep_log(r["name"], kept_log)
        return out

    monkeypatch.setattr(R, "replay", replay)
    assert R.main(["--replay-only", "mh-record-t"]) == 0
    assert len(calls) == 2
    assert "RECORD_STAGE_OK: both replays reached the goal" in capsys.readouterr().out


def test_digest_blocks_are_not_named_as_differing_across_two_masks(vanilla):
    """N3: across two masks every block may differ without the game moving."""
    capture("a")
    capture("b", tick_change={(3, "d_01760000"): "0x0bad0bad"})
    built_from_log("a", "1" * 64)
    built_from_log("b", "2" * 64)
    got = R.compare_to("a", "b")
    assert (got["masks_same"], got["digest_blocks_differ"]) == (False, {})
    assert got["digests"] == "not rebuilt: a run kept no word log"


def test_a_recording_from_before_the_word_log_is_sent_to_the_re_take_tool(vanilla, monkeypatch,
                                                                         capsys):
    """W3: the runner refuses the probe such a recording was made with unless
    it collects a log that probe never writes, so no put-back file replays it;
    it stops before any replay and names the re-take tool, twice."""
    capture("mh-record-t", word_log=False)
    calls = fake_run(monkeypatch)
    assert R.main(["--replay-only", "mh-record-t"]) == 1
    out = capsys.readouterr().out
    assert calls == []
    assert "STOPPED: mh-record-t was recorded before the tick probe kept its word log" in out
    retake = (f"{R.ROOT} && source tools/env.sh && .venv/bin/python "
              "tools/oracle/retake_replays.py mh-record-t")
    assert out.count(retake) == 2
    assert "--replay-only" not in out
