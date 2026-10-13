"""Planted x86 controls for the must-analysis the M7 rule-outs read (`_MustTrace`).

A rule-out acts on a fact only if it holds on every path, so each test here is a path
the first version of the analysis did not model and over-claimed on (the fact it
recorded did not hold on that path), or a precision case the fix gains. Each runs the
production `_MustTrace` over a few authored bytes. No executable, map, replay, game or
device is opened.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d3d9_methods as D  # noqa: E402
import test_d3d9_scan_transfer as T  # noqa: E402

from iced_x86 import Register  # noqa: E402

TEXT_VA, RDATA_VA, DATA_VA = T.TEXT_VA, T.RDATA_VA, T.DATA_VA
ESI, ECX = Register.ESI, Register.ECX
THIS = ("this",)


def trace(functions, entry=TEXT_VA, **kw):
    img, code = T.build(functions, **kw)
    return code, D._MustTrace(code, entry).run()


def reg_at(t, ip, reg):
    st = t.at.get(ip)
    return st[0].get(reg) if st else None


# ---- switch tables -------------------------------------------------------

def _switch(case0: bytes):
    """mov esi,ecx; cmp eax,1; ja JOIN; jmp [eax*4+TABLE]; CASE0: <case0>; jmp JOIN;
    CASE1: nop; JOIN: mov eax,[esi]; call [eax+8]; ret. TABLE = [CASE0, CASE1]."""
    head = bytes.fromhex("8b f1 83 f8 01")                    # 0: mov esi,ecx; cmp eax,1
    case0_at = 14
    case1_at = case0_at + len(case0) + 2
    join_at = case1_at + 1
    body = bytearray(head)
    body += b"\x77" + bytes([join_at - (len(body) + 2)])     # ja JOIN
    body += bytes.fromhex("ff 24 85") + struct.pack("<I", RDATA_VA)
    assert len(body) == case0_at
    body += case0 + b"\xeb" + bytes([join_at - (case1_at)])  # CASE0; jmp JOIN
    body += b"\x90"                                          # CASE1: nop
    body += bytes.fromhex("8b 06 ff 50 08 c3")              # JOIN
    rdata = struct.pack("<II", TEXT_VA + case0_at, TEXT_VA + case1_at)
    code, t = trace({TEXT_VA: bytes(body)}, rdata=rdata)
    return t, TEXT_VA + join_at


def test_a_switch_case_that_overwrites_the_receiver_reaches_the_join():
    # The out-of-range edge reaches JOIN with ESI = this; CASE0 overwrites ESI. The first
    # version did not follow the table, so JOIN saw only the first edge and claimed this.
    t, join = _switch(bytes.fromhex("89 d6"))                 # mov esi,edx
    assert t.converged
    assert t.switch_targets
    assert reg_at(t, join, ESI) is None


def test_a_switch_whose_cases_keep_the_receiver_keeps_it():
    t, join = _switch(bytes.fromhex("90 90"))
    assert reg_at(t, join, ESI) == THIS


# ---- interior points reached without a modelled edge ---------------------

def test_an_interior_point_named_by_an_immediate_is_entered_with_no_facts():
    # push offset HANDLER (a callback or an EH continuation); the code at HANDLER is
    # reached by no modelled edge, overwrites ESI and falls into JOIN.
    handler, join = TEXT_VA + 10, TEXT_VA + 12
    body = (bytes.fromhex("8b f1") + b"\x68" + struct.pack("<I", handler)
            + bytes.fromhex("58 eb 02")                       # pop eax; jmp JOIN
            + bytes.fromhex("89 d6")                          # HANDLER: mov esi,edx
            + bytes.fromhex("8b 06 c3"))                      # JOIN: mov eax,[esi]; ret
    code, t = trace({TEXT_VA: body})
    assert t.unmodelled == [handler]
    assert reg_at(t, join, ESI) is None


def test_a_jump_tables_own_base_is_not_an_entry_point():
    # MSVC puts the table in .text after the function. Its base is the relocated
    # displacement of `jmp [eax*4+TABLE]`: data in code, not a code entry, so it must
    # not be seeded as an unmodelled interior point (that made 0x84765d unknown).
    head = bytes.fromhex("8b f1 83 f8 01") + b"\x77" + bytes([14 + 4 + 1 - 7])
    case0, case1, join = 14, 18, 19
    body = bytearray(head + bytes.fromhex("ff 24 85") + b"\0\0\0\0")
    body += bytes.fromhex("90 90 eb 01 90 8b 06 ff 50 08 c3")
    table = TEXT_VA + len(body)
    struct.pack_into("<I", body, 10, table)
    body += struct.pack("<II", TEXT_VA + case0, TEXT_VA + case1)
    code, t = trace({TEXT_VA: bytes(body)}, relocated={TEXT_VA + 10, table, table + 4})
    assert t.switch_targets == {TEXT_VA + 7: [TEXT_VA + case0, TEXT_VA + case1]}
    assert t.unmodelled == []
    assert reg_at(t, TEXT_VA + join, ESI) == THIS


# ---- stack slots ---------------------------------------------------------

def _pop_ecx_case(middle: bytes):
    """push ecx; <middle>; pop ecx; mov eax,[ecx]; ret -- ECX at the load."""
    body = b"\x51" + middle + bytes.fromhex("59 8b 01 c3")
    code, t = trace({TEXT_VA: body})
    load = TEXT_VA + 1 + len(middle) + 1
    return t, load


def test_a_slot_written_by_xchg_is_not_its_old_value():
    t, load = _pop_ecx_case(bytes.fromhex("87 04 24"))       # xchg [esp],eax
    assert reg_at(t, load, ECX) is None


def test_a_slot_written_through_a_register_into_the_frame_is_not_its_old_value():
    t, load = _pop_ecx_case(bytes.fromhex("8d 04 24 89 10"))  # lea eax,[esp]; mov [eax],edx
    assert t.frame_escapes
    assert reg_at(t, load, ECX) == ("in", int(Register.EDX))


def test_a_slot_kept_by_an_unrelated_write_is_kept():
    t, load = _pop_ecx_case(bytes.fromhex("89 16"))           # mov [esi],edx
    assert not t.frame_escapes
    assert reg_at(t, load, ECX) == THIS


def test_a_byte_write_into_a_slot_clears_it():
    t, load = _pop_ecx_case(bytes.fromhex("c6 44 24 01 00"))  # mov byte [esp+1],0
    assert reg_at(t, load, ECX) is None


# ---- calls and ESP -------------------------------------------------------

def test_a_direct_callee_cleanup_is_exact():
    # push ecx; push edx; call f (ret 4); pop ecx: f pops one argument, so `pop ecx`
    # reads the slot that holds this. The first version lost ESP here.
    callee = TEXT_VA + 0x80
    buf = bytearray(bytes.fromhex("51 52"))
    T.rel_call(buf, TEXT_VA, callee)
    load = TEXT_VA + len(buf) + 1
    buf += bytes.fromhex("59 8b 01 c3")
    code, t = trace({TEXT_VA: bytes(buf), callee: bytes.fromhex("c2 04 00")})
    assert reg_at(t, load, ECX) == THIS


def test_an_indirect_call_leaves_esp_unknown_even_before_add_esp():
    # call [esi]; add esp,4: if the callee cleaned up its own argument the `add` is for
    # something else, so ESP is unknown and `pop ecx` cannot be this.
    body = bytes.fromhex("51 52 ff 16 83 c4 04 59 8b 01 c3")
    code, t = trace({TEXT_VA: body})
    assert reg_at(t, TEXT_VA + 8, ECX) is None


def test_a_call_to_an_irregular_stack_mover_leaves_esp_unknown():
    callee = TEXT_VA + 0x80                                   # sub esp,eax; ret
    buf = bytearray(b"\x51")
    T.rel_call(buf, TEXT_VA, callee)
    load = TEXT_VA + len(buf) + 1
    buf += bytes.fromhex("59 8b 01 c3")
    code, t = trace({TEXT_VA: bytes(buf), callee: bytes.fromhex("29 c4 c3")})
    assert callee in code.irregular_esp
    assert reg_at(t, load, ECX) is None


def test_aligning_esp_leaves_it_unknown():
    t, load = _pop_ecx_case(bytes.fromhex("83 e4 f8"))        # and esp,-8
    assert reg_at(t, load, ECX) is None


# ---- convergence ---------------------------------------------------------

def test_a_trace_that_is_not_run_to_a_fixpoint_says_so(monkeypatch):
    monkeypatch.setattr(D, "MAX_FUNC_INS", 1)
    code, t = trace({TEXT_VA: bytes.fromhex("8b f1 8b 06 c3")})
    assert t.converged is False and t.at == {}


# ---- where a constant address leaves the analysis --------------------------

OBJ = DATA_VA + 0x100


def _events(body):
    code, t = trace({TEXT_VA: body, TEXT_VA + 0x80: b"\xc3"})
    return {k for _, k in t.events.get(("imm", OBJ), ())}, t


def test_arithmetic_on_a_constant_address_is_an_escape():
    kinds, t = _events(b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("83 c1 04 c3"))
    assert kinds == {"arith"}


def test_storing_a_constant_address_is_an_escape():
    kinds, t = _events(b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("89 0d")
                       + struct.pack("<I", DATA_VA + 0x40) + b"\xc3")
    assert kinds == {"store"}


def test_passing_a_constant_address_to_a_call_is_an_escape():
    buf = bytearray(b"\x68" + struct.pack("<I", OBJ))         # push OBJ
    T.rel_call(buf, TEXT_VA, TEXT_VA + 0x80)
    kinds, t = _events(bytes(buf + bytes.fromhex("83 c4 04 c3")))
    assert kinds == {"call"}


def test_a_write_through_a_constant_base_is_recorded_exactly():
    kinds, t = _events(b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("89 41 08 c3"))
    assert kinds == set()
    assert [(D._const_addr(b) + d, size, unb) for _, b, d, size, unb, _ in t.writes] \
        == [(OBJ + 8, 4, False)]


# ---- a stack address that escapes ----------------------------------------

def test_a_call_after_the_frame_escapes_clears_every_slot():
    # push ecx (this); lea eax,[esp]; push eax; call f; add esp,4; pop ecx: f received the
    # address of the slot holding this and may have written it.
    callee = TEXT_VA + 0x80
    buf = bytearray(bytes.fromhex("51 8d 04 24 50"))
    T.rel_call(buf, TEXT_VA, callee)
    buf += bytes.fromhex("83 c4 04")
    load = TEXT_VA + len(buf) + 1
    buf += bytes.fromhex("59 8b 01 c3")
    code, t = trace({TEXT_VA: bytes(buf), callee: b"\xc3"})
    assert t.frame_escapes
    assert reg_at(t, load, ECX) is None


def test_a_write_through_an_unknown_pointer_after_the_frame_escapes_clears_every_slot():
    # push ecx; lea eax,[esp]; add eax,0 (the address is no longer followed);
    # mov [eax],edx; pop ecx.
    t, load = _pop_ecx_case(bytes.fromhex("8d 04 24 83 c0 00 89 10"))
    assert t.frame_escapes
    assert reg_at(t, load, ECX) is None


# ---- a trace cut short is never read ---------------------------------------

def test_a_rule_does_not_read_a_trace_cut_short_by_its_visit_guard(monkeypatch):
    # The member reads its vtable in its first block and loops back after overwriting
    # ECX, so the first visit's `this` is too optimistic. With the guard at one visit the
    # trace stops there; it must say it did not converge and R2 must not use it.
    import test_d3d9_scan_ruleouts as RO
    monkeypatch.setattr(D, "MAX_BLOCK_VISITS", 1)
    monkeypatch.setattr(D, "MUST_VISITS_PER_LEADER", 0)
    body = bytes.fromhex("8b 01 ff 50 08 89 d1 eb f7")   # L: mov eax,[ecx]; call [eax+8];
    #                                                       mov ecx,edx; jmp L
    code, t = trace({TEXT_VA: body})
    assert t.converged is False
    rdata, vt = RO._rtti_rdata(RO.MEMBER)
    functions = RO.base_functions()
    functions[RO.MEMBER] = body
    functions[RO.CTOR] = bytes.fromhex("c7 01") + struct.pack("<I", vt) + b"\xc3"
    img, code, attr, a, ro = RO.run(functions, rdata=rdata, **RO.SEED_KW)
    assert ro[RO.R2]["removed"] == 0
    assert ro["must_analysis"]["functions_whose_analysis_did_not_converge"]


def test_the_loop_back_edge_alone_already_refuses_the_receiver():
    body = bytes.fromhex("8b 01 ff 50 08 89 d1 eb f7")
    code, t = trace({TEXT_VA: body})
    assert t.converged
    assert reg_at(t, TEXT_VA, ECX) is None


# ---------------------------------------------------------------------------
# Guards the analysis' docstring claims, each with a planted case that goes red when
# that one guard is reverted (the w1b fold's mutation survey).

import pytest  # noqa: E402


# ---- every way a constant address leaves the analysis ----------------------

def _tail_jump():
    buf = bytearray(b"\xb9" + struct.pack("<I", OBJ))          # mov ecx,OBJ
    buf += b"\xe9" + struct.pack("<i", (TEXT_VA + 0x80) - (TEXT_VA + len(buf) + 5))
    return bytes(buf)                                           # jmp f (a tail call)


@pytest.mark.parametrize("name,body,kinds", [
    ("a tail jump with it in ECX", _tail_jump(), {"jmp"}),
    ("a return in EAX", b"\xb8" + struct.pack("<I", OBJ) + b"\xc3", {"ret"}),
    ("a merge with this: test eax,eax; jz L; mov ecx,OBJ; L: mov eax,[ecx]",
     bytes.fromhex("85 c0 74 05") + b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("8b 01 c3"),
     {"merge"}),
    ("a push with ESP unknown: and esp,-8; push OBJ",
     bytes.fromhex("83 e4 f8 68") + struct.pack("<I", OBJ) + b"\xc3", {"store"}),
    ("a LEA through an untraced base: lea eax,[edx+OBJ]",
     bytes.fromhex("8d 82") + struct.pack("<I", OBJ) + b"\xc3", {"untraced base"}),
    ("an indexed LEA: lea eax,[OBJ+eax*4]",
     bytes.fromhex("8d 04 85") + struct.pack("<I", OBJ) + b"\xc3", {"untraced base"}),
    ("a write through an untraced base: mov [edx+OBJ],eax",
     bytes.fromhex("89 82") + struct.pack("<I", OBJ) + b"\xc3", {"untraced base"}),
    ("arithmetic with it as an immediate: add eax,offset OBJ",
     b"\x05" + struct.pack("<I", OBJ) + b"\xc3", {"arith"}),
    ("a copy into an SSE register: mov ecx,OBJ; movd xmm0,ecx",
     b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("66 0f 6e c1 c3"), {"arith"}),
    ("a comparison is not an escape: cmp eax,offset OBJ",
     b"\x3d" + struct.pack("<I", OBJ) + b"\xc3", set()),
    ("an implicit read stored to the stack: mov esi,OBJ; pushad; popad",
     b"\xbe" + struct.pack("<I", OBJ) + bytes.fromhex("60 61 c3"), {"arith"}),
    ("an address register is not a value: mov edi,OBJ; mov ecx,4; rep stosd",
     b"\xbf" + struct.pack("<I", OBJ) + bytes.fromhex("b9 04 00 00 00 f3 ab c3"), set()),
])
def test_every_way_a_constant_address_leaves_the_analysis_is_an_event(name, body, kinds):
    got, t = _events(body)
    assert got == kinds, name


def test_a_write_through_an_untraced_base_is_recorded_at_its_displacement():
    got, t = _events(bytes.fromhex("89 82") + struct.pack("<I", OBJ) + b"\xc3")
    assert t.blind_writes == {(TEXT_VA, OBJ)}
    assert t.writes == set()


def test_a_lea_of_a_lea_of_a_constant_is_still_followed():
    # mov esi,OBJ; lea ecx,[esi+4]; lea edi,[ecx+4]; mov [edi-8],ebx writes OBJ itself
    got, t = _events(b"\xbe" + struct.pack("<I", OBJ)
                     + bytes.fromhex("8d 4e 04 8d 79 04 89 5f f8 c3"))
    assert got == set()
    assert [(D._const_addr(b) + d, size, unb) for _, b, d, size, unb, _ in t.writes] \
        == [(OBJ, 4, False)]


def test_a_pointer_derived_from_this_by_a_lea_leaves_the_analysis():
    code, t = trace({TEXT_VA: bytes.fromhex("8d 41 04 c3")})     # lea eax,[ecx+4]
    assert t.events[THIS] == {(TEXT_VA, "arith")}


# ---- stack slots after a clear ------------------------------------------------

def test_an_argument_slot_reads_unknown_after_the_frame_escapes_into_a_call():
    # push ecx; lea eax,[esp]; push eax; call f; mov ecx,[esp+0Ch] (the first argument,
    # never written here, but f had the frame's address)
    callee = TEXT_VA + 0x80
    buf = bytearray(bytes.fromhex("51 8d 04 24 50"))
    T.rel_call(buf, TEXT_VA, callee)
    buf += bytes.fromhex("8b 4c 24 0c")
    load = TEXT_VA + len(buf)
    buf += bytes.fromhex("8b 01 c3")
    code, t = trace({TEXT_VA: bytes(buf), callee: b"\xc3"})
    assert reg_at(t, load, ECX) is None


# ---- calls and ESP ------------------------------------------------------------

def _push_call_pop(callee_body, pushes=b"\x51"):
    callee = TEXT_VA + 0x80
    buf = bytearray(pushes)
    T.rel_call(buf, TEXT_VA, callee)
    load = TEXT_VA + len(buf) + 1
    buf += bytes.fromhex("59 8b 01 c3")                      # pop ecx; mov eax,[ecx]
    code, t = trace({TEXT_VA: bytes(buf), callee: callee_body})
    return code, t, load, callee


def test_a_call_to_a_function_with_no_ret_leaves_esp_unknown():
    code, t, load, callee = _push_call_pop(bytes.fromhex("eb fe"))      # jmp $
    assert callee not in code.has_ret
    assert reg_at(t, load, ECX) is None


def test_a_call_to_a_helper_that_sets_ebp_leaves_esp_unknown():
    # push ecx; push edx; call f; pop ecx -- f is `mov ebp,eax; ret 4`, which a regular
    # callee's cleanup would make exact
    code, t, load, callee = _push_call_pop(bytes.fromhex("89 c5 c2 04 00"),
                                           pushes=bytes.fromhex("51 52"))
    assert callee in code.sets_ebp and callee not in code.irregular_esp
    assert reg_at(t, load, ECX) is None


def test_after_a_helper_that_sets_ebp_an_ebp_operand_is_not_a_frame_slot():
    # push ebp; mov ebp,esp; call f; mov ecx,[ebp+8]: f set EBP, so this is not the
    # first argument
    callee = TEXT_VA + 0x80
    buf = bytearray(bytes.fromhex("55 89 e5"))
    T.rel_call(buf, TEXT_VA, callee)
    buf += bytes.fromhex("8b 4d 08")
    load = TEXT_VA + len(buf)
    buf += bytes.fromhex("8b 01 c9 c3")
    code, t = trace({TEXT_VA: bytes(buf), callee: bytes.fromhex("89 c5 c3")})
    assert callee in code.sets_ebp
    assert reg_at(t, load, ECX) != ("arg", 4)


# ---- every way a function materializes its own frame's address ----------------

def _ebp_frame_case(middle: bytes):
    """push ebp; mov ebp,esp; push ecx; <middle>; mov [esi],edx; pop ecx; mov eax,[ecx];
    leave; ret -- ECX at the load. A write through an unknown pointer clears every
    slot only once the frame's address has escaped."""
    body = bytes.fromhex("55 89 e5 51") + middle + bytes.fromhex("89 16 59 8b 01 c9 c3")
    code, t = trace({TEXT_VA: body})
    return t, TEXT_VA + 4 + len(middle) + 3


