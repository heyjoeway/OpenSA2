#!/usr/bin/env python3
"""S5's exit, design section 5 as superseded 2026-09-21, as four predicates over generated evidence.

The design's text, quoted:

    Exit: Metal Harbor start to goal ring visible and playable in the SDL
    host and ~~in Unity~~ **in the second host the S5 evaluation selects**;
    M7 = 100 % of used device methods; audio audible on the title, menu,
    garden and Metal Harbor; the same replay renders identically
    frame-for-frame in both hosts (screenshot hash at fixed ticks).

G5's floor criterion fails until each of those four clauses has a criterion of
its own in `Documentation/gate-criteria.json`, its text ending "(design 5, S5
exit)" (`agents-journals/gates/2026-09-29-g5.md`). These are the predicates
those four criteria call, kept as code for the reason `stage_exit.py` gives for
S4's: of the three that read receipts, only audible's has been written; play's
needs a person to play in each host and identical's a second host, so their
passing direction can only be exercised on crafted evidence, in
`Documentation/scripts/tests/test_s5_exit.py`, and a predicate nobody can test
is the shape of defect this project keeps finding.

    play       Metal Harbor start to goal ring visible and playable in both hosts
    m7         M7 = 100 % of used device methods
    audible    audio audible on the title, menu, garden and Metal Harbor
    identical  the same replay renders identically frame-for-frame in both hosts

What every predicate does
-------------------------
* It reads `METRICS.md` or receipts under `Documentation/receipts/` that a named
  tool writes (AGENTS.md rule 7), and three hand-kept files as definitions,
  each shape-pinned by the criteria that read it: the replay registry (Metal
  Harbor's level and the registered names), the host selection record (the
  user's ruling in rule 12's format) and `tools/pins.json` (the DXVK library's
  sha256). A receipt is held to its contract: its `schema`, its
  `generated_by`, and a sha256 for that tool in `inputs.pipeline`, which
  `check_receipt_pins.py` holds to the tool as it stood at the receipt's own
  commit at every push.
* It hashes every evidence file a receipt names and refuses one that does not
  match, so the receipt describes files that are on disk.
* It ties the evidence files to one another by what their producers wrote, not
  by the receipt's word: a player's report to its session by the run id the
  recorder put in the stream header; a session's run record to the player by
  the key records one wrote and the other read; a times file to its report by
  the report's own digest of the frame hashes; a renderer to the file the
  report pins; a WAV to its run by the stream's sha256, and the run to the
  per-tick table it names. "What these do not show" says where that stops.
* It recomputes its verdict from the evidence tables. No S5 receipt carries a
  verdict field, and none would be read. What a producer reports of its own
  work is read as its own: the player's `complete` (and, for the hosts clause,
  `pass`), the WAV sidecar's `ok`, a run record's `ok`.
* It is unmet on a structural zero: no frames, no ticks, no sound, an empty
  denominator.
* Where the evidence is absent it says what is missing: the receipt's path and
  the tool that is to write it.
* It prints one line, which `gate_report.py` quotes beside the verdict.

The hosts
---------
"The SDL host" and "the second host the S5 evaluation selects" are the two
hosts `Documentation/decisions/2026-09-26-S5-host-sdl3-dxvk.md` selects, the
user's ruling recorded in rule 12's five-item format: DXVK Native behind the
recording device's COM objects, windowed through SDL3 (receipt id
`dxvk-native`), and the renderer on SDL3's GPU API over the recorded draw list
(`sdl3-gpu`). The superseding note in design 5 makes the selection part of the
stage and a decision record in rule 12's format, so `selection()` holds the
record to that format and to naming both hosts in its claim (item 1). Which of
the two the design's "SDL host" means does not matter here: the exit needs both.

The note's substance is "two independent hosts", so each receipt's renderer is
held to what that host's player loaded (`renderer_loaded`):

    dxvk-native  the renderer is DXVK's d3d9 library at tools/pins.json's
                 tools.dxvk.library_sha256, the file the report pins
                 (files.libdxvk_d3d9) and the player verified
                 (host_identity.dxvk_file_verified)
    sdl3-gpu     the renderer is the GPU player itself, the file the report pins
                 (files.player); it is not DXVK's library, and the report pins
                 no DXVK library and verified none

Every player report a check reads is final and at the player's full stage
(PLAYER_STAGE: `--stage 3`, every texture real), with each REPORT_ZERO counter
zero: no Present, readback, stream, handling or host failure, no call skipped by
the stage, no placeholder texture bound and no NOTE record. A lower stage draws
geometry with white placeholders: design 5's kill/pivot text calls that
"wireframe playable", the smaller exit S5 is split into only if that criterion
fires, which is the user's pivot to make and not this check's.

The receipt contracts
---------------------
Paths in receipts are repository-relative; an evidence reference is
`{"path": ..., "sha256": ...}`. Integers in tables are decimal or 0x-hex. The
second host's player writes the DXVK player's report fields and times format
(`host/player/player.cpp`).

`Documentation/receipts/s5-play-<host>-v1.json`, one per host, written by
`tools/s5/play_live.py --receipt` through `tools/s5/play_receipt.py` (the
session keeps the same document as its untracked play-record.json):

    {"schema": "sa2-s5-play/v1", "generated_by": "tools/s5/play_live.py",
     "inputs": {"pipeline": {"tools/s5/play_live.py": "<sha256>", ...},
                "observer": <ref: the per-tick observer the session generated>},
     "host": "dxvk-native" | "sdl3-gpu", "stage": "metalharbor",
     "run_id": <the session's run id, which play_live hands the recorder in SA2_REC_META>,
     "renderer": <ref: the host's renderer, DXVK's d3d9 library or the GPU player>,
     "evidence": {"ticks": <ref>, "player_report": <ref>, "run": <ref>,
                  "keys": <ref>, "polls": <ref>, "presents": <ref>, "relay_frames": <ref>,
                  "poll_report": <ref>, "relay_report": <ref>, "session": <ref>},
     "input_latency": {"method": "v1", "clock": "CLOCK_MONOTONIC",
                       "press_at": "SDL key event timestamp",
                       "presented_at": "host Present returned", "summary": {...}}}

  `ticks` is one row per GameLoop return of the session, tab-separated, header
  PLAY_TICKS: CurrentLevel, GameMode, GameState and MainCharObj1[0] as
  `tools/s4/stage_boot.py`'s stage trace reads them (STATE_WORDS gives each
  word's address and width); the goal latch (the byte at 0x0174B002,
  `harness/stage_goal_probe.c`, read by the session's observer at the GameLoop
  return) as the tick left it; `frames`, the Presents the
  game has made by the end of the tick; `keys`, the host keys the runtime's
  DirectInput keyboard applied over the tick's keyboard polls, summed (a poll
  counts toward the GameLoop return after it), so the column totals the run
  record's `keys_written`.
  `player_report` is the host's player report: `complete`, `stage`, `presents`,
  the REPORT_ZERO counters, `window.frames_with_flags`, `host_input`
  (`records_written` and `last_seq` among them), `stream_header` (the
  recorder's header, whose `meta` names the run as `run` and, as PLAY_META
  gives them, the session's `runner` tools/s5/play_live.py, `interactive`
  true and `scene` metalharbor), `files` and `host_identity`.
  `run` is the session's run record, the probe's JSON that `tools/s4/loop_run.py`
  keeps (`harness/crt_probe.c`): `ok` true, and its `dinput.host_input`, the
  runtime's own account of its keyboard (`runtime/sa2_dinput.c`): `source`,
  `script_lines`, `records`, `last_seq`, `seq_gaps`, `partial_dropped`,
  `keys_written`.
  The session's build links no harness/replay/wrap_input_replay.c and no wrap
  of UpdateControllers (sub_77e780), only the rest of tools/s4/stage_run.py's
  test-spawn hooks and wraps, and its game runs with no SA2_INPUT_SCRIPT: no
  recording overwrites the controller the keyboard set. `tools/s5/play_live.py`
  takes both out (`scene_hooks`, `game_env`); this module cannot see it ("What
  these do not show").
  The two hosts' sessions share no run id, run record, per-tick table, player
  report or renderer: each host is played in its own window.
  The input latency is the user's ruling of 2026-10-04 ("Its live-play receipt
  reports input latency, button press to presented frame"). The four logs, on
  one CLOCK_MONOTONIC: `keys`, the player's key sender's log (PLAY_KEYS: each
  key change with its SDL event age, when it was dequeued and when its FIFO
  record was sent); `polls`, the game's keyboard polls that read FIFO records
  (PLAY_POLLS, written by the session's observer); `relay_frames`, when each
  frame came whole at play_live's relay (RELAY_FRAMES); `presents`, when the
  player called and returned from each host Present (PLAY_PRESENTS).
  `poll_report` and `relay_report` are the observer's and the relay's own
  account of their logs (opened, closed, nothing dropped), and `session` is
  play_live's play-live.json for the session (its commands, how it ended).
  `latency_evidence` holds them and `input_latency` joins them: a press is presented
  when frame P+1 is presented, P being the Presents the game had made at the
  poll that read the press's record. A press is in the stage when that poll's
  tick is (from entering the stage to the goal, both included); a press the
  sender never sent (`lost`: its state and the next both kept back by a full
  pipe; `unsent`: still owed when the sender closed) is in the stage when the
  frame on the screen as it was dequeued, the last whose Present had returned,
  was made in such a tick, so the stage's summary counts every press made while
  the stage was shown. `input_latency` in the receipt is that
  join's summary, which the clause recomputes and holds equal to the integer
  microsecond; at least MIN_LATENCY_PRESSES presses polled between entering
  the stage and the goal must be presented, and a session whose keys include
  any synthetic event (`--synthetic-keys`, the player's own queue) is not a
  person playing and is refused. Its stream header's meta names the receipt's
  host (play_live writes it into SA2_REC_META), and the session ended with the
  game's own end or the first SIGTERM's WM_QUIT (CLEAN_STOPS): a game a second
  signal or SIGKILL ended did not leave its loop through its own code.

`Documentation/receipts/s5-audio-scenes-v1.json`, written by
`tools/s5/audio_scenes.py`:

    {"schema": "sa2-s5-audio-scenes/v1", "generated_by": "tools/s5/audio_scenes.py",
     "inputs": {"pipeline": {"tools/s5/audio_scenes.py": "<sha256>", ...}},
     "scenes": {"title" | "menu" | "garden" | "metalharbor":
                {"wav": <ref>, "sidecar": <ref>, "ticks": <ref>, "recording": <ref>}}}

  `wav` and `sidecar` are the player's `--audio-wav` output and its `.json`;
  `ticks` is one row per GameLoop return of the run that made the stream,
  header AUDIO_TICKS, with the virtual time at the tick's end and the state
  words `scene_holds` reads. `recording` is that run's own record, in
  `tools/s5/present_run.py`'s present-run.json shape: `ok` true, `audio` true
  (the stream carries the sound), the stream's sha256 at STREAM_OF_RECORDING,
  which must be the sidecar's `stream_sha256`, and `ticks`, the reference of
  the per-tick table its observer wrote, which must be the scene's. Scenes may
  share files.

`Documentation/receipts/s5-host-compare-v1.json`, written by
`tools/s5/host_compare.py` (not written yet):

    {"schema": "sa2-s5-host-compare/v1", "generated_by": "tools/s5/host_compare.py",
     "inputs": {"pipeline": {"tools/s5/host_compare.py": "<sha256>", ...}},
     "replay": "metalharbor", "stream_sha256": <sha256>,
     "recording": <ref: tools/s5/present_run.py's present-run.json for that replay>,
     "hosts": [{"host": ..., "renderer": <ref>, "report": <ref>, "frames": <ref>}, ...]}

  `replay` is Metal Harbor's registered replay, the stage the play clause
  names, and no other: a replay of the boot alone compares pictures that are
  not the exit's stage. `frames` is the host player's times file
  (`host/player/player.cpp`'s `--frames-out` times, header TIMES_HEADER), one
  row per presented frame with `bgrx_hash`, its read-back picture's hash. The
  report names it (`frames_out.times`, resolved against the repository root
  when relative) and holds it: `frames_out.times_rows` rows, and
  `frames_out.bgrx_digest`, the player's FNV-1a over every row's hash, which
  `frames_digest` recomputes.

What these do not show
----------------------
* A play session's per-tick table is tied to its session by one count, not by a
  digest: its keys column totals the run record's `keys_written`. The table
  carries no run id, so that its state columns are the session's rests on that
  count and on the receipt.
* That a play session's keys were the only input is shown for the runtime's
  DirectInput keyboard alone. harness/replay/wrap_input_replay.c, with
  SA2_INPUT_SCRIPT set, writes ControllersRaw[0] after every controller update,
  after the keyboard has read the FIFO, so a session built with it would show
  the same run record, report and table. No evidence names the hooks a build
  linked; that the wrap was out rests on the receipt's writer,
  tools/s5/play_live.py (`scene_hooks` takes it out, `game_env` drops
  SA2_INPUT_SCRIPT), which the receipt's pipeline pins and the stream header's
  meta names as its runner.
* "Visible" is the player's sampling of its window's flags at each Present; on
  Wayland only keyboard focus and OCCLUDED come from the compositor
  (`host/player/player.cpp`, "The window, measured"). A session minimized at any
  Present, even after the goal, does not count. A play session's pictures are
  not read: what the frames show is the hosts clause's, on Metal Harbor's
  registered replay, in both hosts.
* A play session's input latency starts at SDL's timestamp of the key event (when
  SDL read it from the window system), not at the key's travel, and ends when the
  host's Present call returns, not at scanout: the keyboard's and the display's
  own lag are outside it. It is the player's, the game's and the transport's,
  measured on one host clock. What that timestamp is depends on SDL's video
  backend (SDL 3.4.16, the build tools/pins.json pins): on X11 it is
  SDL_GetTicksNS() when SDL handled the event (X11_GetEventTimestamp keeps no
  server time), so the press is taken up to one event pump after the server
  delivered it and its queue component reads near zero; on Wayland it is the
  compositor's time, adjusted to SDL's clock and clamped to now. No session
  has yet carried a key a window system produced (every verification
  session's keys were --synthetic-keys, whose timestamps are the player's
  own), so the key log's age_ns column of the first played session is where
  the backend's figure is first read.
* M7's numerator is metrics.py's: the methods the recording device answers
  (`tools/s4/device_status.py`), not the calls the recorder writes into the
  stream or a player renders. A player's handling is held only where a call is
  made, by the play and hosts clauses (REPORT_ZERO), so a method no run reaches
  is held by neither.
* That the hosts are independent is shown by what their players loaded and
  pinned, not by reading the renderers.
* Until a receipt exists, its criterion's falsifiers can only mutate evidence
  that does exist (the registry, the selection record, tools/pins.json) and
  this module's own definitions; the test file makes a falsifier on the
  receipt owed by every clause the day it lands, and applies every declared
  falsifier to crafted met evidence meanwhile.

Usage: imported by G5's criteria in `Documentation/gate-criteria.json`;
`.venv/bin/python Documentation/scripts/check_s5_exit.py` prints all four.
"""

