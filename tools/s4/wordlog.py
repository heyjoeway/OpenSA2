#!/usr/bin/env python3
"""Read a word log: any tick's words, the digest columns under any mask, the
words that changed, and whether a capture's log is whole and matches its row.

Subtitle: the host side of the tick probe's word log.

    .venv/bin/python tools/s4/wordlog.py info LOG
    .venv/bin/python tools/s4/wordlog.py check CAPTURE_DIR [--mask H] [--write-capture OUT]
    .venv/bin/python tools/s4/wordlog.py check --csv NAMED.csv --log LOG [--mask H]
    .venv/bin/python tools/s4/wordlog.py csv LOG [--mask H] [--native] [--out OUT]
    .venv/bin/python tools/s4/wordlog.py cost LOG
    .venv/bin/python tools/s4/wordlog.py state LOG TICK OUT
    .venv/bin/python tools/s4/wordlog.py changes LOG LO HI
    .venv/bin/python tools/s4/wordlog.py diff A B [--lo VA --hi VA] [--ticks 400,500]

What a word log is
------------------
harness/replay/native_tick_probe.c logs every word of `.dataa` at every tick
into memory and writes it once, as mods/sa2-word-log.bin, after the last tick
it records (harness/replay/word_log_core.h has the format and the reasons). Its
row in sa2-tick-capture.csv carries the named fields alone: the digest columns
a replay compares are computed here, from the log, by tick_probe_core.h's own
code, so the mask is an input of this reader and not of the game.

How it is read
--------------
tools/s4/wordlog.c does the reading. It is compiled on demand with the pinned
zig against one digest mask (the tree's tick_digest.h unless --mask names
another) and harness/replay's tick_probe_core.h, tick_fields.h and
word_log_core.h, and cached by their contents under
private/generated/s4/wordlog/, so the columns it writes are tp_write_row's.

The capture check (`check`, `check_capture`)
--------------------------------------------
What every log must be before anything is read from it (`log_problems`):
  * whole: the reader read every record to the end record, which is last,
    and, where the log carries the end-of-run check, the words it rebuilds at
    the last tick hash to the writer's shadow;
  * not overflowed;
  * no word logged with the value it already held (the shadow's check);
  * not refused where it began.
And a native probe's log, besides (`native`, the default of `check_capture`):
  * version 2, and written at the run's planned end (the TICK_LIMIT it was
    built with, and at that tick, or the tick before the input probe's end),
    not at OnExit;
  * the tree's one buffer size (word_log_core.h WL_BUFFER_BYTES), because the
    reservation moves what the game allocates after it;
  * the probe's two blocks named (SA2O), both region maps, neither cut at
    WL_MAX_REGIONS, and the end-of-run check (SA2V): without them a pointer
    test counts the probe's blocks as the game's memory, or misses regions.
Then the CSV against the log:
  * the same ticks, 1 to N, each once, every one of them in the log, and the
    CSV's named fields equal to the log's bytes at those addresses on every
    tick;
  * the one-tick-shift control: the named fields compared with the log one
    tick off must differ somewhere, or the check above could not have seen a
    shift (the external-reader design's control, judge-plan.md);
  * where the CSV carries digest columns too, each equal to the one computed
    from the log under the mask given (the tree's tick_digest.h by default).
It returns the problems, never a partial pass, and never raises: whatever
stops it is a problem too. `ok` is that there are none. A `.zst` log is
decompressed first, beside the reader's builds.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPLAY = ROOT / "harness" / "replay"
SOURCE = HERE / "wordlog.c"
CORE = REPLAY / "word_log_core.h"
ROW = REPLAY / "tick_probe_core.h"
FIELDS = REPLAY / "tick_fields.h"
MASK = REPLAY / "tick_digest.h"
LOG = "sa2-word-log.bin"
CAPTURE = "sa2-tick-capture.csv"
ZIG = Path(os.environ.get("SA2_ZIG") or
           ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig")
BUILD = Path(os.environ.get("SA2_WORDLOG_BUILD") or ROOT / "private" / "generated" / "s4" / "wordlog")
CFLAGS = ("-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror")

END_REASONS = {"tick_limit": 1, "input_end": 2}     # the planned ends (word_log_core.h)
RULES = {1: "tick_limit", 2: "input_end"}


class WordLogError(SystemExit):
    """The reader refused, or could not be built."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def buffer_bytes_required(core: Path = CORE) -> int:
    """word_log_core.h's WL_BUFFER_BYTES, the one size every native log must record."""
    m = re.search(r"#define WL_BUFFER_BYTES \((\d+)u << (\d+)\)", core.read_text())
    if not m:
        raise WordLogError(f"{core}: WL_BUFFER_BYTES is not `(N u << S)`; say what size it is")
    return int(m.group(1)) << int(m.group(2))


