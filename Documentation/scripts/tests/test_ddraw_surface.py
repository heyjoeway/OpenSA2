#!/usr/bin/env python3
"""`tools/s4/ddraw_surface.py` against a hand-assembled PE, and the device
profiles it writes for harness/ddraw_probe.c.

The fixture is a routine of this file's own composition with the shape the
tool has to follow in the shipped image -- a display query that loads
ddraw.dll, enumerates through a callback, creates the object, queries it for
IDirectDraw7 and asks for video memory -- at different addresses, registers
and frame offsets, so the tool is shown to follow the pointers rather than to
recognise one layout. Each mutation is one argument the runtime refuses on,
and each must turn exactly the fact that cites it false.

The probe's profiles are read back with tools/s4/device_profile.py's reader,
the Python half of the one the runtime traps with: the ones the probe runs
the query under must be accepted, the one it expects refused must be refused
at the reader's limit, and the limit must fit runtime/sa2_ddraw.h's records.
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import pytest
from iced_x86 import BlockEncoder, Code, Instruction, MemoryOperand, Register

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools" / "s4"
sys.path.insert(0, str(TOOLS))

import ddraw_surface as DS  # noqa: E402
import device_profile as DP  # noqa: E402
import image as IM  # noqa: E402

BASE = 0x400000
ROUTINE = 0x401000
CALLBACK = 0x401400
STR_DDRAW = 0x402000
STR_ENUM = 0x402010
STR_CREATE = 0x402030
IID_AT = 0x402050
OTHER_IID_AT = 0x402060
IAT_LL, IAT_GPA, IAT_FL = 0x403100, 0x403104, 0x403108

IID_DD7 = DS.IID_IDIRECTDRAW7
IID_DD = bytes.fromhex("80db146c33a7ce11a5210020af0be560")     # IID_IDirectDraw

# Frame slots, relative to the aligned frame: chosen apart from the shipped
# routine's so a match cannot be a coincidence of layout.
CTX, DD, DD7, CAPS, TOTAL = -0x20, -0x30, -0x2C, -0x40, -0x44
L_NOCB, L_FREE, L_OK, L_DONE, L_RET = 1001, 1002, 1003, 1004, 1005

EAX, EBX, ECX, EDX = Register.EAX, Register.EBX, Register.ECX, Register.EDX
ESI, EDI, ESP, EBP = Register.ESI, Register.EDI, Register.ESP, Register.EBP


class Asm:
    """Instructions with the stack pointer followed, so `at(slot)` is the
    `[esp+n]` that names an aligned-frame slot wherever the code is."""

    def __init__(self) -> None:
        self.ins: list[Instruction] = []
        self.esp = 0

    def add(self, i: Instruction, label: int | None = None) -> None:
        if label is not None:
            i.ip = label
        self.ins.append(i)

    def at(self, slot: int) -> MemoryOperand:
        return MemoryOperand(ESP, displ=slot - self.esp, displ_size=1)

    def push_imm(self, v: int, label: int | None = None) -> None:
        self.add(Instruction.create_u32(Code.PUSHD_IMM32, v), label)
        self.esp -= 4

    def push(self, r: int, label: int | None = None) -> None:
        self.add(Instruction.create_reg(Code.PUSH_R32, r), label)
        self.esp -= 4

    def pop(self, r: int) -> None:
        self.add(Instruction.create_reg(Code.POP_R32, r))
        self.esp += 4

    def call_reg(self, r: int, pops: int) -> None:
        self.add(Instruction.create_reg(Code.CALL_RM32, r))
        self.esp += 4 * pops

    def call_iat(self, slot: int, pops: int) -> None:
        self.add(Instruction.create_mem(Code.CALL_RM32, MemoryOperand(displ=slot, displ_size=4)))
        self.esp += 4 * pops

    def mov_rm(self, r: int, m: MemoryOperand, label: int | None = None) -> None:
        self.add(Instruction.create_reg_mem(Code.MOV_R32_RM32, r, m), label)

    def mov_mr(self, m: MemoryOperand, r: int) -> None:
        self.add(Instruction.create_mem_reg(Code.MOV_RM32_R32, m, r))

    def mov_mi(self, m: MemoryOperand, v: int) -> None:
        self.add(Instruction.create_mem_u32(Code.MOV_RM32_IMM32, m, v))

    def lea(self, r: int, m: MemoryOperand) -> None:
        self.add(Instruction.create_reg_mem(Code.LEA_R32_M, r, m))

    def xor(self, r: int) -> None:
        self.add(Instruction.create_reg_reg(Code.XOR_R32_RM32, r, r))

    def test(self, r: int) -> None:
        self.add(Instruction.create_reg_reg(Code.TEST_RM32_R32, r, r))

    def jcc(self, code: int, label: int) -> None:
        self.add(Instruction.create_branch(code, label))

    def encode(self, va: int) -> bytes:
        enc = BlockEncoder(32)
        enc.add_many(self.ins)
        return enc.encode(va)


def routine(flags: int = 1, riid: int = IID_AT, caps: int = 0x10004000, free: int = 0,
            test_create: bool = False) -> bytes:
    a = Asm()
    a.push(EBP)
    a.add(Instruction.create_reg_reg(Code.MOV_R32_RM32, EBP, ESP))
    a.add(Instruction.create_reg_i32(Code.AND_RM32_IMM8, ESP, -8))
    a.esp = 0                                  # the aligned frame starts here
    a.add(Instruction.create_reg_i32(Code.SUB_RM32_IMM8, ESP, 0x40))
    a.esp -= 0x40
    for r in (EBX, ESI, EDI):
        a.push(r)
    # the display's monitor, from slot 15 of the object at [arg1+4]
    a.mov_rm(EAX, MemoryOperand(EBP, displ=8, displ_size=1))
    a.mov_rm(EAX, MemoryOperand(EAX, displ=4, displ_size=1))
    a.mov_rm(ECX, MemoryOperand(EAX))
    a.mov_rm(EDX, MemoryOperand(ECX, displ=0x3C, displ_size=1))
    a.push_imm(0)
    a.push(EAX)
    a.call_reg(EDX, 2)
    a.add(Instruction.create_reg_reg(Code.MOV_R32_RM32, EBX, EAX))
    a.mov_mi(a.at(TOTAL), 0)
    a.push_imm(STR_DDRAW)
    a.call_iat(IAT_LL, 1)
    a.add(Instruction.create_reg_reg(Code.MOV_R32_RM32, ESI, EAX))
    a.test(ESI)
    a.jcc(Code.JE_REL32_32, L_DONE)
    a.xor(EAX)
    for k in (0, 4, 8, 12, 0x14):
        a.mov_mr(a.at(CTX + k), EAX)
    a.mov_mr(a.at(CTX + 0x10), EBX)
    a.mov_rm(EDI, MemoryOperand(displ=IAT_GPA, displ_size=4))
    a.push_imm(STR_ENUM)
    a.push(ESI)
    a.call_reg(EDI, 2)
    a.test(EAX)
    a.jcc(Code.JE_REL32_32, L_NOCB)
    a.push_imm(flags)
    a.lea(ECX, a.at(CTX))
    a.push(ECX)
    a.push_imm(CALLBACK)
    a.call_reg(EAX, 3)
    a.push_imm(STR_CREATE, label=L_NOCB)
    a.push(ESI)
    a.call_reg(EDI, 2)
    a.test(EAX)
    a.jcc(Code.JE_REL32_32, L_FREE)
    a.push_imm(0)
    a.lea(EDX, a.at(DD))
    a.push(EDX)
    a.lea(ECX, a.at(CTX))
    a.push(ECX)
    a.mov_mi(a.at(DD), 0)
    a.call_reg(EAX, 3)
    if test_create:
        a.test(EAX)
        a.jcc(Code.JL_REL32_32, L_FREE)
    a.mov_rm(EAX, a.at(DD))
    a.mov_rm(EDX, MemoryOperand(EAX))
    a.mov_rm(EDX, MemoryOperand(EDX))
    a.lea(ECX, a.at(DD7))
    a.push(ECX)
    a.push_imm(riid)
    a.push(EAX)
    a.call_reg(EDX, 3)
    a.test(EAX)
    a.jcc(Code.JL_REL32_32, L_FREE)
    a.xor(EAX)
    for k in (4, 8, 12):
        a.mov_mr(a.at(CAPS + k), EAX)
    a.mov_mi(a.at(CAPS), caps)
    a.push_imm(free)
    a.lea(EDX, a.at(TOTAL))
    a.push(EDX)
    a.lea(EDX, a.at(CAPS))
    a.push(EDX)
    a.mov_rm(EAX, a.at(DD7))
    a.mov_rm(ECX, MemoryOperand(EAX))
    a.push(EAX)
    a.mov_rm(EAX, MemoryOperand(ECX, displ=0x5C, displ_size=1))
    a.call_reg(EAX, 4)
    a.test(EAX)
    a.jcc(Code.JGE_REL32_32, L_OK)
    a.mov_mi(a.at(TOTAL), 0)
    a.mov_rm(EAX, a.at(DD7), label=L_OK)
    a.mov_rm(ECX, MemoryOperand(EAX))
    a.mov_rm(EDX, MemoryOperand(ECX, displ=8, displ_size=1))
    a.push(EAX)
    a.call_reg(EDX, 1)
    a.push(ESI, label=L_FREE)
    a.call_iat(IAT_FL, 1)
    a.mov_rm(EAX, a.at(TOTAL), label=L_DONE)
    a.mov_rm(ECX, MemoryOperand(EBP, displ=8, displ_size=1))
    a.add(Instruction.create_reg_u32(Code.SHR_RM32_IMM8, EAX, 20))
    a.mov_mr(MemoryOperand(ECX, displ=0x48, displ_size=1), EAX)
    for r in (EDI, ESI, EBX):
        a.pop(r)
    a.add(Instruction.create_reg_reg(Code.MOV_R32_RM32, ESP, EBP))
    a.pop(EBP)
    a.add(Instruction.create_u32(Code.RETND_IMM16, 4))
    return a.encode(ROUTINE)


def callback(nomatch_ret: int = 1) -> bytes:
    """The callback with its own registers: context in EDX, hm in EAX. With
    `nomatch_ret` other than 1 the no-match path gets an exit of its own that
    answers it, which is the shape "returns TRUE on every path" must reject."""
    a = Asm()
    a.mov_rm(EDX, MemoryOperand(ESP, displ=0x10, displ_size=1))
    a.mov_rm(EAX, MemoryOperand(ESP, displ=0x14, displ_size=1))
    a.add(Instruction.create_mem_reg(Code.CMP_RM32_R32,
                                     MemoryOperand(EDX, displ=0x10, displ_size=1), EAX))
    a.jcc(Code.JNE_REL32_32, L_RET)
    a.add(Instruction.create_mem_u32(Code.MOV_RM8_IMM8,
                                     MemoryOperand(EDX, displ=0x14, displ_size=1), 1))
    a.mov_rm(ECX, MemoryOperand(ESP, displ=4, displ_size=1))
    for k in (0, 4, 8, 12):
        a.mov_rm(EAX, MemoryOperand(ECX, displ=k, displ_size=1))
        a.mov_mr(MemoryOperand(EDX, displ=k, displ_size=1), EAX)
    if nomatch_ret == 1:
        a.add(Instruction.create_reg_u32(Code.MOV_R32_IMM32, EAX, 1), L_RET)
    else:
        a.add(Instruction.create_reg_u32(Code.MOV_R32_IMM32, EAX, 1))
        a.add(Instruction.create_u32(Code.RETND_IMM16, 0x14))
        a.add(Instruction.create_reg_u32(Code.MOV_R32_IMM32, EAX, nomatch_ret), L_RET)
    a.add(Instruction.create_u32(Code.RETND_IMM16, 0x14))
    return a.encode(CALLBACK)


def rdata() -> bytes:
    d = bytearray(0x100)
    for va, text in ((STR_DDRAW, b"ddraw.dll"), (STR_ENUM, b"DirectDrawEnumerateExA"),
                     (STR_CREATE, b"DirectDrawCreate")):
        d[va - 0x402000:va - 0x402000 + len(text) + 1] = text + b"\0"
    d[IID_AT - 0x402000:IID_AT - 0x402000 + 16] = IID_DD7
    d[OTHER_IID_AT - 0x402000:OTHER_IID_AT - 0x402000 + 16] = IID_DD
    return bytes(d)


def idata() -> bytes:
    d = bytearray(0x400)
    rva = 0x3000
    d[0:20] = struct.pack("<IIIII", rva + 0x80, 0, 0, rva + 0x300, rva + 0x100)
    names = [(0x200, b"LoadLibraryA"), (0x220, b"GetProcAddress"), (0x240, b"FreeLibrary")]
    for k, (off, name) in enumerate(names):
        d[0x80 + 4 * k:0x84 + 4 * k] = struct.pack("<I", rva + off)
        d[0x100 + 4 * k:0x104 + 4 * k] = struct.pack("<I", rva + off)
        d[off:off + 2 + len(name) + 1] = b"\0\0" + name + b"\0"
    d[0x300:0x300 + 13] = b"KERNEL32.dll\0"
    return bytes(d)


def build_pe(code: bytes) -> bytes:
    secs = [(b".text", 0x1000, code, 0x60000020),
            (b".rdata", 0x2000, rdata(), 0x40000040),
            (b".idata", 0x3000, idata(), 0xC0000040)]
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = struct.pack("<I", 0x40)
    coff = struct.pack("<HHIIIHH", 0x14C, len(secs), 0, 0, 0, 0xE0, 0x0102)
    dirs = [(0, 0)] * 16
    dirs[1] = (0x3000, 40)
    opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII",
                      0x10B, 9, 0, len(code), 0x600, 0, 0x1000, 0x1000, 0x2000, BASE, 0x1000,
                      0x200, 4, 0, 0, 0, 4, 0, 0, 0x4000, 0x400, 0, 2, 0, 0x100000, 0x1000,
                      0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", rva, size) for rva, size in dirs)
    headers = bytes(dos) + b"PE\0\0" + coff + opt
    raw, body = 0x400, b""
    for name, rva, data, chars in secs:
        size = (len(data) + 0x1FF) & ~0x1FF
        headers += struct.pack("<8sIIIIIIHHI", name, len(data), rva, size, raw + len(body),
                               0, 0, 0, 0, chars)
        body += data.ljust(size, b"\0")
    assert len(headers) <= 0x400
    return headers.ljust(0x400, b"\0") + body


def image(tmp_path: Path, **mut) -> IM.Image:
    cb_ret = mut.pop("nomatch_ret", 1)
    code = bytearray(routine(**mut).ljust(CALLBACK - ROUTINE, b"\xcc"))
    code += callback(cb_ret)
    exe = tmp_path / "ddraw.exe"
    exe.write_bytes(build_pe(bytes(code)))
    return IM.Image(exe)


ENTRIES = [ROUTINE, CALLBACK]


def facts(img: IM.Image) -> dict[str, bool]:
    return {k: v["held"] for k, v in DS.check(DS.surface(img, ENTRIES))["facts"].items()}


def test_every_fact_holds_on_the_well_formed_routine(tmp_path: Path) -> None:
    got = facts(image(tmp_path))
    assert got == {k: True for k in (
        "one-routine", "absent-branch", "enumerate", "callback", "context", "monitor",
        "create", "unchecked", "qi", "v1-only-queried", "vidmem", "kept", "release")}


def test_the_three_methods_and_their_pushed_counts(tmp_path: Path) -> None:
    surf = DS.surface(image(tmp_path), ENTRIES)
    assert [(r["interface"], r["slot"], r["name"], r["argc"]) for r in DS.reached(surf)] == [
        ("IDirectDraw", 0, "QueryInterface", 2),
        ("IDirectDraw7", 23, "GetAvailableVidMem", 3),
        ("IDirectDraw7", 2, "Release", 0),
    ]
    entry = [(c["name"], c["argc"]) for c in surf["routines"][0]["calls"] if c["kind"] == "entry"]
    assert entry == [("DirectDrawEnumerateExA", 3), ("DirectDrawCreate", 3)]


@pytest.mark.parametrize("mutation, broken", [
    ({"flags": 0}, {"enumerate"}),
    ({"caps": 0x4000}, {"vidmem"}),
    ({"free": 0x402000}, {"vidmem"}),
    ({"test_create": True}, {"unchecked"}),
    ({"nomatch_ret": 0}, {"callback"}),
])
def test_each_mutation_breaks_the_fact_that_cites_it(tmp_path: Path, mutation: dict,
                                                     broken: set[str]) -> None:
    got = facts(image(tmp_path, **mutation))
    assert {k for k, held in got.items() if not held} == broken


def test_another_iid_leaves_the_second_interface_unattributed(tmp_path: Path) -> None:
    surf = DS.surface(image(tmp_path, riid=OTHER_IID_AT), ENTRIES)
    got = {k: v["held"] for k, v in DS.check(surf)["facts"].items()}
    assert {k for k, held in got.items() if not held} == {"qi", "vidmem", "kept", "release"}
    assert [(r["interface"], r["slot"]) for r in DS.reached(surf)] == [("IDirectDraw", 0)]


def test_without_following_out_pointers_no_method_is_attributed(tmp_path: Path) -> None:
    img = image(tmp_path)
    assert len(DS.reached(DS.surface(img, ENTRIES))) == 3
    assert DS.reached(DS.surface(img, ENTRIES, follow_out=False)) == []


def run_json() -> dict:
    """The methods array a probe or crt_probe run writes, as the runtime declares it."""
    rows = []
    for iface, names in DS.NAMES.items():
        for slot, name in enumerate(names):
            handled = (iface, slot) in {("IDirectDraw", 0), ("IDirectDraw7", 23),
                                        ("IDirectDraw7", 2)}
            argc = {("IDirectDraw", 0): 2, ("IDirectDraw7", 23): 3,
                    ("IDirectDraw7", 2): 0}.get((iface, slot), 1)
            rows.append({"interface": iface, "name": name, "slot": slot, "argc": argc,
                         "implemented": handled})
    return {"ddraw": {"methods": rows}}


def run_json_over(key: tuple[str, int], fields: dict) -> dict:
    doc = run_json()
    next(r for r in doc["ddraw"]["methods"] if (r["interface"], r["slot"]) == key).update(fields)
    return doc


def test_declared_counts_and_handlers_agree_with_the_image(tmp_path: Path) -> None:
    surf = DS.surface(image(tmp_path), ENTRIES)
    got = DS.argc_check(surf, run_json())
    assert (got["ok"], got["disagreements"], got["handlers_on_unreached_slots"]) == (True, [], [])


def test_the_argc_break_is_a_disagreement(tmp_path: Path) -> None:
    surf = DS.surface(image(tmp_path), ENTRIES)
    got = DS.argc_check(surf, run_json_over(("IDirectDraw7", 23), {"argc": 2}))
    assert got["ok"] is False
    assert [(r["interface"], r["slot"], r["declared_argc"], r["argc"])
            for r in got["disagreements"]] == [("IDirectDraw7", 23, 2, 3)]


def test_a_handler_on_a_slot_the_image_never_calls_is_reported(tmp_path: Path) -> None:
    surf = DS.surface(image(tmp_path), ENTRIES)
    got = DS.argc_check(surf, run_json_over(("IDirectDraw7", 11), {"implemented": True}))
    assert (got["ok"], got["handlers_on_unreached_slots"]) == (False, ["IDirectDraw7:11"])


def test_entry_point_counts_come_from_the_dyn_table(tmp_path: Path) -> None:
    surf = DS.surface(image(tmp_path), ENTRIES)
    rows = [{"dll": "DDRAW", "name": "DirectDrawCreate", "argc": 3},
            {"dll": "DDRAW", "name": "DirectDrawEnumerateExA", "argc": 3}]
    assert DS.entry_argc_check(surf, rows)["ok"] is True
    rows[0]["argc"] = 2
    got = DS.entry_argc_check(surf, rows)
    assert got["ok"] is False
    assert [(r["name"], r["pushed"], r["declared"]) for r in got["rows"]] == [
        ("DirectDrawEnumerateExA", 3, 3), ("DirectDrawCreate", 3, 2)]


# ------------------------------------------------------ the probe's profiles

def header_define(name: str) -> int:
    text = (ROOT / "runtime" / "sa2_ddraw.h").read_text()
    m = re.search(rf"^#define {name}\s+(0x[0-9A-Fa-f]+|[0-9]+)u?\b", text, re.M)
    assert m, name
    return int(m.group(1), 0)


def record_kinds(text: bytes) -> list[str]:
    return [line.split(" ", 1)[0] for line in text.decode("ascii").splitlines()[1:]]


def test_the_records_hold_every_device_a_reader_accepts() -> None:
    # The Python half of sa2_ddraw.c's _Static_assert(SA2_DP_MAX_DDRAW <= SA2_DD_MAX_DEVICES).
    assert DP.MAX_DDRAW <= header_define("SA2_DD_MAX_DEVICES")


def test_the_probe_profiles_carry_every_record_the_grammar_requires() -> None:
    # Only the runs that may be empty are left out: no controller, no entity,
    # and the locale, environment and standard-handle sections, each optional as
    # a whole (the probe's profiles ask for the runtime's default answers there).
    required = set(DP.RECORDS) - {"error", "dinput8_gamectrl", "wmi_pnp", "locale_user",
                                  "locale_info", "environment", "env", "std_handle"}
    for key, text in DS.probe_profiles().items():
        assert required <= set(record_kinds(text)), key


# A `ddraw_device` record as tools/s4/device_profile.py's `dump` spells it.
DDRAW_DEVICE = re.compile(rb'ddraw_device index=[0-9]+ guid=(?:null|\{[0-9A-F-]{36}\}) '
                          rb'monitor=(?:null|adapter[0-9]+) '
                          rb'description="(?P<description>(?:[^"\\]|\\.)*)" '
                          rb'name="(?P<name>(?:[^"\\]|\\.)*)"')


def unquote(b: bytes) -> bytes:
    """A quoted string's bytes: the grammar spells a quote, a backslash and a
    byte outside 0x20..0x7e with a backslash (device_profile.quote)."""
    out, i = bytearray(), 0
    while i < len(b):
        if b[i] != 0x5C:
            out.append(b[i])
            i += 1
        elif b[i + 1:i + 2] == b"x":
            out.append(int(b[i + 2:i + 4], 16))
            i += 4
        else:
            out.append(b[i + 1])
            i += 2
    return bytes(out)


def test_the_probe_profiles_hold_only_made_up_guids_and_strings() -> None:
    # Written out here rather than derived from the tool's own probe_guid or
    # its strings, so a change to either fails this test instead of moving
    # the expectation with it.
    made_up = {f"DD7E{k:04X}-5341-3244-8000-50524F424500" for k in range(1, 64)}
    zero = "00000000-0000-0000-0000-000000000000"
    descriptions = {b"DirectDraw HAL", b"SA2 probe display A \xe9",
                    b'SA2 probe display "B"', b"SA2 probe display", b"D" * 259}
    names = {b"display", b"\\\\.\\PROBE1", b"\\\\.\\PROBE2", b"\\\\.\\PROBE",
             b"N" * 259}
    for key, text in DS.probe_profiles().items():
        for g in re.findall(rb"\{([0-9A-Fa-f-]{36})\}", text):
            assert g.decode().upper() in made_up | {zero}, key
        lines = [ln for ln in text.splitlines() if ln.startswith(b"ddraw_device ")]
        records = [DDRAW_DEVICE.fullmatch(ln) for ln in lines]
        assert lines and all(records), key
        for m in records:
            assert unquote(m.group("description")) in descriptions, key
            assert unquote(m.group("name")) in names, key


def test_the_match_profile_fills_the_records_and_the_last_display_is_kept() -> None:
    p = DP.parse(DS.probe_profiles()["match"], "match")
    assert len(p.ddraw) == DP.MAX_DDRAW
    assert (p.ddraw[0].guid, p.ddraw[0].monitor) == (None, -1)
    assert [i for i, d in enumerate(p.ddraw) if d.monitor == 0] == [1, 2]
    # Two displays on adapter 0's monitor, so "the last one" is what is tested.
    assert DP.chosen_guid(p.ddraw) == p.ddraw[2].guid != p.ddraw[1].guid
    assert max(len(d.name) for d in p.ddraw) == DP.NAME_MAX - 1
    assert any(b'"' in d.description for d in p.ddraw)
    assert any(max(d.description) > 0x7E for d in p.ddraw)
    assert (p.vidmem_hr, p.vidmem_total) == (0, DS.PROBE_TOTAL)
    assert DS.PROBE_TOTAL != DP.DEFAULT_VIDMEM


def test_the_failed_vidmem_profile_is_accepted_with_its_failure() -> None:
    p = DP.parse(DS.probe_profiles()["vidmem-failed"], "vidmem-failed")
    assert (p.vidmem_hr, p.vidmem_total) == (DS.PROBE_FAILED, 0)
    assert p.ddraw == DP.default().ddraw


def test_the_count_profile_is_refused_one_past_the_limit() -> None:
    text = DS.probe_profiles()["count"]
    kinds = record_kinds(text)
    assert kinds.count("ddraw_device") == DP.MAX_DDRAW + 1
    with pytest.raises(DP.ProfileError) as e:
        DP.parse(text, "count")
    # Refused at the device one past the limit, and not before it: its line is
    # the header's, then the records up to it.
    last = len(kinds) - kinds[::-1].index("ddraw_device")
    assert e.value.line == last + 1
    assert "`ddraw_device`" in e.value.message


def test_the_probe_reads_the_files_the_tool_writes(tmp_path: Path) -> None:
    paths = DS.write_probe_profiles(tmp_path)
    keys = set(DS.probe_profiles())
    assert sorted(p.name for p in paths) == sorted(DS.PROBE_PROFILE.format(key=k) for k in keys)
    probe = (ROOT / "harness" / "ddraw_probe.c").read_text()
    assert '"%s/ddraw-probe.%s.profile.txt"' in probe
    named = set(re.findall(r'^\s*\{"[a-z-]+",\s*[01],\s*"([a-z-]+)"', probe, re.M))
    assert named - {"capture"} == keys
