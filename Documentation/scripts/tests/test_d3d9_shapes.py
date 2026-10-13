"""Tiny authored x86 controls for the CTAB seed and D3DX stdcall arguments.

No executable, function map, replay or device is opened. The real Code decoder,
runtime-table parser and Attribution interpreter process a small synthetic image.
"""

from __future__ import annotations

import re
import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import d3d9_methods as D  # noqa: E402
from image import Section  # noqa: E402


class AuthoredImage:
    def __init__(self, functions: dict[int, bytes], other_import: str | None):
        lo, hi = min(functions), max(a + len(b) for a, b in functions.items())
        text = bytearray(b"\xcc" * (hi - lo))
        for address, payload in functions.items():
            text[address - lo:address - lo + len(payload)] = payload
        self.code = Section(".text", lo, len(text), len(text), 0x60000000)
        self.data = Section(".data", 0x4000, 0x100, 0x100, 0xC0000000)
        self.exec_sections = [self.code]
        self._section_bytes = {".text": bytes(text)}
        self.imports = {0x4000: ("d3dx9_42.dll", "D3DXGetShaderConstantTable")}
        self.thunks = {0x2000: 0x4000}
        if other_import:
            self.imports[0x4004] = ("d3dx9_42.dll", other_import)
            self.thunks[0x2010] = 0x4004

    def section_of(self, address):
        return next((s for s in (self.code, self.data) if s.contains(address)), None)

    def in_code(self, address):
        return self.code.contains(address)

    def text(self, instruction):
        return str(instruction)


def relative_call(payload: bytearray, target: int, entry=0x1000):
    payload.extend(b"\xe8" + struct.pack("<i", target - (entry + len(payload) + 5)))


def authored_code(other_import=None, argc=0, through_function=False):
    # sub esp,4; lea eax,[esp]; push eax (out1); push 0 (bytecode0)
    main = bytearray.fromhex("83 ec 04 8d 04 24 50 6a 00")
    relative_call(main, 0x2000)
    if other_import:
        main.extend(bytes.fromhex("6a 00") * argc)
        relative_call(main, 0x2010)
    if through_function:
        # push [esp] carries the actual returned CTAB pointer into a direct callee.
        main.extend(bytes.fromhex("ff 34 24"))
        relative_call(main, 0x1100)
        main.extend(bytes.fromhex("83 c4 04 c3"))
        # mov ecx,[esp+4]; mov eax,[ecx]; push 0; push ecx;
        # call [eax+14h] (GetDesc); ret 4
        helper = bytes.fromhex("8b 4c 24 04 8b 01 6a 00 51 ff 50 14 c2 04 00")
        functions = {0x1000: bytes(main), 0x1100: helper}
    else:
        # mov esi,[esp]; mov eax,[esi]; push 0; push esi;
        # call [eax+14h] (GetDesc); add esp,4; ret
        main.extend(bytes.fromhex("8b 34 24 8b 06 6a 00 56 ff 50 14 83 c4 04 c3"))
        functions = {0x1000: bytes(main)}
    image = AuthoredImage(functions, other_import)
    fmap = SimpleNamespace(functions=[{"address": a} for a in sorted(functions)])
    return D.Code(image, fmap)


def attributed(code, seed=True, shift=0, ifaces=None):
    if ifaces is None:
        ifaces, _ = D.parse_vtables(ROOT / "runtime" / "sa2_d3d9.c")
    state = D.Attribution(code, ifaces, {"class_vt": set(), "entry_class": {}},
                          seed_d3dx=seed, disp_shift=shift)
    state.run(time.time() + 5)
    assert not state.exhausted
    return state


def expected_get_desc(code):
    calls = [i for entry in code.entries for i in code.instructions(entry)
             if i.mnemonic == D.Mnemonic.CALL and i.op0_kind == D.OpKind.MEMORY]
    assert len(calls) == 1
    call = calls[0]
    return {call.ip: {"va": call.ip, "return_va": call.ip + call.len,
                      "interfaces": ["ID3DXConstantTable"], "slot": 5,
                      "method": "GetDesc", "function": code.owner(call.ip)}}


