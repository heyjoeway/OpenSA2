"""The d3d9 lifetime check's comparisons, refusals and verdicts, made to fail
one way at a time.

`tools/s4/d3d9_life_check.py` claims six things of five runs, and a verdict
that holds for everything holds for every run ever made. So each verdict below
is shown holding for runs made to agree and failing for a run made to differ
in the one way it watches -- including the ways a comparison can hold while
comparing nothing: a trace a tick out of step, a gap, a tick held twice, two
files that share no tick, a keepall that stopped early, a released texture
whose bytes were not counted. Each refusal is driven through check() with a
fake run in place of the guest, which this suite never runs; and the command
each arm runs is built with tools/s4/demo_run.py's own command(), whose
defaults are its own run's.

The fake runs and the arms they stand for are named by label, so the cases
that drive check() read the same against any version of the tool.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import d3d9_life_check as L  # noqa: E402
import demo_run as D  # noqa: E402
import device_profile as DP  # noqa: E402
import reach_receipt as RR  # noqa: E402

POLLS = 6
TRACE_HEADER = "tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z"
EXIT_STOP = "the guest called ExitProcess(0) -- it ended the process itself"
ARENA_STOP = "d3d9: the guest heap arena has no room for 307200 bytes of backing store for a " \
             "locked resource"


def trace_row(t: int, player: str = "0x053ad1fc", level: str = "0x000d",
              x: str = "0x42b40000", frames: int | None = None) -> str:
    f = t if frames is None else frames
    return f"{t},{level},0x00000002,0x0010,0x{f:08x},{player},{x},0x00000000,0x00000000"


def trace_rows(n: int, player: str = "0x053ad1fc", level: str = "0x000d") -> list[str]:
    return [trace_row(t, player, level) for t in range(1, n + 1)]


def tick_rows(n: int) -> list[str]:
    return [f"{t},0x{t:08x},0x0000000d,0x{t * 7:08x}" for t in range(1, n + 1)]


def dicts(rows: list[str]) -> list[dict]:
    return [dict(zip(TRACE_HEADER.split(","), r.split(","))) for r in rows]


def write(path: Path, header: str, rows: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def arm_of(label: str):
    return next(a for a in L.ARMS if a.label == label)


def lifetime(levels: int = 3, freed: int = 4096, on_destroyed: int = 0, past_zero: int = 0):
    return {"objects_destroyed": 9, "guest_bytes_freed": freed, "level_surfaces": levels,
            "calls_on_destroyed": on_destroyed, "calls_on_destroyed_first_caller": "0x00866d6d",
            "releases_past_zero": past_zero, "releases_past_zero_first_caller": "0x00866da7"}


def tex_row(seq: int, surface: str = "0x00000000", nonzero: int = 4096, locks: int = 0,
            refused: bool = False, guest_copied: bool = False, filled: bool = True) -> dict:
    """A textures_from_files row as harness/crt_probe.c writes it; surface 0 is
    a store freed at the texture's final release."""
    return {"seq": seq, "refused": refused, "surface": surface, "surface_nonzero_bytes": nonzero,
            "locks": locks, "released": surface == "0x00000000", "guest_copied": guest_copied,
            "pixels_decoded": filled, "runtime_wrote_pixels": filled}


# fakepixels' rows: one released, one still alive, one the guest locked (left out).
FAKE_ROWS = [tex_row(1), tex_row(2, surface="0x05400020"), tex_row(3, nonzero=0, locks=1)]
# Every other run's: nothing written by this runtime, one released and one alive.
PLAIN_ROWS = [tex_row(1, nonzero=0, filled=False),
              tex_row(2, surface="0x05400020", nonzero=0, filled=False)]


def record(arm, polls: int = POLLS) -> dict:
    """A run record with what the check reads, as the arm's run would write it:
    the model, keepall and fakepixels to the host's quit and the game's exit,
    neverfree at the arena, ownzero at the object pool."""
    label = arm.label
    # keepall frees only the stores a lock grew past, so less than the model.
    life = lifetime(levels=3 if arm.d3d9_model else 0,
                    freed={"neverfree": 0, "keepall": 1024}.get(label, 4096))
    stop = {"model": EXIT_STOP, "keepall": EXIT_STOP, "fakepixels": EXIT_STOP,
            "neverfree": ARENA_STOP,
            "ownzero": "d3d9: the fabricated-object pool of 16384 is exhausted"}[label]
    done = label in ("model", "keepall", "fakepixels")
    rows = FAKE_ROWS if label == "fakepixels" else PLAIN_ROWS
    filled = sum(1 for r in rows if r["runtime_wrote_pixels"])
    return {"pump": {"iterations": polls if done else 3, "quit_by_host": 1 if done else 0,
                     "quit_by_guest": 0, "injected": 0},
            "trap": {"status": 1, "note": stop},
            "heap": {"consistent": True, "peak_bytes": 1000, "failures": 0},
            "d3d9": {"objects_built": 12, "guest_bytes_allocated": 8192, "lifetime": life,
                     "textures_from_files": {"answered": len(rows), "with_decoded_pixels": filled,
                                             "rows_counted": 2,
                                             "unlocked_surfaces_holding_bytes": 0,
                                             "guest_copied_rows_left_out": 0,
                                             "rows": [dict(r) for r in rows]}},
            "clauses": [{"name": L.TEXTURE_CLAUSE, "value": label != "fakepixels",
                         "in_verdict": False}],
            "device_profile": {"default": True},
            "virtual": {"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"},
            "dinput": {"device_enabled": True}}


