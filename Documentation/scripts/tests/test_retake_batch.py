"""tools/oracle/retake_batch.py: the whole native re-take batch as one tool.

Each case is a way an unattended batch could keep taking captures it should
not: a launch under load, a warm first run, a Proton update before a run or
under one, an edit to a frozen probe or reader, a device-profile control that
fails, two boots that disagree on the device profile, a run launched past the
gate, a host that suspended, a game left running, a Steam client that
restarted, a sleep inhibitor that is gone, another installation or another
DLL, a series that failed a check the new mask cannot excuse, or a runner
that failed; each must stop the batch at once, launch nothing more, and leave
a receipt that says where and why. And the batch that meets every condition
must record every launch's load, idle time, host state and gate.

No runner, game, Wine or Steam process runs, and no test takes a sleep
inhibitor: under_inhibitor, which would start the batch again under
systemd-inhibit in place of pytest, is replaced for every test (never_exec).
The runners are a made-up oracle (Oracle) that does what they do to the
files the batch reads: it holds each launch at the runner's own gate
(run_oracle_probe.launch_gate, for real), writes a receipt, a tick capture and
an installation snapshot as the runner does, marks the game's exit, and for a
Metal Harbor series writes retake_replays' summary. The host's measures
(Steam, the games, the inhibitors, the suspend clock, the lock state, the
Proton build, the load, the idle time) are made up.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import retake_batch as B  # noqa: E402
from test_oracle_tools import CHAO, runner_tree  # noqa: E402

RP, R, BC = B.RP, B.R, B.BC
UNDER_INHIBITOR = B.under_inhibitor
BUILD = {"build": "experimental-11.0-20260924-x86_64",
         "files_tree": {"entries": 7496, "sha256": "7" * 64}}
PROFILE = "a" * 64
REC = "mh-record-t"
OFF, ON = "devprofile-control-off-v2a", "devprofile-control-on-v2a"
TICKS = b"tick,frame_count\n1,1\n2,2\n"
SNAPSHOT = '{"files": {"sonic2app-unpacked.exe": "e"}}\n'
READER = ("tools/s4/wordlog.c", "harness/replay/word_log_core.h",
          "harness/replay/tick_probe_core.h", "harness/replay/tick_fields.h",
          "harness/replay/tick_digest.h")
TREE = {"harness/replay/native_tick_probe.c": "/* probe */\n",
        "harness/replay/native_vanilla_probe.c": "/* vanilla */\n",
        "harness/replay/native_device_profile.c": "/* profile probe */\n",
        "harness/replay/tick_fields.h": "/* fields */\n",
        "harness/replay/word_log_core.h": "/* core */\n",
        "harness/replay/tick_probe_core.h": "/* row */\n",
        "harness/replay/tick_digest.h": "/* digest */\n",
        "harness/replay/masks/boot.h": "/* mask */\n",
        "tools/oracle/run_oracle_probe.py": "# runner\n",
        "tools/oracle/test_run_oracle_probe.py": "# a test\n",
        "tools/s4/wordlog.py": "# reader\n",
        "tools/s4/wordlog.c": "/* the reader */\n",
        "Documentation/replays.json": "{}\n",
        "Documentation/save-state.json": "{}\n",
        "tools/pins.json": "{}\n"}
HELD = (b"sa2-device-profile 1\nsystem processors=16 process_mask=0x0000ffff "
        b"system_mask=0x0000ffff\nxinput_caps slot=0 code=0x0000048f\n")
LOCALE = b"locale_user lcid=0x00000409 system=0x00000409 acp=1252 oemcp=437 valid=1\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def never_exec(monkeypatch):
    """No test may start a batch in place of pytest: every main() runs as if
    already under the inhibitor, and reaching under_inhibitor fails."""
    monkeypatch.setenv(B.INHIBITED_ENV, "1")

    def refuse(argv):
        raise AssertionError(f"a test reached under_inhibitor with {argv}")

    monkeypatch.setattr(B, "under_inhibitor", refuse)


class Time:
    """retake_batch's clock: the wall clock as it is, and a monotonic clock
    and sleep that pass no real time; each sleep leaves the oracle idler."""

    def __init__(self, oracle: "Oracle"):
        self.oracle, self.now = oracle, 0.0
        self.time = __import__("time").time

    def monotonic(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s
        self.oracle.idle += s


class Oracle:
    """The host and the three runners, made up. `load`, `idle`, `steam`,
    `games`, `inhibitors`, `suspend`, `locked`, `blank` and `build` are what the host
    measures now; `receipt_change` maps a capture name to what its receipt
    records otherwise; `profile_of` to its device profile; `profile_data`,
    when set, is every profiled run's profile file; `tick_of` to its tick
    capture; `snapshot_of` to its installation snapshot; `dll_of` and
    `defines_of` a (name, mod) to the DLL it built and its defines;
    `suspended_in` to the seconds the host slept
    under it; `proton_moves_in` to the build its end reads; `failed_checks`
    maps a series' number to the checks its summary fails; `exit_of` a capture
    to its runner's exit status; `after` a capture to a function run once it
    has been taken."""

    def __init__(self, tmp_path: Path, monkeypatch):
        runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
        self.root = tmp_path / "tree"
        for rel, text in TREE.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        captures = tmp_path / "captures"
        (captures / REC).mkdir(parents=True)
        (captures / REC / "receipt.json").write_text(json.dumps({"session": {"device_profile": {
            "file": "sa2-device-profile.txt", "sha256": PROFILE}}}))
        for module, name, value in ((B, "ROOT", self.root), (RP, "ROOT", self.root),
                                    (RP, "CAPTURES", captures), (R, "CAPTURES", captures)):
            monkeypatch.setattr(module, name, value)
        self.load, self.idle, self.steam = [0.3, 0.4, 0.5], 250.0, [5255]
        self.games: list[int] = []
        self.inhibitors = [{"what": "sleep", "who": B.INHIBIT_WHO, "why": "a batch",
                            "mode": "block", "uid": 1000, "pid": os.getppid()}]
        self.suspend, self.locked, self.blank = 100.0, False, False
        self.build = copy.deepcopy(BUILD)
        self.receipt_change: dict[str, dict] = {}
        self.profile_of: dict[str, str] = {}
        self.profile_data: bytes | None = None
        self.tick_of: dict[str, bytes] = {}
        self.snapshot_of: dict[str, str] = {}
        self.dll_of: dict[tuple[str, str], str] = {}
        self.defines_of: dict[tuple[str, str], list[str]] = {}
        self.suspended_in: dict[str, float] = {}
        self.proton_moves_in: dict[str, str] = {}
        self.failed_checks: dict[int, list[str]] = {}
        self.exit_of: dict[str, int] = {}
        self.after: dict[str, object] = {}
        self.calls: list[list[str]] = []
        self.launched: list[str] = []
        self.gates_seen: list[str] = []
        monkeypatch.setattr(RP, "proton_build", lambda launcher=None, steam_client=None:
                            copy.deepcopy(self.build))
        monkeypatch.setattr(RP, "steam_pids", lambda: list(self.steam))
        monkeypatch.setattr(RP, "game_pids", lambda: list(self.games))
        monkeypatch.setattr(RP, "sleep_inhibitors", lambda: copy.deepcopy(self.inhibitors))
        monkeypatch.setattr(RP, "suspend_clock", lambda: self.suspend)
        monkeypatch.setattr(RP, "host_state", lambda: {
            "boottime_minus_monotonic_s": self.suspend, "session": "2", "locked": self.locked,
            "idle": self.blank})
        monkeypatch.setattr(RP, "load_average", lambda: list(self.load))
        monkeypatch.setattr(RP, "idle_before_run", lambda now=None: {"seconds": self.idle})
        monkeypatch.setattr(BC, "busy", lambda: [])
        monkeypatch.setattr(BC, "runner_pids", lambda: [])
        monkeypatch.setattr(B.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(B.RT, "check_recording", lambda name: (
            {"goal_tick": 15286, "end_tick": 15586, "misses": 0}, []))
        monkeypatch.setattr(B, "launch", self)
        monkeypatch.setattr(B, "time", Time(self))
        monkeypatch.setenv("DISPLAY", ":0")

    # -- the runners
    def __call__(self, cmd: list[str], log: Path, env: dict) -> tuple[int, str]:
        self.calls.append(cmd)
        self.gates_seen.append(env.get(RP.LAUNCH_GATE))
        log.parent.mkdir(parents=True, exist_ok=True)
        if Path(cmd[1]).name == "retake_replays.py":
            code, out = self.series(cmd, env)
        else:
            profile = Path(cmd[1]).name != "boot_capture.py" or "--no-device-profile" not in cmd
            code, out = self.run(cmd[cmd.index("--name") + 1], env, profile)
        log.write_text(out)
        return code, out

    def probe(self, name: str, mod: str, source: str, headers: tuple[str, ...] = ()) -> dict:
        path = self.root / "harness/replay" / source
        return {"mod_id": mod, "source": str(path), "source_sha256": sha(path.read_bytes()),
                "headers_sha256": {h: sha((path.parent / h).read_bytes()) for h in headers},
                "defines": self.defines_of.get(
                    (name, mod), ["TICK_LIMIT=1200"] if mod == "tick_probe" else []),
                "dll_sha256": self.dll_of.get((name, mod), sha(mod.encode()))}

    def run(self, name: str, env: dict, profile: bool = True) -> tuple[int, str]:
        """One runner run: its gate, the game, the receipt, as the runner
        writes them."""
        try:                                             # the runner's gate, on this clock
            gate = RP.launch_gate(env, clock=B.time.monotonic, sleep=B.time.sleep)
        except SystemExit as refused:
            return 1, f"{refused.code}\n"
        self.launched.append(name)
        built = [self.probe(name, "vanilla_probe", "native_vanilla_probe.c"),
                 self.probe(name, "tick_probe", "native_tick_probe.c", ("tick_fields.h",))]
        if profile:
            built.append(self.probe(name, "device_profile", "native_device_profile.c"))
        mods = [b["mod_id"] for b in built]
        device = None
        if profile:
            data = self.profile_data
            device = {"file": "sa2-device-profile.txt",
                      "sha256": self.profile_of.get(name, PROFILE if data is None else sha(data))}
        rec = {"name": name, "cwd": "/mirror",
               "command": "timeout 200s env proton runinprefix ./sonic2app-unpacked.exe",
               "run": {"return_code": 0, "elapsed_seconds": 23.4},
               "installation_untouched": True, "mirror_path_differences": [],
               "artifacts": [{"file": "sa2-tick-capture.csv", "present": True,
                              "check": {"ok": True, "problems": []}}],
               "inputs": {"built_probes": built, "enabled_mods": mods,
                          "modloader_profile": {"EnabledMods": mods,
                                                "Graphics": {"SkipIntro": False}},
                          "game_args": [], "timeout_seconds": 200,
                          "executable_sha256": "e" * 64, "installation": "/install"},
               "word_log": {"ok": True, "stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64,
                            "ticks": 1199, "end_reason": "tick_limit",
                            "capture_sha256": sha(self.tick_of.get(name, TICKS)), "cost": {},
                            "digest_inputs": {p: sha((self.root / p).read_bytes())
                                              for p in READER}},
               "session": {"launch_gate": gate,
                           "load_average": {"at_launch": list(self.load),
                                            "at_end": [0.6, 0.5, 0.5]},
                           "idle_before_run": {"seconds": self.idle},
                           "proton": RP.proton_build(),
                           "host_state": {"at_launch": RP.host_state()},
                           "saves": {"matches_pin": True, "pin": RP.SS.load().record()},
                           "device_profile": device,
                           "game_environment": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
                           "winedebug": None, "standard_handles": "pipes",
                           "oracle": {"mode": "vanilla", "kept": [], "iat": [], "config": {}},
                           "live_desktop_session": True}}
        # the game runs, and the host may sleep or Steam update Proton under it
        self.suspend += self.suspended_in.get(name, 0.0)
        if name in self.proton_moves_in:
            self.build["build"] = self.proton_moves_in[name]
        rec["session"]["host_state"]["at_end"] = RP.host_state()
        rec["session"]["proton_at_end"] = RP.proton_build()
        for key, value in self.receipt_change.get(name, {}).items():
            where, _, field = key.rpartition(".")
            (rec[where] if where else rec)[field] = value
        capture = RP.CAPTURES / name
        capture.mkdir(parents=True)
        (capture / "receipt.json").write_text(json.dumps(rec))
        (capture / RP.TICK_CAPTURE).write_bytes(self.tick_of.get(name, TICKS))
        if profile and self.profile_data is not None:
            (capture / "sa2-device-profile.txt").write_bytes(self.profile_data)
        snapshot = RP.OUT / "snapshots" / f"{name}-install-before.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(self.snapshot_of.get(name, SNAPSHOT))
        RP.mark_game_exit(name)
        self.idle = 2.0                                  # the next launch follows at once
        if name in self.after:
            self.after[name]()
        code = self.exit_of.get(name, 0)
        return code, f"SA2_ORACLE_CAPTURE_{'OK' if code == 0 else 'PROBLEM'} name={name}\n"

    def series(self, cmd: list[str], env: dict) -> tuple[int, str]:
        """retake_replays: each re-take in turn, the series ending at the first
        the runner does not accept, then its summary beside the recording."""
        count = int(cmd[cmd.index("--count") + 1])
        number = len(list((RP.CAPTURES / REC).glob("retakes-*.json"))) + 1
        runs = []
        for k in range(1, count + 1):
            name = f"{REC}-retake{k}-s{number}"
            code, out = self.run(name, env)
            runs.append({"name": name, "error": None if code == 0 else out.strip(),
                         "device_profile": {"sha256": PROFILE}})
            if code:
                break
        checks = {"every_retake_ran": all(r["error"] is None for r in runs) and
                  len(runs) == count, "trajectory_as_recorded": True,
                  "tick_captures_identical": False, "import_logs_registrable": False}
        for k in self.failed_checks.get(number, []):
            checks[k] = False
        ok = all(checks.values())
        path = RP.CAPTURES / REC / f"retakes-s{number}.json"
        path.write_text(json.dumps({"recording": REC, "runs": runs, "checks": checks,
                                    "verdict": "RETAKE_OK" if ok else "RETAKE_DISAGREE"}))
        return (0 if ok else 1), f"Re-take 1 of {count}...\nsummary: {path}\nRETAKE_DISAGREE\n"


def batch(oracle: Oracle, tmp_path: Path, *argv: str) -> tuple[int, dict]:
    """retake_batch's command line with `argv`; its exit status and receipt."""
    receipt = tmp_path / "batches" / "b.json"
    code = B.main([*argv, "--recording", REC, "--receipt", str(receipt)])
    return code, json.loads(receipt.read_text())