def section(core: Path = CORE) -> tuple[int, int]:
    """word_log_core.h's WL_BASE and WL_SIZE: the section every log holds."""
    text = core.read_text()
    base = re.search(r"#define WL_BASE (0x[0-9A-Fa-f]+)u", text)
    size = re.search(r"#define WL_SIZE (0x[0-9A-Fa-f]+)u", text)
    if not base or not size:
        raise WordLogError(f"{core}: WL_BASE or WL_SIZE is not `0x...u`; say what the section is")
    return int(base.group(1), 16), int(size.group(1), 16)


def named_fields(fields: Path = FIELDS) -> list[tuple[str, int, int]]:
    """tick_fields.h's rows, (name, va, width), in the order the row writes them."""
    code = re.sub(r"/\*.*?\*/", " ", fields.read_text(), flags=re.S)
    rows = re.findall(r"TP_FIELD\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)u?\s*,\s*(\d+)\s*\)", code)
    if not rows:
        raise WordLogError(f"{fields}: no TP_FIELD rows")
    return [(n, int(v, 16), int(w)) for n, v, w in rows]


def mask_blocks(mask: Path = MASK) -> list[int]:
    """The digest columns a mask header gives, in its order."""
    src = mask.read_text()
    body = src.split("td_base", 1)[1].split("};", 1)[0]
    return [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{8})u", body)]


# ------------------------------------------------------------------ the reader

def reader(mask: Path | None = None) -> Path:
    """The reader built against `mask` (the tree's tick_digest.h by default),
    compiled once per content of its inputs, the compiler's path and its flags."""
    mask = MASK if mask is None else Path(mask)
    parts = [("wordlog.c", SOURCE), ("word_log_core.h", CORE), ("tick_probe_core.h", ROW),
             ("tick_fields.h", FIELDS), ("tick_digest.h", mask)]
    key = hashlib.sha256()
    key.update(str(ZIG).encode() + b"\0" + "\0".join(CFLAGS).encode() + b"\0")
    for name, path in parts:
        key.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    out = BUILD / "readers" / key.hexdigest()[:24]
    exe = out / "wordlog"
    if exe.is_file():
        return exe
    if not ZIG.is_file():
        raise WordLogError(f"the pinned zig is missing: {ZIG}")
    BUILD.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="build-", dir=BUILD))
    try:
        for name, path in parts:
            shutil.copyfile(path, work / name)
        env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(BUILD / "zig-cache"),
                   ZIG_GLOBAL_CACHE_DIR=str(BUILD / "zig-global"))
        p = subprocess.run([str(ZIG), "cc", *CFLAGS, "-o", str(work / "wordlog"),
                            str(work / "wordlog.c")], env=env, capture_output=True, text=True)
        if p.returncode:
            raise WordLogError(f"the reader did not build against {mask}:\n{p.stderr}")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            work.rename(out)
        except OSError:
            if not exe.is_file():        # another process built the same one first
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return exe


@contextlib.contextmanager
def opened(log: Path) -> Iterator[Path]:
    """The log as a file the reader can map: a `.zst` is decompressed first."""
    log = Path(log)
    if log.suffix != ".zst":
        yield log
        return
    BUILD.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="log-", suffix=".bin", dir=BUILD)
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            p = subprocess.run(["zstd", "-dc", str(log)], stdout=f, stderr=subprocess.PIPE)
        if p.returncode:
            raise WordLogError(f"{log}: zstd could not decompress it: {p.stderr.decode()[-300:]}")
        yield Path(tmp)
    finally:
        os.unlink(tmp)


def run(mode: str, *args: str | Path, mask: Path | None = None,
        partial: bool = False) -> subprocess.CompletedProcess:
    """One reader call. Exit 3 is a log that did not read whole: refused unless
    `partial`, when the caller reads what came before and says so."""
    p = subprocess.run([str(reader(mask)), mode, *map(str, args)], capture_output=True, text=True)
    if p.returncode == 2 or (p.returncode == 3 and not partial) or p.returncode not in (0, 2, 3):
        raise WordLogError(f"wordlog {mode} {' '.join(map(str, args))}: exit {p.returncode}: "
                           f"{(p.stderr or p.stdout).strip()[-500:]}")
    return p


