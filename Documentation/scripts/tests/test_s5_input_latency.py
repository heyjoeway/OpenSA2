"""Literal clock/sequence joins, without a game, renderer or device."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

PATH = Path(__file__).parents[1] / "check_s5_exit.py"
SPEC = importlib.util.spec_from_file_location("private_input_latency_checker", PATH)
X = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = X
SPEC.loader.exec_module(X)


def evidence():
    return {
        "keys": [{"change": 1, "scancode": 40, "dik": 28, "down": 1, "synthetic": 0,
                  "age_ns": 100_000_000, "handled_s": "1.2", "seq": 0,
                  "sent_s": "1.3", "status": "sent"}],
        "polls": [{"poll": 5, "first_seq": 0, "last_seq": 0, "records": 1,
                   "tick": 2, "presents": 1, "mono_ns": 1_400_000_000}],
        "presents": [{"frame": 1, "virtual_us": 0, "call_s": "0.8", "returned_s": "0.9"},
                     {"frame": 2, "virtual_us": 16667, "call_s": "1.6", "returned_s": "1.7"},
                     {"frame": 3, "virtual_us": 33334, "call_s": "2.6", "returned_s": "2.7"}],
        "arrivals": [{"frame": 1, "arrived_s": "0.7"}, {"frame": 2, "arrived_s": "1.5"},
                     {"frame": 3, "arrived_s": "2.5"}],
        "ticks": [{"tick": 1, "frames": 1, "keys": 0}, {"tick": 2, "frames": 2, "keys": 4},
                  {"tick": 3, "frames": 3, "keys": 4}, {"tick": 4, "frames": 4, "keys": 0}],
        "entry": 1, "goal": 3,
    }


def one_distribution(value):
    return {"min": value, "max": value, "mean": value,
            "p50": value, "p90": value, "p99": value}


def test_first_present_after_the_actual_poll_and_literal_clock_components():
    actual = X.input_latency(**evidence())
    row = {"press": 1, "dik": 28, "seq": 0, "poll": 5, "tick": 2, "frame": 2,
           "status": "presented", "total_us": 600000, "from_dequeue_us": 500000,
           "queue_us": 100000, "send_us": 100000, "poll_wait_us": 100000,
           "game_us": 100000, "player_us": 100000, "present_call_us": 100000}
    summary = {"presses": 1, "by_status": {"presented": 1, "lost": 0, "unsent": 0,
                                           "unpresented": 0},
               "total_us": one_distribution(600000), "from_dequeue_us": one_distribution(500000),
               "components_us": {name: one_distribution(100000) for name in
                                 ("queue", "send", "poll_wait", "game", "player", "present_call")}}
    assert actual == {"method": "v1", "clock": "CLOCK_MONOTONIC",
                      "press_at": "SDL key event timestamp", "presented_at": "host Present returned",
                      "presses": [row], "summary": summary | {"stage": copy.deepcopy(summary)}}


def test_poll_after_a_ticks_present_uses_the_next_frame():
    values = evidence()
    values["polls"][0].update(presents=2, mono_ns=1_800_000_000)
    result = X.input_latency(**values)
    assert result["presses"][0] == {
        "press": 1, "dik": 28, "seq": 0, "poll": 5, "tick": 2, "frame": 3,
        "status": "presented", "total_us": 1600000, "from_dequeue_us": 1500000,
        "queue_us": 100000, "send_us": 100000, "poll_wait_us": 500000,
        "game_us": 700000, "player_us": 100000, "present_call_us": 100000}


def test_lost_unsent_unpresented_and_stage_scope_are_kept_separate():
    """A press never sent has no poll: it is placed by the frame on the screen as it was
    dequeued (the last Present returned by then) and the tick that made that frame. Here
    frame 2 (returned at 1.7) is on the screen at 1.8 and 1.9, and tick 2 made it."""
    values = evidence()
    template = values["keys"][0]
    values["keys"] += [template | {"change": 2, "handled_s": "1.8", "age_ns": 0,
                                  "status": "lost", "seq": "-", "sent_s": "-"},
                       template | {"change": 3, "handled_s": "1.9", "age_ns": 0,
                                  "status": "unsent", "seq": "-", "sent_s": "-"},
                       template | {"change": 4, "handled_s": "3.2", "age_ns": 0,
                                  "seq": 1, "sent_s": "3.3"}]
    values["polls"].append({"poll": 8, "first_seq": 1, "last_seq": 1, "records": 1,
                           "tick": 3, "presents": 3, "mono_ns": 3_400_000_000})
    result = X.input_latency(**values)
    assert result["presses"][1:] == [
        {"press": 2, "dik": 28, "seq": None, "poll": None, "tick": None, "frame": None,
         "status": "lost", "shown_tick": 2},
        {"press": 3, "dik": 28, "seq": None, "poll": None, "tick": None, "frame": None,
         "status": "unsent", "shown_tick": 2},
        {"press": 4, "dik": 28, "seq": 1, "poll": 8, "tick": 3, "frame": 4, "status": "unpresented"}]
    assert result["summary"]["by_status"] == {"presented": 1, "lost": 1, "unsent": 1,
                                              "unpresented": 1}
    assert result["summary"]["stage"]["by_status"] == {"presented": 1, "lost": 1, "unsent": 1,
                                                       "unpresented": 1}
    values.update(entry=2, goal=3)                  # tick 2 is no longer in the stage
    stage = X.input_latency(**values)["summary"]["stage"]
    assert stage["total_us"] is None
    assert stage["by_status"] == {"presented": 0, "lost": 0, "unsent": 0, "unpresented": 1}


@pytest.mark.parametrize("handled, tick", [
    ("0.85", None),       # before the first Present returned: no frame on the screen
    ("0.9", 1),           # frame 1's Present returned at that instant: frame 1, tick 1's
    ("1.69", 1), ("1.7", 2), ("2.69", 2),
    ("2.7", 4), ("9.0", 4)])   # frame 3: tick 3 made none, tick 4 made it
def test_a_press_never_sent_is_placed_by_the_frame_on_the_screen_as_it_was_dequeued(
        handled, tick):
    values = evidence()
    values["keys"][0] |= {"handled_s": handled, "age_ns": 0, "status": "lost", "seq": "-",
                          "sent_s": "-"}
    values["polls"], values["ticks"][1]["keys"] = [], 0
    values["ticks"][2]["frames"], values["ticks"][3]["frames"] = 2, 3
    press = X.input_latency(**values)["presses"][0]
    assert (press["status"], press["shown_tick"]) == ("lost", tick)


def test_a_press_on_a_frame_the_table_never_made_is_outside_the_stage():
    """The Present log can run past the table's last tick: a frame made after the last
    GameLoop return belongs to no tick."""
    values = evidence()
    values["keys"][0] |= {"handled_s": "2.8", "age_ns": 0, "status": "unsent", "seq": "-",
                          "sent_s": "-"}
    values["polls"], values["ticks"][1]["keys"] = [], 0
    values["ticks"] = values["ticks"][:2]            # the table ends having made 2 frames
    values["goal"] = 1
    values["entry"] = 0
    result = X.input_latency(**values)
    assert result["presses"][0]["shown_tick"] is None
    assert result["summary"]["stage"]["presses"] == 0


def test_log_completion_order_is_independent_of_the_event_order():
    values = evidence()
    values["keys"].append(values["keys"][0] | {"change": 2, "down": 0})
    ordered = X.input_latency(**values)
    values["keys"].reverse()
    assert X.input_latency(**values) == ordered


def test_release_only_poll_may_have_no_keys_and_adds_no_press():
    values = evidence()
    values["keys"][0]["down"] = 0
    values["ticks"][1]["keys"] = 0
    result = X.input_latency(**values)
    assert result["presses"] == []
    assert result["summary"]["by_status"] == {"presented": 0, "lost": 0, "unsent": 0,
                                              "unpresented": 0}
    assert result["summary"]["total_us"] is None


def test_nearest_rank_quantiles_and_integer_mean_use_literal_values():
    values = evidence()
    first = values["keys"][0]
    values["keys"] = [first | {"change": i, "age_ns": i * 1000} for i in range(1, 11)]
    summary = X.input_latency(**values)["summary"]
    assert summary["total_us"] == {"min": 500001, "max": 500010, "mean": 500006,
                                   "p50": 500005, "p90": 500009, "p99": 500010}


def test_timestamps_are_rounded_before_subtraction_and_components_sum():
    values = evidence()
    values["keys"][0].update(age_ns=500, sent_s="1.3000005")
    values["polls"][0]["mono_ns"] = 1_400_001_500
    values["arrivals"][1]["arrived_s"] = "1.5000005"
    values["presents"][1].update(call_s="1.6000015", returned_s="1.7000005")
    row = X.input_latency(**values)["presses"][0]
    assert row["total_us"] == row["from_dequeue_us"] == 500000
    assert [row[name + "_us"] for name in X.LATENCY_COMPONENTS] == [0, 100000, 100002, 99998, 100002, 99998]
    assert sum(row[name + "_us"] for name in X.LATENCY_COMPONENTS) == row["total_us"]


@pytest.mark.parametrize("group,index,field,value,message", [
    ("polls", 0, "first_seq", 1, "tile"), ("polls", 0, "records", 0, "tile"),
    ("polls", 0, "last_seq", 2, "tile"), ("polls", 0, "presents", 3, "disagrees"),
    ("polls", 0, "tick", 0, "outside"), ("polls", 0, "mono_ns", 1_200_000_000, "before send"),
    ("keys", 0, "seq", 1, "no poll"), ("keys", 0, "change", 2, "numbered"),
    ("keys", 0, "status", "maybe", "status"), ("keys", 0, "down", 2, "invalid"),
    ("keys", 0, "dik", 0, "invalid"), ("keys", 0, "age_ns", -1, "nonnegative"),
    ("keys", 0, "handled_s", "NaN", "finite"), ("keys", 0, "sent_s", "inf", "finite"),
    ("keys", 0, "sent_s", "1.1", "backwards"),
    ("ticks", 1, "keys", 0, "no keys"), ("ticks", 1, "frames", 0, "backwards"),
    ("presents", 1, "frame", 3, "numbered"), ("presents", 1, "returned_s", "1.5", "backwards"),
    ("arrivals", 1, "frame", 3, "numbered"), ("arrivals", 1, "arrived_s", "1.2", "chain"),
])
def test_malformed_or_contradictory_rows_refuse(group, index, field, value, message):
    values = evidence()
    values[group][index][field] = value
    with pytest.raises(X.Refused, match=message):
        X.input_latency(**values)


def test_raw_clock_disagreement_is_not_hidden_by_microsecond_rounding():
    values = evidence()
    values["polls"][0]["mono_ns"] = 1_400_000_001
    values["arrivals"][1]["arrived_s"] = "1.400000000"
    with pytest.raises(X.Refused, match="chain"):
        X.input_latency(**values)


def test_missing_arrival_duplicate_sequence_and_unsent_forgery_refuse():
    values = evidence()
    values["arrivals"] = values["arrivals"][:1]
    with pytest.raises(X.Refused, match="no complete relay"):
        X.input_latency(**values)
    values = evidence()
    values["polls"].append(values["polls"][0] | {"poll": 6})
    with pytest.raises(X.Refused, match="tile"):
        X.input_latency(**values)
    values = evidence()
    values["keys"][0]["status"] = "lost"
    with pytest.raises(X.Refused, match="carries a send"):
        X.input_latency(**values)


def test_one_record_has_one_send_time_even_for_several_changes():
    values = evidence()
    values["keys"].append(values["keys"][0] | {"change": 2, "dik": 30, "sent_s": "1.35"})
    with pytest.raises(X.Refused, match="backwards"):
        X.input_latency(**values)


def test_whole_multi_record_poll_consumes_each_sequence_once():
    values = evidence()
    values["keys"] += [values["keys"][0] | {"change": 2, "dik": 30, "seq": 1},
                       values["keys"][0] | {"change": 3, "dik": 31, "seq": 2}]
    values["polls"][0].update(last_seq=2, records=3)
    result = X.input_latency(**values)
    assert [(row["seq"], row["poll"], row["frame"], row["total_us"]) for row in result["presses"]] == [
        (0, 5, 2, 600000), (1, 5, 2, 600000), (2, 5, 2, 600000)]


@pytest.mark.parametrize("group,field,value", [
    ("ticks", "tick", True), ("ticks", "tick", 1.0),
    ("presents", "frame", True), ("presents", "frame", 1.0),
    ("arrivals", "frame", True), ("arrivals", "frame", 1.0),
])
def test_bool_and_float_cannot_impersonate_table_indices(group, field, value):
    values = evidence()
    values[group][0][field] = value
    with pytest.raises(X.Refused, match="nonnegative integer"):
        X.input_latency(**values)
