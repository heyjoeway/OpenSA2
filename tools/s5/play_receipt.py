#!/usr/bin/env python3
"""G5's live-play receipt for either host, from one tools/s5/play_live.py session.

Subtitle: the hook that makes a Metal Harbor session write its per-tick table and its
keyboard polls, and, when the session ends, the receipt that names that table, the player's
report, the run record and the input latency logs by path and sha256, with the host's
renderer, the session's run id and its input latency, button press to presented frame:
Documentation/receipts/s5-play-<host>-v1.json (dxvk-native or sdl3-gpu), the receipt that
Documentation/scripts/check_s5_exit.py's play clause reads. Every session keeps that
document, untracked, in its own directory; the receipt is the same bytes, written only when
the evidence is fit for it (below).

Usage: not a command. `tools/s5/play_live.py --scene metalharbor --receipt` links
ticks_observer()'s hook into the session's build and calls write() when the session ends.
The receipt's writer is play_live, and its pipeline pins PIPELINE's files.

The table's observer
--------------------
The clause's table (check_s5_exit.py, "The receipt contracts") wants, at every GameLoop
return, the stage's state words and the goal latch as the tick left it, the Presents made
so far, and the host keys the DirectInput keyboard applied over the tick's keyboard polls.
No hook in the tree writes the last two, and the goal-latch column of
harness/stage_goal_probe.c has no row for a session's last tick, since it reads at the next
GameLoop call. GameLoop's exit is harness/replay/wrap_tick_probe.c's wrap, and a symbol is
wrapped once, so the observer is that file unchanged, included by path with its wrap renamed
by the preprocessor, inside a wrap of its own. The runtime's d3d9 and DirectInput headers
come before it, so the counters' structs the observer hands the runtime are laid out as the
runtime lays them out, whatever the probe defines. The tick probe runs as it does
everywhere. When it returns, the observer writes one row to TICKS_ENV's path, with these
columns:

  tick           the tick probe's GameLoop count
  state words    CurrentLevel, GameMode, GameState, MainCharObj1[0] and the goal latch,
                 read with the tick probe's own tp_read at check_s5_exit.STATE_WORDS's
                 addresses and widths; a word tp_read cannot read comes out as UNREADABLE,
                 which no 1- or 2-byte word can hold and no 4-byte state word here does
                 (GameMode is a small enum, MainCharObj1[0] a pointer or null)
  frames         the d3d9 device's present_calls: every Present, the device's and a swap
                 chain's, each of which the recorder follows with a FRAME
  keys           the DirectInput keyboard's keys_written less its value at the row before:
                 the keys of every poll since the last GameLoop return, so the column totals
                 keys_written up to the last return

It opens its file at GameLoop's first entry, so a session that ends inside the first tick
leaves a header and no rows. It reads guest memory and the runtime's counters, and writes
nothing the guest can see. The source is generated into the session's directory.

With POLLS_ENV and POLLS_REPORT_ENV both set (play_live sets them beside TICKS_ENV), the
observer also defines the runtime's weak hook sa2_dinput_host_records_read
(runtime/sa2_dinput.h): at every keyboard poll that read FIFO records it writes one row to
POLLS_ENV's path, check_s5_exit.PLAY_POLLS (the poll, the first and last record sequence
it read and their count, the GameLoop return the poll counts toward, the Presents so far,
and CLOCK_MONOTONIC in ns), flushed; at exit, its own account of that log to
POLLS_REPORT_ENV's path (sa2-s5-play-polls/v1: opened, closed, rows and records logged).

What a session gives write()
----------------------------
play_live passes the session's host and run id, its level (the stage's test spawn, which
play_live holds to the level the clause reads), the pipeline's digests as they were when the
session started, for sdl3-gpu the GPU player it ran (`renderer`), and these paths in the
session's directory:

  ticks         the observer's table
  observer      the observer's source, which the session's build compiled
  report        the host player's report.json
  run           loop_run's run record, the probe's JSON
  keys          the player's key log (--key-log)
  presents      the player's Present log (--present-log)
  polls         the game's keyboard polls that read FIFO records (POLLS_ENV)
  relay_frames  play_live's relay: when each frame came whole
  poll_report   the observer's account of the poll log (POLLS_REPORT_ENV)
  relay_report  the relay's account of its arrivals log
  session       play_live's play-live.json for the session, written before write() runs
  record        where the session's document goes
  replaced      where a receipt the session replaces is kept

What it writes
--------------
One document, to the session's record whatever the evidence. It holds the clause's
contract (check_s5_exit.py, "The receipt contracts"): `schema`, `generated_by` (play_live),
`inputs.pipeline` (PIPELINE's files, by sha256), `host`, `stage`, `run_id`, `renderer` and
`evidence` (`ticks`, `player_report`, `run` and the seven input latency files above),
each file named by its path below the repository root as the session wrote it, with the
sha256 of what it holds when the document is written, and `input_latency`. `renderer` is,
for dxvk-native, the DXVK library the player loaded: its report's files.libdxvk_d3d9, by
the path the player was given (present_live's DXVK_LIB below the root), so a worktree,
whose private/ is a link, names it as the main checkout does; for sdl3-gpu, the GPU player
itself, by the content-addressed path play_live ran it from (files.player.real_path is
where that resolves), with no DXVK library in the report. `input_latency` is the clause's
own join of the logs (check_s5_exit.latency_evidence), less its per-press rows: the
summary the clause recomputes. Beside them there are only `written`, the time, and
`inputs.observer`, the observer's source by path and sha256: the code that wrote the table
and the poll log, made from this file, check_s5_exit.py's columns and tick_replay.py's tick
probe. It is kept out of `inputs.pipeline`, which check_receipt_pins.py holds to git at the
receipt's commit, because it is the session's file and not the repository's. Nothing in
the document is a verdict.

When the evidence is fit for it (below), the receipt is written with the record's bytes.
Otherwise the record carries `not_tracked`, listing every reason, and Documentation/ is not
touched; where the clause's join refused but the logs join without its session conditions
(a scripted session's synthetic keys, a session that never reached the goal), the record
also carries `input_latency_diagnostic`, the same summary marked diagnostic, which nothing
reads as evidence.

When the receipt is tracked
---------------------------
All of these must hold:

  * every file is under the root and present;
  * the table reads as the clause reads it: its header, and one integer row per tick
    from 1; and every state word in it is one the observer read, neither UNREADABLE nor
    wider than its width;
  * a tick in the stage (check_s5_exit.in_stage at the session's level) comes before the
    first tick whose goal latch is not zero: the goal ring reached after the stage's start;
    and the table shows the rest the clause reads of it (check_s5_exit.stage_reached): not
    in the stage from its first tick, the level kept from entering it to the goal, at least
    check_s5_exit.AFTER_GOAL_TICKS ticks after the goal (a window closed, or a Ctrl-C,
    sooner after the goal than that ends a session the clause refuses), and a host key
    applied in between;
  * the observer is the source ticks_observer() gives now, so the table's columns are the
    clause's as they are when the receipt is written;
  * the player's report names this session's run in its stream header's meta, is final
    (`complete` true: the stream read to its END, which a closed window or a cancel
    never gives), and counts no synthetic key event;
  * the renderer still hashes to what the player reported loading (for sdl3-gpu: the
    binary the session ran is the one the report names, and no DXVK library is pinned);
  * the run record and the session's play-live.json parse;
  * the clause's latency join takes the logs (check_s5_exit.latency_evidence: every log
    closed whole, every FIFO record polled, every frame relayed and presented, at least
    check_s5_exit.MIN_LATENCY_PRESSES presses presented in the stage, no synthetic key, the
    stream header's meta naming the session's host, the game stopped cleanly:
    check_s5_exit.CLEAN_STOPS); a join that fails in any other way is a reason too, and the
    record is still written;
  * PIPELINE's files hash now as they did when the session started.

What it does not judge
----------------------
It does not judge whether the session meets the play clause: check_s5_exit.py recomputes
that from the evidence at every gate pass. The next session to reach the goal ring replaces
a receipt in place, whatever the clause says of either. Before it does, it copies the
receipt in place, byte for byte, to its own `replaced` path, and it never writes over a
file there, so no receipt is lost to the session after it. reading() gives the clause's
reading of the one in place, which play_live prints.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_s5_exit as X  # noqa: E402  the clause's contract: its names, its table, its stage
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import tick_replay as TR  # noqa: E402  the tick probe: its hook and the GameLoop wrap

HOSTS = tuple(host for host, _ in X.HOSTS)   # play_live --host: each host's player
POLLS_ENV = "SA2_PLAY_POLLS"
POLLS_REPORT_ENV = "SA2_PLAY_POLLS_REPORT"
TICKS_ENV = "SA2_PLAY_TICKS"         # where the game writes its per-tick table
OBSERVER = "sa2_play_ticks.c"        # the observer's source, in the session's directory
RENAMED_WRAP = "sa2_play_ticks_tick_probe"   # the tick probe's GameLoop wrap, in the observer
UNREADABLE = 0xFFFFFFFF              # tp_read's value for a word it cannot read (TR.HOOK)
# The session's input latency logs, as the receipt names them and what each is.
LATENCY_FILES = {"keys": "the player's key log", "polls": "the game's poll log",
                 "presents": "the player's Present log",
                 "relay_frames": "the relay's frame arrivals",
                 "poll_report": "the poll log's report", "relay_report": "the relay's arrivals report",
                 X.SESSION_EVIDENCE: "the session's play-live.json"}
# What made the receipt's evidence beside the build: the writer, and the tick probe the
# observer includes unchanged.
PIPELINE = (X.PLAY_TOOL, "tools/s5/play_receipt.py", TR.HOOK)


POLL_OBSERVER_C = r"""/* Included in the generated play observer after the unchanged tick probe. */
static FILE *play_poll_out;
static const char *play_poll_path, *play_poll_report_path, *play_poll_error;
static uint64_t play_poll_callbacks, play_poll_rows, play_poll_records, play_poll_logged;
static uint64_t play_poll_previous_ns;
static uint32_t play_poll_first_seq, play_poll_last_seq, play_poll_last_poll;
static unsigned play_poll_clock_errors, play_poll_io_errors;
static int play_poll_seen, play_poll_initialized, play_poll_requested;
static int play_poll_opened, play_poll_closed, play_poll_finalized;

