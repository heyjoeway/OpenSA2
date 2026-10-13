#!/usr/bin/env python3
"""Play the lifted game live, with the player window's keyboard and the game's sound.

Subtitle: tools/s5/present_live.py's live transport (the recorder writing into a named
pipe, the relay, a host's player in an SDL3 window), with no bound on the run and with the
game's sound in the stream for the player to play, and the other way round too: the
player sends its window's keys through a second FIFO to the runtime's DirectInput keyboard
(SA2_INPUT_DI_SOURCE), which logs what it applied so the session can be replayed
headlessly (SA2_INPUT_DI_LOG, in SA2_INPUT_DI_SCRIPT's format).

Usage:
  .venv/bin/python tools/s5/play_live.py                          # the boot, to the title and on
  .venv/bin/python tools/s5/play_live.py --scene metalharbor      # Metal Harbor from its test spawn
  .venv/bin/python tools/s5/play_live.py --scene metalharbor --receipt   # and G5's play receipt
  .venv/bin/python tools/s5/play_live.py --host sdl3-gpu --scene metalharbor --receipt
                                                                  # the same in the second host
  .venv/bin/python tools/s5/play_live.py --dry-run [--scene S]    # print the commands, run nothing
  (--host dxvk-native|sdl3-gpu, --out DIR, --build NAME, --player-bin DIR, --no-player-build,
   --renderer-store DIR, --profile default|registered, --keep-stream, --stream-cap-gb N,
   --disk-floor-gb N, --no-sound, --sound-driver alsa|dummy, --audio-wav and --no-pace move
   or add to the defaults below; --synthetic-keys N --max-frames M [--synthetic-seed S]
   verify a --receipt session with no person, below)

The keys are the game's own bindings: with private/install's Config/Keyboard.cfg, player 1
is W, S, A and D for up, down, left and right, I, J, U, K, Q and E, and Enter; the arrows
are player 2's (runtime/sa2_dinput.h, "The host keyboard").

It hands a person a playable window, so under AGENTS.md rule 11 it runs when the user
asks, never in a gate pass or beside another presentation run (it refuses both).

Why a tool
----------
A session is four parties that start in order and must end together whatever one of them
does: the game (tools/s4/loop_run.py), the relay that hands its stream on, the player, and
this process holding the keyboard's FIFO. Design 3.5 feeds input "from the host", and
runtime/sa2_dinput.h ("The host keyboard") is the game's half; this is the rest, run the
same way every time.

What it runs
------------
  * the game: loop_run on a build of its own name (present-play-boot.<tag> or
    present-play-mh.<tag>, never a gate's or a registered replay's), the presentation
    build of the scene with the recorder (host/sa2_present_rec.c) and its wraps:
      boot          the boot's hooks, tools/s4/tick_replay.py's tick hook with no
                    observer output set (it passes through), so the game plays the
                    intro and goes on to the title and whatever the keys choose;
      metalharbor   tools/s4/stage_run.py's test-spawn hooks and wraps for the stage
                    but harness/replay/wrap_input_replay.c and its wrap of
                    UpdateControllers (sub_77e780): the keyboard, not a recording,
                    drives the controller, with stage_run's level, character and
                    command line; with --receipt, the tick probe inside the per-tick
                    table's observer (below, "The play receipt");
    on the reach configuration (tools/s4/reach_receipt.py ENV) with no bound on the run:
    SA2_IMPORT_BUDGET=0 and SA2_PUMP_POLLS=0 (no host WM_QUIT), SA2_VIRTUAL_BUDGET_MS=0,
    the probe's wall-clock alarm off (--timeout 0, runtime/sa2_call.c) and loop_run's
    process bound SESSION_TIMEOUT. The device profile is the default unless --profile
    registered asks for the scene's registered capture's: a session stands for no
    capture, and its log replays on the profile it was made on.
    SA2_INPUT_DI_SOURCE names the keyboard FIFO, SA2_INPUT_DI_LOG the session's log;
    SA2_VFS_WRITE_ROOT an overlay of its own, emptied and given the pinned saves first
    (tools/oracle/save_state.py), so a session and its replay start alike; and, unless
    --no-sound, SA2_REC_AUDIO=1 (below, "Sound").
  * the player, --host's (below, "The hosts"), --interactive on three inherited
    descriptors (--video-fd, the relay's pipe; --cancel-fd, this process's way to stop
    it; --end-fd, the player's way to ask this process to end the session) with
    --input-path the keyboard FIFO; unless --no-pace, --pace and --pace-log (below,
    "Pace"); unless --no-sound, --audio (and --audio-wav with --audio-wav); and with
    --receipt the latency logs --key-log and --present-log (below, "The play receipt").
    It starts when the game's binary does, not during the link, in a process group of
    its own, so a Ctrl-C at the terminal reaches this process alone.
  * the recorder's counts: SA2_REC_COUNTS, the session's counts.json, so a session joins
    M7's recorded runs (tools/s4/m7_inputs.py registers a directory holding a recorder
    counts.json beside its run.json). A --synthetic-keys session writes them to
    counts.synthetic.json instead, which no census reads: a verification run never
    enters M7's registry, which only grows.

The hosts
---------
--host names the player, as G5's play clause names the two hosts
(Documentation/scripts/check_s5_exit.py, "The hosts"; SA2_REC_META carries it):

  dxvk-native  host/player/build.sh's sa2_player in --player-bin, run through
               host/player/run_player.sh: DXVK Native behind the recorded calls, its
               renderer DXVK's library (tools/pins.json)
  sdl3-gpu     host/d3d9gpu/build.sh's sa2_player_gpu (host/player/player.cpp with
               SA2_PLAYER_GPU=1), run as tools/s5/compare_hosts.py runs it, through
               host/player/player_env.sh, with every inherited SA2_D3D9GPU_ variable
               taken out and SA2_D3D9GPU_REPORT the session's player/d3d9gpu.json. The
               player is its own renderer, so the session runs a copy of the build kept
               by content, --renderer-store's <sha256>/sa2_player_gpu (read-only, never
               written over; its build record beside it): a later build cannot change the
               renderer a receipt names.

The window's title names the host.

The keyboard FIFO is made here and held open O_RDWR by this process until the session
ends: the player's O_WRONLY open then never answers ENXIO, the game's O_RDONLY open never
waits, and the game never reads end of file (which lets every key up) because the player
is slow to start. This process never reads it.

Sound
-----
Design 3.5: "dsound and the CRI ADX path recorded as decoded PCM streams and cue events;
the host plays them". Unless --no-sound, the recorder copies the game's DirectSound and
XAudio2 sound into the stream (SA2_REC_AUDIO=1: a version 2 stream, and "audio": true in
its meta, as tools/s5/present_run.py --audio records it; the guest sees no difference,
agents-journals/s5/2026-10-02-input-and-sound.md), and the player mixes it by the
runtime's clock (host/player/mixer.h) and plays it on SDL's default playback device
(--audio). The driver is --sound-driver's, else the one this process inherits, read as
SDL reads it: SDL_AUDIO_DRIVER, or where that is unset SDL2's SDL_AUDIODRIVER, which SDL3
still takes. It is kept, passed on as SDL_AUDIO_DRIVER (neither name is passed on
otherwise) and shown, so SDL_AUDIO_DRIVER=dummy silences a session. Else it is SDL's own
choice: on this desktop ALSA, routed to PipeWire and the speakers. Only SOUND_DRIVERS are
taken, from any of these; an inherited "disk" or any other value is refused under its
variable's name, since "disk" writes the sound into the working directory, the checkout.
"dummy" plays nothing, for a test. It takes 1024 frames (21.33 ms of sound at 48 kHz)
every 21 ms, about 1.6 % faster than a real device, so its queue runs short where a real
device's would not; SDL_AUDIO_DEVICE_SAMPLE_FRAMES=960 (20 ms every 20 ms) brings it near
real time. With --audio-wav the player also writes the sound to player/sound.wav and its
.json, about 1.4 GB an hour (48 kHz stereo float).

The player's live reader takes a version 2 stream's records as it takes version 1's
(host/player/stream.cpp, open_live), and each Present queues the sound up to its frame's
virtual time as that frame is presented (player.cpp, do_present and audio_block), behind
a lead of 60 ms of silence given at the first. Paced (below), the device's queue holds
that lead. Unpaced (--no-pace), frames are shown as they arrive, so the sound keeps the
game's pace: a game ahead of real time fills the queue to its bound (half a second) and
the player drops blocks. A game behind real time lets the device run short, paced or not,
a gap the listener hears, and so does a long wait between two frames (a loading pause),
since the sound between them arrives with the later one. The receipt's player.audio
counts both (device.blocks_dropped, device.shortfalls).

--no-sound is the session as it was before sound: a version 1 stream and a player given
neither --audio nor --audio-wav (nor either audio driver variable, which it would not
use).

Pace
----
Design 3.5's time is "under host control", and here the host is the player. Unless
--no-pace, the player presents each frame at its virtual time on the wall clock (--pace,
host/player/pace.h), and the game is held to that through the stream: while the player
waits it reads nothing, the relay holds what it has (present_live.relay with lead_frames
LEAD_FRAMES: it takes nothing more from the game's pipe while that many whole frames wait
to be handed on, one more once the game has not been found waiting on it for
present_live.QUIET_FRAMES whole frames running), the game's pipe fills, and the game
waits in the recorder's write at its next Present. No runtime change, and the guest sees
nothing new: it reads only the virtual clock, and its keyboard comes when it polls, as
before. A game slower than real time is never held, and the player shows each frame when
it is ready; after a frame more than the sound's lead late the schedule moves (pace.h), so
the game is never shown fast to make up a stall, and cannot bank time either: a stretch
the game runs faster than real time is shown at real time.

Being held costs the frames the game makes ahead of the picture: its keys act on a frame
shown that much later. The receipt's pace.ahead counts, at each Present, the frames the
relay had whole beyond it. Beyond those, while the game waits, its pipe holds less than
one more once the relay has sized it to the frames (present_live.fit_pipe), and the game
is in its write of the next. The size can lag: the kernel refuses to shrink a pipe below
what it holds (EBUSY), and the waiting game refills it as soon as the relay reads, so a
pipe grown for a large frame can hold many small ones until a shrink after a read takes
(the receipt's pace.relay_held counts the refusals, resize_refused). The player's report's
"pace" counts the waits, the lateness and the moves, and the virtual time over the wall
time; player/pace.tsv has them per frame.

--no-pace is the session as it was before pacing: the game as fast as it runs, the frames
as they come, and the relay never holding the game.

How a session ends
------------------
Every way but the game's own end asks the game to stop: SIGTERM to the game's binary
(never to loop_run, which keeps the run's record), which the runtime turns into WM_QUIT at
its next keyboard poll, posted as the host's, as the pump's poll budget posts it in a
registered run; the guest leaves its loop through its own code, the probe writes its run
record, and exit() lets the runtime write the log's last line. The recorder ends the
stream with its END, and the player reads it to there, so its report is final
(`complete`), which G5's play clause needs. If the game has not ended STOP_GRACE seconds
later, a second SIGTERM ends it (the runtime's handler resets itself), and KILL_GRACE
seconds after that SIGKILL ends loop_run's group. The game's process is looked up again
before each SIGTERM, so a signal never goes to a pid the game has given up. What asks:

  * the goal (--receipt): once the per-tick table shows the goal latch risen after the
    stage was entered, and the game has made check_s5_exit.AFTER_GOAL_TICKS and
    END_MARGIN_TICKS more ticks, the session ends by itself. A person plays to the goal
    ring and lets go; nothing needs their hands after it.
  * the window's close: the player lets every key up, sends no more, tells this process
    on --end-fd, and reads on to END (host/player/player.cpp, "Input"). A close
    request kForceCloseAfterS or more after that stops the player as before, and its
    report is then not final.
  * Ctrl-C at the terminal, the first: as the window's close. A second Ctrl-C stops the
    session at once, cancelling the player.
  * --max-frames, with --synthetic-keys: once the table shows that many Presents.
  * the disk (below).

Each of these but the window's close asks the player first (SIGUSR1): it lets every key up
and closes its key sender, as a close does, and answers on --end-fd, and its answer stops
the game. So no key record is written after the game's last keyboard poll, which the play
clause would refuse (every record the player wrote is read). A player that cannot be asked,
or has not answered END_ASK_GRACE seconds later, and the game is stopped anyway. With
--receipt, an end that comes once the table shows the goal but fewer than the clause's
AFTER_GOAL_TICKS past it (a person closing the window as the goal ring is touched) waits
for the game, its keys already up, to make them, at most GOAL_HOLD_S, before it asks the
game to stop: the clause refuses a session that ends sooner after the goal, and
play_receipt then does not track it. A second Ctrl-C does not wait.

If the player ends first, the game is asked to stop. If the game ends first, the player
is given the rest of the stream (present_live's FEED_GRACE), then cancelled through
--cancel-fd, then killed. The session's receipt's session.end says what ended it.

The disk ends a session too. The relay keeps the whole stream on disk while it plays (the
live boot wrote 965 MB in 85 s, about 40 GB an hour; Metal Harbor runs to about 4 MB a
frame), so a watchdog asks the game to stop, as closing the window does, when that copy
passes --stream-cap-gb (default STREAM_CAP_GB) or the free space under the session's
directory falls below --disk-floor-gb (default DISK_FLOOR_GB); the receipt's
session.limit says which. A session refuses to start below the floor.

The play receipt
----------------
G5's play clause (Documentation/scripts/check_s5_exit.py) reads one receipt per host,
Documentation/receipts/s5-play-<host>-v1.json, --host's. With --receipt (Metal Harbor
only) the game also writes its per-tick table to the session's play-ticks.tsv
(play_receipt.TICKS_ENV in the run's settings): one row per GameLoop return with the
stage's state words, the goal latch as the tick left it, the Presents made so far and the
host keys applied over the tick's keyboard polls; and its keyboard polls that read FIFO
records to play-polls.tsv, with their report (play_receipt.POLLS_ENV, POLLS_REPORT_ENV).
The writer is tools/s5/play_receipt.py's observer, generated into the session's directory
as sa2_play_ticks.c and linked in the tick probe's place: it is the tick probe, unchanged,
inside a wrap that writes the row (play_receipt's docstring, "The table's observer"). It
reads and writes nothing the guest can see, and the session links no other hook or wrap
than without --receipt. Metal Harbor's level is the one the clause reads (the replay
registry's); a stage_run spawning another is refused before the session starts.

Input latency, the user's ruling of 2026-10-04 ("Its live-play receipt reports input
latency, button press to presented frame"): with --receipt the player also writes its key
sender's log (player-keys.tsv: each key change, its SDL event age, when it was dequeued
and sent) and its Present log (player-presents.tsv: each host Present's call and return),
and the relay when each frame came whole (relay-frames.tsv, and its report), all on
CLOCK_MONOTONIC. The clause joins the four logs (check_s5_exit.latency_evidence).

When the session ends, with its own receipt (play-live.json) already written and not
written again, play_receipt writes the session's document to its play-record.json,
untracked: the table, the player's report, the run record, the latency logs and the
session's receipt by path and sha256, the observer that wrote the table, the host's
renderer and the latency's summary. If the evidence is fit for it (play_receipt's
docstring, "When the receipt is tracked": the goal ring reached after the stage was
entered, the player's report final, no synthetic key, the clause's latency join taken),
the same bytes become the receipt, once a receipt in place is copied to the session's
play-receipt-replaced.json; otherwise the record lists the reasons. The receipt carries no
verdict: the closing line gives the clause's reading of this host's session, and the
latency. What the receipt step did, or the error it met, is kept in the session's
play-receipt-step.json and said in the closing line, not raised. A session without
--receipt writes none of these and runs exactly as before.

Verification with no person: --synthetic-keys N --max-frames M (each refused without the
other, and both need --receipt) writes N presses of the jump key, at irregular times from
--synthetic-seed (recorded), into the session's synthetic-keys.tsv; the player queues them
in its own SDL queue from its first Present on (host/player/player.cpp, --synthetic-keys),
so they take the path a real key takes from SDL's queue on: the sender, the FIFO, the
runtime's poll, the stream, the relay and the Present. The window's own keys are not sent
in that mode. The script ends with the window's close request (SYNTHETIC_CLOSE of the
budget), so the session ends the way a person closing the window ends one (above, "How a
session ends"); if it has not ended by then, it ends once the game has made M Presents. The jump key is J
(JUMP_SCANCODE): private/install's Config/Keyboard.cfg binds it to player 1's sixth slot,
which the reader at 0x004055D0 hands button 5, the button the image's own defaults give X,
the game's jump. play_receipt never tracks such a session (its keys are synthetic): its
record holds the latency as a diagnostic only.

Outputs, in a directory of the session's own, <out>/<run id> (out: --out, default
private/generated/s5/input/<scene>; the run id is the start time, this process's pid and
eight random hex digits, and "dry-run" in a dry run), so no session touches another's:
input.log (the session's keys, in SA2_INPUT_DI_SCRIPT's format), run.json and matrix.json
(loop_run's, moved here), counts.json (the recorder's; counts.synthetic.json with
--synthetic-keys), game.log, game.live.sa2s (the relay's copy of the stream; removed at
the end unless --keep-stream), relay-frames.tsv (when each frame came whole at the relay,
on CLOCK_MONOTONIC, paced or not; pace.ahead reads it), player/ (report.json, player.log,
pace.tsv unless --no-pace, with --audio-wav sound.wav and sound.wav.json, and for
sdl3-gpu d3d9gpu.json), dxvk-log/, vfsroot/, play-live.json, the session's receipt, and
with --receipt sa2_play_ticks.c, play-ticks.tsv, play-polls.tsv and its report,
relay-frames-report.json, player-keys.tsv, player-presents.tsv, play-record.json,
play-receipt-step.json and, when the session replaced a play receipt,
play-receipt-replaced.json; with --synthetic-keys, synthetic-keys.tsv.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import json
import os
import random
import re
import secrets
import select
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import present_live as PL  # noqa: E402  the live transport and host checks this reuses
import play_receipt as PRC  # noqa: E402  G5's play receipt, written at a session's end

PR, TR, RR, SS = PL.PR, PL.TR, PL.RR, PL.SS
DP = TR.DP

GEN_INPUT = ROOT / "private" / "generated" / "s5" / "input"
# Each scene's build name before the tree's tag, and the registered replay it stands
# beside (present_run.build_name_problem keeps a scene's builds off the other's names).
SCENES = {"boot": ("present-play-boot", "boot"), "metalharbor": ("present-play-mh", "metalharbor")}
# What a Metal Harbor session leaves out of the stage run: the recording's controller.
INPUT_REPLAY_HOOK = "harness/replay/wrap_input_replay.c"
INPUT_REPLAY_WRAP = "sub_77e780"          # UpdateControllers (0x0077E780), the hook's wrap
UNBOUNDED = {"SA2_IMPORT_BUDGET": "0", "SA2_PUMP_POLLS": "0", "SA2_VIRTUAL_BUDGET_MS": "0"}
LABEL = "play"
SESSION_TIMEOUT = 24 * 3600   # loop_run's bound on the game's process, in seconds
STOP_GRACE = 120              # after SIGTERM, how long the game has to leave its loop
KILL_GRACE = 30               # after the second SIGTERM, before SIGKILL
STREAM_CAP_GB = 200.0         # the relay's copy past this many GB (10^9 bytes) ends a session
DISK_FLOOR_GB = 50.0          # less free space than this under the session's directory, too
WATCH_EVERY = 1.0             # seconds between the disk watchdog's looks
VIDEO_FD, CANCEL_FD = 40, 41  # the player's inherited descriptors, fixed so a command is exact
END_FD = 42                   # the player's end request (--end-fd), read by this process
# The hosts (the module docstring, "The hosts"): G5's play clause's ids, each player's
# binary in --player-bin, and the script that builds it.
HOSTS = {"dxvk-native": ("sa2_player", "host/player/build.sh", "BUILD_OK"),
         "sdl3-gpu": ("sa2_player_gpu", "host/d3d9gpu/build.sh", "D3D9GPU_BUILD_OK")}
RENDERER_STORE = GEN_INPUT / "renderers"   # sdl3-gpu: the players sessions ran, by sha256
GPU_VARS = "SA2_D3D9GPU_"                  # the GPU device's settings, none inherited
# --receipt: the goal ends a session once the game has made this many ticks beyond the
# clause's AFTER_GOAL_TICKS past it (the module docstring, "How a session ends").
END_MARGIN_TICKS = 240
TICK_WATCH_EVERY = 0.25       # seconds between the per-tick table's looks
# Every end this process asks for goes through the player first (SIGUSR1: it lets every key
# up, closes its key sender and answers on --end-fd); one that has not answered in this
# many seconds, or cannot be asked, and the game is stopped anyway.
END_ASK_GRACE = 5.0
# --receipt: an end asked (a close, a Ctrl-C, a limit) once the table shows the goal but fewer
# than the clause's AFTER_GOAL_TICKS past it waits, the keys already up, for the game to make
# them, at most this many seconds, before the game is asked to stop.
GOAL_HOLD_S = 10.0
SHELLS = ("bash", "sh", "dash")   # a player not yet exec'd by its launcher is not asked
# --synthetic-keys: the jump key (the module docstring, "The play receipt"), SDL's
# scancode for J; the presses start at SYNTHETIC_START of the frame budget at 60 frames a
# second and spread over SYNTHETIC_SPAN of it; the player stops itself
# MAX_FRAMES_SLACK Presents past the budget if the game has not ended by then.
JUMP_SCANCODE = 13
SYNTHETIC_SEED = 20261008
SYNTHETIC_START, SYNTHETIC_SPAN = 0.2, 0.6
SYNTHETIC_CLOSE = 0.9         # and the window's close request, at this much of the budget
SYNTHETIC_MIN_GAP_MS = 150
MAX_FRAMES_SLACK = 600
INPUT_VARS = ("SA2_INPUT_DI_SCRIPT", "SA2_INPUT_DI_SOURCE", "SA2_INPUT_DI_LOG",
              "SA2_INPUT_SCRIPT", "SA2_INPUT_APPLIED")
# The playback drivers a session takes, from --sound-driver or an inherited
# SDL_AUDIO_DRIVER: the pinned SDL3 build's one (tools/pins.json, "sdl3": dynamic ALSA;
# PulseAudio, PipeWire, JACK and sndio are off) and "dummy", which plays nothing. SDL's
# "disk" driver is left out: it writes the sound into the working directory, which is the
# checkout.
SOUND_DRIVERS = ("alsa", "dummy")
# The environment variables SDL takes its audio driver from, in its order: the first that
# is set, even empty, is the one (SDL_hints.c, GetHintEnvironmentVariable: SDL3's name,
# else SDL2's, which SDL3 still reads).
SDL_AUDIO_DRIVER_VARS = ("SDL_AUDIO_DRIVER", "SDL_AUDIODRIVER")
# A paced session's relay takes nothing more from the game while this many whole frames
# wait to be handed to the player, and the player's pipe holds this many bytes, so that
# little of the next frames waits in it either (the module docstring, "Pace").
LEAD_FRAMES = 1
PLAYER_PIPE_BYTES = 16 << 10


def rel(p) -> str:
    return PR.rel(Path(p))


def default_build(scene: str) -> str:
    return f"{SCENES[scene][0]}.{PR.tree_tag()}"


def paths(out: Path) -> dict:
    player = out / "player"
    return {"out": out, "input_fifo": out / "input.fifo", "input_log": out / "input.log",
            "game_fifo": out / "game.fifo", "copy": out / "game.live.sa2s",
            "overlay": out / "vfsroot", "run": out / "run.json", "matrix": out / "matrix.json",
            "game_log": out / "game.log", "receipt": out / "play-live.json",
            "relay_frames": out / "relay-frames.tsv", "ticks": out / "play-ticks.tsv",
            "relay_report": out / "relay-frames-report.json",
            "polls": out / "play-polls.tsv", "poll_report": out / "play-polls-report.json",
            "keys": out / "player-keys.tsv", "presents": out / "player-presents.tsv",
            "ticks_observer": out / PRC.OBSERVER, "play_record": out / "play-record.json",
            "replaced_receipt": out / "play-receipt-replaced.json",
            "receipt_step": out / "play-receipt-step.json",
            "counts": out / "counts.json", "synthetic_counts": out / "counts.synthetic.json",
            "synthetic_keys": out / "synthetic-keys.tsv",
            "build_log": out / "player-build.log", "dxvk_log": out / "dxvk-log",
            "player": {"dir": player, "report": player / "report.json",
                       "log": player / "player.log", "wav": player / "sound.wav",
                       "pace_log": player / "pace.tsv", "device": player / "d3d9gpu.json"}}


# ---------------------------------------------------------------- the commands

def scene_hooks(scene: str, observer: str | None = None) -> tuple[list[str], list[str]]:
    """The scene's hooks and wraps before the recorder's: the boot's tick hook, or
    stage_run's with the input replay's hook and wrap taken out, which must each be
    there to take out; with `observer`, that hook in the tick probe's place (the module
    docstring, "The play receipt")."""
    if scene == "boot":
        hooks, wraps = [TR.HOOK], [TR.WRAP]
    else:
        import stage_run as SR
        hooks = [h for h in SR.HOOKS if h != INPUT_REPLAY_HOOK]
        wraps = [w for w in SR.WRAPS if w != INPUT_REPLAY_WRAP]
        if len(hooks) != len(SR.HOOKS) - 1 or len(wraps) != len(SR.WRAPS) - 1:
            raise SystemExit(f"tools/s4/stage_run.py no longer links {INPUT_REPLAY_HOOK} with "
                             f"the wrap {INPUT_REPLAY_WRAP}; play_live cannot take them out")
    if observer:
        if hooks.count(TR.HOOK) != 1:
            raise SystemExit(f"the session links {TR.HOOK} {hooks.count(TR.HOOK)} times; the "
                             "per-tick table's observer takes its one place")
        hooks = [observer if h == TR.HOOK else h for h in hooks]
    return hooks, wraps


def scene_env(scene: str) -> dict[str, str]:
    """The reach configuration, the stage's test spawn for Metal Harbor, and no bound."""
    env = dict(RR.ENV)
    if scene == "metalharbor":
        import stage_run as SR
        level, character, cmdline = SR.STAGES["metalharbor"]
        env.update(SA2_TEST_SPAWN_LEVEL=str(level), SA2_TEST_SPAWN_CHARACTER=str(character),
                   SA2_GUEST_CMDLINE=cmdline)
    env.update(UNBOUNDED)
    return env


