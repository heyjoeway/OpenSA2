"""`tools/s4/dinput_surface.py`: the push rule, the row check in both
directions and the claims, each made to fail one way at a time; and the
profiles harness/dinput_probe.c writes, read by the profile reader.

The surface tool claims that runtime/sa2_dinput.c and runtime/sa2_wbem.c
declare, for every DirectInput and WMI slot the image calls, the argument count
the image pushes, and a handler; and that they declare no handler for a slot
the image does not call. A checker that agrees with everything passes every
agreement test, so each agreement here is paired with a mutation that must
disagree, and a claim with more than one clause gets a mutation that only the
clause under test can see.

The probe writes its own device profiles. The grammar gained a record after
they were written, and every profile case would have exited 3 in a linked run
nobody had made yet; so the probe's profile writer is built alone here and
every profile it writes is read with tools/s4/device_profile.py, which must
accept the ones the probe runs and refuse the others exactly where the probe
expects.

The push rule and the row check run on synthetic code and on the modules' own
sources, so they run in any clone. The decode of the shipped image needs the
private executable, and the profile writer the pinned zig; each is skipped only
when it is absent, as in a clone.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from iced_x86 import Code, Decoder

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import device_profile as DP  # noqa: E402
import dinput_surface as DS  # noqa: E402
import image as IM  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
PROBE = ROOT / "harness" / "dinput_probe.c"

# What the image pushes at each site: the literal the whole-image test below
# checks against the decode, and the operand the synthetic row checks use.
PUSHED = {
    0x008631D0: 0, 0x00863208: 3, 0x00863222: 1, 0x00863236: 2, 0x00863244: 0,
    0x00863282: 2, 0x008633A1: 2, 0x008633B0: 0, 0x008633C9: 0, 0x00863740: 0,
    0x00863781: 3, 0x0086379E: 1, 0x008637B2: 2, 0x008637C4: 0, 0x00863810: 2,
    0x00863885: 2, 0x00863894: 0, 0x008638A9: 0, 0x0086474A: 8, 0x00864787: 4,
    0x008647B4: 4, 0x008647E7: 5, 0x008648B1: 0, 0x008648DD: 4, 0x0086491E: 0,
    0x00864938: 0, 0x0086494C: 0, 0x00864960: 0, 0x00864CAE: 3, 0x00864CCE: 0,
    0x00864CDA: 0, 0x00864E19: 4,
}
IMPORTS_PUSHED = {
    0x008646BC: ("ole32.dll", "CoInitialize", 1),
    0x008646DB: ("ole32.dll", "CoCreateInstance", 5),
    0x008646FE: ("OLEAUT32.dll", "Ordinal#2", 1),
    0x00864711: ("OLEAUT32.dll", "Ordinal#2", 1),
    0x00864724: ("OLEAUT32.dll", "Ordinal#2", 1),
    0x0086476B: ("ole32.dll", "CoSetProxyBlanket", 8),
    0x008648F9: ("OLEAUT32.dll", "Ordinal#6", 1),
    0x00864900: ("OLEAUT32.dll", "Ordinal#6", 1),
    0x0086490B: ("OLEAUT32.dll", "Ordinal#6", 1),
    0x0086496C: ("ole32.dll", "CoUninitialize", 0),
    0x00864D33: ("DINPUT8.dll", "DirectInput8Create", 5),
    0x00864E29: ("XINPUT1_3.dll", "Ordinal#4", 3),
}


def synthetic_calls(pushed: dict[int, int] | None = None) -> dict[int, DS.Call]:
    """The decode the image gives, built from the literal tables rather than the
    executable, so the row check can be driven without it."""
    p = PUSHED if pushed is None else pushed
    out = {site: DS.Call(site=site, kind="method", slot=slot, argc=p[site], this_reg="EAX",
                         load=site - 3)
           for site, (_iface, slot, _name) in DS.SITES.items()}
    for site in DS.GAME_SITES:
        out[site] = DS.Call(site=site, kind="method", slot=4, argc=0)
    for site, (dll, name, argc) in IMPORTS_PUSHED.items():
        out[site] = DS.Call(site=site, kind="import", import_=(dll, name), argc=argc)
    return out


# ------------------------------------------------------------ the push rule

def test_a_prologue_save_is_not_an_argument_and_this_is_not_counted():
    code = bytes.fromhex(
        "56"          # push esi          a save: ESI not written yet
        "8bf1"        # mov esi,ecx
        "33db"        # xor ebx,ebx
        "53"          # push ebx          argument
        "53"          # push ebx          argument
        "6a06"        # push 6            argument
        "8b06"        # mov eax,[esi]
        "8b500c"      # mov edx,[eax+0Ch] slot 3
        "56"          # push esi          this
        "ffd2"        # call edx
        "c3")
    (c,) = DS.decode(code, 0x1000, {}, {})
    assert (c.kind, c.slot, c.argc, c.this_reg, c.load) == ("method", 3, 3, "ESI", 0x100B)


def test_a_register_written_before_the_last_call_still_pushes_an_argument():
    # ConnectServer's shape: EBX is zeroed long before, and a call intervenes.
    # Counting only registers written since the last call would give 0 here.
    code = bytes.fromhex(
        "33db"        # xor ebx,ebx
        "e800000000"  # call $+5          a boundary
        "53" "53"     # push ebx x2       arguments
        "8b11"        # mov edx,[ecx]
        "8b520c"      # mov edx,[edx+0Ch]
        "51"          # push ecx          this
        "ffd2"        # call edx
        "c3")
    calls = DS.decode(code, 0x2000, {}, {})
    assert [(c.kind, c.argc) for c in calls] == [("direct", 0), ("method", 2)]


def test_an_import_through_a_register_and_through_a_thunk_is_named():
    slot, thunk = 0x3100, 0x3200
    code = (bytes.fromhex("8b35") + struct.pack("<I", slot)          # mov esi,[slot]
            + bytes.fromhex("68") + struct.pack("<I", 0x12345678)    # push imm
            + bytes.fromhex("ffd6")                                   # call esi
            + bytes.fromhex("6a00") + bytes.fromhex("e8")
            + struct.pack("<i", thunk - (0x300F + 5))                 # push 0; call thunk
            + bytes.fromhex("c3"))
    imports = {slot: ("OLEAUT32.dll", "Ordinal#2"), 0x3104: ("ole32.dll", "CoUninitialize")}
    calls = DS.decode(code, 0x3000, imports, {thunk: 0x3104})
    assert [(c.kind, c.import_, c.argc) for c in calls] == [
        ("import", ("OLEAUT32.dll", "Ordinal#2"), 1),
        ("import", ("ole32.dll", "CoUninitialize"), 1)]


# ------------------------------------------------- the row check, both ways

def test_the_modules_declare_what_the_image_pushes():
    res = DS.check(synthetic_calls(), DS.module_rows())
    assert res["problems"] == [] and res["ok"]
    assert len(res["sites"]) == len(DS.SITES) == 32


def test_a_declared_argc_off_by_one_disagrees_at_every_site_of_that_slot():
    text = DS.DINPUT_C.read_text()
    row = '{3,  3, DF_OUT, "CreateDevice", m_CreateDevice}'
    assert row in text
    bad = DS.module_rows(dinput_text=text.replace(row, row.replace("{3,  3,", "{3,  2,")))
    res = DS.check(synthetic_calls(), bad)
    assert not res["ok"]
    assert sorted(p["site"] for p in res["problems"]) == ["0x00863208", "0x00863781",
                                                          "0x00864cae"]
    assert {p["why"] for p in res["problems"]} == {"declared argc 2, the image pushes 3"}


def test_a_pushed_count_that_moves_disagrees():
    moved = {**PUSHED, 0x008647E7: 4}
    res = DS.check(synthetic_calls(moved), DS.module_rows())
    assert [(p["site"], p["why"]) for p in res["problems"]] == [
        ("0x008647e7", "declared argc 5, the image pushes 4")]


def test_a_handler_no_site_reaches_fails():
    text = DS.DINPUT_C.read_text()
    row = '{25, 0, 0, "Poll", NULL}'
    assert row in text
    bad = DS.module_rows(dinput_text=text.replace(row, '{25, 0, 0, "Poll", m_Acquire}'))
    res = DS.check(synthetic_calls(), bad)
    assert [(p["interface"], p["slot"], p["why"]) for p in res["problems"]] == [
        (DS.DIDEV, 25, "a handler for a slot no site in the image reaches")]


def test_a_slot_the_image_calls_with_no_handler_fails():
    text = DS.WBEM_C.read_text()
    row = '{4,  5, DF_OUT, "Get", m_Get}'
    assert row in text
    bad = DS.module_rows(wbem_text=text.replace(row, '{4,  5, DF_OUT, "Get", NULL}'))
    res = DS.check(synthetic_calls(), bad)
    assert [(p["site"], p["why"]) for p in res["problems"]] == [
        ("0x008647e7", "the image calls this slot and it has no handler")]


def test_a_vtable_call_in_neither_table_fails():
    calls = synthetic_calls()
    calls[0x00864D00] = DS.Call(site=0x00864D00, kind="method", slot=7, argc=0)
    res = DS.check(calls, DS.module_rows())
    assert [p["why"] for p in res["problems"]] == [
        "a vtable call in these functions that is in neither SITES nor GAME_SITES"]


def test_a_stale_game_site_fails():
    calls = synthetic_calls()
    del calls[0x00864E76]
    res = DS.check(calls, DS.module_rows())
    assert [(p["site"], p["why"][:28]) for p in res["problems"]] == [
        ("0x00864e76", "GAME_SITES names a site that")]


def test_the_stub_rows_are_compared_when_asked():
    stubs = {(dll, name): argc for dll, name, argc in IMPORTS_PUSHED.values()}
    assert DS.check(synthetic_calls(), DS.module_rows(), stubs)["ok"]
    short = dict(stubs)
    short[("ole32.dll", "CoSetProxyBlanket")] = 7
    del short[("OLEAUT32.dll", "Ordinal#6")]
    res = DS.check(synthetic_calls(), DS.module_rows(), short)
    assert sorted((p["site"], p["why"]) for p in res["problems"]) == [
        ("0x0086476b", "the stub pops 7, the image pushes 8"),
        ("0x008648f9", "runtime/sa2_imports.c has no stub row"),
        ("0x00864900", "runtime/sa2_imports.c has no stub row"),
        ("0x0086490b", "runtime/sa2_imports.c has no stub row")]


def test_the_stub_parser_reads_the_import_table_shape():
    text = ('{"ole32.dll",    "CoSetProxyBlanket",    8, SA2_IMP_STDCALL, imp_x},\n'
            '{"OLEAUT32.dll", "Ordinal#2", 1, SA2_IMP_STDCALL, imp_y},')
    assert DS.stub_rows(text) == {("ole32.dll", "CoSetProxyBlanket"): 8,
                                  ("OLEAUT32.dll", "Ordinal#2"): 1}


def test_a_run_record_that_published_the_argc_break_disagrees(tmp_path):
    declared = DS.module_rows()
    methods = {"dinput": [], "wbem": []}
    for (iface, slot), d in declared.items():
        block = "dinput" if iface in (DS.DI8, DS.DIDEV) else "wbem"
        argc = 2 if (iface, slot) == (DS.DI8, 3) else d["argc"]
        methods[block].append({"interface": iface, "name": d["name"], "slot": slot,
                               "argc": argc, "implemented": d["handler"]})
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"dinput": {"methods": methods["dinput"]},
                               "wbem": {"methods": methods["wbem"]}}))
    res = DS.check(synthetic_calls(), DS.argc_from_run(run))
    assert sorted(p["site"] for p in res["problems"]) == ["0x00863208", "0x00863781",
                                                          "0x00864cae"]
    (tmp_path / "empty.json").write_text("{}")
    with pytest.raises(SystemExit, match="no dinput.methods"):
        DS.argc_from_run(tmp_path / "empty.json")


# ------------------------------------------------------ the shipped image

@pytest.fixture(scope="module")
def img():
    if not IM.DEFAULT_EXE.is_file():
        pytest.skip(f"the unpacked executable is not present: {IM.DEFAULT_EXE}")
    return IM.Image(IM.DEFAULT_EXE)


def test_the_image_decodes_to_the_literal_table(img):
    calls = DS.scan(img)
    assert {s: (calls[s].slot, calls[s].argc) for s in DS.SITES} == {
        s: (slot, PUSHED[s]) for s, (_i, slot, _n) in DS.SITES.items()}
    assert {s: (*calls[s].import_, calls[s].argc) for s in DS.IMPORT_SITES} == IMPORTS_PUSHED
    res = DS.check(calls, DS.module_rows())
    assert res["problems"] == [] and res["ok"]


class Patched:
    """The image with some bytes replaced, for the claims that decode ranges.

    A patch in code starts at an instruction of the linear sweep, replaces
    whole instructions, and decodes to valid ones, and the sweep is decoded
    again there, so the claims that walk the sweep (through references()) see
    the patch as the claims that read bytes do. A patch outside the sweep is
    data. A partial patch is refused: the two bytes 0f 85 over a six-byte
    `je near` decode alone as one INVALID instruction, and the re-decoded sweep
    would lose the jump and every instruction the old one's tail becomes."""

    def __init__(self, img, patches: dict[int, bytes]):
        self._img, self._p = img, patches
        self._ins = None

    def __getattr__(self, k):
        return getattr(self._img, k)

    def read(self, va: int, n: int) -> bytes:
        b = bytearray(self._img.read(va, n))
        for at, data in self._p.items():
            for i, x in enumerate(data):
                if va <= at + i < va + n:
                    b[at + i - va] = x
        return bytes(b)

    @property
    def instructions(self) -> list:
        if self._ins is None:
            ins = list(self._img.instructions)
            for at, data in sorted(self._p.items(), reverse=True):
                k = self._img.index.get(at)
                if k is None:
                    continue
                end, j = at + len(data), k
                while j < len(ins) and ins[j].ip < end:
                    j += 1
                new = list(Decoder(32, self.read(at, len(data)), ip=at))
                assert sum(i.len for i in new) == len(data), "a patch must be whole instructions"
                assert all(i.code != Code.INVALID for i in new), "a patch must decode"
                assert j > k and ins[j - 1].next_ip == end, (
                    "a patch must end where an original instruction ends")
                ins[k:j] = new
            self._ins = ins
        return self._ins


