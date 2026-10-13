#!/usr/bin/env python3
"""Run a registered replay's lifted run as a presentation build, and hold it to the replay.

Subtitle: tools/s4/loop_run.py on a build of its own name (present-boot.<tag>,
present-mh.<tag>), with the S5 recorder (host/sa2_present_rec.c) linked in by
-Wl,-wrap beside the replay's own hooks, the registered replay's own environment and
save reset, and the checks that show what the recorder did and did not move.

Usage:
  .venv/bin/python tools/s5/present_run.py                # the boot: stream and counts
  .venv/bin/python tools/s5/present_run.py --no-trace     # the counts alone
  .venv/bin/python tools/s5/present_run.py --check        # hold the last run again
  .venv/bin/python tools/s5/present_run.py --replay metalharbor          # Metal Harbor
  .venv/bin/python tools/s5/present_run.py --replay metalharbor --check
  (--build NAME, --out DIR and --trace PATH move the defaults below; --keep-failed
  keeps a failed run's stream as <trace>.<tag>.failed)

Metal Harbor (--replay metalharbor)
-----------------------------------
tools/s4/tick_replay.py runs a stage entry twice (run_stage_lifted): a baseline with
every observer off, then the observed run, both of build replay-metalharbor. A stage
has no reach receipt, so the baseline is what shows the observers changed nothing
(non_perturbation_stage); the two registered run records are one file byte for
byte, so both runs made the same calls and drew the same frames. Only the observed
run has outputs to hold a run to -- the tick capture, stage trace, goal-latch
column, applied-input log, import table and word log the result pins -- so the
presentation run is the observed run once, with the recorder: stage_run.command, the
one definition of a stage run, with the registered run's arguments (the stage, its
poll budget, the input script, the device profile, label "ticks", the stage trace
and word log as its observers), writing into this run's own directory, and with
--dump-imports and the recorder's hook and wraps appended as tick_replay appends
--dump-imports. It takes run_stage_lifted's guards too: the input script at its
pin, and no link from objects the map or arms have since replaced. The one setting
that differs from the registered run's is the probe's alarm, a wall-clock bound the
guest never reads (runtime/sa2_call.c): STAGE_ALARM_FACTOR times the registered
max(600, polls // 5), because a recorder writing a stream is slower and an alarm
is a run lost at its end; the receipt keeps the row's time beside both. Before it
builds, all four stage hooks' objects and the recorder's are deleted.

Why a tool
----------
The recorder is a measuring instrument: a presentation run says nothing about the
game until it is shown to be the registered run with a recorder attached, and that
has to be shown on every recorder change (the S5 critic's section 5, item 7). So the
run and its checks are one command, and the checks are the receipt it writes, not a
reading of a terminal.

What it runs
------------
The boot replay's lifted run exactly as tools/s4/tick_replay.py run_lifted builds
it -- RR.ENV, SA2_REPLAY_TICKS and SA2_REPLAY_WORDS, the registered capture's device
profile (RR.profile_env), the pinned saves reset (save_state.reset_lifted and
require_empty) and RR.clean_environ -- with these differences, none of which the
guest can see:
  * the build is named present-<replay>.<tag> (present-<replay>-counts.<tag> with
    --no-trace), the tag being this checkout's directory name and eight hex digits
    of the sha256 of its absolute path, so no two checkouts, and no two modes of
    one, link or run the same binary, state file, outputs or partial stream; a name
    the gates' builds use (reach, replay*, stage*, loop*) is refused, so no
    registered binary is ever replaced. Before it builds, the two hooks' objects
    are deleted, so both are compiled from this tree's sources (tools/s4/link_s4.py
    reuses an object by mtime), and the build check below ties the linked objects
    and binary to this tree;
  * the recorder is a second --hook with its -Wl,-wrap list (objcheck.WRAPS); nothing
    under runtime/, harness/ or tools/s4/ is edited;
  * SA2_VFS_WRITE_ROOT is an overlay of the run's own under private/generated/s5/:
    the registered overlay is the one gate passes reset and check. The boot writes
    nothing to its overlay (files.stats.writes is 0 in the registered run), and the
    registered run itself was made from another tree's overlay (files.write_root,
    the one leaf of the run record allowed to differ). The receipt lists what the
    overlay holds after the run, by sha256;
  * SA2_REC_COUNTS, SA2_REC_TRACE (not with --no-trace), SA2_REC_META and, with
    --audio only, SA2_REC_AUDIO=1 (the audio calls recorded too: a version 2 stream,
    and "audio": true in the meta; its default paths are <replay>-audio, its own, so
    it never replaces a plain run's stream or receipt; refused with --no-trace) are
    the recorder's; the guest's environment block
    is the device profile's, never the host's (runtime/sa2_imports.c,
    GetEnvironmentStringsW). The meta names the build, the replay, the commit, the
    hashes of the five files that make the run (RUN_FILES) and a run id no other run
    has;
  * probe arguments --timeout 600, the wall-clock alarm around the guest call and
    nothing else (runtime/sa2_call.c), and --dump-imports, the run's own import
    table. The registered run has the probe's default alarm, REGISTERED_ALARM_S; the
    receipt records the run's own time beside it and flags a run that went past it,
    one the registered configuration would have killed;
  * loop_run's process bound is 1200 s, and the run's JSON and matrix are moved from
    private/generated/s4/run/ into the run's own directory.

The checks (each can fail, and the receipt keeps what each read)
-----------------------------------------------------------------
Before the run:
  inputs       tick_replay's own inputs() for the boot entry -- the reach receipt,
               the runtime, the lifted pipeline, the device profile, the compare
               pipeline -- equal the registered result's, key for key; a tree whose
               inputs differ is not run at all
After the build:
  build        the link state (main.<build>.json) finished every phase, compiled both
               hooks afresh from this tree's own files (its extra_c names them by
               absolute path) and linked; the recorder object it linked is this
               tree's source compiled again now with link_s4's flags, byte for byte,
               and holds to tools/s5/objcheck.py; the binary is the one the matrix ran
               and is no older than that object; and the source holds to
               objcheck.SOURCE_RULES. The objects' and the binary's sha256 are kept
After the run:
  lifted_csv   the tick capture's sha256 is the registered result's lifted sha256
  words        the word log is whole, holds against its own tick capture
               (wordlog.check_capture, the lifted rules), and equals the registered
               log by value: every tick's FNV-1a over .dataa (wordlog.py hashes)
               and every word (wordlog.py diff). Not by file hash: the log carries
               the host's clock at every tick (harness/replay/word_log_core.h)
  reach        iterations, import calls, frames presented and the stop equal the
               registered result's
  record       the run's whole record (run.json) equals the registered run's leaf for
               leaf, type and value, but for ALLOWED_RECORD_DIFFS
  configuration  the run read the device profile it was handed and answered its
               capture's directory (device_profile.check_run), and its record shows
               every device switch on (reach_receipt.switch_problems): tick_replay
               asks both of the registered run
  counts       the recorder's own counts: per (interface, slot) equal to the run's
               dynamic surface and the registered run's; per entry point equal to the
               run's import table; method calls plus d3dx calls equal to
               d3d9.method_calls; Presents equal to d3d9.frames.present; with audio,
               every `audio` line wrapped, every audio call that entered came back, and
               no store changed after its Unlock (audio_counts)
  stream       (with a trace) tools/s5/stream_dump.py finds the stream whole and
               true to its own rules (framing, every record's blobs, FRAME after every
               Present with its clock and frame count never going back, the locks,
               the objects), its header's rule table the lines of host/sa2_stream.h,
               ending in SA2S_END; its counts equal the recorder's; no note of any
               class the receipt does not accept; every FILE blob and load
               description equal to the recording device's own rows and the first
               calls to its call log (stream_dump.hold_to_run); each entry's argc the
               import table's; the header's meta names this run; the sidecar says it
               closed. A run with --audio holds a version 2 stream of AUDIO records from
               a recorder that wrapped every `audio` line, its records and counts held to
               the audio devices' own record of the run (stream_dump.hold_audio_to_run:
               per op the device's calls, and its call log walked against the records);
               a run without it, a version 1 stream (audio_stream_problems). A file that
               is not a stream fails this check with a receipt
The stream also holds its object introductions and method counts to the run
record (stream_dump.hold_counts_to_run), and recomputes every buffer-watch
counter from its own Lock, Unlock, Release and FRAME records.
  spec         the settings loop_run recorded for the run (its matrix row) equal the
               registered run's, but for the observers' outputs, which must be this
               run's own files of the same names, and the recorder's own. A matrix
               row keeps none of loop_run's BASE_ENV keys, so SA2_INSTALL and
               SA2_VFS_WRITE_ROOT are held by `saves` instead
  saves        the save reset the run started from (save_state.reset_lifted's record)
               has the registered run's pin and installation, the installation at its
               pin, and nothing left in the write overlay, which is the run's own
  stage        (a stage replay) the stage trace, goal-latch column, applied-input log
               and import table each hash to the registered result's pin, and the
               applied input, held to the recording again by tick_replay.input_record,
               equals the result's account of it but for its path
The counts check also holds the recorder's buffer watch: it ran (the counts file has
it), buffer Unlocks set it (buffer_watch.set is at least 1 when there were any, and
never more than there were), and no buffer changed after its Unlock
(buffer_watch.changed is 0; a change is also a "buffer-changed" note, which the stream
check refuses). The stream check holds its other counters -- the Unlocks that set it,
the checks it ran, the bytes it hashed and the buffers it watched at exit -- to the
ones the stream's own Unlock, Lock, Release and FRAME records give, so a watch that
stopped checking fails.
All holding, the stream written to <trace>.<tag>.partial is renamed over <trace>,
and <trace>.READY is written after it: the stream's sha256 on its first line and
the run's meta, as JSON, on its second, so a reader can tell whose stream it
describes. A run that fails leaves the last good stream and its READY as they were
and deletes its own partial stream (--keep-failed keeps it as .failed). Runners
take an exclusive flock on <trace>.lock around all of this, so two runs that
finish together leave one run's stream and that run's READY.

What the checks cover, and what they do not
-------------------------------------------
The run-time checks see what the registered run records: the .dataa words at every
tick, the tick capture, the reach, and the run record (the recording device's own
account of every call, the files, the heap, the threads). A write into guest memory
outside .dataa that nothing reads back, or a change the record does not keep, passes
them. The build check narrows that, and only that far: objcheck proves that the
object calls no runtime writer by name (no sa2_poke, sa2_guest_fill, heap or obj_*
function, no reference to sa2_guest), defines nothing but its wraps, and holds no
floating point, FP-state or system instruction of its own and no float formatting.
It does not see a store through the CPU state rec_thunk is handed, nor count the
recorder's calls to the natives and __real_ functions it may call, each of which
writes guest memory by design. Those calls are held here, at run time: once per guest
call by the per-method and per-entry counts and the run record, and with the guest's
own arguments by the device-log walk over the first 8,192 calls. A store into the
register file that the guest never reads back is held by nothing in a run.
tools/s5/tests/rec_harness.c holds one only where it lands in the harness's stand-in
for the register file -- nine 32-bit registers and the x87 control word, 40 bytes in
the harness's own layout, not the runtime's State -- and only for the calls the
harness makes.

--check re-holds an existing run with this runner. It refuses when no receipt
exists, and fails unless the files that made the run (run_files(reg)) are the current
tree's and the stream at the trace is this run's (its header's meta names it). It
removes READY only when the stream is this run's, the tree can re-hold the run, the
re-held checks fail, and READY describes that stream (its digest is the stream's and
its meta, when it has one, this run's); otherwise it says why and leaves READY. Its
receipt's "audio" is the held run's (the meta's "audio", which the checks hold the
stream to), not --audio's, which only chooses the default paths.

Outputs: private/generated/s5/present/<tag>/<replay>/ (with --no-trace, <replay>-counts/; with
--audio, <replay>-audio/, and the stream at trace/<replay>-audio.sa2s):
lifted.csv, lifted-words.bin.zst, counts.json, imports.table.json, run.json,
matrix.json, loop_run.log, vfsroot/, and present-run.json, the receipt; the stream
at private/generated/s5/trace/boot.sa2s, its .status sidecar, boot.READY, and the
runners' lock file boot.sa2s.lock (empty).

Never run it during a gate pass: the build and the run are heavy, and the machine
is shared with the gates.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import tick_replay as TR  # noqa: E402  the registered replay's own definitions
import stream_dump as SD  # noqa: E402
import objcheck as OC  # noqa: E402

C, RR, SS, WL, DP = TR.C, TR.RR, TR.SS, TR.WL, TR.DP

REPLAY = "boot"
# Each registered replay a presentation run is made of, and its build's name before
# the tree's: a build name belongs to one replay, so a run of one never relinks the
# other's binary.
BUILDS = {"boot": "present-boot", "metalharbor": "present-mh"}
# A stage run's alarm: the registered run's (tick_replay.run_stage_lifted,
# max(600, polls // 5)) times this. The registered Metal Harbor row took 1465.56 s of
# its 3129 (private/generated/s4/run/replay-metalharbor.matrix.json).
STAGE_ALARM_FACTOR = 3
# The observers' outputs: a presentation run writes its own, of the same names, in
# its own directory; every other setting is the registered run's (check_spec).
OBSERVER_OUTPUTS = ("SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_STAGE_TRACE", "SA2_GOAL_LATCH",
                    "SA2_INPUT_APPLIED")
RECORDER_VARS = ("SA2_REC_COUNTS", "SA2_REC_TRACE", "SA2_REC_META", "SA2_REC_AUDIO")
# What a stage replay's result pins beside lifted.csv and the word log: this run's
# own output (paths() names them) to the result's key.
STAGE_PINS = (("stage_trace", "stage_trace"), ("goal_latch", "goal_latch"),
              ("input_applied", "input_applied"), ("imports", "import_table"))
RECORDER = "host/sa2_present_rec.c"
STREAM_H = "host/sa2_stream.h"
WRAPS = OC.WRAPS
RESERVED = ("reach", "replay", "stage", "loop")
PROBE_TIMEOUT = 600
RUN_TIMEOUT = 1200
# The alarm the registered run had: RR.PROBE_ARGS is empty, so harness/crt_probe.c's
# default (`timeout = 120` in its main). test_s5_stream.py holds this to the source.
REGISTERED_ALARM_S = 120
GEN_S5 = "private/generated/s5"
LINKDIR = C.GEN / "s4" / "link"
ENTRY_NAMES = SD.D3DX_NAMES                        # entry -> name, 1..12
DIRECT3D_CREATE9 = SD.DIRECT3D_CREATE9
# The run record's leaves a presentation run may differ in: its own write overlay.
ALLOWED_RECORD_DIFFS = (("files", "write_root"),)
# Note classes whose notes a READY stream may carry. None: every note says the
# stream lacks something.
ACCEPTED_NOTES: tuple[str, ...] = ()
# What made a run: compared by --check with the current tree and the stream's meta;
# a stage replay's run also names tools/s4/stage_run.py and every stage hook.
RUN_FILES = ("recorder_sha256", "stream_h_sha256", "runner_sha256", "runtime_sha256",
             "tick_hook_sha256")
STAGE_RUN_FILES = ("stage_run_sha256", "stage_hooks_sha256")
# Where loop_run leaves a run's record and matrix (tools/s4/loop_run.py RUNDIR), before
# the runner moves them into the run's own directory.
RUN_DIR = C.GEN / "s4" / "run"


def rel(p: Path) -> str:
    p = Path(p)
    return str(p.relative_to(ROOT)) if p.is_absolute() and p.is_relative_to(ROOT) else str(p)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def tree_name() -> str:
    """This checkout's directory name, as a build name can carry it."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", ROOT.name) or "tree"