def info(log: Path) -> dict:
    """What the log is and how far it read: every key `wordlog info` prints,
    numbers as numbers."""
    with opened(log) as path:
        return _info(path, log)


def _info(path: Path, log: Path) -> dict:
    """`info` of a log already opened (decompressed) at `path`."""
    p = run("info", path, partial=True)
    out: dict = {}
    for line in p.stdout.splitlines():
        k, _, v = line.partition("=")
        if k in ("path", "end_reason", "problem"):
            out[k] = v
        else:
            out[k] = int(v, 0)
    out["path"] = str(log)
    out["whole"] = bool(out["ended"]) and not out["problem"]
    if out["ref_end"] > out["ref_init"] and out["clk_end"] > out["clk_init"] and out["ref_hz"]:
        out["clock_hz"] = ((out["clk_end"] - out["clk_init"]) * out["ref_hz"]
                           / (out["ref_end"] - out["ref_init"]))
    elif out["version"] == 1:
        out["clock_hz"] = float(out["ref_hz"])       # the prototype's clock was QPC itself
    else:
        out["clock_hz"] = None
    return out


def log_problems(i: dict, native: bool = True, *, allow_overflow: bool = False) -> list[str]:
    """What is wrong with a log, from its `info`: the rules every log is held
    to, and with `native` the native probe's (the module docstring's list).
    `allow_overflow` leaves the overflow out, for a caller that reads the ticks
    before it and says so."""
    problems: list[str] = []
    if not i["whole"]:
        problems.append(f"the log does not read whole: {i['problem'] or 'no end record'} "
                        f"(at byte {i['problem_at']}, after tick {i['last_tick']})")
    if i["overflow_tick"] and not allow_overflow:
        problems.append(f"the log overflowed its buffer at tick {i['overflow_tick']}: every tick "
                        "from there is missing")
    if i["noop_words"]:
        problems.append(f"{i['noop_words']} words are logged with the value they already held: "
                        "the writer's shadow went wrong")
    if i["end_reason"] == "refused":
        problems.append("the log's writer refused it where it began: it holds no tick")
    if not native:
        return problems
    if i["version"] != 2:
        problems.append(f"the log is version {i['version']}, not the probe's 2")
    want = buffer_bytes_required()
    if i["buffer_bytes"] != want:
        problems.append(f"the log's buffer is {i['buffer_bytes']} bytes, not the one size "
                        f"word_log_core.h gives every run ({want})")
    rule = RULES.get(i["end_rule"])
    if rule is None:
        problems.append("the probe had no planned end (neither TICK_LIMIT nor INPUT_END_DLL), "
                        "so the log was written, if at all, at OnExit")
    elif i["whole"] and i["end_reason"] != rule:
        problems.append(f"the log was written by {i['end_reason']}, not at its planned end "
                        f"({rule})")
    if rule == "tick_limit" and i["whole"] and i["last_tick"] != i["tick_limit"]:
        problems.append(f"the log ends at tick {i['last_tick']}, not at its TICK_LIMIT "
                        f"{i['tick_limit']}")
    if i["version"] == 2 and i["own_blocks"] != 2:
        problems.append(f"the log names {i['own_blocks']} blocks of its own (SA2O), not the "
                        "probe's shadow and buffer: a pointer test would take them for the "
                        "game's memory")
    if i["whole"]:
        missing = [w for w, k in (("at Init", "regions_init_present"),
                                  ("at the end", "regions_end_present")) if not i.get(k)]
        if missing:
            problems.append(f"the log has no region map {' or '.join(missing)}")
        if i["regions_init_more"] or i["regions_end_more"]:
            problems.append("a region map stopped at WL_MAX_REGIONS regions: a pointer test "
                            "would miss what lies past them")
        if not i["overflow_tick"] and not i["shadow_checked"]:
            problems.append("the log carries no end-of-run check (SA2V): the probe writes one "
                            "after its last tick unless it overflowed")
    return problems