from __future__ import annotations

import array
from bisect import bisect_left, bisect_right
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import importlib.util
import json
import math
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    met: bool
    detail: str


class Refused(Exception):
    """Evidence that is absent or not what its contract says; the message says which."""


# The four criteria as Documentation/gate-criteria.json words them, by predicate.
CRITERIA = {
    "play": ("Metal Harbor start to goal ring visible and playable in the SDL host and in "
             "the second host the S5 evaluation selects (design 5, S5 exit)"),
    "m7": "M7 = 100 % of used device methods (design 5, S5 exit)",
    "audible": "audio audible on the title, menu, garden and Metal Harbor (design 5, S5 exit)",
    "identical": ("the same replay renders identically frame-for-frame in both hosts "
                  "(screenshot hash at fixed ticks) (design 5, S5 exit)"),
}

REGISTRY = "Documentation/replays.json"
STAGE = "metalharbor"
SELECTION = "Documentation/decisions/2026-09-26-S5-host-sdl3-dxvk.md"
RULE_12_ITEMS = ("1. **Claim replaced.**", "2. **Evidence.**", "3. **New approach.**",
                 "4. **What remains valid.**", "5. **Smallest falsifying experiment.**")
FALLBACK = "**Fallback route, and what it inherits.**"
# Each host's id in a receipt, and the name the selection record's item 1 gives it.
HOSTS = (("dxvk-native", "DXVK Native"), ("sdl3-gpu", "SDL3's GPU API"))
PINS = "tools/pins.json"
DXVK_PIN = "tools.dxvk.library_sha256"

PLAY_RECEIPT = "Documentation/receipts/s5-play-{host}-v1.json"
PLAY_SCHEMA = "sa2-s5-play/v1"
PLAY_TOOL = "tools/s5/play_live.py"
AUDIO_RECEIPT = "Documentation/receipts/s5-audio-scenes-v1.json"
AUDIO_SCHEMA = "sa2-s5-audio-scenes/v1"
AUDIO_TOOL = "tools/s5/audio_scenes.py"
COMPARE_RECEIPT = "Documentation/receipts/s5-host-compare-v1.json"
COMPARE_SCHEMA = "sa2-s5-host-compare/v1"
COMPARE_TOOL = "tools/s5/host_compare.py"
RECORDING_SCHEMA = "sa2-present-run/v3"
STREAM_OF_RECORDING = "ready.stream_sha256"   # where a run's record keeps its stream's sha256

# The game's state, named as the Mod Loader's headers name it
# (private/predecessor-tools/sa2-mod-loader/SA2ModLoader/include/SA2Enums.h and
# SA2Variables.h; the test holds each value to them).
GAME_MODE_LEVEL = 5          # GameMode_Level
GAME_MODE_ADVERTISE = 14     # GameMode_Advertise: the title screen and the menus
GAME_STATE_INGAME = 16       # GameStates_Ingame
MENU_TITLE = 0               # Menus_TitleScreen
MENU_MAIN = 1                # Menus_Main
LEVEL_CHAO_WORLD = 90        # LevelIDs_ChaoWorld
CHAO_GARDENS = (1, 2, 3)     # ChaoArea_NeutralGarden, ChaoArea_HeroGarden, ChaoArea_DarkGarden
# Where a tool reads each table column, and how many bytes (SA2Variables.h; the
# goal latch is harness/stage_goal_probe.c's byte, the player tools/s4/stage_boot.py's).
STATE_WORDS = {
    "game_mode": ("GameMode", 0x0174B040, 4),
    "current_level": ("CurrentLevel", 0x01934B70, 2),
    "game_state": ("GameState", 0x01934BE0, 2),
    "current_menu": ("CurrentMenu", 0x01D7BB10, 4),
    "chao_area": ("CurrentChaoArea", 0x0134046C, 4),
    "player": ("MainCharObj1", 0x01DEA6C0, 4),
    "goal_latch": (None, 0x0174B002, 1),
}

# Every player report a check reads: final, at the player's full stage, with these zero.
PLAYER_STAGE = 3
REPORT_ZERO = ("present_failures", "readback_failures", "errors", "calls.not_handled",
               "calls.skipped_by_stage", "textures.placeholder_binds",
               "host_failures.count", "stream_problems.count", "notes.count")

# Clause 1: a session in each host.
PLAY_TICKS = ("tick", "current_level", "game_mode", "game_state", "player", "goal_latch",
              "frames", "keys")
AFTER_GOAL_TICKS = 60        # the goal ring is shown: the session runs a second past the goal
MIN_KEY_EVENTS = 1           # the host window's own key events forwarded to the game
WINDOW_UNSEEN = ("hidden", "minimized", "occluded")
INPUT_SOURCE = "fifo"        # the runtime's keyboard read the player's FIFO, not a script
# The stream header's meta of a session play_live ran: its runner, a player taking the
# window's keys, the stage's scene (tools/s5/play_live.py writes all three).
PLAY_META = {"runner": PLAY_TOOL, "interactive": True, "scene": STAGE}
RUN_KEYBOARD = ("script_lines", "records", "last_seq", "seq_gaps", "partial_dropped",
                "keys_written")
# What the two hosts' sessions may not share: each is played in its own window.
SESSION_APART = (("renderer", "renderer"), ("report", "player report"),
                 ("ticks", "per-tick table"), ("run", "run record"), ("run_id", "run id"))

# Clause 3: the host's mixed output, scene by scene.
AUDIO_TICKS = ("tick", "virtual_us", "game_mode", "current_level", "game_state",
               "current_menu", "chao_area")
SCENE_ORDER = ("title", "menu", "garden", "metalharbor")
AUDIBLE_DBFS = -40.0         # RMS over both channels of one whole second
SAMPLE_RATE = 48000          # the mixer's (host/player/mixer.h): one second is this many frames
CHANNELS = 2
SCENE_MIN_TICKS = 180        # a scene is held three seconds, not passed through

# Clause 4: every presented frame, both hosts.
TIMES_HEADER = ("frame", "seq", "virtual_us", "guest_frames", "polls", "wall_us", "bgrx_hash")
COMPARED = ("frame", "seq", "virtual_us", "guest_frames", "polls", "bgrx_hash")
BGRX_HASH = re.compile(r"0x[0-9a-f]{16}\Z")
MIN_DISTINCT_PICTURES = 2
# The player's digest of its frame hashes (host/player/player.cpp, digest_step): FNV-1a 64
# over each hash's eight bytes, low byte first.
FNV_BASIS, FNV_PRIME = 0xCBF29CE484222325, 0x100000001B3

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
M7_FLOOR = re.compile(r"denominator is a floor -- at most (\d+) of (\d+) vtable-shaped call sites "
                      r"could still add to it")
# metrics.py states how many methods a driven run called that the scan did not find, zero too.
M7_MISSED = re.compile(r"the scan missed (\d+) a driven run called")


# ------------------------------------------------------------------ evidence


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(root: Path, rel: str) -> dict:
    path = root / rel
    if not path.is_file():
        raise Refused(f"absent: {rel}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(f"{rel} does not parse: {error}") from None
    if not isinstance(doc, dict):
        raise Refused(f"{rel} is not a JSON object")
    return doc


def receipt(root: Path, rel: str, schema: str, tool: str) -> dict:
    """A receipt held to its contract: its schema, its writer, the writer's digest."""
    if not (root / rel).is_file():
        made = "" if (root / tool).is_file() else ", not written yet"
        raise Refused(f"no receipt {rel} (its writer: {tool}{made})")
    doc = load_json(root, rel)
    if doc.get("schema") != schema:
        raise Refused(f"{rel}: schema {doc.get('schema')!r}, not {schema!r}")
    if doc.get("generated_by") != tool:
        raise Refused(f"{rel}: generated_by {doc.get('generated_by')!r}, not {tool!r}")
    inputs = doc.get("inputs")
    pipeline = inputs.get("pipeline") if isinstance(inputs, dict) else None
    pin = pipeline.get(tool) if isinstance(pipeline, dict) else None
    if isinstance(pin, dict):
        pin = pin.get("sha256")
    if not (isinstance(pin, str) and SHA256.match(pin)):
        raise Refused(f"{rel}: inputs.pipeline holds no sha256 for {tool}")
    return doc


def evidence(root: Path, rel: str, ref: object, what: str) -> Path:
    """The file an evidence reference names, once it hashes to the reference's sha256."""
    if not (isinstance(ref, dict) and isinstance(ref.get("path"), str)
            and isinstance(ref.get("sha256"), str) and SHA256.match(ref["sha256"])):
        raise Refused(f"{rel}: {what} is not a {{path, sha256}} reference")
    named = ref["path"]
    if not named or named.startswith("/") or ".." in Path(named).parts:
        raise Refused(f"{rel}: {what} {named!r} is not a repository-relative path")
    path = root / named
    if not path.is_file():
        raise Refused(f"{rel}: {what} {named} is absent")
    if sha256_file(path) != ref["sha256"]:
        raise Refused(f"{rel}: {what} {named} does not hash to the receipt's sha256")
    return path


def evidence_json(root: Path, rel: str, ref: object, what: str) -> dict:
    path = evidence(root, rel, ref, what)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(f"{rel}: {what} does not parse: {error}") from None
    if not isinstance(doc, dict):
        raise Refused(f"{rel}: {what} is not a JSON object")
    return doc


def table(path: Path, header: tuple[str, ...], what: str) -> list[dict[str, str]]:
    """A tab-separated table with exactly `header`, and at least one row."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as error:
        raise Refused(f"{what} does not read as UTF-8 text: {error}") from None
    if not lines or tuple(lines[0].split("\t")) != header:
        raise Refused(f"{what}: header is not {' '.join(header)}")
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(header):
            raise Refused(f"{what} line {number}: {len(cells)} cells, not {len(header)}")
        rows.append(dict(zip(header, cells)))
    if not rows:
        raise Refused(f"{what} has no rows")
    return rows


def integers(rows: list[dict[str, str]], columns: tuple[str, ...], what: str) -> list[dict[str, int]]:
    out = []
    for row in rows:
        parsed = {}
        for column in columns:
            try:
                parsed[column] = int(row[column], 0)
            except ValueError:
                raise Refused(f"{what}: {column} {row[column]!r} is not an integer") from None
        out.append(parsed)
    return out


def numbered(rows: list[dict], key: str, what: str) -> None:
    if [row[key] for row in rows] != list(range(1, len(rows) + 1)):
        raise Refused(f"{what}: {key} does not run 1, 2, 3, ... with one row each")


def field(doc: object, dotted: str) -> object:
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def count(doc: dict, dotted: str, what: str) -> int:
    value = field(doc, dotted)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Refused(f"{what}: {dotted} is {value!r}, not a count")
    return value


def resolved(root: Path, named: str) -> Path:
    """A path a producer wrote, absolute or relative to the repository root."""
    path = Path(named)
    return (path if path.is_absolute() else root / path).resolve()


def stage_level(root: Path, name: str = STAGE) -> int:
    """The registered stage replay's level (Documentation/replays.json's `stage` block)."""
    replays = load_json(root, REGISTRY).get("replays")
    if not isinstance(replays, list):
        raise Refused(f"{REGISTRY} holds no replays list")
    for entry in replays:
        if isinstance(entry, dict) and entry.get("name") == name:
            stage = entry.get("stage")
            level = stage.get("level") if isinstance(stage, dict) else None
            if (isinstance(level, int) and not isinstance(level, bool)
                    and stage.get("name") == name):
                return level
            raise Refused(f"{REGISTRY}: {name} has no stage block naming its level")
    raise Refused(f"{REGISTRY} registers no replay named {name}")


def registered(root: Path) -> set[str]:
    replays = load_json(root, REGISTRY).get("replays")
    if not isinstance(replays, list):
        raise Refused(f"{REGISTRY} holds no replays list")
    return {entry["name"] for entry in replays
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)}


def selection(root: Path) -> list[str]:
    """The two host ids, once the selection record is in rule 12's format and names both."""
    path = root / SELECTION
    if not path.is_file():
        raise Refused(f"no selection record {SELECTION}")
    text = path.read_text(encoding="utf-8")
    lacking = [heading for heading in RULE_12_ITEMS + (FALLBACK,) if heading not in text]
    if lacking:
        raise Refused(f"{SELECTION} is not in rule 12's format: it lacks {'; '.join(lacking)}")
    claim = " ".join(text.split(RULE_12_ITEMS[0], 1)[1].split(RULE_12_ITEMS[1], 1)[0].split())
    unnamed = [name for _, name in HOSTS if name not in claim]
    if unnamed:
        raise Refused(f"{SELECTION} item 1 does not name {'; '.join(unnamed)}")
    return [host for host, _ in HOSTS]