def stopped(doc: dict) -> tuple[str, str]:
    return doc["stopped"]["step"], doc["stopped"]["reason"]


SMALL = ("--boots", "2,1", "--series", "2,2", "--no-stack-pad", "--no-demo")


# ------------------------------------------------------------ the plan


def test_the_default_plan_is_the_batch_the_plan_asks_for_after_the_control():
    steps = B.plan(list(B.BOOT_SERIES), True, list(B.MH_SERIES), True)
    assert [(s.label, s.kind, s.name, s.count, s.cold) for s in steps[:6]] == [
        ("control-off", "control_off", OFF, 1, True), ("control-on", "control_on", ON, 1, True),
        ("boot-01", "boot", "boot-ticks-v15a", 1, True),
        ("boot-02", "boot", "boot-ticks-v15b", 1, False),
        ("boot-03", "boot", "boot-ticks-v15d", 1, False),
        ("boot-04", "boot", "boot-ticks-v15e", 1, True)]
    boots = [s for s in steps if s.kind == "boot"]
    assert (len(boots), sum(s.cold for s in boots), boots[-1].name) == (14, 5, "boot-ticks-v15o")
    assert [(s.label, s.name, s.cold) for s in steps if s.kind == "stack_pad"] == [
        ("stack-pad", "boot-ticks-v15c-stackpad", True)]
    series = [s for s in steps if s.kind == "series"]
    assert [s.count for s in series] == [3, 3, 3, 3, 3, 3, 2] and all(s.cold for s in series)
    assert (steps[-1].kind, steps[-1].name, steps[-1].cold) == ("demo", "attract-demo-v3a", True)
    assert (len(steps), sum(s.count for s in steps), sum(s.cold for s in steps)) == (25, 38, 16)
    assert [s.kind for s in B.plan([2], False, [], False, control=False)] == ["boot", "boot"]
    with pytest.raises(SystemExit, match="at least 2 re-takes"):
        B.plan([], False, [3, 1], False)
    with pytest.raises(SystemExit, match="at most 25 boots"):
        B.plan([26], False, [], False)