def capture_csv(log: Path, mask: Path | None = None, *, allow_overflow: bool = False,
                native: bool = False) -> str:
    """The tick capture the log gives: tick_probe_core.h's header and rows, the
    named fields and the digest columns of `mask`, for every tick. Refused
    unless the log reads whole, and if it overflowed (unless `allow_overflow`:
    a capture shorter than its run must not pass for the run), logs a word
    with the value it held, or was refused where it began; with `native`, also
    unless it meets the native probe's rules (`log_problems`), for a caller
    that has not run `check_capture` first.

    `native` defaults to False, the lifted side's and a synthetic log's case,
    so a native caller must say `native=True`: every call in the tree's tools
    names `native` (test_wordlog.py holds them to it), and a forgotten flag is
    a failing test, not a capture taken from an OnExit log."""
    key = (sha256(Path(log)), str(reader(mask)), bool(allow_overflow), bool(native))
    if key in _CAPTURES:
        return _CAPTURES[key]
    with opened(log) as path:
        problems = log_problems(_info(path, log), native, allow_overflow=allow_overflow)
        if problems:
            raise WordLogError(f"{log}: {'; '.join(problems)}")
        text = run("csv", path, mask=mask).stdout
    _CAPTURES[key] = text
    return text


# What a log gives is a function of its bytes and of the reader it is read with,
# whose build directory `reader` names by the content of every input (wordlog.c,
# the three headers, the mask, the compiler and its flags). So within a process a
# capture and a field read are kept by the log's sha256 and that directory, with
# the rules they were held to: the same inputs give the same text, and a changed
# log, mask, header, reader or rule is another key. Only an answer is kept; a
# refusal is asked again. The replay-results gate recomputes every registered
# replay for each of its falsifications, and one Metal Harbor pass reads seven
# logs of 15,585 ticks (the U6 fold, the gate's cost). `forget` empties both.
_CAPTURES: dict[tuple, str] = {}
_FIELDS: dict[tuple, dict[int, list[str]]] = {}
_DIFFS: dict[tuple, tuple[list[dict], dict]] = {}


def forget() -> None:
    """Drop every kept capture, field read and diff (the tests' reset)."""
    _CAPTURES.clear()
    _FIELDS.clear()
    _DIFFS.clear()


def field_values(log: Path, fields: list[tuple[str, int, int]] | None = None,
                 partial: bool = False) -> dict[int, list[str]]:
    """tick -> the fields' values as the row writes them (`0x%08x`), every tick;
    kept per process by content (the comment above)."""
    fields = named_fields() if fields is None else fields
    spec = [f"{va:#x}:{w}" for _, va, w in fields]
    key = (sha256(Path(log)), str(reader()), tuple(spec), bool(partial))
    if key not in _FIELDS:
        with opened(log) as path:
            p = run("fields", path, *spec, partial=partial)
        rows = list(csv.reader(io.StringIO(p.stdout)))
        _FIELDS[key] = {int(r[0]): r[1:] for r in rows[1:]}
    return {t: list(v) for t, v in _FIELDS[key].items()}


def state(log: Path, tick: int) -> bytes:
    """The section at `tick` (0: the keyframe), as the log rebuilds it."""
    BUILD.mkdir(parents=True, exist_ok=True)
    with opened(log) as path, tempfile.TemporaryDirectory(dir=BUILD) as d:
        out = Path(d) / "state.bin"
        run("state", path, str(tick), out)
        return out.read_bytes()


def word_at(log: Path, tick: int, va: int) -> int:
    """One word's value at `tick` (from 1)."""
    values = field_values(log, [("w", va, 4)])
    if tick not in values:
        raise WordLogError(f"{log} holds no tick {tick}")
    return int(values[tick][0], 16)


def hashes(log: Path) -> dict[int, str]:
    """tick -> the 64-bit FNV-1a over the section's words (word_log_core.h
    wl_hash, the standard offset basis and prime), the keyframe as tick 0."""
    with opened(log) as path:
        p = run("hashes", path)
    return {int(r["tick"]): r["hash"] for r in csv.DictReader(io.StringIO(p.stdout))}


def changes(log: Path, lo: int, hi: int) -> list[tuple[int, int, int, int]]:
    """Every change of a word in [lo, hi): (tick, va, old, new); tick 0 lists
    the keyframe's words that are not zero."""
    with opened(log) as path:
        p = run("changes", path, f"{lo:#x}", f"{hi:#x}")
    return [(int(r["tick"]), int(r["va"], 16), int(r["old"], 16), int(r["new"], 16))
            for r in csv.DictReader(io.StringIO(p.stdout))]


