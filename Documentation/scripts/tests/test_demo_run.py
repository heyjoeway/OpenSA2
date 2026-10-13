"""tools/s4/demo_run.py: how a lifted stage trace is compared with the native
attract demo's. The run itself is exercised by every use; these pin the
comparison, which decides what "matched" means -- and how many rows it
compared, since no divergence over no compared row is no evidence."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import demo_run as D  # noqa: E402


def row(tick, level="0x0000", mode="0x00000002", state="0x0000", frames="0x00000000",
        player="0x00000000", x="0x00000000", y="0x00000000", z="0x00000000"):
    return {"tick": str(tick), "current_level": level, "game_mode": mode, "game_state": state,
            "ingame_frames": frames, "player": player, "x": x, "y": y, "z": z}


def demo(n=6):
    rows = [row(t) for t in range(1, n + 1)]
    rows[2] = row(3, level="0x000d", state="0x0010", player="0x053ad1fc", x="0x42b40000")
    rows[3] = row(4, level="0x000d", state="0x0010", player="0x053ad1fc", x="0x42b50000")
    rows[4] = row(5, level="0x0030", state="0x0010", player="0x0ecd73dc")
    return rows


def test_identical_traces_have_no_divergence_and_the_same_levels():
    got = D.compare_traces(demo(), demo())
    assert got["first_divergence"] is None
    assert (got["native_rows"], got["lifted_rows"], got["compared_rows"]) == (6, 6, 6)
    assert got["levels_played_native"] == got["levels_played_lifted"] == ["0x000d", "0x0030"]


def test_a_player_address_is_compared_only_as_null_or_not():
    lifted = demo()
    lifted[2] = dict(lifted[2], player="0x0badf00d")        # another heap, same object
    assert D.compare_traces(demo(), lifted)["first_divergence"] is None
    lifted[3] = dict(lifted[3], player="0x00000000")        # the object is gone
    got = D.compare_traces(demo(), lifted)
    assert got["first_divergence"]["tick"] == 4
    assert got["first_divergence"]["columns"] == ["player (null or not)"]
    assert got["compared_rows"] == 4


def test_the_first_differing_row_is_reported_with_its_columns():
    lifted = demo()
    lifted[3] = dict(lifted[3], x="0x42b60000", game_state="0x000e")
    got = D.compare_traces(demo(), lifted)
    first = got["first_divergence"]
    assert first["tick"] == 4
    assert first["columns"] == ["game_state", "x"]
    assert first["native"]["x"] == "0x42b50000" and first["lifted"]["x"] == "0x42b60000"
    assert got["compared_rows"] == 4                         # the differing row included


def test_a_shorter_lifted_trace_is_compared_as_far_as_it_goes():
    got = D.compare_traces(demo(), demo()[:3])
    assert got["first_divergence"] is None
    assert got["lifted_rows"] == 3 and got["levels_played_lifted"] == ["0x000d"]
    assert got["compared_rows"] == 3


def test_a_lifted_trace_a_tick_out_of_step_compares_no_row():
    shifted = [dict(r, tick=str(int(r["tick"]) + 1)) for r in demo()]
    got = D.compare_traces(demo(), shifted)
    assert (got["first_divergence"], got["compared_rows"], got["lifted_rows"]) == (None, 0, 6)


def test_a_gap_stops_the_comparison_before_the_rows_after_it():
    lifted = [demo()[0]] + [dict(r, x="0x7f7fffff") for r in demo()[2:]]   # no tick 2
    got = D.compare_traces(demo(), lifted)
    assert (got["first_divergence"], got["compared_rows"]) == (None, 1)


def test_traces_sharing_no_tick_compare_no_row():
    later = [dict(r, tick=str(int(r["tick"]) + 100)) for r in demo()]
    got = D.compare_traces(demo(), later)
    assert (got["first_divergence"], got["compared_rows"]) == (None, 0)


def test_a_tick_either_trace_holds_twice_is_named():
    rows = demo()
    lifted = rows[:2] + [dict(rows[2], x="0x7f7fffff")] + rows[2:]   # tick 3 twice, the first bad
    got = D.compare_traces(demo(), lifted)
    assert (got["repeated_tick_native"], got["repeated_tick_lifted"]) == (None, 3)
    assert got["first_divergence"] is None                   # the last tick-3 row is the one read
    got = D.compare_traces(rows[:4] + rows[3:], demo())
    assert (got["repeated_tick_native"], got["repeated_tick_lifted"]) == (4, None)
    assert D.compare_traces(demo(), demo())["repeated_tick_lifted"] is None