def test_runtime_ctab_layout_is_parsed_without_changing_texture_prefix():
    ifaces, rows = D.parse_vtables(ROOT / "runtime" / "sa2_d3d9.c")
    assert rows["ID3DXConstantTable"] == "ROWS_CONSTANTTABLE"
    names = ["QueryInterface", "AddRef", "Release", "GetBufferPointer", "GetBufferSize",
             "GetDesc", "GetConstantDesc", "GetSamplerIndex", "GetConstant",
             "GetConstantByName", "GetConstantElement", "SetDefaults", "SetValue",
             "SetBool", "SetBoolArray", "SetInt", "SetIntArray", "SetFloat",
             "SetFloatArray", "SetVector", "SetVectorArray", "SetMatrix", "SetMatrixArray",
             "SetMatrixPointerArray", "SetMatrixTranspose", "SetMatrixTransposeArray",
             "SetMatrixTransposePointerArray"]
    counts = [2, 0, 0, 0, 0, 1, 3, 1, 2, 2, 2, 1, 4, 3, 4, 3, 4, 3, 4, 3, 4, 3, 4, 4, 3, 4, 4]
    assert ifaces["ID3DXConstantTable"] == {
        slot: {"name": name, "argc": argc}
        for slot, (name, argc) in enumerate(zip(names, counts, strict=True))}
    assert sorted(ifaces["IDirect3DBaseTexture9"]) == list(range(18))
    assert ifaces["IDirect3DBaseTexture9"] == {
        slot: ifaces["IDirect3DTexture9"][slot] for slot in range(18)}


@pytest.mark.parametrize("through_function", [False, True])
def test_ctab_out_argument_types_the_actual_receiver_and_direct_callee(through_function):
    code = authored_code(through_function=through_function)
    assert attributed(code).calls == expected_get_desc(code)
    assert attributed(code, seed=False).calls == {}
    # The existing displacement falsifier changes this literal slot, not its shape.
    shifted = attributed(code, shift=4).calls
    assert [(r["slot"], r["method"]) for r in shifted.values()] == [(6, "GetConstantDesc")]
    assert shifted != expected_get_desc(code)


@pytest.mark.parametrize("name,argc", [("D3DXLoadSurfaceFromSurface", 8),
                                        ("D3DXSaveSurfaceToFileA", 5)])
def test_import_stack_cleanup_preserves_the_later_ctab_receiver(name, argc, monkeypatch):
    code = authored_code(name, argc)
    shape = D.layer_a(code)
    assert shape["vtable_shaped_call_sites"] == 1
    assert shape["by_displacement"] == {"0x14": 1}
    assert attributed(code).calls == expected_get_desc(code)
    assert attributed(code, seed=False).calls == {}
    # Literal former counts leave ESP at the wrong cell, so the receiver stays unknown.
    monkeypatch.setitem(D.D3DX_SIGS, name, {"argc": argc - 1, "out": None, "type": None})
    assert attributed(code).calls == {}
    assert D.layer_a(code) == shape


def test_without_the_ctab_layout_the_receiver_is_not_given_a_method_name():
    code = authored_code()
    ifaces, _ = D.parse_vtables(ROOT / "runtime" / "sa2_d3d9.c")
    ifaces.pop("ID3DXConstantTable", None)
    assert attributed(code, ifaces=ifaces).calls == {}
    assert D.layer_a(code)["vtable_shaped_call_sites"] == 1