def diff(a: Path, b: Path, lo: int | None = None, hi: int | None = None,
         ticks: list[int] | None = None) -> tuple[list[dict], dict]:
    """Every word of [lo, hi) the two logs disagree on at some tick from 1, in
    address order: va, first_tick, last_tick, ticks_differing, and both values
    at the first. The second value says how far the logs were compared."""
    base, size = section()
    key = (sha256(Path(a)), sha256(Path(b)), str(reader()), lo, hi,
           tuple(ticks) if ticks else None)
    if key in _DIFFS:                    # kept per process by content, as capture_csv
        rows, meta = _DIFFS[key]
        return [dict(r) for r in rows], dict(meta)
    with opened(a) as pa, opened(b) as pb:
        args: list[str] = [str(pa), str(pb)]
        if lo is not None or hi is not None or ticks:
            args += [f"{lo if lo is not None else base:#x}",
                     f"{hi if hi is not None else base + size:#x}"]
        if ticks:
            args.append(",".join(str(t) for t in ticks))
        p = run("diff", *args)
    meta = dict(kv.split("=", 1) for kv in p.stderr.split() if "=" in kv)
    meta["compared_through"] = int(meta.get("compared_through", 0))
    rows = [{"va": int(r["va"], 16), "first_tick": int(r["first_tick"]),
             "last_tick": int(r["last_tick"]), "ticks_differing": int(r["ticks_differing"]),
             "a": int(r["a"], 16), "b": int(r["b"], 16)}
            for r in csv.DictReader(io.StringIO(p.stdout))]
    if ticks and max(ticks) > meta["compared_through"]:
        raise WordLogError(f"ticks past {meta['compared_through']} are not in both logs: "
                           f"{sorted(t for t in ticks if t > meta['compared_through'])}")
    _DIFFS[key] = ([dict(r) for r in rows], dict(meta))
    return rows, meta


def regions(log: Path) -> dict[str, list[tuple[int, int]]]:
    """The committed regions the log recorded, {"init": [(base, size)...],
    "end": [...]}, and "own": the blocks the probe allocated for the log, which
    the maps list among the game's memory (sizes as requested)."""
    with opened(log) as path:
        p = run("regions", path)
    out: dict[str, list[tuple[int, int]]] = {"init": [], "end": [], "own": []}
    for r in csv.DictReader(io.StringIO(p.stdout)):
        out[r["which"]].append((int(r["base"], 16), int(r["size"], 16)))
    return out


def touched(log: Path) -> dict[int, int]:
    """Every word that changes after the keyframe -> on how many ticks it does."""
    with opened(log) as path:
        p = run("touched", path)
    return {int(r["va"], 16): int(r["ticks_changed"]) for r in csv.DictReader(io.StringIO(p.stdout))}


def pointer_scan(log: Path, small: int = 0x10000, below: int | None = None,
                 cut: tuple[int, int] | None = None) -> dict[int, tuple[int, int, int | None]]:
    """Every word that at some tick (0: the keyframe) holds a value of at least
    `small` inside a region either of the log's maps lists as committed ->
    (that first tick, the value, the first tick it holds a value that is
    neither zero nor an address by the same test, or None if it never does):
    snapshot_fields.py's pointer test, over every value, from the run's own
    maps, less the blocks the probe allocated for the log (their addresses are
    the probe's, and a value inside them is the game's data). `below` keeps
    only regions starting below it: 0x80000000 is the view
    native_snapshot_probe.c's walk had. `cut` (lo, hi) is taken out of every
    region too: snapshot_fields.py cuts the executable's image, so a word is
    flagged only for a value the host chose (the G4 interrogation, B1). The
    third element is the data a word flagged for one value holds at other
    ticks (the review of the G4 round-one fold, Reviewer B's finding 1): a
    value in committed memory is not always an address."""
    args = [f"{small:#x}"]
    if below is not None or cut is not None:
        args.append(f"{below:#x}" if below is not None else "-")
    if cut is not None:
        if not 0 <= cut[0] <= cut[1] <= 0xFFFFFFFF:
            raise ValueError(f"a span to cut is lo <= hi within 32 bits, not {cut}")
        args += [f"{cut[0]:#x}", f"{cut[1]:#x}"]
    with opened(log) as path:
        p = run("pointers", path, *args)
    return {int(r["va"], 16): (int(r["first_tick"]), int(r["value"], 16),
                               int(r["data_tick"]) if r["data_tick"] else None)
            for r in csv.DictReader(io.StringIO(p.stdout))}


def pointer_words(log: Path, small: int = 0x10000, below: int | None = None,
                  cut: tuple[int, int] | None = None) -> dict[int, tuple[int, int]]:
    """pointer_scan's words -> (the first tick one holds an address, that value)."""
    return {va: (t, v) for va, (t, v, _d) in pointer_scan(log, small, below, cut).items()}