@pytest.mark.parametrize("flag", [("--max-load", "1.5"), ("--min-idle", "60")])
def test_the_plan_s_limits_can_be_made_stricter_and_never_looser(flag, capsys):
    with pytest.raises(SystemExit) as e:
        B.main([*flag, "--dry-run"])
    assert e.value.code == 2
    assert "would loosen the batch's conditions" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [("--max-load", "nan"), ("--min-idle", "nan"),
                                  ("--max-wait", "inf"), ("--poll", "nan")])
def test_a_limit_that_is_not_a_finite_number_is_refused(flag, capsys):
    with pytest.raises(SystemExit) as e:
        B.main([*flag, "--dry-run"])
    assert e.value.code == 2 and "must be finite" in capsys.readouterr().err


def test_each_call_is_its_runner_s_own_command():
    steps = {s.label: s for s in B.plan([2], True, [3], True)}
    assert B.command(steps["control-off"], REC, 90, 1800) == [
        B.PY, str(B.HERE / "boot_capture.py"), "--name", OFF, "--no-device-profile",
        "--min-idle", "90", "--max-wait", "1800"]
    assert B.command(steps["control-on"], REC, 90, 1800) == [
        B.PY, str(B.HERE / "boot_capture.py"), "--name", ON, "--min-idle", "90",
        "--max-wait", "1800"]
    assert B.command(steps["boot-01"], REC, 90, 1800) == [
        B.PY, str(B.HERE / "boot_capture.py"), "--name", "boot-ticks-v15a",
        "--min-idle", "90", "--max-wait", "1800"]
    assert B.command(steps["boot-02"], REC, 90, 1800) == [
        B.PY, str(B.HERE / "boot_capture.py"), "--name", "boot-ticks-v15b"]
    assert B.command(steps["stack-pad"], REC, 90, 1800)[3:5] == ["boot-ticks-v15c-stackpad",
                                                                 "--stack-pad"]
    assert B.command(steps["metalharbor-1"], REC, 90, 1800) == [
        B.PY, str(B.HERE / "retake_replays.py"), REC, "--count", "3"]
    demo = B.command(steps["demo"], REC, 90, 1800)
    assert demo[:4] == [B.PY, str(B.HERE / "run_oracle_probe.py"), "--name", "attract-demo-v3a"]
    args = RP.arg_parser().parse_args(demo[2:])
    builds = [(m, Path(s)) for m, s in (b.split("=", 1) for b in args.build)]
    assert RP.game_arguments(args) == [] and "--" not in demo
    assert RP.word_log_refusal(builds, args.capture, args.binary_capture) is None
    assert (args.build, args.define, args.timeout, args.skip_intro, args.device_profile) == (
        ["vanilla_probe=harness/replay/native_vanilla_probe.c",
         "tick_probe=harness/replay/native_tick_probe.c",
         "stage_trace=harness/replay/native_stage_trace.c"],
        ["tick_probe:TICK_LIMIT=3100", "stage_trace:TICK_LIMIT=3100"], 240, False, True)


def test_the_control_s_two_runner_commands_differ_only_in_the_profile_probe(capsys):
    """harness/replay/native_device_profile.c's pair: boot_capture's runner
    call without --no-device-profile is its call with it, plus
    --device-profile, the vanilla probe built first in both."""
    off, on = BC.command(OFF, profile=False), BC.command(OFF)
    assert "--device-profile" not in off and on.count("--device-profile") == 1
    assert [a for a in on if a != "--device-profile"] == off
    assert off[off.index("--build") + 1].startswith("vanilla_probe=")
    printed = {}
    for flag in ("--no-device-profile", None):
        assert BC.main(["--name", OFF, "--dry-run", *([flag] if flag else [])]) == 0
        printed[flag] = capsys.readouterr().out
    assert "--device-profile" not in printed["--no-device-profile"]
    assert printed[None].count(" --device-profile ") == 1


