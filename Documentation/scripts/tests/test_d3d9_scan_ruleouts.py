"""Planted x86 controls for the M7 scanner's sound ceiling rule-outs R1, R2, R3.

Each test authors a tiny 32-bit image and runs the production `layer_a`, `Attribution`
and `rule_outs`. Three kinds of test:

  * a positive: a site the rule must remove from the ceiling;
  * a refusal: a site shaped like the rule that it must NOT remove, because one of
    the rule's conditions fails (most were written by the two reviewers of the first
    version, and failed against it);
  * a near-miss d3d9 positive per rule: a real d3d9 call shaped as closely as the
    rule allows, which the rule must not touch; where Layer B attributes it, it is a
    control, and `control_sites_ruled_out` must stay empty. A mutation test then
    disables the rule's guard and shows the control does go red.

No executable, map, replay, game or device is opened.
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d3d9_methods as D  # noqa: E402
import test_d3d9_scan_transfer as T  # noqa: E402  (shared authored-image builder)

IFACES = T.IFACES
TEXT_VA = T.TEXT_VA
RDATA_VA = T.RDATA_VA
DATA_VA = T.DATA_VA
D3D9_SLOT_VA = T.D3D9_SLOT_VA
D3D9_THUNK = T.D3D9_THUNK
G = DATA_VA + 0x20                 # an R1 table global
OBJ = DATA_VA + 0x200              # an R3 static object

R1 = "R1_constant_function_table_global"
R2 = "R2_this_of_rtti_confirmed_vtable_member"
R3 = "R3_static_or_embedded_image_object"


def indirect_call_va(code, fn):
    """The VA of the indirect call instruction in function `fn` (the Layer A site key)."""
    return next(i.ip for i in code.instructions(fn)
                if i.mnemonic == D.Mnemonic.CALL
                and i.op0_kind in (D.OpKind.MEMORY, D.OpKind.REGISTER))


def run(functions, outside=None, **kw):
    """(img, code, attr, layer_a, rule_outs) over authored functions, with a fixpoint.
    `outside` is code laid at TEXT_VA in no function extent: only the linear sweep
    decodes it."""
    functions = dict(functions)
    if outside is not None:
        assert TEXT_VA not in functions
        functions[TEXT_VA] = outside
    lo = min(functions)
    hi = max(a + len(b) for a, b in functions.items())
    blob = bytearray(b"\xcc" * (hi - lo))
    for a, b in functions.items():
        blob[a - lo:a - lo + len(b)] = b
    assert lo == TEXT_VA
    img = T.FakeImage(bytes(blob), **kw)
    code = D.Code(img, T.fmap_for([f for f in functions if outside is None or f != TEXT_VA]))
    if outside is not None:
        assert code.owner(TEXT_VA) is None
    vtinfo = D.find_cpp_vtables(img)
    a = D.layer_a(code)
    attr = D.Attribution(code, IFACES, vtinfo)
    attr.run(deadline=time.time() + 5)
    assert attr.stopped_by == "fixpoint"
    ro = D.rule_outs(code, a, attr, IFACES)
    return img, code, attr, a, ro


def d3d9_positive_function(entry):
    """A function that gets an IDirect3D9 from Direct3DCreate9 and calls Release on it.
    Its call is attributed; its receiver is a call return, which no rule can match."""
    buf = bytearray(b"\x83\xec\x10")             # sub esp,10h
    T.rel_call(buf, entry, D3D9_THUNK)           # call Direct3DCreate9  -> eax
    buf += bytes.fromhex("89 c2"                 # mov edx,eax
                         "8b 02"                  # mov eax,[edx]   (vtable)
                         "ff 50 08"               # call [eax+8]    (IDirect3D9::Release)
                         "c3")
    return bytes(buf)


def _thunk_bytes():
    return bytes(b"\xff\x25" + struct.pack("<I", D3D9_SLOT_VA))   # jmp [slot]


SEED_KW = dict(imports={D3D9_SLOT_VA: ("d3d9.dll", "Direct3DCreate9")},
               thunks={D3D9_THUNK: D3D9_SLOT_VA})


def base_functions(outside=False):
    """The d3d9 positive and its thunk; with `outside` the positive moves to
    TEXT_VA+300h so TEXT_VA is free for code outside every function."""
    at = TEXT_VA + 0x300 if outside else TEXT_VA
    return {at: d3d9_positive_function(at), D3D9_THUNK: _thunk_bytes()}


def cut_short(monkeypatch, entry):
    """Stop `entry`'s must-analysis after one block visit, so it does not converge."""
    monkeypatch.setattr(D, "MUST_VISITS_PER_LEADER", 0)
    real = D._MustTrace.run

    def run_cut(self):
        old = D.MAX_BLOCK_VISITS
        D.MAX_BLOCK_VISITS = 1 if self.entry == entry else old
        try:
            return real(self)
        finally:
            D.MAX_BLOCK_VISITS = old
    monkeypatch.setattr(D._MustTrace, "run", run_cut)


def assert_control_clean(ro):
    for rule in (R1, R2, R3):
        assert ro[rule]["control_sites_ruled_out"] == [], (rule, ro[rule])


def d3d9_release_attributed(attr):
    return any("IDirect3D9" in r["interfaces"] and r["method"] == "Release"
               for r in attr.calls.values())


def _rtti_rdata(*slots, name=b".?AVFoo@@"):
    """.rdata with a COL at 0, a Type Descriptor at 16 and a class vtable VT at 0x40 whose
    VT-4 is the COL pointer and whose slots are `slots`. Returns (bytes, VT)."""
    col, td, vt = RDATA_VA, RDATA_VA + 16, RDATA_VA + 0x40
    buf = bytearray(b"\0" * (0x40 + 4 * len(slots) + 8))
    struct.pack_into("<I", buf, 0, 0)              # COL signature = 0
    struct.pack_into("<I", buf, 12, td)            # COL.pTypeDescriptor
    buf[td - RDATA_VA + 8: td - RDATA_VA + 8 + len(name) + 1] = name + b"\0"
    struct.pack_into("<I", buf, (vt - 4) - RDATA_VA, col)
    for k, fn in enumerate(slots):
        struct.pack_into("<I", buf, vt - RDATA_VA + 4 * k, fn)
    return bytes(buf), vt


def _second_vtable(rdata, member, name=b".?AVBar@@"):
    """Append a second COL + TD + vtable at 0x80.. that lists `member`. Returns
    (bytes, VT2)."""
    buf = bytearray(rdata) + b"\0" * max(0, 0xC8 - len(rdata))
    col2, td2, vt2 = RDATA_VA + 0x80, RDATA_VA + 0x90, RDATA_VA + 0xC0
    struct.pack_into("<I", buf, 0x80, 0)
    struct.pack_into("<I", buf, 0x80 + 12, td2)
    buf[td2 - RDATA_VA + 8: td2 - RDATA_VA + 8 + len(name) + 1] = name + b"\0"
    struct.pack_into("<I", buf, (vt2 - 4) - RDATA_VA, col2)
    struct.pack_into("<I", buf, vt2 - RDATA_VA, member)
    return bytes(buf), vt2


