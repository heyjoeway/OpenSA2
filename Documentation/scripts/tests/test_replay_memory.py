"""The tick-replay comparison, bounded in memory, gives every result the unbounded one gave.

A Metal Harbor verdict held every native repeat's word stream at once, the canonical numbering
of each beside it, and the reader's own copies of every log: about 18 GB, more than the machine
leaves a job. tools/s4/tick_replay.py now takes each repeat's stream, compares it with the
capture and drops it before reading the next (`native_streams`, `native_repeat_streams`), writes
one string per canonical number rather than one per cell (`_canonical`), keeps streams per
process only within `KEPT_CELLS` (`remembered`), and leaves the reader's caches as it found them
(`unkept`). These tests hold that comparison to the one it replaced, written out below as it
stood at development_snapshot (`old_canonical`, `old_repeat_streams`), on seeded synthetic streams: the same
numbering, the same determinism record, the same refusals, and never two repeats' streams held
at once.
"""
from __future__ import annotations

import random
import sys
import weakref
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import tick_replay as T  # noqa: E402

HEADER = ["tick", "frame", "g_heap", "d_x", "h_a", "h_b", "h_c"]
IDENT = {"g_heap": "heap", "h_a": "handle", "h_b": "handle", "h_c": "handle"}
SHIFTING = ["h_a", "h_b", "h_c"]
HEAPS = [0, 0x04000010, 0x04000020, 0x04000400, 0x00001000]   # the last is outside the heap
HANDLES = [0x230, 0x22C, 0x234, 0x7D000000, 0xFFFFFFFF]


def old_canonical(header, rows, identity, lifted=False):
    """tick_replay._canonical as it stood at development_snapshot: one string per cell."""
    fields = header[1:]
    cols = [(i, identity[f]) for i, f in enumerate(fields) if f in identity]
    count = {k: 0 for k in T.KINDS}
    live = {k: {} for k in T.KINDS}
    out = []
    for tick, words in rows:
        w = list(words)
        held = {k: set() for k in T.KINDS}
        for i, kind in cols:
            v = int(w[i], 16)
            if v in T.NULLS[kind]:
                w[i] = f"0x{v:08x}"
            elif lifted and kind == "heap" and not T.GUEST_HEAP[0] <= v < T.GUEST_HEAP[1]:
                w[i] = T.OUT_OF_HEAP
            else:
                held[kind].add(v)
                if v not in live[kind]:
                    count[kind] += 1
                    live[kind][v] = count[kind]
                w[i] = f"0x{live[kind][v]:08x}"
        for kind in T.RELEASED:
            if len(live[kind]) != len(held[kind]):
                live[kind] = {v: n for v, n in live[kind].items() if v in held[kind]}
        out.append((tick, w))
    return out


def old_repeat_streams(entry, header, native, work, streams, identity, shift=None):
    """tick_replay.native_repeat_streams as it stood at development_snapshot: every repeat's stream held,
    and every repeat named, before the first is compared."""
    T.repeat_directories(entry)
    repeats = entry["native"].get("repeats", [])
    if len(streams) != len(repeats):
        raise T.refuse(f"{entry['name']}: {len(streams)} repeat streams for {len(repeats)} repeats")
    policy = entry.get("field_policy", {})
    ident = dict(T.policy_identity(policy)) | dict(identity)
    cols = [f for f in header[1:] if f in ident]
    order = {c: k for k, c in enumerate(cols)}
    named = {f: k for f, k in ident.items() if T.column_kind(f, {}) != k}
    moving = set(shift or [])
    spec = {"columns": sorted(moving), "bound": None} if moving else None
    cap = old_canonical(header, native, ident)
    for k, (rep, h2, rows) in enumerate(streams):
        if rep is not repeats[k] and rep != repeats[k]:
            raise T.refuse(f"{entry['name']}: repeat stream {k} is not repeat {k}'s")
        if h2 != header:
            raise T.refuse(f"repeat {rep['capture']} has different fields")
    others = [old_canonical(header, rows, ident)[:min(len(native), len(rows))]
              for _, _, rows in streams]
    split_header = T.project(header, [], moving)[0]
    out, judged, evs = [], len(native), []
    for k, (rep, h2, rows) in enumerate(streams):
        common = min(len(native), len(rows))
        other = others[k]
        report = T.split(split_header, T.project(header, cap[:common], moving)[1],
                         T.project(header, other, moving)[1], work, f"repeat{k}")
        tick = int(report["identity"][T.IDENTITY]) if report["status"] == "different" else None
        field = report.get("field") if tick is not None else None
        pair = (T.events(header, cap[:common], ident), T.events(header, other, ident))
        checks = T.identity_checks(*pair, cols, named, common, shift=spec)
        if moving:
            evs.append(pair)
            failed = T.first_check_failure(checks, order)
            if failed and (tick is None or failed[1]["tick"] < tick):
                tick, field = failed[1]["tick"], failed[1]["field"]
        judged = min(judged, (tick - 1) if tick is not None else common)
        first = {c: v["first"] for c, v in checks.items() if not v["ok"]}
        out.append({"capture": rep["capture"], "ticks": len(rows), "compared_ticks": common,
                    "first_divergent_tick": tick, "first_divergent_field": field,
                    "byte_identical": rep["sha256"] == entry["native"]["sha256"],
                    "identity": {c: {"ok": v["ok"], "mismatches": v["mismatches"]}
                                 for c, v in checks.items()} | {"first": first}})
    det = {"repeats": out, "judged_ticks": judged}
    if moving:
        for rec, pair in zip(out, evs):
            s = T.identity_checks(*pair, cols, named, judged, shift=spec)["open"]["shift"]
            rec["open_shift"] = {"pairs": s["pairs"], "shifted": s["shifted"],
                                 "widest": s["widest"], "first_shifted": s["first_shifted"]}
        det["open_shift"] = {"columns": len(moving),
                             "bound": max((r["open_shift"]["widest"] for r in out), default=0)}
    return det