def test_the_freeze_is_every_file_a_run_is_made_of_and_no_test(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    got = B.freeze()
    assert sorted(got) == sorted(p for p in TREE if not Path(p).name.startswith("test_"))
    assert {"tools/s4/wordlog.c", "Documentation/replays.json"} <= set(got)
    assert got["harness/replay/tick_fields.h"] == sha(b"/* fields */\n")
    (oracle.root / "harness/replay/masks/boot.h").write_text("/* another mask */\n")
    assert B.freeze() != got


# ------------------------------------------------------------ the whole batch


def test_a_batch_that_meets_every_condition_runs_every_call_and_records_every_launch(
        tmp_path, monkeypatch, capsys):
    oracle = Oracle(tmp_path, monkeypatch)
    code, doc = batch(oracle, tmp_path)
    assert code == 0 and doc["verdict"] == "BATCH_OK" and doc["stopped"] is None
    out = doc["outputs"]
    assert (out["control"], len(out["boots"]), len(out["cold_boots"]), out["stack_pad"],
            len(out["series"]), len(out["retakes"]), out["demo"]) == (
        [OFF, ON], 14, 5, ["boot-ticks-v15c-stackpad"], 7, 20, ["attract-demo-v3a"])
    assert out["cold_boots"] == ["boot-ticks-v15a", "boot-ticks-v15e", "boot-ticks-v15h",
                                 "boot-ticks-v15k", "boot-ticks-v15n"]
    assert len(oracle.launched) == 38 and len(oracle.calls) == 25
    assert oracle.launched[:3] == [OFF, ON, "boot-ticks-v15a"]
    gate = str(tmp_path / "batches" / "b" / "launch-gate.json")
    assert set(oracle.gates_seen) == {gate}             # every call names the gate
    captures = [c for s in doc["steps"] for c in s["captures"]]
    assert len(captures) == 38
    for c in captures:
        assert c["launch_gate"]["batch"] == doc["batch"] and c["load_at_launch"][0] < 1.0
        assert c["device_profile_sha256"] == (None if c["name"] == OFF else PROFILE)
        assert c["whole"] is True and c["host_state"]["at_end"]["locked"] is False
        assert c["installation_snapshot_sha256"] == sha(SNAPSHOT.encode())
    firsts = [s["captures"][0] for s in doc["steps"] if s["cold"]]
    assert all(c["idle_before_run_s"] >= 90 for c in firsts) and len(firsts) == 16
    refs = doc["references"]
    assert refs["device_profile_sha256"] == PROFILE
    assert doc["steps"][3]["device_profile_fixed"] == PROFILE
    assert doc["steps"][2]["gate"]["profile_sha256"] is None
    assert doc["steps"][4]["gate"]["profile_sha256"] == PROFILE
    assert refs["proton"] == BUILD and doc["preflight"]["ok"] is True
    assert (refs["steam_pid"], refs["suspend_clock_s"], refs["installation_sha256"]) == (
        5255, 100.0, sha(SNAPSHOT.encode()))
    assert refs["probe_dlls"] == {
        "vanilla_probe harness/replay/native_vanilla_probe.c": sha(b"vanilla_probe"),
        "tick_probe harness/replay/native_tick_probe.c TICK_LIMIT=1200": sha(b"tick_probe"),
        "device_profile harness/replay/native_device_profile.c": sha(b"device_profile")}
    assert doc["sleep_inhibitor"] == {"pid": os.getppid(), "who": B.INHIBIT_WHO}
    control = out["device_profile_control"]
    assert control == refs["device_profile_control"]
    assert (control["passed"], control["off"], control["on"], control["probe"],
            control["tick_capture_sha256"], control["configuration_difference"]) == (
        True, OFF, ON, "harness/replay/native_device_profile.c",
        {OFF: sha(TICKS), ON: sha(TICKS)}, None)
    assert control["probe_sha256"] == sha(b"/* profile probe */\n")
    assert "whose tick captures must be byte-identical" in control["criterion"]
    assert doc["rule_11"]["every_run_on_the_live_desktop"] is True
    assert doc["rule_11"]["lock_states_seen"] == ["unlocked"]
    assert "Determinism (time, RNG seeding)" in doc["rule_11"]["focus_and_occlusion"]
    assert out["rejudge_command"] == (
        ".venv/bin/python tools/oracle/retake_replays.py mh-record-t --rejudge "
        + " ".join(f"retakes-s{k}.json" for k in range(1, 8)))
    assert (tmp_path / "batches" / "b" / "boot-01.log").read_text().startswith(
        "SA2_ORACLE_CAPTURE_OK name=boot-ticks-v15a")
    assert capsys.readouterr().out.splitlines()[-2].startswith(
        f"RETAKE_BATCH_OK {doc['batch']}: device-profile control passed ({OFF}, {ON}), "
        "14 boots (5 cold), stack control boot-ticks-v15c-stackpad, 20 re-takes in 7 series, "
        "demo attract-demo-v3a")


def test_a_host_that_needs_the_user_s_hands_stops_the_batch_before_any_run(tmp_path,
                                                                          monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.steam = []
    (RP.CAPTURES / "boot-ticks-v15b").mkdir()
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert (code, doc["verdict"], step, oracle.calls) == (1, "BATCH_STOPPED", "preflight", [])
    assert "Steam is not running" in reason
    assert "1 capture name(s) already taken: boot-ticks-v15b" in reason


@pytest.mark.parametrize("break_it, says", [
    (lambda o: setattr(o, "games", [4242]),
     "a game is still running (pid [4242]), outside any runner"),
    (lambda o: setattr(o, "inhibitors", []),
     f"the batch holds no sleep inhibitor (sa2 retake_batch, pid {os.getppid()})"),
    (lambda o: o.inhibitors[0].update(mode="delay"), "the batch holds no sleep inhibitor"),
    (lambda o: o.inhibitors[0].update(pid=1), "the batch holds no sleep inhibitor"),
])
def test_a_game_left_running_or_no_sleep_inhibitor_stops_the_batch_before_any_run(
        tmp_path, monkeypatch, break_it, says):
    oracle = Oracle(tmp_path, monkeypatch)
    break_it(oracle)
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert (code, step, oracle.calls) == (1, "preflight", []) and says in reason


def test_a_load_that_stays_high_stops_the_batch_before_the_call(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.load = [1.4, 1.2, 1.0]
    code, doc = batch(oracle, tmp_path, *SMALL, "--max-wait", "0")
    assert (code, stopped(doc)) == (1, ("control-off", f"{RP.GATE_REFUSED} control-off after "
                                                       "waiting 0 s: the one-minute load is 1.4, "
                                                       "not below 1"))
    assert oracle.calls == [] and "exit" not in doc["steps"][0]


def test_a_launch_the_runner_s_gate_refuses_inside_a_series_ends_the_batch(tmp_path,
                                                                           monkeypatch):
    """The load rises between two re-takes of one retake_replays call: the
    runner's gate refuses the second, the series ends, and so does the batch."""
    oracle = Oracle(tmp_path, monkeypatch)
    first = f"{REC}-retake1-s1"
    oracle.after[first] = lambda: setattr(oracle, "load", [1.5, 1.0, 0.8])
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert (code, step) == (1, "metalharbor-1")
    assert f"{REC}-retake2-s1 did not run: {RP.GATE_REFUSED} metalharbor-1 after waiting " \
           "1800 s: the one-minute load is 1.5, not below 1" in reason
    assert oracle.launched[-1] == first and len(oracle.calls) == 6


# ------------------------------------------------------------ the control


@pytest.mark.parametrize("name, change, says", [
    (OFF, {"tick": b"tick,frame_count\n1,1\n2,3\n"},
     "their tick captures are not byte-identical ("),
    (ON, {"inputs.game_args": ["-console"]},
     "the pair differs in more than --device-profile: game_args:"),
    (ON, {"session.game_environment": {"PATH": "/usr/bin", "LANG": "C"}},
     "the pair differs in more than --device-profile: game_environment."),
    (ON, {"defines": ("tick_probe", ["TICK_LIMIT=1300"])},
     "the pair differs in more than --device-profile: built_probes[1].defines[0]:"),
])
def test_a_control_whose_pair_differs_stops_the_batch_before_any_boot(tmp_path, monkeypatch,
                                                                       name, change, says):
    """The probe's own criterion: byte-identical tick captures, from runs that
    differ only in --device-profile."""
    oracle = Oracle(tmp_path, monkeypatch)
    if "tick" in change:
        oracle.tick_of[name] = change.pop("tick")
    if "defines" in change:
        mod, defines = change.pop("defines")
        oracle.defines_of[(name, mod)] = defines
    oracle.receipt_change[name] = change
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert (code, step, oracle.launched) == (1, "control-on", [OFF, ON])
    assert reason.startswith("the device-profile control failed (harness/replay/"
                             "native_device_profile.c: a pair of captures") and says in reason
    assert doc["references"]["device_profile_control"]["passed"] is False
    assert doc["outputs"]["device_profile_control"]["passed"] is False


def test_the_control_s_run_without_the_probe_must_not_load_it(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.receipt_change[OFF] = {"session.device_profile": {"file": "x", "sha256": PROFILE}}
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0], oracle.launched) == (1, "control-off", [OFF])
    assert f"{OFF} is the control's run without the profile probe and loaded it" in \
        stopped(doc)[1]


def test_the_control_s_run_with_the_probe_is_held_to_the_boots_profile(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.profile_of[ON] = "c" * 64
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "boot-02")
    assert f"{ON} recorded device profile cccccccccccc, and the batch's first two boots " \
           "aaaaaaaaaaaa" in stopped(doc)[1]


def test_a_top_up_takes_no_control_and_needs_the_earlier_batch_s_passed(tmp_path,
                                                                       monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    code, first = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "0",
                        "--no-demo")
    assert code == 0 and first["references"]["device_profile_control"]["passed"] is True
    kept = tmp_path / "earlier.json"
    kept.write_text((tmp_path / "batches" / "b.json").read_text())
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--no-stack-pad", "--series", "2",
                      "--no-demo", "--after", str(kept))
    assert code == 0 and [s["kind"] for s in doc["steps"]] == ["series"]
    assert doc["outputs"]["device_profile_control"]["on"] == ON
    failed = json.loads(kept.read_text())
    failed["references"]["device_profile_control"]["passed"] = False
    kept.write_text(json.dumps(failed))
    with pytest.raises(SystemExit, match="whose device-profile control passed"):
        B.references(kept)


# ------------------------------------------------------------ the device profile


def test_two_first_boots_that_disagree_on_the_device_profile_stop_the_batch(tmp_path,
                                                                          monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.profile_of["boot-ticks-v15b"] = "b" * 64
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "boot-02")
    assert "the first two boots recorded device profiles aaaaaaaaaaaa and bbbbbbbbbbbb" in \
        stopped(doc)[1]
    assert oracle.launched == [OFF, ON, "boot-ticks-v15a", "boot-ticks-v15b"]
    assert doc["references"]["device_profile_sha256"] is None


def test_a_later_run_on_another_device_profile_stops_the_batch_at_its_gate(tmp_path,
                                                                           monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.profile_of[f"{REC}-retake1-s1"] = "c" * 64
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert (code, step) == (1, "metalharbor-1")
    assert f"{RP.GATE_REFUSED} metalharbor-1: the last run, {REC}-retake1-s1, recorded device " \
           "profile cccccccccccc" in reason
    assert oracle.launched[-1] == f"{REC}-retake1-s1"


@pytest.mark.parametrize("boots, stops", [
    (HELD.replace(b"processors=16", b"processors=8") + LOCALE + b"end\n", True),
    (HELD + LOCALE + b"end\n", False),
])
def test_a_device_set_that_is_not_the_recording_s_stops_the_batch_at_the_second_boot(
        tmp_path, monkeypatch, boots, stops):
    """Every re-take is held to the recording's devices and to the batch's one
    profile, so the boots' profile is compared with the recording's before
    the first series; a newer probe's added section passes."""
    oracle = Oracle(tmp_path, monkeypatch)
    recording = HELD + b"end\n"
    (RP.CAPTURES / REC / "sa2-device-profile.txt").write_bytes(recording)
    (RP.CAPTURES / REC / "receipt.json").write_text(json.dumps({"session": {"device_profile": {
        "file": "sa2-device-profile.txt", "sha256": sha(recording)}}}))
    oracle.profile_data = boots
    code, doc = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "2",
                      "--no-demo")
    if stops:
        assert (code, stopped(doc)[0], oracle.launched[-1]) == (1, "boot-02", "boot-ticks-v15b")
        assert "the batch's device profile cannot be the recording's, so every Metal Harbor " \
               "re-take would fail: boot-ticks-v15b's device profile differs from the " \
               "recording's in a section the recording holds, first in 'system'" in \
               stopped(doc)[1]
    else:
        assert (code, doc["steps"][3]["device_profile_fixed"]) == (0, sha(boots))


# ------------------------------------------------------------ what moves mid-batch


@pytest.mark.parametrize("move, says", [
    (lambda o: o.build.update(build="experimental-11.0-20261001-x86_64"),
     "the Proton build is not the one the batch began on: build:"),
    (lambda o: (o.root / "harness/replay/native_tick_probe.c").write_text("/* late fix */\n"),
     "1 frozen file(s) changed since the batch began: harness/replay/native_tick_probe.c"),
    (lambda o: (o.root / "tools/s4/wordlog.c").write_text("/* another reader */\n"),
     "1 frozen file(s) changed since the batch began: tools/s4/wordlog.c"),
    (lambda o: setattr(o, "steam", []), "Steam is not running"),
    (lambda o: setattr(o, "steam", [9999]),
     "Steam restarted during the batch (pid 5255 is gone, now [9999])"),
    (lambda o: setattr(o, "games", [4242]), "a game is still running (pid [4242])"),
    (lambda o: setattr(o, "inhibitors", []), "the batch's sleep inhibitor (sa2 retake_batch"),
    (lambda o: setattr(o, "suspend", o.suspend + 60.0),
     "the host was suspended for 60 s since the batch began"),
])
def test_what_moves_mid_batch_stops_it_at_the_next_launch(tmp_path, monkeypatch, move, says):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.after["boot-ticks-v15b"] = lambda: move(oracle)
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert (code, step) == (1, "boot-03") and says in reason
    assert oracle.launched == [OFF, ON, "boot-ticks-v15a", "boot-ticks-v15b"]


@pytest.mark.parametrize("name, change, says", [
    ("boot-ticks-v15a", {"session.launch_gate": None},
     "boot-ticks-v15a was not launched through this batch's gate at boot-01"),
    ("boot-ticks-v15b", {"session.load_average": {"at_launch": [1.02, 0.9, 0.8]}},
     "boot-ticks-v15b launched at a one-minute load of 1.02, not below 1"),
    ("boot-ticks-v15b", {"session.load_average": {"at_launch": [1.0, 0.9, 0.8]}},
     "boot-ticks-v15b launched at a one-minute load of 1.0, not below 1"),
    ("boot-ticks-v15d", {"session.idle_before_run": {"seconds": 45.0}},
     "boot-ticks-v15d opens its series and launched after 45.0 s idle, not 90 s"),
    ("boot-ticks-v15b", {"session.proton": {"build": "experimental-10.0-x"}},
     "boot-ticks-v15b ran on another Proton build than the batch's: build:"),
    ("boot-ticks-v15b", {"session.proton_at_end": {"build": "experimental-10.0-x"}},
     "boot-ticks-v15b ran on another Proton build than the batch's by its end: build:"),
    ("boot-ticks-v15b", {"session.host_state": None},
     "boot-ticks-v15b records no suspend clock at launch and at its end"),
    ("boot-ticks-v15b", {"session.saves": {"matches_pin": True, "pin": {"digest": "0" * 64}}},
     "boot-ticks-v15b did not start from the batch's pinned saves"),
    ("boot-ticks-v15b", {"session.saves": {"matches_pin": False, "pin": "the batch's"}},
     "boot-ticks-v15b did not start from the batch's pinned saves"),
    ("boot-ticks-v15b", {"installation_untouched": False},
     "boot-ticks-v15b: the installation was written"),
    ("boot-ticks-v15b", {"inputs.built_probes": [{
        "mod_id": "tick_probe", "source": "/elsewhere/native_tick_probe.c"}]},
     "boot-ticks-v15b built /elsewhere/native_tick_probe.c, outside this tree"),
    ("boot-ticks-v15b", {"inputs.built_probes": [{
        "mod_id": "tick_probe", "source": "harness/replay/native_tick_probe.c",
        "source_sha256": "0" * 64, "dll_sha256": sha(b"tick_probe")}]},
     "boot-ticks-v15b built tick_probe from files the freeze does not hold: "
     "harness/replay/native_tick_probe.c"),
    ("boot-ticks-v15b", {"word_log": {"digest_inputs": {"tools/s4/wordlog.c": "0" * 64}}},
     "boot-ticks-v15b built its tick capture with a reader the freeze does not hold: "
     "tools/s4/wordlog.c"),
    ("boot-ticks-v15b", {"word_log.digest_inputs": {}},
     "boot-ticks-v15b records no word-log reader inputs"),
])
def test_a_receipt_that_breaks_a_condition_stops_the_batch_after_its_call(
        tmp_path, monkeypatch, name, change, says):
    oracle = Oracle(tmp_path, monkeypatch)
    if change.get("session.saves", {}).get("pin") == "the batch's":
        change["session.saves"]["pin"] = RP.SS.load().record()
    oracle.receipt_change[name] = change
    monkeypatch.chdir(oracle.root)                      # a relative source is this tree's
    code, doc = batch(oracle, tmp_path, *SMALL)
    step, reason = stopped(doc)
    assert code == 1 and says in reason
    assert oracle.launched[-1] == name


def test_a_suspend_under_a_run_stops_the_batch_after_it(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.suspended_in["boot-ticks-v15b"] = 30.0
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "boot-02")
    assert "boot-ticks-v15b: the host was suspended 30 s during the run" in stopped(doc)[1]
    oracle = Oracle(tmp_path / "2", monkeypatch)
    oracle.suspended_in["boot-ticks-v15b"] = 1.5    # within SUSPEND_SLACK_S: not a suspend
    code, doc = batch(oracle, tmp_path / "2", *SMALL)
    assert code == 0 and doc["verdict"] == "BATCH_OK"


def test_a_proton_update_under_the_last_run_stops_the_batch(tmp_path, monkeypatch):
    """The demo is the last call: no later gate would see the update."""
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.proton_moves_in["attract-demo-v3a"] = "experimental-11.0-20261001-x86_64"
    code, doc = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "0")
    assert (code, stopped(doc)[0], oracle.launched[-1]) == (1, "demo", "attract-demo-v3a")
    assert "attract-demo-v3a ran on another Proton build than the batch's by its end: build:" \
        in stopped(doc)[1]


