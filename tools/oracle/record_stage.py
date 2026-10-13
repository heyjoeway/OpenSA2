#!/usr/bin/env python3
"""Record one human run of Metal Harbor, then show the recording replays.

Subtitle: the one command for the play session M6 needs. It checks the game
unattended, starts it at Metal Harbor through the Mod Loader's test spawn,
records the controller state the game reads while the user plays to the goal
ring, and then replays that recording twice with nobody touching anything,
requiring each replay to reach the goal on the recording's tick and to match
the recording, with one named allowance (below) that the verdict reports.

    source tools/env.sh
    .venv/bin/python tools/oracle/record_stage.py                      # check, record, replay twice
    .venv/bin/python tools/oracle/record_stage.py --replay-only NAME   # replay a recording again
    .venv/bin/python tools/oracle/record_stage.py --preflight-only     # the unattended check alone

Why the pieces are what they are is `Documentation/decisions/2026-09-23-S4-stage-route.md`:
test spawn (`-- --level metalharbor --character sonic`, no `--position`), the
runner's profile, the keyboard rather than a pad (the pad/keyboard mode
selects different files at load), and input recorded and replayed at the raw
controller level (harness/replay/native_input_probe.c). The profile has every
game patch off except the Mod Loader's frame limiter, and StretchToWindow on,
since the evening of 2026-09-23: without that the picture was black and no one
could see to play. The user adopted it on 2026-09-24 as option A of the
vanilla decision's addendum (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md),
with the vanilla probe keeping the BeginScene wrapper alone.

The saves and the environment every run starts from
---------------------------------------------------
The save file is a recorded input (the user's ruling of 2026-09-24). The
runner refuses to start unless the staged installation's saves are the pinned
start state, Documentation/save-state.json (tools/oracle/save_state.py), and
each receipt records the saves the run started from (session.saves). The
verdict requires the recording and every replay to have started from the pin
(start_saves_pinned), and the preflight both passes, as their receipts record
it: a run whose receipt records no start saves has not shown it, and no
snapshot stands in for the record (start_saves). What each run ended
with, the save the pickup writes among it, is reported. The runner also
launches the game with a fixed environment (run_oracle_probe.game_environment,
XAUTHORITY given as a link whose name does not change at the next login),
because native heap addresses follow it, and the verdict requires every
replay's to be the recording's (same_game_environment_as_recording).
--replay-only from a shell that would give the game another stops before any
replay runs, naming the variables that differ.

One probe set
-------------
The recording, both replays and both preflight passes load the same probes,
built the same way, in the same order, so every native-vs-native comparison
here is between runs of one configuration. Only the input probe's mode
differs, recording or replaying:

  1. harness/replay/native_vanilla_probe.c, first, so the game runs its own
     code rather than the Mod Loader's replacements and the probe sees the
     import table before the import probe wraps it
     (agents-journals/s4/2026-09-23-vanilla-oracle.md). Every run must pass
     tools/oracle/oracle_mode.py's vanilla_problems, the one check of it.
  2. native_import_probe.c, whose log each run writes on its last captured
     tick (below).
  3. native_input_probe.c, which ends the run 300 ticks after the goal latch
     rises, or at its TICK_LIMIT.
  4. native_tick_probe.c and native_stage_trace.c: the tick capture, and the
     stage trace of level, mode, state, frame counter, player object and
     position that a lifted replay of the recording is compared with. The
     tick probe keeps the word log (harness/replay/word_log_core.h): every
     word of the game's data section at every tick, in memory, written once
     on the run's last captured tick (INPUT_END_DLL, DEFINES), never at
     ExitProcess. Every run collects it (--binary-capture, before `--`), and
     the runner refuses a run whose log is missing, not whole, or not the
     probe's own row on every tick, and builds sa2-tick-capture.csv's digest
     columns from it with the tree's mask (tools/oracle/run_oracle_probe.py,
     its docstring's item 5), so the comparisons below read the file they
     always read.
  5. the device-profile probe, which the runner loads last (--device-profile).
     The user ruled on 2026-09-23 that the device set is a recorded property of
     each capture and the lifted replay reads it back, so each replay must
     record the recording's own profile, byte for byte, and the keyboard's
     receiver stays plugged in throughout. A probe newer than the recording
     can write sections it never held (the locale section, 2026-09-26), and a
     re-take made with it is held to the recording line for line in every
     section but those, and to the other re-takes in those
     (device_differences, ADDED_SECTIONS).

The Mod Loader calls OnFrame in load order, so on the input probe's last tick
its ExitProcess comes before the tick probe's row: a run that ends at tick E
captures E - 1 rows (mh-preflight-20260923-090333: TICK_LIMIT=600, 599 rows),
and the tick probe writes its word log on tick E - 1, after that tick's row.

What the import log covers
--------------------------
Every run's import log counts the calls through its last captured tick, E - 1,
the whole run the tick capture holds: the recording's, whatever tick the
player's goal ends it on, each replay's, and each preflight pass's, which is
tick 599 as it always was. The probe is built with INPUT_END_DLL (DEFINES): it
asks the input probe, loaded after it, for the tick the run will end on
(SA2InputProbeEnd, the rule the input probe ends the run by) and writes once,
from its own OnFrame, on the tick before. It never writes at DLL detach, which
can hang on a heap lock a killed thread held
(agents-journals/s4/2026-09-23-m4-replay-imports.md). Until 2026-09-23 the log
was written at the fixed tick 599, because a recording's last tick is decided
while it is played, so a recording's log counted only its first 599 ticks.

The preflight holds both passes' logs to their last captured tick
(import_log_at_last_tick), so a probe that misses it stops the session before
anyone plays. After that the log never decides whether a run is kept: a run
whose only fault is its import log, missing or refused, is accepted (`run`,
`accepted`), and the session reports, in the summary and the verdict, the tick
each run's log was counted at against its last captured tick, whether that is
the whole run, and why a log could not be read. The one path the preflight
cannot exercise is the goal ending the run, and a fault there must cost the
log, not the user's recording. What the replays must show is that they
reproduce the recording, and a registration re-reads every log it takes
(tools/s4/native_imports.py replay_calls).

What the verdict allows, and says it allows
-------------------------------------------
Byte-identical native stage replays have never been observed. Three pairs of
idle native runs of Metal Harbor were measured, and each pair differed in
exactly three digest blocks, d_01af0000, d_01b00000 and d_01b10000:
mh-recordcfg-check-v2a against mh-preflight-20260923-090333 (the same probes,
both paced), mh-spawn-v1a against mh-spawn-v1b (the loader only), and
mh-snap-vanilla-v1c against mh-snap-vanilla-v1d (two probe versions). Where
the pair v1c/v1d was examined word by word, ticks 120 to 200, the words that
differed were CRI's streaming state, which its own threads update on their
own timing (agents-journals/s4/2026-09-23-vanilla-oracle.md). So a replay
whose tick capture differs from the recording's only in those blocks passes;
the verdict reports tick_captures_identical beside it and names every block
that differed with its first tick.

The allowance is coarser than that finding. It works on whole 64 KiB blocks,
and the blocks hold game-owned words too: about 13 masked words in d_01af0000
are referenced only from game code (0x01AF196C, from code at 0x0042807D,
among them) and 5 in d_01b10000 (the vanilla-oracle interrogation, reviewer
B, C2: private/generated/s4/interrogations/vanilla-oracle-B-opus.md). A
gameplay difference confined to those words passes. The allowance is this
tool's, pending a stage digest mask chosen by the mask's own rule (keep the
words two native runs agree on at every tick); nothing else uses it.

d_01750000 joined the list on 2026-09-24, with the user's yes. Push D
(development_snapshot) re-derived the digest mask from vanilla runs, and it compares 34
words of that block: 27 in entry 0 of CRI's file table (in use, read status,
the path, the size, the file and event handles), the C runtime's count of
buffered streams (_cflush) and six words of two ADX playback slots
(agents-journals/s4/2026-09-24-tick-380-xeci.md). The user's preflight that
evening stopped on it: two idle runs of one configuration,
mh-preflight-20260924-201834 and its replay, differed there on 32 scattered
ticks, as CRI's read status does when a read is still in flight at a tick's
end in one run only. The question put to the user, and the answer: "Add the
file-streaming block (d_01750000) to the recorder's list of blocks that are
allowed to differ between two real-game runs, then you record?" -- "Yes, add
it". The allowance passes a difference in any of the 34 words, so a
difference in which file the game streams, how many files it has open, or
those slots' state passes too. The file and event handles are Wine's
numbers, which that pair did not show varying and a replay can: the recording
mh-record-20260924-202305's second replay differs from it in the block over
most of the stage, in a way that fits handle numbers (the recorder review of
2026-09-24).

It is also looser than the acceptance the user ratified. Stage-route item 5
(Documentation/decisions/2026-09-23-S4-stage-route.md) says the two replays
"reproduce each other's tick captures" and set the goal latch on the same
tick. When that holds and every check passes, the verdict is RECORD_STAGE_OK.
When every check passes but the two replays' tick captures differ, which the
allowance lets through, the verdict is RECORD_STAGE_OK_BY_ALLOWANCE: the
session is kept and the tool exits 0, and the summary records
stage_route_item_5_met false and passed_by_allowance true, because loosening
item 5 is the user's ruling to make (AGENTS.md rule 10).

The stage trace is compared line for line, its `player` column included. That
column is a heap address, and so are the tick capture's g_01a557c0 and
g_01af19f0. The lifted build never shares them, so they compare only here,
never native against lifted. Native runs of one configuration share them when
they are launched with one environment, and not otherwise. On 2026-09-23 the
user's recording and its preflight, launched from the user's terminal, shared
one set of values, and the agent's runs of the same configuration that
evening, launched from its shell, shared another, on every row and whatever
the mirror's layout (a pair from each side ran in the same layout); the
runner then passed its caller's whole environment to the game, whose C runtime
copies it into its heap (agents-journals/s4/2026-09-23-recording-session.md).
The runner now launches the game with a fixed environment and records it
(session.environment), and two boots from callers whose environments differed
recorded one and matched byte for byte
(private/generated/s4/addendum-native/RESULTS.md, step 3). So the verdict
holds every replay to its recording's game environment, and a difference in
those columns under one environment is the summary's to name
(against_recording, named_columns_differ).

What it will not throw away
---------------------------
Some runs exit 247 at shutdown after every capture is written: Proton exits -9
when Wine is killed during teardown, and the runner accepts only 0 and 124
(five complete captures on disk, listed in
Documentation/decisions/2026-09-23-S4-vanilla-oracle.md). This tool accepts a
run whose receipt shows exit 247 with every artifact present and passing its
check and the installation untouched, and reports it as a teardown exit. Once
a whole recording exists, any failure prints the exact --replay-only command
that resumes from it. A recording that cannot be replayed (not a whole
capture, not the game's own code, no goal) says to record again instead, and
one the probes in the tree have moved away from names the files to put back.

Rule 11: the game runs on the user's live desktop because the user asked to
play it; every other run this tool makes drives input through the hook only,
never through synthetic OS events, and each receipt records the session.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import device_profile as DP  # noqa: E402  DP.PROBE: what the runner's --device-profile builds
import native_imports as NI  # noqa: E402  read_at: the one reader of an import log
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
import oracle_mode as OM  # noqa: E402  vanilla_problems and require_same: the one check of both
import run_oracle_probe as RP  # noqa: E402  game_environment: what the game is launched with
import save_state as SS  # noqa: E402  the pinned start state every run is held to

PY = str(ROOT / ".venv" / "bin" / "python")
RUNNER = ROOT / "tools" / "oracle" / "run_oracle_probe.py"
PIN = SS.PIN
# The runner's own, its private tree resolved (run_oracle_probe.PRIVATE): the
# replay header's path is an input probe define, so a copy of these tools in a
# scratch tree names it as the tree does.
CAPTURES = RP.CAPTURES
SCRIPTS = RP.OUT / "input-scripts"
PROBE = "harness/replay/native_input_probe.c"
TICK = "harness/replay/native_tick_probe.c"
IMPORTS = "harness/replay/native_import_probe.c"
VANILLA = "harness/replay/native_vanilla_probe.c"
STAGE_TRACE = "harness/replay/native_stage_trace.c"
SPAWN = ["--", "--level", "metalharbor", "--character", "sonic"]
FIELDS = ["on", "off", "press", "release", "r", "l", "x1", "y1", "x2", "y2", "old"]

# Every run's probes in load order, as the module docstring gives them. The
# runner builds the device-profile probe itself, last, as this mod id.
INPUT_MOD = "input_probe"
PROBES = (("vanilla_probe", VANILLA), ("import_probe", IMPORTS), (INPUT_MOD, PROBE),
          ("tick_probe", TICK), ("stage_trace", STAGE_TRACE))
PROFILE_MOD = "device_profile"

CAP = 480                  # seconds: the runner's largest --timeout, the recording's cap
PREFLIGHT_TICKS = 600
PREFLIGHT_TIMEOUT = 200
# The game's own time for the preflight's 600 ticks that counts as paced for a
# person (`preflight`). The pace is a floor, so a run can fall behind and
# nothing in the game notices: only the upper bound catches it.
PACED_SECONDS = (PREFLIGHT_TICKS / 60 * 0.9, 16.5)
# CRI's timer thread wakes at every 1/60 s of timeGetTime and reads it about 120
# times per wall-clock second, so the import log's count for this slot measures
# a run's wall-clock length over the ticks it counts (`pace`).
TIMER = "WINMM.dll!timeGetTime"
PACE_TOLERANCE = 0.01
# The slowest replay measured against its recording: 187.0 s against 179.0 s
# (mh-record-20260923-212428-replay1-20260923-213237), 4.5 % longer.
REPLAY_SLOWDOWN = 1.05
# The import probe writes its log on the tick before the input probe ends the
# run ("What the import log covers"), and so does the tick probe its word log
# (harness/replay/native_tick_probe.c, which then never ends the process: a
# TICK_LIMIT beside INPUT_END_DLL does not compile). Each finds the input probe
# by its DLL's name, which the runner makes of the mod id
# (run_oracle_probe.compile_probe).
DEFINES = {"import_probe": [f'INPUT_END_DLL="{INPUT_MOD}.dll"'],
           "tick_probe": [f'INPUT_END_DLL="{INPUT_MOD}.dll"']}
IMPORT_LOG = NI.LOG
# The tick probe's word log, collected as a binary capture: a runner flag, so
# `command` puts it before `--`, after which every argument reaches the game.
WORD_LOG = RP.WORD_LOG
# The rule-5 review of this tool, which the comments below cite as REVIEW.
REVIEW = "private/generated/s4/review-oracle-folds.json"
# Each run after the recording waits this long for the oracle's lock: the lock
# is taken per run, so an agent's capture can start between two of them (REVIEW).
LOCK_WAIT = 900
TEARDOWN_EXIT = 247

COMMON = ["sa2-vanilla.csv", "sa2-import-calls.csv", "sa2-input-end.csv",
          "sa2-tick-capture.csv", "sa2-stage-trace.csv"]
RECORD_CAPTURES = ["sa2-input.csv"] + COMMON
REPLAY_CAPTURES = ["sa2-input-applied.csv"] + COMMON
# The import log has one row per import slot (251) and no frame counter.
POLICIES = ["sa2-import-calls.csv=251:calls", "sa2-vanilla.csv=1"]

# The digest blocks that differ between idle native runs of one configuration
# (module docstring). A difference confined to them passes, and is reported.
NATIVE_UNSTABLE = ("d_01750000", "d_01af0000", "d_01b00000", "d_01b10000")
# The idle native pairs measured to differ in exactly those blocks.
MEASURED_PAIRS = (("mh-recordcfg-check-v2a", "mh-preflight-20260923-090333"),
                  ("mh-spawn-v1a", "mh-spawn-v1b"),
                  ("mh-snap-vanilla-v1c", "mh-snap-vanilla-v1d"))
# d_01750000 joined on 2026-09-24, with the user's yes: the vanilla mask of push D
# (development_snapshot) compares CRI's file table there (agents-journals/s4/2026-09-24-tick-380-xeci.md),
# whose handles and read status vary between runs, and this idle pair differed in it
# as well as in the three above.
MEASURED_PAIRS_FILE_TABLE = (("mh-preflight-20260924-201834", "mh-preflight-20260924-201834-replay"),)
# What the allowance is and what it hides, printed with every verdict that
# used it and kept in the summary (module docstring).
ALLOWANCE_LINES = (
    "Allowed: tick-capture differences only in d_01750000, d_01af0000, d_01b00000 and",
    "  d_01b10000.",
    "Why: three pairs of idle native runs differed, under the mask before 2026-09-24, in",
    "  exactly the last three blocks:",
    *(f"  {a} vs {b}{'.' if k == len(MEASURED_PAIRS) - 1 else ','}"
      for k, (a, b) in enumerate(MEASURED_PAIRS)),
    "  Once the mask compared CRI's file table (2026-09-24), an idle pair differed in",
    "  d_01750000 too, on 32 scattered ticks, as CRI's read status does when a read is",
    "  still in flight at a tick's end in one run only:",
    *(f"  {a} vs {b}." for a, b in MEASURED_PAIRS_FILE_TABLE),
    "  Its file and event handles are Wine's numbers, which that pair did not show",
    "  varying and a replay can (agents-journals/s4/2026-09-24-tick-380-xeci.md).",
    "Caveat: it allows whole 64 KiB blocks, which also hold game words (about 13 in",
    "  d_01af0000, 5 in d_01b10000), so a gameplay difference in those words would pass.",
    "  In d_01750000 it hides all 34 compared words: entry 0 of CRI's file table (27:",
    "  in use, read status, path, size, file and event handles), the C runtime's stream",
    "  count (_cflush) and two ADX playback slots (6), so a difference in which file the",
    "  game streams, how many files it has opened, or those slots' state would pass too.",
    "The allowance is this tool's, pending a stage digest mask.")
ALLOWANCE = " ".join(line.strip() for line in ALLOWANCE_LINES)
ITEM_5_NOT_MET = ("stage-route item 5 (replays reproduce each other's tick captures) is not met; "
                  "the allowance for the natively unstable blocks stands until the stage has its "
                  "own digest mask, and whether item 5 may pass by it is the user's ruling")

BEFORE = """\
Before anything starts:
  * Stop every agent working in this repository until the replays finish.
    The game needs the machine to itself for up to half an hour, and a probe
    file edited after you play stops your recording from replaying.
  * Unplug any gamepad. Leave the keyboard's receiver plugged in.
  * Steam must be running.

