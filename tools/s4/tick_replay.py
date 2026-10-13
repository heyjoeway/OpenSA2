#!/usr/bin/env python3
"""Harness level 2: replay a native tick capture through the lifted build.

Subtitle: from a registered native capture to M5's row in one command -- run
the lifted build with its tick hook, show the hook changed nothing, compare
with Omochao Split, and write the result.

What a replay is here
---------------------
`Documentation/replays.json` lists the retained replays. Each names a native
capture the live oracle took (one row per GameLoop return, the fields in
`harness/replay/tick_fields.h`), the repeats that show the native run is
deterministic, and a comparison policy for any field whose raw value is an
allocation address. The lifted side is the reach run exactly -- the tag,
environment and probe arguments `tools/s4/reach_receipt.py` defines -- with
`harness/replay/wrap_tick_probe.c` linked in by `-wrap sub_433ee0`.

What it refuses to do quietly
-----------------------------
* **Compare a perturbed run.** The hook only reads guest memory, so the hooked
  run must reproduce the reach receipt's iterations, import calls, frames and
  stop exactly. If it does not, nothing is compared.
* **Compare a lifted capture from another build.** What the lifted capture
  depends on (the runtime, the reach build, the hook and the link tools) is
  recorded before the run and checked after it, and `--no-run` refuses unless
  it is unchanged since the capture was taken.
* **Run on a device set the native capture did not have.** The host's device
  answers are a recorded property of each capture (the user's ruling of
  2026-09-23): the registry names the capture's device profile, which must be
  the one its receipt records and every repeat's, and the lifted run reads it
  through SA2_DEVICE_PROFILE, with every device switch the profile needs on
  (tools/s4/reach_receipt.py `DEVICES`, which the reach configuration switches
  on for every run; `profile_env` is the one definition of what a profile adds),
  and reports the sha256 of what it read. A capture with no recorded profile is
  refused, never run on the default, and the reach run the hooked run is held
  to must have run on the same profile.
* **Run in another directory than the capture's game did.** The profile
  carries the capture's current directory and executable path as Wine named
  them (tools/s4/device_profile.py `guest_paths`), `profile_env` hands them to
  the run, and the run's own record must show it answered them; the reach run
  it is held to must have run in the same one. CRI's file table holds the full
  path of every file it opens (agents-journals/s4/2026-09-24-tick-380-xeci.md),
  but no word of the table is in either replay's mask, since the natives do not
  hold its path words alike, so the directory and the paths are held by the run's
  own record, not by the comparison: an open is compared by its number, order and
  kind, its pairing and its close tick, and comparing its path at the pairing is
  carried forward (Documentation/S4_CARRY_FORWARD.md). Until 2026-09-27 this said
  the directory was compared state (the G4 interrogation, Reviewer B's finding 4).
* **Trust a native capture that is not the registered one, or not shown to
  repeat.** The capture must hash to its pin and have been built from the
  probe now in the tree, there must be at least one registered repeat, and a
  replay is judged only over the ticks every repeat reproduces. When that is
  short of the capture, the replay is **unrepeatable** past it: an unmet state
  in its own right, never a pass.
* **Take a registered capture's oracle on trust.** The capture and every
  repeat must have been produced by one oracle mode, since a replay compares
  them with each other (tools/oracle/oracle_mode.py `require_same`: the user's
  direction of 2026-09-23 that comparisons across modes are refused), and that
  mode must be the one the registry names. An entry that says
  `"oracle": "vanilla"` stands for the game's own code with the Mod Loader's
  rewrites undone (harness/replay/native_vanilla_probe.c), so each of its
  captures must also be one the vanilla oracle as it stands in the tree took:
  tools/oracle/oracle_mode.py `vanilla_problems` is the one check of that, and
  the recorder, the stage run, the attract-demo run and the import-log check
  ask it too (`native_vanilla_problems` below is its caller here). The user
  adopted the vanilla oracle on 2026-09-23
  (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md), and each replay
  moves to it when it is re-captured; until then an entry without the flag is
  the Mod Loader's oracle, and its captures must be the loader's. An entry
  naming any other oracle is refused.
* **Compare runs that started from different saves.** The lifted run starts
  from the pinned saves (Documentation/save-state.json) and an overlay that
  holds nothing else, and the capture and every repeat must record that they
  started from the same pin (tools/oracle/save_state.py native_start_problems,
  `native_start` below: the user's ruling of 2026-09-24 that the save file is
  a recorded input).
* **Invent a residue.** A divergence is `bug` unless the registry labels that
  exact tick and field with a residue record that exists and names the same
  replay, tick, field and both values in its `observed_in`, or the word is one
  a residue class the entry names lists (below). A run that ended cannot be
  labelled at all: a crash is never a residue.
* **Stop looking after a residue.** A labelled cell is recorded and masked and
  the comparison continues, so a later bug or a crash is still the replay's
  result. Design 7.4 promotes a residue that changes gameplay-visible state to
  a bug, and nobody can see that change if the comparison has stopped.

Residue classes: words, not cells
---------------------------------
The user's direction of 2026-09-26 (decisions 2 and 3,
Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md) made two
classes of words residues: host services (the Steam achievement table) and
host timing (words whose value depends on when background work finished).
Each is a record under Documentation/receipts/residues/ of schema
`sa2-residue-class/v1`, and a word-policy entry names the ones its comparison
applies in `residue_classes`. A record's groups are generated from a table
definition, each with its owner, why it is not gameplay, a rule its lifted
values must keep, its evidence and what would retire it (`record_problems`,
which refuses any word that is a named field of tick_fields.h, an address a
stage probe reads, a field of the input the input probe applies, or a word
compared by identity). A group may name the replays it applies to (`applies`);
in every other its words stay judged wherever that replay's mask selects them. A
rule is held on the lifted log alone
(zero, below) or against the native capture's (native: `evaluate_native` reads
both logs, and `rule_problems` re-derives its verdict from what it observed),
and a native-relative group must name its replays. Its words leave the judged
digests as the identity words do (`effective_mask`) and leave the native
repeatability test with them; every listed word, masked or not, is then
compared on both sides and reported per group (`residue_report`). A host-timing
record may also name the file-table handle words whose open ticks may shift
(`identity_open_shift`): their opens are paired in order and must agree in
number and kind, each pair within the widest shift the replay's own native
repeats show, computed on every pass and never typed; their pairing and close
ticks stay judged, as do all three checks of every other identity word. The
class is what a replay reads only when nothing judged diverges (`settle`): a
judged divergence anywhere, or a lifted value that breaks a group's rule, is
the replay's result, classed bug. What the replay would read with no class is
recorded beside it (`unclassed`), and `residue_problems` -- M5's check reads it
-- rederives the class, the rules, the bound and that record from what the
result holds.

The user's ruling of 2026-09-27
(Documentation/decisions/2026-09-27-S4-sofdec-thread-race-residue.md) made a
third class, host thread race, for one race alone: Sofdec MPV's picture
dispatcher 0x007fbed0 posts its slice job 0x007fbe40 to three worker threads
and runs it itself, and which of the four participants takes the slice decides
which of the four decoder contexts holds the decode. The class is scoped in code,
not in its record (THREAD_RACE_SCOPE, `thread_race_problems`): one record, one
group, the 244 words of the four contexts' 61 listed offsets, the boot replay,
those two routines, the context-permutation rule and the one observable effect
the record must name; a record that adds a word, a group, a replay or a routine,
edits the rule's parameters, another record of the class (a race recorded
anywhere else under it), or another class listing any word of the four contexts
is refused. So a race anywhere else reads as a bug until a reviewed change of the
scope covers it -- wherever a replay's mask selects its words. A race the native
captures themselves split takes its words out of the mask as unstable, as these
244 were taken out, and such words are compared by nothing, class or not. The
rule (`evaluate_native`, form context-permutation) holds, on every tick, the
lifted decode to the native capture's in whichever context holds it, each side's
other contexts to the dispatcher's copy, and the group to native before the
contexts exist; with the record's effect it also holds the movie's uploads,
read from Sofdec's frame number on both logs, to the capture's frames in its
order, on its ticks but the ones the effect lists (`uploads_verdict`), so a
runtime change that moves another upload, or puts this one back, is a bug until
the record is measured again.
* **Treat a run that stopped as a run that agreed.** Omochao Split rejects an
  identity present in only one stream, so it compares the ticks both streams
  have, and a lifted stream that ends first is its own divergence.

Stage entries
-------------
An entry with a `stage` block (`name`, `level`) is a stage replay: a native
replay of a recording (tools/oracle/record_stage.py, tools/oracle/retake_replays.py)
and the lifted build replaying the same recording through the test spawn
(tools/s4/stage_run.py `command`, the one definition of a stage run). Before
anything runs, `stage_entry_problems` holds it to what stage-route item 5 asks
(Documentation/decisions/2026-09-23-S4-stage-route.md): the stage is one
stage_run.py starts, at its level; every file is its pin (the recording's
input, each native tick capture, end record and stage trace); every native
capture is a run of its own, in its own directory, with its end record and
trace beside it (tools/s4/stage_boot.py native_run_problems, so the two tools
refuse the same entries); every end record names one goal tick above zero
with no missed input row; and the entry compares through its own digest mask,
harness/replay/masks/<name>.h, whose sources' build and configuration every
native capture must be (`stage_mask_problems`). Its lifted side is two runs
of one build, in one invocation (`run_stage_lifted`): the observers off, then
on, and the two must agree on every reach field, the device profile, the import
table and the binary (`non_perturbation_stage`); the stage has no reach receipt
to be held to. The observed run must apply every recorded input row up to the
native capture's last tick and miss none (`input_record`). Its tick capture is
compared as boot's is, and its stage trace too (`compare_trace`: the player's
position and the stage's state, which the tick capture's named fields do not
hold), and the replay's first divergence is the earlier of the two
(`first_reported`). The goal is not judged here: tools/s4/stage_boot.py reads
the goal-latch column and stage trace this records and writes M6's
`replay_complete` (agents-journals/s4/2026-09-23-m6-goal-latch.md).

Values the host chooses: one rule
---------------------------------
A value the host chooses -- an allocation address, or a Win32 handle number --
is compared by what it names, not by its bits (the user's rulings of
2026-09-24, decisions 1 and 2, one rule on M5:
Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md). The words are
harness/replay/masks/identity-words.json's, the one record the mask tool reads
too: the heap words and CRI's file-table handle words. An entry with a
`word_policy` is compared from both sides' word logs (tools/s4/wordlog.py; the
native captures' sa2-word-log.bin.zst, the lifted run's SA2_REPLAY_WORDS):
the named fields, the digest columns recomputed under the replay's own mask
(harness/replay/masks/<replay>.h when it exists, else harness/replay/
tick_digest.h) less the identity words, and one column per identity word the
named fields do not hold (`h_<va>` a handle, `a_<va>` a heap word).

Each stream's values are numbered by first appearance, in column order within
a tick, heap values and handle values apart (`canonical`); a handle number is
released at the end of the tick in which no handle word holds it (`RELEASED`),
since Wine and the runtime's file layer reuse a freed number and two table
entries can hold one handle; a heap value keeps its number for good.
Null stays null, and for a handle so does INVALID_HANDLE_VALUE. On the lifted
side a heap value outside the guest heap (`runtime/guest_memory.h`) cannot be
an allocation the runtime made, and never compares equal. The comparison then
holds three checks, each able to fail on its own (`identity_checks`): the
pairing (while both sides hold a value at a word, the native value and the
lifted one are paired for good at first sight, and every later cell keeps
that pairing), the open tick (a word goes from null to a value on the same
tick on both sides) and the close tick (from a value to null likewise). Its
events and verdicts are recorded in the result (`identity`), and
`identity_problems` -- M5's check reads it -- recomputes them. A native repeat
that opens or closes on another tick than the capture is a native-native
divergence like any other, so the replay is unrepeatable from that tick, not
a lifted bug. An `identity` field policy without evidence is refused, and one
that disagrees with the record is too.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/tick_replay.py                  # every registered replay
  .venv/bin/python tools/s4/tick_replay.py --replay boot    # one
  .venv/bin/python tools/s4/tick_replay.py --no-run         # re-compare the kept lifted captures
  .venv/bin/python tools/s4/tick_replay.py --receipt        # and write the tracked receipt
  .venv/bin/python tools/s4/tick_replay.py --exe shipped    # on the shipped sonic2app.exe

Writes private/generated/replay/<name>/{lifted.csv,result.json},
private/generated/replay-results.json (M5's input, per metrics.py), and with
--receipt Documentation/receipts/s4-tick-replay-v1.json, which
Documentation/scripts/check_replay_results.py holds current. Prints summary
lines prefixed REPLAY. `--exe shipped` runs the lifted side on the shipped
executable through the overlay table (tools/s4/loop_run.py --exe, link_s4.py
--exe-overlay) and writes only under private/generated/replay-shipped/: the
registered results and the receipt stay the unpacked executable's.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
sys.path.insert(0, str(HERE.parent / "oracle"))
import common as C  # noqa: E402
import device_profile as DP  # noqa: E402
import oracle_mode as OM  # noqa: E402  the oracle a capture shows, and the one vanilla check
import reach_receipt as RR  # noqa: E402
import save_state as SS  # noqa: E402  the pinned saves every lifted run starts from
import wordlog as WL  # noqa: E402  the one reader of both sides' word logs

ROOT = C.ROOT
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import compare_sa2_record_streams as SPLIT  # noqa: E402  Omochao Split, design 7.2's comparer

PY = ROOT / ".venv" / "bin" / "python"
REGISTRY = ROOT / "Documentation" / "replays.json"
RESIDUES = ROOT / "Documentation" / "receipts" / "residues"
REACH = ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-tick-replay-v1.json"
OUT = C.GEN / "replay"
RESULTS = C.GEN / "replay-results.json"
RUNDIR = C.GEN / "s4" / "run"

HOOK = "harness/replay/wrap_tick_probe.c"
NATIVE_PROBE = "harness/replay/native_tick_probe.c"
WRAP = "sub_433ee0"
BUILD = f"replay.{RR.TAG}"
# The executable the lifted runs load (tools/s4/loop_run.py --exe). The registered replays,
# replay-results.json and the receipt are the unpacked executable's, always. `--exe shipped`
# runs the same replays on the shipped sonic2app.exe through the overlay table
# (runtime/sa2_exe_overlay.h): into OUT_SHIPPED, under builds and run records named apart, and
# it writes neither RESULTS nor the receipt, so a shipped run never stands for a registered one.
EXE = {"kind": "unpacked", "overlay": None}
OUT_SHIPPED = C.GEN / "replay-shipped"
IDENTITY = "tick"
NAME = re.compile(r"[A-Za-z0-9._-]+\Z")    # what stage_exit.py can parse back out of METRICS.md

# The kinds of first divergence, and what each is classed as when unlabelled.
RUN_ENDED = "run ended"        # has a space, so it can never be a field name
UNREPEATED = "native repeat"   # likewise
BOUNDED = "run bounded"        # likewise
# The note runtime/sa2_imports.c writes when SA2_IMPORT_BUDGET stops a run: the
# harness's own deterministic bound, not a failure of the build.
BUDGET_NOTE = "the import-call budget of "
# The other leash: at the pump's poll budget (SA2_PUMP_POLLS) the host posts
# WM_QUIT and the guest leaves its loop. The run JSON's pump block says so
# (`quit_by_host`), whatever trap the guest's exit path meets afterwards.
QUIT_NOTE = "the host posted WM_QUIT at the pump's poll budget of "
POLICIES = ("identity",)
REACH_KEYS = ("iterations", "import_calls", "frames_presented", "stop")

# runtime/guest_memory.h: SA2_GUEST_HEAP_BASE .. SA2_GUEST_HEAP_END. Every
# allocation the runtime hands the guest comes from here.
GUEST_HEAP = (0x04000000, 0x20000000)
OUT_OF_HEAP = "0xffffffff"     # never an ordinal: a stream has fewer distinct values

# What the lifted capture depends on. The runtime is in it on purpose: a replay
# run against a runtime that has since moved describes a build that no longer
# exists, which is the defect the reach receipt was built to stop.
LIFTED_PIPELINE = ["tools/s4/loop_run.py", "tools/s4/link_s4.py", "tools/s4/reach_receipt.py",
                   HOOK, "harness/replay/tick_probe_core.h", "harness/replay/tick_fields.h",
                   # the generated digest mask: which words each digest column hashes.
                   # Missing until the 2026-09-23 review showed a changed mask left the
                   # recorded replay looking current.
                   "harness/replay/tick_digest.h",
                   "harness/crt_probe.c",
                   # which device profile a lifted run is handed, and how it is checked
                   "tools/s4/device_profile.py",
                   # which saves a lifted run starts from, and how the overlay is cleared
                   "Documentation/save-state.json", "tools/oracle/save_state.py"]
# The vanilla oracle's probe, and what a registry entry may say its oracle is;
# absent is the Mod Loader's. The log's grammar and what a vanilla capture must
# show are tools/oracle/oracle_mode.py's alone: this file kept a second parser
# of sa2-vanilla.csv until 2026-09-24, and the two disagreed (verify-profile's
# review found this one accepting an experiment built with RESTORE_IAT=1).
VANILLA_PROBE = OM.VANILLA_PROBE
ORACLES = ("vanilla",)
# The mode each registry value names: absent is the Mod Loader's.
ORACLE_MODE = {None: "loader", "vanilla": "vanilla"}
# What the comparison depends on, besides the registry entry and the captures.
# The vanilla probe and oracle_mode.py are here because a registered capture's
# oracle is read by the one and held to the other's hash, KEEP table and import
# slot default: an edit to either stales the result.
COMPARE_PIPELINE = ["tools/s4/tick_replay.py", NATIVE_PROBE, DP.PROBE, VANILLA_PROBE,
                    "tools/oracle/oracle_mode.py",
                    "Documentation/scripts/compare_sa2_record_streams.py"]
# What a word-policy entry's comparison reads besides: the identity record, its
# parser and the mask writer (tools/s4/snapshot_fields.py identity_words and
# write_mask, the mask tool's own), and the reader of both sides' logs with
# everything it is compiled from but the mask, which compare_pipeline adds per
# entry (`mask_for`).
IDENTITY_RECORD = "harness/replay/masks/identity-words.json"
WORD_COMPARE = [IDENTITY_RECORD, "tools/s4/snapshot_fields.py", "tools/s4/wordlog.py",
                "tools/s4/wordlog.c", "harness/replay/word_log_core.h",
                "harness/replay/tick_probe_core.h", "harness/replay/tick_fields.h"]
MASKS_DIR = "harness/replay/masks"
BOOT_MASK = "harness/replay/tick_digest.h"
# The kinds of value the host chooses (the record's `kind`), and what in each
# is no value the host chose: a null pointer, and for a handle also Win32's
# INVALID_HANDLE_VALUE, which CreateFile returns when an open fails. Those are
# compared by their bits.
KINDS = ("heap", "handle")
# The kinds whose numbers are released when no word of the kind holds the value.
# Handles only: the record of the user's ruling 1 describes the release for
# handles (Wine reuses a freed number), and ruling 2 confirms heap identity as it stood, a
# number kept for good. Releasing heap numbers too would only remove
# divergences, a re-set pointer among them, which is the user's to allow (rule
# 10; the U5 fold review, W1).
RELEASED = ("handle",)
NULLS = {"heap": frozenset({0}), "handle": frozenset({0, 0xFFFFFFFF})}
COLUMN_PREFIX = {"heap": "a_", "handle": "h_"}
CHECKS = ("pairing", "open", "close")
LIFTED_WORDS = "lifted-words.bin"      # SA2_REPLAY_WORDS, kept compressed beside lifted.csv
# A quoted #include, and any other: a probe that includes a file by macro or by
# a path this cannot resolve cannot say which headers it was built from.
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*(.*?)[ \t]*$', re.M)
RUNTIME_GLOBS = ("runtime/*.c", "runtime/*.cpp", "runtime/*.h")

# A stage entry's native side is built from two probes beside the tick probe:
# the stage trace it is compared on, and the input probe its goal tick and
# input come from. Each capture's receipt must record the ones in the tree
# (native_probe_current). The input probe includes its replay script by macro
# (INPUT_SCRIPT), so only its source is compared; the script is the entry's
# `input` pin.
STAGE_PROBES = ("harness/replay/native_stage_trace.c", "harness/replay/native_input_probe.c")
# What a stage entry's comparison reads besides boot's: those two probes, the
# entry's checks (tools/s4/stage_boot.py) and the input's one parser
# (tools/oracle/record_stage.py recorded_rows, wrap_input_replay.c's grammar).
STAGE_COMPARE = [*STAGE_PROBES, "tools/s4/stage_boot.py", "tools/oracle/record_stage.py"]
# What a stage entry's lifted run is built and run from besides boot's: the
# stage run's one definition and every hook it links (stage_run.HOOKS, read
# when asked, since stage_run imports this module).
STAGE_LIFTED = ["tools/s4/stage_run.py", "tools/s4/rebuild_image.py"]
# The poll budget past the native capture's rows: a native replay ends 300
# ticks after the goal and captures one row fewer than it ran, so the lifted
# run's ticks cover every native row and the goal with room to spare.
STAGE_LEASH_MARGIN = 60
# The stage trace's columns as a comparison names them: `trace.<column>`, so a
# divergence in it can never be mistaken for a tick-capture field, and a
# residue label names which stream it is about.
TRACE_PREFIX = "trace."
TRACE_POLICY = {f"{TRACE_PREFIX}player": {
    "compare": "identity",
    "evidence": "MainCharObj1[0] (0x01DEA6C0), which harness/replay/native_stage_trace.c and "
                "harness/replay/wrap_tick_probe.c both print: the player object's address, a heap "
                "allocation on either side (the guest heap lifted, Wine's natively), so it is "
                "compared by which allocation it names, as the tick capture's heap pointers are"}}
APPLIED_HEADER = ["tick", "ord", "matched", "on", "off", "press", "release", "r", "l", "x1",
                  "y1", "x2", "y2", "old"]

# Residue classes of words (the module docstring): the record schema, the two
# classes the user ruled on 2026-09-26, the registry field an entry names them
# in, and the rules a group's lifted values may be held to.
RESIDUE_SCHEMA = "sa2-residue-class/v1"
RESIDUE_CLASSES = ("host-services", "host-timing", "host-thread-race")
RESIDUE_FIELD = "residue_classes"
RESIDUE_RULES = ("zero", "below", "none", "native")
# A native-relative rule's forms (`native_rule_problem`, `evaluate_native`): each
# reads the group's words from both sides' logs and holds the lifted series to the
# native capture's, over the ticks compared. one-tick-scan: the group is two or more
# copies of one value (its table's entries), which may differ from native on at most
# `max_ticks` ticks, on each of them the lifted copies equal, every native word at
# `reset` and the lifted `progress` word, outside the group, still moving to the next
# tick. context-permutation: the group is one workspace per context (its table's
# entries), a `dispatcher` context whose values a picture's dispatch copies into the
# others before one participant decodes into its own; up to and at `kept_from` (when
# the contexts are created) the whole group equals native; after it, on each side
# and at every tick, every context but one holds one value (the copies) and the one
# left the decode (`contexts_form`), the lifted decode equals the native one in
# whichever context holds it (a context permutation), on every tick a side's
# contexts change its copies are its dispatcher context of the tick before, and no
# run of ticks on which the group differs from native is longer than `episode_max`;
# and, when its record names an effect with the uploads it moves (`native_params`,
# `uploads`), the movie's uploads -- the ticks the effect's frame word advances on,
# on both sides -- are the capture's frames in its order, on its ticks but exactly
# the ones the effect lists.
NATIVE_FORMS = {"one-tick-scan": ("max_ticks", "reset", "progress"),
                "context-permutation": ("dispatcher", "kept_from", "episode_max")}
EPISODES_LISTED = 16           # runs of differing ticks a context-permutation report lists
# The host-thread-race class's scope, the user's ruling of 2026-09-27, condition 1:
# "Scope the class to this race only: the Sofdec decode routine and the 244 listed
# values. A race anywhere else is a bug until reviewed." The class holds this one
# record, whose one group is exactly this (`thread_race_problems`): its words, its
# replays, its routines and its rule's parameters, and its effect read from the
# frame word below; no record of another class may list any word of the four
# contexts' whole span. Widening it is a change of this literal, reviewed as a
# comparer change, never an edit of a record (the race comparer review's finding 2).
# Condition 2's effect is the scope's too (the G4 interrogation, Reviewer B's
# finding 2): the named upload and every upload the record may list as moved are
# pinned here, so a record edit that renames the effect, or lists another upload
# as moved with its owner to match, is refused however consistent it is. Both are
# fitted to the registered capture (boot-ticks-v15a) and the lifted boot they were
# measured on: the natives move uploads by a tick among themselves, so another
# capture or a runtime that moves an upload is a change of this literal, measured
# again and reviewed, never a record edit (Reviewer A's note 5; the record says so).
THREAD_RACE_CLASS = "host-thread-race"
THREAD_RACE_SCOPE = {
    "id": "host-thread-race-sofdec-v1",
    "ruling": "Documentation/decisions/2026-09-27-S4-sofdec-thread-race-residue.md",
    "group": "sofdec-mpv-slice-race",
    "replays": ["boot"],
    # Sofdec MPV's multi-thread picture dispatch and the slice job it posts and runs
    "routines": ["0x007fbed0", "0x007fbe40"],
    "form": "context-permutation",
    # the rule's parameters: the dispatcher's context, the tick the contexts are
    # created on, and the longest run of differing ticks the registered capture shows
    "rule": {"dispatcher": 0, "kept_from": 378, "episode_max": 6},
    # Sofdec's frame number: it advances on the row after each movie upload (403 of
    # 403 in unit SF's texture-hash runs), so the effect is read from it on both logs
    "uploads_word": "0x01d0c6d0",
    # the one observable effect the ruling names, in the replay's own ticks (the
    # capture's rows): movie frame 270's upload at tick 1032 lifted, 1033 in the
    # registered capture; the ruling's 1031 and 1032 count GameLoop returns, one less
    "effect": {"frame": 270, "lifted": 1032, "native": 1033, "capture": "boot-ticks-v15a"},
    # every upload the lifted boot makes on another tick than the registered capture,
    # [frame, lifted tick, native tick, owner]: the named effect, and the movie's first
    # frame, which host timing's opening scan owns (the ruling's gloss: "host timing's
    # opening pass, not this race")
    "moved": [[1, 389, 383, "host-timing-v1/sofdec-opening-scan"],
              [270, 1032, 1033, "host-thread-race-sofdec-v1/sofdec-mpv-slice-race"]],
    "table": {"base": "0x01c0a480", "stride": "0x1700", "count": 4, "offsets": [
        "0x328", "0x32c", "0x334", "0x344", "0x348", "0x368", "0x370", "0x378", "0x37c",
        "0x390", "0x398", "0x39c", "0x3a4", "0x3a8", "0x3ac", "0x3c0", "0x3e4", "0x3ec",
        "0x3f0", "0x3f4", "0x3fc", "0x41c", "0x428", "0x438", "0x43c", "0x444", "0x448",
        "0x474", "0x47c", "0x490", "0x494", "0x498", "0x49c", "0x4a0", "0x4a4", "0x4b0",
        "0x4b8", "0x4bc", "0x4c4", "0x4c8", "0x4cc", "0x4d0", "0x4d4", "0x4d8", "0x4e4",
        "0x4e8", "0x4ec", "0x4f0", "0x58c", "0x598", "0x59c", "0x5a8", "0x5ac", "0x5b8",
        "0x5bc", "0x734", "0x78c", "0x794", "0x7a8", "0x7b0", "0x7b4"]},
}
# The stage probes' own definitions of what they read (harness/replay/native_stage_trace.c
# and native_input_probe.c, their #defines at or above the image base, and the input
# probe's PDS_PERIPHERAL fields from RAW0; the lifted hooks mirror them): no residue
# word may be one of them. The goal latch and the applied input are gameplay values a
# stage entry compares as surely as the trace's level and position.
STAGE_SOURCES = STAGE_PROBES
STAGE_SOURCE_WHAT = {STAGE_PROBES[0]: "the stage trace's source",
                     STAGE_PROBES[1]: "the input probe's source"}
PERIPHERAL = re.compile(r"\b(off|width)\[F_COUNT\]\s*=\s*\{([^}]*)\}")
DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+(0x[0-9A-Fa-f]+)u?\b", re.M)
IMAGE_BASE = 0x00400000
DECISIONS = "Documentation/decisions/"
RESIDUE_LISTED = 8             # differing words a group's report names, besides its digest
RULE_FIELD = "w_"              # a rule's divergence names the word: w_<va>, never a column


def refuse(message: str) -> SystemExit:
    return SystemExit(f"REPLAY_REFUSED {message}")


def run_dir(path: str) -> str:
    """The directory of a run's file, lexically ("a/../a/x" is "a/x"'s run):
    tools/s4/stage_boot.py's rule, which cannot be imported here at load."""
    return os.path.normpath(os.path.dirname(str(path)))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def must_sha256(path: Path) -> str:
    """A missing file must not hash to "" and then equal another missing file."""
    if not path.is_file():
        raise refuse(f"{path} does not exist")
    return sha256(path)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def runtime_digest(root: Path = ROOT) -> str:
    h = hashlib.sha256()
    files = [p for pattern in RUNTIME_GLOBS for p in sorted(root.glob(pattern))]
    if not files:
        raise refuse(f"no runtime sources under {root}; the digest would describe nothing")
    for p in files:
        h.update(f"{p.relative_to(root)}:{sha256(p)}\n".encode())
    return h.hexdigest()