@pytest.mark.parametrize("change, says", [
    (lambda o: o.snapshot_of.update({"boot-ticks-v15b": '{"files": {}}\n'}),
     "boot-ticks-v15b launched from another installation than the batch's first run"),
    (lambda o: o.dll_of.update({("boot-ticks-v15b", "tick_probe"): "f" * 64}),
     "boot-ticks-v15b built tick_probe harness/replay/native_tick_probe.c TICK_LIMIT=1200 as "
     "another DLL (ffffffffffff) than the batch's first build of it"),
])
def test_another_installation_or_another_build_of_a_probe_stops_the_batch(
        tmp_path, monkeypatch, change, says):
    oracle = Oracle(tmp_path, monkeypatch)
    change(oracle)
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "boot-02") and says in stopped(doc)[1]


def test_a_runner_that_fails_stops_the_batch(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.exit_of["boot-ticks-v15a"] = 1
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "boot-01")
    assert "the runner call exited 1: SA2_ORACLE_CAPTURE_PROBLEM name=boot-ticks-v15a" in \
        stopped(doc)[1]
    assert oracle.launched == [OFF, ON, "boot-ticks-v15a"]


def test_a_series_is_held_to_every_check_but_the_two_the_new_mask_settles(tmp_path,
                                                                          monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert code == 0
    assert [s["failed_checks"] for s in doc["outputs"]["series"]] == [
        ["tick_captures_identical", "import_logs_registrable"]] * 2
    oracle = Oracle(tmp_path / "2", monkeypatch)
    oracle.failed_checks[1] = ["trajectory_as_recorded"]
    code, doc = batch(oracle, tmp_path / "2", *SMALL)
    step, reason = stopped(doc)
    assert (code, step) == (1, "metalharbor-1")
    assert reason == ("retake_replays failed trajectory_as_recorded (only "
                      "tick_captures_identical, import_logs_registrable wait for the new mask)")
    assert not any("s2" in n for n in oracle.launched)


def test_the_re_judge_takes_no_series_that_broke_a_condition(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.failed_checks[2] = ["trajectory_as_recorded"]
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "metalharbor-2")
    assert [s["whole"] for s in doc["outputs"]["series"]] == [True, True]
    assert doc["outputs"]["rejudge_command"] == (
        ".venv/bin/python tools/oracle/retake_replays.py mh-record-t --rejudge retakes-s1.json")


def test_a_series_that_ran_fewer_re_takes_than_asked_stops_the_batch(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    real = oracle.series

    def short(cmd, env):
        k = cmd.index("--count") + 1
        return real([*cmd[:k], str(int(cmd[k]) - 1), *cmd[k + 1:]], env)
    monkeypatch.setattr(oracle, "series", short)
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert stopped(doc) == ("metalharbor-1", "1 re-take(s), not 2")


def test_a_series_that_wrote_no_summary_stops_the_batch(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    monkeypatch.setattr(oracle, "series", lambda cmd, env: (1, "STOPPED: no recording\n"))
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert stopped(doc) == ("metalharbor-1",
                            "retake_replays wrote no summary: STOPPED: no recording")


# ------------------------------------------------------------ stops of the batch itself


def test_sigterm_or_an_error_stops_the_batch_and_the_receipt_says_so(tmp_path, monkeypatch):
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.after["boot-ticks-v15a"] = lambda: os.kill(os.getpid(), signal.SIGTERM)
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, doc["verdict"], stopped(doc)) == (
        130, "BATCH_STOPPED", ("boot-01", "interrupted (SIGTERM); a runner and its game may "
                                          "outlive the batch"))
    assert doc["stopped"]["left_running"] == {"runners": [], "games": []}
    assert doc["rule_11"]["session_locked"] == {OFF: [False, False], ON: [False, False]}
    assert {s: signal.getsignal(s) for s in before} == before     # the handlers are put back
    oracle = Oracle(tmp_path / "2", monkeypatch)

    def full():
        raise OSError(28, "No space left on device")
    oracle.after["boot-ticks-v15a"] = full
    code, doc = batch(oracle, tmp_path / "2", *SMALL)
    assert (code, doc["verdict"], stopped(doc)) == (
        1, "BATCH_STOPPED", ("boot-01", "OSError: [Errno 28] No space left on device"))
    assert doc["outputs"]["control"] == [OFF, ON] and doc["ended_utc"]


def test_a_real_batch_runs_under_a_blocking_sleep_inhibitor_and_a_dry_run_takes_none(
        tmp_path, monkeypatch, capsys):
    oracle = Oracle(tmp_path, monkeypatch)
    execs = []

    def execvpe(file, args, env):
        execs.append((file, args, env))
        raise SystemExit("exec'd")
    monkeypatch.setattr(B.os, "execvpe", execvpe)
    monkeypatch.setattr(B, "under_inhibitor", UNDER_INHIBITOR)
    monkeypatch.delenv(B.INHIBITED_ENV)
    assert B.main([*SMALL, "--recording", REC, "--dry-run"], reexec=True) == 0 and execs == []
    with pytest.raises(SystemExit, match="exec'd"):
        B.main([*SMALL, "--recording", REC], reexec=True)
    (file, args, env), = execs
    assert (file, args[:6]) == ("systemd-inhibit", [
        "systemd-inhibit", "--no-ask-password", "--what=sleep", "--mode=block",
        "--who=sa2 retake_batch", f"--why={B.INHIBIT_WHY}"])
    assert args[6:] == [sys.executable, str(Path(B.__file__).resolve()), *SMALL,
                        "--recording", REC]
    assert env[B.INHIBITED_ENV] == "1" and oracle.calls == []

    def missing(file, args, env):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(B.os, "execvpe", missing)
    with pytest.raises(SystemExit, match="RETAKE_BATCH_REFUSED: cannot run under a sleep "
                                         "inhibitor"):
        B.main([*SMALL, "--recording", REC], reexec=True)
    # a caller that imports the tool never execs a batch: without the inhibitor
    # its preflight refuses, and nothing runs
    oracle.inhibitors = []
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0], oracle.calls, len(execs)) == (1, "preflight", [], 1)
    assert "the batch holds no sleep inhibitor" in stopped(doc)[1]


