#!/usr/bin/env python3
"""Decoder for the inherited reference-project fixture corpus (design 3.7, level 1).

The corpus is 61 private binaries under `private/fixtures/LocalAssets-SA2`
(60 in `Precise/`, one in `Fixtures/`). Each begins with an eight-byte magic
`SA2XXXX\\0` written by a predecessor stager under
`/home/user/Documents/reference-project/Documentation/scripts/stage_*.py`. This module
holds one `Format` per magic: the header `struct` layout, the section layouts,
the native function address(es) the rows belong to, the authority of the rows
and the predecessor receipt that states it.

A format is *decoded* only when the layout reproduces the file byte for byte:
`decode()` parses the header, walks every section, and fails unless the
consumed byte count equals the file size. A layout the stager does not pin
down is not written here; the magic is listed in `UNDECODED` with the reason
instead. Nothing in this module guesses a field.

The module is a library; `register_corpus.py` beside it writes the registry
and the receipt. `python -m` use:

    .venv/bin/python tools/s2/fixtures/corpus.py            # inventory table
"""
from __future__ import annotations

import bisect
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIRS = (ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "Precise",
               ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "Fixtures")
FUNCTION_MAP = ROOT / "private" / "generated" / "function-map.json"
PREDECESSOR = Path("/home/user/Documents/reference-project")

# Authority vocabulary (design 9 claim labels, narrowed to what a fixture row can be).
DIRECT = "directly-traced"          # the row's expected words were read out of a native capture
SHIPPED = "shipped-data-derived"    # the row's words come from shipped game data, not from a capture
COMPUTED = "predecessor-computed"   # the row's expected words were computed by the predecessor's port


@dataclass(frozen=True)
class Section:
    """One run of equal-sized records inside a fixture.

    A record is either a fixed `struct` format (`fmt`) or a run of u32 words whose
    length a header field gives (`words_field`); the stagers use both shapes.
    """
    name: str
    count_field: str               # header field giving the record count ("" -> `count` literal)
    fields: tuple[str, ...]        # field names in order (documentation for the layout)
    fmt: str = ""                  # struct format of one record, little-endian
    words_field: str = ""          # header field giving the record's u32 word count
    count: int = 0                 # used when count_field is ""

    def size(self, header: dict) -> int:
        if self.words_field:
            return header[self.words_field] * 4
        return struct.calcsize(self.fmt)


@dataclass(frozen=True)
class Format:
    magic: str
    stager: str                       # file name under the predecessor's Documentation/scripts
    header_fmt: str                   # struct format after the eight magic bytes
    header_fields: tuple[str, ...]
    sections: tuple[Section, ...]
    rows_field: str                   # which header field is "the row count" for M3 purposes
    functions: tuple[str, ...]        # native addresses whose words the rows carry, "0x........"
    function_basis: str               # where the addresses were read
    authority: str
    authority_basis: str              # predecessor receipt (relative to the predecessor repo)
    covers: str
    inputs: tuple[str, ...]           # row fields that are inputs to the native computation
    outputs: tuple[str, ...]          # row fields that are the native/expected results
    runnable_now: bool = False
    blocker: str | None = None
    tail: str = ""                    # note for trailing bytes the sections do not cover
    notes: str = ""
    covers_function: bool = True      # false for level/asset data that is nobody's input/output
    custom: object = None             # callable(data, header, offset) -> (consumed, {section: count})
                                      # for the stagers whose records are variable-length


@dataclass
class Decoded:
    path: Path
    magic: str
    fmt: Format | None
    header: dict
    rows: int
    sections: dict[str, int] = field(default_factory=dict)
    bytes_consumed: int = 0
    file_bytes: int = 0
    error: str | None = None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def corpus_files() -> list[Path]:
    return sorted((p for d in CORPUS_DIRS for p in d.glob("*.bin")), key=lambda p: p.name)


def magic_of(path: Path) -> str:
    with path.open("rb") as f:
        return f.read(8)[:7].decode("ascii", "replace")


def decode(path: Path) -> Decoded:
    """Parse one fixture against its format. The byte-count identity is the proof."""
    data = path.read_bytes()
    magic = data[:7].decode("ascii", "replace")
    fmt = FORMATS.get(magic)
    d = Decoded(path=path, magic=magic, fmt=fmt, header={}, rows=0, file_bytes=len(data))
    if fmt is None:
        d.error = UNDECODED.get(magic, "no format recorded for this magic")
        return d
    off = 8
    values = struct.unpack_from(fmt.header_fmt, data, off)
    if len(values) != len(fmt.header_fields):
        d.error = (f"header format {fmt.header_fmt} yields {len(values)} values for "
                   f"{len(fmt.header_fields)} names")
        return d
    d.header = dict(zip(fmt.header_fields, values))
    off += struct.calcsize(fmt.header_fmt)
    for section in fmt.sections:
        n = d.header[section.count_field] if section.count_field else section.count
        d.sections[section.name] = n
        off += n * section.size(d.header)
    if fmt.custom is not None:
        try:
            consumed, extra = fmt.custom(data, d.header, off)  # type: ignore[operator]
        except Exception as exc:  # a layout that does not parse is not a layout
            d.error = f"variable-length walk failed at byte {off}: {exc}"
            return d
        d.sections.update(extra)
        off += consumed
    d.bytes_consumed = off
    d.rows = d.header.get(fmt.rows_field, 0) if fmt.rows_field in d.header else d.sections.get(fmt.rows_field, 0)
    if off != len(data):
        d.error = (f"layout does not reproduce the file: {off} bytes from the header and "
                   f"sections, {len(data)} bytes on disk")
    return d


# --------------------------------------------------------------- the function map
class FunctionMap:
    """Containing-function lookup over private/generated/function-map.json."""

    def __init__(self, path: Path = FUNCTION_MAP) -> None:
        self.available = path.exists()
        self.entries: list[dict] = []
        self.starts: list[int] = []
        if not self.available:
            return
        functions = json.loads(path.read_text())["functions"]
        self.entries = sorted(functions, key=lambda f: int(f["address"], 16))
        self.starts = [int(f["address"], 16) for f in self.entries]

    def containing(self, address: int) -> dict | None:
        if not self.available:
            return None
        i = bisect.bisect_right(self.starts, address) - 1
        if i < 0:
            return None
        f = self.entries[i]
        return f if address < self.starts[i] + f["size"] else None

    def describe(self, hexaddr: str) -> dict:
        a = int(hexaddr, 16)
        f = self.containing(a)
        if f is None:
            return {"address": hexaddr, "in_map": False}
        return {"address": hexaddr, "in_map": True, "entry": f["address"], "name": f["name"],
                "modloader_named": f["name_source"] == "modloader", "region": f["region"],
                "size": f["size"], "instructions": f["instructions"],
                "direct_calls": len(f["direct_calls"]), "import_calls": len(f["import_calls"]),
                "is_entry": int(f["address"], 16) == a}


# --------------------------------------------------------------- variable-length walks
# The stagers that write records whose width a per-record count decides. Each walk
# returns the bytes it consumed and the counts it found, and raises if it runs past
# the file; `decode` then compares the total against the file size.
def _walk_queries(data: bytes, off: int, queries: int, fixed: int, arrays: int, trailer: int,
                  count_index: int) -> tuple[int, int]:
    """Query records: `fixed` words, of which one is the array length, then `arrays`
    arrays of that length, then `trailer` words. Returns (bytes, total array entries)."""
    start, total = off, 0
    for _ in range(queries):
        head = struct.unpack_from(f"<{fixed}I", data, off)
        n = head[count_index]
        off += fixed * 4 + arrays * n * 4 + trailer * 4
        total += n
        if off > len(data):
            raise ValueError("query record runs past the end of the file")
    return off - start, total


def walk_cse1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    off += header["polygons"] * 16 * 4
    consumed, selected = _walk_queries(data, off, header["queries"], 12, 1, 0, 9)
    return off + consumed - start, {"polygons": header["polygons"], "queries": header["queries"],
                                    "selected_ordinals": selected}


def walk_ecc1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    off += header["table_words"] * 4 + header["records"] * 14 * 4
    consumed, selected = _walk_queries(data, off, header["queries"], 18, 2, 20, 9)
    return off + consumed - start, {"table_words": header["table_words"],
                                    "builder_inputs": header["records"],
                                    "queries": header["queries"], "selected_ordinals": selected}