def test_all_eleven_d3dx_argument_counts_agree_with_runtime_and_pinned_headers():
    expected = {"D3DXCreateTexture": 8, "D3DXCreateTextureFromFileInMemoryEx": 15,
                "D3DXCreateCubeTexture": 7, "D3DXCreateCubeTextureFromFileInMemoryEx": 14,
                "D3DXCreateVolumeTexture": 9, "D3DXCreateVolumeTextureFromFileInMemoryEx": 16,
                "D3DXFilterTexture": 4, "D3DXGetShaderConstantTable": 2,
                "D3DXLoadSurfaceFromSurface": 8, "D3DXSaveSurfaceToFileA": 5,
                "D3DXSaveTextureToFileA": 4}
    runtime = (ROOT / "runtime" / "sa2_imports.c").read_text()
    rows = re.findall(r'\{"d3dx9_42\.dll",\s*"(D3DX\w+)",\s*(\d+),\s*SA2_IMP_STDCALL', runtime)
    assert len(rows) == 11
    assert {name: int(argc) for name, argc in rows} == expected
    header_dir = ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/lib/libc/include/any-windows-any"
    headers = "\n".join((header_dir / name).read_text() for name in ("d3dx9shader.h", "d3dx9tex.h"))
    declarations = dict(re.findall(r"HRESULT\s+WINAPI\s+(D3DX\w+)\s*\((.*?)\);", headers, re.S))
    assert {name: len(declarations[name].split(",")) for name in expected} == expected
    assert {name: sig["argc"] for name, sig in D.D3DX_SIGS.items()} == expected
    out = declarations["D3DXGetShaderConstantTable"].split(",")[1].strip()
    assert out == "ID3DXConstantTable **constant_table"
    assert D.D3DX_SIGS["D3DXGetShaderConstantTable"] == {
        "argc": 2, "out": 1, "type": "ID3DXConstantTable"}


# ---------------------------------------------------------------------------
# The absorbing-TOP lattice and the deadline-safe driver.
#
# The driver tests run the production `Attribution.run` over an authored work graph
# with no image, adopting the reviewed deadline suffix-retention repair
# (m7-scanner-deadline-repair-f-01) and proving a deadline can never be read as a
# fixpoint: `run` keeps the stopped round's unprocessed suffix as pending work and sets
# `stopped_by`, which `summarise().fixpoint_reached` requires to be "fixpoint".


def test_join_has_a_distinct_absorbing_top():
    # bottom (empty) is "nothing known"; TOP is "more than MAX_ATOMS things". They are
    # distinct, so an overflowed set never refills and the scan can reach a fixpoint.
    j = D.Attribution.join
    a = frozenset({("I", "IDirect3DDevice9")})
    assert j(frozenset(), a) == a and j(a, frozenset()) == a
    assert j(None, None) == frozenset()
    small = frozenset({("C", 1), ("C", 2)})
    assert j(small, frozenset({("C", 3)})) == frozenset({("C", 1), ("C", 2), ("C", 3)})
    over = j(frozenset({("C", 1), ("C", 2), ("C", 3)}), frozenset({("C", 4), ("C", 5)}))
    assert over == D.TOP
    assert over != frozenset()                      # the bug this fixes
    assert j(over, a) == D.TOP and j(a, over) == D.TOP      # TOP absorbs
    assert D.TOP != frozenset()


def driver_graph(monkeypatch, seeds, edges, readings, deadline=1):
    """Run the production driver over an authored work graph, without an image."""
    state = D.Attribution.__new__(D.Attribution)
    state.seed_functions = set(seeds)
    state.rounds = 0
    state.calls = {}
    state.conflicts = 0
    visited = []
    values = iter(readings)
    last = readings[-1]

    def clock():
        return next(values, last)

    def analyse(entry):
        visited.append(entry)
        return set(edges.get(entry, ()))

    state._analyse = analyse
    monkeypatch.setattr(D, "time", SimpleNamespace(time=clock))
    state.run(deadline)
    return state, visited, D.summarise(state, {"sites": {}})["fixpoint_reached"]


@pytest.mark.parametrize("readings,expected_visited", [([0, 2], []),
                                                       ([0, 0, 2], [10])])