def held(claims: list[dict]) -> dict[str, bool]:
    return {c["id"]: c["held"] for c in claims}


def failing(img) -> list[str]:
    return [k for k, v in held(DS.claims(img)).items() if not v]


def test_the_claims_hold(img):
    assert held(DS.claims(img)) == {
        "pointer-guard": True, "callback-keeps-nothing": True,
        "xinput-branch-is-a-no-op": True, "one-site-each": True, "is-xinput-device": True,
        "formats-and-pushes": True, "module-guids-are-the-images": True,
        "xinput-scans-slots-0-to-3": True}


def test_a_guard_that_tests_the_hresult_fails_the_pointer_claim(img):
    je = img.read(0x00864D45, 6)
    assert je == bytes.fromhex("0f84a9010000")                  # je near 00864EF4h
    assert failing(Patched(img, {0x00864D45: b"\x0f\x85" + je[2:]})) == ["pointer-guard"]


def test_the_patch_helper_refuses_a_partial_or_invalid_patch(img):
    # The old pointer-guard mutation: 0f 85 over the first two bytes of the
    # six-byte je near decodes alone as INVALID.
    with pytest.raises(AssertionError, match="a patch must decode"):
        Patched(img, {0x00864D45: b"\x0f\x85"}).instructions
    # Two valid nops over the same six bytes: the je runs past the patch.
    with pytest.raises(AssertionError, match="must end where an original instruction ends"):
        Patched(img, {0x00864D45: b"\x90\x90"}).instructions
    # 0f 04 over the two-byte `push 1` at 0x00864CE6: whole, and no instruction.
    assert img.read(0x00864CE6, 2) == bytes.fromhex("6a01")
    with pytest.raises(AssertionError, match="a patch must decode"):
        Patched(img, {0x00864CE6: bytes.fromhex("0f04")}).instructions