# ===========================================================================
# R1: a vtable read from a constant function-table global

TABLE_READER = TEXT_VA + 0x100
INIT = TEXT_VA + 0x140


def r1_image(extra=b"", init_value=RDATA_VA, outside=None, functions_extra=None, **kw):
    """G is read by `mov eax,[G]; call [eax+8]` and written once by `mov [G],VALUE`,
    followed by `extra` in the same initialiser."""
    functions = base_functions(outside is not None)
    functions.update(functions_extra or {})
    functions[TABLE_READER] = (bytes.fromhex("8b 05") + struct.pack("<I", G)
                               + bytes.fromhex("ff 50 08") + b"\xc3")
    functions[INIT] = (bytes.fromhex("c7 05") + struct.pack("<I", G)
                       + struct.pack("<I", init_value) + extra + b"\xc3")
    return run(functions, outside=outside, **SEED_KW, **kw)


def test_r1_rules_out_a_constant_function_table_global():
    img, code, attr, a, ro = r1_image()
    assert d3d9_release_attributed(attr)
    r1 = ro[R1]
    assert r1["sites"] == [hex(indirect_call_va(code, TABLE_READER))]
    assert r1["globals"] == [hex(G)]
    assert r1["removed"] == 1
    assert_control_clean(ro)


@pytest.mark.parametrize("name,extra", [
    ("mov [G],eax", bytes.fromhex("89 05") + struct.pack("<I", G)),
    ("lea eax,[G]", bytes.fromhex("8d 05") + struct.pack("<I", G)),
    ("pop dword [G]", bytes.fromhex("50 8f 05") + struct.pack("<I", G)),
    ("or [G],eax", bytes.fromhex("09 05") + struct.pack("<I", G)),
    ("xchg [G],eax", bytes.fromhex("87 05") + struct.pack("<I", G)),
    ("add [G],eax", bytes.fromhex("01 05") + struct.pack("<I", G)),
    ("add dword [G],4", bytes.fromhex("83 05") + struct.pack("<I", G) + b"\x04"),
    ("cmpxchg [G],ecx", bytes.fromhex("0f b1 0d") + struct.pack("<I", G)),
    ("movd [G],xmm0", bytes.fromhex("66 0f 7e 05") + struct.pack("<I", G)),
    ("mov byte [G+1],al", bytes.fromhex("a2") + struct.pack("<I", G + 1)),
    ("mov [edx+G],eax", bytes.fromhex("89 82") + struct.pack("<I", G)),
    ("mov [G+eax*4],ecx", bytes.fromhex("89 0c 85") + struct.pack("<I", G)),
    ("push offset G; pop eax", bytes.fromhex("68") + struct.pack("<I", G) + b"\x58"),
    ("enclosing object: mov ecx,G-4; mov [ecx+4],eax",
     bytes.fromhex("b9") + struct.pack("<I", G - 4) + bytes.fromhex("89 41 04")),
    ("enclosing object: lea ecx,[G-8]; mov [ecx+8],eax",
     bytes.fromhex("8d 0d") + struct.pack("<I", G - 8) + bytes.fromhex("89 41 08")),
    ("indexed from the enclosing object: mov [G-10h+eax*4],ecx",
     bytes.fromhex("b8") + struct.pack("<I", G - 0x10)       # mov eax,G-10h (base)
     + bytes.fromhex("89 0c 85") + struct.pack("<I", G - 0x10)),
])
def test_r1_refuses_a_global_with_any_other_write_or_address_use(name, extra):
    img, code, attr, a, ro = r1_image(extra)
    assert d3d9_release_attributed(attr)
    assert ro[R1]["removed"] == 0, (name, ro[R1])
    assert ro[R1]["globals_refused"][hex(G)], name


def test_r1_refuses_a_global_written_with_a_writable_address():
    img, code, attr, a, ro = r1_image(init_value=DATA_VA + 0x400)
    assert ro[R1]["removed"] == 0


def test_r1_records_the_enclosing_object_and_its_escapes():
    # G-0x10 is materialized and stored to memory (its address escapes) but nothing the
    # analysis can see writes G through it: R1 fires, and the receipt says the object
    # holding G and where its address leaves the analysis (the stated hole).
    extra = (bytes.fromhex("b9") + struct.pack("<I", G - 0x10)      # mov ecx,G-10h
             + bytes.fromhex("89 0d") + struct.pack("<I", DATA_VA + 0x80))  # mov [P],ecx
    img, code, attr, a, ro = r1_image(extra)
    assert ro[R1]["removed"] == 1
    obj = ro[R1]["objects"][hex(G)]
    assert obj["object_base"] == hex(G - 0x10)
    assert obj["offset_in_object"] == hex(0x10)
    assert obj["object_address_escapes_by_kind"] == {"store": 1}


# ---- R1 near-miss d3d9 positive: a d3d9 vtable cached in a global ----------

def r1_near_miss():
    # The d3d9 function copies the IDirect3D9 vtable pointer into G (`mov [G],eax`);
    # the reader then calls through it. That is a real d3d9 call, attributed by
    # Layer B, shaped exactly like R1's sites (the vtable read from an absolute global).
    functions = {D3D9_THUNK: _thunk_bytes()}
    buf = bytearray(b"\x83\xec\x10")
    T.rel_call(buf, TEXT_VA, D3D9_THUNK)
    buf += bytes.fromhex("8b 00") + bytes.fromhex("a3") + struct.pack("<I", G) + b"\xc3"
    functions[TEXT_VA] = bytes(buf)                     # mov eax,[eax]; mov [G],eax
    functions[TABLE_READER] = (bytes.fromhex("8b 05") + struct.pack("<I", G)
                               + bytes.fromhex("ff 50 08") + b"\xc3")
    return run(functions, **SEED_KW)


def test_r1_near_miss_d3d9_vtable_in_a_global_is_a_clean_control():
    img, code, attr, a, ro = r1_near_miss()
    site = indirect_call_va(code, TABLE_READER)
    assert site in attr.calls and attr.calls[site]["method"] == "Release"
    assert site in a["sites"] and a["sites"][site]["vt_load"]["base"] is None
    assert ro["must_analysis"]["control_sites"] == 1
    assert ro[R1]["removed"] == 0
    assert_control_clean(ro)