# ------------------------------------------------------------------ the players


def dxvk_pin(root: Path) -> str:
    """The DXVK library's sha256 as tools/pins.json pins it."""
    pin = field(load_json(root, PINS), DXVK_PIN)
    if not (isinstance(pin, str) and SHA256.match(pin)):
        raise Refused(f"{PINS} holds no sha256 at {DXVK_PIN}")
    return pin


def player_ran_whole(report: dict, what: str) -> None:
    """A final report of a player at its full stage that failed, skipped and left out nothing."""
    if report.get("complete") is not True:
        raise Refused(f"{what} is not final (complete is {report.get('complete')!r})")
    stage = report.get("stage")
    if isinstance(stage, bool) or stage != PLAYER_STAGE:
        raise Refused(f"{what}: stage {stage!r}, not the player's full stage {PLAYER_STAGE}")
    failing = [name for name in REPORT_ZERO if count(report, name, what) != 0]
    if failing:
        raise Refused(f"{what}: {'; '.join(f'{n} {field(report, n)}' for n in failing)}")


def renderer_loaded(root: Path, host: str, renderer: str, report: dict, what: str) -> None:
    """The receipt's renderer is the file the host's player loaded ("The hosts")."""
    pin = dxvk_pin(root)
    dxvk = field(report, "files.libdxvk_d3d9.sha256")
    verified = field(report, "host_identity.dxvk_file_verified")
    if host == "dxvk-native":
        if renderer != pin:
            raise Refused(f"{host}: its renderer is not the DXVK library {PINS} pins")
        if dxvk != pin or verified is not True:
            raise Refused(f"{what}: the player did not load and verify the pinned DXVK library "
                          f"(files.libdxvk_d3d9 {dxvk!r}, verified {verified!r})")
    elif host == "sdl3-gpu":
        if renderer == pin:
            raise Refused(f"{host}: its renderer is DXVK's library, not a renderer of its own")
        if field(report, "files.player.sha256") != renderer:
            raise Refused(f"{what}: the player that ran (files.player) is not the receipt's "
                          f"renderer")
        if dxvk is not None or verified is True:
            raise Refused(f"{what}: the player pins a DXVK library (files.libdxvk_d3d9 "
                          f"{dxvk!r}, verified {verified!r})")
    else:
        raise Refused(f"no definition of the host {host!r}")


def stream_meta(report: dict, what: str) -> dict:
    """The meta the recorder wrote into the stream header, as the player read it, once it
    names a run."""
    try:
        meta = json.loads(json.loads(report["stream_header"])["meta"])
    except (KeyError, TypeError, ValueError):
        meta = None
    run = meta.get("run") if isinstance(meta, dict) else None
    if not isinstance(run, str) or not run:
        raise Refused(f"{what}: its stream header's meta names no run")
    return meta


# ------------------------------------------------------------------ clause 1


def in_stage(row: dict[str, int], level: int) -> bool:
    return (row["current_level"] == level and row["game_mode"] == GAME_MODE_LEVEL
            and row["game_state"] == GAME_STATE_INGAME and row["player"] != 0)


# Clause 1's input latency ("The receipt contracts"): the four logs' headers, the join's
# components and quantiles, the evidence a receipt names for it, and the presses the
# stage must hold presented for the distribution to be one.
PLAY_KEYS = ("change", "scancode", "dik", "down", "synthetic", "age_ns",
             "handled_s", "seq", "sent_s", "status")
PLAY_POLLS = ("poll", "first_seq", "last_seq", "records", "tick", "presents", "mono_ns")
PLAY_PRESENTS = ("frame", "virtual_us", "call_s", "returned_s")
RELAY_FRAMES = ("frame", "arrived_s")
LATENCY_COMPONENTS = ("queue", "send", "poll_wait", "game", "player", "present_call")
LATENCY_QUANTILES = (50, 90, 99)
MIN_LATENCY_PRESSES = 20
LATENCY_EVIDENCE = ("keys", "polls", "presents", "relay_frames", "poll_report", "relay_report")
SESSION_EVIDENCE = "session"         # play_live's play-live.json
LATENCY_SUMMARY = ("method", "clock", "press_at", "presented_at", "summary")
LATENCY_STATUSES = ("presented", "lost", "unsent", "unpresented")
# How play_live's session.stop may say the game ended for a session the join takes: by itself,
# or at its next keyboard poll after the first SIGTERM (tools/s5/play_live.py, stop_game).
# A second SIGTERM meets the runtime's reset handler, the default action, and SIGKILL is
# SIGKILL: neither lets the guest leave its loop through its own code.
CLEAN_STOPS = ("the game had ended", "SIGTERM: WM_QUIT at the game's next keyboard poll")