def pointer_data_words(log: Path, small: int = 0x10000, below: int | None = None,
                       cut: tuple[int, int] | None = None) -> dict[int, int]:
    """pointer_scan's words that also hold data at some tick -> the first such
    tick (0: the keyframe): a word the pointer test drops for one value while
    it holds data at others, which the drop leaves compared by nothing."""
    return {va: d for va, (_t, _v, d) in pointer_scan(log, small, below, cut).items()
            if d is not None}


def stats(log: Path) -> list[dict]:
    """Per tick: runs, words, the record's bytes and the three clock readings."""
    with opened(log) as path:
        p = run("stats", path, partial=True)
    return [{k: int(v) for k, v in r.items()} for r in csv.DictReader(io.StringIO(p.stdout))]


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def cost(log: Path) -> dict:
    """What the log cost the game's thread and how big it is: the row and the
    scan per tick, the period between ticks, in milliseconds by the clock's
    rate at Init and at the end."""
    i = info(log)
    rows = stats(log)
    hz = i.get("clock_hz")
    out = {"ticks": i["ticks"], "file_bytes": i["file_bytes"],
           "keyframe_bytes": i["size"], "delta_bytes": i["file_bytes"] - i["size"],
           "words_per_tick_p50": _pct([r["nwords"] for r in rows], .5),
           "words_per_tick_p99": _pct([r["nwords"] for r in rows], .99),
           "record_bytes_p50": _pct([r["rec_bytes"] for r in rows], .5),
           "record_bytes_p99": _pct([r["rec_bytes"] for r in rows], .99),
           "record_bytes_max": max((r["rec_bytes"] for r in rows), default=0),
           "clock_hz": hz}
    if hz:
        ms = 1000.0 / hz
        for name, xs in (("row_ms", [(r["t_row"] - r["t_enter"]) * ms for r in rows]),
                         ("scan_ms", [(r["t_scan"] - r["t_row"]) * ms for r in rows]),
                         ("period_ms", [(b["t_enter"] - a["t_enter"]) * ms
                                        for a, b in zip(rows, rows[1:])])):
            if xs:
                out[name] = {"mean": statistics.fmean(xs), "p50": _pct(xs, .5),
                             "p99": _pct(xs, .99), "max": max(xs)}
        periods = [(b["t_enter"] - a["t_enter"]) * ms for a, b in zip(rows, rows[1:])]
        acc = [0.0]
        for x in periods:
            acc.append(acc[-1] + x)
        windows = [(acc[k + 600] - acc[k]) / 1000.0 for k in range(0, max(len(periods) - 599, 0))]
        out["worst_600_tick_window_s"] = max(windows) if windows else None
        out["worst_600_tick_window_s_after_400"] = (max(windows[399:]) if len(windows) > 399
                                                    else None)
    return out


def receipt_fields(log: Path) -> dict:
    """What a capture's receipt says of its log: its identity, whether and how
    it ended, and what it cost (`cost`). The log itself stays private: its
    keyframe is the game's own data section."""
    i = info(log)
    keep = ("version", "file_bytes", "buffer_bytes", "end_rule", "tick_limit", "ticks",
            "end_reason", "overflow_tick", "noop_words", "whole", "problem", "clock_hz",
            "regions_init", "regions_init_more", "regions_end", "regions_end_more",
            "total_words", "max_tick_words", "max_tick_bytes", "own_blocks",
            "regions_init_present", "regions_end_present", "shadow_checked", "shadow_unscanned")
    return {"sha256": sha256(Path(log)), **{k: i[k] for k in keep},
            "cost": cost(log) if i["whole"] else None}


# ------------------------------------------------------------- the capture check

def read_capture(path: Path) -> tuple[list[str], dict[int, list[str]]]:
    """(header, tick -> the row's cells after the tick) of a tick capture."""
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise WordLogError(f"{path}: empty")
    body: dict[int, list[str]] = {}
    for r in rows[1:]:
        if len(r) != len(rows[0]):
            raise WordLogError(f"{path}: a row of {len(r)} cells under a header of {len(rows[0])}")
        t = int(r[0])
        if t in body:
            raise WordLogError(f"{path}: tick {t} has two rows")
        body[t] = r[1:]
    return rows[0], body


