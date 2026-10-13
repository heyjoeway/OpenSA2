"""M6's "replay complete" judge, tools/s4/stage_boot.py, in both directions.

The judge is what decides whether a registered stage replay reached its goal:
the lifted goal-latch column (harness/stage_goal_probe.c) against the native
goal tick every registered capture's sa2-input-end.csv names. No stage replay is
registered yet, so nothing but these fixtures and check_stage_results.py's
synthetic replay exercises it until the Metal Harbor recording is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import stage_boot as SB  # noqa: E402

GOAL, LEVEL = 40, 10


def latch(rise: int | None = GOAL, rows: int = 60) -> list[int]:
    return [1 if rise is not None and t >= rise else 0 for t in range(rows)]


def trace(rows: int = 60, level_at: dict[int, int] | None = None) -> list[dict]:
    at = level_at or {}
    return [{"tick": str(t), "current_level": f"0x{at.get(t, LEVEL):04x}"} for t in range(1, rows + 1)]


def end(goal: int = GOAL, misses: int = 0) -> dict:
    return {"goal_tick": goal, "end_tick": goal + 300, "misses": misses}


TWO = [end(), end()]      # the recording's two native replays (stage-route item 5)


def test_the_latch_rising_on_the_native_goal_tick_completes() -> None:
    v = SB.judge_goal(latch(), trace(), LEVEL, TWO)
    assert (v["native_goal_tick"], v["lifted_goal_tick"], v["latch_rises"], v["level_at_goal"],
            v["complete"]) == (GOAL, GOAL, True, LEVEL, True)


def test_a_run_that_ended_inside_a_gameloop_call_has_one_latch_row_more() -> None:
    """The probe writes a row at each GameLoop call's entry, the trace at each return."""
    assert SB.judge_goal(latch(rows=61), trace(rows=60), LEVEL, TWO)["complete"] is True


@pytest.mark.parametrize("latch_rows,trace_rows,level,ends,why", [
    (latch(None), trace(), LEVEL, TWO, "the latch never rises"),
    (latch(GOAL + 1), trace(), LEVEL, TWO, "the latch rises a tick late"),
    (latch(GOAL - 1), trace(), LEVEL, TWO, "the latch rises a tick early"),
    (latch(), trace(), LEVEL, [end(), end(GOAL + 1)], "the native captures disagree"),
    (latch(), trace(), LEVEL, [end(), end(misses=2)], "a native replay missed input rows"),
    (latch(), trace(), LEVEL, [end(0), end(0)], "the native runs never reached the goal"),
    (latch(), trace(), LEVEL, [end()], "one native capture: nothing shows it repeats"),
    (latch(), trace(), LEVEL, [], "no native end record"),
    (latch(), trace(level_at={GOAL: 11}), LEVEL, TWO, "the goal tick is in another level"),
    (latch(), trace(), 11, TWO, "the replay is judged for another stage"),
    (latch(rows=55), trace(), LEVEL, TWO, "the latch column misses GameLoop calls"),
    (latch(rows=59), trace(), LEVEL, TWO, "the latch column is one GameLoop call short"),
    (latch(rows=62), trace(), LEVEL, TWO, "the latch column has calls the trace never saw"),
    (latch(0), trace(), LEVEL, [end(1), end(1)], "a latch held from before the first tick"),
])
def test_what_does_not_complete(latch_rows, trace_rows, level, ends, why) -> None:
    assert SB.judge_goal(latch_rows, trace_rows, level, ends)["complete"] is False, why


def test_row_0_is_not_a_tick() -> None:
    """Row 0 is the latch before the first tick, which no native OnFrame reads:
    set there and cleared when the level starts, it is not the goal."""
    v = SB.judge_goal([1] + [0] * (GOAL - 1) + [1] * 20, trace(), LEVEL, TWO)
    assert (v["lifted_goal_tick"], v["latch_rises"], v["complete"]) == (GOAL, True, True)


def test_a_latch_held_from_before_the_first_tick_is_no_rise() -> None:
    """The native probe would call it tick 1; the decision's goal is the latch
    rising, and a latch never seen at zero has not risen."""
    v = SB.judge_goal([1] * 60, trace(), LEVEL, [end(1), end(1)])
    assert (v["native_goal_tick"], v["lifted_goal_tick"], v["latch_rises"], v["complete"]) == \
        (1, 1, False, False)
    v = SB.judge_goal([0] + [1] * 59, trace(), LEVEL, [end(1), end(1)])
    assert (v["lifted_goal_tick"], v["latch_rises"], v["complete"]) == (1, True, True)