static void play_poll_problem(const char *message) {
    if (!play_poll_error) play_poll_error = message;
}

static void play_poll_io_problem(const char *message) {
    ++play_poll_io_errors;
    play_poll_problem(message);
}

static int play_poll_monotonic_ns(uint64_t *out) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) || t.tv_sec < 0 ||
        t.tv_nsec < 0 || t.tv_nsec >= 1000000000L ||
        (uint64_t)t.tv_sec > (UINT64_MAX - (uint64_t)t.tv_nsec) / 1000000000ULL)
        return 0;
    *out = (uint64_t)t.tv_sec * 1000000000ULL + (uint64_t)t.tv_nsec;
    return 1;
}

static void play_poll_json_string(FILE *out, const char *value) {
    if (!value) { fputs("null", out); return; }
    fputc('"', out);
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p == '"' || *p == '\\') { fputc('\\', out); fputc(*p, out); }
        else if (*p < 32) fprintf(out, "\\u%04x", *p);
        else fputc(*p, out);
    }
    fputc('"', out);
}

static void play_poll_finish(void) {
    if (play_poll_finalized) return;
    play_poll_finalized = 1;
    if (play_poll_out) {
        if (fflush(play_poll_out) || ferror(play_poll_out))
            play_poll_io_problem("poll final flush failed");
        if (fclose(play_poll_out)) play_poll_io_problem("poll close failed");
        else play_poll_closed = 1;
        play_poll_out = NULL;
    }
    if (!play_poll_requested || !play_poll_report_path || !*play_poll_report_path)
        return;
    size_t length = strlen(play_poll_report_path);
    if (length > SIZE_MAX - 6) {
        play_poll_io_problem("poll report path overflow"); return;
    }
    char *part = (char *)malloc(length + 6);
    if (!part) { play_poll_io_problem("poll report allocation failed"); return; }
    memcpy(part, play_poll_report_path, length);
    memcpy(part + length, ".part", 6);
    FILE *report = fopen(part, "wx");
    if (!report) {
        play_poll_io_problem("poll report open failed"); free(part); return;
    }
    int ok = play_poll_requested && play_poll_opened && play_poll_closed &&
             !play_poll_error && !play_poll_clock_errors && !play_poll_io_errors &&
             play_poll_callbacks == play_poll_rows && play_poll_records == play_poll_logged;
    fputs("{\"schema\":\"sa2-s5-play-polls/v1\",\"method\":\"v1\","
          "\"clock\":\"CLOCK_MONOTONIC\",\"unit\":\"ns\",\"path\":", report);
    play_poll_json_string(report, play_poll_path);
    fprintf(report, ",\"requested\":%s,\"opened\":%s,\"finalized\":true,"
            "\"closed\":%s,\"ok\":%s,\"callbacks\":%llu,\"rows\":%llu,"
            "\"records_observed\":%llu,\"records_logged\":%llu,\"first_seq\":",
            play_poll_requested ? "true" : "false", play_poll_opened ? "true" : "false",
            play_poll_closed ? "true" : "false", ok ? "true" : "false",
            (unsigned long long)play_poll_callbacks, (unsigned long long)play_poll_rows,
            (unsigned long long)play_poll_records, (unsigned long long)play_poll_logged);
    if (play_poll_seen) fprintf(report, "%u", play_poll_first_seq); else fputs("null", report);
    fputs(",\"last_seq\":", report);
    if (play_poll_seen) fprintf(report, "%u", play_poll_last_seq); else fputs("null", report);
    fputs(",\"last_poll\":", report);
    if (play_poll_seen) fprintf(report, "%u", play_poll_last_poll); else fputs("null", report);
    fprintf(report, ",\"clock_errors\":%u,\"io_errors\":%u,\"dropped_rows\":%llu,\"error\":",
            play_poll_clock_errors, play_poll_io_errors,
            (unsigned long long)(play_poll_callbacks - play_poll_rows));
    play_poll_json_string(report, play_poll_error);
    fputs("}\n", report);
    int bad = ferror(report);
    if (fflush(report)) bad = 1;
    if (ferror(report)) bad = 1;
    if (fclose(report)) bad = 1;
    struct stat existing;
    if (!bad && (lstat(play_poll_report_path, &existing) == 0 || errno != ENOENT)) bad = 1;
    if (!bad && rename(part, play_poll_report_path)) bad = 1;
    if (bad) {
        play_poll_io_problem("poll report publication failed");
        fprintf(stderr, "play poll report publication failed: %s\n", part);
    }
    free(part);
}