def input_latency(keys: list[dict], polls: list[dict], presents: list[dict],
                  arrivals: list[dict], ticks: list[dict], entry: int, goal: int) -> dict:
    """Join held event, FIFO, poll, relay and Present rows on one host clock.

    Entry and goal are zero-based indices into ticks, as in session(). The end
    is a host Present's return, not scanout. Input times remain exact decimal
    values until each timestamp is rounded to an integer microsecond. A press
    is in the stage when the tick of the poll that read it is in [entry, goal];
    one never sent (lost, unsent) has no poll, and is in the stage when the
    tick that made the frame on the screen at its dequeue (the last Present
    returned by then: `shown_tick`) is.
    """
    def integer(value, what):
        if type(value) is not int or value < 0:
            raise Refused(f"input latency: {what} must be a nonnegative integer")
        return value

    def seconds(value, what):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise Refused(f"input latency: {what} is not a timestamp") from None
        if not result.is_finite() or result < 0:
            raise Refused(f"input latency: {what} is not a finite nonnegative timestamp")
        return result

    def micros(value):
        return int((value * 1_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))

    if type(entry) is not int or type(goal) is not int or not 0 <= entry < goal < len(ticks):
        raise Refused("input latency: entry/goal are not ordered tick indices")
    previous_frames = 0
    for index, row in enumerate(ticks, 1):
        if integer(row["tick"], "tick index") != index:
            raise Refused("input latency: ticks are not numbered from 1")
        frames = integer(row["frames"], "tick frames")
        integer(row["keys"], "tick keys")
        if frames < previous_frames:
            raise Refused("input latency: tick frame count runs backwards")
        previous_frames = frames

    present_times = {}
    returns = []                      # each frame's Present return, in frame order
    previous_return = Decimal(0)
    for index, row in enumerate(presents, 1):
        if integer(row["frame"], "Present frame") != index:
            raise Refused("input latency: Presents are not numbered from 1")
        called = seconds(row["call_s"], "Present call")
        returned = seconds(row["returned_s"], "Present return")
        if called < previous_return or returned < called:
            raise Refused("input latency: Present timestamps run backwards")
        present_times[index] = (called, returned)
        returns.append(returned)
        previous_return = returned
    made_by = [row["frames"] for row in ticks]    # the Presents made by each tick's end

    arrival_times = {}
    previous_arrival = Decimal(0)
    for index, row in enumerate(arrivals, 1):
        if integer(row["frame"], "relay frame") != index:
            raise Refused("input latency: relay frames are not numbered from 1")
        arrived = seconds(row["arrived_s"], "relay arrival")
        if arrived < previous_arrival:
            raise Refused("input latency: relay arrival timestamps run backwards")
        arrival_times[index] = arrived
        previous_arrival = arrived

    ranges, starts = [], []
    next_seq = 0
    previous_poll = -1
    previous_tick = previous_presents = 0
    previous_poll_time = Decimal(0)
    for row in polls:
        poll = integer(row["poll"], "poll index")
        first = integer(row["first_seq"], "first sequence")
        last = integer(row["last_seq"], "last sequence")
        records = integer(row["records"], "poll records")
        tick = integer(row["tick"], "poll tick")
        made = integer(row["presents"], "poll Present count")
        polled = Decimal(integer(row["mono_ns"], "poll time")) / 1_000_000_000
        if first != next_seq or last < first or records != last - first + 1:
            raise Refused("input latency: poll sequence ranges do not tile whole records from 0")
        if poll <= previous_poll or tick < previous_tick or made < previous_presents:
            raise Refused("input latency: poll indices, ticks or Present counts run backwards")
        if polled < previous_poll_time or not 1 <= tick <= len(ticks):
            raise Refused("input latency: poll time or tick is outside its ordered table")
        before = ticks[tick - 2]["frames"] if tick > 1 else 0
        if not before <= made <= ticks[tick - 1]["frames"]:
            raise Refused("input latency: poll Present count disagrees with its tick")
        starts.append(first)
        ranges.append((last, row, polled))
        next_seq = last + 1
        previous_poll, previous_tick, previous_presents = poll, tick, made
        previous_poll_time = polled

    press_rows = []
    previous_handled = previous_sent = Decimal(0)
    previous_seq = -1
    for row in keys:
        integer(row["change"], "key change")
    ordered_keys = sorted(keys, key=lambda row: row["change"])
    for change, row in enumerate(ordered_keys, 1):
        if row["change"] != change:
            raise Refused("input latency: key changes are not numbered from 1")
        for name in ("scancode", "dik", "down", "synthetic", "age_ns"):
            integer(row[name], f"key {name}")
        if not 1 <= row["dik"] <= 255 or row["down"] not in (0, 1) or row["synthetic"] not in (0, 1):
            raise Refused("input latency: key code, state or synthetic flag is invalid")
        handled = seconds(row["handled_s"], "key dequeue")
        pressed = handled - Decimal(row["age_ns"]) / 1_000_000_000
        if handled < previous_handled or pressed < 0:
            raise Refused("input latency: key dequeue or event time runs backwards")
        previous_handled = handled
        status = row["status"]
        record = {"press": change, "dik": row["dik"], "seq": None, "poll": None,
                  "tick": None, "frame": None, "status": "lost"}
        if status in ("lost", "unsent"):
            if row["seq"] != "-" or row["sent_s"] != "-":
                raise Refused("input latency: an unsent change carries a send timestamp or sequence")
            shown = bisect_right(returns, handled)
            made_in = bisect_left(made_by, shown) if shown else len(made_by)
            record.update(status=status,
                          shown_tick=ticks[made_in]["tick"] if made_in < len(made_by) else None)
        elif status == "sent":
            seq = integer(row["seq"], "sent sequence")
            sent = seconds(row["sent_s"], "key send")
            if (seq < previous_seq or sent < previous_sent or sent < handled
                    or (seq == previous_seq and sent != previous_sent)):
                raise Refused("input latency: sent key sequence or timestamp runs backwards")
            previous_seq, previous_sent = seq, sent
            where = bisect_right(starts, seq) - 1
            if where < 0 or seq > ranges[where][0]:
                raise Refused("input latency: a sent key is in no poll sequence range")
            _, consumed, polled = ranges[where]
            tick, frame = consumed["tick"], consumed["presents"] + 1
            if sent > polled or (row["down"] and ticks[tick - 1]["keys"] == 0):
                raise Refused("input latency: a sent key is polled before send or in a tick with no keys")
            record.update(seq=seq, poll=consumed["poll"], tick=tick, frame=frame,
                          status="unpresented")
            if frame in present_times:
                if frame not in arrival_times:
                    raise Refused("input latency: a presented key has no complete relay frame")
                arrived = arrival_times[frame]
                called, returned = present_times[frame]
                stamps = (pressed, handled, sent, polled, arrived, called, returned)
                if any(b < a for a, b in zip(stamps, stamps[1:])):
                    raise Refused("input latency: the press-to-Present timestamp chain runs backwards")
                us = [micros(value) for value in stamps]
                record.update(status="presented", total_us=us[-1] - us[0],
                              from_dequeue_us=us[-1] - us[1])
                for name, a, b in zip(LATENCY_COMPONENTS, us, us[1:]):
                    record[name + "_us"] = b - a
        else:
            raise Refused("input latency: a key change has no sent/lost/unsent status")
        if row["down"]:
            press_rows.append(record)

    def distribution(values):
        if not values:
            return None
        values = sorted(values)
        result = {"min": values[0], "max": values[-1],
                  "mean": int((Decimal(sum(values)) / len(values)).to_integral_value(
                      rounding=ROUND_HALF_EVEN))}
        for quantile in LATENCY_QUANTILES:
            result[f"p{quantile}"] = values[(quantile * len(values) + 99) // 100 - 1]
        return result

    def summary(rows):
        shown = [row for row in rows if row["status"] == "presented"]
        return {"presses": len(rows),
                "by_status": {name: sum(row["status"] == name for row in rows)
                              for name in LATENCY_STATUSES},
                "total_us": distribution([row["total_us"] for row in shown]),
                "from_dequeue_us": distribution([row["from_dequeue_us"] for row in shown]),
                "components_us": {name: distribution([row[name + "_us"] for row in shown])
                                  for name in LATENCY_COMPONENTS}}

    def staged(row):
        tick = row["tick"] if row["tick"] is not None else row.get("shown_tick")
        return tick is not None and entry <= tick - 1 <= goal

    totals = summary(press_rows)
    totals["stage"] = summary([row for row in press_rows if staged(row)])
    return {"method": "v1", "clock": "CLOCK_MONOTONIC",
            "press_at": "SDL key event timestamp", "presented_at": "host Present returned",
            "presses": press_rows, "summary": totals}


def keyboard(run: dict, report: dict, rows: list[dict[str, int]], what: str) -> None:
    """The runtime read its keys from the player's FIFO and nothing else, every record the
    player wrote, in order; and the per-tick table's keys are the ones it applied."""
    if run.get("ok") is not True:
        raise Refused(f"{what}: the run record's ok is {run.get('ok')!r}")
    source = field(run, "dinput.host_input.source")
    if source != INPUT_SOURCE:
        raise Refused(f"{what}: the runtime's keyboard source is {source!r}, not "
                      f"{INPUT_SOURCE!r}, the player window's")
    read = {name: count(run, f"dinput.host_input.{name}", what) for name in RUN_KEYBOARD}
    if read["script_lines"] or read["seq_gaps"] or read["partial_dropped"]:
        raise Refused(f"{what}: {read['script_lines']} script lines, {read['seq_gaps']} "
                      f"sequence gaps, {read['partial_dropped']} bytes of a record dropped")
    sent, last = field(report, "host_input.records_written"), field(report, "host_input.last_seq")
    if read["records"] < 1 or read["records"] != sent or read["last_seq"] != last:
        raise Refused(f"{what}: the runtime read {read['records']} key records to sequence "
                      f"{read['last_seq']}; the player wrote {sent!r} to sequence {last!r}")
    total = sum(row["keys"] for row in rows)
    if total != read["keys_written"]:
        raise Refused(f"{what}: the per-tick table's keys total {total}; the runtime applied "
                      f"{read['keys_written']}")


def stage_reached(rows: list[dict[str, int]], level: int, host: str) -> tuple[int, int]:
    """What a session's per-tick table must show (clause 1): the stage entered after its first
    tick, the goal latch risen after that with the level kept to it, AFTER_GOAL_TICKS more
    ticks, and a host key applied in between; the zero-based indices (entry, goal) of the
    first tick in the stage and the first with the latch set. tools/s5/play_receipt.py holds
    a session to this before it writes the receipt."""
    entry = next((i for i, row in enumerate(rows) if in_stage(row, level)), None)
    if entry is None:
        raise Refused(f"{host}: the session is never in Metal Harbor (level {level}, "
                      f"GameMode_Level, GameStates_Ingame, a player)")
    if entry == 0:
        raise Refused(f"{host}: the session is in the stage from its first tick, so it does "
                      f"not show the stage's start")
    goal = next((i for i, row in enumerate(rows) if row["goal_latch"] != 0), None)
    if goal is None:
        raise Refused(f"{host}: the goal latch never rises")
    if goal <= entry:
        raise Refused(f"{host}: the goal latch is set at tick {rows[goal]['tick']}, before "
                      f"the stage is entered at tick {rows[entry]['tick']}")
    left = [row["tick"] for row in rows[entry:goal + 1] if row["current_level"] != level]
    if left:
        raise Refused(f"{host}: the session leaves level {level} at tick {left[0]}, before "
                      f"the goal at tick {rows[goal]['tick']}")
    after = len(rows) - 1 - goal
    if after < AFTER_GOAL_TICKS:
        raise Refused(f"{host}: the session ends {after} ticks after the goal, under "
                      f"{AFTER_GOAL_TICKS}")
    if not any(row["keys"] > 0 for row in rows[entry:goal + 1]):
        raise Refused(f"{host}: no host key reached the game between entering the stage and "
                      f"the goal")
    return entry, goal


def session(root: Path, host: str, level: int) -> dict[str, str]:
    """One host's session: what it shows ("shows"), and the identities SESSION_APART names."""
    rel = PLAY_RECEIPT.format(host=host)
    doc = receipt(root, rel, PLAY_SCHEMA, PLAY_TOOL)
    if doc.get("host") != host:
        raise Refused(f"{rel}: host {doc.get('host')!r}, not {host!r}")
    if doc.get("stage") != STAGE:
        raise Refused(f"{rel}: stage {doc.get('stage')!r}, not {STAGE!r}")
    run_id = doc.get("run_id")
    if not (isinstance(run_id, str) and run_id.strip()):
        raise Refused(f"{rel}: run_id {run_id!r} is not a run id")
    evidence(root, rel, doc.get("renderer"), "renderer")
    found = doc.get("evidence") if isinstance(doc.get("evidence"), dict) else {}
    ticks_path = evidence(root, rel, found.get("ticks"), "evidence.ticks")
    report = evidence_json(root, rel, found.get("player_report"), "evidence.player_report")
    run = evidence_json(root, rel, found.get("run"), "evidence.run")
    what = f"{rel} ticks"
    rows = integers(table(ticks_path, PLAY_TICKS, what), PLAY_TICKS, what)
    numbered(rows, "tick", what)
    if any(row[c] < 0 for row in rows for c in ("player", "goal_latch", "frames", "keys")):
        raise Refused(f"{what}: a negative player, latch, frame count or key count")
    if any(b["frames"] < a["frames"] for a, b in zip(rows, rows[1:])):
        raise Refused(f"{what}: frames runs backwards")

    entry, goal = stage_reached(rows, level, host)
    shown = rows[goal + AFTER_GOAL_TICKS]["frames"]

    what = f"{rel} player report"
    player_ran_whole(report, what)
    renderer_loaded(root, host, doc["renderer"]["sha256"], report, what)
    meta = stream_meta(report, what)
    if meta["run"] != run_id:
        raise Refused(f"{what}: its stream is run {meta['run']!r}, not the receipt's {run_id!r}")
    other = [f"{key} {meta.get(key)!r}" for key, value in PLAY_META.items()
             if type(meta.get(key)) is not type(value) or meta.get(key) != value]
    if other:
        raise Refused(f"{what}: its stream header's meta reads {', '.join(other)}, not "
                      f"{PLAY_TOOL}'s interactive {STAGE} session")
    if type(meta.get("host")) is not str or meta["host"] != host:
        raise Refused(f"{what}: its stream header's meta names the host "
                      f"{meta.get('host')!r}, not the receipt's {host!r}")
    presents = count(report, "presents", what)
    if shown < 1 or presents < shown:
        raise Refused(f"{host}: {presents} frames presented; the game made {shown} by "
                      f"{AFTER_GOAL_TICKS} ticks past the goal")
    unseen = [name for name in WINDOW_UNSEEN
              if count(report, f"window.frames_with_flags.{name}", what) != 0]
    if unseen:
        raise Refused(f"{host}: the window was {' or '.join(unseen)} at a Present")
    if field(report, "host_input.started") is not True:
        raise Refused(f"{what}: host_input did not start; the window's keyboard reached nothing")
    keys = count(report, "host_input.key_events", what)
    written = count(report, "host_input.records_written", what)
    if keys < MIN_KEY_EVENTS or written < 1 or count(report, "host_input.write_errors", what):
        raise Refused(f"{host}: {keys} host key events, {written} records written, "
                      f"{field(report, 'host_input.write_errors')} write errors")
    keyboard(run, report, rows, f"{rel} run record")
    synthetic = count(report, "host_input.synthetic_key_events", what)
    if synthetic:
        raise Refused(f"{host}: {synthetic} synthetic key events (--synthetic-keys, the "
                      f"player's own queue): a scripted session is not a person playing")
    latency = play_latency(root, rel, host, found, report, run, rows, entry, goal)
    if not same_json_value(latency, doc.get("input_latency")):
        raise Refused(f"{rel}: input_latency is not the latency its evidence gives")
    shows = (f"{host}: level {level} entered at tick {rows[entry]['tick']}, goal at tick "
             f"{rows[goal]['tick']}, {presents} frames presented of the {shown} made by "
             f"{AFTER_GOAL_TICKS} ticks past it, {keys} host key events; "
             + latency_said(latency["summary"]["stage"]))
    return {"shows": shows, "renderer": doc["renderer"]["sha256"], "run_id": run_id,
            "ticks": found["ticks"]["sha256"], "report": found["player_report"]["sha256"],
            "run": found["run"]["sha256"]}


def play(root: Path | str = ".") -> Result:
    """Clause 1: Metal Harbor start to goal ring visible and playable in both hosts."""
    root = Path(root)
    try:
        hosts = selection(root)
        level = stage_level(root)
    except Refused as why:
        return Result(False, str(why))
    parts, sessions = [], []
    for host in hosts:
        try:
            shows = session(root, host, level)
        except Refused as why:
            parts.append(str(why))
            continue
        parts.append(shows["shows"])
        sessions.append(shows)
    met = len(sessions) == len(hosts)
    for key, name in SESSION_APART:
        if met and len({shows[key] for shows in sessions}) != len(sessions):
            met = False
            parts.append(f"the hosts' sessions share one {name}")
    return Result(met, "; ".join(parts))


# ------------------------------------------------------------------ clause 2


def m7(path: Path | str = "METRICS.md") -> Result:
    """Clause 2: the M7 row's ratio whole over a nonempty denominator, the row stating the
    scan's ceiling exactly once and the methods a driven run called that the scan missed
    exactly once.

    The user ruled on 2026-10-09 (Documentation/decisions/2026-10-09-S5-user-rulings.md,
    item 1): "Drop "ceiling zero" and "missed zero" as G5 pass conditions. Keep the denominator
    as the union. Print the ceiling and the misses on the M7 row". Their reading of design 8's
    "methods called by the binary" is "the methods the scan proves plus what runs observe",
    the union tools/s4/m7_inputs.py counts, not every method the binary could call; the
    could-call space is handled by the closed world the device enforces at run time. So the
    ceiling and the misses are read here only as stated: a row that does not state each once
    is unmet, because the instrument's health is then unread rather than shown. Whether either
    grew is instrument health, held by Documentation/scripts/check_m7_inputs.py against HEAD's
    receipt, not by this clause.

    The numerator is metrics.py's: the receipt's three-way held methods once that block reads
    measured, else the methods the recording device answers (tools/s4/device_status.py), an
    upper bound the row says it is; the criterion's check reads the three-way block beside this.
    """
    path = Path(path)
    if not path.is_file():
        return Result(False, f"{path} is absent")
    match = re.search(r"^\|\s*M7 device\s*\|([^|]*)\|", path.read_text(encoding="utf-8"), re.M)
    if not match:
        return Result(False, "no M7 device row")
    value = match.group(1).strip()
    ratio = re.match(r"(\d+)/(\d+)\b", value)
    if not ratio:
        return Result(False, f"{value or 'empty'} (no implemented/called ratio)")
    implemented, called = int(ratio.group(1)), int(ratio.group(2))
    if called == 0:
        return Result(False, f"{value} (no method is called: the denominator is empty)")
    if implemented != called:
        return Result(False, f"{value} (not every used method is implemented)")
    floors = M7_FLOOR.findall(value)
    if len(floors) != 1:
        return Result(False, f"{value} (the row states the scan's ceiling {len(floors)} times, "
                             f"not once, so the instrument's ceiling is unread)")
    bound, shaped = int(floors[0][0]), int(floors[0][1])
    if shaped == 0 or bound > shaped:
        return Result(False, f"{value} (a ceiling of {bound} over {shaped} vtable-shaped call "
                             f"sites is no scan's reading)")
    missed = M7_MISSED.findall(value)
    if len(missed) != 1:
        return Result(False, f"{value} (the row states what the scan missed {len(missed)} times, "
                             f"not once, so the instrument's misses are unread)")
    return Result(True, value)


# ------------------------------------------------------------------ clause 3


def scene_holds(scene: str, row: dict[str, int], level: int) -> bool:
    if scene == "title":
        return row["game_mode"] == GAME_MODE_ADVERTISE and row["current_menu"] == MENU_TITLE
    if scene == "menu":
        return row["game_mode"] == GAME_MODE_ADVERTISE and row["current_menu"] == MENU_MAIN
    if scene == "garden":
        return row["current_level"] == LEVEL_CHAO_WORLD and row["chao_area"] in CHAO_GARDENS
    if scene == "metalharbor":
        return (row["current_level"] == level and row["game_mode"] == GAME_MODE_LEVEL
                and row["game_state"] == GAME_STATE_INGAME)
    raise Refused(f"no definition of the scene {scene!r}")


def wav_layout(path: Path, what: str) -> tuple[int, int]:
    """(offset of the first sample, frames on disk) of a float32 stereo 48 kHz WAV.

    The player's WAV header clamps the data chunk's size at 4 GiB
    (host/player/mixer.cpp, wav_header), so the frames are counted from the
    file's size, and held to the sidecar's count by the caller.
    """
    with path.open("rb") as handle:
        head = handle.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise Refused(f"{what} is not a RIFF WAVE file")
        layout = None
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                raise Refused(f"{what} has no data chunk")
            name, size = chunk[:4], int.from_bytes(chunk[4:], "little")
            if name == b"fmt ":
                body = handle.read(size)
                if len(body) < 16:
                    raise Refused(f"{what}: fmt chunk of {size} bytes")
                tag, channels, rate = struct.unpack("<HHI", body[:8])
                layout = (tag, channels, rate, struct.unpack("<H", body[14:16])[0])
                handle.seek(size & 1, 1)
            elif name == b"data":
                if layout != (3, CHANNELS, SAMPLE_RATE, 32):
                    raise Refused(f"{what}: format (tag, channels, rate, bits) {layout}, not "
                                  f"float32 stereo at {SAMPLE_RATE} Hz")
                offset = handle.tell()
                break
            else:
                handle.seek(size + (size & 1), 1)
    return offset, (path.stat().st_size - offset) // (4 * CHANNELS)


def second_dbfs(handle, offset: int, start: int) -> float:
    """RMS over both channels of the second starting at frame `start`, in dBFS."""
    handle.seek(offset + start * 4 * CHANNELS)
    samples = array.array("f")
    samples.frombytes(handle.read(SAMPLE_RATE * 4 * CHANNELS))
    if sys.byteorder != "little":
        samples.byteswap()
    energy = math.fsum(x * x for x in samples) / max(len(samples), 1)
    if not math.isfinite(energy):
        return math.nan
    return -math.inf if energy <= 0 else 10.0 * math.log10(energy)


def scene_audible(root: Path, scene: str, ref: object, level: int) -> tuple[bool, str]:
    rel = AUDIO_RECEIPT
    if not isinstance(ref, dict):
        raise Refused(f"{rel}: no evidence for the scene {scene}")
    wav = evidence(root, rel, ref.get("wav"), f"scenes.{scene}.wav")
    side = evidence_json(root, rel, ref.get("sidecar"), f"scenes.{scene}.sidecar")
    ticks_path = evidence(root, rel, ref.get("ticks"), f"scenes.{scene}.ticks")
    recording = evidence_json(root, rel, ref.get("recording"), f"scenes.{scene}.recording")
    what = f"{rel} scenes.{scene}.sidecar"
    want = {"format": "float32", "sample_rate": SAMPLE_RATE, "channels": CHANNELS, "ok": True}
    wrong = [key for key, value in want.items() if side.get(key) != value]
    if wrong:
        raise Refused(f"{what}: {'; '.join(f'{k} {side.get(k)!r}' for k in wrong)}")
    if count(side, "audio_problems", what):
        raise Refused(f"{what}: the mixer counted {side['audio_problems']} audio problems")
    origin, frames = count(side, "origin_us", what), count(side, "frames", what)
    offset, on_disk = wav_layout(wav, f"{rel} scenes.{scene}.wav")
    if on_disk != frames:
        raise Refused(f"{rel} scenes.{scene}.wav holds {on_disk} frames; its sidecar says {frames}")

    what = f"{rel} scenes.{scene}.recording"
    if recording.get("ok") is not True or recording.get("audio") is not True:
        raise Refused(f"{what} is not an ok run that recorded sound (ok "
                      f"{recording.get('ok')!r}, audio {recording.get('audio')!r})")
    made = field(recording, STREAM_OF_RECORDING)
    if not (isinstance(made, str) and SHA256.match(made)) or side.get("stream_sha256") != made:
        raise Refused(f"{rel} scenes.{scene}: the WAV's stream {side.get('stream_sha256')!r} is "
                      f"not the one its run made ({STREAM_OF_RECORDING} {made!r})")
    named = recording.get("ticks")
    if not (isinstance(named, dict) and named.get("path") == ref["ticks"]["path"]
            and named.get("sha256") == ref["ticks"]["sha256"]):
        raise Refused(f"{what} does not name the scene's per-tick table as its own")

    what = f"{rel} scenes.{scene}.ticks"
    rows = integers(table(ticks_path, AUDIO_TICKS, what), AUDIO_TICKS, what)
    numbered(rows, "tick", what)
    if any(b["virtual_us"] <= a["virtual_us"] for a, b in zip(rows, rows[1:])):
        raise Refused(f"{what}: virtual_us does not advance on every tick")
    runs, current = [], []
    for row in rows:
        if scene_holds(scene, row, level):
            current.append(row)
            continue
        if current:
            runs.append(current)
        current = []
    if current:
        runs.append(current)
    held = [run for run in runs if len(run) >= SCENE_MIN_TICKS]
    if not held:
        longest = max((len(run) for run in runs), default=0)
        return False, f"{scene} never held {SCENE_MIN_TICKS} ticks (longest {longest})"

    seconds, loudest = 0, -math.inf
    with wav.open("rb") as handle:
        for run in held:
            first = max(0, -((origin - run[0]["virtual_us"]) * SAMPLE_RATE // 1_000_000))
            end = min(frames, (run[-1]["virtual_us"] - origin) * SAMPLE_RATE // 1_000_000)
            while first + SAMPLE_RATE <= end:
                level_db = second_dbfs(handle, offset, first)
                seconds += 1
                if level_db >= AUDIBLE_DBFS:
                    return True, (f"{scene} {level_db:.1f} dBFS at second {seconds} of the scene, "
                                  f"at least {AUDIBLE_DBFS:.0f}")
                if not math.isnan(level_db):
                    loudest = max(loudest, level_db)
                first += SAMPLE_RATE
    if not seconds:
        return False, f"{scene}: the WAV holds no whole second inside the scene"
    return False, (f"{scene}: loudest of {seconds} whole seconds {loudest:.1f} dBFS, under "
                   f"{AUDIBLE_DBFS:.0f}")


def audible(root: Path | str = ".") -> Result:
    """Clause 3: audio audible on the title, the main menu, a Chao Garden and Metal Harbor."""
    root = Path(root)
    try:
        level = stage_level(root)
        doc = receipt(root, AUDIO_RECEIPT, AUDIO_SCHEMA, AUDIO_TOOL)
    except Refused as why:
        return Result(False, str(why))
    scenes = doc.get("scenes") if isinstance(doc.get("scenes"), dict) else {}
    parts, met = [], True
    for scene in SCENE_ORDER:
        try:
            heard, text = scene_audible(root, scene, scenes.get(scene), level)
        except Refused as why:
            heard, text = False, str(why)
        met = met and heard
        parts.append(text)
    return Result(met, "; ".join(parts))


# ------------------------------------------------------------------ clause 4


def frames_digest(rows: list[dict]) -> str:
    """The player's bgrx_digest of these frames: FNV-1a 64 over each hash's bytes, low first."""
    digest = FNV_BASIS
    for row in rows:
        frame = int(row["bgrx_hash"], 16)
        for shift in range(0, 64, 8):
            digest = ((digest ^ ((frame >> shift) & 0xFF)) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return f"0x{digest:016x}"


def rendered(root: Path, entry: object, stream: str) -> tuple[str, str, list[dict]]:
    """One host's rendering of the stream: (host id, renderer sha256, its frame rows)."""
    rel = COMPARE_RECEIPT
    if not isinstance(entry, dict) or not isinstance(entry.get("host"), str):
        raise Refused(f"{rel}: a hosts entry names no host")
    host = entry["host"]
    renderer = evidence(root, rel, entry.get("renderer"), f"{host} renderer")
    report = evidence_json(root, rel, entry.get("report"), f"{host} report")
    frames_path = evidence(root, rel, entry.get("frames"), f"{host} frames")
    what = f"{host} report"
    player_ran_whole(report, what)
    if report.get("pass") is not True:
        raise Refused(f"{what}: pass {report.get('pass')!r}")
    if report.get("stream_sha256") != stream or report.get("stream_records_read_to_end") is not True:
        raise Refused(f"{what}: did not read the compared stream to its end")
    renderer_sha = sha256_file(renderer)
    renderer_loaded(root, host, renderer_sha, report, what)
    what = f"{host} frames"
    rows = table(frames_path, TIMES_HEADER, what)
    numeric = tuple(column for column in TIMES_HEADER if column != "bgrx_hash")
    parsed = integers(rows, numeric, what)
    numbered(parsed, "frame", what)
    for row, values in zip(rows, parsed):
        if not BGRX_HASH.match(row["bgrx_hash"]):
            raise Refused(f"{what}: frame {row['frame']} hash {row['bgrx_hash']!r} is not 0x and "
                          f"sixteen hex digits")
        values["bgrx_hash"] = row["bgrx_hash"]
    presents = count(report, "presents", f"{host} report")
    if not (presents == count(report, "frame_records", f"{host} report") == len(parsed)
            == field(report, "frames_out.frames")):
        raise Refused(f"{host}: {presents} presents, {field(report, 'frame_records')} frame "
                      f"records, {field(report, 'frames_out.frames')} frames out, "
                      f"{len(parsed)} rows: not every frame of the stream")
    times = field(report, "frames_out.times")
    if not isinstance(times, str) or resolved(root, times) != resolved(root, entry["frames"]["path"]):
        raise Refused(f"{host}: the report's times file is not the receipt's frames table")
    digest = frames_digest(parsed)
    if (field(report, "frames_out.times_rows") != len(parsed)
            or field(report, "frames_out.bgrx_digest") != digest):
        raise Refused(f"{host}: the report's {field(report, 'frames_out.times_rows')!r} rows "
                      f"digesting to {field(report, 'frames_out.bgrx_digest')!r} are not the "
                      f"frames table's {len(parsed)} digesting to {digest}")
    return host, renderer_sha, parsed


def identical(root: Path | str = ".") -> Result:
    """Clause 4: Metal Harbor's replay stream, every frame's read-back hash equal in both hosts."""
    root = Path(root)
    rel = COMPARE_RECEIPT
    try:
        hosts = selection(root)
        doc = receipt(root, rel, COMPARE_SCHEMA, COMPARE_TOOL)
        replay, stream = doc.get("replay"), doc.get("stream_sha256")
        if replay != STAGE:
            raise Refused(f"{rel}: replay {replay!r}, not {STAGE!r}, the stage the play clause "
                          f"names")
        if replay not in registered(root):
            raise Refused(f"{rel}: replay {replay!r} is not registered in {REGISTRY}")
        if not (isinstance(stream, str) and SHA256.match(stream)):
            raise Refused(f"{rel}: stream_sha256 is not a sha256")
        recording = evidence_json(root, rel, doc.get("recording"), "recording")
        if (recording.get("schema") != RECORDING_SCHEMA or recording.get("replay") != replay
                or recording.get("ok") is not True
                or field(recording, STREAM_OF_RECORDING) != stream):
            raise Refused(f"{rel}: the recording is not an ok {RECORDING_SCHEMA} run of "
                          f"{replay} that made the compared stream")
        entries = doc.get("hosts")
        if not isinstance(entries, list):
            raise Refused(f"{rel}: hosts is not a list")
        done = [rendered(root, entry, stream) for entry in entries]
        if sorted(host for host, _, _ in done) != sorted(hosts):
            raise Refused(f"{rel}: hosts {[host for host, _, _ in done]}, not one each of {hosts}")
        if len({renderer for _, renderer, _ in done}) != len(done):
            raise Refused(f"{rel}: the hosts name one renderer")
    except Refused as why:
        return Result(False, str(why))
    tables = {host: rows for host, _, rows in done}
    first, second = (tables[host] for host in hosts)
    if len(first) != len(second):
        return Result(False, f"{replay}: {hosts[0]} presented {len(first)} frames, "
                             f"{hosts[1]} {len(second)}")
    for a, b in zip(first, second):
        for column in COMPARED:
            if a[column] != b[column]:
                return Result(False, f"{replay} frame {a['frame']} (virtual_us {a['virtual_us']}): "
                                     f"{column} {hosts[0]} {a[column]}, {hosts[1]} {b[column]}")
    distinct = len({row["bgrx_hash"] for row in first})
    if distinct < MIN_DISTINCT_PICTURES:
        return Result(False, f"{replay}: {len(first)} frames equal, but {distinct} distinct "
                             f"picture(s): a comparison of nothing")
    return Result(True, f"{replay}: {len(first)} frames, each equal in {hosts[0]} and {hosts[1]} "
                        f"in {', '.join(COMPARED)}; {distinct} distinct pictures")


PREDICATES = {"play": play, "m7": m7, "audible": audible, "identical": identical}
# The receipts each criterion's check reads; once one exists, every clause of its
# criterion owes a falsifier naming it, and the criterion a pinned shape for it
# (Documentation/scripts/tests/test_s5_exit.py).
RECEIPTS = {
    "play": tuple(PLAY_RECEIPT.format(host=host) for host, _ in HOSTS),
    "m7": (),
    "audible": (AUDIO_RECEIPT,),
    "identical": (COMPARE_RECEIPT,),
}


def main() -> int:
    unmet = 0
    for name, predicate in PREDICATES.items():
        result = predicate()
        unmet += not result.met
        print(f"{name}: {'met' if result.met else 'not met'}: {result.detail}")
    return 1 if unmet else 0


# The caller holds physical identities and report/run/session/tick bytes through publication.
# This boundary consumes exactly the six supplied raw/sidecar byte strings.
@dataclass(frozen=True)
class HeldLatencyFile:
    path: str
    sha256: str
    data: bytes


def same_json_value(expected: object, stored: object) -> bool:
    """Exact JSON types, dictionary keys and ordered list contents, recursively."""
    if type(expected) is not type(stored):
        return False
    if type(expected) is dict:
        return (all(type(k) is str for k in expected) and set(expected) == set(stored)
                and all(same_json_value(expected[k], stored[k]) for k in expected))
    if type(expected) is list:
        return (len(expected) == len(stored)
                and all(same_json_value(a, b) for a, b in zip(expected, stored)))
    if type(expected) not in (str, int, float, bool, type(None)):
        return False
    return expected == stored and (type(expected) is not float or math.isfinite(expected))


def _latency_at(doc: dict, dotted: str, what: str) -> object:
    node = doc
    for part in dotted.split('.'):
        if type(node) is not dict or part not in node:
            raise Refused(f"{what}: missing {dotted}")
        node = node[part]
    return node


def _latency_expect(doc: dict, dotted: str, expected: object, what: str) -> None:
    value = _latency_at(doc, dotted, what)
    if not same_json_value(expected, value):
        raise Refused(f"{what}: {dotted} is {value!r}, expected {expected!r}")


def _latency_count(doc: dict, dotted: str, what: str) -> int:
    value = _latency_at(doc, dotted, what)
    if type(value) is not int or not 0 <= value <= 0xffffffffffffffff:
        raise Refused(f"{what}: {dotted} is not an integer count")
    return value


def _latency_bytes(root: Path, held: HeldLatencyFile, what: str) -> bytes:
    if type(held) is not HeldLatencyFile:
        raise Refused(f"{what}: expected HeldLatencyFile")
    if (type(held.path) is not str or not held.path or Path(held.path).is_absolute()
            or '..' in Path(held.path).parts or type(held.sha256) is not str
            or SHA256.fullmatch(held.sha256) is None or type(held.data) is not bytes):
        raise Refused(f"{what}: invalid held file reference")
    if hashlib.sha256(held.data).hexdigest() != held.sha256:
        raise Refused(f"{what}: held bytes do not match their sha256")
    return held.data


def _latency_table(data: bytes, header: tuple[str, ...], what: str) -> list[dict]:
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise Refused(f"{what}: invalid UTF-8") from None
    if not text.endswith('\n') or '\r' in text:
        raise Refused(f"{what}: incomplete or noncanonical TSV lines")
    lines = text[:-1].split('\n')
    if tuple(lines[0].split('\t')) != header:
        raise Refused(f"{what}: wrong header")
    rows = []
    for line in lines[1:]:
        cells = line.split('\t')
        if len(cells) != len(header):
            raise Refused(f"{what}: wrong row width")
        rows.append(dict(zip(header, cells)))
    if not rows:
        raise Refused(f"{what}: no rows")
    return rows


def _latency_json(data: bytes, what: str) -> dict:
    def object_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate JSON key')
            out[key] = value
        return out
    def constant(value):
        raise ValueError(f'nonfinite JSON value {value}')
    try:
        doc = json.loads(data.decode('utf-8'), object_pairs_hook=object_pairs,
                         parse_constant=constant)
    except (UnicodeError, ValueError):
        raise Refused(f"{what}: invalid JSON object") from None
    if type(doc) is not dict:
        raise Refused(f"{what}: not a JSON object")
    return doc


def _latency_integer(raw: str, what: str, maximum: int = 0xffffffffffffffff) -> int:
    if re.fullmatch(r'(?:0|[1-9][0-9]*)', raw) is None:
        raise Refused(f"{what}: not a canonical decimal integer")
    value = int(raw)
    if value > maximum:
        raise Refused(f"{what}: integer exceeds producer width")
    return value


def _latency_seconds(raw: str, what: str) -> str:
    if re.fullmatch(r'(?:0|[1-9][0-9]*)\.[0-9]{9}', raw) is None:
        raise Refused(f"{what}: not nine-decimal monotonic seconds")
    return raw


def latency_tables(data: dict[str, bytes], what: str,
                   synthetic: bool = False) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """The four raw logs' bytes parsed to typed rows: (keys, polls, presents, arrivals). A key
    row marked synthetic is refused unless `synthetic` (a diagnostic join of a scripted
    session, which no receipt holds)."""
    keys = _latency_table(data['keys'], PLAY_KEYS, f'{what} keys')
    polls = _latency_table(data['polls'], PLAY_POLLS, f'{what} polls')
    presents = _latency_table(data['presents'], PLAY_PRESENTS, f'{what} presents')
    arrivals = _latency_table(data['relay_frames'], RELAY_FRAMES, f'{what} relay frames')
    for row in keys:
        for name in ('change', 'scancode', 'dik', 'down', 'synthetic', 'age_ns'):
            row[name] = _latency_integer(row[name], f'{what} key {name}')
        if row['synthetic'] != 0 and not synthetic:
            raise Refused(f"{what}: synthetic raw key row")
        row['handled_s'] = _latency_seconds(row['handled_s'], f'{what} key handled_s')
        if row['status'] == 'sent':
            row['seq'] = _latency_integer(row['seq'], f'{what} key seq', 0xffffffff)
            row['sent_s'] = _latency_seconds(row['sent_s'], f'{what} key sent_s')
        elif row['status'] not in ('lost', 'unsent') or row['seq'] != '-' or row['sent_s'] != '-':
            raise Refused(f"{what}: invalid key status/unsent fields")
    for row in polls:
        for name in PLAY_POLLS:
            row[name] = _latency_integer(row[name], f'{what} poll {name}',
                                        0xffffffffffffffff if name == 'mono_ns' else 0xffffffff)
    for row in presents:
        for name in ('frame', 'virtual_us'):
            row[name] = _latency_integer(row[name], f'{what} Present {name}')
        for name in ('call_s', 'returned_s'):
            row[name] = _latency_seconds(row[name], f'{what} Present {name}')
    for row in arrivals:
        row['frame'] = _latency_integer(row['frame'], f'{what} arrival frame')
        row['arrived_s'] = _latency_seconds(row['arrived_s'], f'{what} arrival time')
    return keys, polls, presents, arrivals


def latency_evidence(root: Path, what: str, host: str,
                     files: dict[str, HeldLatencyFile], report: dict, run: dict,
                     live_session: dict, ticks: list[dict], entry: int, goal: int) -> dict:
    """Validate closed producer coverage, then run the unchanged Decimal join.

    No named file is opened. The caller owns current physical identities, exact
    report/run/session/tick byte custody, host-aware build/renderer/source graph,
    stage checks and publication. Supplied byte hashes alone prove none of those.
    """
    if host not in {name for name, _ in HOSTS}:
        raise Refused(f"{what}: unknown host")
    if type(files) is not dict or any(type(d) is not dict for d in (report, run, live_session)):
        raise Refused(f"{what}: invalid boundary documents")
    names = ('keys', 'polls', 'presents', 'relay_frames', 'poll_report', 'relay_report')
    if any(name not in files for name in names):
        raise Refused(f"{what}: missing latency file")
    data = {name: _latency_bytes(root, files[name], f'{what} {name}') for name in names}
    keys, polls, presents, arrivals = latency_tables(data, what)
    poll_report = _latency_json(data['poll_report'], f'{what} poll report')
    relay_report = _latency_json(data['relay_report'], f'{what} relay report')

    def expect(doc, key, value):
        _latency_expect(doc, key, value, what)
    def total(doc, key):
        return _latency_count(doc, key, what)
    def path_matches(doc, key, name):
        value = _latency_at(doc, key, what)
        if type(value) is not str or not value or resolved(root, value) != resolved(root, files[name].path):
            raise Refused(f'{what}: {key} does not name {name}')

    for key, value in {'method': 'v1', 'clock': 'CLOCK_MONOTONIC',
                       'event_age_clock': 'SDL_GetTicksNS', 'event_age_unit': 'ns',
                       'handled_at': 'after successful SDL_PollEvent',
                       'presented_at': 'host Present returned'}.items():
        expect(report, 'latency_diagnostics.' + key, value)
    for key in ('requested_keys', 'requested_presents', 'sender_start_attempted', 'sender_started',
                'key_log_header_accepted', 'present_log_header_accepted', 'key_log_opened',
                'key_log_closed', 'closed', 'evidence_ready'):
        expect(report, 'latency_diagnostics.' + key, True)
    expect(report, 'latency_diagnostics.requested_synthetic', False)
    expect(report, 'latency_diagnostics.error', None)
    for key in ('key_log_owner_io_errors', 'future_key_timestamps', 'event_clock_errors',
                'invalid_key_timestamps', 'present_clock_errors', 'report_io_errors'):
        expect(report, 'latency_diagnostics.' + key, 0)
    if 'synthetic_keys' in report:
        raise Refused(f'{what}: synthetic provenance is present')
    expect(report, 'host_input.synthetic_key_events', 0)
    for key, value in {'started': True, 'open': False, 'held_now': 0, 'owed': False,
                       'write_errors': 0, 'last_error': None}.items():
        expect(report, 'host_input.' + key, value)
    if total(report, 'host_input.key_events') < MIN_KEY_EVENTS:
        raise Refused(f'{what}: no host key events')
    path_matches(report, 'host_input.key_log.path', 'keys')
    expect(report, 'host_input.key_log.rows', len(keys))
    for status in ('sent', 'lost', 'unsent'):
        expect(report, 'host_input.key_log.' + status, sum(row['status'] == status for row in keys))
    expect(report, 'host_input.key_log.io_errors', 0)
    expect(report, 'host_input.key_log.error', None)
    path_matches(report, 'present_log.path', 'presents')
    for key, value in {'rows': len(presents), 'opened': True, 'closed': True,
                       'io_errors': 0, 'dropped_rows': 0, 'error': None}.items():
        expect(report, 'present_log.' + key, value)

    for doc, schema, name in ((poll_report, 'sa2-s5-play-polls/v1', 'polls'),
                               (relay_report, 'sa2-s5-relay-arrivals/v1', 'relay_frames')):
        for key, value in {'schema': schema, 'method': 'v1', 'clock': 'CLOCK_MONOTONIC', 'unit': 'ns',
                           'requested': True, 'opened': True, 'finalized': True, 'closed': True,
                           'ok': True, 'clock_errors': 0, 'io_errors': 0, 'error': None}.items():
            expect(doc, key, value)
        path_matches(doc, 'path', name)
    for key, value in {'dropped_rows': 0, 'callbacks': len(polls), 'rows': len(polls),
                       'records_observed': sum(r['records'] for r in polls),
                       'records_logged': sum(r['records'] for r in polls),
                       'first_seq': polls[0]['first_seq'], 'last_seq': polls[-1]['last_seq'],
                       'last_poll': polls[-1]['poll']}.items():
        expect(poll_report, key, value)
    expect(run, 'ok', True)
    expect(run, 'dinput.host_input.source', INPUT_SOURCE)
    for key in ('script_lines', 'seq_gaps', 'partial_dropped'):
        expect(run, 'dinput.host_input.' + key, 0)
    records = total(run, 'dinput.host_input.records')
    if not records or records != total(report, 'host_input.records_written'):
        raise Refused(f'{what}: runtime/sender FIFO counts disagree')
    if (sum(r['records'] for r in polls) != records or polls[-1]['last_seq'] != records - 1
            or total(run, 'dinput.host_input.last_seq') != records - 1
            or total(report, 'host_input.last_seq') != records - 1):
        raise Refused(f'{what}: incomplete FIFO range coverage, including rowless tail')
    final_polls = total(run, 'dinput.host_input.polls')
    if any(r['poll'] >= final_polls for r in polls):
        raise Refused(f'{what}: poll callback exceeds runtime polls')
    if type(ticks) is not list or not ticks or any(type(r) is not dict for r in ticks):
        raise Refused(f'{what}: invalid ticks')
    for row in ticks:
        for key in ('tick', 'frames', 'keys'):
            _latency_count(row, key, what)
    if sum(r['keys'] for r in ticks) != total(run, 'dinput.host_input.keys_written'):
        raise Refused(f'{what}: tick key count differs from runtime')

    for key, value in {'dropped_frames': 0, 'rows': len(arrivals), 'complete_frames': len(arrivals),
                       'framing_problem': None, 'feed_error': None, 'feed_ended': True,
                       'transport_end': 'the writer closed the pipe',
                       'clock_info.monotonic': True, 'clock_info.adjustable': False}.items():
        expect(relay_report, key, value)
    implementation = _latency_at(relay_report, 'clock_info.implementation', what)
    if type(implementation) is not str or not implementation:
        raise Refused(f'{what}: clock implementation is absent')
    cleanup = _latency_at(relay_report, 'session_cleanup_errors', what)
    if cleanup is not None and not same_json_value([], cleanup):
        raise Refused(f'{what}: session cleanup errors')
    for key, value in {'complete': True, 'pass': True, 'exit_status': 0, 'stage': PLAYER_STAGE,
                       'stream_records_read_to_end': True, 'stream_sha256_after_stop': False,
                       'records.end': 1, 'records.unknown': 0, 'records.seq_gaps': 0,
                       'records.after_end': 0, 'presents_without_frame_record': 0,
                       'options.max_frames': 0, 'options.allow_truncated': False,
                       'options.stats_only': False, 'options.hidden': False}.items():
        expect(report, key, value)
    for key in REPORT_ZERO:
        expect(report, key, 0)
    for key in WINDOW_UNSEEN:
        expect(report, 'window.frames_with_flags.' + key, 0)
    frames = len(arrivals)
    if not frames or len(presents) != frames or any(r['frames'] > frames for r in ticks):
        raise Refused(f'{what}: incomplete frame table coverage')
    for key in ('presents', 'frame_records', 'records.frame'):
        expect(report, key, frames)
    expect(run, 'd3d9.frames.present', frames)
    for key, value in {'schema': 'sa2-s5-play-live/v1', 'scene': STAGE,
                       'session.game_rc': 0, 'session.player_rc': 0,
                       'session.interrupted': False, 'session.player_cancelled': False,
                       'session.player_error': None, 'session.limit': None,
                       'session.relay.framing_problem': None, 'session.relay.feed_error': None,
                       'session.relay.end': 'the writer closed the pipe',
                       'session.relay.frames_arrived': frames}.items():
        expect(live_session, key, value)
    stop = _latency_at(live_session, 'session.stop', what)
    if stop is not None:
        if type(stop) is not dict or stop.get('how') not in CLEAN_STOPS:
            raise Refused(f'{what}: forced/unknown game stop')
    for key in ('stream_cap_bytes', 'disk_floor_bytes'):
        total(live_session, 'limits.' + key)
    stream_bytes = total(relay_report, 'bytes_read')
    if not stream_bytes:
        raise Refused(f'{what}: no stream bytes')
    for key in ('stream_file_bytes_read', 'stream_bytes'):
        expect(report, key, stream_bytes)
    expect(live_session, 'session.relay.bytes', stream_bytes)
    kinds = ('call', 'd3dx', 'frame', 'note', 'end') + (('audio',) if 'audio' in report['records'] else ())
    headers = sum(total(report, 'records.' + key) for key in kinds)
    expect(relay_report, 'record_headers_seen', headers)
    expect(live_session, 'session.relay.records', headers)
    meta = stream_meta(report, what)
    for key, value in PLAY_META.items():
        expect(meta, key, value)
    expect(meta, 'host', host)
    for key in ('runner_sha256', 'latency_transport_source.sha256', 'ticks_observer.sha256'):
        digest = _latency_at(live_session, key, what)
        if type(digest) is not str or SHA256.fullmatch(digest) is None:
            raise Refused(f'{what}: invalid {key}')
    for key in ('build', 'latency_transport_source.path', 'ticks_observer.path'):
        value = _latency_at(live_session, key, what)
        if type(value) is not str or not value:
            raise Refused(f'{what}: missing {key}')
    profile = _latency_at(live_session, 'profile', what)
    if profile != 'default' and (type(profile) is not dict or type(profile.get('path')) is not str
                               or type(profile.get('sha256')) is not str
                               or SHA256.fullmatch(profile['sha256']) is None):
        raise Refused(f'{what}: invalid session profile')
    for key in ('commands.game', 'commands.player'):
        command = _latency_at(live_session, key, what)
        if type(command) is not list or not command or any(type(v) is not str or not v for v in command):
            raise Refused(f'{what}: invalid {key}')
    env = _latency_at(live_session, 'commands.player_env', what)
    if type(env) is not str:
        raise Refused(f'{what}: invalid player environment')
    for key in ('status', 'iterations', 'quit_by_host', 'quit_by_guest'):
        value = total(run, 'pump.' + key)
        expect(live_session, 'run_record.pump.' + key, value)
    expect(live_session, 'run_record.ok', True)
    result = input_latency(keys, polls, presents, arrivals, ticks, entry, goal)
    shown = result['summary']['stage']['by_status']['presented']
    if shown < MIN_LATENCY_PRESSES:
        raise Refused(f'{what}: {shown} presses presented between entering the stage and the '
                      f'goal, under {MIN_LATENCY_PRESSES}')
    return result


def latency_summary(result: dict) -> dict:
    """What a receipt holds of the join: everything but the per-press rows, which the
    evidence keeps and the clause recomputes."""
    return {key: result[key] for key in LATENCY_SUMMARY}


def play_latency(root: Path, rel: str, host: str, found: dict, report: dict, run: dict,
                 rows: list[dict[str, int]], entry: int, goal: int) -> dict:
    """The session's input latency from the evidence its receipt names, each file held to
    its sha256 and read once; its summary as a receipt holds it."""
    files = {}
    for name in LATENCY_EVIDENCE:
        path = evidence(root, rel, found.get(name), f"evidence.{name}")
        files[name] = HeldLatencyFile(found[name]["path"], found[name]["sha256"],
                                      path.read_bytes())
    live = evidence_json(root, rel, found.get(SESSION_EVIDENCE), f"evidence.{SESSION_EVIDENCE}")
    return latency_summary(latency_evidence(root, f"{rel} input latency", host, files, report,
                                            run, live, rows, entry, goal))


def latency_said(stage: dict) -> str:
    """The stage's latency, as the clause's line gives it."""
    total = stage["total_us"]
    return (f"input latency, press to presented frame, over {stage['by_status']['presented']} "
            f"presses in the stage: median {total['p50'] / 1000:.1f} ms, p90 "
            f"{total['p90'] / 1000:.1f} ms, max {total['max'] / 1000:.1f} ms")

# The outer graph owns physical kinds, aliases, descriptors and publication rechecks.
@dataclass(frozen=True)
class BuildFileIdentity:
    path: str
    canonical_path: str
    sha256: str
    size: int
    mode: int
    device: int
    inode: int
    checkout_links: tuple[str, ...]


@dataclass(frozen=True)
class HeldBuildText:
    identity: BuildFileIdentity
    data: bytes


@dataclass(frozen=True)
class SourcePin:
    path: str
    identity: BuildFileIdentity


@dataclass(frozen=True)
class PlayerBuildFiles:
    record: HeldBuildText
    report: HeldBuildText
    expectations: HeldBuildText
    depfiles: tuple[HeldBuildText, ...]
    inputs: tuple[BuildFileIdentity, ...]


@dataclass(frozen=True)
class PlayerBuildBinding:
    host: str
    binary: str
    record_sha256: str
    binary_sha256: str
    input_paths: tuple[str, ...]


_PLAYER_BUILD_PROVIDER = None


def _player_build_providers():
    """Load the actual comparer constants and its build_record parser, once by path."""
    global _PLAYER_BUILD_PROVIDER
    if _PLAYER_BUILD_PROVIDER is None:
        repo = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
        path = repo / 'tools/s5/compare_hosts.py'
        spec = importlib.util.spec_from_file_location('_sa2_player_build_compare_hosts', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if (Path(module.__file__).resolve() != path or module.ROOT != repo
                or Path(module.build_record.__file__).resolve() != repo / 'host/player/build_record.py'
                or sys.modules.get('play_report') is not module.play_report
                or sys.modules.get('stream_dump') is not module.stream_dump
                or Path(module.play_report.__file__).resolve() != repo / 'tools/s5/play_report.py'
                or Path(module.stream_dump.__file__).resolve() != repo / 'tools/s5/stream_dump.py'):
            raise Refused('player build: wrong constants/parser provider')
        _PLAYER_BUILD_PROVIDER = module
    return _PLAYER_BUILD_PROVIDER, _PLAYER_BUILD_PROVIDER.build_record


def _build_path(value: str, what: str, *, absolute: bool) -> Path:
    if (type(value) is not str or not value or '\x00' in value
            or value.startswith('//') or Path(value).is_absolute() != absolute
            or str(Path(value)) != value or '..' in Path(value).parts or value == '.'):
        raise Refused(f'player build: {what} is not a canonical path')
    return Path(value)


def _build_identity(identity: BuildFileIdentity) -> BuildFileIdentity:
    if type(identity) is not BuildFileIdentity:
        raise Refused('player build: expected BuildFileIdentity')
    _build_path(identity.path, 'input path', absolute=True)
    _build_path(identity.canonical_path, 'canonical input path', absolute=True)
    if type(identity.sha256) is not str or SHA256.fullmatch(identity.sha256) is None:
        raise Refused('player build: invalid input digest')
    for name in ('size', 'mode', 'device', 'inode'):
        n = getattr(identity, name)
        if type(n) is not int or not 0 <= n <= 0xffffffffffffffff:
            raise Refused(f'player build: invalid identity {name}')
    if identity.mode > 0o7777:
        raise Refused('player build: identity mode is not permission bits')
    if type(identity.checkout_links) is not tuple or any(type(p) is not str for p in identity.checkout_links):
        raise Refused('player build: invalid checkout link observation')
    if identity.checkout_links:
        raise Refused('player build: input traverses a checkout symbolic link')
    return identity


def _build_text(held: HeldBuildText, what: str) -> bytes:
    if type(held) is not HeldBuildText or type(held.data) is not bytes:
        raise Refused(f'player build: {what} is not held bytes')
    identity = _build_identity(held.identity)
    if len(held.data) != identity.size or hashlib.sha256(held.data).hexdigest() != identity.sha256:
        raise Refused(f'player build: {what} bytes disagree with identity')
    return held.data


def player_build_evidence(root: str, build: str, host: str,
                          before: tuple[SourcePin, ...], files: PlayerBuildFiles) -> PlayerBuildBinding:
    """Match one host's held build graph and report; no evidence path is reopened.

    The actual constants/parser loader is separate from byte validation. Before
    ordering, physical inventory completeness, FD leases, compilation, execution
    and stable publication remain the outer caller's obligations.
    """
    tree = _build_path(root, 'root', absolute=True)
    bin_dir = _build_path(build, 'build', absolute=True)
    if tree.is_relative_to(bin_dir):
        raise Refused('player build: checkout is under build')
    if type(host) is not str or host not in ('dxvk-native', 'sdl3-gpu'):
        raise Refused('player build: unknown host')
    if type(files) is not PlayerBuildFiles or type(before) is not tuple:
        raise Refused('player build: invalid graph arguments')
    if type(files.inputs) is not tuple or type(files.depfiles) is not tuple or not files.depfiles:
        raise Refused('player build: missing input/depfile inventory')
    ch, br = _player_build_providers()
    role = {'dxvk-native': 'dxvk', 'sdl3-gpu': 'sdlgpu'}[host]
    binary = ch.PLAYERS[role]
    where, keys = ch.CONTRACTS[binary]
    record = _latency_json(_build_text(files.record, 'record'), 'player build record')
    report = _latency_json(_build_text(files.report, 'report'), 'player build report')
    expectations = _latency_json(_build_text(files.expectations, 'expectations'), 'player build expectations')
    dep_texts = [_build_text(d, 'depfile') for d in files.depfiles]
    identities = {}
    canonical = {}

    def add(identity):
        identity = _build_identity(identity)
        old = identities.get(identity.path)
        if old is not None and old != identity:
            raise Refused('player build: conflicting input identity')
        identities[identity.path] = identity
        old = canonical.get(identity.canonical_path)
        if old is not None and (old.sha256, old.size, old.mode, old.device, old.inode) != (
                identity.sha256, identity.size, identity.mode, identity.device, identity.inode):
            raise Refused('player build: conflicting alias identity')
        canonical[identity.canonical_path] = identity

    input_paths = set()
    for identity in files.inputs:
        if type(identity) is not BuildFileIdentity or identity.path in input_paths:
            raise Refused('player build: duplicate or invalid graph input')
        input_paths.add(identity.path)
        add(identity)
    for held in (files.record, files.report, files.expectations, *files.depfiles):
        add(held.identity)

    def at(path, digest=None):
        identity = identities.get(str(path))
        if identity is None:
            raise Refused(f'player build: unheld input {path}')
        if digest is not None and identity.sha256 != digest:
            raise Refused(f'player build: digest mismatch {path}')
        return identity

    def digest_map(kind):
        doc = record.get(kind)
        if type(doc) is not dict or (kind in ('sources', 'depfiles') and not doc):
            raise Refused(f'player build: invalid {kind} map')
        for key, digest in doc.items():
            _build_path(key, kind + ' key', absolute=kind == 'external')
            if type(digest) is not str or SHA256.fullmatch(digest) is None:
                raise Refused(f'player build: invalid {kind} digest')
        return doc

    if record.get('schema') != ch.EXACT_RECORD or record.get('tree') != root:
        raise Refused('player build: wrong schema or checkout')
    maps = {kind: digest_map(kind) for kind in ('binaries', 'sources', 'generated', 'external', 'depfiles')}
    if list(maps['binaries']) != [binary]:
        raise Refused('player build: wrong sole binary')
    if (files.record.identity.path != str(bin_dir / ch.PLAYER_RECORDS[binary])
            or files.record.identity.canonical_path != files.record.identity.path):
        raise Refused('player build: wrong record path')
    if files.expectations.identity.path != str(bin_dir / where / 'expectations.json'):
        raise Refused('player build: wrong expectations path')
    pins = {}
    for pin in before:
        if type(pin) is not SourcePin or pin.path in pins:
            raise Refused('player build: duplicate or invalid before source')
        key = _build_path(pin.path, 'before source', absolute=False)
        identity = _build_identity(pin.identity)
        if (key.is_relative_to('private') or identity.path != str(tree / key)
                or identity.canonical_path != identity.path):
            raise Refused('player build: before source is not checkout source')
        pins[pin.path] = identity
    recipe = ('host/player/generate_expectations.py', 'tools/pins.json')
    if role == 'dxvk':
        recipe += ('host/player/dxvk.conf',)
    else:
        recipe += ('host/d3d9gpu/gen_com.py', 'host/d3d9gpu/test/extract_refs.py',
                   'host/d3d9gpu/shader/build_translator.sh', 'host/d3d9gpu/shader/extract_dxvk_shader.py')
    if any(key not in maps['sources'] for key in (*ch.REQUIRED_SOURCES[binary], *recipe)):
        raise Refused('player build: missing required source')
    if role == 'dxvk' and any(k.startswith('host/d3d9gpu/') for k in maps['sources']):
        raise Refused('player build: DXVK record contains second-host sources')
    for key, digest in maps['sources'].items():
        if Path(key).is_relative_to('private') or (tree / key).is_relative_to(bin_dir):
            raise Refused('player build: private/generated input misclassified as source')
        identity = at(tree / key, digest)
        if identity.canonical_path != identity.path:
            raise Refused('player build: aliased checkout source')
        if pins.get(key) != identity:
            raise Refused(f'player build: source not held unchanged before {key}')
    for kind in ('binaries', 'generated', 'depfiles'):
        for key, digest in maps[kind].items():
            identity = at(bin_dir / key, digest)
            if identity.canonical_path != identity.path:
                raise Refused(f'player build: aliased {kind} path')
    for key, digest in maps['external'].items():
        path = Path(key)
        if path.is_relative_to(bin_dir) or (path.is_relative_to(tree) and not path.is_relative_to(tree / 'private')):
            raise Refused('player build: wrong external classification')
        identity = at(path, digest)
        if identity.canonical_path != key:
            raise Refused('player build: external key is not canonical')
    player = _latency_at(report, 'files.player', 'player build report')
    player_identity = at(bin_dir / binary, maps['binaries'][binary])
    if (type(player) is not dict or player.get('sha256') != player_identity.sha256
            or player.get('real_path') != player_identity.canonical_path
            or player.get('path') != '/proc/self/exe'):
        raise Refused('player build: report does not identify held binary')
    scopes = ('obj/sa2_player',) if role == 'dxvk' else (
        'obj/d9g', 'obj/sa2_player_gpu', 'shader/shader-obj')
    inventory = {}
    for held in files.depfiles:
        path = Path(held.identity.path)
        if not path.is_relative_to(bin_dir):
            raise Refused('player build: depfile outside build')
        key = str(path.relative_to(bin_dir))
        if path.suffix != '.d' or str(Path(key).parent) not in scopes or key in inventory:
            raise Refused('player build: invalid/duplicate depfile inventory')
        inventory[key] = held.identity.sha256
    if inventory != maps['depfiles']:
        raise Refused('player build: depfile inventory differs from record')
    prerequisites = set()
    for data in dep_texts:
        try:
            paths = br.depfile_paths(data.decode('utf-8'))
        except (UnicodeError, SystemExit) as exc:
            raise Refused(f'player build: invalid depfile ({exc})') from None
        for raw in paths:
            path = _build_path(raw, 'depfile prerequisite', absolute=True)
            identity = at(path)
            resolved_path = Path(identity.canonical_path)
            if resolved_path.is_relative_to(bin_dir):
                kind, key = 'generated', str(resolved_path.relative_to(bin_dir))
            elif resolved_path.is_relative_to(tree) and not resolved_path.is_relative_to(tree / 'private'):
                kind, key = 'sources', str(resolved_path.relative_to(tree))
            else:
                kind, key = 'external', str(resolved_path)
            if maps[kind].get(key) != identity.sha256:
                raise Refused('player build: prerequisite missing/wrong classification')
            prerequisites.add(str(resolved_path))
    required_compiles = [tree / p for p in ch.REQUIRED_SOURCES[binary] if p.endswith('.cpp')]
    if any(str(p) not in prerequisites for p in (*required_compiles, tree / 'host/sa2_stream.h')):
        raise Refused('player build: required compile input absent from depfiles')
    contract = expectations.get('contract')
    if type(contract) is not dict or list(contract) != list(keys):
        raise Refused('player build: wrong host contract keys/order')
    for key, value in contract.items():
        if key.endswith('_sha256'):
            valid = type(value) is str and SHA256.fullmatch(value) is not None
        elif key == 'sdl_version':
            valid = type(value) is int and 0 < value <= 0xffffffff
        else:
            valid = type(value) is str and bool(value.strip())
        if not valid:
            raise Refused('player build: invalid contract value')
    json_key = f'{where}/expectations.json'
    header_key = f'{where}/sa2_host_expectations.h'
    if maps['generated'].get(json_key) != files.expectations.identity.sha256:
        raise Refused('player build: expectations absent from generated map')
    header = expectations.get('header')
    if type(header) is not dict or header.get('path') != str(bin_dir / header_key):
        raise Refused('player build: wrong expectations header')
    header_identity = at(bin_dir / header_key, maps['generated'].get(header_key))
    if (header_key not in maps['generated'] or header.get('sha256') != header_identity.sha256
            or header_identity.path not in prerequisites):
        raise Refused('player build: header not held/generated/compiled')
    libraries = expectations.get('libraries')
    expected_libraries = {'sdl3', 'dxvk'} if role == 'dxvk' else {'sdl3'}
    if type(libraries) is not dict or set(libraries) != expected_libraries:
        raise Refused('player build: wrong expectations libraries')
    for name, item in libraries.items():
        if type(item) is not dict:
            raise Refused('player build: invalid library metadata')
        path = _build_path(item.get('path'), 'library path', absolute=True)
        selected = _build_path(item.get('selected_path'), 'selected library path', absolute=True)
        identity = at(path, item.get('sha256'))
        alias = at(selected, identity.sha256)
        if (identity.canonical_path != str(path) or alias.canonical_path != str(path)
                or item.get('sha256') != identity.sha256
                or contract['sdl_sha256' if name == 'sdl3' else 'dxvk_sha256'] != identity.sha256):
            raise Refused('player build: library contract/alias mismatch')
    source_metadata = expectations.get('sources')
    if type(source_metadata) is not list or not source_metadata:
        raise Refused('player build: missing expectations sources')
    metadata_paths = set()
    for item in source_metadata:
        if type(item) is not dict:
            raise Refused('player build: invalid expectations source')
        path = _build_path(item.get('path'), 'expectations source path', absolute=True)
        digest = item.get('sha256')
        if type(digest) is not str or SHA256.fullmatch(digest) is None or str(path) in metadata_paths:
            raise Refused('player build: invalid/duplicate expectations source')
        identity = at(path, digest)
        metadata_paths.add(str(path))
        canonical_path = Path(identity.canonical_path)
        if canonical_path.is_relative_to(tree) and not canonical_path.is_relative_to(tree / 'private'):
            key = str(canonical_path.relative_to(tree))
            if pins.get(key) != identity:
                raise Refused('player build: expectations source absent/drifted before')
    metadata_required = {str(tree / 'tools/pins.json'), str(tree / 'host/player/generate_expectations.py')}
    if role == 'dxvk':
        config = str(tree / 'host/player/dxvk.conf')
        metadata_required.add(config)
        if at(config).sha256 != contract['dxvk_config_sha256']:
            raise Refused('player build: config contract mismatch')
    if (not metadata_required <= metadata_paths
            or not {'SDL_version.h', 'SDL_revision.h'} <= {Path(p).name for p in metadata_paths}):
        raise Refused('player build: incomplete expectations source metadata')
    if expectations.get('sdl3_configuration') not in ('stock', 'patched'):
        raise Refused('player build: invalid SDL configuration')
    if (role == 'sdlgpu' and expectations.get('host') != role
            or role == 'dxvk' and 'host' in expectations and expectations['host'] != role):
        raise Refused('player build: wrong expectations host')
    compiler = record.get('compiler')
    if (type(compiler) is not dict or any(type(compiler.get(k)) is not str or not compiler[k].strip()
                                        for k in ('command', 'version'))
            or type(compiler.get('search')) is not list or not compiler['search']):
        raise Refused('player build: invalid compiler metadata')
    for directory in compiler['search']:
        _build_path(directory, 'compiler search directory', absolute=True)
    return PlayerBuildBinding(host, binary, files.record.identity.sha256, player_identity.sha256,
                              tuple(sorted(identities)))


if __name__ == "__main__":
    raise SystemExit(main())