@pytest.mark.parametrize("text,why", [
    ("tick,latch\n0,0x00\n", "another header"),
    ("tick,goal_latch\n1,0x00\n", "ticks start at 0"),
    ("tick,goal_latch\n0,0x00\n2,0x00\n", "a tick skipped"),
    ("tick,goal_latch\n0,0x00\n0,0x00\n", "a tick repeated"),
    ("tick,goal_latch\n0,0xffffffff\n", "the probe's unreadable marker"),
    ("tick,goal_latch\n0,0x0\n", "not the probe's two digits"),
    ("tick,goal_latch\n0,0X01\n", "not the probe's spelling"),
    ("tick,goal_latch\n0,1\n", "no 0x"),
    ("tick,goal_latch\n0,0x00,0x00\n", "a column too many"),
])
def test_a_column_the_probe_cannot_have_written_is_refused(tmp_path: Path, text: str, why: str) -> None:
    p = tmp_path / "goal-latch.csv"
    p.write_text(text)
    with pytest.raises(SystemExit, match="STAGE_BOOT_REFUSED"):
        SB.read_goal(p)


def test_the_probe_s_column_reads_back(tmp_path: Path) -> None:
    p = tmp_path / "goal-latch.csv"
    p.write_text("tick,goal_latch\n0,0x00\n1,0x00\n2,0x01\n")
    assert SB.read_goal(p) == [0, 0, 1]


@pytest.mark.parametrize("text", [
    "goal_tick,end_tick\n40,340\n",
    "goal_tick,end_tick,misses\n40,340,0\n41,341,0\n",
    "goal_tick,end_tick,misses\n-1,340,0\n",
    "goal_tick,end_tick,misses\n40,340,0x0\n",
    "goal_tick,end_tick,misses\n",
])
def test_an_end_record_the_native_probe_cannot_have_written_is_refused(tmp_path: Path, text: str) -> None:
    p = tmp_path / "sa2-input-end.csv"
    p.write_text(text)
    with pytest.raises(SystemExit, match="STAGE_BOOT_REFUSED"):
        SB.read_end(p)


def test_the_native_end_record_reads_back(tmp_path: Path) -> None:
    p = tmp_path / "sa2-input-end.csv"
    p.write_bytes(b"goal_tick,end_tick,misses\n2981,3281,0\n")    # native_input_probe.c's "wb"
    assert SB.read_end(p) == {"goal_tick": 2981, "end_tick": 3281, "misses": 0}


def test_replay_complete_is_the_completing_stage_ids_and_nothing_else() -> None:
    replays = [{"stage_id": "10", "complete": True}, {"stage_id": "13", "complete": False},
               {"stage_id": "10", "complete": True}]
    assert SB.completed(replays) == ["10"]
    assert SB.completed([]) == []


A_END = {"file": "cap/a/sa2-input-end.csv", "sha256": "0" * 64}
B_END = {"file": "cap/b/sa2-input-end.csv", "sha256": "1" * 64}
A_CAP, B_CAP = "cap/a/sa2-tick-capture.csv", "cap/b/sa2-tick-capture.csv"


def evidence(**changes) -> dict:
    ev = {"replay": "r", "stage": "metalharbor", "level": LEVEL, "stage_id": "10",
          "captures": [A_CAP, B_CAP], "captures_sha256": ["4" * 64, "5" * 64],
          "ends": [A_END, B_END],
          "lifted_capture": "replay/r/lifted.csv", "lifted_capture_sha256": "6" * 64,
          "latch": "replay/r/goal-latch.csv", "latch_sha256": "2" * 64,
          "trace": "replay/r/stage-trace.csv", "trace_sha256": "3" * 64}
    ev.update(changes)
    return ev


def test_evidence_from_the_replay_s_own_runs_is_accepted() -> None:
    assert SB.evidence_problems(evidence()) == []
    assert SB.evidence_problems(evidence(captures=[A_CAP, B_CAP, "cap/c/sa2-tick-capture.csv"],
                                         captures_sha256=["4" * 64] * 3,
                                         ends=[A_END, B_END, {"file": "cap/c/sa2-input-end.csv",
                                                              "sha256": "7" * 64}])) == []


@pytest.mark.parametrize("changes,names", [
    ({"ends": [{"file": "cap/c/sa2-input-end.csv", "sha256": "0" * 64}, B_END]},
     "native end record 0"),
    ({"latch": "replay/other/goal-latch.csv"}, "goal-latch column"),
    ({"trace": "replay/other/stage-trace.csv"}, "stage trace"),
    ({"lifted_capture": None}, "goal-latch column"),
    ({"latch": None}, "goal-latch column"),
    # One native run counted as two: stage-route item 5's two replays are two runs.
    ({"captures": [A_CAP, A_CAP], "ends": [A_END, A_END]},
     "native captures 0 and 1 are one run listed 2 times"),
    ({"captures": [A_CAP, B_CAP, B_CAP], "captures_sha256": ["4" * 64, "5" * 64, "5" * 64],
      "ends": [A_END, B_END, B_END]}, "native captures 1 and 2 are one run listed 2 times"),
    ({"captures": [A_CAP, "cap/a/sa2-tick-capture-copy.csv"],
      "ends": [A_END, A_END]}, "native captures 0 and 1 are one run listed 2 times"),
    ({"captures": [A_CAP, "cap/b/../a/sa2-tick-capture.csv"],
      "ends": [A_END, {"file": "cap/b/../a/sa2-input-end.csv", "sha256": "0" * 64}]},
     "native captures 0 and 1 are one run listed 2 times"),
])
def test_evidence_from_another_run_is_named(changes: dict, names: str) -> None:
    problems = SB.evidence_problems(evidence(**changes))
    assert problems and names in problems[0], problems