def test_a_callback_that_stores_fails_its_claim_through_the_store_scan(img, monkeypatch):
    # 0x00864CC2 mov eax,[esp+10h] becomes mov [esp+10h],eax: the same length,
    # no ESI, and the same returns and `mov eax,1`s, so of the claim's clauses
    # only "stores nothing" can see it. With the store scan disabled the
    # mutated image passes, which is what shows the scan does the seeing.
    assert img.read(0x00864CC2, 4) == bytes.fromhex("8b442410")
    mutated = Patched(img, {0x00864CC2: bytes.fromhex("89442410")})
    assert failing(mutated) == ["callback-keeps-nothing"]
    monkeypatch.setattr(DS, "WRITES", ())
    assert failing(mutated) == []


def test_a_callback_that_returns_other_than_1_fails_its_claim(img):
    assert img.read(0x00864CDC, 5) == bytes.fromhex("b801000000")        # mov eax,1
    assert failing(Patched(img, {0x00864CDC: bytes.fromhex("b800000000")})) == [
        "callback-keeps-nothing"]


def test_a_store_or_another_argument_fails_the_xinput_branch_claim(img, monkeypatch):
    # 0x00864990 mov edi,eax becomes mov [eax],edi: a store before the branch,
    # which only the head's store scan can see.
    assert img.read(0x00864990, 2) == bytes.fromhex("8bf8")
    stores = Patched(img, {0x00864990: bytes.fromhex("8938")})
    assert failing(stores) == ["xinput-branch-is-a-no-op"]
    # The callback's `push 1` becomes `push 0`: 0x00864980 then builds a
    # joystick for the device.
    assert img.read(0x00864CE6, 2) == bytes.fromhex("6a01")
    assert failing(Patched(img, {0x00864CE6: bytes.fromhex("6a00")})) == [
        "xinput-branch-is-a-no-op"]
    monkeypatch.setattr(DS, "WRITES", ())
    assert failing(stores) == []


