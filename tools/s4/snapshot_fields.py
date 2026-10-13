#!/usr/bin/env python3
"""Which words of the game's data a tick replay compares, chosen from native evidence.

Subtitle: choose a replay's digest mask word by word from the tick probe's word
logs of native runs configured exactly like the comparison runs, and refuse it
unless a held-out capture reproduces it; or read the older snapshot evidence.

    .venv/bin/python tools/s4/snapshot_fields.py --word-logs CAP CAP CAP [...] \
        --held-out CAP [--exclude-words JSON] --emit harness/replay/masks/REPLAY.h \
        --receipt Documentation/receipts/s4-mask-REPLAY-v1.json
    .venv/bin/python tools/s4/snapshot_fields.py --word-logs CAP ... --dry-run \
        [--leave-one-out] [--compare HEADER] [--out DIR]
    .venv/bin/python tools/s4/snapshot_fields.py lifted [NAME]    # the lifted run's snapshots
    .venv/bin/python tools/s4/snapshot_fields.py NATIVE_A NATIVE_B LIFTED [--out DIR]

The word-log mode is the method (the comment that opens its section has the rule
and its refusals). The user ruled on 2026-09-24 that a mask is built from runs
configured exactly like the comparison runs, and that a probe loaded only to
build it perturbs what it measures
(Documentation/decisions/2026-09-24-S4-halt-220h.md): the snapshot runs below
loaded harness/replay/native_snapshot_probe.c, which no comparison run loads,
so the snapshot mode still reads that evidence and no longer writes a mask.

Why (the snapshot method, 2026-09-22 to 24)
-------------------------------------------
The boot replay compares the words GameLoop references, and over the boot only
three of them vary. A replay whose compared state barely moves cannot show
much. Choosing more words by hand would be guessing, and choosing the words
where the lifted build happens to agree would be worse: a field list selected
for agreement proves nothing. So the classes are decided by the two native
runs alone:

  * **varying**: the word takes more than one value over the sampled ticks,
    and both native runs give the same sequence;
  * **unrepeatable**: the two native runs disagree at some sampled tick, so a
    replay could not be judged on it;
  * **pointer-like**: a varying word one of whose native values falls inside
    a region the native process had committed (the probe's region map), with
    the null page and small integers excluded. Such a value is an address,
    and allocation addresses are not comparable across the two sides without
    an identity policy;
  * **constant**: one value throughout, the same in both runs.

The candidates for the tick field list are the varying words that are not
pointer-like. The lifted run is then compared on every class, and reported
separately, so a word that is constant natively and moves lifted is visible
too, not just the candidates.

Only aligned 32-bit words are classified. A word holding several byte-sized
variables varies when any of them does, which is the right answer for exact
comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import tempfile
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = 4096
SMALL = 0x10000          # values below this are counts and flags, never addresses


def parse(path: Path) -> tuple[dict[int, bytes], list[tuple[int, int]], int]:
    """(tick -> snapshot bytes, committed regions, base) from one snapshot file."""
    data = path.read_bytes()
    snaps: dict[int, bytes] = {}
    regions: list[tuple[int, int]] = []
    base = None
    i = 0
    while i < len(data):
        if i + 16 > len(data):
            raise SystemExit(f"{path}: a record header is cut short at byte {i}")
        magic = data[i:i + 4]
        a, b, c = struct.unpack_from("<III", data, i + 4)
        i += 16
        if magic == b"SA2S":
            if base is None:
                base = b
            elif b != base:
                raise SystemExit(f"{path}: snapshots at different bases {base:#x} and {b:#x}")
            if a in snaps:
                raise SystemExit(f"{path}: tick {a} snapshotted twice")
            snaps[a] = data[i:i + c]
            if len(snaps[a]) != c:
                raise SystemExit(f"{path}: the snapshot at tick {a} is cut short")
            i += c
        elif magic == b"SA2R":
            regions = [struct.unpack_from("<II", data, i + 8 * k) for k in range(b)]
            i += 8 * b
        else:
            raise SystemExit(f"{path}: unknown record {magic!r} at byte {i - 16}")
    if not snaps:
        raise SystemExit(f"{path}: no snapshot")
    return snaps, regions, base


def parse_blocks(path: Path, regions: list | None = None) -> dict[tuple[int, int], bytes]:
    """(tick, block base) -> bytes, from a per-block ("SA2B") snapshot file.
    Its region map, if any, is appended to `regions` when one is passed."""
    data = path.read_bytes()
    out: dict[tuple[int, int], bytes] = {}
    i = 0
    while i < len(data):
        if i + 16 > len(data):
            raise SystemExit(f"{path}: a record header is cut short at byte {i}")
        magic = data[i:i + 4]
        tick, base, size = struct.unpack_from("<III", data, i + 4)
        i += 16
        if magic == b"SA2R":                # the region map: `base` counts its pairs
            if regions is not None:
                regions.extend(struct.unpack_from("<II", data, i + 8 * k) for k in range(base))
            i += 8 * base
            continue
        if magic != b"SA2B":
            raise SystemExit(f"{path}: record {magic!r} at byte {i - 16} is not a block snapshot")
        out[(tick, base)] = data[i:i + size]
        if len(out[(tick, base)]) != size:
            raise SystemExit(f"{path}: the block {base:#x} at tick {tick} is cut short")
        i += size
    return out


def unstable(paths: list[Path], candidates: set[int] | None = None) -> tuple[set[int], dict]:
    """Words that differ between any two native runs at any tick they share.

    The 100-tick samples cannot see a word that differs between runs only
    between samples -- the loading threads' progress, which follows the wall
    clock -- and a digest over such a word does not repeat. Every run's
    per-tick block snapshots are compared word by word."""
    maps: list[list] = [[] for _ in paths]
    runs = [parse_blocks(p, m) for p, m in zip(paths, maps)]
    keys = set(runs[0])
    for r in runs[1:]:
        keys &= set(r)
    if not keys:
        raise SystemExit("the block snapshot files share no (tick, block)")
    out: set[int] = set()
    first: dict[int, int] = {}
    for tick, base in sorted(keys):
        ref = runs[0][(tick, base)]
        for r in runs[1:]:
            other = r[(tick, base)]
            if other == ref:
                continue
            for off in range(0, len(ref) - 3, 4):
                if ref[off:off + 4] != other[off:off + 4]:
                    a = base + off
                    out.add(a)
                    first.setdefault(base, tick)
    # A word that holds an address at any of these ticks is an address, even if
    # the 100-tick samples only ever saw it hold an integer (2026-09-23 review):
    # it is dropped with the unstable words, and counted apart. Only the
    # candidates are looked at, at every third tick, with the regions bisected:
    # a word-by-word scan of every block at every tick is some 10^8 lookups.
    import bisect
    pointerish: set[int] = set()
    if candidates:
        cand = sorted(candidates)
        for run, regs in zip(runs, maps):
            if not regs:
                continue
            regs = sorted(regs)
            starts = [b for b, _ in regs]
            ticks = sorted({t for t, _ in keys})[::3]
            for (tick, base), blob in run.items():
                if tick not in ticks or (tick, base) not in keys:
                    continue
                words = array("I", blob[:len(blob) - len(blob) % 4])
                lo, hi = bisect.bisect_left(cand, base), bisect.bisect_left(cand, base + len(blob))
                for a in cand[lo:hi]:
                    k = (a - base) >> 2
                    if k >= len(words) or a in pointerish:
                        continue
                    v = words[k]
                    if v < SMALL:
                        continue
                    j = bisect.bisect_right(starts, v) - 1
                    if j >= 0 and v < regs[j][0] + regs[j][1]:
                        pointerish.add(a)
    pointerish -= out
    out |= pointerish
    blocks = sorted({b for _, b in keys})
    return out, {"runs": len(paths), "ticks": len({t for t, _ in keys}),
                 "pointer_like_at_some_tick": len(pointerish),
                 "blocks": [f"{b:#010x}" for b in blocks],
                 "first_unstable_tick_by_block": {f"{b:#010x}": t for b, t in sorted(first.items())},
                 "unstable_words": len(out)}


def in_regions(value: int, regions: list[tuple[int, int]]) -> bool:
    return value >= SMALL and any(b <= value < b + s for b, s in regions)


def classify(a: dict[int, bytes], b: dict[int, bytes], regions: list[tuple[int, int]],
             base: int) -> dict[str, list[int]]:
    """Every aligned word's native class, by address."""
    ticks = sorted(a)
    if sorted(b) != ticks:
        raise SystemExit(f"the native runs sampled different ticks: {ticks} and {sorted(b)}")
    size = len(a[ticks[0]])
    out = {"varying": [], "unrepeatable": [], "pointer_like": []}
    for off in range(0, size, PAGE):
        pages_a = [a[t][off:off + PAGE] for t in ticks]
        pages_b = [b[t][off:off + PAGE] for t in ticks]
        moves = any(p != pages_a[0] for p in pages_a[1:])
        repeat = pages_a == pages_b
        if not moves and repeat:
            continue                        # constant and repeatable: every word constant
        wa = [array("I", p[:len(p) - len(p) % 4]) for p in pages_a]
        wb = [array("I", p[:len(p) - len(p) % 4]) for p in pages_b]
        for k in range(len(wa[0])):
            seq_a = [w[k] for w in wa]
            if seq_a != [w[k] for w in wb]:
                out["unrepeatable"].append(base + off + 4 * k)
            elif any(v != seq_a[0] for v in seq_a):
                cls = ("pointer_like" if any(in_regions(v, regions) for v in set(seq_a))
                       else "varying")
                out[cls].append(base + off + 4 * k)
    return out


