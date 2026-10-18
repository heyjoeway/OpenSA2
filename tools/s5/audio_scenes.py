#!/usr/bin/env python3
"""Write G5's audio-scenes receipt from runs of the lifted game that recorded its sound.

Subtitle: three scripted sessions, each a presentation build with the recorder
(host/sa2_present_rec.c, SA2_REC_AUDIO=1) and an observer that writes the game's state and
the virtual clock at every GameLoop return; the player mixes each stream's sound into a WAV
(--stats-only --audio-wav); and Documentation/receipts/s5-audio-scenes-v1.json names, per
scene, the WAV, its sidecar, the per-tick table and the run's own record, as
Documentation/scripts/check_s5_exit.py audible() reads them.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s5/audio_scenes.py run menu          # the title and the main menu
  .venv/bin/python tools/s5/audio_scenes.py run garden        # a Chao Garden
  .venv/bin/python tools/s5/audio_scenes.py run metalharbor   # Metal Harbor in play
  .venv/bin/python tools/s5/audio_scenes.py receipt           # the receipt, then audible()
  .venv/bin/python tools/s5/audio_scenes.py all               # the player, three runs, the receipt
  (--out DIR moves the runs from private/generated/s5/audio-scenes; --replace runs sessions
   again into their own directories ("Running again"); --player-bin DIR chooses where the
   player is built; --no-build and --no-player-build make runs no receipt stands on)

Why a tool
----------
G5's audio clause reads a receipt that names, for each of four scenes, a WAV, the run that
made its stream, and that run's per-tick table, all tied by digests. The sessions were first
run by hand (agents-journals/s5/2026-10-03-live-play.md); a receipt made by hand is the drift
AGENTS.md rule 7 forbids, and this is the third time the steps are taken (rule 13).

The sessions
------------
  menu         the boot on the boot capture's device profile, with the keyboard script
               KEYS["menu"] (SA2_INPUT_DI_SCRIPT): the opening event, the title, file
               select, NEW FILE, the main menu, STORY SELECT and back; 3600 polls. Its table
               holds the title (GameMode_Advertise, CurrentMenu at Menus_TitleScreen) and the
               main menu (CurrentMenu at Menus_Main).
  garden       the Mod Loader's test spawn into Chao World (level 90, Sonic;
               harness/replay/wrap_test_spawn.c) on the same profile, with KEYS["garden"]: J
               past the empty A-Life save's dialog, then W through the arch into the Neutral
               Garden; 4200 polls. A NEW FILE has no menu route to Chao World (the journal
               above), so the garden is reached by the test spawn, which the user approved
               for G5 on 2026-10-04
               (Documentation/decisions/2026-10-04-S5-user-rulings.md). NOTE, below, says
               so in each receipt this tool writes; the landed
               Documentation/receipts/s5-audio-scenes-v1.json, written before the ruling,
               still calls the scenes the agent's readings until it is regenerated.
  metalharbor  Metal Harbor's registered replay (Documentation/replays.json): its test spawn,
               its recording replayed into the controller (harness/replay/wrap_input_replay.c,
               SA2_INPUT_SCRIPT, at its pin) and its capture's device profile, for its first
               2400 polls. Its tick capture, stage trace, goal-latch column and applied-input
               log must each be the registered result's, row for row, as far as it runs.

Each session links one of two builds, named apart from every gate's and registered replay's
(present_run.build_name_problem): present-as-boot.<tag>, the observer and the recorder; and
present-as-spawn.<tag>, tools/s4/stage_run.py's test-spawn hooks with the observer in the tick
probe's place, and the recorder. Both are on the reach configuration (reach_receipt.ENV) with
stage_run's budgets scaled to the polls, the pinned saves in an empty overlay of the run's
own, and every observer output in the run's own directory.

The observer
------------
The contract's table needs the virtual clock at the end of each tick, which no observer
wrote. GameLoop's exit is harness/replay/wrap_tick_probe.c's wrap, and a symbol is wrapped
once, so the observer (observer_source) is that file unchanged, compiled with its wrap
renamed by the preprocessor, inside a wrap of its own: the tick probe runs exactly as it
does everywhere (tick capture, stage trace, the GameLoop count the other hooks key by), and
when it returns the observer writes one row to SA2_AUDIO_TICKS: the tick, sa2_virtual_us()
(the clock the recorder stamps every FRAME and AUDIO record with, so the WAV's), and the
state words check_s5_exit.STATE_WORDS names, read by the tick probe's own tp_read. Its
columns are check_s5_exit.AUDIO_TICKS. It reads guest memory and the clock, and writes
nothing the guest can see. The source is generated into the output directory and linked as
a hook; its sha256 is in every run's record.

What a run holds
----------------
Its record (audio-run.json) keeps each check, and `ok` is all of them:
  build          present_run.check_build: every hook and the recorder compiled afresh from
                 this tree, the recorder object this tree's source compiled again and held to
                 tools/s5/objcheck.py, the binary the one that ran
  run            loop_run's exit status 0 and the probe's own record `ok`
  configuration  the device profile the run was handed is the one it read
                 (device_profile.check_run), and every device switch is on
                 (reach_receipt.switch_problems; a keyboard session's own host_input is
                 held by `input` instead)
  saves          the pinned saves, the installation at its pin, an empty overlay of its own
  input          a keyboard session: the runtime's keyboard read the script and nothing else
                 (dinput.host_input: source "script", every line, keys applied, no record from
                 a FIFO); metalharbor: the recording at its registry pin
  ticks          the observer's table: header AUDIO_TICKS, one row per GameLoop return
                 numbered from 1, as many as the tick capture's and the stage trace's rows, the
                 clock advancing on every tick, and CurrentLevel, GameMode and GameState equal
                 to the stage trace's at every tick
  registered     metalharbor only: its tick capture, stage trace, goal-latch column and
                 applied-input log each the registered result's file, which hashes to its pin,
                 for as many rows as the run made
  stream         present_run.check_stream with this run's meta: whole and true to its rules,
                 its counts the recorder's, held to the run's record and the audio devices'
                 record, a version 2 stream of AUDIO records from a recorder that wrapped every
                 `audio` line; and present_run.audio_counts
  player         the player's --stats-only replay passed (every stream check that needs no
                 device), read the stream to its end and hashed it to the stream's sha256
  wav            the sidecar says the WAV is whole: ok, float32 stereo 48 kHz, no audio
                 problem, the frames the WAV holds, and the stream's sha256
  tree           every file `pins` names hashes at the run's end as it did when its call
                 began, before the player's build: no file the run is made of moved under it
When all hold, READY is written beside the stream (its sha256, then the run's meta, as
present_run.write_ready writes it), and the record says `ready` {path, stream,
stream_sha256}. The record is in present_run.py's present-run.json shape where
check_s5_exit reads it: `ok`, `audio` (the stream carries sound), `ready.stream_sha256` and
`ticks`, the table's {path, sha256}. Its schema is its own (RUN_SCHEMA). Its `inputs` say
what made it: the pins, the runtime digest, `fresh_build` (every hook, the observer among
them, compiled afresh: not --no-build) and `player_built` (the player built from the tree
by the call that ran it: not --no-player-build); `files.observer` is the observer it linked.

Running again
-------------
A session directory is used once. --replace runs sessions again: every refusal (a directory
an interrupted --replace left aside, a gate pass, another presentation or audio-scenes run,
the player's build, each session's own) comes before anything on disk moves; then each
session's old directory is set aside (.<name>.previous beside it) and the session runs into
its own path, so the paths its record and the receipt name stay the same. When every session
of the call is ok the old runs are removed; otherwise, or when the call is stopped, each old
run is put back where the receipt cites it and this call's run is kept at <name>.unkept for
its logs. Only a directory under private/generated is moved or removed (REPLACEABLE). While
the sessions run, the old runs are aside: no gate pass reads them then (refuse_busy refuses
to start beside one; never start one beside this).

The receipt
-----------
`receipt` reads the three records and refuses one that is not ok, that recorded no sound,
that was made on --no-build or --no-player-build, that names a file whose bytes are not the
ones it recorded, or whose pins (PIPELINE and the runtime's sources), runtime digest or
observer are not the tree's: the receipt pins the tree's, so every run behind it must be
theirs. It writes the scenes as check_s5_exit's contract gives them (the title and the main
menu share the menu session's files), and no verdict: audible() recomputes it, and `receipt`
prints what it says. Each scene's held seconds, as audible() cuts them, are written to
<out>/scene-levels.json beside the runs (scene_levels), so a journal cites a generated file
rather than typing them (AGENTS.md rule 7).

What it does not show
---------------------
* "Audible" is the player's mix measured, not heard (check_s5_exit's criterion note).
* The menu and garden sessions stand for no native capture: they show the lifted game's
  sound in those scenes, not that native's is the same. Metal Harbor's is held to the
  registered lifted run, which the registered replay holds to native.
* The garden comes from the test spawn, and the save files are the pinned ones; a player
  who reaches a garden from a story save is not shown.

Never run it during a gate pass, beside another presentation run or another of its own runs
(it refuses all three: they share the builds), or with SDL's audio on a device: the player
gets SDL_AUDIO_DRIVER=dummy and --stats-only opens no device at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_s5_exit as X  # noqa: E402  the contract: AUDIO_TICKS, STATE_WORDS, audible()
import present_run as PR  # noqa: E402  the recorder's hooks, builds and stream checks
import stage_run as SR  # noqa: E402  the test spawn's hooks, wraps and Metal Harbor's spawn

TR, RR, SS, DP = PR.TR, PR.RR, PR.SS, PR.DP
TOOL = X.AUDIO_TOOL
RUN_SCHEMA = "sa2-s5-audio-scene-run/v1"
RECORD = "audio-run.json"
DEFAULT_OUT = Path(PR.GEN_S5) / "audio-scenes"
OBSERVER_VAR = "SA2_AUDIO_TICKS"
OBSERVER_FILE = "sa2_audio_ticks.c"
# The tick probe's GameLoop wrap, renamed inside the observer (observer_source).
RENAMED_WRAP = "sa2_audio_scenes_tick_probe"
SPAWN_BUILD, BOOT_BUILD = "present-as-spawn", "present-as-boot"
ALARM_S = 900                         # the probe's wall-clock alarm around the guest call
PLAYER_TIMEOUT_S = 3600
MIN_FREE_BYTES = 30 << 30
# The only tree --replace moves or removes a directory in ("Running again").
REPLACEABLE = ROOT / "private" / "generated"
# The player as host/player/build.sh builds sa2_player and run_player.sh runs it: the
# scripts, the expectations generator and what it reads (dxvk.conf, tools/pins.json), and
# every source and header the binary is compiled from (host/sa2_stream.h is the recorder's),
# the host tweaks' reader and every player tweak's files among them (host/player/tweaks/, which
# build.sh compiles by glob and player.cpp includes: runtime/sa2_tweaks.h, "Add a host tweak").
PLAYER = ("host/player/build.sh", "host/player/run_player.sh", "host/player/player_env.sh",
          "host/player/generate_expectations.py", "host/player/dxvk.conf", "tools/pins.json",
          "host/player/player.cpp", "host/player/input.cpp", "host/player/mixer.cpp",
          "host/player/stream.cpp", "host/player/sha256.cpp", "host/player/texcodec.cpp",
          "host/player/input.h", "host/player/mixer.h", "host/player/names.h", "host/player/pace.h",
          "host/player/sha256.h", "host/player/stream.h", "host/player/texcodec.h",
          "host/player/sdl_mark.h", "host/player/build_record.py", "host/player/touch_overlay.h",
          "host/player/prof.h", "host/player/tweaks.h", "host/player/tweaks.cpp",
          *sorted(f"host/player/tweaks/{p.name}" for p in (ROOT / "host" / "player" / "tweaks").glob("*")
                  if p.suffix in (".cpp", ".h")))
# What the runs are made of, beside the lifted image: this tool; the recorder and its stream
# format; tools/s4/tick_replay.py's lifted pipeline for a stage run (the link, the probe, the
# tick probe and its headers, the device profile, the saves, tools/s4/stage_run.py and every
# hook it links) and the tick probe's word-log header, which that list leaves out; tick_replay
# itself and the registry, which name the hooks, the profiles, Metal Harbor's recording and
# its registered result; the code that decides a run's `ok`; and the player. Pinned, with the
# runtime's sources (pins), in every record, and in the receipt once every record agrees with
# the tree, where Documentation/scripts/check_receipt_pins.py holds them.
PIPELINE = tuple(dict.fromkeys([
    "tools/s5/audio_scenes.py", PR.RECORDER, PR.STREAM_H, *TR.LIFTED_PIPELINE, *TR.STAGE_LIFTED,
    *SR.HOOKS, "harness/replay/word_log_core.h", "tools/s4/tick_replay.py",
    "Documentation/replays.json", "tools/s5/present_run.py", "tools/s5/stream_dump.py",
    "tools/s5/objcheck.py", *PLAYER]))

# The keyboard scripts (runtime/sa2_dinput.h, SA2_INPUT_DI_SCRIPT: first and last poll,
# inclusive, and DIK codes), as found on 2026-10-03 (agents-journals/s5/2026-10-03-live-play.md,
# "Menu and Chao Garden sound"). Player 1 under private/install's Config/Keyboard.cfg: Enter
# 0x1C is Start, W 0x11 up, S 0x1F down, J 0x24 A (confirm), K 0x25 B (back). A key held at
# poll N acts at tick N-1.
KEYS = {
    "menu": """\