# ------------------------------------------------------- the arms' commands

def test_every_run_sets_its_break_the_models_to_empty():
    assert [(a.label, a.brk, a.d3d9_model, a.polls) for a in L.ARMS] == [
        ("model", "", True, 0), ("neverfree", "neverfree", False, 0),
        ("ownzero", "ownzero", False, 0), ("keepall", "keepall", True, 0),
        ("fakepixels", "fakepixels", True, 1500)]
    for arm in L.ARMS:
        # present on every arm, "" included: an absent key lets the caller's break in
        assert L.arm_env(arm) == {"SA2_D3D9_BREAK": arm.brk}


def test_each_break_is_one_the_runtime_parses():
    src = (ROOT / "runtime" / "sa2_d3d9.c").read_text()
    for arm in L.ARMS[1:]:
        assert f'!strcmp(v, "{arm.brk}")' in src, arm.brk


def test_the_notes_the_verdicts_read_are_the_runtimes_and_the_harness_s():
    assert L.ARENA_NOTE in (ROOT / "runtime" / "sa2_d3d9.c").read_text()
    assert L.TEXTURE_CLAUSE in (ROOT / "harness" / "crt_probe.c").read_text()


@pytest.fixture
def no_owned_env(monkeypatch):
    for var in RR.DEVICE_VARS:
        monkeypatch.delenv(var, raising=False)


def test_demo_runs_own_command_is_unchanged_by_the_new_parameters(no_owned_env):
    cmd, run_json, ticks, trace = D.command(100, DP.DEFAULT, 60)
    assert cmd[cmd.index("--name") + 1] == cmd[cmd.index("--out-prefix") + 1] == "attract-demo"
    spec = cmd[cmd.index("--run") + 1].split("|")
    assert spec[0] == "a" and not any(s.startswith("SA2_D3D9_BREAK=") for s in spec)
    assert run_json == D.SR.RUNDIR / "attract-demo.a.json"
    assert (ticks, trace) == (D.OUT / "lifted.csv", D.OUT / "stage-trace.csv")


def test_an_arms_command_names_its_build_its_run_and_its_break(no_owned_env):
    cmd, run_json, ticks, trace = D.command(100, DP.DEFAULT, 60, name=L.NAME,
                                            label="ownzero", out=L.OUT / "ownzero",
                                            extra_env=L.arm_env(L.OWNZERO))
    assert cmd[cmd.index("--name") + 1] == cmd[cmd.index("--out-prefix") + 1] == "d3d9-life-check"
    spec = cmd[cmd.index("--run") + 1].split("|")
    assert spec[0] == "ownzero" and "SA2_D3D9_BREAK=ownzero" in spec
    assert "SA2_STAGE_TRACE=private/generated/s4/d3d9-life/ownzero/stage-trace.csv" in spec
    assert run_json == D.SR.RUNDIR / "d3d9-life-check.ownzero.json"
    assert ticks == L.OUT / "ownzero" / "lifted.csv"
    # the model's empty break is in the spec, not left out
    cmd, *_ = D.command(100, DP.DEFAULT, 60, name=L.NAME, label="model",
                        out=L.OUT / "model", extra_env=L.arm_env(L.MODEL))
    assert "SA2_D3D9_BREAK=" in cmd[cmd.index("--run") + 1].split("|")


def test_each_arm_runs_its_own_poll_budget(monkeypatch):
    asked = []

    class Stop(Exception):
        pass

    def command(polls, profile, timeout, **kw):
        asked.append((kw["label"], polls))
        raise Stop
    monkeypatch.setattr(L.D, "command", command)
    for arm in L.ARMS:
        with pytest.raises(Stop):
            L.run_arm(arm, argparse.Namespace(polls=305, timeout=60), DP.DEFAULT)
    assert asked == [("model", 305), ("neverfree", 305), ("ownzero", 305), ("keepall", 305),
                     ("fakepixels", 1500)]


def test_an_extra_setting_may_not_name_the_device_profiles_variables(no_owned_env):
    with pytest.raises(SystemExit, match="DEMO_RUN_REFUSED SA2_DEVICE_PROFILE in extra_env"):
        D.command(100, DP.DEFAULT, 60, extra_env={"SA2_DEVICE_PROFILE": "x"})