def game_command(scene: str, build: str, p: dict, profile_env: dict[str, str],
                 meta: dict, sound: bool, ticks: bool = False,
                 synthetic: bool = False) -> tuple[list[str], str]:
    """loop_run's command for the session, and its --run spec. With `sound` the recorder
    records the game's sound too (SA2_REC_AUDIO=1, a version 2 stream); with `ticks` the
    game writes its per-tick table and its keyboard polls for the play receipt, through the
    observer in the tick probe's place (the module docstring, "The play receipt"). The
    recorder's counts go to the session's counts.json, or with `synthetic` to a name no
    census of M7's recorded runs reads."""
    env = scene_env(scene)
    env.update(profile_env)
    env.update({"SA2_VFS_WRITE_ROOT": rel(p["overlay"]), "SA2_REC_TRACE": rel(p["game_fifo"]),
                "SA2_REC_COUNTS": rel(p["synthetic_counts" if synthetic else "counts"])})
    if sound:
        env["SA2_REC_AUDIO"] = "1"
    env.update({"SA2_REC_META": json.dumps(meta, separators=(",", ":"), sort_keys=True),
                "SA2_INPUT_DI_SOURCE": rel(p["input_fifo"]),
                "SA2_INPUT_DI_LOG": rel(p["input_log"])})
    if ticks:
        env[PRC.TICKS_ENV] = rel(p["ticks"])
        env[PRC.POLLS_ENV] = rel(p["polls"])
        env[PRC.POLLS_REPORT_ENV] = rel(p["poll_report"])
    items = [LABEL] + [f"{k}={v}" for k, v in env.items()]
    for it in items[1:]:
        if "|" in it:
            raise SystemExit(f"a run setting carries `|`, loop_run's separator: {it!r}")
    spec = "|".join(items)
    hooks, wraps = scene_hooks(scene, rel(p["ticks_observer"]) if ticks else None)
    cmd = [str(TR.PY), str(ROOT / "tools" / "s4" / "loop_run.py"), "--tag", RR.TAG,
           "--main", RR.MAIN, "--name", build, "--out-prefix", build]
    for h in hooks + [PR.RECORDER]:
        cmd += ["--hook", h]
    for w in wraps + list(PR.WRAPS):
        cmd += ["--wrap", w]
    cmd += ["--timeout", str(SESSION_TIMEOUT), "--run", spec]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", "--probe-arg=0"]
    return cmd, spec