def test_r1_control_goes_red_when_the_constant_check_is_disabled(monkeypatch):
    # Mutation: every candidate global is called a constant table. The very predicate
    # that removes ceiling sites then fires on the attributed d3d9 call.
    monkeypatch.setattr(D, "_const_table_globals",
                        lambda ctx, cands: ({g: True for g in cands},
                                            {g: None for g in cands}))
    img, code, attr, a, ro = r1_near_miss()
    assert ro[R1]["control_sites_ruled_out"] == [hex(indirect_call_va(code, TABLE_READER))]


# ===========================================================================
# R2: `this` of an RTTI-confirmed vtable member

MEMBER = TEXT_VA + 0x100
CTOR = TEXT_VA + 0x140
CALLER = TEXT_VA + 0x200
MEMBER_BODY = bytes.fromhex("8b 01 ff 50 08 c3")      # mov eax,[ecx]; call [eax+8]; ret


def r2_image(callers=None, rdata=None, ctor=None, member=None, **kw):
    """MEMBER, listed in one RTTI vtable, calls through `this`; CTOR stores the vtable.
    `callers` adds {entry: builder(entry)}; `member` replaces MEMBER's body."""
    if rdata is None:
        rdata, vt = _rtti_rdata(MEMBER)
    else:
        vt = RDATA_VA + 0x40
    functions = base_functions()
    functions[MEMBER] = member(MEMBER) if callable(member) else (member or MEMBER_BODY)
    functions[CTOR] = ctor or (bytes.fromhex("c7 01") + struct.pack("<I", vt) + b"\xc3")
    for entry, build in (callers or {}).items():
        functions[entry] = build(entry)
    img, code, attr, a, ro = run(functions, rdata=rdata, **SEED_KW, **kw)
    return img, code, attr, a, ro, vt


def member_site(code):
    return indirect_call_va(code, MEMBER)


def test_r2_rules_out_a_this_call_in_an_rtti_vtable_member():
    img, code, attr, a, ro, vt = r2_image()
    assert vt in attr.class_vt
    assert D.rtti_type_name(img, vt) == ".?AVFoo@@"
    assert ro[R2]["sites"] == [hex(member_site(code))]
    assert d3d9_release_attributed(attr)
    assert_control_clean(ro)


def test_r2_accepts_a_member_that_passes_its_own_this_to_the_member():
    # A second member `mid` (in its own RTTI vtable) calls MEMBER with its unchanged
    # `this`: that entry hands MEMBER an image object, so R2 still fires.
    mid = TEXT_VA + 0x1C0
    rdata, vt = _rtti_rdata(MEMBER)
    rdata, vt2 = _second_vtable(rdata, mid, name=b".?AVMid@@")

    def mid_body(entry):
        buf = bytearray()
        T.rel_call(buf, entry, MEMBER)
        return bytes(buf + b"\xc3")
    ctor = (bytes.fromhex("c7 01") + struct.pack("<I", vt)
            + bytes.fromhex("c7 02") + struct.pack("<I", vt2) + b"\xc3")
    img, code, attr, a, ro, _ = r2_image({mid: mid_body}, rdata=rdata, ctor=ctor)
    assert ro[R2]["sites"] == [hex(member_site(code))]


def test_r2_refuses_a_vtable_member_without_rtti():
    rdata, vt = _rtti_rdata(MEMBER)
    rdata = bytearray(rdata)
    struct.pack_into("<I", rdata, (vt - 4) - RDATA_VA, 0)      # clear the COL pointer
    img, code, attr, a, ro, _ = r2_image(rdata=bytes(rdata))
    assert vt in attr.class_vt
    assert D.rtti_type_name(img, vt) is None
    assert ro[R2]["removed"] == 0


def test_rtti_name_must_be_a_decorated_type_name():
    # A readable string at TD+8 that is not `.?A...` is not a Type Descriptor name.
    rdata, vt = _rtti_rdata(MEMBER, name=b"hello")
    img, code, attr, a, ro, _ = r2_image(rdata=rdata)
    assert D.rtti_type_name(img, vt) is None
    assert ro[R2]["removed"] == 0


def test_r2_excludes_a_function_folded_across_two_vtables():
    rdata, vt1 = _rtti_rdata(MEMBER)
    rdata, vt2 = _second_vtable(rdata, MEMBER)
    ctor = (bytes.fromhex("c7 01") + struct.pack("<I", vt1)
            + bytes.fromhex("c7 02") + struct.pack("<I", vt2) + b"\xc3")
    img, code, attr, a, ro, _ = r2_image(rdata=rdata, ctor=ctor)
    assert vt1 in attr.class_vt and vt2 in attr.class_vt
    assert len(attr.entry_class[MEMBER]) == 2
    assert ro[R2]["removed"] == 0
    assert ro[R2]["comdat_guard_excluded_by_reason"]["listed in several vtables"] == 1


def test_r2_excludes_a_member_listed_in_an_undiscovered_second_vtable():
    # The second vtable's constant is never stored by `mov [reg],VT` (its vptr store
    # was elided), so find_cpp_vtables never sees it; its relocated slot still names
    # the member, so the body is shared and R2 must not fire.
    rdata, vt1 = _rtti_rdata(MEMBER)
    rdata, vt2 = _second_vtable(rdata, MEMBER)
    img, code, attr, a, ro, _ = r2_image(rdata=rdata, relocated={vt1, vt2})
    assert vt1 in attr.class_vt and vt2 not in attr.class_vt
    assert ro[R2]["removed"] == 0


def _caller(prefix: bytes, jmp=False):
    def build(entry):
        buf = bytearray(prefix)
        if jmp:
            buf += b"\xe9" + struct.pack("<i", MEMBER - (entry + len(buf) + 5))
        else:
            T.rel_call(buf, entry, MEMBER)
            buf += b"\xc3"
        return bytes(buf)
    return build


@pytest.mark.parametrize("name,build", [
    ("a field of another object: mov ecx,[esi+10h]", _caller(bytes.fromhex("8b 4e 10"))),
    ("an argument: mov ecx,[esp+4]", _caller(bytes.fromhex("8b 4c 24 04"))),
    ("an indexed load the analysis cannot place: mov ecx,[eax+ebx*4]",
     _caller(bytes.fromhex("8b 0c 98"))),
    ("a meet of two loads", _caller(bytes.fromhex("85 c0 74 05 8b 4e 10 eb 03 8b 4f 14"))),
    ("a tail jump with an argument in ECX", _caller(bytes.fromhex("8b 4c 24 04"), jmp=True)),
    ("this of a function that is not a vtable member", _caller(b"")),
])
def test_r2_excludes_a_member_entered_directly_with_a_foreign_ecx(name, build):
    img, code, attr, a, ro, _ = r2_image({CALLER: build})
    assert ro[R2]["removed"] == 0, (name, ro[R2])
    assert ro[R2]["comdat_guard_excluded_functions"] == 1