# The boot, the title, file select, NEW FILE, the main menu, STORY SELECT and back.
600 605 0x1C     # Enter: the opening event -> the title
1000 1005 0x1C   # Enter: the title -> file select
1300 1305 0x1C   # Enter: NEW FILE -> the main menu
2300 2303 0x1F   # S: the cursor down to 2P BATTLE
2400 2403 0x11   # W: the cursor back up to 1P PLAY
2500 2505 0x1C   # Enter: 1P PLAY -> STORY SELECT
2900 2905 0x25   # K: back to the main menu
""",
    "garden": """\
# Chao World from the test spawn: the empty A-Life save's dialog, the Chao Lobby, the garden.
800 805 0x24     # J: continue without saving, into the Chao Lobby
1300 1700 0x11   # W: through the stone arch ahead, into the Neutral Garden
""",
}


@dataclass(frozen=True)
class Session:
    name: str
    scenes: tuple[str, ...]
    polls: int
    profile_of: str                        # the registered replay whose capture's profile it reads
    spawn: tuple[int, int, str] | None     # the test spawn's level, character, guest command line
    keys: str | None                       # KEYS' script, or None
    recording: bool                        # the registered stage replay's recording, into the controller


SESSIONS = {
    "menu": Session("menu", ("title", "menu"), 3600, "boot", None, "menu", False),
    "garden": Session("garden", ("garden",), 4200, "boot",
                      (X.LEVEL_CHAO_WORLD, 0, "--level chaoworld --character sonic"), "garden", False),
    "metalharbor": Session("metalharbor", ("metalharbor",), 2400, "metalharbor",
                           SR.STAGES[X.STAGE], None, True),
}
SCENE_SESSION = {scene: s.name for s in SESSIONS.values() for scene in s.scenes}
NOTE = ("The user's ruling 4 of 2026-10-04 "
        "(Documentation/decisions/2026-10-04-S5-user-rulings.md) confirmed the four scenes "
        "and approved test-spawn starts for G5. The title and the main "
        "menu are one scripted keyboard session from the boot through NEW FILE "
        "(SA2_INPUT_DI_SCRIPT). The garden is reached through the Mod Loader's test spawn "
        "into Chao World (harness/replay/wrap_test_spawn.c), then walked into the Neutral "
        "Garden by a keyboard script, because a NEW FILE offers no menu route to Chao World "
        "(agents-journals/s5/2026-10-03-live-play.md). Metal Harbor is the registered "
        "replay's recording from its test spawn, for the session's polls, held row for row "
        "to the registered lifted run. Each WAV is the player's --stats-only mix of the "
        "stream its run made; no sound device was opened.")


# ---------------------------------------------------------------- small things

def rel(p: Path, root: Path = ROOT) -> str:
    """`p` relative to `root` when it lies under it, without following links; a path outside
    `root` stays absolute, and a relative `p` is taken as given (present_run.rel's rule)."""
    p = Path(p)
    if not p.is_absolute():
        return str(p)
    a, r = Path(os.path.abspath(p)), Path(os.path.abspath(root))
    return str(a.relative_to(r)) if a.is_relative_to(r) else str(a)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def ref(path: Path, root: Path = ROOT) -> dict:
    """An evidence reference as check_s5_exit reads one: repository-relative, and its sha256.
    A file outside `root` has no such reference, and check_s5_exit.evidence would refuse one."""
    named = rel(path, root)
    if Path(named).is_absolute():
        raise ValueError(f"{named} is not under {root}: an evidence reference is repository-relative")
    return {"path": named, "sha256": sha256(path)}


def pins(root: Path = ROOT, pipeline: tuple[str, ...] = PIPELINE) -> dict[str, str]:
    """Each file of `pipeline`, then each runtime source (reach_receipt.RUNTIME_GLOBS, what
    runtime_digest is made of), by repository path, and its sha256. The runtime's files are
    pinned by name as well as digested because check_receipt_pins.py reads a receipt's
    `inputs.pipeline` and nothing beside it (tools/s5/poll_handoff_check.py runtime_pins)."""
    runtime = [str(p.relative_to(root)) for g in RR.RUNTIME_GLOBS for p in sorted(root.glob(g))]
    return {name: sha256(root / name) for name in dict.fromkeys([*pipeline, *runtime])}


def observer_sha256(root: Path = ROOT) -> str:
    """The sha256 of the observer this tree generates (observer_source), as it is written."""
    return hashlib.sha256(observer_source(root).encode()).hexdigest()


def tree_now(root: Path = ROOT, pipeline: tuple[str, ...] = PIPELINE) -> dict:
    """What a run behind the receipt must have been made by: the tree's pins, its runtime
    digest and its observer."""
    return {"pipeline": pins(root, pipeline), "runtime_sha256": RR.runtime_digest(root),
            "observer_sha256": observer_sha256(root)}


def key_lines(script: str) -> int:
    """The lines of a keyboard script the runtime counts (script_lines): not blank, not
    only a comment."""
    return sum(1 for line in script.splitlines() if line.split("#", 1)[0].strip())


def key_presses(script: str) -> int:
    """The keys a keyboard script holds, summed over its polls: what the runtime's
    keys_written counts when every poll the script names is made (runtime/sa2_dinput.c
    host_keys adds the keys a poll holds, each key once)."""
    held = set()
    for line in script.splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            first, last, key = (int(w, 0) for w in words)
            held.update((poll, key) for poll in range(first, last + 1))
    return len(held)


def build_name(session: Session) -> str:
    return f"{SPAWN_BUILD if session.spawn else BOOT_BUILD}.{PR.tree_tag()}"


# ---------------------------------------------------------------- the observer

def observer_source(root: Path = ROOT) -> str:
    """The observer's C source (the module docstring, "The observer"): the tick probe of
    `root`, included by absolute path with its wrap renamed, and a wrap around it that
    writes check_s5_exit.AUDIO_TICKS' row after it returns."""
    if X.AUDIO_TICKS[:2] != ("tick", "virtual_us"):
        raise SystemExit(f"check_s5_exit.AUDIO_TICKS begins {X.AUDIO_TICKS[:2]}, not the tick "
                         "and the clock this observer writes first")
    reads = []
    for column in X.AUDIO_TICKS[2:]:
        _, va, width = X.STATE_WORDS[column]
        reads.append(f"tp_read(0x{va:08X}u, {width})")
    header = "\\t".join(X.AUDIO_TICKS) + "\\n"
    row = "\\t".join(["%lu", "%llu"] + ["%u"] * len(reads)) + "\\n"
    probe = (root / TR.HOOK).resolve()
    return f"""/* Generated by tools/s5/audio_scenes.py (observer_source); do not edit.
 *
 * The audio scenes' observer: harness/replay/wrap_tick_probe.c, unchanged, with its
 * GameLoop wrap renamed, and a wrap around it that writes one row per GameLoop return to
 * {OBSERVER_VAR}: the tick (the tick probe's count), the virtual clock at the tick's end
 * (sa2_virtual_us, the clock the recorder stamps FRAME and AUDIO records with) and the state
 * words Documentation/scripts/check_s5_exit.py names (STATE_WORDS), in AUDIO_TICKS' order,
 * read with the tick probe's own tp_read. It reads guest memory and the clock and writes
 * nothing the guest can see.
 */
#define __wrap_{TR.WRAP} {RENAMED_WRAP}
#include "{probe}"
#undef __wrap_{TR.WRAP}

uint64_t sa2_virtual_us(void);      /* runtime/sa2_imports.c */

static FILE *audio_out;
static int audio_off;

static void audio_row(unsigned long t) {{
    if (!audio_out && !audio_off) {{
        const char *path = getenv("{OBSERVER_VAR}");
        if (!path || !*path || !(audio_out = fopen(path, "w"))) {{
            audio_off = 1;
            return;
        }}
        fputs("{header}", audio_out);
    }}
    if (!audio_out)
        return;
    fprintf(audio_out, "{row}", t, (unsigned long long)sa2_virtual_us(),
            {", ".join(reads)});
    fflush(audio_out);          /* a run can end in a trap; every row must be on disk */
}}

void *__wrap_{TR.WRAP}(void *state, uint32_t pc, void *memory);
void *__wrap_{TR.WRAP}(void *state, uint32_t pc, void *memory) {{
    void *r = {RENAMED_WRAP}(state, pc, memory);
    audio_row(sa2_replay_gameloop_returns());
    return r;
}}
"""


def hooks(session: Session, observer: str) -> tuple[list[str], list[str]]:
    """The session's hooks and wraps before the recorder's: the observer in the tick probe's
    place, and for a test spawn every other hook of tools/s4/stage_run.py's."""
    if session.spawn:
        hs, ws = list(SR.HOOKS), list(SR.WRAPS)
    else:
        hs, ws = [TR.HOOK], [TR.WRAP]
    if hs.count(TR.HOOK) != 1:
        raise SystemExit(f"the hooks name {TR.HOOK} {hs.count(TR.HOOK)} times, not once")
    return [observer if h == TR.HOOK else h for h in hs], ws


# ---------------------------------------------------------------- the run

def paths(out: Path, name: str) -> dict:
    d = out / name
    return {"dir": d, "stream": d / "stream.sa2s", "status": d / "stream.sa2s.status",
            "ready": d / "stream.READY", "counts": d / "counts.json",
            "imports": d / "imports.table.json", "run": d / "run.json", "matrix": d / "matrix.json",
            "log": d / "loop_run.log", "capture": d / "lifted.csv", "stage_trace": d / "stage-trace.csv",
            "table": d / "audio-ticks.tsv", "goal_latch": d / "goal-latch.csv",
            "input_applied": d / "input-applied.csv", "keys": d / "keys.txt", "keys_log": d / "keys.log",
            "overlay": d / "vfsroot", "player": d / "player", "wav": d / "player" / "sound.wav",
            "sidecar": d / "player" / "sound.wav.json", "report": d / "player" / "report.json",
            "player_log": d / "player" / "player.log", "record": d / RECORD}


def run_env(session: Session, p: dict, meta: dict, profile_env: dict,
            recording: Path | None = None) -> dict[str, str]:
    """The run's settings: the reach configuration with stage_run's budgets scaled to the
    polls, the device profile's, the test spawn's, the observers' outputs and the recorder's
    (all in the run's own directory), and the session's input."""
    polls = session.polls
    env = dict(RR.ENV, SA2_PUMP_POLLS=str(polls), SA2_IMPORT_BUDGET=str(max(8_000_000, polls * 5000)),
               SA2_VIRTUAL_BUDGET_MS=str(max(30000, polls * 20)))
    env.update(profile_env)
    if session.spawn:
        level, character, cmdline = session.spawn
        env.update(SA2_TEST_SPAWN_LEVEL=str(level), SA2_TEST_SPAWN_CHARACTER=str(character),
                   SA2_GUEST_CMDLINE=cmdline)
    env.update({"SA2_VFS_WRITE_ROOT": rel(p["overlay"]), "SA2_REPLAY_TICKS": rel(p["capture"]),
                "SA2_STAGE_TRACE": rel(p["stage_trace"]), OBSERVER_VAR: rel(p["table"]),
                "SA2_REC_TRACE": rel(p["stream"]), "SA2_REC_COUNTS": rel(p["counts"]),
                "SA2_REC_AUDIO": "1",
                "SA2_REC_META": json.dumps(meta, separators=(",", ":"), sort_keys=True)})
    if session.keys:
        env.update(SA2_INPUT_DI_SCRIPT=rel(p["keys"]), SA2_INPUT_DI_LOG=rel(p["keys_log"]))
    if session.recording:
        if recording is None:
            raise SystemExit("a recording session needs the recording's path")
        env.update(SA2_INPUT_SCRIPT=str(recording.resolve()), SA2_INPUT_APPLIED=rel(p["input_applied"]),
                   SA2_GOAL_LATCH=rel(p["goal_latch"]))
    for k, v in env.items():
        if "|" in v:
            raise SystemExit(f"a run setting carries `|`, loop_run's separator: {k}={v!r}")
    return env


def command(session: Session, p: dict, build: str, env: dict[str, str], observer: str,
            link: bool = True) -> list[str]:
    """loop_run's command: the build, the session's hooks and the recorder's, one run."""
    hs, ws = hooks(session, observer)
    cmd = [str(TR.PY), str(ROOT / "tools" / "s4" / "loop_run.py"), "--tag", RR.TAG,
           "--main", RR.MAIN, "--name", build, "--out-prefix", build]
    for h in hs:
        cmd += ["--hook", h]
    for w in ws:
        cmd += ["--wrap", w]
    cmd += ["--hook", PR.RECORDER]
    for w in PR.WRAPS:
        cmd += ["--wrap", w]
    cmd += ["--timeout", str(ALARM_S + 600),
            "--run", "|".join([session.name] + [f"{k}={v}" for k, v in env.items()])]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", f"--probe-arg={ALARM_S}",
            "--probe-arg=--dump-imports", f"--probe-arg={rel(p['imports'])}"]
    if not link:
        cmd.append("--no-build")
    return cmd


