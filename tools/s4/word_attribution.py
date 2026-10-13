#!/usr/bin/env python3
"""Which compared words differ between two snapshot runs, and whose are they?

Subtitle: a digest block a replay diverges in, taken down to the word and to
the code that owns it.

    .venv/bin/python tools/s4/word_attribution.py A.bin B.bin --block 0x01760000 \\
        [--block ...] [--all-words] [--ticks 400,500] [--reach 0x400] [--json OUT]

A and B are two word logs (sa2-word-log.bin), or two snapshot files.

Why this exists
---------------
A replay's tick capture says *which 64 KiB block* differs, because its digest
columns hash the words `harness/replay/tick_digest.h` selects, one column per
block. Knowing which *words* differ, and which code writes them, took the same
hand-written steps twice on 2026-09-23:

- the three natively unstable stage blocks, CRI's streaming state
  (`agents-journals/s4/2026-09-23-vanilla-oracle.md`);
- the boot replay's `d_01760000` at tick 379, Sofdec's worker records, which
  exist only when GetSystemInfo reports more than one processor
  (`agents-journals/s4/2026-09-23-native-outcomes.md`).

Rule 13 makes the step a tool before its third use.

What it does
------------
A and B are files of the same kind:
- two word logs, "SA2F", which hold every word of `.dataa` at every tick
  (harness/replay/word_log_core.h), written natively by the tick probe; read
  through tools/s4/wordlog.py, which compares them word by word at every tick
  in C and says how far both logs went;
- or two snapshot files: "SA2S" whole-`.dataa` records, written natively by
  `harness/replay/native_snapshot_probe.c` and on the lifted side by
  `harness/replay/wrap_tick_probe.c` with SA2_REPLAY_SNAPSHOTS, or "SA2B"
  per-block records.
A log and a snapshot file are not compared with each other: their ticks are
not the same kind of sample.

It compares every tick both files hold, or `--ticks`; the report's `ticks` is
the list compared and `ticks_range` its first and last. In each `--block` it
compares the words the digest mask selects, or every word with `--all-words`;
on logs, only the words of the logged section, which the first and last digest
blocks reach past; the report's `words_compared` counts those, and
`words_outside_the_log` the block's words left out.
A word the mask does not select cannot move a digest column, and the first
hand attribution named such a word as the cause, wrongly (the interrogation
of the vanilla-oracle decision, reviewer B, W4).

Each differing word is attributed by the executable's base relocations. It
takes the nearest address a relocation points at, at or below the word and
within `--reach` bytes. That is the variable the code names, with the word as
a field of it. Then it lists every code site holding that address, with the
function and region the analyzer's map gives it. The attribution is a lead
for a reader, not a verdict: a word reached only through a pointer the code
computes has no relocated base near it. Such a word is reported as
unattributed, not guessed.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

DIGEST = ROOT / "harness" / "replay" / "tick_digest.h"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
BLOCK = 0x10000


def digest_words(block: int, header: Path = DIGEST) -> list[int]:
    """The words `tick_digest.h` selects in `block`: bit i of mask word k is
    the block's word 32k+i. Refused if the block has no digest column."""
    src = header.read_text()
    bases = [int(x, 16) for x in
             re.findall(r"0x([0-9a-fA-F]{8})u", src.split("td_base", 1)[1].split("};", 1)[0])]
    if block not in bases:
        raise SystemExit(f"{block:#010x} is not a digest block in {header}")
    table = src.split("td_mask", 1)[1].split("= {", 1)[1]
    rows = re.findall(r"\{([^{}]*)\}", table)
    mask = [int(x.strip().rstrip("uU"), 0) for x in rows[bases.index(block)].split(",") if x.strip()]
    if len(mask) != BLOCK // 4 // 32:
        raise SystemExit(f"{header}: block {block:#010x} has {len(mask)} mask words, not "
                         f"{BLOCK // 4 // 32}")
    return [block + 4 * (32 * k + i) for k in range(len(mask)) for i in range(32)
            if mask[k] >> i & 1]


def load_kind(path: Path) -> str:
    """"log" for a word log, "snapshot" for a snapshot file; refused otherwise."""
    if path.suffix == ".zst":
        return "log"                    # wordlog.py decompresses it; snapshots are never compressed
    with path.open("rb") as f:
        magic = f.read(4)
    if magic == b"SA2F":
        return "log"
    if magic in (b"SA2S", b"SA2B", b"SA2R"):
        return "snapshot"
    raise SystemExit(f"{path}: not a word log or a snapshot file (magic {magic!r})")