def test_the_rule_11_note_records_each_run_s_lock_state_and_how_its_effect_is_tested(
        tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.after["boot-ticks-v15a"] = lambda: setattr(oracle, "locked", True)
    oracle.after["boot-ticks-v15b"] = lambda: setattr(oracle, "blank", True)
    code, doc = batch(oracle, tmp_path, *SMALL)
    note = doc["rule_11"]
    assert code == 0
    assert (note["session_locked"]["boot-ticks-v15a"], note["session_locked"]["boot-ticks-v15b"],
            note["idle"]["boot-ticks-v15b"], note["idle"]["boot-ticks-v15d"]) == (
        [False, False], [True, True], [False, False], [True, True])
    assert note["lock_states_seen"] == ["locked", "unlocked"]
    assert "left the user's screen-lock and idle settings as they were" in \
        note["focus_and_occlusion"]
    assert "Whether a locked or occluded screen changes the game's timing is tested by the " \
           "repeats' agreement" in note["focus_and_occlusion"]
    assert "blocking sleep inhibitor" in note["sleep"]


# ------------------------------------------------------------ top-ups and the dry run


def test_a_top_up_is_held_to_the_batch_it_tops_up(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    code, first = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "0",
                        "--no-demo")
    assert code == 0
    earlier = tmp_path / "batches" / "b.json"
    kept = tmp_path / "earlier.json"
    kept.write_text(earlier.read_text())
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--no-stack-pad", "--series", "2",
                      "--no-demo", "--after", str(kept))
    assert code == 0 and doc["references"]["from"]["batch"] == first["batch"]
    assert doc["steps"][0]["gate"]["profile_sha256"] == PROFILE
    assert doc["references"]["installation_sha256"] == sha(SNAPSHOT.encode())
    oracle.snapshot_of[f"{REC}-retake1-s2"] = '{"files": {}}\n'
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--no-stack-pad", "--series", "2",
                      "--no-demo", "--after", str(kept))
    assert (code, stopped(doc)[0]) == (1, "metalharbor-1")
    assert "launched from another installation than the batch's first run" in stopped(doc)[1]
    oracle.build["build"] = "experimental-11.0-20261001-x86_64"
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--no-stack-pad", "--series", "2",
                      "--no-demo", "--after", str(kept))
    assert (code, stopped(doc)[0]) == (1, "preflight")
    assert "the Proton build is not the earlier batch's (build:" in stopped(doc)[1]