@pytest.mark.parametrize("var", ["SA2_PUMP_POLLS", "SA2_STAGE_TRACE", "SA2_D3D9_DEVICE"])
def test_an_extra_setting_may_not_replace_one_the_command_sets(no_owned_env, var):
    with pytest.raises(SystemExit, match=f"DEMO_RUN_REFUSED {var} in extra_env: this command "
                                         "sets it itself"):
        D.command(100, DP.DEFAULT, 60, name=L.NAME, label="x", out=L.OUT / "x",
                  extra_env={var: "1", "SA2_D3D9_BREAK": ""})


def test_every_pinned_path_is_a_file_of_this_repository():
    for t in L.PIPELINE:
        assert not t.startswith(("private/", "/")) and (ROOT / t).is_file(), t
    p = subprocess.run(["git", "check-ignore", "--", *L.PIPELINE], cwd=str(ROOT),
                       capture_output=True, text=True)
    assert p.returncode == 1, p.stdout or p.stderr


# ------------------------------------------------------------- comparing

def test_same_rows_agrees_on_identical_files_and_counts_the_shared_rows(tmp_path):
    a = write(tmp_path / "a.csv", "tick,x", tick_rows(5))
    b = write(tmp_path / "b.csv", "tick,x", tick_rows(3))
    assert L.same_rows(a, b) == {"rows_a": 5, "rows_b": 3, "headers_match": True,
                                 "repeated_tick_a": None, "repeated_tick_b": None,
                                 "shared_rows": 3, "first_difference": None}


def test_same_rows_names_the_first_differing_tick(tmp_path):
    rows = tick_rows(5)
    a = write(tmp_path / "a.csv", "tick,x", rows)
    rows[3] = rows[3][:-1] + "f"                        # one byte of tick 4
    rows[4] = rows[4][:-1] + "f"
    b = write(tmp_path / "b.csv", "tick,x", rows)
    got = L.same_rows(a, b)
    assert (got["shared_rows"], got["first_difference"]) == (5, 4)


def test_same_rows_compares_nothing_across_different_headers_or_a_missing_file(tmp_path):
    a = write(tmp_path / "a.csv", "tick,x", tick_rows(3))
    b = write(tmp_path / "b.csv", "tick,y", tick_rows(3))
    assert L.same_rows(a, b)["headers_match"] is False
    assert L.same_rows(a, tmp_path / "none.csv")["headers_match"] is False
    assert L._rows(L.same_rows(a, b), "the tick capture", "x's") == [
        "the tick capture: the headers differ, or a file is missing"]


def test_two_captures_that_share_no_tick_compare_nothing(tmp_path):
    later = [f"{t + 3},0x{t:08x},0x0000000d,0x{t * 7:08x}" for t in range(1, 4)]
    got = L.same_rows(write(tmp_path / "a.csv", "tick,x", tick_rows(3)),
                      write(tmp_path / "b.csv", "tick,x", later))
    assert (got["shared_rows"], got["first_difference"]) == (0, None)
    assert L._rows(got, "the tick capture", "x's") == ["the tick capture: no row is shared"]


def test_same_rows_names_a_tick_either_file_holds_twice(tmp_path):
    rows = tick_rows(4)
    got = L.same_rows(write(tmp_path / "a.csv", "tick,x", rows[:2] + rows[1:]),
                      write(tmp_path / "b.csv", "tick,x", rows + [rows[3]]))
    assert (got["repeated_tick_a"], got["repeated_tick_b"], got["shared_rows"]) == (2, 4, 0)
    assert L._rows(got, "the tick capture", "keepall's") == [
        "the tick capture: the model's holds tick 2 twice",
        "the tick capture: keepall's holds tick 4 twice"]


# --------------------------------------------------------------- refusals

@pytest.mark.parametrize("label, change, why", [
    ("model", lambda r, e: e.update(SA2_D3D9_BREAK="keepall"),
     "its loop_run row sets SA2_D3D9_BREAK to 'keepall', not ''"),
    ("keepall", lambda r, e: e.pop("SA2_D3D9_BREAK"),
     "its loop_run row sets SA2_D3D9_BREAK to None, not 'keepall'"),
    ("model", lambda r, e: r["d3d9"].pop("lifetime"), "its record has no d3d9.lifetime block"),
    ("keepall", lambda r, e: r["d3d9"]["lifetime"].update(level_surfaces=0),
     "0 level surfaces: the Direct3D 9 model's GetSurfaceLevel never ran"),
    ("ownzero", lambda r, e: r["d3d9"]["lifetime"].update(level_surfaces=5),
     "5 level surfaces, which only the Direct3D 9 model builds"),
    ("neverfree", lambda r, e: r["d3d9"]["lifetime"].update(guest_bytes_freed=64),
     "64 bytes freed, where neverfree frees none"),
    ("fakepixels", lambda r, e: r["d3d9"]["textures_from_files"].update(with_decoded_pixels=0),
     "no texture filled by this runtime, where fakepixels fills every file texture"),
    ("model", lambda r, e: r["d3d9"]["textures_from_files"].update(with_decoded_pixels=2),
     "2 textures filled by this runtime, which only fakepixels does"),
    ("ownzero", lambda r, e: r["d3d9"].pop("textures_from_files"),
     "its record has no d3d9.textures_from_files.with_decoded_pixels"),
])
def test_a_run_that_did_not_run_its_model_says_why(label, change, why):
    arm = arm_of(label)
    rec, env = record(arm), dict(L.arm_env(arm))
    assert L.ran_its_model(arm, rec, env) == []
    change(rec, env)
    assert why in L.ran_its_model(arm, rec, env)


