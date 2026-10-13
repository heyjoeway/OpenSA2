#!/usr/bin/env python3
"""Read a fixture's records out of its file, using `tools/s2/fixtures/corpus.py`'s layouts.

`corpus.py` proves each layout by reproducing the file's byte count; it counts records
but does not hand them back, because registering the corpus needed only the counts.
Driving the corpus needs the values, so this module walks the same `Format` and yields
one tuple per record. It imports corpus.py read-only and adds no layout of its own: a
magic corpus.py does not decode is an error here too.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s2" / "fixtures"))
import corpus as C  # noqa: E402  (read-only)


def sections(path: Path) -> tuple[dict, dict[str, list[tuple]]]:
    """(header, {section name: [record tuples]}) for a fixture with fixed-size sections.

    Raises when the format has a variable-length walk (`Format.custom`): those need a
    per-format reader, not this one.
    """
    data = path.read_bytes()
    magic = data[:7].decode("ascii", "replace")
    fmt = C.FORMATS.get(magic)
    if fmt is None:
        raise SystemExit(f"{path.name}: no decoded format for magic {magic}")
    if fmt.custom is not None:
        raise SystemExit(f"{path.name}: {magic} has a variable-length walk; use a per-format reader")
    off = 8
    header = dict(zip(fmt.header_fields, struct.unpack_from(fmt.header_fmt, data, off)))
    off += struct.calcsize(fmt.header_fmt)
    out: dict[str, list[tuple]] = {}
    for sec in fmt.sections:
        n = header[sec.count_field] if sec.count_field else sec.count
        recs = []
        if sec.words_field:
            words = header[sec.words_field]
            for _ in range(n):
                recs.append(struct.unpack_from(f"<{words}I", data, off))
                off += words * 4
        else:
            size = struct.calcsize(sec.fmt)
            for _ in range(n):
                recs.append(struct.unpack_from(sec.fmt, data, off))
                off += size
        out[sec.name] = recs
    if off != len(data):
        raise SystemExit(f"{path.name}: walked {off} bytes of {len(data)}")
    return header, out


def cse1(path: Path) -> tuple[dict, list[tuple], list[tuple]]:
    """SA2CSE1's variable-length walk, as values.

    `corpus.py`'s `walk_cse1` proves the layout by reproducing the byte count and returns
    only the totals. The same walk, handing back what it walked:

        header   version, polygons, queries, z_extent
        polygons `polygons` records of 16 u32
        queries  `queries` records of 12 u32 whose word 9 is a count, then that many
                 u32 ordinals

    The twelve query words are (tick, radius, point[3], motion[3], expanded radius,
    selected count, 1, visited); `expanded radius` and the ordinals are the captured
    outputs, `motion` and `visited` are the stager's own (the fixture's authority note).
    """
    data = path.read_bytes()
    if data[:7].decode("ascii", "replace") != "SA2CSE1":
        raise SystemExit(f"{path.name}: not SA2CSE1")
    header = dict(zip(("version", "polygons", "queries", "z_extent"),
                      struct.unpack_from("<4I", data, 8)))
    off = 24
    polys = []
    for _ in range(header["polygons"]):
        polys.append(struct.unpack_from("<16I", data, off))
        off += 64
    queries = []
    for _ in range(header["queries"]):
        head = struct.unpack_from("<12I", data, off)
        off += 48
        n = head[9]
        queries.append(head + struct.unpack_from(f"<{n}I", data, off))
        off += 4 * n
    if off != len(data):
        raise SystemExit(f"{path.name}: walked {off} bytes of {len(data)}")
    return header, polys, queries


def find(fixture_id: str) -> Path:
    for d in C.CORPUS_DIRS:
        p = d / f"{fixture_id}.bin"
        if p.exists():
            return p
    raise SystemExit(f"no fixture file for {fixture_id}")


# --------------------------------------------------------------- variable-length readers
# `corpus.py`'s custom walks prove a layout by reproducing the byte count and hand back
# only the totals. Driving needs the values. Each reader below walks the same bytes as
# the `walk_*` it names and returns them; it adds no layout corpus.py does not already
# assert, and it re-checks the walk against the file size exactly as corpus.py does.

def _end(path: Path, off: int, data: bytes) -> None:
    if off != len(data):
        raise SystemExit(f"{path.name}: walked {off} bytes of {len(data)}")


def gfc1(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2GFC1 (`walk_gfc1`): the whole COL table, then one row per tick.

        header  version, cols, ticks, attribute_mask, reach_bits
        cols    `cols` records of (attribute, centre[3], radius)
        ticks   (tick, selected), reference[3], `selected` u16 COL indices, word-padded
    """
    data = path.read_bytes()
    if data[:7] != b"SA2GFC1":
        raise SystemExit(f"{path.name}: not SA2GFC1")
    header = dict(zip(("version", "cols", "ticks", "attribute_mask", "reach_bits"),
                      struct.unpack_from("<5I", data, 8)))
    off = 28
    cols = []
    for _ in range(header["cols"]):
        cols.append(struct.unpack_from("<5I", data, off))
        off += 20
    rows = []
    for _ in range(header["ticks"]):
        tick, n = struct.unpack_from("<2I", data, off)
        ref = struct.unpack_from("<3I", data, off + 8)
        off += 20
        sel = struct.unpack_from(f"<{n}H", data, off)
        off += n * 2 + (n & 1) * 2
        rows.append((tick, ref, sel))
    _end(path, off, data)
    return header, rows, {"cols": cols, "ticks": rows}