def test_r2_excludes_a_member_whose_address_is_taken():
    def callback(entry):                     # push offset MEMBER; call ...; ret
        return bytes.fromhex("68") + struct.pack("<I", MEMBER) + b"\x58\xc3"
    img, code, attr, a, ro, _ = r2_image({CALLER: callback})
    assert ro[R2]["removed"] == 0
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {"address is an immediate": 1}


def test_r2_excludes_a_member_named_by_a_relocated_data_dword():
    # a callback table in .rdata, past the end of the vtable run, names MEMBER
    rdata, vt = _rtti_rdata(MEMBER)
    rdata = bytearray(rdata) + b"\0" * (0x70 - len(rdata))
    struct.pack_into("<I", rdata, 0x60, MEMBER)
    img, code, attr, a, ro, _ = r2_image(rdata=bytes(rdata), relocated={RDATA_VA + 0x60})
    assert ro[R2]["removed"] == 0
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {
        "address in a relocated dword outside every vtable": 1}


# ---- R2 near-miss d3d9 positives: an IDirect3D9 handed to a member ---------

def r2_d3d9_into(target):
    def build(entry):          # sub esp,10h; call Direct3DCreate9; mov ecx,eax; call target
        buf = bytearray(b"\x83\xec\x10")
        T.rel_call(buf, entry, D3D9_THUNK)
        buf += bytes.fromhex("89 c1")
        T.rel_call(buf, entry, target)
        return bytes(buf + b"\xc3")
    return build


def test_r2_near_miss_d3d9_pointer_as_a_members_this_is_a_clean_control():
    img, code, attr, a, ro, _ = r2_image({CALLER: r2_d3d9_into(MEMBER)})
    site = member_site(code)
    assert site in attr.calls and "IDirect3D9" in attr.calls[site]["interfaces"]
    assert ro[R2]["removed"] == 0
    assert_control_clean(ro)


def r2_transitive():
    # MID, a member of its own RTTI class, passes its own `this` to MEMBER; a plain
    # function hands MID an IDirect3D9. MEMBER's call is then a real d3d9 call, and the
    # exclusion must reach MEMBER through MID.
    mid = TEXT_VA + 0x1C0
    rdata, vt = _rtti_rdata(MEMBER)
    rdata, vt2 = _second_vtable(rdata, mid, name=b".?AVMid@@")

    def mid_body(entry):
        buf = bytearray()
        T.rel_call(buf, entry, MEMBER)
        return bytes(buf + b"\xc3")
    ctor = (bytes.fromhex("c7 01") + struct.pack("<I", vt)
            + bytes.fromhex("c7 02") + struct.pack("<I", vt2) + b"\xc3")
    return r2_image({mid: mid_body, CALLER: r2_d3d9_into(mid)}, rdata=rdata, ctor=ctor)


def test_r2_exclusion_propagates_along_this_passing_calls_and_the_control_stays_clean():
    img, code, attr, a, ro, _ = r2_transitive()
    site = member_site(code)
    assert site in attr.calls                                   # a real d3d9 call
    assert ro[R2]["removed"] == 0
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {
        "entered with an ECX that is not the caller's this": 1,
        "receives this from an excluded member": 1}
    assert_control_clean(ro)


@pytest.mark.parametrize("image", [lambda: r2_image({CALLER: r2_d3d9_into(MEMBER)}),
                                   r2_transitive])
def test_r2_control_goes_red_when_the_comdat_guard_is_disabled(monkeypatch, image):
    monkeypatch.setattr(D, "_r2_comdat_guard", lambda ctx: (set(), {}))
    img, code, attr, a, ro, _ = image()
    assert hex(member_site(code)) in ro[R2]["control_sites_ruled_out"]


# ===========================================================================
# R3: a static image object whose vptr slot holds only image vtables

R3_READER = TEXT_VA + 0x100
R3_INIT = TEXT_VA + 0x140
METHOD = TEXT_VA + 0x180            # slot 2 of the vtable: `call [eax+8]` reaches it
VT3 = RDATA_VA + 0x40               # the vtable `_rtti_rdata` lays out


def r3_image(init_tail=b"", method=b"\xc3", init=None, functions_extra=None,
             slots=(METHOD, METHOD, METHOD), outside=None, **kw):
    """R3_READER: mov eax,OBJ; mov eax,[eax]; call [eax+8]. R3_INIT: mov [OBJ],VT, then
    `init_tail`, then `mov [ecx],VT` so find_cpp_vtables sees VT. VT lists `slots`."""
    rdata, vt = _rtti_rdata(*slots)
    assert vt == VT3
    functions = base_functions(outside is not None)
    functions[R3_READER] = (bytes.fromhex("b8") + struct.pack("<I", OBJ)
                            + bytes.fromhex("8b 00 ff 50 08") + b"\xc3")
    functions[R3_INIT] = init or (
        bytes.fromhex("c7 05") + struct.pack("<I", OBJ) + struct.pack("<I", vt)
        + init_tail + bytes.fromhex("c7 01") + struct.pack("<I", vt) + b"\xc3")
    functions[METHOD] = method
    functions.update(functions_extra or {})
    img, code, attr, a, ro = run(functions, rdata=rdata, outside=outside, **SEED_KW, **kw)
    return img, code, attr, a, ro, vt


def test_r3_rules_out_a_static_object_with_an_image_vptr():
    img, code, attr, a, ro, vt = r3_image()
    assert vt in attr.class_vt
    r3 = ro[R3]
    assert r3["sites"] == [hex(indirect_call_va(code, R3_READER))], r3
    assert_control_clean(ro)


@pytest.mark.parametrize("name,tail", [
    ("a non-vtable immediate", bytes.fromhex("c7 05") + struct.pack("<I", OBJ)
     + struct.pack("<I", 0x12345)),
    ("a register store through the object's address: mov ecx,OBJ; mov [ecx],eax",
     bytes.fromhex("b9") + struct.pack("<I", OBJ) + bytes.fromhex("89 01")),
    ("a byte store to the slot", bytes.fromhex("a2") + struct.pack("<I", OBJ + 1)),
    ("the object's address stored to memory", bytes.fromhex("c7 05")
     + struct.pack("<I", DATA_VA + 0x80) + struct.pack("<I", OBJ)),
    ("the object passed to a call (a constructor)",
     bytes.fromhex("b9") + struct.pack("<I", OBJ) + b"\xe8" + struct.pack("<i", 0)),
])
def test_r3_refuses_a_slot_it_cannot_prove_holds_only_image_vtables(name, tail):
    img, code, attr, a, ro, vt = r3_image(tail)
    assert ro[R3]["removed"] == 0, (name, ro[R3])
    assert ro[R3]["slots_refused"], name