def check_capture(named_csv: Path, log: Path, *, mask: Path | None = None,
                  native: bool = True) -> dict:
    """The capture check (module docstring): the log whole and planned, and the
    CSV's row equal to it on every tick. `native` holds the log to the probe's
    rules too (`log_problems`). Never raises: whatever stops the check is one of
    the problems, and `ok` is that there are none."""
    out: dict = {"csv": str(named_csv), "log": str(log), "ticks": 0, "problems": []}
    try:
        _check_capture(Path(named_csv), Path(log), None if mask is None else Path(mask), native,
                       out)
    except (WordLogError, Exception) as e:      # the contract: a problem, never a raise
        out["problems"].append(f"the check could not finish: {type(e).__name__}: {e}")
    out["ok"] = not out["problems"]
    return out


def _check_capture(named_csv: Path, log: Path, mask: Path | None, native: bool,
                   out: dict) -> None:
    problems: list[str] = out["problems"]
    fields = named_fields()
    try:
        i = info(log)
    except WordLogError as e:
        problems.append(f"the reader refused the log: {e}")
        return
    out.update({"log_sha256": sha256(log), "log_bytes": i["file_bytes"], "ticks": i["ticks"],
                "end_reason": i["end_reason"], "overflow_tick": i["overflow_tick"],
                "noop_words": i["noop_words"], "buffer_bytes": i["buffer_bytes"],
                "shadow_checked": i["shadow_checked"], "shadow_unscanned": i["shadow_unscanned"]})
    problems += log_problems(i, native)
    try:
        header, rows = read_capture(named_csv)
    except (WordLogError, OSError, ValueError, csv.Error) as e:
        problems.append(f"the CSV cannot be read: {e}")
        return
    names = [n for n, _, _ in fields]
    if header[:1 + len(names)] != ["tick", *names]:
        problems.append(f"{named_csv}: the header is not tick and tick_fields.h's fields in order")
    extra = header[1 + len(names):]
    ticks = sorted(rows)
    if ticks != list(range(1, len(ticks) + 1)):
        problems.append(f"{named_csv}: the ticks are not 1 to {len(ticks)} without a gap")
    if len(ticks) != i["last_tick"]:
        problems.append(f"the CSV holds {len(ticks)} ticks and the log {i['last_tick']}")
    values = field_values(log, fields, partial=True)
    absent = [t for t in ticks if t not in values]
    if absent:
        problems.append(f"the log holds no row for {len(absent)} of the CSV's ticks, first "
                        f"{absent[0]}")
    n = len(names)
    mismatched: dict[str, list[int]] = {}
    for t in ticks:
        got = values.get(t)
        if got is None:
            continue
        for k, name in enumerate(names):
            if rows[t][k] != got[k]:
                mismatched.setdefault(name, []).append(t)
    compared = sum(1 for t in ticks if t in values) * n
    out["named_cells_compared"] = compared
    out["named_cells_differing"] = sum(len(v) for v in mismatched.values())
    for name, bad in sorted(mismatched.items()):
        problems.append(f"{name} differs from the log's bytes on {len(bad)} ticks, first "
                        f"{bad[0]} (CSV {rows[bad[0]][names.index(name)]}, log "
                        f"{values[bad[0]][names.index(name)]})")
    # The one-tick-shift control: the same comparison a tick off must differ.
    shifted = {d: sum(1 for t in ticks if t + d in values and rows[t][:n] != values[t + d])
               for d in (-1, 1)}
    out["shift_control_rows_differing"] = {"log_tick_before": shifted[-1],
                                           "log_tick_after": shifted[1]}
    if compared and (not shifted[-1] or not shifted[1]):
        problems.append("the named fields are the same a tick off, so this check could not see "
                        "the log shifted a tick against the CSV")
    if not extra:
        return
    digest_cols = [c for c in extra if c.startswith("d_")]
    if len(digest_cols) != len(extra):
        problems.append(f"{named_csv}: columns after the named fields that are not digests: "
                        f"{[c for c in extra if not c.startswith('d_')][:4]}")
    mask_path = MASK if mask is None else mask
    try:
        blocks = mask_blocks(mask_path)
    except (OSError, IndexError, ValueError) as e:
        problems.append(f"the mask {mask_path} cannot be read: {e}")
        return
    if [f"d_{b:08x}" for b in blocks] != digest_cols:
        problems.append(f"{named_csv}: its digest columns are not {mask_path.name}'s blocks")
        return
    if not i["whole"]:
        return
    with opened(log) as path:          # the ticks it holds, overflowed or not
        _, got_rows = read_capture_text(run("csv", path, mask=mask_path).stdout)
    both = [t for t in ticks if t in got_rows]
    bad_cells = [(t, c) for t in both for k, c in enumerate(extra)
                 if rows[t][n + k] != got_rows[t][n + k]]
    out["digest_cells_compared"] = len(both) * len(extra)
    out["digest_cells_differing"] = len(bad_cells)
    if bad_cells:
        problems.append(f"{len(bad_cells)} digest cells differ from the log's, first tick "
                        f"{bad_cells[0][0]} in {bad_cells[0][1]}")