def is_stage(entry: dict) -> bool:
    """A stage replay: an entry with a `stage` block (the module docstring)."""
    return "stage" in entry


def lifted_pipeline(entry: dict | None = None) -> list[str]:
    """What an entry's lifted capture is built and run from: boot's, and for a
    stage entry the stage run's definition and every hook it links."""
    if entry is None or not is_stage(entry):
        return list(LIFTED_PIPELINE)
    import stage_run as SR           # here: stage_run imports this module
    return list(dict.fromkeys(LIFTED_PIPELINE + STAGE_LIFTED + list(SR.HOOKS)))


def compare_pipeline(entry: dict | None = None, root: Path | None = None) -> list[str]:
    """What an entry's comparison reads, besides the registry entry and the
    captures: for a word-policy entry also the identity record, the log reader
    and the replay's own digest mask (`mask_for`), so a new mask or record
    stales M5 as a moved comparer does."""
    out = list(COMPARE_PIPELINE)
    if entry is not None and is_stage(entry):
        out += STAGE_COMPARE
    if entry is not None and "word_policy" in entry:
        root = ROOT if root is None else root
        out += WORD_COMPARE + [str(mask_for(entry, root).relative_to(root))]
    # A residue class's record, and the stage probes' sources its words are kept
    # out of: a changed record stales M5 as a moved mask does.
    ids = (entry or {}).get(RESIDUE_FIELD)
    for rid in ids if isinstance(ids, list) else []:
        if isinstance(rid, str) and NAME.match(rid):
            out += [f"Documentation/receipts/residues/{rid}.json", *STAGE_SOURCES]
    return list(dict.fromkeys(out))


def lifted_inputs(root: Path = ROOT, entry: dict | None = None) -> dict:
    """What the lifted capture was built and run from."""
    reach = root / "Documentation" / "receipts" / "s4-run-reach-v1.json"
    doc = json.loads(reach.read_text()) if reach.is_file() else {}
    return {
        # The manifest says which build; the reach says what it did. Both, so a
        # reach that moves under an unchanged manifest also makes this stale.
        # A stage entry's lifted runs are not the reach run, but they link the
        # same build, so the manifest is theirs too.
        "reach_sha256": digest({"manifest": doc.get("manifest", {}),
                                "reach": doc.get("reach", {})}),
        "runtime_sha256": runtime_digest(root),
        "pipeline": {t: must_sha256(root / t) for t in lifted_pipeline(entry)},
    }


def compare_inputs(entry: dict, root: Path = ROOT) -> dict:
    return {
        "registry_entry_sha256": digest(entry),
        "native_sha256": [entry["native"]["sha256"]]
                         + [r["sha256"] for r in entry["native"].get("repeats", [])],
        "pipeline": {t: must_sha256(root / t) for t in compare_pipeline(entry, root)},
    }


def inputs(entry: dict, root: Path = ROOT) -> dict:
    # The device profile the lifted run is handed is the registry's, so a
    # re-registered profile makes the lifted capture stale as a moved runtime does.
    lifted = dict(lifted_inputs(root, entry), device_profile=entry["native"].get("device_profile"))
    return {"lifted": lifted, "compare": compare_inputs(entry, root)}


def pipeline_files(entries: list[dict], root: Path | None = None) -> list[str]:
    """Every tracked file `inputs` reads for any of `entries`, in one list:
    what a receipt pins, and what a copied tree needs to compute them."""
    out: list[str] = []
    for e in entries or [None]:
        out += lifted_pipeline(e) + compare_pipeline(e, root)
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ captures

