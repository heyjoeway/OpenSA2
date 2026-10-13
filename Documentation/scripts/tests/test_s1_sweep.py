#!/usr/bin/env python3
"""The three S1 sweep scripts against a hand-built 32-bit PE.

pefile cannot write a PE, so `build_pe` lays the headers out with `struct`:
one `.text` section at RVA 0x1000 holding hand-assembled code, an import
directory for KERNEL32!CreateThread and KERNEL32!VirtualAlloc, a C++
`__ehhandler$` stub with its FuncInfo magic, and a thread start routine.
Each test asserts the counting functions find exactly the planted patterns.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import s1_dynamic_code_tls as dyn  # noqa: E402
import s1_exception_handling as eh  # noqa: E402
import s1_sweep as sw  # noqa: E402
import s1_threads as th  # noqa: E402

BASE = 0x400000
TEXT_RVA = 0x1000
CODE = BASE + TEXT_RVA
HANDLER = CODE + 0x80          # __ehhandler$ stub
START = CODE + 0x90            # thread start routine
FRAME_HANDLER = CODE + 0xA0    # __CxxFrameHandler3 stand-in
FUNCINFO = CODE + 0xC0
THUNK = CODE + 0x70            # jmp [VirtualAlloc]
IAT_CREATETHREAD = CODE + 0x140
IAT_VIRTUALALLOC = CODE + 0x144

REGISTRATION_READ = CODE + 0x07
REGISTRATION_WRITE = CODE + 0x0E
CREATETHREAD_CALL = CODE + 0x24
VIRTUALALLOC_CALL = CODE + 0x38
TLS_READ = CODE + 0x3D
SELF_WRITE = CODE + 0x47
UNREGISTER_WRITE = CODE + 0x5A


def rel32(src_end: int, dst: int) -> bytes:
    return struct.pack("<i", dst - src_end)


def text_section() -> bytes:
    t = bytearray(0x200)
    code = bytearray()
    code += b"\x6a\xff"                                  # 00 push -1
    code += b"\x68" + struct.pack("<I", HANDLER)         # 02 push handler
    code += b"\x64\xa1\x00\x00\x00\x00"                  # 07 mov eax, fs:[0]
    code += b"\x50"                                      # 0d push eax
    code += b"\x64\x89\x25\x00\x00\x00\x00"              # 0e mov fs:[0], esp
    code += b"\x6a\x00\x6a\x00\x6a\x00"                  # 15 push 0 x3 (id, flags, param)
    code += b"\x68" + struct.pack("<I", START)           # 1b push start routine
    code += b"\x6a\x00\x6a\x00"                          # 20 push 0 x2 (stack, attributes)
    code += b"\xff\x15" + struct.pack("<I", IAT_CREATETHREAD)  # 24 call [CreateThread]
    code += b"\x6a\x40"                                  # 2a push 0x40 PAGE_EXECUTE_READWRITE
    code += b"\x68\x00\x10\x00\x00"                      # 2c push 0x1000 MEM_COMMIT
    code += b"\x68\x00\x10\x00\x00"                      # 31 push 0x1000 size
    code += b"\x6a\x00"                                  # 36 push 0 address
    code += b"\xe8" + rel32(CODE + 0x3D, THUNK)          # 38 call thunk (VirtualAlloc)
    code += b"\x64\x8b\x0d\x2c\x00\x00\x00"              # 3d mov ecx, fs:[2Ch]
    code += b"\x8b\x04\x8a"                              # 44 mov eax, [edx+ecx*4]
    code += b"\xc7\x05" + struct.pack("<I", CODE) + b"\x01\x00\x00\x00"  # 47 mov dword ptr [code], 1
    code += b"\x8b\x0d" + struct.pack("<I", CODE)        # 51 mov ecx, [code]  (a read: must not count)
    code += b"\x8b\x0c\x24"                              # 57 mov ecx, [esp]
    code += b"\x64\x89\x0d\x00\x00\x00\x00"              # 5a mov fs:[0], ecx  (unregistration)
    code += b"\xc3"                                      # 61 ret
    assert len(code) == 0x62
    t[0:len(code)] = code
    t[0x70:0x76] = b"\xff\x25" + struct.pack("<I", IAT_VIRTUALALLOC)      # jmp [VirtualAlloc]
    t[0x80:0x8A] = b"\xb8" + struct.pack("<I", FUNCINFO) + b"\xe9" + rel32(HANDLER + 0xA, FRAME_HANDLER)
    t[0x90:0x93] = b"\x31\xc0\xc3"                                        # xor eax,eax; ret
    t[0xA0] = 0xC3                                                        # ret
    t[0xC0:0xC4] = struct.pack("<I", 0x19930522)
    # import directory at RVA 0x1100: descriptor + null descriptor, ILT 0x1130, IAT 0x1140, names, DLL name.
    t[0x100:0x114] = struct.pack("<IIIII", TEXT_RVA + 0x130, 0, 0, TEXT_RVA + 0x180, TEXT_RVA + 0x140)
    for off in (0x130, 0x140):
        t[off:off + 12] = struct.pack("<III", TEXT_RVA + 0x150, TEXT_RVA + 0x160, 0)
    t[0x150:0x150 + 15] = b"\x00\x00CreateThread\x00"
    t[0x160:0x160 + 15] = b"\x00\x00VirtualAlloc\x00"
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


@pytest.fixture
def image(tmp_path: Path) -> sw.Image:
    exe = tmp_path / "tiny.exe"
    exe.write_bytes(build_pe())
    return sw.Image(exe)


@pytest.fixture
def no_map(tmp_path: Path) -> sw.FunctionMap:
    return sw.FunctionMap(tmp_path / "absent-function-map.json")


@pytest.fixture
def game_map(tmp_path: Path) -> sw.FunctionMap:
    path = tmp_path / "function-map.json"
    path.write_text(json.dumps({"functions": [
        {"address": hex(CODE), "size": 0x62, "name": "main", "region": "game"},
        {"address": START, "size": 3, "name": "worker", "region": "cri"},
    ]}), encoding="utf-8")
    return sw.FunctionMap(path)


def test_sweep_maps_imports_thunks_and_call_sites(image: sw.Image) -> None:
    assert image.base == BASE
    assert [s.name for s in image.exec_sections] == [".text"]
    assert image.imports == {IAT_CREATETHREAD: "CreateThread", IAT_VIRTUALALLOC: "VirtualAlloc"}
    assert image.thunks == {THUNK: "VirtualAlloc"}
    assert image.import_call_sites("CreateThread") == [CREATETHREAD_CALL]
    assert image.import_call_sites("VirtualAlloc") == [VIRTUALALLOC_CALL]
    assert image.import_uses("VirtualAlloc") == [(VIRTUALALLOC_CALL, "call"), (THUNK, "jmp")]
    assert image.direct_calls[THUNK] == [VIRTUALALLOC_CALL]
    pushes = image.pushed_arguments(image.at(VIRTUALALLOC_CALL))
    assert pushes == [0x40, 0x1000, 0x1000, 0]


def test_exception_handling_finds_the_planted_registration(image: sw.Image, no_map: sw.FunctionMap) -> None:
    sites = eh.find_registrations(image)
    assert sites == [{"read": REGISTRATION_READ, "write": REGISTRATION_WRITE, "handler": HANDLER}]
    assert eh.handler_shape(image, HANDLER) == {"shape": "cxx-ehhandler-stub", "frame_handler": FRAME_HANDLER,
                                                "funcinfo": FUNCINFO, "funcinfo_magic": 0x19930522}
    out = eh.analyse(image, no_map)
    assert out["fs0_registration_sites"] == {"total": 1, "per_region": {"unpartitioned": 1}}
    assert out["fs0_writes"]["total"] == 2
    assert out["fs0_unregistration_writes"] == {"total": 1, "per_region": {"unpartitioned": 1}}
    assert out["cxx_frame_handler3"]["address"] == hex(FRAME_HANDLER)
    assert out["cxx_frame_handler3"]["ehhandler_stubs"] == 1
    assert out["safeseh"]["load_config_present"] is False
    assert out["top_20_registration_sites"][0]["read"] == hex(REGISTRATION_READ)
    assert out["cxx_throw"]["throw_sites"]["total"] == 0


def test_threads_finds_the_pushed_start_routine(image: sw.Image, no_map: sw.FunctionMap, game_map: sw.FunctionMap) -> None:
    assert th.start_routine(image, CREATETHREAD_CALL) == START
    sites = th.thread_sites(image, no_map)
    assert [(s["call"], s["start_routine"], s["region"]) for s in sites["direct"]] == [(CREATETHREAD_CALL, START, "unpartitioned")]
    assert sites["wrappers"] == []  # no direct-call target precedes the site, so no enclosing function without a map
    with_map = th.thread_sites(image, game_map)
    assert with_map["direct"][0]["region"] == "game"
    assert with_map["direct"][0]["start_region"] == "cri"
    assert [w["entry"] for w in with_map["wrappers"]] == [CODE]
    out = th.analyse(image, game_map)
    assert out["CreateThread_call_sites"] == {"total": 1, "per_region": {"game": 1}}
    assert out["wrapper_internal_start_routines"] == {"total": 1, "per_region": {"cri": 1}}
    assert [s["import"] for s in out["sync_imports"]] == ["CreateThread"]
    assert out["lock_prefixed_instructions"]["total"] == 0


def test_dynamic_code_finds_execute_alloc_self_write_and_tls(image: sw.Image, no_map: sw.FunctionMap) -> None:
    prot = dyn.protection_calls(image, no_map)
    assert prot == [{"import": "VirtualAlloc", "call": hex(VIRTUALALLOC_CALL), "region": "unpartitioned",
                     "pushed_immediates": ["0x40", "0x1000", "0x1000", "0x0"], "protect": "0x40", "execute_flag": True}]
    assert dyn.code_writes(image) == [{"address": SELF_WRITE, "target": CODE}]
    assert dyn.tls_index_sequences(image) == [TLS_READ]
    out = dyn.analyse(image, no_map)
    assert out["writable_and_executable"] == []
    assert out["fs_accesses"]["by_displacement"]["0x0"]["total"] == 3
    assert out["fs_0x2c_tls_array"]["total"] == 1
    assert out["tls_directory"]["present"] is False
    assert out["verdict"] == [f"VirtualAlloc at {hex(VIRTUALALLOC_CALL)} with PAGE_EXECUTE* protection 0x40",
                              f"write into code at {hex(SELF_WRITE)} -> {hex(CODE)}"]