def hx(v: int) -> str:
    return f"0x{v:08x}"


def capture(rng: random.Random, ticks: int) -> list[tuple[int, list[str]]]:
    """A synthetic capture: a frame counter, a heap word that moves among a few values (one
    outside the guest heap), a digest, and three handle words that open, hold and close."""
    rows, heap, handles = [], 0, [0, 0, 0]
    for t in range(1, ticks + 1):
        if rng.random() < 0.2:
            heap = rng.choice(HEAPS)
        for c in range(3):
            if handles[c] == 0 and rng.random() < 0.25:
                handles[c] = rng.choice(HANDLES)
            elif handles[c] and rng.random() < 0.15:
                handles[c] = 0
        rows.append((t, [hx(100 + t), hx(heap), hx(rng.choice([7, 8, 9])), *map(hx, handles)]))
    return rows


def repeat_of(rng: random.Random, rows, kind: str) -> list[tuple[int, list[str]]]:
    """The capture again, as a repeat may differ from it: the same, renumbered by the host,
    with one digest apart, with one handle open moved a tick, or shorter."""
    out = [(t, list(w)) for t, w in rows]
    if kind == "renumbered":
        moved = {hx(v): hx(v + 0x1000) for v in HANDLES if v != 0xFFFFFFFF}
        for _, w in out:
            w[3:] = [moved.get(x, x) for x in w[3:]]
    elif kind == "digest":
        t = rng.randrange(len(out))
        out[t][1][2] = hx(0xD1FF)
    elif kind == "shifted":
        opens = [t for t in range(1, len(out)) if out[t - 1][1][3] == hx(0) != out[t][1][3]]
        if opens:
            t = rng.choice(opens)
            out[t - 1][1][3] = out[t][1][3]
    elif kind == "short":
        out = out[:rng.randrange(1, len(out))]
    return out


def world(seed: int, kinds: list[str], ticks: int = 40):
    rng = random.Random(seed)
    native = capture(rng, ticks)
    reps = [repeat_of(rng, native, k) for k in kinds]
    entry = {"name": f"s{seed}", "native": {
        "capture": "n/c.csv", "sha256": "n",
        "repeats": [{"capture": f"r{k}/c.csv", "sha256": "n" if kind == "same" else f"r{k}"}
                    for k, kind in enumerate(kinds)]}}
    return entry, native, [(entry["native"]["repeats"][k], list(HEADER), rows)
                           for k, rows in enumerate(reps)]


KINDS = ["same", "renumbered", "digest", "shifted", "short"]


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("lifted", [False, True])
def test_the_canonical_numbering_is_the_one_it_replaced(seed, lifted):
    rows = capture(random.Random(seed), 60)
    assert T._canonical(HEADER, rows, IDENT, lifted) == old_canonical(HEADER, rows, IDENT, lifted)
    assert T.canonical(HEADER, rows, IDENT, lifted) == old_canonical(HEADER, rows, IDENT, lifted)


def test_a_number_s_cells_are_one_string_each():
    """The memory half of the numbering: every cell naming one number is the same string, so a
    stream's identity cells cost one pointer each, not one string each."""
    rows = T._canonical(HEADER, capture(random.Random(3), 200), IDENT, False)
    cols = [i for i, f in enumerate(HEADER[1:]) if f in IDENT]
    cells = [w[i] for _, w in rows for i in cols]
    assert len({id(c) for c in cells}) == len(set(cells))


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("shift", [None, SHIFTING])
def test_repeats_read_one_at_a_time_judge_what_every_repeat_held_at_once_did(tmp_path, seed,
                                                                            shift):
    kinds = [KINDS[(seed + k) % len(KINDS)] for k in range(1 + seed % 4)]
    entry, native, streams = world(seed, kinds)
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    (tmp_path / "gen").mkdir()
    want = old_repeat_streams(entry, HEADER, native, tmp_path / "old", streams, IDENT, shift)
    assert T.native_repeat_streams(entry, HEADER, native, tmp_path / "new", streams, IDENT,
                                   shift) == want
    assert T.native_repeat_streams(entry, HEADER, native, tmp_path / "gen", iter(streams), IDENT,
                                   shift) == want