def compare_lifted(native: dict[int, bytes], lifted: dict[int, bytes], base: int,
                   addrs: list[int]) -> dict:
    """For each address, the first sampled tick where the lifted word differs."""
    ticks = sorted(native)
    missing = [t for t in ticks if t not in lifted]
    first = {}
    for addr in addrs:
        o = addr - base
        for t in ticks:
            if t in missing:
                break
            if native[t][o:o + 4] != lifted[t][o:o + 4]:
                first[addr] = t
                break
    return {"compared": len(addrs), "differ": len(first), "missing_ticks": missing,
            "first_divergence": {f"{a:#010x}": t for a, t in sorted(first.items())}}


def constant_moved(a: dict[int, bytes], b: dict[int, bytes], lifted: dict[int, bytes],
                   base: int, skip: set[int]) -> list[int]:
    """Words constant and repeatable natively that the lifted run changes."""
    ticks = sorted(a)
    out = []
    size = len(a[ticks[0]])
    for off in range(0, size, PAGE):
        if all(lifted.get(t, b"")[off:off + PAGE] == a[t][off:off + PAGE] for t in ticks):
            continue
        for k in range(0, min(PAGE, size - off) - 3, 4):
            addr = base + off + k
            if addr in skip:
                continue
            vals = {a[t][off + k:off + k + 4] for t in ticks} | \
                {b[t][off + k:off + k + 4] for t in ticks}
            if len(vals) == 1 and any(lifted.get(t, b"")[off + k:off + k + 4] != a[t][off + k:off + k + 4]
                                      for t in ticks):
                out.append(addr)
    return out


def ranges(addrs: list[int]) -> list[list[int]]:
    """Consecutive words merged into [first, last] address ranges."""
    out: list[list[int]] = []
    for x in sorted(addrs):
        if out and x == out[-1][1] + 4:
            out[-1][1] = x
        else:
            out.append([x, x])
    return out


BLOCK = 0x10000
DIGEST_H = ROOT / "harness" / "replay" / "tick_digest.h"


