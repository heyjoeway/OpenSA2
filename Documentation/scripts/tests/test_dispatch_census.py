"""Split routines and the refused-edge census (issue #16).

FUN_007059a0 is `push ecx; mov ecx,[1AF00CCh]; xor eax,eax; test ecx,ecx; jne 007059AFh; pop ecx;
ret` and 0x007059af, its body, is the next function-map entry, so the `jne` leaves the head's
lifted range. The edge table refused it (jcc-taken, no exception) and nothing recorded that it
did: Pyramid Cave loads DOOR and SANDGLASS, the branch is taken on every load, and the game
stopped after the stage's title card (exit 1, `control left the lifted code at 0x007059af`).

These tests hold the two halves of the fix: the edge itself, decoded from the executable over
the lift log's ranges, dispatches by a reviewed exception from Documentation/dispatch-exceptions.json
(as do Death Chamber's twin, Menu_Tutorial's page advance and the CRT's x87 domain handler); and
check_dispatch_exceptions.py names every edge the generated table still refuses that no record
explains, which is the check that would have caught #16 before a player did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_dispatch_exceptions as CD  # noqa: E402
import fp_mask_census as FP  # noqa: E402
import mb_edges as MB  # noqa: E402

TAG = "next+allarms"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
LIFT = ROOT / "private" / "generated" / "s3" / "whole_image" / "lift" / "next.jsonl"

# head, body (the next map entry), the branch or fall-through between them, its kind
SPLIT = [
    (0x007059A0, 0x007059AF, 0x007059AB, MB.EDGE_JCC),   # Pyramid Cave (issue #16)
    (0x006B1650, 0x006B165F, 0x006B165B, MB.EDGE_JCC),   # Death Chamber
    (0x00674230, 0x00674257, 0x0067424E, MB.EDGE_JCC),   # Menu_Tutorial's page advance
    (0x0082C176, 0x0082C189, 0x0082C187, MB.EDGE_FALL),  # the x87 domain handler
]


def image_view() -> bytes:
    """The executable mapped by section, as mb_edges.main does."""
    import pefile  # noqa: PLC0415
    pe = pefile.PE(str(EXE), fast_load=True)
    view = bytearray(0x02375000)
    for s in pe.sections:
        raw = s.get_data()
        view[s.VirtualAddress:s.VirtualAddress + len(raw)] = raw
    return bytes(view)


def lift_ranges(vas: set[int]) -> dict[int, tuple[int, int]]:
    ranges = {}
    for line in LIFT.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            va = int(d["va"], 16)
            if va in vas and d.get("lifted"):
                ranges[va] = (va, int(d["end"], 16))
    return ranges


@pytest.mark.skipif(not (EXE.is_file() and LIFT.is_file()),
                    reason="needs the private unpacked executable and the whole-image lift log")
def test_each_split_routines_inner_edge_dispatches_by_a_reviewed_exception():
    """The heads' modules end at their bodies, so the edge is refused by kind; the tracked record
    is what lets it dispatch, and the record must name it."""
    ranges = lift_ranges({h for h, *_ in SPLIT} | {b for _, b, *_ in SPLIT})
    for head, body, _, _ in SPLIT:
        assert ranges[head] == (head, body), f"{head:#010x}'s lifted range no longer ends at {body:#010x}"
        assert ranges[body][0] == body
    edges, _ = MB.decode_edges(image_view(), ranges)
    rows = {e["target"]: e for e in edges}
    assert MB.apply_allow(edges, MB.reviewed_allow(MB.REVIEWED, TAG)) == len(SPLIT)
    for head, body, source, kind in SPLIT:
        e = rows[body]
        assert (e["source"], e["kind"], e["module"]) == (source, kind, f"0x{head:08x}")
        assert e.get("allow") == 1 and e["why"].startswith(f"0x{body:08x} is FUN_{body:08x}")


def record(tmp_path: Path, refused: list[dict]) -> Path:
    p = tmp_path / "exceptions.json"
    p.write_text(json.dumps({"schema": "sa2-recomp-dispatch-exceptions/v2", "exceptions": [],
                             "refused": refused}))
    return p


def generated(tmp_path: Path, refused: list[dict]) -> Path:
    gen = tmp_path / "gen"
    gen.mkdir()
    (gen / f"mb_edges.{TAG}.json").write_text(json.dumps(
        {"reviewed_exceptions": {}, "named_exception_targets": [], "refused": refused}))
    return gen


WHY = "Reached only through a path this test invents, so it is left trapping on purpose."
PYRAMID = {"target": "0x007059af", "source": "0x007059ab", "kind": "jcc-taken"}
CRT = {"target": "0x007a8648", "source": "0x007a8770", "kind": "jcc-taken"}


def test_a_trapping_edge_no_record_names_fails_by_its_address(tmp_path):
    """The state issue #16 shipped in: the table refuses 0x007059af and no record says why."""
    decl = record(tmp_path, [{"tag": TAG, "group": "g", "why": WHY,
                              "rows": [{"va": "0x007a8648", "source": "0x007a8770",
                                        "kind": "jcc-taken"}]}])
    assert CD.problems(decl, generated(tmp_path, [PYRAMID, CRT])) == [
        "0x007059af traps if reached (jcc-taken from 0x007059ab) and exceptions.json names it in "
        "no exception and no refused group for tag next+allarms: decide which, and record the "
        "reason"]