def game_env() -> dict[str, str]:
    """This process's environment for loop_run and the player, less every SA2_ variable:
    loop_run hands the guest os.environ under the run's own settings, the runtime reads
    dozens of SA2_ variables, and no build tool reads one, so the run's settings come from
    its spec alone (stricter than present_run.clean_env, which drops the device, recorder
    and host-keyboard variables)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}


# ---------------------------------------------------------------- the checks

def read_stage_trace(path: Path) -> list[tuple[int, int, int, int]]:
    """(tick, current_level, game_mode, game_state) per row of the tick probe's stage trace."""
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith("tick,current_level,game_mode,game_state,"):
        raise ValueError(f"{rel(path)} is not a stage trace")
    out = []
    for line in lines[1:]:
        c = line.split(",")
        out.append((int(c[0]), int(c[1], 0), int(c[2], 0), int(c[3], 0)))
    return out


def ticks_check(table: Path, stage_trace: Path, capture: Path) -> dict:
    """The observer's table against the contract and the run's own tick probe."""
    out: dict = {"path": rel(table)}
    try:
        rows = X.integers(X.table(table, X.AUDIO_TICKS, rel(table)), X.AUDIO_TICKS, rel(table))
    except (X.Refused, OSError) as e:
        return {**out, "ok": False, "problems": [str(e)]}
    problems = []
    if [r["tick"] for r in rows] != list(range(1, len(rows) + 1)):
        problems.append("tick does not run 1, 2, 3, ... with one row each")
    stalls = [b["tick"] for a, b in zip(rows, rows[1:]) if b["virtual_us"] <= a["virtual_us"]]
    if stalls:
        problems.append(f"virtual_us does not advance at tick {stalls[0]} ({len(stalls)} ticks)")
    wide = [r["tick"] for r in rows for c in X.AUDIO_TICKS[2:] if X.STATE_WORDS[c][2] < 4
            and r[c] >= 1 << (8 * X.STATE_WORDS[c][2])]
    if wide:
        problems.append(f"a state word wider than its width at tick {wide[0]} (unreadable)")
    try:
        trace = read_stage_trace(stage_trace)
    except (OSError, ValueError, IndexError) as e:
        trace = None
        problems.append(f"the stage trace: {e}")
    captured = (len(capture.read_text().splitlines()) - 1) if capture.is_file() else None
    if captured != len(rows):
        problems.append(f"{len(rows)} rows; the tick capture has {captured}")
    if trace is not None:
        if len(trace) != len(rows):
            problems.append(f"{len(rows)} rows; the stage trace has {len(trace)}")
        for r, (tick, level, mode, state) in zip(rows, trace):
            if (r["tick"], r["current_level"], r["game_mode"], r["game_state"]) != (tick, level, mode, state):
                problems.append(f"tick {r['tick']}: level {r['current_level']}, mode {r['game_mode']}, "
                                f"state {r['game_state']}; the stage trace's tick {tick} reads "
                                f"{level}, {mode}, {state}")
                break
    out.update({"rows": len(rows), "first_virtual_us": rows[0]["virtual_us"],
                "last_virtual_us": rows[-1]["virtual_us"], "problems": problems, "ok": not problems})
    return out