def write_mask(words: list[int], comment: list[str], out: Path) -> dict:
    """A mask header in tick_digest.h's shape (TD_BLOCKS, TD_DATA_END, td_base,
    td_mask), which every reader of a mask takes (tools/s4/wordlog.py mask=)."""
    blocks: dict[int, list[int]] = {}
    for a in words:
        blocks.setdefault(a & ~(BLOCK - 1), []).append(a)
    bases = sorted(blocks)
    lines = ["/* GENERATED by tools/s4/snapshot_fields.py; do not edit.", " *"] + comment + [
        f" * Words: {len(words)}; blocks: {len(bases)}.",
        " */",
        "#ifndef SA2_TICK_DIGEST_H",
        "#define SA2_TICK_DIGEST_H",
        "#include <stdint.h>",
        f"#define TD_BLOCKS {len(bases)}u",
        "#define TD_DATA_END 0x02670598u     /* .dataa's end: 0x00917000 + 0x01D59598 */",
        "static const uint32_t td_base[TD_BLOCKS] = {",
    ]
    lines += [f"    0x{b:08x}u," for b in bases]
    lines += ["};", "static const uint32_t td_mask[TD_BLOCKS][512] = {"]
    for b in bases:
        mask = [0] * 512
        for a in blocks[b]:
            w = (a - b) // 4
            mask[w // 32] |= 1 << (w % 32)
        body = ",".join(f"0x{m:x}u" if m else "0" for m in mask)
        lines.append(f"    {{{body}}},")
    lines += ["};", "#endif", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    where = str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out)
    return {"blocks": len(bases), "words": len(words), "path": where}


def read_mask(header: Path) -> set[int]:
    """Every word a mask header selects (oracle_mode.read_mask_words, the one
    parser: the header reader counts a word-log mask's words with it too)."""
    return _oracle_mode().read_mask_words(header)


def run_lifted(name: str, every: int = 100, limit: int = 1200) -> Path:
    """The boot replay's lifted run with the hook's snapshot mode on, on the
    boot capture's device profile: the snapshots are compared with the boot
    capture's native ones, so the run stands for that capture and reads its
    devices (tools/s4/tick_replay.py native_device_profile). A boot entry that
    records no profile is refused, never run on the default."""
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import tick_replay as T
    boot = [e for e in json.loads(T.REGISTRY.read_text())["replays"] if e["name"] == "boot"]
    if not boot:
        raise SystemExit(f"{T.REGISTRY.relative_to(ROOT)} registers no replay named boot, whose "
                         "capture the snapshots stand for")
    profile = T.native_device_profile(boot[0])
    out = T.OUT / name / "lifted-snapshots.bin"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    run = T.run_lifted(name, {"SA2_REPLAY_SNAPSHOTS": str(out.relative_to(ROOT)),
                              "SA2_REPLAY_SNAP_EVERY": str(every),
                              "SA2_REPLAY_SNAP_LIMIT": str(limit)},
                       device_profile=profile)
    if not out.is_file():
        raise SystemExit(f"the lifted run wrote no snapshots at {out}")
    print(f"lifted run: {run['reach']} -> {out.relative_to(ROOT)}")
    return out


def exclusions(path: Path) -> set[int]:
    """The words an exclusion record drops: each an aligned address inside `.dataa`,
    with the evidence the record gives for it (a pair of plain native repeats whose
    digest of its block disagrees, and the repeats that agree once it is left out).
    A record with no evidence for a word, or a word that is not aligned, is refused:
    the record is an input of the mask, and a word dropped for no recorded reason
    would narrow what the replay compares without saying why."""
    doc = json.loads(path.read_text())
    words = doc.get("words")
    if not isinstance(words, list) or not words:
        raise SystemExit(f"{path}: an exclusion record needs a non-empty 'words' list")
    out = set()
    for w in words:
        addr = (int(w["address"], 16) if isinstance(w, dict) and isinstance(w.get("address"), str)
                and w["address"].startswith("0x") else None)
        if addr is None or addr % 4 or not (w.get("disagree") and w.get("agree_without")):
            raise SystemExit(f"{path}: {w!r} is not an aligned address with 'disagree' and "
                             "'agree_without' evidence")
        out.add(addr)
    return out


def retirements(path: Path, build: str) -> dict[int, dict]:
    """The words an exclusion record retires for `build`, the probe build of a
    mask's runs (oracle_mode.build_digest): address -> the word's `retired`
    entry for that build. A retirement names the build, the runs of it that were
    each seen to take one path through the word (capture directories), and why.
    A retired word is compared in a mask of that build, and the mask tool still
    records every run's path through it and refuses the mask when any of its
    runs, or any run the retirement names, takes a second one (word_log_mask):
    the record's evidence is its own build's, never another's (the U6 masks
    review, W1). A retirement that does not say all three is refused."""
    doc = json.loads(path.read_text())
    out: dict[int, dict] = {}
    for w in doc.get("words") or []:
        got = w.get("retired") if isinstance(w, dict) else None
        if got is None:
            continue
        for e in (got if isinstance(got, list) else [got]):
            if not (isinstance(e, dict) and isinstance(e.get("build"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", e["build"])
                    and isinstance(e.get("runs"), list) and e["runs"]
                    and all(isinstance(x, str) and x for x in e["runs"])
                    and str(e.get("why", "")).strip()):
                raise SystemExit(f"{path}: {w.get('address')}'s retirement {e!r} must name a "
                                 "build (64 hex digits), the runs of it that show one path "
                                 "(runs), and why")
            if e["build"] == build:
                out[int(w["address"], 16)] = e
    return out


def retired_paths(WL, OM, words: dict[int, dict], build: dict,
                  everyone: list) -> tuple[dict, list[str]]:
    """Each retired word's path (word_history) in every run of the mask and in
    every run its retirement names, and the problems: a named run of another
    build or configuration, one that holds no word log, or more than one path
    among them all."""
    report: dict = {}
    problems: list[str] = []
    for w, e in sorted(words.items()):
        paths = {r.name: word_history(WL, r.log, w)["path"] for r in everyone}
        for rel in e["runs"]:
            d = Path(rel) if Path(rel).is_absolute() else ROOT / rel
            name = d.name
            if name in paths:
                continue
            logs = [d / n for n in OM.WORD_LOGS if (d / n).is_file()]
            if len(logs) != 1:
                problems.append(f"{w:#010x}'s retirement names {rel}, which holds "
                                f"{len(logs)} word logs, not one")
                continue
            try:
                diff = OM.first_difference(build, OM.build_of(d))
            except SystemExit as err:
                diff = f"its build cannot be read ({err})"
            if diff:
                problems.append(f"{w:#010x}'s retirement names {rel}, which is not the mask's "
                                f"build and configuration: {diff}")
                continue
            paths[name] = word_history(WL, logs[0], w)["path"]
        seen = sorted(set(paths.values()))
        report[f"{w:#010x}"] = {"build": e["build"], "why": e["why"],
                                "runs_named": len(e["runs"]), "runs_read": len(paths),
                                "paths": seen, "path_by_run": paths}
        if len(seen) > 1:
            problems.append(f"{w:#010x} is retired for build {e['build'][:12]} on the evidence "
                            f"that its runs take one path through it, and these take "
                            f"{len(seen)}: "
                            + ", ".join(f"{k} {v}" for k, v in sorted(paths.items())))
    return report, problems


# ------------------------------------------------------------------ word logs
#
# The strict mask (`--word-logs`): chosen from the word logs of runs configured
# exactly like the comparison runs, since the tick probe every native run loads
# keeps the log (harness/replay/word_log_core.h), and held to a capture no
# source contributed to. The rule, in the order it is applied:
#
#   varying      the word takes more than one value over ticks 1..N in a source
#   stable       every source holds the same value at every tick 1..N
#   not pointer  not a word every source's own maps flag: in each source some
#                value it takes at some tick (the keyframe too) falls in a
#                region that run's maps list as committed, the probe's own
#                blocks (SA2O) and the executable's image (IMAGE) left out
#                (tools/s4/wordlog.py pointer_words, `cut`)
#   not identity not a word the comparer names by what it identifies: the heap
#                pointers and CRI's file-table handle words
#                (harness/replay/masks/identity-words.json); the entries'
#                in-use words are the game's own flags and stay compared
#   not excluded not in the exclusion record (plain repeats disagree on it)
#
# Two decisions in the pointer test, recorded in every mask receipt
# ("pointer_rule") with the words each drops (the mask-rule review, W3):
#   * the keyframe (tick 0) counts, though varying and stable read ticks 1..N:
#     the reader's pointers mode has no first tick (a request to U1), so a word
#     whose only address-like value is its Init value is dropped too;
#   * a value inside the executable's own image (IMAGE) does not count as an
#     address (since 2026-09-27; the G4 interrogation, Reviewer B's finding 1).
#     Those are fixed addresses the host does not choose, and the lifted build
#     keeps the guest image where the native loader put it, so a word the
#     sources hold alike is compared whether its values are data that happen
#     to fall in the image's range or the image's own addresses. Until then
#     the test counted them as addresses, a rule this comment called
#     unmeasured ("one lifted replay would settle it"). One settled it: every
#     source flagged Sofdec's timer unit 0x0176a608 only because its value,
#     10,000,000 (Wine's QueryPerformanceFrequency), lies in the image's range,
#     so the lifted boot's 3,579,545 there was compared by nothing; and Metal
#     Harbor's goal latch 0x0174b000 and its packed-flag neighbours left that
#     mask the same way. A word whose values fall both in the image and in
#     memory the host chose is still dropped when every source flags it for
#     the latter; the receipt counts the words the image alone flagged
#     (`pointer_rule`), and among them those some sources flag for a value
#     outside the image too.
#   * a word is dropped for every tick, though it may hold data on the ticks
#     its flagged value is not there: the receipt counts the dropped words any
#     source holds a value in that is neither zero nor an address by the same
#     test (`stable_words_dropped_holding_data_at_some_tick`; the review of the
#     G4 round-one fold, Reviewer B's finding 1). Judging such a word per tick
#     is carried forward (Documentation/S4_CARRY_FORWARD.md, section 7).
#
# and refused unless there are at least MIN_SOURCES sources and one held-out
# capture, each a different run (its own directory, log hash, receipt name and
# creation time, and clock reading at Init), all one probe build and
# configuration (oracle_mode.require_same_build),
# every log whole and planned (wordlog.check_capture) with both region maps whole
# and the probe's own blocks named, every run started from the pinned saves, one
# source at least COLD_SECONDS idle before its run, and the held-out capture equal
# to the sources on every selected word at every tick.
#
# The pointer test reads each source's maps at Init and at the last tick, and
# drops a word only on evidence every source agrees with: every source's maps
# flag it (the U6 masks review, W2). Until then the flags were united over the
# sources, so a stable word counted as an address when one run's maps happened
# to hold its value: the boot mask chosen from eight runs dropped 3,284 of
# tick_digest.h's words that way where three runs had dropped 387, all of them
# data the lifted build holds alike. A word only some sources flag is compared;
# if it names memory after all, it shows as a divergence on the lifted side,
# never as a silent pass. A region committed and released between the two maps
# is not seen by either, so a word pointing only into such a region reads as
# data too, with the same consequence.
#
# The per-replay masks live in harness/replay/masks/<replay>.h (`mask_header`),
# in tick_digest.h's shape, each with its receipt
# (Documentation/receipts/s4-mask-<replay>-v1.json, `mask_receipt`); `--replay`
# names both.

MASKS = ROOT / "harness" / "replay" / "masks"
IDENTITY = MASKS / "identity-words.json"
IDENTITY_SCHEMA = "sa2-identity-words/v1"
def _oracle_mode():
    p = str(ROOT / "tools" / "oracle")
    if p not in sys.path:
        sys.path.insert(0, p)
    import oracle_mode
    return oracle_mode


MIN_SOURCES = _oracle_mode().MIN_MASK_SOURCES      # one constant each, the header reader's
COLD_SECONDS = _oracle_mode().COLD_SECONDS
IDLE = "idle_before_run_seconds"     # the key a source's idle time is reported under
SECTION = (0x00917000, 0x00917000 + 0x01D59598)
IMAGE = (0x00400000, 0x02775000)     # sonic2app.exe: ImageBase to ImageBase + SizeOfImage
# The reason a re-emitted mask gives a word the image rule of 2026-09-27 added: every
# source's maps flag it, and not every source for a value outside the image, so at
# least one flags it for an image value alone (most for image values alone; the
# receipt's pointer_rule counts the rest).
IMAGE_RULE_ADDED = ("flagged by every source, by at least one for image values alone, "
                    "compared since 2026-09-27")
CURRENT_MASKS = {"boot": ROOT / "harness" / "replay" / "tick_digest.h"}   # before masks/
NAMED = ("sa2-tick-named.csv", "sa2-tick-capture.csv")     # the game's row, first found


def mask_header(replay: str) -> Path:
    """Where a replay's digest mask lives: harness/replay/masks/<replay>.h."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", replay):
        raise SystemExit(f"{replay!r} is not a replay name (lower case, digits and hyphens)")
    return MASKS / f"{replay}.h"


def mask_receipt(replay: str) -> Path:
    """The receipt of a replay's mask: what it was chosen from and how it held."""
    mask_header(replay)
    return ROOT / "Documentation" / "receipts" / f"s4-mask-{replay}-v1.json"


def _modules():
    for d in ("s4", "oracle"):
        p = str(ROOT / "tools" / d)
        if p not in sys.path:
            sys.path.insert(0, p)
    import oracle_mode as OM
    import save_state as SS
    import wordlog as WL
    return WL, OM, SS


def _hex(v: object, what: str) -> int:
    if not (isinstance(v, str) and v.startswith("0x")):
        raise SystemExit(f"{what}: {v!r} is not a 0x hex string")
    return int(v, 16)


def identity_words(path: Path) -> dict[int, str]:
    """address -> what it is, for every word the comparer compares by what it
    identifies rather than by its bits (the user's ruling of 2026-09-24,
    decision 1 and 2): single words ("words") and tables of them ("tables":
    base, stride, count and the offsets in each entry). Each word needs a kind
    and its evidence; a word outside `.dataa`, unaligned or named twice is
    refused, since the record narrows what the mask compares."""
    doc = json.loads(path.read_text())
    if doc.get("schema") != IDENTITY_SCHEMA:
        raise SystemExit(f"{path}: not an identity record ({IDENTITY_SCHEMA})")
    out: dict[int, str] = {}

    def add(addr: int, what: str) -> None:
        if addr % 4 or not SECTION[0] <= addr < SECTION[1]:
            raise SystemExit(f"{path}: {addr:#010x} ({what}) is not an aligned word of .dataa")
        if addr in out:
            raise SystemExit(f"{path}: {addr:#010x} is named twice ({out[addr]}; {what})")
        out[addr] = what
    for w in doc.get("words") or []:
        if not (isinstance(w, dict) and w.get("kind") and w.get("evidence")):
            raise SystemExit(f"{path}: {w!r} needs an address, a kind and its evidence")
        add(_hex(w.get("address"), str(path)), f"{w['kind']}: {w.get('name', 'word')}")
    for t in doc.get("tables") or []:
        if not (isinstance(t, dict) and t.get("name") and t.get("evidence")
                and isinstance(t.get("offsets"), dict) and t["offsets"]
                and isinstance(t.get("count"), int) and t["count"] > 0):
            raise SystemExit(f"{path}: table {t!r} needs a name, base, stride, count, offsets "
                             "and its evidence")
        base, stride = _hex(t.get("base"), str(path)), _hex(t.get("stride"), str(path))
        offs = {_hex(o, str(path)): kind for o, kind in t["offsets"].items()}
        if any(o >= stride for o in offs):
            raise SystemExit(f"{path}: {t['name']} has an offset past its stride")
        for k in range(t["count"]):
            for o, kind in sorted(offs.items()):
                add(base + k * stride + o, f"{kind}: {t['name']} entry {k} +{o:#x}")
    if not out:
        raise SystemExit(f"{path}: names no word")
    return out


def _cite(path: Path) -> str:
    shown = path.resolve().relative_to(ROOT) if path.resolve().is_relative_to(ROOT) else path
    return str(shown)


class Run:
    """One capture's word log and what its receipt says about the run."""

    def __init__(self, arg: Path, WL, OM, SS):
        arg = Path(arg)
        self.capture = arg.parent if arg.is_file() else arg
        self.name = self.capture.name
        self.problems: list[str] = []
        found = ([arg] if arg.is_file() else
                 [self.capture / n for n in OM.WORD_LOGS if (self.capture / n).is_file()])
        if len(found) != 1:
            raise SystemExit(f"{self.name}: holds {len(found)} word logs "
                             f"({', '.join(OM.WORD_LOGS)}), not one")
        self.log = found[0]
        if self.log.name not in OM.WORD_LOGS:
            self.problems.append(f"{self.log.name} is not the tick probe's word log "
                                 f"({' or '.join(OM.WORD_LOGS)})")
        self.sha256 = WL.sha256(self.log)
        self.receipt = {}
        try:
            self.receipt = json.loads((self.capture / "receipt.json").read_text())
        except (OSError, ValueError) as e:
            self.problems.append(f"no readable receipt.json: {e}")
        if self.receipt and self.receipt.get("name") != self.name:
            self.problems.append(f"its receipt names the run {self.receipt.get('name')!r}; a "
                                 "renamed copy of a capture is not another run")
        self.created = self.receipt.get("created_utc")
        art = next((a for a in self.receipt.get("artifacts") or []
                    if a.get("file") == self.log.name), None)
        if ((art or {}).get("check") or {}).get("sha256") != self.sha256:
            self.problems.append(f"its receipt does not record {self.log.name} at "
                                 f"{self.sha256[:12]}, so nothing shows the runner collected it")
        self.info = WL.info(self.log)
        i = self.info
        # The clock the probe read at Init: two files of one run (a .bin and its .zst,
        # whose hashes differ) share it, and two runs do not.
        self.clock = (i.get("ref_init"), i.get("clk_init"))
        self.ticks = i["last_tick"] if i["whole"] else 0
        # The capture check against the game's own row: the log whole, planned and
        # native, and the named fields equal to it on every tick. The digest columns
        # are left out, since they follow whichever mask built them.
        named = next((self.capture / n for n in NAMED if (self.capture / n).is_file()), None)
        if named is None:
            self.problems.append(f"no {' or '.join(NAMED)} to hold the log to")
        else:
            row = next((a for a in self.receipt.get("artifacts") or []
                        if a.get("file") == named.name), None)
            if ((row or {}).get("check") or {}).get("sha256") != WL.sha256(named):
                self.problems.append(f"its receipt does not record {named.name} at "
                                     f"{WL.sha256(named)[:12]}, so the row the log is held to "
                                     "may be another run's (the mask evidence review, N6)")
            got = WL.check_capture(self._named_only(named, WL), self.log)
            self.problems += [f"capture check: {p}" for p in got["problems"]]
        if i.get("own_blocks") != 2:
            self.problems.append(f"the log names {i.get('own_blocks')} blocks of the probe's own "
                                 "(SA2O), not its shadow and buffer, so its maps' pointer test "
                                 "would count the probe's memory as the game's (the reader "
                                 "review, N3)")
        if not (i.get("regions_init") and i.get("regions_end")) or \
                i.get("regions_init_more") or i.get("regions_end_more"):
            self.problems.append("the log's region maps are missing or cut short, so pointers "
                                 "cannot be told apart")
        self.problems += SS.native_start_problems(self.capture)
        self.idle = OM.idle_seconds(self.receipt)
        self.maps = bool(i.get("regions_init") and i.get("regions_end"))

    def _named_only(self, named: Path, WL) -> Path:
        """The capture's CSV with the tick and the named fields alone."""
        header, rows = WL.read_capture(named)
        keep = 1 + len(WL.named_fields())
        WL.BUILD.mkdir(parents=True, exist_ok=True)
        out = WL.BUILD / f"named-{self.sha256[:16]}.csv"
        lines = [",".join(header[:keep])] + [",".join([str(t)] + rows[t][:keep - 1])
                                               for t in sorted(rows)]
        out.write_text("\n".join(lines) + "\n")
        return out

    def describe(self) -> dict:
        return {"capture": _cite(self.capture), "log": _cite(self.log), "sha256": self.sha256,
                "ticks": self.ticks, IDLE: self.idle,
                "cold": self.idle is not None and self.idle >= COLD_SECONDS,
                "load_average": (self.receipt.get("session") or {}).get("load_average"),
                "problems": self.problems}


class Observed:
    """What the logs show, each piece read once: every run's varying words and
    pointer words, and every pair's differing words."""

    def __init__(self, WL, runs: list[Run]):
        self.WL, self.runs = WL, {r.name: r for r in runs}
        self._varying: dict[str, set[int]] = {}
        self._pointers: dict[str, dict[int, tuple[int, int]]] = {}
        self._host: dict[str, dict[int, tuple[int, int]]] = {}
        self._data: dict[str, dict[int, int]] = {}
        self._pairs: dict[tuple[str, str], dict[int, dict]] = {}

    def varying(self, name: str) -> set[int]:
        """Words that take more than one value over ticks 1..N: a word changed on
        two ticks or more changed on some tick after the first, and one changed
        on one tick varies unless that tick was 1 (then tick 1 already holds its
        last value)."""
        if name not in self._varying:
            r = self.runs[name]
            tc = self.WL.touched(r.log)
            out = {va for va, n in tc.items() if n >= 2}
            once = [va for va, n in tc.items() if n == 1]
            if once:
                first = array("I", self.WL.state(r.log, 1))
                last = array("I", self.WL.state(r.log, r.ticks))
                out |= {va for va in once if first[(va - SECTION[0]) >> 2]
                        != last[(va - SECTION[0]) >> 2]}
            self._varying[name] = out
        return self._varying[name]

    def pointers(self, name: str) -> dict[int, tuple[int, int]]:
        """Every word the run's maps flag for some value, the image's included."""
        if name not in self._pointers:
            r = self.runs[name]
            self._pointers[name] = self.WL.pointer_words(r.log) if r.maps else {}
        return self._pointers[name]

    def _host_scan(self, name: str) -> None:
        if name not in self._host:
            r = self.runs[name]
            scan = self.WL.pointer_scan(r.log, cut=IMAGE) if r.maps else {}
            self._host[name] = {va: (t, v) for va, (t, v, _d) in scan.items()}
            self._data[name] = {va: d for va, (_t, _v, d) in scan.items() if d is not None}

    def host_pointers(self, name: str) -> dict[int, tuple[int, int]]:
        """Every word the run's maps flag for a value outside the executable's
        image (IMAGE): an address the host chose, which the pointer test drops on."""
        self._host_scan(name)
        return self._host[name]

    def host_data(self, name: str) -> dict[int, int]:
        """The words host_pointers flags that hold, at some tick (0: the
        keyframe), a value neither zero nor an address the host chose -> that
        first tick: data the pointer test drops with the word (the review of the
        G4 round-one fold, Reviewer B's finding 1). Read in the same pass."""
        self._host_scan(name)
        return self._data[name]

    def differ(self, a: str, b: str) -> dict[int, dict]:
        """Every word runs `a` and `b` hold differently at some tick from 1, with
        `a`'s and `b`'s values at the first such tick (wordlog.diff)."""
        key = tuple(sorted((a, b)))
        if key not in self._pairs:
            ra, rb = self.runs[key[0]], self.runs[key[1]]
            rows, meta = self.WL.diff(ra.log, rb.log)
            if meta["compared_through"] != min(ra.ticks, rb.ticks) or ra.ticks != rb.ticks:
                raise SystemExit(f"{ra.name} and {rb.name} were compared through tick "
                                 f"{meta['compared_through']} of {ra.ticks} and {rb.ticks}")
            self._pairs[key] = {row["va"]: row for row in rows}
        rows = self._pairs[key]
        if key == (a, b):
            return rows
        return {va: dict(row, a=row["b"], b=row["a"]) for va, row in rows.items()}


def select(obs: Observed, sources: list[str], identity: dict[int, str],
           excluded: set[int]) -> dict:
    """The rule (the section comment above) over `sources`, with every word it
    drops kept under the reason it was dropped for."""
    varying: set[int] = set()
    for s in sources:
        varying |= obs.varying(s)
    unstable: dict[int, dict] = {}
    for other in sources[1:]:
        for va, row in obs.differ(sources[0], other).items():
            if va in varying:           # a word no source moves is not unstable, only apart
                unstable.setdefault(va, dict(row, run=other))
    flagged: dict[int, list[str]] = {}       # word -> the sources whose maps flag it
    host: dict[int, list[str]] = {}          # the same, for a value outside the image
    first_host: dict[int, tuple] = {}
    for s in sources:
        for va in obs.pointers(s):
            flagged.setdefault(va, []).append(s)
        for va, (t, v) in obs.host_pointers(s).items():
            host.setdefault(va, []).append(s)
            first_host.setdefault(va, (t, v, s))
    # Dropped as pointer-like only when every source's maps flag it for a value
    # outside the executable's image (the section comment: evidence every source
    # agrees with, and an image value is no address the host chose).
    pointer = {va: first_host[va] for va, by in host.items() if len(by) == len(sources)}
    stable = varying - set(unstable)
    candidates = stable - set(pointer)
    dropped_pointer = stable & set(pointer)
    kept_flagged = {va for va in stable if va in flagged and va not in pointer}
    # Every source's maps flag it, and only the image's range made some of them:
    # the words the pointer test dropped until 2026-09-27 and compares now.
    image_alone = {va for va in stable - set(pointer)
                   if len(flagged.get(va, ())) == len(sources)}
    # Of those, the ones some sources (not every one) flag for a value outside the
    # image too: compared under the "every source" rule, not for image values alone
    # (the review of the G4 round-one fold, Reviewer A's finding 2).
    image_some_host = {va for va in image_alone if va in host}
    # Dropped, yet some source holds data in it at some tick: the drop leaves those
    # ticks compared by nothing, since a value in committed memory is not always an
    # address (the same review, Reviewer B's finding 1; a per-tick rule is carried
    # forward, Documentation/S4_CARRY_FORWARD.md section 7).
    holding = [set(obs.host_data(s)) for s in sources]
    holding_data = {va for va in dropped_pointer if any(va in h for h in holding)}
    by_count: dict[int, int] = {}
    for va in stable & set(flagged):
        by_count[len(flagged[va])] = by_count.get(len(flagged[va]), 0) + 1
    ident = candidates & set(identity)
    dropped_excluded = (candidates - ident) & excluded
    mask = candidates - ident - excluded
    exclusion = {}
    for w in sorted(excluded):
        exclusion[f"{w:#010x}"] = ("dropped by the record" if w in dropped_excluded else
                                   "unstable among the sources" if w in unstable else
                                   "pointer-like" if w in varying and w in pointer else
                                   "an identity word" if w in ident else
                                   "does not vary in any source")
    return {"mask": mask, "varying": varying, "unstable": unstable, "pointer": pointer,
            "identity": ident, "excluded": dropped_excluded, "exclusion": exclusion,
            "image_alone": image_alone,
            "pointer_rule": {
                "small": f"{SMALL:#x}", "ticks": "0 (the keyframe) to N",
                "image": [f"{IMAGE[0]:#010x}", f"{IMAGE[1]:#010x}"],
                "image_values_count_as_pointers": False,
                "dropped_when_flagged_by": "every source, for a value outside the image",
                "sources": len(sources),
                "stable_words_by_sources_flagging": {str(k): by_count[k]
                                                     for k in sorted(by_count)},
                "stable_words_flagged_by_some_sources_compared": len(kept_flagged),
                "stable_words_flagged_by_every_source_for_image_values_alone_compared":
                    len(image_alone),
                "of_them_flagged_by_some_sources_for_a_host_value_too": len(image_some_host),
                "stable_words_dropped": len(dropped_pointer),
                "stable_words_dropped_holding_data_at_some_tick": len(holding_data),
                "dropped_first_at_the_keyframe": sum(
                    1 for w in dropped_pointer if pointer[w][0] == 0)}}


def held_out_words(obs: Observed, sources: list[str], held: str, mask: set[int]) -> list[dict]:
    """The selected words on which the held-out run differs from the sources at
    some tick; [] is the mask reproducing."""
    return sorted((dict(row, va=f"{va:#010x}") for va, row in obs.differ(sources[0], held).items()
                   if va in mask), key=lambda r: (r["first_tick"], r["va"]))


def digest_agreement(WL, header: Path, runs: list[Run]) -> dict:
    """The digest columns the mask gives each run, from its log, compared tick by
    tick with the first run's: the form a replay compares (`wordlog.capture_csv`)."""
    tables = {r.name: WL.read_capture_text(WL.capture_csv(r.log, header, native=True)) for r in runs}
    head, ref = tables[runs[0].name]
    cols = [k for k, c in enumerate(head[1:]) if c.startswith("d_")]
    out = {}
    for r in runs[1:]:
        _, got = tables[r.name]
        bad = [(t, head[1 + k]) for t in sorted(ref) for k in cols
               if got.get(t, [None] * len(head))[k] != ref[t][k]]
        out[r.name] = {"cells": len(ref) * len(cols), "differing": len(bad),
                       "first": list(bad[0]) if bad else None}
    return out


def word_history(WL, log: Path, va: int, limit: int = 12) -> dict:
    """One word's values over the run: its value at tick 1 and every change after,
    the first `limit` of them, and a hash of the whole sequence, which names the
    path the run took through it."""
    ch = [(t, new) for t, _, _, new in WL.changes(log, va, va + 4)]
    start = 0
    for t, new in ch:
        if t <= 1:
            start = new
    later = [(t, new) for t, new in ch if t > 1]
    path = hashlib.sha256(json.dumps([start] + later).encode()).hexdigest()[:16]
    return {"at_tick_1": f"{start:#x}", "changes": len(later),
            "first_changes": [[t, f"{v:#x}"] for t, v in later[:limit]], "path": path}


def compare_masks(obs: Observed, sources: list[str], sel: dict, old: set[int],
                  identity: dict[int, str], sampled: list[int]) -> dict:
    """The new mask against an older header's: each word added and dropped, with
    the reason from the logs."""
    new = sel["mask"]
    added, dropped = sorted(new - old), sorted(old - new)
    reasons: dict[str, list[int]] = {}
    for w in dropped:
        # The first step of the rule that drops it, in the rule's order.
        why = ("does not vary over the logs' ticks" if w not in sel["varying"] else
               "unstable among the sources" if w in sel["unstable"] else
               "pointer-like in every source's maps" if w in sel["pointer"] else
               "an identity word" if w in sel["identity"] else
               "excluded by the record" if w in sel["excluded"] else
               "not explained (a defect of this tool)")
        reasons.setdefault(why, []).append(w)
    added_why: dict[str, list[int]] = {}
    image_alone = sel.get("image_alone", set())
    for w in (w for w in added if w in image_alone):
        added_why.setdefault(IMAGE_RULE_ADDED, []).append(w)
    rest = [w for w in added if w not in image_alone]
    if rest and sampled:
        r = obs.runs[sources[0]]
        states = [array("I", obs.WL.state(r.log, t)) for t in sampled if t <= r.ticks]
        for w in rest:
            k = (w - SECTION[0]) >> 2
            vals = {s[k] for s in states}
            added_why.setdefault("varies at the old sampled ticks too" if len(vals) > 1 else
                                 "varies only between the old sampled ticks", []).append(w)

    def blocks(ws: list[int]) -> dict[str, int]:
        out: dict[str, int] = {}
        for w in ws:
            b = f"{w & ~(BLOCK - 1):#010x}"
            out[b] = out.get(b, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1])[:12])
    return {"old_words": len(old), "new_words": len(new), "common": len(new & old),
            "added": len(added), "dropped": len(dropped),
            "dropped_by_reason": {k: {"words": len(v), "top_blocks": blocks(v),
                                      "examples": [f"{x:#010x}" for x in v[:8]]}
                                  for k, v in sorted(reasons.items())},
            "added_by_reason": {k: {"words": len(v), "top_blocks": blocks(v),
                                    "examples": [f"{x:#010x}" for x in v[:8]]}
                                for k, v in sorted(added_why.items())},
            "added_ranges": [[f"{x:#010x}", f"{y:#010x}"] for x, y in ranges(added)][:200],
            "dropped_ranges": [[f"{x:#010x}", f"{y:#010x}"] for x, y in ranges(dropped)][:200]}


def identity_report(obs: Observed, runs: list[str], sel: dict, identity: dict[int, str],
                    old: set[int] | None) -> list[dict]:
    """Every identity word that varies in some run: whether the runs agree on
    it, whether the rule would have selected it but for the identity record,
    whether the older mask held it, and its last value in each run."""
    out = []
    moving = sorted(w for w in identity if any(w in obs.varying(r) for r in runs))
    for w in moving:
        lasts = {}
        for r in runs:
            h = word_history(obs.WL, obs.runs[r].log, w, limit=0)
            lasts[r] = {"at_tick_1": h["at_tick_1"], "changes": h["changes"], "path": h["path"]}
        out.append({"va": f"{w:#010x}", "what": identity[w],
                    "stable_among_sources": w not in sel["unstable"],
                    "pointer_like": w in sel["pointer"],
                    "rule_would_select_it": w in sel["identity"],
                    "in_old_mask": (w in old) if old is not None else None,
                    "runs": lasts})
    return out


def word_log_mask(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="snapshot_fields.py --word-logs",
        description="A strict digest mask from the word logs of runs configured like the "
                    "comparison runs; refused unless every source and the held-out capture "
                    "follow the rule (module comment, 'word logs').")
    ap.add_argument("--word-logs", type=Path, nargs="+", required=True, metavar="CAPTURE",
                    help=f"the source runs' capture directories (or their logs), at least "
                         f"{MIN_SOURCES}")
    ap.add_argument("--held-out", type=Path, metavar="CAPTURE",
                    help="a capture of the same build and configuration that no source is: it "
                         "must equal the sources on every selected word at every tick")
    ap.add_argument("--exclude-words", type=Path,
                    help="an exclusion record (harness/replay/tick_digest_exclusions.json)")
    ap.add_argument("--identity-words", type=Path, default=IDENTITY,
                    help=f"the identity record (default {IDENTITY.relative_to(ROOT)})")
    ap.add_argument("--replay", metavar="NAME",
                    help="the replay the mask is for: --emit and --receipt default to "
                         "mask_header(NAME) and mask_receipt(NAME)")
    ap.add_argument("--emit", type=Path, metavar="HEADER",
                    help="write the mask header (harness/replay/masks/<replay>.h); refused "
                         "while anything is refused")
    ap.add_argument("--receipt", type=Path, help="write the mask's receipt (JSON) here")
    ap.add_argument("--compare", type=Path, metavar="HEADER",
                    help="an older mask header to compare the selection with, word by word")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what the rule selects and every refusal; write no header")
    ap.add_argument("--leave-one-out", action="store_true",
                    help="with --dry-run, also choose from each set of all sources but one and "
                         "hold that one out")
    ap.add_argument("--out", type=Path, help="a directory for the report (JSON)")
    a = ap.parse_args(argv)
    if a.replay and not a.dry_run:
        a.emit = a.emit or mask_header(a.replay)
        a.receipt = a.receipt or mask_receipt(a.replay)
    if a.emit and a.dry_run:
        ap.error("--dry-run writes no header")
    if a.emit and not a.receipt:
        ap.error("--emit needs --receipt: a mask without its receipt names no run's idle time")
    if a.identity_words.resolve() != IDENTITY.resolve() and not a.dry_run:
        ap.error(f"--identity-words other than {IDENTITY.relative_to(ROOT)} is for --dry-run: the "
                 "comparer reads that record, so a mask cut with another would leave out words "
                 "it does not compare by identity (the mask-rule review, N5)")
    WL, OM, SS = _modules()
    runs = [Run(p, WL, OM, SS) for p in a.word_logs]
    held = Run(a.held_out, WL, OM, SS) if a.held_out else None
    everyone = runs + ([held] if held else [])
    problems: list[str] = []
    names = [r.name for r in everyone]
    if len(set(names)) != len(names):
        problems.append(f"a capture is named twice: {names}")
    # One run counts once (the mask reviews' C1/W1): a capture copied or linked under
    # another name keeps its log's bytes, its receipt's creation time and the clock
    # its probe read at Init; a .bin and its .zst keep the last two.
    for what, key in (("directory", lambda r: r.capture.resolve()),
                      ("log sha256", lambda r: r.sha256),
                      ("receipt created_utc", lambda r: r.created),
                      ("clock at Init (ref_init, clk_init)",
                       lambda r: None if None in r.clock else r.clock)):
        seen: dict = {}
        for r in everyone:
            k = key(r)
            if k is None:
                problems.append(f"{r.name}: records no {what}, so it cannot be shown another run")
            elif k in seen:
                problems.append(f"{seen[k]} and {r.name} share a {what}: they are one run, "
                                "which would count twice")
            else:
                seen[k] = r.name
    if a.emit and any(a.emit.resolve() == m.resolve() for m in CURRENT_MASKS.values()):
        problems.append(f"{_cite(a.emit)} is a mask chosen before word logs; a word-log mask goes "
                        "to harness/replay/masks/<replay>.h (--replay NAME), where its exclusion "
                        "record and comparison are required (the U4 fold review, W1)")
    replay = a.replay or (a.emit.stem if a.emit and a.emit.resolve().parent == MASKS.resolve()
                          else None)
    need = OM.EXCLUSIONS_REQUIRED.get(replay)
    if need and (a.exclude_words is None or a.exclude_words.resolve() != (ROOT / need).resolve()):
        problems.append(f"the {replay} mask is chosen with --exclude-words {need}, so its receipt "
                        "names each run's path through the words plain repeats disagree on (the "
                        "mask-rule review, W4)")
    current = CURRENT_MASKS.get(replay)
    if replay and mask_header(replay).is_file():
        current = mask_header(replay)
    if current is not None and current.is_file() and not a.dry_run and (
            a.compare is None or a.compare.resolve() != current.resolve()):
        problems.append(f"the {replay} replay has a mask already ({_cite(current)}): pass "
                        "--compare with it, so the receipt records every word the new mask stops "
                        "comparing (the mask evidence review, W5)")
    if len(runs) < MIN_SOURCES:
        problems.append(f"{len(runs)} source runs; a mask needs at least {MIN_SOURCES}, so that "
                        "no two runs that happen to agree choose it")
    if held is None:
        problems.append("no held-out capture, so nothing shows the mask repeats on a run it "
                        "was not chosen from")
    for r in everyone:
        problems += [f"{r.name}: {p}" for p in r.problems]
    if not any(r.idle is not None and r.idle >= COLD_SECONDS for r in runs):
        problems.append(f"no source run's receipt records {COLD_SECONDS} s or more idle before "
                        f"it (session.idle_before_run.seconds: "
                        f"{', '.join(f'{r.name} {r.idle}' for r in runs)}): a run after a "
                        "pause can take a path warm runs do not, as the boot's tick-369 second "
                        "path did (agents-journals/s4/2026-09-23-recording-session.md)")
    ticks = {r.name: r.ticks for r in everyone}
    if len(set(ticks.values())) != 1 or not all(ticks.values()):
        problems.append(f"the runs do not hold one whole run of ticks: {ticks}")
    builds = []
    try:
        builds = [OM.build_of(r.capture) for r in everyone]
        for r, b in zip(everyone[1:], builds[1:]):
            d = OM.first_difference(builds[0], b)
            if d:
                problems.append(f"{r.name} is another probe build or configuration than "
                                f"{everyone[0].name}: {d}")
    except SystemExit as e:
        problems.append(f"the build cannot be read: {e}")
    identity = identity_words(a.identity_words)
    recorded = exclusions(a.exclude_words) if a.exclude_words else set()
    # A word the record retires for these runs' build is compared (`retirements`).
    retired = (retirements(a.exclude_words, OM.build_digest(builds[0]))
               if a.exclude_words and builds else {})
    excluded = recorded - set(retired)
    if len(set(ticks.values())) != 1:
        raise SystemExit("REFUSED: " + "; ".join(problems))
    obs = Observed(WL, everyone)
    src = [r.name for r in runs]
    sel = select(obs, src, identity, excluded)
    idle_words = [w for w, why in sel["exclusion"].items() if why == "does not vary in any source"]
    if idle_words:
        problems.append(f"{a.exclude_words}: {', '.join(idle_words)} vary in no source, so "
                        "excluding them drops nothing; the record names only words the runs move")
    if not sel["mask"]:
        problems.append("the rule selects no word, and an empty mask compares nothing")
    report: dict = {
        "schema": "sa2-digest-mask/v1", "method": "word logs",
        "command": ["tools/s4/snapshot_fields.py"] + argv,
        "rule": {"min_sources": MIN_SOURCES, "cold_seconds": COLD_SECONDS,
                 "ticks": [1, max(ticks.values())]},
        "sources": [r.describe() for r in runs],
        "held_out": held.describe() if held else None,
        "build": OM.build_digest(builds[0]) if builds else None,
        "records": {"identity words": {"path": _cite(a.identity_words),
                                       "sha256": WL.sha256(a.identity_words)},
                    **({"excluded words": {"path": _cite(a.exclude_words),
                                           "sha256": WL.sha256(a.exclude_words)}}
                       if a.exclude_words else {})},
        "counts": {"varying": len(sel["varying"]), "unstable": len(sel["unstable"]),
                   "pointer_like": len(set(sel["pointer"]) & sel["varying"]),
                   "identity": len(sel["identity"]), "excluded": len(sel["excluded"]),
                   "selected": len(sel["mask"]),
                   "blocks": len({w & ~(BLOCK - 1) for w in sel["mask"]})},
        "per_run": {r.name: {"varying": len(obs.varying(r.name)),
                             "pointer_words": len(obs.pointers(r.name))} for r in everyone},
        "exclusion_record": dict(sel["exclusion"], **{
            f"{w:#010x}": "retired for this build: compared" for w in sorted(retired)}),
        "pointer_rule": sel["pointer_rule"],
        # Every pair's differing words, all and varying (the mask-rule review, N6): the
        # held-out check cannot see a wrong source, which only removes words.
        "pair_differences": {
            f"{x.name} ~ {y.name}": {"words": len(d), "varying_words": len(set(d) & sel["varying"])}
            for i, x in enumerate(everyone) for y in everyone[i + 1:]
            for d in [obs.differ(x.name, y.name)]},
    }
    if held:
        bad = held_out_words(obs, src, held.name, sel["mask"])
        report["held_out_check"] = {"differing_selected_words": len(bad), "words": bad[:40]}
        if bad:
            problems.append(f"the held-out capture {held.name} differs from the sources on "
                            f"{len(bad)} selected words, first {bad[0]['va']} at tick "
                            f"{bad[0]['first_tick']}: the mask does not repeat")
    if a.exclude_words:
        report["exclusion_words_by_run"] = {
            f"{w:#010x}": {r.name: word_history(WL, r.log, w) for r in everyone}
            for w in sorted(recorded)}
        # Whether these runs took more than one path through an excluded word: when
        # every run takes one, the record's reason was not shown again here (the mask
        # evidence review, N4), and the receipt says so.
        report["exclusion_exercised"] = any(
            len({h["path"] for h in by_run.values()}) > 1
            for by_run in report["exclusion_words_by_run"].values())
        if retired:
            report["exclusion_retired"], why = retired_paths(WL, OM, retired, builds[0],
                                                             everyone)
            problems += [f"{a.exclude_words}: {p}" for p in why]
    old = read_mask(a.compare) if a.compare else None
    if old is not None:
        m = re.search(r"Sampled ticks: ([0-9, ]+)\.", a.compare.read_text())
        sampled = [int(x) for x in m.group(1).split(",")] if m else []
        report["compared_with"] = dict(compare_masks(obs, src, sel, old, identity, sampled),
                                       header=_cite(a.compare), sampled_ticks=sampled)
        unexplained = report["compared_with"]["dropped_by_reason"].get(
            "not explained (a defect of this tool)")
        if unexplained:
            problems.append(f"{unexplained['words']} words {_cite(a.compare)} compares leave the "
                            f"mask for no reason the rule gives, first {unexplained['examples']}")
    report["identity_words"] = identity_report(obs, [r.name for r in everyone], sel, identity,
                                               old)
    if a.dry_run and a.leave_one_out and len(runs) >= 2:
        loo = {}
        for r in runs:
            rest = [s for s in src if s != r.name]
            s2 = select(obs, rest, identity, excluded)
            bad = held_out_words(obs, rest, r.name, s2["mask"])
            loo[r.name] = {"chosen_from": rest, "selected": len(s2["mask"]),
                           "held_out_differs_on": len(bad), "first": bad[:10]}
        report["leave_one_out"] = loo
    report["problems"] = problems
    report["selected_ranges"] = [[f"{x:#010x}", f"{y:#010x}"]
                                 for x, y in ranges(sorted(sel["mask"]))]
    if held and not problems and (a.emit or a.receipt):
        with tempfile.TemporaryDirectory(dir=WL.BUILD) as d:
            trial = write_mask(sorted(sel["mask"]), [" * trial"], Path(d) / "mask.h")
            agreement = digest_agreement(WL, Path(d) / "mask.h", runs + [held])
        report["digest_agreement"] = agreement
        broken = {k: v for k, v in agreement.items() if v["differing"]}
        if broken:
            problems.append(f"the digest columns under the mask differ from the first source's: "
                            f"{broken}")
    if a.emit and not problems:
        lines = [
            " * The words of `.dataa` a replay's tick capture digests, chosen word by word",
            " * from the word logs of native runs configured exactly like the comparison",
            " * runs: every aligned word that takes more than one value over ticks 1 to",
            f" * {max(ticks.values())} and that every source holds alike at every tick, less",
            " * the words whose values fall in memory every source had committed (each run's",
            " * own maps, the probe's own blocks and the executable's image left out), the",
            " * identity words and the words the exclusion record drops for this build.",
            " * A held-out capture holds every selected word alike at every tick too.",
            " * One TD column per 64 KiB block: bit i of mask word k selects the block's",
            " * word 32k+i.",
            " *",
            f" * Method: word logs. Ticks: 1 to {max(ticks.values())}.",
            f" * Probe build: {report['build']}",
        ] + [f" * source {k + 1}: {_cite(r.log)} sha256 {r.sha256}" for k, r in enumerate(runs)] \
          + [f" * held out: {_cite(held.log)} sha256 {held.sha256}",
             f" * identity words: {_cite(a.identity_words)} sha256 "
             f"{WL.sha256(a.identity_words)}",
             f" * Receipt: {_cite(a.receipt)}"] \
          + ([f" * excluded words: {_cite(a.exclude_words)} sha256 {WL.sha256(a.exclude_words)}"]
             if a.exclude_words else [])
        report["emitted"] = write_mask(sorted(sel["mask"]), lines, a.emit)
        report["emitted"]["sha256"] = WL.sha256(a.emit)
    if a.receipt and not problems:
        a.receipt.parent.mkdir(parents=True, exist_ok=True)
        a.receipt.write_text(json.dumps(report, indent=1) + "\n")
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "word-log-mask.json").write_text(json.dumps(report, indent=1) + "\n")
    summary = {k: report[k] for k in ("counts", "problems")}
    print(json.dumps(summary, indent=1))
    if problems and not a.dry_run:
        raise SystemExit(f"REFUSED: {len(problems)} problem(s); nothing written")
    print(f"WORDLOG_MASK {'DRY-RUN' if a.dry_run else 'OK'} selected={len(sel['mask'])} "
          f"problems={len(problems)}")
    return 0