def test_every_refused_row_named_with_its_edge_passes(tmp_path):
    decl = record(tmp_path, [{"tag": TAG, "group": "g", "why": WHY, "rows": [
        {"va": "0x007a8648", "source": "0x007a8770", "kind": "jcc-taken"},
        {"va": "0x007059af", "source": "0x007059ab", "kind": "jcc-taken"}]}])
    assert CD.problems(decl, generated(tmp_path, [PYRAMID, CRT])) == []


def test_a_named_row_the_table_no_longer_refuses_or_reaches_differently_is_named(tmp_path):
    decl = record(tmp_path, [{"tag": TAG, "group": "g", "why": WHY, "rows": [
        {"va": "0x007a8648", "source": "0x007a8770", "kind": "fall-through"},
        {"va": "0x007059af", "source": "0x007059ab", "kind": "jcc-taken"}]}])
    assert CD.problems(decl, generated(tmp_path, [CRT])) == [
        "0x007059af from 0x007059ab is refused for tag next+allarms in exceptions.json but the "
        "generated table does not refuse that edge (it dispatches now, or no longer reaches it "
        "from there): drop it from `refused`",
        "0x007a8648 from 0x007a8770 is refused as fall-through, and the generated table reaches "
        "it from there by jcc-taken: the edge changed, so its reason needs a fresh look"]


MODF_SSE2 = {"target": "0x0082ccc5", "source": "0x008294a7", "kind": "jcc-taken"}
MODF_MASKS = {"target": "0x0082ccc5", "source": "0x008294d8", "kind": "jcc-taken"}


def test_a_second_source_into_a_named_target_fails_by_its_source(tmp_path):
    """The census was per target, so 0x008294d8's `jne` into __modf_default (an FP-exception-
    mask test) hid behind 0x008294a7's named `je` into the same address. The runtime looks the
    row up by target, so each source traps and each needs its own reason."""
    decl = record(tmp_path, [{"tag": TAG, "group": "g", "why": WHY, "rows": [
        {"va": "0x0082ccc5", "source": "0x008294a7", "kind": "jcc-taken"}]}])
    assert CD.problems(decl, generated(tmp_path, [MODF_SSE2, MODF_MASKS])) == [
        "0x0082ccc5 traps if reached (jcc-taken from 0x008294d8) and exceptions.json names it in "
        "no exception and no refused group for tag next+allarms: decide which, and record the "
        "reason"]
    both = record(tmp_path, [{"tag": TAG, "group": "g", "why": WHY, "rows": [
        {"va": "0x0082ccc5", "source": "0x008294a7", "kind": "jcc-taken"},
        {"va": "0x0082ccc5", "source": "0x008294d8", "kind": "jcc-taken"}]}])
    assert CD.problems(both, tmp_path / "gen") == []
    twice = record(tmp_path, [{"tag": TAG, "group": "g", "why": WHY, "rows": [
        {"va": "0x0082ccc5", "source": "0x008294a7", "kind": "jcc-taken"},
        {"va": "0x0082ccc5", "source": "0x008294d8", "kind": "jcc-taken"},
        {"va": "0x0082ccc5", "source": "0x008294d8", "kind": "jcc-taken"}]}])
    assert CD.problems(twice, tmp_path / "gen") == [
        "0x0082ccc5 from 0x008294d8 is refused twice for tag 'next+allarms'"]


@pytest.mark.skipif(not (EXE.is_file() and LIFT.is_file()),
                    reason="needs the private unpacked executable and the whole-image lift log")
def test_the_refused_census_lists_every_source_into_a_refused_target():
    """decode_edges keeps one runtime row per target and, beside it, every distinct source; the
    refused list mb_edges.py writes is built from those, one row per edge."""
    ranges = lift_ranges({0x008294A0, 0x0082CCC5})
    edges, _ = MB.decode_edges(image_view(), ranges)
    row = next(e for e in edges if e["target"] == 0x0082CCC5)
    assert (row["source"], row["kind"]) == (0x008294A7, MB.EDGE_JCC)
    assert [(s["source"], s["kind_name"], s["insn"]) for s in row["sources"]] == [
        (0x008294A7, "jcc-taken", "je 0082CCC5h"), (0x008294D8, "jcc-taken", "jne 0082CCC5h")]