def read_capture_text(text: str) -> tuple[list[str], dict[int, list[str]]]:
    rows = list(csv.reader(io.StringIO(text)))
    return rows[0], {int(r[0]): r[1:] for r in rows[1:]}


# ------------------------------------------------------------------ the command

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    sub = ap.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("info")
    p.add_argument("log", type=Path)
    p = sub.add_parser("cost")
    p.add_argument("log", type=Path)
    p = sub.add_parser("csv")
    p.add_argument("log", type=Path)
    p.add_argument("--mask", type=Path)
    p.add_argument("--native", action="store_true",
                   help="a native probe's log: hold it to the probe's rules too (log_problems)")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("check")
    p.add_argument("capture", type=Path, nargs="?")
    p.add_argument("--csv", type=Path)
    p.add_argument("--log", type=Path)
    p.add_argument("--mask", type=Path)
    p.add_argument("--foreign", action="store_true",
                   help="not a native probe's log: skip the version, buffer and end-rule checks")
    p.add_argument("--write-capture", type=Path,
                   help="when the check passes, write the tick capture with the mask's digest "
                        "columns here")
    p = sub.add_parser("state")
    p.add_argument("log", type=Path)
    p.add_argument("tick", type=int)
    p.add_argument("out", type=Path)
    p = sub.add_parser("changes")
    p.add_argument("log", type=Path)
    p.add_argument("lo", type=lambda s: int(s, 0))
    p.add_argument("hi", type=lambda s: int(s, 0))
    p = sub.add_parser("diff")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    p.add_argument("--lo", type=lambda s: int(s, 0))
    p.add_argument("--hi", type=lambda s: int(s, 0))
    p.add_argument("--ticks")
    a = ap.parse_args(argv)
    if a.mode == "info":
        print(json.dumps(info(a.log), indent=1))
    elif a.mode == "cost":
        print(json.dumps(cost(a.log), indent=1))
    elif a.mode == "csv":
        text = capture_csv(a.log, a.mask, native=a.native)
        if a.out:
            a.out.write_text(text)
        else:
            sys.stdout.write(text)
    elif a.mode == "check":
        named = a.csv or (a.capture / CAPTURE if a.capture else None)
        log = a.log or (a.capture / LOG if a.capture else None)
        if named is None or log is None:
            ap.error("check wants CAPTURE_DIR, or --csv and --log")
        got = check_capture(named, log, mask=a.mask, native=not a.foreign)
        if got["ok"] and a.write_capture:
            a.write_capture.write_text(capture_csv(log, a.mask, native=not a.foreign))
            got["wrote"] = str(a.write_capture)
        print(json.dumps(got, indent=1))
        print(f"WORDLOG_CHECK {'OK' if got['ok'] else 'REFUSED'} ticks={got['ticks']} "
              f"problems={len(got['problems'])}")
        return 0 if got["ok"] else 1
    elif a.mode == "state":
        a.out.write_bytes(state(a.log, a.tick))
    elif a.mode == "changes":
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(["tick", "va", "old", "new"])
        for t, va, old, new in changes(a.log, a.lo, a.hi):
            w.writerow([t, f"{va:#010x}", f"{old:#010x}", f"{new:#010x}"])
    elif a.mode == "diff":
        ticks = [int(t) for t in a.ticks.split(",")] if a.ticks else None
        rows, meta = diff(a.a, a.b, a.lo, a.hi, ticks)
        print(f"compared through tick {meta['compared_through']}; the keyframes differ in "
              f"{meta.get('keyframe_differs')} words; {len(rows)} words differ at some tick",
              file=sys.stderr)
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(["va", "first_tick", "last_tick", "ticks_differing", "a", "b"])
        for r in sorted(rows, key=lambda r: (r["first_tick"], r["va"])):
            w.writerow([f"{r['va']:#010x}", r["first_tick"], r["last_tick"], r["ticks_differing"],
                        f"{r['a']:#010x}", f"{r['b']:#010x}"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