def prefix_check(mine: Path, theirs: Path, pin: str | None) -> dict:
    """`mine`'s lines are the first lines of `theirs`, which hashes to `pin`."""
    out: dict = {"file": rel(mine), "registered": rel(theirs)}
    if not mine.is_file() or not theirs.is_file():
        return {**out, "ok": False, "why": "a file is absent"}
    if sha256(theirs) != pin:
        return {**out, "ok": False, "why": f"{rel(theirs)} does not hash to its pin {pin}"}
    a, b = mine.read_text().splitlines(), theirs.read_text().splitlines()
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    out.update({"lines": len(a), "registered_lines": len(b), "first_differing_line": first})
    out["ok"] = first is None and 1 < len(a) <= len(b)
    return out


def registered_check(p: dict, result: dict) -> dict:
    """Metal Harbor's session against the registered result's own files."""
    lifted = result["lifted"]
    out = {"capture": prefix_check(p["capture"], ROOT / lifted["capture"], lifted["sha256"]),
           "stage_trace": prefix_check(p["stage_trace"], ROOT / lifted["stage_trace"]["path"],
                                       lifted["stage_trace"]["sha256"]),
           "goal_latch": prefix_check(p["goal_latch"], ROOT / lifted["goal_latch"]["path"],
                                      lifted["goal_latch"]["sha256"]),
           "input_applied": prefix_check(p["input_applied"], ROOT / lifted["input"]["applied"]["path"],
                                         lifted["input"]["applied"]["sha256"])}
    out["ok"] = all(c["ok"] for c in out.values())
    return out