def test_the_fp_mask_census_tells_a_kept_mask_from_a_cleared_one():
    """The census's symbolic walk over the compiler's float-to-int sequence (`fnstcw; mov ax;
    or ah,0Ch; mov; fldcw`) keeps the saved masks; an `and` that clears them, or a constant
    with a mask bit clear, is named."""
    from iced_x86 import Decoder  # noqa: PLC0415

    def cls(code: bytes) -> str:
        ins = list(Decoder(32, code, ip=0x00401000))
        return FP.word_class(b"", ins, len(ins) - 1, FP.X87_MASKS)

    ftol = bytes.fromhex("d93c24" "668b0424" "80cc0c" "6689442402" "d96c2402")
    assert cls(ftol) == FP.SAVED
    cleared = bytes.fromhex("d93c24" "668b0424" "6625c0ff" "6689442402" "d96c2402")
    assert cls(cleared) == "unknown"
    assert cls(bytes.fromhex("66c70424" "7213" "d92c24")) == "constant 0x1372 UNMASKS"
    assert cls(bytes.fromhex("66c70424" "7f02" "d92c24")) == "constant, masks set"


@pytest.mark.skipif(not (EXE.is_file() and LIFT.is_file()),
                    reason="needs the private unpacked executable and the whole-image lift log")
def test_no_lifted_code_unmasks_an_fp_exception_and_a_dropped_review_fails_by_address(
        monkeypatch):
    """Why 0x0082ccc5 from 0x008294d8 may stay refused: no lifted site loads a word that clears
    an exception mask. A hand-reviewed site taken off the list must fail by its address."""
    counts, failures, stale = FP.census()
    assert (failures, stale) == ([], set())
    assert counts["reviewed"] == len(FP.REVIEWED)
    monkeypatch.setattr(FP, "REVIEWED", {k: v for k, v in FP.REVIEWED.items()
                                         if k != 0x0082CCE4})
    _, failures, _ = FP.census()
    assert [f.split(":")[0] for f in failures] == ["0x0082cce4 in module 0x0082ccc5"]


def test_a_table_without_the_refused_list_must_be_regenerated(tmp_path):
    decl = record(tmp_path, [])
    gen = tmp_path / "gen"
    gen.mkdir()
    (gen / f"mb_edges.{TAG}.json").write_text(json.dumps(
        {"reviewed_exceptions": {}, "named_exception_targets": []}))
    assert CD.problems(decl, gen) == [
        "the generated table for tag next+allarms lists no `refused` rows: it was written by an "
        "mb_edges.py older than the record's refused groups; regenerate it"]


def test_a_stale_table_is_refused_by_the_link_with_the_exceptions_it_lacks(tmp_path):
    """release_build.py and win_link.py call not_in_force before linking: a tree that tracks the
    #16 exception must not link a table generated before it."""
    c = tmp_path / f"mb_edges.{TAG}.c"
    c.write_text("/* a table */\n")
    j = c.with_suffix(".json")
    reviewed = tmp_path / "exceptions.json"
    reviewed.write_text(json.dumps({"schema": "sa2-recomp-dispatch-exceptions/v2", "exceptions": [
        {"va": "0x007059af", "tag": TAG, "why": "the body of FUN_007059a0, reviewed"}]}))
    doc = {"tag": TAG, "lift_tag": "next", "arms_tag": "next", "allow_from": None,
           "named_exception_targets": [], "output_c_sha256": MB.C.sha256(c)}
    j.write_text(json.dumps(doc))
    assert MB.not_in_force(c, TAG, reviewed) == [
        f"0x007059af is a reviewed dispatch exception in exceptions.json that {c} does not allow: "
        "the table predates it. Regenerate it: .venv/bin/python tools/s4/mb_edges.py --tag "
        "next+allarms --lift-tag next --arms-tag next"]
    j.write_text(json.dumps(dict(doc, named_exception_targets=["0x007059af"])))
    assert MB.not_in_force(c, TAG, reviewed) == []
    j.write_text(json.dumps(dict(doc, output_c_sha256="0" * 64)))
    assert MB.not_in_force(c, TAG, reviewed) == [
        f"{j} describes another {c.name} (its output_c_sha256 is not this file's); regenerate "
        "both with tools/s4/mb_edges.py"]