def logged(words: list[int]) -> list[int]:
    """The words of `words` a word log holds: those inside the logged section
    (wordlog.section()). The first and last digest blocks (0x00910000,
    0x02670000) reach past `.dataa`, and no log holds their words outside it,
    so on logs these are the words compared, and the report counts them."""
    import wordlog as WL
    base, size = WL.section()
    return [w for w in words if base <= w and w + 4 <= base + size]


def differing_logs(a: Path, b: Path, words: list[int], ticks: list[int] | None) -> dict[int, dict]:
    """`differing` for two word logs: word -> {first tick it differs, the two
    values then, the last tick and how many}, over every tick both logs hold,
    or `ticks`. Only the `logged` words are compared. Refused where either log
    does not read whole to where they were compared."""
    import wordlog as WL
    words = logged(words)
    if not words:
        return {}
    rows, _ = WL.diff(a, b, min(words), max(words) + 4, ticks)
    want = set(words)
    return {r["va"]: {"first_tick": r["first_tick"], "a": r["a"], "b": r["b"],
                      "last_tick": r["last_tick"], "ticks_differing": r["ticks_differing"]}
            for r in rows if r["va"] in want}


def report_head(a: Path, b: Path, logs: bool, ticks: list[int]) -> dict:
    """The report's first keys: the files, their kind, and the ticks compared,
    as a list (`ticks`) and as its first and last (`ticks_range`)."""
    return {"a": str(a), "b": str(b), "kind": "log" if logs else "snapshot",
            "ticks": list(ticks), "ticks_range": [ticks[0], ticks[-1]]}


def load(path: Path):
    """tick -> address -> the 4 bytes there, or None where the file holds none."""
    import snapshot_fields as SF
    magic = path.read_bytes()[:4]
    if magic == b"SA2S":
        snaps, _, base = SF.parse(path)

        def get(tick: int, addr: int):
            s = snaps.get(tick)
            off = addr - base
            return s[off:off + 4] if s is not None and 0 <= off <= len(s) - 4 else None
        return sorted(snaps), get
    if magic in (b"SA2B", b"SA2R"):
        blocks = SF.parse_blocks(path)

        def get(tick: int, addr: int):
            for (t, b), data in ((k, v) for k, v in blocks.items() if k[0] == tick):
                if b <= addr <= b + len(data) - 4:
                    return data[addr - b:addr - b + 4]
            return None
        return sorted({t for t, _ in blocks}), get
    raise SystemExit(f"{path}: not a snapshot file (magic {magic!r})")


def differing(get_a, get_b, ticks: list[int], words: list[int]) -> dict[int, dict]:
    """word -> {first tick it differs, the two values then, ticks it differs}.
    A word neither file holds at a tick is not compared there."""
    out: dict[int, dict] = {}
    for t in ticks:
        for w in words:
            a, b = get_a(t, w), get_b(t, w)
            if a is None or b is None or a == b:
                continue
            row = out.setdefault(w, {"first_tick": t, "a": struct.unpack("<I", a)[0],
                                     "b": struct.unpack("<I", b)[0], "ticks": []})
            row["ticks"].append(t)
    return out


def relocation_targets(exe: Path, lo: int, hi: int) -> dict[int, list[int]]:
    """Every address in [lo, hi) a HIGHLOW base relocation's slot holds -> the
    slots' addresses, which are the code or data that names it."""
    import pefile
    pe = pefile.PE(str(exe), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_BASERELOC"]])
    image = pe.get_memory_mapped_image()
    base = pe.OPTIONAL_HEADER.ImageBase
    out: dict[int, list[int]] = {}
    for blk in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []):
        for e in blk.entries:
            if e.type != 3:                          # IMAGE_REL_BASED_HIGHLOW
                continue
            v = struct.unpack_from("<I", image, e.rva)[0]
            if lo <= v < hi:
                out.setdefault(v, []).append(base + e.rva)
    return out