static void play_poll_open(void) {
    play_poll_initialized = 1;
    play_poll_path = getenv("SA2_PLAY_POLLS");
    play_poll_report_path = getenv("SA2_PLAY_POLLS_REPORT");
    int raw = play_poll_path && *play_poll_path;
    int report = play_poll_report_path && *play_poll_report_path;
    play_poll_requested = raw || report;
    if (!play_poll_requested) return;
    if (atexit(play_poll_finish)) {
        play_poll_io_problem("poll finalizer registration failed"); return;
    }
    if (!raw || !report || !strcmp(play_poll_path, play_poll_report_path)) {
        play_poll_problem("poll raw/report paths must both be distinct"); return;
    }
    play_poll_out = fopen(play_poll_path, "wx");
    if (!play_poll_out) { play_poll_io_problem("poll log open failed"); return; }
    play_poll_opened = 1;
    if (fputs("poll\tfirst_seq\tlast_seq\trecords\ttick\tpresents\tmono_ns\n", play_poll_out) < 0)
        play_poll_io_problem("poll header write failed");
    if (fflush(play_poll_out) || ferror(play_poll_out))
        play_poll_io_problem("poll header flush failed");
}

void sa2_dinput_host_records_read(uint32_t poll, uint32_t first_seq,
                                  uint32_t last_seq, uint32_t records) {
    /* Snapshot the actual boundary before lazy configuration or any output IO. */
    uint64_t now = 0;
    int clock_ok = play_poll_monotonic_ns(&now);
    unsigned long returned = sa2_replay_gameloop_returns();
    sa2_d3d9_stats device;
    sa2_d3d9_stats_get(&device);
    uint32_t presents = device.present_calls;
    uint32_t at_poll = poll, at_first = first_seq, at_last = last_seq, at_records = records;
    if (play_poll_callbacks == UINT64_MAX || UINT64_MAX - play_poll_records < at_records) {
        play_poll_problem("poll observer counter overflow");
    } else {
        ++play_poll_callbacks;
        play_poll_records += at_records;
    }
    if (!clock_ok || (play_poll_seen && now < play_poll_previous_ns)) {
        ++play_poll_clock_errors;
        play_poll_problem("poll monotonic clock failed or moved backwards");
    }
    if (returned == ULONG_MAX || !at_records || at_last < at_first ||
        (uint64_t)at_last - at_first + 1 != at_records ||
        (play_poll_seen && (at_poll <= play_poll_last_poll ||
         play_poll_last_seq == UINT32_MAX || at_first != play_poll_last_seq + 1)))
        play_poll_problem("poll tick or record range is invalid");
    if (!play_poll_seen) play_poll_first_seq = at_first;
    play_poll_last_seq = at_last;
    play_poll_last_poll = at_poll;
    play_poll_seen = 1;
    if (clock_ok) play_poll_previous_ns = now;
    if (!play_poll_initialized) play_poll_open();
    if (!play_poll_out || play_poll_error) return;
    if (fprintf(play_poll_out, "%u\t%u\t%u\t%u\t%lu\t%u\t%llu\n",
                at_poll, at_first, at_last, at_records, returned + 1, presents,
                (unsigned long long)now) < 0) {
        play_poll_io_problem("poll row write failed"); return;
    }
    if (fflush(play_poll_out) || ferror(play_poll_out)) {
        play_poll_io_problem("poll row flush failed"); return;
    }
    ++play_poll_rows;
    play_poll_logged += at_records;
}
"""

class Unfit(Exception):
    """A reason the session's evidence cannot be the tracked receipt."""