def player_launcher(host: str, binary: Path | None) -> list[str]:
    """How the host's player is started, from ROOT: dxvk-native's sa2_player through
    run_player.sh (which execs $OUT/sa2_player); sdl3-gpu's `binary`, the renderer kept by
    content, under player_env.sh as tools/s5/compare_hosts.py runs it."""
    if host == "dxvk-native":
        return ["bash", rel(PL.PLAYER_DIR / "run_player.sh")]
    shown = rel(binary) if binary is not None else "<renderer-store>/<sha256>/sa2_player_gpu"
    return ["bash", "-c", 'source "$0" && exec "$@"', rel(PL.PLAYER_DIR / "player_env.sh"), shown]


def player_command(p: dict, sound: dict, paced: bool, latency: bool = False,
                   host: str = "dxvk-native", binary: Path | None = None,
                   synthetic: dict | None = None) -> list[str]:
    """The player, run from ROOT: live on the three inherited descriptors, sending its
    window's keys to the FIFO, presenting each frame at its time (`paced`) and logging
    when, with `latency` its key and Present logs, with `synthetic` its own queued keys
    and a frame bound past the session's, and playing the stream's sound (and writing it)
    as `sound` asks."""
    cmd = player_launcher(host, binary) + [
        "--interactive", "--video-fd", str(VIDEO_FD), "--cancel-fd", str(CANCEL_FD),
        "--end-fd", str(END_FD), "--input-path", rel(p["input_fifo"]), "--stage", "3",
        "--report", rel(p["player"]["report"])]
    if paced:
        cmd += ["--pace", "--pace-log", rel(p["player"]["pace_log"])]
    if latency:
        cmd += ["--key-log", rel(p["keys"]), "--present-log", rel(p["presents"])]
    if synthetic:
        cmd += ["--synthetic-keys", rel(p["synthetic_keys"]),
                "--max-frames", str(synthetic["max_frames"] + MAX_FRAMES_SLACK)]
    if sound["on"]:
        cmd.append("--audio")
    if sound["wav"]:
        cmd += ["--audio-wav", rel(sound["wav"])]
    return cmd


def inherited_driver(environ) -> tuple[str | None, str | None]:
    """The audio driver SDL would take from `environ`, and the variable it is in: the
    first of SDL_AUDIO_DRIVER_VARS that is set, even empty (an empty one is SDL's own
    choice, and hides the next); (None, None) when neither is."""
    for name in SDL_AUDIO_DRIVER_VARS:
        if name in environ:
            return environ[name], name
    return None, None


def sound_plan(args, p: dict, inherited: tuple[str | None, str | None]) -> dict:
    """What the session does with the game's sound (the module docstring, "Sound"):
    recorded and played on SDL's default device, by the driver --sound-driver names, else
    by `inherited` (inherited_driver's: the value and the variable SDL would take it
    from), else by SDL's choice, and with --audio-wav written to player/sound.wav; with
    --no-sound, nothing, as before sound existed. An inherited driver outside
    SOUND_DRIVERS is refused, under the variable's own name, unless --sound-driver
    replaces it or --no-sound leaves nothing for it to play."""
    if args.no_sound:
        if args.audio_wav or args.sound_driver:
            raise SystemExit("--no-sound records and plays no sound: --audio-wav and "
                             "--sound-driver cannot apply")
        return {"on": False, "driver": None, "driver_from": None, "wav": None}
    driver, source = args.sound_driver, "--sound-driver"
    value, name = inherited
    if not driver and value:
        if value not in SOUND_DRIVERS:
            raise SystemExit(
                f"{name}={value!r} is in the environment: a session takes only "
                f"{', '.join(SOUND_DRIVERS)} (SDL's 'disk' would write the sound into the "
                "checkout); unset it, or give --sound-driver")
        driver, source = value, name
    return {"on": True, "driver": driver, "driver_from": source if driver else None,
            "wav": p["player"]["wav"] if args.audio_wav else None}


def sound_env(penv: dict, sound: dict) -> dict:
    """The player's environment with SDL's audio driver as `sound` says, and no other:
    --sound-driver's or the inherited one sound_plan took, as SDL_AUDIO_DRIVER, or none,
    so SDL chooses. Neither of SDL_AUDIO_DRIVER_VARS is passed on as inherited: SDL would
    take SDL2's name where SDL3's is unset."""
    env = {k: v for k, v in penv.items() if k not in SDL_AUDIO_DRIVER_VARS}
    if sound["driver"]:
        env["SDL_AUDIO_DRIVER"] = sound["driver"]
    return env


def host_env(penv: dict, host: str, p: dict) -> dict:
    """The player's environment for `host`: for sdl3-gpu, no inherited SA2_D3D9GPU_
    variable (a fault, a capability off, a test run) and the device's report in the
    session's player directory, as tools/s5/compare_hosts.py runs that player."""
    if host != "sdl3-gpu":
        return penv
    env = {k: v for k, v in penv.items() if not k.startswith(GPU_VARS)}
    env["SA2_D3D9GPU_REPORT"] = rel(p["player"]["device"])
    return env