def keyboard_check(run_rec: dict, script: str) -> dict:
    """The runtime's keyboard read the session's script and nothing else."""
    host = (run_rec.get("dinput") or {}).get("host_input")
    if not isinstance(host, dict):
        return {"ok": False, "why": "the run's record keeps no dinput.host_input"}
    want = {"source": "script", "script_lines": key_lines(script), "records": 0, "seq_gaps": 0,
            "partial_dropped": 0, "keys_written": key_presses(script)}
    wrong = {k: host.get(k) for k, v in want.items() if host.get(k) != v}
    return {"host_input": host, "want": want, "wrong": wrong, "ok": not wrong}


def configuration_check(run_rec: dict, profile, env: dict, keyboard: bool) -> dict:
    """present_run.check_configuration, but a keyboard session's host_input is `input`'s."""
    problems = []
    try:
        DP.check_run(run_rec, profile)
    except DP.Refusal as e:
        problems.append(str(e))
    for line in RR.switch_problems(run_rec, env):
        if keyboard and line.startswith("the run's record reads dinput.host_input"):
            continue
        problems.append(line)
    return {"problems": problems, "ok": bool(run_rec) and not problems}


def saves_check(saves: dict | None, p: dict) -> dict:
    problems = []
    if not isinstance(saves, dict):
        return {"ok": False, "why": "the run kept no record of its save reset"}
    if saves.get("installation_matches_pin") is not True:
        problems.append("the installation was not at its pin")
    if saves.get("overlay") != rel(p["overlay"]):
        problems.append(f"the reset cleared {saves.get('overlay')!r}, not the run's own overlay")
    if saves.get("left") != {}:
        problems.append(f"the overlay held {sorted(saves.get('left') or {})[:5]} after the reset")
    return {"pin": (saves.get("pin") or {}).get("digest"), "overlay": saves.get("overlay"),
            "problems": problems, "ok": not problems}


def player_check(report: Path, rc: int | None, stream_sha: str | None) -> dict:
    """The player's --stats-only replay: passed, the stream read to its end, its sha256."""
    try:
        rep = json.loads(report.read_text())
    except (OSError, ValueError) as e:
        return {"ok": False, "why": f"no player report: {e}"}
    problems = []
    if rc != 0 or rep.get("exit_status") != 0:
        problems.append(f"the player exited {rc} (report {rep.get('exit_status')!r}): "
                        f"{rep.get('failures')}")
    if rep.get("complete") is not True:
        problems.append("the report is not final")
    if rep.get("stream_sha256") != stream_sha or rep.get("stream_records_read_to_end") is not True:
        problems.append("the player did not read this stream to its end")
    records = X.field(rep, "audio.stream_records")
    if not isinstance(records, int) or records < 1:
        problems.append(f"the player read {records!r} AUDIO records")
    return {"report": rel(report), "audio_records": records, "presents": rep.get("presents"),
            "problems": problems, "ok": not problems}


def wav_check(wav: Path, sidecar: Path, stream_sha: str | None) -> dict:
    """The sidecar as audible() reads it, and the WAV it describes."""
    try:
        side = json.loads(sidecar.read_text())
        offset, frames = X.wav_layout(wav, rel(wav))
    except (OSError, ValueError, X.Refused) as e:
        return {"ok": False, "why": str(e)}
    want = {"ok": True, "format": "float32", "sample_rate": X.SAMPLE_RATE, "channels": X.CHANNELS,
            "audio_problems": 0, "frames": frames, "stream_sha256": stream_sha}
    wrong = {k: side.get(k) for k, v in want.items() if side.get(k) != v}
    return {"frames": frames, "seconds": round(frames / X.SAMPLE_RATE, 3),
            "origin_us": side.get("origin_us"), "data_offset": offset, "wrong": wrong,
            "ok": not wrong and isinstance(side.get("origin_us"), int)}


def verdict(checks: dict) -> bool:
    """Every check present and ok, and at least one: no checks is no evidence."""
    return bool(checks) and all(c.get("ok") is True for c in checks.values())


def moved(was: dict, now: dict) -> list[str]:
    """The names whose digests differ between two pin maps, or that one of them lacks."""
    return sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))


def tree_check(made: dict[str, str], root: Path = ROOT, pipeline: tuple[str, ...] = PIPELINE) -> dict:
    """The files the run pinned at its start (`made`), hashed again at its end."""
    files = moved(made, pins(root, pipeline))
    return {"moved": files, "ok": not files}


# ---------------------------------------------------------------- one session

def is_audio_scenes_run(argv: list[str]) -> bool:
    """A Python process running this tool's sessions (`run` or `all`), in any tree: two
    would share the present-as-* builds. present_live.is_presentation_run sees the tool as
    well as its loop_run children, so a live session refuses to start beside one; this
    predicate is the tool's own reading, which names the run as an audio-scenes run."""
    if not argv or not Path(argv[0]).name.startswith("python"):
        return False
    args = argv[1:]
    return any(a.endswith(TOOL) for a in args) and any(a in ("run", "all") for a in args)


def refuse_busy() -> None:
    import present_live as PL
    scenes = PL.processes(is_audio_scenes_run)
    busy = [f"gate pass {g}" for g in PL.gate_passes()]
    # A presentation run is an audio-scenes run too when it is this tool: named once, as one.
    busy += [f"presentation run {g}" for g in PL.processes(PL.is_presentation_run)
             if g not in scenes]
    busy += [f"audio-scenes run {g}" for g in scenes]
    if busy:
        raise SystemExit("refusing: " + "; ".join(busy))