# --------------------------------------------------------------- verdicts

def model_trace(n: int = POLLS - 1) -> dict:
    native = dicts(trace_rows(n))
    return D.compare_traces(native, native)


def test_the_model_finishes():
    assert L.model_finishes(record(L.MODEL), POLLS, model_trace()) == []


@pytest.mark.parametrize("change, why", [
    (lambda r, v: r["pump"].update(iterations=4), "4 iterations, not the 6 polls asked for"),
    (lambda r, v: r["pump"].update(quit_by_host=0), "the loop did not end on the host's quit"),
    (lambda r, v: r["pump"].update(injected=1), "injected 1 message(s), not 0"),
    (lambda r, v: r["trap"].update(note="d3d9: the guest heap arena has no room for 8 bytes"),
     "its stop is not the game's own ExitProcess(0): d3d9: the guest heap arena"),
    (lambda r, v: r["d3d9"]["lifetime"].update(calls_on_destroyed=2),
     "calls_on_destroyed is 2, the first from 0x00866d6d"),
    (lambda r, v: r["d3d9"]["lifetime"].update(releases_past_zero=1),
     "releases_past_zero is 1, the first from 0x00866da7"),
    (lambda r, v: r["heap"].update(consistent=False), "the guest heap walk is not consistent"),
    (lambda r, v: v.update(first_divergence={"tick": 3, "columns": ["x"]}),
     "the stage trace first differs from native at tick 3 in x"),
    (lambda r, v: v.update(compared_rows=2),
     "the stage trace compared 2 of native's 5 rows (the model has 5), so not every native row "
     "was compared"),
    (lambda r, v: v.update(lifted_rows=0, native_rows=0, compared_rows=0),
     "the stage trace: native's has no rows, so none was compared"),
    (lambda r, v: v.update(repeated_tick_lifted=2),
     "the stage trace: the model's holds tick 2 twice"),
])
def test_the_model_does_not_finish_when_one_thing_is_wrong(change, why):
    rec, vs = record(L.MODEL), model_trace()
    change(rec, vs)
    got = L.model_finishes(rec, POLLS, vs)
    assert len(got) == 1 and got[0].startswith(why), got


# Stage traces that compare nothing, or less than they seem to. Each lifted
# trace has at least native's row count and no row that differs from native's
# at a tick both have, so a verdict that counted rows but not comparisons
# would hold for all four.
N = POLLS - 1
HOLLOW_TRACES = {
    "a tick out of step": [trace_row(t + 1, frames=t) for t in range(1, N + 1)],
    "a gap at tick 2, every later row different":
        [trace_row(1)] + [trace_row(t, x="0x42b50000") for t in range(3, N + 3)],
    "no tick shared": [trace_row(t + 100) for t in range(1, N + 1)],
    "tick 2 twice, the first different":
        [trace_row(1), trace_row(2, x="0x42b50000")] + trace_rows(N)[1:],
}


@pytest.mark.parametrize("case", HOLLOW_TRACES)
def test_the_model_does_not_finish_on_a_trace_that_compares_less_than_every_native_row(case):
    vs = D.compare_traces(dicts(trace_rows(N)), dicts(HOLLOW_TRACES[case]))
    assert vs["first_divergence"] is None and vs["lifted_rows"] >= vs["native_rows"] == N
    assert L.model_finishes(record(L.MODEL), POLLS, vs) != [], case


def test_neverfree_stops_at_the_arena_and_nowhere_else():
    assert L.stops_at_the_arena(record(L.NEVERFREE)) == []
    assert L.stops_at_the_arena(record(L.OWNZERO)) == [
        "its stop is not the arena: d3d9: the fabricated-object pool of 16384 is exhausted"]


def pair(tmp_path, a_rows, b_rows, header="tick,x"):
    return L.same_rows(write(tmp_path / "a.csv", header, a_rows),
                       write(tmp_path / "b.csv", header, b_rows))


def test_the_model_equals_neverfree_on_every_row_neverfree_ran(tmp_path):
    ticks = pair(tmp_path, tick_rows(9), tick_rows(4))
    trace = pair(tmp_path / "t", trace_rows(9), trace_rows(4), TRACE_HEADER)
    assert L.equals_before_the_wall(ticks, trace) == []