def walk_ecc2(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    off += header["table_words"] * 4
    off += header["vertices"] * 3 * 4 + header["strips"] * 4
    off += header["polygon_words"] * 2 + 2        # u16 strip stream, padded to a word
    consumed, selected = _walk_queries(data, off, header["queries"], 18, 2, 20, 9)
    return off + consumed - start, {"table_words": header["table_words"],
                                    "vertices": header["vertices"], "strips": header["strips"],
                                    "queries": header["queries"], "selected_ordinals": selected}


def walk_ecc3(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    off += header["table_words"] * 4 + header["matrix_words"] * 4 + header["points"] * 3 * 4
    off += header["strips"] * 4 + header["polygon_words"] * 2 + 2
    consumed, selected = _walk_queries(data, off, header["queries"], 18, 2, 20, 9)
    return off + consumed - start, {"table_words": header["table_words"], "points": header["points"],
                                    "queries": header["queries"], "selected_ordinals": selected}


def walk_ecc4(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    off += header["table_words"] * 4 + header["transform_words"] * 4 + header["points"] * 3 * 4
    off += header["strips"] * 4 + header["polygon_words"] * 2 + 2
    consumed, selected = _walk_queries(data, off, header["queries"], 18, 2, 20, 9)
    return off + consumed - start, {"table_words": header["table_words"],
                                    "transform_words": header["transform_words"],
                                    "points": header["points"], "queries": header["queries"],
                                    "selected_ordinals": selected}


def walk_bsd1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    off += header["vertices"] * 3 * 4 + header["strips"] * 4
    off += header["polygon_words"] * 2 + 2
    off += header["triangles"] * 15 * 4 + header["triangles"] * 4
    return off - start, {"vertices": header["vertices"], "strips": header["strips"],
                         "polygon_words": header["polygon_words"],
                         "cache_payloads": header["triangles"], "order": header["triangles"]}


def walk_goo2(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start, entries = off, 0
    for _ in range(header["calls"]):
        _tick, count, _prior_task, _prior_object, _cursor = struct.unpack_from("<5I", data, off)
        off += 20 + count * 2 * 16
        entries += count
        if off > len(data):
            raise ValueError("object-order call runs past the end of the file")
    return off - start, {"calls": header["calls"], "entries_per_list": entries}


def walk_gpc1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start, polygons, selected = off, 0, 0
    off += header["table_words"] * 4
    for _ in range(header["objects"]):
        _token, _attribute, npoly, _z, nsel = struct.unpack_from("<5I", data, off)
        off += 20 + nsel * 4 + npoly * 16 * 4
        polygons += npoly
        selected += nsel
        if off > len(data):
            raise ValueError("object cache runs past the end of the file")
    off += header["cases"] * 46 * 4
    return off - start, {"table_words": header["table_words"], "objects": header["objects"],
                         "polygons": polygons, "selected_ordinals": selected,
                         "cases": header["cases"]}


def walk_gex2(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    """Two calls; each call is 13 words, then `bound` iterations whose width the
    iteration's own kind word decides, then a 117-word final/reset block."""
    width = {0: 152, 2: 221, 1: 323}   # miss, rejected hit, accepted hit
    start, iterations = off, 0
    for _ in range(header["calls"]):
        call = struct.unpack_from("<13I", data, off)
        off += 13 * 4
        for _ in range(call[2]):       # bound
            kind = struct.unpack_from("<I", data, off)[0]
            off += width[kind] * 4
            iterations += 1
        off += 117 * 4
        if off > len(data):
            raise ValueError("executor call runs past the end of the file")
    return off - start, {"calls": header["calls"], "iterations": iterations}


def _walk_index_rows(data: bytes, off: int, rows: int, fixed_fmt: str, count_index: int,
                     prefix_words: int = 0) -> tuple[int, int]:
    """Rows of `fixed_fmt` followed by `count` u16 indices, padded to a word."""
    start, total = off, 0
    size = struct.calcsize(fixed_fmt)
    for _ in range(rows):
        head = struct.unpack_from(fixed_fmt, data, off)
        n = head[count_index]
        off += size + prefix_words * 4 + n * 2 + (n & 1) * 2
        total += n
        if off > len(data):
            raise ValueError("index row runs past the end of the file")
    return off - start, total


def walk_gfc1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    consumed, selected = _walk_index_rows(data, off, header["ticks"], "<2I3I", 1)
    return off + consumed - start, {"cols": header["cols"], "ticks": header["ticks"],
                                    "selected_indices": selected}


def walk_gfn1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start, entries = off, 0
    for _ in range(header["calls"]):
        _tick, n = struct.unpack_from("<2I", data, off)
        off += 8 + 16 + n * 8
        entries += n
        if off > len(data):
            raise ValueError("funnel call runs past the end of the file")
    return off - start, {"cols": header["cols"], "ticks": header["ticks"],
                         "calls": header["calls"], "entries": entries}


def walk_ggo1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start, queries, observed = off, 0, 0
    for _ in range(header["observations"]):
        _tick, nq, no = struct.unpack_from("<3I", data, off)
        off += 12 + nq * 16 + no * 2 + (no & 1) * 2
        queries += nq
        observed += no
        if off > len(data):
            raise ValueError("gather observation runs past the end of the file")
    return off - start, {"cols": header["cols"], "ticks": header["ticks"],
                         "observations": header["observations"], "queries": queries,
                         "observed_indices": observed}


def walk_gfl1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start = off
    consumed, ordered = _walk_index_rows(data, off, header["ticks"], "<I3I4I4I", 11)
    return off + consumed - start, {"cols": header["cols"], "ticks": header["ticks"],
                                    "ordered_indices": ordered}


def walk_selection(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    """SA2SLC1 (3 fixed words) and SA2SLO1 (4 fixed words plus 20 output words),
    each followed by `count` records of 22 words."""
    start, records = off, 0
    fixed = 3 if data[:7] == b"SA2SLC1" else 24
    for _ in range(header["cases"]):
        count = struct.unpack_from("<I", data, off + 4)[0]
        off += fixed * 4 + count * 22 * 4
        records += count
        if off > len(data):
            raise ValueError("selection row runs past the end of the file")
    if records != header["total_records"]:
        raise ValueError(f"walked {records} records, the header says {header['total_records']}")
    return off - start, {"cases": header["cases"], "records": records}


def walk_mhc1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    start, objects, vertices, triangles = off, 0, 0, 0
    for _ in range(header["records"]):
        _index, _attribute = struct.unpack_from("<2I", data, off)
        nobj = struct.unpack_from("<I", data, off + 24)[0]
        off += 28
        objects += nobj
        for _ in range(nobj):
            nv, nt = struct.unpack_from("<2I", data, off + 40)
            off += 48 + nv * 12 + nt * 6 + (nt & 1) * 2
            vertices += nv
            triangles += nt
            if off > len(data):
                raise ValueError("collision object runs past the end of the file")
    return off - start, {"records": header["records"], "objects": objects,
                         "vertices": vertices, "triangles": triangles}


def _walk_source_objects(data: bytes, header: dict, off: int, va_word: bool) -> tuple[int, dict]:
    start, vertices, strips, words = off, 0, 0, 0
    off += header["table_words"] * 4
    for _ in range(header["objects"]):
        counts = struct.unpack_from("<7I", data, off)
        nv, ns, nw = counts[2], counts[3], counts[4]
        off += 28 + (4 if va_word else 0) + 48 + nv * 12 + ns * 4 + nw * 2 + (nw & 1) * 2
        vertices += nv
        strips += ns
        words += nw
        if off > len(data):
            raise ValueError("source object runs past the end of the file")
    return off - start, {"table_words": header["table_words"], "objects": header["objects"],
                         "vertices": vertices, "strips": strips, "polygon_words": words}


def walk_gms1(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    return _walk_source_objects(data, header, off, va_word=True)


def walk_gmc(data: bytes, header: dict, off: int) -> tuple[int, dict]:
    return _walk_source_objects(data, header, off, va_word=False)


# --------------------------------------------------------------- the format table
# Blockers, written once so the wording is the same wherever they apply.
B_TEN = ("{fn} is outside the ten functions the S1 spike lifted "
         "(Documentation/SA2_RECOMP_DESIGN.md appendix B); S3 emits every function in the map")
B_WINDOW = ("the rows are a window ({window}) inside {fn}, entered mid-function with the whole "
            "player/work state live; S3 lifts the routine and S4's runtime supplies the state")
B_KERNEL = "whole-kernel corpus over {fns}, not a single-function fixture; S3 then S4"
B_ORACLE = ("0x00459260 is the harness's native oracle stub (transcendental, design 7.4 residue "
            "class) and is not lifted; S4's live oracle or a runtime v0 stub policy unblocks it")

FORMATS: dict[str, Format] = {
    # ---------------------------------------------------------------- leaf x87, runnable today
    "SA2NPT1": Format(
        magic="SA2NPT1", stager="stage_precise_ninja_point_transform_fixture.py",
        header_fmt="<4I", header_fields=("version", "rows", "vertices", "matrix_words"),
        sections=(Section("matrix", "", ("m0..m11",), fmt="<12I", count=1),
                  Section("rows", "rows", ("kind", "index", "src_x", "src_y", "src_z",
                                           "exp_x", "exp_y", "exp_z"), fmt="<8I")),
        rows_field="rows", functions=("0x00426CC0", "0x00426D50"),
        function_basis="NINJA_POINT_TRANSFORM_KERNEL.md:9-12 (explicit-matrix helper 0x00426CC0, "
                       "active-matrix wrapper 0x00426D50, call site 0x0048B5E2 in 0x0048B510)",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-ninja-point-transform-v1.json: 52 source "
                        "vertices observed at 0x00426CC0, one attachment centre at 0x00426D50",
        covers="Ninja point transform, mode 0, one captured matrix",
        inputs=("matrix", "src_x", "src_y", "src_z"), outputs=("exp_x", "exp_y", "exp_z"),
        runnable_now=True, blocker=None,
        notes="the 12-word matrix is captured; the source points are shipped vertices; the expected "
              "words are the game's"),
    "SA2TXC1": Format(
        magic="SA2TXC1", stager="stage_precise_contact_transform_corpus_fixture.py",
        header_fmt="<5I", header_fields=("version", "calls", "accepts", "call_words", "accept_words"),
        sections=(Section("calls", "calls", ("site", "src[3]", "matrix[12]", "dst[3]"),
                          words_field="call_words"),
                  Section("accepts", "accepts", ("per-accept regrouping of the calls",),
                          words_field="accept_words")),
        rows_field="calls", functions=("0x00426D50",),
        function_basis="PRECISE_CONTACT_TRANSFORM.md:184-185 (eight wrapped call sites around "
                       "0x00426D50 inside CL_ColPolCheckTouchRe 0x0048CE40)",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/contact-transform-corpus-v1.json; every row is a "
                        "captured call: source, matrix and the destination the game wrote",
        covers="every 0x00426D50 call inside CL_ColPolCheckTouchRe",
        inputs=("matrix[12]", "src[3]"), outputs=("dst[3]",),
        runnable_now=True, blocker=None,
        notes="section B regroups section A's per-accept calls and adds no words (entry test)"),

    # ---------------------------------------------------------------- Ninja object matrix
    "SA2NOM1": Format(
        magic="SA2NOM1", stager="stage_precise_ninja_object_matrix_fixture.py",
        header_fmt="<6I", header_fields=("version", "table_words", "cases", "parent_words",
                                         "transform_words", "result_words"),
        sections=(Section("table", "table_words", ("quarter-wave word",), fmt="<I"),
                  Section("cases", "cases", ("parent[12]", "position[3]", "rotation[3]",
                                             "scale[3]", "expected[12]"), fmt="<33I")),
        rows_field="cases", functions=("0x0048B9F0", "0x00427470", "0x004274E0", "0x004275B0",
                                       "0x00427680", "0x00427750"),
        function_basis="NINJA_OBJECT_MATRIX_KERNEL.md:9,22-30 (the object-local matrix sequence in "
                       "BuildObjectTreePolygons 0x0048B9F0, call sites 0x0048BA3A/46/52/5E/79); the "
                       "five helper addresses from CANDIDATE_CACHE_BUILDER_EVIDENCE.md:288-292",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ninja-object-matrix-v1.json: case 0's "
                        "parent is captured and its expected matrix is pinned to the captured matrix "
                        "in SA2ECC3; cases 1 and 2 are authored synthetics",
        covers="Ninja object-local 3x4 matrix construction",
        inputs=("parent[12]", "position[3]", "rotation[3]", "scale[3]"), outputs=("expected[12]",),
        runnable_now=False,
        blocker="0x0048B9F0 is one of the ten lifted functions and the harness shape matches "
                "(object_tree, 21 in / 12 out), but the lifted body leaves the lifted bytes at the "
                "first call: measured status 1, exit_pc 0x00427470 (njTranslate_) on all three rows. "
                "The five Ninja helpers 0x00427470/0x004274E0/0x004275B0/0x00427680/0x00427750 are "
                "not lifted; S3 emits them",
        notes="the only blocked fixture whose blocker was measured rather than reasoned"),

    # ---------------------------------------------------------------- Basic strip / cache builder
    "SA2BSD1": Format(
        magic="SA2BSD1", stager="stage_precise_basic_strip_decoder_fixture.py",
        header_fmt="<11I", header_fields=("version", "vertices", "strips", "polygon_words",
                                          "index_encounters", "triangles", "buckets",
                                          "root_center_z", "max_z_span", "head_generation",
                                          "tail_generation"),
        sections=(), custom=walk_bsd1, rows_field="triangles",
        functions=("0x0048B610", "0x0048B510"),
        function_basis="BASIC_STRIP_DECODER_KERNEL.md:20-29 (windows inside BuildObjectPolygons "
                       "0x0048B610); vertices from TransformMeshVertices 0x0048B510",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-basic-strip-decoder-v1.json; the "
                        "transformed vertices and the cache payloads are captured, the strip words "
                        "and material flags are shipped-data-derived, the bucket is computed",
        covers="Basic signed-strip expansion into cache triangles",
        inputs=("transformed vertices", "material flags", "strip words"),
        outputs=("cache payloads", "final order"),
        blocker=B_WINDOW.format(window="0x0048B589..0x0048B9C8, six windows",
                                fn="0x0048B610 (987 bytes, 3 callees)"),
        tail="the 81-word strip stream is u16 and padded to a word boundary"),
    "SA2CCB1": Format(
        magic="SA2CCB1", stager="stage_precise_candidate_cache_builder_fixture.py",
        header_fmt="<7I", header_fields=("version", "records", "buckets", "root_center_z",
                                         "max_z_span", "head_generation", "tail_generation"),
        sections=(Section("inputs", "records", ("ordinal", "group", "triangle", "seq_w0..8",
                                                "swap", "flags"), fmt="<14I"),
                  Section("payloads", "records", ("record_w2..w15", "bucket"), fmt="<15I"),
                  Section("order", "records", ("generation ordinal",), fmt="<I")),
        rows_field="records", functions=("0x004974F0", "0x00497560"),
        function_basis="CANDIDATE_CACHE_BUILDER_EVIDENCE.md:170-175 names the path "
                       "InsertPolygonZBucket 0x004974F0 and FlattenPolygonZBuckets 0x00497560; "
                       "neither the stager nor CANDIDATE_CACHE_BUILDER_KERNEL.md pins one entry",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-candidate-cache-builder-v1.json; the "
                        "sequential vertices, payload words and final order are captured, the bucket "
                        "and the winding swap are computed by the stager",
        covers="cache record arithmetic and 128-bucket Z flattening",
        inputs=("seq_w0..8", "swap", "flags"), outputs=("record_w2..w15", "bucket", "order"),
        blocker=B_KERNEL.format(fns="0x004974F0 and 0x00497560, neither lifted")),
    "SA2CSE1": Format(
        magic="SA2CSE1", stager="stage_precise_candidate_enumeration_fixture.py",
        header_fmt="<4I", header_fields=("version", "polygons", "queries", "z_extent"),
        sections=(), custom=walk_cse1, rows_field="queries",
        functions=("0x0048BAF0",),
        function_basis="CANDIDATE_ENUMERATION_EVIDENCE.md:7,19-20 (CL_ColPolListUpNear 0x0048BAF0, "
                       "called from 0x004620C5, itself calling ListGroundForCollision at 0x0048BB41)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-candidate-enumeration-v1.json; the polygon "
                        "words, query words and accepted ordinals are captured, `direction` and "
                        "`visited` are computed and no native column is compared against them",
        covers="Z-chain trim and sphere test selecting candidate faces",
        inputs=("polygon rows", "query_w0..w6"),
        outputs=("expanded radius", "selected ordinals", "direction", "visited"),
        blocker=B_TEN.format(fn="0x0048BAF0 CL_ColPolListUpNear (756 bytes, 2 callees)")),

    # ---------------------------------------------------------------- the composition chain
    "SA2ECC1": Format(
        magic="SA2ECC1", stager="stage_precise_candidate_cache_composition_fixture.py",
        header_fmt="<12I", header_fields=("version", "records", "table_words", "queries",
                                          "selected_total", "accepted", "accepted_repeat",
                                          "result_words", "root_center_z", "max_z_span",
                                          "head_generation", "tail_generation"),
        sections=(), custom=walk_ecc1, rows_field="queries",
        functions=("0x0048BAF0", "0x0048CE40", "0x004974F0", "0x0048B610"),
        function_basis="CANDIDATE_CACHE_COMPOSITION.md names no address; the constituents' documents "
                       "do (0x004974F0/0x00497560 builder, 0x0048BAF0 selector, 0x0048D648 record "
                       "constructor inside CL_ColPolCheckTouchRe 0x0048CE40)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-candidate-cache-composition-v1.json; every "
                        "word is inherited from three hash-pinned fixtures",
        covers="triangles through cache, enumeration and contact",
        inputs=("quarter-wave table", "builder input triangles", "query_w0..w6"),
        outputs=("selected ordinals", "acceptance bits", "20 result words"),
        blocker=B_KERNEL.format(fns="0x0048B610, 0x004974F0, 0x0048BAF0 and 0x0048CE40")),
    "SA2ECC2": Format(
        magic="SA2ECC2", stager="stage_precise_candidate_cache_strip_composition_fixture.py",
        header_fmt="<16I", header_fields=("version", "vertices", "strips", "polygon_words",
                                          "index_encounters", "records", "table_words", "queries",
                                          "selected_total", "accepted", "accepted_repeat",
                                          "result_words", "root_center_z", "max_z_span",
                                          "head_generation", "tail_generation"),
        sections=(), custom=walk_ecc2, rows_field="queries",
        functions=("0x0048BAF0", "0x0048CE40", "0x0048B610"),
        function_basis="CANDIDATE_CACHE_STRIP_COMPOSITION.md names no address; constituents as ECC1 "
                       "plus the strip windows in 0x0048B610",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-candidate-cache-strip-composition-v2.json",
        covers="Basic strips through selected contact words",
        inputs=("transformed vertices", "strip words", "query_w0..w6"),
        outputs=("selected ordinals", "acceptance bits", "20 result words"),
        blocker=B_KERNEL.format(fns="0x0048B610, 0x004974F0, 0x0048BAF0 and 0x0048CE40")),
    "SA2ECC3": Format(
        magic="SA2ECC3", stager="stage_precise_candidate_cache_point_composition_fixture.py",
        header_fmt="<17I", header_fields=("version", "points", "vertices", "matrix_words", "strips",
                                          "polygon_words", "index_encounters", "records",
                                          "table_words", "queries", "selected_total", "accepted",
                                          "accepted_repeat", "result_words", "max_z_span",
                                          "head_generation", "tail_generation"),
        sections=(), custom=walk_ecc3, rows_field="queries",
        functions=("0x0048BAF0", "0x0048CE40", "0x00426CC0", "0x0048B510"),
        function_basis="CANDIDATE_CACHE_POINT_COMPOSITION.md:97-98 (52 vertex rows at 0x00426CC0, "
                       "the attachment centre at 0x00426D50); the composition's expected words are "
                       "the selector's and the contact routine's",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-candidate-cache-point-composition-v3.json",
        covers="source points through selected contact words",
        inputs=("object matrix", "53 source points", "query_w0..w6"),
        outputs=("selected ordinals", "acceptance bits", "20 result words"),
        blocker="v3 removed the 52 transformed point outputs the transform would be checked against "
                "(the oracle-removal chain), so the fixture's expected words are the composed "
                "contact results over 0x0048B610, 0x004974F0, 0x0048BAF0 and 0x0048CE40, none "
                "lifted; S3 then S4"),
    "SA2ECC4": Format(
        magic="SA2ECC4", stager="stage_precise_candidate_cache_object_matrix_composition_fixture.py",
        header_fmt="<17I", header_fields=("version", "points", "vertices", "transform_words",
                                          "strips", "polygon_words", "index_encounters", "records",
                                          "table_words", "queries", "selected_total", "accepted",
                                          "accepted_repeat", "result_words", "max_z_span",
                                          "head_generation", "tail_generation"),
        sections=(), custom=walk_ecc4, rows_field="queries",
        functions=("0x0048BAF0", "0x0048CE40", "0x0048B9F0"),
        function_basis="CANDIDATE_CACHE_OBJECT_MATRIX_COMPOSITION.md names no address; the matrix "
                       "stage is BuildObjectTreePolygons 0x0048B9F0 (NINJA_OBJECT_MATRIX_KERNEL.md:9)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/"
                        "precise-candidate-cache-object-matrix-composition-v4.json",
        covers="shipped object words through selected contact words",
        inputs=("9 object transform words", "53 source points", "query_w0..w6"),
        outputs=("selected ordinals", "acceptance bits", "20 result words"),
        blocker=B_KERNEL.format(fns="0x0048B9F0 (lifted, but trapping at the Ninja helpers), "
                                    "0x0048B610, 0x004974F0, 0x0048BAF0 and 0x0048CE40")),

    # ---------------------------------------------------------------- contact records
    "SA2CRI1": Format(
        magic="SA2CRI1", stager="stage_precise_compact_contact_corpus_fixture.py",
        header_fmt="<3I", header_fields=("version", "accepts", "accept_words"),
        sections=(Section("accepts", "accepts", ("frame_tick", "matrix[12]", "origin[3]",
                                                 "sweep[3]", "effective_radius", "face[16]",
                                                 "record[22]"), words_field="accept_words"),),
        rows_field="accepts", functions=("0x0048CE40",),
        function_basis="stager docstring (the last per-accept transform call returns to 0x0048DB6D, "
                       "inside CL_ColPolCheckTouchRe 0x0048CE40); PRECISE_COMPACT_CONTACT.md:33-34",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/compact-contact-corpus-v1.json; every row field is "
                        "captured, nothing in the row is computed",
        covers="22-word compact contact record from its read inputs",
        inputs=("matrix[12]", "origin[3]", "sweep[3]", "effective_radius", "face[16]"),
        outputs=("record[22]",),
        blocker=B_WINDOW.format(window="the per-accept record assembly ending at 0x0048DB6D",
                                fn="0x0048CE40 CL_ColPolCheckTouchRe (4015 bytes, 14 callees)")),
    "SA2CFN1": Format(
        magic="SA2CFN1", stager="stage_contact_face_fixture.py",
        header_fmt="<3I", header_fields=("version", "records", "record_words"),
        sections=(Section("records", "records", ("onframe_tick", "record_index", "surface_flags",
                                                 "record_w0..w21"), fmt="<25I"),),
        rows_field="records", functions=("0x0048CE40",),
        function_basis="CONTACT_INTERNAL_RECORD.md:175-176 (the record copy at 0x0048DB9C inside "
                       "CL_ColPolCheckTouchRe 0x0048CE40); read at the response boundary "
                       "0x00462F7E (CONTACT_RESPONSE_EVIDENCE.md:5-6)",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/metal-harbor-contact-faces-v1.json; the records and "
                        "surface flags are captured columns of hash-pinned survey captures",
        covers="every accepted contact record the survey observed",
        inputs=(), outputs=("record_w0..w21",),
        runnable_now=False,
        blocker="carries no inputs, so it is not a function fixture (design 3.7 wants inputs and "
                "expected words); it is level-2 replay material",
        covers_function=False,
        notes="registered nowhere: outputs only"),

    # ---------------------------------------------------------------- GroundCollisionStep windows
    "SA2GAC1": Format(
        magic="SA2GAC1", stager="stage_precise_ground_acceptance_fixture.py",
        header_fmt="<3I", header_fields=("version", "cases", "case_words"),
        sections=(Section("cases", "cases", ("onframe_tick", "iteration", "policy", "candidate_w0",
                                             "global_w4", "player words", "angle", "stack_w22..24",
                                             "entity_w0..7", "world_w0..2", "work_w0..32",
                                             "bit8_expected", "verdict", "helper_return"),
                          words_field="case_words"),),
        rows_field="cases", functions=("0x00461F10", "0x004469E0"),
        function_basis="stager docstring (block entry 0x00462C19, velocity test "
                       "0x00462847..0x004629F0) inside GroundCollisionStep 0x00461F10; the angle "
                       "comes from 0x004469E0",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-acceptance-v1.json; words 0..57 are "
                        "captured, the bit-8 expectation and the rejected sentinel are computed",
        covers="acceptance block's policy bit 8 and verdict",
        inputs=("policy", "entity/world/work state", "angle"),
        outputs=("bit8_expected", "verdict", "helper_return"),
        blocker=B_WINDOW.format(window="0x00462C19 and 0x00462847..0x004629F0",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GPW1": Format(
        magic="SA2GPW1", stager="stage_precise_ground_policy_word_fixture.py",
        header_fmt="<3I", header_fields=("version", "cases", "case_words"),
        sections=(Section("cases", "cases", ("sequence", "tick", "iteration", "entity_w0..7",
                                             "player words", "world_w0..2", "attribute",
                                             "support_matches", "stack words", "global 0x01A5A244",
                                             "gravity[3]", "head work_w0..32", "precise work_w13..32",
                                             "expected_policy", "expected_angle", "flags"),
                          words_field="case_words"),),
        rows_field="cases", functions=("0x00461F10", "0x0077FC30", "0x004469E0"),
        function_basis="stager docstring lines 3-8 and GROUND_POLICY_WORD_KERNEL.md:8,24,32,37,45 "
                       "(four blocks 0x00462526..0x00462C14 inside GroundCollisionStep 0x00461F10; "
                       "the blocks call the length helper 0x0077FC30 and the angle helper 0x004469E0)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-policy-word-v1.json; the policy word "
                        "and the slope angle are captured, `support_matches` is computed, and the "
                        "flat fixture's angle word is 0 because ESI is not captured at the loop tail",
        covers="policy word and contact angle at acceptance entry",
        inputs=("entity/player/world/work state", "attribute", "gravity"),
        outputs=("expected_policy", "expected_angle"),
        blocker=B_WINDOW.format(window="0x00462526..0x00462C14, four blocks",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")
                + "; the length and unit-vector words the window computes are not in the case layout"),
    "SA2GQS3": Format(
        magic="SA2GQS3", stager="stage_precise_ground_query_seed_fixture.py",
        header_fmt="<8I", header_fields=("version", "table_words", "cases", "player_fields",
                                         "entity_words", "world_words", "query_words", "case_words"),
        sections=(Section("player_schema", "player_fields", ("player word index",), fmt="<I"),
                  Section("table", "table_words", ("quarter-wave word",), fmt="<I"),
                  Section("cases", "cases", ("sequence", "tick", "entity_w0..7", "world_w0..9",
                                             "24 normalised player words", "player_w19..21",
                                             "gravity[3]", "query_w0..6", "tangent[3]",
                                             "head work_w0..32"), words_field="case_words")),
        rows_field="cases", functions=("0x00461F10", "0x00468E70", "0x00464680"),
        function_basis="GROUND_QUERY_SEED_KERNEL.md (construction order 0x00462003..0x00462038 "
                       "inside GroundCollisionStep 0x00461F10); the window calls LocalToWorld "
                       "0x00468E70 and the centre-correction helper 0x00464680",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-query-seed-slope-v1.json; every word "
                        "is captured except the player 40/44 support-token normalisation",
        covers="initial Work/query seed before the candidate loop",
        inputs=("entity/world/player state", "gravity"),
        outputs=("query_w0..6", "tangent[3]", "head work_w0..32"),
        blocker=B_WINDOW.format(window="0x00462003..0x00462038",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GCL1": Format(
        magic="SA2GCL1", stager="stage_precise_ground_candidate_loop_fixture.py",
        header_fmt="<8I", header_fields=("version", "cases", "player_fields", "entity_words",
                                         "world_words", "work_words", "record_words", "case_words"),
        sections=(Section("player_schema", "player_fields", ("player word index",), fmt="<I"),
                  Section("cases", "cases", ("sequence", "tick", "semantic[16]", "input_state[75]",
                                             "record[22]", "accepted_state[75]", "continuation[4]",
                                             "continued_state[75]", "missed_state[75]",
                                             "final_state[75]", "reset_state[42]"),
                          words_field="case_words")),
        rows_field="cases", functions=("0x00461F10", "0x00465DB0", "0x00469050"),
        function_basis="GROUND_CANDIDATE_LOOP_KERNEL.md:14-21 (loop head 0x004624C0, precise call "
                       "0x0046250A, continuation 0x00463685, finaliser 0x004637E7..0x0046381A, "
                       "PResetPosition 0x00469050) inside GroundCollisionStep 0x00461F10",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-candidate-loop-v1.json; every state "
                        "word is captured, the support tokens and three semantic words are computed",
        covers="accepted candidate preserved through a later miss",
        inputs=("semantic[16]", "input_state[75]", "record[22]"),
        outputs=("accepted_state[75]", "final_state[75]", "reset_state[42]"),
        blocker=B_WINDOW.format(window="0x004624C0..0x0046381A",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GEX1": Format(
        magic="SA2GEX1", stager="stage_precise_ground_candidate_executor_fixture.py",
        header_fmt="<10I", header_fields=("version", "calls", "supported", "player_fields",
                                          "entity_words", "world_words", "work_words",
                                          "record_words", "topology_words", "case_words"),
        sections=(Section("player_schema", "player_fields", ("player word index",), fmt="<I"),
                  Section("topology", "calls", ("ground_sequence", "tick", "bound", "kind",
                                                "entity_w1", "player_w7", "stack_w10",
                                                "initial policy/accepted/support",
                                                "3 (attribute, precise_result) pairs"),
                          words_field="topology_words"),
                  Section("payloads", "supported", ("sequence", "tick", "semantic[16]",
                                                    "input_state[75]", "record[22]",
                                                    "accepted_state[75]", "expected_final[75]",
                                                    "expected_reset[42]"), words_field="case_words")),
        rows_field="calls", functions=("0x00461F10", "0x00465DB0", "0x00469050"),
        function_basis="GROUND_FLAT_FINAL_CANDIDATE_CORPUS.md:38-48 (the loop windows "
                       "0x004622EE..0x00463692 inside GroundCollisionStep 0x00461F10); helper "
                       "0x00465DB0 called at 0x00462F7E",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-candidate-executor-v1.json; the "
                        "topology words and states are captured, the initial tokens and the padded "
                        "(0, 0xFFFFFFFF) pairs are the stager's",
        covers="ordered per-call candidate loop, one accepted hit",
        inputs=("topology", "semantic[16]", "input_state[75]", "record[22]"),
        outputs=("accepted_state[75]", "expected_final[75]", "expected_reset[42]"),
        blocker=B_WINDOW.format(window="0x004622EE..0x00463692",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GEX2": Format(
        magic="SA2GEX2", stager="stage_precise_ground_executor_multi_fixture.py",
        header_fmt="<7I", header_fields=("version", "calls", "player_fields", "entity_words",
                                         "world_words", "work_words", "record_words"),
        sections=(Section("player_schema", "player_fields", ("player word index",), fmt="<I"),),
        custom=walk_gex2, rows_field="calls",
        functions=("0x00461F10", "0x00465DB0", "0x00463EE0", "0x00469050"),
        function_basis="IMPLEMENTATION_TIMELINE.md:1093-1136 (block entry 0x00462C19, rejection tail "
                       "0x00463651, SA2_RejectedContactResponse 0x00463EE0) inside "
                       "GroundCollisionStep 0x00461F10",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-executor-multi-v1.json; every state, "
                        "acceptance and response word is captured, the kind and initial tokens are "
                        "the stager's",
        covers="two-accept calls, including a rejected hit",
        inputs=("per-call metadata", "head_state[75]", "acceptance inputs"),
        outputs=("exit_state[75]", "final_state[75]", "reset_state[42]"),
        blocker=B_WINDOW.format(window="0x00462C19 and the loop map 0x004622EE..0x00463692",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)"),
        tail="each call's payload is a run of per-iteration records whose width the iteration's own "
             "`kind` word decides (miss 152, rejected 221, accepted 323 words)"),
    "SA2GOO2": Format(
        magic="SA2GOO2", stager="stage_precise_ground_object_order_fixture.py",
        header_fmt="<4I", header_fields=("version", "calls", "total_entries", "demotions"),
        sections=(), custom=walk_goo2, rows_field="calls", functions=("0x00461F10",),
        function_basis="stager docstring (ordering block 0x004622EE..0x0046244A) inside "
                       "GroundCollisionStep 0x00461F10 (GROUND_FLAT_FINAL_CANDIDATE_CORPUS.md:33-45)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/ground-object-order-v1.json; the triples and the "
                        "prior words are captured, the cursor slot and the COL indices are computed",
        covers="candidate-triple reorder before the collision loop",
        inputs=("count", "prior_task", "prior_object", "source list"),
        outputs=("ordered list", "cursor"),
        blocker=B_WINDOW.format(window="0x004622EE..0x0046244A",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GPC1": Format(
        magic="SA2GPC1", stager="stage_precise_ground_contact_composition_fixture.py",
        header_fmt="<4I", header_fields=("version", "table_words", "objects", "cases"),
        sections=(), custom=walk_gpc1, rows_field="cases",
        functions=("0x00461F10", "0x0048BAF0", "0x0048CE40"),
        function_basis="GROUND_PRECISE_INPUT_PROBE.md:11,62-63 (selector call site 0x004620C5 to "
                       "CL_ColPolListUpNear 0x0048BAF0, precise call site 0x0046250A to "
                       "CL_ColPolCheckTouchRe 0x0048CE40), both inside GroundCollisionStep 0x00461F10",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-contact-composition-v1.json; the "
                        "object and case words are captured, the object token, the polygon link "
                        "normalisation and the miss sentinel are the stager's",
        covers="selector-to-precise-contact call chain, three object caches",
        inputs=("object caches", "selector_w0..6", "precise_w0..6"),
        outputs=("selector_result", "result", "record_count", "output_w13..32"),
        blocker=B_WINDOW.format(window="0x004620C5 and 0x0046250A",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GPE2": Format(
        magic="SA2GPE2", stager="stage_precise_ground_source_cache_expectation_fixture.py",
        header_fmt="<4I", header_fields=("version", "table_words", "objects", "cases"),
        sections=(), custom=walk_gpc1, rows_field="cases",
        functions=("0x00461F10", "0x0048BAF0", "0x0048CE40"),
        function_basis="inherited from SA2GPC1 (selector 0x004620C5, precise 0x0046250A inside "
                       "GroundCollisionStep 0x00461F10)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/precise-ground-source-cache-schedule-v1.json; a "
                        "byte-for-byte reduction of the hash-pinned SA2GPC1 fixture",
        covers="contact expectations minus the two rebuilt caches",
        inputs=("one captured object cache", "selector_w0..6", "precise_w0..6"),
        outputs=("selector_result", "result", "record_count", "output_w13..32"),
        blocker=B_WINDOW.format(window="0x004620C5 and 0x0046250A",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)")),
    "SA2GPE3": Format(
        magic="SA2GPE3", stager="stage_precise_ground_all_source_expectation_fixture.py",
        header_fmt="<4I", header_fields=("version", "table_words", "objects", "cases"),
        sections=(Section("cases", "cases", ("sequence", "onframe_tick", "caller_iteration",
                                             "caller_bound", "object_token", "attribute",
                                             "selector_sequence", "selector_result", "pni_count",
                                             "result", "record_count", "work_w10",
                                             "selector_w0..6", "precise_w0..6", "output_w13..32"),
                          fmt="<46I"),),
        rows_field="cases", functions=("0x00461F10", "0x0048BAF0", "0x0048CE40"),
        function_basis="same window as SA2GPC1 (selector 0x004620C5, precise 0x0046250A inside "
                       "GroundCollisionStep 0x00461F10); neither stager names an address",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/slope-contact-expectations-v1.json and "
                        "ground-all-source-cache-schedule-v1.json",
        covers="per-call precise contact expectations, no caches",
        inputs=("object_token", "selector_w0..6", "precise_w0..6"),
        outputs=("selector_result", "result", "record_count", "output_w13..32"),
        blocker=B_WINDOW.format(window="0x004620C5 and 0x0046250A",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)"),
        notes="tableCount and objectCount are structurally zero in this schema"),

    # ---------------------------------------------------------------- Ground tail family
    **{magic: Format(
        magic=magic, stager=stager,
        header_fmt="<7I", header_fields=("version", "cases", "player_fields", "entity_words",
                                         "work_words", "record_words", "case_words"),
        sections=(Section("player_schema", "player_fields", ("player word index",), fmt="<I"),
                  Section("cases", "cases", ("sequence", "tick", "semantic", "post-helper input",
                                             "Ground-exit expectation", "PReset seam"),
                          words_field="case_words")),
        rows_field="cases", functions=("0x00461F10", "0x00469050", "0x00464960", "0x00780310",
                                       "0x00464680", "0x00464CC0"),
        function_basis="GROUND_TAIL_KERNEL.md:16-17 and GROUND_TAIL_FIXTURE.md:7 (the window begins "
                       "after helper 0x00465DB0 returns, before the caller's EAX test at 0x00462F86, "
                       "and runs to the end of GroundCollisionStep 0x00461F10); tail callees "
                       "0x00464680, 0x00780310, 0x00464960, 0x00464CC0 and PResetPosition 0x00469050",
        authority="directly-traced+predecessor-computed",
        authority_basis=basis,
        covers=covers, inputs=("semantic", "post-helper input block"),
        outputs=("Ground-exit expectation", "PReset seam"),
        blocker=B_WINDOW.format(window="after 0x00465DB0 to the end of the routine",
                                fn="0x00461F10 GroundCollisionStep (6418 bytes, 23 callees)"),
        notes=notes)
       for magic, stager, basis, covers, notes in (
           ("SA2GTR1", "stage_precise_ground_tail_fixture.py",
            "Documentation/native-evidence-receipts/precise-ground-tail-fixture-v1.json",
            "accepted Ground tail plus PReset seam", "superseded by SA2GTR2..5; 13 semantic words"),
           ("SA2GTR2", "stage_precise_ground_tail_fixture.py",
            "Documentation/native-evidence-receipts/precise-ground-tail-v2.json",
            "accepted Ground tail plus PReset seam", "superseded by SA2GTR3..5; 16 semantic words"),
           ("SA2GTR3", "stage_precise_ground_tail_fixture.py",
            "Documentation/native-evidence-receipts/precise-ground-tail-v3.json",
            "accepted Ground tail plus PReset seam", "world block widened to 10 words at v3"),
           ("SA2GTR4", "stage_precise_ground_tail_fixture_v4.py",
            "Documentation/native-evidence-receipts/precise-ground-tail-v4.json",
            "accepted Ground tail, three contact-angle families", ""),
           ("SA2GTR5", "stage_precise_ground_tail_final_fixture_v5.py",
            "Documentation/native-evidence-receipts/precise-ground-tail-final-v5.json",
            "final-iteration accepted Ground tail",
            "ground-tail-slope-v1.bin is written by stage_precise_ground_tail_slope_fixture.py, "
            "which emits the same schema"))},

    # ---------------------------------------------------------------- ListGroundForCollision family
    **{magic: Format(
        magic=magic, stager="stage_precise_ground_admission_source_fixture.py",
        header_fmt="<4I", header_fields=("version", "cols", "cases", "accepted_total"),
        sections=(Section("cols", "cols", ("col_index", "attribute", "centre[3]", "radius"),
                          fmt="<6I"),
                  Section("cases", "cases", fields, fmt=case_fmt)),
        rows_field="cases", functions=("0x0047CD60",),
        function_basis="GROUND_ADMISSION_KERNEL.md:7 (the admission branch of ListGroundForCollision, "
                       "site 0x0047D0EC; entry 0x0047CD60 per NEAR_OBJECT_SET_PROBE.md:137)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/ground-admission-kernel-v1.json; the decisions and "
                        "query words are captured, the COL operands are shipped-data-derived and "
                        "asserted equal to the captured columns, the dense col_slot is computed",
        covers="gather-admission sphere test per COL entry",
        inputs=("col_slot", "query[3]", "query_radius"), outputs=outputs,
        blocker=B_WINDOW.format(window="0x0047D0EC",
                                fn="0x0047CD60 ListGroundForCollision (1009 bytes, 6 callees)"),
        notes=notes)
       for magic, case_fmt, fields, outputs, notes in (
           ("SA2GAS1", "<7I", ("tick", "accepted", "col_slot", "query[3]", "query_radius"),
            ("accepted",), "superseded by v2: carries no captured distance word"),
           ("SA2GAS2", "<8I", ("tick", "accepted", "col_slot", "query[3]", "query_radius",
                               "distance_bits"), ("accepted", "distance_bits"),
            "adds the word the probe read at [ESP+0x18]"),
           ("SA2GAS3", "<8I", ("tick", "accepted", "col_slot", "query[3]", "query_radius",
                               "distance_bits"), ("accepted", "distance_bits"),
            "v3 widens the corpus; the two files differ only in the captures merged"))},
    **{magic: Format(
        magic=magic, stager="stage_precise_ground_near_set_fixture.py",
        header_fmt="<4I", header_fields=("version", "cases", "selected_total", "reach_bits"),
        sections=(Section("cases", "cases", fields, fmt=case_fmt),),
        rows_field="cases", functions=("0x0047D160",),
        function_basis="stager docstring (the loop compare at 0x0047D620) and "
                       "GROUND_NEAR_SET_KERNEL.md:7-8 (installed body 0x0047D583..0x0047D620) inside "
                       "RefreshCollisionObjectSet 0x0047D160",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/ground-near-set-kernel-v1.json; every row field is "
                        "read by the probe at the decision itself",
        covers="near-object-set distance and radius compare",
        inputs=("ref[3]", "centre[3]", "radius"), outputs=outputs,
        blocker=B_WINDOW.format(window="0x0047D583..0x0047D620",
                                fn="0x0047D160 RefreshCollisionObjectSet (1351 bytes, 6 callees)"),
        notes=notes)
       for magic, case_fmt, fields, outputs, notes in (
           ("SA2GNS1", "<9I", ("tick", "selected", "ref[3]", "centre[3]", "radius"), ("selected",),
            "superseded by v2: the captured distance is not stored"),
           ("SA2GNS2", "<10I", ("tick", "selected", "ref[3]", "centre[3]", "radius",
                                "distance_bits"), ("selected", "distance_bits"),
            "the captured distance travels with the case from v2 on"),
           ("SA2GNS3", "<10I", ("tick", "selected", "ref[3]", "centre[3]", "radius",
                                "distance_bits"), ("selected", "distance_bits"),
            "v3 and v4 share the schema; the stager pins no capture list, so which captures fed "
            "which file is not recoverable from the stager"))},
    "SA2GFC1": Format(
        magic="SA2GFC1", stager="stage_precise_ground_funnel_fixture.py",
        header_fmt="<5I", header_fields=("version", "cols", "ticks", "attribute_mask", "reach_bits"),
        sections=(Section("cols", "cols", ("attribute", "centre[3]", "radius"), fmt="<5I"),),
        custom=walk_gfc1, rows_field="ticks", functions=("0x0047D160",),
        function_basis="GROUND_FUNNEL_COMPOSITION.md:79 (attribute gate 0x0047D553) and "
                       "GROUND_NEAR_SET_KERNEL.md:7 (selection loop 0x0047D583..0x0047D620) inside "
                       "RefreshCollisionObjectSet 0x0047D160",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/ground-funnel-composition-v1.json; the reference "
                        "points and membership are captured, the COL table is shipped-data-derived "
                        "and the indices are resolved through a hardcoded relocation delta",
        covers="whole-table funnel down to the near set",
        inputs=("COL table", "reference[3]", "attribute_mask", "reach_bits"),
        outputs=("selected COL indices",),
        blocker=B_WINDOW.format(window="0x0047D553 and 0x0047D583..0x0047D620",
                                fn="0x0047D160 RefreshCollisionObjectSet (1351 bytes, 6 callees)"),
        tail="each tick row is 20 bytes plus one u16 per selected index, padded to a word"),
    "SA2GFN1": Format(
        magic="SA2GFN1", stager="stage_precise_ground_funnel_chain_fixture.py",
        header_fmt="<7I", header_fields=("version", "cols", "ticks", "calls", "decisions",
                                         "attribute_mask", "reach_bits"),
        sections=(Section("cols", "cols", ("attribute", "centre[3]", "radius"), fmt="<5I"),
                  Section("ticks", "ticks", ("tick", "reference[3]"), fmt="<4I")),
        custom=walk_gfn1, rows_field="decisions", functions=("0x0047CD60", "0x0047D160"),
        function_basis="GROUND_FULL_CHAIN.md:13-14 (near set 0x0047D583 in 0x0047D160, gather "
                       "admission 0x0047D0EC in ListGroundForCollision 0x0047CD60); neither the "
                       "stager nor GROUND_FUNNEL_CHAIN.md names a code address",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/ground-funnel-chain-v1.json; decisions and distance "
                        "words are captured, the COL index is resolved from a captured pointer",
        covers="near set and admission composed, entry by entry",
        inputs=("COL table", "reference[3]", "query[4]"),
        outputs=("accepted", "distance_bits", "walk order"),
        blocker=B_KERNEL.format(fns="0x0047D160 and 0x0047CD60, neither lifted"),
        tail="each call row is 24 bytes plus 8 per decision entry"),
    "SA2GGO1": Format(
        magic="SA2GGO1", stager="stage_precise_ground_gather_output_fixture.py",
        header_fmt="<6I", header_fields=("version", "cols", "ticks", "observations",
                                         "attribute_mask", "reach_bits"),
        sections=(Section("cols", "cols", ("attribute", "centre[3]", "radius"), fmt="<5I"),
                  Section("ticks", "ticks", ("tick", "reference[3]"), fmt="<4I")),
        custom=walk_ggo1, rows_field="observations", functions=("0x0047CD60",),
        function_basis="GROUND_FULL_CHAIN.md:15 (gather output 0x0047D641 inside "
                       "ListGroundForCollision 0x0047CD60)",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/ground-gather-output-v1.json; the observed lists "
                        "are captured, their COL indices are resolved from captured pointers",
        covers="the list the gather writes, in table order",
        inputs=("COL table", "reference[3]", "candidate queries"),
        outputs=("observed COL indices",),
        blocker=B_WINDOW.format(window="0x0047D641",
                                fn="0x0047CD60 ListGroundForCollision (1009 bytes, 6 callees)"),
        tail="each observation row is 12 bytes plus 16 per query plus 2 per observed index"),
    "SA2GFL1": Format(
        magic="SA2GFL1", stager="stage_precise_ground_full_chain_fixture.py",
        header_fmt="<6I", header_fields=("version", "cols", "ticks", "attribute_mask",
                                         "reach_bits", "relocation_delta"),
        sections=(Section("cols", "cols", ("attribute", "centre[3]", "radius", "object_va"),
                          fmt="<6I"),),
        custom=walk_gfl1, rows_field="ticks",
        functions=("0x00461F10", "0x0047CD60", "0x0047D160"),
        function_basis="GROUND_FULL_CHAIN.md:12-16 (gate 0x0047D553, near set 0x0047D583, admission "
                       "0x0047D0EC, gather output 0x0047D641, ordering 0x004622EE); the expected "
                       "words are the ordering window's, inside GroundCollisionStep 0x00461F10",
        authority="directly-traced+predecessor-computed",
        authority_basis="Documentation/native-evidence-receipts/ground-full-chain-v1.json and "
                        "slope-full-chain-v1.json; the reference points, queries, prior words, "
                        "cursor and ordered list are captured, the relocation delta is solved for",
        covers="COL table to the ordered contact list",
        inputs=("COL table", "reference[3]", "query[4]", "prior_task", "prior_object"),
        outputs=("ordered COL indices", "cursor"),
        blocker=B_KERNEL.format(fns="0x0047D160, 0x0047CD60 and 0x00461F10, none lifted"),
        tail="each tick row is 48 bytes plus 2 per ordered index, padded to a word"),
    "SA2GST1": Format(
        magic="SA2GST1", stager="stage_precise_ground_shadow_table_fixture.py",
        header_fmt="<4I6f", header_fields=("version", "cols", "attribute_mask", "reach_bits",
                                           "low_x", "low_y", "low_z", "high_x", "high_y", "high_z"),
        sections=(Section("cols", "cols", ("attribute", "centre[3]", "radius"), fmt="<5I"),),
        rows_field="cols", functions=("0x0047D160",),
        function_basis="no code address is named by the stager, GROUND_FUNNEL_SHADOW.md or "
                       "GROUND_FUNNEL_SHADOW_RUNNER.md; the funnel it feeds is the SA2GFC1 window",
        authority=SHIPPED,
        authority_basis="Documentation/native-evidence-receipts/ground-funnel-shadow-v1.json: \"Nothing here is "
                        "directly traced, because nothing here is compared against the game\"",
        covers="shipped COL table as a shadow-sweep input",
        inputs=("COL table", "attribute_mask", "reach_bits"), outputs=(),
        covers_function=False,
        blocker="carries no expected words at all (the stager says so); it is a runtime input table, "
                "not a function fixture",
        notes="registered nowhere: inputs only"),

    # ---------------------------------------------------------------- response and selection
    "SA2GRC1": Format(
        magic="SA2GRC1", stager="stage_precise_rejected_contact_fixture.py",
        header_fmt="<3I", header_fields=("version", "cases", "case_words"),
        sections=(Section("cases", "cases", ("tick", "index", "attribute", "accumulator",
                                             "entity_w0..7", "player words", "work_w0..32",
                                             "contact point[3]", "offset[3]", "expected entity 5..7",
                                             "expected work 1..3", "expected player 28..30",
                                             "expected return", "reserved[7]"),
                          words_field="case_words"),),
        rows_field="cases", functions=("0x00463EE0",),
        function_basis="stager docstring (SA2_RejectedContactResponse 0x00463EE0, return store at "
                       "0x0046367A); Documentation/native-evidence-receipts/collision-response-v1.json",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-rejected-contact-v1.json; every row word is "
                        "captured, only the seven trailing pad words are the stager's",
        covers="response run for an acceptance-rejected precise hit",
        inputs=("entity/player/work state", "contact point[3]", "offset[3]"),
        outputs=("expected entity 5..7", "expected work 1..3", "expected player 28..30",
                 "expected return"),
        blocker=B_TEN.format(fn="0x00463EE0 SA2_RejectedContactResponse (1483 bytes, 8 callees, "
                                "among them 0x004469E0 and 0x00466910)")),
    "SA2SLC1": Format(
        magic="SA2SLC1", stager="stage_precise_selection_corpus_fixture.py",
        header_fmt="<3I", header_fields=("version", "cases", "total_records"),
        sections=(), custom=walk_selection, rows_field="cases", functions=("0x0048CE40",),
        function_basis="PRECISE_CONTACT_SELECTION.md:26,329 (selection loop "
                       "0x0048DBFB..0x0048DC33, probe at 0x0048DC76, inside CL_ColPolCheckTouchRe "
                       "0x0048CE40)",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-selection-corpus-v1.json; the index is the "
                        "one the game put in the CollisionWork and the records are what it chose from",
        covers="compact-contact selection decision",
        inputs=("count", "record_w0..w21 per record"), outputs=("selected",),
        blocker=B_WINDOW.format(window="0x0048DBFB..0x0048DC33",
                                fn="0x0048CE40 CL_ColPolCheckTouchRe (4015 bytes, 14 callees)"),
        tail="each row is 12 bytes plus 88 per record; the header's total_records checks the walk"),
    "SA2SLO1": Format(
        magic="SA2SLO1", stager="stage_precise_selection_output_fixture.py",
        header_fmt="<3I", header_fields=("version", "cases", "total_records"),
        sections=(), custom=walk_selection, rows_field="cases", functions=("0x0048CE40",),
        function_basis="PRECISE_CONTACT_SELECTION.md:239-248 (loop 0x0048DBFB..0x0048DC33 with exits "
                       "0x0048DD0B and 0x0048DDE4, inside CL_ColPolCheckTouchRe 0x0048CE40)",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-selection-output-v1.json; the twenty words "
                        "are the ones the game left in the CollisionWork",
        covers="selection decision and CollisionWork aggregation",
        inputs=("count", "record_w0..w21 per record"),
        outputs=("selected", "exit_site", "csts_w13..w32"),
        blocker=B_WINDOW.format(window="0x0048DBFB..0x0048DDE4",
                                fn="0x0048CE40 CL_ColPolCheckTouchRe (4015 bytes, 14 callees)"),
        tail="each row is 96 bytes plus 88 per record"),

    # ---------------------------------------------------------------- PResetPosition family
    "SA2PRS1": Format(
        magic="SA2PRS1", stager="stage_precise_preset_position_fixture.py",
        header_fmt="<8I", header_fields=("version", "table_words", "cases", "case_words",
                                         "eps_x", "eps_z", "ground_scale_lo", "ground_scale_hi"),
        sections=(Section("table", "table_words", ("quarter-wave word",), fmt="<I"),
                  Section("cases", "cases", ("sequence", "onframe_tick", "callsite",
                                             "pre_entity_w1..w4", "pre_world_w0..2",
                                             "pre_player_w34/w35", "pre_player_w31..33",
                                             "gravity[3]", "pre_player_w63", "post_player_w25..27",
                                             "post_player_w13"), words_field="case_words")),
        rows_field="cases", functions=("0x00469050", "0x00468DF0", "0x00468EF0"),
        function_basis="PRECISE_PRESET_POSITION_KERNEL.md:6 (PResetPosition 0x00469050); the "
                       "ordinary transform 0x00468DF0 at 0x0046909B and the Stairs transform "
                       "0x00468EF0 at 0x0046908D",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-preset-position-v1.json; all 23 words are "
                        "read from a pinned native capture required to repeat byte for byte",
        covers="ordinary PResetPosition world-to-local velocity and Up",
        inputs=("pre_entity_w1..w4", "pre_world_w0..2", "pre_player words", "gravity[3]"),
        outputs=("post_player_w25..27", "post_player_w13"),
        blocker=B_TEN.format(fn="0x00469050 PResetPos (585 bytes, 3 callees)"),
        notes="the four unnamed header words are 0.01f, 0.001f and the halves of double 0.9"),
    "SA2PRW1": Format(
        magic="SA2PRW1", stager="stage_precise_preset_position_corpus_fixture.py",
        header_fmt="<4I", header_fields=("version", "table_words", "cases", "case_words"),
        sections=(Section("table", "table_words", ("quarter-wave word",), fmt="<I"),
                  Section("cases", "cases", ("sequence", "onframe_tick", "callsite",
                                             "pre_entity_w1..w4", "pre_world_w0..2",
                                             "pre_player_w34/w35", "pre_player_w31..33",
                                             "gravity[3]", "pre_player_w63", "selector",
                                             "post_player_w25..27", "post_player_w13"),
                          words_field="case_words")),
        rows_field="cases", functions=("0x00469050", "0x00468DF0", "0x00468EF0"),
        function_basis="PRECISE_PRESET_POSITION_KERNEL.md:6 (PResetPosition 0x00469050); call sites "
                       "0x717CC5, 0x717DD6, 0x717FB1, 0x718590, 0x718D73 travel with each row",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-preset-position-corpus-v1.json; every "
                        "expected word is one the routine left in the player block",
        covers="wide PResetPosition corpus of native calls",
        inputs=("pre_entity_w1..w4", "pre_world_w0..2", "pre_player words", "gravity[3]", "selector"),
        outputs=("post_player_w25..27", "post_player_w13"),
        blocker=B_TEN.format(fn="0x00469050 PResetPos (585 bytes, 3 callees)"),
        notes="one stager, three files: the wide, jump and stairs capture profiles"),
    "SA2STF1": Format(
        magic="SA2STF1", stager="stage_precise_stairs_transform_fixture.py",
        header_fmt="<4I", header_fields=("version", "table_words", "cases", "case_words"),
        sections=(Section("table", "table_words", ("quarter-wave word",), fmt="<I"),
                  Section("cases", "cases", ("dir[3]", "angle", "in[3]", "out[3]"),
                          words_field="case_words")),
        rows_field="cases",
        functions=("0x00468EF0", "0x00459260", "0x0044B210", "0x004275B0", "0x004274E0",
                   "0x00427680", "0x004273B0"),
        function_basis="Documentation/native-evidence-receipts/precise-stairs-transform-v1.json and "
                       "PRECISE_PRESET_POSITION_KERNEL.md:506,529-545 (0x00468EF0, single caller "
                       "0x0046908D; its six callees in order)",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-stairs-transform-v1.json: \"checked word "
                        "for word against a capture of the same function in the same build\"; the "
                        "inputs are a predecessor-chosen sweep driven through the native routine",
        covers="Stairs-branch matrix-stack direction transform",
        inputs=("dir[3]", "angle", "in[3]"), outputs=("out[3]",),
        blocker=B_TEN.format(fn="0x00468EF0 (173 bytes, 6 callees)")
                + "; two of its six callees (0x0044B210, 0x004273B0) are lifted, and "
                + B_ORACLE),
    "SA2V2A1": Format(
        magic="SA2V2A1", stager="stage_precise_vector_to_angles_fixture.py",
        header_fmt="<3I", header_fields=("version", "cases", "case_words"),
        sections=(Section("cases", "cases", ("first_word", "second_word", "third_word",
                                             "first_angle", "second_angle"), words_field="case_words"),),
        rows_field="cases", functions=("0x00459260",),
        function_basis="stager docstring (\"the oracle drove the real 0x00459260 with 5305 chosen "
                       "inputs\"); check_sa2_vector_to_angles_shape.py FUNCTION = 0x00459260",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/precise-vector-to-angles-v1.json: the angles are "
                        "captured from the routine in the same build; the inputs are a chosen sweep",
        covers="direction vector to two BAM angles",
        inputs=("first_word", "second_word", "third_word"),
        outputs=("first_angle", "second_angle"),
        blocker=B_ORACLE + "; it calls the CRT asin 0x0086BC80 and atan2 0x00859B6A through the "
                           "truncation helper 0x008287C0"),

    # ---------------------------------------------------------------- shipped level data
    "SA2MHC1": Format(
        magic="SA2MHC1", stager="stage_metal_harbor_collision_geometry.py",
        header_fmt="<5I", header_fields=("version", "records", "objects_total", "vertices_total",
                                         "triangles_total"),
        sections=(), custom=walk_mhc1, rows_field="records", functions=(),
        function_basis="no function address is named; the only address is the Data DLL COL array "
                       "VA 0x11843AC8",
        authority=SHIPPED,
        authority_basis="Documentation/native-evidence-receipts/metal-harbor-collision-geometry-v1.json: "
                        "\"Shipped-data-derived. Every byte is extracted from shipped files\"",
        covers="Metal Harbor's 322 gate-passing collision records",
        inputs=(), outputs=(), covers_function=False,
        blocker="shipped level geometry, not a function's inputs and outputs",
        notes="registered nowhere: level data"),
    "SA2GMS1": Format(
        magic="SA2GMS1", stager="stage_metal_harbor_source_objects_fixture.py",
        header_fmt="<8I", header_fields=("version", "table_words", "objects", "vertices_total",
                                         "strips_total", "polygon_words_total",
                                         "source_indices_total", "records_total"),
        sections=(), custom=walk_gms1, rows_field="objects", functions=(),
        function_basis="no function address is named; data VAs only (COL array 0x11843AC8, "
                       "relocation delta 0x659D0000)",
        authority=SHIPPED,
        authority_basis="Documentation/native-evidence-receipts/ground-live-gather-v1.json",
        covers="shipped source data for 322 Metal Harbor collision objects",
        inputs=(), outputs=(), covers_function=False,
        blocker="shipped level geometry, not a function's inputs and outputs",
        notes="registered nowhere: level data"),
    "SA2GMC1": Format(
        magic="SA2GMC1", stager="stage_ground_missing_cache_composition_fixtures.py",
        header_fmt="<8I", header_fields=("version", "table_words", "objects", "vertices_total",
                                         "strips_total", "polygon_words_total",
                                         "source_indices_total", "records_total"),
        sections=(), custom=walk_gmc, rows_field="objects", functions=(),
        function_basis="no function address is named; data VAs only",
        authority=SHIPPED,
        authority_basis="Documentation/native-evidence-receipts/ground-missing-cache-composition-v1.json: \"The "
                        "fixture contains no cache or selection answers\"",
        covers="two shipped Metal Harbor collision objects as pointer-free source",
        inputs=(), outputs=(), covers_function=False,
        blocker="shipped level geometry, not a function's inputs and outputs",
        notes="registered nowhere: level data"),
    "SA2GMC2": Format(
        magic="SA2GMC2", stager="stage_ground_all_source_cache_fixtures.py",
        header_fmt="<8I", header_fields=("version", "table_words", "objects", "vertices_total",
                                         "strips_total", "polygon_words_total",
                                         "source_indices_total", "records_total"),
        sections=(), custom=walk_gmc, rows_field="objects", functions=(),
        function_basis="no function address is named; data VAs only",
        authority=SHIPPED,
        authority_basis="Documentation/native-evidence-receipts/ground-all-source-cache-schedule-v1.json",
        covers="three shipped Metal Harbor collision objects as pointer-free source",
        inputs=(), outputs=(), covers_function=False,
        blocker="shipped level geometry, not a function's inputs and outputs",
        notes="registered nowhere: level data"),
    "SA2GMQ1": Format(
        magic="SA2GMQ1", stager="stage_ground_missing_cache_composition_fixtures.py",
        header_fmt="<2I", header_fields=("version", "cases"),
        sections=(Section("cases", "cases", ("sequence", "tick", "input_w0..w6"), fmt="<9I"),),
        rows_field="cases", functions=("0x0048BAF0",),
        function_basis="the capturing probe sa2_native_ground_precise_input_probe.c wraps the "
                       "selector call site 0x004620C5 whose target is CL_ColPolListUpNear 0x0048BAF0",
        authority=DIRECT,
        authority_basis="Documentation/native-evidence-receipts/ground-missing-cache-composition-v1.json; the seven "
                        "input words are captured, and the receipt records "
                        "\"expected_selected_ordinals_embedded\": false",
        covers="one native Ground selector query's seven input words",
        inputs=("input_w0..w6",), outputs=(), covers_function=False,
        blocker="inputs only: the native result is asserted by the stager but not stored, so there "
                "is nothing to compare a lifted run against",
        notes="registered nowhere: no expected words"),
}

# Magics in the corpus that this module does not decode, with the reason. Empty: every
# magic the corpus carries has a layout the stagers pin down.
UNDECODED: dict[str, str] = {}

# Per-file corrections where one magic has more than one writer or meaning.
FILE_NOTES: dict[str, dict[str, str]] = {
    "ground-executor-slope-v1": {
        "stager": "stage_precise_ground_executor_slope_fixture.py",
        "notes": "the slope stager imports MAGIC, VERSION, TOPOLOGY_WORDS, CASE_WORDS and "
                 "compact_state from the candidate-executor stager, so the schema is identical and "
                 "only the window constants differ; topology word 3 is a `kind`, not the flat "
                 "`ordinary` flag"},
    "ground-tail-slope-v1": {
        "stager": "stage_precise_ground_tail_slope_fixture.py",
        "notes": "same SA2GTR5 schema as ground-tail-final-v5.bin, staged from the slope capture pair"},
    "ground-contact-expectations-slope-v1": {
        "stager": "stage_precise_ground_contact_expectations_fixture.py --window slope",
        "notes": "the same SA2GPE3 schema as the all-source file, staged directly from the "
                 "hash-pinned slope calls capture rather than by reduction"},
    "ground-near-set-v4": {
        "notes": "the widest fixture in the corpus: 641 424 captured selection decisions"},
    "preset-position-jump-v1": {"notes": "the preset-jump-v1 capture profile"},
    "preset-position-stairs-v1": {
        "notes": "the preset-obj-l capture profile; 17 of its rows take the Stairs arm at 0x00468EF0"},
    "precise-ground-query-seed-v1": {
        "notes": "the flat window of the query-seed stager; the only fixture outside Precise/"},
}


def rows_for_harness(magic: str, data: bytes) -> tuple[int, int, int, list[list[int]], list[list[int]]]:
    """Rows of a fixture whose function the S1 spike harness can already drive.

    Returns (va, in_words, out_words, inputs, expected) in the harness's vector shape
    (tools/spike/fixtures/shapes.h). Raises KeyError for every other magic.
    """
    if magic == "SA2NPT1":
        _version, rows, _vertices, _mwords = struct.unpack_from("<4I", data, 8)
        matrix = list(struct.unpack_from("<12I", data, 24))
        ins, exp, off = [], [], 72
        for _ in range(rows):
            r = struct.unpack_from("<8I", data, off)
            off += 32
            ins.append(matrix + [r[2], r[3], r[4], 0])
            exp.append([r[5], r[6], r[7]])
        return 0x00426CC0, 16, 3, ins, exp
    if magic == "SA2TXC1":
        _version, calls, _accepts, cw, _aw = struct.unpack_from("<5I", data, 8)
        ins, exp, off = [], [], 28
        for _ in range(calls):
            r = struct.unpack_from(f"<{cw}I", data, off)
            off += cw * 4
            ins.append(list(r[4:16]) + list(r[1:4]) + [0])
            exp.append(list(r[16:19]))
        return 0x00426CC0, 16, 3, ins, exp
    if magic == "SA2NOM1":
        _version, table, cases, _pw, _tw, _rw = struct.unpack_from("<6I", data, 8)
        ins, exp, off = [], [], 32 + table * 4
        for _ in range(cases):
            r = struct.unpack_from("<33I", data, off)
            off += 132
            ins.append(list(r[:21]))
            exp.append(list(r[21:]))
        return 0x0048B9F0, 21, 12, ins, exp
    raise KeyError(magic)


def main() -> None:
    fm = FunctionMap()
    for p in corpus_files():
        d = decode(p)
        state = "decoded" if d.fmt is not None and d.error is None else "undecoded"
        note = d.error or ""
        fns = ",".join(d.fmt.functions) if d.fmt else ""
        print(f"{p.name:50s} {d.magic} {d.file_bytes:>9d} {state:9s} rows={d.rows:<7d} {fns} {note}")
    print(f"{len(corpus_files())} files, {len(FORMATS)} formats, {len(UNDECODED)} undecoded magics; "
          f"function map {'available' if fm.available else 'absent'}")


if __name__ == "__main__":
    main()
