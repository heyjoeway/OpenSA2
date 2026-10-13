"""The runner's Proton record and launch gate (tools/oracle/run_oracle_probe.py,
its docstring's item 6), and the recorder's device check for a probe that
writes sections the recording never held (tools/oracle/record_stage.py
device_differences).

Each case is a way the batch's conditions could pass quietly: a Proton update
the record does not see (a Steam client update or a user_settings.py among
them), a gate that is named and not whole or holds a limit that is not a
number, a run launched under load, a run of another tool in the middle of a
batch, a game left running, a Steam client that restarted, a host that
suspended or a sleep inhibitor that is gone, a device profile compared by a
section the recording lacks, by a kind no probe added on purpose, or not
compared at all.

No runner, game, Wine or Steam process runs, and no inhibitor is taken:
/proc, the Proton wrapper, the Steam client's files, logind's answers, the
load, the idle measure and the game are made up under tmp_path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import record_stage as R  # noqa: E402
import run_oracle_probe as RP  # noqa: E402
from test_oracle_tools import CHAO, fake_game, runner_tree  # noqa: E402
import test_record_stage as TR  # noqa: E402  the recorder's made-up captures
from test_record_stage import vanilla  # noqa: E402,F401  the fixture judge's captures need

BUILD = "experimental-11.0-20260924-x86_64"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake_proton(tmp_path: Path) -> Path:
    """Steam's layout on this host: a wrapper whose launcher is its own copy,
    whose `version` is stale, and whose `files` links into the distribution,
    beside the distribution's own `version`."""
    dist = tmp_path / "Proton - Experimental"
    (dist / "files" / "bin").mkdir(parents=True)
    (dist / "files" / "lib").mkdir()
    (dist / "files" / "bin" / "wine").write_bytes(b"wine")
    (dist / "files" / "lib" / "a.so").write_bytes(b"lib")
    (dist / "files" / "share").symlink_to("bin")
    (dist / "version").write_text(f"1790244902 {BUILD}\n")
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    (wrapper / "proton").write_bytes(b"#!/usr/bin/env python3\n")
    (wrapper / "version").write_text("1789159601 experimental-11.0-20260910b-x86_64\n")
    (wrapper / "files").symlink_to(dist / "files")
    return wrapper / "proton"


# ------------------------------------------------------------ the Proton build


def test_the_build_is_the_distribution_s_and_the_wrapper_s_stale_version_is_kept_apart(
        tmp_path):
    launcher = fake_proton(tmp_path)
    got = RP.proton_build(launcher)
    dist = tmp_path / "Proton - Experimental"
    assert {k: v for k, v in got.items()
            if k not in ("files_tree", "steam_client", "user_settings_sha256")} == {
        "launcher": str(launcher), "launcher_sha256": sha(b"#!/usr/bin/env python3\n"),
        "wrapper_version_file": "1789159601 experimental-11.0-20260910b-x86_64",
        "files_link": str(dist / "files"), "files": str(dist / "files"),
        "distribution_version_file": f"1790244902 {BUILD}", "build": BUILD}
    assert got["files_tree"]["entries"] == 3          # two files and one link
    assert RP.proton_build(launcher) == got           # a second run records it alike


@pytest.mark.parametrize("move", ["update_a_file", "new_version", "new_launcher", "new_file",
                                  "relink"])
def test_every_way_steam_or_the_wrapper_moves_the_build_moves_the_record(tmp_path, move):
    launcher = fake_proton(tmp_path)
    before = RP.proton_build(launcher)
    dist = tmp_path / "Proton - Experimental"
    if move == "update_a_file":
        wine = dist / "files" / "bin" / "wine"
        os.utime(wine, ns=(wine.stat().st_atime_ns, wine.stat().st_mtime_ns + 1))
    elif move == "new_version":
        (dist / "version").write_text("1790999999 experimental-11.0-20261001-x86_64\n")
    elif move == "new_launcher":
        launcher.write_bytes(b"#!/usr/bin/env python3\n# edited\n")
    elif move == "new_file":
        (dist / "files" / "lib" / "b.so").write_bytes(b"")
    else:
        (dist / "files" / "share").unlink()
        (dist / "files" / "share").symlink_to("lib")
    assert RP.proton_build(launcher) != before


def test_a_build_that_cannot_be_read_says_so_and_names_no_build(tmp_path):
    launcher = fake_proton(tmp_path)
    (launcher.parent / "files").unlink()
    (launcher.parent / "files").symlink_to(tmp_path / "gone")
    got = RP.proton_build(launcher)
    assert (got["build"], got["files_tree"]) == (None, None)
    assert got["problems"] == [f"{launcher.parent / 'files'} leads to no directory"]