def function_index(fmap: list[dict]):
    """A lookup from an address to the map's function holding it, or None."""
    funcs = sorted((int(f["address"], 16), int(f.get("size") or 0), f) for f in fmap)
    starts = [f[0] for f in funcs]

    def of(addr: int):
        i = bisect.bisect_right(starts, addr) - 1
        if i >= 0 and addr < funcs[i][0] + max(funcs[i][1], 1):
            return funcs[i][2]
        return None
    return of


def attribute(word: int, targets: dict[int, list[int]], func_of, reach: int) -> dict:
    """The nearest relocated base at or below `word` within `reach`, and who holds it."""
    below = [t for t in targets if t <= word and word - t < reach]
    if not below:
        return {"base": None, "sites": []}
    b = max(below)
    sites = []
    for s in sorted(targets[b]):
        f = func_of(s)
        sites.append({"site": f"{s:#010x}", "function": f["name"] if f else None,
                      "region": f.get("region") if f else None})
    return {"base": f"{b:#010x}", "offset": f"{word - b:#x}", "sites": sites}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--block", action="append", required=True,
                    type=lambda s: int(s, 0), help="a digest block's base; repeatable")
    ap.add_argument("--all-words", action="store_true",
                    help="every word of the block, not only those the digest selects")
    ap.add_argument("--ticks", default=None, help="comma-separated; default every common tick")
    ap.add_argument("--reach", type=lambda s: int(s, 0), default=0x400)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)

    kinds = {load_kind(a.a), load_kind(a.b)}
    if len(kinds) != 1:
        raise SystemExit(f"{a.a} and {a.b} are a word log and a snapshot file; compare two of "
                         "one kind")
    logs = kinds == {"log"}
    if logs:
        import wordlog as WL
        through = min(WL.info(a.a)["last_tick"], WL.info(a.b)["last_tick"])
        ticks = [int(t) for t in a.ticks.split(",")] if a.ticks else list(range(1, through + 1))
        if not ticks or max(ticks) > through or min(ticks) < 1:
            raise SystemExit(f"the logs share ticks 1 to {through}; asked for {a.ticks}")
    else:
        ticks_a, get_a = load(a.a)
        ticks_b, get_b = load(a.b)
        ticks = sorted(set(ticks_a) & set(ticks_b))
        if a.ticks:
            want = [int(t) for t in a.ticks.split(",")]
            missing = sorted(set(want) - set(ticks))
            if missing:
                raise SystemExit(f"ticks {missing} are not in both files (common: {ticks})")
            ticks = want
    if not ticks:
        raise SystemExit("the two files share no tick")

    import common as C
    fmap = json.loads((C.GEN / "function-map.json").read_text())["functions"]
    func_of = function_index(fmap)
    report = {**report_head(a.a, a.b, logs, ticks), "blocks": []}
    for blk in a.block:
        words = (list(range(blk, blk + BLOCK, 4)) if a.all_words else digest_words(blk))
        asked = len(words)
        if logs:                # what the logs hold, so words_compared counts what was compared
            words = logged(words)
        diff = (differing_logs(a.a, a.b, words, ticks if a.ticks else None) if logs
                else differing(get_a, get_b, ticks, words))
        targets = relocation_targets(EXE, blk - a.reach, blk + BLOCK)
        rows = []
        for w in sorted(diff):
            d = diff[w]
            rows.append({"word": f"{w:#010x}", "first_tick": d["first_tick"],
                         "a": f"{d['a']:#010x}", "b": f"{d['b']:#010x}",
                         "ticks_differing": d.get("ticks_differing", len(d.get("ticks", []))),
                         **attribute(w, targets, func_of, a.reach)})
        outside = {"words_outside_the_log": asked - len(words)} if logs else {}
        report["blocks"].append({"block": f"{blk:#010x}", "words_compared": len(words), **outside,
                                 "words_differing": len(rows), "rows": rows})
        print(f"WORD_ATTRIBUTION block={blk:#010x} compared={len(words)} "
              + (f"outside_the_log={asked - len(words)} " if logs else "")
              + f"differing={len(rows)} ticks={len(ticks)}")
        for r in rows:
            owners = sorted({f"{s['function']} ({s['region']})" for s in r["sites"]})
            where = (f"base {r['base']}+{r['offset']}: {', '.join(owners[:4])}"
                     if r["base"] else "unattributed: no relocated base within reach")
            print(f"  {r['word']} from tick {r['first_tick']}: {r['a']} vs {r['b']}; {where}")
    if a.json:
        a.json.write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