@pytest.mark.parametrize("name,middle,escapes", [
    ("nothing", b"\x90", False),
    ("push ebp after the frame is set", bytes.fromhex("55 58"), True),
    ("lea eax,[ebp-4]", bytes.fromhex("8d 45 fc"), True),
    ("mov eax,ebp", bytes.fromhex("89 e8"), True),
    ("mov eax,esp", bytes.fromhex("89 e0"), True),
    ("push esp", bytes.fromhex("54 58"), True),
])
def test_every_way_the_frame_address_escapes_clears_the_slots(name, middle, escapes):
    t, load = _ebp_frame_case(middle)
    assert t.frame_escapes is escapes, name
    assert reg_at(t, load, ECX) == (None if escapes else THIS), name


# ---- interior points reached without a modelled edge ------------------------------

def _handler_body():
    """mov esi,ecx; jmp JOIN; HANDLER: mov esi,edx; JOIN: mov eax,[esi]; ret."""
    return bytes.fromhex("8b f1 eb 02 89 d6 8b 06 c3"), TEXT_VA + 4, TEXT_VA + 6


def test_an_interior_point_named_by_a_relocated_dword_is_entered_with_no_facts():
    body, handler, join = _handler_body()
    code, t = trace({TEXT_VA: body}, rdata=struct.pack("<I", handler), relocated={RDATA_VA})
    assert t.unmodelled == [handler]
    assert reg_at(t, join, ESI) is None


def test_an_interior_point_another_function_branches_to_is_entered_with_no_facts():
    body, handler, join = _handler_body()
    other = TEXT_VA + 0x80
    jmp = b"\xe9" + struct.pack("<i", handler - (other + 5))
    code, t = trace({TEXT_VA: body, other: jmp})
    assert t.unmodelled == [handler]
    assert reg_at(t, join, ESI) is None