def read_capture(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Header and rows. Ticks must run 1..N with no gap or repeat: a gap is a
    lost row, and a replay indexed by tick cannot tell which one."""
    if not path.is_file():
        raise refuse(f"capture does not exist: {path}")
    with path.open(newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or not rows[0]:
        raise refuse(f"empty capture: {path}")
    header, body = rows[0], rows[1:]
    if header[0] != IDENTITY:
        raise refuse(f"{path}: the first column is {header[0]!r}, not {IDENTITY!r}")
    out = []
    for k, row in enumerate(body, 1):
        if len(row) != len(header):
            raise refuse(f"{path}: row {k} has {len(row)} fields of {len(header)}")
        if row[0] != str(k):
            raise refuse(f"{path}: row {k} carries tick {row[0]!r}; ticks must run 1..N")
        out.append((k, row[1:]))
    return header, out


def check_policy(policy: dict, header: list[str]) -> None:
    for field, p in policy.items():
        if field not in header[1:]:
            raise refuse(f"the field policy names {field!r}, which the capture does not have")
        if p.get("compare") not in POLICIES:
            raise refuse(f"{field}: compare mode {p.get('compare')!r} is not one of {POLICIES}")
        if not str(p.get("evidence", "")).strip():
            raise refuse(f"{field}: an {p['compare']} policy needs evidence that the word "
                         "is an allocation address, or it is a way to stop comparing a field")


def normalise(header: list[str], rows: list[tuple[int, list[str]]], policy: dict,
              heap: tuple[int, int] | None = None) -> list[tuple[int, list[str]]]:
    """A field policy's identity fields as `canonical` numbers them, each a heap
    value; `heap` given means the lifted side, where a value outside it is made
    unmatchable. Kept for the callers that name a field policy (the stage
    trace's, tools/s4/stage_run.py compare)."""
    ident = {f: "heap" for f, p in policy.items() if p.get("compare") == "identity"}
    if heap is not None and tuple(heap) != GUEST_HEAP:
        raise refuse(f"normalise: a lifted heap is {GUEST_HEAP}, not {heap}")
    return canonical(header, rows, ident, lifted=heap is not None)


def canonical(header: list[str], rows: list[tuple[int, list[str]]], identity: dict[str, str],
              lifted: bool = False) -> list[tuple[int, list[str]]]:
    """Every identity column (`identity`: column -> kind) named by what it
    holds, the rest as it is: the module docstring's one rule. Within a tick
    the columns are read in order; a value not held the tick before, by any
    column of its kind, takes the next number of that kind, and a value no
    column of its kind holds at the end of a tick is released, for the kinds in
    RELEASED (handles), so it takes a new number if it comes back. Both sides
    need the release: Wine reuses a freed handle number, and so does the
    runtime's file layer (runtime/sa2_files.c handle_alloc takes the lowest free
    slot; only its kernel handles, in runtime/sa2_imports.c, are never reused).
    A heap value keeps its number for good, as it did before handles joined the
    rule. A null (NULLS) is written as it is. On the
    lifted side (`lifted`) a heap value outside GUEST_HEAP is written
    OUT_OF_HEAP, which no number equals. Kept per process by the stream's content
    and every input of the rule while it fits (`remembered`, as `split`'s reports
    are kept)."""
    bad = sorted({k for k in identity.values() if k not in KINDS})
    if bad:
        raise refuse(f"identity kinds {bad} are none of {KINDS}")
    key = (stream_digest(header, rows), json.dumps(identity, sort_keys=True), bool(lifted),
           repr((KINDS, RELEASED, sorted((k, sorted(v)) for k, v in NULLS.items()),
                 GUEST_HEAP, OUT_OF_HEAP)))
    out, shared = remembered(_CANONICAL, key, lambda: _canonical(header, rows, identity, lifted),
                             cells)
    return [(t, list(w)) for t, w in out] if shared else out


_CANONICAL: dict[tuple, tuple[int, list[tuple[int, list[str]]]]] = {}
_STREAMS: dict[tuple, tuple[int, tuple[list[str], list[tuple[int, list[str]]]]]] = {}

# What `canonical` and `log_stream` each keep per process, in cells (ticks times
# columns), the oldest dropped first (`remembered`). The replay-results gate
# recomputes a replay once per falsification, and a boot's streams (fifteen of
# about 0.6 million cells) are kept whole. A Metal Harbor stream is about 6.7
# million: its twenty native streams are each read, compared and released in
# turn, so a verdict holds the streams it is comparing and at most this many
# cells per cache besides. Keeping every stream held 18 GB for one Metal Harbor
# verdict.
KEPT_CELLS = 12_000_000


def cells(rows: list[tuple[int, list[str]]]) -> int:
    """A stream's cells: its ticks times its columns."""
    return len(rows) * len(rows[0][1]) if rows else 0


def remembered(cache: dict, key, make, size) -> tuple[object, bool]:
    """make()'s value, kept in `cache` under `key` while it fits in KEPT_CELLS with
    what the cache keeps (`size` reads a value's cells), the oldest entries
    dropped to make room. Returns (value, shared): a shared value is the one the
    cache keeps, so the caller hands out a copy; one too large to keep is the
    caller's alone. Every read goes through the cache's own lookup, so a cache a
    caller swaps in that drops a value once read (tools/s7/arm64_replays.py
    read_once) is read once."""
    if key not in cache:
        value = make()
        n = size(value)
        if n > KEPT_CELLS:
            return value, False
        while cache and sum(c for c, _ in cache.values()) + n > KEPT_CELLS:
            del cache[next(iter(cache))]
        cache[key] = (n, value)
    value = cache[key][1]
    return value, key in cache


def _canonical(header: list[str], rows: list[tuple[int, list[str]]], identity: dict[str, str],
               lifted: bool) -> list[tuple[int, list[str]]]:
    """`canonical`, every time. A number's cell is one string per number (`text`),
    shared by every cell that holds it, not one per cell: a Metal Harbor stream
    has five million identity cells."""
    fields = header[1:]
    cols = [(i, identity[f]) for i, f in enumerate(fields) if f in identity]
    count = {k: 0 for k in KINDS}
    live: dict[str, dict[int, int]] = {k: {} for k in KINDS}
    text: dict[int, str] = {}
    out = []
    for tick, words in rows:
        w = list(words)
        held: dict[str, set[int]] = {k: set() for k in KINDS}
        for i, kind in cols:
            v = int(w[i], 16)
            if v in NULLS[kind]:
                n = v
            elif lifted and kind == "heap" and not GUEST_HEAP[0] <= v < GUEST_HEAP[1]:
                w[i] = OUT_OF_HEAP
                continue
            else:
                held[kind].add(v)
                if v not in live[kind]:
                    count[kind] += 1
                    live[kind][v] = count[kind]
                n = live[kind][v]
            cell = text.get(n)
            if cell is None:
                cell = text[n] = f"0x{n:08x}"
            w[i] = cell
        for kind in RELEASED:
            if len(live[kind]) != len(held[kind]):
                live[kind] = {v: n for v, n in live[kind].items() if v in held[kind]}
        out.append((tick, w))
    return out


def is_value(cell: str, kind: str) -> bool:
    """Whether a canonical cell of `kind` names a value the host chose (a
    number, or OUT_OF_HEAP) rather than a null."""
    return int(cell, 16) not in NULLS[kind] or (kind == "heap" and cell == OUT_OF_HEAP)


def events(header: list[str], rows: list[tuple[int, list[str]]],
           identity: dict[str, str]) -> list[str]:
    """Every change of an identity column in a canonical stream, as
    "<tick> <column> <was> <now>", in tick and column order; before tick 1
    every column is null. What a result records of each stream, and all the
    three checks read."""
    cols = [(i, f) for i, f in enumerate(header[1:]) if f in identity]
    prev = {f: "0x00000000" for _, f in cols}
    out = []
    for tick, words in rows:
        for i, f in cols:
            if words[i] != prev[f]:
                out.append(f"{tick} {f} {prev[f]} {words[i]}")
                prev[f] = words[i]
    return out


def parse_event(text: str) -> tuple[int, str, str, str]:
    parts = str(text).split(" ")
    if len(parts) != 4 or not parts[0].isdigit() or not all(
            re.fullmatch(r"0x[0-9a-f]{8}", p) for p in parts[2:]):
        raise ValueError(f"not an identity event: {text!r}")
    return int(parts[0]), parts[1], parts[2], parts[3]


def column_kind(column: str, named: dict[str, str]) -> str | None:
    """A column's kind: a named field's from the record (`named`), else its
    prefix's (COLUMN_PREFIX)."""
    if column in named:
        return named[column]
    return next((k for k, p in COLUMN_PREFIX.items() if column.startswith(p)), None)


def identity_checks(native: list[str], lifted: list[str], columns: list[str],
                    named: dict[str, str], judged: int, shift: dict | None = None) -> dict:
    """The three checks of the one rule over ticks 1..`judged`, from each
    stream's `events` (`columns`: the identity columns in the streams' order),
    each able to fail on its own:

    * pairing: wherever both sides hold a value at a column, the native number
      and the lifted one are paired at their first meeting and every later
      meeting keeps it -- a number is never reused within a stream, so a
      pairing never has to be released; a lifted OUT_OF_HEAP pairs with nothing;
    * open: the (tick, column) at which a column leaves a null are the same on
      both sides, and so is where it goes when that is another null: a failed
      open (0 to INVALID_HANDLE_VALUE) and its reset are opens too;
    * close: the (tick, column) at which a column goes from a value to a null
      are the same on both sides, and so is the null.

    Every change of an identity column leaves a null (an open), leaves a value
    for a null (a close) or leaves a value for a value (the pairing's), so the
    three hold exactly when the two streams agree cell for cell over the
    judged ticks (the review of 2026-09-25, W2: a null read as 0 on one side
    and as INVALID_HANDLE_VALUE on the other passed all three).

    Each is {"ok", "mismatches", "first"}, `first` the earliest failing cell
    (tick, then column order) with what each side did there.

    `shift` ({"columns", "bound"}) names handle columns whose open ticks may
    shift (a host-timing record's identity_open_shift): their opens are held
    by `shifted_opens` instead -- paired in order, equal in number and kind,
    each pair at most `bound` ticks apart, or any distance when `bound` is
    None -- and the open check carries what that found (`shift`). Their
    pairing and close stay as above."""
    order = {c: k for k, c in enumerate(columns)}
    kind = {c: column_kind(c, named) for c in columns}
    unknown = sorted(c for c, k in kind.items() if k is None)
    if unknown or len(order) != len(columns):
        raise ValueError(f"identity columns of no kind, or named twice: {unknown[:4]}")
    shifted = set((shift or {}).get("columns") or [])
    if shift is not None and (not shifted or shifted - set(order)
                              or any(kind[c] != "handle" for c in shifted)):
        raise ValueError(f"open-shift columns must be handle columns of the comparison: "
                         f"{sorted(shifted - set(order))[:4] or sorted(shifted)[:4]}")
    streams = {}
    for side, evs in (("native", native), ("lifted", lifted)):
        parsed = [parse_event(e) for e in evs]
        stray = sorted({c for _, c, _, _ in parsed} - set(order))
        if stray:
            raise ValueError(f"{side} events name columns that are not identity columns: "
                             f"{stray[:4]}")
        streams[side] = [e for e in parsed if e[0] <= judged]
    # (tick, column, where it went): "" for a value, else the null's bits
    lifecycle: dict[str, dict[str, set[tuple[int, str, str]]]] = {}
    for side, evs in streams.items():
        opens, closes = set(), set()
        for t, c, was, now in evs:
            a, b = is_value(was, kind[c]), is_value(now, kind[c])
            if not a:
                opens.add((t, c, "" if b else now))
            elif not b:
                closes.add((t, c, now))
        lifecycle[side] = {"open": opens, "close": closes}
    out: dict[str, dict] = {}
    for check in ("open", "close"):
        n, lf = lifecycle["native"][check], lifecycle["lifted"][check]
        if check == "open" and shifted:
            n, lf = ({e for e in s if e[1] not in shifted} for s in (n, lf))
        bad = sorted(n ^ lf, key=lambda e: (e[0], order[e[1]], e[2]))
        first = None
        if bad:
            t, c, _ = bad[0]
            plain = "" if check == "open" else "0x00000000"     # where it usually goes
            said = {}
            for side in ("native", "lifted"):
                near = min(((x, to) for x, col, to in lifecycle[side][check] if col == c),
                           key=lambda e: (abs(e[0] - t), e[0], e[1]), default=None)
                said[side] = (f"does not {check}" if near is None else f"{check}s at {near[0]}"
                              + ("" if near[1] == plain else f" to {near[1]}"))
            first = {"tick": t, "field": c, "native": said["native"], "lifted": said["lifted"]}
        out[check] = {"ok": not bad, "mismatches": len(bad), "first": first}
    if shifted:
        moved = shifted_opens(lifecycle["native"]["open"], lifecycle["lifted"]["open"],
                              [c for c in columns if c in shifted], shift.get("bound"))
        exact = out["open"]
        firsts = [f for f in (exact["first"], moved["first"]) if f]
        out["open"] = {"ok": exact["ok"] and not moved["mismatches"],
                       "mismatches": exact["mismatches"] + moved["mismatches"],
                       "first": min(firsts, key=lambda f: (f["tick"], order[f["field"]]),
                                    default=None),
                       "shift": moved["summary"]}
    # The pairing, walked over every tick on which either side changes: a cell
    # nobody changed holds the pair it held, which was checked when it formed.
    now = {side: {c: "0x00000000" for c in order} for side in streams}
    by_tick: dict[int, list[tuple[str, str, str]]] = {}
    for side, evs in streams.items():
        for t, c, _, v in evs:
            by_tick.setdefault(t, []).append((side, c, v))
    n2l: dict[str, str] = {}
    l2n: dict[str, str] = {}
    bad_cells = []
    for t in sorted(by_tick):
        changed = set()
        for side, c, v in by_tick[t]:
            now[side][c] = v
            changed.add(c)
        for c in sorted(changed, key=order.get):
            a, b = now["native"][c], now["lifted"][c]
            k = kind[c]
            if not (is_value(a, k) and is_value(b, k)):
                continue
            if b == OUT_OF_HEAP and k == "heap":
                bad_cells.append((t, c, a, b, "the lifted value is outside the guest heap"))
                continue
            key_n, key_l = f"{k}:{a}", f"{k}:{b}"
            if n2l.get(key_n, key_l) != key_l or l2n.get(key_l, key_n) != key_n:
                was = n2l.get(key_n) or l2n.get(key_l)
                bad_cells.append((t, c, a, b, f"paired before with {was.split(':')[1]}"))
                continue
            n2l[key_n], l2n[key_l] = key_l, key_n
    first = None
    if bad_cells:
        t, c, a, b, why = bad_cells[0]
        first = {"tick": t, "field": c, "native": a, "lifted": b, "why": why}
    out["pairing"] = {"ok": not bad_cells, "mismatches": len(bad_cells), "first": first}
    return {k: out[k] for k in CHECKS}


def shifted_opens(native: set, lifted: set, columns: list[str], bound: int | None) -> dict:
    """The open check on the columns whose open ticks may shift: each side's
    opens of a column ((tick, column, where it went) as identity_checks keeps
    them) paired in order. A mismatch, at the earlier tick of its pair, is an
    open one side has and the other has not (the column's later opens are then
    not paired), a pair going to different places (a value, or which null a
    failed open writes), or a pair more than `bound` ticks apart; with `bound`
    None the distance is only measured. `summary` says how many pairs there
    were, how many moved, the widest distance and the first pair that moved."""
    per: dict[str, tuple[list, list]] = {c: ([], []) for c in columns}
    for side, opens in ((0, native), (1, lifted)):
        for t, c, to in sorted(opens):
            if c in per:
                per[c][side].append((t, to))
    bad: list[tuple] = []
    pairs = 0
    moved: list[tuple[int, str]] = []
    widest = 0
    for c in columns:
        a, b = per[c]
        for i in range(max(len(a), len(b))):
            if i >= len(a) or i >= len(b):
                t, _ = (a if i < len(a) else b)[i]
                bad.append((t, c, f"opens at {t}" if i < len(a) else f"has no open {i + 1}",
                            f"opens at {t}" if i >= len(a) else f"has no open {i + 1}"))
                break
            (tn, kn), (tl, kl) = a[i], b[i]
            pairs += 1
            if kn != kl:
                bad.append((min(tn, tl), c, f"opens at {tn}" + (f" to {kn}" if kn else ""),
                            f"opens at {tl}" + (f" to {kl}" if kl else "")))
                continue
            if tn != tl:
                moved.append((min(tn, tl), c))
                widest = max(widest, abs(tn - tl))
            if bound is not None and abs(tn - tl) > bound:
                bad.append((min(tn, tl), c, f"opens at {tn}",
                            f"opens at {tl}, {abs(tn - tl)} ticks apart and the bound is {bound}"))
    col = {c: k for k, c in enumerate(columns)}
    bad.sort(key=lambda e: (e[0], col[e[1]]))
    first = moved and min(moved, key=lambda e: (e[0], col[e[1]]))
    return {"mismatches": len(bad),
            "first": ({"tick": bad[0][0], "field": bad[0][1], "native": bad[0][2],
                       "lifted": bad[0][3]} if bad else None),
            "summary": {"columns": len(columns), "bound": bound, "pairs": pairs,
                        "shifted": len(moved), "widest": widest,
                        "first_shifted": {"tick": first[0], "field": first[1]} if first else None}}


def event_problems(evs: list[str], named: dict[str, str]) -> list[str]:
    """Why a recorded stream is not one `canonical` writes, [] when it is: per
    kind, numbers appear first in order 1, 2, 3 ..., and, for the kinds in
    RELEASED, a number no column of its kind held at the end of a tick never
    comes back (the release)."""
    out = []
    try:
        parsed = [parse_event(e) for e in evs]
    except ValueError as e:
        return [str(e)]
    held: dict[str, dict[str, str]] = {}     # kind -> column -> number held
    top = {k: 0 for k in KINDS}
    gone: dict[str, set[int]] = {k: set() for k in KINDS}
    prev_tick = 0
    for t, c, was, now in parsed + [(None, "", "", "")]:
        if t != prev_tick:
            # the end of tick prev_tick: what no column of its kind holds is released
            for k in RELEASED:
                alive = {int(v, 16) for v in held.get(k, {}).values()}
                gone[k] |= set(range(1, top[k] + 1)) - alive
            if t is None:
                break
            if t < prev_tick:
                return out + [f"the events are not in tick order at {t}"]
            prev_tick = t
        k = column_kind(c, named)
        if k is None:
            return out + [f"{c} is no identity column"]
        if held.get(k, {}).get(c, "0x00000000") != was:
            out.append(f"tick {t} {c}: the event says it held {was}, and it held "
                       f"{held.get(k, {}).get(c, '0x00000000')}")
        held.setdefault(k, {})[c] = now
        if not is_value(now, k) or now == OUT_OF_HEAP:
            continue
        n = int(now, 16)
        if n > top[k] + 1:
            out.append(f"tick {t} {c}: number {n} of kind {k} before number {top[k] + 1}")
        elif n == top[k] + 1:
            top[k] = n
        elif n in gone[k]:
            out.append(f"tick {t} {c}: number {n} of kind {k} comes back after its release")
    return out


def write_capture(path: Path, header: list[str], rows: list[tuple[int, list[str]]]) -> None:
    with path.open("w", newline="") as stream:
        w = csv.writer(stream, lineterminator="\n")
        w.writerow(header)
        for tick, words in rows:
            w.writerow([str(tick)] + words)


# Omochao Split's report is a function of the two streams alone, and parsing them
# is most of a recompute: a Metal Harbor replay with sixteen repeats spent 240 of
# its 267 seconds there, once per repeat, every time the replay gate recomputed it
# (the U6 fold). So a report is kept per process by the sha256 of both streams as
# the comparer reads them (the header and every row, `stream_digest`), and a
# refusal is asked again.
_SPLITS: dict[str, dict] = {}


def stream_digest(header: list[str], rows: list[tuple[int, list[str]]]) -> str:
    h = hashlib.sha256(",".join(header).encode())
    h.update("".join(f"\n{t}," + ",".join(w) for t, w in rows).encode())
    return h.hexdigest()


def split(header: list[str], left: list[tuple[int, list[str]]],
          right: list[tuple[int, list[str]]], work: Path, label: str) -> dict:
    """Omochao Split over two normalised streams carrying the same ticks; kept
    per process by both streams' content (the comment above)."""
    key = stream_digest(header, left) + stream_digest(header, right)
    if key in _SPLITS:
        return json.loads(json.dumps(_SPLITS[key]))
    lp, rp = work / f"{label}.left.csv", work / f"{label}.right.csv"
    write_capture(lp, header, left)
    write_capture(rp, header, right)
    try:
        code, report = SPLIT.compare(SPLIT.load_stream(lp, [IDENTITY]),
                                     SPLIT.load_stream(rp, [IDENTITY]), [IDENTITY])
    except SPLIT.StreamError as error:
        raise refuse(f"Omochao Split refused the {label} streams: "
                     f"{json.dumps(error.report, sort_keys=True)}") from None
    _SPLITS[key] = json.loads(json.dumps(report))
    return report


# --------------------------------------------------------- word-log streams

def mask_for(entry: dict, root: Path | None = None) -> Path:
    """The digest mask a word-policy entry's columns are recomputed under: its
    own harness/replay/masks/<name>.h once U6 has generated it, else the
    tree's tick_digest.h, the boot's mask before word logs."""
    root = ROOT if root is None else root
    own = root / MASKS_DIR / f"{entry.get('name')}.h"
    return own if own.is_file() else root / BOOT_MASK


def identity_record(root: Path | None = None) -> dict[int, str]:
    """address -> kind, for every word the identity record names, read by the
    mask tool's own parser (tools/s4/snapshot_fields.py identity_words), so the
    words the mask leaves out and the words compared by identity are one list."""
    import snapshot_fields as SF      # here: it is big, and only a word-policy entry needs it
    root = ROOT if root is None else root
    try:
        words = SF.identity_words(root / IDENTITY_RECORD)
    except (SystemExit, OSError, ValueError) as e:
        raise refuse(f"the identity record {IDENTITY_RECORD} does not read: {e}") from None
    out = {}
    for va, what in words.items():
        kind = str(what).split(":", 1)[0].strip()
        if kind not in KINDS:
            raise refuse(f"{IDENTITY_RECORD}: {va:#010x} is of kind {kind!r}, none of {KINDS}")
        out[va] = kind
    return out


def word_policy_problem(entry: dict) -> str | None:
    """Why an entry's word_policy is not one the comparer reads, None when it
    is: it names the identity record (identity), says it is compared by
    identity (compare) and carries the ruling it rests on (evidence)."""
    wp = entry.get("word_policy")
    if not isinstance(wp, dict) or wp.get("identity") != IDENTITY_RECORD \
            or wp.get("compare") != "identity" or not str(wp.get("evidence", "")).strip():
        return (f"{entry.get('name')}: its word_policy must name the identity record "
                f"{IDENTITY_RECORD} (identity), say it is compared by identity (compare) and "
                f"carry the ruling it rests on (evidence); it is {wp!r}")
    return None


def identity_spec(entry: dict, root: Path | None = None) -> dict:
    """What a word-policy entry compares by identity: the record (its path and
    sha256), the named fields at an identity word with their kinds, and one
    column per identity word no named field holds, in address order. The
    entry's field policy must name exactly the named fields the record names:
    one rule, one record (the user's decision 2)."""
    root = ROOT if root is None else root
    name = entry.get("name")
    why = word_policy_problem(entry)
    if why:
        raise refuse(why)
    record = identity_record(root)
    fields = WL.named_fields(root / "harness" / "replay" / "tick_fields.h")
    at = {va: (n, width) for n, va, width in fields}
    narrow = sorted(n for va, (n, width) in at.items() if va in record and width != 4)
    if narrow:
        raise refuse(f"{name}: the named fields {narrow} sit at identity words and are not one "
                     "word wide")
    named = {n: record[va] for n, va, width in fields if va in record}
    policy = {f for f, p in (entry.get("field_policy") or {}).items()
              if p.get("compare") == "identity"}
    if policy != set(named):
        raise refuse(f"{name}: the field policy compares {sorted(policy)} by identity and the "
                     f"identity record's named fields are {sorted(named)}: one rule reads one "
                     "record")
    extra = [(f"{COLUMN_PREFIX[k]}{va:08x}", va, k) for va, k in sorted(record.items())
             if va not in at]
    return {"record": {"path": IDENTITY_RECORD, "sha256": must_sha256(root / IDENTITY_RECORD)},
            "named": named, "extra": extra,
            "columns": dict(named) | {c: k for c, _, k in extra}}


# ----------------------------------------------------------- residue classes

def table_words(table: dict) -> list[int]:
    """The words a group's table definition gives, in address order: `at`, a
    list of addresses, or `base`, `stride`, `count` and `offsets` (a list of
    offsets, or {"from", "to", "step"} with both ends included). ValueError
    when it is neither, or gives no word."""
    if not isinstance(table, dict):
        raise ValueError(f"a table definition is an object, not {table!r}")
    if "at" in table:
        if set(table) != {"at"} or not isinstance(table["at"], list):
            raise ValueError(f"an `at` table is a list of addresses alone: {table!r}")
        words = [int(str(a), 16) for a in table["at"]]
    else:
        if set(table) != {"base", "stride", "count", "offsets"}:
            raise ValueError(f"a table is base, stride, count and offsets: {sorted(table)}")
        base, stride, count = int(str(table["base"]), 16), int(str(table["stride"]), 16), \
            table["count"]
        offs = table["offsets"]
        if isinstance(offs, dict):
            if set(offs) != {"from", "to", "step"} or not isinstance(offs["step"], int) \
                    or offs["step"] < 1:
                raise ValueError(f"an offset range is from, to and a positive step: {offs!r}")
            offs = list(range(int(str(offs["from"]), 16), int(str(offs["to"]), 16) + 1,
                              offs["step"]))
        elif isinstance(offs, list):
            offs = [int(str(o), 16) for o in offs]
        else:
            raise ValueError(f"offsets are a list or a range, not {offs!r}")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1 or stride < 1 \
                or any(not 0 <= o < stride for o in offs):
            raise ValueError(f"a table of {count!r} entries of {stride:#x} bytes cannot hold "
                             f"offsets {[hex(o) for o in offs]}")
        words = [base + k * stride + o for k in range(count) for o in offs]
    if not words or len(set(words)) != len(words):
        raise ValueError(f"the table gives no word, or one word twice: {table!r}")
    return sorted(words)


def normal_text(text: str) -> str:
    """Text as a quote is looked for in it: a line's Markdown quote marks and
    list bullet dropped, and every run of whitespace one space."""
    lines = [re.sub(r"^\s*(?:>\s?)*(?:[-*]\s+)?", "", ln) for ln in text.splitlines()]
    return " ".join(" ".join(lines).split())


def quoted_in(ref: dict, root: Path, where: str) -> list[str]:
    """Why `ref` ({"record": path, "quote": text}) does not quote its record,
    [] when the quote is in it (normal_text on both)."""
    if not isinstance(ref, dict) or not str(ref.get("quote", "")).strip():
        return [f"{where} quotes nothing (record, quote)"]
    path = root / str(ref.get("record") or "")
    if not ref.get("record") or not path.is_file():
        return [f"{where} names {ref.get('record')!r}, which does not exist"]
    if normal_text(ref["quote"]) not in normal_text(path.read_text()):
        return [f"{where}'s quote is not in {ref['record']}: {ref['quote'][:80]!r}"]
    return []


def gameplay_words(root: Path | None = None) -> dict[int, str]:
    """Every byte a residue word may not touch, and what it is: each named
    field of harness/replay/tick_fields.h, its whole width; each address the
    stage probes read (STAGE_SOURCES' #defines at or above the image base:
    the stage trace's level, mode, state, frame count and player object, the
    input probe's goal latch and its PDS_PERIPHERAL base), four bytes wide;
    and each field of the input the input probe applies (RAW0 plus its off[]
    and width[]). Design 7.4 promotes a residue that changes gameplay-visible
    state to a bug; these are the state the replay shows, and the input it
    is driven by."""
    root = ROOT if root is None else root
    out: dict[int, str] = {}
    for n, va, width in WL.named_fields(root / "harness" / "replay" / "tick_fields.h"):
        for b in range(va, va + width):
            out[b] = f"the named field {n}"
    for rel in STAGE_SOURCES:
        source = root / rel
        if not source.is_file():
            raise refuse(f"{rel} does not exist, so the stage probes' sources cannot be kept "
                         "out of a residue class")
        text = source.read_text()
        defines = dict(DEFINE.findall(text))
        for name, value in defines.items():
            va = int(value, 16)
            if va >= IMAGE_BASE:
                for b in range(va, va + 4):
                    out.setdefault(b, f"{STAGE_SOURCE_WHAT[rel]} {name} ({rel})")
        arrays = {k: [int(x.strip(), 0) for x in v.split(",") if x.strip()]
                  for k, v in PERIPHERAL.findall(text)}
        if "RAW0" in defines:
            if len(arrays.get("off", [])) != len(arrays.get("width", [])) or not arrays.get("off"):
                raise refuse(f"{rel} names RAW0 and its off[]/width[] do not read, so the input "
                             "it applies cannot be kept out of a residue class")
            raw = int(defines["RAW0"], 16)
            for o, w in zip(arrays["off"], arrays["width"]):
                for b in range(raw + o, raw + o + w):
                    out.setdefault(b, f"the input probe's field RAW0+{o:#x} ({rel})")
    return out


def table_entries(table: dict) -> list[list[int]] | None:
    """A base/stride/count table's words entry by entry, each entry's in offset
    order (the copies of a one-tick-scan, the contexts of a context-permutation);
    None for an `at` table. ValueError as `table_words`."""
    words = table_words(table)
    if "at" in table:
        return None
    per = len(words) // table["count"]
    return [words[k * per:(k + 1) * per] for k in range(table["count"])]


def is_count(value, low: int = 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def native_params(rule: dict, table: dict, effect: dict | None = None) -> dict:
    """A native rule's form and parameters as the comparer records them beside its
    verdict, with how many entries its table has: what `residue_problems` holds
    the recorded rule to against the record as it is now. A context-permutation
    rule whose record names an effect with the uploads it moves (`effect`: its
    frame word and `moved`, each upload's frame and its lifted and native ticks)
    also holds the uploads to that list (`uploads`)."""
    form = rule.get("form")
    out = {"form": form, **{k: rule.get(k) for k in NATIVE_FORMS.get(form, ())}}
    out["entries"] = len(table_entries(table) or [])
    if form == "context-permutation" and isinstance(effect, dict) and "moved" in effect:
        out["uploads"] = {"word": effect.get("word"),
                          "moved": [[m.get("frame"), m.get("lifted"), m.get("native")]
                                    for m in effect.get("moved") or []
                                    if isinstance(m, dict)]}
    return out


def native_rule_problem(rule: dict, words: list[int], table) -> str | None:
    """Why a native-relative rule is not one `evaluate_native` reads, None when
    it is (NATIVE_FORMS)."""
    form = rule.get("form")
    if form not in NATIVE_FORMS:
        return f"its native rule's form is not one of {sorted(NATIVE_FORMS)}: {form!r}"
    try:
        entries = table_entries(table)
    except (ValueError, TypeError, KeyError):
        entries = None
    if not entries or len(entries) < 2:
        return (f"its {form} rule reads its table's entries, and the table is not one of two "
                "or more entries (base, stride, count and offsets)")
    if form == "one-tick-scan":
        if not is_count(rule.get("max_ticks")):
            return "its one-tick-scan rule names no max_ticks (a count of ticks)"
        try:
            reset, progress = int(str(rule.get("reset")), 16), int(str(rule.get("progress")), 16)
        except ValueError:
            return f"its one-tick-scan rule's reset or progress does not read: {rule!r}"
        if not 0 <= reset <= 0xFFFFFFFF or progress % 4 or progress in words:
            return ("its one-tick-scan rule's reset is not a word's value, or its progress is "
                    "not a word outside its group")
    else:
        n = len(entries)
        if not (is_count(rule.get("dispatcher"), 0) and rule["dispatcher"] < n):
            return f"its context-permutation rule's dispatcher is not an entry of its table's {n}"
        if not is_count(rule.get("kept_from")) or not is_count(rule.get("episode_max")):
            return "its context-permutation rule names no kept_from tick or episode_max count"
    return None


def rule_problem(rule: dict, words: list[int], table=None) -> str | None:
    """Why a group's lifted_rule is not one `evaluate_rule` reads, None when it
    is: zero, below (a limit for words of the group), none with why, or native
    (`native_rule_problem`)."""
    if not isinstance(rule, dict) or rule.get("kind") not in RESIDUE_RULES:
        return f"its lifted_rule is not one of {RESIDUE_RULES}: {rule!r}"
    if rule["kind"] == "native":
        why = native_rule_problem(rule, words, table)
        if why:
            return why
    if rule["kind"] == "below":
        limits = rule.get("limits")
        if not isinstance(limits, dict) or not limits:
            return "its below rule names no limits"
        try:
            bad = [a for a, v in limits.items() if int(a, 16) not in words
                   or not isinstance(v, int) or isinstance(v, bool) or v < 1]
        except ValueError:
            return f"its below rule's limits do not read: {limits!r}"
        if bad:
            return f"its below rule limits words it does not list, or not by a count: {bad}"
    if rule["kind"] == "none" and not str(rule.get("why", "")).strip():
        return "its lifted_rule is none and says not why no rule holds"
    if rule["kind"] != "none" and not str(rule.get("text", "")).strip():
        return "its lifted_rule does not say what it holds (text)"
    return None


def evidence_problems(evidence, root: Path, where: str) -> list[str]:
    """Why a list of evidence does not stand, [] when it does: a non-empty list
    of strings, each beginning with a repository path that exists."""
    if not isinstance(evidence, list) or not evidence:
        return [f"{where} lists no evidence"]
    out = []
    for item in evidence:
        head = str(item).split(" ", 1)[0]
        if not head or not (root / head).exists():
            out.append(f"{where} cites {head!r}, which does not exist")
    return out


def record_problems(rec: dict, rid: str, root: Path | None = None,
                    identity: dict[int, str] | None = None) -> list[str]:
    """Why a residue class record is not one a comparison may apply, [] when
    it is (the module docstring):

    * its schema, its id (its file's name) and a class the user ruled;
    * the user's ruling quoted from its record, and design 7.4's rule;
    * groups, each generated from its table definition (the words it lists are
      exactly the ones the definition gives), with its owner, why it is not
      gameplay, a lifted_rule `evaluate_rule` reads, evidence that exists and
      what would retire it; a group may name the replays it applies to
      (`replays`, `applies`), and one whose rule is native-relative must, since
      that rule was measured on one replay's runs;
    * no word listed twice, none a named field, a stage probe's source or a
      field of the input the input probe applies (`gameplay_words`: a residue
      that changes a gameplay value is a bug), and
      none compared by identity (`identity`: address -> kind);
    * an identity_open_shift, only in a host-timing record, naming handle words
      of the identity record alone, generated from its table as a group is;
    * the host-thread-race class's scope (`thread_race_problems`): its one record
      is THREAD_RACE_SCOPE exactly and names its observable effect, and no record
      of another class lists a word of it."""
    root = ROOT if root is None else root
    where = f"residue class {rid}"
    if not isinstance(rec, dict):
        return [f"{where} is not a JSON object"]
    out = []
    if rec.get("schema") != RESIDUE_SCHEMA or rec.get("id") != rid:
        out.append(f"{where}: its schema is {rec.get('schema')!r} and its id {rec.get('id')!r}, "
                   f"not {RESIDUE_SCHEMA} and {rid}")
    if rec.get("class") not in RESIDUE_CLASSES:
        out.append(f"{where}: its class {rec.get('class')!r} is none the user ruled "
                   f"({', '.join(RESIDUE_CLASSES)})")
    ruling = rec.get("ruling") or {}
    if not str(ruling.get("record", "")).startswith(DECISIONS):
        out.append(f"{where}: its ruling cites {ruling.get('record')!r}, not a decision record "
                   f"under {DECISIONS}")
    out += quoted_in(ruling, root, f"{where}: its ruling")
    out += quoted_in(rec.get("design"), root, f"{where}: its design rule")
    identity = identity_record(root) if identity is None else identity
    gameplay = gameplay_words(root)
    seen: dict[int, str] = {}
    groups = rec.get("groups")
    if not isinstance(groups, list) or not groups:
        return out + [f"{where} lists no groups"]
    for g in groups:
        name = g.get("name") if isinstance(g, dict) else None
        gw = f"{where} group {name!r}"
        if not isinstance(name, str) or not NAME.match(name):
            out.append(f"{gw}: its name must match {NAME.pattern}")
            continue
        try:
            words = table_words(g.get("table"))
        except (ValueError, TypeError) as e:
            out.append(f"{gw}: its table definition does not read: {e}")
            continue
        listed = g.get("words")
        if listed != [f"0x{va:08x}" for va in words]:
            out.append(f"{gw}: the words it lists are not the ones its table definition gives "
                       f"({len(words)} words, first 0x{words[0]:08x})")
        for key in ("owner", "why_not_gameplay", "retire_when"):
            if len(str(g.get(key, "")).split()) < 5:
                out.append(f"{gw}: it does not say {key}")
        why = rule_problem(g.get("lifted_rule"), words, g.get("table"))
        if why:
            out.append(f"{gw}: {why}")
        scope = g.get("replays")
        if scope is not None and (not isinstance(scope, list) or not scope
                                  or len(set(map(str, scope))) != len(scope)
                                  or not all(isinstance(r, str) and NAME.match(r)
                                             for r in scope)):
            out.append(f"{gw}: its replays (the replays it applies to) are not a list of "
                       f"replay names, none twice: {scope!r}")
        if (g.get("lifted_rule") or {}).get("kind") == "native" and scope is None:
            out.append(f"{gw}: its native rule was measured on one replay's runs, so the group "
                       "must name the replays it applies to (replays)")
        out += evidence_problems(g.get("evidence"), root, gw)
        for va in words:
            if va % 4:
                out.append(f"{gw}: 0x{va:08x} is not a word's address")
            if va in seen:
                out.append(f"{gw}: 0x{va:08x} is listed by {seen[va]} too")
            seen[va] = f"group {name!r}"
            touch = sorted({gameplay[b] for b in range(va, va + 4) if b in gameplay})
            if touch:
                out.append(f"{gw}: 0x{va:08x} is {', '.join(touch)}: a residue that changes a "
                           "gameplay value is a bug, and a word the replay's gameplay state is "
                           "read from cannot be listed as one")
            if va in identity:
                out.append(f"{gw}: 0x{va:08x} is compared by what it names "
                           f"({IDENTITY_RECORD}); only an identity_open_shift reaches such a "
                           "word, and only its open ticks")
    out += thread_race_problems(rec, rid, root)
    shift = rec.get("identity_open_shift")
    if shift is not None:
        sw = f"{where}'s identity_open_shift"
        if rec.get("class") != "host-timing":
            out.append(f"{sw}: file-open ticks are host timing, and this is a "
                       f"{rec.get('class')} record")
        try:
            words = table_words((shift or {}).get("table"))
        except (ValueError, TypeError) as e:
            return out + [f"{sw}: its table definition does not read: {e}"]
        if shift.get("words") != [f"0x{va:08x}" for va in words]:
            out.append(f"{sw}: the words it lists are not the ones its table definition gives")
        stray = [f"0x{va:08x}" for va in words if identity.get(va) != "handle"]
        if stray:
            out.append(f"{sw}: {stray[:4]} are not handle words of {IDENTITY_RECORD}")
        for key in ("rule", "bound", "retire_when"):
            if len(str(shift.get(key, "")).split()) < 5:
                out.append(f"{sw}: it does not say {key}")
        out += evidence_problems(shift.get("evidence"), root, sw)
    return out


def thread_race_problems(rec: dict, rid: str, root: Path | None = None) -> list[str]:
    """Why a record breaks the host-thread-race class's scope, [] when it keeps it
    (THREAD_RACE_SCOPE; the user's ruling of 2026-09-27, conditions 1 and 2):

    * a record of the class is the scope's one id, cites the ruling, and holds one
      group: the scope's name, table (so its 244 words), replays and routines, and
      the context-permutation rule with the scope's parameters; another record of
      the class is a race recorded anywhere else under it, and is refused whatever
      it holds;
    * the record names its one observable effect (`effect`: what it is, the word it
      was read from, which is the scope's frame word, the ticks on each side, and
      evidence that exists), and lists every upload the lifted run makes on another
      tick than the capture (`moved`): exactly one of them this group's, the named
      effect itself, and each other one owned by a group of another record, which
      the rule holds the comparison to on every pass (`uploads_verdict`); the named
      effect is the scope's (`effect`: its frame and both ticks, on the scope's
      capture) and the list is the scope's (`moved`, owners included), so an edit
      that keeps the record consistent with itself is refused as surely as one that
      does not (the G4 interrogation, Reviewer B's finding 2);
    * a record of any other class lists no word of the four contexts: neither one
      of the 244, which are this class's alone, nor any other word of the contexts'
      span, whose listing anywhere would be a change of this class's scope (the
      race comparer review's finding 3)."""
    root = ROOT if root is None else root
    scope = THREAD_RACE_SCOPE
    where = f"residue class {rid}"
    ruled = set(table_words(scope["table"]))
    t = scope["table"]
    lo = int(t["base"], 16)
    hi = lo + int(t["stride"], 16) * t["count"]
    if rec.get("class") != THREAD_RACE_CLASS:
        words = sorted({va for g in rec.get("groups") or [] if isinstance(g, dict)
                        for va in _words_or_none(g.get("table"))})
        listed = [va for va in words if va in ruled]
        span = [va for va in words if lo <= va < hi and va not in ruled]
        out = []
        if listed:
            out.append(f"{where}: {', '.join(f'0x{va:08x}' for va in listed[:4])} "
                       f"{'is' if len(listed) == 1 else 'are'} Sofdec's decoder-context race, "
                       f"which is the {THREAD_RACE_CLASS} class's alone ({scope['ruling']})")
        if span:
            out.append(f"{where}: {', '.join(f'0x{va:08x}' for va in span[:4])} "
                       f"{'is a word' if len(span) == 1 else 'are words'} of Sofdec's four "
                       f"decoder contexts (0x{lo:08x} to 0x{hi:08x}), whose race is the "
                       f"{THREAD_RACE_CLASS} class's alone: listing another of their words "
                       "anywhere is a change of that class's scope (THREAD_RACE_SCOPE), reviewed "
                       f"as a comparer change ({scope['ruling']})")
        return out
    if rid != scope["id"]:
        return [f"{where}: a {THREAD_RACE_CLASS} record other than {scope['id']}: the class "
                "holds Sofdec's decoder-context race alone, and a race anywhere else is a bug "
                f"until reviewed ({scope['ruling']}, condition 1)"]
    out = []
    own = f"{scope['id']}/{scope['group']}"
    if (rec.get("ruling") or {}).get("record") != scope["ruling"]:
        out.append(f"{where}: its ruling is not {scope['ruling']}")
    groups = rec.get("groups")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict):
        out.append(f"{where}: it holds one group, {scope['group']}, and it lists "
                   f"{len(groups) if isinstance(groups, list) else 'no'} "
                   "(a group added to the class is refused)")
    else:
        g = groups[0]
        for key, what in (("name", "its name is"), ("table", "its words (table) are"),
                          ("replays", "its replays are"), ("routines", "its owner's routines are")):
            want = scope["group"] if key == "name" else scope[key]
            if g.get(key) != want:
                out.append(f"{where} group {g.get('name')!r}: {what} not the ruled scope's "
                           f"({json.dumps(want)[:60]}): the class holds that race alone")
        owner = str(g.get("owner", ""))
        missing = [r for r in scope["routines"] if r not in owner]
        if missing:
            out.append(f"{where} group {g.get('name')!r}: its owner does not name {missing}")
        rule = g.get("lifted_rule") or {}
        if rule.get("kind") != "native" or rule.get("form") != scope["form"]:
            out.append(f"{where} group {g.get('name')!r}: its lifted rule is not the "
                       f"{scope['form']} rule the class is held to")
        else:
            edited = {k: rule.get(k) for k in scope["rule"] if rule.get(k) != scope["rule"][k]}
            if edited:
                out.append(f"{where} group {g.get('name')!r}: its lifted rule's "
                           f"{', '.join(f'{k} {v!r}' for k, v in edited.items())} "
                           f"{'is' if len(edited) == 1 else 'are'} not the ruled scope's "
                           f"({json.dumps(scope['rule'])}): the rule's parameters are the "
                           "comparer's, reviewed as a comparer change")
    effect = rec.get("effect")
    if not isinstance(effect, dict) or len(str(effect.get("text", "")).split()) < 5 \
            or not isinstance(effect.get("word"), str) or not effect.get("lifted") \
            or not effect.get("native"):
        out.append(f"{where}: it does not name its one observable effect (effect: text, word, "
                   f"lifted, native; {scope['ruling']}, condition 2)")
        return out
    out += evidence_problems(effect.get("evidence"), root, f"{where}'s effect")
    if effect.get("word") != scope["uploads_word"]:
        out.append(f"{where}'s effect is read from {effect.get('word')!r}, not the scope's frame "
                   f"word {scope['uploads_word']}")
    moved = effect.get("moved")
    named = [effect.get("frame"), (effect.get("lifted") or {}).get("tick"),
             (effect.get("native") or {}).get("tick")]
    if not isinstance(moved, list) or not moved or not all(
            isinstance(m, dict) and all(is_count(m.get(k)) for k in ("frame", "lifted", "native"))
            and m["lifted"] != m["native"] and isinstance(m.get("owner"), str) for m in moved):
        out.append(f"{where}'s effect does not list the uploads the lifted run makes on another "
                   "tick than the capture (moved: frame, lifted and native ticks, apart, and the "
                   "group whose difference each is)")
        return out
    pin = scope["effect"]
    if named != [pin["frame"], pin["lifted"], pin["native"]] or \
            (effect.get("native") or {}).get("capture") != pin["capture"]:
        out.append(f"{where}'s effect names frame {named[0]}'s upload at {named[1]} lifted and "
                   f"{named[2]} native on {(effect.get('native') or {}).get('capture')!r}, and the "
                   f"ruled scope's is frame {pin['frame']}'s at {pin['lifted']} and "
                   f"{pin['native']} on {pin['capture']!r}: the named effect is the comparer's, "
                   f"measured again and reviewed as a comparer change ({scope['ruling']}, "
                   "condition 2)")
    listed = [[m["frame"], m["lifted"], m["native"], m["owner"]] for m in moved]
    if listed != scope["moved"]:
        out.append(f"{where}'s effect lists {listed} as the uploads the lifted run moves, and "
                   f"the ruled scope lists {scope['moved']}: which uploads may move, and whose "
                   "each is, is the comparer's, never a record edit")
    mine = [[m["frame"], m["lifted"], m["native"]] for m in moved if m["owner"] == own]
    if mine != [named]:
        out.append(f"{where}'s effect lists {mine} as this group's moved uploads, and the one "
                   f"observable effect it names is {named} (frame, lifted tick, native tick): "
                   f"condition 2 names one ({scope['ruling']})")
    for m in moved:
        if m["owner"] == own:
            continue
        other, _, gname = m["owner"].partition("/")
        path = root / "Documentation" / "receipts" / "residues" / f"{other}.json"
        try:
            names = [x.get("name") for x in json.loads(path.read_text()).get("groups") or []]
        except (OSError, ValueError, AttributeError):
            names = []
        if other == scope["id"] or not NAME.match(other or "") or gname not in names:
            out.append(f"{where}'s effect gives frame {m['frame']}'s moved upload to "
                       f"{m['owner']!r}, which is no group of another residue class record")
    return out


def _words_or_none(table) -> list[int]:
    try:
        return table_words(table)
    except (ValueError, TypeError, KeyError):
        return []


def residue_record(rid: str, root: Path | None = None) -> dict:
    """A residue class record by id, read; refuses when it is not on disk or
    not JSON. Only Documentation/receipts/residues/<id>.json is read: a group
    held for the user's ruling (Documentation/receipts/residues/held/) is no
    class and no comparison reads it as one; its words are judged only where a
    replay's mask selects them."""
    root = ROOT if root is None else root
    path = root / "Documentation" / "receipts" / "residues" / f"{rid}.json"
    if not isinstance(rid, str) or not NAME.match(rid) or not path.is_file():
        raise refuse(f"no residue class record Documentation/receipts/residues/{rid}.json")
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise refuse(f"residue class {rid} is not JSON: {e}") from None


def residue_classes(entry: dict, root: Path | None = None) -> list[dict]:
    """The residue classes an entry's comparison applies (its registry
    `residue_classes`, ids of records), each read and held to record_problems
    and carrying the entry's name (`replay`, which `residue_groups` scopes
    groups by); [] when it names none. Refused: a class on an entry compared
    without word logs, a class named twice, and a word two classes list."""
    root = ROOT if root is None else root
    name = entry.get("name")
    ids = entry.get(RESIDUE_FIELD)
    if ids is None:
        return []
    if "word_policy" not in entry:
        raise refuse(f"{name}: names residue classes, which are words of both sides' word logs, "
                     "and has no word_policy")
    if not isinstance(ids, list) or not ids or len(set(map(str, ids))) != len(ids):
        raise refuse(f"{name}: its {RESIDUE_FIELD} is a list of record ids, none twice: {ids!r}")
    identity = identity_record(root)
    out, seen = [], {}
    for rid in ids:
        rec = residue_record(rid, root)
        problems = record_problems(rec, rid, root, identity)
        if problems:
            raise refuse(f"{name}: " + "; ".join(problems))
        if rec["class"] == THREAD_RACE_CLASS and name not in THREAD_RACE_SCOPE["replays"]:
            raise refuse(f"{name}: names {rid}, whose class holds Sofdec's decoder-context race "
                         f"on {THREAD_RACE_SCOPE['replays']} alone ({THREAD_RACE_SCOPE['ruling']})")
        for g in rec["groups"]:
            for va in table_words(g["table"]):
                if va in seen:
                    raise refuse(f"{name}: 0x{va:08x} is listed by {seen[va]} and by {rid}")
                seen[va] = rid
        out.append({"id": rid, "class": rec["class"], "record": rec, "replay": name})
    return out


def applies(group: dict, replay) -> bool:
    """Whether a record's group applies to `replay`: every group does unless it
    names the replays it applies to (`replays`), and then only to those. A
    group scoped to a replay is that replay's alone: in every other replay's
    comparison its words stay judged wherever that replay's mask selects them."""
    scope = group.get("replays")
    return scope is None or replay in scope


def residue_groups(classes: list[dict]) -> list[dict]:
    """Every group of `classes` that applies to the replay each was read for
    (`applies`; `residue_classes` names it), in record then group order: its
    record's id, its name, its words, its table and its lifted rule, and its
    record's named effect when it has one (`native_params`)."""
    return [dict({"record": c["id"], "group": g["name"], "words": table_words(g["table"]),
                  "table": g["table"], "rule": g["lifted_rule"]},
                 **({"effect": c["record"]["effect"]} if "effect" in c["record"] else {}))
            for c in classes for g in c["record"]["groups"] if applies(g, c.get("replay"))]


def residue_words(classes: list[dict]) -> set[int]:
    return {va for g in residue_groups(classes) for va in g["words"]}


def open_shift_columns(classes: list[dict], spec: dict | None = None) -> list[str]:
    """The identity columns whose open ticks may shift (a host-timing record's
    identity_open_shift), in address order; each must be an identity column of
    `spec` of kind handle."""
    cols = sorted(f"{COLUMN_PREFIX['handle']}{va:08x}" for c in classes
                  for va in (table_words(c["record"]["identity_open_shift"]["table"])
                             if c["record"].get("identity_open_shift") else []))
    if spec is not None:
        stray = [c for c in cols if spec["columns"].get(c) != "handle"]
        if stray:
            raise refuse(f"the open-shift columns {stray[:4]} are not handle columns of the "
                         "identity comparison")
    return cols


def effective_mask(entry: dict, spec: dict, out_dir: Path,
                   root: Path | None = None,
                   classes: list[dict] | None = None) -> tuple[Path, dict]:
    """The replay's mask (`mask_for`) less every identity word and every word
    of the residue classes `classes` (the comparer passes the entry's,
    `residue_classes`; none when not given, so a caller that holds native runs
    to each other under the mask, tools/s4/native_imports.py word_captures,
    still compares every mask word): a value the host chooses leaves the
    digests and is compared by what it names, and a residue word leaves them
    and is reported by its class (`residue_report`). A word-log mask leaves the identity words out already
    (snapshot_fields.py's rule); the boot's snapshot mask held entry 0's four
    handle words. When nothing is left out the mask is used as it is, else a
    copy without them is written to `out_dir`. Returns the header the reader is
    built against and what the result records of it."""
    import snapshot_fields as SF
    root = ROOT if root is None else root
    classes = [] if classes is None else classes
    mask = mask_for(entry, root)
    words = OM.read_mask_words(mask)
    record = {va for c, va, _ in spec["extra"]} | {
        va for n, va, _ in WL.named_fields(root / "harness" / "replay" / "tick_fields.h")
        if n in spec["named"]}
    residue = (words - record) & residue_words(classes)
    drop = sorted((words & record) | residue)
    info = {"mask": str(mask.relative_to(root)), "mask_sha256": must_sha256(mask),
            "identity_words_removed": len(words & record), "words": len(words) - len(drop)}
    if classes:
        info["residue_words_removed"] = len(residue)
    if not drop:
        return mask, dict(info, effective_sha256=info["mask_sha256"])
    out_dir.mkdir(parents=True, exist_ok=True)
    text_dir = Path(tempfile.mkdtemp(prefix="mask-", dir=out_dir))
    out = text_dir / "tick_digest.h"
    held = (f" * of {IDENTITY_RECORD} (sha256 {spec['record']['sha256']}) it held: written by"
            if not classes else
            f" * of {IDENTITY_RECORD} (sha256 {spec['record']['sha256']}) it held and the "
            f"{len(residue)} words of residue classes {', '.join(c['id'] for c in classes)}: "
            "written by")
    SF.write_mask(sorted(words - set(drop)), [
        f" * {info['mask']} (sha256 {info['mask_sha256']}) less the "
        f"{info['identity_words_removed']} identity words", held,
        " * tools/s4/tick_replay.py effective_mask, for the host's recompute from word logs."],
        out)
    return out, dict(info, effective_sha256=sha256(out))


def parse_capture_text(text: str, what: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """A tick capture's text as read_capture reads a file: ticks 1..N, whole rows."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or not rows[0] or rows[0][0] != IDENTITY:
        raise refuse(f"{what}: not a tick capture")
    out = []
    for k, row in enumerate(rows[1:], 1):
        if len(row) != len(rows[0]) or row[0] != str(k):
            raise refuse(f"{what}: row {k} is not tick {k} with {len(rows[0])} fields")
        out.append((k, row[1:]))
    return rows[0], out


def unkept(cache: dict, call):
    """call()'s value, with `cache` (one of wordlog's per-process caches) left
    holding only what it held before: what log_stream keeps of a log is its
    stream, so the reader's text and field values for it are released once read
    (a Metal Harbor log's field values are five million strings)."""
    before = set(cache)
    try:
        return call()
    finally:
        for k in [k for k in cache if k not in before]:
            cache.pop(k, None)


def log_stream(log: Path, mask: Path, extra: list[tuple[str, int, str]], *,
               native: bool) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """A word log as the stream a replay compares: the tick capture the log
    gives under `mask` (tools/s4/wordlog.py capture_csv: the named fields and
    the digest columns), then one column per `extra` identity word, every tick.
    `native` holds the log to the native probe's rules too. Kept per process by
    the log's and the mask's sha256 while it fits (`remembered`), since the gate
    recomputes a result more than once; a stream too large to keep is the
    caller's, released when the caller drops it. The reader's own copies of what
    it gave are not kept (`unkept`)."""
    log, mask = Path(log), Path(mask)
    # By content alone: a run's directory copied elsewhere is the same stream (the replay
    # gate copies runs for its falsifications), and each value is interned, since the
    # native repeats of one replay hold the same values tick for tick (the U6 fold: the
    # gate held 12 GB with seventeen Metal Harbor logs).
    key = (sha256(log), sha256(mask), tuple(c for c, _, _ in extra), native)

    def read() -> tuple[list[str], list[tuple[int, list[str]]]]:
        try:
            header, rows = parse_capture_text(
                unkept(WL._CAPTURES, lambda: WL.capture_csv(log, mask, native=native)), str(log))
            values = unkept(WL._FIELDS, lambda: WL.field_values(
                log, [(c, va, 4) for c, va, _ in extra])) if extra else {}
        except SystemExit as e:           # wordlog.WordLogError: the reader refused the log
            raise refuse(f"{log}: {e}") from None
        if extra:
            short = [t for t, _ in rows if len(values.get(t) or ()) != len(extra)]
            if short:
                raise refuse(f"{log}: the identity words are missing at {len(short)} ticks, "
                             f"first {short[0]}")
            header = header + [c for c, _, _ in extra]
        intern = sys.intern
        return header, [(t, [intern(v) for v in w] + [intern(v) for v in values[t]] if extra
                         else [intern(v) for v in w]) for t, w in rows]

    (header, rows), shared = remembered(_STREAMS, key, read, lambda v: cells(v[1]))
    if not shared:
        return header, rows
    return list(header), [(t, list(w)) for t, w in rows]


def native_log(entry: dict, cap: dict, root: Path | None = None) -> Path:
    """A native capture's word log, held to its pin and to the run that took
    the capture: registered beside the tick capture with its sha256
    (`word_log`), the file the capture's receipt collected, and the log the
    runner checked and built this very capture from (the receipt's word_log:
    ok, and the capture's sha256)."""
    root = ROOT if root is None else root
    name, capture = entry.get("name"), cap.get("capture")
    wl = cap.get("word_log")
    if not isinstance(wl, dict) or not wl.get("file") or not wl.get("sha256"):
        raise refuse(f"{name}: {capture} registers no word log with its sha256 (word_log), and a "
                     "word-policy entry is compared from both sides' logs")
    if run_dir(wl["file"]) != run_dir(str(capture)):
        raise refuse(f"{name}: the word log {wl['file']} is not beside its tick capture {capture}")
    path = root / wl["file"]
    if sha256(path) != wl["sha256"]:
        raise refuse(f"{name}: the word log {wl['file']} is missing or does not hash to its pin")
    try:
        receipt = json.loads((root / str(cap.get("receipt"))).read_text())
    except (OSError, ValueError) as e:
        raise refuse(f"{name}: {capture}'s receipt does not read ({e})") from None
    collected = {a.get("file"): (a.get("check") or {}).get("sha256")
                 for a in receipt.get("artifacts") or [] if isinstance(a, dict)}
    if collected.get(Path(wl["file"]).name) != wl["sha256"]:
        raise refuse(f"{name}: {wl['file']} is not the log {capture}'s receipt collected")
    rec = receipt.get("word_log") or {}
    if rec.get("ok") is not True or rec.get("capture_sha256") != cap.get("sha256"):
        raise refuse(f"{name}: {capture}'s receipt does not record its word log checked and the "
                     "tick capture built from it (word_log.ok, word_log.capture_sha256)")
    return path


def native_stream(entry: dict, cap: dict, mask: Path, spec: dict,
                  root: Path | None = None) -> tuple[list[str], list]:
    """(header, rows) of one registered native capture, from its log; the log's
    named fields must be its pinned tick capture's, tick for tick."""
    root = ROOT if root is None else root
    header, rows = log_stream(native_log(entry, cap, root), mask, spec["extra"], native=True)
    pinned = root / cap["capture"]
    if sha256(pinned) != cap["sha256"]:
        raise refuse(f"{entry['name']}: {cap['capture']} does not hash to its pin")
    ph, prow = read_capture(pinned)
    named = [f for f in ph[1:] if not f.startswith("d_")]
    if header[1:1 + len(named)] != named or len(prow) != len(rows) or any(
            a[:len(named)] != b[:len(named)] for (_, a), (_, b) in zip(rows, prow)):
        raise refuse(f"{entry['name']}: the named fields of {cap['capture']} are not its "
                     "word log's, tick for tick")
    return header, rows


def native_streams(entry: dict, mask: Path, spec: dict,
                   root: Path | None = None) -> Iterator[tuple[dict, list[str], list]]:
    """(registry capture, header, rows) of the capture and then each repeat
    (`native_stream`), each read only when the one before it has been taken: a
    comparison that drops each repeat's stream before taking the next
    (native_repeat_streams) holds the capture's and one repeat's, never every
    repeat's at once."""
    root = ROOT if root is None else root
    for cap in [entry["native"]] + list(entry["native"].get("repeats", [])):
        yield (cap, *native_stream(entry, cap, mask, spec, root))


def lifted_stream(entry: dict, lifted: dict, mask: Path, spec: dict,
                  root: Path | None = None) -> tuple[list[str], list]:
    """The lifted run's stream, from the log it kept (`lifted["words"]`), held
    to its pin; its named fields must be the lifted tick capture's."""
    root = ROOT if root is None else root
    words = lifted.get("words") or {}
    path = root / str(words.get("path") or "")
    if not words.get("path") or sha256(path) != words.get("sha256"):
        raise refuse(f"{entry['name']}: the lifted word log {words.get('path')} is missing or "
                     "does not hash to its pin")
    header, rows = log_stream(path, mask, spec["extra"], native=False)
    ph, prow = read_capture(root / lifted["capture"])
    named = [f for f in ph[1:] if not f.startswith("d_")]
    if header[1:1 + len(named)] != named or len(prow) != len(rows) or any(
            a[:len(named)] != b[:len(named)] for (_, a), (_, b) in zip(rows, prow)):
        raise refuse(f"{entry['name']}: the named fields of {lifted['capture']} are not its word "
                     "log's, tick for tick")
    return header, rows


_WORDS: dict[tuple, dict] = {}


def digest_words(native_log_path: Path, lifted_log_path: Path, mask: Path, column: str,
                 tick: int, limit: int = 8) -> dict:
    """The words of a digest column's block the mask selects that differ at
    `tick`, native against lifted: the first `limit` by address, and how many.
    Kept per process by the logs' and the mask's sha256, as log_stream is."""
    key = (sha256(Path(native_log_path)), sha256(Path(lifted_log_path)), sha256(Path(mask)),
           column, tick, limit)
    if key not in _WORDS:
        _WORDS[key] = _digest_words(Path(native_log_path), Path(lifted_log_path), Path(mask),
                                    column, tick, limit)
    return json.loads(json.dumps(_WORDS[key]))


def _digest_words(native_log_path: Path, lifted_log_path: Path, mask: Path, column: str,
                  tick: int, limit: int) -> dict:
    base = int(column[2:], 16)
    words = sorted(va for va in OM.read_mask_words(mask) if base <= va < base + 0x10000)
    lo, _ = WL.section()
    a, b = WL.state(native_log_path, tick), WL.state(lifted_log_path, tick)
    diff = []
    for va in words:
        x, y = (int.from_bytes(s[va - lo:va - lo + 4], "little") for s in (a, b))
        if x != y:
            diff.append({"va": f"{va:#010x}", "native": f"0x{x:08x}", "lifted": f"0x{y:08x}"})
    return {"words_differing": len(diff), "words": diff[:limit]}


# --------------------------------------------------------------- comparison

def residue_for(name: str, div: dict, labels: list[dict], residues: Path) -> str | None:
    """The residue id a registry label gives this exact divergence, or None."""
    for label in labels:
        if label.get("tick") != div["tick"] or label.get("field") != div["field"]:
            continue
        rid = str(label.get("residue", ""))
        if div["kind"] != "value":
            raise refuse(f"the registry labels tick {div['tick']} {div['field']!r} as a residue, "
                         "and a run that ended or an oracle that did not repeat is never one")
        if not NAME.match(rid) or not (residues / f"{rid}.json").is_file():
            raise refuse(f"the registry labels tick {div['tick']} {div['field']} with residue "
                         f"{rid!r}, and no record Documentation/receipts/residues/{rid}.json exists")
        record = json.loads((residues / f"{rid}.json").read_text())
        want = {"replay": name, "tick": div["tick"], "field": div["field"],
                "native": div["native"], "lifted": div["lifted"]}
        if want not in (record.get("observed_in") or []):
            raise refuse(f"residue {rid} does not record this observation in its observed_in "
                         f"({json.dumps(want)}); a label is a claim the record must carry")
        return rid
    return None


def policy_identity(policy: dict) -> dict[str, str]:
    """A field policy's identity fields, each a heap value."""
    return {f: "heap" for f, p in policy.items() if p.get("compare") == "identity"}


def class_of(div: dict | None, masked: list[dict], seen: list[str] = ()) -> str | None:
    """A result's class, the one rule for it: a divergence's by its kind (a
    value, a run that ended or a residue rule broken is a bug), else the first
    labelled cell's residue, else `residue` when a residue class's words
    differed (`seen`), else none."""
    if div is not None:
        return {"native_unrepeated": "unrepeatable",
                "lifted_run_bounded": "bounded"}.get(div["kind"], "bug")
    if masked:
        return f"residue {masked[0]['residue']}"
    return "residue" if seen else None


def project(header: list[str], rows: list[tuple[int, list[str]]],
            drop: set[str]) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """A stream without the columns in `drop`, as Omochao Split is handed it."""
    if not drop:
        return header, rows
    keep = [i for i, f in enumerate(header[1:]) if f not in drop]
    return ([header[0]] + [header[1:][i] for i in keep],
            [(t, [w[i] for i in keep]) for t, w in rows])


def first_check_failure(checks: dict, order: dict[str, int]) -> tuple[str, dict] | None:
    """The earliest failing cell among the identity checks, (check, first)."""
    fails = [(c, v["first"]) for c, v in checks.items() if not v["ok"] and v["first"]]
    return min(fails, key=lambda cf: (cf[1]["tick"], order[cf[1]["field"]], CHECKS.index(cf[0])),
               default=None)


def compare(header: list[str], native: list[tuple[int, list[str]]],
            lifted_header: list[str], lifted: list[tuple[int, list[str]]],
            policy: dict, labels: list[dict], work: Path, judged: int | None = None,
            residues: Path = RESIDUES, name: str = "replay", lifted_stop: str = "",
            identity: dict[str, str] | None = None, shift: dict | None = None) -> dict:
    """The lifted stream against the native one.

    `judged` is how many native ticks every repeat reproduced; past it the
    oracle is not shown to repeat and the replay is unrepeatable, not passed.
    `identity` (column -> kind) names the columns of a word-policy entry
    compared by the one rule (`canonical`); a field policy's identity fields
    join it as heap values. With it, the result also carries `identity`: each
    stream's events over the ticks both have, and the three checks on them
    (`identity_checks`).

    `shift` ({"columns", "bound"}, a host-timing record's open-shift columns
    and the bound the native repeats give) takes those columns out of Omochao
    Split's streams: they are judged by the three checks alone, their opens
    within the bound (`shifted_opens`), and the earliest check that fails on
    them is a divergence like any other, compared as identity, when it comes
    before Split's first.
    """
    if lifted_header != header:
        raise refuse(f"the lifted capture's fields differ from the native capture's: "
                     f"{lifted_header} against {header}")
    check_policy(policy, header)
    ident = dict(policy_identity(policy))
    for f, k in (identity or {}).items():
        if f not in header[1:]:
            raise refuse(f"the identity column {f!r} is not in the capture")
        if ident.get(f, k) != k:
            raise refuse(f"{f} is compared as a {ident[f]} value by the field policy and as a "
                         f"{k} value by the identity record")
        ident[f] = k
    for label in labels:
        if label.get("field") not in header[1:]:
            raise refuse(f"the registry labels {label.get('field')!r} at tick {label.get('tick')} "
                         "as a residue, and only a field can be one: a run that ended or an "
                         "oracle that did not repeat is never one")
        if identity is not None and label.get("field") in ident:
            raise refuse(f"the registry labels {label.get('field')!r} at tick {label.get('tick')} "
                         "as a residue, and it holds a value the host chooses: a residue is a "
                         "difference in a value, and this one is compared by what it names")
    judged = len(native) if judged is None else min(judged, len(native))
    if judged < 1:
        raise refuse("no native tick is judged: no repeat reproduces tick 1")
    moving = set((shift or {}).get("columns") or [])
    if shift is not None and (identity is None or not moving or any(
            ident.get(c) != "handle" for c in moving) or not isinstance(shift.get("bound"), int)):
        raise refuse(f"an open shift needs handle columns of the identity comparison and a "
                     f"bound: {sorted(moving)[:4]}, bound {shift.get('bound')!r}")
    n = canonical(header, native, ident)[:judged]
    lift = canonical(header, lifted, ident, lifted=True)
    common = min(judged, len(lift))
    masked: list[dict] = []
    div = None
    work_lift = [(t, list(w)) for t, w in lift[:common]]
    split_header, split_native = project(header, n[:common], moving)
    while common:
        report = split(split_header, split_native, project(header, work_lift, moving)[1], work,
                       "replay")
        if report["status"] != "different":
            break
        tick = int(report["identity"][IDENTITY])
        field = report["field"]
        col = header.index(field) - 1
        found = {"tick": tick, "field": field, "kind": "value",
                 "native": native[tick - 1][1][col], "lifted": lifted[tick - 1][1][col],
                 "compared_as": "identity" if field in ident else "exact"}
        if identity is not None and field in ident:
            before = n[tick - 2][1][col] if tick > 1 else "0x00000000"
            now_n, now_l = n[tick - 1][1][col], work_lift[tick - 1][1][col]
            found.update(native_name=now_n, lifted_name=now_l, check=(
                "pairing" if is_value(now_n, ident[field]) and is_value(now_l, ident[field])
                else "close" if is_value(before, ident[field]) else "open"))
        rid = residue_for(name, found, labels, residues)
        if rid is None:
            div = found
            break
        masked.append(dict(found, residue=rid))
        work_lift[tick - 1][1][col] = n[tick - 1][1][col]    # accept this one cell, keep looking
    ident_doc = None
    if identity is not None:
        cols = [f for f in header[1:] if f in ident]
        named = {f: k for f, k in ident.items() if column_kind(f, {}) != k}
        nev, lev = events(header, n[:common], ident), events(header, lift[:common], ident)
        spec = {"columns": sorted(moving), "bound": shift["bound"]} if moving else None
        checks = identity_checks(nev, lev, cols, named, common, shift=spec)
        ident_doc = {"columns": cols, "named": named, "judged_ticks": common,
                     "native_events": nev, "lifted_events": lev, "checks": checks}
        if spec:
            # recorded as `shifting`, not `columns`: the receipt's "columns": [ is the
            # identity columns' alone (M5's falsifiers anchor on it)
            ident_doc["open_shift"] = {"shifting": spec["columns"], "bound": spec["bound"]}
            # The shifted columns left Split's streams: the checks are all that judge
            # them, so the earliest failure is a divergence when it comes first.
            failed = first_check_failure(checks, {c: k for k, c in enumerate(cols)})
            if failed and (div is None or failed[1]["tick"] < div["tick"]):
                check, f = failed
                col = header.index(f["field"]) - 1
                div = {"tick": f["tick"], "field": f["field"], "kind": "value",
                       "native": native[f["tick"] - 1][1][col],
                       "lifted": lifted[f["tick"] - 1][1][col], "compared_as": "identity",
                       "native_name": n[f["tick"] - 1][1][col],
                       "lifted_name": lift[f["tick"] - 1][1][col], "check": check,
                       "said": {"native": f["native"], "lifted": f["lifted"]}}
    if div is None and len(lift) < judged:
        # Why the lifted stream is short decides what it means. A trap is the
        # build failing; the import-call budget, or the host's WM_QUIT at the
        # pump's poll budget, is the harness stopping a run at the same point
        # every time, and saying "bug" for that would blame the build for the
        # length of the harness's leash.
        bounded = lifted_stop.startswith(BUDGET_NOTE) or lifted_stop.startswith(QUIT_NOTE)
        div = {"tick": len(lift) + 1, "field": BOUNDED if bounded else RUN_ENDED,
               "kind": "lifted_run_bounded" if bounded else "lifted_run_ended",
               "native": "a row", "lifted": "no row", "stop": lifted_stop[:300]}
    if div is None and judged < len(native):
        div = {"tick": judged + 1, "field": UNREPEATED, "kind": "native_unrepeated",
               "native": "no repeat reproduces this tick", "lifted": "not judged"}
    cls = class_of(div, masked)
    # Which fields the judged native ticks actually move. An agreement over
    # fields that never change, or that count ticks, says less than one over
    # fields the game's state drives, and the result says which it was.
    varying = [f for i, f in enumerate(header[1:])
               if len({w[i] for _, w in native[:judged]}) > 1]
    out = {
        "native_ticks": len(native), "judged_ticks": judged, "lifted_ticks": len(lifted),
        "varying_fields": varying,
        "compared_ticks": common, "compared_fields": len(header) - 1,
        "identity_fields": sorted(f for f, p in policy.items() if p.get("compare") == "identity"),
        "first_divergence": div, "residues_masked": masked,
        "reported": div or (masked[0] if masked else None),
        "class": cls,
    }
    if ident_doc is not None:
        out["identity"] = ident_doc
    return out


def native_repeats(entry: dict, header: list[str], native: list[tuple[int, list[str]]],
                   work: Path, *, streams: Iterable | None = None,
                   identity: dict[str, str] | None = None,
                   shift: list[str] | None = None) -> dict:
    """Every registered repeat against the capture, under the replay's policy.

    `judged` is the number of leading ticks every repeat reproduces: the first
    native-native divergence, or a repeat that is simply shorter, caps it.

    A word-policy entry passes each repeat's stream from its log (`streams`,
    native_streams' after the capture's) and its identity columns: the repeat
    is compared under the one rule, so a repeat that opens or closes a handle
    on another tick than the capture diverges there, and the replay is
    unrepeatable from that tick rather than the lifted build's bug; each
    repeat's record says which check it failed first (`identity`). `shift`
    names the handle columns whose open ticks may shift (native_repeat_streams)."""
    if streams is not None:
        return native_repeat_streams(entry, header, native, work, streams, identity or {},
                                     shift or [])
    repeats = entry["native"].get("repeats", [])
    if not repeats:
        raise refuse(f"{entry['name']}: no native repeat is registered, so nothing shows the "
                     "oracle is deterministic and a divergence could be the oracle's")
    # One native run is one directory. A repeat in the capture's directory, or
    # in an earlier repeat's, is one run listed twice, and it would read as a
    # repeat agreeing on every tick: perfect determinism from one run (U9's
    # second review, W2). tools/s4/stage_boot.py refuses the same for M6.
    seen = {run_dir(entry["native"]["capture"]): "the registered capture"}
    for k, rep in enumerate(repeats):
        where = run_dir(rep["capture"])
        if where in seen:
            raise refuse(f"{entry['name']}: repeat {k} ({rep['capture']}) is in the directory of "
                         f"{seen[where]} ({where}): one native run listed twice shows nothing "
                         "about the oracle repeating, and each repeat must be a run of its own")
        seen[where] = f"repeat {k}"
    policy = entry.get("field_policy", {})
    out, judged = [], len(native)
    for k, rep in enumerate(repeats):
        path = ROOT / rep["capture"]
        if sha256(path) != rep["sha256"]:
            raise refuse(f"repeat {rep['capture']} does not hash to its pin")
        h2, rows = read_capture(path)
        if h2 != header:
            raise refuse(f"repeat {rep['capture']} has different fields")
        common = min(len(native), len(rows))
        report = split(header, normalise(header, native, policy)[:common],
                       normalise(header, rows, policy)[:common], work, f"repeat{k}")
        tick = int(report["identity"][IDENTITY]) if report["status"] == "different" else None
        judged = min(judged, (tick - 1) if tick is not None else common)
        out.append({"capture": rep["capture"], "ticks": len(rows), "compared_ticks": common,
                    "first_divergent_tick": tick,
                    "byte_identical": sha256(path) == entry["native"]["sha256"]})
    return {"repeats": out, "judged_ticks": judged}


def repeat_directories(entry: dict) -> None:
    """One native run is one directory: a repeat in the capture's directory, or
    in an earlier repeat's, is one run listed twice (native_repeats)."""
    repeats = entry["native"].get("repeats", [])
    if not repeats:
        raise refuse(f"{entry['name']}: no native repeat is registered, so nothing shows the "
                     "oracle is deterministic and a divergence could be the oracle's")
    seen = {run_dir(entry["native"]["capture"]): "the registered capture"}
    for k, rep in enumerate(repeats):
        where = run_dir(rep["capture"])
        if where in seen:
            raise refuse(f"{entry['name']}: repeat {k} ({rep['capture']}) is in the directory of "
                         f"{seen[where]} ({where}): one native run listed twice shows nothing "
                         "about the oracle repeating, and each repeat must be a run of its own")
        seen[where] = f"repeat {k}"


def native_repeat_streams(entry: dict, header: list[str], native: list[tuple[int, list[str]]],
                          work: Path, streams: Iterable, identity: dict[str, str],
                          shift: list[str] | None = None) -> dict:
    """native_repeats for a word-policy entry: each repeat's log stream against
    the capture's, both named by `canonical`, with the three identity checks
    of each pair beside the first divergence. `streams` gives (repeat, header,
    rows) for each repeat in order, and is read once: native_streams' generator
    after the capture's, or a list.

    `shift` (a host-timing record's open-shift columns) takes those columns
    out of Omochao Split's streams, as the lifted comparison does: a repeat is
    held on them by the checks alone, its opens paired in order and equal in
    number and kind at any distance, and its first divergence is the earlier
    of Split's and the checks'. The bound the lifted run is then held to is
    the widest distance between paired opens any repeat shows over the ticks
    the repeats judge (`open_shift`), computed here on every pass: among the
    repeats it holds by construction, so what judges them on those columns is
    the opens' number, order and kind, the pairing and the close ticks."""
    repeat_directories(entry)
    repeats = entry["native"].get("repeats", [])
    policy = entry.get("field_policy", {})
    ident = dict(policy_identity(policy)) | dict(identity)
    cols = [f for f in header[1:] if f in ident]
    order = {c: k for k, c in enumerate(cols)}
    named = {f: k for f, k in ident.items() if column_kind(f, {}) != k}
    moving = set(shift or [])
    spec = {"columns": sorted(moving), "bound": None} if moving else None
    cap = canonical(header, native, ident)
    split_header = project(header, [], moving)[0]
    out, judged, evs = [], len(native), []
    # One repeat at a time: each repeat's stream is taken (native_streams reads it
    # then), named, compared with the capture and dropped before the next is
    # taken, so what is held is the capture's stream and one repeat's. Every repeat
    # held at once was most of an 18 GB Metal Harbor verdict. The loop counts by
    # hand: enumerate would hold the previous repeat's stream while the next is read.
    given = iter(streams)
    k = 0
    for rep, h2, rows in given:
        if k >= len(repeats):
            n = k + 1 + sum(1 for _ in given)
            raise refuse(f"{entry['name']}: {n} repeat streams for {len(repeats)} repeats")
        if rep is not repeats[k] and rep != repeats[k]:
            raise refuse(f"{entry['name']}: repeat stream {k} is not repeat {k}'s")
        if h2 != header:
            raise refuse(f"repeat {rep['capture']} has different fields")
        common, ticks = min(len(native), len(rows)), len(rows)
        other = canonical(header, rows, ident)[:common]
        del rows
        report = split(split_header, project(header, cap[:common], moving)[1],
                       project(header, other, moving)[1], work, f"repeat{k}")
        tick = int(report["identity"][IDENTITY]) if report["status"] == "different" else None
        field = report.get("field") if tick is not None else None
        pair = (events(header, cap[:common], ident), events(header, other, ident))
        del other
        checks = identity_checks(*pair, cols, named, common, shift=spec)
        if moving:
            evs.append(pair)
            failed = first_check_failure(checks, order)
            if failed and (tick is None or failed[1]["tick"] < tick):
                tick, field = failed[1]["tick"], failed[1]["field"]
        judged = min(judged, (tick - 1) if tick is not None else common)
        first = {c: v["first"] for c, v in checks.items() if not v["ok"]}
        out.append({"capture": rep["capture"], "ticks": ticks, "compared_ticks": common,
                    "first_divergent_tick": tick,
                    "first_divergent_field": field,
                    "byte_identical": rep["sha256"] == entry["native"]["sha256"],
                    # "native" is the registered capture and "lifted" this repeat
                    "identity": {c: {"ok": v["ok"], "mismatches": v["mismatches"]}
                                 for c, v in checks.items()} | {"first": first}})
        k += 1
    if k != len(repeats):
        raise refuse(f"{entry['name']}: {k} repeat streams for {len(repeats)} repeats")
    det = {"repeats": out, "judged_ticks": judged}
    if moving:
        # The distances over the ticks every repeat reproduces, which are the
        # ticks the lifted run is judged on: a pair that starts after them says
        # nothing about the lifted run's.
        for rec, pair in zip(out, evs):
            s = identity_checks(*pair, cols, named, judged, shift=spec)["open"]["shift"]
            rec["open_shift"] = {"pairs": s["pairs"], "shifted": s["shifted"],
                                 "widest": s["widest"], "first_shifted": s["first_shifted"]}
        det["open_shift"] = {"columns": len(moving),
                             "bound": max((r["open_shift"]["widest"] for r in out), default=0)}
    return det


def probe_headers(source: Path) -> dict[str, str]:
    """The headers `source` is built from, name -> sha256: every file it
    includes with quotes, and every file those include, each found beside the
    file that includes it, as the compiler finds them first. A system header
    (`<...>`) is the toolchain's, not the probe's. An include by macro, or one
    this cannot find beside its includer, refuses: the capture's receipt records
    only the headers beside the probe, so such a build's inputs are not a
    recorded fact."""
    seen: dict[str, str] = {}
    todo = [source]
    while todo:
        f = todo.pop()
        code = re.sub(r"/\*.*?\*/", " ", f.read_text(errors="replace"), flags=re.S)
        code = re.sub(r"//[^\n]*", " ", code)
        for target in INCLUDE.findall(code):
            if target.startswith("<") and target.endswith(">"):
                continue
            if not (target.startswith('"') and target.endswith('"') and len(target) > 2):
                raise refuse(f"{f.name} includes {target!r}, which names no file this can "
                             "hash, so the headers its captures were built from are unknown")
            name = target[1:-1]
            header = f.parent / name
            if "/" in name or "\\" in name:
                raise refuse(f"{f.name} includes {name!r} from outside {source.parent.name}/, "
                             "and a capture's receipt records only the headers beside its probe")
            if not header.is_file():
                raise refuse(f"{f.name} includes {name!r}, which is not beside it")
            if name not in seen:
                seen[name] = sha256(header)
                todo.append(header)
    return dict(sorted(seen.items()))


def native_probe_current(entry: dict, root: Path | None = None) -> dict:
    """The capture and every repeat were built from the native probe now in the
    tree -- the source and the headers it includes, where the field list and the
    digest mask live -- or they are stale: a probe edit is a different capture.

    Only the headers the probe includes are compared (`probe_headers`). The
    runner records every header beside the probe, so comparing all of them made
    a header no probe includes -- a per-replay mask, say -- stale every
    registered capture (the M5 path review's H5, 2026-09-23). A header the probe
    includes, directly or through another, is still compared, and one the
    receipt does not record is a mismatch, never a pass."""
    root = ROOT if root is None else root
    probe = root / NATIVE_PROBE
    now = must_sha256(probe)
    headers = probe_headers(probe)
    captures = [entry["native"]] + list(entry["native"].get("repeats", []))
    for cap in captures:
        receipt = root / str(cap.get("receipt", ""))
        if not cap.get("receipt") or not receipt.is_file():
            raise refuse(f"{entry['name']}: {cap['capture']} has no oracle receipt, so "
                         "nothing says which probe built it")
        built = json.loads(receipt.read_text())["inputs"]["built_probes"]
        source = [b for b in built if Path(b["source"]).name == probe.name]
        if len(source) != 1 or source[0]["source_sha256"] != now:
            raise refuse(f"{entry['name']}: {cap['capture']} was built from a {probe.name} "
                         f"that is not the one in the tree "
                         f"({[b['source_sha256'][:12] for b in source]} against {now[:12]}); "
                         "re-capture it")
        recorded = source[0].get("headers_sha256") or {}
        moved = sorted(h for h in headers if recorded.get(h) != headers[h])
        if moved:
            raise refuse(f"{entry['name']}: {cap['capture']} was built against headers that "
                         f"are not the ones {probe.name} includes now ({', '.join(moved)}); "
                         "the field list and the digest mask are among what it includes, so "
                         "re-capture it")
    out = {"probe": NATIVE_PROBE, "source_sha256": now, "headers_sha256": headers,
           "captures": len(captures)}
    if is_stage(entry):
        # A stage entry's trace and goal tick come from two more probes, and a
        # capture taken with other ones is another capture (the spec's section 5).
        stage = {}
        for rel in STAGE_PROBES:
            want = must_sha256(root / rel)
            for cap in captures:
                built = json.loads((root / str(cap["receipt"])).read_text())["inputs"]["built_probes"]
                got = [b for b in built if Path(b["source"]).name == Path(rel).name]
                if len(got) != 1 or got[0]["source_sha256"] != want:
                    raise refuse(f"{entry['name']}: {cap['capture']} was built from a "
                                 f"{Path(rel).name} that is not the one in the tree "
                                 f"({[b['source_sha256'][:12] for b in got]} against "
                                 f"{want[:12]}); re-take it")
            stage[rel] = want
        out["stage_probes"] = stage
    return out


def native_vanilla_problems(capture_dir: Path, *, root: Path | None = None) -> list[str]:
    """Why a native capture is not one the vanilla oracle as it stands in the
    tree took, or [] when it is: tools/oracle/oracle_mode.py `vanilla_problems`,
    the one check, which the recorder and the attract-demo run ask directly and
    the stage run and the import-log check ask through here. It reads the log
    with oracle_mode's strict parser: the vanilla probe first in the build list
    and in both of the loader's orders, at the tree's hash and with no
    experiment's defines; the log the runner collected, by its receipt's
    sha256; only the actions a correct run writes, each row once; the kept
    ranges exactly the source's KEEP table; the import slots as the source's
    default treats them; and tick-1 rows naming exactly the restored ranges. A
    refusal to read the tree's own probe comes back as a reason, so a caller
    that lists every reason still lists it."""
    try:
        return list(OM.vanilla_problems(Path(capture_dir), root=ROOT if root is None else root))
    except SystemExit as e:              # oracle_mode.ModeError: the tree's probe unreadable
        return [str(e)]
    except OSError as e:                 # the tree's probe source itself is missing
        return [f"{VANILLA_PROBE} cannot be read ({e.strerror}), so no capture can be held to "
                "the vanilla oracle as it stands in the tree"]


def native_oracle(entry: dict, root: Path | None = None) -> str | None:
    """The oracle a registry entry says its native side is -- None for the Mod
    Loader's, "vanilla" for the game's own code -- after holding the capture and
    every repeat to it: one oracle mode among them, the mode the entry names,
    and for "vanilla" the vanilla oracle as it stands in the tree."""
    root = ROOT if root is None else root
    oracle = entry.get("oracle")
    if not (oracle is None or isinstance(oracle, str)) or oracle not in ORACLE_MODE:
        raise refuse(f"{entry['name']}: the registry names oracle {oracle!r}; this tool knows "
                     f"{list(ORACLES)}, and an entry without one is the Mod Loader's")
    caps = [entry["native"]] + list(entry["native"].get("repeats", []))
    for cap in caps:                     # the directory is the receipt's, so there must be one
        if not cap.get("receipt"):
            raise refuse(f"{entry['name']}: {cap.get('capture')} names no oracle receipt, so "
                         "nothing says which oracle took it")
    dirs = [(root / str(c["receipt"])).parent for c in caps]
    try:
        mode = OM.require_same(dirs, f"{entry['name']}'s native capture and repeats")
    except SystemExit as e:              # oracle_mode.ModeError
        raise refuse(f"{entry['name']}: {e}") from None
    if mode["mode"] != ORACLE_MODE[oracle]:
        said = "no oracle, which is the Mod Loader's" if oracle is None else f"oracle {oracle!r}"
        raise refuse(f"{entry['name']}: the registry names {said}, and its native capture and "
                     f"repeats were taken by the {mode['mode']} oracle ({OM.describe(mode)}); "
                     "the flag must say which oracle the replay compares with")
    if oracle == "vanilla":
        for cap, d in zip(caps, dirs):
            problems = native_vanilla_problems(d, root=root)
            if problems:
                raise refuse(f"{entry['name']}: {cap['capture']} is registered as the vanilla "
                             f"oracle's and does not show it: {'; '.join(problems)}")
    # The digest mask compares only the words it was chosen to, from runs of one oracle mode;
    # a replay whose native side another mode took would be compared through it across modes,
    # which the vanilla decision refuses until the mask is re-derived from runs of the
    # replay's mode (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md, the mask). A
    # word-policy entry's mask is its own once it exists (`mask_for`), and a word-log mask
    # holds the native side to the very build and configuration its sources ran with
    # (oracle_mode.require_mask_configuration: the user's mask rule read from the comparison's
    # side, which the U4 masks unit asked this tool to ask).
    header = mask_for(entry, root) if "word_policy" in entry else root / BOOT_MASK
    try:
        OM.require_same(OM.digest_mask_sources(header, root, by_receipt=True) + dirs,
                        f"{header.name}'s source captures and {entry['name']}'s native side",
                        config=False)
        if header.resolve() != (root / BOOT_MASK).resolve():
            OM.require_mask_configuration(header, dirs, root)
    except OSError as e:                 # no header to read: nothing names the mask's runs
        raise refuse(f"{entry['name']}: {header.name} cannot be read ({e.strerror}), so the "
                     "digest mask's runs cannot be held to the replay's oracle mode") from None
    except SystemExit as e:              # oracle_mode.ModeError, or an unreadable provenance
        raise refuse(f"{entry['name']}: its native side and the digest mask's runs are not one "
                     f"oracle mode, or not one build and configuration, so the replay would "
                     f"compare across them: {e}. Re-derive the mask from runs of the replay's "
                     "configuration (tools/s4/snapshot_fields.py --word-logs).") from None
    return oracle


def require_mask_sources_hashed(root: Path | None = None,
                                entries: list[dict] | None = None) -> None:
    """Each registered replay's digest mask's sources hash as its header names
    them, file by file: the boot's tick_digest.h, and a word-policy entry's own
    mask once it has one (`mask_for`; the U4 masks unit's request).

    The per-replay mode check reads each source's hash from its capture's receipt
    (`oracle_mode.digest_mask_sources(by_receipt=True)`), which is enough to hold
    the replay to one oracle mode and costs nothing. The receipt that publishes M5
    also hashes the files themselves, so a source changed on disk after its capture
    was taken stops that push (the mask review of 2026-09-24, W4).
    """
    root = root or ROOT
    headers = [root / BOOT_MASK] + [mask_for(e, root) for e in entries or []
                                    if "word_policy" in e]
    for header in dict.fromkeys(headers):
        try:
            OM.digest_mask_sources(header, root)
        except OSError as e:
            raise refuse(f"{header.name} cannot be read ({e.strerror})") from None
        except SystemExit as e:
            raise refuse(f"the digest mask {header.name}'s sources do not hash as its header "
                         f"names them: {e}") from None


def lifted_guest(entry: dict, lifted: dict, root: Path | None = None) -> None:
    """The recorded lifted run ran in the directory its native capture's game
    ran in (the capture's receipt, device_profile.guest_paths), as Wine named it:
    a result must not outlive a run in another directory (the working-directory
    review, W1)."""
    root = root or ROOT
    rel = entry["native"].get("receipt")
    try:
        want = DP.guest_paths(json.loads((root / str(rel)).read_text()), str(rel))
    except (OSError, ValueError) as e:
        raise refuse(f"{entry['name']}: its capture's receipt {rel} does not say which guest "
                     f"directory the game ran in ({e})") from None
    if lifted.get("guest_paths") != want:
        raise refuse(f"{entry['name']}: the lifted run's recorded guest directory "
                     f"{lifted.get('guest_paths')} is not its capture's {want}")


def native_start(entry: dict, root: Path | None = None) -> None:
    """The capture and every repeat started from the saves the lifted run
    starts from -- the pin in Documentation/save-state.json, which
    reset_lifted gives run_lifted -- or refuse: tools/oracle/save_state.py
    native_start_problems, which tools/s4/stage_run.py and tools/s4/demo_run.py
    already ask of their captures. A capture taken from other saves, or one
    whose receipt records none, is refused, not compared (the user's ruling of
    2026-09-24 that the save file is a recorded input). Asked here before a
    replay runs and by Documentation/scripts/check_replay_results.py of every
    recorded one, so a result does not outlive a pin that moved (P6's review,
    W1)."""
    root = ROOT if root is None else root
    problems = []
    for cap in [entry["native"]] + list(entry["native"].get("repeats", [])):
        receipt = root / str(cap.get("receipt") or "")
        if not cap.get("receipt") or not receipt.is_file():
            raise refuse(f"{entry['name']}: {cap.get('capture')} has no oracle receipt, so "
                         "nothing says which saves it started from")
        problems += SS.native_start_problems(receipt.parent, root)
    if problems:
        raise refuse(f"{entry['name']}: {'; '.join(problems)}")


# ------------------------------------------------------------------ controls

# A control is a native run of another configuration, registered beside a
# word-policy entry's native side (`controls`) and never among its repeats: the
# boot's stack control, whose tick probe fills 8 KiB of dead stack with a
# pattern each tick (--define tick_probe:STACK_PAD=8192), so that a read of dead
# stack would move what the game computes. It is another probe build, which the
# mask rule refuses as a comparison run (oracle_mode.require_mask_configuration),
# so it judges nothing. What it shows against the entry's mask -- every mask word
# its log holds apart from the registered capture's at some tick, each with its
# first and last tick -- is its recorded `verdict`, recomputed on every pass of
# Documentation/scripts/check_replay_results.py, so a control whose showing moves
# (a new mask, a re-taken run) is seen rather than silently kept or dropped (the
# U6 reviews: masks W3, registration W6). `--controls` prints the verdicts.


def control_verdict(entry: dict, control: dict, root: Path | None = None) -> dict:
    """What a control shows against the entry's own mask (the comment above)."""
    root = ROOT if root is None else root
    spec = identity_spec(entry, root)
    with tempfile.TemporaryDirectory() as td:
        # the words the replay judges: its mask less identity and residue words
        mask, info = effective_mask(entry, spec, Path(td), root, residue_classes(entry, root))
        words = OM.read_mask_words(mask)
    rows, meta = WL.diff(native_log(entry, entry["native"], root),
                         native_log(entry, control, root))
    apart = [r for r in rows if r["va"] in words]
    listing = [[f"{r['va']:#010x}", r["first_tick"], r["last_tick"]] for r in apart]
    return {"mask_sha256": info["effective_sha256"],
            "compared_through": meta["compared_through"],
            "words": len(apart),
            "first_tick": min((r["first_tick"] for r in apart), default=None),
            "last_tick": max((r["last_tick"] for r in apart), default=None),
            "blocks": sorted({f"{r['va'] & ~0xFFFF:#010x}" for r in apart}),
            "listing_sha256": hashlib.sha256(json.dumps(listing).encode()).hexdigest()}


def control_problems(entry: dict, root: Path | None = None) -> list[str]:
    """Why an entry's controls are not what it records of them, [] when they
    are: each control's capture and word log are their pins and its receipt's,
    it started from the pinned saves, it is not also a repeat, and its verdict
    recomputed is the recorded one."""
    root = ROOT if root is None else root
    name = entry.get("name")
    controls = entry.get("controls") or []
    if controls and "word_policy" not in entry:
        return [f"{name}: registers controls, which are held against a word-policy entry's own "
                "mask, and has no word_policy"]
    native = entry.get("native") or {}
    judged = {run_dir(str(c.get("capture"))) for c in [native] + list(native.get("repeats") or [])
              if isinstance(c, dict)}
    out = []
    for c in controls:
        cap = c.get("capture") if isinstance(c, dict) else None
        if not cap or not c.get("sha256") or not str(c.get("what", "")).strip():
            out.append(f"{name}: a control registers no capture, sha256 or what it controls "
                       f"for: {c!r}")
            continue
        if run_dir(str(cap)) in judged:
            out.append(f"{name}: its control {cap} is also its capture or a repeat; a control "
                       "judges nothing")
            continue
        if sha256(root / str(cap)) != c["sha256"]:
            out.append(f"{name}: its control {cap} is missing or does not hash to its pin")
            continue
        try:
            receipt = root / str(c.get("receipt") or "")
            out += [f"{name}: its control {cap}: {p}"
                    for p in SS.native_start_problems(receipt.parent, root)]
            got = control_verdict(entry, c, root)
        except SystemExit as e:
            out.append(f"{name}: its control {cap} cannot be read against the mask: {e}")
            continue
        if got != c.get("verdict"):
            out.append(f"{name}: its control {cap} no longer shows what its recorded verdict "
                       f"says against the mask (recorded {c.get('verdict')}, now {got}); "
                       "re-derive it (tools/s4/tick_replay.py --controls) and say why it moved")
    return out


# ------------------------------------------------------------- stage entries

# The per-replay digest mask. The user ruled that each stage replay gets its own
# mask by the existing rule (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md:
# "per-replay mask by the existing rule"), chosen from runs configured exactly
# like the comparison runs (Documentation/decisions/2026-09-24-S4-halt-220h.md),
# and that Metal Harbor's acceptance is strict under it
# (Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md, ruling 3).
# Since the word log no side compiles the mask a comparison uses: the host
# recomputes both sides' digest columns from their word logs under the entry's
# own mask (`effective_mask`), and only a word-policy entry is compared that
# way. So a stage entry is refused unless it has a word policy, its own
# harness/replay/masks/<name>.h exists (`mask_for` would otherwise fall back to
# the boot's tick_digest.h), and that header passes
# oracle_mode.require_mask_configuration against every native capture the entry
# registers: a word-log mask held to the whole rule as it is read, and each
# capture the build and configuration the mask's sources ran with. This is the
# check the switch that refused every stage entry until such a mask existed
# (STAGE_MASKS_BUILT, the re-take review of 2026-09-24, W4) stood for.


def stage_mask_problems(entry: dict, root: Path | None = None) -> list[str]:
    """Why a stage entry does not compare through its own digest mask, [] when
    it does (the comment above): a word policy, its own
    harness/replay/masks/<name>.h, and that header holding each native capture
    the entry registers, the repeats among them, to the mask rule."""
    root = ROOT if root is None else root
    name = entry.get("name")
    own = root / MASKS_DIR / f"{name}.h"
    out = []
    if "word_policy" not in entry:
        out.append(f"{name}: it has no word_policy, so its digest columns would be the stored "
                   f"captures', under the boot's {BOOT_MASK}; a stage replay compares through "
                   "its own mask, which the host applies to both sides' word logs "
                   "(Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md, "
                   "ruling 3)")
    elif word_policy_problem(entry):
        # A key alone is not a policy: `null` or `{}` passed here and was refused only
        # at compare time (the U6 registration review, N1).
        out.append(word_policy_problem(entry))
    # The mask is found by the entry's name here (`mask_for`) and by the stage's in the
    # re-take tool, which judges item 5 (retake_replays.stage_mask): one file only while
    # the two are one name (the U6 registration review, N2).
    block = entry.get("stage")
    if isinstance(block, dict) and block.get("name") != name:
        out.append(f"{name}: its stage block names {block.get('name')!r}, so its own mask "
                   f"{MASKS_DIR}/{name}.h would not be the mask the re-take tool judges that "
                   f"stage's item 5 under ({MASKS_DIR}/<stage>.h); an entry is named for its "
                   "stage")
    if not own.is_file():
        out.append(f"{name}: its own digest mask {MASKS_DIR}/{name}.h does not exist, so it would "
                   f"be compared through the boot's {BOOT_MASK}; a stage replay registers once "
                   f"tools/s4/snapshot_fields.py --word-logs --replay {name} has chosen it")
        return out
    native = entry.get("native") or {}
    dirs = [(root / str(c["capture"])).parent
            for c in [native] + list(native.get("repeats") or [])
            if isinstance(c, dict) and c.get("capture")]
    if not dirs:
        return out + [f"{name}: registers no native capture to hold to its mask's build"]
    try:
        OM.require_mask_configuration(own, dirs, root)
    except OSError as e:
        out.append(f"{name}: {MASKS_DIR}/{name}.h or a file it cites cannot be read "
                   f"({e.strerror}), so its native captures cannot be held to the mask rule")
    except ValueError as e:              # a receipt that is not JSON (the review's N3)
        out.append(f"{name}: a receipt {MASKS_DIR}/{name}.h's check reads cannot be read ({e}), "
                   "so its native captures cannot be held to the mask rule")
    except SystemExit as e:              # oracle_mode.ModeError
        out.append(f"{name}: its own digest mask {MASKS_DIR}/{name}.h does not hold its native "
                   f"captures to the mask rule: {e}")
    return out


def native_input_problems(entry: dict, cap: dict, root: Path) -> list[str]:
    """Why one native capture of a stage entry is not shown to have replayed the
    entry's registered recording, [] when it is. An end record that names the
    goal with no miss is not enough: the recording itself, or a replay of
    another recording, writes one too (the M6 plumbing review, W2; the re-take
    review, W6).

    * its receipt records one input probe, built to replay (INPUT_MODE=2) the
      header tools/oracle/record_stage.py writes for the recording the entry's
      input sits in (an INPUT_SCRIPT naming <recording>.h);
    * its applied input, sa2-input-applied.csv, is registered with its sha256
      (input_applied), sits beside its tick capture, hashes to its pin, and is
      the file its receipt collected;
    * that log applied the registered input's rows, in order, with their
      values, up to the capture's last tick, and found a row on every call:
      input_record's rule, the one the lifted run is held to."""
    capture = cap.get("capture")
    script = (entry.get("input") or {}).get("script") if isinstance(entry.get("input"), dict) \
        else None
    if not capture or not script:
        return []                       # the capture's or the input's own problem names it
    recording = Path(str(script)).parent.name
    out = []
    receipt_path = root / Path(str(capture)).parent / "receipt.json"
    if cap.get("receipt") and Path(str(cap["receipt"])) != Path(str(capture)).parent / "receipt.json":
        # native_probe_current and native_oracle read cap["receipt"]; this reads the one
        # beside the capture, so the two must be one file (the M6 fold review, nit 7).
        out.append(f"its registered receipt {cap['receipt']} is not the one beside its tick "
                   f"capture ({DP.rel(receipt_path, root)})")
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError) as e:
        return [f"its receipt {DP.rel(receipt_path, root)} cannot be read ({e}), so nothing "
                f"shows it replayed {recording}"]
    built = [b for b in ((receipt.get("inputs") or {}).get("built_probes") or [])
             if isinstance(b, dict) and b.get("mod_id") == "input_probe"]
    defines = [d for b in built for d in (b.get("defines") or [])]
    if len(built) != 1 or "INPUT_MODE=2" not in defines or not any(
            d.startswith("INPUT_SCRIPT=") and d.rstrip('"').endswith(f"/{recording}.h")
            for d in defines):
        out.append(f"its input probe was not built to replay {recording} (INPUT_MODE=2 and an "
                   f"INPUT_SCRIPT naming {recording}.h; its receipt records "
                   f"{len(built)} input probe(s) with defines {defines})")
    applied = cap.get("input_applied")
    if not isinstance(applied, dict) or not applied.get("file") or not applied.get("sha256"):
        return out + ["registers no applied input with its sha256 (input_applied), so nothing "
                      f"shows which input it applied"]
    if sha256(root / applied["file"]) != applied["sha256"]:
        return out + [f"its applied input {applied['file']} is missing or does not hash to its pin"]
    import stage_boot as SB           # here: stage_boot imports this module
    if not SB.beside(applied["file"], capture):
        return out + [f"its applied input {applied['file']} is not beside its tick capture "
                      f"({capture})"]
    collected = {a.get("file"): (a.get("check") or {}).get("sha256")
                 for a in receipt.get("artifacts") or [] if isinstance(a, dict)}
    if collected.get(Path(applied["file"]).name) != applied["sha256"]:
        out.append(f"its applied input {applied['file']} is not the file its receipt collected "
                   f"({str(collected.get(Path(applied['file']).name))[:12]})")
    try:
        ticks = len(read_capture(root / str(capture))[1])
    except SystemExit:
        return out                      # the capture's own problem names it
    rec = input_record(entry, root / applied["file"], ticks, root, side="the native replay")
    out += [f"its applied input: {p}" for p in rec["problems"]]
    return out


def stage_entry_problems(entry: dict, root: Path | None = None) -> list[str]:
    """Why a stage entry cannot be replayed, [] when it can: asked before
    anything runs, and by Documentation/scripts/check_replay_results.py of
    every recorded one (the spec's section 1).

    * the stage is one tools/s4/stage_run.py starts, at that stage's level;
    * the recording's input is registered and is its pin;
    * at least two native captures (the registered one and its repeats),
      stage-route item 5's two replays, each with its tick capture, end record
      and stage trace hashing to their pins, and the end record and trace
      beside the tick capture of their own run;
    * each native capture a run of its own, in a directory no other of its
      captures shares: tools/s4/stage_boot.py native_run_problems, the rule M6's
      writer applies, so the two tools refuse the same entries;
    * every end record names one goal tick above zero, and no native replay
      missed a recorded input row: item 5's "set the goal latch on the same
      tick", in code;
    * each native capture replayed the registered recording, as its receipt
      and its applied input show (native_input_problems);
    * the entry compares through its own digest mask (`stage_mask_problems`):
      a word policy, harness/replay/masks/<name>.h, and that header holding
      every native capture to the build and configuration of the runs it was
      chosen from, so no stage entry registers under the boot mask."""
    import stage_boot as SB           # here: stage_boot imports this module
    import stage_run as SR
    root = ROOT if root is None else root
    name = entry.get("name")
    out: list[str] = stage_mask_problems(entry, root)
    block = entry.get("stage")
    if not isinstance(block, dict) or block.get("name") not in SR.STAGES:
        out.append(f"{name}: its stage block {block!r} names no stage tools/s4/stage_run.py "
                   f"starts ({', '.join(sorted(SR.STAGES))})")
    elif block.get("level") != SR.STAGES[block["name"]][0]:
        out.append(f"{name}: stage {block['name']!r} is level {SR.STAGES[block['name']][0]} in "
                   f"tools/s4/stage_run.py, and the entry says level {block.get('level')!r}")
    script = entry.get("input")
    if not isinstance(script, dict) or not script.get("script") or not script.get("sha256"):
        out.append(f"{name}: no recording's input is registered with its sha256 "
                   "(input.script, input.sha256), so the lifted run has nothing pinned to replay")
    elif sha256(root / script["script"]) != script["sha256"]:
        out.append(f"{name}: its input {script['script']} is missing or does not hash to its pin")
    native = entry.get("native") or {}
    caps = [native] + list(native.get("repeats") or [])
    if len(caps) < SB.MIN_NATIVE_ENDS:
        out.append(f"{name}: {len(caps)} native capture(s) registered; stage-route item 5 "
                   f"registers {SB.MIN_NATIVE_ENDS} native replays of the recording")
    ends = []
    for k, cap in enumerate(caps):
        what = "the registered capture" if k == 0 else f"repeat {k - 1}"
        if not cap.get("capture") or not cap.get("sha256") \
                or sha256(root / str(cap["capture"])) != cap["sha256"]:
            out.append(f"{name}: {what}'s tick capture {cap.get('capture')} is missing, unpinned "
                       "or does not hash to its pin")
        for key, label in (("end", "end record"), ("stage_trace", "stage trace")):
            f = cap.get(key)
            if not isinstance(f, dict) or not f.get("file") or not f.get("sha256"):
                out.append(f"{name}: {what} registers no {label} with its sha256 ({key})")
            elif sha256(root / f["file"]) != f["sha256"]:
                out.append(f"{name}: {what}'s {label} {f['file']} is missing or does not hash "
                           "to its pin")
            elif key == "end":
                try:
                    ends.append(SB.read_end(root / f["file"]))
                except SystemExit as e:
                    out.append(f"{name}: {e}")
            elif not SB.beside(f["file"], cap.get("capture")):
                out.append(f"{name}: {what}'s stage trace {f['file']} is not beside its tick "
                           f"capture ({cap.get('capture')})")
        out += [f"{name}: {what}: {p}" for p in native_input_problems(entry, cap, root)]
    out += [f"{name}: {p}" for p in SB.native_run_problems(
        [c.get("capture") for c in caps],
        [c.get("end") if isinstance(c.get("end"), dict) else {} for c in caps])]
    goals = [e["goal_tick"] for e in ends]
    if goals and (len(set(goals)) != 1 or goals[0] <= 0):
        out.append(f"{name}: the native end records name goal ticks {goals}: stage-route item "
                   "5's replays set the goal latch on one tick, above zero")
    if any(e["misses"] for e in ends):
        out.append(f"{name}: a native replay found no recorded row on some input calls "
                   f"(misses {[e['misses'] for e in ends]}), so it did not replay the recording")
    return out


def capture_labels(entry: dict) -> list[dict]:
    """The residue labels for the tick capture: every one that does not name a
    stage-trace column."""
    return [x for x in entry.get("residues", [])
            if not str(x.get("field", "")).startswith(TRACE_PREFIX)]


def trace_labels(entry: dict) -> list[dict]:
    """The residue labels for the stage trace: `trace.<column>`."""
    return [x for x in entry.get("residues", [])
            if str(x.get("field", "")).startswith(TRACE_PREFIX)]


def read_trace_stream(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """A stage trace -- harness/replay/native_stage_trace.c's
    sa2-stage-trace.csv, or the lifted run's (wrap_tick_probe.c,
    SA2_STAGE_TRACE), one format (tools/s4/stage_boot.py TRACE_HEADER) -- as a
    stream compare() takes: ticks 1..N, each column named trace.<column>."""
    import stage_boot as SB           # here: stage_boot imports this module
    header, rows = read_capture(path)
    if header != SB.TRACE_HEADER:
        raise refuse(f"{path}: not a stage trace (its header is {header}, not "
                     f"{SB.TRACE_HEADER})")
    return [IDENTITY] + [TRACE_PREFIX + c for c in header[1:]], rows


def trace_repeats(entry: dict, header: list[str], native: list[tuple[int, list[str]]],
                  work: Path, root: Path | None = None) -> dict:
    """Every registered repeat's stage trace against the capture's, under the
    trace's policy, as native_repeats does for the tick capture: `judged` is
    how many leading ticks every repeat reproduces."""
    root = ROOT if root is None else root
    out, judged = [], len(native)
    want = (entry["native"].get("stage_trace") or {}).get("sha256")
    for k, rep in enumerate(entry["native"].get("repeats", [])):
        f = rep.get("stage_trace") or {}
        path = root / str(f.get("file") or "")
        if not f.get("file") or sha256(path) != f.get("sha256"):
            raise refuse(f"{entry['name']}: repeat {k}'s stage trace {f.get('file')} is missing "
                         "or does not hash to its pin")
        h2, rows = read_trace_stream(path)
        common = min(len(native), len(rows))
        report = split(header, normalise(header, native, TRACE_POLICY)[:common],
                       normalise(header, rows, TRACE_POLICY)[:common], work, f"trace-repeat{k}")
        tick = int(report["identity"][IDENTITY]) if report["status"] == "different" else None
        judged = min(judged, (tick - 1) if tick is not None else common)
        out.append({"stage_trace": f["file"], "ticks": len(rows), "compared_ticks": common,
                    "first_divergent_tick": tick, "byte_identical": f.get("sha256") == want})
    if not out:
        raise refuse(f"{entry['name']}: no native repeat is registered, so nothing shows the "
                     "stage trace repeats")
    return {"repeats": out, "judged_ticks": judged}


def compare_trace(entry: dict, lifted_trace: Path, work: Path, lifted_stop: str = "",
                  root: Path | None = None, residues: Path = RESIDUES) -> dict:
    """The lifted run's stage trace against the native capture's (the spec's
    section 3.2): tick, level, mode, state, in-game frames and the player's
    position exactly, and the player object by which allocation it names
    (TRACE_POLICY), over the ticks every repeat's trace reproduces. A
    divergence carries the field `trace.<column>`, is `bug` unless a residue
    label names that tick and field, and a lifted trace that ends first is a
    run that ended or was bounded, as for the tick capture."""
    root = ROOT if root is None else root
    f = entry["native"].get("stage_trace") or {}
    path = root / str(f.get("file") or "")
    if not f.get("file") or sha256(path) != f.get("sha256"):
        raise refuse(f"{entry['name']}: its stage trace {f.get('file')} is missing or does not "
                     "hash to its pin")
    header, native = read_trace_stream(path)
    det = trace_repeats(entry, header, native, work, root)
    lheader, lrows = read_trace_stream(lifted_trace)
    result = compare(header, native, lheader, lrows, TRACE_POLICY, trace_labels(entry), work,
                     judged=det["judged_ticks"], residues=residues, name=entry["name"],
                     lifted_stop=lifted_stop)
    result["determinism"] = det
    return result


def first_reported(result: dict) -> tuple[dict | None, str | None]:
    """The divergence M5 reports for a replay's result, and its class.

    For boot, the tick capture's (`reported`, `class`). For a stage entry, the
    earlier of the tick capture's and the stage trace's (`result.trace`), and
    an unlabelled divergence on either before any residue on either: compare()
    reports a bug after a residue rather than the residue, and so does this,
    or a residue in one stream would hide a bug in the other. On a tie the
    tick capture's comes first."""
    rep, cls = result.get("reported"), result.get("class")
    trace = result.get("trace")
    if not trace:
        return rep, cls
    divs = [(d, c) for d, c in ((result.get("first_divergence"), cls),
                                (trace.get("first_divergence"), trace.get("class"))) if d]
    if divs:
        return min(divs, key=lambda dc: dc[0]["tick"])
    masked = [(d, c) for d, c in ((rep, cls), (trace.get("reported"), trace.get("class"))) if d]
    return min(masked, key=lambda dc: dc[0]["tick"]) if masked else (None, None)


def read_applied(path: Path) -> list[tuple[int, int, int, list[int]]]:
    """harness/replay/wrap_input_replay.c's SA2_INPUT_APPLIED log, the native
    replay's sa2-input-applied.csv format: (tick, ordinal, matched, values)
    per controller update. Anything the wrap cannot have written is refused."""
    if not path.is_file():
        raise refuse(f"{path}: no applied-input log")
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != APPLIED_HEADER:
        raise refuse(f"{path}: the header is not {','.join(APPLIED_HEADER)}")
    out = []
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != len(APPLIED_HEADER) or r[2] not in ("0", "1") \
                or not r[0].isdigit() or not r[1].isdigit() \
                or not all(v.startswith("0x") for v in r[3:]):
            raise refuse(f"{path}:{n}: not a row wrap_input_replay.c writes")
        try:
            values = [int(v, 16) for v in r[3:]]
        except ValueError:
            raise refuse(f"{path}:{n}: not a row wrap_input_replay.c writes") from None
        out.append((int(r[0]), int(r[1]), int(r[2]), values))
    return out


LIFTED_SIDE = "the lifted run"


def input_record(entry: dict, applied: Path, native_ticks: int,
                 root: Path | None = None, side: str = LIFTED_SIDE) -> dict:
    """The input the lifted run applied, held to the recording up to the native
    capture's last tick (the spec's section 2.4): every call found its recorded
    row (the native replays' end records say they missed none), and every
    recorded row was applied, in order, with its values, so a keying drift is
    never read as a game bug. `problems` is [] when both hold. The recording is
    read by tools/oracle/record_stage.py recorded_rows, the one reader of
    wrap_input_replay.c's grammar. `side` names whose log it is: a native
    replay of a stage entry is held to the same rule (native_input_problems)."""
    import record_stage as RS         # tools/oracle, on this module's path
    root = ROOT if root is None else root
    rec = {"applied": {"path": DP.rel(applied, root), "sha256": sha256(applied)},
           "judged_to_tick": native_ticks, "recorded_rows": None, "calls": None,
           "applied_rows": None, "misses": None, "problems": []}
    try:
        want = [r for r in RS.recorded_rows(root / entry["input"]["script"])
                if r[0] <= native_ticks]
    except SystemExit as e:
        rec["problems"].append(f"the recording's input cannot be read: {e.code}")
        return rec
    except OSError as e:
        rec["problems"].append(f"the recording's input cannot be read: {e}")
        return rec
    try:
        calls = [c for c in read_applied(applied) if c[0] <= native_ticks]
    except SystemExit as e:
        rec["problems"].append(str(e.code))
        return rec
    misses = [(t, o) for t, o, m, _ in calls if not m]
    got = [[t, o] + v for t, o, m, v in calls if m]
    rec.update(recorded_rows=len(want), calls=len(calls), applied_rows=len(got),
               misses=len(misses))
    if misses:
        rec["problems"].append(f"{len(misses)} of {side}'s {len(calls)} input calls up "
                               f"to tick {native_ticks} found no recorded row, the first at tick "
                               f"{misses[0][0]} call {misses[0][1]}"
                               + ("; the native replays missed none" if side == LIFTED_SIDE
                                  else ""))
    for k, (a, b) in enumerate(zip(want, got)):
        if a != b:
            rec["problems"].append(
                f"{side} applied tick {b[0]} call {b[1]} where the recording's row "
                f"{k + 1} is tick {a[0]} call {a[1]}" if a[:2] != b[:2] else
                f"{side} applied other values than the recording's at tick {a[0]} "
                f"call {a[1]}")
            break
    else:
        if len(got) != len(want):
            rec["problems"].append(f"{side} applied {len(got)} recorded rows up to tick "
                                   f"{native_ticks}, and the recording holds {len(want)}")
    return rec


# ---------------------------------------------------------------- lifted run

def native_device_profile(entry: dict) -> dict:
    """The device profile the replay's lifted run reads: its native capture's,
    as the registry names it and the capture's receipt records it, the same on
    every repeat, and one the runtime can reproduce (tools/s4/device_profile.py)."""
    try:
        return DP.replay_profile(entry, ROOT)
    except DP.Refusal as e:
        raise refuse(str(e)) from None


def use_shipped_exe(overlay: str | None = None) -> None:
    """Make every lifted run of this process load the shipped executable through the overlay
    table `overlay` (link_s4.py's default when None), into OUT_SHIPPED and a build of its own."""
    global OUT, BUILD
    EXE.update(kind="shipped", overlay=overlay)
    OUT = OUT_SHIPPED
    BUILD = f"replay.{RR.TAG}.shipped"


def exe_args() -> list[str]:
    """loop_run.py's arguments for the executable EXE names: none for the unpacked one."""
    if EXE["kind"] != "shipped":
        return []
    return ["--exe", "shipped", "--exe-overlay"] + ([EXE["overlay"]] if EXE["overlay"] else [])


def run_prefix(name: str) -> str:
    """The build and run-record prefix of replay `name`'s lifted run: replay-<name>, and
    replay-shipped-<name> for the shipped executable, so neither run replaces the other's."""
    return f"replay-{name}" if EXE["kind"] == "unpacked" else f"replay-shipped-{name}"


def exe_record(d: dict) -> dict:
    """What a shipped run's result keeps of its record's `exe` block (harness/crt_probe.c):
    which file the loader mapped and the overlay's identity. {} for the unpacked executable,
    so the registered replays' results are what they were."""
    if EXE["kind"] == "unpacked":
        return {}
    exe = d.get("exe") or {}
    if exe.get("kind") != "shipped":
        raise refuse(f"the lifted run asked for the shipped executable and its record says "
                     f"the loader mapped {exe.get('kind')!r}")
    return {"exe": exe}


def run_lifted(name: str, extra_env: dict[str, str] | None = None, *,
               device_profile: dict | str, words: bool = False) -> dict:
    """The replay's lifted run. `extra_env` adds to the hook's environment only
    (tools/s4/snapshot_fields.py passes SA2_REPLAY_SNAPSHOTS); the reach
    configuration itself is RR.ENV and is never overridden. `device_profile` is
    what native_device_profile returned, or DP.DEFAULT for a run that stands
    for no capture; it has no default, so no caller runs on the default by
    forgetting to say which. What a run on it sets beside RR.ENV -- the
    profile's path, and any device switch RR.ENV leaves off, which is none -- is
    RR.profile_env's, the one definition the reach run takes too, so the hooked
    run and the reach run it is held to are configured alike. The run starts
    from the pinned saves, as every native one does: the installation must be
    the pin and the write overlay's save directory is cleared first
    (tools/oracle/save_state.py reset_lifted), the overlay must then hold
    nothing, as the reach run's must (save_state.require_empty), and the result
    keeps what was cleared and left (`saves`). The run's own record must show
    every switch it was handed on (RR.switch_problems), as the reach run's
    must. With `words` (a word-policy entry's run) the hook also keeps the
    word log the comparison reads (SA2_REPLAY_WORDS, `keep_lifted_words`)."""
    extra = dict(extra_env or {})
    try:
        profile_env = RR.profile_env(device_profile)
    except DP.Refusal as e:
        raise refuse(str(e)) from None
    # The device profile, the host-device overrides beside it
    # (DP.HOST_OVERRIDES) and the devices a capture's profile switches on come
    # only from `device_profile`: a replay's devices are its capture's.
    clash = sorted(set(extra) & (set(RR.ENV) | {"SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"}
                                 | set(DP.OWNED) | set(profile_env)))
    if clash:
        raise refuse(f"extra_env may not override the replay's own settings: {clash}")
    out = OUT / name / "lifted.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)             # existence is the proof the hook wrote it
    # The word log (SA2_REPLAY_WORDS, wrap_tick_probe.c): what a word-policy
    # entry is compared from. The hook writes it as the host's file, so the
    # run is the same with it and without it (test_lifted_wordlog.py).
    log = out.parent / LIFTED_WORDS
    for f in (log, log.with_name(log.name + ".zst")):
        f.unlink(missing_ok=True)
    spec = "|".join(["ticks"] + [f"{k}={v}" for k, v in RR.ENV.items()]
                    + [f"SA2_REPLAY_TICKS={out.relative_to(ROOT)}"]
                    + ([f"SA2_REPLAY_WORDS={log.relative_to(ROOT)}"] if words else [])
                    + [f"{k}={v}" for k, v in profile_env.items()]
                    + [f"{k}={v}" for k, v in extra.items()])
    prefix = run_prefix(name)
    cmd = [str(PY), str(HERE / "loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN,
           "--name", BUILD,
           "--hook", HOOK, "--wrap", WRAP, "--out-prefix", prefix, "--run", spec] + exe_args()
    for a in RR.PROBE_ARGS:
        cmd.append(f"--probe-arg={a}")          # the `=` form; see reach_receipt.run
    run_json = RUNDIR / f"{prefix}.ticks.json"
    matrix = RUNDIR / f"{prefix}.matrix.json"
    run_json.unlink(missing_ok=True)
    matrix.unlink(missing_ok=True)
    try:
        saves = SS.reset_lifted(cmd, ROOT)
        SS.require_empty(saves)          # the reach run it is held to starts from an empty one
    except SS.SaveStateError as e:
        raise refuse(str(e.code)) from None
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=3600,
                       env=RR.clean_environ())
    if p.returncode or not run_json.is_file() or not matrix.is_file():
        raise refuse(f"the lifted run did not complete (rc {p.returncode})\n"
                     f"{p.stdout[-3000:]}\n{p.stderr[-2000:]}")
    if not out.is_file():
        raise refuse(f"the lifted run wrote no tick capture at {out}: the hook did not run")
    d = json.loads(run_json.read_text())
    try:
        read = DP.check_run(d, device_profile)
    except DP.Refusal as e:
        raise refuse(f"the lifted run is not the one asked for: {e}") from None
    off = RR.switch_problems(d, {**RR.ENV, **profile_env, **extra})
    if off:
        raise refuse(f"the lifted run is not the one asked for: its devices are not its "
                     f"configuration's: {'; '.join(off)}")
    trap = d.get("trap") or {}
    kept = {"words": keep_lifted_words(out, log)} if words else {}
    return {
        **exe_record(d),
        "device_profile": read,
        # The directory and executable the run answered (DP.check_run held them
        # to the capture's): what its record says it ran in.
        "guest_paths": DP.run_guest(d),
        "saves": saves,
        "capture": str(out.relative_to(ROOT)),
        "sha256": sha256(out),
        **kept,
        "binary_sha256": json.loads(matrix.read_text())["sha256"],
        "run_json": str(run_json.relative_to(ROOT)),
        "reach": reach_of(d),
        "poll_handoff": poll_counters(d),
        "stop": {"note": stop_note(d), "exit_pc": trap.get("exit_pc"),
                 "esp": trap.get("esp"), "stack": trap.get("stack", [])},
    }


def keep_lifted_words(capture: Path, words: Path) -> dict:
    """The lifted run's word log, checked against its tick capture as the
    runner checks a native one (tools/s4/wordlog.py check_capture, the lifted
    rules: whole, not overflowed, the named fields equal to the log's bytes on
    every tick with the one-tick-shift control, and the hook's own digest
    columns equal to the host's recompute), then kept compressed beside it:
    {path, sha256, check}. A run whose log is missing or fails is refused."""
    if not words.is_file():
        raise refuse(f"the lifted run wrote no word log at {words}: SA2_REPLAY_WORDS did not run")
    got = WL.check_capture(capture, words, native=False)
    if not got["ok"]:
        raise refuse(f"the lifted run's word log does not hold against its tick capture: "
                     f"{'; '.join(got['problems'])}")
    kept = words.with_name(words.name + ".zst")
    p = subprocess.run(["zstd", "-3", "-q", "-f", "--rm", str(words), "-o", str(kept)],
                       capture_output=True, text=True)
    if p.returncode or not kept.is_file():
        raise refuse(f"zstd could not keep {words}: {p.stderr[-300:]}")
    return {"path": str(kept.relative_to(ROOT)), "sha256": sha256(kept),
            "check": {k: got.get(k) for k in ("ticks", "log_sha256", "named_cells_compared",
                                              "named_cells_differing", "digest_cells_compared",
                                              "digest_cells_differing")}}


def reach_of(run: dict) -> dict:
    """REACH_KEYS from a run's own record, as the reach receipt reads them."""
    return {
        "iterations": (run.get("pump") or {}).get("iterations", 0),
        "import_calls": (run.get("calls") or {}).get("total", 0),
        "frames_presented": ((run.get("d3d9") or {}).get("frames") or {}).get("present", 0),
        "stop": (run.get("trap") or {}).get("note", "")[:300],
    }


def poll_counters(run: dict) -> dict | None:
    """The guest-poll rule's counters from a run's own record (harness/crt_probe.c
    `threads.poll_handoff`, runtime/sa2_threads.h sa2_sched_poll), None when it has
    none. Each lifted run's are recorded with its reach, so the registration pins
    them: the boot's record has no pin of its own, and
    Documentation/scripts/check_poll_handoff.py holds every registered run's record
    to what its registration recorded."""
    ph = (run.get("threads") or {}).get("poll_handoff")
    return ph if isinstance(ph, dict) else None


def stop_note(run: dict) -> str:
    """Why the lifted run ended: the host's WM_QUIT at the poll budget when it
    posted one, with whatever the guest's exit path met after it, else the trap."""
    pump = run.get("pump") or {}
    note = (run.get("trap") or {}).get("note", "")
    if pump.get("quit_by_host"):
        return (f"{QUIT_NOTE}{pump.get('poll_limit')} (SA2_PUMP_POLLS); the guest then left "
                f"its loop through its own code" + (f", and its exit path met: {note}" if note else ""))
    return note


def poll_budget(entry: dict | None = None) -> int:
    """The poll budget an entry's lifted run is given: a stage entry's own
    (stage_poll_budget), and otherwise the reach configuration's, or the
    runtime's default."""
    if entry is not None and is_stage(entry):
        return stage_poll_budget(entry)
    return int(RR.ENV.get("SA2_PUMP_POLLS", "600"), 0)


def stage_poll_budget(entry: dict, root: Path | None = None) -> int:
    """A stage entry's poll budget: its native capture's rows and
    STAGE_LEASH_MARGIN, so the host's WM_QUIT comes after every native row
    and the goal (the spec's section 2.2)."""
    root = ROOT if root is None else root
    return len(read_capture(root / entry["native"]["capture"])[1]) + STAGE_LEASH_MARGIN


def run_stage_lifted(entry: dict, profile: dict, *, observe: bool) -> dict:
    """A stage entry's lifted run, built by tools/s4/stage_run.py `command`, the
    one definition of a stage run: the entry's stage through the test spawn,
    the recording's input, the capture's device profile, its own build name
    (replay-<entry>) and output directory, never the walk's.

    `observe` True writes the tick capture, stage trace, goal-latch column and
    applied-input log beside each other in OUT/<entry>; False writes none of
    them, into OUT/<entry>/baseline, and is the baseline the observed run is
    held to (non_perturbation_stage), since a stage has no reach receipt. Both
    dump the runtime's import table. Guards as the reach run's: no link from
    objects the map or arms have since replaced (rebuild_image), the pinned
    saves and an overlay that holds nothing else (save_state reset_lifted and
    require_empty), no device variable from this shell (RR.clean_environ), and
    a run record that read its profile (DP.check_run) and shows every switch
    it was handed on (RR.switch_problems)."""
    import rebuild_image as RI
    import stage_run as SR           # here: stage_run imports this module
    name = entry["name"]
    script = ROOT / entry["input"]["script"]
    if sha256(script) != entry["input"]["sha256"]:
        raise refuse(f"{name}: its input {entry['input']['script']} is missing or does not hash "
                     "to its pin")
    polls = stage_poll_budget(entry)
    out = OUT / name if observe else OUT / name / "baseline"
    trace = out / "stage-trace.csv"
    # A word-policy entry is compared from both sides' word logs, so its observed
    # run keeps one (SA2_REPLAY_WORDS); the baseline observes nothing.
    words = out / LIFTED_WORDS if observe and "word_policy" in entry else None
    alarm = max(600, polls // 5)       # at least 5 ticks a second; the stage boot ran 11.7
    build = run_prefix(name)
    watch = {"SA2_STAGE_TRACE": str(trace.relative_to(ROOT))}
    if words is not None:
        watch["SA2_REPLAY_WORDS"] = str(words.relative_to(ROOT))
    cmd, run_json, ticks = SR.command(
        entry["stage"]["name"], polls, label="ticks" if observe else "baseline",
        input_script=script, extra_env=watch if observe else None,
        timeout=alarm, device_profile=profile, name=build, out_dir=out, observe=observe,
        exe=EXE["kind"], exe_overlay=EXE["overlay"])
    table = out / "imports.table.json"
    # The dump is an output, as in reach_receipt.run: the `=` form, since
    # argparse refuses a value that starts with `--` in the other.
    cmd += ["--probe-arg=--dump-imports", f"--probe-arg={table.relative_to(ROOT)}"]
    stale = RI.staged_not_rebuilt()
    if stale:
        raise refuse(f"{len(stale)} object(s) the link reads are not the current map or arm "
                     f"objects (first {stale[:3]}): run tools/s3/arms/stage_merged_build.py "
                     f"--tag next --merged-tag {RR.TAG} --force")
    matrix = RUNDIR / f"{build}.matrix.json"
    written = {"tick capture": ticks, "stage trace": trace, "goal-latch column":
               out / "goal-latch.csv", "applied-input log": out / "input-applied.csv"}
    if words is not None:
        written["word log"] = words
    for f in [run_json, matrix, table, *written.values()] + (
            [words.with_name(words.name + ".zst")] if words is not None else []):
        f.unlink(missing_ok=True)       # existence is the proof this run wrote it
    try:
        saves = SS.reset_lifted(cmd, ROOT)
        SS.require_empty(saves)
    except SS.SaveStateError as e:
        raise refuse(str(e.code)) from None
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=alarm + 900,
                       env=RR.clean_environ())
    if p.returncode or not run_json.is_file() or not matrix.is_file():
        raise refuse(f"{name}: the lifted stage run did not complete (rc {p.returncode})\n"
                     f"{p.stdout[-3000:]}\n{p.stderr[-2000:]}")
    if not table.is_file() or not json.loads(table.read_text()).get("rows"):
        raise refuse(f"{name}: the lifted stage run dumped no import table at {table}")
    if observe:
        missing = [what for what, f in written.items() if not f.is_file()]
        if missing:
            raise refuse(f"{name}: the observed run wrote no {', '.join(missing)}: a hook did "
                         "not run")
    d = json.loads(run_json.read_text())
    try:
        read = DP.check_run(d, profile)
    except DP.Refusal as e:
        raise refuse(f"the lifted run is not the one asked for: {e}") from None
    off = RR.switch_problems(d, {**RR.ENV, **RR.profile_env(profile)})
    if off:
        raise refuse(f"the lifted run is not the one asked for: its devices are not its "
                     f"configuration's: {'; '.join(off)}")
    rec = {**exe_record(d),
           "polls": polls, "device_profile": read, "guest_paths": DP.run_guest(d),
           "saves": saves,
           "run_json": str(run_json.relative_to(ROOT)), "run_json_sha256": sha256(run_json),
           "binary_sha256": json.loads(matrix.read_text())["sha256"],
           "reach": reach_of(d), "poll_handoff": poll_counters(d),
           "import_table": {"path": str(table.relative_to(ROOT)), "sha256": sha256(table)}}
    if not observe:
        return rec
    trap = d.get("trap") or {}
    def pinned(f: Path) -> dict:
        return {"path": str(f.relative_to(ROOT)), "sha256": sha256(f)}
    if words is not None:
        rec["words"] = keep_lifted_words(ticks, words)
    return dict(rec, capture=str(ticks.relative_to(ROOT)), sha256=sha256(ticks),
                stop={"note": stop_note(d), "exit_pc": trap.get("exit_pc"),
                      "esp": trap.get("esp"), "stack": trap.get("stack", [])},
                goal_latch=pinned(written["goal-latch column"]),
                stage_trace=pinned(trace),
                input_applied=pinned(written["applied-input log"]))


# What the observed stage run is held to its baseline on, beside REACH_KEYS.
STAGE_BASELINE_KEYS = ("device_profile", "guest_paths", "import_table", "binary_sha256")


def non_perturbation_stage(lifted: dict, base: dict | None, name: str = "<replay>") -> dict:
    """A stage entry's observed run against its baseline, the same build and
    configuration with the observers off (the spec's section 2.3): every
    REACH_KEYS field, the device profile read, the import table (so the
    observers changed which imports run nowhere, and the observed run's table
    stands for the configuration's in M4) and the binary. What it cannot see:
    both runs link the observers' wraps, so it shows what switching them on
    does, not what linking them does."""
    if not isinstance(base, dict):
        raise refuse(f"{name}: no baseline run is recorded, so nothing shows the observers left "
                     "the stage run unchanged")
    diff = {k: {"baseline": (base.get("reach") or {}).get(k), "observed": lifted["reach"].get(k)}
            for k in REACH_KEYS if (base.get("reach") or {}).get(k) != lifted["reach"].get(k)}
    if not DP.same(base.get("device_profile"), lifted.get("device_profile")):
        diff["device_profile"] = {"baseline": DP.describe(base.get("device_profile")),
                                  "observed": DP.describe(lifted.get("device_profile"))}
    if not lifted.get("guest_paths") or base.get("guest_paths") != lifted.get("guest_paths"):
        diff["guest_paths"] = {"baseline": base.get("guest_paths"),
                               "observed": lifted.get("guest_paths")}
    tables = [(r.get("import_table") or {}).get("sha256") for r in (base, lifted)]
    if not tables[0] or tables[0] != tables[1]:
        diff["import_table"] = {"baseline": tables[0], "observed": tables[1]}
    binaries = [r.get("binary_sha256") for r in (base, lifted)]
    if not binaries[0] or binaries[0] != binaries[1]:
        diff["binary_sha256"] = {"baseline": binaries[0], "observed": binaries[1]}
    if diff:
        raise refuse(f"{name}: the observed stage run is not its baseline, so the observers "
                     f"perturbed it or the two are not one build: {json.dumps(diff)}")
    return {"against": "the same build and configuration with the observers off "
                       "(lifted.baseline)",
            "fields": list(REACH_KEYS) + list(STAGE_BASELINE_KEYS), "equal": True}


def stage_recheck(entry: dict, lifted: dict, native_ticks: int,
                  root: Path | None = None) -> list[str]:
    """What a recorded stage result rests on, re-checked from its kept files, []
    when it holds: every file the observed and baseline runs recorded is its
    pin (the tick capture, both run records and import tables, the stage trace,
    goal-latch column and applied-input log); the observers' files sit beside
    the tick capture and the baseline's table in its baseline directory; each
    run's reach fields and device profile are its own run record's; the
    observed run is its baseline (non_perturbation_stage); the input it
    applied is the recording's (input_record); and its poll budget outlasted
    the native capture. `--no-run` refuses on any, and
    Documentation/scripts/check_replay_results.py fails on any."""
    import stage_boot as SB           # here: stage_boot imports this module
    root = ROOT if root is None else root
    name = entry["name"]
    out: list[str] = []
    base = lifted.get("baseline") if isinstance(lifted.get("baseline"), dict) else {}
    if not base:
        out.append(f"{name}: no baseline run is recorded, so nothing shows the observers left "
                   "the stage run unchanged")
    files = [("the lifted tick capture", lifted.get("capture"), lifted.get("sha256")),
             ("the observed run record", lifted.get("run_json"), lifted.get("run_json_sha256")),
             ("the baseline run record", base.get("run_json"), base.get("run_json_sha256"))]
    for who, rec in (("observed", lifted), ("baseline", base)):
        t = rec.get("import_table") or {}
        files.append((f"the {who} run's import table", t.get("path"), t.get("sha256")))
    for key, what in (("goal_latch", "goal-latch column"), ("stage_trace", "stage trace"),
                      ("input_applied", "applied-input log")):
        f = lifted.get(key) or {}
        files.append((f"the {what}", f.get("path"), f.get("sha256")))
        if not SB.beside(f.get("path"), lifted.get("capture")):
            out.append(f"{name}: the {what} ({f.get('path')}) is not beside the lifted tick "
                       f"capture ({lifted.get('capture')})")
    if not SB.beside((lifted.get("import_table") or {}).get("path"), lifted.get("capture")):
        out.append(f"{name}: the observed run's import table is not beside its tick capture")
    if "word_policy" in entry:
        # the observed run's word log, which a word-policy entry is compared from
        w = lifted.get("words") or {}
        files.append(("the lifted word log", w.get("path"), w.get("sha256")))
        if not SB.beside(w.get("path"), lifted.get("capture")):
            out.append(f"{name}: the lifted word log ({w.get('path')}) is not beside the lifted "
                       f"tick capture ({lifted.get('capture')})")
    table = (base.get("import_table") or {}).get("path")
    if not table or run_dir(table) != os.path.join(run_dir(str(lifted.get("capture"))),
                                                   "baseline"):
        out.append(f"{name}: the baseline run's import table ({table}) is not in the baseline "
                   "directory beside the lifted tick capture")
    bad = [f"{what} {rel}" for what, rel, pin in files
           if not rel or not pin or sha256(root / str(rel)) != pin]
    if bad:
        return out + [f"{name}: {'; '.join(bad)} is missing, unpinned or does not hash to its "
                      "pin"]
    for who, rec in (("observed", lifted), ("baseline", base)):
        d = json.loads((root / rec["run_json"]).read_text())
        if reach_of(d) != rec.get("reach"):
            out.append(f"{name}: the {who} run's recorded reach {rec.get('reach')} is not its "
                       f"run record's {reach_of(d)}")
        try:
            read = DP.run_record(d)
        except DP.Refusal as e:
            read = str(e)
        if read != rec.get("device_profile"):
            out.append(f"{name}: the {who} run's recorded device profile is not its run "
                       "record's")
        try:
            ran_in = DP.run_guest(d)
        except DP.Refusal as e:
            ran_in = str(e)
        if ran_in != rec.get("guest_paths"):
            out.append(f"{name}: the {who} run's recorded guest directory is not its run "
                       "record's")
    try:
        non_perturbation_stage(lifted, base, name)
    except SystemExit as e:
        out.append(str(e.code))
    again = input_record(entry, root / lifted["input_applied"]["path"], native_ticks, root)
    if again != lifted.get("input"):
        out.append(f"{name}: the recorded input record is not what the kept applied-input log "
                   "gives")
    out += [f"{name}: {p}" for p in again["problems"]]
    if not isinstance(lifted.get("polls"), int) or lifted["polls"] <= native_ticks:
        out.append(f"{name}: the lifted run's poll budget ({lifted.get('polls')}) does not "
                   f"outlast the native capture's {native_ticks} ticks, so the replay would "
                   "judge the harness's leash")
    return out


def non_perturbation(lifted: dict, reach: Path = REACH, name: str = "<replay>") -> dict:
    """The hooked run against the reach receipt, field for field -- after the
    device profile, since a run on another device set makes other imports (the
    joystick scan's WMI query, for one) and the fields below would differ for
    that reason alone."""
    if not reach.is_file():
        raise refuse(f"{reach} is absent; there is nothing to show the hook left the run "
                     "unchanged against")
    recorded = json.loads(reach.read_text())["reach"]
    # A record from before device profiles carries neither side; one that
    # carries either must carry both, and the same.
    if "device_profile" in recorded or "device_profile" in lifted:
        was, now = recorded.get("device_profile"), lifted.get("device_profile")
        if not DP.same(was, now):
            raise refuse(f"the reach receipt was measured on device profile {DP.describe(was)} "
                         f"and {name} runs on {DP.describe(now)}, so the two are different runs "
                         f"before the hook is considered: re-run tools/s4/reach_receipt.py "
                         f"--replay {name} --write")
    # And in one directory: the command line the runtime builds from the
    # executable's path is parsed character by character by the CRT, so the
    # import calls differ with the directory alone (2026-09-24, 708 calls). A
    # record from before guest paths is refused: the reach receipt is regenerated
    # with them, so both sides must carry the directory, and the same one (the
    # working-directory review, W2).
    was, now = recorded.get("guest_paths"), lifted.get("guest_paths")
    if not was or was != now:
        raise refuse(f"the reach receipt was measured in guest directory {was} and {name} "
                     f"runs in {now}, so the two are different runs before the hook is "
                     f"considered: re-run tools/s4/reach_receipt.py --replay {name} --write")
    diff = {k: {"reach_receipt": recorded.get(k), "hooked": lifted["reach"].get(k)}
            for k in REACH_KEYS if recorded.get(k) != lifted["reach"].get(k)}
    if diff:
        raise refuse("the hooked run is not the reach run, so the hook perturbed it or the "
                     f"build moved without the reach receipt: {json.dumps(diff)}")
    return {"against": "Documentation/receipts/s4-run-reach-v1.json",
            "fields": list(REACH_KEYS), "equal": True}


# ------------------------------------------------------ the word-policy comparison

def column_of(va: int, mask_words: set[int]) -> str | None:
    """The digest column a word is hashed into under a mask, None when the
    mask does not select it."""
    return f"d_{va & ~0xFFFF:08x}" if va in mask_words else None


def group_diffs(a: Path, b: Path, groups: list[dict], upto: int,
                mask_words: set[int]) -> list[dict]:
    """Every listed word of each group the two logs disagree on at some tick
    of 1..`upto`, read from one wordlog diff of the whole section: per group
    how many, the first (by tick, then address) with both values, the first
    the replay's own mask selects (`mask_words`, its digest column), and the
    sha256 of the whole list, so a recompute holds every word and not only
    the ones named. "native" is `a`."""
    rows, meta = WL.diff(a, b)
    if meta["compared_through"] < upto:
        raise refuse(f"{b}: compared through tick {meta['compared_through']}, not {upto}")
    at = {r["va"]: r for r in rows if r["first_tick"] <= upto}
    out = []
    for g in groups:
        apart = sorted((at[va] for va in g["words"] if va in at),
                       key=lambda r: (r["first_tick"], r["va"]))
        listing = [[f"0x{r['va']:08x}", r["first_tick"], f"0x{r['a']:08x}", f"0x{r['b']:08x}"]
                   for r in apart]
        inmask = [r for r in apart if r["va"] in mask_words]
        out.append({
            "record": g["record"], "group": g["group"], "listed": len(g["words"]),
            "in_mask": sum(1 for va in g["words"] if va in mask_words),
            "differing": len(apart),
            "first": ({"tick": apart[0]["first_tick"], "va": f"0x{apart[0]['va']:08x}",
                       "native": f"0x{apart[0]['a']:08x}", "lifted": f"0x{apart[0]['b']:08x}"}
                      if apart else None),
            "first_in_mask": ({"tick": inmask[0]["first_tick"], "va": f"0x{inmask[0]['va']:08x}",
                               "column": column_of(inmask[0]["va"], mask_words)}
                              if inmask else None),
            "words": listing[:RESIDUE_LISTED],
            "listing_sha256": hashlib.sha256(json.dumps(listing).encode()).hexdigest()})
    return out


def hexes(values) -> list[str]:
    return [f"0x{v:08x}" for v in values]


def word_rows(log: Path, words: list[int]) -> dict[int, tuple[int, ...]]:
    """tick -> the words' values, every tick the log holds from 1."""
    values = WL.field_values(log, [(f"{RULE_FIELD}{va:08x}", va, 4) for va in words])
    return {t: tuple(int(c, 16) for c in row) for t, row in values.items()}


def scan_verdict(params: dict, observed: dict) -> dict | None:
    """Where a one-tick-scan rule breaks on what was observed (NATIVE_FORMS),
    None when it holds: the first tick on which the group differs from native
    past `max_ticks` of them, or on which the lifted copies are not one value,
    a native word is not at `reset`, or the lifted progress word is not moving
    (at `reset`, or the same on the next tick). The comparer and
    `rule_problems` both read the verdict from here."""
    words = [int(w, 16) for w in observed["words"]]
    copies = params["entries"]
    per = len(words) // copies
    reset = int(str(params["reset"]), 16)
    for i, a in enumerate(observed["apart"]):
        lv, nv = [int(v, 16) for v in a["lifted"]], [int(v, 16) for v in a["native"]]
        p0, p1 = a["progress"]
        why = None
        if i >= params["max_ticks"]:
            why = f"the group differs from native on more than {params['max_ticks']} tick(s)"
        elif len({tuple(lv[k * per:(k + 1) * per]) for k in range(copies)}) != 1:
            why = "the lifted copies are not one value"
        elif any(v != reset for v in nv):
            why = f"the native is not at the reset value {params['reset']}"
        elif p1 is None or p0 == p1 or int(p0, 16) == reset:
            why = (f"the lifted progress word {params['progress']} is not moving at the sample "
                   f"({p0} then {p1})")
        if why:
            k = next((j for j in range(len(words)) if lv[j] != nv[j]), 0)
            return {"tick": a["tick"], "va": f"0x{words[k]:08x}", "value": f"0x{lv[k]:08x}",
                    "native": f"0x{nv[k]:08x}", "why": why}
    return None


def permutation_verdict(params: dict, observed: dict) -> dict | None:
    """Where a context-permutation rule breaks on what was observed (NATIVE_FORMS),
    None when it holds: the earliest of the first tick up to and at `kept_from` the
    group differs from native, the first a side's contexts are not one decode and
    copies, the first the lifted decode is not the native one in any context (and
    whether it is the native decode of the tick before or after: a decode a tick
    apart, not a wrong one), the first a side's copies are not its dispatcher
    context of the tick before, the tick a run of differing ticks passes
    `episode_max`, and, with `uploads`, the first upload out of the capture's order
    or on a tick the record's effect does not list (`uploads_verdict`). The comparer
    and `rule_problems` both read it from here."""
    found = []
    for key, why in (
            ("pre", f"the group differs from native at or before tick {params['kept_from']}, "
                    "when the contexts are created"),
            ("shape", f"the {{side}} contexts are not one decode and {params['entries'] - 1} "
                      "equal copies"),
            ("decode", "the lifted decode is not the native one in any context"),
            ("copy", f"the {{side}} copies are not context {params['dispatcher']}'s values of "
                     "the tick before (the dispatcher's copy)")):
        first = observed[key]["first"]
        if first:
            near = first.get("near")
            found.append(dict({k: first[k] for k in ("tick", "va", "value", "native")},
                              why=why.format(side=first.get("side")) + (
                                  f" (it is the native decode of tick {near}: the same decode a "
                                  "tick apart)" if near else "")))
    long = observed["episodes"]["first_long"]
    if long:
        found.append(dict({k: long[k] for k in ("tick", "va", "value", "native")},
                          why=f"ticks {long['start']}..{long['end']} differ, a run longer than "
                              f"{params['episode_max']}"))
    if params.get("uploads") is not None:
        b = uploads_verdict(params["uploads"], observed["uploads"])
        if b:
            found.append(b)
    return min(found, key=lambda b: (b["tick"], b["va"]), default=None)


def upload_list(text: str) -> list[tuple[int, int]]:
    """An uploads observation's "tick:frame ..." string, as (tick, frame) pairs."""
    return [(int(a), int(b)) for a, b in (x.split(":") for x in str(text).split())]


def uploads_observed(log: Path, native: Path, word: str, upto: int) -> dict:
    """The movie's uploads on both sides over ticks 1..`upto` (`through`): every tick
    the frame word advances on, with the frame it advances to ("tick:frame ..." per
    side), and its value before the first (`initial`). Which uploads the two sides make
    on different ticks is not recorded beside them: `uploads_verdict` derives it from
    these on every read, so the lists are the one thing a recorded verdict rests on."""
    va = int(word, 16)
    out: dict = {"word": word, "through": upto}
    for side, path in (("lifted", log), ("native", native)):
        ch = [(t, old, new) for t, a, old, new in WL.changes(path, va, va + 4)
              if a == va and 0 < t <= upto]
        start = ch[0][1] if ch else word_rows(path, [va])[1][0]
        out[side] = " ".join(f"{t}:{new}" for t, _, new in ch)
        out[f"{side}_uploads"] = len(ch)
        out.setdefault("initial", []).append(f"0x{start:08x}")
    return out


def uploads_verdict(params: dict, observed: dict) -> dict | None:
    """Where the uploads clause of a context-permutation rule breaks, None when it
    holds: the lifted uploads are the capture's frames in its order, and the ones on
    another tick than the capture's are exactly the record's effect lists
    (`params`: its frame word and `moved`, [frame, lifted tick, native tick] each),
    so a runtime change that moves another upload, or brings a listed one back,
    breaks it at the earlier of its two ticks. A listed upload whose ticks both lie
    past the ticks observed (`through`) is not asked for; one that straddles them is
    an upload one side has made and the other has not, which breaks the order."""
    word = params["word"]
    lifted, nat = upload_list(observed["lifted"]), upload_list(observed["native"])
    start = [int(v, 16) for v in observed["initial"]]

    def value(seq, first, t):
        v = first
        for tt, f in seq:
            if tt > t:
                break
            v = f
        return v

    def broken(t, why):
        return {"tick": t, "va": word, "value": f"0x{value(lifted, start[0], t):08x}",
                "native": f"0x{value(nat, start[1], t):08x}", "why": why}
    for i in range(max(len(lifted), len(nat))):
        a = lifted[i] if i < len(lifted) else None
        b = nat[i] if i < len(nat) else None
        if a is None or b is None or a[1] != b[1]:
            t = min(x[0] for x in (a, b) if x is not None)
            return broken(t, f"the movie's uploads ({word}) are not the capture's frames in its "
                             f"order: upload {i + 1} is frame {a[1] if a else 'none'} lifted and "
                             f"{b[1] if b else 'none'} native")
    moved = [[a[1], a[0], b[0]] for a, b in zip(lifted, nat) if a[0] != b[0]]
    want = [list(w) for w in params["moved"] if max(w[1], w[2]) <= observed["through"]]
    if moved == want:
        return None
    cands = [(min(m[1], m[2]), f"frame {m[0]}'s upload is at tick {m[1]} lifted and {m[2]} "
                               "native, which the record's effect does not list: an observable "
                               "effect the class does not account for")
             for m in moved if m not in want]
    cands += [(min(w[1], w[2]), f"the record's effect lists frame {w[0]}'s upload at tick "
                                f"{w[1]} lifted and {w[2]} native, and the run does not move it so")
              for w in want if w not in moved]
    t, why = min(cands) if cands else (min(min(m[1], m[2]) for m in moved + want),
                                       "the moved uploads are not in the record's order")
    return broken(t, why)


def contexts_form(row: tuple, n: int, count: int) -> tuple | None:
    """One side's contexts at a tick as (decode, its context, copy): every context
    but one holds one value, the copies, and the one left the decode (context None
    when all hold one value, which is then decode and copy both); None when no
    such split exists."""
    ctxs = [row[k * n:(k + 1) * n] for k in range(count)]
    for w in range(count):
        rest = [ctxs[k] for k in range(count) if k != w]
        if all(c == rest[0] for c in rest[1:]):
            return ctxs[w], (None if ctxs[w] == rest[0] else w), rest[0]
    return None


def permutation_observed(words: list[int], entries: list[list[int]], lifted: dict,
                         nat: dict, ticks: list[int], params: dict) -> dict:
    """What a context-permutation rule observed over `ticks` of both sides' rows
    (NATIVE_FORMS): per clause the ticks it failed on and the first, as a broken
    word (the side named for a side's own clause), and which context held the
    decode on each tick a side's contexts changed ("same": all four agree)."""
    n, count = len(entries[0]), len(entries)
    disp, kept = params["dispatcher"], params["kept_from"]
    sides = {"lifted": lifted, "native": nat}

    def at(t, i, **extra):
        return dict({"tick": t, "va": f"0x{words[i]:08x}", "value": f"0x{lifted[t][i]:08x}",
                     "native": f"0x{nat[t][i]:08x}"}, **extra)

    def first_apart(t, **extra):
        i = next((i for i in range(len(words)) if lifted[t][i] != nat[t][i]), 0)
        return at(t, i, **extra)
    hits: dict[str, list[int]] = {k: [] for k in ("pre", "shape", "decode", "copy")}
    firsts: dict[str, dict] = {}
    held_by: dict[str, dict[str, int]] = {"lifted": {}, "native": {}}

    def hit(key, t, rec):
        hits[key].append(t)
        firsts.setdefault(key, rec)
    for t in ticks:
        if t <= kept:
            if lifted[t] != nat[t]:
                hit("pre", t, first_apart(t))
            continue
        forms = {s: contexts_form(rows[t], n, count) for s, rows in sides.items()}
        bad = [s for s in sides if forms[s] is None]
        if bad:
            hit("shape", t, first_apart(t, side=bad[0]))
            continue
        (dl, kl, _), (dn, _, _) = forms["lifted"], forms["native"]
        if dl != dn:
            j = next(j for j in range(n) if dl[j] != dn[j])
            i = (disp if kl is None else kl) * n + j
            # the native decode of the tick before or after, when the lifted one is it:
            # a decode a tick apart (the race comparer review's nit), not a wrong one
            near = next((u for u in (t - 1, t + 1) if u in nat and u > kept
                         and (contexts_form(nat[u], n, count) or (None,))[0] == dl), None)
            hit("decode", t, dict({"tick": t, "va": f"0x{words[i]:08x}",
                                   "value": f"0x{dl[j]:08x}", "native": f"0x{dn[j]:08x}"},
                                  **({"near": near} if near else {})))
        for s, rows in sides.items():
            if rows[t] == rows[t - 1]:
                continue
            decode, k, copy = forms[s]
            key = "same" if k is None else str(k)
            held_by[s][key] = held_by[s].get(key, 0) + 1
            before = rows[t - 1][disp * n:(disp + 1) * n]
            if copy != before and not any(t2 == t for t2 in hits["copy"]):
                c = next(c for c in range(count) if c != k)
                j = next(j for j in range(n) if copy[j] != before[j])
                hit("copy", t, at(t, c * n + j, side=s))
    return {**{k: {"ticks": len(v), "first": firsts.get(k)} for k, v in hits.items()},
            "decode_context": {s: dict(sorted(h.items())) for s, h in held_by.items()}}


def runs_of(ticks: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for t in ticks:
        if out and t == out[-1][1] + 1:
            out[-1] = (out[-1][0], t)
        else:
            out.append((t, t))
    return out


def evaluate_native(log: Path, native: Path, group: dict, upto: int) -> dict:
    """A native-relative rule (NATIVE_FORMS) over ticks 1..`upto` of the lifted
    log `log` and the native log `native`: what it observed, which
    `rule_problems` re-derives the verdict from, and the verdict
    (`scan_verdict`, `permutation_verdict`)."""
    rule, table = group["rule"], group["table"]
    params = native_params(rule, table, group.get("effect"))
    entries = table_entries(table)
    words = [va for e in entries for va in e]
    lifted, nat = word_rows(log, words), word_rows(native, words)
    ticks = [t for t in range(1, upto + 1)]
    if any(t not in lifted or t not in nat for t in ticks):
        raise refuse(f"{group['record']}/{group['group']}: the logs do not both hold ticks "
                     f"1..{upto}")
    apart = [t for t in ticks if lifted[t] != nat[t]]
    out: dict = {"kind": "native", "form": params["form"], "params": params}
    if params["form"] == "one-tick-scan":
        prog = int(str(rule["progress"]), 16)
        moving = {t: row[0] for t, row in word_rows(log, [prog]).items()}
        listed = apart[:params["max_ticks"] + 1]
        observed = {"words": hexes(words), "apart_ticks": len(apart), "apart": [
            {"tick": t, "lifted": hexes(lifted[t]), "native": hexes(nat[t]),
             "progress": [f"0x{moving[t]:08x}",
                          f"0x{moving[t + 1]:08x}" if t + 1 in moving else None]}
            for t in listed]}
        broken = scan_verdict(params, observed)
    else:
        observed = permutation_observed(words, entries, lifted, nat, ticks, params)
        eps = runs_of(apart)
        longs = [(a, b) for a, b in eps if b - a + 1 > params["episode_max"]]
        first_long = None
        if longs:
            a, b = longs[0]
            t = a + params["episode_max"]
            k = next(i for i in range(len(words)) if lifted[t][i] != nat[t][i])
            first_long = {"start": a, "end": b, "tick": t, "va": f"0x{words[k]:08x}",
                          "value": f"0x{lifted[t][k]:08x}", "native": f"0x{nat[t][k]:08x}"}
        observed["episodes"] = {
            "count": len(eps), "listed": [list(e) for e in eps[:EPISODES_LISTED]],
            "longest": list(max(eps, key=lambda e: (e[1] - e[0], -e[0]))) if eps else None,
            "first_long": first_long}
        if params.get("uploads") is not None:
            observed["uploads"] = uploads_observed(log, native, params["uploads"]["word"], upto)
        broken = permutation_verdict(params, observed)
    out.update(observed=observed, held=broken is None, broken=broken)
    return out


def evaluate_rule(log: Path, group: dict, upto: int, native: Path | None = None) -> dict:
    """A group's lifted_rule over the lifted log's ticks 1..`upto`: `held`,
    and `broken`, the first (tick, word, value) that breaks it. zero: every
    listed word is 0 (the log's changes over the group's span, its keyframe
    counted as tick 1); below: each limited word's value stays under its
    limit, and `max` says how high each went; none: nothing to hold; native:
    the lifted series held to the native log's (`evaluate_native`)."""
    rule = group["rule"]
    kind = rule["kind"]
    if kind == "native":
        if native is None:
            raise refuse(f"{group['record']}/{group['group']}: a native-relative rule needs the "
                         "native capture's log")
        return evaluate_native(log, native, group, upto)
    out: dict = {"kind": kind, "held": True, "broken": None}
    if kind == "zero":
        words = set(group["words"])
        hits = [(max(t, 1), va, new) for t, va, _, new in
                WL.changes(log, min(words), max(words) + 4)
                if va in words and new != 0 and t <= upto]
        out["nonzero_words"] = len({va for _, va, _ in hits})
        if hits:
            t, va, v = min(hits)
            out.update(held=False, broken={"tick": t, "va": f"0x{va:08x}", "value": f"0x{v:08x}"})
    elif kind == "below":
        limits = {int(a, 16): v for a, v in rule["limits"].items()}
        order = sorted(limits)
        values = WL.field_values(log, [(f"{RULE_FIELD}{va:08x}", va, 4) for va in order])
        top = {va: 0 for va in order}
        broken = None
        for t in sorted(values):
            if t > upto:
                break
            for va, cell in zip(order, values[t]):
                v = int(cell, 16)
                top[va] = max(top[va], v)
                if broken is None and v >= limits[va]:
                    broken = {"tick": t, "va": f"0x{va:08x}", "value": f"0x{v:08x}"}
        out["max"] = {f"0x{va:08x}": f"0x{top[va]:08x}" for va in order}
        out["limits"] = {f"0x{va:08x}": limits[va] for va in order}
        if broken:
            out.update(held=False, broken=broken)
    return out


def rule_divergence(g: dict) -> dict | None:
    """The divergence a group's broken rule is: a bug at the tick it breaks,
    in the word that broke it (RULE_FIELD, never a column)."""
    b = (g.get("rule") or {}).get("broken")
    if not b:
        return None
    return {"tick": b["tick"], "field": f"{RULE_FIELD}{b['va'][2:]}", "kind": "residue_rule",
            "native": b.get("native", "not compared: a residue word"), "lifted": b["value"],
            "residue": g["record"], "group": g["group"], "rule": g["rule"]["kind"]}


def residues_seen(block: dict) -> list[str]:
    """The residue classes whose words a lifted run was seen to differ on --
    a listed word apart from native at some compared tick, or an open that
    moved -- in the order the entry names them."""
    ids = [c["id"] for c in block.get("classes") or []]
    moved = (block.get("open_shift") or {})
    hit = {g["record"] for g in block.get("groups") or []
           if (g.get("lifted") or {}).get("differing")}
    if moved.get("shifted"):
        hit.add(moved.get("record"))
    return [i for i in ids if i in hit]


def unclassed(result: dict, det: dict) -> dict | None:
    """What M5 would read of this tick capture with no residue class, from
    what the result and the native determinism hold: the judged ticks cut at
    the first tick a repeat differs from the capture on a masked residue word
    or opens a shifted column elsewhere, and the first divergence the earliest
    of the result's own, the lifted run's first masked residue word and its
    first moved open. {tick, field, class}, or None for none."""
    block = result.get("residues") or {}
    window = det.get("judged_ticks") or 0
    for rep in det.get("repeats") or []:
        for g in rep.get("residues") or []:
            if g.get("first_in_mask_tick"):
                window = min(window, g["first_in_mask_tick"] - 1)
        first = (rep.get("open_shift") or {}).get("first_shifted")
        if first:
            window = min(window, first["tick"] - 1)
    cands = [(g["lifted"]["first_in_mask"]["tick"], g["lifted"]["first_in_mask"]["column"])
             for g in block.get("groups") or [] if (g.get("lifted") or {}).get("first_in_mask")]
    first = (block.get("open_shift") or {}).get("first_shifted")
    if first:
        cands.append((first["tick"], first["field"]))
    div = result.get("first_divergence")
    if div and div.get("kind") == "value":            # a broken rule is not a difference with no class
        cands.append((div["tick"], div["field"]))
    ran = result.get("lifted_ticks") or 0
    cands = [c for c in cands if c[0] <= min(window, ran)]
    if cands:
        t, f = min(cands)
        return {"tick": t, "field": f, "class": "bug"}
    if ran < window:
        bounded = (div or {}).get("kind") == "lifted_run_bounded"
        return {"tick": ran + 1, "field": BOUNDED if bounded else RUN_ENDED,
                "class": "bounded" if bounded else "bug"}
    if window < (result.get("native_ticks") or 0):
        return {"tick": window + 1, "field": UNREPEATED, "class": "unrepeatable"}
    return None


def residue_report(entry: dict, ctx: dict, lifted: dict, result: dict,
                   root: Path | None = None) -> dict:
    """The residue classes' report for one lifted run: every listed word of
    each group compared on both sides, masked or not, over the ticks compared
    (`group_diffs`), each group's lifted rule (`evaluate_rule`; a native-relative
    one reads the native capture's log too), and what the shifted opens did; which classes were seen and what M5 would read with
    none (`unclassed`) are added by `settle`."""
    root = ROOT if root is None else root
    groups = residue_groups(ctx["classes"])
    upto = result["compared_ticks"]
    native = native_log(entry, entry["native"], root)
    log = root / lifted["words"]["path"]
    diffs = group_diffs(native, log, groups, upto, ctx["mask_words"])
    out_groups = []
    for g, d in zip(groups, diffs):
        rule = evaluate_rule(log, g, upto, native)
        if rule["broken"] and "native" not in rule["broken"]:
            va, t = int(rule["broken"]["va"], 16), rule["broken"]["tick"]
            rule["broken"]["native"] = f"0x{WL.word_at(native, t, va):08x}"
        out_groups.append({"record": d["record"], "group": d["group"], "listed": d["listed"],
                           "in_mask": d["in_mask"], "rule": rule,
                           "lifted": {k: d[k] for k in ("differing", "first", "first_in_mask",
                                                        "words", "listing_sha256")}})
    block = {"classes": [{"id": c["id"], "class": c["class"]} for c in ctx["classes"]],
             "compared_ticks": upto, "groups": out_groups}
    shift = ((result.get("identity") or {}).get("checks") or {}).get("open", {}).get("shift")
    if shift is not None:
        owner = next(c["id"] for c in ctx["classes"] if c["record"].get("identity_open_shift"))
        block["open_shift"] = dict(shift, record=owner)
    return block


def settle(result: dict, block: dict, det: dict) -> dict:
    """The result with its residue classes' report (`block`) folded in: a
    broken lifted rule is a divergence classed bug at its tick, and wins over
    the result's own when it comes first; the class is `residue` only when
    nothing diverges; which classes were seen and what the replay would read
    with none are recorded beside it."""
    rules = [d for d in (rule_divergence(g) for g in block["groups"]) if d]
    div = result.get("first_divergence")
    early = min(rules, key=lambda d: (d["tick"], d["field"]), default=None)
    if early and (div is None or early["tick"] < div["tick"]):
        result["first_divergence"] = div = early
        result["reported"] = early
    block["seen"] = residues_seen(block)
    result["residues"] = block
    result["class"] = class_of(div, result.get("residues_masked") or [], block["seen"])
    block["unclassed"] = unclassed(result, det)
    return result


def word_native(entry: dict, work: Path, root: Path | None = None) -> dict:
    """A word-policy entry's native side, before anything runs: what it
    compares by identity (`identity_spec`), the residue classes it names
    (`residue_classes`), the mask its digests are recomputed under
    (`effective_mask`, less both), each native capture's stream from its log
    (`native_streams`: the capture's is kept, each repeat's is read and
    released in turn), and the repeats against the capture under the one
    rule (`native_repeats`), with the open-shift columns' bound and, per
    repeat, where it differs from the capture on each group's words."""
    root = ROOT if root is None else root
    spec = identity_spec(entry, root)
    classes = residue_classes(entry, root)
    mask, mask_info = effective_mask(entry, spec, work, root, classes)
    streams = native_streams(entry, mask, spec, root)
    _, header, native = next(streams)
    shift = open_shift_columns(classes, spec)
    det = native_repeats(entry, header, native, work, streams=streams,
                         identity=spec["columns"], shift=shift)
    mask_words = OM.read_mask_words(mask_for(entry, root)) if classes else set()
    if classes:
        groups = residue_groups(classes)
        cap = native_log(entry, entry["native"], root)
        for rec, rep in zip(det["repeats"], entry["native"].get("repeats") or []):
            rec["residues"] = [
                {"record": d["record"], "group": d["group"], "differing": d["differing"],
                 "first_tick": (d["first"] or {}).get("tick"),
                 "first_in_mask_tick": (d["first_in_mask"] or {}).get("tick")}
                for d in group_diffs(cap, native_log(entry, rep, root), groups,
                                     rec["compared_ticks"], mask_words)]
    return {"spec": spec, "mask": mask, "mask_info": mask_info, "header": header,
            "native": native, "det": det, "classes": classes, "shift": shift,
            "mask_words": mask_words}


def word_compare(entry: dict, ctx: dict, lifted: dict, work: Path,
                 root: Path | None = None) -> dict:
    """The lifted run's log stream against the native capture's (`compare`,
    with the identity columns and the open-shift columns' bound), and, where
    the first divergence is a digest column, the words of its block that
    differ at that tick, read from both logs: M5's field names a block, and
    the words say what in it moved. With residue classes, their report is
    folded in last (`residue_report`, `settle`)."""
    root = ROOT if root is None else root
    lheader, lrows = lifted_stream(entry, lifted, ctx["mask"], ctx["spec"], root)
    shift = ({"columns": ctx["shift"], "bound": ctx["det"]["open_shift"]["bound"]}
             if ctx.get("shift") else None)
    result = compare(ctx["header"], ctx["native"], lheader, lrows, entry.get("field_policy", {}),
                     capture_labels(entry), work, judged=ctx["det"]["judged_ticks"],
                     name=entry["name"], lifted_stop=(lifted.get("stop") or {}).get("note", ""),
                     identity=ctx["spec"]["columns"], shift=shift)
    result["identity"]["record"] = ctx["spec"]["record"]
    result["mask"] = ctx["mask_info"]
    div = result.get("first_divergence")
    if div and div.get("kind") == "value" and div["field"].startswith("d_"):
        div.update(digest_words(native_log(entry, entry["native"], root),
                                root / lifted["words"]["path"], ctx["mask"], div["field"],
                                div["tick"]))
    if ctx.get("classes"):
        settle(result, residue_report(entry, ctx, lifted, result, root), ctx["det"])
    return result


# ---------------------------------------------------------------------- main

def replay(entry: dict, run: bool) -> dict:
    name = entry["name"]
    stage = is_stage(entry)
    if stage:
        problems = stage_entry_problems(entry)
        if problems:
            raise refuse("; ".join(problems))
    residue_classes(entry)            # before anything runs: a record that does not stand stops it
    cap = ROOT / entry["native"]["capture"]
    if sha256(cap) != entry["native"]["sha256"]:
        raise refuse(f"{entry['native']['capture']} does not hash to the registry's pin")
    probe = native_probe_current(entry)
    oracle = native_oracle(entry)
    native_start(entry)
    header, native = read_capture(cap)
    budget = poll_budget(entry)
    if budget and budget <= len(native):
        raise refuse(f"{name}'s native capture has {len(native)} ticks and its lifted run's poll "
                     f"budget is {budget} (SA2_PUMP_POLLS: tools/s4/reach_receipt.py's for the "
                     f"reach run, stage_poll_budget for a stage entry): the host would post "
                     f"WM_QUIT before the lifted run could reach the capture's end, and the "
                     f"replay would judge the harness's leash")
    profile = native_device_profile(entry)
    work = OUT / name
    work.mkdir(parents=True, exist_ok=True)
    before = inputs(entry)
    words = "word_policy" in entry
    with tempfile.TemporaryDirectory(dir=work) as td:
        if words:
            ctx = word_native(entry, Path(td))
            det = ctx["det"]
        else:
            det = native_repeats(entry, header, native, Path(td))
        if run:
            if stage:
                # The baseline first, then the observed run, in one invocation:
                # the same build and configuration, so the only difference is
                # the observers (the spec's section 2.3).
                base = run_stage_lifted(entry, profile, observe=False)
                lifted = run_stage_lifted(entry, profile, observe=True)
                lifted["baseline"] = base
                lifted["input"] = input_record(entry, ROOT / lifted["input_applied"]["path"],
                                               len(native))
                if lifted["input"]["problems"]:
                    raise refuse(f"{name}: the lifted run did not replay the recording: "
                                 f"{'; '.join(lifted['input']['problems'])}")
            else:
                lifted = run_lifted(name, device_profile=profile, words=words)
            lifted["inputs"] = before["lifted"]
        else:
            prior = work / "result.json"
            if not prior.is_file():
                raise refuse(f"--no-run, and {name} has never been run")
            lifted = json.loads(prior.read_text())["lifted"]
            if lifted.get("inputs") != before["lifted"]:
                raise refuse(f"--no-run, and what {name}'s lifted capture depends on has moved "
                             "since it was taken (runtime, reach build, hook or link tools); "
                             "run it again")
            if sha256(ROOT / lifted["capture"]) != lifted["sha256"]:
                raise refuse(f"{lifted['capture']} changed since its run was recorded")
            if not DP.same(lifted.get("device_profile"), DP.recorded(profile)):
                raise refuse(f"--no-run, and {name}'s lifted capture ran on device profile "
                             f"{DP.describe(lifted.get('device_profile'))}, not "
                             f"{DP.describe(DP.recorded(profile))}; run it again")
            if lifted.get("guest_paths") != DP.guest_record(profile):
                raise refuse(f"--no-run, and {name}'s lifted capture ran in guest directory "
                             f"{lifted.get('guest_paths')}, not its capture's "
                             f"{DP.guest_record(profile)}; run it again")
            if stage:
                # Both runs' pins, the baseline's among them, before the
                # recorded comparison is trusted.
                problems = stage_recheck(entry, lifted, len(native))
                if problems:
                    raise refuse(f"--no-run, and {name}'s recorded runs do not hold: "
                                 f"{'; '.join(problems)}; run it again")
        perturb = (non_perturbation_stage(lifted, lifted.get("baseline"), name) if stage
                   else non_perturbation(lifted, name=name))
        stop = (lifted.get("stop") or {}).get("note", "")
        if words:
            result = word_compare(entry, ctx, lifted, Path(td))
        else:
            lheader, lrows = read_capture(ROOT / lifted["capture"])
            result = compare(header, native, lheader, lrows, entry.get("field_policy", {}),
                             capture_labels(entry), Path(td), judged=det["judged_ticks"],
                             name=name, lifted_stop=stop)
        if stage:
            result["trace"] = compare_trace(entry, ROOT / lifted["stage_trace"]["path"],
                                            Path(td), lifted_stop=stop)
    if inputs(entry) != before:
        raise refuse(f"{name}: an input moved while the replay ran; nothing was recorded")
    native_doc = {"capture": entry["native"]["capture"], "sha256": entry["native"]["sha256"],
                  "receipt": entry["native"].get("receipt"), "probe": probe,
                  "oracle": oracle, "device_profile": profile, "determinism": det}
    if stage:
        native_doc.update(stage=entry["stage"], input=entry["input"])
    doc = {"schema": "sa2-replay-result/v1", "name": name, "native": native_doc,
           "lifted": lifted, "non_perturbation": perturb, "result": result,
           "inputs": before}
    (work / "result.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def m5_record(doc: dict) -> dict:
    """M5's row for one replay. `ticks` is the native capture's length, so a
    replay judged over fewer ticks never renders as agreeing with all of it.
    For a stage entry the divergence is the earlier of the tick capture's and
    the stage trace's (first_reported). A replay compared under residue
    classes also names the classes its words were seen to differ on
    (`residues`): beside its first divergence, or alone as class `residue`
    when it has none, which is end-of-capture with residues only; and, when
    the classes absorbed an earlier one, the tick M5 would read with none of
    them (`residues_from`, the result's `unclassed`), so the row still states
    the literal first divergent tick design 8 and 4.7's question 4 ask for;
    and, when that reading is not a bug but the end of the judged window
    (`unrepeatable`, a native repeat) or of the lifted run (`bounded`), its
    class (`residues_from_class`), so it never reads as a lifted difference."""
    r = doc["result"]
    rep, cls = first_reported(r)
    out = {"name": doc["name"], "ticks": r["native_ticks"],
           "first_divergent_tick": rep["tick"] if rep else None,
           "field": rep["field"] if rep else None, "class": cls}
    if "residues" in r:
        seen = list(r["residues"].get("seen") or [])
        out["residues"] = seen
        if rep is None:
            out["class"] = "residue" if seen else None
        # The literal first divergence the classes absorbed (design 8's M5 tick,
        # design 4.7 question 4): what the replay reads with no class, when earlier.
        un = r["residues"].get("unclassed")
        if seen and un and (rep is None or un["tick"] < rep["tick"]):
            out["residues_from"] = un["tick"]
            if un["class"] != "bug":
                out["residues_from_class"] = un["class"]
    return out


def identity_problems(receipt: dict, registry: dict, root: Path | None = None) -> list[str]:
    """Why the recorded results do not show the one rule held, [] when they do:
    M5's check reads this beside its row (Documentation/gate-criteria.json, M5's
    criterion_note), from the files as they are in `root`, since the gate runs
    it in an overlay of the tree (check_criterion_clauses.py).

    * every recorded replay carries the identity comparison, its streams are
      ones `canonical` writes (numbers in order of first appearance per kind,
      none back after its release: `event_problems`), its three checks are
      what its streams give (`identity_checks`, recomputed), and each holds;
    * a registered replay was compared under the tree's identity record, and
      its digests under its own mask (`mask_for`) less every identity word and
      every word of the residue classes its result was compared under: the
      mask, the record and the words left out as they are now.

    A result whose open ticks were held under an open shift (`open_shift`:
    the columns `shifting` and the bound) has its checks recomputed under it;
    `residue_problems` holds the columns and the bound themselves."""
    root = ROOT if root is None else root
    out: list[str] = []
    registered = {e.get("name"): e for e in registry.get("replays") or []}
    for doc in receipt.get("replays") or []:
        name = doc.get("name")
        ident = (doc.get("result") or {}).get("identity")
        if not isinstance(ident, dict):
            out.append(f"{name}: compared without the identity rule (no identity record in its "
                       "result), so values the host chooses were compared by their bits")
            continue
        named = ident.get("named") or {}
        try:
            for side in ("native", "lifted"):
                out += [f"{name}: the {side} stream: {p}"
                        for p in event_problems(ident.get(f"{side}_events") or [], named)]
            again = identity_checks(ident.get("native_events") or [],
                                    ident.get("lifted_events") or [], ident.get("columns") or [],
                                    named, int(ident.get("judged_ticks") or 0),
                                    shift=recorded_shift(ident.get("open_shift")))
        except (ValueError, TypeError, AttributeError) as e:
            out.append(f"{name}: its identity record does not read: {e}")
            continue
        if again != ident.get("checks"):
            out.append(f"{name}: its recorded identity checks are not what its streams give")
        for check in CHECKS:
            if not again[check]["ok"]:
                f = again[check]["first"]
                out.append(f"{name}: the {check} check fails at tick {f['tick']} {f['field']} "
                           f"(native {f['native']}, lifted {f['lifted']})")
        entry = registered.get(name)
        if entry is None:
            continue
        compared = (doc.get("result") or {}).get("compared_ticks")
        if ident.get("judged_ticks") != compared:
            out.append(f"{name}: its identity checks judged {ident.get('judged_ticks')} ticks, "
                       f"and the replay compared {compared}")
        rec = ident.get("record") or {}
        if rec.get("path") != IDENTITY_RECORD or rec.get("sha256") != sha256(root / IDENTITY_RECORD):
            out.append(f"{name}: compared under an identity record that is not the tree's "
                       f"{IDENTITY_RECORD}")
            continue
        mask = (doc.get("result") or {}).get("mask") or {}
        try:
            spec = identity_spec(entry, root)
            want = mask_for(entry, root)
            words = OM.read_mask_words(want)
            record = {va for _, va, _ in spec["extra"]} | {
                va for n, va, _ in WL.named_fields(root / "harness" / "replay" / "tick_fields.h")
                if n in spec["named"]}
            # the classes its result was compared under; residue_problems holds them
            # to the registry's
            ids = [c.get("id") for c in
                   (((doc.get("result") or {}).get("residues") or {}).get("classes") or [])]
            residue = residue_words([{"id": i, "record": residue_record(i, root),
                                      "replay": name} for i in ids])
        except (SystemExit, ValueError, TypeError, KeyError, AttributeError) as e:
            out.append(f"{name}: {e}")
            continue
        order = [n for n, _, _ in WL.named_fields(root / "harness" / "replay" / "tick_fields.h")
                 if n in spec["named"]] + [c for c, _, _ in spec["extra"]]
        if ident.get("columns") != order or named != spec["named"]:
            out.append(f"{name}: its identity columns are not the {len(order)} words the "
                       f"record names, with their kinds")
        cut = (words - record) & residue
        if mask.get("mask") != str(want.relative_to(root)) \
                or mask.get("mask_sha256") != sha256(want) \
                or mask.get("identity_words_removed") != len(words & record) \
                or mask.get("residue_words_removed", 0 if not ids else None) != (
                    len(cut) if ids else 0) \
                or mask.get("words") != len(words - record - residue):
            out.append(f"{name}: its digests were not recomputed under {want.relative_to(root)} "
                       f"less the {len(words & record)} identity words it holds"
                       + (f" and the {len(cut)} residue words" if ids else ""))
    return out


def recorded_shift(spec) -> dict | None:
    """A result's recorded open shift ({"shifting", "bound"}) as identity_checks
    takes it, None when it records none."""
    if spec is None:
        return None
    if not isinstance(spec, dict) or not isinstance(spec.get("shifting"), list):
        raise ValueError(f"its open shift does not read: {spec!r}")
    return {"columns": spec["shifting"], "bound": spec.get("bound")}


def identity_problems_in(receipt: str, registry: str, root: str = ".") -> list[str]:
    """identity_problems on the files at these paths, relative to `root`."""
    base = Path(root)
    try:
        doc = json.loads((base / receipt).read_text())
        reg = json.loads((base / registry).read_text())
    except (OSError, ValueError) as e:
        return [f"the receipt or the registry does not read: {e}"]
    return identity_problems(doc, reg, base)


def rule_problems(name: str, g: dict) -> list[str]:
    """Why a group's recorded rule verdict is not what its recorded values
    give, [] when it is."""
    rule = g.get("rule") or {}
    kind, held, broken = rule.get("kind"), rule.get("held"), rule.get("broken")
    where = f"{name}: {g.get('record')}/{g.get('group')}'s lifted rule ({kind})"
    try:
        if kind == "zero":
            want = not rule.get("nonzero_words") and broken is None
            if broken is not None and int(broken["value"], 16) == 0:
                return [f"{where} is recorded broken by a zero"]
        elif kind == "below":
            limits = {a: int(v) for a, v in (rule.get("limits") or {}).items()}
            top = {a: int(v, 16) for a, v in (rule.get("max") or {}).items()}
            if set(limits) != set(top) or not limits:
                return [f"{where} records no maximum for each word it limits"]
            want = all(top[a] < limits[a] for a in limits) and broken is None
            if broken is not None and int(broken["value"], 16) < limits.get(broken["va"], 0):
                return [f"{where} is recorded broken by a value under its limit"]
        elif kind == "none":
            want = broken is None
        elif kind == "native":
            params, obs = rule.get("params") or {}, rule.get("observed") or {}
            if params.get("form") == "one-tick-scan":
                apart = obs["apart"]
                if len(apart) != min(obs["apart_ticks"], params["max_ticks"] + 1) \
                        or any(a["tick"] >= b["tick"] for a, b in zip(apart, apart[1:])):
                    return [f"{where} records {obs['apart_ticks']} ticks apart and lists "
                            f"{len(apart)}, not the first ones up to one past its bound"]
                again = scan_verdict(params, obs)
            elif params.get("form") == "context-permutation":
                ep = obs["episodes"]
                longest = ep["longest"]
                long = ep["first_long"]
                for key in ("pre", "shape", "decode", "copy"):
                    if (obs[key]["ticks"] == 0) != (obs[key]["first"] is None):
                        return [f"{where} records {obs[key]['ticks']} ticks for {key} and a "
                                f"first of {obs[key]['first']}"]
                span = (longest[1] - longest[0] + 1) if longest else 0
                if (ep["count"] == 0) != (longest is None) or len(ep["listed"]) > ep["count"] \
                        or (long is None and span > params["episode_max"]) \
                        or (long is not None and (
                            long["end"] - long["start"] + 1 <= params["episode_max"]
                            or long["tick"] != long["start"] + params["episode_max"]
                            or span < long["end"] - long["start"] + 1)):
                    return [f"{where} records runs of differing ticks that do not agree with "
                            f"each other (count {ep['count']}, longest {longest}, first long "
                            f"{long})"]
                if params.get("uploads") is not None:
                    up = obs["uploads"]
                    lu, nu = upload_list(up["lifted"]), upload_list(up["native"])
                    if up["word"] != params["uploads"]["word"] or len(up["initial"]) != 2 \
                            or (len(lu), len(nu)) != (up["lifted_uploads"], up["native_uploads"]) \
                            or any(not 0 < t <= up["through"] for t, _ in lu + nu) \
                            or any(a[0] >= b[0] for side in (lu, nu)
                                   for a, b in zip(side, side[1:])):
                        return [f"{where} records uploads that do not agree with each other "
                                f"(word {up['word']}, {up['lifted_uploads']} lifted and "
                                f"{up['native_uploads']} native through tick {up['through']})"]
                again = permutation_verdict(params, obs)
            else:
                return [f"{where}: no such form {params.get('form')!r}"]
            want = again is None
            where_broken = [(b or {}).get(k) for b in (broken, again) for k in ("tick", "va")]
            if where_broken[:2] != where_broken[2:]:
                return [f"{where} is recorded broken at {(broken or {}).get('tick')}, and its "
                        f"recorded values break it at {(again or {}).get('tick')}"]
        else:
            return [f"{where}: no such rule"]
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, ZeroDivisionError) as e:
        return [f"{where} does not read: {e}"]
    if held is not want:
        return [f"{where} is recorded {'held' if held else 'broken'}, and its recorded values "
                f"{'keep' if want else 'break'} it"]
    return []


def residue_problems(receipt: dict, registry: dict, root: Path | None = None,
                     metrics: str | None = None) -> list[str]:
    """Why the recorded results do not show the residue classes applied as the
    user ruled, [] when they do: M5's check reads this beside its row, from
    the files as they are in `root` (the gate runs it in an overlay of the
    tree).

    * every registered entry's classes are records a comparison may apply
      (`residue_classes`, `record_problems`: a word that is a named field, a
      stage probe's source or the input the input probe applies is refused --
      a residue that changes a gameplay value is a bug), a group that names the
      replays it applies to names registered ones, and a registered replay's
      result was compared under exactly the classes the registry names
      (without them it reads `unclassed`);
    * in every recorded result: its groups are the ones its classes' records
      give that replay (`applies`), each rule recorded as the record holds it
      now, each group's rule verdict is what its recorded values give (a
      native-relative rule's re-derived from what it observed on both logs,
      `scan_verdict`, `permutation_verdict`), a broken rule is a divergence no later
      than the tick it broke, the
      open-shift bound is the widest shift the recorded native repeats show
      and the one the identity checks were held to, the class is `residue`
      only when nothing diverges (`class_of`), the classes seen and what M5
      would read with none are what the report gives;
    * with `metrics` (METRICS.md's text), every part of M5's row that names a
      residue is the recorded result's own (`stage_exit.m5_part`), so a
      residue typed into the row over a replay's judged divergence, or for a
      replay with no result, is refused."""
    import stage_exit as SE           # here: Documentation/scripts, for the row's grammar
    root = ROOT if root is None else root
    out: list[str] = []
    registered = {e.get("name"): e for e in registry.get("replays") or []}
    scoped: dict[str, list[str]] = {}
    for name, entry in registered.items():
        try:
            for c in residue_classes(entry, root):
                for g in c["record"]["groups"]:
                    stray = [r for r in g.get("replays") or [] if r not in registered]
                    if stray:
                        scoped[f"{c['id']}/{g['name']}"] = stray
        except SystemExit as e:
            out.append(str(e).removeprefix("REPLAY_REFUSED "))
    for where, stray in scoped.items():
        out.append(f"residue group {where} applies to replays {stray} the registry does not "
                   "register, so it applies to nothing it names")
    docs = {}
    for doc in receipt.get("replays") or []:
        name = doc.get("name")
        docs.setdefault(name, doc)
        result = doc.get("result") or {}
        block = result.get("residues")
        entry = registered.get(name)
        if entry is not None:
            now = list(entry.get(RESIDUE_FIELD) or [])
            was = [c.get("id") for c in (block or {}).get("classes") or []]
            if now != was:
                un = (block or {}).get("unclassed")
                reads = ("no divergence" if un is None else
                         f"{un.get('tick')}/{result.get('native_ticks')} "
                         f"({un.get('class')}: {un.get('field')})")
                out.append(f"{name}: its result was compared under residue classes {was} and the "
                           f"registry names {now}" + (f"; without them it reads {reads}"
                                                      if block else ""))
        if block is None:
            if result.get("class") == "residue":
                out.append(f"{name}: its class is residue and no residue class report backs it")
            continue
        try:
            groups = block.get("groups") or []
            for g in groups:
                out += rule_problems(name, g)
            # the groups its classes give this replay (`applies`), each rule recorded as
            # the record holds it now: a group dropped, or a limit or a native rule's
            # parameters edited in the receipt, is not the record's comparison
            own = [dict(g, record=c.get("id"), effect=rec.get("effect"))
                   for c in block.get("classes") or []
                   for rec in (residue_record(c.get("id"), root),)
                   for g in rec["groups"] if applies(g, name)]
            if [(g["record"], g["name"]) for g in own] != \
                    [(g.get("record"), g.get("group")) for g in groups]:
                out.append(f"{name}: its report lists groups "
                           f"{[g.get('group') for g in groups]}, and its classes' records give "
                           f"it {[g['name'] for g in own]}")
            for g, want in zip(groups, own):
                rule, now = g.get("rule") or {}, want["lifted_rule"]
                as_now = rule.get("kind") == now.get("kind") and (
                    now["kind"] != "below" or rule.get("limits") == now.get("limits")) and (
                    now["kind"] != "native"
                    or rule.get("params") == native_params(now, want["table"], want["effect"]))
                if not as_now:
                    out.append(f"{name}: {g.get('record')}/{g.get('group')}'s lifted rule was "
                               "recorded as something other than its record's rule as it is now")
            div = result.get("first_divergence")
            for g in groups:
                b = (g.get("rule") or {}).get("broken")
                if b and (div is None or div["tick"] > b["tick"]):
                    out.append(f"{name}: {g['record']}/{g['group']}'s lifted rule breaks at tick "
                               f"{b['tick']} ({b['va']} = {b['value']}), and a residue that "
                               "changes what its rule holds is a bug: the replay's first "
                               "divergence must be there or earlier")
            shift = block.get("open_shift")
            spec = (result.get("identity") or {}).get("open_shift")
            if shift is not None or spec is not None:
                reps = ((doc.get("native") or {}).get("determinism") or {}).get("repeats") or []
                widest = max(((r.get("open_shift") or {}).get("widest") or 0 for r in reps),
                             default=0)
                if (shift or {}).get("bound") != widest or (spec or {}).get("bound") != widest:
                    out.append(f"{name}: its open shift was held to a bound of "
                               f"{(spec or {}).get('bound')} (report "
                               f"{(shift or {}).get('bound')}), and the widest shift its native "
                               f"repeats show is {widest}: the bound is computed, never typed")
                if entry is not None:
                    # its own classes' columns; the registry's classes are held above
                    want = open_shift_columns([{"id": c.get("id"),
                                                "record": residue_record(c.get("id"), root)}
                                               for c in block.get("classes") or []])
                    got = (spec or {}).get("shifting") or []
                    if got != want:
                        out.append(f"{name}: its open shift names {len(got)} columns, and its "
                                   f"classes' identity_open_shift {len(want)}")
            seen = residues_seen(block)
            if block.get("seen") != seen:
                out.append(f"{name}: it records residue classes {block.get('seen')} seen, and its "
                           f"report gives {seen}")
            want_cls = class_of(div, result.get("residues_masked") or [], seen)
            if result.get("class") != want_cls:
                out.append(f"{name}: its class is {result.get('class')!r}, and its first "
                           f"divergence and residue report give {want_cls!r}: a residue is the "
                           "class only when nothing judged diverges")
            det = (doc.get("native") or {}).get("determinism") or {}
            if block.get("unclassed") != unclassed(result, det):
                out.append(f"{name}: what it records it would read with no residue class is not "
                           "what its report and its native repeats give")
        except (KeyError, TypeError, ValueError, AttributeError, SystemExit) as e:
            out.append(f"{name}: its residue class report does not read: {e}")
    if metrics is not None:
        cell = SE.row(metrics, "M5 replays")
        for part in (p.strip() for p in cell.split(";") if p.strip()):
            if "residue" not in part:
                continue
            name = part.split(" ", 1)[0]
            doc = docs.get(name)
            try:
                own = SE.m5_part(m5_record(doc)) if doc else None
            except (KeyError, TypeError) as e:
                own = f"(its result does not read: {e})"
            if own != part:
                what = f"of {name} reads {own!r}" if doc else f"has no replay {name!r}"
                out.append(f"M5's row reads {part!r}, which names a residue, and the recorded "
                           f"result {what}")
    return out


def residue_problems_in(receipt: str, registry: str, root: str = ".",
                        metrics: str | None = None) -> list[str]:
    """residue_problems on the files at these paths, relative to `root`."""
    base = Path(root)
    try:
        doc = json.loads((base / receipt).read_text())
        reg = json.loads((base / registry).read_text())
        text = (base / metrics).read_text() if metrics else None
    except (OSError, ValueError) as e:
        return [f"the receipt, the registry or the metrics do not read: {e}"]
    return residue_problems(doc, reg, base, text)


def check_registry(entries: list[dict]) -> list[str]:
    names = [e["name"] for e in entries]
    bad = [n for n in names if not NAME.match(n)]
    if bad:
        raise refuse(f"replay names must match {NAME.pattern}, since M5 is parsed back out of "
                     f"METRICS.md by name: {bad}")
    if len(set(names)) != len(names):
        raise refuse(f"duplicate replay names in the registry: {names}")
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="append", default=[],
                    help="a registered replay name; repeatable; default every one")
    ap.add_argument("--no-run", action="store_true",
                    help="compare the lifted captures already recorded instead of running")
    ap.add_argument("--receipt", action="store_true",
                    help=f"also write {RECEIPT.relative_to(ROOT)}")
    ap.add_argument("--controls", action="store_true",
                    help="print each registered control's verdict against its entry's mask "
                         "(control_verdict), as JSON, and run nothing")
    ap.add_argument("--exe", choices=("unpacked", "shipped"), default="unpacked",
                    help="the executable the lifted runs load: the unpacked one (default, the "
                         "registered replays') or the shipped sonic2app.exe through the overlay "
                         f"table, into {OUT_SHIPPED.relative_to(ROOT)}, with no receipt and no "
                         "replay-results.json")
    ap.add_argument("--exe-overlay", default=None, metavar="TABLE",
                    help="the overlay table a shipped run links (default link_s4.py's)")
    a = ap.parse_args()
    if a.exe == "shipped":
        if a.receipt or a.no_run or a.controls:
            ap.error("--exe shipped runs the replays and records them apart; it takes no "
                     "--receipt, --no-run or --controls")
        use_shipped_exe(a.exe_overlay)
    elif a.exe_overlay:
        ap.error("--exe-overlay is for --exe shipped")
    registry = json.loads(REGISTRY.read_text())
    entries = registry["replays"]
    if a.controls:
        print(json.dumps({e["name"]: [dict(capture=c.get("capture"),
                                           verdict=control_verdict(e, c))
                                      for c in e.get("controls") or []]
                          for e in entries if e.get("controls")}, indent=1))
        return 0
    if a.receipt:
        require_mask_sources_hashed(entries=entries)   # before any run: a bad source stops at once
    names = check_registry(entries)
    unknown = set(a.replay) - set(names)
    if unknown:
        raise refuse(f"not registered: {sorted(unknown)}; registered are {names}")
    chosen = [e for e in entries if not a.replay or e["name"] in a.replay]

    docs = {}
    for entry in chosen:
        doc = replay(entry, run=not a.no_run)
        docs[entry["name"]] = doc
        rec = m5_record(doc)
        where = (f"first divergence tick {rec['first_divergent_tick']} {rec['field']} "
                 f"({rec['class']})" if rec["first_divergent_tick"] else "no divergence")
        print(f"REPLAY {rec['name']} {where}; judged {doc['result']['judged_ticks']} of "
              f"{rec['ticks']} native ticks, lifted ran {doc['result']['lifted_ticks']}", flush=True)

    if EXE["kind"] == "shipped":
        # Never RESULTS: the registered readings are the unpacked executable's.
        shipped = OUT / "results.txt"
        shipped.write_text(json.dumps({"generated_by": "tools/s4/tick_replay.py --exe shipped",
                                       "replays": [m5_record(docs[n]) for n in docs]},
                                      indent=1, sort_keys=True) + "\n")
        print(f"REPLAY wrote {shipped.relative_to(ROOT)} ({len(docs)} replays, shipped exe)")
        return 0

    # M5's input covers every registered replay or none: publishing a subset
    # would drop a replay from the metric without anyone deciding to.
    for entry in entries:
        if entry["name"] not in docs:
            prior = OUT / entry["name"] / "result.json"
            if not prior.is_file():
                raise refuse(f"{entry['name']} is registered and has no result; run it")
            docs[entry["name"]] = json.loads(prior.read_text())
    records = [m5_record(docs[n]) for n in names]
    RESULTS.write_text(json.dumps({"generated_by": "tools/s4/tick_replay.py",
                                   "replays": records}, indent=1, sort_keys=True) + "\n")
    print(f"REPLAY wrote {RESULTS.relative_to(ROOT)} ({len(records)} replays)")

    if a.receipt:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()
        receipt = {
            "schema": "sa2-recomp-receipt/v1",
            "id": "s4-tick-replay-v1",
            "question": "Does the lifted build reproduce each retained native tick capture, "
                        "and where does it first diverge?",
            "generated_by": "tools/s4/tick_replay.py",
            "commit": head,
            "inputs": {"pipeline": {t: sha256(ROOT / t) for t in pipeline_files(entries)}},
            "registry_sha256": sha256(REGISTRY),
            "replays": [docs[n] for n in names],
            "m5": records,
        }
        RECEIPT.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
        print(f"REPLAY wrote {RECEIPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