def test_r3_refuses_when_a_dispatched_method_rewrites_the_slot():
    # METHOD (what `call [eax+8]` reaches) stores an unknown value into [this].
    img, code, attr, a, ro, vt = r3_image(method=bytes.fromhex("89 01 c3"))
    assert ro[R3]["removed"] == 0
    assert "writes the slot" in next(iter(ro[R3]["slots_refused"].values()))


def test_r3_near_miss_copied_d3d9_vtable_stays_in_the_ceiling():
    # The d3d9 function copies the IDirect3D9 vtable pointer into the static object's
    # vptr slot, so the reader's dispatch is a real d3d9 call (Layer B does not type it:
    # it stays in the ceiling). R3 must not remove it.
    buf = bytearray(b"\x83\xec\x10")
    T.rel_call(buf, TEXT_VA, D3D9_THUNK)
    buf += bytes.fromhex("8b 00") + bytes.fromhex("a3") + struct.pack("<I", OBJ) + b"\xc3"
    img, code, attr, a, ro, vt = r3_image(functions_extra={TEXT_VA: bytes(buf)})
    site = indirect_call_va(code, R3_READER)
    assert site in a["sites"] and site not in attr.calls
    assert ro["ceiling_before_rules"] == 1
    assert ro[R3]["removed"] == 0
    assert_control_clean(ro)


def test_r3_near_miss_goes_red_when_the_slot_check_is_disabled(monkeypatch):
    # Mutation: every slot is taken to hold only image vtables; the d3d9 dispatch
    # through the copied vtable pointer is then removed from the ceiling.
    monkeypatch.setattr(D, "_slot_holds_only_image_vtables",
                        lambda ctx, obj, vt_disp, dispatch: (True, None))
    buf = bytearray(b"\x83\xec\x10")
    T.rel_call(buf, TEXT_VA, D3D9_THUNK)
    buf += bytes.fromhex("8b 00") + bytes.fromhex("a3") + struct.pack("<I", OBJ) + b"\xc3"
    img, code, attr, a, ro, vt = r3_image(functions_extra={TEXT_VA: bytes(buf)})
    assert ro[R3]["sites"] == [hex(indirect_call_va(code, R3_READER))]


# ===========================================================================
# the controls are real attributed d3d9 sites, evaluated with the same predicate


def test_the_control_is_the_attributed_d3d9_population():
    img, code, attr, a, ro = r1_image()
    assert d3d9_release_attributed(attr)
    assert ro["must_analysis"]["control_sites"] == len(set(attr.calls) & set(a["sites"]))
    assert ro["ceiling_before_rules"] == 1 and ro[R1]["removed"] == 1
    assert ro["ceiling_after_sound_rule_outs"] == 0
    assert_control_clean(ro)


def test_r2_excludes_a_member_entered_by_another_member_with_an_unknown_ecx():
    # MID is itself a member, but the ECX it passes is an indexed load the analysis
    # cannot place: unknown is not `this`.
    mid = TEXT_VA + 0x1C0
    rdata, vt = _rtti_rdata(MEMBER)
    rdata, vt2 = _second_vtable(rdata, mid, name=b".?AVMid@@")
    ctor = (bytes.fromhex("c7 01") + struct.pack("<I", vt)
            + bytes.fromhex("c7 02") + struct.pack("<I", vt2) + b"\xc3")

    def mid_body(entry):                     # mov ecx,[eax+ebx*4]; call MEMBER; ret
        buf = bytearray(bytes.fromhex("8b 0c 98"))
        T.rel_call(buf, entry, MEMBER)
        return bytes(buf + b"\xc3")
    img, code, attr, a, ro, _ = r2_image({mid: mid_body}, rdata=rdata, ctor=ctor)
    assert ro[R2]["removed"] == 0
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {"entered with an unknown ECX": 1}


# ---------------------------------------------------------------------------
# Guards the soundness text names that no test above exercised (confirm review):
# each passes on the scanner and goes red when its guard is reverted.
def test_r1_refuses_when_an_address_two_bytes_into_g_is_materialized():
    img, code, attr, a, ro = r1_image(bytes.fromhex("68") + struct.pack("<I", G + 2) + b"\x58")
    assert ro[R1]["removed"] == 0, ro[R1]


def test_r1_refuses_when_a_relocated_data_dword_holds_g():
    rdata = bytearray(b"\0" * 0x20)
    struct.pack_into("<I", rdata, 0x10, G)
    img, code, attr, a, ro = r1_image(rdata=bytes(rdata), relocated={RDATA_VA + 0x10})
    assert ro[R1]["removed"] == 0, ro[R1]


def test_r1_refuses_a_misaligned_dword_store_overlapping_g():
    # mov dword [G-2],RDATA_VA: a read-only immediate, but half of it lands in G
    extra = bytes.fromhex("c7 05") + struct.pack("<I", G - 2) + struct.pack("<I", RDATA_VA)
    img, code, attr, a, ro = r1_image(extra)
    assert ro[R1]["removed"] == 0, ro[R1]


def test_r1_refuses_a_rep_store_through_a_constant_base_that_reaches_g():
    # mov edi,G-8; mov ecx,4; rep stosd
    extra = (bytes.fromhex("bf") + struct.pack("<I", G - 8) + bytes.fromhex("b9 04 00 00 00")
             + bytes.fromhex("f3 ab"))
    img, code, attr, a, ro = r1_image(extra)
    assert ro[R1]["removed"] == 0, ro[R1]


def test_r2_excludes_a_member_entered_by_a_conditional_branch_with_a_foreign_ecx():
    def build(entry):   # mov ecx,[esp+4]; test eax,eax; jz MEMBER; ret
        buf = bytearray(bytes.fromhex("8b 4c 24 04 85 c0"))
        buf += b"\x0f\x84" + struct.pack("<i", MEMBER - (entry + len(buf) + 6))
        return bytes(buf + b"\xc3")
    img, code, attr, a, ro, _ = r2_image({CALLER: build})
    assert ro[R2]["removed"] == 0, ro[R2]


def test_r2_excludes_a_member_whose_address_is_a_lea():
    def build(entry):   # lea eax,[MEMBER]; ret
        return bytes.fromhex("8d 05") + struct.pack("<I", MEMBER) + b"\xc3"
    img, code, attr, a, ro, _ = r2_image({CALLER: build})
    assert ro[R2]["removed"] == 0, ro[R2]