def test_every_receipt_records_the_build_and_no_gate_and_the_launch_is_unchanged(
        tmp_path, monkeypatch, capsys):
    """session.proton in every receipt, session.launch_gate null without a
    gate, and the loader's profile and the launch command as they were
    (profile_for and the spawn arguments are frozen for the batch)."""
    probe, _ = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    monkeypatch.setattr(probe, "PROTON", fake_proton(tmp_path))
    monkeypatch.setattr(probe, "STEAM_CLIENT", tmp_path / "Steam")
    states = iter([{"boottime_minus_monotonic_s": 7.0, "session": "2", "locked": False,
                    "idle": False},
                   {"boottime_minus_monotonic_s": 7.25, "session": "2", "locked": True,
                    "idle": True}])
    monkeypatch.setattr(probe, "host_state", lambda: next(states))
    monkeypatch.delenv(probe.LAUNCH_GATE, raising=False)
    args = fake_game(tmp_path, monkeypatch, probe, writes_save=False)
    probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    assert rec["session"]["proton"] == probe.proton_build()
    assert rec["session"]["proton_at_end"] == rec["session"]["proton"]
    assert rec["session"]["proton"]["build"] == BUILD
    assert rec["session"]["host_state"] == {
        "at_launch": {"boottime_minus_monotonic_s": 7.0, "session": "2", "locked": False,
                      "idle": False},
        "at_end": {"boottime_minus_monotonic_s": 7.25, "session": "2", "locked": True,
                   "idle": True}}
    assert "host_state records whether the session was locked or idle" in \
        rec["session"]["note"]
    assert "screen changes the timing is tested by the agreement of repeats taken in both " \
           "states" in rec["session"]["note"]
    assert rec["session"]["launch_gate"] is None
    assert rec["command"] == shlex.join([
        "timeout", "--signal=TERM", "--kill-after=5s", "60s", "env",
        f"STEAM_COMPAT_DATA_PATH={probe.PREFIX}",
        f"STEAM_COMPAT_CLIENT_INSTALL_PATH={probe.STEAM_CLIENT}",
        str(tmp_path / "wrapper" / "proton"), "runinprefix", "./sonic2app-unpacked.exe"])
    assert rec["inputs"]["modloader_profile"] == probe.profile_for(
        ["m"], (640, 480), True, False, 1, 1)
    out = capsys.readouterr().out
    assert f"  Proton: {BUILD}\n" in out
    assert "  host: session locked False at launch and True at the end, idle False and True; " \
           "suspended 0.2 s during the run" in out


def test_a_proton_update_under_a_running_game_shows_in_the_build_read_at_its_end(
        tmp_path, monkeypatch, capsys):
    """Steam updates Proton in place, under a running game too (the RT batch
    review, finding 2): the build is read again once the game has exited."""
    probe, _ = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
    launcher = fake_proton(tmp_path)
    monkeypatch.setattr(probe, "PROTON", launcher)
    monkeypatch.setattr(probe, "STEAM_CLIENT", tmp_path / "Steam")
    monkeypatch.setattr(probe, "host_state", lambda: {"boottime_minus_monotonic_s": 0.0})
    monkeypatch.delenv(probe.LAUNCH_GATE, raising=False)
    args = fake_game(tmp_path, monkeypatch, probe, writes_save=False)
    game = probe.subprocess.run

    def update_under_the_game(cmd, **kw):
        done = game(cmd, **kw)
        if cmd[0] == "timeout":
            (tmp_path / "Proton - Experimental" / "version").write_text(
                "1790999999 experimental-11.0-20261001-x86_64\n")
        return done

    monkeypatch.setattr(probe.subprocess, "run", update_under_the_game)
    probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    assert (rec["session"]["proton"]["build"], rec["session"]["proton_at_end"]["build"]) == (
        BUILD, "experimental-11.0-20261001-x86_64")
    assert "; MOVED during the run, at its end build:" in capsys.readouterr().out


# ------------------------------------------------------------ Steam


def test_steam_is_the_client_s_own_process_even_after_it_updated_itself(tmp_path, monkeypatch):
    steam = tmp_path / "Steam" / "ubuntu12_32" / "steam"
    steam.parent.mkdir(parents=True)
    steam.write_bytes(b"")
    proc = tmp_path / "proc"
    for pid, exe in ((123, str(steam)), (124, str(tmp_path / "other")),
                     (125, f"{steam} (deleted)"), (126, str(steam) + "webhelper")):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "exe").symlink_to(exe)
    (proc / "127").mkdir()                             # a process whose exe cannot be read
    (proc / "self").mkdir()
    monkeypatch.setattr(RP, "PROC", proc)
    monkeypatch.setattr(RP, "STEAM_BINARY", steam)
    assert RP.steam_pids() == [123, 125]


# ------------------------------------------------------------ the gate