def receipt_path(host: str) -> str:
    """G5's play receipt for `host`, the path check_s5_exit.play() reads."""
    if host not in HOSTS:
        raise SystemExit(f"no play receipt for the host {host!r}: the hosts are {', '.join(HOSTS)}")
    return X.PLAY_RECEIPT.format(host=host)


def pipeline() -> dict[str, str]:
    """PIPELINE's files as they are now, by sha256."""
    return {name: X.sha256_file(ROOT / name) for name in PIPELINE}


def ticks_observer(root: Path = ROOT) -> str:
    """The observer's C source (the module docstring, "The table's observer"): `root`'s
    tick probe, included by path with its wrap renamed, and a wrap around it that writes
    check_s5_exit.PLAY_TICKS' row after it returns."""
    columns = X.PLAY_TICKS
    words = columns[1:-2]
    if (columns[0], columns[-2:]) != ("tick", ("frames", "keys")) or not all(
            word in X.STATE_WORDS for word in words):
        raise SystemExit(f"check_s5_exit.PLAY_TICKS is {columns}: not the tick, state words "
                         "check_s5_exit.STATE_WORDS names, then frames and keys, which this "
                         "observer writes")
    reads = [X.STATE_WORDS[word][1:] for word in words]
    row = "\\t".join(["%lu"] + [f"0x%0{2 * width}x" for _, width in reads] + ["%u", "%llu"])
    peeks = (",\n" + " " * 12).join(f"tp_read(0x{va:08X}u, {width})" for va, width in reads)
    probe = (root / TR.HOOK).resolve()
    return f"""/* Generated by tools/s5/play_receipt.py (ticks_observer); do not edit.
 *
 * The play receipt's observer: {TR.HOOK}, unchanged, with its
 * GameLoop wrap renamed, and a wrap around it that writes one row per GameLoop return to
 * {TICKS_ENV}: the tick (the tick probe's count); the state words
 * {", ".join(words)},
 * read with the tick probe's own tp_read at Documentation/scripts/check_s5_exit.py's
 * STATE_WORDS; the Presents so far (the d3d9 device's present_calls); and the host keys the
 * DirectInput keyboard applied since the row before (keys_written, less its value then). It
 * reads guest memory and the runtime's counters and writes nothing the guest can see. The
 * runtime's headers come first, so the counters' structs are laid out as the runtime lays
 * them out, whatever the tick probe defines.
 */
#include "sa2_d3d9.h"
#include "sa2_dinput.h"
#include <time.h>
#include <limits.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>

#define __wrap_{TR.WRAP} {RENAMED_WRAP}
#include "{probe}"
#undef __wrap_{TR.WRAP}

{POLL_OBSERVER_C}

static FILE *play_out;
static int play_off;
static uint64_t play_keys;          /* keys_written at the row before */

static void play_open(void) {{
    const char *path = getenv("{TICKS_ENV}");
    if (!path || !*path || !(play_out = fopen(path, "w"))) {{
        play_off = 1;
        return;
    }}
    fputs("{"\\t".join(columns)}\\n", play_out);
    fflush(play_out);
}}

static void play_row(unsigned long t) {{
    sa2_d3d9_stats d3d;
    sa2_di_input_stats in;
    sa2_d3d9_stats_get(&d3d);
    sa2_dinput_input_stats_get(&in);
    fprintf(play_out, "{row}\\n", t,
            {peeks},
            d3d.present_calls, (unsigned long long)(in.keys_written - play_keys));
    play_keys = in.keys_written;
    fflush(play_out);               /* a session can end in a trap; every row must be on disk */
}}

void *__wrap_{TR.WRAP}(void *state, uint32_t pc, void *memory);
void *__wrap_{TR.WRAP}(void *state, uint32_t pc, void *memory) {{
    if (!play_out && !play_off)
        play_open();
    void *r = {RENAMED_WRAP}(state, pc, memory);
    if (play_out)
        play_row(sa2_replay_gameloop_returns());
    return r;
}}
"""