def tree_tag() -> str:
    """This checkout's directory name and eight hex digits of its absolute path's
    sha256: directory names repeat across worktrees, absolute paths do not."""
    return f"{tree_name()}-{hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]}"


def run_inputs(reg: dict) -> dict:
    """The files that make a presentation run of `reg`, and the commit they were read
    at: for a stage replay also tools/s4/stage_run.py, which builds its command, and
    every hook the run links (replay_hooks)."""
    out = {"commit": git_head(),
           "recorder_sha256": sha256(ROOT / RECORDER), "stream_h_sha256": sha256(ROOT / STREAM_H),
           "runner_sha256": sha256(Path(__file__)), "runtime_sha256": RR.runtime_digest(),
           "tick_hook_sha256": sha256(ROOT / TR.HOOK)}
    if reg["stage"]:
        import stage_run as SR
        out["stage_run_sha256"] = sha256(Path(SR.__file__))
        out["stage_hooks_sha256"] = {h: sha256(ROOT / h) for h in replay_hooks(reg)}
    return out


def run_files(reg: dict) -> tuple[str, ...]:
    """The keys of run_inputs that --check holds to the current tree."""
    return RUN_FILES + (STAGE_RUN_FILES if reg["stage"] else ())


def registered(name: str = REPLAY) -> dict:
    """A registered replay: its registry entry, its result, and the run record behind
    the result with the settings loop_run recorded for it (its matrix row), the two
    held to the result's binary through the matrix, and the record to the result's
    hash of it where the result keeps one (a stage result does)."""
    reg = json.loads(TR.REGISTRY.read_text())
    entry = next((e for e in reg["replays"] if e.get("name") == name), None)
    if entry is None:
        raise SystemExit(f"no registered replay named {name} in {rel(TR.REGISTRY)}")
    result_path = TR.OUT / name / "result.json"
    result = json.loads(result_path.read_text())
    lifted = result["lifted"]
    run_json = ROOT / lifted["run_json"]
    matrix = run_json.with_name(run_json.name.replace(".ticks.json", ".matrix.json"))
    run = None
    row = None
    why = None
    if not run_json.is_file() or not matrix.is_file():
        why = f"{rel(run_json)} or its matrix is not on disk"
    elif json.loads(matrix.read_text()).get("sha256") != lifted["binary_sha256"]:
        why = (f"{rel(matrix)} names another binary than the registered result's "
               f"{lifted['binary_sha256'][:12]}: the run record on disk is not the registered run's")
    elif lifted.get("run_json_sha256") and sha256(run_json) != lifted["run_json_sha256"]:
        why = (f"{rel(run_json)} does not hash to the registered result's run_json_sha256 "
               f"{lifted['run_json_sha256'][:12]}: the run record on disk is not the registered run's")
    else:
        run = json.loads(run_json.read_text())
        rows = [r for r in json.loads(matrix.read_text()).get("rows", [])
                if r.get("json") == lifted["run_json"]]
        row = rows[0] if len(rows) == 1 else None
    return {"name": name, "entry": entry, "result": result, "result_path": result_path,
            "result_sha256": sha256(result_path), "run": run, "run_why": why,
            "run_json": run_json, "matrix": matrix, "matrix_row": row,
            "stage": TR.is_stage(entry)}