class World:
    """The host as the gate sees it, every measure made up: a tree with two
    frozen files, the pinned saves, Steam running, a Proton build, a load, an
    idle time, and the last game that exited."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.probe, self.install = runner_tree(tmp_path, monkeypatch, {"SONIC2B__ALF": CHAO})
        self.root = tmp_path / "tree"
        (self.root / "harness" / "replay").mkdir(parents=True)
        (self.root / "harness" / "replay" / "native_tick_probe.c").write_text("/* probe */\n")
        (self.root / "tools").mkdir()
        (self.root / "tools" / "pins.json").write_text("{}\n")
        monkeypatch.setattr(RP, "ROOT", self.root)
        monkeypatch.setattr(RP, "CAPTURES", tmp_path / "captures")
        self.build = {"build": BUILD, "files_tree": {"entries": 3, "sha256": "7" * 64}}
        self.steam = [5255]
        self.games: list[int] = []
        self.inhibitors = [{"what": "sleep", "who": "sa2 retake_batch", "why": "a batch",
                            "mode": "block", "uid": 1000, "pid": 777}]
        self.suspend = 100.5
        self.load = [0.4, 0.5, 0.6]
        self.idle = 120.0
        monkeypatch.setattr(RP, "proton_build", lambda launcher=None, steam_client=None:
                            copy.deepcopy(self.build))
        monkeypatch.setattr(RP, "steam_pids", lambda: list(self.steam))
        monkeypatch.setattr(RP, "game_pids", lambda: list(self.games))
        monkeypatch.setattr(RP, "sleep_inhibitors", lambda: copy.deepcopy(self.inhibitors))
        monkeypatch.setattr(RP, "suspend_clock", lambda: self.suspend)
        monkeypatch.setattr(RP, "host_state", lambda: {
            "boottime_minus_monotonic_s": self.suspend, "session": "2", "locked": False,
            "idle": False})
        monkeypatch.setattr(RP, "load_average", lambda: None if self.load is None
                            else list(self.load))
        monkeypatch.setattr(RP, "idle_before_run", lambda now=None: {"seconds": self.idle})

    def spec(self, **change) -> dict:
        out = {"schema": RP.GATE_SCHEMA, "batch": "retake-batch-x", "step": "boot-03",
               "root": str(self.root), "batch_started_epoch": 1000.0, "written_epoch": 2000.0,
               "max_load": 1.0, "min_idle_first_s": 90.0, "wait_s": 60.0, "poll_s": 15.0,
               "steam": True, "proton": copy.deepcopy(self.build),
               "freeze": {"harness/replay/native_tick_probe.c": sha(b"/* probe */\n"),
                          "tools/pins.json": sha(b"{}\n")},
               "saves_pin": RP.SS.load().digest, "profile_sha256": None,
               "steam_pid": 5255, "suspend_clock_s": 100.0,
               "sleep_inhibitor": {"pid": 777, "who": "sa2 retake_batch"}}
        out.update(change)
        return out

    def ended(self, name: str, epoch: float, gate_batch: str | None = "retake-batch-x",
              profile: str | None = "a" * 64, receipt: bool = True) -> None:
        """`name`'s game exited at `epoch`, with a receipt as the runner writes one."""
        (RP.OUT / "locks").mkdir(parents=True, exist_ok=True)
        (RP.OUT / "locks" / RP.LAST_EXIT).write_text(json.dumps({"name": name,
                                                                  "epoch": epoch}))
        if receipt:
            (RP.CAPTURES / name).mkdir(parents=True, exist_ok=True)
            (RP.CAPTURES / name / "receipt.json").write_text(json.dumps({"session": {
                "launch_gate": None if gate_batch is None else {"batch": gate_batch},
                "device_profile": {"sha256": profile}}}))


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def test_a_gate_whose_conditions_hold_passes_at_once_and_says_what_it_measured(
        tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.ended("boot-ticks-v15b", 2500.0)                  # since the gate was written: not first
    got = RP.wait_for_gate(w.spec(), clock=Clock(), sleep=Clock().sleep)
    assert got == {"batch": "retake-batch-x", "step": "boot-03", "polls": 1, "waited_s": 0.0,
                   "max_load": 1.0, "min_idle_first_s": 90.0, "profile_sha256_required": None,
                   "frozen_files": 2, "steam_pids": [5255], "game_pids": [],
                   "suspend_clock_s": 100.5, "proton_build": BUILD,
                   "last_run": "boot-ticks-v15b", "first_of_step": False,
                   "load": [0.4, 0.5, 0.6]}


@pytest.mark.parametrize("break_it, says", [
    (lambda w: setattr(w, "steam", []), "Steam is not running"),
    (lambda w: w.build.update(build="experimental-11.0-20261001-x86_64"),
     "the Proton build is not the one the batch began on: build:"),
    (lambda w: w.build["files_tree"].update(sha256="8" * 64),
     "the Proton build is not the one the batch began on: files_tree.sha256:"),
    (lambda w: (w.root / "harness/replay/native_tick_probe.c").write_text("/* edited */\n"),
     "1 frozen file(s) changed since the batch began: harness/replay/native_tick_probe.c"),
    (lambda w: (w.root / "tools/pins.json").unlink(),
     "1 frozen file(s) changed since the batch began: tools/pins.json"),
    (lambda w: (w.install / "resource/gd_PC/SAVEDATA/SONIC2B__S01").write_bytes(b""),
     "are not the pinned start state"),
    (lambda w: setattr(w, "load", None), "the host reports no load average"),
    (lambda w: setattr(w, "steam", [6000]),
     "Steam restarted during the batch (pid 5255 is gone, now [6000])"),
    (lambda w: setattr(w, "games", [4242]),
     "a game is still running (pid [4242]): an earlier run's game outlived its runner"),
    (lambda w: setattr(w, "inhibitors", []),
     "the batch's sleep inhibitor (sa2 retake_batch, pid 777) is no longer held"),
    (lambda w: setattr(w, "inhibitors", None), "is no longer held"),
    (lambda w: w.inhibitors[0].update(mode="delay"), "is no longer held"),
    (lambda w: w.inhibitors[0].update(what="idle"), "is no longer held"),
    (lambda w: setattr(w, "suspend", 160.0),
     "the host was suspended for 60 s since the batch began"),
])
def test_each_condition_that_fails_refuses_the_launch_without_waiting(tmp_path, monkeypatch,
                                                                      break_it, says):
    w = World(tmp_path, monkeypatch)
    spec = w.spec()                                     # as the batch began
    break_it(w)
    clock = Clock()
    with pytest.raises(SystemExit) as e:
        RP.wait_for_gate(spec, clock=clock, sleep=clock.sleep)
    assert e.value.code.startswith(f"{RP.GATE_REFUSED} boot-03: ")
    assert says in e.value.code
    assert clock.slept == []


def test_a_gate_for_another_tree_or_another_pin_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="the gate was written for the tree at /elsewhere"):
        RP.wait_for_gate(w.spec(root="/elsewhere"))
    with pytest.raises(SystemExit, match=r"the save pin is \w{12}, and the batch began on 000000"):
        RP.wait_for_gate(w.spec(saves_pin="0" * 64))