def main(argv: list[str]) -> int:
    if "--word-logs" in argv:
        return word_log_mask(argv)
    if argv[:1] == ["lifted"]:
        run_lifted(argv[1] if len(argv) > 1 else "boot-snapshots")
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("native_a", type=Path)
    ap.add_argument("native_b", type=Path)
    ap.add_argument("lifted", type=Path, nargs="?", default=None,
                    help="a lifted run's snapshots, compared on every class; never needed to "
                         "choose the fields, and --emit does not read it")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--unstable", type=Path, nargs="+", action="append", default=[],
                    help="per-block, per-tick snapshots of two or more native runs taken the "
                         "same way; words that differ between them at any tick leave the "
                         "candidates. Repeat for groups that sampled different ticks or blocks; "
                         "the groups' words are united")
    ap.add_argument("--exclude-words", type=Path, default=None,
                    help="a JSON record of words plain native repeats disagree on that no "
                         "snapshot run can show (harness/replay/tick_digest_exclusions.json): "
                         "each is dropped from the candidates, and the header cites the record")
    ap.add_argument("--emit", action="store_true",
                    help="refused: a mask is chosen with --word-logs (the module docstring)")
    args = ap.parse_args(argv)
    if args.emit:
        raise SystemExit(
            "a digest mask is chosen from runs configured exactly like the comparison runs "
            "(the user's mask rule, Documentation/decisions/2026-09-24-S4-halt-220h.md), and a "
            "snapshot run loads harness/replay/native_snapshot_probe.c, which no comparison run "
            "loads: choose the mask from the tick probe's word logs with --word-logs")
    a, regions, base = parse(args.native_a)
    b, _, base_b = parse(args.native_b)
    lf, base_l = ({}, base) if args.lifted is None else parse(args.lifted)[::2]
    if not regions:
        raise SystemExit(f"{args.native_a}: no region map, so pointers cannot be told apart")
    if len({base, base_b, base_l}) != 1:
        raise SystemExit("the files snapshot different bases")
    cls = classify(a, b, regions, base)
    unstable_report = None
    if args.unstable:
        drop, unstable_report = set(), []
        for group in args.unstable:
            if len(group) < 2:
                raise SystemExit("each --unstable group needs two or more runs")
            words, rep = unstable(group, set(cls["varying"]))
            drop |= words
            unstable_report.append(dict(rep, files=[str(g) for g in group]))
        cls["unrepeatable_between_samples"] = sorted(a_ for a_ in cls["varying"] if a_ in drop)
        cls["varying"] = [a_ for a_ in cls["varying"] if a_ not in drop]
    if args.exclude_words is not None:
        excluded = exclusions(args.exclude_words)
        cls["excluded_by_plain_repeats"] = sorted(a_ for a_ in cls["varying"] if a_ in excluded)
        idle = sorted(excluded - set(cls["excluded_by_plain_repeats"]))
        if idle:
            raise SystemExit(f"{args.exclude_words}: {', '.join(f'{x:#010x}' for x in idle)} "
                             "are not among the candidates, so excluding them drops nothing; the "
                             "record names only words the mask would otherwise keep")
        cls["varying"] = [a_ for a_ in cls["varying"] if a_ not in excluded]
    skip = set().union(*map(set, cls.values()))
    report = {
        "unstable": unstable_report,
        "ticks": sorted(a),
        "native_regions": len(regions),
        "classes": {k: len(v) for k, v in cls.items()},
        "lifted": ({k: compare_lifted(a, lf, base, v) for k, v in cls.items()}
                   if lf else None),
        "constant_natively_moved_lifted": (ranges(constant_moved(a, b, lf, base, skip))
                                           if lf else None),
        "candidates": [f"{x:#010x}" for x in cls["varying"]],
        "candidate_ranges": [[f"{x:#010x}", f"{y:#010x}"] for x, y in ranges(cls["varying"])],
    }
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("candidates", "candidate_ranges")}, indent=1)[:6000])
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "snapshot-fields.json").write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