def default_build(no_trace: bool, replay: str = REPLAY) -> str:
    """A distinct presentation build for this replay, checkout and trace mode."""
    return f"{BUILDS[replay]}{'-counts' if no_trace else ''}.{tree_tag()}"


def build_name_problem(name: str, replay: str | None = None) -> str | None:
    """Why `name` cannot name a presentation build, or None. A presentation build is
    present-<something>, never a name the gates' builds use, so linking one can never
    replace a binary a registered replay or the reach receipt was made with; and for
    `replay`, never another replay's presentation build (BUILDS)."""
    if not name.startswith("present-") or name == "present-" or name.startswith(RESERVED):
        return (f"--build {name}: a presentation build is named present-<something>, never "
                f"one of the gates' builds ({', '.join(RESERVED)})")
    if replay is not None:
        # Any name under another replay's prefix: its exploratory builds
        # (present-mh-probe, present-mh-watch) as well as present-mh.<tree>.
        other = sorted(r for r, b in BUILDS.items() if r != replay and name.startswith(b))
        if other:
            return (f"--build {name}: that is a name of the {other[0]} replay's presentation "
                    f"builds; a run of {replay} would relink it with another configuration")
    return None


def default_paths(replay: str, no_trace: bool, audio: bool, out: Path | None,
                  trace: Path | None) -> tuple[Path, Path | None]:
    """The run's directory and stream, absolute: as given, or the defaults, which are a
    run with audio's own (<replay>-audio), so a run with --audio never renames its
    version 2 stream over a plain run's trace, nor writes its receipt over that run's."""
    name = f"{replay}-counts" if no_trace else f"{replay}-audio" if audio else replay
    out_p = ROOT / (out or Path(GEN_S5) / "present" / tree_tag() / name)
    trace_p = None if no_trace else ROOT / (trace or Path(GEN_S5) / "trace" / f"{name}.sa2s")
    return out_p, trace_p


def paths(out: Path, trace: Path | None) -> dict:
    partial = trace.with_name(f"{trace.name}.{tree_tag()}.partial") if trace else None
    return {"out": out, "ticks": out / "lifted.csv", "words": out / "lifted-words.bin",
            "words_zst": out / "lifted-words.bin.zst", "counts": out / "counts.json",
            "imports": out / "imports.table.json", "overlay": out / "vfsroot",
            "run": out / "run.json", "matrix": out / "matrix.json", "log": out / "loop_run.log",
            "receipt": out / "present-run.json", "trace": trace,
            "trace_status": Path(f"{trace}.status") if trace else None,
            "partial": partial, "partial_status": Path(f"{partial}.status") if trace else None,
            "failed": trace.with_name(f"{trace.name}.{tree_tag()}.failed") if trace else None,
            "counts_status": out / "counts.json.status",
            "ready": trace.with_suffix(".READY") if trace else None,
            # a stage replay's observers' own outputs (stage_run.command names them)
            "stage_trace": out / "stage-trace.csv", "goal_latch": out / "goal-latch.csv",
            "input_applied": out / "input-applied.csv"}


def command(build: str, p: dict, profile_env: dict, meta: dict, audio: bool = False) -> tuple[list[str], str]:
    """loop_run's command for the presentation run, and its --run spec. The stream
    goes to the partial path; it is renamed over the trace only when it holds. With
    `audio` (--audio) the recorder records the audio calls too (SA2_REC_AUDIO=1, a
    version 2 stream); the recorder's settings are its own, so the settings check
    (spec_problems) passes it as it passes the others."""
    meta_json = json.dumps(meta, separators=(",", ":"), sort_keys=True)
    items = (["ticks"] + [f"{k}={v}" for k, v in RR.ENV.items()]
             + [f"SA2_REPLAY_TICKS={rel(p['ticks'])}", f"SA2_REPLAY_WORDS={rel(p['words'])}"]
             + [f"{k}={v}" for k, v in profile_env.items()]
             + [f"SA2_VFS_WRITE_ROOT={rel(p['overlay'])}",
                f"SA2_REC_COUNTS={rel(p['counts'])}"]
             + ([f"SA2_REC_TRACE={rel(p['partial'])}"] if p["trace"] else [])
             + (["SA2_REC_AUDIO=1"] if audio else [])
             + [f"SA2_REC_META={meta_json}"])
    for it in items[1:]:
        if "|" in it:
            raise SystemExit(f"a run setting carries `|`, loop_run's separator: {it!r}")
    spec = "|".join(items)
    cmd = [str(TR.PY), str(ROOT / "tools" / "s4" / "loop_run.py"), "--tag", RR.TAG,
           "--main", RR.MAIN, "--name", build, "--hook", TR.HOOK, "--wrap", TR.WRAP,
           "--hook", RECORDER]
    for w in WRAPS:
        cmd += ["--wrap", w]
    cmd += ["--out-prefix", build, "--timeout", str(RUN_TIMEOUT), "--run", spec]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", f"--probe-arg={PROBE_TIMEOUT}",
            "--probe-arg=--dump-imports", f"--probe-arg={rel(p['imports'])}"]
    return cmd, spec


def stage_alarm(entry: dict) -> tuple[int, int, int]:
    """A stage run's poll budget, the registered run's alarm, and this run's."""
    polls = TR.stage_poll_budget(entry)
    registered_alarm = max(600, polls // 5)       # tick_replay.run_stage_lifted's
    return polls, registered_alarm, STAGE_ALARM_FACTOR * registered_alarm


def stage_command(build: str, p: dict, entry: dict, profile, meta: dict,
                  audio: bool = False) -> tuple[list[str], str]:
    """loop_run's command for a stage replay's presentation run, and its --run spec:
    tools/s4/stage_run.py `command` exactly as tick_replay.run_stage_lifted calls it
    for the observed run -- the entry's stage, its poll budget, its input script, the
    capture's device profile, label "ticks", observe on, the stage trace and word log
    as the observers it adds -- under this build's name and into this run's directory,
    with the recorder's overlay, counts, stream (the partial path) and meta as extra
    settings; then --dump-imports as tick_replay appends it, and the recorder's hook
    and wraps after the stage's own."""
    import stage_run as SR           # here: stage_run imports tick_replay
    polls, _, alarm = stage_alarm(entry)
    extra = {"SA2_STAGE_TRACE": rel(p["stage_trace"]), "SA2_REPLAY_WORDS": rel(p["words"]),
             "SA2_VFS_WRITE_ROOT": rel(p["overlay"]), "SA2_REC_COUNTS": rel(p["counts"])}
    if p["trace"]:
        extra["SA2_REC_TRACE"] = rel(p["partial"])
    if audio:
        extra["SA2_REC_AUDIO"] = "1"
    extra["SA2_REC_META"] = json.dumps(meta, separators=(",", ":"), sort_keys=True)
    for k, v in extra.items():
        if "|" in v:
            raise SystemExit(f"a run setting carries `|`, loop_run's separator: {k}={v!r}")
    cmd, _run_json, ticks = SR.command(
        entry["stage"]["name"], polls, label="ticks", input_script=ROOT / entry["input"]["script"],
        extra_env=extra, timeout=alarm, device_profile=profile, name=build, out_dir=p["out"],
        observe=True)
    if ticks != p["ticks"]:
        raise SystemExit(f"stage_run writes the tick capture to {rel(ticks)}, not {rel(p['ticks'])}")
    cmd += ["--probe-arg=--dump-imports", f"--probe-arg={rel(p['imports'])}"]
    cmd += ["--hook", RECORDER]
    for w in WRAPS:
        cmd += ["--wrap", w]
    return cmd, cmd[cmd.index("--run") + 1]


def replay_hooks(reg: dict) -> tuple[str, ...]:
    """The host sources a replay's lifted run links beside the recorder: the boot's
    tick hook, or every hook of a stage run (tools/s4/stage_run.py HOOKS)."""
    if reg.get("stage"):
        import stage_run as SR
        return tuple(SR.HOOKS)
    return (TR.HOOK,)


def clean_env() -> dict:
    """RR.clean_environ, less anything of the recorder's or the reader's own that a
    shell could leave: the run's spec is the only place those come from. The
    recorder's prefix is its own, so the runtime's SA2_PRESENT_SURVEY is left to
    RR.clean_environ, as the registered run leaves it."""
    return {k: v for k, v in RR.clean_environ().items()
            if not k.startswith(("SA2_REC_", "SA2_STREAM_DUMP_"))}


def compile_c(source: Path, dest: Path, defines: tuple[str, ...] = (),
              env: dict | None = None) -> dict:
    """`source` compiled to `dest` exactly as tools/s4/link_s4.py's compile phase
    compiles a hook: a copy of phase_compile's flags (its zig, target, optimisation
    and include flags, from link_s4's constants), the source by the path given
    (link_s4 passes it resolved), and the checkout's root as the working directory,
    which loop_run gives it and the object's debug information records. The copy is
    held to the phase's own list, read by running the phase, in
    Documentation/scripts/tests/test_s5_stream.py. `env` moves nothing but zig's
    caches. {rc, stderr}."""
    import link_s4 as LS
    cmd = ([str(LS.S.ZIG), "cc"] + LS.TARGET + [LS.OPT, "-w", "-c", "-I", str(LS.RUNTIME),
                                               "-I", str(LS.C.REMILL_INC)]
           + [f"-D{d}" for d in defines] + [str(source), "-o", str(dest)])
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env)
    return {"rc": done.returncode, "stderr": done.stderr[-2000:]}