def ggo1(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2GGO1 (`walk_ggo1`): the COL table, the per-tick reference points, and one
    row per observation of the list the gather leaves in the globals.

        header        version, cols, ticks, observations, attribute_mask, reach_bits
        cols          `cols` records of (attribute, centre[3], radius)
        ticks         `ticks` records of (tick, reference[3])
        observations  (tick, queries, observed), `queries` records of 4 u32,
                      `observed` u16 COL indices, word-padded
    """
    data = path.read_bytes()
    if data[:7] != b"SA2GGO1":
        raise SystemExit(f"{path.name}: not SA2GGO1")
    header = dict(zip(("version", "cols", "ticks", "observations", "attribute_mask",
                       "reach_bits"), struct.unpack_from("<6I", data, 8)))
    off = 32
    cols = []
    for _ in range(header["cols"]):
        cols.append(struct.unpack_from("<5I", data, off))
        off += 20
    ticks = []
    for _ in range(header["ticks"]):
        ticks.append(struct.unpack_from("<4I", data, off))
        off += 16
    obs = []
    for _ in range(header["observations"]):
        tick, nq, no = struct.unpack_from("<3I", data, off)
        off += 12
        queries = [struct.unpack_from("<4I", data, off + i * 16) for i in range(nq)]
        off += nq * 16
        observed = struct.unpack_from(f"<{no}H", data, off)
        off += no * 2 + (no & 1) * 2
        obs.append((tick, queries, observed))
    _end(path, off, data)
    return header, obs, {"cols": cols, "ticks": ticks, "observations": obs}


def gfn1(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2GFN1 (`walk_gfn1`): the COL table, the per-tick reference points, and one
    record per call carrying that call's per-entry admission decisions.

        header  version, cols, ticks, calls, decisions, attribute_mask, reach_bits
        cols    `cols` records of (attribute, centre[3], radius)
        ticks   `ticks` records of (tick, reference[3])
        calls   (tick, entries), query[4], `entries` records of 2 u32
    """
    data = path.read_bytes()
    if data[:7] != b"SA2GFN1":
        raise SystemExit(f"{path.name}: not SA2GFN1")
    header = dict(zip(("version", "cols", "ticks", "calls", "decisions", "attribute_mask",
                       "reach_bits"), struct.unpack_from("<7I", data, 8)))
    off = 36
    cols = []
    for _ in range(header["cols"]):
        cols.append(struct.unpack_from("<5I", data, off))
        off += 20
    ticks = []
    for _ in range(header["ticks"]):
        ticks.append(struct.unpack_from("<4I", data, off))
        off += 16
    calls = []
    for _ in range(header["calls"]):
        tick, n = struct.unpack_from("<2I", data, off)
        query = struct.unpack_from("<4I", data, off + 8)
        off += 24
        entries = [struct.unpack_from("<2I", data, off + i * 8) for i in range(n)]
        off += n * 8
        calls.append((tick, query, entries))
    _end(path, off, data)
    return header, calls, {"cols": cols, "ticks": ticks, "calls": calls}



def _selection(path: Path, magic: bytes, fixed: int) -> tuple[dict, list, dict]:
    """SA2SLC1 / SA2SLO1 (`walk_selection`): one row per selection call, each carrying
    `count` compact contact records of 22 words.

        header  version, cases, total_records
        SLC1 row  tick, count, selected                       then count x 22 words
        SLO1 row  tick, count, selected, exit_site, csts[20]  then count x 22 words
    """
    data = path.read_bytes()
    if data[:7] != magic:
        raise SystemExit(f"{path.name}: not {magic.decode()}")
    header = dict(zip(("version", "cases", "total_records"), struct.unpack_from("<3I", data, 8)))
    off = 20
    rows, total = [], 0
    for _ in range(header["cases"]):
        head = struct.unpack_from(f"<{fixed}I", data, off)
        off += fixed * 4
        n = head[1]
        recs = [struct.unpack_from("<22I", data, off + i * 88) for i in range(n)]
        off += n * 88
        total += n
        rows.append((head, recs))
    if total != header["total_records"]:
        raise SystemExit(f"{path.name}: walked {total} records, the header says "
                         f"{header['total_records']}")
    _end(path, off, data)
    return header, rows, {"cases": rows}


def slc1(path: Path):
    return _selection(path, b"SA2SLC1", 3)


def slo1(path: Path):
    return _selection(path, b"SA2SLO1", 24)



def bsd1(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2BSD1 (`walk_bsd1`): the Basic-strip inputs of the cache builder's triangle loop.

        header         version, vertices, strips, polygon_words, index_encounters,
                       triangles, buckets, root_center_z, max_z_span, head_generation,
                       tail_generation
        vertices       `vertices` transformed points of 3 u32, indexed by SOURCE index
        strips         `strips` u32 material flag words, one per strip
        polygon_words  `polygon_words` u16: per strip, the strip header then its indices
        payloads       `triangles` records of 15 u32 (record_w2..w15, bucket)
        order          `triangles` u32 generation ordinals, head to tail

    The per-strip split of `polygon_words` is the stager's own
    (`polygon_words.extend([header, *indices])`, count = `header & 0x7FFF`), and this
    reader re-derives it that way and checks the total against `index_encounters`.
    """
    data = path.read_bytes()
    if data[:7] != b"SA2BSD1":
        raise SystemExit(f"{path.name}: not SA2BSD1")
    fields = ("version", "vertices", "strips", "polygon_words", "index_encounters",
              "triangles", "buckets", "root_center_z", "max_z_span", "head_generation",
              "tail_generation")
    header = dict(zip(fields, struct.unpack_from("<11I", data, 8)))
    off = 52
    verts = [struct.unpack_from("<3I", data, off + 12 * i) for i in range(header["vertices"])]
    off += header["vertices"] * 12
    flags = list(struct.unpack_from(f"<{header['strips']}I", data, off))
    off += header["strips"] * 4
    words = struct.unpack_from(f"<{header['polygon_words']}H", data, off)
    off += header["polygon_words"] * 2 + 2
    strips, i, encounters = [], 0, 0
    while i < len(words):
        head = words[i]
        n = min(head & 0x7FFF, 0x400)
        strips.append((head, list(words[i + 1:i + 1 + n])))
        i += 1 + n
        encounters += n
    if len(strips) != header["strips"] or encounters != header["index_encounters"]:
        raise SystemExit(f"{path.name}: {len(strips)} strips / {encounters} indices, the "
                         f"header says {header['strips']} / {header['index_encounters']}")
    pays = [struct.unpack_from("<15I", data, off + 60 * i) for i in range(header["triangles"])]
    off += header["triangles"] * 60
    order = list(struct.unpack_from(f"<{header['triangles']}I", data, off))
    off += header["triangles"] * 4
    _end(path, off, data)
    return header, pays, {"vertices": verts, "flags": flags, "strips": strips,
                          "payloads": pays, "order": order}



def ecc(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2ECC1..4 (`walk_ecc1`..`walk_ecc4`): the composition corpus at four depths.

    Every version ends the same way: `queries` records of 18 fixed words, then `count`
    generation ordinals, then `count` acceptance bits, then 20 result words. The 18 are
    the predecessor stager's own tuple (`stage_precise_candidate_cache_*_composition_
    fixture.py`, the single `write_u32` that emits a query):

        0 sequence, 1..7 query (radius, point[3], motion[3]), 8 expanded radius,
        9 selected count, 10 direction, 11 visited, 12 accepted>0, 13 count again,
        14 accepted, 15 accepted again, 16 selected record (signed), 17 zero

    What comes before the queries is the version's own input depth:

        v1  the quarter-wave table, then 72 builder input records of 14 words
        v2  the table, 52 transformed vertices, one material word per strip, the u16
            strip stream
        v3  the table, the 12-word object matrix, 53 source points, then v2's strip
            sections (the 52 transformed vertices are gone: v3 records their input)
        v4  the table, 9 object transform words, 53 source points, then v2's strip
            sections
    """
    data = path.read_bytes()
    magic = data[:7].decode("ascii", "replace")
    fmt = C.FORMATS.get(magic)
    if fmt is None or not magic.startswith("SA2ECC"):
        raise SystemExit(f"{path.name}: not an SA2ECC fixture")
    header = dict(zip(fmt.header_fields, struct.unpack_from(fmt.header_fmt, data, 8)))
    off = 8 + struct.calcsize(fmt.header_fmt)
    secs: dict = {}
    secs["table"] = list(struct.unpack_from(f"<{header['table_words']}I", data, off))
    off += header["table_words"] * 4
    if magic == "SA2ECC1":
        secs["builder_inputs"] = [struct.unpack_from("<14I", data, off + 56 * i)
                                  for i in range(header["records"])]
        off += header["records"] * 56
    else:
        if magic == "SA2ECC3":
            secs["matrix"] = list(struct.unpack_from("<12I", data, off))
            off += 48
        if magic == "SA2ECC4":
            secs["transform"] = list(struct.unpack_from("<9I", data, off))
            off += 36
        if magic in ("SA2ECC3", "SA2ECC4"):
            secs["points"] = [struct.unpack_from("<3I", data, off + 12 * i)
                              for i in range(header["points"])]
            off += header["points"] * 12
        else:
            secs["vertices"] = [struct.unpack_from("<3I", data, off + 12 * i)
                                for i in range(header["vertices"])]
            off += header["vertices"] * 12
        secs["flags"] = list(struct.unpack_from(f"<{header['strips']}I", data, off))
        off += header["strips"] * 4
        words = struct.unpack_from(f"<{header['polygon_words']}H", data, off)
        off += header["polygon_words"] * 2 + 2
        strips, i, encounters = [], 0, 0
        while i < len(words):
            n = min(words[i] & 0x7FFF, 0x400)
            strips.append((words[i], list(words[i + 1:i + 1 + n])))
            i += 1 + n
            encounters += n
        if len(strips) != header["strips"] or encounters != header["index_encounters"]:
            raise SystemExit(f"{path.name}: {len(strips)} strips / {encounters} indices")
        secs["strips"] = strips
    queries = []
    for _ in range(header["queries"]):
        head = struct.unpack_from("<18I", data, off)
        off += 72
        n = head[9]
        gens = list(struct.unpack_from(f"<{n}I", data, off))
        off += 4 * n
        accepts = list(struct.unpack_from(f"<{n}I", data, off))
        off += 4 * n
        result = list(struct.unpack_from("<20I", data, off))
        off += 80
        queries.append((head, gens, accepts, result))
    secs["queries"] = queries
    _end(path, off, data)
    return header, queries, secs


def gfl1(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2GFL1 (`walk_gfl1`): the whole COL table, then one row per tick.

        header  version, cols, ticks, attribute_mask, reach_bits, relocation_delta
        cols    `cols` records of (attribute, centre[3], radius, object_va)
        ticks   (tick, reference[3], query[3], query_radius, prior_task, prior_object,
                cursor, count), then `count` u16 COL indices, word-padded

    `object_va` is the captured object pointer with the capture's own image base taken
    out; `relocation_delta` is what the stager solved for to put it back, and
    `prior_object` is recorded already relocated (col 1212's 0x10BCF250 + 0x659D0000 =
    0x7659F250 on every tick that has one).
    """
    data = path.read_bytes()
    if data[:7] != b"SA2GFL1":
        raise SystemExit(f"{path.name}: not SA2GFL1")
    header = dict(zip(("version", "cols", "ticks", "attribute_mask", "reach_bits",
                       "relocation_delta"), struct.unpack_from("<6I", data, 8)))
    off = 32
    cols = [struct.unpack_from("<6I", data, off + 24 * i) for i in range(header["cols"])]
    off += 24 * header["cols"]
    ticks = []
    for _ in range(header["ticks"]):
        head = struct.unpack_from("<12I", data, off)
        off += 48
        n = head[11]
        idx = list(struct.unpack_from(f"<{n}H", data, off))
        off += n * 2 + (n & 1) * 2
        ticks.append(head + (idx,))
    _end(path, off, data)
    return header, ticks, {"cols": cols, "ticks": ticks}


def gex2(path: Path) -> tuple[dict, list[dict], dict]:
    """SA2GEX2 (`walk_gex2`): two executor calls, each a run of per-iteration records.

        header     version, calls, player_fields, entity_words, world_words, work_words,
                   record_words
        schema     `player_fields` player word indices
        call       tick, ground_sequence, bound, initial[4] (policy, accumulator,
                   accepted flag, support token), gravity[3], thresholds[2], selector;
                   then `bound` iteration records whose width the iteration's own kind
                   word decides -- miss 152, rejected hit 221, accepted hit 323 -- then
                   the expected final state (75) and PReset state (42)
        iteration  kind, attribute, head_state[75], then for a hit the acceptance case
                   (58), for an accepted hit the tail input (semantic[16],
                   input_state[75], record[22]), for a rejected hit the response input
                   (11), then the exit state (75)

    The widths and the order are the predecessor stager's own
    (`stage_precise_ground_executor_multi_fixture.py`, quoted in its docstring).
    """
    data = path.read_bytes()
    if data[:7] != b"SA2GEX2":
        raise SystemExit(f"{path.name}: not SA2GEX2")
    header = dict(zip(("version", "calls", "player_fields", "entity_words", "world_words",
                       "work_words", "record_words"), struct.unpack_from("<7I", data, 8)))
    state = (header["entity_words"] + header["world_words"] + header["player_fields"]
             + header["work_words"])
    off = 36
    schema = list(struct.unpack_from(f"<{header['player_fields']}I", data, off))
    off += header["player_fields"] * 4
    calls = []
    for _ in range(header["calls"]):
        head = struct.unpack_from("<13I", data, off)
        off += 52
        iterations = []
        for _ in range(head[2]):
            kind = struct.unpack_from("<I", data, off)[0]
            it = {"kind": kind, "attribute": struct.unpack_from("<I", data, off + 4)[0]}
            p = off + 8
            it["head_state"] = list(struct.unpack_from(f"<{state}I", data, p))
            p += state * 4
            if kind != 0:
                it["acceptance"] = list(struct.unpack_from("<58I", data, p))
                p += 58 * 4
            if kind == 1:
                it["semantic"] = list(struct.unpack_from("<16I", data, p)); p += 64
                it["input_state"] = list(struct.unpack_from(f"<{state}I", data, p))
                p += state * 4
                it["record"] = list(struct.unpack_from(f"<{header['record_words']}I", data, p))
                p += header["record_words"] * 4
            if kind == 2:
                it["response"] = list(struct.unpack_from("<11I", data, p)); p += 44
            it["exit_state"] = list(struct.unpack_from(f"<{state}I", data, p))
            p += state * 4
            width = {0: 152, 2: 221, 1: 323}[kind]
            if p != off + width * 4:
                raise SystemExit(f"{path.name}: kind {kind} walked {p - off} of {width * 4}")
            off = p
            iterations.append(it)
        final = list(struct.unpack_from(f"<{state}I", data, off)); off += state * 4
        reset = list(struct.unpack_from("<42I", data, off)); off += 168
        calls.append({"tick": head[0], "sequence": head[1], "bound": head[2],
                      "initial": list(head[3:7]), "gravity": list(head[7:10]),
                      "thresholds": list(head[10:12]), "selector": head[12],
                      "iterations": iterations, "final_state": final,
                      "reset_state": reset})
    _end(path, off, data)
    return header, calls, {"player_schema": [(i,) for i in schema], "calls": calls}


def gpc1(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2GPC1 and SA2GPE2 (`walk_gpc1`): the object caches, then the contact cases.

        header   version, table_words, objects, cases
        table    `table_words` words (the shipped quarter-wave table; unused here,
                 because the image carries it)
        object   (token, attribute, polygons, z_extent, selected), then `selected`
                 ordinals, then `polygons` records of 16 u32 -- SA2CSE1's node shape,
                 whose first two words are the forward and backward chain links as
                 ordinals (0xFFFFFFFF for null)
        case     46 words: sequence, onframe_tick, caller_iteration, caller_bound,
                 object_token, attribute, selector_sequence, selector_result,
                 pni_count, result, record_count, work_w10, selector_w0..6,
                 precise_w0..6, output_w13..32
    """
    data = path.read_bytes()
    magic = data[:7].decode("ascii", "replace")
    if magic not in ("SA2GPC1", "SA2GPE2"):
        raise SystemExit(f"{path.name}: not SA2GPC1/SA2GPE2")
    header = dict(zip(("version", "table_words", "objects", "cases"),
                      struct.unpack_from("<4I", data, 8)))
    off = 24
    table = list(struct.unpack_from(f"<{header['table_words']}I", data, off))
    off += header["table_words"] * 4
    objects = []
    for _ in range(header["objects"]):
        token, attribute, npoly, z_extent, nsel = struct.unpack_from("<5I", data, off)
        off += 20
        sel = list(struct.unpack_from(f"<{nsel}I", data, off)) if nsel else []
        off += nsel * 4
        polys = [struct.unpack_from("<16I", data, off + 64 * i) for i in range(npoly)]
        off += npoly * 64
        objects.append({"token": token, "attribute": attribute, "z_extent": z_extent,
                        "selected": sel, "polygons": polys})
    cases = [struct.unpack_from("<46I", data, off + 184 * i) for i in range(header["cases"])]
    off += header["cases"] * 184
    _end(path, off, data)
    return header, cases, {"table": table, "objects": objects, "cases": cases}


def goo2(path: Path) -> tuple[dict, list[tuple], dict]:
    """SA2GOO2 (`walk_goo2`): one record per ordering call.

        header  version, calls, total_entries, demotions
        call    (tick, count, prior_task, prior_object, cursor), then `count` source
                entries of 4 u32 and `count` ordered entries of 4 u32

    Each entry's first three words are the candidate triple the block moves twelve
    bytes at a time (0x0046237D `cmp edi,[eax+4]`, 0x00462392 `mov [ecx],edi` and the
    two stores after it); the fourth is the stager's own COL index, as the format's
    authority note says ("the cursor slot and the COL indices are computed").
    """
    data = path.read_bytes()
    if data[:7] != b"SA2GOO2":
        raise SystemExit(f"{path.name}: not SA2GOO2")
    header = dict(zip(("version", "calls", "total_entries", "demotions"),
                      struct.unpack_from("<4I", data, 8)))
    off = 24
    calls = []
    for _ in range(header["calls"]):
        tick, count, prior_task, prior_object, cursor = struct.unpack_from("<5I", data, off)
        off += 20
        src = [struct.unpack_from("<4I", data, off + 16 * i) for i in range(count)]
        off += 16 * count
        ordered = [struct.unpack_from("<4I", data, off + 16 * i) for i in range(count)]
        off += 16 * count
        calls.append((tick, count, prior_task, prior_object, cursor, src, ordered))
    _end(path, off, data)
    return header, calls, {"calls": calls}


READERS = {
    "SA2GOO2": goo2,
    "SA2GEX2": gex2,
    "SA2GFL1": gfl1,
    "SA2GPC1": gpc1,
    "SA2GPE2": gpc1,
    "SA2GFC1": gfc1,
    "SA2GGO1": ggo1,
    "SA2GFN1": gfn1,
    "SA2SLC1": slc1,
    "SA2SLO1": slo1,
    "SA2BSD1": bsd1,
    "SA2ECC1": ecc,
    "SA2ECC2": ecc,
    "SA2ECC3": ecc,
    "SA2ECC4": ecc,
}


def load(fixture_id: str) -> tuple[dict, list[tuple], dict]:
    """(header, the fixture's row records, every decoded section) for one fixture.

    A format with a variable-length walk is read by its own reader in `READERS`;
    everything else by `sections`, whose `cases` records are the rows -- or, when the
    format names its sections something else (SA2CCB1 calls them `inputs`, `payloads`
    and `order`), the first section whose record count is the format's own
    `rows_field`, which is what `register_corpus.py` counts as the row count.
    """
    path = find(fixture_id)
    magic = path.read_bytes()[:7].decode("ascii", "replace")
    if magic in READERS:
        return READERS[magic](path)
    if magic == "SA2CSE1":
        header, polys, queries = cse1(path)
        return header, queries, {"polygons": polys, "queries": queries}
    header, secs = sections(path)
    if "cases" in secs:
        return header, secs["cases"], secs
    want = header[C.FORMATS[magic].rows_field]
    rows = next((v for v in secs.values() if len(v) == want), [])
    return header, rows, secs