def test_a_reference_moved_or_added_fails_the_one_site_claim(img):
    # The callback's call of IsXInputDevice aims one byte further on: the
    # function is named nowhere.
    assert img.read(0x00864CB8, 5) == bytes.fromhex("e8c3f9ffff")        # call 00864680h
    assert failing(Patched(img, {0x00864CB8: bytes.fromhex("e8c4f9ffff")})) == ["one-site-each"]
    # A call in the XInput scan becomes `push 864C90h`: the callback is named
    # at a second site.
    assert img.read(0x00864E38, 5) == bytes.fromhex("e8640ff4ff")        # call 007A5DA1h
    assert failing(Patched(img, {0x00864E38: bytes.fromhex("68904c8600")})) == ["one-site-each"]


def test_a_format_or_a_spin_that_moves_fails_the_formats_claim(img):
    # c_dfDIKeyboard's dwDataSize, 0x100, becomes 0x80.
    assert img.read(0x00875D78, 4) == bytes.fromhex("00010000")
    assert failing(Patched(img, {0x00875D78: bytes.fromhex("80000000")})) == [
        "formats-and-pushes"]
    # The keyboard poll's first INPUTLOST test compares with another code.
    assert img.read(0x00863896, 5) == bytes.fromhex("3d1e000780")
    assert failing(Patched(img, {0x00863896: bytes.fromhex("3d1f000780")})) == [
        "formats-and-pushes"]