def test_the_load_is_waited_for_and_the_launch_refused_when_it_stays_high(tmp_path,
                                                                          monkeypatch, capsys):
    w = World(tmp_path, monkeypatch)
    w.ended("boot-ticks-v15b", 2500.0)
    loads = iter([[1.3, 1.1, 0.9], [1.0, 1.0, 0.9], [0.8, 1.0, 0.9]])
    monkeypatch.setattr(RP, "load_average", lambda: next(loads))
    clock = Clock()
    got = RP.wait_for_gate(w.spec(), clock=clock, sleep=clock.sleep)
    assert (got["polls"], got["waited_s"], got["load"], clock.slept) == (
        3, 30.0, [0.8, 1.0, 0.9], [15.0, 15.0])
    assert "waiting: the one-minute load is 1.3, not below 1" in capsys.readouterr().err
    monkeypatch.setattr(RP, "load_average", lambda: [1.2, 1.0, 0.9])
    clock = Clock()
    with pytest.raises(SystemExit) as e:
        RP.wait_for_gate(w.spec(), clock=clock, sleep=clock.sleep)
    assert e.value.code == (f"{RP.GATE_REFUSED} boot-03 after waiting 60 s: the one-minute load "
                            "is 1.2, not below 1")


def test_only_the_first_launch_since_the_gate_was_written_must_be_cold(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.idle = 30.0
    w.ended("boot-ticks-v15b", 1500.0)                  # before the gate: this launch is first
    clock = Clock()
    with pytest.raises(SystemExit) as e:
        RP.wait_for_gate(w.spec(), clock=clock, sleep=clock.sleep)
    assert e.value.code.endswith("the oracle has been idle 30.0 s, not 90 s, and the first run "
                                 "of a series is cold")
    assert RP.wait_for_gate(w.spec(min_idle_first_s=0.0))["first_of_step"] is True
    w.ended("mh-retake1", 2500.0)                       # the series' first run has ended
    got = RP.wait_for_gate(w.spec())
    assert (got["first_of_step"], "idle_s" in got) == (False, False)


def test_a_run_of_another_tool_during_the_batch_or_another_profile_is_refused(tmp_path,
                                                                             monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.ended("interloper", 2500.0, gate_batch=None)
    with pytest.raises(SystemExit, match="the last run, interloper, was not launched through "
                                         "batch retake-batch-x's gate"):
        RP.wait_for_gate(w.spec())
    w.ended("boot-ticks-v15b", 2500.0, profile="b" * 64)
    assert RP.wait_for_gate(w.spec())["last_run"] == "boot-ticks-v15b"   # no profile held yet
    with pytest.raises(SystemExit, match="the last run, boot-ticks-v15b, recorded device profile "
                                         "bbbbbbbbbbbb, and the batch's first two boots "
                                         "aaaaaaaaaaaa"):
        RP.wait_for_gate(w.spec(profile_sha256="a" * 64))
    w.ended("crashed", 2500.0, receipt=False)
    with pytest.raises(SystemExit, match="the last run, crashed, left no readable receipt"):
        RP.wait_for_gate(w.spec())


def test_a_run_that_ended_before_the_batch_began_is_not_the_batch_s(tmp_path, monkeypatch):
    """A top-up's first launch follows the earlier batch's last run."""
    w = World(tmp_path, monkeypatch)
    w.ended("earlier-batch-run", 900.0, gate_batch="retake-batch-earlier", profile="b" * 64)
    got = RP.wait_for_gate(w.spec(profile_sha256="a" * 64))
    assert (got["last_run"], got["first_of_step"]) == ("earlier-batch-run", True)


@pytest.mark.parametrize("spoil, names", [
    (lambda s: s.pop("freeze"), "freeze"),
    (lambda s: s.update(max_load=True), "max_load"),
    (lambda s: s.update(profile_sha256=5), "profile_sha256"),
    (lambda s: s.update(schema="sa2-oracle-launch-gate/v0"), "schema, poll_s"),
    (lambda s: s.update(freeze={}), "schema, poll_s"),
    (lambda s: s.update(poll_s=0), "schema, poll_s"),
    (lambda s: s.update(max_load=0), "schema, poll_s"),
    (lambda s: s.update(max_load=float("nan")), "max_load"),
    (lambda s: s.update(min_idle_first_s=float("nan")), "min_idle_first_s"),
    (lambda s: s.update(wait_s=float("inf")), "wait_s"),
    (lambda s: s.update(steam_pid=True), "steam_pid"),
    (lambda s: s.update(steam_pid=5255.0), "steam_pid"),
    (lambda s: s.update(suspend_clock_s=float("nan")), "suspend_clock_s"),
    (lambda s: s.pop("sleep_inhibitor"), "sleep_inhibitor"),
    (lambda s: s.update(sleep_inhibitor={"who": "sa2 retake_batch"}), "sleep_inhibitor"),
    (lambda s: s.update(sleep_inhibitor={"pid": 777}), "sleep_inhibitor"),
])
def test_a_named_gate_that_is_not_whole_refuses_the_launch(tmp_path, monkeypatch, spoil, names):
    w = World(tmp_path, monkeypatch)
    spec = w.spec()
    spoil(spec)
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(spec))            # NaN and Infinity, as json writes them
    with pytest.raises(SystemExit) as e:
        RP.launch_gate({RP.LAUNCH_GATE: str(path)})
    assert e.value.code.startswith(f"{RP.GATE_REFUSED}: the launch gate {path} is not whole (")
    assert names in e.value.code
    with pytest.raises(SystemExit, match="which cannot be read"):
        RP.launch_gate({RP.LAUNCH_GATE: str(tmp_path / "missing.json")})


def test_no_gate_named_is_no_gate_and_a_gate_named_is_recorded_with_its_digest(tmp_path,
                                                                             monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert RP.launch_gate({}) is None and RP.launch_gate({RP.LAUNCH_GATE: ""}) is None
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(w.spec()))
    got = RP.launch_gate({RP.LAUNCH_GATE: str(path)})
    assert (got["gate"], got["gate_sha256"], got["step"]) == (
        str(path), sha(path.read_bytes()), "boot-03")


def gated_game(tmp_path, monkeypatch):
    """The runner's run() with a made-up game and a World, the game's launches
    counted."""
    w = World(tmp_path, monkeypatch)
    probe = w.probe
    args = fake_game(tmp_path, monkeypatch, probe, writes_save=False)
    launched = []
    inner = probe.subprocess.run

    def run(cmd, **kw):
        if cmd[0] == "timeout":
            launched.append(kw.get("env"))
        return inner(cmd, **kw)

    monkeypatch.setattr(probe.subprocess, "run", run)
    return w, args, launched


def test_the_runner_launches_nothing_when_its_gate_refuses(tmp_path, monkeypatch):
    w, args, launched = gated_game(tmp_path, monkeypatch)
    w.steam = []
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(w.spec()))
    monkeypatch.setenv(RP.LAUNCH_GATE, str(path))
    with pytest.raises(SystemExit, match=f"^{RP.GATE_REFUSED} boot-03: Steam is not running"):
        w.probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    assert launched == [] and not (tmp_path / "captures" / "c").exists()


