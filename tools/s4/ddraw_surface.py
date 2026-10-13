#!/usr/bin/env python3
"""Every DirectDraw call the shipped image makes, re-derived from its bytes,
and every argument fact runtime/sa2_ddraw.c refuses on.

Subtitle: find the routine that loads "ddraw.dll", follow the stack slots its
two entry points fill into the vtable calls that consume them, and print each
DirectDraw call with the dwords it pushes; `--check` re-derives each fact the
runtime's comments and refusals cite, `--argc` compares the runtime's declared
argument counts and handlers with the image, and `--write-profiles DIR` writes
the made-up device profiles harness/ddraw_probe.c runs under.

Why this exists
---------------
runtime/sa2_ddraw.c answers one routine and refuses by name every argument
that routine never passes: another flag word, an outer unknown, another IID,
another capability mask, a free-memory pointer. Each refusal cites the address
that proves the image never passes it. A citation nobody re-derives drifts, so
this re-derives them, and a reader checks the tool rather than twenty
addresses.

The argument counts matter for the reason they mattered for d3d9 at S4 slice 6
and Steam at slice 9: a `__stdcall` method declared with the wrong count pops
the wrong number of dwords and the caller's frame drifts silently.

How it follows the pointers
---------------------------
The routine is decoded linearly from its entry, which is the success path: its
branches all jump forward, to failure exits. The stack pointer is tracked
relative to the routine's entry until `and esp, imm` aligns it, and relative to
the aligned frame after that; `ebp` keeps the entry frame, so `[ebp+8]` is the
routine's first argument. A `lea` of a stack slot is that slot's address, and a
call that is handed one as an out-pointer fills it: DirectDrawCreate's `lplpDD`
with an IDirectDraw, QueryInterface's `ppvObj` with the interface its IID
names. A call through `[[slot]+N]` is then a method call on what the slot holds.

Argument counts follow tools/s4/dsound_surface.py's rule: the last push before
a method call is `this`, and walking back from there a push is an argument when
it is an immediate or a register written since the previous call, which leaves
out a register the prologue saves. An import or an entry point takes every push
since the previous call.

The probe's profiles
--------------------
harness/ddraw_probe.c has to exercise the HMONITOR match the game makes at
0x00865C27, and the built-in default profile has no display on adapter 0's
monitor, while a capture's profile is host inventory that no committed file
may hold. So the probe runs under displays made up here: GUIDs no display has,
strings no driver writes, a video-memory figure no card reports. They are
written by tools/s4/device_profile.py's `dump`, the Python writer the readers
are tested against, so they carry every record the grammar has, and
Documentation/scripts/tests/test_ddraw_surface.py reads each back with that
file's reader.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/ddraw_surface.py --check
  .venv/bin/python tools/s4/ddraw_surface.py --check --argc RUN.json --json OUT.json
  .venv/bin/python tools/s4/ddraw_surface.py --falsify
  .venv/bin/python tools/s4/ddraw_surface.py --write-profiles DIR
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import device_profile as DP  # noqa: E402
import image as IM  # noqa: E402
from iced_x86 import Decoder, FlowControl, Mnemonic, OpKind, Register  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
IMPORTS_C = ROOT / "runtime" / "sa2_imports.c"

MODULE = "ddraw.dll"
ENUMERATE = "DirectDrawEnumerateExA"
CREATE = "DirectDrawCreate"
# {15E65EC0-3B9C-11D2-B92F-00609797EA5B}, as it sits in memory.
IID_IDIRECTDRAW7 = bytes.fromhex("c05ee6159c3bd211b92f00609797ea5b")
DDENUM_ATTACHEDSECONDARYDEVICES = 1
CAPS_LOCAL_VIDEO = 0x10004000          # DDSCAPS_VIDEOMEMORY | DDSCAPS_LOCALVIDMEM

# The published vtable orders. They name what the image calls; each name the
# runtime answers for is held to a witness in `check`.
_DD_COMMON = [
    "QueryInterface", "AddRef", "Release", "Compact", "CreateClipper", "CreatePalette",
    "CreateSurface", "DuplicateSurface", "EnumDisplayModes", "EnumSurfaces",
    "FlipToGDISurface", "GetCaps", "GetDisplayMode", "GetFourCCCodes", "GetGDISurface",
    "GetMonitorFrequency", "GetScanLine", "GetVerticalBlankStatus", "Initialize",
    "RestoreDisplayMode", "SetCooperativeLevel", "SetDisplayMode", "WaitForVerticalBlank",
]
NAMES = {
    "IDirectDraw": _DD_COMMON,
    "IDirectDraw7": _DD_COMMON + [
        "GetAvailableVidMem", "GetSurfaceFromDC", "RestoreAllSurfaces",
        "TestCooperativeLevel", "GetDeviceIdentifier", "StartModeTest", "EvaluateMode",
    ],
}

# Partial registers name their full register; this routine and its callback
# write none, but a write through one must not leave a stale symbol behind.
_FULL = {}
for _full, _parts in ((Register.EAX, ("AL", "AH", "AX")), (Register.ECX, ("CL", "CH", "CX")),
                      (Register.EDX, ("DL", "DH", "DX")), (Register.EBX, ("BL", "BH", "BX")),
                      (Register.ESI, ("SI",)), (Register.EDI, ("DI",)), (Register.EBP, ("BP",)),
                      (Register.ESP, ("SP",))):
    _FULL[_full] = _full
    for _p in _parts:
        _FULL[getattr(Register, _p)] = _full

IMMEDIATE = {OpKind.IMMEDIATE8, OpKind.IMMEDIATE16, OpKind.IMMEDIATE32,
             OpKind.IMMEDIATE8TO16, OpKind.IMMEDIATE8TO32}


def sdisp(ins) -> int:
    d = ins.memory_displacement & 0xFFFFFFFF
    return d - (1 << 32) if d >= 0x80000000 else d


def norm(sym):
    """A symbol with every value loaded from a stack slot replaced by what the
    slot held, so two routes to the same object compare equal."""
    if sym is None:
        return None
    if sym[0] == "stack":
        return norm(sym[2]) if sym[2] is not None else ("slot", sym[1])
    if sym[0] == "deref":
        return ("deref", norm(sym[1]), sym[2])
    if sym[0] == "shr":
        return ("shr", norm(sym[1]), sym[2])
    return sym


def show(sym) -> str:
    s = norm(sym)
    if s is None:
        return "?"
    k = s[0]
    if k == "const":
        return "%#x" % s[1]
    if k == "arg":
        return "arg%d" % s[1]
    if k == "addr":
        return "&frame%d[%+#x]" % s[1]
    if k == "slot":
        return "frame%d[%+#x]" % s[1]
    if k == "deref":
        return "[%s%+#x]" % (show(s[1]), s[2])
    if k == "iface":
        return s[1]
    if k == "shr":
        return "(%s >> %d)" % (show(s[1]), s[2])
    return "%s(%s)" % (k, ", ".join(str(x) for x in s[1:]))


def cstring_at(img: "IM.Image", va: int, limit: int = 64) -> str | None:
    data = img.read(va, limit)
    if not data:
        return None
    end = data.find(b"\0")
    if end <= 0 or not all(0x20 <= b < 0x7F for b in data[:end]):
        return None
    return data[:end].decode("ascii")


def string_va(img: "IM.Image", text: str) -> list[int]:
    """Every VA holding `text` NUL-terminated and starting after a NUL (or at a
    section's start), case-blind: a module name is matched the way the loader
    matches it."""
    want = text.lower().encode("ascii") + b"\0"
    out = []
    for sec in img.sections:
        data = img.read(sec.va, sec.vsize) or b""
        low = data.lower()
        at = low.find(want)
        while at >= 0:
            if at == 0 or data[at - 1] == 0:
                out.append(sec.va + at)
            at = low.find(want, at + 1)
    return sorted(out)


class Trace:
    """A linear decode of one routine with the stack slots it names followed."""

    def __init__(self, img: "IM.Image", entry: int, follow_out: bool = True,
                 limit: int = 4000) -> None:
        self.img, self.entry, self.follow_out = img, entry, follow_out
        self.frame, self.esp, self.esp_known = 0, 0, True
        self.ebp = None                  # (frame, offset) when ebp holds a frame address
        self.regs: dict[int, tuple] = {}
        self.fresh: set[int] = set()
        self.slots: dict[tuple, tuple] = {}
        self.reads: list[tuple[int, tuple]] = []        # (site, slot) every stack load
        self.stores: list[dict] = []                    # stores through a non-stack base
        self.compares: list[tuple[int, tuple, tuple]] = []
        self.branches: list[tuple[int, int]] = []       # (site, target)
        self.eax_writes: list[tuple[int, tuple]] = []  # (site, symbol) each write to eax
        self.pushes: list[tuple] = []                   # (symbol, fresh) since the last call
        self.calls: list[dict] = []
        self.ret = None
        self.text: list[tuple[int, str]] = []
        sec = img.section_of(entry)
        if sec is None or not sec.executable:
            raise SystemExit(f"DDRAW_SURFACE_FAILED {entry:#010x} is not in an executable section")
        data = img.read(entry, sec.end - entry) or b""
        for n, ins in enumerate(Decoder(32, data, ip=entry)):
            if n >= limit or ins.is_invalid:
                break
            self.text.append((ins.ip, img.text(ins)))
            if self.step(ins):
                break

    # ------------------------------------------------------------ operands
    def slot_of(self, ins) -> tuple | None:
        if ins.memory_index != Register.NONE:
            return None
        if ins.memory_base == Register.ESP and self.esp_known:
            return (self.frame, self.esp + sdisp(ins))
        if ins.memory_base == Register.EBP and self.ebp is not None:
            return (self.ebp[0], self.ebp[1] + sdisp(ins))
        return None

    def load(self, slot: tuple) -> tuple:
        # The entry frame's positive offsets are the caller's arguments:
        # [entry esp] is the return address, [entry esp + 4n] argument n.
        val = self.slots.get(slot)
        if val is None and slot[0] == 0 and slot[1] >= 4 and slot[1] % 4 == 0:
            val = ("arg", slot[1] // 4)
        return ("stack", slot, val)

    def value(self, ins, k: int):
        kind = ins.op_kind(k)
        if kind == OpKind.REGISTER:
            return self.regs.get(_FULL.get(ins.op_register(k), ins.op_register(k)))
        if kind in IMMEDIATE:
            return ("const", ins.immediate(k) & 0xFFFFFFFF)
        if kind == OpKind.MEMORY:
            if ins.memory_base == Register.NONE and ins.memory_index == Register.NONE:
                disp = ins.memory_displacement & 0xFFFFFFFF
                if disp in self.img.imports:
                    return ("import", "!".join(self.img.imports[disp]))
                return ("absmem", disp)
            slot = self.slot_of(ins)
            if slot is not None:
                self.reads.append((ins.ip, slot))
                return self.load(slot)
            if ins.memory_index == Register.NONE:
                return ("deref", self.regs.get(_FULL.get(ins.memory_base, ins.memory_base)),
                        sdisp(ins))
        return None

    def setreg(self, reg: int, sym) -> None:
        full = _FULL.get(reg, reg)
        if reg != full:
            sym = None                   # a partial write leaves no symbol worth keeping
        self.regs[full] = sym
        self.fresh.add(full)
        if full == Register.EAX:
            self.eax_writes.append((self._ip, sym))

    # ---------------------------------------------------------------- step
    def step(self, ins) -> bool:
        self._ip = ins.ip
        m = ins.mnemonic
        op0_reg = ins.op0_kind == OpKind.REGISTER
        r0 = _FULL.get(ins.op0_register, ins.op0_register) if op0_reg else None
        if m == Mnemonic.PUSH:
            v = self.value(ins, 0)
            fresh = (not op0_reg) or r0 in self.fresh
            self.esp -= 4
            if self.esp_known:
                self.slots[(self.frame, self.esp)] = v
            self.pushes.append((v, fresh))
            return False
        if m == Mnemonic.POP:
            self.esp += 4
            if op0_reg and r0 != Register.ESP:
                self.setreg(ins.op0_register, None)
            return False
        if m in (Mnemonic.SUB, Mnemonic.ADD) and r0 == Register.ESP and ins.op1_kind in IMMEDIATE:
            n = ins.immediate(1) & 0xFFFFFFFF
            self.esp += -n if m == Mnemonic.SUB else n
            return False
        if m == Mnemonic.AND and r0 == Register.ESP:
            # Aligned: a new frame, and every slot after this is relative to it.
            self.frame += 1
            self.esp = 0
            return False
        if m == Mnemonic.MOV and r0 == Register.EBP and ins.op1_kind == OpKind.REGISTER \
                and ins.op1_register == Register.ESP:
            self.ebp = (self.frame, self.esp)
            self.fresh.add(Register.EBP)
            return False
        if m == Mnemonic.MOV and r0 == Register.ESP:
            self.esp_known = False       # the epilogue
            return False
        if m == Mnemonic.MOV:
            v = self.value(ins, 1)
            if op0_reg:
                self.setreg(ins.op0_register, v)
            elif ins.op0_kind == OpKind.MEMORY:
                slot = self.slot_of(ins)
                if slot is not None:
                    self.slots[slot] = v
                elif ins.memory_index == Register.NONE and ins.memory_base != Register.NONE:
                    self.stores.append({
                        "site": ins.ip,
                        "base": self.regs.get(_FULL.get(ins.memory_base, ins.memory_base)),
                        "disp": sdisp(ins), "size": ins.memory_size, "value": v})
            return False
        if m == Mnemonic.LEA and op0_reg:
            slot = self.slot_of(ins)
            self.setreg(ins.op0_register, ("addr", slot) if slot is not None else None)
            return False
        if m == Mnemonic.XOR and op0_reg and ins.op1_kind == OpKind.REGISTER \
                and ins.op1_register == ins.op0_register:
            self.setreg(ins.op0_register, ("const", 0))
            return False
        if m == Mnemonic.SHR and op0_reg and ins.op1_kind in IMMEDIATE:
            self.setreg(ins.op0_register, ("shr", self.regs.get(r0), ins.immediate(1) & 31))
            return False
        if m == Mnemonic.CMP:
            self.compares.append((ins.ip, self.value(ins, 0), self.value(ins, 1)))
            return False
        if m == Mnemonic.CALL:
            self.call(ins)
            return False
        if m == Mnemonic.RET:
            self.ret = {"site": ins.ip,
                        "pops": (ins.immediate(0) & 0xFFFF) if ins.op_count else 0}
            return True
        if ins.flow_control == FlowControl.CONDITIONAL_BRANCH:
            self.branches.append((ins.ip, ins.near_branch_target))
            return False
        if m in (Mnemonic.TEST, Mnemonic.JMP, Mnemonic.NOP, Mnemonic.INT3):
            return False
        if op0_reg:
            self.setreg(ins.op0_register, None)
        return False

    # ---------------------------------------------------------------- calls
    def call(self, ins) -> None:
        pushes, target = self.pushes, None
        row: dict = {"site": ins.ip, "return_to": ins.next_ip, "pushes_total": len(pushes),
                     "slots_before": dict(self.slots)}
        if ins.op0_kind == OpKind.MEMORY:
            target = self.value(ins, 0)
        elif ins.op0_kind == OpKind.REGISTER:
            target = self.regs.get(_FULL.get(ins.op0_register, ins.op0_register))
        elif ins.op0_kind in (OpKind.NEAR_BRANCH32, OpKind.NEAR_BRANCH16):
            target = ("const", ins.near_branch_target)
        t = norm(target)
        result = ("ret", ins.ip)
        pop = None
        if t and t[0] == "import":
            args = [p[0] for p in reversed(pushes)]
            row.update(kind="import", name=t[1], argc=len(args), args=args)
            pop = len(args)
            short = t[1].split("!")[-1]
            if short == "LoadLibraryA" and args and norm(args[0]) and norm(args[0])[0] == "const":
                result = ("module", cstring_at(self.img, norm(args[0])[1]))
            if short == "GetProcAddress" and len(args) == 2 and norm(args[1]) \
                    and norm(args[1])[0] == "const":
                result = ("proc", cstring_at(self.img, norm(args[1])[1]))
        elif t and t[0] == "proc":
            args = [p[0] for p in reversed(pushes)]
            row.update(kind="entry", name=t[1], argc=len(args), args=args)
            pop = len(args)
            if t[1] == CREATE and len(args) == 3 and self.follow_out:
                self.fill(args[1], ("iface", "IDirectDraw"))
        elif t and t[0] == "deref" and t[1] and t[1][0] == "deref" and t[1][2] == 0 \
                and t[2] % 4 == 0:
            obj = t[1][1]
            slot = t[2] // 4
            this_ok = bool(pushes) and norm(pushes[-1][0]) == obj
            n = 0
            if this_ok:
                for sym, fresh in reversed(pushes[:-1]):
                    s = norm(sym)
                    if (s is not None and s[0] == "const") or fresh:
                        n += 1
                    else:
                        break
            args = [p[0] for p in reversed(pushes[len(pushes) - 1 - n:len(pushes) - 1])]
            iface = obj[1] if obj and obj[0] == "iface" else None
            names = NAMES.get(iface or "", [])
            row.update(kind="method", iface=iface, object=show(obj), slot=slot,
                       name=names[slot] if slot < len(names) else None,
                       this_is_the_object=this_ok, argc=n if this_ok else None, args=args,
                       argc_upper=max(len(pushes) - 1, 0))
            pop = n + 1 if this_ok else None
            if iface == "IDirectDraw" and slot == 0 and len(args) == 2 and self.follow_out:
                riid = norm(args[0])
                if riid and riid[0] == "const" and self.img.read(riid[1], 16) == IID_IDIRECTDRAW7:
                    self.fill(args[1], ("iface", "IDirectDraw7"))
        else:
            row.update(kind="other", name=show(target), argc=None, args=[])
        self.calls.append(row)
        if pop is None:
            self.esp_known = False       # a callee whose pops this cannot count
        else:
            self.esp += 4 * pop
        for r in (Register.EAX, Register.ECX, Register.EDX):
            self.regs.pop(r, None)
        self.regs[Register.EAX] = result
        self.pushes = []
        self.fresh = set()

    def fill(self, ptr, sym) -> None:
        p = norm(ptr)
        if p and p[0] == "addr" and p[1] is not None:
            self.slots[p[1]] = sym


# ----------------------------------------------------------------- the surface

def surface(img: "IM.Image", entries: list[int], follow_out: bool = True) -> dict:
    """The DirectDraw routines, their calls, and the callback each enumerates with."""
    strings = {k: string_va(img, v) for k, v in
               (("module", MODULE), ("enumerate", ENUMERATE), ("create", CREATE))}
    loadlib = {slot for slot, (dll, name) in img.imports.items() if name == "LoadLibraryA"}
    sites = []
    module_vas = set(strings["module"])
    for k, ins in enumerate(img.instructions):
        if ins.mnemonic != Mnemonic.PUSH or ins.op0_kind not in IMMEDIATE:
            continue
        if (ins.immediate(0) & 0xFFFFFFFF) not in module_vas:
            continue
        for nxt in img.instructions[k + 1:k + 12]:
            if nxt.mnemonic == Mnemonic.CALL:
                if IM.abs_displacement(nxt) in loadlib:
                    sites.append(ins.ip)
                break
    starts = sorted(entries)
    routines = []
    for site in sites:
        entry = max((e for e in starts if e <= site), default=None)
        if entry is None:
            routines.append({"load_site": site, "entry": None})
            continue
        tr = Trace(img, entry, follow_out=follow_out)
        routine = {"load_site": site, "entry": entry, "trace": tr,
                   "calls": tr.calls, "callback": None}
        for c in tr.calls:
            if c["kind"] == "entry" and c["name"] == ENUMERATE and c["args"]:
                cb = norm(c["args"][0])
                if cb and cb[0] == "const":
                    routine["callback"] = {"va": cb[1], "trace": Trace(img, cb[1], limit=200)}
        routines.append(routine)
    return {"strings": strings, "load_sites": sites, "routines": routines}


def _find(calls, **want):
    return [c for c in calls if all(c.get(k) == v for k, v in want.items())]


def check(surf: dict) -> dict:
    """Every fact runtime/sa2_ddraw.c cites, re-derived. Each is held or not,
    with the detail that decided it."""
    res: dict = {"facts": {}, "ok": True}

    def fact(name: str, held: bool, detail: str) -> None:
        res["facts"][name] = {"held": bool(held), "detail": detail}
        res["ok"] = res["ok"] and bool(held)

    routines = [r for r in surf["routines"] if r.get("entry") is not None]
    fact("one-routine", len(surf["load_sites"]) == 1 and len(routines) == 1,
         "%d push(es) of the module name followed by LoadLibraryA, %d with a routine entry"
         % (len(surf["load_sites"]), len(routines)))
    if len(routines) != 1:
        return res
    r = routines[0]
    tr: Trace = r["trace"]
    calls = r["calls"]

    def imported(name: str) -> list[dict]:
        return [c for c in calls if c["kind"] == "import" and c["name"].split("!")[-1] == name]

    ll, fl = imported("LoadLibraryA"), imported("FreeLibrary")
    absent = [b for b in tr.branches if ll and b[0] > ll[0]["site"]][:1]
    fact("absent-branch",
         bool(ll and fl and absent and absent[0][1] >= fl[-1]["return_to"]),
         "the first branch after LoadLibraryA (%s) lands at %s; FreeLibrary returns to %s"
         % (hex(absent[0][0]) if absent else "-", hex(absent[0][1]) if absent else "-",
            hex(fl[-1]["return_to"]) if fl else "-"))

    en = [c for c in calls if c["kind"] == "entry" and c["name"] == ENUMERATE]
    cr = [c for c in calls if c["kind"] == "entry" and c["name"] == CREATE]
    ctx = None
    if len(en) == 1 and en[0]["argc"] == 3:
        a = [norm(x) for x in en[0]["args"]]
        # The context is followed whatever the flags say, so a wrong flag word
        # breaks this fact and no other.
        ctx = a[1][1] if a[1] and a[1][0] == "addr" else None
        ok = (a[0] and a[0][0] == "const" and ctx is not None
              and a[2] == ("const", DDENUM_ATTACHEDSECONDARYDEVICES))
        fact("enumerate", ok, "one call at %#x with 3 pushes: callback %s, context %s, flags %s"
             % (en[0]["site"], show(a[0]), show(a[1]), show(a[2])))
    else:
        fact("enumerate", False, "%d DirectDrawEnumerateExA call(s), pushes %s"
             % (len(en), [c["argc"] for c in en]))

    cb = r.get("callback")
    if cb:
        ct: Trace = cb["trace"]
        guid, context, hm = ("arg", 1), ("arg", 4), ("arg", 5)   # LPDDENUMCALLBACKEXA's
        cmp_ok = any({norm(x), norm(y)} == {("deref", context, 0x10), hm}
                     for _, x, y in ct.compares)
        flag = [s for s in ct.stores if norm(s["base"]) == context and s["disp"] == 0x14
                and norm(s["value"]) == ("const", 1)]
        copies = sorted(s["disp"] for s in ct.stores if norm(s["base"]) == context
                        and norm(s["value"]) == ("deref", guid, s["disp"]))
        # Every path returns TRUE when the last write to eax before the `ret` is
        # the constant 1 and every branch lands at or before that write.
        last = ct.eax_writes[-1] if ct.eax_writes else None
        eax_one = bool(last) and norm(last[1]) == ("const", 1)
        paths_ok = bool(last) and all(t <= last[0] for _, t in ct.branches)
        fact("callback",
             bool(ct.ret and ct.ret["pops"] == 0x14 and cmp_ok and flag
                  and copies == [0, 4, 8, 12] and eax_one and paths_ok),
             "%#x: ret pops %s; compares [arg4+0x10] with arg5: %s; sets byte [arg4+0x14]: %s; "
             "copies arg1[0..15] to arg4[0..15]: %s; returns 1 on every path: %s"
             % (cb["va"], hex(ct.ret["pops"]) if ct.ret else "-", cmp_ok, bool(flag),
                copies == [0, 4, 8, 12], eax_one and paths_ok))
    else:
        fact("callback", False, "no constant callback was pushed to the enumeration")

    mon = None
    if ctx is not None and en:
        before = en[0]
        zeroed = all(slot_at(before, (ctx[0], ctx[1] + k)) == ("const", 0)
                     for k in (0, 4, 8, 12, 0x14))
        hm = slot_at(before, (ctx[0], ctx[1] + 0x10))
        mon = next((c for c in calls if hm and hm[0] == "ret" and c["site"] == hm[1]), None)
        flag_read = [s for s, sl in tr.reads if s > before["site"] and sl == (ctx[0], ctx[1] + 0x14)]
        fact("context",
             zeroed and mon is not None and not flag_read,
             "GUID and found flag zeroed before the call: %s; +0x10 holds the result of the call "
             "at %s; the found flag is read after the call %d time(s)"
             % (zeroed, hex(mon["site"]) if mon else "-", len(flag_read)))
        fact("monitor",
             bool(mon and mon["kind"] == "method" and mon["object"] == "[arg1+0x4]"
                  and mon["slot"] == 15 and mon["argc"] == 1
                  and norm(mon["args"][0]) == ("const", 0)),
             "the HMONITOR comes from %s" % (
                 "slot %s of %s with arguments %s" % (mon.get("slot"), mon.get("object"),
                                                      [show(x) for x in mon["args"]])
                 if mon else "no call"))
    else:
        fact("context", False, "no context to follow")
        fact("monitor", False, "no context to follow")

    if len(cr) == 1 and cr[0]["argc"] == 3 and ctx is not None:
        a = [norm(x) for x in cr[0]["args"]]
        dd_slot = a[1][1] if a[1] and a[1][0] == "addr" else None
        zeroed = dd_slot is not None and slot_at(cr[0], dd_slot) == ("const", 0)
        fact("create", a[0] == ("addr", ctx) and a[2] == ("const", 0) and zeroed,
             "one call at %#x: lpGUID %s (the context's GUID field is %s), lplpDD %s zeroed "
             "before the call: %s, pUnkOuter %s"
             % (cr[0]["site"], show(a[0]), show(("addr", ctx)), show(a[1]), zeroed, show(a[2])))
        nxt = [c for c in calls if c["site"] > cr[0]["site"]][:1]
        tested = [b for b in tr.branches if nxt and cr[0]["site"] < b[0] < nxt[0]["site"]]
        fact("unchecked", bool(nxt) and not tested and nxt[0].get("iface") == "IDirectDraw",
             "between DirectDrawCreate and the next call (%s) there are %d branch(es); that "
             "call is on %s" % (hex(nxt[0]["site"]) if nxt else "-", len(tested),
                                nxt[0].get("iface") if nxt else "-"))
    else:
        fact("create", False, "%d DirectDrawCreate call(s)" % len(cr))
        fact("unchecked", False, "no DirectDrawCreate to follow")

    qi = _find(calls, kind="method", iface="IDirectDraw", slot=0)
    riid = norm(qi[0]["args"][0]) if len(qi) == 1 and qi[0]["argc"] == 2 else None
    fact("qi", bool(riid and riid[0] == "const"
                    and tr.img.read(riid[1], 16) == IID_IDIRECTDRAW7),
         "%d QueryInterface call(s) on IDirectDraw, argc %s, riid %s"
         % (len(qi), [c["argc"] for c in qi], show(riid)))
    v1 = [c for c in calls if c.get("iface") == "IDirectDraw"]
    fact("v1-only-queried", len(v1) == 1 and v1[0]["slot"] == 0,
         "IDirectDraw is called at slots %s: it is queried and never released"
         % sorted(c["slot"] for c in v1))

    vm = _find(calls, kind="method", iface="IDirectDraw7", slot=23)
    if len(vm) == 1 and vm[0]["argc"] == 3:
        a = [norm(x) for x in vm[0]["args"]]
        caps = a[0][1] if a[0] and a[0][0] == "addr" else None
        words = [slot_at(vm[0], (caps[0], caps[1] + 4 * k)) if caps else None
                 for k in range(4)]
        want = [("const", CAPS_LOCAL_VIDEO), ("const", 0), ("const", 0), ("const", 0)]
        fact("vidmem", words == want and a[1] and a[1][0] == "addr" and a[2] == ("const", 0),
             "GetAvailableVidMem at %#x: DDSCAPS2 %s, lpdwTotal %s, lpdwFree %s"
             % (vm[0]["site"], [show(w) for w in words], show(a[1]), show(a[2])))
        total = a[1][1] if a[1] and a[1][0] == "addr" else None
        kept = [s for s in tr.stores if norm(s["base"]) == ("arg", 1) and s["disp"] == 0x48
                and s["value"] and s["value"][0] == "shr" and s["value"][2] == 20
                and s["value"][1] and s["value"][1][0] == "stack" and s["value"][1][1] == total]
        fact("kept", bool(kept), "the total is kept as total >> 20 at [arg1+0x48]%s"
             % (" at %#x" % kept[0]["site"] if kept else ": no such store"))
    else:
        fact("vidmem", False, "%d GetAvailableVidMem call(s), argc %s"
             % (len(vm), [c["argc"] for c in vm]))
        fact("kept", False, "no GetAvailableVidMem to follow")

    rel = _find(calls, kind="method", iface="IDirectDraw7", slot=2)
    fact("release", len(rel) == 1 and rel[0]["argc"] == 0,
         "%d Release call(s) on IDirectDraw7, argc %s" % (len(rel), [c["argc"] for c in rel]))
    return res


def slot_at(call: dict, slot: tuple):
    """What `slot` held when `call` was made. The trace's own slot map is the
    state at the routine's end, after the failure paths the linear decode also
    walks have written their zeros, so each call keeps its own copy."""
    return norm(call["slots_before"].get(slot))


# ------------------------------------------------------------ against a run

def reached(surf: dict) -> list[dict]:
    """The (interface, slot) pairs the image calls, with the pushed count at each."""
    out = []
    for r in surf["routines"]:
        for c in r.get("calls", []):
            if c["kind"] == "method" and c.get("iface") in NAMES:
                out.append({"interface": c["iface"], "slot": c["slot"], "name": c["name"],
                            "site": "%#010x" % c["site"], "argc": c["argc"]})
    return out


def argc_check(surf: dict, run: dict) -> dict:
    """The runtime's declared counts and handlers against the image: every
    reached slot has a handler and the count the image pushes, and no slot the
    image does not reach has a handler."""
    rows = {(m["interface"], int(m["slot"])): m for m in run.get("ddraw", {}).get("methods", [])}
    out: dict = {"declared_slots": len(rows), "compared": [], "disagreements": [], "ok": True}
    got = reached(surf)
    for s in got:
        d = rows.get((s["interface"], s["slot"]))
        row = dict(s, declared_argc=d["argc"] if d else None,
                   declared_implemented=bool(d and d.get("implemented")))
        out["compared"].append(row)
        if not d or d["argc"] != s["argc"] or not d.get("implemented"):
            out["disagreements"].append(row)
    want = {(s["interface"], s["slot"]) for s in got}
    extra = sorted("%s:%d" % k for k, m in rows.items() if m.get("implemented") and k not in want)
    out["handlers_on_unreached_slots"] = extra
    out["ok"] = bool(got) and not out["disagreements"] and not extra
    return out


def entry_argc_check(surf: dict, dyn_rows: list[dict]) -> dict:
    """The two entry points' g_dyn_table counts, which are what import_native
    pops with, against the dwords the image pushes."""
    declared = {r["name"]: r["argc"] for r in dyn_rows if r["dll"].upper() == "DDRAW"}
    out: dict = {"rows": [], "ok": True}
    for r in surf["routines"]:
        for c in r.get("calls", []):
            if c["kind"] == "entry":
                row = {"name": c["name"], "site": "%#010x" % c["site"], "pushed": c["argc"],
                       "declared": declared.get(c["name"])}
                out["rows"].append(row)
                out["ok"] = out["ok"] and row["declared"] == row["pushed"]
    out["ok"] = out["ok"] and bool(out["rows"])
    return out


def dyn_rows_from_source(path: Path = IMPORTS_C) -> list[dict]:
    import refusal_audit as RA
    return RA.table(path.read_text().split("\n"), "static const dyn_row g_dyn_table[]")


def entries_from_map(path: Path = IM.DEFAULT_MAP) -> list[int]:
    fmap = IM.FunctionMap(path)
    if not fmap.present:
        raise SystemExit(f"DDRAW_SURFACE_FAILED no function map at {path}")
    return [f["address"] for f in fmap.functions]


def to_json(surf: dict) -> dict:
    def row(c: dict) -> dict:
        d = {k: v for k, v in c.items() if k not in ("args", "slots_before")}
        d["site"] = "%#010x" % c["site"]
        d["return_to"] = "%#010x" % c["return_to"]
        d["args"] = [show(a) for a in c.get("args", [])]
        return d
    out = {"strings": {k: ["%#010x" % v for v in vs] for k, vs in surf["strings"].items()},
           "load_sites": ["%#010x" % s for s in surf["load_sites"]], "routines": []}
    for r in surf["routines"]:
        out["routines"].append({
            "load_site": "%#010x" % r["load_site"],
            "entry": "%#010x" % r["entry"] if r.get("entry") is not None else None,
            "callback": "%#010x" % r["callback"]["va"] if r.get("callback") else None,
            "calls": [row(c) for c in r.get("calls", [])],
        })
    return out


# ------------------------------------------------------ the probe's profiles

PROBE_PROFILE = "ddraw-probe.{key}.profile.txt"   # harness/ddraw_probe.c, profile_path
PROBE_TOTAL = 0x12340000                          # made up, and not the default's figure
PROBE_FAILED = 0x80004005                         # E_FAIL, a GetAvailableVidMem that failed


def probe_guid(k: int) -> bytes:
    """The k-th made-up display GUID, {DD7E00kk-5341-3244-8000-50524F424500}."""
    return uuid.UUID(f"dd7e{k:04x}-5341-3244-8000-50524f424500").bytes_le


def probe_profiles() -> dict[str, bytes]:
    """The profiles harness/ddraw_probe.c's cases name, by key.

    match          DP.MAX_DDRAW displays: the primary display driver, two
                   displays on adapter 0's monitor (the game's callback keeps
                   the second, entry 2, since each match copies over the one
                   before), then displays on no monitor whose strings are the
                   longest the grammar holds, so the enumeration records are
                   filled to the capacity sa2_ddraw.c asserts. The strings carry
                   a quote, backslashes and a byte above 0x7e, which are handed
                   to the game byte for byte.
    vidmem-failed  the default displays, and a GetAvailableVidMem that failed.
    count          one display past DP.MAX_DDRAW, which the readers refuse at
                   configuration.
    """
    primary = DP.DDrawDevice(None, -1, b"DirectDraw HAL", b"display")
    longest = DP.NAME_MAX - 1
    match = (primary,
             DP.DDrawDevice(probe_guid(1), 0, b"SA2 probe display A \xe9", b"\\\\.\\PROBE1"),
             DP.DDrawDevice(probe_guid(2), 0, b'SA2 probe display "B"', b"\\\\.\\PROBE2"))
    match += tuple(DP.DDrawDevice(probe_guid(k), -1, b"D" * longest, b"N" * longest)
                   for k in range(3, DP.MAX_DDRAW))
    count = (primary,) + tuple(
        DP.DDrawDevice(probe_guid(k), -1, b"SA2 probe display", b"\\\\.\\PROBE")
        for k in range(1, DP.MAX_DDRAW + 1))
    base = DP.default()
    return {
        "match": DP.dump(dataclasses.replace(base, ddraw=match, vidmem_total=PROBE_TOTAL)),
        "vidmem-failed": DP.dump(dataclasses.replace(base, vidmem_hr=PROBE_FAILED,
                                                     vidmem_total=0)),
        "count": DP.dump(dataclasses.replace(base, ddraw=count)),
    }


def write_probe_profiles(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for key, data in probe_profiles().items():
        path = out / PROBE_PROFILE.format(key=key)
        path.write_bytes(data)
        paths.append(path)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--map", type=Path, default=IM.DEFAULT_MAP)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--argc", type=Path, default=None,
                    help="a probe or run JSON with a ddraw.methods array")
    ap.add_argument("--falsify", action="store_true",
                    help="decode again without following the out-pointers and report what "
                         "is no longer attributed")
    ap.add_argument("--write-profiles", type=Path, default=None, metavar="DIR",
                    help="write harness/ddraw_probe.c's made-up device profiles into DIR")
    args = ap.parse_args()

    if args.write_profiles is not None:
        for path in write_probe_profiles(args.write_profiles):
            print("->", path)
        if not (args.check or args.argc or args.falsify or args.json):
            raise SystemExit(0)

    img = IM.Image(args.exe)
    entries = entries_from_map(args.map)
    surf = surface(img, entries)
    doc: dict = {"schema": "sa2-ddraw-surface/v1",
                 "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "exe": str(args.exe), "exe_sha256": img.sha256}
    doc.update(to_json(surf))
    for r in doc["routines"]:
        print("routine %s (loads %s at %s), callback %s"
              % (r["entry"], MODULE, r["load_site"], r["callback"]))
        for c in r["calls"]:
            if c["kind"] in ("entry", "method") or (c.get("name") or "").endswith(
                    ("LoadLibraryA", "GetProcAddress", "FreeLibrary")):
                if c["kind"] == "method" and c.get("iface"):
                    label = "%s::%s slot %d" % (c["iface"], c["name"], c["slot"])
                elif c["kind"] == "method":
                    label = "slot %d of the object at %s" % (c["slot"], c["object"])
                else:
                    label = c["name"]
                print("  %s  %-44s argc %s  %s" % (c["site"], label, c.get("argc"),
                                                   ", ".join(c["args"])))
    bad = False
    if args.check:
        doc["check"] = check(surf)
        dyn = entry_argc_check(surf, dyn_rows_from_source())
        doc["check"]["entry_argc"] = dyn
        doc["check"]["ok"] = doc["check"]["ok"] and dyn["ok"]
        for k, v in doc["check"]["facts"].items():
            print("  fact %-16s %-5s %s" % (k, v["held"], v["detail"]))
        for r in dyn["rows"]:
            print("  entry %-24s pushed %s, g_dyn_table declares %s" % (r["name"], r["pushed"],
                                                                     r["declared"]))
        print("check ok:", doc["check"]["ok"])
        bad = bad or not doc["check"]["ok"]
    if args.argc:
        doc["argc"] = argc_check(surf, json.loads(args.argc.read_text()))
        for r in doc["argc"]["compared"]:
            print("  argc %-12s slot %2d %-20s declared %s vs %s pushed at %s%s"
                  % (r["interface"], r["slot"], r["name"], r["declared_argc"], r["argc"],
                     r["site"], "" if r not in doc["argc"]["disagreements"] else "   DISAGREES"))
        if doc["argc"]["handlers_on_unreached_slots"]:
            print("  handlers on slots the image never calls:",
                  doc["argc"]["handlers_on_unreached_slots"])
        print("argc ok:", doc["argc"]["ok"])
        bad = bad or not doc["argc"]["ok"]
    if args.falsify:
        blind = surface(img, entries, follow_out=False)
        doc["falsify"] = {"methods_attributed": len(reached(surf)),
                          "methods_attributed_without_out_pointers": len(reached(blind))}
        doc["falsify"]["out_pointers_are_load_bearing"] = (
            doc["falsify"]["methods_attributed"] > 0
            and doc["falsify"]["methods_attributed_without_out_pointers"] == 0)
        print("falsify:", json.dumps(doc["falsify"]))
        bad = bad or not doc["falsify"]["out_pointers_are_load_bearing"]
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(doc, indent=1) + "\n")
        print("->", args.json)
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