def player_env_shown(env: dict) -> str:
    keys = ("SA2_PLAY_OUT", "OUT", "SA2_DXVK_LOG_PATH", "DXVK_LOG_LEVEL", "SDL_AUDIO_DRIVER",
            "SA2_D3D9GPU_REPORT")
    return " ".join(f"{k}={shlex.quote(env[k])}" for k in keys if k in env)


def game_env() -> dict[str, str]:
    """present_run's clean environment, less any host keyboard or input replay setting a
    shell could leave: the spec is the only place those come from."""
    return {k: v for k, v in PR.clean_env().items() if k not in INPUT_VARS}


def synthetic_plan(args) -> dict | None:
    """--synthetic-keys N --max-frames M (the module docstring, "The play receipt"): each
    needs the other and --receipt, the presses must fit the frame budget, and the seed is
    the session's."""
    if (args.synthetic_keys is None) != (args.max_frames is None):
        raise SystemExit("--synthetic-keys and --max-frames go together: a session with no "
                         "person must end without one")
    if args.synthetic_keys is None:
        if args.synthetic_seed is not None:
            raise SystemExit("--synthetic-seed seeds --synthetic-keys")
        return None
    if not args.receipt:
        raise SystemExit("--synthetic-keys verifies the input latency, whose logs only a "
                         "--receipt session writes: add --receipt")
    presses, frames = args.synthetic_keys, args.max_frames
    if presses < 1 or frames < 1:
        raise SystemExit("--synthetic-keys and --max-frames must be above 0")
    seed = SYNTHETIC_SEED if args.synthetic_seed is None else args.synthetic_seed
    script = synthetic_script(presses, frames, seed)    # refuses presses the budget cannot fit
    return {"presses": presses, "max_frames": frames, "seed": seed, "key": "J",
            "scancode": JUMP_SCANCODE, "lines": script.count("\n"),
            "close_ms": int(script.splitlines()[-1].split()[0])}