def ref(root: Path, path: Path, what: str) -> dict:
    """`path` as a receipt names it: below `root` as the session wrote it (a link is not
    followed to name it), with the sha256 of what it holds now."""
    full = Path(os.path.abspath(path))
    try:
        named = full.relative_to(os.path.abspath(root)).as_posix()
    except ValueError:
        raise Unfit(f"{what} {path} is not under {root}") from None
    if not full.is_file():
        raise Unfit(f"{what} {named} is absent")
    return {"path": named, "sha256": X.sha256_file(full)}


def json_object(path: Path, what: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Unfit(f"{what} does not parse: {error}") from None
    if not isinstance(doc, dict):
        raise Unfit(f"{what} is not a JSON object")
    return doc


def table(path: Path) -> list[dict[str, int]]:
    """The game's per-tick table, read as the clause reads it, once every state word in it
    is one the observer read: not UNREADABLE, which the clause would take for a goal latch
    set, and no wider than its width."""
    what = f"the per-tick table {path.name}"
    try:
        rows = X.integers(X.table(path, X.PLAY_TICKS, what), X.PLAY_TICKS, what)
        X.numbered(rows, "tick", what)
    except X.Refused as why:
        raise Unfit(str(why)) from None
    words = [(column, X.STATE_WORDS[column][2]) for column in X.PLAY_TICKS
             if column in X.STATE_WORDS]
    for row in rows:
        for word, width in words:
            value = row[word]
            if value == UNREADABLE:
                raise Unfit(f"{what}: {word} is {value:#x} at tick {row['tick']}, the tick "
                            f"probe's mark for a word it could not read")
            if not 0 <= value < 1 << 8 * width:
                raise Unfit(f"{what}: {word} is {value:#x} at tick {row['tick']}, not a "
                            f"{width}-byte value")
    return rows


def generated_now(path: Path, named: str) -> None:
    """The observer the session compiled is the source ticks_observer() gives now: the
    columns it wrote are check_s5_exit.py's, from the tick probe tick_replay.py names."""
    try:
        now = ticks_observer()
    except SystemExit as why:
        raise Unfit(str(why)) from None
    if path.read_bytes() != now.encode("utf-8"):
        raise Unfit(f"the table's observer {named} is not the one tools/s5/play_receipt.py "
                    f"generates now: the columns or the tick probe it was made from moved")


def goal(rows: list[dict[str, int]], level: int, host: str) -> dict[str, int]:
    """The first tick in the stage and the first tick the goal latch is set, once the
    second comes after the first, and once the table shows all the play clause reads of it
    (check_s5_exit.stage_reached: the stage's start shown, the level kept to the goal, the
    clause's AFTER_GOAL_TICKS after it, a host key in between). A session a close or a
    Ctrl-C ended too soon after the goal is one the clause refuses, so it is not tracked."""
    entered = next((row["tick"] for row in rows if X.in_stage(row, level)), None)
    if entered is None:
        raise Unfit(f"the table never shows level {level} in play (GameMode_Level, "
                    f"GameStates_Ingame, a player)")
    rose = next((row["tick"] for row in rows if row["goal_latch"] != 0), None)
    if rose is None:
        raise Unfit("the goal latch never rises: the session did not reach the goal ring")
    if rose <= entered:
        raise Unfit(f"the goal latch is set at tick {rose}, not after the stage is entered at "
                    f"tick {entered}")
    try:
        X.stage_reached(rows, level, host)
    except X.Refused as why:
        raise Unfit(f"the per-tick table: {why}") from None
    return {"entered": entered, "goal": rose}


def same_run(report: dict, run_id: str) -> None:
    try:
        meta = X.stream_meta(report, "the player's report")
    except X.Refused as why:
        raise Unfit(str(why)) from None
    if meta["run"] != run_id:
        raise Unfit(f"the player's report is of run {meta['run']!r}, not this session's "
                    f"{run_id!r}")


def renderer(root: Path, report: dict, host: str, ran: Path | None = None) -> dict:
    """The host's renderer ("The hosts" in check_s5_exit.py), once it still holds what the
    player reported: for dxvk-native the DXVK library the player loaded, by the path it was
    given (where its report has none, the path that resolved to); for sdl3-gpu the GPU player
    itself, by the path play_live ran it from (`ran`), which must resolve to the binary the
    report names, and a report that pins no DXVK library."""
    if host == "dxvk-native":
        lib = X.field(report, "files.libdxvk_d3d9")
        loaded = (lib.get("path") or lib.get("real_path")) if isinstance(lib, dict) else None
        if not isinstance(loaded, str) or not loaded:
            raise Unfit("the player's report names no DXVK library it loaded")
        named = ref(root, Path(loaded), "the DXVK library the player loaded")
        if named["sha256"] != lib.get("sha256"):
            raise Unfit(f"the DXVK library {named['path']} does not hash to what the player "
                        f"loaded")
        return named
    if host != "sdl3-gpu":
        raise Unfit(f"no definition of the host {host!r}")
    dxvk = X.field(report, "files.libdxvk_d3d9.sha256")
    if dxvk is not None or X.field(report, "host_identity.dxvk_file_verified") is True:
        raise Unfit(f"the GPU player's report pins a DXVK library (files.libdxvk_d3d9 {dxvk!r})")
    player = X.field(report, "files.player")
    real = player.get("real_path") if isinstance(player, dict) else None
    if not isinstance(real, str) or not real:
        raise Unfit("the player's report names no binary that ran (files.player.real_path)")
    if ran is None:
        raise Unfit("the session names no GPU player it ran")
    named = ref(root, ran, "the GPU player the session ran")
    if os.path.realpath(ran) != real:
        raise Unfit(f"the GPU player {named['path']} is not the binary the report names ran "
                    f"({real})")
    if named["sha256"] != player.get("sha256"):
        raise Unfit(f"the GPU player {named['path']} does not hash to what ran")
    return named


def finished(report: dict) -> None:
    """The player read the stream to its END: a report that is not final is a session that
    did not end cleanly (a window closed, a cancel), which the clause refuses."""
    if report.get("complete") is not True:
        raise Unfit(f"the player's report is not final (complete is {report.get('complete')!r}): "
                    "the session did not end with the stream read to its END")


def scripted(report: dict) -> None:
    """No synthetic key reached the game: --synthetic-keys sessions verify the transport and
    are never a person playing."""
    events = X.field(report, "host_input.synthetic_key_events")
    if events != 0 or "synthetic_keys" in report:
        raise Unfit(f"the session's keys were synthetic ({events!r} synthetic key events, "
                    "--synthetic-keys): a scripted session is never the play receipt")


def held(root: Path, files: dict, name: str) -> X.HeldLatencyFile:
    path = root / files[name]["path"]
    return X.HeldLatencyFile(files[name]["path"], files[name]["sha256"], path.read_bytes())


def stage_span(rows: list[dict[str, int]], level: int) -> tuple[int | None, int | None]:
    """The zero-based indices of the first tick in the stage and the first with the goal
    latch set, as check_s5_exit.session() reads them."""
    entry = next((i for i, row in enumerate(rows) if X.in_stage(row, level)), None)
    goal = next((i for i, row in enumerate(rows) if row["goal_latch"] != 0), None)
    return entry, goal


def latency(root: Path, host: str, files: dict, report: dict, run: dict, live: dict,
            rows: list[dict[str, int]], level: int) -> tuple[dict | None, dict | None, list[str]]:
    """The session's input latency: (the receipt's input_latency, a diagnostic join, reasons).

    The first is the clause's own join (check_s5_exit.latency_evidence) over the held logs,
    the summary a receipt holds and the clause recomputes. When the clause's join refuses,
    that is a reason, and the raw logs are joined without its session conditions
    (synthetic keys allowed, the stage taken to the last tick where the goal never came) so
    that a scripted session still measures its latency, in the record only."""
    entry, goal = stage_span(rows, level)
    data = {name: held(root, files, name) for name in X.LATENCY_EVIDENCE}
    why = []
    if entry is not None and goal is not None and entry < goal:
        try:
            result = X.latency_evidence(root, "the input latency", host, data, report, run,
                                        live, rows, entry, goal)
            return X.latency_summary(result), None, why
        except X.Refused as refused:
            why.append(str(refused))
    try:
        keys, polls, presents, arrivals = X.latency_tables(
            {name: data[name].data for name in data}, "the input latency", synthetic=True)
        first = entry if entry is not None else 0
        last = goal if goal is not None and goal > first else len(rows) - 1
        result = X.input_latency(keys, polls, presents, arrivals, rows, first, last)
    except X.Refused as refused:
        if not why:
            why.append(str(refused))
        return None, None, why
    diagnostic = X.latency_summary(result) | {
        "diagnostic": True, "stage_ticks": [rows[first]["tick"], rows[last]["tick"]]}
    return None, diagnostic, why


def assemble(session: dict, root: Path) -> tuple[dict, dict | None, list[str], dict | None]:
    """The receipt's document as far as the evidence lets it be made, where the goal was
    reached, every reason the document is not the tracked receipt, and a diagnostic latency
    join where the clause's own join could not be made."""
    why: list[str] = []
    host = session["host"]

    def take(make, *args):
        try:
            return make(*args)
        except Unfit as reason:
            why.append(str(reason))
            return None

    def now(name: str) -> str | None:
        try:
            return X.sha256_file(ROOT / name)
        except OSError:                 # gone since the session started: changed too
            return None
    changed = [name for name in PIPELINE if session["pipeline"].get(name) != now(name)]
    if changed:
        why.append(f"{', '.join(changed)} changed during the session: the receipt's writer is "
                   f"not the one the session started with")
    observer = take(ref, root, session["observer"], "the table's observer")
    if observer:
        take(generated_now, Path(session["observer"]), observer["path"])
    files = {"ticks": take(ref, root, session["ticks"], "the per-tick table"),
             "player_report": take(ref, root, session["report"], "the player's report"),
             "run": take(ref, root, session["run"], "the run record")}
    for name, what in LATENCY_FILES.items():
        files[name] = take(ref, root, session[name], what)
    reached = report = run = live = rows = None
    if files["ticks"]:
        rows = take(table, Path(session["ticks"]))
        reached = take(goal, rows, session["level"], host) if rows is not None else None
    if files["player_report"]:
        report = take(json_object, Path(session["report"]), "the player's report")
        if report is not None:
            take(same_run, report, session["run_id"])
            take(finished, report)
            take(scripted, report)
    if files["run"]:
        run = take(json_object, Path(session["run"]), "the run record")
    if files["session"]:
        live = take(json_object, Path(session["session"]), "the session's play-live.json")
    measured = diagnostic = None
    if all(files[name] for name in X.LATENCY_EVIDENCE) and None not in (rows, report, run, live):
        try:
            measured, diagnostic, refused = latency(root, host, files, report, run, live, rows,
                                                    session["level"])
        except Exception as error:          # the record is written whatever the join met
            measured = diagnostic = None
            refused = [f"the input latency could not be joined: {type(error).__name__}: {error}"]
        why += refused
    doc = {"schema": X.PLAY_SCHEMA, "generated_by": X.PLAY_TOOL,
           "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "inputs": {"pipeline": dict(session["pipeline"]), "observer": observer},
           "host": host, "stage": X.STAGE, "run_id": session["run_id"],
           "renderer": (take(renderer, root, report, host, session.get("renderer"))
                        if report is not None else None),
           "evidence": files, "input_latency": measured}
    if measured is None and not why:
        why.append("the input latency could not be measured")
    return doc, reached, why, diagnostic


def write(session: dict, root: Path = ROOT, dest_root: Path | None = None) -> dict:
    """The session's document, in its record whatever the evidence; and, when the evidence
    is fit for it, the same bytes as the tracked receipt under `dest_root` (by default the
    root the evidence is named below), once a receipt in place is kept in the session's
    directory. What was written, and why."""
    receipt = receipt_path(session["host"])
    doc, reached, why, diagnostic = assemble(session, Path(root))
    if why:
        doc["not_tracked"] = why
        if diagnostic is not None:
            doc["input_latency_diagnostic"] = diagnostic
    text = json.dumps(doc, indent=1) + "\n"
    record = Path(session["record"])
    record.write_text(text, encoding="utf-8")
    if why:
        return {"receipt": None, "record": str(record), "not_tracked": why,
                "latency": doc["input_latency"] or diagnostic}
    dest = Path(dest_root or root) / receipt
    replaced = keep(dest, Path(session["replaced"])) if dest.exists() else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        part.write_text(text, encoding="utf-8")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)        # a write that failed leaves no part behind
    return {"receipt": receipt, "record": str(record), "replaced": replaced, **reached,
            "latency": doc["input_latency"]}


def keep(dest: Path, kept: Path) -> dict:
    """The receipt in place, copied byte for byte to `kept` before it is replaced, never
    over a file there: the run it names, and where it is kept."""
    held = dest.read_bytes()
    with kept.open("xb") as copy:
        copy.write(held)
    try:
        run = json.loads(held).get("run_id")
    except (ValueError, AttributeError):
        run = None
    return {"run_id": run, "kept": str(kept)}


def reading(host: str, root: Path = ROOT) -> str:
    """The play clause's reading of this host's session from the receipt in place (the
    clause itself needs the other host's too)."""
    try:
        shows = X.session(Path(root), host, X.stage_level(Path(root)))["shows"]
    except X.Refused as why:
        return f"the play clause refuses this host's session: {why}"
    return f"the play clause takes this host's session: {shows}"