def test_r2_excludes_a_member_entered_from_a_caller_whose_analysis_did_not_converge(monkeypatch):
    # CALLER is itself a member (listed in a second RTTI vtable). On its first pass ECX is
    # its `this` at the call; the loop's back edge then overwrites ECX. With the visit
    # guard at 1 the caller's trace stops before the back edge is seen.
    rdata, vt = _rtti_rdata(MEMBER)
    rdata, vt2 = _second_vtable(rdata, CALLER, name=b".?AVMid@@")
    ctor = (bytes.fromhex("c7 01") + struct.pack("<I", vt)
            + bytes.fromhex("c7 02") + struct.pack("<I", vt2) + b"\xc3")

    def build(entry):   # L: call MEMBER; mov ecx,[esp+4]; jmp L
        buf = bytearray()
        T.rel_call(buf, entry, MEMBER)
        buf += bytes.fromhex("8b 4c 24 04")
        buf += b"\xe9" + struct.pack("<i", entry - (entry + len(buf) + 5))
        return bytes(buf)
    cut_short(monkeypatch, CALLER)
    img, code, attr, a, ro, _ = r2_image({CALLER: build}, rdata=rdata, ctor=ctor)
    assert ro[R2]["removed"] == 0, ro[R2]


def test_r3_refuses_when_a_relocated_data_dword_holds_the_object_address():
    rdata, vt = _rtti_rdata(METHOD, METHOD, METHOD)
    rdata = bytearray(rdata) + b"\0" * (0x90 - len(rdata))
    struct.pack_into("<I", rdata, 0x80, OBJ)
    functions = base_functions()
    functions[R3_READER] = (bytes.fromhex("b8") + struct.pack("<I", OBJ)
                            + bytes.fromhex("8b 00 ff 50 08") + b"\xc3")
    functions[R3_INIT] = (bytes.fromhex("c7 05") + struct.pack("<I", OBJ) + struct.pack("<I", vt)
                          + bytes.fromhex("c7 01") + struct.pack("<I", vt) + b"\xc3")
    functions[METHOD] = b"\xc3"
    img, code, attr, a, ro = run(functions, rdata=bytes(rdata), relocated={RDATA_VA + 0x80},
                                 **SEED_KW)
    assert vt in attr.class_vt
    assert ro[R3]["removed"] == 0, ro[R3]


def test_r1_refuses_a_global_whose_initial_value_is_not_read_only():
    # every write sets G to a .rdata address, but G starts out holding a writable one
    img, code, attr, a, ro = r1_image(data={G: DATA_VA + 0x400})
    assert ro[R1]["removed"] == 0, ro[R1]
    assert "initial value" in ro[R1]["globals_refused"][hex(G)]


def test_r3_refuses_a_slot_whose_initial_value_is_not_a_class_vtable():
    img, code, attr, a, ro, vt = r3_image(data={OBJ: 0x12345})
    assert ro[R3]["removed"] == 0, ro[R3]
    assert "initial value" in next(iter(ro[R3]["slots_refused"].values()))


def test_r2_excludes_a_member_entered_from_code_outside_every_function():
    # The code at TEXT_VA (mov ecx,[esp+4]; call MEMBER) is in no function extent: only
    # the linear sweep decodes it, and its ECX is not traced.
    outside = bytearray(bytes.fromhex("8b 4c 24 04"))
    T.rel_call(outside, TEXT_VA, MEMBER)
    outside += b"\xc3"
    rdata, vt = _rtti_rdata(MEMBER)
    d3d9 = TEXT_VA + 0x300
    functions = {TEXT_VA: bytes(outside), d3d9: d3d9_positive_function(d3d9),
                 D3D9_THUNK: _thunk_bytes(), MEMBER: MEMBER_BODY,
                 CTOR: bytes.fromhex("c7 01") + struct.pack("<I", vt) + b"\xc3"}
    lo = min(functions)
    hi = max(x + len(b) for x, b in functions.items())
    blob = bytearray(b"\xcc" * (hi - lo))
    for x, b in functions.items():
        blob[x - lo:x - lo + len(b)] = b
    img = T.FakeImage(bytes(blob), rdata=rdata, **SEED_KW)
    code = D.Code(img, T.fmap_for([f for f in functions if f != TEXT_VA]))
    assert code.owner(TEXT_VA) is None
    a = D.layer_a(code)
    attr = D.Attribution(code, IFACES, D.find_cpp_vtables(img))
    attr.run(deadline=time.time() + 5)
    ro = D.rule_outs(code, a, attr, IFACES)
    assert ro[R2]["removed"] == 0, ro[R2]
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {
        "entered from code outside every function": 1}


# ---------------------------------------------------------------------------
# Guards the rule docstrings, the receipt text and the fold reports name, each with a
# planted case that passes on the scanner and goes red when that one guard is reverted
# (the w1b fold's mutation survey). Minors folded here: a vtable run that swallows an
# adjacent code-pointer table, a based access `[reg+X]` with a base the analysis cannot
# place, and a function naming the object that was not analysed to a fixpoint.

NAMER = TEXT_VA + 0x1C0


def _loop(prefix: bytes) -> bytes:
    """L: <prefix>; mov eax,edx; jmp L -- a loop whose back edge changes the state."""
    body = bytearray(prefix + bytes.fromhex("89 d0"))
    body += b"\xeb" + bytes([(-(len(body) + 2)) & 0xFF])
    return bytes(body)


# ---- R1 -------------------------------------------------------------------

def test_r1_refuses_a_misaligned_constant_base_store_overlapping_g():
    # mov ecx,G-10h; mov dword [ecx+0Eh],RDATA_VA: a read-only value, but 2 bytes below G
    extra = (b"\xb9" + struct.pack("<I", G - 0x10) + bytes.fromhex("c7 41 0e")
             + struct.pack("<I", RDATA_VA))
    img, code, attr, a, ro = r1_image(extra)
    assert ro[R1]["removed"] == 0, ro[R1]
    assert "(constant base)" in ro[R1]["globals_refused"][hex(G)]


def test_r1_refuses_a_write_into_its_object_through_a_base_the_analysis_cannot_place():
    # mov [edx+G-10h],eax with EDX unknown: under OBJECT_BOUNDS it lands anywhere in the
    # object that starts at G-10h, G included
    img, code, attr, a, ro = r1_image(bytes.fromhex("89 82") + struct.pack("<I", G - 0x10))
    assert ro[R1]["removed"] == 0, ro[R1]
    assert "cannot place" in ro[R1]["globals_refused"][hex(G)]


def test_r1_counts_a_lea_into_its_object_through_an_untraced_base_as_an_escape():
    # lea eax,[edx+G-10h]: a pointer into G's object at an unknown offset; R1 still fires
    # (the stated hole) and the receipt counts it
    img, code, attr, a, ro = r1_image(bytes.fromhex("8d 82") + struct.pack("<I", G - 0x10))
    assert ro[R1]["removed"] == 1
    assert ro[R1]["objects"][hex(G)]["object_address_escapes_by_kind"] == {"untraced base": 1}


