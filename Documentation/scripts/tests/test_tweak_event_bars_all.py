"""The tweak event-bars-all (runtime/tweaks/event-bars-all.c): every event the executor asks about is
shown at 4:3 (the "shown at 4:3?" answer, 0x005F8EC0, is 1 at the executor's call), the movie
player's call is left alone, and the tweak cannot be on with no-event-bars.

Built as test_tweaks.py builds no-event-bars, with its driver: the reader, no-event-bars.c (whose
wrap of sub_5f8ec0 this tweak acts in) and event-bars-all.c, linked with a stand-in for the lifted
answer (EAX = 1 when ECX is in the game's table of 14 events, then the RET) and for the runtime's
peek and register accessors. Each call puts a caller's return address at guest [ESP] first, as the
lifted CALL does, and prints "<event> <EAX> <ESP moved>".
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_tweaks as TT  # noqa: E402

EXECUTOR, MOVIE = TT.EXECUTOR, TT.MOVIE
TABLE, IN_ENGINE = TT.TABLE, TT.IN_ENGINE
OUTSIDE = [0, 1, 27, 50, 102, 130, 204, 362, 410, 500]   # events the game draws no bars for


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not TT.ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {TT.ZIG}")
    d = tmp_path_factory.mktemp("event-bars-all")
    (d / "driver.c").write_text(TT.DRIVER)
    out = d / "tw"
    done = subprocess.run([str(TT.ZIG), "cc", *TT.CC, str(d / "driver.c"), str(TT.RUNTIME / "sa2_tweaks.c"),
                           str(TT.RUNTIME / "tweaks" / "no-event-bars.c"),
                           str(TT.RUNTIME / "tweaks" / "event-bars-all.c"), "-o", str(out)],
                          capture_output=True, text=True, env=TT.zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in TT.VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def at(site: str, events: list[int]) -> list[str]:
    return [x for e in events for x in (site, str(e))]


# The intro (361, in the table), event 50 (Hero story, after the Prison Island movie; not in the
# table), a Dark story event in the table (100), the movie 409 at the movie player's call, event 50
# again, and an event outside the table at the movie player's call.
CALLS = (EXECUTOR, "361", EXECUTOR, "50", EXECUTOR, "100", MOVIE, "409", EXECUTOR, "50", MOVIE, "50")
GAME = ["361 1 4", "50 0 4", "100 1 4", "409 1 4", "50 0 4", "50 0 4"]


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}])
def test_off_is_the_lifted_answer_and_says_nothing(exe, env):
    assert run(exe, *CALLS, **env) == (GAME, "")


def test_on_every_event_at_the_executor_is_shown_at_4_3_and_the_movie_is_left_alone(exe):
    out, err = run(exe, *CALLS, SA2_TWEAKS="event-bars-all")
    assert out == ["361 1 4", "50 1 4", "100 1 4", "409 1 4", "50 1 4", "50 0 4"]
    assert err.splitlines() == [
        "sa2 tweaks: event-bars always (SA2_TWEAKS=event-bars-all)",
        "sa2 tweaks: event-bars always: event 361 has its side bars already (it is in the game's list)",
        "sa2 tweaks: event-bars always: event 50's side bars drawn (the game draws none for it)",
        "sa2 tweaks: event-bars always: event 100 has its side bars already (it is in the game's list)"]


def test_each_event_in_and_out_of_the_table(exe):
    """At the executor the answer is 1 for every event; the game's own answer is 1 for the table
    alone. At the movie player's call it is the game's own answer, on or off."""
    events = IN_ENGINE + OUTSIDE
    out, _ = run(exe, *at(EXECUTOR, events), SA2_TWEAKS="event-bars-all")
    assert out == [f"{e} 1 4" for e in events]
    assert run(exe, *at(EXECUTOR, events))[0] == [f"{e} {int(e in TABLE)} 4" for e in events]
    movie = [409] + OUTSIDE
    assert run(exe, *at(MOVIE, movie), SA2_TWEAKS="event-bars-all")[0] == [f"{e} {int(e == 409)} 4" for e in movie]


def test_each_event_is_named_once_and_sixteen_at_most(exe):
    events = list(range(1000, 1020))
    out, err = run(exe, *at(EXECUTOR, events + events), SA2_TWEAKS="event-bars-all")
    assert out == [f"{e} 1 4" for e in events + events]
    assert err.splitlines()[1:] == [
        f"sa2 tweaks: event-bars always: event {e}'s side bars drawn (the game draws none for it)"
        for e in events[:16]]


@pytest.mark.parametrize("value", ["event-bars-all,no-event-bars", "no-event-bars, event-bars-all"])
def test_not_with_no_event_bars(exe, value):
    """Named together, neither is on: the game's own answer, and the report line names the pair."""
    out, err = run(exe, *CALLS, SA2_TWEAKS=value)
    assert out == GAME
    assert err == (f"sa2 tweaks: none on (SA2_TWEAKS={value}; event-bars-all and no-event-bars cannot "
                   "both be on, so neither is)\n")


def test_not_with_no_event_bars_beside_a_name_that_is_no_tweak(exe):
    value = "no-event-bars,event-bars-all,widescreen-fmv"
    out, err = run(exe, *CALLS, SA2_TWEAKS=value)
    assert out == GAME
    assert err == (f"sa2 tweaks: none on (SA2_TWEAKS={value}; event-bars-all and no-event-bars cannot "
                   "both be on, so neither is; no tweak is named \"widescreen-fmv\", so it is ignored; "
                   f"{TT.the_tweaks(TT.plain_names(), TT.host_names())})\n")


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *CALLS, SA2_TWEAKS="event-bars-all", **{replay: "/dev/null"})
    assert out == GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=event-bars-all ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")


def test_an_unreadable_stack_leaves_the_event_to_the_game_and_says_so_once(exe):
    out, err = run(exe, "unreadable", "50", "unreadable", "51", EXECUTOR, "50", SA2_TWEAKS="event-bars-all")
    assert out == ["50 0 4", "51 0 4", "50 1 4"]
    assert err.splitlines() == [
        "sa2 tweaks: event-bars always (SA2_TWEAKS=event-bars-all)",
        "sa2 tweaks: event-bars always: the guest stack at 0x00090000 does not read, so the caller is "
        "unknown and the event is left to the game",
        "sa2 tweaks: event-bars always: event 50's side bars drawn (the game draws none for it)"]


def test_no_event_bars_is_unchanged_beside_it(exe):
    """With this file linked, no-event-bars alone answers as test_tweaks.py holds it to."""
    out, _ = run(exe, *CALLS, SA2_TWEAKS="no-event-bars")
    assert out == ["361 0 4", "50 0 4", "100 0 4", "409 1 4", "50 0 4", "50 0 4"]


def test_the_two_files_declare_the_hook_alike():
    """no-event-bars.c calls sa2_event_bars_all through its own declaration; it is the definition's."""
    decl = ("void *sa2_event_bars_all(void *state, uint32_t pc, void *memory, "
            "void *(*real)(void *, uint32_t, void *));")
    for name in ("no-event-bars.c", "event-bars-all.c"):
        assert decl in (TT.RUNTIME / "tweaks" / name).read_text(), name
