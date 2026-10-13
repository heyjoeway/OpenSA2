"""Planted x86 controls for the M7 scanner's transfer-rule fixes.

Each test authors a few bytes of real 32-bit x86, decodes them with the scanner's own
`Code` builder and runs the production `layer_a`/`Attribution`, so it exercises the
shipped transfer rules, never a reimplementation. No executable, function map, replay,
game or device is opened. The image builder here is shared with
`test_d3d9_scan_ruleouts.py`.

The fixes under test (diagnosed by the read-only scoping unit, m7-scanner):
  * two- and three-operand IMUL clobbers only its destination; one-operand
    MUL/IMUL/DIV/IDIV clobber EAX and EDX (via `written_gprs`);
  * EBP is a frame pointer only after `mov ebp,esp`, a general register otherwise;
  * a store of a class vtable constant into [reg] is a strong update of reg's type;
  * Layer A counts a `mov m,[r2+D]; call m` site only when r2 was loaded from memory
    in the same basic block before the method load (not a function-pointer field, not a
    cross-block vtable load).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from iced_x86 import (Decoder, Formatter, FormatterSyntax, Instruction, Mnemonic,
                      OpKind, Register)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import d3d9_methods as D  # noqa: E402
from image import Section  # noqa: E402

TEXT_VA = 0x401000
RDATA_VA = 0x600000
DATA_VA = 0x700000
_FMT = Formatter(FormatterSyntax.NASM)


def _initial_data(size, data):
    buf = bytearray(size)
    for va, value in (data or {}).items():
        struct.pack_into("<I", buf, va - DATA_VA, value)
    return bytes(buf)


class FakeImage:
    """An authored 32-bit PE view with the surface the scanner reads: sections, a linear
    sweep, imports, thunks and relocations. Addresses are chosen so .text is `rx`,
    .rdata `r`, .data `rw`."""

    def __init__(self, text: bytes, *, rdata=b"", data_size=0x1000,
                 imports=None, thunks=None, relocated=None, data=None):
        self.base = 0x400000
        self._text = Section(".text", TEXT_VA, len(text), len(text), 0x60000000)
        self._rdata = Section(".rdata", RDATA_VA, max(len(rdata), 4), max(len(rdata), 4),
                              0x40000000)
        self._data = Section(".data", DATA_VA, data_size, data_size, 0xC0000000)
        self.sections = [self._text, self._rdata, self._data]
        self.exec_sections = [self._text]
        self._section_bytes = {
            ".text": bytes(text),
            ".rdata": bytes(rdata).ljust(max(len(rdata), 4), b"\0"),
            # `data` maps .data addresses to initial dwords (an initialized global)
            ".data": _initial_data(data_size, data),
        }
        self.imports = dict(imports or {})
        self.thunks = dict(thunks or {})
        self.relocated = set(relocated or ())
        self.instructions = list(Decoder(32, bytes(text), ip=TEXT_VA))
        self.index = {i.ip: k for k, i in enumerate(self.instructions)}

    def section_of(self, addr):
        return next((s for s in self.sections if s.contains(addr)), None)

    def in_code(self, addr):
        return self._text.contains(addr)

    def read(self, va, n):
        # Like pefile.get_data: return what is available from `va` on, clamped to the
        # section (a short read near the end, not an error). read_u32 checks for 4 bytes.
        sec = self.section_of(va)
        if sec is None:
            return None
        data = self._section_bytes[sec.name]
        off = va - sec.va
        if off < 0 or off >= len(data):
            return None
        return data[off:off + n]

    def read_u32(self, va):
        d = self.read(va, 4)
        return None if d is None or len(d) < 4 else int.from_bytes(d, "little")

    def cstring(self, va, limit=128):
        sec = self.section_of(va)
        if sec is None or not sec.readable or sec.executable:
            return None
        data = self.read(va, limit)
        if not data:
            return None
        end = data.find(b"\0")
        if end <= 0:
            return None
        raw = data[:end]
        if not all(0x20 <= b < 0x7F for b in raw):
            return None
        return raw.decode("ascii")

    def text(self, ins):
        return _FMT.format(ins)


def fmap_for(entries):
    return SimpleNamespace(functions=[{"address": a} for a in sorted(entries)],
                           region_of=lambda a: "game")


def build(functions: dict[int, bytes], **kw) -> "D.Code":
    """A `D.Code` over authored functions {entry: bytes}, laid into one .text run."""
    lo = min(functions)
    hi = max(a + len(b) for a, b in functions.items())
    blob = bytearray(b"\xcc" * (hi - lo))
    for a, b in functions.items():
        blob[a - lo:a - lo + len(b)] = b
    img = FakeImage(bytes(blob), **kw)
    # .text starts at TEXT_VA == lo by construction of the callers
    assert lo == TEXT_VA, "author functions starting at TEXT_VA"
    return img, D.Code(img, fmap_for(functions))


def rel_call(buf: bytearray, entry: int, target: int) -> None:
    buf += b"\xe8" + struct.pack("<i", target - (entry + len(buf) + 5))


def decode_one(b: bytes) -> Instruction:
    return next(iter(Decoder(32, b, ip=0x1000)))


# ---------------------------------------------------------------------------
# written_gprs: the table that decides which registers an instruction clobbers

def regname(r):
    return D.full32(r)


@pytest.mark.parametrize("asm,writes", [
    (b"\x0f\xaf\xf1", {Register.ESI}),                 # imul esi,ecx  (2-op)
    (b"\x0f\xaf\x74\x24\x18", {Register.ESI}),         # imul esi,[esp+18h]
    (b"\x69\xf1\xa8\x00\x00\x00", {Register.ESI}),     # imul esi,ecx,0A8h (3-op)
    (b"\xf7\xe1", {Register.EAX, Register.EDX}),       # mul ecx  (1-op)
    (b"\xf7\xe9", {Register.EAX, Register.EDX}),       # imul ecx (1-op)
    (b"\xf7\xf1", {Register.EAX, Register.EDX}),       # div ecx
    (b"\xf7\xf9", {Register.EAX, Register.EDX}),       # idiv ecx
    (b"\x99", {Register.EDX}),                          # cdq
    (b"\x87\xca", {Register.ECX, Register.EDX}),       # xchg ecx,edx
    (b"\x8b\x01", {Register.EAX}),                      # mov eax,[ecx]
    (b"\x89\xc2", {Register.EDX}),                      # mov edx,eax
])
def test_written_gprs_names_every_clobbered_register(asm, writes):
    assert D.written_gprs(decode_one(asm)) == writes


def test_two_operand_imul_does_not_write_eax_or_edx():
    w = D.written_gprs(decode_one(b"\x0f\xaf\xf1"))     # imul esi,ecx
    assert Register.EAX not in w and Register.EDX not in w


# ---------------------------------------------------------------------------
# A seeded attribution harness: Direct3DCreate9 types EAX as IDirect3D9, and the test
# body then flows that pointer to a vtable call. Slot 8 (byte 8 / 4 = slot 2) is Release
# on every interface, so a `call [vt+8]` on an IDirect3D9 names IDirect3D9::Release.

IFACES, _ = D.parse_vtables(ROOT / "runtime" / "sa2_d3d9.c")
D3D9_SLOT_VA = 0x600100           # a .rdata address used as the Direct3DCreate9 IAT slot
D3D9_THUNK = 0x401F00


def seeded(body: bytes):
    """Attribution over a function that calls Direct3DCreate9 then runs `body`."""
    entry = TEXT_VA
    buf = bytearray(b"\x83\xec\x10")                   # sub esp,10h  (keep esp known)
    rel_call(buf, entry, D3D9_THUNK)                   # call Direct3DCreate9
    buf += body
    buf += b"\xc3"                                     # ret
    thunk = bytearray(b"\xff\x25" + struct.pack("<I", D3D9_SLOT_VA))  # jmp [slot]
    functions = {entry: bytes(buf), D3D9_THUNK: bytes(thunk)}
    img, code = build(functions,
                      imports={D3D9_SLOT_VA: ("d3d9.dll", "Direct3DCreate9")},
                      thunks={D3D9_THUNK: D3D9_SLOT_VA})
    attr = D.Attribution(code, IFACES, {"class_vt": set(), "entry_class": {}})
    attr.run(deadline=__import__("time").time() + 5)
    assert attr.stopped_by == "fixpoint"
    return attr


def methods_named(attr):
    return {f"{i}::{r['method']}" for r in attr.calls.values() for i in r["interfaces"]}


# ---- fix 2: two-operand IMUL preserves the object; one-operand MUL clobbers it ----

def test_two_operand_imul_preserves_the_device_in_another_register():
    # eax=IDirect3D9; imul esi,ecx writes esi only; edx<-eax; call through edx's vtable.
    body = bytes.fromhex("0f af f1"      # imul esi,ecx
                         "89 c2"          # mov edx,eax
                         "8b 02"          # mov eax,[edx]   (vtable)
                         "ff 50 08")      # call [eax+8]    (slot 2 = Release)
    assert "IDirect3D9::Release" in methods_named(seeded(body))


def test_one_operand_mul_clobbers_eax_so_the_object_is_lost():
    body = bytes.fromhex("f7 e1"         # mul ecx   (clobbers EAX, EDX)
                         "89 c2"          # mov edx,eax  (edx now untyped)
                         "8b 02"          # mov eax,[edx]
                         "ff 50 08")      # call [eax+8]
    assert methods_named(seeded(body)) == set()


# ---- fix 3: EBP is a general register until `mov ebp,esp` ----

def test_ebp_as_general_register_reaches_the_object_vtable():
    # mov ebp,eax makes EBP a general register holding the object; [ebp] is its vtable.
    body = bytes.fromhex("89 c5"         # mov ebp,eax
                         "8b 45 00"       # mov eax,[ebp]
                         "ff 50 08")      # call [eax+8]
    assert "IDirect3D9::Release" in methods_named(seeded(body))


def test_ebp_as_frame_pointer_is_a_stack_slot_not_the_object():
    # push ebp; mov ebp,esp establishes the frame; [ebp-4] is a local, and a store/load
    # roundtrip of the object through it proves the slot is tracked as a frame slot.
    body = bytes.fromhex("55"            # push ebp
                         "89 e5"          # mov ebp,esp
                         "89 45 fc"       # mov [ebp-4],eax
                         "8b 4d fc"       # mov ecx,[ebp-4]
                         "8b 01"          # mov eax,[ecx]
                         "ff 50 08")      # call [eax+8]
    assert "IDirect3D9::Release" in methods_named(seeded(body))


# ---- fix 4: a class-vtable store is a strong update of the register's type ----

def _one_class_vtable():
    """An image with a single C++ class vtable in .rdata (a run of one code pointer) and
    a `mov [eax],VT` that find_cpp_vtables recognises. Returns (code, attr, vt)."""
    vt = RDATA_VA                                      # the vtable address
    code_ptr = TEXT_VA                                 # a function the vtable lists
    rdata = struct.pack("<I", code_ptr)
    entry = TEXT_VA
    body = bytearray(b"\x8b\x08")                      # mov ecx,[eax]  (some use)
    body += b"\xc7\x00" + struct.pack("<I", vt)        # mov [eax],VT
    body += b"\xc3"
    functions = {entry: bytes(body)}
    img, code = build(functions, rdata=rdata)
    vtinfo = D.find_cpp_vtables(img)
    assert vt in vtinfo["class_vt"]
    attr = D.Attribution(code, IFACES, vtinfo)
    return code, attr, vt, entry


def test_class_vtable_store_replaces_an_overflowed_type_strongly():
    code, attr, vt, entry = _one_class_vtable()
    ins = {i.ip: i for i in code.instructions(entry)}
    store = next(i for i in ins.values()
                 if i.mnemonic == Mnemonic.MOV and i.op0_kind == OpKind.MEMORY)
    # EAX already carries four atoms; a join with the ctor group would overflow to TOP
    # and erase the type. The strong update must leave exactly the class.
    regs = {Register.EAX: frozenset({("C", 1), ("C", 2), ("C", 3), ("C", 4)})}
    assert D.Attribution.join(regs[Register.EAX], frozenset({("C", vt)})) == D.TOP
    attr._step(entry, store, regs, {}, 0, 0, set())
    assert regs[Register.EAX] == frozenset({("C", vt)})


def test_class_vtable_store_into_ebp_frame_pointer_is_not_a_strong_update():
    # When EBP is the frame pointer, `mov [ebp],VT` writes a frame slot, not an object,
    # so it must not retype EBP.
    code, attr, vt, entry = _one_class_vtable()
    store = next(i for i in code.instructions(entry)
                 if i.mnemonic == Mnemonic.MOV and i.op0_kind == OpKind.MEMORY)
    if store.memory_base != Register.EBP:
        store = decode_one(b"\xc7\x45\x00" + struct.pack("<I", vt))   # mov [ebp+0],VT
    regs = {"__fp__": (0, 0)}
    attr._step(entry, store, regs, {}, 0, 0, set())
    assert Register.EBP not in regs


# ---------------------------------------------------------------------------
# fix 5: Layer A counts a `mov m,[r2+D]; call m` site only when r2 was loaded from
# memory in the SAME basic block, BEFORE the method load. A base that is a call return
# (a function-pointer field) or loaded in a prior block is not the stated shape.

def _layer_a_count(body: bytes, extra=None):
    functions = {TEXT_VA: body}
    if extra:
        functions.update(extra)
    img, code = build(functions)
    return D.layer_a(code)


def test_layer_a_keeps_a_same_block_vtable_load():
    # mov edx,[eax] (vtable from object in eax); mov eax,[edx+1Ch] (method); call eax.
    body = bytes.fromhex("8b 10"         # mov edx,[eax]
                         "8b 42 1c"       # mov eax,[edx+1Ch]
                         "ff d0"          # call eax
                         "c3")
    a = _layer_a_count(body)
    assert a["vtable_shaped_call_sites"] == 1
    site = next(iter(a["sites"].values()))
    assert site["disp"] == 0x1C
    assert site["vt_load"]["base"] == D.full32(Register.EAX)   # object register
    assert site["vt_load"]["disp"] == 0                        # vtable at [obj+0]


def test_layer_a_refuses_a_function_pointer_field_from_a_call_return():
    # call X; mov eax,[eax+1Ch]; call eax -- eax is a call return (a struct/object
    # pointer), so [eax+1Ch] is a function-pointer field, not a vtable slot.
    entry = TEXT_VA
    buf = bytearray()
    rel_call(buf, entry, TEXT_VA + 0x80)     # call a helper (returns in eax)
    buf += bytes.fromhex("8b 40 1c"          # mov eax,[eax+1Ch]
                         "ff d0"              # call eax
                         "c3")
    helper = {TEXT_VA + 0x80: b"\xc3"}
    a = _layer_a_count(bytes(buf), extra=helper)
    assert a["vtable_shaped_call_sites"] == 0


def test_layer_a_refuses_a_cross_block_vtable_load():
    # mov edx,[eax] in the entry block; a branch; the method load and call in another
    # block, where edx's in-block provenance is gone. Outside the stated same-block shape.
    body = bytes.fromhex("8b 10"         # mov edx,[eax]     (block 0)
                         "85 c0"          # test eax,eax
                         "74 00"          # je +0  -> leader at the next instruction
                         "8b 42 1c"       # mov eax,[edx+1Ch] (block 1)
                         "ff d0"          # call eax
                         "c3")
    a = _layer_a_count(body)
    assert a["vtable_shaped_call_sites"] == 0


def test_layer_a_direct_call_memory_form_same_block():
    # mov eax,[ecx]; call [eax+8] -- the memory call form; vtable loaded in-block.
    body = bytes.fromhex("8b 01"         # mov eax,[ecx]
                         "ff 50 08"       # call [eax+8]
                         "c3")
    a = _layer_a_count(body)
    assert a["vtable_shaped_call_sites"] == 1
    assert next(iter(a["sites"].values()))["disp"] == 8


def test_layer_a_base_overwritten_after_method_load_still_counts():
    # mov edx,[eax] (vtable); mov esi,[edx+8] (method ptr in esi); mov edx,0 (overwrite
    # the base AFTER the method load); call esi. The old reading, which read the base's
    # provenance at the call, wrongly refused this; the fix captures it at the load.
    body = bytes.fromhex("8b 10"         # mov edx,[eax]
                         "8b 72 08"       # mov esi,[edx+8]
                         "31 d2"          # xor edx,edx
                         "ff d6"          # call esi
                         "c3")
    a = _layer_a_count(body)
    assert a["vtable_shaped_call_sites"] == 1
    assert next(iter(a["sites"].values()))["disp"] == 8


# ---------------------------------------------------------------------------
# the fold pass: convergence, the epoch, stack slots, EBP roles, Layer A's record

def test_a_loop_with_an_unknown_cleanup_call_converges_locally():
    # L: call [esi] (unknown cleanup: a new ESP epoch); dec ebx; jnz L; ret. The first
    # version took max(epoch) at every merge, so the loop head changed on every visit
    # and only MAX_BLOCK_VISITS stopped it (501 functions of the real image).
    img, code = build({TEXT_VA: bytes.fromhex("ff 16 4b 75 fb c3")})
    attr = D.Attribution(code, IFACES, {"class_vt": set(), "entry_class": {}})
    attr._analyse(TEXT_VA)
    assert TEXT_VA not in attr.local_limit_hit


def test_a_new_epoch_drops_stale_stack_slot_addresses():
    # An epoch number can recur on the next loop iteration, so an address of a slot of
    # the new epoch or a later one, still held in a register, names a dead frame.
    keep = {("A", ("s", 2, 8)), ("A", ("g", DATA_VA))}
    regs = {Register.ESI: frozenset(keep | {("A", ("s", 3, 8)), ("A", ("s", 4, 0))}),
            Register.EDI: frozenset({("A", ("s", 3, 0))}),
            "__fp__": (3, -4)}
    stack = {(2, 4): frozenset({("I", "IDirect3D9")})}
    assert D.Attribution._new_epoch(regs, stack, 2) == (0, 3)
    assert regs[Register.ESI] == frozenset(keep)
    assert Register.EDI not in regs
    assert regs["__fp__"] == "?"
    assert stack == {}


def _step_one(code_bytes, regs, stack, esp=0, epoch=0):
    img, code = build({TEXT_VA: code_bytes + b"\xc3"})
    attr = D.Attribution(code, IFACES, {"class_vt": set(), "entry_class": {}})
    i = code.instructions(TEXT_VA)[0]
    return attr._step(TEXT_VA, i, regs, stack, esp, epoch, set())


def test_a_stack_store_strongly_replaces_the_slot():
    X, Y = frozenset({("I", "IDirect3D9")}), frozenset({("I", "IDirect3DDevice9")})
    stack = {(0, 4): X}
    _step_one(bytes.fromhex("89 44 24 04"), {Register.EAX: Y}, stack)   # mov [esp+4],eax
    assert stack[(0, 4)] == Y                                           # not X | Y


def test_a_stack_store_of_an_unknown_value_clears_the_slot():
    stack = {(0, 4): frozenset({("I", "IDirect3D9")})}
    _step_one(bytes.fromhex("89 44 24 04"), {}, stack)                  # eax untyped
    assert (0, 4) not in stack


def test_any_instruction_that_writes_a_stack_slot_clears_it():
    stack = {(0, 4): frozenset({("I", "IDirect3D9")})}
    _step_one(bytes.fromhex("83 44 24 04 01"), {}, stack)               # add dword [esp+4],1
    assert (0, 4) not in stack


def test_a_disagreeing_frame_merge_does_not_keep_the_frame():
    # One path sets the frame, the other does not: after the merge EBP-based operands are
    # unknown, so a round trip through [ebp-4] must not carry the device.
    body = bytes.fromhex("85 c9"         # test ecx,ecx
                         "74 03"          # je +3  (skip the frame setup)
                         "55"             # push ebp
                         "89 e5"          # mov ebp,esp
                         "89 45 fc"       # mov [ebp-4],eax
                         "8b 4d fc"       # mov ecx,[ebp-4]
                         "8b 01"          # mov eax,[ecx]
                         "ff 50 08")      # call [eax+8]
    assert methods_named(seeded(body)) == set()


def test_a_disagreeing_frame_merge_does_not_keep_ebp_as_a_general_register():
    # One path makes EBP the frame, the other puts the device in it: after the merge
    # [ebp] is unknown, not the device's vtable.
    body = bytes.fromhex("85 c9"         # test ecx,ecx
                         "74 05"          # je L_B
                         "55"             # push ebp
                         "89 e5"          # mov ebp,esp
                         "eb 02"          # jmp L1
                         "89 c5"          # L_B: mov ebp,eax
                         "8b 45 00"       # L1: mov eax,[ebp]
                         "ff 50 08")      # call [eax+8]
    assert methods_named(seeded(body)) == set()


def _ebp_helper_image(helper: bytes):
    """entry: Direct3DCreate9; mov ebp,eax; call helper; mov eax,[ebp]; call [eax+8]."""
    entry, helper_va = TEXT_VA, TEXT_VA + 0x80
    buf = bytearray(b"\x83\xec\x10")
    rel_call(buf, entry, D3D9_THUNK)
    buf += bytes.fromhex("89 c5")                       # mov ebp,eax
    rel_call(buf, entry, helper_va)
    buf += bytes.fromhex("8b 45 00 ff 50 08 c3")        # mov eax,[ebp]; call [eax+8]
    functions = {entry: bytes(buf), helper_va: helper,
                 D3D9_THUNK: b"\xff\x25" + struct.pack("<I", D3D9_SLOT_VA)}
    img, code = build(functions, imports={D3D9_SLOT_VA: ("d3d9.dll", "Direct3DCreate9")},
                      thunks={D3D9_THUNK: D3D9_SLOT_VA})
    attr = D.Attribution(code, IFACES, {"class_vt": set(), "entry_class": {}})
    attr.run(deadline=__import__("time").time() + 5)
    return code, attr, helper_va


def test_a_helper_that_sets_ebp_leaves_it_unknown():
    # `lea ebp,[esp+8]; ret`, the shape of __SEH_prolog4: EBP is the caller's frame after
    # it, not the device the caller put there.
    code, attr, helper = _ebp_helper_image(bytes.fromhex("8d 6c 24 08 c3"))
    assert helper in code.sets_ebp
    assert methods_named(attr) == set()


def test_a_function_that_restores_ebp_keeps_the_callers_ebp():
    code, attr, helper = _ebp_helper_image(bytes.fromhex("55 89 e5 5d c3"))
    assert helper not in code.sets_ebp
    assert "IDirect3D9::Release" in methods_named(attr)


@pytest.mark.parametrize("body,irregular", [
    (bytes.fromhex("29 c4 c3"), True),                       # sub esp,eax; ret
    (bytes.fromhex("8b e0 c3"), True),                       # mov esp,eax; ret
    (bytes.fromhex("55 89 e5 29 c4 89 ec 5d c3"), False),    # frame restores ESP
    (bytes.fromhex("56 83 ec 08 83 c4 08 5e c2 04 00"), False),
    (bytes.fromhex("8d a4 24 00 00 00 00 c3"), False),    # lea esp,[esp+0]: padding
    (bytes.fromhex("8d 64 24 08 c3"), False),             # lea esp,[esp+8]
    (bytes.fromhex("8d 61 04 c3"), True),                 # lea esp,[ecx+4]
])
def test_irregular_stack_movers_are_recognised(body, irregular):
    img, code = build({TEXT_VA: body})
    assert (TEXT_VA in code.irregular_esp) is irregular


def test_layer_a_records_the_sites_the_same_block_correction_removes():
    entry = TEXT_VA
    buf = bytearray()
    rel_call(buf, entry, TEXT_VA + 0x80)
    buf += bytes.fromhex("8b 40 1c ff d0 c3")         # mov eax,[eax+1Ch]; call eax
    img, code = build({entry: bytes(buf), TEXT_VA + 0x80: b"\xc3"})
    a = D.layer_a(code)
    assert a["vtable_shaped_call_sites"] == 0
    call_va = next(i.ip for i in code.instructions(entry)
                   if i.mnemonic == Mnemonic.CALL and i.op0_kind == OpKind.REGISTER)
    assert list(a["same_block_correction"]["removed"]) == [call_va]
    rec = D.same_block_correction(code, a, set(), 0x1D8)
    assert rec["removed"] == 1 and rec["removed_that_were_in_the_ceiling"] == 1
    assert rec["removed_by_base_before_the_load"] == {"call return": 1}


def test_layer_a_records_the_sites_the_same_block_correction_admits():
    body = bytes.fromhex("8b 10 8b 72 08 31 d2 ff d6 c3")    # base overwritten after
    img, code = build({TEXT_VA: body})
    a = D.layer_a(code)
    assert len(a["same_block_correction"]["admitted"]) == 1
    assert a["same_block_correction"]["removed"] == {}


def test_a_call_to_an_irregular_stack_mover_starts_a_new_epoch():
    # mov [esp+4],eax (the device in a slot); call `sub esp,eax; ret`; mov ecx,[esp+4]:
    # ESP after the callee is not ESP before it, so the slot read is not the device.
    entry, mover = TEXT_VA, TEXT_VA + 0x80
    buf = bytearray(b"\x83\xec\x10")
    rel_call(buf, entry, D3D9_THUNK)
    buf += bytes.fromhex("89 44 24 04")                 # mov [esp+4],eax
    rel_call(buf, entry, mover)
    buf += bytes.fromhex("8b 4c 24 04 8b 01 ff 50 08 c3")
    functions = {entry: bytes(buf), mover: bytes.fromhex("29 c4 c3"),
                 D3D9_THUNK: b"\xff\x25" + struct.pack("<I", D3D9_SLOT_VA)}
    img, code = build(functions, imports={D3D9_SLOT_VA: ("d3d9.dll", "Direct3DCreate9")},
                      thunks={D3D9_THUNK: D3D9_SLOT_VA})
    assert mover in code.irregular_esp
    attr = D.Attribution(code, IFACES, {"class_vt": set(), "entry_class": {}})
    attr.run(deadline=__import__("time").time() + 5)
    assert methods_named(attr) == set()
    # the same with a regular callee keeps the slot
    functions[mover] = b"\xc3"
    img, code = build(functions, imports={D3D9_SLOT_VA: ("d3d9.dll", "Direct3DCreate9")},
                      thunks={D3D9_THUNK: D3D9_SLOT_VA})
    attr = D.Attribution(code, IFACES, {"class_vt": set(), "entry_class": {}})
    attr.run(deadline=__import__("time").time() + 5)
    assert "IDirect3D9::Release" in methods_named(attr)


@pytest.mark.parametrize("body", [bytes.fromhex("c3 45"),          # ret; inc ebp (data)
                                  bytes.fromhex("eb 01 45 c3")])  # jmp over a 45h byte
def test_ebp_and_esp_roles_ignore_unreachable_bytes(body):
    # Data inside an extent decodes as instructions (`inc ebp` is byte 45h); only code
    # reachable from the entry decides whether a function sets EBP or moves ESP.
    img, code = build({TEXT_VA: body})
    assert TEXT_VA not in code.sets_ebp
    img, code = build({TEXT_VA: body.replace(b"\x45", b"\x8b\xe0")})   # mov esp,eax
    assert TEXT_VA not in code.irregular_esp


def test_a_reachable_ebp_write_after_a_branch_still_counts():
    img, code = build({TEXT_VA: bytes.fromhex("85 c0 74 01 45 c3")})   # je over inc ebp
    assert TEXT_VA in code.sets_ebp