def test_r1_refuses_a_global_set_to_a_code_address():
    # a table read out of G must be read-only data; a code address is not one
    img, code, attr, a, ro = r1_image(init_value=TEXT_VA + 0x10)
    assert ro[R1]["removed"] == 0, ro[R1]


def test_r1_refuses_a_global_that_nothing_writes():
    # G starts out holding a .rdata address and no visible write touches it: "never
    # written" is refused, since a write the scan cannot see is not ruled out
    functions = base_functions()
    functions[TABLE_READER] = (bytes.fromhex("8b 05") + struct.pack("<I", G)
                               + bytes.fromhex("ff 50 08") + b"\xc3")
    img, code, attr, a, ro = run(functions, data={G: RDATA_VA}, **SEED_KW)
    assert ro["ceiling_before_rules"] == 1
    assert ro[R1]["removed"] == 0
    assert ro[R1]["globals_refused"][hex(G)] == "never written"


def test_r1_refuses_when_a_function_naming_its_object_was_not_analysed(monkeypatch):
    # NAMER writes G through a constant base (mov ecx,G-10h; mov [ecx+10h],eax) in a
    # loop, and its analysis is cut short, so that write is not seen
    cut_short(monkeypatch, NAMER)
    namer = _loop(b"\xb9" + struct.pack("<I", G - 0x10) + bytes.fromhex("89 41 10"))
    img, code, attr, a, ro = r1_image(functions_extra={NAMER: namer})
    assert ro[R1]["removed"] == 0, ro[R1]
    assert "was not analysed to a fixpoint" in ro[R1]["globals_refused"][hex(G)]
    assert ro["must_analysis"]["functions_whose_analysis_did_not_converge"] == [hex(NAMER)]


def test_r1_refuses_when_code_outside_every_function_names_g():
    img, code, attr, a, ro = r1_image(outside=b"\x68" + struct.pack("<I", G) + b"\xc3")
    assert ro["ceiling_before_rules"] == 1
    assert ro[R1]["removed"] == 0
    assert "is materialized" in ro[R1]["globals_refused"][hex(G)]


def test_r1_refuses_an_indexed_write_from_its_object_in_code_outside_every_function():
    # the initialiser names G-10h (cmp eax,G-10h), so G's object starts there; code that
    # no function owns writes [G-10h+eax*4], seen only by the linear sweep
    img, code, attr, a, ro = r1_image(
        b"\x3d" + struct.pack("<I", G - 0x10),
        outside=bytes.fromhex("89 0c 85") + struct.pack("<I", G - 0x10) + b"\xc3")
    assert ro[R1]["removed"] == 0, ro[R1]
    assert "(absolute indexed)" in ro[R1]["globals_refused"][hex(G)]


# ---- R2 -------------------------------------------------------------------

def test_r2_excludes_a_member_that_calls_itself_with_a_foreign_ecx():
    def member(entry):   # mov eax,[ecx]; call [eax+8]; mov ecx,[esp+4]; call MEMBER; ret
        buf = bytearray(MEMBER_BODY[:-1] + bytes.fromhex("8b 4c 24 04"))
        T.rel_call(buf, entry, MEMBER)
        return bytes(buf + b"\xc3")
    img, code, attr, a, ro, _ = r2_image(member=member)
    assert ro[R2]["removed"] == 0, ro[R2]
    # after the indirect call ESP is unknown, so [esp+4] is too
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {"entered with an unknown ECX": 1}


def test_r2_refuses_a_member_site_whose_receiver_is_not_this():
    # mov ecx,[ecx+10h]; mov eax,[ecx]; call [eax+8]: the receiver is a field of `this`
    img, code, attr, a, ro, _ = r2_image(member=bytes.fromhex("8b 49 10") + MEMBER_BODY)
    assert ro["ceiling_before_rules"] == 1
    assert ro[R2]["removed"] == 0, ro[R2]


def test_r2_refuses_a_this_call_in_a_function_no_vtable_lists():
    plain = TEXT_VA + 0x280
    img, code, attr, a, ro, _ = r2_image({plain: lambda entry: MEMBER_BODY})
    assert ro["ceiling_before_rules"] == 2
    assert ro[R2]["sites"] == [hex(member_site(code))]


ADJ = TEXT_VA + 0x280


@pytest.mark.parametrize("named", [0, 2])
def test_r2_excludes_a_function_in_a_table_laid_right_after_the_vtable(named):
    # VT lists MEMBER; the dword after it starts another table, listing ADJ, which a
    # function names (`mov eax,offset VT+4`, or an address inside that dword). Read as
    # one run, ADJ would be a member of VT's class; it is excluded, MEMBER is not.
    rdata, vt = _rtti_rdata(MEMBER, ADJ)

    def namer(entry):
        return b"\xb8" + struct.pack("<I", vt + 4 + named) + b"\xc3"
    img, code, attr, a, ro, _ = r2_image({ADJ: lambda entry: MEMBER_BODY, NAMER: namer},
                                         rdata=rdata)
    assert attr.entry_class[ADJ] == {vt}               # the run swallowed the table
    assert ro[R2]["sites"] == [hex(member_site(code))]
    assert ro[R2]["comdat_guard_excluded_by_reason"] == {
        "listed after a named address inside a vtable run": 1}


def test_r2_rules_out_a_member_whose_own_vtable_slot_is_relocated():
    # a relocated vtable slot is the dispatch itself, not an address taken elsewhere
    rdata, vt = _rtti_rdata(MEMBER)
    img, code, attr, a, ro, _ = r2_image(rdata=rdata, relocated={vt})
    assert ro[R2]["sites"] == [hex(member_site(code))]


def test_rtti_refuses_a_col_with_a_nonzero_signature():
    rdata, vt = _rtti_rdata(MEMBER)
    rdata = bytearray(rdata)
    struct.pack_into("<I", rdata, 0, 1)                # COL.signature
    img, code, attr, a, ro, _ = r2_image(rdata=bytes(rdata))
    assert D.rtti_type_name(img, vt) is None
    assert ro[R2]["removed"] == 0


def test_rtti_refuses_a_col_in_code():
    # the dword below VT points into .text, at bytes shaped like a COL (signature 0,
    # TD pointer at +12): a COL is read-only data, never code
    rdata, vt = _rtti_rdata(MEMBER)
    fake_col = TEXT_VA + 0x2C0
    rdata = bytearray(rdata)
    struct.pack_into("<I", rdata, (vt - 4) - RDATA_VA, fake_col)
    col_bytes = b"\0" * 12 + struct.pack("<I", RDATA_VA + 16)
    img, code, attr, a, ro, _ = r2_image({fake_col: lambda entry: col_bytes},
                                         rdata=bytes(rdata))
    assert D.rtti_type_name(img, vt) is None
    assert ro[R2]["removed"] == 0


