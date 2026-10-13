#!/usr/bin/env python3
"""Decode every DirectInput and WMI call the shipped image makes, check the
argument counts runtime/sa2_dinput.c and runtime/sa2_wbem.c declare against
the dwords the image pushes, and re-derive the facts the recording device
rests on.

Subtitle: decode the nine functions that hold the input manager's DirectInput
calls and IsXInputDevice's WMI calls, name each `call` through a vtable or an
import slot with the dwords pushed for it, compare those with the modules' row
tables in both directions, and check each claim the design's section 1 makes
against the instructions that establish it, with one more the XInput stub rests
on: the slots the image asks about.

Why a tool and not the design's table
-------------------------------------
A COM vtable numbered from a header rather than from the image has corrupted a
caller's frame twice in this project (d3d9 at S4 slice 6, Steam at slice 9):
neither gave a wrong answer, both gave a wrong frame, which nothing in the
runtime can see. The design's section 3 table of argument counts was read by
hand. This reads them from the bytes, and a row table that disagrees fails.

How a push becomes an argument
------------------------------
Counting every push since the last call over-counts: a function's prologue
saves callee-saved registers with the same `push`. The rule here: walking back
from the call, a push is an argument while it pushes an immediate, a memory
operand, or a register this function has already written; the walk stops at
the first push of a register the function has not written yet, because that
is a save of the caller's value. For a method call the last push is `this`
and is not counted. `tools/s4/dsound_surface.py` stops at a register not
written *since the last call*, which undercounts here: IsXInputDevice pushes
ConnectServer's six zeros from EBX, zeroed at 0x0086468F, long before
(0x00864742..0x00864747). The rule is exercised on synthetic code by
Documentation/scripts/tests/test_dinput_surface.py.

Both directions of the row check
--------------------------------
  * every site's (interface, slot) has a row whose argument count equals the
    pushes, whose name is the one the site table expects, and which has a
    handler;
  * every row with a handler is reached by some site. A handler no site reaches
    is an answer nothing measured, and the brief for these modules forbids it.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/dinput_surface.py --claims \\
      [--argc private/generated/s4/run/<run>.a.json] \\
      [--json private/generated/s4/dinput/surface.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import image as IM  # noqa: E402
from iced_x86 import (Decoder, InstructionInfoFactory, Mnemonic, OpAccess,  # noqa: E402
                      OpKind, Register, RegisterExt)

ROOT = Path(__file__).resolve().parents[2]
DINPUT_C = ROOT / "runtime" / "sa2_dinput.c"
WBEM_C = ROOT / "runtime" / "sa2_wbem.c"
IMPORTS_C = ROOT / "runtime" / "sa2_imports.c"

# ------------------------------------------------------------- the functions
#
# (entry, last instruction, what it is). Each ends in the `ret` at the last
# address; the decode runs entry..last inclusive.
FUNCTIONS = {
    0x008631C0: (0x008631DA, "mouse wrapper: Unacquire, then forget the device"),
    0x008631E0: (0x008632B1, "mouse wrapper Init"),
    0x00863370: (0x008633EA, "mouse wrapper Poll"),
    0x00863730: (0x0086374A, "keyboard wrapper: Unacquire, then forget the device"),
    0x00863750: (0x0086383C, "keyboard wrapper Init"),
    0x00863840: (0x008638D0, "keyboard wrapper Poll"),
    0x00864680: (0x0086497D, "IsXInputDevice (Microsoft's sample, inlined)"),
    0x00864C90: (0x00864CF7, "the EnumDevices callback"),
    0x00864D00: (0x00864EFA, "InputImpl::Initialize"),
}

DI8, DIDEV = "IDirectInput8A", "IDirectInputDevice8A"
LOC, SVC, ENUM, OBJ = "IWbemLocator", "IWbemServices", "IEnumWbemClassObject", "IWbemClassObject"

# (site) -> (interface, slot, method). The interface is asserted, with the
# object's provenance in the design's section 1; the slot and the argument
# count are decoded, and a slot that disagrees with this table fails.
SITES = {
    0x008631D0: (DIDEV, 8, "Unacquire"),
    0x00863208: (DI8, 3, "CreateDevice"),
    0x00863222: (DIDEV, 11, "SetDataFormat"),
    0x00863236: (DIDEV, 13, "SetCooperativeLevel"),
    0x00863244: (DIDEV, 7, "Acquire"),
    0x00863282: (DIDEV, 9, "GetDeviceState"),
    0x008633A1: (DIDEV, 9, "GetDeviceState"),
    0x008633B0: (DIDEV, 7, "Acquire"),
    0x008633C9: (DIDEV, 7, "Acquire"),
    0x00863740: (DIDEV, 8, "Unacquire"),
    0x00863781: (DI8, 3, "CreateDevice"),
    0x0086379E: (DIDEV, 11, "SetDataFormat"),
    0x008637B2: (DIDEV, 13, "SetCooperativeLevel"),
    0x008637C4: (DIDEV, 7, "Acquire"),
    0x00863810: (DIDEV, 9, "GetDeviceState"),
    0x00863885: (DIDEV, 9, "GetDeviceState"),
    0x00863894: (DIDEV, 7, "Acquire"),
    0x008638A9: (DIDEV, 7, "Acquire"),
    0x0086474A: (LOC, 3, "ConnectServer"),
    0x00864787: (SVC, 18, "CreateInstanceEnum"),
    0x008647B4: (ENUM, 4, "Next"),
    0x008647E7: (OBJ, 4, "Get"),
    0x008648B1: (OBJ, 2, "Release"),
    0x008648DD: (ENUM, 4, "Next"),
    0x0086491E: (OBJ, 2, "Release"),
    0x00864938: (ENUM, 2, "Release"),
    0x0086494C: (LOC, 2, "Release"),
    0x00864960: (SVC, 2, "Release"),
    0x00864CAE: (DI8, 3, "CreateDevice"),
    0x00864CCE: (DIDEV, 8, "Unacquire"),
    0x00864CDA: (DIDEV, 2, "Release"),
    0x00864E19: (DI8, 4, "EnumDevices"),
}

# Vtable-shaped calls in the same functions that are the game's own objects,
# each with the reason it is not DirectInput or WMI. A vtable call in these
# functions that is in neither table fails the scan: nothing is dropped
# silently.
GAME_SITES = {
    0x00863267: "mouse wrapper's own slot 4 (IsValid): thiscall on ESI, the wrapper, "
                "loaded from [ESI] at 0x00863260",
    0x008637EC: "keyboard wrapper's own slot 4 (IsValid): thiscall on EDI, the wrapper, "
                "loaded from [EDI] at 0x008637E2",
    0x00864D9C: "the keyboard wrapper's slot 5 (Init, 0x00863750): thiscall on "
                "InputImpl+0x14 at 0x00864D90",
    0x00864E01: "the mouse wrapper's slot 5 (Init, 0x008631E0): thiscall on "
                "InputImpl+0x10 at 0x00864DF3",
    0x00864E76: "the pad wrapper's slot 5 (vtable 0x008FFFE0 stored at 0x00864E53): "
                "thiscall on ESI",
}

# Import calls in the same functions: (site) -> (dll, runtime name). A call
# through a `jmp [slot]` thunk is named by the thunk's slot.
IMPORT_SITES = {
    0x008646BC: ("ole32.dll", "CoInitialize"),
    0x008646DB: ("ole32.dll", "CoCreateInstance"),
    0x008646FE: ("OLEAUT32.dll", "Ordinal#2"),
    0x00864711: ("OLEAUT32.dll", "Ordinal#2"),
    0x00864724: ("OLEAUT32.dll", "Ordinal#2"),
    0x0086476B: ("ole32.dll", "CoSetProxyBlanket"),
    0x008648F9: ("OLEAUT32.dll", "Ordinal#6"),
    0x00864900: ("OLEAUT32.dll", "Ordinal#6"),
    0x0086490B: ("OLEAUT32.dll", "Ordinal#6"),
    0x0086496C: ("ole32.dll", "CoUninitialize"),
    0x00864D33: ("DINPUT8.dll", "DirectInput8Create"),
    0x00864E29: ("XINPUT1_3.dll", "Ordinal#4"),
}

# The runtime's own name for an import: OLEAUT32 and XINPUT1_3 are imported
# by ordinal, and pefile names OLEAUT32's from its own table.
RUNTIME_NAME = {"SysAllocString": "Ordinal#2", "SysFreeString": "Ordinal#6"}

GP32 = (Register.EAX, Register.ECX, Register.EDX, Register.EBX,
        Register.ESP, Register.EBP, Register.ESI, Register.EDI)
WRITES = (OpAccess.WRITE, OpAccess.READ_WRITE, OpAccess.COND_WRITE, OpAccess.READ_COND_WRITE)


# ------------------------------------------------------------------ decoding

@dataclass
class Push:
    ip: int
    imm: int | None
    reg: int | None
    arg: bool                      # an argument by the rule in the docstring


@dataclass
class Call:
    site: int
    kind: str                      # "method", "import" or "direct"
    slot: int | None = None        # for a method
    load: int | None = None        # the instruction that loaded the method pointer
    import_: tuple[str, str] | None = None
    target: int | None = None      # for a direct call
    this_reg: str | None = None
    argc: int = 0
    pushes: list[Push] = field(default_factory=list)


def reg_name(r: int) -> str:
    return IM.register_name(r) or str(r)


def decode(blob: bytes, ip: int, imports: dict[int, tuple[str, str]],
           thunks: dict[int, int]) -> list[Call]:
    """Every call in `blob` (decoded linearly from `ip`), with its argument count.

    `imports` maps an IAT slot to (dll, name); `thunks` maps a `jmp [slot]`
    thunk's address to its slot."""
    info_f = InstructionInfoFactory()
    written: set[int] = set()
    sym: dict[int, tuple] = {}         # register -> ("method", slot, ip) | ("import", slot)
    pushes: list[Push] = []
    out: list[Call] = []
    for ins in Decoder(32, blob, ip=ip):
        m = ins.mnemonic
        info = info_f.info(ins)
        if m == Mnemonic.PUSH:
            if ins.op0_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                pushes.append(Push(ins.ip, ins.immediate32, None, True))
            elif ins.op0_kind == OpKind.REGISTER:
                r = RegisterExt.full_register32(ins.op0_register)
                pushes.append(Push(ins.ip, None, r, r in written))
            else:
                pushes.append(Push(ins.ip, None, None, True))
            continue
        if m == Mnemonic.CALL:
            c = Call(site=ins.ip, kind="direct")
            if ins.op0_kind == OpKind.REGISTER:
                s = sym.get(RegisterExt.full_register32(ins.op0_register))
                if s and s[0] == "method":
                    c.kind, c.slot, c.load = "method", s[1], s[2]
                elif s and s[0] == "import":
                    c.kind, c.import_ = "import", imports.get(s[1])
                else:
                    c.kind = "register"
            elif ins.op0_kind == OpKind.MEMORY:
                d = IM.abs_displacement(ins)
                if d in imports:
                    c.kind, c.import_ = "import", imports[d]
                else:
                    c.kind = "memory"
            else:
                c.target = ins.near_branch_target
                if c.target in thunks:
                    c.kind, c.import_ = "import", imports.get(thunks[c.target])
            c.pushes = list(pushes)
            args = pushes
            if c.kind == "method" and pushes:
                last = pushes[-1]
                c.this_reg = reg_name(last.reg) if last.reg is not None else None
                args = pushes[:-1]
            n = 0
            for p in reversed(args):
                if not p.arg:
                    break
                n += 1
            c.argc = n
            out.append(c)
            pushes = []
            for r in (Register.EAX, Register.ECX, Register.EDX):
                sym.pop(r, None)
                written.add(r)
            continue
        if m in (Mnemonic.RET, Mnemonic.INT3):
            pushes = []
            continue
        # Register writes: record what a method-pointer or import-pointer load
        # put in the register, and clear it on any other write.
        for ur in info.used_registers():
            if ur.access in WRITES:
                r = RegisterExt.full_register32(ur.register)
                if r in GP32 and r != Register.ESP:
                    written.add(r)
                    sym.pop(r, None)
        if m == Mnemonic.MOV and ins.op0_kind == OpKind.REGISTER and ins.op1_kind == OpKind.MEMORY:
            dst = RegisterExt.full_register32(ins.op0_register)
            if ins.memory_base == Register.NONE and ins.memory_index == Register.NONE:
                d = ins.memory_displacement
                if d in imports:
                    sym[dst] = ("import", d)
            elif (ins.memory_index == Register.NONE and ins.memory_base not in
                  (Register.ESP, Register.EBP) and ins.memory_displacement % 4 == 0):
                sym[dst] = ("method", ins.memory_displacement // 4, ins.ip)
    return out


def runtime_import_name(name: str) -> str:
    """image.py's name for an import, as the runtime's table spells it."""
    if name in RUNTIME_NAME:
        return RUNTIME_NAME[name]
    m = re.search(r"!ordinal(\d+)$", name)
    return "Ordinal#%s" % m.group(1) if m else name


def scan(img) -> dict[int, Call]:
    """Every call in FUNCTIONS, keyed by site."""
    imports = {slot: (dll, runtime_import_name(name)) for slot, (dll, name) in img.imports.items()}
    out: dict[int, Call] = {}
    for entry, (last, _what) in FUNCTIONS.items():
        blob = img.read(entry, last - entry + 16)
        if blob is None:
            raise SystemExit(f"cannot read {entry:#010x}")
        for c in decode(blob, entry, imports, dict(img.thunks)):
            if c.site <= last:
                out[c.site] = c
    return out


# ---------------------------------------------------------- the row tables

TABLE = re.compile(r"static\s+(?:const\s+)?\w+_row\s+(ROWS_\w+)\[\]\s*=\s*\{(.*?)\n\};", re.S)
ROW = re.compile(r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*([A-Za-z0-9_| ]+?)\s*,\s*\"(\w+)\"\s*,\s*(\w+)\s*\}")
TYPES = re.compile(r"\{\s*\"(\w+)\"\s*,\s*(ROWS_\w+)\s*,\s*0\s*\}")


def rows(text: str) -> dict[tuple[str, int], dict]:
    """(interface, slot) -> {name, argc, handler} from a module's source."""
    tables = {m.group(1): m.group(2) for m in TABLE.finditer(text)}
    out: dict[tuple[str, int], dict] = {}
    for iface, tab in TYPES.findall(text):
        if tab not in tables:
            raise SystemExit(f"type {iface} names {tab}, which the source does not define")
        for slot, argc, _flags, name, fn in ROW.findall(tables[tab]):
            out[(iface, int(slot))] = {"name": name, "argc": int(argc),
                                       "handler": fn != "NULL"}
    return out


def module_rows(dinput_text: str | None = None, wbem_text: str | None = None) -> dict:
    d = rows(dinput_text if dinput_text is not None else DINPUT_C.read_text())
    d.update(rows(wbem_text if wbem_text is not None else WBEM_C.read_text()))
    return d


STUB = re.compile(r"\{\s*\"([\w.]+)\"\s*,\s*\"([\w#]+)\"\s*,\s*(\d+)\s*,\s*SA2_IMP_\w+")


def stub_rows(text: str | None = None) -> dict[tuple[str, str], int]:
    t = text if text is not None else IMPORTS_C.read_text()
    return {(dll, name): int(argc) for dll, name, argc in STUB.findall(t)}


# ------------------------------------------------------------------ checks

def check(calls: dict[int, Call], declared: dict, stubs: dict | None = None) -> dict:
    res: dict = {"sites": [], "problems": [], "unattributed": [], "imports": []}
    reached: set[tuple[str, int]] = set()
    for site, c in sorted(calls.items()):
        if c.kind == "method" and site not in GAME_SITES and site not in SITES:
            res["unattributed"].append({"site": "%#010x" % site, "slot": c.slot,
                                        "pushes": len(c.pushes)})
    for site, why in sorted(GAME_SITES.items()):
        c = calls.get(site)
        if c is None or c.kind != "method":
            res["problems"].append({"site": "%#010x" % site, "game_site": why,
                                    "why": "GAME_SITES names a site that decodes to no "
                                           "vtable call, so the table is stale"})
    for site, (iface, slot, name) in sorted(SITES.items()):
        c = calls.get(site)
        row = {"site": "%#010x" % site, "interface": iface, "slot": slot, "method": name}
        if c is None or c.kind != "method":
            res["problems"].append({**row, "why": "no vtable call decodes at this site"})
            continue
        row.update(decoded_slot=c.slot, argc=c.argc, this=c.this_reg,
                   method_load="%#010x" % c.load if c.load else None)
        reached.add((iface, c.slot))
        d = declared.get((iface, c.slot))
        row["declared"] = d
        if c.slot != slot:
            res["problems"].append({**row, "why": "the decoded slot is not the table's"})
        elif c.this_reg is None:
            res["problems"].append({**row, "why": "the last push is not a register (this)"})
        elif d is None:
            res["problems"].append({**row, "why": "the module has no row for this slot"})
        elif d["name"] != name:
            res["problems"].append({**row, "why": "the module names this slot otherwise"})
        elif d["argc"] != c.argc:
            res["problems"].append({**row, "why": "declared argc %d, the image pushes %d"
                                                  % (d["argc"], c.argc)})
        elif not d["handler"]:
            res["problems"].append({**row, "why": "the image calls this slot and it has no "
                                                  "handler"})
        res["sites"].append(row)
    for (iface, slot), d in sorted(declared.items()):
        if d["handler"] and (iface, slot) not in reached:
            res["problems"].append({"interface": iface, "slot": slot, "method": d["name"],
                                    "why": "a handler for a slot no site in the image reaches"})
    for site, (dll, name) in sorted(IMPORT_SITES.items()):
        c = calls.get(site)
        row = {"site": "%#010x" % site, "dll": dll, "name": name}
        if c is None or c.kind != "import" or c.import_ is None:
            res["problems"].append({**row, "why": "no import call decodes at this site"})
            continue
        row["decoded"] = "%s!%s" % c.import_
        row["argc"] = c.argc
        if c.import_[0].lower() != dll.lower() or c.import_[1] != name:
            res["problems"].append({**row, "why": "the site calls another import"})
        if stubs is not None:
            have = stubs.get((dll, name))
            row["stub_argc"] = have
            if have is None:
                res["problems"].append({**row, "why": "runtime/sa2_imports.c has no stub row"})
            elif have != c.argc:
                res["problems"].append({**row, "why": "the stub pops %d, the image pushes %d"
                                                      % (have, c.argc)})
        res["imports"].append(row)
    for u in res["unattributed"]:
        res["problems"].append({**u, "why": "a vtable call in these functions that is in "
                                            "neither SITES nor GAME_SITES"})
    res["ok"] = not res["problems"] and len(res["sites"]) == len(SITES)
    return res


def argc_from_run(run_json: Path) -> dict:
    """(interface, slot) -> {name, argc, handler} as a run published it: the
    `dinput.methods` and `wbem.methods` arrays crt_probe or dinput_probe write.
    The published counts are the ones the dispatcher popped with, so the
    `argc` break shows here as well as in the source."""
    run = json.loads(run_json.read_text())
    out: dict = {}
    for block in ("dinput", "wbem"):
        for r in (run.get(block) or {}).get("methods", []):
            out[(r["interface"], int(r["slot"]))] = {"name": r["name"], "argc": int(r["argc"]),
                                                    "handler": bool(r["implemented"])}
    if not out:
        raise SystemExit(f"{run_json} has no dinput.methods or wbem.methods")
    return out


# ------------------------------------------------------------------ claims

def guid(img, va: int) -> str:
    return str(uuid.UUID(bytes_le=img.read(va, 16))).upper()


def wstr(img, va: int) -> str:
    raw = img.read(va, 128) or b""
    return raw.decode("utf-16-le", "replace").split("\0")[0]


def ins_at(img, va: int):
    """The one instruction a decode from `va` produces first."""
    return next(iter(Decoder(32, img.read(va, 16), ip=va)))


def text_at(img, va: int) -> str:
    return img.text(ins_at(img, va))


def range_ins(img, start: int, last: int) -> list:
    return [i for i in Decoder(32, img.read(start, last - start + 16), ip=start) if i.ip <= last]


def references(img, values: set[int]) -> dict[int, list[int]]:
    """Instructions anywhere in the image's linear sweep that name one of `values`:
    as an immediate, an absolute displacement, or a near branch target. One pass."""
    out: dict[int, list[int]] = {v: [] for v in values}
    for ins in img.instructions:
        if not ins.op_count:
            continue
        hit = set()
        if ins.op0_kind in (OpKind.NEAR_BRANCH32, OpKind.NEAR_BRANCH16):
            hit.add(ins.near_branch_target)
        d = IM.abs_displacement(ins)
        if d is not None:
            hit.add(d)
        hit.update(IM.immediates(ins))
        for v in hit & values:
            out[v].append(ins.ip)
    return out


def data_references(img, values: set[int]) -> dict[int, list[int]]:
    """Relocated dwords that hold one of `values`. The image is relocatable, so
    every absolute address it stores, in data or in code, carries a relocation;
    with references() this names every way to reach an address but a computed
    one."""
    out: dict[int, list[int]] = {v: [] for v in values}
    for r in sorted(img.relocated):
        b = img.read(r, 4)
        if b is not None and len(b) == 4 and int.from_bytes(b, "little") in out:
            out[int.from_bytes(b, "little")].append(r)
    return out


def c_bytes(text: str, name: str) -> str:
    """A `static const uint8_t NAME[16] = {...}` array from a module's source, as a GUID."""
    m = re.search(rf"{name}\[16\]\s*=\s*\{{([^}}]*)\}}", text)
    if not m:
        raise SystemExit(f"no {name}[16] in the source")
    b = bytes(int(x, 0) for x in m.group(1).replace("\n", " ").split(",") if x.strip())
    return str(uuid.UUID(bytes_le=b)).upper()


def claims(img, dinput_text: str | None = None, wbem_text: str | None = None) -> list[dict]:
    dt = dinput_text if dinput_text is not None else DINPUT_C.read_text()
    wt = wbem_text if wbem_text is not None else WBEM_C.read_text()
    out: list[dict] = []

    def claim(cid: str, what: str, held: bool, evidence: list[str]) -> None:
        out.append({"id": cid, "claim": what, "held": bool(held), "evidence": evidence})

    t = {va: text_at(img, va) for va in (0x00864D33, 0x00864D3A, 0x00864D3C, 0x00864D42,
                                         0x00864D45, 0x007C0CE0)}
    claim("pointer-guard",
          "the pointer DirectInput8Create writes, not its HRESULT, gates the device setup",
          t[0x00864D33] == "call 007C0CE0h"
          and img.thunks.get(0x007C0CE0) == 0x00873010
          and t[0x00864D3A] == "jge short 00864D42h"
          and t[0x00864D3C] == "mov dword [ebx],0"
          and t[0x00864D42] == "cmp dword [ebx],0"
          and t[0x00864D45] == "je near 00864EF4h",
          ["%08x  %s" % (va, s) for va, s in sorted(t.items())])

    cb = range_ins(img, 0x00864C90, 0x00864CF7)
    info_f = InstructionInfoFactory()
    stores = [i for i in cb if i.mnemonic not in (Mnemonic.PUSH, Mnemonic.POP, Mnemonic.CALL,
                                                  Mnemonic.RET)
              and i.op_count and i.op0_kind == OpKind.MEMORY
              and info_f.info(i).op0_access in WRITES]
    uses = ["%08x  %s" % (i.ip, img.text(i)) for i in cb
            if any(RegisterExt.full_register32(u.register) == Register.ESI
                   for u in info_f.info(i).used_registers())]
    rets = [(i.ip, img.text(i)) for i in cb if i.mnemonic == Mnemonic.RET]
    claim("callback-keeps-nothing",
          "the EnumDevices callback 0x00864C90 reads lpddi+4 and lpddi+0x14 only, stores "
          "nothing, and returns 1 through `ret 8` on both paths",
          not stores
          and uses == ["00864c90  push esi", "00864c91  mov esi,[esp+8]",
                       "00864ca6  lea edx,[esi+4]", "00864cb4  add esi,14h",
                       "00864cb7  push esi", "00864ce2  pop esi", "00864cf6  pop esi"]
          and text_at(img, 0x00864CDC) == "mov eax,1" and text_at(img, 0x00864CF1) == "mov eax,1"
          and rets == [(0x00864CE3, "ret 8"), (0x00864CF7, "ret 8")],
          uses + ["%08x  %s" % r for r in rets] + ["stores: %d" % len(stores)])

    head = range_ins(img, 0x00864980, 0x00864992)
    head_stores = [i for i in head if i.mnemonic not in (Mnemonic.PUSH,) and i.op_count
                   and i.op0_kind == OpKind.MEMORY and info_f.info(i).op0_access in WRITES]
    claim("xinput-branch-is-a-no-op",
          "0x00864980 called with 1 returns at its epilogue before any store",
          text_at(img, 0x00864CE6) == "push 1" and text_at(img, 0x00864CEB) == "call 00864980h"
          and text_at(img, 0x00864989) == "cmp dword [ebp+0Ch],0"
          and text_at(img, 0x00864992) == "jne short 008649F1h" and not head_stores
          and text_at(img, 0x008649F7) == "ret 8",
          ["%08x  %s" % (i.ip, img.text(i)) for i in head])

    refs = {"the callback 0x00864C90": (0x00864C90, [0x00864E0E]),
            "IsXInputDevice 0x00864680": (0x00864680, [0x00864CB8]),
            "0x00864980": (0x00864980, [0x00864CEB]),
            "the joystick constructor 0x00862B50": (0x00862B50, [0x008649BC]),
            "the DirectInput8Create thunk 0x007C0CE0": (0x007C0CE0, [0x00864D33]),
            "GUID_SysKeyboard 0x0087360C": (0x0087360C, [0x0086377B]),
            "GUID_SysMouse 0x008735FC": (0x008735FC, [0x00863202]),
            "IID_IDirectInput8A 0x0087355C": (0x0087355C, [0x00864D1A]),
            "c_dfDIKeyboard 0x00875D6C": (0x00875D6C, [0x00863798]),
            "c_dfDIMouse 0x00875F74": (0x00875F74, [0x0086321C]),
            "CLSID_WbemLocator 0x008FFF88": (0x008FFF88, [0x008646D1]),
            "IID_IWbemLocator 0x008FFF98": (0x008FFF98, [0x008646C7]),
            "CoCreateInstance's slot 0x008733EC": (0x008733EC, [0x00784103, 0x008646DB]),
            "CoSetProxyBlanket's slot 0x008733E8": (0x008733E8, [0x0086476B]),
            "SysAllocString's slot 0x00873254": (0x00873254, [0x008646F3]),
            "SysFreeString's slot 0x00873250": (0x00873250, [0x008648F2])}
    ev, held = [], True
    # One pass of the sweep names what this claim and the XInput claim below
    # look for.
    found = references(img, {va for va, _ in refs.values()} | set(XINPUT_NAMED))
    for what, (va, want) in refs.items():
        got = found[va]
        ev.append("%s: %s" % (what, " ".join("%#010x" % g for g in got) or "none"))
        held = held and got == want
    claim("one-site-each",
          "each object, constant and import slot the device rests on is named at exactly "
          "the one site the design cites", held, ev)

    wmi = {0x008646BC: "call dword [8733E0h]", 0x008646CC: "push 1", 0x008646CE: "push ebx",
           0x008646DB: "call dword [8733ECh]",
           0x008646E3: "jl near 0086490Dh", 0x008646ED: "je near 0086490Dh",
           0x008646F3: "mov esi,[873254h]", 0x00864760: "push ebx", 0x00864761: "push ebx",
           0x00864762: "push 3", 0x00864764: "push 3", 0x00864766: "push ebx",
           0x00864767: "push ebx", 0x00864768: "push 0Ah", 0x0086476B: "call dword [8733E8h]",
           0x008647A9: "push 14h", 0x008647AB: "push 2710h", 0x008648D2: "push 14h",
           0x008648D4: "push 2710h", 0x008647F1: "cmp word [esp+30h],8",
           0x0086489F: "cmp edx,[ecx]", 0x008648F2: "mov esi,[873250h]",
           0x0086496C: "call dword [8733E4h]"}
    got = {va: text_at(img, va) for va in wmi}
    ole = sorted(n for _, (d, n) in img.imports.items() if d.upper() == "OLEAUT32.DLL")
    strings = [wstr(img, 0x0089F068), wstr(img, 0x0089F088), wstr(img, 0x0089F0A8)]
    claim("is-xinput-device",
          "IsXInputDevice creates WbemLocator, sets the blanket (10, 0, NULL, 3, 3, NULL, 0), "
          "enumerates Win32_PNPEntity 20 at a time with a 10000 ms timeout, reads DeviceID "
          "as VT_BSTR and frees only the three names it allocated: OLEAUT32 gives it "
          "SysAllocString and SysFreeString and nothing else",
          got == wmi and ole == ["SysAllocString", "SysFreeString"]
          and strings == ["\\\\.\\root\\cimv2", "Win32_PNPEntity", "DeviceID"]
          and guid(img, 0x008FFF88) == "4590F811-1D3A-11D0-891F-00AA004B2E24"
          and guid(img, 0x008FFF98) == "DC12A687-737F-11CF-884D-00AA004B2E24"
          and c_bytes(wt, "CLSID_WBEMLOCATOR") == guid(img, 0x008FFF88)
          and c_bytes(wt, "IID_IWBEMLOCATOR") == guid(img, 0x008FFF98),
          ["%08x  %s" % (va, s) for va, s in sorted(got.items())]
          + ["OLEAUT32: %s" % ", ".join(ole), "strings: %r" % strings])

    fmt = {va: list(int.from_bytes(img.read(va + 4 * k, 4), "little") for k in range(6))
           for va in (0x00875D6C, 0x00875F74)}
    push = {va: text_at(img, va) for va in (0x0086380A, 0x0086387C, 0x0086327F, 0x0086339B,
                                            0x008637AB, 0x0086322F, 0x00864D1F, 0x00864E0B,
                                            0x00864E0E, 0x00864E13)}
    lost = {va: text_at(img, va) for va in (0x00863896, 0x008638AB, 0x008633B2, 0x008633CB)}
    claim("formats-and-pushes",
          "the keyboard format is 256 bytes and the mouse's 16, every GetDeviceState "
          "pushes the matching size, SetCooperativeLevel pushes 6, DirectInput8Create "
          "pushes version 0x800, EnumDevices pushes (4, 0x00864C90, pvRef, 1), and both "
          "polls spin while Acquire answers DIERR_INPUTLOST",
          fmt[0x00875D6C][:5] == [0x18, 0x10, 2, 0x100, 256]
          and fmt[0x00875F74][:5] == [0x18, 0x10, 2, 0x10, 7]
          and push == {0x0086380A: "push 100h", 0x0086387C: "push 100h",
                       0x0086327F: "push 10h", 0x0086339B: "push 10h",
                       0x008637AB: "push 6", 0x0086322F: "push 6", 0x00864D1F: "push 800h",
                       0x00864E0B: "push 1", 0x00864E0E: "push 864C90h", 0x00864E13: "push 4"}
          and all(s == "cmp eax,8007001Eh" for s in lost.values()),
          ["%08x  %s" % kv for kv in sorted({**push, **lost}.items())]
          + ["%#010x: %s" % (va, [hex(x) for x in f]) for va, f in fmt.items()])

    ids = {"GUID_KEYBOARD": 0x0087360C, "GUID_MOUSE": 0x008735FC, "IID_DI8A": 0x0087355C}
    claim("module-guids-are-the-images",
          "the GUIDs runtime/sa2_dinput.c compares against are the 16 bytes the image holds",
          all(c_bytes(dt, n) == guid(img, va) for n, va in ids.items()),
          ["%s = %s at %#010x" % (n, guid(img, va), va) for n, va in ids.items()])

    # runtime/sa2_iface.c refuses an XInput slot past 3 by name, on the strength
    # of these two loops and the thunk's three other callers below. Each loop
    # keeps its slot in a register it zeroes, homes it in a stack slot, reloads
    # it after the pad object's construction clobbers it, and leaves at 4.
    loops = {0x00864E1B: "xor esi,esi", 0x00864E1D: "mov [esp+10h],esi",
             0x00864E28: "push esi", 0x00864E29: "call 007C0CCEh",
             0x00864EDC: "mov esi,[esp+10h]", 0x00864EE6: "inc esi",
             0x00864EE7: "mov [esp+10h],esi", 0x00864EEB: "cmp esi,4",
             0x00864EEE: "jb near 00864E21h",
             0x00864A19: "xor edi,edi", 0x00864A1B: "mov [esp+14h],edi",
             0x00864A26: "push edi", 0x00864A27: "call 007C0CCEh",
             0x00864A67: "mov edi,[esp+14h]", 0x00864A6B: "inc edi",
             0x00864A6C: "mov [esp+14h],edi", 0x00864A70: "cmp edi,4",
             0x00864A73: "jb short 00864A1Fh"}
    got = {va: text_at(img, va) for va in loops}
    slot = img.thunks.get(0x007C0CCE)
    called = img.imports.get(slot) if slot is not None else None
    # The thunk's other three callers. The pad object's methods at 0x00863D51
    # and 0x00863DF1 pass its +8 (`this` is ESI, from ECX); the object is built
    # only where its vtable is stored, and of those two sites 0x00864E4A stores
    # the start-up scan's slot at +8 and 0x00863E70 is a constructor nothing
    # names. 0x00863D2D is in 0x00863D20, which nothing names either. Each
    # function start is the instruction after an int3 pad.
    pad = {0x00863D1F: "int3", 0x00863D20: "mov edx,[ecx+8]", 0x00863D2D: "call 007C0CCEh",
           0x00863D3F: "int3", 0x00863D40: "sub esp,14h", 0x00863D49: "mov esi,ecx",
           0x00863D4B: "mov ecx,[esi+8]", 0x00863D50: "push ecx", 0x00863D51: "call 007C0CCEh",
           0x00863DDF: "int3", 0x00863DE0: "sub esp,14h", 0x00863DE9: "mov esi,ecx",
           0x00863DEB: "mov ecx,[esi+8]", 0x00863DF0: "push ecx", 0x00863DF1: "call 007C0CCEh",
           0x00863E6F: "int3", 0x00863E75: "mov dword [eax],8FFFE0h",
           0x00864E4A: "mov [eax+8],esi", 0x00864E53: "mov dword [eax],8FFFE0h"}
    got_pad = {va: text_at(img, va) for va in pad}
    methods = {k: int.from_bytes(img.read(0x008FFFE0 + 4 * k, 4), "little") for k in (5, 7)}
    unbroken = all(i.mnemonic not in (Mnemonic.RET, Mnemonic.INT3, Mnemonic.JMP)
                   for a, b in ((0x00863D20, 0x00863D2D), (0x00863D40, 0x00863D51),
                                (0x00863DE0, 0x00863DF1), (0x00863E70, 0x00863E75))
                   for i in range_ins(img, a, b))
    stored = data_references(img, {0x00863D20, 0x00863E70})
    named = {va: sorted(found[va]) for va in XINPUT_NAMED}
    claim("xinput-scans-slots-0-to-3",
          "both XInputGetCapabilities scans, the start-up one and the rescan, count their slot "
          "up from 0 and stop at 4; the thunk's only other calls are the pad object's "
          "methods at 0x00863D51 and 0x00863DF1 (vtable 0x008FFFE0 slots 7 and 5), which "
          "pass the object's +8, set where it is built, which only the start-up scan does, "
          "from its slot (0x00864E4A); and 0x00863D2D in 0x00863D20, which nothing names; "
          "so the image asks for slots 0 to 3 only",
          got == loops and called is not None
          and runtime_import_name(called[1]) == "Ordinal#4"
          and called[0].upper() == "XINPUT1_3.DLL"
          and named == XINPUT_NAMED and got_pad == pad and unbroken
          and methods == {5: 0x00863DE0, 7: 0x00863D40}
          and stored == {0x00863D20: [], 0x00863E70: []},
          ["%08x  %s" % (va, s) for va, s in sorted({**got, **got_pad}.items())]
          + ["0x007c0cce jumps through %s" % ("%#010x (%s)" % (slot, called[1]) if called
                                            else "no import slot")]
          + ["%#010x named at: %s" % (va, " ".join("%#010x" % g for g in sites) or "none")
             for va, sites in sorted(named.items())]
          + ["vtable 0x008fffe0 slot %d: %#010x" % kv for kv in sorted(methods.items())]
          + ["%#010x held by relocated dwords at: %s"
             % (va, " ".join("%#010x" % g for g in at) or "none")
             for va, at in sorted(stored.items())]
          + ["no ret, int3 or jmp between each function start and its site: %s" % unbroken])
    return out


# What the XInput claim requires references() to find for each address: the
# thunk's five callers, the two stores of the pad object's vtable, and nobody
# for 0x00863D20 and the constructor 0x00863E70.
XINPUT_NAMED = {
    0x007C0CCE: [0x00863D2D, 0x00863D51, 0x00863DF1, 0x00864A27, 0x00864E29],
    0x008FFFE0: [0x00863E75, 0x00864E53],
    0x00863D20: [],
    0x00863E70: [],
}


# -------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--claims", action="store_true")
    ap.add_argument("--stubs", action="store_true",
                    help="also require runtime/sa2_imports.c to have a stub with the pushed "
                         "argument count for every import IsXInputDevice and the input "
                         "manager call")
    ap.add_argument("--argc", type=Path, default=None,
                    help="compare a run's published dinput/wbem method tables instead of "
                         "the sources'")
    args = ap.parse_args()

    img = IM.Image(args.exe)
    calls = scan(img)
    declared = argc_from_run(args.argc) if args.argc else module_rows()
    res = check(calls, declared, stub_rows() if args.stubs else None)
    doc: dict = {
        "schema": "sa2-dinput-surface/v1",
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exe": IM.rel(args.exe), "exe_sha256": img.sha256,
        "declared_from": str(args.argc) if args.argc else
        [IM.rel(DINPUT_C), IM.rel(WBEM_C)],
        "functions": {"%#010x" % k: {"last": "%#010x" % v[0], "what": v[1]}
                      for k, v in FUNCTIONS.items()},
        "check": res,
    }
    for s in res["sites"]:
        d = s.get("declared") or {}
        print("%s %-22s slot %2d %-20s pushes %d, declared %s" % (
            s["site"], s["interface"], s["slot"], s["method"], s["argc"], d.get("argc")))
    for s in res["imports"]:
        print("%s %-32s pushes %d%s" % (s["site"], s["decoded"], s["argc"],
                                        ", stub %s" % s.get("stub_argc") if args.stubs else ""))
    for p in res["problems"]:
        print("PROBLEM", json.dumps(p))
    ok = res["ok"]
    if args.claims:
        doc["claims"] = claims(img)
        for c in doc["claims"]:
            print("claim %-28s %s" % (c["id"], "held" if c["held"] else "FAILED"))
        ok = ok and all(c["held"] for c in doc["claims"])
    print("dinput surface ok:", ok, "(%d sites, %d imports)" % (len(res["sites"]),
                                                                  len(res["imports"])))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(doc, indent=1) + "\n")
        print("->", args.json)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
