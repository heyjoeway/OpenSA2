#!/usr/bin/env python3
"""Re-take a recording's native replays with the probes now in the tree.

Subtitle: replay the user's recording again through the hook, at least twice,
with every probe built as the tree builds it now, and say whether the fresh
runs reproduce the recorded run and each other.

    source tools/env.sh
    .venv/bin/python tools/oracle/retake_replays.py RECORDING             # two re-takes
    .venv/bin/python tools/oracle/retake_replays.py RECORDING --count 3   # three
    .venv/bin/python tools/oracle/retake_replays.py RECORDING --dry-run   # print, run nothing
    .venv/bin/python tools/oracle/retake_replays.py RECORDING --rejudge retakes-A.json \
        retakes-B.json --receipt Documentation/receipts/s4-retake-<stage>-item5-v1.json
                                     # judge series already taken under the stage's mask

RECORDING is the capture tools/oracle/record_stage.py made while the user
played (mh-record-<stamp>). The re-takes are captures named
RECORDING-retake<k>-<stamp>, and the verdict is written beside the recording as
retakes-<stamp>.json.

Why it exists
-------------
Stage-route item 5 (Documentation/decisions/2026-09-23-S4-stage-route.md)
registers two native replays of the recording, and what is registered is held
to the tree. The replays the session itself took (record_stage) cannot all be
registered once the tree moves:

  * a change to any byte of a probe's source, or of a header it includes, a
    comment included, leaves them stale to the check that holds a capture to
    that probe -- the tick probe's word log (2026-09-25) is such a change:
    oracle_mode.vanilla_problems for the vanilla probe, native_probe_current for
    the tick probe, native_imports.replay_calls for the import probe, and
    tools/s4/device_profile.py capture_profile for the profile probe.

`record_stage.py --replay-only` cannot take them again once the tree moves. It
replays a recording only while the tree builds the recording's probes (its
changed_files), because it judges each replay against the recording's own tick
capture, which is sound only between runs of one configuration. That refusal
is right for the session and in the way after it.

A new digest mask is not such a change. The digest columns are computed on
the host from the tick probe's word log, with the tree's mask
(tools/oracle/run_oracle_probe.py, its docstring's item 5), and every
configuration here is compared by the headers each probe includes
(record_stage.normal), which the mask is not.

So the re-takes are a configuration of their own: record_stage's probe set as
the tree builds it now (record_stage.command), the input probe replaying the
recording. The import probe writes its log on each re-take's last captured
tick, as in every run record_stage makes (its "What the import log covers"),
and the tick probe its word log: for a re-take that ends where the recording
did, the recording's end tick less one (last_tick), since a run that ends at
tick E captures E - 1 rows. The runner refuses a re-take whose word log is
missing, not whole, or not the tick probe's own row on every tick, and the
summary reports each re-take's log with the time the oracle had been idle
before it and the load average (`word_log`, reported, not judged).

What is compared, and with what
-------------------------------
The re-takes with each other, in full, because they are one configuration:
  * tick_captures_identical: the tick captures are byte-identical. That is the
    first half of item 5 ("reproduce each other's tick captures"), and what
    native_imports.replay_calls needs of every run whose import log is
    registered. When the stage has its own mask (harness/replay/masks/<level>.h,
    the level record_stage spawns) or --mask names one, the captures compared
    are the ones each re-take's kept word log gives with that mask, each log
    first held to the file its run collected and accepted
    (run_oracle_probe.capture_from_log), and the summary names the mask
    (reported.judged_under_mask); the stored captures keep the tree's mask;
  * stage_traces_identical: line for line, `player` included;
  * import_logs_call_one_set: every log names the same called imports, as
    replay_calls requires of a log and its repeats.

Each re-take with the tree, because it must be what the tree builds now:
  * built_as_the_tree_builds_now: every probe's source, the headers it
    includes and its defines, in record_stage.normal's terms, which leave the
    input probe's defines out (record_stage.tree_builds);
  * runs_the_games_own_code: oracle_mode.vanilla_problems, which holds the
    capture to the tree's vanilla probe;
  * device_profile_reproducible: device_profile.capture_profile, the reader a
    registration uses;
  * import_log_at_last_tick: native_imports reads the log whole, counted at
    the tick of the capture's last row.

The re-takes as a registration takes them:
  * import_logs_registrable: tools/s4/native_imports.py replay_calls, the one
    definition a registered replay's import logs are held to, accepts the
    native side a registration of these re-takes would name: the first
    re-take's tick capture, its import log, and every other re-take's log as a
    repeat (the summary's reported.registration). Beyond the checks above, it
    holds each log to the file its run collected and each run to the executable
    tools/pins.json pins.

Each re-take with the recording, only in what no probe's build decides. The
recording is what the user played, and a re-take that leaves it is not a
replay of it:
  * same_oracle_mode_as_recording and same_code_restored_as_recording: one
    mode and one restored code, as record_stage requires of its replays (the
    user's direction of 2026-09-23 refuses comparisons across modes). The mode
    takes in the Mod Loader profile the runner writes and the game's arguments
    (oracle_mode.config_of), so an edit to tools/oracle/run_oracle_probe.py
    profile_for, or to the spawn arguments, after the recording leaves it
    impossible to re-take;
  * same_probe_set_as_recording: the recording's probes, in its load order,
    each built from the same source file. Their sources, headers and defines
    may have moved, since following the tree is what a re-take is for (a
    word log moved the tick probe's source and defines), and
    moved_since_recording names each file that did; a probe added, dropped,
    reordered or taken from another file is another configuration, which no
    re-take of this recording can be;
  * same_device_profile_as_recording: the devices the game saw, by sha256;
  * start_saves_pinned and same_game_environment_as_recording: the saves and
    the environment, as record_stage requires of every replay under option A
    (the user's ruling of 2026-09-24 that the save file is a recorded input,
    Documentation/decisions/2026-09-23-S4-vanilla-oracle.md): the recording
    and every re-take started from the pinned saves (Documentation/save-state.json,
    tools/oracle/save_state.py), as each receipt records them and nothing else
    stands in for (record_stage.start_saves), and every re-take was launched
    with the recording's game environment (session.environment), which native
    heap addresses follow. The saves each run ended with are reported;
  * goal_on_recording_tick: the goal latch rises on the recording's tick.
    With the tick captures identical, that is item 5's second half;
  * no_missed_rows and every_recorded_row_applied: the input probe found a
    recorded row for every call, and applied every recorded row, in order,
    with the recorded values. A replay whose game makes fewer calls in a tick
    skips rows without counting a miss, which only the second check sees;
  * trajectory_as_recorded: the stage trace equals the recording's in every
    column but `player`, which compares only as null or not. It is a heap
    address, and the probe DLLs are not the recording's (the word log's tick
    probe is another DLL, and reserves its buffer), so where the heap lies may
    differ.

The verdict is RETAKE_OK when every check passes, so item 5 holds on the fresh
runs, and RETAKE_DISAGREE naming each failed check and the reasons behind it
otherwise. The summary keeps both, and what record_stage.changed_files says
moved since the recording.

What it does not do
-------------------
  * Allow anything. record_stage passes differences confined to four natively
    unstable digest blocks, pending a stage mask. This tool passes only
    byte-identical re-takes, so before that mask exists it reads
    RETAKE_DISAGREE wherever those blocks differ, and names each block with its
    first tick, as data.
  * Register anything or write a tracked file. The summary names each
    re-take's tick capture, stage trace and import log with their sha256, and
    the native side a registration would name, for a registration to cite.
  * Repair the recording. One that is not a whole capture, records no device
    profile, never reached the goal, or holds input the lifted replay's parser
    refuses, stops the tool before any run, as it stops record_stage. So does
    one whose input, end, stage trace or vanilla log no longer hashes to what
    its run collected: a re-take would replay, or be judged against, another
    file than the one the user's run wrote. So does one that did not start
    from the pinned saves, records no game environment, or was recorded with a
    game environment a run from this shell would not give the game: every
    re-take would be refused, so none runs (record_stage.check_recording's
    rule, which names the variables that differ and never their values).

Rule 11
-------
Every run is record_stage.run: tools/oracle/run_oracle_probe.py launching the
unpacked executable directly, input only through the hook (the input probe
replays the recording, INPUT_MODE=2), the installation proved untouched, and
the live desktop session and the device profile recorded in each receipt. It
is an agent's oracle run: never while the user records. record_stage asks
every agent to stop until its replays finish, and this tool refuses a
recording until its session has written its verdict (replays-<stamp>.json),
because the session takes the oracle's lock one run at a time and a re-take
could take it between two of the session's replays. It runs with the devices
the recording saw, and a run that needs the user's hands (Steam not running,
a device changed) stops with record_stage's reasons. A recording within 15 s
of the runner's cap is re-taken with a warning, as record_stage replays it:
each re-take takes about as long, and one that hits the cap ends the series.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import re
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record_stage as R  # noqa: E402  the probe set, the runner call, native-native checks

ROOT = R.ROOT
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import native_imports as NI  # noqa: E402  the import log's reader, as a registration reads it

DP = R.DP                  # tools/s4/device_profile.py

MIN_COUNT = 2              # stage-route item 5 compares two replays
INPUT = "sa2-input.csv"
APPLIED = "sa2-input-applied.csv"
APPLIED_HEADER = ["tick", "ord", "matched"] + R.FIELDS
END = "sa2-input-end.csv"
TICKS = "sa2-tick-capture.csv"
TRACE = "sa2-stage-trace.csv"
IMPORTS = NI.LOG
PLAYER = "player"
NULL = "0x00000000"        # native_stage_trace.c's null player, as it prints it
# The recording's files a re-take replays or is judged against, each held to
# the sha256 its run collected (the receipt's artifacts) before any re-take.
RECORDING_FILES = (INPUT, END, TRACE, R.OM.VANILLA_LOG)
# The verdict record_stage.session writes on a recording once its replays end
# (replays-<stamp>.json); --replay-only writes one too.
SESSION_VERDICT = "replays-*.json"
# A replay's own digest mask, tick_digest.h's shape (U4's convention,
# tools/s4/snapshot_fields.py mask_header): <level>.h, for the level
# record_stage spawns.
MASKS = ROOT / "harness" / "replay" / "masks"
POLL = 5.0                 # seconds between looks while waiting for an idle oracle


# ------------------------------------------------------------ the plan

def last_tick(end: dict) -> int:
    """The last tick a replay of a recording that ended at `end` captures, when
    it ends where the recording did: the end tick less one, because the input
    probe's ExitProcess on its last tick comes before the tick probe's row
    (record_stage's docstring). The import probe counts its log there, and
    the tick probe writes its word log there."""
    return end["end_tick"] - 1


def stage_mask(given: Path | None = None) -> Path | None:
    """The mask the re-takes' tick captures are judged under: `given`
    (--mask), else the stage's own (MASKS/<level>.h) when it exists, else None,
    the tree's mask, under which the runner stored every capture."""
    if given is not None:
        return Path(given).resolve()
    path = MASKS / f"{R.SPAWN[R.SPAWN.index('--level') + 1]}.h"
    return path if path.is_file() else None


def first_differences(a: str, b: str) -> dict[str, int | str]:
    """column -> the first tick two capture texts differ in it, over their
    common rows; '<rows>' when their row counts differ."""
    ra, rb = list(csv.reader(a.splitlines())), list(csv.reader(b.splitlines()))
    if not ra or not rb or ra[0] != rb[0]:
        return {"<header>": 0}
    first: dict[str, int | str] = {}
    for x, y in zip(ra[1:], rb[1:]):
        for column, u, v in zip(ra[0], x, y):
            if u != v:
                first.setdefault(column, R._tick(x[0]))
    if len(ra) != len(rb):
        first["<rows>"] = min(len(ra), len(rb))
    return first


def wait_for_idle(min_idle: float, max_wait: float, *, clock=time.monotonic,
                  sleep=time.sleep) -> str | None:
    """None once the oracle has been idle `min_idle` seconds by the runner's
    own measure (run_oracle_probe.idle_before_run's `seconds`; unknown is not
    idle enough), or at once without `min_idle`; the reason after `max_wait`
    seconds (the re-take review of 2026-09-25, N4: a series launched each
    re-take seconds after the one before)."""
    start = clock()
    while min_idle:
        idle = R.RP.idle_before_run()["seconds"]
        if idle is not None and idle >= min_idle:
            return None
        if clock() - start >= max_wait:
            return (f"the oracle had been idle {idle} s, not {min_idle:g} s, after waiting "
                    f"{max_wait:g} s")
        sleep(POLL)
    return None


def moved_probes(recording: str) -> list[str]:
    """The probes, by mod id, whose build (record_stage.normal) the tree no
    longer gives as the recording's: moved_since_recording names files, and
    record_stage.py among them for any probe's define (the re-take review of
    2026-09-25, N3)."""
    got = {g[0]: g for g in R.builds(recording)}
    return [w[0] for w in R.tree_builds() if got.get(w[0]) != w]


def retake_names(recording: str, stamp: str, count: int) -> list[str]:
    return [f"{recording}-retake{k + 1}-{stamp}" for k in range(count)]


def header_path(recording: str) -> Path:
    """The replay header, where record_stage.replay writes it: one recording,
    one generated file, whichever tool replays it."""
    return R.SCRIPTS / f"{recording}.h"


def input_defines(recording: str, end: dict) -> list[str]:
    return R.replay_defines(header_path(recording), end)


def commands(recording: str, end: dict, names: list[str]) -> list[list[str]]:
    """The runner call each re-take makes, from record_stage.command, the one
    place record_stage.run builds it."""
    return [R.command(n, input_defines(recording, end), R.REPLAY_CAPTURES, R.CAP,
                      R.LOCK_WAIT) for n in names]


def check_recording(name: str, launching: bool = True) -> tuple[dict | None, list[str]]:
    """The recording's end, and every reason it cannot be re-taken, [] when it
    can: a whole capture (record_stage.accepted), the files a re-take uses as
    its run collected them (RECORDING_FILES), a device profile recorded, its
    session's verdict written (SESSION_VERDICT), the goal reached, a tick
    before its end for the import log, and input the lifted replay's parser
    takes (record_stage.recorded_rows). Collected, not raised, so a dry run
    names them all; a real run stops on any. `launching` False (a re-judge,
    which runs nothing) leaves out the two refusals about a launch from this
    shell: the environment a run from it would give the game, and a recording
    session holding the oracle."""
    d = R.CAPTURES / name
    if not (d / "receipt.json").is_file():
        return None, [f"{name}: no recording by that name in {R.CAPTURES}"]
    try:
        rec = R.receipt(name)
    except ValueError as e:
        return None, [f"{name}: its receipt.json does not parse ({e})"]
    problems = []
    ok, why = R.accepted(rec)
    if not ok:
        problems.append(f"{name} is not a whole capture: {'; '.join(why)}")
    arts = {a.get("file"): a for a in rec.get("artifacts") or []}
    for f in RECORDING_FILES:
        want, got = ((arts.get(f) or {}).get("check") or {}).get("sha256"), R.sha256(d / f)
        if got is None:
            problems.append(f"{name} holds no {f}")
        elif not want:
            problems.append(f"{name}'s receipt records no sha256 for {f}, so the file cannot be "
                            "shown to be the one its run collected")
        elif got != want:
            problems.append(f"{name}/{f} no longer hashes to what its run collected "
                            f"({want[:12]}), so a re-take would replay or be judged against "
                            "another file than the user's run wrote")
    if not R.facts(rec)["device_profile"]:
        problems.append(f"{name} records no device profile, so no re-take can be shown to have "
                        "seen its devices")
    # The recording's own vanilla probe, as the vanilla oracle builds it: no
    # define. An experiment build is a recording record_stage refuses with
    # RecordAgain, and a re-take under today's probe cannot repair it (the
    # re-take review of 2026-09-24, N3).
    vanilla = [b for b in (rec.get("inputs") or {}).get("built_probes") or []
               if isinstance(b, dict) and b.get("mod_id") == "vanilla_probe"]
    if len(vanilla) != 1 or vanilla[0].get("defines"):
        problems.append(f"{name}'s vanilla probe was not built as the vanilla oracle builds it "
                        f"({len(vanilla)} build(s), defines "
                        f"{[b.get('defines') for b in vanilla]}): record again")
    problems += option_a_problems(name, launching)
    if launching:
        problems += live_session_problems()
    if not any(d.glob(SESSION_VERDICT)):
        problems.append(f"{name} holds no {SESSION_VERDICT}: record_stage has written no verdict "
                        "on it, so its session may still be replaying it, and a re-take could "
                        "take the oracle between two of its replays. Wait for the verdict; if the "
                        f"session was stopped, record_stage.py --replay-only {name} writes one")
    end = None
    try:
        end = R.end_of(name)
    except R.Stop as e:
        problems.append(str(e.code))
    if end is not None:
        if not end["goal_tick"]:
            problems.append(f"{name}: the goal latch never rose, so this recording does not "
                            "reach the goal")
        if last_tick(end) < 1:
            problems.append(f"{name} ended at tick {end['end_tick']}, before any tick a replay "
                            "captures, so no tick can hold the import log")
    path = d / INPUT
    if path.is_file():
        try:
            if not R.recorded_rows(path):
                problems.append(f"{path}: no input rows")
        except R.Stop as e:
            problems.append(str(e.code))
    return end, problems


RECORDER = re.compile(r"^[^ ]*python[^ ]* [^ ]*record_stage\.py")
PROC = Path("/proc")


def live_sessions() -> list[int]:
    """The processes running tools/oracle/record_stage.py under a Python
    interpreter, as `pgrep -f '^[^ ]*python[^ ]* [^ ]*record_stage\\.py'` finds
    them; the tests replace it."""
    out = []
    for proc in PROC.glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if RECORDER.match(" ".join(a.decode(errors="replace") for a in argv if a)):
            out.append(int(proc.name))
    return sorted(out)


def live_session_problems() -> list[str]:
    """A recording session running now, any recording's: a re-take holds the
    oracle lock for a whole replay, and that session's preflight and recording
    runs wait only record_stage.LOCK_WAIT for it (the re-take review of
    2026-09-24, N6). [] when none runs."""
    pids = live_sessions()
    return ([f"a recording session is running (record_stage.py, pid {', '.join(map(str, pids))}): "
             "a re-take would hold the oracle lock through its runs; re-take after it exits"]
            if pids else [])


def option_a_problems(name: str, launching: bool = True) -> list[str]:
    """record_stage.check_recording's two option-A refusals, collected: the
    recording started from the pinned saves, as its receipt alone records
    them, and it records a game environment that a run from this shell would
    give the game too (asked only when `launching`). [] when both hold."""
    out = []
    try:
        pin = R.pinned()
    except R.Stop as e:
        return [str(e.code)]
    if not R.starts_pinned(name, pin):
        began = R.start_saves(name)
        out.append(f"{name} did not start from the pinned saves ({R.SS.rel(pin.path)}, "
                   f"{pin.state}): " + ("its receipt records none" if began is None
                                        else f"it started from {began}")
                   + ", and every re-take starts from the pin")
    recorded = R.game_environment(name)
    if recorded is None:
        out.append(f"{name}'s receipt records no game environment, so no re-take can be shown "
                   "to have been launched with the same one")
        return out
    if not launching:
        return out
    here = R.launch_environment()
    if R.RP.environment_record(here)["sha256"] != recorded:
        was = (R.receipt(name).get("session") or {}).get("game_environment")
        differ = (sorted(k for k in set(was) | set(here) if was.get(k) != here.get(k))
                  if isinstance(was, dict) else ["unknown: the receipt keeps only the digest"])
        out.append(f"{name} was recorded with another game environment than a run from this "
                   f"shell would get ({', '.join(differ)} differ). Native heap addresses follow "
                   "it, so every re-take would be refused. Run this again from a shell where "
                   "those variables are what the recording's receipt records "
                   "(session.game_environment)"
                   + ("; for XAUTHORITY, set it to this login's X authority file: the runner "
                      "gives the game its link" if "XAUTHORITY" in differ else ""))
    return out


def probe_set(builds: list[tuple]) -> list[tuple]:
    """record_stage.normal()'s tuples less what a re-take may move (the
    source's and headers' hashes, and the defines): which probes, from which
    source file, in load order."""
    return [(b[0], b[1]) for b in builds]


def cap_note(name: str) -> str | None:
    """record_stage.cap_warning's test, worded for re-takes: a recording within
    15 s of the runner's cap leaves each re-take, which takes about as long,
    almost no room. A warning, as record_stage gives it before its own replays,
    not a refusal: the session's replays ran under the same cap."""
    rec = R.receipt(name)
    if not R.cap_warning(rec):
        return None
    took = (rec.get("run") or {}).get("elapsed_seconds") or 0
    return (f"WARNING: {name} ran {took:.0f} s of the runner's {R.CAP} s cap, and each re-take "
            "takes about as long; one that stops at the cap (exit 124) ends the series.")


def moved(recording: str) -> list[str] | str:
    """What record_stage.changed_files says moved since the recording: the
    reason to re-take rather than --replay-only. A receipt it cannot read comes
    back as the reason."""
    try:
        return R.changed_files(recording)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return f"unreadable ({e!r})"


# ------------------------------------------------------------ the runs

def retake(recording: str, end: dict, names: list[str], min_idle: float = 0.0,
           max_wait: float = 1800.0) -> list[dict]:
    """Each re-take in turn, each launched once the oracle has been idle
    `min_idle` seconds (wait_for_idle). A run the runner does not accept is kept
    with its reason and ends the series, since the verdict needs every
    re-take; nothing here raises, so the summary is always written
    (record_stage.replay's rule)."""
    info = R.script(R.CAPTURES / recording / INPUT, header_path(recording))
    out = []
    for k, name in enumerate(names):
        print(f"Re-take {k + 1} of {len(names)}...")
        item: dict = {"name": name, "error": None, "script": info}
        why = wait_for_idle(min_idle, max_wait)
        if why:
            print(why)
            item["error"] = why
            out.append(item)
            break
        live = live_session_problems()
        if live:                        # a session started after the series did
            print(live[0])
            item["error"] = live[0]
            out.append(item)
            break
        try:
            item.update(R.facts(R.run(name, input_defines(recording, end), R.REPLAY_CAPTURES,
                                      R.CAP, lock_timeout=R.LOCK_WAIT)))
            item["end"] = R.end_of(name)
        except R.Stop as e:
            print(e)
            item["error"] = str(e)
            out.append(item)
            break
        out.append(item)
    return out


# ------------------------------------------------------------ the checks

def slot_table() -> dict[int, str]:
    """The executable's import slots, as tools/s4/native_imports.py reads them;
    the tests put made-up slots here."""
    return NI.slots()


def import_log(name: str) -> dict:
    """A re-take's import log as a registration reads it
    (native_imports.read_at), and `problem` when it is not whole or not counted
    at the tick of the capture's last row."""
    path = R.CAPTURES / name / IMPORTS
    _, rows = R.tick_columns(name)
    out: dict = {"file": DP.rel(path, ROOT), "sha256": R.sha256(path), "counted_at_tick": None,
                 "tick_rows": len(rows), "called": None, "problem": None}
    if not path.is_file():
        out["problem"] = f"{name} holds no {IMPORTS}"
        return out
    try:
        counts, at = NI.read_at(path, slot_table())
    except SystemExit as e:
        out["problem"] = str(e.code)
        return out
    except ValueError as e:
        # A slot, count or tick that is not a number: read_at's int() raises it
        # before its own refusals, and the runner's capture check passes such a
        # row, which has the probe's four fields.
        out["problem"] = f"{path}: a field is not a number, so it is not the probe's log ({e})"
        return out
    out["counted_at_tick"] = at
    out["called"] = sorted(k for k, v in counts.items() if v)
    if at != len(rows):
        out["problem"] = (f"{name}/{IMPORTS} was counted at tick {at}, and its tick capture "
                          f"holds {len(rows)} ticks")
    return out


def registration(recording: str, names: list[str]) -> tuple[dict, str | None]:
    """The native side a registration of the re-takes `names` would name, in
    Documentation/replays.json's terms: the first re-take's tick capture and
    import log, the others' as repeats. With it, native_imports.replay_calls'
    refusal of it, or None when it takes it, so the verdict holds the import
    logs to the one definition a registration is held to."""
    def ran(n: str, file: str) -> dict:
        path = R.CAPTURES / n / file
        return {"file": DP.rel(path, ROOT), "sha256": R.sha256(path),
                "receipt": DP.rel(R.CAPTURES / n / "receipt.json", ROOT)}

    ticks = [ran(n, TICKS) for n in names]
    logs = [ran(n, IMPORTS) for n in names]
    entry = {"name": recording, "native": {
        "capture": ticks[0]["file"], "sha256": ticks[0]["sha256"], "receipt": ticks[0]["receipt"],
        "repeats": [{"capture": t["file"], "sha256": t["sha256"], "receipt": t["receipt"]}
                    for t in ticks[1:]],
        "imports": {"log": logs[0]["file"], "sha256": logs[0]["sha256"],
                    "receipt": logs[0]["receipt"],
                    "repeats": [{"log": g["file"], "sha256": g["sha256"], "receipt": g["receipt"]}
                                for g in logs[1:]]}}}
    try:
        NI.replay_calls(entry, slot_table())
    except SystemExit as e:
        return entry, f"native_imports.replay_calls refuses the re-takes' registration: {e.code}"
    except (OSError, ValueError, KeyError, TypeError) as e:
        return entry, f"native_imports.replay_calls could not read the re-takes: {e!r}"
    return entry, None


def applied_problem(recording: str, name: str) -> str | None:
    """None when `name` applied every row of the recording, in order, with the
    recorded values; else the first place it did not. A call that found no row
    (matched 0) is no_missed_rows' to count, not this."""
    want = R.recorded_rows(R.CAPTURES / recording / INPUT)
    path = R.CAPTURES / name / APPLIED
    if not path.is_file():
        return f"{name} holds no {APPLIED}"
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != APPLIED_HEADER:
        return f"{name}/{APPLIED}: the header is not {','.join(APPLIED_HEADER)}"
    got = []
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != len(APPLIED_HEADER) or r[2] not in ("0", "1"):
            return f"{name}/{APPLIED}:{n}: not a row the input probe writes"
        if r[2] == "1":
            try:
                got.append([int(r[0]), int(r[1])] + [int(v, 16) for v in r[3:]])
            except ValueError:
                return f"{name}/{APPLIED}:{n}: not a row the input probe writes"
    for k, (a, b) in enumerate(zip(want, got)):
        if a != b:
            return (f"{name} applied tick {b[0]} call {b[1]} where the recording's row {k + 1} "
                    f"is tick {a[0]} call {a[1]}" if a[:2] != b[:2] else
                    f"{name} applied other values than the recording's at tick {a[0]} call {a[1]}")
    if len(got) != len(want):
        return f"{name} applied {len(got)} recorded rows, and the recording holds {len(want)}"
    return None


def trajectory_problem(recording: str, name: str) -> str | None:
    """None when `name`'s stage trace follows the recording's: the same header,
    as many rows, and every column equal but `player`, which is compared only
    as null or not (module docstring); else the first tick it leaves it."""
    want, got = R.trace_lines(recording), R.trace_lines(name)
    if len(want) < 2:
        return f"{recording} holds no stage trace rows to follow"
    if not got or got[0] != want[0]:
        return f"{name}'s stage trace header is not the recording's, so no row can be compared"
    cols = want[0].split(",")
    p = cols.index(PLAYER) if PLAYER in cols else None
    for a, b in zip(want[1:], got[1:]):
        x, y = a.split(","), b.split(",")
        if p is not None and len(x) == len(y) == len(cols):
            x[p], y[p] = str(x[p] == NULL), str(y[p] == NULL)
        if x != y:
            col = next((c for c, u, v in zip(cols, x, y) if u != v), "row width")
            return f"{name} leaves the recorded run at tick {a.split(',')[0]} ({col})"
    if len(got) != len(want):
        return (f"{name}'s stage trace holds {len(got) - 1} rows and the recording's "
                f"{len(want) - 1}")
    return None


def identity_columns() -> set[str]:
    """The tick columns the boot replay's field policy compares by which
    allocation they name (Documentation/replays.json): heap addresses, which
    follow the probe DLLs' sizes as the stage trace's player does."""
    doc = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    boot = next((e for e in doc["replays"] if e["name"] == "boot"), {})
    return {k for k, v in (boot.get("field_policy") or {}).items()
            if v.get("compare") == "identity"}


def named_problem(recording: str, name: str) -> str | None:
    """None when `name`'s tick capture holds the recording's rows and agrees with
    it in every named column but the identity ones; else the first place it does
    not. Digest columns are left to the re-takes: a moved mask moves them."""
    rh, rr = R.tick_columns(recording)
    oh, orows = R.tick_columns(name)
    if len(rr) != len(orows):
        return f"{name}'s tick capture holds {len(orows)} rows and {recording}'s {len(rr)}"
    skip = identity_columns()
    cols = [c for c in rh[1:] if not c.startswith("d_") and c not in skip]
    missing = [c for c in cols if c not in oh]
    if missing:
        return f"{name}'s tick capture has no {', '.join(missing)}"
    ia, ib = [rh.index(c) for c in cols], [oh.index(c) for c in cols]
    for a, b in zip(rr, orows):
        for c, i, j in zip(cols, ia, ib):
            if a[i] != b[j]:
                return f"{name} leaves the recorded run at tick {a[0]} ({c})"
    return None


def profile_problems(name: str) -> list[str]:
    """device_profile.capture_profile's refusal of a re-take, [] when it takes
    it: the reader a registration of the re-take would use."""
    try:
        DP.capture_profile(R.CAPTURES / name, ROOT)
    except (DP.Refusal, ValueError, OSError) as e:
        return [str(e)]
    return []


def mode_problem(recording: str, name: str) -> str | None:
    """oracle_mode.require_same's refusal of the recording and a re-take as one
    mode, as record_stage.same_mode asks it, with the reason kept."""
    try:
        R.OM.require_same([R.CAPTURES / recording, R.CAPTURES / name])
    except SystemExit as e:
        return str(e.code)
    return None


def build_problem(name: str, want: list[tuple]) -> str | None:
    """Which probes a re-take was not built with as the tree builds them now
    (record_stage.normal's terms), or None."""
    got = R.builds(name)
    if got == want:
        return None
    if [g[0] for g in got] != [w[0] for w in want]:
        return (f"{name} built the probes {[g[0] for g in got]}, and the tree builds "
                f"{[w[0] for w in want]}")
    return (f"{name} was not built as the tree builds now: "
            + ", ".join(str(g[0]) for g, w in zip(got, want) if g != w))


def examine(recording: str, name: str, want_builds: list[tuple], ranges: frozenset) -> dict:
    """What the checks need of one re-take, read once."""
    return {
        "vanilla_problems": R.vanilla_problems(name),
        "profile_problems": profile_problems(name),
        "build": build_problem(name, want_builds),
        "mode": mode_problem(recording, name),
        "same_code": bool(ranges) and R.vanilla_rows(name) == ranges,
        "applied": applied_problem(recording, name),
        "trajectory": trajectory_problem(recording, name),
        "named": named_problem(recording, name),
        "import_log": import_log(name),
        "tick_capture": {"file": DP.rel(R.CAPTURES / name / TICKS, ROOT),
                         "sha256": R.sha256(R.CAPTURES / name / TICKS)},
        "stage_trace": {"file": DP.rel(R.CAPTURES / name / TRACE, ROOT),
                        "sha256": R.sha256(R.CAPTURES / name / TRACE)},
        "receipt": DP.rel(R.CAPTURES / name / "receipt.json", ROOT),
        "word_log": word_log(name),
    }


def word_log(name: str) -> dict | None:
    """What the runner recorded of a re-take's word log (the file kept, the
    log's sha256, its ticks and end) and of the machine before its launch:
    reported, not judged, since the runner refuses a re-take whose log it does
    not accept. Null for a receipt from before the word log."""
    facts = R.facts(R.receipt(name))
    log = facts["word_log"]
    if log is None:
        return None
    kept = R.CAPTURES / name / str(log.get("stored_as"))
    return {**log, "file": DP.rel(kept, ROOT), "file_sha256": R.sha256(kept),
            "idle_before_run_s": facts["idle_before_run_s"],
            "load_average_at_launch": facts["load_average_at_launch"]}


def judge(recording: str, end: dict, reps: list[dict], count: int,
          mask: Path | None = None) -> dict:
    """Every check (module docstring), what is reported beside them, and the
    reason behind every failed check. With `mask` (stage_mask), the tick
    captures compared are the ones the re-takes' kept word logs give with it."""
    devices = R.facts(R.receipt(recording))["device_profile"]
    ran = [r for r in reps if not r.get("error")]
    complete = len(ran) == count
    ranges = R.vanilla_rows(recording)
    want_builds = R.tree_builds()
    recorded_set = probe_set(R.builds(recording))
    try:
        pin = R.pinned()
    except R.Stop:
        pin = None                   # no pin matches nothing, and the summary is still written
    environment = R.game_environment(recording)
    per = {r["name"]: examine(recording, r["name"], want_builds, ranges) for r in ran}
    entry, refused = (registration(recording, [r["name"] for r in ran]) if complete
                      else (None, None))

    def every(test) -> bool:
        return bool(ran) and all(test(r, per[r["name"]]) for r in ran)

    def one(values: list) -> bool:
        return complete and None not in values and len(set(values)) == 1

    called = [None if p["import_log"]["called"] is None else tuple(p["import_log"]["called"])
              for p in per.values()]
    traces = [p["stage_trace"]["sha256"] for p in per.values()]
    ticks = [p["tick_capture"]["sha256"] for p in per.values()]
    under, texts, mask_reasons = None, {}, []
    if mask is not None:
        for r in ran:
            n = r["name"]
            try:
                texts[n] = R.RP.capture_from_log(R.CAPTURES / n, mask)
                if texts[n] is None:
                    mask_reasons.append(f"{n} kept no word log, so it has no capture under "
                                        f"{DP.rel(mask, ROOT)}")
            except (SystemExit, OSError, ValueError) as e:
                texts[n] = None
                mask_reasons.append(f"{n}: no capture under {DP.rel(mask, ROOT)} from its word "
                                    f"log: {getattr(e, 'code', None) or e}")
        ticks = [None if texts[r["name"]] is None
                 else hashlib.sha256(texts[r["name"]].encode()).hexdigest() for r in ran]
        under = {"mask": DP.rel(mask, ROOT), "sha256": R.sha256(mask),
                 "tick_sha256": {r["name"]: t for r, t in zip(ran, ticks)},
                 # Only the stage's own mask stands for item 5; a header given by
                 # hand is judged under but cannot meet it (the fold review, W1).
                 "source": "stage" if mask.resolve() == (
                     MASKS / f"{R.SPAWN[R.SPAWN.index('--level') + 1]}.h").resolve()
                 else "given"}
    checks = {
        "every_retake_ran": complete,
        "runs_the_games_own_code": every(lambda r, p: not p["vanilla_problems"]),
        "built_as_the_tree_builds_now": every(lambda r, p: p["build"] is None),
        "device_profile_reproducible": every(lambda r, p: not p["profile_problems"]),
        "same_oracle_mode_as_recording": every(lambda r, p: p["mode"] is None),
        "same_code_restored_as_recording": every(lambda r, p: p["same_code"]),
        "same_probe_set_as_recording": bool(recorded_set) and every(
            lambda r, p: probe_set(R.builds(r["name"])) == recorded_set),
        "same_device_profile_as_recording": bool(ran) and R.same_devices(devices, ran,
                                                                          recording),
        "start_saves_pinned": R.starts_pinned(recording, pin) and every(
            lambda r, p: R.starts_pinned(r["name"], pin)),
        "same_game_environment_as_recording": environment is not None and every(
            lambda r, p: R.game_environment(r["name"]) == environment),
        "goal_on_recording_tick": every(lambda r, p: r["end"]["goal_tick"] == end["goal_tick"]),
        "no_missed_rows": every(lambda r, p: r["end"]["misses"] == 0),
        "every_recorded_row_applied": every(lambda r, p: p["applied"] is None),
        "trajectory_as_recorded": every(lambda r, p: p["trajectory"] is None),
        "named_columns_as_recorded": every(lambda r, p: p["named"] is None),
        "import_log_at_last_tick": every(lambda r, p: p["import_log"]["problem"] is None),
        "import_logs_call_one_set": one(called),
        "stage_traces_identical": one(traces),
        "tick_captures_identical": one(ticks),
        "import_logs_registrable": complete and refused is None,
    }
    reasons = [r["error"] for r in reps if r.get("error")] + mask_reasons
    if pin is None:
        reasons.append(f"the pinned saves ({R.PIN}) cannot be read, so no run is shown to have "
                       "started from them")
    elif not R.starts_pinned(recording, pin):
        reasons.append(f"{recording} did not start from the pinned saves (its receipt records "
                       f"{R.start_saves(recording)})")
    if environment is None:
        reasons.append(f"{recording}'s receipt records no game environment")
    for r in ran:
        n, p, e = r["name"], per[r["name"]], r["end"]
        reasons += [f"{n}: {x}" for x in p["vanilla_problems"] + p["profile_problems"]]
        reasons += [x for x in (p["build"], p["mode"]) if x]
        if recorded_set and probe_set(R.builds(n)) != recorded_set:
            reasons.append(f"{n} loaded another probe set than {recording} (which probes, "
                           "in which order, from which source files), so it is another "
                           "configuration")
        if pin is not None and not R.starts_pinned(n, pin):
            began = R.start_saves(n)
            reasons.append(f"{n} did not start from the pinned saves: "
                           + ("its receipt records none" if began is None
                              else f"it started from {began}"))
        if environment is not None and R.game_environment(n) != environment:
            reasons.append(f"{n} was launched with game environment "
                           f"{str(R.game_environment(n))[:12]}, and {recording} with "
                           f"{environment[:12]}")
        if not p["same_code"]:
            reasons.append(f"{n} restored other code than {recording} did (their "
                           f"{R.OM.VANILLA_LOG} rows differ, or hold none)")
        if e["goal_tick"] != end["goal_tick"]:
            reasons.append(f"{n}: the goal latch rose at tick {e['goal_tick']}, and in "
                           f"{recording} at tick {end['goal_tick']}")
        if e["misses"]:
            reasons.append(f"{n}: {e['misses']} input call(s) found no recorded row")
        reasons += [x for x in (p["applied"], p["trajectory"], p["named"],
                                p["import_log"]["problem"]) if x]
    # The device check's own reasons (record_stage.device_differences): a
    # re-take on a newer probe holds the recording's sections line for line and
    # the added ones to the other re-takes, so a whole-sha256 comparison here
    # would give every such re-take a reason the check does not find.
    reasons += R.device_differences(devices, ran, recording)
    if complete and None not in called and len(set(called)) > 1:
        reasons.append("the re-takes' import logs call different sets of imports")
    if complete and len(set(traces)) > 1:
        reasons.append("the re-takes' stage traces differ")
    first = ran[0]["name"] if ran else None
    among = {r["name"]: R.compare_to(first, r["name"]) for r in ran[1:]}
    said = 0
    differ_under = {k: first_differences(texts[first], texts[k]) for k in among
                    if texts.get(first) is not None and texts.get(k) is not None}
    for k, a in among.items():
        named, blocks = a["named_columns_differ"], a["digest_blocks_differ"]
        where = ""
        if under is not None and k in differ_under:
            named = {c: t for c, t in differ_under[k].items() if not c.startswith("d_")}
            blocks = {c: t for c, t in differ_under[k].items() if c.startswith("d_")}
            where = f" under {under['mask']}"
        parts = [f"the {what} " + ", ".join(f"{c} from tick {t}" for c, t in sorted(cols.items()))
                 for what, cols in (("named columns", named), ("digest blocks", blocks)) if cols]
        if under is None and not a["masks_same"]:
            # Two masks' digests: the reason is the masks, and no block is
            # named as differing (the re-take review of 2026-09-25, N2).
            reasons.append(f"{k}'s digest columns were built with another mask than {first}'s, "
                           f"so none compares ({a['digests']})"
                           + (f"; it differs in {parts[0]}" if named else ""))
        elif parts:
            reasons.append(f"{k} differs from {first}{where} in " + "; ".join(parts))
        elif not (a["headers_same"] and a["rows_same"] and a["columns_same"]
                  and a["masks_same"]):
            reasons.append(f"{k}'s tick capture is not comparable with {first}'s (same probes "
                           f"{a['headers_same']}, rows {a['tick_rows']}, same columns "
                           f"{a['columns_same']}, digests built with one mask "
                           f"{a['masks_same']})")
        else:
            continue
        said += 1
    if complete and not checks["tick_captures_identical"] and not said:
        reasons.append("the re-takes' tick captures are not byte-identical")
    if refused:
        reasons.append(refused)
    goals = {r["end"]["goal_tick"] for r in ran}
    reported = {
        # Documentation/decisions/2026-09-23-S4-stage-route.md item 5's words,
        # on the fresh runs: they "reproduce each other's tick captures" and set
        # the goal latch on the same tick.
        "stage_route_item_5_met": (checks["tick_captures_identical"] and len(goals) == 1
                                   and 0 not in goals
                                   and (under is None or under.get("source") == "stage")),
        "compared_with": first,
        "digest_blocks_differ": {k: a["digest_blocks_differ"] for k, a in among.items()},
        "masks_same": {k: a["masks_same"] for k, a in among.items()},
        # The mask tick_captures_identical was judged under, when not the
        # tree's (stage_mask), with each re-take's capture under it.
        "judged_under_mask": under,
        "digest_blocks_differ_under_mask": (
            None if under is None else {k: {c: t for c, t in d.items() if c.startswith("d_")}
                                        for k, d in differ_under.items()}),
        "named_columns_differ": {k: a["named_columns_differ"] for k, a in among.items()},
        "teardown_exits": [r["name"] for r in ran if r.get("teardown_exit")],
        "elapsed_seconds": {r["name"]: r.get("elapsed_seconds") for r in ran},
        # What each run's saves were at its end, as its receipt records them:
        # reported, not judged, since what the game writes is the run's own
        # doing (record_stage.judge's rule).
        "saves_at_end": {k: R.end_saves(k) for k in [recording] + [r["name"] for r in ran]},
        # What a registration of these re-takes would name as the replay's
        # native side (import_logs_registrable); null unless every re-take ran.
        "registration": entry,
    }
    return {"checks": checks, "reported": reported, "reasons": reasons, "retakes": per}


# ------------------------------------------------------------ the tool

def dry_run(recording: str, count: int, stamp: str) -> int:
    """The recording's checks and each re-take's runner command, printed; no
    run, no file written. Exit 1 when a real run would stop before running."""
    end, problems = check_recording(recording)
    print(f"RETAKE_DRY_RUN {recording}: {count} re-takes; nothing runs and nothing is written.")
    if end is not None:
        print(f"recording: goal at tick {end['goal_tick']}, ended at tick {end['end_tick']}")
        m = moved(recording)
        if isinstance(m, str):
            said = m
        elif m:
            said = ", ".join(m)
        elif R.predates_word_log(recording):
            said = "nothing, but it predates the word log, so only a re-take replays it"
        else:
            said = "nothing (record_stage.py --replay-only also replays it)"
        print(f"moved since the recording: {said}")
        print(f"probes whose build moved: {', '.join(moved_probes(recording)) or 'none'}")
        note = cap_note(recording)
        if note:
            print(note)
    if end is not None and last_tick(end) >= 1:
        print(f"import log: counted on each re-take's last captured tick, {last_tick(end)} "
              "for one that ends where the recording did")
        print(f"replay header: {header_path(recording)}, generated from "
              f"{R.CAPTURES / recording / INPUT} when the re-takes run")
        print(f"runner commands, from {ROOT} (the names carry this call's time stamp):")
        for k, cmd in enumerate(commands(recording, end, retake_names(recording, stamp, count)),
                                start=1):
            print(f"  re-take {k} of {count}: {shlex.join(cmd)}")
    if problems:
        print("A real run stops before any re-take:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    return 0


def session(recording: str, count: int, stamp: str, mask: Path | None = None,
            min_idle: float = 0.0, max_wait: float = 1800.0) -> int:
    end, problems = check_recording(recording)
    if problems:
        raise R.Stop(f"{recording} cannot be re-taken:\n"
                     + "\n".join(f"  - {p}" for p in problems))
    what_moved = moved(recording)
    print(f"recording {recording}: goal at tick {end['goal_tick']}, ended at tick "
          f"{end['end_tick']}; each import log counted on its re-take's last captured tick")
    note = cap_note(recording)
    if note:
        print(note)
    mask = stage_mask(mask)
    if mask is not None:
        print(f"tick captures judged under {DP.rel(mask, ROOT)} ({R.sha256(mask)[:12]}), from "
              "each re-take's word log")
    reps = retake(recording, end, retake_names(recording, stamp, count), min_idle, max_wait)
    result = judge(recording, end, reps, count, mask)
    checks, reported = result["checks"], result["reported"]
    ok = all(checks.values())
    idle = {r["name"]: R.facts(R.receipt(r["name"]))["idle_before_run_s"]
            for r in reps if not r.get("error")}
    summary = {"recording": recording, "recording_run": R.facts(R.receipt(recording)),
               "recording_end": end, "moved_since_recording": what_moved,
               "moved_probes": moved_probes(recording), "cap_warning": note,
               # Reported, not judged: whether each re-take launched as idle as
               # asked, by its receipt (a race at the lease can make it less).
               "min_idle": min_idle, "min_idle_met": None if not min_idle else {
                   k: v is not None and v >= min_idle for k, v in idle.items()},
               "import_tick": last_tick(end), "count": count, "runs": reps,
               "retakes": result["retakes"], "checks": checks, "reported": reported,
               "reasons": result["reasons"],
               "verdict": "RETAKE_OK" if ok else "RETAKE_DISAGREE"}
    out = R.CAPTURES / recording / f"retakes-{stamp}.json"
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(checks, indent=1))
    print(f"summary: {out}")
    if ok:
        mask_said = (f" under the digest mask {reported['judged_under_mask']['mask']}"
                     if reported.get("judged_under_mask") else "")
        print(f"RETAKE_OK: {count} re-takes of {recording} are byte-identical to each other"
              f"{mask_said} and follow the recorded run to the goal on tick "
              f"{end['goal_tick']}; stage-route item 5 holds on them.")
        return 0
    print("RETAKE_DISAGREE: " + ", ".join(k for k, v in checks.items() if not v))
    for reason in result["reasons"]:
        print(f"  {reason}")
    return 1


REJUDGE_SCHEMA = "sa2-retake-rejudge/v1"


def rejudge(recording: str, summaries: list[str], stamp: str, mask: Path | None = None,
            receipt: Path | None = None, argv: list[str] | None = None) -> int:
    """Judge again, launching nothing, re-takes this tool already took: their
    run records read back from the summaries it wrote (in the order given, each
    series in the order its runs were taken), judged by `judge` under the
    stage's own mask. Every stage's mask is chosen from its re-takes, so it
    exists only after them, and this is the step that judges item 5 under it
    (the U6 registration review, W2; rule 13). The recording is checked as a
    re-take checks it, less the two refusals about a launch from this shell
    (check_recording, `launching`). Writes rejudge-<stamp>.json beside the
    recording, and with `receipt` a tracked receipt: the summaries and their
    sha256, the mask and its sha256, every run's word log and its sha256, the
    command, the checks, the mask each capture was judged under and whether
    item 5 is met."""
    end, problems = check_recording(recording, launching=False)
    if problems:
        raise R.Stop(f"{recording} cannot be judged:\n" + "\n".join(f"  - {p}" for p in problems))
    mask = stage_mask(mask)
    if mask is None:
        raise R.Stop(f"no stage mask ({DP.rel(MASKS, ROOT)}/<level>.h) and no --mask: a re-judge "
                     "judges re-takes under the mask chosen from them")
    reps: list[dict] = []
    read: list[dict] = []
    for s in summaries:
        path = Path(s)
        if not path.is_file():
            path = R.CAPTURES / recording / s
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise R.Stop(f"{s}: not a summary this tool wrote ({e})") from None
        runs = doc.get("runs") if isinstance(doc, dict) else None
        if not isinstance(runs, list) or not runs or doc.get("recording") != recording \
                or "rejudged_from" in doc:
            raise R.Stop(f"{s} is not a series of re-takes of {recording} (a re-judge's own "
                         "summary is not one)")
        bad = [r.get("name") for r in runs if r.get("error")]
        if bad:
            raise R.Stop(f"{s}: {', '.join(map(str, bad))} did not run to the end; judge only "
                         "whole series")
        reps += runs
        read.append({"summary": DP.rel(path, ROOT), "sha256": R.sha256(path),
                     "verdict": doc.get("verdict"), "runs": [r["name"] for r in runs]})
    names = [r["name"] for r in reps]
    if len(set(names)) != len(names):
        raise R.Stop(f"a re-take is named twice among {names}")
    result = judge(recording, end, reps, len(reps), mask)
    checks, reported = result["checks"], result["reported"]
    ok = all(checks.values())
    logs = {}
    for n in names:
        log = word_log(n) or {}
        logs[n] = {"file": log.get("file"), "sha256": log.get("file_sha256")}
    summary = {"schema": REJUDGE_SCHEMA, "recording": recording,
               "command": ["tools/oracle/retake_replays.py"] + list(argv or []),
               "rejudged_from": read, "recording_end": end, "count": len(reps),
               "mask": {"path": DP.rel(mask, ROOT), "sha256": R.sha256(mask)},
               "word_logs": logs, "runs": reps, "retakes": result["retakes"],
               "checks": checks, "reported": reported, "reasons": result["reasons"],
               "judged_under_mask": reported.get("judged_under_mask"),
               "stage_route_item_5_met": reported.get("stage_route_item_5_met"),
               "goal_ticks": {r["name"]: r["end"]["goal_tick"] for r in reps},
               "verdict": "RETAKE_OK" if ok else "RETAKE_DISAGREE"}
    out = R.CAPTURES / recording / f"rejudge-{stamp}.json"
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(checks, indent=1))
    print(f"summary: {out}")
    if receipt is not None:
        doc = {k: v for k, v in summary.items() if k not in ("runs", "retakes")}
        doc["summary"] = {"path": DP.rel(out, ROOT), "sha256": R.sha256(out)}
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"receipt: {receipt}")
    print(f"item 5 under {summary['mask']['path']}: "
          f"{'met' if summary['stage_route_item_5_met'] else 'NOT met'}")
    print(("RETAKE_OK" if ok else "RETAKE_DISAGREE: " + ", ".join(k for k, v in checks.items()
                                                                  if not v)))
    for reason in result["reasons"]:
        print(f"  {reason}")
    return 0 if summary["stage_route_item_5_met"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", metavar="RECORDING",
                    help="the capture record_stage.py made while the user played")
    ap.add_argument("--count", type=int, default=MIN_COUNT, metavar="N",
                    help=f"how many re-takes, at least {MIN_COUNT} (stage-route item 5)")
    ap.add_argument("--dry-run", action="store_true",
                    help="check the recording and print each re-take's runner command; nothing "
                         "runs and nothing is written (exit 1 when a real run would stop)")
    ap.add_argument("--mask", type=Path, default=None, metavar="HEADER",
                    help="judge the re-takes' tick captures under this mask, built from their "
                         "word logs (default: the stage's own, harness/replay/masks/<level>.h, "
                         "when it exists, else the stored captures)")
    ap.add_argument("--min-idle", type=float, default=0.0, metavar="SECONDS",
                    help="launch each re-take only once the oracle has been idle this long "
                         "(session.idle_before_run)")
    ap.add_argument("--max-wait", type=float, default=1800.0, metavar="SECONDS",
                    help="how long to wait for that before ending the series (default 1800)")
    ap.add_argument("--rejudge", nargs="+", metavar="SUMMARY", default=None,
                    help="launch nothing: judge again, under the stage's own mask (or --mask), "
                         "the re-takes these summaries (retakes-<stamp>.json, beside the "
                         "recording) name, and write rejudge-<stamp>.json (rejudge)")
    ap.add_argument("--receipt", type=Path, default=None, metavar="JSON",
                    help="with --rejudge, also write this tracked receipt")
    args = ap.parse_args(argv)
    if args.receipt is not None and args.rejudge is None:
        ap.error("--receipt is written by --rejudge")
    if args.mask is not None and not args.mask.is_file():
        ap.error(f"--mask {args.mask}: no such file")
    if args.count < MIN_COUNT:
        ap.error(f"--count must be at least {MIN_COUNT}: item 5 compares two replays")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        if args.rejudge is not None:
            return rejudge(args.recording, args.rejudge, stamp, args.mask, args.receipt,
                           list(argv if argv is not None else sys.argv[1:]))
        if args.dry_run:
            return dry_run(args.recording, args.count, stamp)
        return session(args.recording, args.count, stamp, args.mask, args.min_idle,
                       args.max_wait)
    except R.Stop as e:
        print(f"\nSTOPPED: {e.code}")
        return 1
    except KeyboardInterrupt:
        print("\nSTOPPED: interrupted; the re-takes already taken are kept.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