# ---- R3 -------------------------------------------------------------------

def test_r3_refuses_a_slot_that_nothing_writes():
    # the slot starts out holding VT, but no visible write sets it
    init = bytes.fromhex("c7 01") + struct.pack("<I", VT3) + b"\xc3"   # mov [ecx],VT only
    img, code, attr, a, ro, vt = r3_image(init=init, data={OBJ: VT3})
    assert ro["ceiling_before_rules"] == 1
    assert ro[R3]["removed"] == 0
    assert next(iter(ro[R3]["slots_refused"].values())) == "the slot is never written"


def test_r3_refuses_a_dispatch_that_reaches_no_decoded_function():
    img, code, attr, a, ro, vt = r3_image(method=b"\x90\xc3",
                                          slots=(METHOD, METHOD, METHOD + 1))
    assert ro[R3]["removed"] == 0
    assert "not a decoded function" in next(iter(ro[R3]["slots_refused"].values()))


def test_r3_refuses_when_the_dispatched_methods_analysis_did_not_converge(monkeypatch):
    cut_short(monkeypatch, METHOD)
    img, code, attr, a, ro, vt = r3_image(method=_loop(b""))
    assert ro[R3]["removed"] == 0
    assert "did not converge" in next(iter(ro[R3]["slots_refused"].values()))


@pytest.mark.parametrize("name,method,why", [
    ("passes this to a call", lambda: _call_then_ret(METHOD, TEXT_VA), "lets `this` leave"),
    ("derives a pointer from this: lea edx,[ecx]; mov [edx],eax",
     lambda: bytes.fromhex("8d 11 89 02 c3"), "lets `this` leave"),
    ("a byte store into the slot", lambda: bytes.fromhex("c6 41 01 00 c3"), "writes the slot"),
    ("an indexed store from below: mov [ecx+eax*4-4],edx",
     lambda: bytes.fromhex("89 54 81 fc c3"), "writes the slot"),
    ("a class vtable stored 2 bytes into the slot",
     lambda: bytes.fromhex("c7 41 02") + struct.pack("<I", VT3) + b"\xc3", "writes the slot"),
])
def test_r3_refuses_a_dispatched_method_that_may_change_the_slot(name, method, why):
    img, code, attr, a, ro, vt = r3_image(method=method())
    assert ro[R3]["removed"] == 0, name
    assert why in next(iter(ro[R3]["slots_refused"].values())), name


def _call_then_ret(entry, target):
    buf = bytearray()
    T.rel_call(buf, entry, target)
    return bytes(buf + b"\xc3")


@pytest.mark.parametrize("name,tail", [
    ("lea eax,[edx+OBJ] (an untraced base)", bytes.fromhex("8d 82") + struct.pack("<I", OBJ)),
    ("add eax,offset OBJ", b"\x05" + struct.pack("<I", OBJ)),
    ("mov ecx,OBJ; movd xmm0,ecx", b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("66 0f 6e c1")),
    ("mov esi,OBJ; lea ecx,[esi+4]; lea edi,[ecx+4]; mov [edi-8],ebx",
     b"\xbe" + struct.pack("<I", OBJ) + bytes.fromhex("8d 4e 04 8d 79 04 89 5f f8")),
    ("mov ecx,OBJ; jmp (a tail call)", None),
])
def test_r3_refuses_when_the_object_address_leaves_the_analysis(name, tail):
    if tail is None:
        init = (bytes.fromhex("c7 05") + struct.pack("<I", OBJ) + struct.pack("<I", VT3)
                + bytes.fromhex("c7 01") + struct.pack("<I", VT3)
                + b"\xb9" + struct.pack("<I", OBJ))
        buf = bytearray(init)
        buf += b"\xe9" + struct.pack("<i", TEXT_VA - (R3_INIT + len(buf) + 5))
        img, code, attr, a, ro, vt = r3_image(init=bytes(buf))
    else:
        img, code, attr, a, ro, vt = r3_image(tail)
    assert ro["ceiling_before_rules"] == 1, name
    assert ro[R3]["removed"] == 0, (name, ro[R3])


def test_r3_refuses_when_code_outside_every_function_names_the_object():
    img, code, attr, a, ro, vt = r3_image(outside=b"\x68" + struct.pack("<I", OBJ) + b"\xc3")
    assert ro[R3]["removed"] == 0
    assert "(outside)" in next(iter(ro[R3]["slots_refused"].values()))


def test_r3_refuses_when_a_function_naming_the_object_was_not_analysed(monkeypatch):
    # NAMER stores an unknown value into the slot (mov ecx,OBJ; mov [ecx],eax) in a loop
    # whose analysis is cut short
    cut_short(monkeypatch, NAMER)
    namer = _loop(b"\xb9" + struct.pack("<I", OBJ) + bytes.fromhex("89 01"))
    img, code, attr, a, ro, vt = r3_image(functions_extra={NAMER: namer})
    assert ro[R3]["removed"] == 0
    assert "(unconverged)" in next(iter(ro[R3]["slots_refused"].values()))


# ---------------------------------------------------------------------------
# Every ceiling site listed (the user's ruling of 2026-10-09, item 1: "list every site"). The
# private scan receipt carries each site the ceiling counts with its receiver class and code
# region, and tools/s4/m7_inputs.py carries only its length and sha256 into the tracked one.

def test_every_ceiling_site_is_listed_with_its_receiver_region_and_rule():
    img, code, attr, a, ro = r1_image()
    site = indirect_call_va(code, TABLE_READER)
    assert ro["ceiling_sites"] == [{"va": hex(site), "function": hex(TABLE_READER), "disp": "0x8",
                                    "receiver": "global-vt", "region": "game",
                                    "ruled_out_by": R1}]
    # The list is the ceiling: one row per site, before any rule-out.
    assert len(ro["ceiling_sites"]) == ro["ceiling_before_rules"] == 1


def test_a_this_call_lists_its_receiver_as_this():
    img, code, attr, a, ro, vt = r2_image()
    rows = {r["va"]: r for r in ro["ceiling_sites"]}
    assert rows[hex(member_site(code))]["receiver"] == "this"
    assert rows[hex(member_site(code))]["ruled_out_by"] == R2
    assert len(ro["ceiling_sites"]) == ro["ceiling_before_rules"]


@pytest.mark.parametrize("value,kind", [
    (None, "unknown"), ((), "unknown"), (("this",), "this"), (("ret", 0x401000, 0x401010), "ret"),
    (("f", ("this",), 8), "f:this"), (("lea", ("arg", 4), 0), "lea:arg"), (("f", None, 4), "f"),
    (("global-vt",), "global-vt")])
def test_a_receiver_class_is_the_kind_of_its_symbolic_value(value, kind):
    assert D.receiver_class(value) == kind