# ---------------------------------------------------------------- inputs and build

def check_inputs(reg: dict) -> dict:
    """tick_replay's inputs() for the registered entry now, against the ones its
    result was made from: every key, and every pipeline file by name."""
    now = TR.inputs(reg["entry"])
    want = reg["result"].get("inputs") or {}
    differing = []
    for part in ("lifted", "compare"):
        a, b = now.get(part) or {}, want.get(part) or {}
        for k in sorted(set(a) | set(b)):
            if k == "pipeline":
                pa, pb = a.get(k) or {}, b.get(k) or {}
                differing += [f"{part}.pipeline.{f}" for f in sorted(set(pa) | set(pb))
                              if pa.get(f) != pb.get(f)]
            elif a.get(k) != b.get(k):
                differing.append(f"{part}.{k}")
    return {"registered": rel(reg["result_path"]), "differing": differing, "ok": not differing}


def hook_objects(build: str, hooks: tuple[str, ...] = (TR.HOOK,)) -> dict[str, Path]:
    """The objects link_s4 compiles the replay's hooks and the recorder into for
    `build`, by source, as its compile phase names them (extra<k>_<stem>.o, the hooks
    after the generated inputs), whatever k they get."""
    obj = LINKDIR / "mains" / build / "obj"
    out = {}
    for src in (*hooks, RECORDER):
        stem = Path(src).stem
        out[src] = sorted(obj.glob(f"extra*_{stem}.o"))
    return out


def forget_hook_objects(build: str, hooks: tuple[str, ...] = (TR.HOOK,)) -> list[str]:
    """Delete the hooks' objects, so the build compiles every one from this tree."""
    gone = []
    for objs in hook_objects(build, hooks).values():
        for o in objs:
            o.unlink(missing_ok=True)
            gone.append(rel(o))
    return gone


def recompiled(obj: Path) -> dict:
    """This tree's recorder compiled again now, as link_s4 compiled it, against the
    object at `obj`: {sha256, same}. The compile is deterministic for one source path
    and working directory (neither zig's cache nor the output path moves a byte), so
    any difference is a different source, flags or compiler."""
    with tempfile.TemporaryDirectory(prefix="present-rec-") as d:
        dest = Path(d) / "sa2_present_rec.o"
        done = compile_c((ROOT / RECORDER).resolve(), dest)
        if done["rc"]:
            return {"sha256": None, "same": False, "why": done["stderr"][-400:]}
        got = sha256(dest)
    return {"sha256": got, "same": got == sha256(obj)}


def check_build(build: str, matrix: Path | None,
                hooks: tuple[str, ...] = (TR.HOOK,)) -> dict:
    """The link state and the objects the build linked: every phase finished, all
    hooks compiled afresh from this tree's files, the recorder object this tree's
    source compiled again and held to objcheck, the binary the one `matrix` ran and
    no older than that object, and every hash kept."""
    state_path = LINKDIR / f"main.{build}.json"
    if not state_path.is_file():
        return {"ok": False, "why": f"{rel(state_path)} is not there: nothing was built"}
    st = json.loads(state_path.read_text())
    stems = {Path(h).stem for h in (*hooks, RECORDER)}
    reused = [s for s in st.get("compile_reused") or []
              if (m := re.match(r"extra\d+_(.+)\.c$", s)) and m.group(1) in stems]
    objs = hook_objects(build, hooks)
    one = {src: (v[0] if len(v) == 1 else None) for src, v in objs.items()}
    link = st.get("link") or {}
    rec = one.get(RECORDER)
    oc = OC.check(rec) if rec else {"ok": False, "why": f"no single object for {RECORDER}: "
                                                      f"{[rel(o) for o in objs[RECORDER]]}"}
    # What ties the objects to this tree, and the binary that ran to the objects.
    tied = []
    if st.get("phase_in_flight") is not None:
        tied.append(f"the link state's phase {st.get('phase_in_flight')!r} did not finish")
    for src in (*hooks, RECORDER):
        if str((ROOT / src).resolve()) not in (st.get("extra_c") or []):
            tied.append(f"the link state compiled no {src} of this tree's "
                        f"({(ROOT / src).resolve()})")
    again = recompiled(rec) if rec else None
    if again is not None and not again["same"]:
        tied.append(f"this tree's {RECORDER}, compiled again now, is not the object linked "
                    f"({again.get('sha256')}): {again.get('why', 'the bytes differ')}")
    ran = json.loads(matrix.read_text()).get("sha256") if matrix and matrix.is_file() else None
    if ran is None or ran != link.get("sha256"):
        tied.append(f"the binary that ran ({ran}) is not the one the link state names "
                    f"({link.get('sha256')})")
    binary = LINKDIR / "mains" / build / "sa2_main"
    if rec and (not binary.is_file() or binary.stat().st_mtime < rec.stat().st_mtime):
        tied.append("the binary is older than the recorder object it should hold")
    source = OC.source_problems((ROOT / RECORDER).read_text())
    tied += [f"{RECORDER} {x}" for x in source]
    out = {"state": rel(state_path), "compile_ok": st.get("compile_ok"), "link_rc": link.get("rc"),
           "binary_sha256": link.get("sha256"), "hooks_reused": reused,
           "objects": {src: ({"path": rel(o), "sha256": sha256(o)} if o else None)
                       for src, o in one.items()},
           "recompiled": again, "tied": tied,
           "objcheck": oc, "not_allowed": oc.get("not_allowed"),
           "code_finding_count": oc.get("code_finding_count")}
    out["ok"] = bool(st.get("compile_ok") and link.get("rc") == 0 and not reused
                     and all(one.values()) and oc.get("ok")) and not tied
    return out


# ---------------------------------------------------------------- the run

def run(build: str, p: dict, reg: dict, inputs: dict, audio: bool = False) -> dict:
    """Build and run; the run's record, moved into its own directory."""
    profile = TR.native_device_profile(reg["entry"])
    profile_env = RR.profile_env(profile)
    for k in ("ticks", "words", "words_zst", "counts", "counts_status", "imports", "run",
              "matrix", "log", "receipt", "partial", "partial_status"):
        if p[k] is not None:
            Path(p[k]).unlink(missing_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    if p["trace"]:
        p["trace"].parent.mkdir(parents=True, exist_ok=True)
    p["overlay"].mkdir(parents=True, exist_ok=True)
    forgotten = forget_hook_objects(build)
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{secrets.token_hex(4)}"
    meta = {"build": build, "replay": reg["name"], "run": run_id, **inputs, **({"audio": True} if audio else {})}
    cmd, spec = command(build, p, profile_env, meta, audio)
    saves = SS.reset_lifted(cmd, ROOT)
    SS.require_empty(saves)
    t0 = time.time()
    done = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          timeout=RUN_TIMEOUT + 900, env=clean_env())
    seconds = round(time.time() - t0, 2)
    p["log"].write_text(f"$ {' '.join(cmd)}\n--- stdout\n{done.stdout}\n--- stderr\n{done.stderr}")
    for src, dst in ((RUN_DIR / f"{build}.ticks.json", p["run"]),
                     (RUN_DIR / f"{build}.matrix.json", p["matrix"])):
        if src.is_file():
            shutil.move(str(src), str(dst))
    return {"command": cmd, "spec": spec, "saves": saves, "rc": done.returncode,
            "seconds": seconds, "inputs": inputs, "meta": meta,
            "hook_objects_deleted": forgotten,
            "profile": profile if isinstance(profile, str) else
            {"path": profile.get("path"), "sha256": profile.get("sha256")}}


def overlay_files(p: dict) -> dict:
    """What the run's write overlay holds: {relative path: sha256}. The run record
    counts guest writes; this is what they left."""
    root = p["overlay"]
    if not root.is_dir():
        return {}
    return {str(f.relative_to(root)): sha256(f) for f in sorted(root.rglob("*")) if f.is_file()}