def synthetic_script(presses: int, max_frames: int, seed: int) -> str:
    """`presses` presses and releases of the jump key, in the player's --synthetic-keys
    format (milliseconds after the first Present, the SDL scancode, down or up): each at
    an irregular gap from `seed`'s generator, so the presses fall at every phase of a
    frame, held 40 ms to a third of the mean gap, across SYNTHETIC_SPAN of the budget
    from SYNTHETIC_START of it, at 60 frames a second; then the window's close request
    ("0 close") at SYNTHETIC_CLOSE of the budget, after the last release."""
    budget_ms = max_frames * 1000 // 60
    start, span = int(budget_ms * SYNTHETIC_START), int(budget_ms * SYNTHETIC_SPAN)
    mean = span // presses
    if mean < SYNTHETIC_MIN_GAP_MS:
        raise SystemExit(f"--synthetic-keys {presses} in --max-frames {max_frames}: {mean} ms "
                         f"between presses, under {SYNTHETIC_MIN_GAP_MS}; give more frames")
    rng = random.Random(seed)
    at, lines = start, []
    for _ in range(presses):
        at += rng.randint(mean // 2, mean * 3 // 2)
        hold = rng.randint(40, max(40, mean // 3))
        lines += [f"{at} {JUMP_SCANCODE} down", f"{at + hold} {JUMP_SCANCODE} up"]
    close = max(int(budget_ms * SYNTHETIC_CLOSE), at + mean)
    return "\n".join(lines + [f"{close} 0 close"]) + "\n"


def receipt_plan(args, scene: str, host: str = "dxvk-native") -> dict:
    """What --receipt asks of the session (the module docstring, "The play receipt"): the
    host whose receipt it writes, the stage's level as the clause reads it, once the
    session spawns there, and the pipeline's digests as the session starts."""
    if not args.receipt:
        return {"on": False}
    if scene != "metalharbor":
        raise SystemExit("--receipt: G5's play receipt is Metal Harbor's; add --scene metalharbor")
    import stage_run as SR
    try:
        level = PRC.X.stage_level(ROOT)
    except PRC.X.Refused as why:
        raise SystemExit(f"--receipt: the play clause reads no level for Metal Harbor: "
                         f"{why}") from None
    spawn = SR.STAGES["metalharbor"][0]
    if spawn != level:
        raise SystemExit(f"--receipt: the session spawns level {spawn} (tools/s4/stage_run.py); "
                         f"the play clause reads Metal Harbor at level {level} "
                         f"({PRC.X.REGISTRY})")
    return {"on": True, "host": host, "receipt": PRC.receipt_path(host), "level": level,
            "pipeline": PRC.pipeline(),
            "transport_source": {"path": rel(Path(PL.__file__)),
                                 "sha256": PL.sha256(Path(PL.__file__))}}


def profile_of(choice: str, scene: str):
    """DP.DEFAULT, or the scene's registered capture's checked profile."""
    if choice == "default":
        return DP.DEFAULT
    return TR.native_device_profile(PR.registered(SCENES[scene][1])["entry"])


def under_private(path: Path, option: str, what: str) -> Path:
    if not path.resolve().is_relative_to((ROOT / "private").resolve()):
        raise SystemExit(f"{option} {path}: {what} stay under private/")
    return path


def build_player(host: str, bin_dir: Path, log: Path) -> dict:
    """The host's player built into bin_dir against this tree's host/sa2_stream.h:
    present_live's build of sa2_player, or host/d3d9gpu/build.sh's of sa2_player_gpu (and
    its tools), which ends by printing D3D9GPU_BUILD_OK."""
    if host == "dxvk-native":
        return PL.build_player(bin_dir, log)
    _, script, ok = HOSTS[host]
    bin_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OUT=str(bin_dir))
    env.pop("SA2_STREAM_H_DIR", None)
    t = time.time()
    r = subprocess.run(["bash", str(ROOT / script)], cwd=ROOT, env=env, capture_output=True,
                       text=True)
    log.write_text(f"$ OUT={rel(bin_dir)} bash {script}\n{r.stdout}\n{r.stderr}")
    return {"command": f"OUT={rel(bin_dir)} bash {script}", "rc": r.returncode,
            "seconds": round(time.time() - t, 2), "log": rel(log),
            "ok": r.returncode == 0 and ok in r.stdout}


def keep_renderer(bin_dir: Path, store: Path) -> Path:
    """sdl3-gpu's player kept by content (the module docstring, "The hosts"): bin_dir's
    sa2_player_gpu copied, with its build record, to store/<sha256>/, read-only, and that
    copy's path. A copy already there is used once it still hashes to its name; a
    directory of that name holding anything else is refused, never written over."""
    binary = HOSTS["sdl3-gpu"][0]
    built = bin_dir / binary
    if not built.is_file():
        raise SystemExit(f"no {binary} in {rel(bin_dir)}: build it (drop --no-player-build) or "
                         "name the directory with --player-bin")
    digest = PR.sha256(built)
    home = store / digest
    kept = home / binary
    if home.exists():
        if not kept.is_file() or PR.sha256(kept) != digest:
            raise SystemExit(f"{rel(home)} does not hold {binary} with sha256 {digest}: a kept "
                             "renderer is never written over")
        return kept
    part = store / f".{digest}.{os.getpid()}.part"
    part.mkdir(parents=True)
    shutil.copyfile(built, part / binary)
    record = bin_dir / f"{binary}.json"
    if record.is_file():
        shutil.copyfile(record, part / record.name)
        (part / record.name).chmod(0o444)
    (part / binary).chmod(0o555)
    if PR.sha256(part / binary) != digest:
        raise SystemExit(f"the copy of {rel(built)} does not hash as it did")
    part.chmod(0o555)
    os.rename(part, home)
    return kept


def plan(args) -> dict:
    """Everything a session runs, decided before anything runs."""
    scene = args.scene
    build = args.build or default_build(scene)
    why = PR.build_name_problem(build, SCENES[scene][1])
    if why:
        raise SystemExit(why)
    if build.startswith(tuple(PR.BUILDS.values())) or build.startswith("present-live"):
        raise SystemExit(f"--build {build}: that is a presentation run's own build name; a play "
                         "session relinks it with another configuration")
    base = under_private(ROOT / (args.out or GEN_INPUT.relative_to(ROOT) / scene), "--out",
                         "a session's outputs are captures and")
    bin_dir = under_private(ROOT / args.player_bin, "--player-bin",
                            "the player a session builds and runs is a build output and must")
    store = under_private(ROOT / args.renderer_store, "--renderer-store",
                          "the renderers sessions ran are build outputs and")
    host = args.host
    synthetic = synthetic_plan(args)
    if not (args.stream_cap_gb > 0 and args.disk_floor_gb >= 0):
        raise SystemExit("--stream-cap-gb must be above 0 and --disk-floor-gb not below 0")
    run_id = "dry-run" if args.dry_run else \
        f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{secrets.token_hex(4)}"
    p = paths(base / run_id)
    sound = sound_plan(args, p, inherited_driver(os.environ))
    pace = {"on": not args.no_pace, "lead_frames": None if args.no_pace else LEAD_FRAMES}
    profile = profile_of(args.profile, scene)
    profile_env = RR.profile_env(profile)
    receipt = receipt_plan(args, scene, host)
    meta = {"build": build, "scene": scene, "run": run_id, "runner": "tools/s5/play_live.py",
            "transport": "fifo", "interactive": True, "host": host,
            **({"audio": True} if sound["on"] else {})}
    cmd, spec = game_command(scene, build, p, profile_env, meta, sound["on"], receipt["on"],
                             synthetic is not None)
    penv = host_env(sound_env(PL.player_env(p["out"], bin_dir, p["dxvk_log"]), sound), host, p)
    limits = {"stream_cap_bytes": int(args.stream_cap_gb * 1e9),
              "disk_floor_bytes": int(args.disk_floor_gb * 1e9)}
    pl = {"scene": scene, "build": build, "host": host, "paths": p, "profile": profile,
          "meta": meta, "cmd": cmd, "spec": spec, "player_env": penv, "bin_dir": bin_dir,
          "renderer_store": store, "renderer": None, "synthetic": synthetic,
          "binary": PR.LINKDIR / "mains" / build / "sa2_main", "limits": limits,
          "sound": sound, "pace": pace, "receipt": receipt,
          "hooks": scene_hooks(scene, rel(p["ticks_observer"]) if receipt["on"] else None)[0]}
    pl["player_cmd"] = session_player_command(pl)
    return pl


def session_player_command(pl: dict) -> list[str]:
    """The plan's player command, with the renderer it runs once one is kept (sdl3-gpu)."""
    return player_command(pl["paths"], pl["sound"], pl["pace"]["on"],
                          latency=pl["receipt"]["on"], host=pl["host"], binary=pl["renderer"],
                          synthetic=pl["synthetic"])


def show(pl: dict, build_player: bool) -> list[str]:
    """The commands, as a shell would take them, one per line."""
    p = pl["paths"]
    lines = [f"scene: {pl['scene']}; build: {pl['build']}; run directory: {rel(p['out'])}; "
             f"profile: {'default' if pl['profile'] == DP.DEFAULT else pl['profile']['path']}"]
    binary, script, _ = HOSTS[pl["host"]]
    lines.append(f"host: {pl['host']}")
    if build_player:
        lines.append(f"player build: OUT={shlex.quote(rel(pl['bin_dir']))} bash {script}")
    if pl["host"] == "sdl3-gpu":
        lines.append(f"renderer: {rel(pl['renderer'] if pl['renderer'] else pl['renderer_store'] / '<sha256>' / binary)}, "
                     f"a read-only copy of {rel(pl['bin_dir'] / binary)} kept by its sha256, run "
                     "with no inherited SA2_D3D9GPU_ variable")
    lines.append("game: " + shlex.join(pl["cmd"]))
    lines.append("player: " + player_env_shown(pl["player_env"]) + " " + shlex.join(pl["player_cmd"]))
    lines.append(f"fifos: keyboard {rel(p['input_fifo'])} (made and held open O_RDWR by this "
                 f"runner, never read); stream {rel(p['game_fifo'])} (read by this runner's "
                 f"relay into {rel(p['copy'])}, which feeds descriptor {VIDEO_FD})")
    lim = pl["limits"]
    lines.append(f"limits: the game is asked to stop when {rel(p['copy'])} passes "
                 f"{lim['stream_cap_bytes']} bytes or the free space under {rel(p['out'].parent)} "
                 f"falls below {lim['disk_floor_bytes']} bytes")
    lines.append("sound: " + sound_shown(pl["sound"]))
    lines.append("pace: " + pace_shown(pl["pace"], p))
    lines.append("receipt: " + receipt_shown(pl["receipt"], p))
    lines.append("end: " + end_shown(pl))
    if pl["synthetic"]:
        sy = pl["synthetic"]
        lines.append(f"synthetic: {sy['presses']} presses of {sy['key']} (scancode "
                     f"{sy['scancode']}, seed {sy['seed']}) written to {rel(p['synthetic_keys'])} "
                     f"and queued by the player from its first Present, then the window's "
                     f"close request {sy['close_ms']} ms after it; the window's own keys are "
                     f"not sent; the session ends at the close, or at {sy['max_frames']} "
                     "Presents; never a tracked receipt, and the recorder's counts go to "
                     f"{rel(p['synthetic_counts'])}, outside M7's registry")
    return lines


def end_shown(pl: dict) -> str:
    """How the session asks the game to end (the module docstring, "How a session ends")."""
    said = []
    if pl["receipt"]["on"]:
        said.append(f"the goal: once {pl['paths']['ticks'].name} shows the goal latch risen in "
                    f"the stage and {PRC.X.AFTER_GOAL_TICKS + END_MARGIN_TICKS} ticks past it "
                    f"(any other end sooner than {PRC.X.AFTER_GOAL_TICKS} ticks past it waits "
                    f"for them, at most {GOAL_HOLD_S:g} s)")
    if pl["synthetic"]:
        said.append(f"--max-frames: once it shows {pl['synthetic']['max_frames']} Presents")
    said += [f"the window's close (the player's end request on descriptor {END_FD})",
             "the first Ctrl-C (a second cancels the player)", "the disk limits"]
    return "; ".join(said) + ("; each asks the game to stop, and the player reads the stream "
                              "to its END")


def receipt_shown(receipt: dict, p: dict) -> str:
    if not receipt["on"]:
        return ("none (no --receipt): the game writes no per-tick table and the session no "
                "play receipt")
    return (f"the game writes its per-tick table to {rel(p['ticks'])} ({PRC.TICKS_ENV}, by "
            f"the observer {rel(p['ticks_observer'])} in the tick probe's place); at the end, "
            f"the session's document to {rel(p['play_record'])}, and the same bytes to G5's "
            f"play receipt {receipt['receipt']} if the table shows the goal ring reached after "
            f"level {receipt['level']} was entered and the evidence is fit, a receipt in place "
            f"kept first at {rel(p['replaced_receipt'])}; the latency logs "
            f"{rel(p['keys'])}, {rel(p['presents'])}, {rel(p['polls'])} and "
            f"{rel(p['relay_frames'])}")


def sound_shown(sound: dict) -> str:
    if not sound["on"]:
        return ("none (--no-sound): the game records none (a version 1 stream), and the "
                "player plays and writes none")
    if sound["driver"]:
        came = sound["driver_from"]
        source = (came if came == "--sound-driver" else "inherited" if came == "SDL_AUDIO_DRIVER"
                  else f"inherited as {came}, SDL2's name")
        driver = f"SDL_AUDIO_DRIVER={sound['driver']} ({source})"
    else:
        driver = "SDL's own choice (on this desktop, the speakers)"
    return ("the game records it (SA2_REC_AUDIO=1, a version 2 stream) and the player plays it "
            f"on SDL's default playback device (--audio), driver {driver}"
            + (f"; written to {rel(sound['wav'])} and its .json (--audio-wav)" if sound["wav"]
               else ""))


def pace_shown(pace: dict, p: dict) -> str:
    if not pace["on"]:
        return ("none (--no-pace): the game runs as fast as it can and the player shows each "
                "frame as it comes")
    return (f"the player presents each frame at its virtual time (--pace; per frame in "
            f"{rel(p['player']['pace_log'])}), and the relay takes nothing more from the game "
            f"while {pace['lead_frames']} whole frame(s) wait for the player ({pace['lead_frames'] + 1} "
            "while the game is not found waiting), so a game ahead of real time waits in its "
            "write")


# ---------------------------------------------------------------- the session

def game_pid(loop_pid: int, binary: Path) -> int | None:
    """loop_run's child running the build's binary, or None."""
    want = str(binary.resolve())
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
            if int(stat.rsplit(")", 1)[1].split()[1]) != loop_pid:
                continue
            if os.readlink(f"/proc/{d}/exe") == want:
                return int(d)
        except (OSError, ValueError, IndexError):
            continue
    return None


def kill_group(loop: subprocess.Popen) -> None:
    try:
        os.killpg(loop.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_game(loop: subprocess.Popen, binary: Path, ended: threading.Event) -> dict:
    """Ask the game to end (the module docstring, "How a session ends"); what it took.
    The game's process is found again before each SIGTERM: after a grace its first pid
    may be gone, and by then another process's."""
    t0 = time.monotonic()
    pid = game_pid(loop.pid, binary)
    if pid is None:
        if ended.wait(5):
            return {"how": "the game had ended", "seconds": round(time.monotonic() - t0, 2)}
        kill_group(loop)
        ended.wait()
        return {"how": "no game process to ask: SIGKILL to loop_run's group",
                "seconds": round(time.monotonic() - t0, 2)}
    steps = [("SIGTERM: WM_QUIT at the game's next keyboard poll", STOP_GRACE),
             ("a second SIGTERM", KILL_GRACE)]
    signalled = []
    for how, grace in steps:
        now = pid if not signalled else game_pid(loop.pid, binary)
        if now is not None:
            try:
                os.kill(now, signal.SIGTERM)
                signalled.append(now)
            except ProcessLookupError:
                pass
        if ended.wait(grace):
            return {"how": how, "pid": pid, "signalled": signalled,
                    "seconds": round(time.monotonic() - t0, 2)}
    kill_group(loop)
    ended.wait()
    return {"how": "SIGKILL to loop_run's group", "pid": pid, "signalled": signalled,
            "seconds": round(time.monotonic() - t0, 2)}


def fresh_fifo(path: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()
    os.mkfifo(path, 0o600)


def free_fd(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def nearest_dir(path: Path) -> Path:
    """`path`, or its nearest ancestor that is a directory: where a refused session's free
    space is measured without making anything."""
    while not path.is_dir():
        path = path.parent
    return path


def over_limit(copy: Path, out: Path, limits: dict) -> dict | None:
    """Why the disk ends the session now, or None: the relay's copy past its cap, or the
    free space under the session's directory below its floor."""
    try:
        size = copy.stat().st_size
    except FileNotFoundError:
        size = 0
    free = free_bytes(out)
    if size > limits["stream_cap_bytes"]:
        why = f"the stream copy passed {limits['stream_cap_bytes']} bytes (--stream-cap-gb)"
    elif free < limits["disk_floor_bytes"]:
        why = f"the free space fell below {limits['disk_floor_bytes']} bytes (--disk-floor-gb)"
    else:
        return None
    return {"why": why, "copy_bytes": size, "free_bytes": free}


def drain_pipe(fd: int, ended: threading.Event) -> None:
    """Read and drop whatever the game still writes into the stream's pipe until it ends,
    so a game asked to stop is never held in a write and can reach its next poll."""
    pl = select.poll()
    pl.register(fd, select.POLLIN)
    while not ended.is_set():
        if not pl.poll(100):
            continue
        try:
            if not os.read(fd, PL.CHUNK):
                time.sleep(0.05)          # no writer (yet, or any more)
        except BlockingIOError:
            pass
        except OSError:
            return


def validate_latency_paths(p: dict) -> None:
    """Requested latency files must be fresh and distinct from every producer input/output."""
    requested = [p[key] for key in
                 ("ticks", "polls", "keys", "presents", "relay_frames", "poll_report", "relay_report")]
    others = [p[key] for key in ("input_fifo", "game_fifo", "copy", "run", "ticks_observer")]
    others += [p["player"]["report"], p["player"]["pace_log"]]
    others += [ROOT / name for name in PRC.PIPELINE]
    canonical = [path.resolve() for path in requested + others]
    if len(set(canonical[:len(requested)])) != len(requested) or any(
            path in canonical[len(requested):] for path in canonical[:len(requested)]):
        raise ValueError("latency output paths alias a producer input/output")
    for path in requested:
        if path.exists() or path.is_symlink() or not path.is_relative_to(p["out"]):
            raise ValueError(f"latency output must be fresh in the session: {path}")


class TickWatch:
    """The game's per-tick table read as it grows, for the two ends it gives a session
    (the module docstring, "How a session ends"): the goal latch risen after the stage was
    entered, then AFTER_GOAL_TICKS and END_MARGIN_TICKS more ticks; and, with a frame
    budget, that many Presents. A table of another header, or a row that is not whole or
    not integers, ends nothing."""

    def __init__(self, level: int, budget: int | None = None):
        self.level, self.budget = level, budget
        self.offset, self.rest, self.header = 0, b"", None
        self.entered = self.goal = None
        self.tick = self.frames = 0

    def read(self, path: Path) -> dict | None:
        """The rows written since the last read; the end they give, or None."""
        try:
            with open(path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return None
        self.offset += len(data)
        lines = (self.rest + data).split(b"\n")
        self.rest = lines.pop()
        for line in lines:
            cells = line.decode("utf-8", "replace").split("\t")
            if self.header is None:
                self.header = cells
                continue
            if self.header != list(PRC.X.PLAY_TICKS) or len(cells) != len(self.header):
                continue
            try:
                row = {k: int(v, 0) for k, v in zip(self.header, cells)}
            except ValueError:
                continue
            self.tick, self.frames = row["tick"], row["frames"]
            if self.entered is None and PRC.X.in_stage(row, self.level):
                self.entered = self.tick
            if (self.entered is not None and self.goal is None
                    and row["goal_latch"] not in (0, PRC.UNREADABLE)):
                self.goal = self.tick
        if self.goal is not None and \
                self.tick - self.goal >= PRC.X.AFTER_GOAL_TICKS + END_MARGIN_TICKS:
            return {"by": "the goal", "entered_tick": self.entered, "goal_tick": self.goal,
                    "tick": self.tick}
        if self.budget is not None and self.frames >= self.budget:
            return {"by": "--max-frames", "frames": self.frames, "tick": self.tick}
        return None


def run_session(pl: dict) -> dict:
    """The game, the relay, the player and the keyboard FIFO, together; what each did and
    how the session ended. On the main thread the first Ctrl-C asks the session to end
    (the module docstring, "How a session ends") and the next raises as Ctrl-C does."""
    main = threading.current_thread() is threading.main_thread()
    asked: dict = {}

    def on_sigint(signum, frame) -> None:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        ask = asked.get("end")
        if ask is None:
            raise KeyboardInterrupt
        threading.Thread(target=ask, args=({"by": "Ctrl-C"},), daemon=True).start()

    previous = signal.signal(signal.SIGINT, on_sigint) if main else None
    try:
        return session_parties(pl, asked)
    finally:
        if main:
            signal.signal(signal.SIGINT, previous)


def session_parties(pl: dict, asked: dict) -> dict:
    """run_session's body; `asked["end"]` is set to its way to ask the session to end."""
    p, pp = pl["paths"], pl["paths"]["player"]
    if pl["receipt"]["on"]:
        validate_latency_paths(p)
    if not (free_fd(VIDEO_FD) and free_fd(CANCEL_FD) and free_fd(END_FD)):
        raise SystemExit(f"descriptors {VIDEO_FD}, {CANCEL_FD} and {END_FD} are in use in this "
                         "process")
    fresh_fifo(p["input_fifo"])
    hold = os.open(p["input_fifo"], os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    game_fd = PL.open_pipe(p["game_fifo"])
    video_r, video_w = os.pipe()
    if pl["pace"]["on"]:
        fcntl.fcntl(video_w, PL.F_SETPIPE_SZ, PLAYER_PIPE_BYTES)
    cancel_r, cancel_w = os.pipe()
    end_r, end_w = os.pipe()
    os.dup2(video_r, VIDEO_FD, inheritable=False)
    os.dup2(cancel_r, CANCEL_FD, inheritable=False)
    os.dup2(end_w, END_FD, inheritable=False)
    os.close(video_r)
    os.close(cancel_r)
    os.close(end_w)
    glog = open(p["game_log"], "w")
    glog.write(f"$ {shlex.join(pl['cmd'])}\n")
    glog.flush()
    plog = open(pp["log"], "w")
    ended, player_ended = threading.Event(), threading.Event()
    stop_lock, end_lock = threading.Lock(), threading.Lock()
    box: dict = {"stop": None, "player": None, "end": None}
    t0 = time.monotonic()
    loop = subprocess.Popen(pl["cmd"], cwd=ROOT, stdout=glog, stderr=subprocess.STDOUT,
                            env=game_env(), start_new_session=True)

    def said_end(why: dict) -> None:
        with end_lock:
            if box["end"] is None:
                box["end"] = why | {"at_s": round(time.monotonic() - t0, 2)}

    def wait_game() -> None:
        loop.wait()
        box["game_ended"] = time.monotonic()
        said_end({"by": "the game ended"})
        ended.set()

    def stop_once() -> None:
        box["stopping"] = True
        with stop_lock:
            if box["stop"] is None and not ended.is_set():
                box["stop"] = stop_game(loop, pl["binary"], ended)

    watch = (TickWatch(pl["receipt"]["level"],
                       pl["synthetic"]["max_frames"] if pl.get("synthetic") else None)
             if pl["receipt"]["on"] else None)
    watch_lock = threading.Lock()

    def look() -> dict | None:
        """The per-tick table read on from where it was last read: the end it gives."""
        with watch_lock:
            return watch.read(p["ticks"])

    def stop_past_goal() -> None:
        """stop_once, once the game has made the clause's AFTER_GOAL_TICKS past a goal the
        table shows (the module docstring, "How a session ends"), or GOAL_HOLD_S later."""
        start = time.monotonic()
        held = None
        while (watch is not None and not ended.is_set() and not box.get("stopping")
               and time.monotonic() < start + GOAL_HOLD_S):    # a stop under way: no wait
            look()
            if watch.goal is None or watch.tick - watch.goal >= PRC.X.AFTER_GOAL_TICKS:
                break
            held = held or {"goal_tick": watch.goal, "from_tick": watch.tick}
            ended.wait(TICK_WATCH_EVERY / 5)
        if held:
            box.setdefault("goal_hold", held | {"to_tick": watch.tick,
                                                "seconds": round(time.monotonic() - start, 2)})
        stop_once()

    end_heard = threading.Event()
    asks_player = "--end-fd" in pl["player_cmd"]

    def ask_player() -> bool:
        """The player asked to end first (SIGUSR1, --end-fd's other half): it lets every key
        up and closes its sender, then answers on END_FD, and the answer stops the game, so
        no key record comes after the game's last keyboard poll. False when it cannot be."""
        player = box.get("player")
        if not asks_player or player is None or player.poll() is not None or end_heard.is_set():
            return False
        try:
            if Path(os.readlink(f"/proc/{player.pid}/exe")).name in SHELLS:
                return False
            os.kill(player.pid, signal.SIGUSR1)
        except OSError:
            return False
        return True

    def ask_end(why: dict) -> None:
        """The session asked to end: the first reason is kept, the player asked to let its
        keys up, and the game asked to stop once it answers (or END_ASK_GRACE later)."""
        said_end(why)
        if ask_player() and end_heard.wait(END_ASK_GRACE):
            return                    # watch_end stops the game on the player's answer
        stop_past_goal()

    asked["end"] = ask_end

    def watch_end() -> None:
        """The player's end request (--end-fd): its window's close, or its answer to
        ask_player; either way its keys are up and its sender closed, and the game is asked
        to stop. End of file is the player gone, with this process's copy closed."""
        try:
            got = os.read(end_r, 1)
        except OSError:
            return
        if got:
            end_heard.set()
            said_end({"by": "the window's close"})
            stop_past_goal()

    def watch_ticks() -> None:
        """The game's per-tick table: the goal, or --max-frames' budget."""
        while not ended.wait(TICK_WATCH_EVERY):
            why = look()
            if why:
                ask_end(why)
                return

    def run_player() -> None:
        """The player once the game's binary runs; when it ends first, the game asked to."""
        try:
            while not ended.is_set():
                pid = game_pid(loop.pid, pl["binary"])
                if pid:
                    box["game_pid"] = pid
                    box["game_started"] = time.monotonic()
                    break
                time.sleep(0.2)
            else:
                return
            try:
                # A process group of its own: a Ctrl-C at the terminal is this process's.
                box["player"] = subprocess.Popen(
                    pl["player_cmd"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=plog,
                    stderr=subprocess.STDOUT, env=pl["player_env"],
                    pass_fds=(VIDEO_FD, CANCEL_FD, END_FD), process_group=0)
                box["player_started"] = time.monotonic()
            except OSError as e:
                box["player_error"] = f"{type(e).__name__}: {e}"
        finally:
            os.close(VIDEO_FD)        # the player holds its own; with none, the feeder's
            os.close(CANCEL_FD)       # writes fail instead of filling a pipe nobody reads,
            os.close(END_FD)          # and the end request's reader sees end of file
        if box["player"] is not None:
            box["player"].wait()
            box["player_ended"] = time.monotonic()
        player_ended.set()
        ask_end({"by": "the player ended"})

    def watch_disk() -> None:
        """The game asked to stop, as a closed window asks it, before the relay's copy
        fills the disk."""
        while not ended.wait(WATCH_EVERY):
            hit = over_limit(p["copy"], p["out"], pl["limits"])
            if hit:
                box["limit"] = hit | {"at_s": round(time.monotonic() - t0, 2)}
                ask_end({"by": "the disk limits"})
                return

    threading.Thread(target=wait_game, daemon=True).start()
    watcher = threading.Thread(target=run_player, daemon=True)
    watcher.start()
    threading.Thread(target=watch_disk, daemon=True).start()
    end_reader = threading.Thread(target=watch_end, daemon=True)
    end_reader.start()
    if pl["receipt"]["on"]:
        threading.Thread(target=watch_ticks, daemon=True).start()
    error = None
    arrival_diagnostics = {} if pl["receipt"]["on"] else None
    cleanup_error = None

    def cleanup_failed(name: str, failure: Exception) -> None:
        nonlocal cleanup_error
        if cleanup_error is None:
            cleanup_error = failure
        detail = {"operation": name, "error": f"{type(failure).__name__}: {failure}"}
        arrival_diagnostics.setdefault("session_cleanup_errors", []).append(detail)
        if arrival_diagnostics.get("error") is None:
            arrival_diagnostics["error"] = f"session cleanup {name}: {detail['error']}"

    def attempt_cleanup(name: str, action):
        try:
            return action()
        except Exception as failure:
            cleanup_failed(name, failure)

    try:
        relay_st = PL.relay(game_fd, p["copy"], video_w, lambda: not ended.is_set(),
                            lead_frames=pl["pace"]["lead_frames"],
                            **({"arrival_diagnostics": arrival_diagnostics}
                               if arrival_diagnostics is not None else {}))
    except BaseException as e:        # Ctrl-C, or the relay failing: the session ends
        error = e
        relay_st = {"end": f"stopped by {type(e).__name__}: {e}"}
        drainer = threading.Thread(target=drain_pipe, args=(game_fd, ended), daemon=True)
        if arrival_diagnostics is None:
            drainer.start()
            stop_once()
            drainer.join(timeout=5)
        else:
            attempt_cleanup("start drainer", drainer.start)
            attempt_cleanup("stop game", stop_once)
            attempt_cleanup("join drainer", lambda: drainer.join(timeout=5))
    if arrival_diagnostics is None:
        ended.wait()
        os.close(game_fd)
        # The game is gone. A player still running is reading the rest of the stream.
        player = box.get("player")
        if player is not None and player.poll() is None:
            if error is not None or not player_ended.wait(PL.FEED_GRACE):
                os.write(cancel_w, b"\x01")           # stream.h: byte 1 cancels the read
                try:
                    player.wait(timeout=KILL_GRACE)
                except subprocess.TimeoutExpired:
                    player.kill()
                    player.wait()
                box["player_cancelled"] = True
        watcher.join(timeout=STOP_GRACE + KILL_GRACE + 30)
        end_reader.join(timeout=5)
        os.close(cancel_w)
        os.close(end_r)
        os.close(hold)
        glog.close()
        plog.close()
    else:
        attempt_cleanup("wait game", ended.wait)
        attempt_cleanup("close game stream", lambda: os.close(game_fd))
        player = box.get("player")
        if player is not None:
            alive = attempt_cleanup("poll player", player.poll) is None
            stream_ended = (False if error is not None or not alive else
                            attempt_cleanup("wait player stream", lambda: player_ended.wait(PL.FEED_GRACE)))
            if alive and not stream_ended:
                cancelled = attempt_cleanup("cancel player", lambda: os.write(cancel_w, b"\x01"))
                box["player_cancelled"] = cancelled == 1
                try:
                    player.wait(timeout=KILL_GRACE)
                except subprocess.TimeoutExpired:
                    attempt_cleanup("kill player", player.kill)
                    attempt_cleanup("wait killed player", player.wait)
                except Exception as failure:
                    cleanup_failed("wait player", failure)
        attempt_cleanup("join player watcher", lambda: watcher.join(timeout=STOP_GRACE + KILL_GRACE + 30))
        attempt_cleanup("join end reader", lambda: end_reader.join(timeout=5))
        attempt_cleanup("close cancel pipe", lambda: os.close(cancel_w))
        attempt_cleanup("close end pipe", lambda: os.close(end_r))
        attempt_cleanup("close input hold", lambda: os.close(hold))
        attempt_cleanup("close game log", glog.close)
        attempt_cleanup("close player log", plog.close)
    arrival_report = None
    if arrival_diagnostics is not None:
        try:
            arrival_report = write_arrivals_ns(p["relay_frames"], arrival_diagnostics,
                                              p["relay_report"])
        except Exception as diagnostic_error:
            # Preserve an original transport exception even if diagnostic publication fails.
            if error is None and cleanup_error is None:
                raise
            text = f"{type(diagnostic_error).__name__}: {diagnostic_error}\n"
            try:
                with open(p["out"] / "relay-diagnostic-error.txt", "x") as diagnostic_log:
                    diagnostic_log.write(text)
            except OSError as retention_error:
                print(text + f"diagnostic retention failed: {retention_error}", file=sys.stderr)
    if error is not None and not isinstance(error, KeyboardInterrupt):
        raise error
    if cleanup_error is not None:
        raise cleanup_error
    relay_st.pop("feed_thread", None)
    if arrival_diagnostics is None and relay_st.get("frame_arrival_s") is not None:
        write_arrivals(p["relay_frames"], relay_st["t0_monotonic"], relay_st["frame_arrival_s"])
    for src, dst in ((PR.RUN_DIR / f"{pl['build']}.{LABEL}.json", p["run"]),
                     (PR.RUN_DIR / f"{pl['build']}.matrix.json", p["matrix"])):
        if src.is_file():
            shutil.move(str(src), str(dst))
    on = (lambda k: round(box[k] - t0, 2) if k in box else None)
    result = {"interrupted": error is not None, "game_rc": loop.returncode,
            "player_rc": player.returncode if player is not None else None,
            "player_error": box.get("player_error"),
            "player_cancelled": bool(box.get("player_cancelled")), "stop": box["stop"],
            "limit": box.get("limit"), "end": box["end"],
            "goal_hold": box.get("goal_hold"),
            "seconds": {"game_started": on("game_started"), "player_started": on("player_started"),
                        "player_ended": on("player_ended"), "game_ended": on("game_ended")},
            "relay": {k: relay_st.get(k) for k in ("end", "bytes", "records", "frames_arrived",
                                                   "framing_problem", "max_gap_s", "held")}
             | {"feed_error": (relay_st.get("feed") or {}).get("error")}}
    if arrival_report is not None:
        result["arrival_diagnostics"] = arrival_report
    return result


def write_arrivals(path: Path, t0: float, arrivals: list[float]) -> None:
    """The relay's frames as they came whole: frame, and the CLOCK_MONOTONIC second its
    record's last byte was read (the clock of the player's pace log)."""
    with open(path, "w") as f:
        f.write("frame\tarrived_s\n")
        for k, a in enumerate(arrivals, 1):
            f.write(f"{k}\t{t0 + a:.6f}\n")


def write_arrivals_ns(path: Path, diagnostics: dict, report_path: Path) -> dict:
    """Serialize held integer samples after transport cleanup; readiness is file evidence."""
    report = {"schema": "sa2-s5-relay-arrivals/v1", "method": "v1",
              "clock": "CLOCK_MONOTONIC", "unit": "ns", "requested": True,
              "path": str(path), "opened": False, "finalized": False, "closed": False,
              "ok": False, "rows": 0,
              **{key: diagnostics.get(key) for key in
                 ("clock_info", "complete_frames", "record_headers_seen", "bytes_read",
                  "clock_errors", "io_errors", "transport_end", "framing_problem",
                  "feed_ended", "feed_error", "error", "session_cleanup_errors")}}
    report["io_errors"] = report.get("io_errors") or 0
    raw = None

    def io_error(error):
        report["io_errors"] += 1
        if report["error"] is None:
            report["error"] = f"{type(error).__name__}: {error}"

    def row(line):
        if raw.write(line) != len(line):
            raise OSError("short arrival-log write")
        raw.flush()

    try:
        raw = open(path, "x", encoding="utf-8", newline="")
        report["opened"] = True
        row("frame\tarrived_s\n")
        for frame, stamp in diagnostics.get("arrivals", []):
            if type(frame) is not int or frame < 1 or type(stamp) is not int or stamp < 0:
                raise ValueError("invalid held arrival sample")
            seconds, nanoseconds = divmod(stamp, 1_000_000_000)
            row(f"{frame}\t{seconds}.{nanoseconds:09d}\n")
            report["rows"] += 1
    except (OSError, ValueError) as error:
        io_error(error)
    finally:
        if raw is not None:
            try:
                raw.close()
                report["closed"] = True
            except OSError as error:
                io_error(error)
        report["finalized"] = True
    report["dropped_frames"] = (diagnostics.get("complete_frames", 0) - report["rows"])
    report["ok"] = bool(report["opened"] and report["closed"] and
                        not report["clock_errors"] and not report["io_errors"] and
                        not report["error"] and not report["framing_problem"] and
                        not report["feed_error"] and report["feed_ended"] is True and
                        diagnostics.get("relay_finished") is True and
                        diagnostics.get("copy_closed") is True and
                        not diagnostics.get("parser_partial", True) and
                        report["transport_end"] == "the writer closed the pipe" and
                        report["dropped_frames"] == 0)
    part = report_path.with_name(report_path.name + ".part")
    with open(part, "x", encoding="utf-8") as output:
        body = json.dumps(report, indent=2) + "\n"
        if output.write(body) != len(body):
            raise OSError("short arrival-report write")
        output.flush()
    if report_path.exists() or report_path.is_symlink():
        raise FileExistsError(report_path)
    os.rename(part, report_path)
    return report


# ---------------------------------------------------------------- the receipt

KEY_LINE = re.compile(r"^\d+ \d+ 0x[0-9A-F]{2}(,0x[0-9A-F]{2})*$")


def log_summary(log: Path) -> dict:
    if not log.is_file():
        return {"path": rel(log), "present": False}
    lines = log.read_text().splitlines()
    return {"path": rel(log), "present": True, "sha256": PR.sha256(log),
            "key_lines": sum(1 for x in lines if KEY_LINE.match(x)),
            "closed": next((x for x in lines if x.startswith("# closed at exit:")), None)}


def run_record(path: Path) -> dict | None:
    if not path.is_file():
        return None
    d = json.loads(path.read_text())
    pump = d.get("pump") or {}
    return {"ok": d.get("ok"), "trap": d.get("trap"),
            "pump": {k: pump.get(k) for k in ("iterations", "quit_by_host", "quit_by_guest",
                                              "status")}}


def player_summary(report: Path) -> dict | None:
    if not report.is_file():
        return None
    r = json.loads(report.read_text())
    return {k: r.get(k) for k in ("complete", "pass", "exit_status", "stop", "presents",
                                  "host_input", "end_request", "synthetic_keys", "audio", "pace",
                                  "failures")}


def read_tsv(path: Path) -> list[dict] | None:
    if not path.is_file():
        return None
    lines = path.read_text().splitlines()
    if not lines:
        return []
    head = lines[0].split("\t")
    return [dict(zip(head, x.split("\t"))) for x in lines[1:] if x.count("\t") == len(head) - 1]


def quantiles(xs: list[int]) -> dict:
    s = sorted(xs)
    return {"p50": s[len(s) // 2], "p90": s[int(len(s) * 0.9)], "max": s[-1],
            "mean": round(sum(s) / len(s), 3)}


def frames_ahead(pace_log: Path, relay_frames: Path) -> dict | None:
    """At each paced Present, how many frames the relay had whole beyond the one presented:
    frames the game had made ahead of the picture (the module docstring, "Pace"). From the
    player's pace log and the relay's arrivals, both on CLOCK_MONOTONIC; frame k is the
    k-th Present and the k-th FRAME record."""
    rows, arrived = read_tsv(pace_log), read_tsv(relay_frames)
    if not rows or not arrived:
        return None
    times = sorted(float(a["arrived_s"]) for a in arrived)
    ahead = [bisect.bisect_right(times, float(r["present_s"])) - int(r["frame"]) for r in rows]
    return {"presents": len(ahead), "relay_frames": len(times), **quantiles(ahead)}


def pace_said(pace: dict, player: dict | None, relay_held: dict | None,
              ahead: dict | None) -> str:
    """The session's pace, from the player's report and the relay's account (the module
    docstring, "Pace")."""
    if not pace["on"]:
        return "pace: none (--no-pace)"
    pa = (player or {}).get("pace")
    if not isinstance(pa, dict):
        return "pace: the player's report has no pace account"
    late = pa.get("late") or {}
    said = (f"pace: {pa.get('virtual_per_wall')} virtual s per wall s over {pa.get('frames')} "
            f"frames ({pa.get('virtual_s')} s in {pa.get('wall_s')} s); "
            f"{late.get('over_frame')} frames presented over a frame late, the latest by "
            f"{late.get('max_ms')} ms; the schedule moved {pa.get('moves')} times, "
            f"{pa.get('moved_s')} s in all")
    if relay_held:
        said += (f"; the relay held the game {relay_held.get('pauses')} times, "
                 f"{relay_held.get('paused_s')} s in all")
    if ahead:
        said += (f"; frames whole at the relay beyond the one presented: median "
                 f"{ahead['p50']}, at most {ahead['max']}")
    return said


def sound_said(sound: dict, player: dict | None) -> str:
    """The session's sound, from the player's report: its device, the blocks it queued,
    and the two ways the game's pace shows (the module docstring, "Sound")."""
    if not sound["on"]:
        return "sound: none (--no-sound)"
    au = (player or {}).get("audio")
    if not isinstance(au, dict):
        return "sound: the player's report has no audio account"
    dev = au.get("device") if isinstance(au.get("device"), dict) else {}
    said = (f"sound: {au.get('stream_records')} audio records; SDL {dev.get('driver')} driver"
            f"{', failed: ' + str(dev['failure']) if dev.get('failure') else ''}; "
            f"{dev.get('blocks_queued')} blocks queued, {dev.get('blocks_dropped')} dropped on a "
            f"full queue, {dev.get('shortfalls')} device shortfalls; "
            f"{(au.get('problems') or {}).get('count')} mixer problems")
    wav = au.get("wav") if isinstance(au.get("wav"), dict) else None
    return said + (f"; WAV {wav.get('path')}, {wav.get('frames')} samples" if wav else "")


def play_receipt(pl: dict) -> dict:
    """The session's play record and, from the same bytes when the session is fit for it,
    G5's play receipt (the module docstring, "The play receipt"), and the clause's reading
    of a receipt written. A step that fails gives its error, which the session's
    play-receipt-step.json keeps, instead of raising."""
    p = pl["paths"]
    got: dict = {"receipt": None}
    try:
        got = PRC.write({"host": pl["host"], "run_id": pl["meta"]["run"],
                         "level": pl["receipt"]["level"], "pipeline": pl["receipt"]["pipeline"],
                         "renderer": pl.get("renderer"), "ticks": p["ticks"],
                         "observer": p["ticks_observer"], "report": p["player"]["report"],
                         "run": p["run"], "record": p["play_record"],
                         "replaced": p["replaced_receipt"],
                         "keys": p["keys"], "presents": p["presents"],
                         "polls": p["polls"], "relay_frames": p["relay_frames"],
                         "poll_report": p["poll_report"], "relay_report": p["relay_report"],
                         "session": p["receipt"]})
        if got["receipt"]:
            got["clause_reading"] = PRC.reading(pl["host"])
    except Exception as error:      # the step's record keeps it; the session is over
        got = got | {"error": f"{type(error).__name__}: {error}"}
    return got


def latency_said(latency: dict | None) -> str:
    """The closing line for the input latency: the stage's presses, else all of them."""
    if not latency:
        return "input latency: not measured"
    summary = latency["summary"]
    part, where = (summary["stage"], "in the stage") if summary["stage"]["total_us"] else (
        summary, "in the session")
    total, dequeue = part["total_us"], part["from_dequeue_us"]
    if not total:
        return f"input latency: no press presented of {summary['presses']}"
    ms = (lambda us: f"{us / 1000:.1f}")
    by = part["by_status"]
    return (f"input latency{' (diagnostic)' if latency.get('diagnostic') else ''}, press to "
            f"presented frame: median {ms(total['p50'])} ms, p90 {ms(total['p90'])}, max "
            f"{ms(total['max'])} over {by['presented']} presses {where} ({by['lost']} lost, "
            f"{by['unsent']} unsent, {by['unpresented']} unpresented; from dequeue: median "
            f"{ms(dequeue['p50'])} ms, "
            f"p90 {ms(dequeue['p90'])}, max {ms(dequeue['max'])})")


def play_said(got: dict) -> str:
    """The closing line for the play receipt: where it went, and why when it is untracked."""
    if got.get("error"):
        made = f"play receipt {got['receipt']} written, then" if got["receipt"] else (
            "no play receipt:")
        return f"{made} the receipt step failed: {got['error']}"
    if not got["receipt"]:
        return (f"play record {got['record']} (untracked; not the play receipt): "
                + "; ".join(got["not_tracked"]))
    replaced = got.get("replaced")
    gone = ""
    if replaced:
        whose = f"run {replaced['run_id']}'s" if replaced["run_id"] else "one naming no run"
        gone = f"; it replaces {whose}, kept at {replaced['kept']}"
    return (f"play receipt {got['receipt']}: level entered at tick {got['entered']}, the goal at "
            f"tick {got['goal']}{gone}; {got['clause_reading']}")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scene", choices=sorted(SCENES), default="boot")
    ap.add_argument("--host", choices=sorted(HOSTS), default="dxvk-native",
                    help="the player: dxvk-native (sa2_player) or sdl3-gpu (sa2_player_gpu)")
    ap.add_argument("--build", default=None, help="default: present-play-<scene>.<tree tag>")
    ap.add_argument("--out", type=Path, default=None,
                    help="where the session's directory, named by its run id, is made; "
                         "default: private/generated/s5/input/<scene>")
    ap.add_argument("--player-bin", type=Path, default=GEN_INPUT.relative_to(ROOT) / "bin",
                    help="the player's build directory, under private/")
    ap.add_argument("--no-player-build", action="store_true")
    ap.add_argument("--renderer-store", type=Path, default=RENDERER_STORE.relative_to(ROOT),
                    help="sdl3-gpu: where the GPU player a session runs is kept by its sha256, "
                         "under private/")
    ap.add_argument("--profile", choices=("default", "registered"), default="default")
    ap.add_argument("--keep-stream", action="store_true",
                    help="keep the relay's copy of the stream (large)")
    ap.add_argument("--stream-cap-gb", type=float, default=STREAM_CAP_GB,
                    help="end the session when the relay's copy of the stream passes this")
    ap.add_argument("--disk-floor-gb", type=float, default=DISK_FLOOR_GB,
                    help="end the session when the free space under its directory falls below "
                         "this, and refuse to start below it")
    ap.add_argument("--no-sound", action="store_true",
                    help="the session as before sound: the game records none (a version 1 "
                         "stream) and the player plays and writes none")
    ap.add_argument("--sound-driver", choices=SOUND_DRIVERS, default=None,
                    help="SDL's audio driver for the player (SDL_AUDIO_DRIVER). Default: an "
                         "inherited SDL_AUDIO_DRIVER, or where that is unset SDL2's "
                         "SDL_AUDIODRIVER (alsa or dummy), else SDL's own choice, which on "
                         "this desktop is the speakers (ALSA, routed to PipeWire). dummy plays "
                         "nothing")
    ap.add_argument("--audio-wav", action="store_true",
                    help="also write the session's sound to player/sound.wav (48 kHz stereo "
                         "float, about 1.4 GB an hour) and its .json")
    ap.add_argument("--no-pace", action="store_true",
                    help="the session as before pacing: the game as fast as it runs and each "
                         "frame shown as it comes")
    ap.add_argument("--receipt", action="store_true",
                    help="Metal Harbor only: the game writes its per-tick table, the session "
                         "its document to the untracked play-record.json, and a session that "
                         "reaches the goal ring the same bytes as G5's play receipt for this "
                         "host (Documentation/receipts/s5-play-<host>-v1.json)")
    ap.add_argument("--synthetic-keys", type=int, default=None, metavar="N",
                    help="verification with no person (with --receipt and --max-frames): N "
                         "seeded presses of the jump key from the player's own queue; the "
                         "window's keys are not sent and no receipt is tracked")
    ap.add_argument("--max-frames", type=int, default=None, metavar="M",
                    help="with --synthetic-keys: the session ends once the game has made M "
                         "Presents")
    ap.add_argument("--synthetic-seed", type=int, default=None,
                    help=f"with --synthetic-keys: the presses' seed (default {SYNTHETIC_SEED})")
    ap.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    pl = plan(args)
    for line in show(pl, not args.no_player_build):
        print(line)
    if args.dry_run:
        return 0
    busy = [f"gate pass {g}" for g in PL.gate_passes()]
    busy += [f"presentation run {g}" for g in PL.processes(PL.is_presentation_run)]
    if PL.mem_available_gb() < PL.MIN_FREE_GB:
        busy.append(f"{PL.mem_available_gb():.1f} GB available (want {PL.MIN_FREE_GB})")
    sess = PL.session()
    if not PL.session_holds(sess):
        busy.append(f"the desktop session is not live, unlocked and non-idle: {sess.get('logind')}")
    p = pl["paths"]
    free = free_bytes(nearest_dir(p["out"].parent))
    if free < pl["limits"]["disk_floor_bytes"]:
        busy.append(f"{free} bytes free under {rel(p['out'].parent)}, below the floor of "
                    f"{pl['limits']['disk_floor_bytes']} (--disk-floor-gb)")
    if busy:
        print("refusing: " + "; ".join(busy), file=sys.stderr)
        return 2
    # The session's own directory, new: nothing an earlier session left is removed or reused.
    p["out"].mkdir(parents=True)
    for d in (p["player"]["dir"], p["dxvk_log"], p["overlay"]):
        d.mkdir()
    player_build = None
    if not args.no_player_build:
        player_build = build_player(pl["host"], pl["bin_dir"], p["build_log"])
        if not player_build["ok"]:
            print(f"the player did not build; see {rel(p['build_log'])}", file=sys.stderr)
            return 2
    if pl["host"] == "sdl3-gpu":
        pl["renderer"] = keep_renderer(pl["bin_dir"], pl["renderer_store"])
        pl["player_cmd"] = session_player_command(pl)
        print(f"renderer: {rel(pl['renderer'])}")
    if pl["receipt"]["on"]:
        p["ticks_observer"].write_text(PRC.ticks_observer(), encoding="utf-8")
    if pl["synthetic"]:
        sy = pl["synthetic"]
        p["synthetic_keys"].write_text(synthetic_script(sy["presses"], sy["max_frames"], sy["seed"]))
    forgotten = PR.forget_hook_objects(pl["build"], tuple(pl["hooks"]))
    saves = SS.reset_lifted(pl["cmd"], ROOT)
    SS.require_empty(saves)
    written = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    session = run_session(pl)
    if not args.keep_stream:
        p["copy"].unlink(missing_ok=True)
    for f in (p["input_fifo"], p["game_fifo"]):
        f.unlink(missing_ok=True)
    player = player_summary(p["player"]["report"])
    ahead = frames_ahead(p["player"]["pace_log"], p["relay_frames"]) if pl["pace"]["on"] else None
    receipt = PL.relativized({
        "schema": "sa2-s5-play-live/v1", "written": written, "scene": pl["scene"],
        "host": pl["host"], "renderer": ({"path": rel(pl["renderer"]),
                                          "sha256": PR.sha256(pl["renderer"])}
                                         if pl["renderer"] else None),
        "synthetic": (pl["synthetic"] | {"script": {"path": rel(p["synthetic_keys"]),
                                                    "sha256": PR.sha256(p["synthetic_keys"])}}
                      if pl["synthetic"] else None),
        "build": pl["build"], "argv": [Path(sys.executable).name, rel(Path(__file__))]
        + (argv if argv is not None else sys.argv[1:]),
        "profile": "default" if pl["profile"] == DP.DEFAULT else
        {"path": pl["profile"].get("path"), "sha256": pl["profile"].get("sha256")},
        "commands": {"game": pl["cmd"], "player": pl["player_cmd"],
                     "player_env": player_env_shown(pl["player_env"])},
        "sound": {"on": pl["sound"]["on"], "driver": pl["sound"]["driver"],
                  "driver_from": pl["sound"]["driver_from"],
                  "wav": rel(pl["sound"]["wav"]) if pl["sound"]["wav"] else None},
        "pace": {**pl["pace"], "relay_held": session["relay"].get("held"), "ahead": ahead,
                 "pace_log": rel(p["player"]["pace_log"]) if pl["pace"]["on"] else None,
                 "relay_frames": rel(p["relay_frames"]) if p["relay_frames"].is_file() else None},
        "player_build": player_build, "hook_objects_deleted": forgotten, "saves": saves,
        "desktop_session": sess, "limits": pl["limits"], "session": session,
        "latency_transport_source": pl["receipt"].get("transport_source"),
        "input": log_summary(p["input_log"]), "run_record": run_record(p["run"]),
        "player": player,
        "stream_kept": bool(args.keep_stream and p["copy"].is_file()),
        "runner_sha256": PR.sha256(Path(__file__))})
    if pl["receipt"]["on"]:
        receipt["ticks_observer"] = {"path": rel(p["ticks_observer"]),
                                     "sha256": PR.sha256(p["ticks_observer"])}
    p["receipt"].write_text(json.dumps(receipt, indent=1, default=str) + "\n")
    if pl["receipt"]["on"]:         # the session's receipt is on disk first: this cannot lose it
        # The writer binds the session's receipt as written, so it is not written again:
        # what the step did goes to play-receipt-step.json.
        receipt["play_receipt"] = PL.relativized(play_receipt(pl))
        try:
            p["receipt_step"].write_text(json.dumps(receipt["play_receipt"], indent=1,
                                                    default=str) + "\n")
        except OSError as error:
            print(f"play-receipt-step.json not written: {error}", file=sys.stderr)
    inp = receipt["input"]
    end = session.get("end") or {}
    print(f"game rc {session['game_rc']}, player rc {session['player_rc']}; ended by "
          f"{end.get('by', 'nothing recorded')}; stop: "
          f"{(session['stop'] or {}).get('how', 'the game ended first')}")
    if session["limit"]:
        print(f"the disk ended the session: {session['limit']['why']}")
    print(f"input log {inp['path']}: {inp.get('key_lines')} key lines; {inp.get('closed')}")
    print(sound_said(pl["sound"], receipt["player"]))
    print(pace_said(pl["pace"], receipt["player"], session["relay"].get("held"), ahead))
    if pl["receipt"]["on"]:
        print(latency_said(receipt["play_receipt"].get("latency")))
        print(play_said(receipt["play_receipt"]))
    print(f"receipt {rel(p['receipt'])}")
    return 0 if session["game_rc"] == 0 and not session["interrupted"] else 1


if __name__ == "__main__":
    sys.exit(main())