def player_run(p: dict, bin_dir: Path) -> tuple[int, float]:
    """The player's --stats-only replay of the stream, its sound to the WAV: no window, no
    DXVK, no sound device (SDL_AUDIO_DRIVER=dummy all the same)."""
    p["player"].mkdir(parents=True, exist_ok=True)
    env = dict(game_env(), OUT=str(bin_dir), SA2_PLAY_OUT=str(p["player"]), SDL_AUDIO_DRIVER="dummy")
    env.pop("DXVK_LOG_PATH", None)
    cmd = ["bash", str(ROOT / "host" / "player" / "run_player.sh"), rel(p["stream"]), "--stats-only",
           "--stage", "3", "--audio-wav", rel(p["wav"]), "--report", rel(p["report"])]
    t = time.time()
    with open(p["player_log"], "w") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        rc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env,
                            timeout=PLAYER_TIMEOUT_S).returncode
    return rc, round(time.time() - t, 2)


@dataclass(frozen=True)
class Plan:
    """A session once every refusal it can meet before it runs has passed (plan)."""
    session: Session
    p: dict
    build: str
    profile: object
    profile_env: dict
    recording: Path | None
    result: dict | None


def plan(session: Session, out: Path, bin_dir: Path, link: bool) -> Plan:
    """The session's refusals that need no run: the free space, the lifted objects the link
    would read, the player, the build's name, the registry's profile and recording, and with
    --no-build an observer already written. Nothing on disk moves here."""
    import rebuild_image as RI
    if shutil.disk_usage(out if out.exists() else ROOT).free < MIN_FREE_BYTES:
        raise SystemExit(f"under {MIN_FREE_BYTES >> 30} GB free")
    stale, fold = RI.staged_not_rebuilt(), RI.fold_code_problems()
    if stale or fold:
        raise SystemExit(f"the link would read objects that are not the rebuild's: {stale[:3]} {fold}")
    player = bin_dir / "sa2_player"
    if not player.is_file():
        raise SystemExit(f"no player at {rel(player)} (build it: OUT={rel(bin_dir)} bash "
                         "host/player/build.sh, or run without --no-player-build)")
    build = build_name(session)
    if PR.build_name_problem(build):
        raise SystemExit(PR.build_name_problem(build))
    entry = PR.registered(session.profile_of)["entry"]
    profile = TR.native_device_profile(entry)
    profile_env = RR.profile_env(profile)
    recording, result = None, None
    if session.recording:
        reg = PR.registered(X.STAGE)
        result = reg["result"]
        recording = ROOT / reg["entry"]["input"]["script"]
        if not recording.is_file() or sha256(recording) != reg["entry"]["input"]["sha256"]:
            raise SystemExit(f"{reg['entry']['input']['script']} is missing or not at its pin")
    if not link and not (out / "hook" / OBSERVER_FILE).is_file():
        raise SystemExit("--no-build, and no observer source was written: build once first")
    return Plan(session, paths(out, session.name), build, profile, profile_env, recording, result)


def run_session(pl: Plan, out: Path, bin_dir: Path, made: dict[str, str], link: bool = True,
                player_built: bool = True) -> dict:
    """Build, run, replay into the WAV, check; the record, written to the run's directory,
    which must not exist yet (run_sessions sets an old one aside). `made` is the tree's pins
    when the call began, before the player's build: the record's, held to the tree at the end."""
    refuse_busy()
    session, p = pl.session, pl.p
    hook_dir = out / "hook"
    hook_dir.mkdir(parents=True, exist_ok=True)
    observer_path = hook_dir / OBSERVER_FILE
    observer = rel(observer_path)
    if link:
        observer_path.write_bytes(observer_source().encode())
    p["dir"].mkdir(parents=True)
    p["overlay"].mkdir()
    if session.keys:
        p["keys"].write_text(KEYS[session.keys])
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{secrets.token_hex(4)}"
    meta = {"runner": TOOL, "build": pl.build, "session": session.name, "run": run_id,
            "audio": True, "commit": PR.git_head()}
    env = run_env(session, p, meta, pl.profile_env, pl.recording)
    cmd = command(session, p, pl.build, env, observer, link)
    hs, _ = hooks(session, observer)
    if link:
        PR.forget_hook_objects(pl.build, tuple(hs))
    saves = SS.reset_lifted(cmd, ROOT)
    SS.require_empty(saves)
    run_json = PR.RUN_DIR / f"{pl.build}.{session.name}.json"
    matrix = PR.RUN_DIR / f"{pl.build}.matrix.json"
    for f in (run_json, matrix):
        f.unlink(missing_ok=True)
    t = time.time()
    with open(p["log"], "w") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        rc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=game_env(),
                            timeout=ALARM_S + 2400).returncode
    seconds = round(time.time() - t, 2)
    for src, dst in ((run_json, p["run"]), (matrix, p["matrix"])):
        if src.is_file():
            shutil.move(str(src), str(dst))
    run_rec = json.loads(p["run"].read_text()) if p["run"].is_file() else {}
    stream_sha = sha256(p["stream"]) if p["stream"].is_file() else None
    player_rc, player_s = player_run(p, bin_dir) if stream_sha else (None, 0.0)

    checks: dict = {}
    checks["build"] = PR.check_build(pl.build, p["matrix"], tuple(hs))
    checks["run"] = {"loop_run_rc": rc, "probe_ok": run_rec.get("ok"),
                     "stop": (run_rec.get("trap") or {}).get("note"),
                     "ok": rc == 0 and run_rec.get("ok") is True}
    checks["configuration"] = configuration_check(run_rec, pl.profile, {**RR.ENV, **pl.profile_env},
                                                  bool(session.keys))
    checks["saves"] = saves_check(saves, p)
    if session.keys:
        checks["input"] = keyboard_check(run_rec, KEYS[session.keys])
    else:
        checks["input"] = {"recording": rel(pl.recording), "sha256": sha256(pl.recording),
                           "ok": sha256(pl.recording) == PR.registered(X.STAGE)["entry"]["input"]["sha256"]}
    checks["ticks"] = ticks_check(p["table"], p["stage_trace"], p["capture"])
    if session.recording:
        checks["registered"] = registered_check(p, pl.result)
    checks["stream"] = PR.check_stream({"counts": p["counts"], "imports": p["imports"]},
                                       p["stream"], run_rec, meta)
    counts = json.loads(p["counts"].read_text()) if p["counts"].is_file() else {}
    checks["audio_counts"] = PR.audio_counts(counts)
    checks["player"] = player_check(p["report"], player_rc, stream_sha)
    checks["wav"] = wav_check(p["wav"], p["sidecar"], stream_sha)
    checks["tree"] = tree_check(made)
    ok = verdict(checks) and stream_sha is not None
    stream_audio = checks["stream"].get("audio") or {}
    audio = bool(meta["audio"] and stream_audio.get("records") and not checks["stream"].get("audio_problems"))
    if ok:
        PR.write_ready(p["ready"], stream_sha, meta)
        ready: dict | str = {"path": rel(p["ready"]), "stream": rel(p["stream"]), "stream_sha256": stream_sha}
    else:
        p["ready"].unlink(missing_ok=True)
        ready = "not written: " + ", ".join(k for k, c in checks.items() if c.get("ok") is not True)
    files = {k: ref(p[k]) for k in ("wav", "sidecar", "report", "run", "matrix", "counts", "imports",
                                     "capture", "stage_trace", "goal_latch", "input_applied", "keys",
                                     "keys_log", "log", "player_log") if p[k].is_file()}
    files["observer"] = ref(observer_path)
    import present_live as PL
    record = {
        "schema": RUN_SCHEMA, "generated_by": TOOL, "session": session.name,
        "scenes": list(session.scenes), "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": ok, "audio": audio, "ready": ready,
        "ticks": ref(p["table"]) if p["table"].is_file() else None,
        "inputs": {"pipeline": made, "commit": meta["commit"], "runtime_sha256": RR.runtime_digest(),
                   "fresh_build": link, "player_built": player_built,
                   "binary_sha256": checks["build"].get("binary_sha256"),
                   "player": PL.player_inputs(bin_dir)["binary_sha256"],
                   "device_profile": pl.profile_env.get("SA2_DEVICE_PROFILE", DP.DEFAULT),
                   "check_s5_exit_sha256": sha256(Path(X.__file__))},
        "run": {"meta": meta, "build": pl.build, "polls": session.polls, "command": cmd,
                "rc": rc, "seconds": seconds, "saves": saves, "player_rc": player_rc,
                "player_seconds": player_s,
                "frames_presented": ((run_rec.get("d3d9") or {}).get("frames") or {}).get("present"),
                "stream_bytes": p["stream"].stat().st_size if p["stream"].is_file() else None},
        "files": files, "checks": checks}
    p["record"].write_text(json.dumps(record, indent=1, default=str) + "\n")
    return record