def test_deadline_pending_entries_do_not_report_a_fixpoint(monkeypatch, readings,
                                                            expected_visited):
    state, visited, complete = driver_graph(monkeypatch, {30, 10, 20}, {}, readings)
    assert visited == expected_visited
    assert state.exhausted is True
    assert state.stopped_by == "deadline"
    assert complete is False


@pytest.mark.parametrize("edges,expected_visited", [({}, [10, 20, 30]),
                                                    ({10: {40}}, [10, 20, 30, 40])])
def test_deadline_keeps_every_suffix_entry_and_awakened_entry_on_clock_recovery(
        monkeypatch, edges, expected_visited):
    state, visited, complete = driver_graph(monkeypatch, {30, 10, 20}, edges,
                                            [0, 0, 2, 0, 0, 0, 0, 0])
    assert visited == expected_visited
    assert state.exhausted is False
    assert state.stopped_by == "fixpoint"
    assert complete is True


@pytest.mark.parametrize("deadline,readings", [(0, [1]), (-1, [0]), (0, [0, 1])])
def test_zero_or_expired_deadline_keeps_seed_work_incomplete(monkeypatch, deadline,
                                                             readings):
    state, visited, complete = driver_graph(monkeypatch, {10, 20}, {}, readings,
                                            deadline=deadline)
    assert visited == []
    assert state.exhausted is True
    assert complete is False


@pytest.mark.parametrize("seeds,edges,expected_visited,expected_rounds", [
    (set(), {}, [], 0),
    ({30, 10}, {10: {20}, 30: {20}, 20: {40}}, [10, 30, 20, 40], 3),
])
def test_empty_work_and_normal_transitive_propagation_complete(monkeypatch, seeds,
                                                                edges, expected_visited,
                                                                expected_rounds):
    state, visited, complete = driver_graph(monkeypatch, seeds, edges, [0])
    assert visited == expected_visited
    assert state.rounds == expected_rounds
    assert state.exhausted is False
    assert state.stopped_by == "fixpoint"
    assert complete is True


@pytest.mark.parametrize("transitive", [False, True])
def test_round_cap_leaves_pending_work_incomplete_not_a_fixpoint(monkeypatch, transitive):
    edges = {n: {n + 1} for n in range(1, 42)} if transitive else {1: {1}}
    state, visited, complete = driver_graph(monkeypatch, {1}, edges, [0])
    assert D.MAX_ROUNDS == 40
    assert (visited == list(range(1, 41))) if transitive else (visited == [1] * 40)
    assert state.rounds == 40
    assert state.exhausted is True
    assert state.stopped_by == "round_cap"
    assert complete is False


def test_deadline_keeps_transitively_awakened_work_incomplete(monkeypatch):
    state, visited, complete = driver_graph(monkeypatch, {10}, {10: {20}}, [0, 0, 2])
    assert visited == [10]
    assert state.rounds == 1
    assert state.exhausted is True
    assert state.stopped_by == "deadline"
    assert complete is False


@pytest.mark.parametrize("limit_hit,too_large", [({0x401000}, set()), (set(), {0x401000})])
def test_a_truncated_local_analysis_is_not_a_fixpoint(limit_hit, too_large):
    # The work list emptied, but one function's analysis stopped at MAX_BLOCK_VISITS or
    # was skipped for size, so its locations could still change.
    state = D.Attribution.__new__(D.Attribution)
    state.rounds, state.calls, state.conflicts = 3, {}, 0
    state.exhausted, state.stopped_by, state.pending = False, "fixpoint", []
    state.local_limit_hit, state.too_large = limit_hit, too_large
    assert D.summarise(state, {"sites": {}})["fixpoint_reached"] is False
    state.local_limit_hit, state.too_large = set(), set()
    assert D.summarise(state, {"sites": {}})["fixpoint_reached"] is True
