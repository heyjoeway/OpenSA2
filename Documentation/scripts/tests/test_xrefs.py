#!/usr/bin/env python3
"""`tools/s4/xrefs.py` and `tools/s4/xref_census.py` against a hand-built PE.

Each planted case is one way the recorded-size bound lost or doubled a
reference on the shipped image: a call past a function's recorded size, an
instruction inside two overlapping recorded extents, a call after data, and a
conditional jump into an import thunk, which the S1 census does not count.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "tools" / "s4"
sys.path.insert(0, str(TOOLS))

import image as IM  # noqa: E402
import xref_census as XC  # noqa: E402
import xrefs as X  # noqa: E402

BASE = 0x400000
TEXT_RVA = 0x1000
CODE = BASE + TEXT_RVA
THUNK = CODE + 0x40              # jmp [Sleep]
IAT_SLEEP = CODE + 0x140

SITE_BEYOND = CODE + 0x03        # past F1's recorded size of 3
SITE_OVERLAP = CODE + 0x12       # inside F2's recorded extent and at F3's entry
SITE_AFTER_DATA = CODE + 0x28    # after two bytes of data in F4's range
RESUMED_AT = CODE + 0x22         # where the decode picks up after that data
SITE_JCC = CODE + 0x32           # je into the thunk


def rel32(src_end: int, dst: int) -> bytes:
    return struct.pack("<i", dst - src_end)


def text_section() -> bytes:
    t = bytearray(b"\xcc" * 0x200)
    t[0x00:0x09] = b"\x90\xeb\x00" + b"\xe8" + rel32(CODE + 0x08, THUNK) + b"\xc3"
    t[0x10:0x18] = b"\x90\x90" + b"\xe8" + rel32(CODE + 0x17, THUNK) + b"\xc3"
    # ret, then FE FF (FE /7 does not exist), then padding and the call.
    t[0x20:0x2E] = b"\xc3\xfe\xff\xcc\xcc\xcc\xcc\xcc" + b"\xe8" + rel32(CODE + 0x2D, THUNK) + b"\xc3"
    t[0x30:0x35] = b"\x85\xc0" + b"\x74" + struct.pack("<b", THUNK - (CODE + 0x34)) + b"\xc3"
    t[0x40:0x46] = b"\xff\x25" + struct.pack("<I", IAT_SLEEP)
    # import directory at RVA 0x1100: one descriptor and a null one, ILT 0x1130,
    # IAT 0x1140, the hint/name, the DLL name.
    t[0x100:0x128] = bytes(0x28)
    t[0x100:0x114] = struct.pack("<IIIII", TEXT_RVA + 0x130, 0, 0, TEXT_RVA + 0x180, TEXT_RVA + 0x140)
    for off in (0x130, 0x140):
        t[off:off + 8] = struct.pack("<II", TEXT_RVA + 0x150, 0)
    t[0x150:0x150 + 8] = b"\x00\x00Sleep\x00"
    t[0x180:0x180 + 13] = b"KERNEL32.dll\x00"
    return bytes(t)


def build_pe() -> bytes:
    text = text_section()
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = struct.pack("<I", 0x40)
    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 0xE0, 0x0102)
    dirs = [(0, 0)] * 16
    dirs[1] = (TEXT_RVA + 0x100, 40)
    opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII",
                      0x10B, 9, 0, len(text), 0, 0, TEXT_RVA, TEXT_RVA, TEXT_RVA, BASE, 0x1000, 0x200,
                      4, 0, 0, 0, 4, 0, 0, 0x2000, 0x200, 0, 2, 0, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", rva, size) for rva, size in dirs)
    section = struct.pack("<8sIIIIIIHHI", b".text", len(text), TEXT_RVA, len(text), 0x200, 0, 0, 0, 0, 0x60000020)
    headers = bytes(dos) + b"PE\0\0" + coff + opt + section
    assert len(headers) <= 0x200
    return headers.ljust(0x200, b"\0") + text


FUNCTION_MAP = {"functions": [
    {"address": hex(CODE + 0x00), "size": 0x03, "name": "F1"},
    {"address": hex(CODE + 0x10), "size": 0x10, "name": "F2"},
    {"address": hex(CODE + 0x12), "size": 0x06, "name": "F3"},
    {"address": hex(CODE + 0x20), "size": 0x01, "name": "F4"},
    {"address": hex(CODE + 0x30), "size": 0x05, "name": "F5"},
    {"address": hex(THUNK), "size": 0x06, "name": "thunk_Sleep"},
]}


@pytest.fixture(scope="module")
def image(tmp_path_factory: pytest.TempPathFactory) -> IM.Image:
    exe = tmp_path_factory.mktemp("xrefs") / "tiny.exe"
    exe.write_bytes(build_pe())
    return IM.Image(exe)


def calls(index: dict, target: int) -> list[tuple[int, str, int]]:
    return sorted(ref for ref in index.get(target, []) if ref[1] == "call")


def test_next_bound_finds_each_call_once_and_says_which_came_after_data(image: IM.Image) -> None:
    xref, iref, _ = X.build(image, FUNCTION_MAP)
    assert calls(xref, THUNK) == [(SITE_BEYOND, "call", CODE + 0x00),
                                  (SITE_OVERLAP, "call", CODE + 0x12),
                                  (SITE_AFTER_DATA, "call", RESUMED_AT)]
    assert [ref for ref in xref[THUNK] if ref[1] == "jcc"] == [(SITE_JCC, "jcc", CODE + 0x30)]
    assert iref == {IAT_SLEEP: [(THUNK, "jmp", THUNK)]}


def test_recorded_bound_misses_two_calls_and_counts_one_twice(image: IM.Image) -> None:
    xref, _, _ = X.build(image, FUNCTION_MAP, bound="recorded")
    assert calls(xref, THUNK) == [(SITE_OVERLAP, "call", CODE + 0x10),
                                  (SITE_OVERLAP, "call", CODE + 0x12)]


def test_census_fields_count_the_jcc_apart(image: IM.Image) -> None:
    xref, iref, _ = X.build(image, FUNCTION_MAP)
    assert XC.counts(xref, iref, IAT_SLEEP) == {
        "direct_call_sites": 0, "thunks": ["0x00401040"], "slot_pointer_loads": 0,
        "calls_to_thunks": 3, "jumps_to_thunks": 0, "thunk_pointer_loads": 0,
        "jcc_to_thunks": ["0x00401032"],
    }


def test_an_unknown_bound_is_refused(image: IM.Image) -> None:
    with pytest.raises(ValueError):
        X.build(image, FUNCTION_MAP, bound="to-the-end")


def test_compare_reports_a_census_field_the_index_disagrees_with(image: IM.Image) -> None:
    census = [{"dll": "KERNEL32.dll", "name": "Sleep", "iat_slot": f"0x{IAT_SLEEP:08X}",
               "direct_call_sites": 0, "thunks": ["0x00401040"], "slot_pointer_loads": 0,
               "calls_to_thunks": 3, "jumps_to_thunks": 0, "thunk_pointer_loads": 0}]
    agree = XC.compare(image, FUNCTION_MAP, census, "next")
    assert (agree["disagreements"], agree["conditional_jumps_to_thunks"]) == (
        0, [{"dll": "KERNEL32.dll", "import": "Sleep", "jcc_to_thunk_sites": ["0x00401032"]}])
    control = XC.compare(image, FUNCTION_MAP, json.loads(json.dumps(census)), "recorded")
    assert control["rows"] == [{"dll": "KERNEL32.dll", "import": "Sleep", "field": "calls_to_thunks",
                                "census": 3, "xrefs": 2}]