def test_the_model_and_neverfree_differ_on_one_byte_of_one_row(tmp_path):
    rows = tick_rows(4)
    rows[1] = rows[1].replace("0x0000000d", "0x0000000e")
    trace = pair(tmp_path / "t", trace_rows(9), trace_rows(4), TRACE_HEADER)
    assert L.equals_before_the_wall(pair(tmp_path, tick_rows(9), rows), trace) == [
        "the tick capture: neverfree's first differs at tick 2"]
    # the player compared as an address here, not as null or not
    moved = pair(tmp_path / "p", trace_rows(9), trace_rows(4, player="0x0badf00d"), TRACE_HEADER)
    assert L.equals_before_the_wall(pair(tmp_path, tick_rows(9), tick_rows(4)), moved) == [
        "the stage trace: neverfree's first differs at tick 1"]


def test_a_neverfree_row_the_model_lacks_fails(tmp_path):
    ticks = pair(tmp_path, tick_rows(3), tick_rows(4))
    trace = pair(tmp_path / "t", trace_rows(3), trace_rows(3), TRACE_HEADER)
    assert L.equals_before_the_wall(ticks, trace) == [
        "the model's tick capture has 3 of neverfree's 4 rows"]


def test_a_model_tick_held_twice_does_not_stand_for_the_neverfree_row_it_lacks(tmp_path):
    # The model holds tick 2 twice and lacks tick 4, neverfree has 1..4: counting
    # the model's rows as shared would make 4 of 4, and tick 4 was never read.
    rows = tick_rows(4)
    ticks = pair(tmp_path, [rows[0], rows[1], rows[1], rows[2]], rows)
    trace = pair(tmp_path / "t", trace_rows(9), trace_rows(4), TRACE_HEADER)
    assert L.equals_before_the_wall(ticks, trace) == [
        "the tick capture: the model's holds tick 2 twice"]


def test_a_break_matches_the_model_on_every_row_it_ran(tmp_path):
    ticks = pair(tmp_path, tick_rows(9), tick_rows(5))
    rows = dicts(trace_rows(9))
    other = [dict(r, player="0x0badf00d") for r in rows[:5]]   # another heap address
    assert L.matches_the_model("ownzero", ticks, D.compare_traces(rows, other)) == []


def test_a_break_that_differs_from_the_model_fails(tmp_path):
    rows = tick_rows(5)
    rows[2] = rows[2][:-1] + "f"
    trace = dicts(trace_rows(9))
    moved = [dict(r) for r in trace[:5]]
    moved[3]["x"] = "0x42b50000"
    got = L.matches_the_model("keepall", pair(tmp_path, tick_rows(9), rows),
                              D.compare_traces(trace, moved))
    assert got == ["the tick capture: keepall's first differs at tick 3",
                   "the stage trace: keepall's first differs at tick 4 in x"]
    assert L.matches_the_model("keepall", pair(tmp_path, tick_rows(9), tick_rows(9)),
                               D.compare_traces(trace, [])) == [
        "the stage trace: a trace is empty"]


@pytest.mark.parametrize("case, why", [
    ("a tick out of step", "the stage trace: 0 of ownzero's 5 rows were compared"),
    ("a gap at tick 2, every later row different",
     "the stage trace: 1 of ownzero's 6 rows were compared"),
    ("no tick shared", "the stage trace: 0 of ownzero's 5 rows were compared"),
    ("tick 2 twice, the first different", "the stage trace: ownzero's holds tick 2 twice"),
])
def test_a_break_whose_trace_compares_less_than_every_row_it_ran_fails(tmp_path, case, why):
    # The tick capture agrees on every row; the stage trace alone is hollow.
    ticks = pair(tmp_path, tick_rows(9), tick_rows(5))
    got = L.matches_the_model("ownzero", ticks,
                              D.compare_traces(dicts(trace_rows(9)), dicts(HOLLOW_TRACES[case])))
    assert got == [why], got


def test_a_break_tick_capture_with_rows_the_model_lacks_fails(tmp_path):
    rows = tick_rows(12)
    ticks = pair(tmp_path, tick_rows(9), rows[:3] + rows[10:])     # 1..3, then 11 and 12
    got = L.matches_the_model("ownzero", ticks,
                              D.compare_traces(dicts(trace_rows(9)), dicts(trace_rows(5))))
    assert got == ["the tick capture: 3 of ownzero's 5 rows are the model's"]


def test_a_break_tick_capture_holding_a_tick_twice_fails(tmp_path):
    rows = tick_rows(5)
    other = rows[:1] + [rows[1][:-1] + "f"] + rows[1:]       # tick 2 twice, the first different
    got = L.matches_the_model("ownzero", pair(tmp_path, tick_rows(9), other),
                              D.compare_traces(dicts(trace_rows(9)), dicts(trace_rows(5))))
    assert got == ["the tick capture: ownzero's holds tick 2 twice"]


def test_keepall_matches_when_it_runs_what_the_model_ran(tmp_path):
    ticks = pair(tmp_path, tick_rows(9), tick_rows(9))
    trace = D.compare_traces(dicts(trace_rows(9)), dicts(trace_rows(9, player="0x0badf00d")))
    assert L.keepall_matches(record(L.KEEPALL), POLLS, ticks, trace, record(L.MODEL)) == []