def test_a_gated_run_records_the_gate_and_the_game_never_sees_it(tmp_path, monkeypatch):
    w, args, launched = gated_game(tmp_path, monkeypatch)
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(w.spec(min_idle_first_s=0.0)))
    monkeypatch.setenv(RP.LAUNCH_GATE, str(path))
    w.probe.run(args, tmp_path / "captures" / "c", (640, 480), [])
    rec = json.loads((tmp_path / "captures" / "c" / "receipt.json").read_text())
    gate = rec["session"]["launch_gate"]
    assert (gate["batch"], gate["step"], gate["gate"], gate["load"]) == (
        "retake-batch-x", "boot-03", str(path), [0.4, 0.5, 0.6])
    assert len(launched) == 1 and RP.LAUNCH_GATE not in launched[0]
    assert rec["session"]["game_environment"] == launched[0]


# ------------------------------------------------------------ the recorder's device check

HELD = (b"sa2-device-profile 1\nsystem processors=16 process_mask=0x0000ffff "
        b"system_mask=0x0000ffff\nxinput_caps slot=0 code=0x0000048f\n"
        b"xinput_caps slot=1 code=0x0000048f\nxaudio2_master voice=1 flags=0x00000000 "
        b"channels=2 rate=48000\n")
LOCALE = (b"locale_user lcid=0x00000409 system=0x00000409 acp=1252 oemcp=437 valid=1\n"
          b"locale_info lctype=0x00000001 error=0x00000000 value=\"0409\"\n")
RECORDING = HELD + b"end\n"
WITH_LOCALE = HELD + LOCALE + b"end\n"


def profiles(tmp_path, monkeypatch, **files: bytes) -> dict:
    """Each capture's profile written as the runner keeps it; name -> the
    session.device_profile record."""
    monkeypatch.setattr(R, "CAPTURES", tmp_path / "captures")
    out = {}
    for name, data in files.items():
        (tmp_path / "captures" / name).mkdir(parents=True)
        (tmp_path / "captures" / name / "sa2-device-profile.txt").write_bytes(data)
        out[name] = {"file": "sa2-device-profile.txt", "sha256": sha(data), "counts": {}}
    return out


def replays(recs: dict, *names: str) -> list[dict]:
    return [{"name": n, "device_profile": recs[n]} for n in names]


def test_a_re_take_by_a_newer_probe_is_held_to_every_line_the_recording_holds(tmp_path,
                                                                            monkeypatch):
    recs = profiles(tmp_path, monkeypatch, rec=RECORDING, a=WITH_LOCALE, b=WITH_LOCALE)
    assert R.device_differences(recs["rec"], replays(recs, "a", "b"), "rec") == []
    # retake_replays.judge names no recording: its profile is found by its sha256
    assert R.same_devices(recs["rec"], replays(recs, "a", "b")) is True
    assert R.same_devices(recs["rec"], replays(recs, "a"), "rec") is True