def test_a_verdict_is_refused_on_any_file_that_is_not_its_pinned_one(tmp_path: Path) -> None:
    """Each native tick capture, each end record, the lifted tick capture, the
    latch column and the stage trace: the verdict stands only on the files the
    registry and the tick-replay receipt pin, the tick captures included,
    though nothing here reads their columns."""
    body = {A_CAP: b"tick,synthetic\n1,1\n", B_CAP: b"tick,synthetic\n1,1\n",
            A_END["file"]: b"goal_tick,end_tick,misses\n40,340,0\n",
            B_END["file"]: b"goal_tick,end_tick,misses\n40,340,0\n",
            "replay/r/lifted.csv": b"tick,synthetic\n1,2\n",
            "replay/r/goal-latch.csv": ("tick,goal_latch\n" + "".join(
                f"{t},{'0x01' if t >= GOAL else '0x00'}\n" for t in range(60))).encode(),
            "replay/r/stage-trace.csv": ("tick,current_level,game_mode,game_state,ingame_frames,"
                                         "player,x,y,z\n" + "".join(
                f"{t},0x000a,0x00000005,0x0010,0x{t:08x},0x10000000,0x0,0x0,0x0\n"
                for t in range(1, 61))).encode()}
    for rel, data in body.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    pin = {rel: SB.TR.sha256(tmp_path / rel) for rel in body}
    ev = evidence(captures_sha256=[pin[A_CAP], pin[B_CAP]],
                  ends=[{"file": A_END["file"], "sha256": pin[A_END["file"]]},
                        {"file": B_END["file"], "sha256": pin[B_END["file"]]}],
                  lifted_capture_sha256=pin["replay/r/lifted.csv"],
                  latch_sha256=pin["replay/r/goal-latch.csv"],
                  trace_sha256=pin["replay/r/stage-trace.csv"])
    assert SB.goal_verdict(ev, tmp_path)["complete"] is True
    whats = [what for what, _, _ in SB.pinned_files(ev)]
    assert whats == ["native tick capture 0", "native tick capture 1", "native end record 0",
                     "native end record 1", "lifted tick capture", "goal-latch column",
                     "stage trace"]
    for what, rel, _ in SB.pinned_files(ev):
        saved = (tmp_path / rel).read_bytes()
        (tmp_path / rel).write_bytes(saved + b"\n")
        with pytest.raises(SystemExit, match=f"its {what} {rel} is missing or does not hash"):
            SB.goal_verdict(ev, tmp_path)
        (tmp_path / rel).unlink()
        with pytest.raises(SystemExit, match=f"its {what} {rel} is missing or does not hash"):
            SB.goal_verdict(ev, tmp_path)
        (tmp_path / rel).write_bytes(saved)
    assert SB.goal_verdict(ev, tmp_path)["complete"] is True
    for changes in ({"captures_sha256": [pin[A_CAP], None]}, {"lifted_capture_sha256": None},
                    {"lifted_capture": None, "latch": None, "trace": None}):
        with pytest.raises(SystemExit, match="STAGE_BOOT_REFUSED"):
            SB.goal_verdict({**ev, **changes}, tmp_path)


def test_a_pinned_trace_whose_tick_is_no_number_is_refused_not_raised(tmp_path: Path) -> None:
    """The M6 review, N5: a refusal the gate reports, not a ValueError."""
    p = tmp_path / "stage-trace.csv"
    p.write_text(",".join(SB.TRACE_HEADER) + "\n" + "x," + ",".join(["0x0"] * (len(SB.TRACE_HEADER) - 1))
                 + "\n")
    with pytest.raises(SystemExit, match="STAGE_BOOT_REFUSED .*tick x on row 1"):
        SB.read_trace(p)


def test_a_registered_stage_replay_tick_replay_recorded_no_run_of_is_named_first(
        tmp_path: Path) -> None:
    """The M6 review, W3: when tick_replay.py refused the entry (or was not run)
    the writer says what to run and what to do on a refusal, before it asks for
    any file the missing run would have written."""
    import json
    reg = tmp_path / SB.REGISTRY_REL
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"replays": [{"name": "mh", "stage": {"name": "metalharbor",
                                                                     "level": 10},
                                             "native": {"repeats": [None]}}]}))
    with pytest.raises(SystemExit, match="mh: .*records no run of it; run "
                                         "tools/s4/tick_replay.py --receipt"):
        SB.goal_verdicts(tmp_path)
    rec = tmp_path / SB.TICK_REPLAY_REL
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"replays": [{"name": "other"}]}))
    with pytest.raises(SystemExit, match="records no run of it"):
        SB.goal_verdicts(tmp_path)
    # A run recorded, and a null repeat in the registry: a refusal, not an
    # AttributeError (the M6 review, N5).
    rec.write_text(json.dumps({"replays": [{"name": "mh", "lifted": {}}]}))
    with pytest.raises(SystemExit, match="has no usable stage block or native side"):
        SB.goal_verdicts(tmp_path)