def test_keepall_that_frees_as_much_as_the_model_fails(tmp_path):
    """The second fold's review, W1: the break doing nothing held every verdict."""
    ticks = pair(tmp_path, tick_rows(9), tick_rows(9))
    trace = D.compare_traces(dicts(trace_rows(9)), dicts(trace_rows(9)))
    rec = record(L.KEEPALL)
    rec["d3d9"]["lifetime"]["guest_bytes_freed"] = 4096
    assert L.keepall_matches(rec, POLLS, ticks, trace, record(L.MODEL)) == [
        "keepall freed 4096 bytes and the model 4096: keepall must free less, or nothing "
        "shows it kept a store the model frees at a final release"]


def test_ownzero_that_frees_nothing_did_not_run_its_model():
    rec = record(L.OWNZERO)
    assert L.ran_its_model(L.OWNZERO, rec, L.arm_env(L.OWNZERO)) == []
    rec["d3d9"]["lifetime"]["guest_bytes_freed"] = 0
    assert L.ran_its_model(L.OWNZERO, rec, L.arm_env(L.OWNZERO)) == [
        "0 bytes freed, where ownzero frees each store at its object's own zero"]


def test_keepall_stopped_at_the_arena_fails_though_every_row_it_ran_matches(tmp_path):
    rec = record(L.KEEPALL)
    rec["pump"].update(iterations=3, quit_by_host=0)
    rec["trap"]["note"] = ARENA_STOP
    ticks = pair(tmp_path, tick_rows(9), tick_rows(4))
    trace = D.compare_traces(dicts(trace_rows(9)), dicts(trace_rows(4)))
    assert L.matches_the_model("keepall", ticks, trace) == []    # every row it ran matches
    assert L.keepall_matches(rec, POLLS, ticks, trace, record(L.MODEL)) == [
        "keepall: 3 iterations, not the 6 polls asked for",
        "keepall: the loop did not end on the host's quit",
        "keepall: its stop is not the game's own ExitProcess(0): " + ARENA_STOP,
        "the tick capture: keepall has 4 rows to the model's 9",
        "the stage trace: keepall has 4 rows to the model's 9"]


def fake_texture_record(rows: list[dict]) -> dict:
    rec = record(arm_of("fakepixels"))
    rec["d3d9"]["textures_from_files"]["rows"] = rows
    return rec


def test_fakepixels_bytes_are_counted_in_every_texture_the_count_reads():
    assert L.counted_after_release(fake_texture_record([dict(r) for r in FAKE_ROWS])) == []
    # a refused, a locked and a copied-into row are not the count's, whatever they hold
    assert L.counted_after_release(fake_texture_record(
        [tex_row(1), tex_row(2, nonzero=0, refused=True), tex_row(3, nonzero=0, locks=2),
         tex_row(4, nonzero=0, guest_copied=True)])) == []


@pytest.mark.parametrize("rows, why", [
    # the count before it was taken at the release: a freed store reads zero
    ([tex_row(1, nonzero=0), tex_row(2, surface="0x05400020")],
     "1 of the 2 rows the byte count reads count no non-zero byte, 1 of them released; the "
     "first is row 1"),
    ([tex_row(1, surface="0x05400020"), tex_row(2, surface="0x05410020")],
     "none of the 2 rows the byte count reads was released"),
    ([tex_row(1, locks=1), tex_row(2, refused=True)],
     "no texture row is one the byte count reads"),
    ([tex_row(1), tex_row(2, surface="0x05400020", nonzero=0)],
     "1 of the 2 rows the byte count reads count no non-zero byte, 0 of them released"),
])
def test_fakepixels_bytes_missing_from_a_texture_the_count_reads_fail(rows, why):
    got = L.counted_after_release(fake_texture_record(rows))
    assert len(got) == 1 and got[0].startswith(why), got


def test_the_answer_is_generated_only_from_verdicts_that_hold():
    held = {k: [] for k in L.CLAIMS}
    assert L.answer(held) == "Each holds: " + "; ".join(L.CLAIMS.values()) + "."
    assert L.answer(dict(held, keepall_matches_the_model=["x"])) == "Not every verdict holds."

# ------------------------------------------------- check(), without the guest


ROWS = {"model": POLLS - 1, "neverfree": 2, "ownzero": 3, "keepall": POLLS - 1, "fakepixels": 2}


def fake(where: Path, change=None, ran: list | None = None):
    """check()'s `run` with no guest: each arm's record and files as its run
    would leave them -- the model's trace equal to native's, neverfree's rows a
    prefix of the model's, ownzero's too with another player address -- then
    `change(arm, got)` for the case under test."""

    def run(arm, a, profile):
        if ran is not None:
            ran.append(arm.label)
        n = ROWS[arm.label]
        d = where / arm.label
        got = {"record": record(arm, getattr(arm, "polls", 0) or a.polls),
               "env": dict(L.arm_env(arm)), "binary_sha256": "e" * 64,
               "files": {"ticks": write(d / "lifted.csv", "tick,frame,mode,d", tick_rows(n)),
                         "trace": write(d / "stage-trace.csv", TRACE_HEADER, trace_rows(
                             n, player="0x0badf00d" if arm.label == "ownzero" else "0x053ad1fc"))}}
        if change:
            change(arm, got)
        got["run_json"] = where / f"{arm.label}.json"
        got["run_json"].write_text(json.dumps(got["record"]))
        return got
    return run