@pytest.mark.parametrize("replay, says", [
    (HELD.replace(b"processors=16", b"processors=8") + LOCALE + b"end\n",
     "a's device profile differs from the recording's in a section the recording holds, first "
     "in 'system'"),
    (HELD.replace(b"xinput_caps slot=1 code=0x0000048f\n", b"") + LOCALE + b"end\n",
     "first in 'xinput_caps'"),
    (HELD.replace(b"xinput_caps slot=0", b"xinput_caps slot=X") + LOCALE + b"end\n",
     "first in 'xinput_caps'"),
    (LOCALE + HELD + b"end",
     "first in ''"),
])
def test_a_re_take_that_changes_drops_or_reorders_a_held_line_did_not_see_the_devices(
        tmp_path, monkeypatch, replay, says):
    recs = profiles(tmp_path, monkeypatch, rec=RECORDING, a=replay)
    got = R.device_differences(recs["rec"], replays(recs, "a"), "rec")
    assert len(got) == 1 and says in got[0]
    assert R.same_devices(recs["rec"], replays(recs, "a"), "rec") is False


def test_re_takes_must_hold_the_same_new_sections(tmp_path, monkeypatch):
    other = WITH_LOCALE.replace(b"value=\"0409\"", b"value=\"0804\"")
    recs = profiles(tmp_path, monkeypatch, rec=RECORDING, a=WITH_LOCALE, b=other, c=RECORDING)
    assert R.device_differences(recs["rec"], replays(recs, "a", "b"), "rec") == [
        "the replays' device profiles differ in the sections the recording does not hold: "
        "a 2 line(s), b 2 line(s)"]
    # an old probe's re-take beside a new probe's: two configurations
    assert R.same_devices(recs["rec"], replays(recs, "a", "c"), "rec") is False


def test_a_profile_that_cannot_be_read_or_moved_on_disk_is_a_difference(tmp_path,
                                                                       monkeypatch):
    recs = profiles(tmp_path, monkeypatch, rec=RECORDING, a=WITH_LOCALE)
    (tmp_path / "captures" / "a" / "sa2-device-profile.txt").write_bytes(WITH_LOCALE + b"x")
    assert R.device_differences(recs["rec"], replays(recs, "a"), "rec") == [
        f"a recorded device profile {recs['a']['sha256'][:12]}, and the recording "
        f"{recs['rec']['sha256'][:12]}, and a's own file no longer holds it"]
    recs = profiles(tmp_path / "2", monkeypatch, rec=RECORDING, a=WITH_LOCALE)
    (tmp_path / "2" / "captures" / "rec" / "sa2-device-profile.txt").unlink()
    assert R.device_differences(recs["rec"], replays(recs, "a")) == [
        f"no capture holds the recording's device profile ({recs['rec']['sha256'][:12]}), so no "
        "replay's can be compared with it"]


def test_a_replay_record_without_a_capture_is_compared_by_sha256_alone(tmp_path, monkeypatch):
    """The preflight's two passes run one probe, and its records carry no name."""
    recs = profiles(tmp_path, monkeypatch, rec=RECORDING, a=WITH_LOCALE)
    assert R.same_devices(recs["rec"], [{"device_profile": recs["rec"]}]) is True
    assert R.device_differences(recs["rec"], [{"device_profile": recs["a"]}]) == [
        f"a replay recorded device profile {recs['a']['sha256'][:12]}, and the recording "
        f"{recs['rec']['sha256'][:12]}"]
    assert R.device_differences(None, []) == ["the recording records no device profile"]
    assert R.device_differences(recs["rec"], [{"name": "z", "device_profile": None}]) == [
        "z records no device profile"]


def judged_with_profiles(monkeypatch, name: str, replay_files: list[bytes],
                         on_disk: bytes = RECORDING) -> dict:
    """record_stage.judge over a recording whose receipt records RECORDING as
    its profile, whose own file holds `on_disk`, and two replays whose
    profiles are `replay_files`, every other file of theirs alike
    (tests/test_record_stage.py's captures); its checks."""
    TR.capture(name, profile={"file": "sa2-device-profile.txt", "sha256": sha(RECORDING)})
    (R.CAPTURES / name / "sa2-device-profile.txt").write_bytes(on_disk)
    TR.fake_run(monkeypatch, replay=[{"profile": {"file": "sa2-device-profile.txt",
                                                  "sha256": sha(data)}}
                                     for data in replay_files])
    inner, made = R.run, iter(replay_files)

    def run(replay, *a, **k):
        rec = inner(replay, *a, **k)
        (R.CAPTURES / replay / "sa2-device-profile.txt").write_bytes(next(made))
        return rec

    monkeypatch.setattr(R, "run", run)
    return TR.judged_directly(name)[0]