class Rows(list):
    """A stream's rows that can be watched for being released."""


def test_no_two_repeats_streams_are_held_at_once(tmp_path):
    """Each repeat's stream is released before the next is read: when the comparison asks for
    repeat k, no earlier repeat's rows are alive anywhere."""
    entry, native, streams = world(5, ["same", "digest", "renumbered", "shifted", "short"])
    alive: list[weakref.ref] = []
    held_when_asked = []

    def given():
        for rep, header, rows in streams:
            held_when_asked.append(sum(r() is not None for r in alive))
            fresh = Rows((t, list(w)) for t, w in rows)
            alive.append(weakref.ref(fresh))
            yield rep, header, fresh
            del fresh

    det = T.native_repeat_streams(entry, HEADER, native, tmp_path, given(), IDENT, SHIFTING)
    assert held_when_asked == [0, 0, 0, 0, 0]
    assert all(r() is None for r in alive)
    assert det == old_repeat_streams(entry, HEADER, native, tmp_path, streams, IDENT, SHIFTING)


@pytest.mark.parametrize("given", [list, iter])
def test_too_few_or_too_many_repeat_streams_are_refused_as_before(tmp_path, given):
    entry, native, streams = world(1, ["same", "digest", "renumbered"])
    for wrong in (streams[:2], streams + streams[:1]):
        with pytest.raises(SystemExit) as old:
            old_repeat_streams(entry, HEADER, native, tmp_path, wrong, IDENT)
        with pytest.raises(SystemExit) as new:
            T.native_repeat_streams(entry, HEADER, native, tmp_path, given(wrong), IDENT)
        assert str(new.value) == str(old.value)


def test_a_repeat_stream_out_of_order_or_with_other_fields_is_refused_as_before(tmp_path):
    entry, native, streams = world(2, ["same", "digest"])
    swapped = [streams[1], streams[0]]
    other = [streams[0], (streams[1][0], HEADER[:-1] + ["h_z"], streams[1][2])]
    for wrong in (swapped, other):
        with pytest.raises(SystemExit) as old:
            old_repeat_streams(entry, HEADER, native, tmp_path, wrong, IDENT)
        with pytest.raises(SystemExit) as new:
            T.native_repeat_streams(entry, HEADER, native, tmp_path, iter(wrong), IDENT)
        assert str(new.value) == str(old.value)


def test_a_value_is_kept_within_the_budget_and_the_oldest_goes_first(monkeypatch):
    monkeypatch.setattr(T, "KEPT_CELLS", 10)
    cache: dict = {}
    made = []

    def make(n):
        def f():
            made.append(n)
            return [(1, ["x"] * n)]
        return f

    a, shared = T.remembered(cache, "a", make(4), T.cells)
    assert shared and a == [(1, ["x"] * 4)] and list(cache) == ["a"]
    T.remembered(cache, "b", make(4), T.cells)
    assert T.remembered(cache, "a", make(4), T.cells)[0] == a and made == [4, 4]   # a hit
    T.remembered(cache, "c", make(4), T.cells)                     # 12 > 10: a goes
    assert list(cache) == ["b", "c"]
    big, shared = T.remembered(cache, "d", make(11), T.cells)      # never fits: not kept
    assert not shared and big == [(1, ["x"] * 11)] and list(cache) == ["b", "c"]


def test_a_log_stream_too_large_to_keep_is_the_same_stream_and_leaves_no_cache(tmp_path,
                                                                             monkeypatch):
    """log_stream over a synthetic word log: kept or not, the same header and rows; one too
    large for the budget is kept nowhere, neither here nor in the reader's caches."""
    import test_snapshot_fields as TSF  # noqa: PLC0415
    log = TSF.write_log(tmp_path / "n.bin", TSF.base_ticks())
    extra = [("h_01755220", 0x01755220, "handle")]
    monkeypatch.setattr(T, "_STREAMS", {})
    reader = (dict(T.WL._CAPTURES), dict(T.WL._FIELDS))
    kept = T.log_stream(log, ROOT / T.BOOT_MASK, extra, native=False)
    assert len(T._STREAMS) == 1
    monkeypatch.setattr(T, "_STREAMS", {})
    monkeypatch.setattr(T, "KEPT_CELLS", 1)
    alone = T.log_stream(log, ROOT / T.BOOT_MASK, extra, native=False)
    assert alone == kept and kept[1][3][1][-1] == "0x00000230"
    assert T._STREAMS == {}
    assert (dict(T.WL._CAPTURES), dict(T.WL._FIELDS)) == reader