# ---------------------------------------------------------------- running again

def aside_of(d: Path) -> Path:
    """Where a session's old directory waits while the session runs again."""
    return d.with_name(f".{d.name}.previous")


def unkept_of(d: Path) -> Path:
    """Where a call's run goes when the old one is put back."""
    return d.with_name(f"{d.name}.unkept")


def remove(d: Path) -> None:
    """shutil.rmtree, only under REPLACEABLE."""
    if not d.resolve().is_relative_to(REPLACEABLE.resolve()):
        raise SystemExit(f"refusing to remove {d}: not under {REPLACEABLE}")
    shutil.rmtree(d)


def may_use(d: Path, replace: bool) -> None:
    """Refuse a session directory a call may not run into: one an interrupted --replace left
    aside, one that exists without --replace, or one --replace may not move."""
    if aside_of(d).exists():
        raise SystemExit(f"{rel(aside_of(d))} holds the run an interrupted --replace set aside: "
                         f"move it back to {rel(d)}, or remove it, then run again")
    if not d.exists():
        return
    if not replace:
        raise SystemExit(f"{rel(d)} exists: a session directory is used once "
                         "(--replace runs it again)")
    if not d.resolve().is_relative_to(REPLACEABLE.resolve()):
        raise SystemExit(f"--replace moves only a directory under {rel(REPLACEABLE)}: {d}")


def put_back(held: list[Path]) -> None:
    """Each old run back at its own path, and the call's run beside it at <name>.unkept."""
    for d in held:
        unkept = unkept_of(d)
        if unkept.exists():
            remove(unkept)
        if d.exists():
            d.rename(unkept)
        aside_of(d).rename(d)
        print(f"{rel(d)}: the run before this call is back; this call's run is at {rel(unkept)}"
              if unkept.exists() else f"{rel(d)}: the run before this call is back")


def swapped_in(dirs: list[Path], replace: bool, refuse: Callable[[], None],
               run: Callable[[], dict[str, dict]]) -> dict[str, dict]:
    """`run` into `dirs` ("Running again"): every refusal (may_use, then `refuse`) before
    anything moves; the old directories set aside; kept only if every record `run` returns is
    ok, and put back if not, or if `run` raises."""
    for d in dirs:
        may_use(d, replace)
    refuse()
    held = [d for d in dirs if d.exists()]
    for d in held:
        d.rename(aside_of(d))
    try:
        records = run()
    except BaseException:
        put_back(held)
        raise
    if records and all(r.get("ok") is True for r in records.values()):
        for d in held:
            remove(aside_of(d))
    else:
        put_back(held)
    return records


def report(name: str, rec: dict, out: Path) -> None:
    bad = {k: c for k, c in rec["checks"].items() if c.get("ok") is not True}
    print(f"{name}: {'OK' if rec['ok'] else 'NOT OK'}; ticks {rec['checks']['ticks'].get('rows')}, "
          f"stream {rec['run']['stream_bytes']} bytes, WAV {rec['checks']['wav'].get('seconds')} s; "
          f"record {rel(paths(out, name)['record'])}")
    for k, c in bad.items():
        print(f"  FAIL {k}: " + json.dumps({x: y for x, y in c.items()
                                            if x in ("why", "problems", "wrong", "tied", "moved",
                                                     "audio_problems", "meta_problems",
                                                     "held_to_run", "counts_mismatch",
                                                     "first_differing_line")},
                                           default=str)[:900])


def run_sessions(names: list[str], out: Path, bin_dir: Path, *, link: bool, replace: bool,
                 build_player: bool) -> dict[str, dict]:
    """The sessions `names`, each into its own directory under `out`: the refusals of every
    one of them, the tree's pins and the player's build, before any old run is set aside
    (swapped_in)."""
    def refuse() -> None:
        refuse_busy()
        made.update(pins())
        if build_player:
            import present_live as PL
            out.mkdir(parents=True, exist_ok=True)
            built = PL.build_player(bin_dir, out / "player-build.log")
            print(f"player: {built['command']} rc {built['rc']} ({built['seconds']} s)")
            if not built["ok"]:
                raise SystemExit(f"the player did not build: {built['log']}")
        plans.extend(plan(SESSIONS[n], out, bin_dir, link) for n in names)

    def run() -> dict[str, dict]:
        records = {}
        for pl in plans:
            records[pl.session.name] = run_session(pl, out, bin_dir, made, link, build_player)
            report(pl.session.name, records[pl.session.name], out)
        return records

    plans: list[Plan] = []
    made: dict[str, str] = {}
    return swapped_in([paths(out, n)["dir"] for n in names], replace, refuse, run)


# ---------------------------------------------------------------- the receipt

class ReceiptRefused(SystemExit):
    """A session record the receipt cannot stand on; the message says which and why."""


def load_record(path: Path, root: Path, now: dict) -> dict:
    """A session's record, once it is ok, recorded sound, was made on a fresh build with a
    player its call built, by `now`'s pins, runtime and observer (tree_now), and names files
    that still hash to what it recorded."""
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ReceiptRefused(f"{rel(path, root)}: {e}") from None
    where = rel(path, root)
    if rec.get("schema") != RUN_SCHEMA or rec.get("generated_by") != TOOL:
        raise ReceiptRefused(f"{where} is not a record of {TOOL} ({RUN_SCHEMA})")
    if rec.get("ok") is not True or rec.get("audio") is not True:
        raise ReceiptRefused(f"{where}: ok {rec.get('ok')!r}, audio {rec.get('audio')!r}")
    inputs = rec.get("inputs") if isinstance(rec.get("inputs"), dict) else {}
    if inputs.get("fresh_build") is not True or inputs.get("player_built") is not True:
        raise ReceiptRefused(f"{where} was not made on a fresh build with a player its call built "
                             f"(fresh_build {inputs.get('fresh_build')!r}, player_built "
                             f"{inputs.get('player_built')!r}: --no-build or --no-player-build)")
    differ = moved(inputs.get("pipeline") or {}, now["pipeline"])
    if differ:
        raise ReceiptRefused(f"{where} was made by other files than the tree's: {', '.join(differ)}")
    if inputs.get("runtime_sha256") != now["runtime_sha256"]:
        raise ReceiptRefused(f"{where} was made by the runtime {inputs.get('runtime_sha256')!r}, "
                             f"not the tree's {now['runtime_sha256']}")
    files = rec.get("files") if isinstance(rec.get("files"), dict) else {}
    observer = (files.get("observer") or {}).get("sha256") if isinstance(files.get("observer"), dict) else None
    if observer != now["observer_sha256"]:
        raise ReceiptRefused(f"{where} linked the observer {observer!r}, not the one this tree "
                             f"generates ({now['observer_sha256']})")
    ready = rec.get("ready")
    if not isinstance(ready, dict):
        raise ReceiptRefused(f"{where}: no READY")
    for r in [rec.get("ticks"), files.get("wav"), files.get("sidecar")]:
        if not (isinstance(r, dict) and (root / r["path"]).is_file()
                and sha256(root / r["path"]) == r["sha256"]):
            raise ReceiptRefused(f"{where}: {r} is not the file it recorded")
    return rec