def test_the_recorder_s_verdict_holds_a_newer_probe_s_replays_to_the_recording_s_sections(
        vanilla, monkeypatch):
    """record_stage.judge names its recording for the check: replays by a
    probe that writes a locale section pass it, and one that changes a line
    the recording holds fails it and nothing else."""
    checks = judged_with_profiles(monkeypatch, "mh-record-p", [WITH_LOCALE, WITH_LOCALE])
    assert all(checks.values()), checks
    moved = HELD.replace(b"channels=2", b"channels=6") + LOCALE + b"end\n"
    checks = judged_with_profiles(monkeypatch, "mh-record-q", [WITH_LOCALE, moved])
    assert [k for k, v in checks.items() if not v] == ["same_device_profile_as_recording"]
    checks = judged_with_profiles(monkeypatch, "mh-record-r", [RECORDING, WITH_LOCALE])
    assert [k for k, v in checks.items() if not v] == ["same_device_profile_as_recording"]


def test_the_recorder_reads_its_own_recording_s_profile_and_no_other_capture_s(
        vanilla, monkeypatch):
    """A recording whose own profile file no longer holds what its receipt
    recorded is not compared through another capture that does."""
    (R.CAPTURES / "elsewhere").mkdir(parents=True)
    (R.CAPTURES / "elsewhere" / "sa2-device-profile.txt").write_bytes(RECORDING)
    checks = judged_with_profiles(monkeypatch, "mh-record-s", [WITH_LOCALE, WITH_LOCALE],
                                  on_disk=RECORDING + b"# edited\n")
    assert [k for k, v in checks.items() if not v] == ["same_device_profile_as_recording"]


# ------------------------------------------------ the RT fold: the device check's named kinds

SWAPPED = HELD.replace(b"xinput_caps slot=0 code=0x0000048f\nxinput_caps slot=1 code=0x0000048f",
                       b"xinput_caps slot=1 code=0x0000048f\nxinput_caps slot=0 code=0x0000048f")
ERROR = b'error step="GetEnvironmentStringsW answered NULL" code=0x00000008\n'
UNNAMED = b"hid_device index=0 vid=0x0000046d pid=0x0000c52b\n"
# The record kinds of the probe the recording ran (tools/s4/device_profile.py
# RECORDS at HEAD development_snapshot, the probe before the locale's).
RECORDING_PROBE_KINDS = {
    "d3d9_adapters", "ddraw_create", "ddraw_device", "ddraw_enum", "ddraw_query",
    "ddraw_vidmem", "dinput8_create", "dinput8_enum", "dinput8_gamectrl",
    "dinput8_syskeyboard", "dinput8_sysmouse", "end", "error", "system", "wmi_blanket",
    "wmi_coinit", "wmi_connect", "wmi_entities", "wmi_enum", "wmi_locator", "wmi_next",
    "wmi_pnp", "xaudio2_master", "xinput_caps"}


@pytest.mark.parametrize("replay", [
    SWAPPED + LOCALE + b"end\n",                   # two held lines swapped
    HELD + LOCALE + ERROR + b"end\n",              # a step the probe could not measure
    HELD + LOCALE + UNNAMED + b"end\n",            # a kind no probe added on purpose
])
def test_only_the_named_new_sections_may_be_missing_from_the_recording(tmp_path, monkeypatch,
                                                                       replay):
    recs = profiles(tmp_path, monkeypatch, rec=RECORDING, a=replay, b=replay)
    assert R.same_devices(recs["rec"], replays(recs, "a", "b"), "rec") is False


def test_a_recording_that_holds_a_new_section_holds_its_re_takes_to_it(tmp_path, monkeypatch):
    other = WITH_LOCALE.replace(b'value="0409"', b'value="0804"')
    recs = profiles(tmp_path, monkeypatch, rec=WITH_LOCALE, a=other, b=other)
    assert R.same_devices(recs["rec"], replays(recs, "a", "b"), "rec") is False
    # and only the named kinds it lacks are held to the other re-takes instead
    newer = HELD + LOCALE + b"environment strings=80 crt=78\n" + b"end\n"
    recs = profiles(tmp_path / "2", monkeypatch, rec=WITH_LOCALE, a=newer, b=newer)
    assert R.device_differences(recs["rec"], replays(recs, "a", "b"), "rec") == []


def test_the_named_new_sections_are_every_kind_a_newer_probe_added_and_none_older():
    """ADDED_SECTIONS against the reader this tree has: every kind it reads
    that the recording's probe did not write is named (else every re-take by
    the newer probe fails the check, and the batch stops at its first
    series), and no kind the recording's probe wrote is."""
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import device_profile as DP
    kinds = {k.encode() for k in DP.RECORDS}
    old = {k.encode() for k in RECORDING_PROBE_KINDS}
    assert kinds - old <= R.ADDED_SECTIONS
    assert not R.ADDED_SECTIONS & old
    assert R.ADDED_SECTIONS == {b"locale_user", b"locale_info", b"environment", b"env",
                                b"std_handle"}


@pytest.mark.parametrize("move", ["user_settings", "steam_client_update", "client_appears"])
def test_a_user_settings_file_or_a_steam_client_update_moves_the_record(tmp_path, move):
    launcher = fake_proton(tmp_path)
    client = tmp_path / "Steam"
    (client / "linux32").mkdir(parents=True)
    lib = client / "linux32" / "steamclient.so"
    lib.write_bytes(b"client")
    before = RP.proton_build(launcher, client)
    assert before["user_settings_sha256"] is None and "problems" not in before
    assert before["steam_client"]["legacycompat/Steam.dll"] is None
    assert before["steam_client"]["linux32/steamclient.so"] == f"6 {lib.stat().st_mtime_ns}"
    assert RP.proton_build(launcher, client) == before
    if move == "user_settings":
        (launcher.parent / "user_settings.py").write_text('user_settings = {"WINEDEBUG": "+all"}\n')
    elif move == "steam_client_update":
        os.utime(lib, ns=(lib.stat().st_atime_ns, lib.stat().st_mtime_ns + 1))
    else:
        (client / "legacycompat").mkdir()
        (client / "legacycompat" / "Steam.dll").write_bytes(b"MZ")
    assert RP.proton_build(launcher, client) != before