def test_a_scan_that_reaches_slot_4_fails_the_xinput_slot_claim(img):
    assert img.read(0x00864EEB, 3) == bytes.fromhex("83fe04")            # cmp esi,4
    assert failing(Patched(img, {0x00864EEB: bytes.fromhex("83fe05")})) == [
        "xinput-scans-slots-0-to-3"]
    assert img.read(0x00864A70, 3) == bytes.fromhex("83ff04")            # cmp edi,4
    assert failing(Patched(img, {0x00864A70: bytes.fromhex("83ff05")})) == [
        "xinput-scans-slots-0-to-3"]


XIN = "xinput-scans-slots-0-to-3"


def test_another_caller_of_the_xinput_thunk_fails_the_slot_claim(img):
    # A sixth call of the thunk, whatever slot it passes: only the pinned list
    # of callers sees it.
    assert img.read(0x00864E38, 5) == bytes.fromhex("e8640ff4ff")        # call 007A5DA1h
    call = b"\xe8" + struct.pack("<i", 0x007C0CCE - (0x00864E38 + 5))
    assert failing(Patched(img, {0x00864E38: call})) == [XIN]
    # 0x00863D20 named by an instruction, then by a relocated dword of data
    # that no other clause reads (the pad vtable's slot 11).
    assert failing(Patched(img, {0x00864E38: bytes.fromhex("68203d8600")})) == [XIN]
    assert 0x0090000C in img.relocated
    assert img.read(0x0090000C, 4) == struct.pack("<I", 0x00708E00)
    assert failing(Patched(img, {0x0090000C: struct.pack("<I", 0x00863D20)})) == [XIN]