def write_receipt(out: Path, root: Path = ROOT, pipeline: tuple[str, ...] = PIPELINE,
                  receipt: str = X.AUDIO_RECEIPT) -> dict:
    """The receipt from the sessions' records (the module docstring, "The receipt")."""
    now = tree_now(root, pipeline)
    records, record_paths = {}, {}
    for name in sorted(set(SCENE_SESSION.values())):
        path = out / name / RECORD
        records[name] = load_record(path, root, now)
        record_paths[name] = path
    scenes = {}
    for scene in X.SCENE_ORDER:
        rec = records[SCENE_SESSION[scene]]
        scenes[scene] = {"wav": rec["files"]["wav"], "sidecar": rec["files"]["sidecar"],
                         "ticks": rec["ticks"], "recording": ref(record_paths[SCENE_SESSION[scene]], root)}
    doc = {"schema": X.AUDIO_SCHEMA, "generated_by": TOOL,
           "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "inputs": {"pipeline": now["pipeline"], "runtime_sha256": now["runtime_sha256"],
                      "observer_sha256": now["observer_sha256"],
                      "commits": sorted({r["inputs"]["commit"] for r in records.values()})},
           "scenes": scenes,
           # A list, so each scene's name is a key once in the file (scenes) and a falsifier
           # can anchor on it.
           "runs": [{"session": name, "record": ref(record_paths[name], root),
                     "scenes": rec["scenes"], "build": rec["run"]["build"],
                     "polls": rec["run"]["polls"], "binary_sha256": rec["inputs"]["binary_sha256"],
                     "device_profile": rec["inputs"]["device_profile"]}
                    for name, rec in sorted(records.items())],
           "note": NOTE}
    path = root / receipt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def scene_levels(root: Path = ROOT, receipt: str = X.AUDIO_RECEIPT) -> dict[str, dict]:
    """Per scene, the held stretches' whole seconds as audible() cuts them: how many, the
    loudest and the quietest, in dBFS. What the writer prints and writes beside the runs
    (write_levels); the receipt keeps none."""
    doc = json.loads((root / receipt).read_text())
    level = X.stage_level(root)
    out = {}
    for scene, refs in doc["scenes"].items():
        wav = root / refs["wav"]["path"]
        side = json.loads((root / refs["sidecar"]["path"]).read_text())
        rows = X.integers(X.table(root / refs["ticks"]["path"], X.AUDIO_TICKS, scene),
                          X.AUDIO_TICKS, scene)
        offset, frames = X.wav_layout(wav, scene)
        origin = side["origin_us"]
        runs, current = [], []
        for row in rows:
            if X.scene_holds(scene, row, level):
                current.append(row)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
        held = [r for r in runs if len(r) >= X.SCENE_MIN_TICKS]
        levels = []
        with wav.open("rb") as handle:
            for run in held:
                first = max(0, -((origin - run[0]["virtual_us"]) * X.SAMPLE_RATE // 1_000_000))
                end = min(frames, (run[-1]["virtual_us"] - origin) * X.SAMPLE_RATE // 1_000_000)
                while first + X.SAMPLE_RATE <= end:
                    levels.append(X.second_dbfs(handle, offset, first))
                    first += X.SAMPLE_RATE
        finite = [x for x in levels if math.isfinite(x)]
        out[scene] = {"held_ticks": [[r[0]["tick"], r[-1]["tick"]] for r in held],
                      "seconds": len(levels),
                      "loudest_dbfs": round(max(finite), 2) if finite else None,
                      "quietest_dbfs": round(min(finite), 2) if finite else None,
                      "seconds_at_or_over": sum(1 for x in finite if x >= X.AUDIBLE_DBFS)}
    return out


# ---------------------------------------------------------------- main

def write_levels(out: Path, root: Path = ROOT, receipt: str = X.AUDIO_RECEIPT) -> dict:
    """scene_levels, written to <out>/scene-levels.json beside the runs and tied to the
    receipt it read by its sha256: the table a journal cites instead of typing it."""
    doc = {"generated_by": TOOL, "receipt": {"path": receipt, "sha256": sha256(root / receipt)},
           "audible_dbfs": X.AUDIBLE_DBFS, "scenes": scene_levels(root, receipt)}
    (out / "scene-levels.json").write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["run", "receipt", "all"])
    ap.add_argument("session", nargs="?", choices=sorted(SESSIONS))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"default {DEFAULT_OUT}")
    ap.add_argument("--player-bin", type=Path, default=None, help="default <out>/bin")
    ap.add_argument("--no-player-build", action="store_true",
                    help="use the player already in --player-bin (no receipt stands on the runs)")
    ap.add_argument("--no-build", action="store_true",
                    help="run on the session's last build (no receipt stands on the runs)")
    ap.add_argument("--replace", action="store_true",
                    help="run sessions again into their directories (\"Running again\")")
    args = ap.parse_args(argv)
    out = (ROOT / args.out).resolve()
    if not out.is_relative_to((ROOT / "private").resolve()):
        print(f"--out must lie under private/ (AGENTS.md rule 9): {out}", file=sys.stderr)
        return 2
    bin_dir = (ROOT / args.player_bin).resolve() if args.player_bin else out / "bin"
    if args.what == "run" and not args.session:
        print("run: name a session", file=sys.stderr)
        return 2
    if args.what in ("run", "all"):
        names = [args.session] if args.what == "run" else list(SESSIONS)
        records = run_sessions(names, out, bin_dir, link=not args.no_build, replace=args.replace,
                               build_player=not args.no_player_build)
        if not all(rec["ok"] for rec in records.values()):
            return 1
        if args.what == "run":
            return 0
    try:
        write_receipt(out)
    except ReceiptRefused as e:
        print(f"no receipt: {e}", file=sys.stderr)
        return 1
    print(f"receipt {X.AUDIO_RECEIPT}")
    for scene, lv in write_levels(out)["scenes"].items():
        print(f"  {scene}: held {lv['held_ticks']}, {lv['seconds']} whole seconds, loudest "
              f"{lv['loudest_dbfs']} dBFS, quietest {lv['quietest_dbfs']} dBFS, "
              f"{lv['seconds_at_or_over']} at or over {X.AUDIBLE_DBFS:.0f}")
    print(f"scene levels {rel(out / 'scene-levels.json')}")
    result = X.audible(ROOT)
    print(f"audible: {'met' if result.met else 'not met'}: {result.detail}")
    return 0 if result.met else 1


if __name__ == "__main__":
    sys.exit(main())
