"""tools/s4/stage_run.py command(): the goal probe in every stage run, and a run that observes nothing.

A stage run links harness/stage_goal_probe.c and writes the goal-latch column
beside its tick capture, so the walk sees the latch rise. tools/s4/tick_replay.py's
stage entry needs two more things from the same one definition: its own build
name and output directory, never the walk's, and a run with every observer off,
which is its baseline for "the observers changed nothing".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import device_profile as DP  # noqa: E402
import stage_run as SR  # noqa: E402


@pytest.fixture
def sr(tmp_path: Path, monkeypatch):
    # command() refuses a device variable left in the shell: DP.OWNED, and with
    # push D every recording-device switch too (RR.DEVICE_VARS).
    for var in set(DP.OWNED) | set(getattr(SR.RR, "DEVICE_VARS", ())):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(SR, "ROOT", tmp_path)
    monkeypatch.setattr(SR, "OUT", tmp_path / "replay")
    monkeypatch.setattr(SR, "RUNDIR", tmp_path / "run")
    return SR


def env_of(cmd: list[str]) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in cmd[cmd.index("--run") + 1].split("|")[1:])


def flags(cmd: list[str], flag: str) -> list[str]:
    return [cmd[i + 1] for i, w in enumerate(cmd) if w == flag]


def test_every_stage_run_links_the_goal_probe_and_writes_its_column(sr, tmp_path) -> None:
    cmd, run_json, ticks = sr.command("metalharbor")
    assert "harness/stage_goal_probe.c" in flags(cmd, "--hook")
    assert "sub_7a8948" in flags(cmd, "--wrap")
    env = env_of(cmd)
    assert env["SA2_GOAL_LATCH"] == "replay/stage-metalharbor/goal-latch.csv"
    assert env["SA2_REPLAY_TICKS"] == "replay/stage-metalharbor/lifted.csv"
    assert run_json == tmp_path / "run" / "stage-metalharbor.a.json"


def test_the_stage_boot_configuration_keeps_its_import_budget(sr) -> None:
    """8 000 000 at 1300 polls, as before the budget scaled with the polls."""
    assert env_of(sr.command("metalharbor")[0])["SA2_IMPORT_BUDGET"] == "8000000"
    assert env_of(sr.command("metalharbor", 28_800)[0])["SA2_IMPORT_BUDGET"] == "144000000"


def test_a_named_run_writes_only_under_its_own_directory(sr, tmp_path) -> None:
    out = tmp_path / "replay" / "metalharbor-m1"
    script = tmp_path / "sa2-input.csv"
    script.write_text("tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old\n")
    cmd, run_json, ticks = sr.command("metalharbor", input_script=script,
                                      name="replay-metalharbor-m1", out_dir=out)
    env = env_of(cmd)
    assert flags(cmd, "--name") == ["replay-metalharbor-m1"]
    assert run_json == tmp_path / "run" / "replay-metalharbor-m1.a.json"
    assert ticks == out / "lifted.csv"
    for var in ("SA2_REPLAY_TICKS", "SA2_GOAL_LATCH", "SA2_INPUT_APPLIED"):
        assert env[var].startswith("replay/metalharbor-m1/"), (var, env[var])
    assert not (tmp_path / "replay" / "stage-metalharbor-input").exists()


def test_a_run_that_observes_nothing_sets_no_observer(sr, tmp_path) -> None:
    script = tmp_path / "sa2-input.csv"
    script.write_text("tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old\n")
    on = env_of(sr.command("metalharbor", input_script=script)[0])
    off = env_of(sr.command("metalharbor", input_script=script, observe=False)[0])
    assert {v for v in SR.OBSERVERS if v in on} == set(SR.OBSERVERS) - {"SA2_STAGE_TRACE"}
    assert not {v for v in SR.OBSERVERS if v in off}
    # Everything else is the same run: the observers are all that differ.
    assert {k: v for k, v in on.items() if k not in SR.OBSERVERS} == off
    with pytest.raises(SystemExit, match="STAGE_RUN_REFUSED SA2_STAGE_TRACE in extra_env of a run "
                                         "that observes nothing"):
        sr.command("metalharbor", extra_env={"SA2_STAGE_TRACE": "x.csv"}, observe=False)


def test_the_walk_s_goal_summary(sr, tmp_path) -> None:
    latch = tmp_path / "goal-latch.csv"
    assert sr.goal(latch) == {"latch": None, "first_nonzero_tick": None}
    latch.write_text("tick,goal_latch\n0,0x01\n1,0x00\n2,0x00\n3,0x01\n")
    assert sr.goal(latch) == {"latch": "goal-latch.csv", "first_nonzero_tick": 3}
    latch.write_text("tick,goal_latch\n0,0x00\n2,0x01\n")
    assert "STAGE_BOOT_REFUSED" in sr.goal(latch)["refused"]


def test_the_stage_boot_configuration_is_what_it_was_apart_from_the_goal_probe(sr) -> None:
    """At the stage-boot configuration (1300 polls, no input) the command is the
    one it was before the goal probe joined it, apart from the probe's hook,
    its wrap and SA2_GOAL_LATCH: every setting, hook and wrap listed."""
    cmd, run_json, ticks = sr.command("metalharbor")
    env = env_of(cmd)
    assert env == {**SR.RR.ENV, "SA2_IMPORT_BUDGET": "8000000", "SA2_PUMP_POLLS": "1300",
                   "SA2_TEST_SPAWN_LEVEL": "10", "SA2_TEST_SPAWN_CHARACTER": "0",
                   "SA2_GUEST_CMDLINE": "--level metalharbor --character sonic",
                   "SA2_REPLAY_TICKS": "replay/stage-metalharbor/lifted.csv",
                   "SA2_GOAL_LATCH": "replay/stage-metalharbor/goal-latch.csv",
                   "SA2_VIRTUAL_BUDGET_MS": "30000"}
    assert flags(cmd, "--hook") == ["harness/replay/wrap_tick_probe.c",
                                    "harness/replay/wrap_test_spawn.c",
                                    "harness/replay/wrap_input_replay.c",
                                    "harness/stage_goal_probe.c"]
    assert flags(cmd, "--wrap") == ["sub_433ee0", "sub_77e780", "sub_7a8948"]
    assert flags(cmd, "--name") == ["stage-metalharbor"]