def test_a_top_up_after_a_receipt_with_no_agreed_profile_is_refused(tmp_path, monkeypatch):
    Oracle(tmp_path, monkeypatch)
    kept = tmp_path / "earlier.json"
    kept.write_text(json.dumps({"schema": B.SCHEMA, "references": {
        "device_profile_sha256": None}}))
    with pytest.raises(SystemExit, match="whose first two boots agreed on a device profile"):
        B.references(kept)


def test_a_batch_that_does_not_open_with_the_control_and_two_boots_is_refused(
        tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--series", "2", "--no-demo")
    assert (code, stopped(doc)[0], oracle.calls) == (1, "preflight", [])
    assert "a batch opens with the device-profile control's two boots and then two plain " \
           "boots" in stopped(doc)[1]


def test_the_dry_run_prints_every_call_and_writes_nothing(tmp_path, monkeypatch, capsys):
    oracle = Oracle(tmp_path, monkeypatch)
    before = sorted(p for p in tmp_path.rglob("*"))
    assert B.main([*SMALL, "--recording", REC, "--dry-run", "--receipt",
                   str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("RETAKE_BATCH_DRY_RUN: 7 calls, 9 runs, 6 of them cold;")
    assert f"boot_capture.py --name {OFF} --no-device-profile --min-idle 90" in out
    assert f"retake_replays.py {REC} --count 2" in out
    assert "device-profile control the first two runs" in out
    assert "a real batch runs under systemd-inhibit --what=sleep" in out
    assert out.rstrip().endswith("preflight: ok")
    assert sorted(p for p in tmp_path.rglob("*")) == before and oracle.calls == []
    oracle.steam = []
    assert B.main([*SMALL, "--dry-run"]) == 1
    assert "A real batch stops before any run:\n  - Steam is not running" in \
        capsys.readouterr().out
    oracle.steam = [5255]
    monkeypatch.setattr(RP, "sleep_inhibitors", lambda: None)
    assert B.main([*SMALL, "--recording", REC, "--dry-run"]) == 1
    assert "no sleep inhibitor can be taken" in capsys.readouterr().out


# ------------------------------------------------------------ the fold review's additions


def _control_pair(tmp_path, monkeypatch) -> dict:
    """A batch whose control passed; its references. The pair's receipts stay
    in the made-up oracle's captures for judge_control to read again."""
    oracle = Oracle(tmp_path, monkeypatch)
    code, doc = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "0",
                      "--no-demo")
    assert code == 0 and doc["references"]["device_profile_control"]["passed"] is True
    return doc["references"]


def _rewrite(name: str, change) -> None:
    path = RP.CAPTURES / name / "receipt.json"
    rec = json.loads(path.read_text())
    change(rec)
    path.write_text(json.dumps(rec))


def _tick_first(rec):
    built = rec["inputs"]["built_probes"]
    built[0], built[1] = built[1], built[0]


def _off_records_a_profile(rec):
    rec["session"]["device_profile"] = {"file": "sa2-device-profile.txt", "sha256": PROFILE}


@pytest.mark.parametrize("change, clause", [
    # both runs build the tick probe first: alike, and neither as the criterion asks
    ({OFF: lambda r: _tick_first(r), ON: lambda r: _tick_first(r)}, "vanilla_probe_first"),
    # the profile probe loaded, but not last
    ({ON: lambda r: r["inputs"].update(built_probes=[r["inputs"]["built_probes"][i]
                                                      for i in (0, 2, 1)])},
     "profile_probe_only_in_on"),
    # the run without the probe recorded a profile anyway
    ({OFF: _off_records_a_profile}, "profile_probe_only_in_on"),
])
def test_each_clause_of_the_control_s_criterion_can_fail_it(tmp_path, monkeypatch, change,
                                                           clause):
    refs = _control_pair(tmp_path, monkeypatch)
    for name, how in change.items():
        _rewrite(name, how)
    verdict = B.judge_control(OFF, ON, refs)
    assert (verdict[clause], verdict["passed"]) == (False, False)
    assert verdict["configuration_difference"] is None and verdict["tick_captures_identical"]


@pytest.mark.parametrize("change, where", [
    (lambda r: r["inputs"]["built_probes"][1].update(dll_sha256="f" * 64),
     "built_probes[1].dll_sha256"),
    (lambda r: r["inputs"]["enabled_mods"].insert(0, "another_mod"), "enabled_mods"),
    (lambda r: r.update(command="timeout 200s env proton run ./sonic2app-unpacked.exe"),
     "command"),
])
def test_the_control_s_pair_differs_in_its_dlls_mods_or_command(tmp_path, monkeypatch, change,
                                                                where):
    refs = _control_pair(tmp_path, monkeypatch)
    _rewrite(ON, change)
    verdict = B.judge_control(OFF, ON, refs)
    assert verdict["passed"] is False
    assert str(verdict["configuration_difference"]).startswith(where)


@pytest.mark.parametrize("change, says", [
    (lambda o: o.after.update({"boot-ticks-v15b": lambda: (
        RP.OUT / "snapshots" / "boot-ticks-v15b-install-before.json").unlink()}),
     "boot-ticks-v15b left no snapshot of the installation it launched from"),
    (lambda o: o.dll_of.update({("boot-ticks-v15b", "tick_probe"): None}),
     "boot-ticks-v15b records no DLL for tick_probe harness/replay/native_tick_probe.c"),
])
def test_a_run_with_no_snapshot_or_no_dll_stops_the_batch(tmp_path, monkeypatch, change, says):
    oracle = Oracle(tmp_path, monkeypatch)
    change(oracle)
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, stopped(doc)[0]) == (1, "boot-02") and says in stopped(doc)[1]


@pytest.mark.parametrize("change, says", [
    (lambda o: o.snapshot_of.update({f"{REC}-retake{k}-s1": '{"files": {}}\n'
                                     for k in (1, 2)}),
     "launched from another installation than the batch's first run"),
    (lambda o: o.dll_of.update({(f"{REC}-retake{k}-s1", "tick_probe"): "f" * 64
                                for k in (1, 2)}),
     "than the batch's first build of it"),
])
def test_a_top_up_is_held_to_the_earlier_batch_s_installation_and_dlls(tmp_path, monkeypatch,
                                                                       change, says):
    """Every run of the top-up alike, and unlike the earlier batch's: only the
    earlier batch's references can refuse it."""
    oracle = Oracle(tmp_path, monkeypatch)
    code, first = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "0",
                        "--no-demo")
    assert code == 0
    kept = tmp_path / "earlier.json"
    kept.write_text((tmp_path / "batches" / "b.json").read_text())
    change(oracle)
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--no-stack-pad", "--series", "2",
                      "--no-demo", "--after", str(kept))
    assert (code, stopped(doc)[0]) == (1, "metalharbor-1") and says in stopped(doc)[1]