def run_stage(build: str, p: dict, reg: dict, inputs: dict, audio: bool = False) -> dict:
    """A stage replay's presentation run, as run() is the boot's, under
    tick_replay.run_stage_lifted's guards besides: the input script at its pin and no
    link from objects the map or arms have since replaced (rebuild_image). loop_run's
    output goes to the log as it comes, so a run of half an hour can be watched."""
    import rebuild_image as RI
    entry = reg["entry"]
    script = ROOT / entry["input"]["script"]
    if not script.is_file() or sha256(script) != entry["input"]["sha256"]:
        raise SystemExit(f"{entry['name']}: its input {entry['input']['script']} is missing or "
                         "does not hash to its pin")
    stale = RI.staged_not_rebuilt()
    if stale:
        raise SystemExit(f"{len(stale)} object(s) the link reads are not the current map or arm "
                         f"objects (first {stale[:3]}): run tools/s3/arms/stage_merged_build.py "
                         f"--tag next --merged-tag {RR.TAG} --force")
    profile = TR.native_device_profile(entry)
    for k in ("ticks", "words", "words_zst", "counts", "counts_status", "imports", "run",
              "matrix", "log", "receipt", "partial", "partial_status", "stage_trace",
              "goal_latch", "input_applied"):
        if p[k] is not None:
            Path(p[k]).unlink(missing_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    if p["trace"]:
        p["trace"].parent.mkdir(parents=True, exist_ok=True)
    p["overlay"].mkdir(parents=True, exist_ok=True)
    forgotten = forget_hook_objects(build, replay_hooks(reg))
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{secrets.token_hex(4)}"
    meta = {"build": build, "replay": entry["name"], "run": run_id, **inputs, **({"audio": True} if audio else {})}
    cmd, spec = stage_command(build, p, entry, profile, meta, audio)
    polls, registered_alarm, alarm_s = stage_alarm(entry)
    for f in (RUN_DIR / f"{build}.ticks.json", RUN_DIR / f"{build}.matrix.json"):
        f.unlink(missing_ok=True)          # existence is the proof this run wrote it
    saves = SS.reset_lifted(cmd, ROOT)
    SS.require_empty(saves)
    t0 = time.time()
    with open(p["log"], "w") as log:
        log.write(f"$ {' '.join(cmd)}\n")
        log.flush()
        done = subprocess.run(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                              text=True, timeout=alarm_s + 900, env=clean_env())
    seconds = round(time.time() - t0, 2)
    for src, dst in ((RUN_DIR / f"{build}.ticks.json", p["run"]),
                     (RUN_DIR / f"{build}.matrix.json", p["matrix"])):
        if src.is_file():
            shutil.move(str(src), str(dst))
    return {"command": cmd, "spec": spec, "saves": saves, "rc": done.returncode,
            "seconds": seconds, "inputs": inputs, "meta": meta,
            "hook_objects_deleted": forgotten, "polls": polls,
            "profile": profile if isinstance(profile, str) else
            {"path": profile.get("path"), "sha256": profile.get("sha256")}}


# ---------------------------------------------------------------- the checks

def spec_problems(mine: dict, theirs: dict, out: str) -> list[str]:
    """Where the settings loop_run recorded for this run (`mine`, its matrix row's
    env) differ from the registered run's (`theirs`): every setting loop_run records
    equal, but the observers' outputs, each this run's own file of the same name in
    `out`, and the recorder's own, which the registered run cannot have. loop_run
    records none of its BASE_ENV keys, even one a run overrides (tools/s4/loop_run.py),
    so SA2_INSTALL and SA2_VFS_WRITE_ROOT are not here: check_saves holds both, as the
    installation and the overlay the run's save reset read."""
    problems = [f"{k} is not in this run's settings: it was not the recorder's run"
                for k in ("SA2_REC_COUNTS", "SA2_REC_META") if k not in mine]
    for k in sorted(set(mine) | set(theirs)):
        if k in RECORDER_VARS:
            if k in theirs:
                problems.append(f"{k} is in the registered run's settings")
            continue
        if k not in mine or k not in theirs:
            problems.append(f"{k} is only in {'this' if k in mine else 'the registered'} "
                            f"run's settings")
        elif k in OBSERVER_OUTPUTS:
            if Path(mine[k]).parent != Path(out) or Path(mine[k]).name != Path(theirs[k]).name:
                problems.append(f"{k}={mine[k]}: an observer writes {Path(theirs[k]).name} "
                                f"into this run's own {out}")
        elif mine[k] != theirs[k]:
            problems.append(f"{k}={mine[k]!r} where the registered run has {theirs[k]!r}")
    return problems


def check_spec(p: dict, reg: dict) -> dict:
    """The run's settings (its matrix row) against the registered run's."""
    row = reg.get("matrix_row")
    if row is None:
        return {"ok": False, "why": reg.get("run_why") or "the registered matrix has no row "
                                                          "for the result's run"}
    mine = json.loads(p["matrix"].read_text()) if p["matrix"].is_file() else {}
    rows = mine.get("rows") or []
    if len(rows) != 1:
        return {"ok": False, "why": f"this run's matrix has {len(rows)} rows, not 1"}
    problems = spec_problems(rows[0].get("env") or {}, row.get("env") or {}, rel(p["out"]))
    if rows[0].get("label") != row.get("label"):
        problems.append(f"label {rows[0].get('label')!r}, registered {row.get('label')!r}")
    return {"against": rel(reg["matrix"]), "settings": len(rows[0].get("env") or {}),
            "problems": problems, "ok": not problems}


def check_saves(saves, reg: dict, p: dict) -> dict:
    """The save reset the run started from (save_state.reset_lifted's record, kept in
    the receipt's run) against the registered run's: the same pin (file, digest and
    state) and the same installation, the installation at its pin, the write overlay
    the run's own, and nothing left in it; the registered run started the same way.
    What each reset removed is not compared: the registered observed run's removed
    the save its baseline run had just written, and a presentation run's overlay is
    its own."""
    want = reg["result"]["lifted"].get("saves") or {}
    if not isinstance(saves, dict):
        return {"ok": False, "why": "the run kept no record of its save reset"}
    problems = [f"{k} {saves.get(k)!r}, and the registered run's is {want.get(k)!r}"
                for k in ("pin", "installation") if saves.get(k) != want.get(k)]
    if saves.get("installation_matches_pin") is not True:
        problems.append("the installation was not at its pin")
    if saves.get("overlay") != rel(p["overlay"]):
        problems.append(f"the reset cleared {saves.get('overlay')!r}, not this run's own "
                        f"overlay {rel(p['overlay'])!r}")
    if saves.get("left") != {}:
        problems.append(f"the write overlay held {sorted(saves.get('left') or {})[:5]} after the "
                        f"reset, or the reset did not record what it held")
    if want.get("installation_matches_pin") is not True or want.get("left") != {}:
        problems.append("the registered run's own reset did not leave an empty overlay at the pin")
    return {"against": rel(reg["result_path"]),
            "pin": (saves.get("pin") or {}).get("digest"), "overlay": saves.get("overlay"),
            "removed": saves.get("removed"), "problems": problems, "ok": not problems}


def check_stage(p: dict, reg: dict) -> dict:
    """A stage replay's own outputs, each hashed to the registered result's pin; and
    the input the run applied, held to the recording again by tick_replay's own
    input_record and equal to the result's account of it (but for its path)."""
    lifted = reg["result"]["lifted"]
    out: dict = {}
    for mine, theirs in STAGE_PINS:
        want = (lifted.get(theirs) or {}).get("sha256")
        got = sha256(p[mine]) if p[mine].is_file() else None
        out[theirs] = {"path": rel(p[mine]), "want": want, "got": got,
                       "ok": bool(want) and got == want}
    inp = lifted.get("input")
    if not isinstance(inp, dict):
        out["input"] = {"ok": False, "why": "the registered result keeps no account of the input "
                                            "its run applied"}
    else:
        def strip(r: dict) -> dict:
            return {k: v for k, v in r.items() if k != "applied"}
        got = (TR.input_record(reg["entry"], p["input_applied"], inp.get("judged_to_tick"))
               if p["input_applied"].is_file() else {"problems": ["no applied-input log"]})
        out["input"] = {"want": strip(inp), "got": strip(got),
                        "ok": not got["problems"] and strip(got) == strip(inp)
                        and (got.get("applied") or {}).get("sha256")
                        == (inp.get("applied") or {}).get("sha256")}
    out["ok"] = all(v["ok"] for v in out.values())
    return out


def leaves(obj, prefix: tuple = ()):
    """(path, value) for every leaf of a JSON value, the path a tuple of keys and
    indices; an empty dict or list is a leaf."""
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            yield from leaves(v, prefix + (k,))
    elif isinstance(obj, list) and obj:
        for i, v in enumerate(obj):
            yield from leaves(v, prefix + (i,))
    else:
        yield prefix, obj


def leaf_name(path: tuple) -> str:
    return "".join(f"[{k}]" if isinstance(k, int) else (f".{k}" if i else str(k))
                   for i, k in enumerate(path))


def same_leaf(a, b) -> bool:
    """Equal in type and value: JSON's true is not 1, and 1 is not 1.0."""
    return type(a) is type(b) and a == b


def check_record(run_rec: dict, reg: dict) -> dict:
    """The run's whole record against the registered run's, leaf for leaf."""
    if reg["run"] is None:
        return {"ok": False, "why": reg["run_why"]}
    if not run_rec:
        return {"ok": False, "why": "the run wrote no record"}
    mine, theirs = dict(leaves(run_rec)), dict(leaves(reg["run"]))
    differing = sorted((k for k in set(mine) | set(theirs)
                        if k not in mine or k not in theirs or not same_leaf(mine[k], theirs[k])),
                       key=leaf_name)
    bad = [k for k in differing if k not in ALLOWED_RECORD_DIFFS]
    return {"against": rel(reg["run_json"]), "leaves": len(theirs),
            "allowed": [leaf_name(k) for k in differing if k in ALLOWED_RECORD_DIFFS],
            "differing": [leaf_name(k) for k in bad[:20]], "differing_count": len(bad),
            "first": {leaf_name(k): [mine.get(k), theirs.get(k)] for k in bad[:5]},
            "ok": not bad}


def check_configuration(run_rec: dict, profile, profile_env: dict) -> dict:
    """What tick_replay.run_lifted asks of the registered run's record: the profile it
    read and the directory it answered (DP.check_run), and every device switch on."""
    problems = []
    try:
        DP.check_run(run_rec, profile)
    except DP.Refusal as e:
        problems.append(str(e))
    problems += RR.switch_problems(run_rec, {**RR.ENV, **profile_env})
    return {"problems": problems, "ok": bool(run_rec) and not problems}


def check_words(p: dict, reg: dict) -> dict:
    """The word log: whole, true to its own capture, and equal to the registered log
    by value at every tick."""
    words = p["words"] if p["words"].is_file() else p["words_zst"]
    reg_words = ROOT / reg["result"]["lifted"]["words"]["path"]
    out: dict = {"log": rel(words), "registered": rel(reg_words)}
    if not words.is_file():
        return {**out, "ok": False, "why": "the run wrote no word log"}
    mine, theirs = WL.info(words), WL.info(reg_words)
    out["whole"] = [mine["whole"], theirs["whole"]]
    out["ticks"] = [mine.get("ticks"), theirs.get("ticks")]
    cap = WL.check_capture(p["ticks"], words, native=False)
    out["capture_check"] = {k: cap.get(k) for k in ("ok", "ticks", "named_cells_compared",
                                                    "named_cells_differing",
                                                    "digest_cells_compared",
                                                    "digest_cells_differing", "problems")}
    h_mine, h_theirs = WL.hashes(words), WL.hashes(reg_words)
    # every tick either side hashed: a tick one side lacks differs too
    differing = sorted(t for t in set(h_mine) | set(h_theirs)
                       if h_mine.get(t) != h_theirs.get(t))
    out["tick_hashes"] = {"mine": len(h_mine), "registered": len(h_theirs),
                          "differing": len(differing), "first_differing": differing[:5]}
    rows, meta = WL.diff(words, reg_words)
    out["diff"] = {"words_differing": len(rows), "first": rows[:5],
                   "compared_through": meta.get("compared_through"),
                   "keyframe_differs": meta.get("keyframe_differs")}
    last = max(h_theirs) if h_theirs else 0
    # The registered run's own capture check, less the file hash the clock moves.
    reg_check = {k: v for k, v in (reg["result"]["lifted"]["words"].get("check") or {}).items()
                 if k != "log_sha256"}
    mine_check = {k: cap.get(k) for k in reg_check}
    out["capture_check_vs_registered"] = {"registered": reg_check, "mine": mine_check}
    out["ok"] = bool(mine["whole"] and theirs["whole"] and cap.get("ok") is True
                     and not differing and h_mine
                     and not rows and meta.get("compared_through") == last
                     and meta.get("keyframe_differs") == "0"
                     and reg_check and mine_check == reg_check)
    return out


def import_rows(p: dict) -> list[dict] | None:
    return json.loads(p["imports"].read_text())["rows"] if p["imports"].is_file() else None


def check_counts(p: dict, run_rec: dict, reg: dict) -> dict:
    """The recorder's own counts against the run's record, the registered run's, and
    the run's import table."""
    if not p["counts"].is_file():
        return {"ok": False, "why": "the recorder wrote no counts (no exit handler ran)"}
    counts = json.loads(p["counts"].read_text())
    d3d9 = run_rec.get("d3d9") or {}
    surface = {(r["interface"], r["slot"]): r["calls"] for r in d3d9.get("dynamic_surface", [])}
    reg_surface = {(r["interface"], r["slot"]): r["calls"]
                   for r in ((reg["run"] or {}).get("d3d9") or {}).get("dynamic_surface", [])}
    mine = {(r["interface"], r["slot"]): r["calls"] for r in counts["methods"]}
    unfinished = [f"{r['interface']}.{r['slot']}" for r in counts["methods"]
                  if r["calls"] != r["entered"]]
    unfinished += [r["name"] for r in counts.get("entries", []) if r["calls"] != r["entered"]]
    rows = import_rows(p) or []
    table = {r["name"]: r["calls"] for r in rows if "d3dx" in r["dll"].lower()
             or r["name"] == ENTRY_NAMES[DIRECT3D_CREATE9]}
    dx = {r["name"]: r["calls"] for r in counts.get("entries", [])}
    dx_diff = {n: [dx.get(n, 0), table.get(n)] for n in ENTRY_NAMES.values()
               if dx.get(n, 0) != table.get(n)}
    tff = (d3d9.get("textures_from_files") or {}).get("answered")
    ctab = (d3d9.get("constant_tables") or {}).get("built")
    loads = sum(dx.get(ENTRY_NAMES[e], 0) for e in (1, 6, 7))
    out = {
        "counting": counts.get("counting"), "why": counts.get("why"),
        "method_calls": counts["method_calls"],
        "surface_sum": sum(surface.values()),
        "distinct_methods": [counts["distinct_methods"], d3d9.get("distinct_methods")],
        "presents": [counts["presents"], (d3d9.get("frames") or {}).get("present")],
        "d3dx_calls": counts["d3dx_calls"],
        "method_and_d3dx_calls": [counts["method_and_d3dx_calls"], d3d9.get("method_calls")],
        "entries": dx,
        "entries_vs_import_table": dx_diff,
        "entries_vs_record": {"textures_from_files": [loads, tff],
                              "constant_tables": [dx.get(ENTRY_NAMES[5]), ctab]},
        "per_method_vs_run": sorted(f"{k[0]}.{k[1]}: {mine.get(k)} vs {surface.get(k)}"
                                    for k in set(mine) | set(surface)
                                    if mine.get(k) != surface.get(k)),
        "per_method_vs_registered": sorted(f"{k[0]}.{k[1]}: {mine.get(k)} vs {reg_surface.get(k)}"
                                           for k in set(mine) | set(reg_surface)
                                           if mine.get(k) != reg_surface.get(k)),
        "unfinished": unfinished,
        "thunks_wrapped": counts.get("thunks_wrapped"),
    }
    out["buffer_watch"] = watch = buffer_watch(counts)
    if "audio" in counts:
        out["audio"] = audio_counts(counts)
    out["ok"] = bool(
        counts.get("counting") is True and not counts.get("why")
        and counts["method_calls"] == out["surface_sum"]
        and out["distinct_methods"][0] == out["distinct_methods"][1]
        and out["presents"][0] == out["presents"][1]
        and out["method_and_d3dx_calls"][0] == out["method_and_d3dx_calls"][1]
        and table and not dx_diff
        and loads == tff and dx.get(ENTRY_NAMES[5]) == ctab
        and not out["per_method_vs_run"] and reg_surface and not out["per_method_vs_registered"]
        and not unfinished and watch["ok"] and (out.get("audio") or {"ok": True})["ok"])
    return out


def audio_counts(counts: dict) -> dict:
    """The recorder's audio counts, on their own: every one of the format's `audio` lines
    wrapped (thunks_wrapped), every call that entered came back, and no store changed
    after its Unlock (store_watch.changed; a change is also an "audio" note, which the
    stream check refuses). The stream check holds the rest to the stream and to the
    devices' own record (stream_dump.compare_counts, stream_dump.hold_audio_to_run)."""
    au = counts.get("audio") or {}
    lines = len(SD.doc_table((ROOT / STREAM_H).read_text())[2])
    unfinished = [r.get("name") for r in au.get("ops") or [] if r.get("entered") != r.get("returned")]
    sw = au.get("store_watch")
    out = {"thunks_wrapped": [au.get("thunks_wrapped"), lines], "unfinished": unfinished, "store_watch": sw}
    out["ok"] = bool(au.get("thunks_wrapped") == lines and not unfinished and isinstance(sw, dict)
                     and sw.get("changed") == 0)
    return out


def buffer_watch(counts: dict) -> dict:
    """The recorder's buffer watch, from its counts file: it ran, buffer Unlocks set it
    (at least one when there were any, and never more than there were), and no
    buffer's bytes changed after its Unlock. The counts file alone cannot say whether
    the watch checked as often as it should have: check_stream holds its counters to
    the ones the stream's own records give (stream_dump.compare_counts). A
    counting-only run has no stream to be faithful to, and no watch."""
    bw = counts.get("buffer_watch")
    unlocks = sum(r["calls"] for r in counts.get("methods", [])
                  if r.get("method") == "Unlock"
                  and r.get("interface") in ("IDirect3DVertexBuffer9", "IDirect3DIndexBuffer9"))
    out = {"unlocks": unlocks, "watch": bw}
    if counts.get("stream") is None:
        out["ok"] = True
        return out
    out["ok"] = bool(isinstance(bw, dict) and bw.get("changed") == 0
                     and bw.get("set", 0) <= unlocks and (bw.get("set", 0) > 0 or not unlocks))
    return out


def check_stream(p: dict, trace: Path, run_rec: dict, meta_want: dict | None,
                 accepted: tuple[str, ...] = ACCEPTED_NOTES) -> dict:
    """The stream at `trace`: whole and true to its rules, its header's rule table the
    lines of host/sa2_stream.h (stream_dump.scan), its counts the recorder's, no note
    outside `accepted`, held to the run's own record and import table
    (stream_dump.hold_to_run), and its meta `meta_want`. A file that is not a stream
    fails, with why."""
    if not trace or not trace.is_file():
        return {"ok": False, "why": "the run wrote no stream"}
    status_path = Path(f"{trace}.status")
    log = ((run_rec.get("d3d9") or {}).get("calls")) or []
    try:
        s = SD.scan(trace, keep_calls=len(log) + 64,
                    doc=SD.doc_table((ROOT / STREAM_H).read_text()),
                    keep_audio=SD.audio_keep(run_rec) if run_rec else 0)
    except SD.StreamError as e:
        return {"path": rel(trace), "ok": False,
                "why": f"{rel(trace)} is not a stream this reader reads: {e}"}
    counts = json.loads(p["counts"].read_text()) if p["counts"].is_file() else {}
    mismatch = SD.compare_counts(s, counts) if counts else ["no counts to hold it to"]
    notes = counts.get("notes") or {}
    notes_bad = {k: v for k, v in notes.items() if v and k not in accepted}
    held = (SD.hold_to_run(s, run_rec, import_rows(p)) + SD.hold_audio_to_run(s, run_rec, counts or None)
            if run_rec else ["no run record"])
    audio_bad = audio_stream_problems(s, counts, meta_want)
    meta_bad = ([] if meta_want is None or s["meta"] == meta_want else
                [f"the stream's meta is {s['meta']!r}, and this run's is {meta_want!r}"])
    status = status_path.read_text().strip() if status_path.is_file() else ""
    out = {"path": rel(trace), "bytes": s["bytes"], "records": s["records"],
           "kinds": s["kinds"], "method_calls": s["method_calls"],
           "distinct_methods": s["distinct_methods"], "d3dx": s["d3dx"],
           "d3dx_calls": s["d3dx_calls"], "frames": s["frames"], "blobs": s["blobs"],
           "first_frame": s["first_frame"], "last_frame": s["last_frame"],
           "content_checked": s["content_checked"],
           "objects_introduced": s["objects_introduced"], "buffer_watch": s["buffer_watch"],
           "files": len(s["files"]),
           "end": s["end"], "notes": notes, "notes_accepted": list(accepted),
           "notes_not_accepted": notes_bad,
           "note_records": s["note_records"], "first_notes": s["notes"][:10],
           "problems": s["problems"][:20], "counts_mismatch": mismatch[:20],
           "held_to_run": held[:20], "meta": s["meta"], "meta_problems": meta_bad,
           "version": s["version"], "audio": s["audio"], "audio_problems": audio_bad,
           "status": status, "header": s["header"]}
    out["ok"] = bool(s["end"] and not s["problems"] and not mismatch and not notes_bad
                     and not held and not meta_bad and not audio_bad and status.startswith("closed:"))
    return out


def audio_stream_problems(s: dict, counts: dict, meta_want: dict | None) -> list[str]:
    """Why the stream is not the one the run asked for: with --audio (the meta's "audio"),
    a version 2 stream of at least one AUDIO record, from a recorder that wrapped every one
    of the format's `audio` lines; without it, a version 1 stream. A recorder that did not
    take SA2_REC_AUDIO, or a stale object, would otherwise pass every other check."""
    lines = len(SD.doc_table((ROOT / STREAM_H).read_text())[2])
    if not (meta_want or {}).get("audio"):
        return ([] if s["version"] == SD.VERSION else
                [f"the run did not ask for audio, and its stream is version {s['version']}"])
    out = []
    if s["version"] != SD.VERSION_AUDIO:
        out.append(f"the run asked for audio (--audio), and its stream is version {s['version']}")
    elif not (s.get("audio") or {}).get("records"):
        out.append("the run asked for audio (--audio), and its stream holds no AUDIO record")
    wrapped = ((counts or {}).get("audio") or {}).get("thunks_wrapped")
    if wrapped != lines:
        out.append(f"the recorder wrapped {wrapped} audio thunks, and host/sa2_stream.h has {lines} `audio` lines")
    return out


def checks(p: dict, reg: dict, *, trace: Path | None, meta_want: dict | None, profile,
           profile_env: dict, saves, accepted: tuple[str, ...] = ACCEPTED_NOTES) -> dict:
    """Every after-the-run check (the module docstring), on the outputs at `p`, the
    stream at `trace` and the save reset the run recorded (`saves`)."""
    out: dict = {}
    want = reg["result"]["lifted"]["sha256"]
    got = sha256(p["ticks"]) if p["ticks"].is_file() else None
    out["lifted_csv"] = {"want": want, "got": got, "ok": got == want}
    run_rec = json.loads(p["run"].read_text()) if p["run"].is_file() else {}
    reach = TR.reach_of(run_rec) if run_rec else None
    out["reach"] = {"want": reg["result"]["lifted"]["reach"], "got": reach,
                    "ok": reach == reg["result"]["lifted"]["reach"]}
    out["record"] = check_record(run_rec, reg)
    out["configuration"] = check_configuration(run_rec, profile, profile_env)
    try:
        out["words"] = check_words(p, reg)
    except SystemExit as e:          # WL.WordLogError is a SystemExit
        out["words"] = {"ok": False, "why": str(e)}
    out["counts"] = check_counts(p, run_rec, reg)
    if trace is not None:
        out["stream"] = check_stream(p, trace, run_rec, meta_want, accepted)
    out["spec"] = check_spec(p, reg)
    out["saves"] = check_saves(saves, reg, p)
    if reg["stage"]:
        out["stage"] = check_stage(p, reg)
    return out


def alarm(p: dict, reg: dict | None = None) -> dict:
    """The run's own time beside the alarm the registered configuration had, and
    this run's: the probe's default and PROBE_TIMEOUT for the boot, and a stage
    run's (stage_alarm)."""
    rows = (json.loads(p["matrix"].read_text()).get("rows") or []) if p["matrix"].is_file() else []
    secs = rows[0].get("seconds") if rows else None
    registered_s, run_s = REGISTERED_ALARM_S, PROBE_TIMEOUT
    if reg is not None and reg.get("stage"):
        _, registered_s, run_s = stage_alarm(reg["entry"])
    return {"registered_alarm_s": registered_s, "run_alarm_s": run_s,
            "row_seconds": secs,
            "past_registered_alarm": secs is not None and secs > registered_s}


# ---------------------------------------------------------------- READY

def stream_meta(trace: Path):
    """The meta the stream at `trace` names in its header (stream_dump.header_meta),
    read without reading the records. StreamError when it is not a stream."""
    with open(trace, "rb") as f:
        first = f.read(16)
        hb = int.from_bytes(first[12:16], "little") if len(first) == 16 else 0
        rest = f.read(max(0, min(hb, 1 << 24) - 16))
    head, _ = SD.read_header(first + rest)
    return SD.header_meta(head)


def stream_owner(trace: Path | None, meta_want: dict | None) -> dict:
    """Whether the stream at `trace` is the run `meta_want` names: its header's meta
    is that run's. {ours, why, meta}."""
    if trace is None:
        return {"ours": False, "why": "no trace", "meta": None}
    if not trace.is_file():
        return {"ours": False, "why": f"there is no stream at {rel(trace)}", "meta": None}
    try:
        meta = stream_meta(trace)
    except SD.StreamError as e:
        return {"ours": False, "why": f"{rel(trace)} is not a stream: {e}", "meta": None}
    if not meta_want:
        return {"ours": False, "why": "the receipt's run names no meta", "meta": meta}
    if meta != meta_want:
        who = meta if not isinstance(meta, dict) else {k: meta.get(k) for k in ("build", "run")}
        return {"ours": False, "meta": meta,
                "why": f"the stream at {rel(trace)} is another run's: its meta names {who}"}
    return {"ours": True, "why": "the stream's meta names this run", "meta": meta}


def read_ready(path: Path) -> dict | None:
    """READY: the stream's sha256 on the first line, the meta of the run that wrote
    it, as JSON, on the second (absent from a READY this runner's first form wrote)."""
    if not path.is_file():
        return None
    lines = path.read_text().splitlines()
    meta = None
    if len(lines) > 1:
        try:
            meta = json.loads(lines[1])
        except ValueError:
            meta = lines[1]
    return {"digest": lines[0].strip() if lines else "", "meta": meta}


def write_ready(path: Path, digest: str, meta: dict | None) -> None:
    tmp = path.with_suffix(".READY.tmp")
    tmp.write_text(digest + "\n" + json.dumps(meta, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(tmp, path)                        # a reader never sees half of it


@contextlib.contextmanager
def trace_lock(trace: Path):
    """An exclusive flock on <trace>.lock, held while a runner changes the trace or its
    READY: every checkout's default trace is the same file, and two runs that settle
    together would otherwise leave one run's stream beside the other's READY. The lock
    is advisory and runners are its only takers; a reader holds the stream to READY's
    digest instead."""
    trace.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{trace}.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def settle(receipt: dict, p: dict, trace: Path | None, source: Path | None,
           keep_failed: bool = False) -> None:
    """READY. On a run that holds, the stream checked at `source` (the partial file of
    a run, the trace itself for --check) becomes the trace, and READY, written after
    it, names its sha256 and the run. A run that fails leaves the last good stream and
    its READY, and deletes its own partial (or keeps it as .failed). A --check that
    fails removes READY only when READY describes this run's stream (the module
    docstring); otherwise it leaves it and says why. All of it under trace_lock."""
    if not trace:
        return
    with trace_lock(trace):
        _settle(receipt, p, trace, source, keep_failed)


def _settle(receipt: dict, p: dict, trace: Path, source: Path | None, keep_failed: bool) -> None:
    meta = (receipt.get("run") or {}).get("meta")
    if receipt["ok"]:
        digest = sha256(source)
        if source != trace:
            p["ready"].unlink(missing_ok=True)
            os.replace(source, trace)
            status = Path(f"{source}.status")
            if status.is_file():
                os.replace(status, p["trace_status"])
        write_ready(p["ready"], digest, meta)
        receipt["ready"] = {"path": rel(p["ready"]), "stream": rel(trace), "stream_sha256": digest}
    elif source == trace:
        why = ready_withdrawal(receipt, p, trace, meta)
        if why is None:
            p["ready"].unlink(missing_ok=True)
            receipt["ready"] = "removed: the stream no longer holds"
        else:
            receipt["ready"] = f"left as it was: {why}"
    else:
        kept = settle_failed(p, keep_failed)
        receipt["ready"] = f"left as it was: this run's stream did not hold; {kept}"


def ready_withdrawal(receipt: dict, p: dict, trace: Path, meta: dict | None) -> str | None:
    """Why a failing --check must leave READY, or None when it withdraws it: READY
    goes only when the stream is this run's, this tree can re-hold the run, and READY
    describes that stream."""
    owner = receipt.get("stream_owner") or {}
    if not owner.get("ours"):
        return owner.get("why") or "the stream is not this run's"
    if not ((receipt.get("inputs_check") or {}).get("ok")
            and (receipt.get("run_matches_tree") or {}).get("ok")):
        return "this tree cannot re-hold the run: its inputs or the files that made it differ"
    ready = read_ready(p["ready"])
    if ready is None:
        return "there is no READY"
    if ready["meta"] is not None and ready["meta"] != meta:
        return "READY describes another run's stream"
    if ready["digest"] != sha256(trace):
        return "READY names other bytes than the stream at the trace"
    return None


def settle_failed(p: dict, keep_failed: bool) -> str:
    """A failed run's partial stream and sidecar: deleted, or kept as .failed."""
    partial, status = p["partial"], p["partial_status"]
    if not partial or not partial.is_file():
        return "it wrote no stream"
    size = partial.stat().st_size
    if keep_failed:
        os.replace(partial, p["failed"])
        if status.is_file():
            os.replace(status, Path(f"{p['failed']}.status"))
        return f"its {size} bytes are kept at {rel(p['failed'])}"
    partial.unlink()
    status.unlink(missing_ok=True)
    return f"its {size} bytes are deleted"


def verdict(receipt: dict) -> bool:
    """Every part holds: the inputs, (for --check) the run's files are the tree's, the
    build, every check, and the run's own exit status."""
    checks_ = receipt.get("checks") or {}
    return bool((receipt.get("inputs_check") or {}).get("ok")
                and (receipt.get("run_matches_tree") or {"ok": receipt.get("mode") == "run"}).get("ok")
                and (receipt.get("build_check") or {}).get("ok")
                and checks_ and all(c.get("ok") for c in checks_.values())
                and receipt.get("run") and not receipt["run"].get("rc"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--replay", choices=sorted(BUILDS), default=REPLAY,
                    help=f"the registered replay to present (default {REPLAY})")
    ap.add_argument("--build", default=None,
                    help=f"default present-<replay>.<tag> (with -counts for --no-trace), tag {tree_tag()}")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"default {GEN_S5}/present/<tag>/<replay> (<replay>-counts with --no-trace)")
    ap.add_argument("--trace", type=Path, default=None,
                    help=f"default {GEN_S5}/trace/<replay>.sa2s")
    ap.add_argument("--no-trace", action="store_true", help="count only: no stream")
    ap.add_argument("--check", action="store_true", help="hold the last run again, no run")
    ap.add_argument("--keep-failed", action="store_true",
                    help="keep a failed run's stream as <trace>.<tag>.failed")
    ap.add_argument("--audio", action="store_true",
                    help="record the audio calls too (SA2_REC_AUDIO=1): a version 2 stream, by "
                         "default at <replay>-audio paths of its own")
    args = ap.parse_args(argv)
    replay = args.replay
    if args.audio and args.no_trace:
        print("--audio records the audio calls into the stream; --no-trace writes none", file=sys.stderr)
        return 2
    build = args.build or default_build(args.no_trace, replay)
    why = build_name_problem(build, replay)
    if why:
        print(why, file=sys.stderr)
        return 2
    out, trace = default_paths(replay, args.no_trace, args.audio, args.out, args.trace)
    p = paths(out, trace)
    if args.check and not p["receipt"].is_file():
        print(f"--check: {rel(p['receipt'])} is not there, so there is no run to hold",
              file=sys.stderr)
        return 2
    old = None
    if args.check:
        try:
            old = json.loads(p["receipt"].read_text())
        except ValueError as e:
            old = f"{e}"
        if not isinstance(old, dict):
            print(f"--check: {rel(p['receipt'])} does not read as a receipt: {old}", file=sys.stderr)
            return 2
    reg = registered(replay)
    now = run_inputs(reg)
    receipt: dict = {"schema": "sa2-present-run/v3", "replay": replay, "build": build,
                     "mode": "check" if args.check else "run", "audio": bool(args.audio),
                     "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                     "tools": {"stream_dump_sha256": sha256(Path(SD.__file__)),
                               "objcheck_sha256": sha256(Path(OC.__file__))},
                     "current": now}
    receipt["inputs_check"] = check_inputs(reg)
    if args.check:
        receipt["run"] = old.get("run")
        made = (receipt["run"] or {}).get("inputs") or {}
        differ = [k for k in run_files(reg) if made.get(k) != now.get(k)]
        receipt["run_matches_tree"] = {"differing": differ,
                                       "ok": bool(receipt["run"]) and bool(made) and not differ}
        receipt["build_check"] = old.get("build_check")
        meta_want = (receipt["run"] or {}).get("meta")
        # the held run's, as the checks read its meta: --audio only chooses the paths
        receipt["audio"] = isinstance(meta_want, dict) and bool(meta_want.get("audio"))
        source = trace
        receipt["stream_owner"] = stream_owner(trace, meta_want) if trace else None
    else:
        if not receipt["inputs_check"]["ok"]:
            receipt["ok"] = False
            p["out"].mkdir(parents=True, exist_ok=True)
            p["receipt"].write_text(json.dumps(receipt, indent=1, default=str) + "\n")
            print(f"NOT RUN: the tree's inputs are not the registered result's: "
                  f"{receipt['inputs_check']['differing'][:10]}", file=sys.stderr)
            return 1
        receipt["run"] = (run_stage(build, p, reg, now, args.audio) if reg["stage"]
                          else run(build, p, reg, now, args.audio))
        if receipt["run"]["rc"]:
            print(f"loop_run exited {receipt['run']['rc']}; see {rel(p['log'])}", file=sys.stderr)
        receipt["build_check"] = check_build(build, p["matrix"], replay_hooks(reg))
        meta_want = receipt["run"]["meta"]
        source = p["partial"]
    profile = TR.native_device_profile(reg["entry"])
    receipt["binary_sha256"] = (json.loads(p["matrix"].read_text()).get("sha256")
                                if p["matrix"].is_file() else None)
    owner = receipt.get("stream_owner")
    held_trace = source if (owner is None or owner["ours"]) else None
    receipt["checks"] = checks(p, reg, trace=held_trace, meta_want=meta_want, profile=profile,
                               profile_env=RR.profile_env(profile),
                               saves=(receipt["run"] or {}).get("saves"))
    if source is not None and held_trace is None:
        receipt["checks"]["stream"] = {"ok": False, "why": owner["why"]}
    receipt["alarm"] = alarm(p, reg)
    receipt["overlay_after"] = overlay_files(p)
    receipt["ok"] = verdict(receipt)
    if p["words"].is_file() and receipt["checks"]["words"].get("ok") is not None:
        z = subprocess.run(["zstd", "-3", "-q", "-f", "--rm", str(p["words"]), "-o",
                            str(p["words_zst"])], capture_output=True, text=True)
        receipt["words_kept"] = {"path": rel(p["words_zst"]), "rc": z.returncode}
    settle(receipt, p, trace, source, args.keep_failed)
    p["out"].mkdir(parents=True, exist_ok=True)
    p["receipt"].write_text(json.dumps(receipt, indent=1, default=str) + "\n")
    shown = ("want", "got", "differing", "differing_count", "problems", "tick_hashes", "diff",
             "hooks_reused", "not_allowed", "code_finding_count", "tied", "binary_sha256",
             "method_calls", "surface_sum", "presents", "entries_vs_import_table",
             "method_and_d3dx_calls", "bytes", "records", "frames", "end", "counts_mismatch",
             "notes_not_accepted", "held_to_run", "meta_problems", "why", "whole", "ticks",
             "settings", "buffer_watch", "content_checked", "audio_problems")
    for k, c in [("inputs", receipt["inputs_check"]), ("build", receipt.get("build_check") or {}),
                 *receipt["checks"].items()]:
        body = {x: y for x, y in c.items() if x in shown}
        if k == "stage":
            body = {x: y.get("ok") for x, y in c.items() if isinstance(y, dict)}
        print(f"{'ok  ' if c.get('ok') else 'FAIL'} {k}: "
              + json.dumps(body, default=str)[:900])
    if args.check:
        print(f"{'ok  ' if receipt['run_matches_tree']['ok'] else 'FAIL'} run_matches_tree: "
              f"{receipt['run_matches_tree']}")
    a = receipt["alarm"]
    print(f"row time {a['row_seconds']} s against the registered alarm of "
          f"{a['registered_alarm_s']} s{': PAST IT' if a['past_registered_alarm'] else ''}")
    print(f"receipt {rel(p['receipt'])}; READY {receipt.get('ready')}; "
          f"{'OK' if receipt['ok'] else 'NOT OK'}")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