First the game runs twice on its own, for about a minute, to check it is
ready. Touch nothing until you are asked to press Enter.
"""

INSTRUCTIONS = """\
Metal Harbor, Sonic, Mission 1. The game window opens on your desktop.

  * Play with the KEYBOARD. Plug nothing in and unplug nothing until the
    replays finish: every run records the devices it saw, and the replays
    must see the same ones.
  * Touch nothing until Sonic can move. Keep the game window focused; do not
    minimise, resize or click away from it.
  * If Sonic does not move when you press the keys that move him, close the
    game window and tell the agent. The game now starts with fewer of your
    desktop's settings than before, and no one has played it that way yet.
  * Play to the goal ring. Do not pause, and try not to die. Take the Light
    Dash upgrade on the way, since the stage needs it: every run starts from
    the saves from before it, so the pickup and the save it writes happen in
    your run and in each replay. Avoid Omochao if you can: a message box that
    waits for a button is replayable, but the fewer the better.
  * At the goal ring, let go of the keyboard. The game closes itself a few
    seconds later. The whole run is capped at eight minutes.

When the game has closed, two replays of your run follow automatically (about
the same length each). Nothing needs your hands during them. If anything goes
wrong after you play, this prints the command that replays your recording
without playing again, or says to record again if the recording is unusable.
"""


class Stop(SystemExit):
    """A failure this tool explains. main() prints it, and once a whole
    recording exists, the command that resumes from it."""


class RecordAgain(Stop):
    """A failure of the recording itself, which no replay of it can pass:
    main() says to record again instead of printing the resume command."""


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def receipt(name: str) -> dict:
    return json.loads((CAPTURES / name / "receipt.json").read_text())


# ------------------------------------------------------------------ runs

def import_log_problem(rec: dict) -> str | None:
    """What the runner held against the run's import log, or None: the one
    artifact a run is kept without ("What the import log covers")."""
    for a in rec.get("artifacts") or []:
        if a.get("file") == IMPORT_LOG:
            if not a.get("present"):
                return f"{IMPORT_LOG} missing"
            if not (a.get("check") or {}).get("ok"):
                why = (a.get("check") or {}).get("problems") or []
                return f"{IMPORT_LOG} rejected: {'; '.join(why)}"
    return None


def accepted(rec: dict) -> tuple[bool, list[str]]:
    """Whether a receipt shows a whole capture, and if not, why: at least one
    artifact, every one but the import log present and passing its check, the
    installation untouched, the mirror's path to its save directory as built,
    no runner's word that its saves at start were not the pin, and exit 0 or
    124 (what the runner accepts) or 247, a teardown exit (module docstring).
    The import log is reported, never a reason to discard a run
    (import_log_problem).

    No capture that wrote the installation is whole. The user's first
    recording, mh-record-20260923-212428, wrote its save there before the
    mirror had a save directory of its own and was excused by name until the
    user ruled on 2026-09-24 that the saves are a recorded input and asked for
    a re-record; it is refused like any other."""
    problems = []
    arts = [a for a in rec.get("artifacts") or [] if a.get("file") != IMPORT_LOG]
    if not arts:
        problems.append("no artifacts")
    for a in arts:
        check = a.get("check") or {}
        if not a.get("present"):
            problems.append(f"{a.get('file')} missing")
        elif not check.get("ok"):
            problems.append(f"{a.get('file')} rejected: {'; '.join(check.get('problems') or [])}")
    if rec.get("installation_untouched") is not True:
        problems.append("the installation was written")
    if rec.get("mirror_path_differences"):
        problems.append("the mirror was written outside its save directory")
    if ((rec.get("session") or {}).get("saves") or {}).get("matches_pin") is False:
        problems.append("the run did not start from the pinned saves")
    code = (rec.get("run") or {}).get("return_code")
    if code not in (0, 124, TEARDOWN_EXIT):
        problems.append(f"exit {code}")
    return not problems, problems


def causes(rec: dict | None, stderr: str = "") -> list[str]:
    """The usual reasons a run is rejected, in plain words."""
    out = []
    if "another oracle run holds" in stderr:
        out.append("another run holds the oracle: stop any agent capturing the game, then "
                   "run this again")
    if "leads to no X authority file" in stderr or "leads back to" in stderr:
        out.append("the game could not be given this login's X authority file: run this from a "
                   "terminal of your current desktop login")
    if "are not the pinned start state" in stderr:
        out.append(f"the staged installation's saves are not the pinned start state: run "
                   f"`{SS.RESTORE}` from {ROOT}, then this again")
    if rec is None:
        return out
    arts = rec.get("artifacts") or []
    missing = {a.get("file") for a in arts if not a.get("present")}
    if arts and len(missing) == len(arts):
        out.append("no capture at all: is Steam running? Without it the game stops at a "
                   "message box")
    if (rec.get("run") or {}).get("return_code") == 124:
        cap = (rec.get("inputs") or {}).get("timeout_seconds")
        out.append(f"exit 124: the run hit its {cap} s cap before the goal")
    if "sa2-input-end.csv" in missing:
        out.append("no sa2-input-end.csv: the run never reached its end (the goal, or its "
                   "tick limit)")
    if any(a.get("device_profile") and a.get("present") and not (a.get("check") or {}).get("ok")
           for a in arts):
        out.append("the device check refused this machine's devices (the reason is above): "
                   "unplug any gamepad and keep the keyboard's receiver plugged in")
    if rec.get("installation_untouched") is False:
        out.append("the game wrote into its installation: stop, and tell the agent")
    if rec.get("mirror_path_differences"):
        out.append("the game wrote into the mirror outside its save directory (MIRROR WRITTEN "
                   "above): stop, and tell the agent")
    return out


def command(name: str, input_defines: list[str], captures: list[str], timeout: int,
            lock_timeout: float | None = None) -> list[str]:
    """The runner call `run` makes, from the repository root: the tool's probe
    set in load order with DEFINES, and the input probe given `input_defines`.
    tools/oracle/retake_replays.py prints it for a dry run, so what a dry run
    shows is what runs."""
    cmd = [PY, str(RUNNER), "--name", name, "--timeout", str(timeout), "--no-skip-intro"]
    if lock_timeout is not None:
        cmd += ["--lock-timeout", str(lock_timeout)]
    for mod, source in PROBES:
        cmd += ["--build", f"{mod}={source}"]
    for mod, defines in DEFINES.items():
        cmd += [f"--define={mod}:{d}" for d in defines]
    cmd += [f"--define={INPUT_MOD}:{d}" for d in input_defines]
    for c in captures:
        cmd += ["--capture", c]
    for p in POLICIES:
        cmd += ["--capture-policy", p]
    cmd += ["--binary-capture", WORD_LOG, "--min-rows", "1", "--device-profile"] + SPAWN
    return cmd


def run(name: str, input_defines: list[str], captures: list[str], timeout: int,
        lock_timeout: float | None = None) -> dict:
    """One runner call with the tool's probe set (`command`), the input probe
    given `input_defines`; its receipt, or Stop.

    Accepted when the runner reports its captures good, or when exit 247 or the
    import log is all it objected to (the log is reported by the session, not
    judged here). A rejected run's message carries every line the
    runner printed about a problem -- MISSING, REJECTED with its reasons (the
    device profile's refusal among them), INSTALLATION WRITTEN -- and the
    usual causes, so the user sees why, not only that."""
    if (CAPTURES / name).exists():
        # The runner refuses to overwrite it, and its old receipt would then be
        # read as this run's.
        raise Stop(f"{name}: a capture by that name already exists; run this again")
    cmd = command(name, input_defines, captures, timeout, lock_timeout)
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    lines = p.stdout.strip().splitlines()
    # The runner's verdict is its SA2_ORACLE_CAPTURE_ line, wherever it falls: a
    # note the runner printed ahead of it once made a whole replay read as
    # rejected (replays-20260923-213237.json, every_replay_ran false).
    verdict = next((line for line in lines if line.startswith("SA2_ORACLE_CAPTURE_")), "")
    if verdict:
        print(verdict)
    path = CAPTURES / name / "receipt.json"
    rec = json.loads(path.read_text()) if path.is_file() else None
    ok, problems = accepted(rec) if rec else (False, ["the run left no receipt"])
    runner_ok = p.returncode == 0 and verdict.startswith("SA2_ORACLE_CAPTURE_OK")
    # The runner also objects when it cannot establish the oracle mode, which
    # neither excuse below covers.
    mode_refused = bool(rec) and "refused" in ((rec.get("session") or {}).get("oracle") or {})
    judged = verdict.startswith("SA2_ORACLE_CAPTURE_PROBLEM") and not mode_refused
    teardown = judged and f" exit={TEARDOWN_EXIT} " in verdict
    log = import_log_problem(rec) if rec else None
    log_only = judged and bool(log)
    if rec and ok and (runner_ok or teardown or log_only):
        if teardown:
            print(f"NOTE: {name} exited {TEARDOWN_EXIT} at shutdown with every "
                  f"{'other ' if log else ''}capture whole; accepted as a teardown exit.")
        if log:
            print(f"NOTE: {name} is kept although its import log is not usable ({log}); "
                  "the verdict reports what the log covers.")
        return rec
    out = [f"{name}: the run was not accepted ({'; '.join(problems) or 'the runner said so'})."]
    out += [line for line in lines if not line.startswith("  ok ")]
    if p.stderr.strip():
        out.append(p.stderr.strip()[-1500:])
    reasons = causes(rec, p.stderr)
    if reasons:
        out.append("Likely cause:" if len(reasons) == 1 else "Likely causes:")
        out += [f"  - {r}" for r in reasons]
    raise Stop("\n".join(out))


def facts(rec: dict) -> dict:
    """What the summary keeps of a run's receipt: with the run's end and its
    devices, what the runner recorded of its word log, how long the oracle had
    been idle before it and the load average at its launch (reported, not
    judged; null in a receipt from before the word log)."""
    ran = rec.get("run") or {}
    session = rec.get("session") or {}
    log = rec.get("word_log") if isinstance(rec.get("word_log"), dict) else None
    return {"return_code": ran.get("return_code"),
            "teardown_exit": ran.get("return_code") == TEARDOWN_EXIT,
            "elapsed_seconds": ran.get("elapsed_seconds"),
            "device_profile": session.get("device_profile"),
            "word_log": None if log is None else {
                k: log.get(k) for k in ("stored_as", "sha256", "ticks", "end_reason", "ok")},
            "idle_before_run_s": (session.get("idle_before_run") or {}).get("seconds"),
            "load_average_at_launch": (session.get("load_average") or {}).get("at_launch")}


def end_of(name: str) -> dict:
    path = CAPTURES / name / "sa2-input-end.csv"
    if not path.is_file():
        raise Stop(f"{name}: no sa2-input-end.csv, so the run never reached its end (the goal, "
                   "or its tick limit)")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    try:
        return {k: int(rows[0][k]) for k in ("goal_tick", "end_tick", "misses")}
    except (IndexError, KeyError, TypeError, ValueError):
        raise Stop(f"{path}: not one row of goal_tick,end_tick,misses") from None


def cap_warning(rec: dict) -> str | None:
    """A recording whose replays, at the slowest pace a replay has run
    against its recording (REPLAY_SLOWDOWN), would come within 15 s of the cap
    leaves them almost no room (REVIEW). The pace is a floor, not a lock, so a
    replay can take longer than the run it replays."""
    took = (rec.get("run") or {}).get("elapsed_seconds") or 0
    if took * REPLAY_SLOWDOWN < CAP - 15:
        return None
    return (f"WARNING: your run took {took:.0f} s of the {CAP} s cap, and a replay can take "
            "longer than the run it replays. If a replay stops with exit 124, record again "
            "and finish sooner.")


def timer_calls(name: str) -> tuple[int, int] | None:
    """(calls to TIMER, the tick they were counted at) from `name`'s import
    log, read by tools/s4/native_imports.py's reader, or None when there is no
    log it takes."""
    path = CAPTURES / name / IMPORT_LOG
    if not path.is_file():
        return None
    try:
        counts, at = NI.read_at(path)
    except SystemExit:
        return None
    except Exception:  # noqa: BLE001  a reader that fails must not cost the summary
        return None
    return (counts[TIMER], at) if TIMER in counts else None


def pace(recording: str, replay: str) -> float | None:
    """How long `replay` ran against `recording`, by CRI's timer calls over the
    same ticks: 1.0 for the same wall-clock length, 1.045 for replay1 of the
    user's recording. None when either log is missing or refused, or the two
    were counted at different ticks, since the ratio would then compare
    different spans."""
    a, b = timer_calls(recording), timer_calls(replay)
    if not a or not b or a[1] != b[1] or not a[0]:
        return None
    return round(b[0] / a[0], 4)


def start_saves(name: str) -> dict | None:
    """The save files `name` started from, name -> sha256, as its receipt
    records them (session.saves.at_start, the mirror's save directory just
    before the game ran), or None when it records none. Nothing else stands in
    for that record. The install-before snapshot shows the installation, and
    since the mirror got a save directory of its own (the evening of
    2026-09-23, between the first recording and its replays) that is not what
    the game read: the first recording's second replay records no saves and
    ran on the mirror after its first replay had passed the pickup, with no
    record of what it found there (the save-state review of 2026-09-24, W3)."""
    saves = (receipt(name).get("session") or {}).get("saves") or {}
    return saves["at_start"] if isinstance(saves.get("at_start"), dict) else None


def end_saves(name: str) -> dict | None:
    """The saves `name` ended with (session.saves.at_end), or None when its
    receipt does not record them: reported, not judged, since what the game
    writes is the run's own doing (the module docstring)."""
    saves = (receipt(name).get("session") or {}).get("saves") or {}
    return saves["at_end"] if isinstance(saves.get("at_end"), dict) else None


def pinned() -> SS.Pin:
    """The pinned start state, Documentation/save-state.json, as
    tools/oracle/save_state.py reads it; Stop naming why when it cannot."""
    try:
        return SS.load(PIN)
    except SS.SaveStateError as e:
        raise Stop(str(e.code)) from None


def starts_pinned(name: str, pin: SS.Pin | None) -> bool:
    """Whether `name`'s receipt records that it started from the pinned saves
    (start_saves). A run that records none has not shown it did, and no pin
    is no match."""
    saves = start_saves(name)
    return pin is not None and saves is not None and saves == pin.files


def game_environment(name: str) -> str | None:
    """The digest of the environment the game of `name` was launched with
    (session.environment.sha256, run_oracle_probe.environment_record), or None
    when its receipt does not record one."""
    env = ((receipt(name).get("session") or {}).get("environment")) or {}
    return env.get("sha256") if isinstance(env, dict) else None


def launch_environment() -> dict:
    """The environment a run this process starts would give the game
    (run_oracle_probe.game_environment), by name and value, which only this
    process and a private receipt hold."""
    return RP.game_environment(dict(os.environ))


# -------------------------------------------------------------- the checks

def vanilla_problems(name: str) -> list[str]:
    """tools/oracle/oracle_mode.py's check that a capture ran the game's own
    code as the vanilla oracle in the tree runs it; [] when it did. A refusal
    comes back as a reason, so a replay's summary is written whatever the check
    finds."""
    try:
        return list(OM.vanilla_problems(CAPTURES / name))
    except SystemExit as e:
        return [str(e)]


def same_mode(names: list[str]) -> bool:
    """Whether every named capture was produced by one oracle mode
    (tools/oracle/oracle_mode.py require_same): the user's direction of
    2026-09-23 that comparisons across modes are refused."""
    try:
        OM.require_same([CAPTURES / n for n in names])
        return True
    except SystemExit:
        return False


def vanilla_rows(name: str) -> frozenset:
    """The (address, length, action) rows the vanilla probe wrote. The loader's
    bytes are left out, because its thunks move between runs; a length could in
    principle move with them, where a thunk's address shares a byte with the
    file's, and none has in any capture so far."""
    path = CAPTURES / name / "sa2-vanilla.csv"
    if not path.is_file():
        return frozenset()
    with path.open(newline="") as f:
        return frozenset((r.get("va"), r.get("length"), r.get("action")) for r in csv.DictReader(f))


# Each mod's source in the tree, as this tool builds it.
TREE_SOURCES = dict(PROBES + ((PROFILE_MOD, DP.PROBE),))


# The one include by macro this tool knows: the input probe's script,
# INPUT_SCRIPT, which an input define names (left out of normal() by design,
# since the recording and its replays differ in it) and which is the
# recording's own input, generated under private/ by `script`, never a header
# beside the probe.
SCRIPT_INCLUDE = "INPUT_SCRIPT"


def included_headers(source: Path) -> dict[str, str] | None:
    """The headers `source` is built from, name -> sha256: every one it
    includes, directly or through another, as tools/s4/tick_replay.py
    probe_headers finds them, the one definition native_probe_current holds a
    registered capture to. The input probe's script include (SCRIPT_INCLUDE)
    is passed over, and what the probe includes besides it is read header by
    header. None when the includes are not all headers beside the probe, and
    then the caller compares everything the runner recorded."""
    import tick_replay as TR          # tools/s4; here, since it imports much this tool never uses
    try:
        return TR.probe_headers(source)
    except SystemExit:
        if source.name != Path(PROBE).name:
            return None
    code = re.sub(r"/\*.*?\*/", " ", source.read_text(errors="replace"), flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    out: dict[str, str] = {}
    for target in TR.INCLUDE.findall(code):
        if target == SCRIPT_INCLUDE or (target.startswith("<") and target.endswith(">")):
            continue
        name = target[1:-1] if target.startswith('"') and target.endswith('"') else ""
        header = source.parent / name
        if not name or "/" in name or "\\" in name or not header.is_file():
            return None
        try:
            out.update({name: sha256(header), **TR.probe_headers(header)})
        except SystemExit:
            return None
    return dict(sorted(out.items()))


def normal(b: dict) -> tuple:
    """A built probe as this tool compares two runs by it: the mod, its
    source's name and sha256, the headers it was built from, and its defines
    -- except the input probe's, which differ between recording and replay by
    design.

    The headers are the ones the probe includes (included_headers), not every
    header beside it, which is what the runner records. A header no probe
    includes is no input of the run: the digest mask is one such since the
    digest columns are computed on the host from the word log, so a new mask
    is not a new configuration (private/generated/s4/wordobs/reports/
    judge-plan.md). What a probe includes is read from the tree's copy of its
    source, and it is the recorded build's only when that copy is the recorded
    source and every header it includes has the recorded sha256; for any other
    build the recorded headers are kept whole, as the runner recorded them, so
    such a build is compared on no less than before."""
    mod, name = b.get("mod_id"), Path(str(b.get("source", ""))).name
    recorded = b.get("headers_sha256")
    headers = recorded
    tree = TREE_SOURCES.get(mod)
    if (tree is not None and Path(tree).name == name and isinstance(recorded, dict)
            and b.get("source_sha256") == sha256(ROOT / tree)):
        included = included_headers(ROOT / tree)
        if included is not None and all(recorded.get(h) == s for h, s in included.items()):
            headers = included
    return (mod, name, b.get("source_sha256"), headers,
            None if mod == INPUT_MOD else list(b.get("defines") or []))


def builds(name: str) -> list[tuple]:
    return [normal(b) for b in (receipt(name).get("inputs") or {}).get("built_probes") or []]


def tree_builds() -> list[tuple]:
    """What this tool builds now, from the tree, in the runner's receipt terms."""
    out = []
    for mod, source in PROBES + ((PROFILE_MOD, DP.PROBE),):
        path = ROOT / source
        out.append(normal({"mod_id": mod, "source": source, "source_sha256": sha256(path),
                           "headers_sha256": {h.name: sha256(h)
                                              for h in sorted(path.parent.glob("*.h"))},
                           "defines": DEFINES.get(mod, [])}))
    return out


def changed_files(name: str) -> list[str]:
    """The files, relative to the repository, that changed since `name` was
    recorded so that the tree's probes would not rebuild its configuration, or
    []. Each replay is held to the recording's builds (compare_to's
    headers_same), so replaying with other ones would spend both replays on a
    verdict that cannot pass; putting these files back makes it replayable.
    The probe set, a probe's source path and its defines are this tool's own."""
    this = str(Path(__file__).resolve().relative_to(ROOT))
    got, want = builds(name), tree_builds()
    if [g[0] for g in got] != [w[0] for w in want]:
        return [this]
    out: list[str] = []
    for g, w, (_, source) in zip(got, want, PROBES + ((PROFILE_MOD, DP.PROBE),)):
        if g[2] != w[2]:
            out.append(source)
        if g[3] != w[3]:
            # The recorded build is not the tree's (normal), so its headers
            # are the runner's whole record: named against what the runner
            # would record beside the probe now.
            old = g[3] if isinstance(g[3], dict) else {}
            new = {h.name: sha256(h) for h in sorted((ROOT / source).parent.glob("*.h"))}
            out += [str(Path(source).parent / h) for h in sorted(set(old) | set(new))
                    if old.get(h) != new.get(h)]
        if g[1] != w[1] or g[4] != w[4]:
            out.append(this)
    return list(dict.fromkeys(out))


def trace_lines(name: str) -> list[str]:
    path = CAPTURES / name / "sa2-stage-trace.csv"
    return path.read_text().splitlines() if path.is_file() else []


def tick_columns(name: str) -> tuple[list[str], list[list[str]]]:
    path = CAPTURES / name / "sa2-tick-capture.csv"
    if not path.is_file():
        return [], []
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    return (rows[0], rows[1:]) if rows else ([], [])


def import_log(name: str) -> dict:
    """What `name`'s import log covers: the tick its counts were taken at, read
    by tools/s4/native_imports.py's reader, against the last tick the run's
    tick capture holds (its row count, as that reader measures a replay).
    whole_run when the two agree and the capture has a row: a log counted at
    tick 0 beside no capture agrees with nothing. A log the reader refuses has
    no tick, and the refusal is kept."""
    rows = len(tick_columns(name)[1])
    out: dict = {"file": IMPORT_LOG, "counted_at_tick": None, "last_captured_tick": rows,
                 "whole_run": False}
    path = CAPTURES / name / IMPORT_LOG
    if not path.is_file():
        return {**out, "refused": f"no {IMPORT_LOG}"}
    try:
        _, at = NI.read_at(path)
    except SystemExit as e:
        return {**out, "refused": str(e.code)}
    except Exception as e:  # noqa: BLE001  a reader that fails must not cost the summary
        return {**out, "refused": f"the log could not be read: {e!r}"}
    return {**out, "counted_at_tick": at, "whole_run": rows > 0 and at == rows}


def coverage(logs: dict[str, dict]) -> list[str]:
    """The verdict's lines on what each run's import log covers. A log counts
    no calls made before the import probe's Init, and none on the tick the run
    ends on, which the tick capture does not hold either."""
    out = []
    for name, log in logs.items():
        at, last = log["counted_at_tick"], log["last_captured_tick"]
        if log["whole_run"]:
            out.append(f"  import log of {name}: counted at tick {at}, the run's last captured "
                       "tick (every tick the capture holds)")
        elif at is not None:
            why = ("the run has no tick capture" if not last
                   else "it stops before the run ends" if at < last
                   else "it counts past the capture")
            out.append(f"  WARNING: the import log of {name} was counted at tick {at} but the "
                       f"run's last captured tick is {last}: {why}")
        else:
            out.append(f"  WARNING: the import log of {name} was not read: {log['refused']}")
    return out


def _tick(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def logged_columns(name: str, mask: Path | None = None) -> tuple[list[str], list[list[str]]] | None:
    """`name`'s tick capture as its kept word log gives it with `mask` (the
    tree's by default), read as tick_columns reads the stored file; None when
    the capture kept no log. Raises (a SystemExit) when the log is not the
    one its run collected, its run refused it, or the reader refuses it
    (run_oracle_probe.capture_from_log)."""
    text = RP.capture_from_log(CAPTURES / name, mask)
    if text is None:
        return None
    rows = list(csv.reader(io.StringIO(text)))
    return (rows[0], rows[1:]) if rows else ([], [])


def compare_to(ref: str, other: str) -> dict:
    """What `other` shares with `ref`, two native runs of this tool's probe set.

    headers_same: every probe built as it was for `ref`, from the same source
    and against the same headers it includes (normal()), so both rows sample
    one field list. rows_same: the tick captures hold as many rows. Every
    tick-capture verdict is false unless both hold and the column headers
    match, because a comparison over the shorter capture, or across two field
    lists, would pass without having compared anything (REVIEW).

    masks_same: both captures' digest columns hash the same words. The mask is
    no probe's input since the word log: the runner builds the columns from
    the log with the tree's mask of the day, and records the reader's inputs
    beside the capture (digest_inputs). A digest verdict is false unless both
    were built from the same, or neither was (the game computed them, under
    the headers headers_same compared); the named columns are compared
    either way.

    The stage trace compares line for line, `player` included, which is sound
    only between native runs of one configuration (module docstring)."""
    rt, ot = trace_lines(ref), trace_lines(other)
    rh, rr = tick_columns(ref)
    oh, orows = tick_columns(other)
    rb = builds(ref)
    headers_same = bool(rb) and rb == builds(other)
    masks_same = digest_inputs(ref) == digest_inputs(other)
    digests = "as stored"
    if not masks_same:
        # Two masks' digests, or one run's the game computed: when both runs
        # kept their word logs, both captures are rebuilt from them with the
        # tree's mask, so a mask that moved since the recording costs no
        # replay (the re-take review of 2026-09-25, W2).
        try:
            both = [logged_columns(ref), logged_columns(other)]
        except (SystemExit, OSError, ValueError) as e:
            both = [None, None]
            digests = f"not rebuilt from the word logs: {getattr(e, 'code', None) or e}"
        if None not in both:
            (rh, rr), (oh, orows) = both
            masks_same = True
            digests = (f"rebuilt from both word logs with {tree_mask_name()} "
                       f"({RP.sha256(RP.WL.MASK)[:12]})")
        elif digests == "as stored":
            digests = "not rebuilt: a run kept no word log"
    rows_same = bool(rr) and len(rr) == len(orows)
    columns_same = bool(rh) and rh == oh
    comparable = headers_same and rows_same and columns_same
    first: dict[str, int | str] = {}
    if comparable:
        for a, b in zip(rr, orows):
            if len(a) != len(b):
                first.setdefault("<row width>", _tick(a[0]))
                continue
            for column, x, y in zip(rh, a, b):
                if x != y:
                    first.setdefault(column, _tick(a[0]))
    named = {c: t for c, t in first.items() if not c.startswith("d_")}
    # Across two masks every block may differ without the game having moved,
    # so none is named (the runner review of 2026-09-25, N3).
    blocks = {c: t for c, t in first.items() if c.startswith("d_")} if masks_same else {}
    return {
        "headers_same": headers_same, "rows_same": rows_same, "columns_same": columns_same,
        "masks_same": masks_same, "digests": digests,
        "tick_rows": [len(rr), len(orows)],
        "trace_rows": [max(len(rt) - 1, 0), max(len(ot) - 1, 0)],
        "trace_same": len(rt) > 1 and ot == rt,
        "named_columns_same": comparable and not named,
        "named_columns_differ": named,
        "digest_blocks_differ": blocks,
        "tick_captures_identical": comparable and masks_same and not first,
        "differences_only_in_natively_unstable_blocks":
            comparable and masks_same and not named and set(blocks) <= set(NATIVE_UNSTABLE),
    }


def tree_mask_name() -> str:
    """The tree's mask, by repository path."""
    return str(Path(RP.WL.MASK).resolve().relative_to(ROOT.resolve()))


def digest_inputs(name: str) -> dict | None:
    """What the digest columns of `name`'s tick capture are a function of, as
    the runner recorded building them from the word log (its artifact's
    built_from_log, run_oracle_probe.digest_inputs: the reader's sources and
    the mask, by sha256), or None for a capture whose digests the game
    computed."""
    for a in receipt(name).get("artifacts") or []:
        if isinstance(a, dict) and a.get("file") == "sa2-tick-capture.csv":
            return (a.get("built_from_log") or {}).get("digest_inputs")
    return None


def same_devices(recording: dict | None, replays: list[dict],
                 recording_capture: str | None = None) -> bool:
    """Every replay recorded the recording's device profile: a replay on
    another device set shows nothing about the recording (the user's ruling
    of 2026-09-23 makes the set part of each capture). device_differences
    says what "the recording's profile" is for a replay whose probe writes
    sections the recording's did not."""
    return bool(recording) and not device_differences(recording, replays, recording_capture)


# The sections the probe of 2026-09-26 (the locale take, unit LC) writes that no
# earlier probe did (tools/s4/device_profile.py RECORDS). A re-take's lines of
# these kinds, where the recording holds none, are held to the other re-takes;
# every other line, `error` records and any kind not named here among them, is
# held to the recording's.
ADDED_SECTIONS = frozenset({b"locale_user", b"locale_info", b"environment", b"env",
                            b"std_handle"})


def profile_kind(line: bytes) -> bytes:
    """A device-profile line's section: its first word, the record's kind
    (tools/s4/device_profile.py RECORDS; `sa2-device-profile` for the header)."""
    return line.split(b" ", 1)[0]


def profile_bytes(record: dict, capture: str | None) -> bytes | None:
    """The bytes of the profile a run recorded (session.device_profile's file
    and sha256): the file in `capture`'s directory when that is named, else
    any capture's file of that name with that sha256, which is those bytes;
    None when no file holds them."""
    name, want = record.get("file"), record.get("sha256")
    if not name or not want:
        return None
    places = ([CAPTURES / capture] if capture is not None
              else sorted(p for p in CAPTURES.iterdir() if p.is_dir()) if CAPTURES.is_dir()
              else [])
    for place in places:
        path = place / name
        if path.is_file():
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == want:
                return data
    return None


def device_differences(recording: dict | None, replays: list[dict],
                       recording_capture: str | None = None) -> list[str]:
    """Why the replays did not see the recording's devices; [] when they did.

    A replay whose profile has the recording's sha256 saw them. One whose
    sha256 differs is read from its capture (`name`; a replay record without
    one is compared by sha256 alone) and the recording's profile from
    `recording_capture`, or by its sha256 from any capture (profile_bytes). It
    saw the recording's devices when its lines of every section the
    recording holds (profile_kind) are the recording's lines, byte for byte
    and in order. Its lines of the sections a newer probe added
    (ADDED_SECTIONS) that the recording does not hold are held to the other
    replays' instead: every replay must hold the same ones. A line of any
    other kind the recording does not hold, an `error` record among them, is
    a difference. A profile that cannot be read, or whose
    file no longer has the sha256 its run recorded, is a difference."""
    if not recording or not recording.get("sha256"):
        return ["the recording records no device profile"]
    want = recording["sha256"]
    out: list[str] = []
    extras: dict[str, list[bytes]] = {}
    base: list[bytes] | None = None
    loose: frozenset[bytes] = frozenset()
    for r in replays:
        name = r.get("name", "a replay")
        got = r.get("device_profile") or {}
        if not got.get("sha256"):
            out.append(f"{name} records no device profile")
            continue
        if got["sha256"] == want:
            extras[name] = []
            continue
        data = profile_bytes(got, r["name"]) if "name" in r else None
        if data is None:
            out.append(f"{name} recorded device profile {got['sha256'][:12]}, and the "
                       f"recording {want[:12]}"
                       + (f", and {name}'s own file no longer holds it" if "name" in r else ""))
            continue
        if base is None:
            held = profile_bytes(recording, recording_capture)
            if held is None:
                return out + [f"no capture holds the recording's device profile ({want[:12]}), "
                              "so no replay's can be compared with it"]
            base = held.split(b"\n")
            loose = ADDED_SECTIONS - {profile_kind(line) for line in base}
        lines = data.split(b"\n")
        kept = [line for line in lines if profile_kind(line) not in loose]
        if kept != base:
            at = next((k for k, (a, b) in enumerate(zip(kept, base)) if a != b),
                      min(len(kept), len(base)))
            section = profile_kind(base[at] if at < len(base) else kept[at])
            out.append(f"{name}'s device profile differs from the recording's in a section the "
                       f"recording holds, first in {section.decode(errors='replace')!r}")
            continue
        extras[name] = [line for line in lines if profile_kind(line) in loose]
    if len({tuple(v) for v in extras.values()}) > 1:
        out.append("the replays' device profiles differ in the sections the recording does not "
                   "hold: " + ", ".join(f"{k} {len(v)} line(s)" for k, v in extras.items()))
    return out


def playable(name: str) -> bool:
    """The stage trace reached GameStates_Ingame, 0x0010 (SA2Enums.h): tick 169
    in every Metal Harbor trace so far."""
    rows = list(csv.reader(trace_lines(name)))
    if not rows or "game_state" not in rows[0]:
        return False
    col = rows[0].index("game_state")
    return any(len(r) > col and r[col] == "0x0010" for r in rows[1:])


# ------------------------------------------------------------ the script

HEADER = ["tick", "ord"] + FIELDS
# Each value's width in bytes, native_input_probe.c's width[].
WIDTHS = dict(zip(FIELDS, (4, 4, 4, 4, 2, 2, 2, 2, 2, 2, 4)))
_DEC = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX = re.compile(r"0x(?:0|[1-9a-f][0-9a-f]*)\Z")


def recorded_rows(recording: Path) -> list[list[int]]:
    """A recording's rows as numbers, read as harness/replay/wrap_input_replay.c
    reads them, or Stop naming the line.

    The native replay's header and the lifted replay must hold the same rows,
    so this takes what that parser takes: the recorder's header, then tick and
    ord in decimal and each value as `0x` and lower-case hex, no sign, no
    space, no leading zero, every column within 32 bits and each value within
    its field's width, and rows in (tick, ord) order. A leading zero is refused
    for parity with that parser alone: script() writes each number as
    str(int(...)), so C never sees the file's spelling.

    Known differences, none of which native_input_probe.c can write (it opens
    its file "wb" and writes only `\\n` line ends and the recorder's
    characters): a last line ending in a bare `\\r` with no `\\n` is taken here
    and refused there; a header followed by `\\r` and more text, or a NUL byte
    in the header or the last line, is refused here and taken there, because
    C's string functions stop at the `\\r` or the NUL."""
    try:
        text = recording.read_bytes().decode("ascii")
    except UnicodeDecodeError:
        raise Stop(f"{recording}: not ASCII, so not the recorder's") from None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    if not lines or lines[0].split(",") != HEADER:
        raise Stop(f"{recording}: the header is not {','.join(HEADER)}")
    rows: list[list[int]] = []
    for n, line in enumerate(lines[1:], start=2):
        cols = line.split(",")
        if len(cols) != len(HEADER):
            raise Stop(f"{recording}:{n}: {len(cols)} columns, not {len(HEADER)}")
        for label, v in zip(("tick", "ord"), cols[:2]):
            if not _DEC.match(v) or int(v) > 0xFFFFFFFF:
                raise Stop(f"{recording}:{n}: {label} {v!r} is not decimal with no leading "
                           "zero within 32 bits, as the lifted replay's parser requires")
        for field, v in zip(FIELDS, cols[2:]):
            if not _HEX.match(v) or int(v, 16) >> (8 * WIDTHS[field]):
                raise Stop(f"{recording}:{n}: {field} {v!r} is not 0x and lower-case hex "
                           f"with no leading zero within {WIDTHS[field]} bytes")
        row = [int(cols[0]), int(cols[1])] + [int(v, 16) for v in cols[2:]]
        if rows and row[:2] <= rows[-1][:2]:
            raise Stop(f"{recording}:{n}: rows out of (tick, ord) order")
        rows.append(row)
    return rows


def script(recording: Path, out: Path) -> dict:
    """The replay header from a recording: one row per OnInput call."""
    rows = recorded_rows(recording)
    if not rows:
        raise Stop(f"{recording}: no input rows")
    body = ",\n".join("    {" + ",".join(str(v) for v in r) + "}" for r in rows)
    digest = hashlib.sha256(recording.read_bytes()).hexdigest()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"/* GENERATED by tools/oracle/record_stage.py from {recording.name} "
                   f"(sha256 {digest}); do not edit. */\n"
                   f"static const uint32_t in_rows[][{2 + len(FIELDS)}] = {{\n{body}\n}};\n"
                   f"static const unsigned in_count = {len(rows)}u;\n")
    return {"rows": len(rows), "recording_sha256": digest, "header": str(out)}


def replay_defines(header: Path, end: dict) -> list[str]:
    """The input probe's defines to replay `header` against a recording that
    ended at `end`: to its own last tick when it ended by its tick limit (a
    preflight pass), and to 60 ticks past it when it ended by the goal, so a
    replay whose goal comes late still stops near where the recording did."""
    limit = end["end_tick"] + 60 if end["goal_tick"] else end["end_tick"]
    return ["INPUT_MODE=2", f'INPUT_SCRIPT="{header}"', f"TICK_LIMIT={limit}"]


# ------------------------------------------------------------ the session

def preflight(stamp: str) -> dict:
    """Two unattended 600-tick runs before anyone is asked to play, the second
    a replay of the first, so the path the session takes runs once with nobody
    at the keyboard (REVIEW's recommendation):

    * the recording's configuration is paced for a person, runs the game's own
      code and becomes playable. Pace is read from each receipt's
      run.elapsed_seconds, the game process's own time, and both passes must
      fall within PACED_SECONDS. For 600 ticks the unpaced game took 4.8 s
      (mh-recordcfg-check-v1a: the vanilla probe once restored the Present
      wrapper that paced it then); the earlier child-window pace 14.0 to
      14.8 s (mh-preflight-20260923-090333, mh-recordcfg-check-v2a); the Mod
      Loader's frame limiter, the pace since the evening of 2026-09-23, 13.1 to
      13.2 s (mh-preflight-20260923-212428 and its replay); and a run slowed
      while the desktop was in use 26.4 s (mh-preflight-20260923-212312). The
      limiter is a floor that never makes up a slow frame, so only the upper
      bound catches a run that fell behind. The runner's own time is not read:
      it adds probe builds and two sha256 passes over the installation, and
      put the unpaced game within a second of the lower bound;
    * the replay's configuration reproduces the recording's by the checks the
      session's replays get from judge(), run on a pair known to be alike, less
      the goal, which 600 idle ticks never reach. Pass 2 ends on pass 1's end
      tick by construction (its TICK_LIMIT), so that is reported, not checked,
      and so is pass 2's pace against pass 1's by CRI's timer calls (`pace`);
    * both passes started from the pinned saves and were launched with one
      game environment, as the session's recording and replays must be. The
      digest of that environment is reported: a recording launched from
      another shell than the preflight's gives its game another one if any
      value the runner keeps differs there."""
    first, second = f"mh-preflight-{stamp}", f"mh-preflight-{stamp}-replay"
    try:
        pin = pinned()
        print("Check 1 of 2: the recording's configuration...")
        rec1 = run(first, ["INPUT_MODE=1", f"TICK_LIMIT={PREFLIGHT_TICKS}"], RECORD_CAPTURES,
                   PREFLIGHT_TIMEOUT)
        end1 = end_of(first)
        header = SCRIPTS / f"{first}.h"
        script(CAPTURES / first / "sa2-input.csv", header)
        print("Check 2 of 2: a replay of check 1...")
        rec2 = run(second, replay_defines(header, end1), REPLAY_CAPTURES, PREFLIGHT_TIMEOUT)
        end2 = end_of(second)
    except Stop as e:
        raise Stop(f"PREFLIGHT_FAILED, and nothing was recorded.\n{e}") from None
    problems = {first: vanilla_problems(first), second: vanilla_problems(second)}
    against = compare_to(first, second)
    logs = {first: import_log(first), second: import_log(second)}
    elapsed = rec1["run"]["elapsed_seconds"]
    times = [r["run"]["elapsed_seconds"] for r in (rec1, rec2)]
    ranges = vanilla_rows(first)
    environment = game_environment(first)
    checks = {
        "paced": all(PACED_SECONDS[0] <= t <= PACED_SECONDS[1] for t in times),
        "playable": playable(first),
        "runs_the_games_own_code": not any(problems.values()),
        "same_code_restored": bool(ranges) and vanilla_rows(second) == ranges,
        "same_device_profile": same_devices(facts(rec1)["device_profile"], [facts(rec2)]),
        "same_oracle_mode": same_mode([first, second]),
        "no_missed_rows": end2["misses"] == 0,
        "same_probes": against["headers_same"],
        "tick_rows_same": against["rows_same"],
        "stage_trace_same": against["trace_same"],
        "named_columns_same": against["named_columns_same"],
        "differences_only_in_natively_unstable_blocks":
            against["differences_only_in_natively_unstable_blocks"],
        "import_log_at_last_tick": all(log["whole_run"] for log in logs.values()),
        "start_saves_pinned": starts_pinned(first, pin) and starts_pinned(second, pin),
        "same_game_environment": environment is not None
                                 and game_environment(second) == environment,
    }
    out = {"runs": [first, second], "elapsed_seconds": elapsed, "elapsed_seconds_each": times,
           "pace_of_replay": pace(first, second), "checks": checks,
           "start_saves": {"pin": pin.record(ROOT), first: start_saves(first),
                           second: start_saves(second)},
           "game_environment": environment,
           "import_logs": logs,
           "end_ticks": [end1["end_tick"], end2["end_tick"]],
           "teardown_exits": [k for k, r in ((first, rec1), (second, rec2))
                              if facts(r)["teardown_exit"]],
           "vanilla_problems": {k: v for k, v in problems.items() if v},
           "tick_captures_identical": against["tick_captures_identical"],
           "digest_blocks_differ": against["digest_blocks_differ"],
           "named_columns_differ": against["named_columns_differ"]}
    if not all(checks.values()):
        failed = ", ".join(k for k, v in checks.items() if not v)
        raise Stop(f"PREFLIGHT_FAILED ({failed}), and nothing was recorded. The game is not "
                   f"ready for a person to play.\n{json.dumps(out, indent=1)}")
    return out


def check_recording(name: str) -> dict:
    """The recording as its replays need it: a whole capture, built as the tree
    builds it now, the game's own code, started from the pinned saves,
    launched with a recorded game environment that a run from this shell
    would give the game too, and the goal reached. Otherwise Stop naming what
    to put back when the tree or the shell moved, and RecordAgain when the
    recording itself is at fault. The files come before the vanilla check,
    which pins the tree's vanilla probe and would blame the recording for an
    edit to it."""
    ok, problems = accepted(receipt(name))
    if not ok:
        raise RecordAgain(f"{name} is not a whole capture: {'; '.join(problems)}")
    if predates_word_log(name):
        raise Stop(f"{name} was recorded before the tick probe kept its word log (2026-09-25). "
                   "Every run this tool makes builds the probe that keeps one, and the runner "
                   "refuses a tick probe's run that leaves no log, which the probe it was "
                   "recorded with never writes: putting files back cannot replay it. Its "
                   f"replays can be re-taken with the tree's probes instead: {retake_command(name)}")
    files = changed_files(name)
    if files:
        raise Stop(f"{name} cannot be replayed with the tree as it is now. These files changed "
                   f"since it was recorded: {', '.join(files)}. Put them back as they were, and "
                   "the command below works.")
    problems = vanilla_problems(name)
    if problems:
        raise RecordAgain(f"{name} did not run the game's own code: {'; '.join(problems[:5])}")
    pin = pinned()
    if not starts_pinned(name, pin):
        began = start_saves(name)
        raise RecordAgain(f"{name} did not start from the pinned saves ({SS.rel(pin.path)}, "
                          f"{pin.state}): "
                          + ("its receipt records none" if began is None
                             else f"it started from {began}")
                          + ", and every replay starts from the pin.")
    recorded = game_environment(name)
    if recorded is None:
        raise RecordAgain(f"{name}'s receipt records no game environment, so no replay can be "
                          "shown to have been launched with the same one.")
    here = launch_environment()
    if RP.environment_record(here)["sha256"] != recorded:
        was = (receipt(name).get("session") or {}).get("game_environment")
        differ = (sorted(k for k in set(was) | set(here) if was.get(k) != here.get(k))
                  if isinstance(was, dict) else ["unknown: the receipt keeps only the digest"])
        raise Stop(f"{name} was recorded with another game environment than a run from this "
                   f"shell would get ({', '.join(differ)} differ). Native heap addresses follow "
                   "it, so every replay would be refused. Run this again from a shell where "
                   "those variables are what the recording's receipt records "
                   "(session.game_environment)"
                   + ("; for XAUTHORITY, set it to this login's X authority file: the runner "
                      "gives the game its link" if "XAUTHORITY" in differ else "")
                   + ".")
    try:
        end = end_of(name)
    except Stop as e:
        raise RecordAgain(e.code) from None
    if not end["goal_tick"]:
        raise RecordAgain(f"{name}: the goal latch never rose, so this recording does not reach "
                          "the goal.")
    print(f"recording {name}: goal at tick {end['goal_tick']}, ended at {end['end_tick']}")
    return end


def replay(recording: str, stamp: str, end: dict, n: int = 2) -> list[dict]:
    """The recording replayed `n` times. A replay the runner does not accept is
    kept with its reason and ends the series, since the verdict needs every
    replay; nothing here raises, so the summary is always written."""
    header = SCRIPTS / f"{recording}.h"
    info = script(CAPTURES / recording / "sa2-input.csv", header)
    out = []
    for k in range(n):
        name = f"{recording}-replay{k + 1}-{stamp}"
        print(f"Replay {k + 1} of {n}...")
        item: dict = {"name": name, "error": None, "script": info}
        try:
            item.update(facts(run(name, replay_defines(header, end), REPLAY_CAPTURES, CAP,
                                  lock_timeout=LOCK_WAIT)))
            item["end"] = end_of(name)
        except Stop as e:
            print(e)
            item["error"] = str(e)
            out.append(item)
            break
        item["vanilla_problems"] = vanilla_problems(name)
        item["tick_sha256"] = sha256(CAPTURES / name / "sa2-tick-capture.csv")
        out.append(item)
    return out


def judge(name: str, end: dict, reps: list[dict], n: int = 2) -> dict:
    """The checks every replay must pass against the recording, and what is
    reported beside them.

    The recording and every replay must have started from the pinned saves
    (start_saves_pinned; the user's ruling of 2026-09-24): a save left in
    place changes what the stage offers. Every replay must have been launched
    with the recording's game environment, which native heap addresses
    follow (same_game_environment_as_recording). The saves each run ended
    with are reported, not judged. Each replay's pace against the recording
    (`pace`) is reported, not judged, and named when it is off by more than
    PACE_TOLERANCE: the pace is a floor, so a replay can run slower than its
    recording and still reproduce it, and what that moves is the state the
    game drives from real time (Documentation/risks.json, Determinism)."""
    rec = receipt(name)
    devices = (rec.get("session") or {}).get("device_profile")
    ran = [r for r in reps if not r.get("error")]
    against = {r["name"]: compare_to(name, r["name"]) for r in ran}
    complete = len(ran) == n

    def every(test) -> bool:
        return bool(ran) and all(test(r) for r in ran)

    ranges = vanilla_rows(name)
    tick_files = {r["tick_sha256"] for r in ran}
    identical = complete and None not in tick_files and len(tick_files) == 1
    goals = {r["end"]["goal_tick"] for r in ran}
    try:
        pin = pinned()
    except Stop:                    # no pin matches nothing, and the summary is still written
        pin = None
    environment = game_environment(name)
    paces = {r["name"]: pace(name, r["name"]) for r in ran}
    ended = {k: end_saves(k) for k in [name] + [r["name"] for r in ran]}
    checks = {
        "every_replay_ran": complete,
        "runs_the_games_own_code": every(lambda r: not r["vanilla_problems"]),
        "same_code_restored": every(lambda r: bool(ranges) and vanilla_rows(r["name"]) == ranges),
        "same_oracle_mode_as_recording": every(lambda r: same_mode([name, r["name"]])),
        "same_probes_as_recording": every(lambda r: against[r["name"]]["headers_same"]),
        "same_device_profile_as_recording": bool(ran) and same_devices(devices, ran, name),
        "goal_on_recording_tick": every(lambda r: r["end"]["goal_tick"] == end["goal_tick"]),
        "no_missed_rows": every(lambda r: r["end"]["misses"] == 0),
        "tick_rows_as_recorded": every(lambda r: against[r["name"]]["rows_same"]),
        "stage_trace_as_recorded": every(lambda r: against[r["name"]]["trace_same"]),
        "named_columns_as_recorded": every(lambda r: against[r["name"]]["named_columns_same"]),
        "differences_only_in_natively_unstable_blocks": every(
            lambda r: against[r["name"]]["differences_only_in_natively_unstable_blocks"]),
        "start_saves_pinned": starts_pinned(name, pin) and every(
            lambda r: starts_pinned(r["name"], pin)),
        "same_game_environment_as_recording": environment is not None and every(
            lambda r: game_environment(r["name"]) == environment),
    }
    reported = {
        "pace_against_recording": paces,
        "pace_off_by_more_than_tolerance": sorted(
            k for k, v in paces.items() if v is not None and abs(v - 1) > PACE_TOLERANCE),
        "pace_unknown": sorted(k for k, v in paces.items() if v is None),
        "tick_captures_identical": {k: a["tick_captures_identical"] for k, a in against.items()},
        # Documentation/decisions/2026-09-23-S4-stage-route.md item 5's words:
        # the two replays "reproduce each other's tick captures" and set the
        # goal latch on the same tick. Its literal clause, which the allowance
        # does not relax (module docstring).
        "replays_tick_captures_identical_to_each_other": identical,
        "stage_route_item_5_met": identical and len(goals) == 1 and 0 not in goals,
        "natively_unstable_blocks_differ": {
            k: {b: t for b, t in a["digest_blocks_differ"].items() if b in NATIVE_UNSTABLE}
            for k, a in against.items()},
        "teardown_exits": ([name] if facts(rec)["teardown_exit"] else [])
        + [r["name"] for r in ran if r.get("teardown_exit")],
        "saves_at_end": ended,
        "replays_end_with_the_recording_s_saves": ended[name] is not None and every(
            lambda r: ended[r["name"]] == ended[name]),
    }
    return {"checks": checks, "reported": reported, "against_recording": against}


def record_command() -> str:
    return f"cd {ROOT} && source tools/env.sh && .venv/bin/python tools/oracle/record_stage.py"


def resume_command(name: str) -> str:
    return f"{record_command()} --replay-only {name}"


def retake_command(name: str) -> str:
    """tools/oracle/retake_replays.py on `name`: what replays a recording this
    tool cannot (predates_word_log)."""
    return (f"cd {ROOT} && source tools/env.sh && .venv/bin/python "
            f"tools/oracle/retake_replays.py {name}")


def predates_word_log(name: str) -> bool:
    """Whether `name` was recorded before the tick probe kept its word log
    (its receipt has no word_log) while every run this tool makes keeps one
    (run_oracle_probe.keeps_word_log over PROBES): then no replay this tool
    makes can be judged against it, and no put-back file changes that (the
    re-take review of 2026-09-25, W3)."""
    return (RP.keeps_word_log([(m, Path(s)) for m, s in PROBES])
            and not isinstance(receipt(name).get("word_log"), dict))


def whole(name: str) -> bool:
    """Whether `name`'s receipt shows a whole capture, which --replay-only
    needs first (check_recording)."""
    try:
        return accepted(receipt(name))[0]
    except (OSError, ValueError):
        return False


def resume(name: str, again: bool = False) -> None:
    """Once the recording's directory exists, how to go on: the command that
    replays it, or, when it is not a whole capture or `again`, record again."""
    if not (CAPTURES / name).is_dir():
        return
    if again or not whole(name):
        print("This recording cannot be replayed, so record again:")
        print(f"    {record_command()}")
        return
    try:
        old = predates_word_log(name)
    except (OSError, ValueError):
        old = False
    if old:
        print(f"Your recording is kept in {CAPTURES / name}. It was made before the word log, "
              "so it is not replayed by this tool; to re-take its replays without playing:")
        print(f"    {retake_command(name)}")
        return
    print(f"Your recording is kept in {CAPTURES / name}")
    print("To replay it again without playing:")
    print(f"    {resume_command(name)}")


def session(replay_only: str | None, name: str, stamp: str) -> int:
    pre = None
    if replay_only:
        if not (CAPTURES / name / "receipt.json").is_file():
            raise Stop(f"{name}: no recording by that name in {CAPTURES}")
    else:
        print(BEFORE)
        pre = preflight(stamp)
        print(f"PREFLIGHT_OK {json.dumps(pre['checks'])}")
        print(INSTRUCTIONS)
        input("Press Enter here when you are ready; the game starts a few seconds later. ")
        rec = run(name, ["INPUT_MODE=1"], RECORD_CAPTURES, CAP)
        warning = cap_warning(rec)
        if warning:
            print(warning)
        print("Leave your devices as they are until the replays finish.")
    end = check_recording(name)
    reps = replay(name, stamp, end)
    result = judge(name, end, reps)
    checks, reported = result["checks"], result["reported"]
    agree = all(checks.values())
    item_5 = reported["stage_route_item_5_met"]
    logs = {n: import_log(n) for n in [name] + [r["name"] for r in reps if not r.get("error")]}
    summary = {"recording": name, "recording_run": facts(receipt(name)), "recording_end": end,
               "import_logs": logs,
               "preflight": pre, "replays": reps,
               "against_recording": result["against_recording"], "checks": checks,
               "reported": reported, "allowance": ALLOWANCE, "replays_agree": agree,
               "stage_route_item_5_met": item_5, "passed_by_allowance": agree and not item_5}
    out = CAPTURES / name / f"replays-{stamp}.json"
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({"checks": checks, **reported}, indent=1))
    print(f"summary: {out}")
    print("\n".join(coverage(logs)))
    for k in reported["pace_off_by_more_than_tolerance"]:
        ratio = reported["pace_against_recording"][k]
        print(f"  WARNING: {k} ran {abs(ratio - 1) * 100:.1f} % "
              f"{'longer' if ratio > 1 else 'shorter'} than the recording by CRI's timer calls. "
              "The pace is a floor, not a lock, and state the game drives from real time "
              "follows it (Documentation/risks.json, Determinism).")
    for k in reported["pace_unknown"]:
        print(f"  WARNING: the pace of {k} against the recording is unknown: an import log is "
              "missing or refused, or the two were counted at different ticks.")
    if not agree:
        print("RECORD_STAGE_REPLAYS_DISAGREE: "
              + ", ".join(k for k, v in checks.items() if not v))
        resume(name)
        return 1
    goal = end["goal_tick"]
    if item_5:
        print(f"RECORD_STAGE_OK: both replays reached the goal on tick {goal} and match your run.")
    else:
        print(f"RECORD_STAGE_OK_BY_ALLOWANCE: both replays reached the goal on tick {goal}; "
              "they match your run only by the allowance below.")
        print(f"  {ITEM_5_NOT_MET}")
    used = False
    for k, blocks in reported["natively_unstable_blocks_differ"].items():
        if blocks:
            used = True
            print(f"  Not byte-identical: in {k} the digest blocks "
                  + ", ".join(f"{b} from tick {t}" for b, t in sorted(blocks.items()))
                  + " differ.")
    if used or not item_5:
        print("\n".join(f"  {line}" for line in ALLOWANCE_LINES))
    return 0


def preflight_only(stamp: str) -> int:
    """Both unattended preflight passes and their verdict, with no prompt, so
    the preflight can be run before the user's session (the recommendation of
    private/generated/s4/nodf/verify-7.md). Nothing is recorded."""
    print("Preflight only: two unattended runs, about a minute in all. Nothing is recorded.")
    pre = preflight(stamp)
    print(json.dumps(pre, indent=1))
    print(f"PREFLIGHT_OK {json.dumps(pre['checks'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--replay-only", default=None, metavar="RECORDING",
                       help="skip the session and replay an existing recording's capture")
    which.add_argument("--preflight-only", action="store_true",
                       help="run the two unattended preflight passes, print the verdict and "
                            "exit (0 on pass); no prompt, nothing recorded")
    args = ap.parse_args(argv)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = args.replay_only or f"mh-record-{stamp}"
    try:
        if args.preflight_only:
            return preflight_only(stamp)
        return session(args.replay_only, name, stamp)
    except Stop as e:
        print(f"\nSTOPPED: {e.code}")
        resume(name, again=isinstance(e, RecordAgain))
        return 1
    except KeyboardInterrupt:
        print("\nSTOPPED: interrupted.")
        resume(name)
        return 130
    except BaseException:
        resume(name)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