@pytest.fixture
def world(tmp_path, monkeypatch):
    native = tmp_path / "native"
    write(native / "sa2-stage-trace.csv", TRACE_HEADER, trace_rows(POLLS - 1))
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "RECEIPT", tmp_path / "receipt.json")
    monkeypatch.setattr(L.D, "native_side", lambda n: (DP.DEFAULT, []))
    monkeypatch.setattr(L.SR, "compare", lambda n, l: {"ticks": 0, "note": "not compared here"})
    monkeypatch.setattr(L, "inputs", lambda n: {"pipeline": {"tools/s4/x.py": "f" * 64}})
    monkeypatch.setattr(L, "provenance", lambda: {"commit": "c" * 40, "uncommitted_changes": True})
    return tmp_path


def args(where: Path, write_: bool = True) -> argparse.Namespace:
    return argparse.Namespace(native=where / "native", polls=None, timeout=3000, write=write_)


def test_runs_that_hold_every_verdict_write_the_receipt(world, capsys):
    ran: list[str] = []
    assert L.check(args(world), fake(world, ran=ran)) == 0
    assert ran == ["model", "neverfree", "ownzero", "keepall", "fakepixels"]
    r = json.loads((world / "receipt.json").read_text())
    assert [(x["label"], x["break"], x["polls"]) for x in r["runs"]] == [
        ("model", "", 305), ("neverfree", "neverfree", 305), ("ownzero", "ownzero", 305),
        ("keepall", "keepall", 305), ("fakepixels", "fakepixels", 1500)]
    assert r["verdicts"] == {k: {"claim": c, "holds": True} for k, c in L.CLAIMS.items()}
    assert list(r["verdicts"]) == sorted([
        "model_finishes", "neverfree_stops_at_the_arena", "model_equals_neverfree_before_the_wall",
        "ownzero_matches_the_model", "keepall_matches_the_model",
        "fakepixels_counted_after_release"])
    assert r["answer"] == L.answer({k: [] for k in L.CLAIMS})
    assert (r["polls"], r["native_rows"]) == (POLLS - 1 + 300, POLLS - 1)
    assert (r["commit"], r["uncommitted_changes"]) == ("c" * 40, True)
    assert r["command"] == (".venv/bin/python tools/s4/d3d9_life_check.py --native native "
                            "--polls 305 --timeout 3000 --write")
    assert r["runs"][1]["vs_model"]["ticks"]["shared_rows"] == 2
    assert r["runs"][3]["vs_model"]["trace"]["compared_rows"] == POLLS - 1
    assert "vs_model" not in r["runs"][0] and "vs_model" not in r["runs"][4]
    assert r["runs"][0]["texture_clause"][L.TEXTURE_CLAUSE] is True
    assert r["runs"][4]["texture_clause"]["with_decoded_pixels"] == 3
    assert r["runs"][0]["d3d9"]["lifetime"]["level_surfaces"] == 3
    assert capsys.readouterr().out.startswith("D3D9_LIFE OK")


def test_without_write_the_receipt_is_not_written(world):
    assert L.check(args(world, write_=False), fake(world)) == 0
    assert not (world / "receipt.json").exists()


def on(label: str, change):
    return lambda a, got: change(got) if a.label == label else None


def every(labels: tuple[str, ...], rows):
    """Every named arm's stage trace rewritten alike, `rows(n)` for its own n."""
    return lambda a, got: write(got["files"]["trace"], TRACE_HEADER, rows(ROWS[a.label])) \
        if a.label in labels else None


def at_the_arena(got):
    got["record"]["pump"].update(iterations=3, quit_by_host=0)
    got["record"]["trap"]["note"] = ARENA_STOP
    write(got["files"]["ticks"], "tick,frame,mode,d", tick_rows(2))
    write(got["files"]["trace"], TRACE_HEADER, trace_rows(2))


LIFE = ("model", "neverfree", "ownzero", "keepall")