# ------------------------------------------------ the RT fold: the host's own state


def test_a_game_is_wine_s_process_for_it_or_proton_s_launch_and_no_lifted_run(tmp_path,
                                                                             monkeypatch):
    proc = tmp_path / "proc"
    rows = {
        101: ["Z:\\home\\user\\x\\oracle\\game\\sonic2app-unpacked.exe"],       # Wine's game
        102: ["/usr/bin/python3", "/w/proton", "runinprefix", "./sonic2app-unpacked.exe"],
        103: ["timeout", "--signal=TERM", "200s", "env", "/w/proton", "runinprefix",
              "./sonic2app-unpacked.exe"],
        104: ["/x/private/generated/s4/link/mains/replay/sa2_main",           # a lifted run
              "/x/private/exe/sonic2app-unpacked.exe", "--json", "out.json"],
        105: ["grep", "sonic2app-unpacked.exe"],                              # a search for it
        106: ["C:\\windows\\system32\\services.exe"],                         # Wine's own
        os.getpid(): ["Z:\\sonic2app-unpacked.exe"],                          # this process
    }
    for pid, argv in rows.items():
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (proc / "107").mkdir()                             # a process whose cmdline cannot be read
    (proc / "108").mkdir()
    (proc / "108" / "cmdline").write_bytes(b"")        # a zombie
    monkeypatch.setattr(RP, "PROC", proc)
    assert RP.game_pids() == [101, 102, 103]


def test_the_host_state_is_logind_s_lock_and_idle_word_and_the_suspend_clock(monkeypatch):
    said = {("loginctl", "show-user"): "2\n",
            ("loginctl", "show-session"): "IdleHint=yes\nLockedHint=yes\n"}
    asked = []

    def out(cmd, timeout=5.0):
        asked.append(cmd)
        return said[tuple(cmd[:2])]

    monkeypatch.setattr(RP, "_command_out", out)
    monkeypatch.setattr(RP, "suspend_clock", lambda: 17561.345)
    assert RP.host_state() == {"boottime_minus_monotonic_s": 17561.345, "session": "2",
                               "locked": True, "idle": True}
    assert asked == [["loginctl", "show-user", str(os.getuid()), "-p", "Display", "--value"],
                     ["loginctl", "show-session", "2", "-p", "LockedHint", "-p", "IdleHint"]]
    said[("loginctl", "show-session")] = "IdleHint=no\nLockedHint=no\n"
    assert (RP.host_state()["locked"], RP.host_state()["idle"]) == (False, False)

    def fails(cmd, timeout=5.0):
        raise RP.subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(RP, "_command_out", fails)
    assert RP.host_state() == {"boottime_minus_monotonic_s": 17561.345, "session": None,
                               "locked": None, "idle": None}
    said[("loginctl", "show-user")] = "\n"             # no graphical session
    monkeypatch.setattr(RP, "_command_out", out)
    assert RP.host_state()["session"] is None


def test_the_suspend_clock_is_boottime_less_monotonic():
    got = RP.suspend_clock()
    assert isinstance(got, float) and got >= 0.0
    assert abs(RP.suspend_clock() - got) < RP.SUSPEND_SLACK_S


def test_the_inhibitors_are_logind_s_list_and_a_blocking_sleep_one_is_what_holds(monkeypatch):
    listing = {"type": "a(ssssuu)", "data": [[
        ["sleep", "GNOME Shell", "GNOME needs to lock the screen", "delay", 1000, 2034],
        ["sleep", "sa2 retake_batch", "a batch", "block", 1000, 777],
        ["handle-power-key:sleep", "sa2 retake_batch", "a batch", "block", 1000, 778]]]}
    asked = []

    def out(cmd, timeout=5.0):
        asked.append(cmd)
        return json.dumps(listing)

    monkeypatch.setattr(RP, "_command_out", out)
    got = RP.sleep_inhibitors()
    assert got[1] == {"what": "sleep", "who": "sa2 retake_batch", "why": "a batch",
                      "mode": "block", "uid": 1000, "pid": 777}
    assert asked[0][:3] == ["busctl", "--system", "--json=short"]
    assert asked[0][-1] == "ListInhibitors"
    assert RP.holds_sleep(got, 777, "sa2 retake_batch") is True
    assert RP.holds_sleep(got, 778, "sa2 retake_batch") is True
    assert RP.holds_sleep(got, 2034, "GNOME Shell") is False      # delay mode holds nothing
    assert RP.holds_sleep(got, 777, "someone else") is False
    assert RP.holds_sleep(None, 777, "sa2 retake_batch") is False
    monkeypatch.setattr(RP, "_command_out", lambda cmd, timeout=5.0: "not json")
    assert RP.sleep_inhibitors() is None