def test_a_top_up_from_another_tree_is_refused_before_any_run(tmp_path, monkeypatch):
    oracle = Oracle(tmp_path, monkeypatch)
    code, first = batch(oracle, tmp_path, "--boots", "2", "--no-stack-pad", "--series", "0",
                        "--no-demo")
    assert code == 0
    kept = tmp_path / "earlier.json"
    kept.write_text(json.dumps({**first, "root": "/elsewhere/tree"}))
    launched = list(oracle.launched)
    code, doc = batch(oracle, tmp_path, "--boots", "0", "--no-stack-pad", "--series", "2",
                      "--no-demo", "--after", str(kept))
    assert (code, stopped(doc)[0], oracle.launched) == (1, "preflight", launched)
    assert "the earlier batch ran from /elsewhere/tree" in stopped(doc)[1]


def test_sighup_stops_the_batch_as_sigterm_does(tmp_path, monkeypatch):
    before = signal.getsignal(signal.SIGHUP)
    oracle = Oracle(tmp_path, monkeypatch)
    oracle.after["boot-ticks-v15a"] = lambda: os.kill(os.getpid(), signal.SIGHUP)
    code, doc = batch(oracle, tmp_path, *SMALL)
    assert (code, doc["verdict"], stopped(doc)) == (
        130, "BATCH_STOPPED", ("boot-01", "interrupted (SIGHUP); a runner and its game may "
                                          "outlive the batch"))
    assert signal.getsignal(signal.SIGHUP) == before