@pytest.mark.parametrize("change, verdict", [
    (on("model", lambda got: got["record"]["d3d9"]["lifetime"].update(calls_on_destroyed=1)),
     "model_finishes"),
    # every trace a tick out of step: the runs agree with each other and the
    # model's compares none of native's rows
    (every(LIFE, lambda n: [trace_row(t + 1, frames=t) for t in range(1, n + 1)]),
     "model_finishes"),
    # every trace with a gap at tick 2 and every later row different from native's
    (every(LIFE, lambda n: [trace_row(1)] + [trace_row(t, x="0x42b50000")
                                             for t in range(3, n + 2)]),
     "model_finishes"),
    (on("neverfree", lambda got: got["record"]["trap"].update(note="ExitProcess(0)")),
     "neverfree_stops_at_the_arena"),
    (on("neverfree", lambda got: write(got["files"]["ticks"], "tick,frame,mode,d",
                                       [tick_rows(2)[0], "2,0x0,0x0,0x0"])),
     "model_equals_neverfree_before_the_wall"),
    (on("ownzero", lambda got: write(got["files"]["ticks"], "tick,frame,mode,d",
                                     tick_rows(2) + ["3,0x0,0x0,0x0"])),
     "ownzero_matches_the_model"),
    # ownzero's stage trace a tick out of step: no row of it is compared
    (on("ownzero", lambda got: write(got["files"]["trace"], TRACE_HEADER,
                                     [trace_row(t + 1, player="0x0badf00d", frames=t)
                                      for t in range(1, 4)])),
     "ownzero_matches_the_model"),
    (on("keepall", lambda got: write(got["files"]["trace"], TRACE_HEADER,
                                     trace_rows(POLLS - 1, level="0x0030"))),
     "keepall_matches_the_model"),
    # keepall stopped where neverfree stops: every row it ran matches the model's
    (on("keepall", at_the_arena), "keepall_matches_the_model"),
    # the count before it was taken at the release: the released row reads zero
    (on("fakepixels", lambda got: got["record"]["d3d9"]["textures_from_files"]["rows"][0]
        .update(surface_nonzero_bytes=0)),
     "fakepixels_counted_after_release"),
])
def test_one_verdict_failing_fails_the_check_and_writes_nothing(world, capsys, change, verdict):
    ran: list[str] = []
    assert L.check(args(world), fake(world, change, ran)) == 1
    assert ran == ["model", "neverfree", "ownzero", "keepall", "fakepixels"]  # every run still ran
    assert not (world / "receipt.json").exists()
    out = capsys.readouterr().out
    assert out.startswith("D3D9_LIFE FAILED")
    failing = [k for k in L.CLAIMS if f"  {k}: FAILS" in out]
    assert failing == [verdict], out


@pytest.mark.parametrize("change, refusal", [
    (on("ownzero", lambda got: got["env"].update(SA2_D3D9_BREAK="")),
     "ownzero: its loop_run row sets SA2_D3D9_BREAK to '', not 'ownzero'"),
    (on("keepall", lambda got: got["record"]["trap"].update(status=L.TRAP_TIMEOUT)),
     "keepall: stopped by the harness's wall-clock alarm"),
    (on("neverfree", lambda got: got["record"]["d3d9"]["lifetime"].update(level_surfaces=4)),
     "neverfree: 4 level surfaces, which only the Direct3D 9 model builds"),
    (on("fakepixels", lambda got: got["record"]["d3d9"]["textures_from_files"]
        .update(with_decoded_pixels=0)),
     "fakepixels: no texture filled by this runtime"),
    (on("keepall", lambda got: got.update(binary_sha256="0" * 64)),
     "the runs were not one binary"),
    (on("fakepixels", lambda got: got.update(binary_sha256="0" * 64)),
     "the runs were not one binary"),
    (on("model", lambda got: got.update(binary_sha256=None)), "the runs were not one binary"),
])
def test_what_cannot_be_compared_is_refused_and_nothing_written(world, change, refusal):
    with pytest.raises(SystemExit, match=re.escape(f"D3D9_LIFE_REFUSED {refusal}")):
        L.check(args(world), fake(world, change))
    assert not (world / "receipt.json").exists()


def test_a_run_that_read_another_device_profile_is_refused(world, monkeypatch):
    def other(run, profile):
        raise DP.Refusal("the run read device profile x and was handed default")
    monkeypatch.setattr(L.DP, "check_run", other)
    with pytest.raises(SystemExit, match=re.escape(
            "D3D9_LIFE_REFUSED model: the run read device profile x")):
        L.check(args(world), fake(world))


def test_a_capture_that_cannot_stand_for_the_runs_is_refused_before_any_run(world, monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(L.D, "native_side", lambda n: (None, ["not the vanilla oracle's"]))
    with pytest.raises(SystemExit, match="cannot stand for these runs: not the vanilla oracle's"):
        L.check(args(world), fake(world, ran=ran))
    assert ran == []


def test_an_input_that_moves_while_the_runs_run_is_refused(world, monkeypatch):
    seen = iter([{"pipeline": {"tools/s4/x.py": d * 64}} for d in "12"])
    monkeypatch.setattr(L, "inputs", lambda n: next(seen))
    with pytest.raises(SystemExit, match="D3D9_LIFE_REFUSED an input moved while the runs ran"):
        L.check(args(world), fake(world))


def test_provenance_reads_this_repositorys_commit_and_tree():
    got = L.provenance()
    assert re.fullmatch(r"[0-9a-f]{40}", got["commit"])
    assert isinstance(got["uncommitted_changes"], bool)