def test_a_pad_method_that_passes_another_slot_fails_the_slot_claim(img):
    # The slot-7 method reads +0Ch instead of +8.
    assert img.read(0x00863D4B, 3) == bytes.fromhex("8b4e08")            # mov ecx,[esi+8]
    assert failing(Patched(img, {0x00863D4B: bytes.fromhex("8b4e0c")})) == [XIN]
    # The start-up scan stores EDI, not its slot in ESI, at +8.
    assert img.read(0x00864E4A, 3) == bytes.fromhex("897008")            # mov [eax+8],esi
    assert failing(Patched(img, {0x00864E4A: bytes.fromhex("897808")})) == [XIN]
    # Slot 7 of the pad vtable names another method.
    assert img.read(0x008FFFFC, 4) == struct.pack("<I", 0x00863D40)
    assert failing(Patched(img, {0x008FFFFC: struct.pack("<I", 0x00863CD0)})) == [XIN]
    # A ret between the method's start and its call: the call is no longer
    # shown to be in the method the vtable names.
    assert img.read(0x00863D43, 1) == b"\x56"                           # push esi
    assert failing(Patched(img, {0x00863D43: b"\xc3"})) == [XIN]


def test_a_blanket_the_image_does_not_push_fails_its_claim(img):
    assert img.read(0x00864768, 2) == b"\x6a\x0a"             # push 0Ah
    assert failing(Patched(img, {0x00864768: b"\x6a\x09"})) == ["is-xinput-device"]


def test_a_module_guid_that_is_not_the_images_fails(img):
    text = DS.DINPUT_C.read_text()
    first = "GUID_KEYBOARD[16] = {0x61,"
    assert first in text
    got = held(DS.claims(img, dinput_text=text.replace(first, "GUID_KEYBOARD[16] = {0x62,")))
    assert [k for k, v in got.items() if not v] == ["module-guids-are-the-images"]


# ------------------------------------------------------- the probe's profiles

@pytest.fixture(scope="module")
def probe_profiles(tmp_path_factory) -> dict[str, tuple[Path, int, str]]:
    """harness/dinput_probe.c built with -DSA2_DINPUT_PROBE_PROFILES_ONLY and run:
    key -> (the profile it wrote, the line a reader must refuse it at, and the
    reader's words there), with 0 and "" for a profile both readers accept."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("dinput-probe-profiles")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR",
                   str(ROOT / "private" / "generated" / "zig-cache-tests")))
    exe = d / "profiles"
    done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall",
                           "-Wextra", "-Werror", "-DSA2_DINPUT_PROBE_PROFILES_ONLY",
                           "-I", str(ROOT / "runtime"), str(PROBE), "-o", str(exe)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    ran = subprocess.run([str(exe), str(d)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    rows = {}
    for line in ran.stdout.splitlines():
        key, path, defect, words = line.split("\t")
        rows[key] = (Path(path), int(defect), words)
    return rows


def test_the_probe_writes_every_profile_its_cases_name(probe_profiles):
    assert sorted(probe_profiles) == ["batch", "dup", "entities", "guest", "hr", "kbdctrl",
                                      "mousectrl", "stop", "two", "utf8"]


def test_every_profile_the_probe_runs_is_one_the_reader_accepts(probe_profiles):
    """What the replay tools' reader makes of each profile a case loads: the
    literal answers the probe then asserts the device gives."""
    got = {}
    for key, (path, defect, _words) in probe_profiles.items():
        if defect:
            continue
        p, _sha = DP.load(path)
        got[key] = (p.syskeyboard, p.sysmouse, [g.create for g in p.gamectrl], p.blanket,
                    len(p.pnp))
    assert got == {
        "two": (0, 0, [0], 0, 2),
        "guest": (0, 0, [0], 0, 2),
        "hr": (0x80040154, 0x80070005, [0x80070005], 0x80004005, 2),
        "batch": (0, 0, [], 0, 21),
        "stop": (0, 0, [0, 0], 0, 0),
    }


def test_every_profile_the_probe_refuses_is_refused_where_the_probe_expects(probe_profiles):
    """A refuse case passes when the lifted run's configuration trap names the
    profile's file and this line, and holds these words. The Python reader is
    held word for word to the C one by test_device_profile.py, so these are the
    trap's line and words, checked without a link."""
    got = {}
    for key, (path, defect, words) in probe_profiles.items():
        if not defect:
            continue
        with pytest.raises(DP.ProfileError) as e:
            DP.load(path)
        got[key] = (defect, e.value.line, bool(words) and words in e.value.message)
    assert got == {"entities": (282, 282, True), "utf8": (17, 17, True),
                   "kbdctrl": (6, 6, True), "mousectrl": (6, 6, True), "dup": (7, 7, True)}
