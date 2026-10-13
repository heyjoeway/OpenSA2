#!/usr/bin/env python3
"""The static d3d9/d3dx9 surface of the shipped executable: metric M7's denominator.

Subtitle: `source tools/env.sh && .venv/bin/python tools/s4/d3d9_methods.py`

M7 in `Documentation/SA2_RECOMP_DESIGN.md` section 8 reads

    M7 device | d3d9/d3dx9 methods implemented / methods called by the binary
              | analyzer call-site scan

and has read "not measured" since S0 because neither side of the fraction
exists. This tool produces the *denominator*: the set of d3d9 interface methods
and d3dx9 entry points that the image contains a call site for.

The binary reaches Direct3D through exactly one import, `d3d9.dll!Direct3DCreate9`.
Everything after that is a COM vtable call -- `mov ecx,[obj]; mov eax,[ecx+DISP];
call eax`, or `call [ecx+DISP]` -- and a displacement on its own names nothing,
because slot 7 is `IDirect3D9::EnumAdapterModes` and `IDirect3DDevice9::GetDeviceCaps`
and `IDirect3DTexture9::SetPriority` at the same time. So the scan is deliberately
in two layers that are never mixed:

  **Layer A, the shape census.** Every indirect call whose target came out of a
  vtable load, counted and grouped by displacement, with no claim at all about
  which interface it lands on. This is the population an attributing pass draws
  from, and its size minus the attributed count is the honest error bar.

  **Layer B, the attributed surface.** An abstract interpretation seeded from
  what is certain -- the `Direct3DCreate9` return value, and the eleven named
  `d3dx9_42.dll` imports -- which propagates COM pointer types through
  registers, stack slots, program globals and C++ object fields, across direct
  calls, to a fixpoint, and names a call site only when the interface of the
  receiver is known. A site the propagation cannot type is left in Layer A and
  counted against the result, never guessed at.

The vtable layouts are not typed here. They are parsed out of `runtime/sa2_d3d9.c`,
which is the runtime's own dispatch table and the file M7's *numerator* will be
computed from, so the two cannot disagree about what slot 23 of
`IDirect3DDevice9` is called.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/d3d9_methods.py
  .venv/bin/python tools/s4/d3d9_methods.py --no-falsification
  .venv/bin/python tools/s4/d3d9_methods.py --break shift-displacements
"""

from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

from iced_x86 import (Decoder, Instruction, InstructionInfoFactory, MemorySizeExt, Mnemonic,
                      OpAccess, OpKind, Register)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image import (DEFAULT_EXE, ROOT, FunctionMap, Image,  # noqa: E402
                   abs_displacement, rel, sha256_of)

SCHEMA = "sa2-recomp-measurement/v1"
RECEIPT_ID = "s4-d3d9-methods"
D3D9_C = ROOT / "runtime" / "sa2_d3d9.c"
DEFAULT_OUT = ROOT / "private" / "generated" / "s4" / "receipts" / "d3d9-methods.json"

# --------------------------------------------------------------------------
# the vtable layouts, parsed out of the runtime's own table


ROW_RE = re.compile(
    r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*([A-Za-z0-9_|\s]+?)\s*,\s*\"([A-Za-z0-9_]+)\"\s*,"
    r"\s*(?:[A-Za-z0-9_]+|NULL)\s*\}")


def _macro_body(text: str, name: str) -> str:
    """The body of a line-continued `#define NAME \\ ... ` object-like macro."""
    m = re.search(r"#define\s+" + name + r"\s*\\\n((?:.*\\\n)*.*)\n", text)
    if not m:
        raise SystemExit(f"{D3D9_C}: cannot find #define {name}")
    return m.group(1).replace("\\\n", "\n")


def parse_vtables(path: Path) -> tuple[dict[str, dict[int, dict]], dict[str, str]]:
    """({interface: {slot: {"name", "argc"}}}, {ROWS_X: interface}) out of `sa2_d3d9.c`.

    Nothing about D3D9 is typed in this file: a wrong slot number is a wrong
    method name, and the only way to be sure the scanner and the runtime agree
    is for both to read the same table.
    """
    text = path.read_text(encoding="utf-8")
    macros = {n: _macro_body(text, n) for n in ("RESOURCE_ROWS", "BASETEXTURE_ROWS")}

    tables: dict[str, list[tuple[int, int, str]]] = {}
    for m in re.finditer(r"static const d3d_row (ROWS_[A-Z0-9_]+)\[\]\s*=\s*\{(.*?)\n\};",
                         text, re.S):
        name, body = m.group(1), m.group(2)
        for macro, mbody in macros.items():
            body = body.replace(macro, mbody)
        rows = []
        for r in ROW_RE.finditer(body):
            rows.append((int(r.group(1)), int(r.group(2)), r.group(4)))
        tables[name] = rows

    order = re.search(r"static d3d_type g_types\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if not order:
        raise SystemExit(f"{path}: cannot find g_types[]")
    ifaces: dict[str, dict[int, dict]] = {}
    rows_of: dict[str, str] = {}
    for m in re.finditer(r"\{\s*\"(IDirect3D[A-Za-z0-9]*|ID3DXConstantTable)\"\s*,\s*(ROWS_[A-Z0-9_]+)",
                         order.group(1)):
        iface, rows_name = m.group(1), m.group(2)
        if rows_name not in tables:
            raise SystemExit(f"{path}: {iface} names {rows_name}, which was not parsed")
        ifaces[iface] = {slot: {"name": nm, "argc": argc}
                         for slot, argc, nm in tables[rows_name]}
        rows_of[iface] = rows_name

    # A `GetTexture`/`GetContainer` out-parameter is an IDirect3DBaseTexture9, a type
    # the runtime has no table for because it never instantiates one. Rather than
    # invent a layout, derive the part three real tables already agree on: slots 0..16
    # of Texture, CubeTexture and VolumeTexture are IUnknown + Resource + BaseTexture
    # and are identical. A call at a slot above that agreed prefix stays unattributed.
    base = {}
    a, b, c = (ifaces["IDirect3DTexture9"], ifaces["IDirect3DCubeTexture9"],
               ifaces["IDirect3DVolumeTexture9"])
    for slot in sorted(a):
        if slot in b and slot in c and a[slot]["name"] == b[slot]["name"] == c[slot]["name"] \
                and a[slot]["argc"] == b[slot]["argc"] == c[slot]["argc"]:
            base[slot] = dict(a[slot])
        else:
            break
    ifaces["IDirect3DBaseTexture9"] = base
    rows_of["IDirect3DBaseTexture9"] = "derived: agreed prefix of ROWS_TEXTURE/CUBETEX/VOLTEX"
    return ifaces, rows_of


# --------------------------------------------------------------------------
# what a creator method hands back, and through which argument
#
# (interface, slot) -> (argument index, produced interface). The argument index
# is zero-based and excludes `this`; the method's own `argc` in the parsed table
# is used as a cross-check that the index is in range.

CREATORS: dict[tuple[str, int], tuple[int, str]] = {
    ("IDirect3D9", 16): (5, "IDirect3DDevice9"),                 # CreateDevice
    ("IDirect3DDevice9", 6): (0, "IDirect3D9"),                  # GetDirect3D
    ("IDirect3DDevice9", 13): (1, "IDirect3DSwapChain9"),        # CreateAdditionalSwapChain
    ("IDirect3DDevice9", 14): (1, "IDirect3DSwapChain9"),        # GetSwapChain
    ("IDirect3DDevice9", 18): (3, "IDirect3DSurface9"),          # GetBackBuffer
    ("IDirect3DDevice9", 23): (6, "IDirect3DTexture9"),          # CreateTexture
    ("IDirect3DDevice9", 24): (7, "IDirect3DVolumeTexture9"),    # CreateVolumeTexture
    ("IDirect3DDevice9", 25): (5, "IDirect3DCubeTexture9"),      # CreateCubeTexture
    ("IDirect3DDevice9", 26): (4, "IDirect3DVertexBuffer9"),     # CreateVertexBuffer
    ("IDirect3DDevice9", 27): (4, "IDirect3DIndexBuffer9"),      # CreateIndexBuffer
    ("IDirect3DDevice9", 28): (6, "IDirect3DSurface9"),          # CreateRenderTarget
    ("IDirect3DDevice9", 29): (6, "IDirect3DSurface9"),          # CreateDepthStencilSurface
    ("IDirect3DDevice9", 36): (4, "IDirect3DSurface9"),          # CreateOffscreenPlainSurface
    ("IDirect3DDevice9", 38): (1, "IDirect3DSurface9"),          # GetRenderTarget
    ("IDirect3DDevice9", 40): (0, "IDirect3DSurface9"),          # GetDepthStencilSurface
    ("IDirect3DDevice9", 59): (1, "IDirect3DStateBlock9"),       # CreateStateBlock
    ("IDirect3DDevice9", 61): (0, "IDirect3DStateBlock9"),       # EndStateBlock
    ("IDirect3DDevice9", 64): (1, "IDirect3DBaseTexture9"),      # GetTexture
    ("IDirect3DDevice9", 86): (1, "IDirect3DVertexDeclaration9"),  # CreateVertexDeclaration
    ("IDirect3DDevice9", 88): (0, "IDirect3DVertexDeclaration9"),  # GetVertexDeclaration
    ("IDirect3DDevice9", 91): (1, "IDirect3DVertexShader9"),     # CreateVertexShader
    ("IDirect3DDevice9", 93): (0, "IDirect3DVertexShader9"),     # GetVertexShader
    ("IDirect3DDevice9", 101): (1, "IDirect3DVertexBuffer9"),    # GetStreamSource
    ("IDirect3DDevice9", 105): (0, "IDirect3DIndexBuffer9"),     # GetIndices
    ("IDirect3DDevice9", 106): (1, "IDirect3DPixelShader9"),     # CreatePixelShader
    ("IDirect3DDevice9", 108): (0, "IDirect3DPixelShader9"),     # GetPixelShader
    ("IDirect3DDevice9", 118): (1, "IDirect3DQuery9"),           # CreateQuery
    ("IDirect3DTexture9", 18): (1, "IDirect3DSurface9"),         # GetSurfaceLevel
    ("IDirect3DCubeTexture9", 18): (2, "IDirect3DSurface9"),     # GetCubeMapSurface
    ("IDirect3DVolumeTexture9", 18): (1, "IDirect3DVolume9"),    # GetVolumeLevel
    ("IDirect3DSwapChain9", 5): (2, "IDirect3DSurface9"),        # GetBackBuffer
}
# `GetDevice` is slot 3 on every resource-derived interface and slot 8 on a swap
# chain, and always hands back the device through argument 0.
for _i in ("IDirect3DTexture9", "IDirect3DCubeTexture9", "IDirect3DVolumeTexture9",
           "IDirect3DSurface9", "IDirect3DVolume9", "IDirect3DVertexBuffer9",
           "IDirect3DIndexBuffer9", "IDirect3DVertexDeclaration9",
           "IDirect3DVertexShader9", "IDirect3DPixelShader9",
           "IDirect3DStateBlock9", "IDirect3DQuery9", "IDirect3DBaseTexture9"):
    CREATORS.setdefault((_i, 3), (0, "IDirect3DDevice9"))
CREATORS.setdefault(("IDirect3DSwapChain9", 8), (0, "IDirect3DDevice9"))

# `d3dx9_42.dll` entry points, __stdcall, with the argument count and the
# zero-based index of the out-parameter that receives a d3d9 interface.
D3DX_SIGS: dict[str, dict] = {
    "D3DXCreateTexture": {"argc": 8, "out": 7, "type": "IDirect3DTexture9"},
    "D3DXCreateTextureFromFileInMemoryEx": {"argc": 15, "out": 14,
                                            "type": "IDirect3DTexture9"},
    "D3DXCreateCubeTexture": {"argc": 7, "out": 6, "type": "IDirect3DCubeTexture9"},
    "D3DXCreateCubeTextureFromFileInMemoryEx": {"argc": 14, "out": 13,
                                                "type": "IDirect3DCubeTexture9"},
    "D3DXCreateVolumeTexture": {"argc": 9, "out": 8, "type": "IDirect3DVolumeTexture9"},
    "D3DXCreateVolumeTextureFromFileInMemoryEx": {"argc": 16, "out": 15,
                                                  "type": "IDirect3DVolumeTexture9"},
    "D3DXFilterTexture": {"argc": 4, "out": None, "type": None},
    "D3DXGetShaderConstantTable": {"argc": 2, "out": 1, "type": "ID3DXConstantTable"},
    "D3DXLoadSurfaceFromSurface": {"argc": 8, "out": None, "type": None},
    "D3DXSaveSurfaceToFileA": {"argc": 5, "out": None, "type": None},
    "D3DXSaveTextureToFileA": {"argc": 4, "out": None, "type": None},
}

# --------------------------------------------------------------------------
# registers

GPR32 = (Register.EAX, Register.ECX, Register.EDX, Register.EBX,
         Register.ESP, Register.EBP, Register.ESI, Register.EDI)
_SUB = {}
for _full, _s16, _s8 in ((Register.EAX, Register.AX, Register.AL),
                         (Register.ECX, Register.CX, Register.CL),
                         (Register.EDX, Register.DX, Register.DL),
                         (Register.EBX, Register.BX, Register.BL),
                         (Register.ESP, Register.SP, None),
                         (Register.EBP, Register.BP, Register.CH),
                         (Register.ESI, Register.SI, Register.DH),
                         (Register.EDI, Register.DI, Register.BH)):
    _SUB[_full] = _full
    _SUB[_s16] = _full
# the 8-bit names above are wrong pairings on purpose-free grounds; do it properly:
_SUB.update({Register.AL: Register.EAX, Register.AH: Register.EAX,
             Register.CL: Register.ECX, Register.CH: Register.ECX,
             Register.DL: Register.EDX, Register.DH: Register.EDX,
             Register.BL: Register.EBX, Register.BH: Register.EBX})
VOLATILE = (Register.EAX, Register.ECX, Register.EDX)


def full32(reg: int) -> int | None:
    return _SUB.get(reg)


_INFO = InstructionInfoFactory()
_WRITES = frozenset((OpAccess.WRITE, OpAccess.COND_WRITE, OpAccess.READ_WRITE,
                     OpAccess.READ_COND_WRITE))
_READS = frozenset((OpAccess.READ, OpAccess.COND_READ, OpAccess.READ_WRITE,
                    OpAccess.READ_COND_WRITE))


def written_gprs(i: Instruction) -> set[int]:
    """Every general register the instruction writes, explicitly or implicitly, as its
    full 32-bit name.

    The operand list alone is not enough: `mul ecx` reads ECX and writes EAX and EDX,
    `imul esi,[esp+18h]` writes ESI and nothing else, `cdq` writes EDX with no operand
    at all, `xchg` writes both of its operands, and `rep stosd` writes EDI and ECX. The
    first version of this scan cleared EAX/EDX for every IMUL, which erased the device
    pointer at 0x866221 and lost IDirect3DDevice9::CreateVertexBuffer with the two
    VertexBuffer9 calls behind it. iced's own instruction info is the table that knows
    every implicit operand, so it is the one consulted.
    """
    out = set()
    for u in _INFO.info(i).used_registers():
        if u.access in _WRITES:
            r = full32(u.register)
            if r is not None:
                out.add(r)
    return out


def sdisp(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value >= 0x80000000 else value


# instructions whose first operand is read, not written
NON_WRITING = {Mnemonic.CMP, Mnemonic.TEST, Mnemonic.PUSH, Mnemonic.JMP,
               Mnemonic.CALL, Mnemonic.RET, Mnemonic.NOP, Mnemonic.INT3,
               Mnemonic.JE, Mnemonic.JNE, Mnemonic.JB, Mnemonic.JBE,
               Mnemonic.JA, Mnemonic.JAE, Mnemonic.JL, Mnemonic.JLE,
               Mnemonic.JG, Mnemonic.JGE, Mnemonic.JS, Mnemonic.JNS}


# --------------------------------------------------------------------------
# the code universe: one anchored decode per analyzer function


def find_cpp_vtables(image: Image) -> dict:
    """C++ vtable constants in the image, and the functions they list.

    A `mov dword [reg],IMM` where `IMM` addresses a run of code pointers in a
    read-only section is MSVC setting an object's primary vptr: `reg` points at
    that class, and every function in the run is a `__thiscall` member whose
    `ECX` at entry is that same pointer. This is the only mechanism that gives a
    C++ object field an owner, and a field with no owner is never typed at all.

    It runs on the plain linear sweep because it has to run *before* function
    extents are fixed: a virtual method the analyzer map missed is still a
    function entry, and folding it into its neighbour's extent is what made
    `IDirect3DVertexShader9::GetFunction` unreachable in the first version of
    this scan.

    A run is every consecutive code-pointer dword from the vtable on (`vtable_run`), so
    a table of code pointers laid out right after the vtable with no gap (another
    class's vtable without RTTI, a callback table) is read as more of the run and its
    functions as members. Layer B is a may-analysis and keeps that reading; R2, which
    must not over-claim membership, cuts each run where the program names an address
    inside it (`_r2_comdat_guard`).
    """
    cands = set()
    for ins in image.instructions:
        if ins.mnemonic != Mnemonic.MOV or ins.op0_kind != OpKind.MEMORY:
            continue
        if ins.memory_base == Register.NONE or ins.memory_index != Register.NONE:
            continue
        if sdisp(ins.memory_displacement) != 0:
            continue
        if ins.op1_kind not in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
            continue
        cands.add(ins.immediate(1) & 0xFFFFFFFF)
    class_vt: set[int] = set()
    entry_class: dict[int, set[int]] = collections.defaultdict(set)
    for va in sorted(cands):
        sec = image.section_of(va)
        if sec is None or sec.executable or not sec.readable or sec.writable:
            continue
        first = image.read_u32(va)
        if first is None or not image.in_code(first):
            continue
        class_vt.add(va)
        for fn in vtable_run(image, va):
            entry_class[fn].add(va)
    return {"class_vt": class_vt, "entry_class": dict(entry_class),
            "members": sorted(entry_class)}


VTABLE_RUN_MAX = 512


def vtable_run(image: Image, vt: int) -> list[int]:
    """The functions a vtable at `vt` lists: the consecutive dwords from `vt` on that
    address code, at most VTABLE_RUN_MAX. The one reading of a run that
    `find_cpp_vtables` and R2's guard share."""
    out: list[int] = []
    while len(out) < VTABLE_RUN_MAX:
        fn = image.read_u32(vt + 4 * len(out))
        if fn is None or not image.in_code(fn):
            break
        out.append(fn)
    return out


class Code:
    """Function extents, anchored instruction lists, call graph helpers."""

    def __init__(self, image: Image, fmap: FunctionMap, extra_entries=()) -> None:
        self.image = image
        self.fmap = fmap
        entries = sorted({f["address"] for f in fmap.functions
                          if image.in_code(f["address"])}
                         | {va for va in extra_entries if image.in_code(va)})
        self.extents: dict[int, tuple[int, int]] = {}
        for k, entry in enumerate(entries):
            sec = image.section_of(entry)
            hi = sec.end
            if k + 1 < len(entries) and entries[k + 1] < hi:
                hi = entries[k + 1]
            self.extents[entry] = (entry, hi)
        self.entries = entries
        self._ins: dict[int, list[Instruction]] = {}
        self._owner: dict[int, int] = {}          # instruction ip -> function entry
        self.cleanup: dict[int, int | None] = {}  # function entry -> `ret imm`
        # functions with at least one `ret`; `cleanup` reads 0 for one with none, which
        # the must-analysis below does not take as a known cleanup
        self.has_ret: set[int] = set()
        # Helpers that write EBP and return (`ret`) without restoring it (no `pop ebp`,
        # no `leave`): `__SEH_prolog4` and `__EH_prolog` set the caller's frame
        # pointer. A compiled function saves and restores EBP, and one with no `ret`
        # (an extent the map cut short, a no-return function) is not a helper either.
        self.sets_ebp: set[int] = set()
        # functions that move ESP by something other than pushes, pops and constant
        # adjustments with no `mov ebp,esp` frame to restore it (`__alloca_probe`,
        # `__SEH_prolog4`): their `ret imm` is not their net stack effect
        self.irregular_esp: set[int] = set()
        self._foreign: list[int] | None = None
        for entry in entries:
            lo, hi = self.extents[entry]
            sec = image.section_of(lo)
            data = image._section_bytes[sec.name]
            dec = Decoder(32, data[lo - sec.va:hi - sec.va], ip=lo)
            ins = list(dec)
            self._ins[entry] = ins
            rets = set()
            writes_ebp = restores_ebp = False
            for i in ins:
                self._owner[i.ip] = entry
                if i.mnemonic == Mnemonic.RET:
                    rets.add(i.immediate(0) if i.op_count else 0)
            # EBP and ESP roles are judged on the code reachable from the entry, not on
            # data inside the extent decoded as instructions (`inc ebp` is byte 45h)
            live = _reachable(image, ins)
            for i in live:
                mn = i.mnemonic
                if mn == Mnemonic.RET:
                    continue
                elif mn == Mnemonic.LEAVE or (mn == Mnemonic.POP
                                              and i.op0_kind == OpKind.REGISTER
                                              and i.op0_register == Register.EBP):
                    restores_ebp = True
                elif i.op_count and i.op0_kind == OpKind.REGISTER \
                        and full32(i.op0_register) == Register.EBP \
                        and mn not in NON_WRITING:
                    writes_ebp = True
                elif mn == Mnemonic.XCHG and i.op_count > 1 \
                        and i.op1_kind == OpKind.REGISTER \
                        and full32(i.op1_register) == Register.EBP:
                    writes_ebp = True
            if rets:
                self.has_ret.add(entry)
            if writes_ebp and not restores_ebp and rets:
                self.sets_ebp.add(entry)
            if _irregular_esp(live):
                self.irregular_esp.add(entry)
            self.cleanup[entry] = rets.pop() if len(rets) == 1 else (0 if not rets else None)

        # thunk targets: a `jmp [iat slot]` stub reached by a direct call
        self.thunk_import: dict[int, tuple[str, str]] = {
            va: image.imports[slot] for va, slot in image.thunks.items()}

        covered = sum(hi - lo for lo, hi in self.extents.values())
        total = sum(s.vsize for s in image.exec_sections)
        self.coverage = {"executable_bytes": total, "bytes_in_function_extents": covered,
                         "functions": len(entries)}

    def instructions(self, entry: int) -> list[Instruction]:
        return self._ins.get(entry, [])

    def owner(self, va: int) -> int | None:
        return self._owner.get(va)

    def foreign_refs_in(self, lo: int, hi: int) -> list[int]:
        """Code addresses in [lo, hi) that something other than the owning function's own
        direct branches can transfer to: every relocated dword's value, every 32-bit
        immediate, and every direct branch target reached from a different function or
        from code outside every extent. A switch table, an SEH scope table, a C++ EH
        continuation and a callback all reach a function's interior this way, so the
        must-analysis seeds each such point with no facts rather than assume the paths
        it models are the only ones. A relocated value that some instruction uses as an
        absolute memory address is data in code (a jump table's own base) and is left
        out; an address that is both would be a hole."""
        if self._foreign is None:
            img = self.image
            out: set[int] = set()
            # an address in code that an instruction reads or writes as memory (a jump
            # table's own base, `jmp [eax*4+table]`) is data, not an entry point; its
            # relocation is that instruction's operand, so it is not counted
            data_in_code: set[int] = set()
            seen: set[int] = set()
            streams = [self._ins[e] for e in self.entries] + [img.instructions]
            for ins in streams:
                for i in ins:
                    if i.ip in seen:
                        continue
                    seen.add(i.ip)
                    for k in range(i.op_count):
                        kind = i.op_kind(k)
                        if kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                            v = i.immediate(k) & 0xFFFFFFFF
                            if img.in_code(v):
                                out.add(v)
                        elif kind == OpKind.NEAR_BRANCH32:
                            t = i.near_branch_target
                            src = self._owner.get(i.ip)
                            if src is None or not (self.extents[src][0] <= t
                                                   < self.extents[src][1]):
                                out.add(t)
                        elif kind == OpKind.MEMORY and i.mnemonic != Mnemonic.LEA \
                                and i.memory_base == Register.NONE:
                            v = i.memory_displacement & 0xFFFFFFFF
                            if img.in_code(v):
                                data_in_code.add(v)
            for r in img.relocated:
                v = img.read_u32(r)
                if v is not None and img.in_code(v) and v not in data_in_code:
                    out.add(v)
            self._foreign = sorted(out)
        a = bisect.bisect_left(self._foreign, lo)
        b = bisect.bisect_left(self._foreign, hi)
        return self._foreign[a:b]


def _reachable(image, ins: list[Instruction]) -> list[Instruction]:
    """The instructions reachable from the first one by fall-through, direct branches
    and switch tables (`jmp [t+r*4]` entries that are instruction starts), in order."""
    if not ins:
        return []
    index = {i.ip: k for k, i in enumerate(ins)}
    seen: set[int] = set()
    work = [0]
    while work:
        k = work.pop()
        while k < len(ins) and k not in seen:
            seen.add(k)
            i = ins[k]
            mn = i.mnemonic
            if i.op0_kind == OpKind.NEAR_BRANCH32 and mn != Mnemonic.CALL:
                t = index.get(i.near_branch_target)
                if t is not None:
                    work.append(t)
                if mn == Mnemonic.JMP:
                    break
            elif mn == Mnemonic.JMP:
                if i.op0_kind == OpKind.MEMORY and i.memory_base == Register.NONE \
                        and i.memory_index != Register.NONE:
                    tbl = i.memory_displacement & 0xFFFFFFFF
                    for n in range(1024):
                        t = index.get(image.read_u32(tbl + 4 * n))
                        if t is None:
                            break
                        work.append(t)
                break
            elif mn in (Mnemonic.RET, Mnemonic.INT3, Mnemonic.UD2, Mnemonic.HLT):
                break
            k += 1
    return [ins[k] for k in sorted(seen)]


def _irregular_esp(ins: list[Instruction]) -> bool:
    """True when a function writes ESP other than by push/pop/call/ret or a constant
    `add`/`sub`, and has no `mov ebp,esp` frame whose epilogue would restore it."""
    frame = False
    odd = False
    for i in ins:
        mn = i.mnemonic
        if mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER \
                and i.op0_register == Register.EBP and i.op1_kind == OpKind.REGISTER \
                and i.op1_register == Register.ESP:
            frame = True
            continue
        if mn in (Mnemonic.PUSH, Mnemonic.POP, Mnemonic.CALL, Mnemonic.RET,
                  Mnemonic.PUSHAD, Mnemonic.POPAD, Mnemonic.PUSHFD, Mnemonic.POPFD):
            if mn == Mnemonic.POP and i.op0_kind == OpKind.REGISTER \
                    and i.op0_register == Register.ESP:
                odd = True
            continue
        if i.op_count and i.op0_kind == OpKind.REGISTER and i.op0_register == Register.ESP:
            if mn in (Mnemonic.ADD, Mnemonic.SUB) \
                    and i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                continue
            if mn == Mnemonic.LEA and i.memory_base == Register.ESP \
                    and i.memory_index == Register.NONE:
                continue      # a constant adjustment; `lea esp,[esp+0]` is MSVC padding
            odd = True
        elif mn == Mnemonic.XCHG and i.op_count > 1 and i.op1_kind == OpKind.REGISTER \
                and i.op1_register == Register.ESP:
            odd = True
        elif mn == Mnemonic.LEAVE:
            odd = True
    return odd and not frame


# --------------------------------------------------------------------------
# Layer A -- the shape census


def layer_a(code: Code) -> dict:
    """Indirect calls whose target came out of a vtable load, grouped by displacement.

    A site qualifies when it is `call [r+D]` with `r` itself loaded from memory in
    the same basic block, or `call r` with `r` loaded by `mov r,[r2+D]` and `r2`
    loaded from memory in the same block *before that load*. No interface is claimed;
    that is Layer B's job, and keeping the two apart is the whole point of the split.

    Each site also records its vtable load: the instruction that put the vtable
    pointer in a register (`vt_load`, its address, base register or None for an
    absolute `[imm32]`, and displacement). The rule-outs read it; the census does not.

    Why `r2` is judged before the load (a correctness fix, not a loosening). The
    first version kept one provenance per register and read `r2`'s at the call, so
    `mov eax,[eax+1Ch]; call eax` passed: by then EAX had been loaded from memory --
    by the very load under test. That self-overwrite admitted two kinds of site that
    the census's own stated shape excludes, and both are removed here:

      * a function-pointer field, where `r2` is a struct/return/argument pointer, not
        a vtable, e.g. `call 7AC4D3h; mov eax,[eax+78h]; call eax` (the base is a
        call's return value). For a COM call `r2` must hold a vtable pointer, and code
        obtains a d3d9 vtable pointer only by reading it out of the object
        (`mov r2,[obj]`); a base that was not loaded from memory is the object itself,
        and its field is a function pointer, not a vtable slot. These are genuinely not
        vtable calls.
      * a call whose vtable register was loaded in an *earlier* basic block, e.g. `mov
        edx,[eax]` before a branch, then `mov edx,[edx+5Ch]; call edx` after it
        (CreateTexture at 0x869776). These are cross-block, which `limits` already
        excludes from the census; the old reading counted them only through the
        self-overwrite. Layer B attributes the ones it can type (CreateTexture is in
        `layer_b_attributed.attributed_sites_not_in_layer_a`); the others leave the
        census and its error bar together, under that declared limit.

    Removing both conforms the census to its stated same-block shape; neither is a
    loosening of a criterion, but the error bar does shrink by them, so the receipt
    lists every site the correction removes or admits, with the provenance of the base
    before the load (`layer_a_shape_census.same_block_correction`). Conversely, an `r2`
    loaded from memory and then overwritten *after* the method-pointer load, which the
    old reading refused, now qualifies.
    """
    sites: dict[int, dict] = {}
    # the sites where the corrected reading and the old one (r2's provenance read at
    # the call) disagree, for the receipt
    removed: dict[int, dict] = {}
    admitted: dict[int, dict] = {}
    for entry in code.entries:
        ins = code.instructions(entry)
        leaders = {entry}
        for i in ins:
            if i.op0_kind == OpKind.NEAR_BRANCH32:
                t = i.near_branch_target
                if ins and ins[0].ip <= t <= ins[-1].ip:
                    leaders.add(t)
        # provenance of a register loaded by a 32-bit `mov reg,[base+disp]`:
        #   ("mem", base_reg_or_None, disp, load_ip, provenance_of_base_before_the_load)
        prov: dict[int, tuple] = {}
        for i in ins:
            if i.ip in leaders:
                prov = {}
            mn = i.mnemonic
            if mn == Mnemonic.CALL:
                tgt = vt_load = None
                if i.op0_kind == OpKind.MEMORY and i.memory_index == Register.NONE:
                    base = full32(i.memory_base)
                    p = prov.get(base) if base is not None else None
                    if p and p[0] == "mem":
                        tgt, vt_load = sdisp(i.memory_displacement), p
                elif i.op0_kind == OpKind.REGISTER:
                    p = prov.get(full32(i.op0_register))
                    if p and p[0] == "mem" and p[1] is not None:
                        new_ok = p[4] is not None and p[4][0] == "mem"
                        old_ok = prov.get(p[1], (None,))[0] == "mem"
                        if new_ok:
                            tgt, vt_load = p[2], p[4]
                        if new_ok != old_ok and p[2] >= 0:
                            (admitted if new_ok else removed)[i.ip] = {
                                "va": i.ip, "function": entry, "disp": p[2],
                                "method_load_va": p[3], "base": p[1]}
                if tgt is not None and tgt >= 0:
                    sites[i.ip] = {"va": i.ip, "disp": tgt, "function": entry,
                                   "text": code.image.text(i),
                                   "return_va": i.ip + i.len,
                                   "vt_load": {"va": vt_load[3], "base": vt_load[1],
                                               "disp": vt_load[2]}}
                for r in VOLATILE:
                    prov.pop(r, None)
                continue
            if mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER:
                d = full32(i.op0_register)
                if d is None:
                    continue
                if i.op1_kind == OpKind.MEMORY and i.memory_index == Register.NONE \
                        and i.op0_register == d:
                    base = full32(i.memory_base) if i.memory_base != Register.NONE else None
                    prov[d] = ("mem", base, sdisp(i.memory_displacement), i.ip,
                               prov.get(base) if base is not None else None)
                elif i.op1_kind == OpKind.REGISTER and i.op0_register == d:
                    src = full32(i.op1_register)
                    if src is not None and src in prov:
                        prov[d] = prov[src]
                    else:
                        prov.pop(d, None)
                else:
                    prov.pop(d, None)
                continue
            for d in written_gprs(i):
                prov.pop(d, None)
    hist = collections.Counter(s["disp"] for s in sites.values())
    return {"sites": sites,
            "vtable_shaped_call_sites": len(sites),
            "by_displacement": {hex(d): n for d, n in sorted(hist.items())},
            "same_block_correction": {"removed": removed, "admitted": admitted}}


def same_block_correction(code: Code, a: dict, attributed: set[int], widest: int) -> dict:
    """The receipt's record of what the base-before-the-load correction changed: each
    site removed from (or admitted to) the census, and what the base register held just
    before the method-pointer load, from the must-analysis. A call return, an argument
    or a live-in register is an object, not a vtable, so the call is through a
    function-pointer field; a field or global load on every path is a vtable loaded in
    an earlier block (cross-block, the declared limit); unknown is a meet of both."""
    corr = a["same_block_correction"]

    def kind(sym):
        if sym is None:
            return "unknown (paths disagree)"
        return {"ret": "call return", "arg": "stack argument", "loc": "stack local",
                "this": "live-in ECX (this)", "in": "live-in register",
                "f": "loaded from a field on every path",
                "g": "loaded from a global on every path",
                "imm": "immediate", "lea": "address of a field",
                "lea-stack": "address of a stack slot"}.get(sym[0], sym[0])

    rows = {"removed": [], "admitted": []}
    traces: dict[int, _MustTrace] = {}
    for which in ("removed", "admitted"):
        for va, r in sorted(corr[which].items()):
            fn = r["function"]
            if fn not in traces:
                traces[fn] = _MustTrace(code, fn, record_at={
                    x["method_load_va"] for x in list(corr["removed"].values())
                    + list(corr["admitted"].values()) if x["function"] == fn}).run()
            t = traces[fn]
            st = t.at.get(r["method_load_va"]) if t.converged else None
            sym = st[0].get(r["base"]) if st else None
            rows[which].append({
                "va": hex(va), "disp": hex(r["disp"]),
                "base_before_the_load": kind(sym),
                "attributed": va in attributed,
                "in_the_ceiling": va not in attributed and r["disp"] <= widest})
    removed = rows["removed"]
    return {
        "means": ("sites the old reading (the vtable register's provenance read at the "
                  "call, after the load had overwritten it) and the corrected one "
                  "(read before the load) disagree on; `removed` left the census, "
                  "`admitted` joined it"),
        "removed": len(removed),
        "removed_that_were_in_the_ceiling": sum(1 for r in removed if r["in_the_ceiling"]),
        "removed_attributed_by_layer_b": sum(1 for r in removed if r["attributed"]),
        "removed_by_base_before_the_load": dict(sorted(collections.Counter(
            r["base_before_the_load"] for r in removed).items())),
        "admitted": len(rows["admitted"]),
        "sites_removed": removed,
        "sites_admitted": rows["admitted"],
    }


# --------------------------------------------------------------------------
# Layer B -- the attributed surface

MAX_ATOMS = 4          # a value that may be more than this many things is no value
MAX_ROUNDS = 40
MAX_FUNC_INS = 40000
MAX_BLOCK_VISITS = 4000  # per function analysis; a function that hits it is reported
# the must-analysis' visit guard is the larger of MAX_BLOCK_VISITS and this many visits
# per block; its in-states only shrink, so it converges far below either
MUST_VISITS_PER_LEADER = 40

# The lattice's top: "more than MAX_ATOMS things", which is as good as "anything". It is
# a distinct value because the empty set is the bottom ("nothing known yet"), and the
# first version used the empty set for both. A set that overflowed became empty, the
# next join refilled it, and it overflowed again (1, 2, 3, 4, 0, 1, ...), so `_grow`
# reported a change on every visit and the work list never emptied: 32 rounds in 720 s
# with about 10,380 functions re-woken every round. TOP absorbs every join, so every
# location changes at most MAX_ATOMS + 1 times and the iteration terminates.
TOP = frozenset({("TOP",)})


class Attribution:
    """Abstract interpretation over COM interface pointers and C++ object pointers.

    Values are *sets* of atoms, joined by union; a union that grows past
    `MAX_ATOMS` becomes `TOP`, which absorbs every later join and names nothing, so a
    site whose receiver is `TOP` is never attributed. The atoms are

      ("I", iface)        a COM interface pointer
      ("C", vtable_va)    a pointer to a C++ object whose primary vptr is `vtable_va`
      ("VT", iface)       the vtable pointer read out of a COM object
      ("M", iface, slot)  a method pointer read out of a COM vtable
      ("A", location)     the *address* of a tracked location, for out-parameters

    Locations are registers and stack slots (flow-sensitive, per function) and
    program globals `("g", va)` and C++ object fields `("f", vtable_va, disp)`
    (flow-insensitive, program-wide, growing monotonically to a fixpoint).

    Seeds: the `Direct3DCreate9` return value; the six `d3dx9_42` creators'
    out-parameters; a store of a C++ vtable constant into `[reg]`, which types
    `reg` as that class; and the `ECX` of any function whose address appears in
    a C++ vtable, which is the `this` of that class under MSVC `__thiscall`.
    Types cross function boundaries through globals, through object fields, and
    through direct calls (argument stack slots and live-in registers).
    """

    def __init__(self, code: Code, ifaces: dict, vtinfo: dict, disp_shift: int = 0,
                 seed_d3d: bool = True, seed_d3dx: bool = True) -> None:
        self.code = code
        self.image = code.image
        self.ifaces = ifaces
        self.vtinfo = vtinfo
        self.disp_shift = disp_shift
        self.seed_d3d = seed_d3d
        self.seed_d3dx = seed_d3dx

        self.globals: dict[int, frozenset] = {}
        self.fields: dict[tuple[int, int], frozenset] = {}
        self.entry_regs: dict[int, dict[int, frozenset]] = {}
        self.entry_stack: dict[int, dict[int, frozenset]] = {}
        self.calls: dict[int, dict] = {}      # call VA -> attribution record
        self.rounds = 0
        self.conflicts = 0
        # functions whose latest analysis stopped at MAX_BLOCK_VISITS or was skipped
        # for size; the global fixpoint is over what those analyses saw
        self.local_limit_hit: set[int] = set()
        self.too_large: set[int] = set()

        self._index()
        self._class_vtables()

    # ---- indexes and seeds ------------------------------------------------
    def _index(self) -> None:
        """Which functions to wake when a global or a field displacement is typed."""
        self.by_global: dict[int, set[int]] = collections.defaultdict(set)
        self.by_field_disp: dict[int, set[int]] = collections.defaultdict(set)
        self.callers: dict[int, set[int]] = collections.defaultdict(set)
        self.seed_functions: set[int] = set()
        d3dx_thunks = {va for va, (dll, nm) in self.code.thunk_import.items()
                       if dll.lower().startswith("d3dx9")}
        d3d9_thunks = {va for va, (dll, nm) in self.code.thunk_import.items()
                       if nm == "Direct3DCreate9"}
        d3dx_slots = {va for va, (dll, nm) in self.image.imports.items()
                      if dll.lower().startswith("d3dx9")}
        d3d9_slots = {va for va, (dll, nm) in self.image.imports.items()
                      if nm == "Direct3DCreate9"}
        for entry in self.code.entries:
            for i in self.code.instructions(entry):
                if i.op0_kind == OpKind.NEAR_BRANCH32 and i.mnemonic == Mnemonic.CALL:
                    t = i.near_branch_target
                    self.callers[t].add(entry)
                    if t in d3dx_thunks or t in d3d9_thunks:
                        self.seed_functions.add(entry)
                d = abs_displacement(i)
                if d is not None:
                    self.by_global[d].add(entry)
                    if d in d3dx_slots or d in d3d9_slots:
                        self.seed_functions.add(entry)
                # EBP is a field base wherever it is not the frame pointer, which only
                # the flow analysis knows; indexing every EBP operand only wakes more
                if i.op_count and i.memory_base != Register.NONE \
                        and i.memory_index == Register.NONE \
                        and i.memory_base != Register.ESP:
                    self.by_field_disp[sdisp(i.memory_displacement)].add(entry)

    def _class_vtables(self) -> None:
        """Adopt the precomputed C++ vtable map, keeping only real function entries."""
        self.class_vt: set[int] = self.vtinfo["class_vt"]
        self.entry_class: dict[int, set[int]] = {
            fn: vts for fn, vts in self.vtinfo["entry_class"].items()
            if fn in self.code.extents}
        for fn in self.entry_class:
            self.seed_functions.add(fn)

        # A constructor writes the base class's vptr and then the derived class's
        # into the same object, through the same register. Both name the same
        # storage, so a field stored between the two writes belongs to both. This
        # records, per (function, base register), every vtable constant the function
        # writes at offset zero, and the store rule below types the register with
        # all of them at once -- which is why `[this+4]` set in the constructor is
        # found again by a virtual method reached through the derived vtable.
        self.ctor_group: dict[tuple[int, int], frozenset] = {}
        for entry in self.code.entries:
            groups: dict[int, set[int]] = collections.defaultdict(set)
            for i in self.code.instructions(entry):
                if i.mnemonic != Mnemonic.MOV or i.op0_kind != OpKind.MEMORY:
                    continue
                # `[ebp]` is a class base too when EBP is a general register; when EBP
                # is the frame pointer the store rule in _step does not use the group
                if i.memory_base in (Register.NONE, Register.ESP) \
                        or i.memory_index != Register.NONE \
                        or sdisp(i.memory_displacement) != 0:
                    continue
                if i.op1_kind not in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                    continue
                imm = i.immediate(1) & 0xFFFFFFFF
                if imm in self.class_vt:
                    b = full32(i.memory_base)
                    if b is not None:
                        groups[b].add(imm)
            for b, vts in groups.items():
                self.ctor_group[(entry, b)] = frozenset(vts)

    # ---- the lattice ------------------------------------------------------
    @staticmethod
    def join(a: frozenset | None, b: frozenset | None) -> frozenset:
        """Least upper bound: empty is bottom, TOP absorbs, an overflowing union is TOP."""
        if not a:
            return b or frozenset()
        if not b:
            return a
        if a == TOP or b == TOP:
            return TOP
        u = a | b
        return TOP if len(u) > MAX_ATOMS else u

    def _grow(self, store: dict, key, value: frozenset) -> bool:
        if not value:
            return False
        old = store.get(key)
        new = self.join(old, value)
        if new != old:
            store[key] = new
            return True
        return False

    # ---- the driver -------------------------------------------------------
    def run(self, deadline: float) -> None:
        """Iterate to a fixpoint, or stop at the deadline or the round cap and say which.

        `stopped_by` is "fixpoint" only when the work list is empty. A deadline inside
        a round keeps the round's unprocessed suffix (and everything already woken) as
        pending work, so a stop can never discard work and then read as a fixpoint;
        `exhausted` is true exactly when work remains.
        """
        work = set(self.seed_functions)
        self.stopped_by = None
        self.deadline_crossings = 0
        while work:
            if time.time() > deadline:
                self.stopped_by = "deadline"
                break
            if self.rounds >= MAX_ROUNDS:
                self.stopped_by = "round_cap"
                break
            self.rounds += 1
            nxt: set[int] = set()
            ordered = sorted(work)
            for k, entry in enumerate(ordered):
                if time.time() > deadline:
                    self.deadline_crossings += 1
                    nxt.update(ordered[k:])
                    break
                touched = self._analyse(entry)
                nxt |= touched
            work = nxt - {None}
        if not work:
            self.stopped_by = "fixpoint"
        self.pending = sorted(work)
        self.exhausted = bool(work)

    def _wake_for(self, kind: str, key) -> set[int]:
        if kind == "g":
            return set(self.by_global.get(key, ()))
        if kind == "f":
            return set(self.by_field_disp.get(key[2], ()))
        return set()

    # ---- per-function analysis -------------------------------------------
    def _analyse(self, entry: int) -> set[int]:
        ins = self.code.instructions(entry)
        if not ins or len(ins) > MAX_FUNC_INS:
            if ins:
                self.too_large.add(entry)
            return set()
        woken: set[int] = set()

        leaders = {ins[0].ip}
        lo, hi = ins[0].ip, ins[-1].ip
        index = {i.ip: k for k, i in enumerate(ins)}
        for k, i in enumerate(ins):
            if i.op0_kind == OpKind.NEAR_BRANCH32 and i.mnemonic != Mnemonic.CALL:
                t = i.near_branch_target
                if lo <= t <= hi and t in index:
                    leaders.add(t)
                if k + 1 < len(ins):
                    leaders.add(ins[k + 1].ip)
            elif i.mnemonic == Mnemonic.RET and k + 1 < len(ins):
                leaders.add(ins[k + 1].ip)
        leader_list = sorted(leaders)
        blocks: list[tuple[int, int]] = []
        for k, start in enumerate(leader_list):
            stop = leader_list[k + 1] if k + 1 < len(leader_list) else hi + 1
            blocks.append((start, stop))

        # entry state
        regs0: dict[int, frozenset] = dict(self.entry_regs.get(entry, {}))
        for vt in self.entry_class.get(entry, ()):
            regs0[Register.ECX] = self.join(regs0.get(Register.ECX),
                                            frozenset({("C", vt)}))
        # incoming arguments live in epoch 0 at their offset from the entry ESP
        stack0 = {(0, off): v for off, v in self.entry_stack.get(entry, {}).items()}

        block_in: dict[int, tuple] = {leader_list[0]: (regs0, stack0, 0, 0)}
        seen: set[int] = set()
        queue = [leader_list[0]]
        guard = 0
        while queue and guard < MAX_BLOCK_VISITS:
            guard += 1
            start = queue.pop(0)
            st = block_in.get(start)
            if st is None:
                continue
            regs, stack, esp, epoch = ({k: v for k, v in st[0].items()},
                                       {k: v for k, v in st[1].items()}, st[2], st[3])
            k = index.get(start)
            if k is None:
                continue
            stop = next((b[1] for b in blocks if b[0] == start), hi + 1)
            fall = True
            branch_targets: list[int] = []
            while k < len(ins) and ins[k].ip < stop:
                i = ins[k]
                regs, stack, esp, epoch, flow = self._step(
                    entry, i, regs, stack, esp, epoch, woken)
                if flow == "end":
                    fall = False
                    break
                if i.op0_kind == OpKind.NEAR_BRANCH32 and i.mnemonic != Mnemonic.CALL:
                    t = i.near_branch_target
                    if t in index:
                        branch_targets.append(t)
                    if i.mnemonic == Mnemonic.JMP:
                        fall = False
                        break
                k += 1
            succs = list(branch_targets)
            if fall and k < len(ins) and ins[k].ip >= stop:
                succs.append(ins[k].ip)
            elif fall and k < len(ins):
                pass
            for s in succs:
                cur = block_in.get(s)
                new_regs = {r: v for r, v in regs.items()
                            if v or not isinstance(r, int)}
                new_stack = {o: v for o, v in stack.items() if v}
                if cur is None:
                    block_in[s] = (new_regs, new_stack, esp, epoch)
                    queue.append(s)
                else:
                    cr, cs, ce, cep = cur
                    merged_r = {}
                    for r in set(cr) | set(new_regs):
                        if r == "__fp__":
                            # EBP's role: absent means a general register, a tuple
                            # means the frame pointer at that (epoch, offset). Paths
                            # that disagree leave EBP-based operands unknown ("?"),
                            # never a general register on one path's say-so.
                            a, b = cr.get(r), new_regs.get(r)
                            if a == b:
                                if a is not None:
                                    merged_r[r] = a
                            else:
                                merged_r[r] = "?"
                            continue
                        if not isinstance(r, int):      # ("o", reg), a load site
                            if cr.get(r) == new_regs.get(r) and cr.get(r) is not None:
                                merged_r[r] = cr[r]
                            continue
                        j = self.join(cr.get(r), new_regs.get(r))
                        if j:
                            merged_r[r] = j
                    merged_s = {}
                    if ce == esp and cep == epoch:
                        for o in set(cs) | set(new_stack):
                            j = self.join(cs.get(o), new_stack.get(o))
                            if j:
                                merged_s[o] = j
                        ne, nep = ce, cep
                    else:
                        # Paths disagree on the stack, so ESP is unknown here and the
                        # merged stack is empty. Once that is so (ce is None) the block
                        # keeps the epoch it already has. The first version took
                        # max(cep, epoch) on every merge, and a loop around a call with
                        # an unknown stack cleanup bumps the epoch on each back edge, so
                        # the block's state changed on every visit and 501 functions
                        # never converged (0x401040, 35 instructions, went through 1335
                        # epochs); only MAX_BLOCK_VISITS stopped them. Keeping the epoch
                        # lets an epoch number recur on a later iteration, which is why
                        # `_new_epoch` drops every register atom that names a slot of the
                        # epoch it starts or a later one.
                        ne, nep = None, (cep if ce is None else max(cep, epoch))
                    if merged_r != cr or merged_s != cs or ne != ce or nep != cep:
                        block_in[s] = (merged_r, merged_s, ne, nep)
                        if s not in seen or True:
                            queue.append(s)
                seen.add(s)
        if queue:
            self.local_limit_hit.add(entry)
        else:
            self.local_limit_hit.discard(entry)
        return woken

    # ---- one instruction --------------------------------------------------
    def _loc(self, i: Instruction, regs: dict, esp: int | None, epoch: int,
             opidx: int = None):
        """The abstract location an instruction's memory operand names, or None."""
        if i.memory_index != Register.NONE:
            return None
        base = i.memory_base
        disp = sdisp(i.memory_displacement)
        if base == Register.NONE:
            return ("g", disp & 0xFFFFFFFF)
        if base == Register.ESP:
            return None if esp is None else ("s", epoch, esp + disp)
        b = full32(base)
        if b is None:
            return None
        if b == Register.EBP:
            # EBP is the frame pointer only on paths where `mov ebp,esp` set it and
            # nothing has written EBP since ("__fp__" = (epoch, offset)); everywhere
            # else it is a general register like any other. The first version took
            # every EBP operand for a frame slot, so `mov ebp,ecx` followed by
            # `[ebp+38h]` at 0x869720 never reached the device field and
            # IDirect3DDevice9::CreateTexture went unattributed. After a call to a
            # helper that sets EBP itself (`__SEH_prolog4`), `_call` sets "?".
            # A frame slot is keyed by the epoch the frame was set in. A new epoch
            # (a call with an unknown stack cleanup) clears every stack slot, frame
            # slots included, so nothing written before it survives. Slots written
            # after it through EBP and through ESP are keyed apart even when they
            # name the same memory: a value stored through one and read through the
            # other is missed, and an earlier value at the other key is read instead.
            # That is a stated imprecision of this may-analysis: a missed value leaves
            # a site unattributed and in the ceiling; a stale one could type a site
            # wrongly, which the receiver-conflict count and the anchors would show.
            fp = regs.get("__fp__")
            if fp is not None:
                if fp == "?" or fp[1] is None:
                    return None
                return ("s", fp[0], fp[1] + disp)
        t = regs.get(b) or frozenset()
        out = []
        for atom in t:
            if atom[0] == "C":
                out.append(("f", atom[1], disp))
            elif atom[0] == "O":
                out.append(("f", atom, disp))
            elif atom[0] == "I" and disp == 0:
                out.append(("vt", atom[1]))
        # An object whose class this tool never identified still has an identity:
        # the instruction that loaded the pointer. Fields under that identity are
        # only ever populated by a store of an already-typed value, so the
        # namespace stays sparse, and two different load sites are never merged --
        # which is the difference between this and keying a field on its offset
        # alone, where `[x+0Ch]` holding a texture in one class and a vertex
        # shader in another would be the same slot.
        site = regs.get(("o", b))
        if site is not None:
            out.append(("f", ("O", site), disp))
        return out or None

    def _read(self, loc, stack: dict) -> frozenset:
        if loc is None:
            return frozenset()
        locs = loc if isinstance(loc, list) else [loc]
        acc = frozenset()
        for l in locs:
            if l[0] == "g":
                acc = self.join(acc, self.globals.get(l[1]))
            elif l[0] == "f":
                acc = self.join(acc, self.fields.get((l[1], l[2])))
            elif l[0] == "s":
                acc = self.join(acc, stack.get((l[1], l[2])))
            elif l[0] == "vt":
                acc = self.join(acc, frozenset({("VT", l[1])}))
        return acc

    def _write(self, loc, value: frozenset, stack: dict, woken: set,
               strong: bool = False) -> None:
        """Store `value` at `loc`. Globals and fields are flow-insensitive and only grow.
        A stack slot is flow-sensitive: a `strong` store to one definite slot replaces
        what it held (an unknown value clears it); any other stack store joins."""
        if loc is None:
            return
        locs = loc if isinstance(loc, list) else [loc]
        if strong and len(locs) == 1 and locs[0][0] == "s":
            key = (locs[0][1], locs[0][2])
            if value:
                stack[key] = value
            else:
                stack.pop(key, None)
            return
        if not value:
            return
        for l in locs:
            if l[0] == "g":
                if self._grow(self.globals, l[1], value):
                    woken |= self._wake_for("g", l[1])
            elif l[0] == "f":
                if self._grow(self.fields, (l[1], l[2]), value):
                    woken |= self._wake_for("f", l)
            elif l[0] == "s":
                stack[(l[1], l[2])] = self.join(stack.get((l[1], l[2])), value)

    def _addr_of(self, loc) -> frozenset:
        if loc is None:
            return frozenset()
        locs = loc if isinstance(loc, list) else [loc]
        return frozenset(("A", l) for l in locs if l[0] in ("g", "f", "s"))

    def _step(self, entry: int, i: Instruction, regs: dict, stack: dict,
              esp: int | None, epoch: int, woken: set):
        mn = i.mnemonic

        if mn == Mnemonic.PUSH:
            if esp is not None:
                esp -= 4
            val = frozenset()
            if i.op0_kind == OpKind.REGISTER:
                val = regs.get(full32(i.op0_register)) or frozenset()
            elif i.op0_kind == OpKind.MEMORY:
                val = self._read(self._loc(i, regs, esp + 4 if esp is not None else None,
                                           epoch), stack)
            elif i.op0_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                val = self._imm_addr(i.immediate(0) & 0xFFFFFFFF)
            if esp is not None:
                stack[(epoch, esp)] = val
            return regs, stack, esp, epoch, None

        if mn == Mnemonic.POP:
            val = stack.get((epoch, esp)) if esp is not None else None
            if i.op0_kind == OpKind.REGISTER:
                r = full32(i.op0_register)
                if r is not None:
                    if val:
                        regs[r] = val
                    else:
                        regs.pop(r, None)
                    regs[("o", r)] = i.ip
                    if r == Register.EBP:
                        regs.pop("__fp__", None)
            if esp is not None:
                esp += 4
            return regs, stack, esp, epoch, None

        if mn in (Mnemonic.SUB, Mnemonic.ADD) and i.op0_kind == OpKind.REGISTER \
                and full32(i.op0_register) == Register.ESP:
            if i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) and esp is not None:
                d = sdisp(i.immediate(1))
                esp += -d if mn == Mnemonic.SUB else d
            else:
                esp = None
            return regs, stack, esp, epoch, None

        if mn == Mnemonic.AND and i.op0_kind == OpKind.REGISTER \
                and full32(i.op0_register) == Register.ESP:
            # `and esp,-8` shifts every later [esp+k] by the same run-time constant,
            # so slot identity inside the frame is preserved by treating it as a no-op.
            return regs, stack, esp, epoch, None

        if mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER \
                and i.op0_register == Register.EBP and i.op1_register == Register.ESP:
            regs.pop(Register.EBP, None)
            regs.pop(("o", Register.EBP), None)
            regs["__fp__"] = (epoch, esp)
            return regs, stack, esp, epoch, None

        if mn == Mnemonic.LEAVE:
            # mov esp,ebp; pop ebp
            fp = regs.get("__fp__")
            if isinstance(fp, tuple) and fp[1] is not None:
                epoch, esp = fp
            else:
                esp = None
            val = stack.get((epoch, esp)) if esp is not None else None
            self._kill(regs, Register.EBP)
            if val:
                regs[Register.EBP] = val
            if esp is not None:
                esp += 4
            return regs, stack, esp, epoch, None

        if i.op0_kind == OpKind.REGISTER and i.op0_register == Register.ESP \
                and mn in (Mnemonic.MOV, Mnemonic.LEA):
            # `mov esp,ebp` and `lea esp,[ebp-N]` restore the frame; anything else
            # leaves ESP unknown. ESP is never given a type.
            src = Register.NONE
            disp = 0
            if mn == Mnemonic.MOV and i.op1_kind == OpKind.REGISTER:
                src = i.op1_register
            elif mn == Mnemonic.LEA and i.memory_index == Register.NONE:
                src, disp = i.memory_base, sdisp(i.memory_displacement)
            fp = regs.get("__fp__")
            if src == Register.EBP and isinstance(fp, tuple) and fp[1] is not None:
                epoch, esp = fp[0], fp[1] + disp
            elif src == Register.ESP and mn == Mnemonic.LEA and esp is not None:
                esp += disp
            else:
                esp = None
            return regs, stack, esp, epoch, None

        if mn == Mnemonic.LEA and i.op0_kind == OpKind.REGISTER:
            r = full32(i.op0_register)
            loc = self._loc(i, regs, esp, epoch)
            val = self._addr_of(loc)
            if r is not None:
                if r == Register.EBP:
                    regs.pop("__fp__", None)
                if val:
                    regs[r] = val
                else:
                    regs.pop(r, None)
                regs.pop(("o", r), None)
            return regs, stack, esp, epoch, None

        if mn == Mnemonic.MOV:
            if i.op0_kind == OpKind.REGISTER:
                r = full32(i.op0_register)
                if r is None:
                    return regs, stack, esp, epoch, None
                if r == Register.EBP:
                    regs.pop("__fp__", None)
                if i.op0_register != r:
                    # an 8- or 16-bit write leaves no pointer in the register
                    self._kill(regs, r)
                    return regs, stack, esp, epoch, None
                if i.op1_kind == OpKind.REGISTER:
                    s = full32(i.op1_register)
                    src = regs.get(s)
                    if src:
                        regs[r] = src
                    else:
                        regs.pop(r, None)
                    if regs.get(("o", s)) is not None:
                        regs[("o", r)] = regs[("o", s)]
                    else:
                        regs.pop(("o", r), None)
                elif i.op1_kind == OpKind.MEMORY:
                    loc = self._loc(i, regs, esp, epoch)
                    val = self._read(loc, stack)
                    # a vtable pointer load also yields the method pointers below it
                    base = full32(i.memory_base) if i.memory_base != Register.NONE else None
                    if base is not None:
                        bt = regs.get(base) or frozenset()
                        extra = []
                        for atom in bt:
                            if atom[0] == "VT":
                                extra.append(("M", atom[1],
                                              (sdisp(i.memory_displacement)
                                               + self.disp_shift) // 4))
                        if extra:
                            val = self.join(val, frozenset(extra))
                    if val:
                        regs[r] = val
                    else:
                        regs.pop(r, None)
                    regs[("o", r)] = i.ip
                elif i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                    val = self._imm_addr(i.immediate(1) & 0xFFFFFFFF)
                    if val:
                        regs[r] = val
                    else:
                        regs.pop(r, None)
                    regs.pop(("o", r), None)
                else:
                    regs.pop(r, None)
                    regs.pop(("o", r), None)
                return regs, stack, esp, epoch, None
            if i.op0_kind == OpKind.MEMORY:
                # A C++ vtable constant stored into [reg] makes reg that class: a
                # strong update, replacing whatever reg was typed with before. After
                # the store the object's vptr *is* that constant, whatever the
                # callers passed, so the earlier atoms describe nothing the code can
                # still observe through this register. The first version joined, and
                # in the base destructor 0x865ba0 three caller classes plus the
                # two-member constructor group overflowed MAX_ATOMS and erased the
                # type, losing IDirect3D9::Release at 0x865bd8.
                if i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) \
                        and i.memory_base not in (Register.NONE, Register.ESP) \
                        and i.memory_index == Register.NONE \
                        and sdisp(i.memory_displacement) == 0:
                    imm = i.immediate(1) & 0xFFFFFFFF
                    b = full32(i.memory_base)
                    if imm in self.class_vt and b is not None \
                            and not (b == Register.EBP and "__fp__" in regs):
                        group = self.ctor_group.get((entry, b), frozenset({imm}))
                        regs[b] = frozenset(("C", v) for v in group)
                loc = self._loc(i, regs, esp, epoch)
                val = frozenset()
                if i.op1_kind == OpKind.REGISTER:
                    if full32(i.op1_register) == i.op1_register:
                        val = regs.get(i.op1_register) or frozenset()
                elif i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                    val = self._imm_addr(i.immediate(1) & 0xFFFFFFFF)
                self._write(loc, val, stack, woken, strong=True)
                return regs, stack, esp, epoch, None

        if mn == Mnemonic.CALL:
            return self._call(entry, i, regs, stack, esp, epoch, woken)

        if mn == Mnemonic.RET:
            return regs, stack, esp, epoch, "end"

        # Anything else: every register it writes, explicitly or implicitly, stops
        # holding what it held (written_gprs: `mul`/`div` write EAX and EDX and not
        # their operand, two- and three-operand `imul` only its destination, `cdq`
        # EDX, `xchg` both operands), and a definite stack slot it writes is cleared.
        written = written_gprs(i)
        for r in written:
            if r != Register.ESP:
                self._kill(regs, r)
        if Register.ESP in written:
            esp = None
        if esp is not None or "__fp__" in regs:
            for k in range(i.op_count):
                if i.op_kind(k) == OpKind.MEMORY:
                    if any(u.access in _WRITES for u in _INFO.info(i).used_memory()):
                        loc = self._loc(i, regs, esp, epoch)
                        if isinstance(loc, tuple) and loc[0] == "s":
                            stack.pop((loc[1], loc[2]), None)
                    break
        return regs, stack, esp, epoch, None

    @staticmethod
    def _kill(regs: dict, r: int) -> None:
        regs.pop(r, None)
        regs.pop(("o", r), None)
        if r == Register.EBP:
            regs.pop("__fp__", None)

    def _imm_addr(self, imm: int) -> frozenset:
        """An immediate that addresses a writable data dword is the address of a global.

        This exists only so `push offset g_slot` reaches an out-parameter rule. It
        never produces an interface type on its own.
        """
        sec = self.image.section_of(imm)
        if sec is None or sec.executable or not sec.writable:
            return frozenset()
        return frozenset({("A", ("g", imm))})

    def _clobber(self, regs: dict) -> None:
        for r in VOLATILE:
            regs.pop(r, None)
            regs.pop(("o", r), None)

    @staticmethod
    def _new_epoch(regs: dict, stack: dict, epoch: int) -> tuple[int, int]:
        """Start a new ESP epoch after a call whose stack cleanup is unknown.

        ESP is re-anchored at 0 in epoch `epoch + 1` and every stack slot is dropped.
        Because a merge that already lost ESP keeps its epoch (see `_analyse`), the
        epoch started here can recur on the next iteration of a loop. A register may
        still hold the address of a slot from the previous iteration, ("A", ("s", e,
        off)) with e at least the new epoch, and that address names a different
        incarnation of the frame, so it is dropped; so is an EBP frame keyed by such
        an epoch. Without this a stale out-parameter address could type a slot of the
        new incarnation."""
        new = epoch + 1
        for r in [k for k in regs if isinstance(k, int)]:
            v = regs[r]
            if v == TOP:
                continue
            keep = frozenset(a for a in v
                             if not (a[0] == "A" and a[1][0] == "s" and a[1][1] >= new))
            if keep != v:
                if keep:
                    regs[r] = keep
                else:
                    regs.pop(r, None)
        fp = regs.get("__fp__")
        if isinstance(fp, tuple) and fp[0] >= new:
            regs["__fp__"] = "?"
        stack.clear()
        return 0, new

    def _call(self, entry: int, i: Instruction, regs: dict, stack: dict,
              esp: int | None, epoch: int, woken: set):
        cleanup: int | None = None

        if i.op0_kind == OpKind.NEAR_BRANCH32:
            tgt = i.near_branch_target
            imp = self.code.thunk_import.get(tgt)
            if imp is None and tgt in self.image.imports:
                imp = self.image.imports[tgt]
            if imp is not None:
                dll, name = imp
                if name == "Direct3DCreate9":
                    cleanup = 4
                    self._clobber(regs)
                    if self.seed_d3d:
                        regs[Register.EAX] = frozenset({("I", "IDirect3D9")})
                elif name in D3DX_SIGS:
                    sig = D3DX_SIGS[name]
                    cleanup = 4 * sig["argc"]
                    if self.seed_d3dx and sig["out"] is not None and esp is not None:
                        slot = stack.get((epoch, esp + 4 * sig["out"])) or frozenset()
                        targets = [atom[1] for atom in slot if atom[0] == "A"]
                        for t in targets:
                            self._write(t, frozenset({("I", sig["type"])}), stack, woken,
                                        strong=len(targets) == 1)
                    self._clobber(regs)
                else:
                    self._clobber(regs)
            else:
                # a direct call to an in-image function: propagate into it
                if tgt in self.code.extents:
                    # a callee that moves ESP irregularly (`__alloca_probe`) or sets EBP
                    # (`__SEH_prolog4`) does not leave ESP at its `ret imm`: unknown
                    cleanup = (self.code.cleanup.get(tgt)
                               if tgt not in self.code.irregular_esp
                               and tgt not in self.code.sets_ebp else None)
                    changed = False
                    callee_regs = self.entry_regs.setdefault(tgt, {})
                    for r in GPR32:
                        if r == Register.ESP:
                            continue
                        v = regs.get(r) or frozenset()
                        site = regs.get(("o", r))
                        if site is not None:
                            # the object identity travels with the pointer, so a
                            # field the caller set is the field the callee reads
                            v = self.join(v, frozenset({("O", site)}))
                        if self._grow(callee_regs, r, v):
                            changed = True
                    if esp is not None:
                        callee = self.entry_stack.setdefault(tgt, {})
                        for k in range(0, 24):
                            v = stack.get((epoch, esp + 4 * k))
                            if v and self._grow(callee, 4 + 4 * k, v):
                                changed = True
                    if changed:
                        woken.add(tgt)
                    if tgt in self.code.sets_ebp:
                        # a helper that leaves EBP set (`__SEH_prolog4` makes it the
                        # caller's frame pointer at an offset this scan does not model):
                        # EBP-based operands are unknown from here, never object fields
                        self._kill(regs, Register.EBP)
                        regs["__fp__"] = "?"
                self._clobber(regs)
            if esp is not None:
                esp = esp + cleanup if cleanup is not None else None
            if esp is None:
                esp, epoch = self._new_epoch(regs, stack, epoch)
            return regs, stack, esp, epoch, None

        # indirect: is the receiver typed?
        cands: list[tuple[str, int]] = []
        if i.op0_kind == OpKind.MEMORY and i.memory_index == Register.NONE \
                and i.memory_base != Register.NONE:
            b = full32(i.memory_base)
            bt = regs.get(b) or frozenset()
            slot = (sdisp(i.memory_displacement) + self.disp_shift) // 4
            for atom in bt:
                if atom[0] == "VT":
                    cands.append((atom[1], slot))
        elif i.op0_kind == OpKind.REGISTER:
            rt = regs.get(full32(i.op0_register)) or frozenset()
            for atom in rt:
                if atom[0] == "M":
                    cands.append((atom[1], atom[2]))

        argc = None
        if cands:
            names = set()
            for iface, slot in cands:
                row = self.ifaces.get(iface, {}).get(slot)
                names.add((row["name"], row["argc"]) if row else None)
            if len(names) == 1 and None not in names:
                name, argc = names.pop()
                rec = self.calls.get(i.ip)
                ifs = sorted({c[0] for c in cands})
                slot = cands[0][1]
                if rec is None:
                    self.calls[i.ip] = {"va": i.ip, "return_va": i.ip + i.len,
                                        "interfaces": ifs, "slot": slot,
                                        "method": name, "function": entry}
                else:
                    rec["interfaces"] = sorted(set(rec["interfaces"]) | set(ifs))
                # a creator hands a typed pointer back through an out-parameter
                for iface, s in cands:
                    spec = CREATORS.get((iface, s))
                    if spec and esp is not None:
                        idx, produced = spec
                        cell = stack.get((epoch, esp + 4 * (1 + idx))) or frozenset()
                        targets = [atom[1] for atom in cell if atom[0] == "A"]
                        for t in targets:
                            self._write(t, frozenset({("I", produced)}), stack, woken,
                                        strong=len(targets) == 1)
            else:
                self.conflicts += 1

        self._clobber(regs)
        if argc is not None and esp is not None:
            esp += 4 * (argc + 1)
        else:
            esp, epoch = self._new_epoch(regs, stack, epoch)
        return regs, stack, esp, epoch, None


# --------------------------------------------------------------------------
# Sound rule-outs for the Layer A error bar
#
# The ceiling is "unattributed vtable-shaped sites that could still be a d3d9 call":
# the honest upper bound on how many d3d9 methods the attributing pass may be missing.
# A rule-out lowers it by *proving* a site is not a d3d9 COM vtable call -- never by
# guessing it probably is not. None of the rules is heuristic. Each one's soundness
# argument, the assumption it rests on and its known holes are written at its
# implementation below and copied into the receipt, with measured evidence for the
# holes where the image gives some.
#
# The facts the rules read come from a per-function forward MUST analysis
# (`_MustTrace`, first ported from the read-only scoping unit's classify.py). "Must"
# means a value is recorded only when every path agrees on it; paths that disagree meet
# to unknown, so a fact the rules act on holds on every path the analysis models. What
# it models, and what it does not, is in its docstring.

# The assumption R1 and R3 share. It is the receipt's `assumption` field.
OBJECT_BOUNDS = (
    "Object bounds: an access through an address the program materializes for an "
    "image data object (a 32-bit immediate, a LEA displacement, the displacement of a "
    "based or indexed operand, or a relocated data dword) stays inside the object that "
    "starts there, and the objects of a section are delimited by the addresses so "
    "materialized. A memory-unsafe access is outside what any rule can see.")


def rtti_type_name(image: Image, vt: int) -> str | None:
    """The RTTI type name of a C++ vtable, or None when there is no valid COL at vt-4.

    MSVC puts a pointer to the Complete Object Locator in the dword just below the
    vtable. A COL whose signature dword is 0 carries, at offset 12, a pointer to the
    Type Descriptor, whose decorated name begins at offset 8 and starts `.?A` (`.?AV`
    class, `.?AU` struct). A vtable with a valid COL is a real RTTI-emitted C++ class
    of this image; d3d9 interface vtables live in d3d9.dll and have no COL in this
    image. Any other string at TD+8 is not a type name, so it is refused.
    """
    col = image.read_u32(vt - 4)
    if col is None:
        return None
    sec = image.section_of(col)
    if sec is None or sec.executable or not sec.readable:
        return None
    if image.read_u32(col) != 0:               # COL signature: 0 for 32-bit images
        return None
    td = image.read_u32(col + 12)
    if td is None:
        return None
    name = image.cstring(td + 8, 256)
    return name if name is not None and name.startswith(".?A") else None


_ENTRY = object()       # a stack slot not written since entry: it holds the entry value
_WIPED = "*"            # stack key: every slot not listed is unknown, not the entry value


def _const_like(v) -> bool:
    """A value whose escape the rules track: a constant address (`imm`, or a LEA of
    one), or the receiver `this`."""
    return isinstance(v, tuple) and bool(v) and (
        v[0] == "this" or v[0] == "imm"
        or (v[0] == "lea" and isinstance(v[1], tuple) and bool(v[1]) and v[1][0] == "imm"))


def _const_addr(v) -> int | None:
    """The address a constant-like value names, or None (`this`, or not constant)."""
    if not isinstance(v, tuple) or not v:
        return None
    if v[0] == "imm":
        return v[1] & 0xFFFFFFFF
    if v[0] == "lea" and isinstance(v[1], tuple) and v[1] and v[1][0] == "imm":
        return (v[1][1] + v[2]) & 0xFFFFFFFF
    return None


class _MustTrace:
    """A forward must-analysis of one function's integer registers and stack.

    Symbolic values:
      ("this",)            ECX at entry (MSVC __thiscall receiver), while unchanged
      ("in", reg)          another register's entry value
      ("g", addr)          the dword at an absolute global address
      ("imm", value)       an immediate (a static object address when it addresses data)
      ("ret", target, ip)  a call's return value
      ("arg", off)/("loc", off)  a stack slot above/below the return address, unwritten
      ("f", base, disp)    a field read `[base+disp]` of a symbolic base
      ("lea", base, disp)  the address of such a field; ("lea-stack", off); ("fp",)
    `at[ip]` is the state before each instruction, at the fixpoint.

    What makes a fact hold on every path. Each point was a hole in the first version:
      * Every block is re-analysed until its in-state stops changing. The first version
        skipped a block after 60 visits, which can leave a state that is too
        optimistic. Now the only stop is the visit guard, and `converged` says whether
        it was hit. A rule never reads a trace that did not converge.
      * A switch `jmp [table+reg*4]` has the table's entries as successors. Any other
        interior point that code can reach by an edge this trace does not model is
        seeded with no facts at all: a point named by a relocated dword, an immediate
        or a direct branch from another function (`Code.foreign_refs_in`). That covers
        SEH and C++ EH continuations, unresolved switches and callbacks.
      * A stack slot is written by any instruction that writes memory there, of any
        width (iced's used-memory list), not only by `mov`. Once the function has
        materialized an address in its own frame (`lea r,[esp+k]`, `mov r,esp`, ...),
        every write through a non-stack base and every call clear every slot. A slot
        written with an unknown value reads as unknown, never as its entry value, and
        so does every slot after a clear.
      * A direct call moves ESP by the callee's `ret imm` only when the callee has a
        `ret` and moves ESP regularly (`Code.irregular_esp`). Any other call leaves ESP
        unknown. The first version kept ESP unchanged whenever an `add esp,N`
        followed, which is wrong after a callee-cleanup call. `and esp,-N` leaves ESP
        unknown; EBP-frame slots stay exact.
    Holes, stated:
      * a mid-block entry from a computed jump that is not a resolved switch and is not
        named by a relocation, an immediate or a foreign branch (an arithmetic target);
      * a callee whose net stack effect is not its `ret imm` though it moves ESP only
        by pushes, pops and constant adjustments;
      * an image without relocations, whose data tables then name no interior points.

    For the rules it also records three things. For every constant-like value
    (`_const_like`), `events[value]` holds each (ip, kind) where the value leaves what
    this trace follows: in a register or an argument slot at a call or a tail jump, a
    store to non-stack memory, arithmetic on it (an instruction that reads it from a
    register and writes any register or memory, a LEA from `this`), a merge with a
    different value, a return. A data address combined with a value this trace cannot
    place leaves it too, recorded under ("imm", address): a 32-bit immediate operand
    of such arithmetic ("arith", `add eax,offset obj`), and the displacement of a based
    or indexed memory write or LEA whose base or index is not a constant address
    ("untraced base", `mov [ebx+obj],eax`, `lea eax,[obj+ecx*4]`). `writes` holds every
    memory write through a constant-like base, and `blind_writes` (ip, address) every
    memory write of the second kind, which under OBJECT_BOUNDS stays in the object
    that starts at that address.
    """

    def __init__(self, code: Code, entry: int, record_at=True) -> None:
        self.code = code
        self.entry = entry
        # True records `at` for every instruction, None for none, a set for those ips
        self.record_at = record_at
        self.ins = code.instructions(entry)
        self.index = {i.ip: k for k, i in enumerate(self.ins)}
        self.at: dict[int, tuple] = {}
        self.converged = False
        self.switch_targets: dict[int, list[int]] = {}
        self.unmodelled: list[int] = []
        self.events: dict[tuple, set] = collections.defaultdict(set)
        self.writes: set[tuple] = set()  # (ip, base value, disp, size, unbounded, exact)
        self.blind_writes: set[tuple] = set()  # (ip, data address in the displacement)
        self.frame_escapes = self._frame_escapes()

    def _data_addr(self, disp: int) -> int | None:
        """`disp` as a 32-bit address when it lies in a data (non-executable) section."""
        x = disp & 0xFFFFFFFF
        sec = self.code.image.section_of(x)
        return x if sec is not None and not sec.executable else None

    def _untraced(self, i, x) -> None:
        """A data address in the displacement of a memory operand whose base or index
        this trace cannot place: the access may land anywhere in the object at x."""
        if x is not None:
            self.events[("imm", x)].add((i.ip, "untraced base"))

    # ---- the function's shape -------------------------------------------
    def _frame_escapes(self) -> bool:
        """Whether the function ever materializes an address in its own stack frame."""
        frame = any(_is_mov_ebp_esp(i) for i in self.ins)
        seen_frame = False
        for i in self.ins:
            mn = i.mnemonic
            if _is_mov_ebp_esp(i):
                seen_frame = True
                continue
            if mn == Mnemonic.LEA:
                if i.memory_base == Register.ESP or (frame and i.memory_base == Register.EBP):
                    return True
                continue
            if mn == Mnemonic.PUSH and i.op0_kind == OpKind.REGISTER:
                r = full32(i.op0_register)
                if r == Register.ESP or (r == Register.EBP and seen_frame):
                    return True
                continue
            for k in range(1, i.op_count):
                if i.op_kind(k) != OpKind.REGISTER:
                    continue
                r = full32(i.op_register(k))
                if r == Register.ESP:
                    return True
                if r == Register.EBP and frame and not (
                        mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER
                        and i.op0_register == Register.ESP):
                    return True
        return False

    def _table(self, j: Instruction) -> list[int]:
        tbl = j.memory_displacement & 0xFFFFFFFF
        out = []
        for k in range(1024):
            t = self.code.image.read_u32(tbl + 4 * k)
            if t is None or t not in self.index:
                break
            out.append(t)
        return out

    def _leaders(self) -> list[int]:
        ins = self.ins
        leaders = {ins[0].ip}
        for k, i in enumerate(ins):
            if i.op0_kind == OpKind.NEAR_BRANCH32 and i.mnemonic != Mnemonic.CALL:
                if i.near_branch_target in self.index:
                    leaders.add(i.near_branch_target)
                if k + 1 < len(ins):
                    leaders.add(ins[k + 1].ip)
            elif i.mnemonic in (Mnemonic.RET, Mnemonic.JMP) and k + 1 < len(ins):
                leaders.add(ins[k + 1].ip)
            if i.mnemonic == Mnemonic.JMP and i.op0_kind == OpKind.MEMORY \
                    and i.memory_base == Register.NONE \
                    and i.memory_index != Register.NONE and i.memory_index_scale == 4:
                t = self._table(i)
                if t:
                    self.switch_targets[i.ip] = t
                    leaders.update(t)
        lo, hi = self.code.extents.get(self.entry, (self.entry, self.entry))
        cases = {t for ts in self.switch_targets.values() for t in ts}
        self.unmodelled = [a for a in self.code.foreign_refs_in(lo + 1, hi)
                           if a in self.index and a not in cases]
        leaders.update(self.unmodelled)
        return sorted(leaders)

    # ---- the fixpoint ---------------------------------------------------
    def _lost(self, va, vb, at_ip) -> None:
        for v in (va, vb):
            if _const_like(v):
                self.events[v].add((at_ip, "merge"))

    def _meet(self, a, b, at_ip):
        if a is None:
            return b
        ra, sa, ea, fa = a
        rb, sb, eb, fb = b
        r = {}
        for k in set(ra) | set(rb):
            va, vb = ra.get(k), rb.get(k)
            if va == vb:
                if va is not None:
                    r[k] = va
            else:
                self._lost(va, vb, at_ip)
        wa, wb = bool(sa.get(_WIPED)), bool(sb.get(_WIPED))
        s = {_WIPED: True} if (wa or wb) else {}
        for k in (set(sa) | set(sb)) - {_WIPED}:
            va = sa[k] if k in sa else (None if wa else _ENTRY)
            vb = sb[k] if k in sb else (None if wb else _ENTRY)
            if va == vb:
                if va is not None or not s:
                    s[k] = va
            else:
                self._lost(va, vb, at_ip)
                if not s:
                    s[k] = None
        return (r, s, ea if ea == eb else None, fa if fa == fb else None)

    def run(self) -> "_MustTrace":
        if not self.ins or len(self.ins) > MAX_FUNC_INS:
            return self
        regs0 = {r: ("in", int(r)) for r in GPR32 if r != Register.ESP}
        regs0[Register.ECX] = ("this",)
        leaders = self._leaders()
        lset = set(leaders)
        block_in = {leaders[0]: (regs0, {}, 0, None)}
        work = [leaders[0]]
        for u in self.unmodelled:
            block_in[u] = ({}, {_WIPED: True}, None, None)
            work.append(u)
        guard = 0
        limit = max(MAX_BLOCK_VISITS, MUST_VISITS_PER_LEADER * len(leaders))
        while work and guard < limit:
            guard += 1
            start = work.pop()
            st = block_in[start]
            regs, stack, esp, fp = dict(st[0]), dict(st[1]), st[2], st[3]
            k = self.index[start]
            succs = []
            while k < len(self.ins):
                i = self.ins[k]
                if i.ip != start and i.ip in lset:
                    succs.append(i.ip)
                    break
                rec = self.record_at
                if rec is True or (rec is not None and i.ip in rec):
                    self.at[i.ip] = (dict(regs), dict(stack), esp, fp)
                end, regs, stack, esp, fp = self._step(i, regs, stack, esp, fp)
                if i.ip in self.switch_targets:
                    succs.extend(self.switch_targets[i.ip])
                    break
                if i.op0_kind == OpKind.NEAR_BRANCH32 and i.mnemonic != Mnemonic.CALL:
                    if i.near_branch_target in self.index:
                        succs.append(i.near_branch_target)
                    if i.mnemonic == Mnemonic.JMP:
                        break
                if end:
                    break
                k += 1
            out = (regs, stack, esp, fp)
            for s in succs:
                old = block_in.get(s)
                new = self._meet(old, out, s)
                if old is None or new != old:
                    block_in[s] = new
                    work.append(s)
        self.converged = not work
        return self

    # ---- locations ------------------------------------------------------
    def _locate(self, base, index, disp, regs, esp, fp):
        b = full32(base) if base != Register.NONE else None
        bval = regs.get(b) if b is not None and b != Register.ESP else None
        stacky = b == Register.ESP or bval == ("fp",) or (
            isinstance(bval, tuple) and bool(bval) and bval[0] == "lea-stack")
        if index != Register.NONE:
            return ("s?",) if stacky else ("idx", bval, disp)
        if b is None:
            return ("g", disp & 0xFFFFFFFF)
        if b == Register.ESP:
            return ("s", esp + disp) if esp is not None else ("s?",)
        if bval == ("fp",):
            return ("s", fp + disp) if fp is not None else ("s?",)
        if stacky:
            return ("s", bval[1] + disp)
        return ("m", bval, disp)

    def _load(self, i, regs, esp, fp):
        return self._locate(i.memory_base, i.memory_index, sdisp(i.memory_displacement),
                            regs, esp, fp)

    @staticmethod
    def _read(loc, stack):
        if loc[0] == "g":
            return ("g", loc[1])
        if loc[0] == "s":
            off = loc[1]
            if off in stack:
                return stack[off]
            if stack.get(_WIPED):
                return None
            return ("arg", off) if off >= 4 else ("loc", off)
        if loc[0] == "m":
            b = loc[1]
            if isinstance(b, tuple) and b and b[0] == "f":       # keep chains shallow
                return None
            return ("f", b if b is not None else ("?",), loc[2])
        return None

    @staticmethod
    def _wipe(stack):
        stack.clear()
        stack[_WIPED] = True

    def _write(self, i, loc, size, stack, value, exact, unbounded=False):
        """The effect of a memory write on the stack facts, and its record."""
        if loc[0] == "s" and not unbounded:
            off = loc[1]
            wiped = stack.get(_WIPED)
            for k in range(off - 3, off + max(size, 1)):
                if wiped:
                    stack.pop(k, None)
                else:
                    stack[k] = None
            if exact and value is not None:
                stack[off] = value
            return
        if _const_like(value):
            self.events[value].add((i.ip, "store"))
        if loc[0] in ("s", "s?"):
            self._wipe(stack)
            return
        if loc[0] in ("m", "idx"):
            if _const_like(loc[1]):
                self.writes.add((i.ip, loc[1], loc[2], size,
                                 bool(unbounded or loc[0] == "idx"),
                                 _const_addr(value) if exact else None))
            if _const_addr(loc[1]) is None:
                x = self._data_addr(loc[2])
                if x is not None:
                    self.blind_writes.add((i.ip, x))
                self._untraced(i, x)
            if self.frame_escapes:
                self._wipe(stack)

    def _leave(self, i, regs, stack, esp, kind):
        """Every constant-like value a call, tail jump or return hands over."""
        if kind == "ret":
            held = [regs.get(Register.EAX), regs.get(Register.EDX)]
        else:
            held = [v for r, v in regs.items() if isinstance(r, int)]
            for off, v in stack.items():
                if off != _WIPED and (esp is None or self.frame_escapes
                                      or esp <= off < esp + 4 * 32):
                    held.append(v)
        for v in held:
            if _const_like(v):
                self.events[v].add((i.ip, kind))

    # ---- one instruction ------------------------------------------------
    def _step(self, i, regs, stack, esp, fp):
        mn = i.mnemonic
        E = Register.ESP
        BP = Register.EBP

        def setr(r, v):
            if r is None:
                return
            if v is None:
                regs.pop(r, None)
            else:
                regs[r] = v

        if mn == Mnemonic.PUSH:
            v = None
            if i.op0_kind == OpKind.REGISTER:
                v = regs.get(full32(i.op0_register))
            elif i.op0_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32, OpKind.IMMEDIATE8):
                v = ("imm", i.immediate(0) & 0xFFFFFFFF)
            elif i.op0_kind == OpKind.MEMORY:
                v = self._read(self._load(i, regs, esp, fp), stack)
            if esp is not None:
                esp -= 4
                self._write(i, ("s", esp), 4, stack, v, True)
            elif _const_like(v):
                # with ESP unknown the push writes below every live slot, but the value
                # is now in a slot this trace cannot name
                self.events[v].add((i.ip, "store"))
            return False, regs, stack, esp, fp
        if mn == Mnemonic.POP:
            v = stack.get(esp) if esp is not None else None
            if esp is not None:
                esp += 4
            if i.op0_kind == OpKind.REGISTER:
                r = full32(i.op0_register)
                if r == E:
                    esp = None
                else:
                    setr(r, v)
            elif i.op0_kind == OpKind.MEMORY:
                self._write(i, self._load(i, regs, esp, fp), 4, stack, v, True)
            return False, regs, stack, esp, fp
        if mn in (Mnemonic.SUB, Mnemonic.ADD) and i.op0_kind == OpKind.REGISTER \
                and full32(i.op0_register) == E:
            if i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) and esp is not None:
                d = sdisp(i.immediate(1))
                esp += -d if mn == Mnemonic.SUB else d
            else:
                esp = None
            return False, regs, stack, esp, fp
        if mn == Mnemonic.AND and i.op0_kind == OpKind.REGISTER and full32(i.op0_register) == E:
            return False, regs, stack, None, fp
        if mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER and i.op1_kind == OpKind.REGISTER:
            d, s = full32(i.op0_register), full32(i.op1_register)
            if d == BP and s == E:
                fp, regs[BP] = esp, ("fp",)
            elif d == E:
                esp = fp if (s == BP and regs.get(BP) == ("fp",)) else None
            elif d is not None and i.op0_register == d:
                setr(d, regs.get(s))
            elif d is not None:
                regs.pop(d, None)
            return False, regs, stack, esp, fp
        if mn == Mnemonic.LEAVE:
            esp = fp if regs.get(BP) == ("fp",) else None
            setr(BP, stack.get(esp) if esp is not None else None)
            if esp is not None:
                esp += 4
            return False, regs, stack, esp, None
        if mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER and i.op1_kind == OpKind.MEMORY:
            d = full32(i.op0_register)
            if d == E:
                esp = None
            elif d is not None and i.op0_register == d:
                setr(d, self._read(self._load(i, regs, esp, fp), stack))
            elif d is not None:
                regs.pop(d, None)
            return False, regs, stack, esp, fp
        if mn == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER \
                and i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
            d = full32(i.op0_register)
            if d == E:
                esp = None
            elif d is not None and i.op0_register == d:
                setr(d, ("imm", i.immediate(1) & 0xFFFFFFFF))
            elif d is not None:
                regs.pop(d, None)
            return False, regs, stack, esp, fp
        if mn == Mnemonic.MOV and i.op0_kind == OpKind.MEMORY:
            loc = self._load(i, regs, esp, fp)
            size = MemorySizeExt.size(i.memory_size)
            v = None
            if i.op1_kind == OpKind.REGISTER and full32(i.op1_register) == i.op1_register:
                v = regs.get(i.op1_register)
            elif i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) and size == 4:
                v = ("imm", i.immediate(1) & 0xFFFFFFFF)
            self._write(i, loc, size, stack, v, size == 4)
            return False, regs, stack, esp, fp
        if mn == Mnemonic.LEA and i.op0_kind == OpKind.REGISTER:
            d = full32(i.op0_register)
            loc = self._load(i, regs, esp, fp)
            if d == E:
                esp = loc[1] if loc[0] == "s" else None
                return False, regs, stack, esp, fp
            v = None
            if loc[0] == "m":
                a = _const_addr(loc[1])
                if a is not None:
                    # a LEA of a LEA of a constant is still that constant plus offsets
                    v = ("lea", ("imm", a), loc[2])
                else:
                    if _const_like(loc[1]):
                        # `lea r,[this+k]`: a pointer derived from `this` is not followed
                        self.events[loc[1]].add((i.ip, "arith"))
                    else:
                        self._untraced(i, self._data_addr(loc[2]))
                    v = ("lea", loc[1] if loc[1] is not None else ("?",), loc[2])
            elif loc[0] == "s":
                v = ("lea-stack", loc[1])
            elif loc[0] == "g":
                v = ("imm", loc[1])
            elif loc[0] == "idx":
                if _const_like(loc[1]):
                    self.events[loc[1]].add((i.ip, "arith"))
                if _const_addr(loc[1]) is None:
                    self._untraced(i, self._data_addr(loc[2]))
            setr(d, v)
            return False, regs, stack, esp, fp
        if mn == Mnemonic.XOR and i.op0_kind == OpKind.REGISTER and i.op1_kind == OpKind.REGISTER \
                and i.op0_register == i.op1_register:
            d = full32(i.op0_register)
            if d == E:
                esp = None
            else:
                setr(d, ("imm", 0))
            return False, regs, stack, esp, fp
        if mn == Mnemonic.CALL:
            self._leave(i, regs, stack, esp, "call")
            target = i.near_branch_target if i.op0_kind == OpKind.NEAR_BRANCH32 else None
            for r in VOLATILE:
                regs.pop(r, None)
            regs[Register.EAX] = ("ret", target, i.ip)
            code = self.code
            if target is not None and target in code.sets_ebp:
                regs.pop(BP, None)
                fp = None
            cleanup = None
            if target is not None and target in code.extents and target in code.has_ret \
                    and target not in code.irregular_esp and target not in code.sets_ebp:
                cleanup = code.cleanup.get(target)
            if esp is not None:
                if cleanup is not None:
                    if cleanup:
                        # the callee pops `cleanup` bytes of arguments, dead afterwards
                        self._write(i, ("s", esp), cleanup, stack, None, False)
                    esp += cleanup
                else:
                    esp = None
            if self.frame_escapes:
                self._wipe(stack)
            return False, regs, stack, esp, fp
        if mn == Mnemonic.RET:
            self._leave(i, regs, stack, esp, "ret")
            return True, regs, stack, esp, fp
        if mn == Mnemonic.JMP and (i.op0_kind != OpKind.NEAR_BRANCH32
                                   or i.near_branch_target not in self.index):
            if i.ip not in self.switch_targets:
                self._leave(i, regs, stack, esp, "jmp")       # a tail call
            return True, regs, stack, esp, fp
        # Generic. A register read as a value (an explicit register operand) that holds a
        # constant-like value leaves the trace when the instruction writes anything, any
        # register (`movd xmm0,ecx`) or memory (`add ecx,4` makes an address this trace
        # no longer follows); so does a data address that is an immediate operand
        # (`add eax,offset obj`). Every written general register becomes unknown, and
        # every memory write takes effect.
        info = _INFO.info(i)
        used_mem = info.used_memory()
        used_regs = info.used_registers()
        mem_writes = [u for u in used_mem if u.access in _WRITES]
        reg_writes = written_gprs(i)
        if mem_writes or reg_writes or any(u.access in _WRITES for u in used_regs):
            # values read: every explicit register operand, and every register read
            # implicitly (`pushad`, the count of `rep stosd`) that does not only form an
            # address of a memory operand
            addr = {full32(r) for u in used_mem for r in (u.base, u.index)
                    if r != Register.NONE}
            read = {full32(i.op_register(kk)) for kk in range(i.op_count)
                    if i.op_kind(kk) == OpKind.REGISTER}
            read |= {full32(u.register) for u in used_regs if u.access in _READS} - addr
            for r in read:
                v = regs.get(r) if r is not None else None
                if _const_like(v):
                    self.events[v].add((i.ip, "arith"))
            for kk in range(i.op_count):
                if i.op_kind(kk) in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                    x = self._data_addr(i.immediate(kk))
                    if x is not None:
                        self.events[("imm", x)].add((i.ip, "arith"))
        rep = i.has_rep_prefix or i.has_repe_prefix or i.has_repne_prefix
        for u in mem_writes:
            if u.segment in (Register.FS, Register.GS):
                continue
            loc = self._locate(u.base, u.index, sdisp(u.displacement), regs, esp, fp)
            self._write(i, loc, MemorySizeExt.size(u.memory_size), stack, None, False,
                        unbounded=rep)
        for r in reg_writes:
            if r == E:
                esp = None
            else:
                regs.pop(r, None)
        return False, regs, stack, esp, fp


def _is_mov_ebp_esp(i: Instruction) -> bool:
    return (i.mnemonic == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER
            and i.op0_register == Register.EBP and i.op1_kind == OpKind.REGISTER
            and i.op1_register == Register.ESP)


# --------------------------------------------------------------------------
# the rules' shared context


class _RuleContext:
    """What R1, R2 and R3 read, computed once per scan. A predicate reads only this."""

    def __init__(self, code: Code, a: dict, attr: "Attribution", ifaces: dict) -> None:
        self.code = code
        self.image = code.image
        self.attr = attr
        self.sites = a["sites"]
        self.widest = max(max(v) for v in ifaces.values() if v) * 4
        unattr = sorted(set(self.sites) - set(attr.calls))
        self.ceiling_sites = [v for v in unattr if self.sites[v]["disp"] <= self.widest]
        # the control population: attributed d3d9 calls that Layer A also shaped, so
        # every predicate is evaluated on them exactly as on a ceiling site
        self.control_sites = [v for v in sorted(attr.calls) if v in self.sites]
        self.rtti = {vt: rtti_type_name(self.image, vt) for vt in attr.class_vt}
        self._traces: dict[int, _MustTrace] = {}
        need: dict[int, set] = collections.defaultdict(set)
        for v in self.ceiling_sites + self.control_sites:
            vl = self.sites[v]["vt_load"]
            if vl["base"] is not None:
                need[self.sites[v]["function"]].add(vl["va"])
        for fn, ips in need.items():
            self.trace(fn, ips)
        self.recv = {v: self._receiver(v)
                     for v in self.ceiling_sites + self.control_sites}
        self._materialized()
        self.abs_writes = _absolute_writes(self)
        self._cwrites: list[tuple] | None = None
        r1_cands = {self.sites[v]["vt_load"]["disp"] & 0xFFFFFFFF
                    for v in self.ceiling_sites + self.control_sites
                    if self.sites[v]["vt_load"]["base"] is None}
        self.const_tbl, self.r1_why = _const_table_globals(self, r1_cands)
        self.folded, self.fold_reasons = _r2_comdat_guard(self)
        self.slot_ok: dict[tuple, tuple[bool, str | None]] = {}

    def trace(self, fn: int, ips=()) -> _MustTrace:
        """fn's must-analysis, cached, with `at` recorded at least for `ips`."""
        t = self._traces.get(fn)
        if t is None or not set(ips) <= t.record_at:
            rec = set(ips) | (t.record_at if t is not None else set())
            t = self._traces[fn] = _MustTrace(self.code, fn, record_at=rec).run()
        return t

    def _receiver(self, v: int):
        """The symbolic object pointer at site v's vtable load, or None when unknown or
        when the function's trace did not converge."""
        vl = self.sites[v]["vt_load"]
        if vl["base"] is None:
            return ("global-vt",)
        t = self.trace(self.sites[v]["function"])
        if not t.converged:
            return None
        st = t.at.get(vl["va"])
        return st[0].get(vl["base"]) if st else None

    def _materialized(self) -> None:
        """Every data address the image materializes, and where.

        `mat` is the sorted list of distinct data addresses named by a 32-bit
        immediate, a LEA displacement or the displacement of a based or indexed operand
        in any decoded instruction, or by a relocated dword in a data section.
        `mat_fns[addr]` are the function entries whose instructions name it,
        `mat_outside` the addresses named by code outside every function extent (seen
        only by the linear sweep), and `data_refs[addr]` the relocated data dwords
        holding it.
        """
        image, code = self.image, self.code
        by_addr: dict[int, set] = collections.defaultdict(set)
        outside: set[int] = set()
        data_refs: dict[int, list] = collections.defaultdict(list)

        def data(v):
            sec = image.section_of(v)
            return sec is not None and not sec.executable

        seen: set[int] = set()
        streams = [(e, code.instructions(e)) for e in code.entries] \
            + [(None, image.instructions)]
        for owner, ins in streams:
            for i in ins:
                if i.ip in seen:
                    continue
                seen.add(i.ip)
                fn = owner if owner is not None else code.owner(i.ip)
                for k in range(i.op_count):
                    kind = i.op_kind(k)
                    if kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                        v = i.immediate(k) & 0xFFFFFFFF
                    elif kind == OpKind.MEMORY and (
                            i.mnemonic == Mnemonic.LEA or i.memory_base != Register.NONE
                            or i.memory_index != Register.NONE):
                        v = i.memory_displacement & 0xFFFFFFFF
                    else:
                        continue
                    if data(v):
                        if fn is None:
                            outside.add(v)
                        else:
                            by_addr[v].add(fn)
        for r in image.relocated:
            sec = image.section_of(r)
            if sec is None or sec.executable:
                continue
            v = image.read_u32(r)
            if v is not None and data(v):
                data_refs[v].append(r)
        self.mat = sorted(set(by_addr) | outside | set(data_refs))
        # the addresses that delimit objects: a function's or a data relocation's, not
        # one only the linear sweep decodes (a misaligned decode is not an object)
        self.mat_delim = sorted(set(by_addr) | set(data_refs))
        self.mat_fns = by_addr
        self.mat_outside = outside
        self.data_refs = data_refs

    def object_base(self, addr: int) -> int | None:
        """The start of the object holding `addr` under OBJECT_BOUNDS: the greatest
        materialized address at or below it in the same section, or None."""
        k = bisect.bisect_right(self.mat_delim, addr) - 1
        if k < 0:
            return None
        b = self.mat_delim[k]
        return b if self.image.section_of(b) is self.image.section_of(addr) else None

    def materialized_in(self, lo: int, hi: int) -> list[int]:
        """Materialized data addresses in [lo, hi]."""
        a = bisect.bisect_left(self.mat, lo)
        b = bisect.bisect_right(self.mat, hi)
        return self.mat[a:b]

    def const_writes(self) -> list[tuple]:
        """Every write through a base the must-analysis proves constant, in every
        function that materializes a data address: (lo, hi or None, ip, base, exact).
        `hi` is None for an indexed or `rep` write, which reaches up from `lo`; `base`
        is the constant base address; `exact` the 32-bit value of a `mov dword` store
        of a constant, else None. Functions whose analysis did not converge are in
        `cwrite_unresolved`. The same pass fills `blind_writes`, sorted (address, ip):
        each write whose base or index the analysis cannot place, at the data address
        in its displacement (`_MustTrace.blind_writes`)."""
        if self._cwrites is None:
            fns = sorted({fn for s in self.mat_fns.values() for fn in s})
            out, unresolved, blind = [], [], []
            for fn in fns:
                t = self._traces.get(fn) or _MustTrace(self.code, fn, record_at=None).run()
                if not t.converged:
                    unresolved.append(fn)
                    continue
                for ip, bval, disp, size, unbounded, exact in t.writes:
                    a = _const_addr(bval)
                    if a is None:
                        continue
                    lo = (a + disp) & 0xFFFFFFFF
                    out.append((lo, None if unbounded else lo + max(size, 1), ip, a, exact))
                blind.extend((x, ip) for ip, x in t.blind_writes)
            out.sort(key=lambda w: (w[0], w[2]))
            self._cwrites = out
            self.cwrite_unresolved = unresolved
            self.blind_writes = sorted(blind)
        return self._cwrites


def _absolute_writes(ctx: _RuleContext) -> list[tuple]:
    """Every write to an absolute address, of any instruction and width, in the anchored
    decodes and the linear sweep: (lo, hi or None, ip, exact). `exact` is the
    immediate of a `mov dword [imm32],imm32`, None for any other write. An absolute
    indexed write `[imm32 + reg*s]` reaches up from imm32 (hi None)."""
    image, code = ctx.image, ctx.code
    out = []
    seen: set[int] = set()
    for ins in [code.instructions(e) for e in code.entries] + [image.instructions]:
        for i in ins:
            if i.ip in seen:
                continue
            seen.add(i.ip)
            if not i.op_count or i.mnemonic == Mnemonic.LEA:
                continue
            for u in _INFO.info(i).used_memory():
                if u.access not in _WRITES or u.base != Register.NONE \
                        or u.segment in (Register.FS, Register.GS):
                    continue
                lo = u.displacement & 0xFFFFFFFF
                if u.index != Register.NONE:
                    out.append((lo, None, i.ip, None))
                    continue
                size = max(MemorySizeExt.size(u.memory_size), 1)
                exact = None
                if i.mnemonic == Mnemonic.MOV and i.op0_kind == OpKind.MEMORY \
                        and i.op1_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) \
                        and size == 4:
                    exact = i.immediate(1) & 0xFFFFFFFF
                out.append((lo, lo + size, i.ip, exact))
    out.sort(key=lambda w: w[0])
    return out


def _writes_touching(ctx: _RuleContext, slot: int, object_base: int | None) -> list[tuple]:
    """Every visible write that may touch the dword at `slot`: (ip, exact or None, how).

    An absolute write, of any form, overlapping it; a write through a constant base
    overlapping it; an indexed or `rep` write that starts in the object holding the
    slot (from `object_base`, OBJECT_BOUNDS) at or below it, absolute or through a
    constant base; and a write `[reg+X]` whose base or index the must-analysis cannot
    place, with X in that object at or below the slot (it may land anywhere in the
    object that starts at X). `exact` is the stored dword only for an exact `mov dword`
    store."""
    lo, hi = slot, slot + 4
    out = []
    for wlo, whi, ip, ex in ctx.abs_writes:
        if wlo >= hi:
            break
        if whi is None:
            if object_base is not None and object_base <= wlo:
                out.append((ip, None, "absolute indexed"))
        elif lo < whi:
            out.append((ip, ex if (wlo == lo and whi == hi) else None, "absolute"))
    for wlo, whi, ip, base, ex in ctx.const_writes():
        if wlo >= hi:
            break
        if whi is None:
            if object_base is not None and object_base <= base:
                out.append((ip, None, "indexed through a constant base"))
        elif lo < whi:
            out.append((ip, ex if (wlo == lo and whi == hi) else None, "constant base"))
    for x, ip in ctx.blind_writes:
        if x >= hi:
            break
        if object_base is not None and object_base <= x:
            out.append((ip, None, "through a base the analysis cannot place"))
    return out


def _escapes_of(ctx: _RuleContext, lo: int, hi: int, exempt=frozenset()) -> list[tuple]:
    """Where an address in [lo, hi] leaves the must-analysis: (ip, kind) for each
    materializing function's events (a call, a tail jump, a store, arithmetic, a merge,
    a return, or a based or indexed write or LEA through a base the analysis cannot
    place, "untraced base"), plus a relocated data dword holding one ("data"), code
    outside every function naming one ("outside"), and a materializing function whose
    analysis did not converge ("unconverged"). A call in `exempt` is skipped."""
    out = []
    for v in ctx.materialized_in(lo, hi):
        for r in ctx.data_refs.get(v, ()):
            out.append((r, "data"))
        if v in ctx.mat_outside:
            out.append((v, "outside"))
    fns = sorted({fn for v in ctx.materialized_in(lo, hi) for fn in ctx.mat_fns.get(v, ())})
    for fn in fns:
        t = ctx.trace(fn)
        if not t.converged:
            out.append((fn, "unconverged"))
            continue
        for v, evs in t.events.items():
            a = _const_addr(v)
            if a is None or not (lo <= a <= hi):
                continue
            out.extend((ip, kind) for ip, kind in sorted(evs)
                       if not (kind == "call" and ip in exempt))
    return out


# --------------------------------------------------------------------------
# R1


def _const_table_globals(ctx: _RuleContext, candidates: set[int]) -> tuple[dict, dict]:
    """For each candidate absolute global g, whether it is a constant function table,
    and if not why: (ok by global, reason by global).

    Soundness (R1). A global that every write sets to a fixed read-only image address,
    and that nothing else can write, holds that `.rdata` pointer at every point of the
    run: a dispatch/function table fixed at link time. A d3d9 COM object's vtable
    pointer is the opposite: the D3D runtime writes it into a heap object at creation,
    and the program obtains the object from `Direct3DCreate9` or a creator, never as a
    constant read out of such a global. So a vtable read from it is not a d3d9 vtable.

    Checked: every write that may touch g (`_writes_touching`: any instruction form and
    width -- `pop [g]`, `xchg [g],r`, `add [g],r`, `movd [g],xmm` -- absolute,
    through a constant base such as `mov ecx,G-4; mov [ecx+4],eax`, indexed or `rep`
    from the object holding g, or `[reg+X]` with X that object's address and a base the
    must-analysis cannot place) is a `mov dword` of a read-only image address, and
    there is one; no address within 3 bytes of g is materialized (an immediate, a LEA,
    `[reg+g]`, `[g+reg*s]`, a relocated data dword, code outside every function); every
    function that names an address of g's object (from its start to g+3) was analysed
    to a fixpoint, so its writes through constant bases are all seen; the initial image
    value is 0 or read-only.
    Hole (OBJECT_BOUNDS aside): when g lies inside a larger object, a pointer to that
    object that reaches a write through memory, a call, arithmetic or a path merge is
    not followed, and neither is a LEA `[reg+X]` of the object's address with a base
    the analysis cannot place; the receipt's `objects` lists each such object and its
    escapes ("untraced base" for the LEA).
    """
    image = ctx.image
    ro, why = {}, {}

    def read_only(x):
        sec = image.section_of(x) if x else None
        return sec is not None and not sec.executable and not sec.writable

    for g in sorted(candidates):
        reason = None
        near = ctx.materialized_in(g - 3, g + 3)
        if near:
            reason = "its address %s is materialized" % hex(near[0])
        if reason is None:
            ws = _writes_touching(ctx, g, ctx.object_base(g))
            bad = [(ip, how) for ip, ex, how in ws if not read_only(ex)]
            if bad:
                reason = "written at %s (%s) with a value that is not a read-only image " \
                         "address" % (hex(bad[0][0]), bad[0][1])
            elif not ws:
                reason = "never written"
        if reason is None:
            base = ctx.object_base(g)
            unresolved = set(ctx.cwrite_unresolved)
            fns = sorted({fn for v in ctx.materialized_in(g if base is None else base, g + 3)
                          for fn in ctx.mat_fns.get(v, ()) if fn in unresolved})
            if fns:
                reason = ("function %s names its object and was not analysed to a "
                          "fixpoint" % hex(fns[0]))
        if reason is None:
            init = image.read_u32(g)
            if init and not read_only(init):
                reason = "its initial value %s is not a read-only image address" % hex(init)
        ro[g] = reason is None
        why[g] = reason
    return ro, why


def _r1_fires(ctx: _RuleContext, v: int) -> bool:
    vl = ctx.sites[v]["vt_load"]
    return vl["base"] is None and bool(ctx.const_tbl.get(vl["disp"] & 0xFFFFFFFF))


# --------------------------------------------------------------------------
# R2


def _r2_comdat_guard(ctx: _RuleContext) -> tuple[set, dict]:
    """The vtable members R2 may not rely on: (excluded set, {fn: (kind, where)}).

    R2 needs every entry into a member to hand it, as ECX, an image C++ object. Virtual
    dispatch through the member's own vtable does: an object whose vptr is an image
    vtable is an image object. A member is excluded when anything else may reach it:
      * it is listed in more than one discovered vtable (identical-COMDAT folding);
      * it is listed at or after an address inside a vtable's run that the program
        names (`ctx.mat`: an immediate, a LEA, a displacement or a relocated data
        dword) and that is not itself a discovered vtable: a run is every consecutive
        code pointer, so a table laid out right after the vtable with no gap reads as
        more of it, and a named interior address is where such a table starts (a
        relocated slot after that point names a function excluded here, so the
        relocation check below needs no cut of its own);
      * its address appears outside a discovered vtable run: an immediate or a LEA in
        any decoded instruction (a callback, a pointer-to-member thunk), or a relocated
        dword in any section outside those runs (an undiscovered second vtable whose
        vptr store was elided, a callback table, a switch case);
      * a direct call, tail jump or conditional branch enters it from another function
        with an ECX that is not provably that caller's own unchanged `this` (the
        caller's analysis did not converge, ECX is unknown, or it is another value),
        or from a caller that is not itself a vtable member, or from code outside every
        function extent, whose ECX this scan cannot trace;
      * it receives `this` from a member that is excluded. The exclusion closes along
        `this`-passing transfers, because a d3d9 pointer that reaches one folded body's
        `this` reaches every member that body hands its `this` to.
    Assumption: virtual dispatch passes the object whose vtable it read, as MSVC does."""
    image, code, attr = ctx.image, ctx.code, ctx.attr
    members = set(attr.entry_class)
    in_vtable: set[int] = set()
    after_named: dict[int, int] = {}       # member -> the named interior address before it
    for vt in sorted(attr.class_vt):
        cut = None
        for k, f in enumerate(vtable_run(image, vt)):
            a = vt + 4 * k
            in_vtable.add(a)
            if cut is None and k and a not in attr.class_vt and ctx.materialized_in(a, a + 3):
                cut = a
            if cut is not None and f in members:
                after_named.setdefault(f, cut)
    folded: dict[int, tuple] = {}
    for fn, vts in attr.entry_class.items():
        if len(vts) > 1:
            folded[fn] = ("listed in several vtables", hex(len(vts)))
    for fn, a in sorted(after_named.items()):
        folded.setdefault(fn, ("listed after a named address inside a vtable run", hex(a)))
    for r in sorted(image.relocated):
        if r in in_vtable:
            continue
        v = image.read_u32(r)
        if v in members and v not in folded:
            folded[v] = ("address in a relocated dword outside every vtable", hex(r))
    entries_into: dict[int, list] = collections.defaultdict(list)
    seen: set[int] = set()
    streams = [(e, code.instructions(e)) for e in code.entries] \
        + [(None, image.instructions)]
    for owner, ins in streams:
        for i in ins:
            if i.ip in seen:
                continue
            seen.add(i.ip)
            src = owner if owner is not None else code.owner(i.ip)
            for k in range(i.op_count):
                kind = i.op_kind(k)
                if kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                    v = i.immediate(k) & 0xFFFFFFFF
                    if v in members and v not in folded:
                        folded[v] = ("address is an immediate", hex(i.ip))
                elif kind == OpKind.MEMORY and i.mnemonic == Mnemonic.LEA:
                    v = i.memory_displacement & 0xFFFFFFFF
                    if v in members and v not in folded:
                        folded[v] = ("address is a LEA", hex(i.ip))
            if i.op0_kind == OpKind.NEAR_BRANCH32 and i.near_branch_target in members:
                t = i.near_branch_target
                if src == t and i.mnemonic != Mnemonic.CALL:
                    continue                       # a branch inside the member itself
                if src is None:
                    folded.setdefault(t, ("entered from code outside every function",
                                          hex(i.ip)))
                else:
                    entries_into[t].append((src, i.ip))
    passes_this: dict[int, set] = collections.defaultdict(set)
    for fn in sorted(members):
        if fn in folded:
            continue
        for caller, ip in entries_into.get(fn, ()):
            t = ctx.trace(caller, (ip,))
            st = t.at.get(ip) if t.converged else None
            e = st[0].get(Register.ECX) if st else None
            if e == ("this",) and caller in members:
                passes_this[fn].add(caller)
                continue
            folded[fn] = (("entered from a caller whose analysis did not converge"
                           if not t.converged else
                           "entered with an unknown ECX" if e is None else
                           "entered with an ECX that is not the caller's this"
                           if e != ("this",) else
                           "entered from a caller that is not a vtable member"), hex(ip))
            break
    changed = True
    while changed:
        changed = False
        for fn, cs in sorted(passes_this.items()):
            if fn not in folded:
                hit = sorted(cs & set(folded))
                if hit:
                    folded[fn] = ("receives this from an excluded member", hex(hit[0]))
                    changed = True
    return set(folded), folded


def _r2_fires(ctx: _RuleContext, v: int) -> bool:
    fn = ctx.sites[v]["function"]
    vts = ctx.attr.entry_class.get(fn)
    return (ctx.recv.get(v) == ("this",) and bool(vts) and fn not in ctx.folded
            and all(ctx.rtti.get(vt) for vt in vts))


# --------------------------------------------------------------------------
# R3


def _slot_holds_only_image_vtables(ctx: _RuleContext, obj: int, vt_disp: int,
                                   dispatch: frozenset) -> tuple[bool, str | None]:
    """Whether the vptr slot at obj+vt_disp provably holds only image class vtables:
    (ok, reason it is not).

    Every visible write that may touch the slot (`_writes_touching`) is a `mov dword`
    of a discovered image class vtable, and there is one. The object's own address --
    every materialized address in [obj, slot+3] -- does not leave the analysis
    (`_escapes_of`; a based access `[reg+X]` with an untraced base counts as leaving
    it), with one exemption: the object's own virtual dispatches through
    this slot (`dispatch`) pass it as `this` to the methods its vtables list, and each
    such method must write the slot only with class vtables and must not let `this`
    leave its own analysis. The slot's initial image value is 0 or a class vtable."""
    image, attr = ctx.image, ctx.attr
    slot = (obj + vt_disp) & 0xFFFFFFFF
    key = (obj, slot, dispatch)
    if key in ctx.slot_ok:
        return ctx.slot_ok[key]

    def done(ok, why):
        ctx.slot_ok[key] = (ok, why)
        return ok, why

    sec = image.section_of(slot)
    if sec is None or sec.executable:
        return done(False, "the slot is not in a data section")
    init = image.read_u32(slot)
    if init and init not in attr.class_vt:
        return done(False, "its initial value %s is not a class vtable" % hex(init))
    esc = _escapes_of(ctx, obj, slot + 3, exempt=dispatch)
    if esc:
        return done(False, "the object's address leaves the analysis at %s (%s)"
                    % (hex(esc[0][0]), esc[0][1]))
    ws = _writes_touching(ctx, slot, ctx.object_base(obj))
    if not ws:
        return done(False, "the slot is never written")
    vts = set()
    for ip, ex, how in ws:
        if ex is None or ex not in attr.class_vt:
            return done(False, "written at %s (%s) with a value that is not a class vtable"
                        % (hex(ip), how))
        vts.add(ex)
    for v in sorted(dispatch):
        disp = ctx.sites[v]["disp"]
        for vt in sorted(vts):
            m = image.read_u32(vt + disp)
            if m is None or m not in ctx.code.extents:
                return done(False, "dispatch %s reaches %s, not a decoded function"
                            % (hex(v), hex(m or 0)))
            t = ctx.trace(m)
            if not t.converged:
                return done(False, "the analysis of method %s did not converge" % hex(m))
            if t.events.get(("this",)):
                ip, kind = sorted(t.events[("this",)])[0]
                return done(False, "method %s lets `this` leave its analysis at %s (%s)"
                            % (hex(m), hex(ip), kind))
            for ip, bval, d, size, unbounded, ex in sorted(t.writes, key=str):
                if bval != ("this",):
                    continue
                touches = (d <= vt_disp + 3) if unbounded else \
                    (d < vt_disp + 4 and vt_disp < d + size)
                if touches and (unbounded or size != 4 or d != vt_disp
                                or ex not in attr.class_vt):
                    return done(False, "method %s writes the slot at %s with a value that "
                                "is not a class vtable" % (hex(m), hex(ip)))
    return done(True, None)


def _r3_object(ctx: _RuleContext, v: int) -> tuple[int, int] | None:
    """(object address, vptr slot) when site v's receiver is a fixed image data object."""
    obj = _const_addr(ctx.recv.get(v))
    if obj is None:
        return None
    sec = ctx.image.section_of(obj)
    if sec is None or sec.executable:
        return None
    return obj, (obj + ctx.sites[v]["vt_load"]["disp"]) & 0xFFFFFFFF


def _r3_fires(ctx: _RuleContext, v: int) -> bool:
    o = _r3_object(ctx, v)
    if o is None:
        return False
    obj, slot = o
    if not hasattr(ctx, "r3_by_slot"):
        by: dict[int, set] = collections.defaultdict(set)
        for w in ctx.ceiling_sites + ctx.control_sites:
            ow = _r3_object(ctx, w)
            if ow is not None:
                by[ow[1]].add(w)
        ctx.r3_by_slot = {k: frozenset(x) for k, x in by.items()}
    dispatch = ctx.r3_by_slot.get(slot, frozenset())
    return _slot_holds_only_image_vtables(ctx, obj, ctx.sites[v]["vt_load"]["disp"],
                                          dispatch)[0]


# --------------------------------------------------------------------------
# applying the rules


RULES = (("R1_constant_function_table_global", "_r1_fires"),
         ("R2_this_of_rtti_confirmed_vtable_member", "_r2_fires"),
         ("R3_static_or_embedded_image_object", "_r3_fires"))


def receiver_class(v) -> str:
    """The kind of a ceiling site's receiver at its vtable load, from the must-analysis's
    symbolic value (_MustTrace): "this", "in" (another entry register), "g" (a global's
    dword), "imm" (an immediate), "ret" (a call's return), "arg" or "loc" (a stack slot),
    "f:<kind>" or "lea:<kind>" (a field of, or the address of a field of, a base of that
    kind), "global-vt" (the vtable itself read from an absolute global), and "unknown" when
    the analysis cannot place it or did not converge."""
    if not isinstance(v, tuple) or not v:
        return "unknown"
    if v[0] in ("f", "lea") and len(v) > 1 and isinstance(v[1], tuple) and v[1]:
        return f"{v[0]}:{v[1][0]}"
    return str(v[0])


def ceiling_site_row(ctx: "_RuleContext", v: int, region: str, ruled_out_by: str | None) -> dict:
    """One ceiling site as the private scan receipt lists it: its address, its function,
    the slot displacement, the receiver class at its vtable load, the code region of its
    function, and the sound rule that rules it out, if any (the ceiling still counts it)."""
    s = ctx.sites[v]
    return {"va": hex(v), "function": hex(s["function"]), "disp": hex(s["disp"]),
            "receiver": receiver_class(ctx.recv.get(v)), "region": region,
            "ruled_out_by": ruled_out_by}


def rule_outs(code: Code, a: dict, attr: "Attribution", ifaces: dict) -> dict:
    """The sound rule-outs R1, R2, R3 over the unattributed ceiling sites.

    Each rule is applied to the unattributed sites whose displacement is within the
    widest d3d9 vtable (the ceiling population). A site removed by an earlier rule is
    not offered to a later one, so the per-rule counts partition the removed set. The
    control is the same predicate -- the very function that removes ceiling sites --
    evaluated on every attributed d3d9 call that Layer A also shaped. A sound rule never
    fires on a proven d3d9 call, so each rule's `control_sites_ruled_out` must be
    empty, and tools/s4/m7_inputs.py refuses a scan where it is not.
    """
    ctx = _RuleContext(code, a, attr, ifaces)
    preds = [(name, globals()[fn]) for name, fn in RULES]
    removed_by: dict[str, list[int]] = {name: [] for name, _ in preds}
    taken: set[int] = set()
    for name, pred in preds:
        for v in ctx.ceiling_sites:
            if v not in taken and pred(ctx, v):
                removed_by[name].append(v)
                taken.add(v)
    control = {name: [hex(v) for v in ctx.control_sites if pred(ctx, v)]
               for name, pred in preds}

    def region(v):
        return code.fmap.region_of(ctx.sites[v]["function"])

    n = len(ctx.ceiling_sites)
    r1, r2, r3 = (removed_by[name] for name, _ in preds)
    removed = sorted(taken)
    ctx.const_writes()
    unconverged = sorted({fn for fn, t in ctx._traces.items() if not t.converged}
                         | set(ctx.cwrite_unresolved))
    r1_globals = sorted(g for g, ok in ctx.const_tbl.items() if ok)
    r1_objects = {}
    for g in r1_globals:
        base = ctx.object_base(g)
        esc = _escapes_of(ctx, base, g - 4) if base is not None and base < g - 3 else []
        r1_objects[hex(g)] = {
            "object_base": hex(base) if base is not None else None,
            "offset_in_object": hex(g - base) if base is not None else None,
            "next_materialized_address": (
                hex(ctx.mat_delim[bisect.bisect_right(ctx.mat_delim, g)])
                if bisect.bisect_right(ctx.mat_delim, g) < len(ctx.mat_delim) else None),
            "object_address_escapes": len(esc),
            "object_address_escapes_by_kind": dict(collections.Counter(k for _, k in esc)),
            "object_address_escape_examples": [[hex(ip), k] for ip, k in esc[:6]],
        }
    r3_objects = sorted({o for v in ctx.ceiling_sites if (o := _r3_object(ctx, v))})
    ruled_by = {v: name for name, sites in removed_by.items() for v in sites}
    return {
        # main() moves this into layer_a_shape_census: every ceiling site, in address order.
        "ceiling_sites": [ceiling_site_row(ctx, v, region(v), ruled_by.get(v))
                          for v in ctx.ceiling_sites],
        "note": (
            "Sound rule-outs lower the ceiling by proving a site is not a d3d9 COM "
            "vtable call. Each rule is a predicate; the same predicate removes ceiling "
            "sites and is evaluated on every attributed d3d9 call as its control, and "
            "`control_sites_ruled_out` must be empty for each rule. The receiver of each "
            "site is from a per-function must-analysis; a site whose function's "
            "analysis did not converge, or whose receiver it cannot place, stays in the "
            "ceiling."),
        "assumption": OBJECT_BOUNDS,
        "ceiling_population_is_disp_within": hex(ctx.widest),
        "ceiling_before_rules": n,
        "R1_removed": len(r1),
        "ceiling_after_R1": n - len(r1),
        "R2_removed": len(r2),
        "ceiling_after_R2": n - len(r1) - len(r2),
        "R3_removed": len(r3),
        "ceiling_after_R3": n - len(removed),
        "must_analysis": {
            "functions_traced": len(ctx._traces),
            "functions_traced_for_constant_base_writes": len(
                {fn for s in ctx.mat_fns.values() for fn in s}),
            "functions_whose_analysis_did_not_converge": [hex(f) for f in unconverged],
            "ceiling_sites_whose_receiver_is_unknown": sum(
                1 for v in ctx.ceiling_sites if ctx.recv.get(v) is None),
            "control_sites": len(ctx.control_sites),
        },
        "R1_constant_function_table_global": {
            "soundness": ("the vtable is read from an absolute global that every visible "
                          "write of any form sets to a read-only image address, whose "
                          "address is never materialized, whose object's naming functions "
                          "were all analysed to a fixpoint and whose initial value is 0 or "
                          "read-only, so it is a link-time-constant dispatch table, not a "
                          "d3d9 object's run-time vtable pointer"),
            "holes": ("OBJECT_BOUNDS; and when the global lies inside a larger object, a "
                      "pointer to that object that reaches a write through memory, a "
                      "call, arithmetic or a path merge is not followed (only writes "
                      "through a base the must-analysis proves constant are, and writes "
                      "[reg+X] at the object's address X with a base it cannot place), "
                      "nor is a LEA [reg+X] of the object's address with such a base. "
                      "`objects` measures it per global: the object the materialized "
                      "addresses imply and every place its address leaves the analysis "
                      "(\"untraced base\" for such a LEA)."),
            "removed": len(r1),
            "globals": [hex(g) for g in r1_globals],
            "objects": r1_objects,
            "globals_refused": {hex(g): w for g, w in sorted(ctx.r1_why.items()) if w},
            "sites": [hex(v) for v in r1],
            "control_sites_ruled_out": control["R1_constant_function_table_global"],
        },
        "R2_this_of_rtti_confirmed_vtable_member": {
            "soundness": ("the receiver is `this` (ECX at entry, unchanged on every "
                          "path) of a function listed in exactly one discovered image "
                          "C++ vtable, ahead of any address inside its run that the "
                          "program names, with a valid RTTI COL at vt-4 (a `.?A` name) "
                          "and entered only by virtual dispatch or by a member passing "
                          "its own `this`; `this` of an image class is never a d3d9 "
                          "interface pointer"),
            "holes": ("assumes virtual dispatch passes the object whose vtable it read; "
                      "a body reachable some way none of the exclusions sees (a second "
                      "vtable with no relocation, an address computed arithmetically) "
                      "would be a hole; a vtable's run is every consecutive code pointer "
                      "and is cut where the program names an address inside it, so a "
                      "code-pointer table laid out right after a vtable whose start "
                      "nothing names (reached only through an address computed from "
                      "another) is still read as more of that vtable"),
            "comdat_guard_excluded_functions": len(ctx.folded),
            "comdat_guard_excluded_by_reason": dict(sorted(collections.Counter(
                k for k, _ in ctx.fold_reasons.values()).items())),
            "removed": len(r2),
            "sites": [hex(v) for v in r2],
            "control_sites_ruled_out": control["R2_this_of_rtti_confirmed_vtable_member"],
        },
        "R3_static_or_embedded_image_object": {
            "soundness": ("the receiver is the address of a fixed image data object "
                          "whose vtable-pointer slot every visible write sets to a "
                          "discovered image class vtable, whose own address never leaves "
                          "the analysis except as `this` of its dispatches through that "
                          "slot, and whose dispatched methods write the slot only with "
                          "class vtables and keep `this`; so the slot always holds an "
                          "image vtable"),
            "holes": ("OBJECT_BOUNDS; a pointer to an enclosing object (materialized "
                      "below the object) that reaches a write through memory, a call, "
                      "arithmetic or a merge is not followed; the dispatched methods are "
                      "checked one level deep, and one that lets `this` go refuses the "
                      "object"),
            "removed": len(r3),
            "candidate_sites": sum(1 for v in ctx.ceiling_sites if _r3_object(ctx, v)),
            "candidate_objects": len(r3_objects),
            "slots_refused": {hex(k[1]): w for k, (ok, w) in sorted(
                ctx.slot_ok.items(), key=lambda kv: kv[0][:2]) if not ok},
            "sites": [hex(v) for v in r3],
            "control_sites_ruled_out": control["R3_static_or_embedded_image_object"],
        },
        "ceiling_after_sound_rule_outs": n - len(removed),
        "removed_sites": [hex(v) for v in removed],
        "by_region_removed": dict(collections.Counter(region(v) for v in removed)),
    }


# --------------------------------------------------------------------------
# d3dx9_42 import call sites


def d3dx_sites(code: Code) -> list[dict]:
    """Call sites of each `d3dx9_42.dll` import.

    The same three shapes `tools/s4/import_surface.py` enumerates for
    `GetProcAddress`: a direct `call [slot]`, a direct `call <thunk>` where the
    thunk is `jmp [slot]`, and a pointer load into a register followed by a
    `call <reg>` inside the same function.
    """
    image = code.image
    slots = {va: nm for va, (dll, nm) in image.imports.items()
             if dll.lower().startswith("d3dx9")}
    thunk_of = {slot: va for va, slot in image.thunks.items() if slot in slots}
    out: dict[str, dict] = {nm: {"name": nm, "slot": hex(va), "sites": [],
                                 "thunk": hex(thunk_of[va]) if va in thunk_of else None}
                            for va, nm in sorted(slots.items(), key=lambda kv: kv[1])}
    rev_thunk = {va: slots[slot] for slot, va in thunk_of.items()}
    for entry in code.entries:
        ins = code.instructions(entry)
        loaded: dict[int, str] = {}
        for i in ins:
            if i.mnemonic == Mnemonic.CALL:
                if i.op0_kind == OpKind.NEAR_BRANCH32:
                    nm = rev_thunk.get(i.near_branch_target)
                    if nm:
                        out[nm]["sites"].append(i.ip)
                elif i.op0_kind == OpKind.MEMORY:
                    d = abs_displacement(i)
                    if d in slots:
                        out[slots[d]]["sites"].append(i.ip)
                elif i.op0_kind == OpKind.REGISTER:
                    nm = loaded.get(full32(i.op0_register))
                    if nm:
                        out[nm]["sites"].append(i.ip)
                for r in VOLATILE:
                    loaded.pop(r, None)
                continue
            if i.mnemonic == Mnemonic.MOV and i.op0_kind == OpKind.REGISTER:
                r = full32(i.op0_register)
                if r is None:
                    continue
                d = abs_displacement(i) if i.op1_kind == OpKind.MEMORY else None
                if d in slots:
                    loaded[r] = slots[d]
                else:
                    loaded.pop(r, None)
            elif i.op0_kind == OpKind.REGISTER and i.mnemonic not in NON_WRITING:
                loaded.pop(full32(i.op0_register), None)
    for rec in out.values():
        rec["sites"] = sorted(set(rec["sites"]))
        rec["call_sites"] = len(rec["sites"])
    return list(out.values())


# --------------------------------------------------------------------------
# the anchors, and the checks that can fail
#
# Every address below came from a real run of the lifted image; a scan that does
# not find them is wrong, and the run says so rather than printing a total.

ANCHORS = [
    {"va": 0x008685b3, "kind": "seed-deref",
     "expect_interface": "IDirect3D9", "expect_method": None,
     "note": "dereferences Direct3DCreate9's result with no null check; the call "
             "is at 0x00868599 returning to 0x0086859e"},
    {"va": 0x008685bd, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 8, "expect_method": "GetAdapterDisplayMode"},
    {"va": 0x00865f4a, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 11, "expect_method": "CheckDeviceMultiSampleType"},
    {"va": 0x00865f69, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 11, "expect_method": "CheckDeviceMultiSampleType"},
    {"va": 0x00865f7e, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 4, "expect_method": "GetAdapterCount"},
    {"va": 0x00865f96, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 5, "expect_method": "GetAdapterIdentifier"},
    {"va": 0x00865fee, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 16, "expect_method": "CreateDevice"},
    {"va": 0x00865d3b, "kind": "return", "expect_interface": "IDirect3D9",
     "expect_slot": 15, "expect_method": "GetAdapterMonitor"},
    {"va": 0x00865cb3, "kind": "return", "expect_interface": "IDirect3DDevice9",
     "expect_slot": 7, "expect_method": "GetDeviceCaps"},
    {"va": 0x008671cf, "kind": "return", "expect_interface": "IDirect3DVertexShader9",
     "expect_slot": 4, "expect_method": "GetFunction"},
    {"va": 0x00867202, "kind": "return", "expect_interface": "IDirect3DVertexShader9",
     "expect_slot": 4, "expect_method": "GetFunction"},
    {"va": 0x00866a8d, "kind": "return", "expect_interface": "IDirect3DTexture9",
     "expect_slot": 17, "expect_method": "GetLevelDesc"},
    {"va": 0x00866b50, "kind": "return", "expect_interface": "IDirect3DTexture9",
     "expect_slot": 13, "expect_method": "GetLevelCount"},
]


def check_anchors(attr: Attribution, a_sites: dict) -> dict:
    # The brief gives most anchors as the *return* address of the call and one
    # (0x00865f96) as the call instruction itself, so both spellings are accepted
    # and the record says which one matched.
    by_return: dict[int, dict] = {}
    for rec in attr.calls.values():
        by_return.setdefault(rec["return_va"], rec)
        by_return.setdefault(rec["va"], rec)
    rows = []
    found = 0
    for anc in ANCHORS:
        row = {"va": hex(anc["va"]), "kind": anc["kind"],
               "expect_interface": anc["expect_interface"],
               "expect_method": anc.get("expect_method"),
               "expect_slot": anc.get("expect_slot")}
        if "note" in anc:
            row["note"] = anc["note"]
        if anc["kind"] == "seed-deref":
            # the seed itself: the Direct3DCreate9 call site and the deref after it
            seed_call = 0x00868599
            ok = any(rec["function"] == attr.code.owner(seed_call)
                     and "IDirect3D9" in rec["interfaces"]
                     for rec in attr.calls.values())
            row["found"] = bool(ok)
            row["got"] = ("the seed reaches an IDirect3D9 call in the same function"
                          if ok else "no IDirect3D9 attribution in the seeding function")
        else:
            rec = by_return.get(anc["va"])
            if rec is None:
                shaped = next((s for s in a_sites.values()
                               if anc["va"] in (s["return_va"], s["va"])), None)
                row["found"] = False
                row["got"] = ("vtable-shaped but unattributed, displacement %s"
                              % hex(shaped["disp"]) if shaped
                              else "no call site ends at this address")
            else:
                iface_ok = anc["expect_interface"] in rec["interfaces"]
                method_ok = rec["method"] == anc["expect_method"]
                slot_ok = rec["slot"] == anc["expect_slot"]
                row["found"] = bool(iface_ok and method_ok and slot_ok)
                row["got"] = {"interfaces": rec["interfaces"], "slot": rec["slot"],
                              "method": rec["method"], "call_va": hex(rec["va"])}
                if iface_ok and method_ok and slot_ok and len(rec["interfaces"]) > 1:
                    row["note"] = ("the receiver is a join of %d interfaces that agree "
                                   "on this slot's method" % len(rec["interfaces"]))
        found += 1 if row["found"] else 0
        rows.append(row)
    return {"anchors": rows, "anchors_found": found, "anchors_total": len(rows),
            "anchors_note": ("the brief listed these in ten bullets; three bullets name "
                             "two addresses each, so the honest total is %d"
                             % len(rows))}


# --------------------------------------------------------------------------


def analyse(code: Code, ifaces: dict, vtinfo: dict, *, disp_shift: int = 0,
            seed_d3d: bool = True, seed_d3dx: bool = True,
            budget: float = 420.0) -> Attribution:
    attr = Attribution(code, ifaces, vtinfo, disp_shift=disp_shift, seed_d3d=seed_d3d,
                       seed_d3dx=seed_d3dx)
    attr.run(deadline=time.time() + budget)
    return attr


def summarise(attr: Attribution, a: dict) -> dict:
    per: dict[tuple[str, int], dict] = {}
    for rec in sorted(attr.calls.values(), key=lambda r: r["va"]):
        key = ("|".join(rec["interfaces"]), rec["slot"])
        e = per.setdefault(key, {"interface": rec["interfaces"][0] if len(
            rec["interfaces"]) == 1 else "|".join(rec["interfaces"]),
            "slot": rec["slot"], "method": rec["method"], "call_sites": []})
        e["call_sites"].append(hex(rec["va"]))
    methods = sorted(per.values(), key=lambda e: (-len(e["call_sites"]), e["interface"],
                                                  e["slot"]))
    for e in methods:
        e["count"] = len(e["call_sites"])
    by_iface = collections.Counter()
    distinct: set[tuple[str, int]] = set()
    for rec in attr.calls.values():
        for i in rec["interfaces"]:
            by_iface[i] += 1          # call sites where this interface is a possible
            distinct.add((i, rec["slot"]))   # receiver; joins are counted under each
    attributed_shaped = set(attr.calls) & set(a["sites"])
    return {
        "methods": methods,
        "distinct_methods": len(distinct),
        "distinct_interface_slot_pairs_after_joins": len(per),
        "call_sites": len(attr.calls),
        "by_interface": dict(sorted(by_iface.items())),
        "by_interface_means": ("call sites where that interface is a possible "
                               "receiver; a site whose receiver is a join of two "
                               "interfaces is counted under both, so the sum is at "
                               "least call_sites"),
        "attributed_sites_that_are_layer_a_shaped": len(attributed_shaped),
        "attributed_sites_not_in_layer_a": sorted(
            hex(v) for v in set(attr.calls) - set(a["sites"])),
        "fixpoint_rounds": attr.rounds,
        # A global fixpoint over truncated local analyses is not a fixpoint: a function
        # whose analysis stopped at MAX_BLOCK_VISITS, or was skipped for size, may still
        # have locations that would change.
        "fixpoint_reached": (not getattr(attr, "exhausted", True)
                             and getattr(attr, "stopped_by", "fixpoint") == "fixpoint"
                             and not getattr(attr, "local_limit_hit", ())
                             and not getattr(attr, "too_large", ())),
        "stopped_by": getattr(attr, "stopped_by", None),
        "stopped_by_means": (
            "fixpoint: the work list emptied; deadline: the wall-clock budget ran out "
            "with work pending (the round's unprocessed suffix is kept as pending work, "
            "never dropped); round_cap: MAX_ROUNDS rounds ran with work pending. "
            "fixpoint_reached is true only when the work list emptied AND no function's "
            "last analysis stopped at the block-visit limit or was skipped for size, so "
            "no location can change again under these transfer rules."),
        "pending_functions_at_stop": len(getattr(attr, "pending", ())),
        "deadline_crossings_inside_a_round": getattr(attr, "deadline_crossings", 0),
        "functions_whose_last_analysis_hit_the_block_visit_limit": sorted(
            hex(v) for v in getattr(attr, "local_limit_hit", ())),
        "functions_skipped_for_size": sorted(hex(v) for v in getattr(attr, "too_large", ())),
        "receiver_type_conflicts": attr.conflicts,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--break", dest="brk", default="none",
                    choices=("none", "shift-displacements", "no-d3d-seed"))
    ap.add_argument("--no-falsification", action="store_true")
    ap.add_argument("--budget", type=float, default=240.0,
                    help="seconds for one fixpoint; every wait is bounded")
    args = ap.parse_args()

    started = time.time()
    doc: dict = {"schema": SCHEMA, "id": RECEIPT_ID,
                 "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                 "question": (
                     "Which d3d9 interface methods and d3dx9_42 entry points does the "
                     "shipped executable contain a call site for? That set is the "
                     "denominator of metric M7; the numerator is what runtime/sa2_d3d9.c "
                     "implements.")}
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def flush(extra: dict | None = None) -> None:
        if extra:
            doc.update(extra)
        doc["elapsed_seconds"] = round(time.time() - started, 1)
        args.out.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n",
                            encoding="utf-8")

    flush({"verdict": "incomplete: the scan did not finish"})

    ifaces, rows_of = parse_vtables(D3D9_C)
    doc["inputs"] = {
        "executable": {"path": rel(args.exe), "sha256": sha256_of(args.exe)},
        "vtable_tables": {"from": rel(D3D9_C), "sha256": sha256_of(D3D9_C),
                          "interfaces": {k: {"slots": len(v), "rows": rows_of[k]}
                                         for k, v in sorted(ifaces.items())}},
        "function_map": None,
    }
    doc["commands"] = ["source tools/env.sh",
                       ".venv/bin/python tools/s4/d3d9_methods.py"]
    flush()

    image = Image(args.exe)
    fmap = FunctionMap()
    doc["inputs"]["function_map"] = fmap.describe()
    vtinfo = find_cpp_vtables(image)
    code = Code(image, fmap, extra_entries=vtinfo["members"])
    doc["inputs"]["decode_coverage"] = code.coverage | {
        "cpp_vtables": len(vtinfo["class_vt"]),
        "vtable_listed_functions": len(vtinfo["members"]),
        "entries_added_beyond_the_function_map": len(
            set(vtinfo["members"]) & set(code.extents)
            - {f["address"] for f in fmap.functions}),
    }
    flush()

    a = layer_a(code)
    attr = analyse(code, ifaces, vtinfo, budget=args.budget,
                   disp_shift=(4 if args.brk == "shift-displacements" else 0),
                   seed_d3d=(args.brk != "no-d3d-seed"))
    b = summarise(attr, a)
    checks = check_anchors(attr, a["sites"])

    unattributed = sorted(set(a["sites"]) - set(attr.calls))
    widest = max(max(v) for v in ifaces.values() if v) * 4
    beyond = [va for va in unattributed if a["sites"][va]["disp"] > widest]
    doc["layer_a_shape_census"] = {
        "vtable_shaped_call_sites": a["vtable_shaped_call_sites"],
        "census_counts_every_cpp_virtual_call": (
            "Layer A recognises a vtable-shaped indirect call, and the game's own C++ "
            "virtual dispatch has exactly that shape. The two largest displacements in "
            "the histogram, 0x0 and 0x4, are %d and %d sites and are dominated by the "
            "game's own vtables, not by d3d9. The census is therefore an upper bound "
            "on the d3d9 population, not an estimate of it."
            % (a["by_displacement"].get("0x0", 0), a["by_displacement"].get("0x4", 0))),
        "by_displacement": a["by_displacement"],
        "unattributed_vtable_call_sites": len(unattributed),
        "unattributed_displacement_past_every_d3d9_vtable": len(beyond),
        "widest_d3d9_vtable_displacement": hex(widest),
        "unattributed_that_could_still_be_d3d9": len(unattributed) - len(beyond),
        "unattributed_by_displacement": {
            hex(d): n for d, n in sorted(collections.Counter(
                a["sites"][va]["disp"] for va in unattributed).items())},
    }
    doc["layer_a_shape_census"]["same_block_correction"] = same_block_correction(
        code, a, set(attr.calls), widest)
    doc["layer_b_attributed"] = b
    ro = rule_outs(code, a, attr, ifaces)
    # Every site the ceiling counts, listed here, in the private receipt, and nowhere tracked:
    # a list of thousands of guest addresses in Documentation/receipts/ would fail
    # Documentation/scripts/check_boundary.py. tools/s4/m7_inputs.py carries its length and
    # the sha256 of its canonical form into the tracked receipt (the user's ruling of
    # 2026-10-09, item 1: "list every site").
    doc["layer_a_shape_census"]["ceiling_sites"] = ro.pop("ceiling_sites")
    doc["sound_rule_outs"] = ro

    dx = d3dx_sites(code)
    called = [r for r in dx if r["call_sites"]]
    doc["d3dx9_42"] = {
        "imports": [{"name": r["name"], "slot": r["slot"], "thunk": r["thunk"],
                     "call_sites": r["call_sites"],
                     "sites": [hex(v) for v in r["sites"]]} for r in dx],
        "distinct_imported_functions": len(dx),
        "called_at_least_once_statically": len(called),
    }

    d3d9_methods = b["distinct_methods"]
    doc["denominator"] = {"d3d9_methods": d3d9_methods,
                          "d3dx9_functions": len(called),
                          "total": d3d9_methods + len(called)}
    doc["checks"] = checks

    doc["method"] = (
        "Layer A decodes every analyzer function from its own entry (12683 anchored "
        "decodes, not one linear sweep) and counts every indirect call whose target "
        "came out of a vtable load: `call [r+D]` where r was itself loaded from "
        "memory in the same basic block, and `call r` where r was loaded by "
        "`mov r,[r2+D]` with r2 loaded from memory in the same block. It names no "
        "interface. "
        "Layer B is an abstract interpretation whose values are sets of atoms -- a "
        "COM interface pointer, a C++ object pointer identified by its primary "
        "vtable constant, a vtable pointer, a method pointer, or the address of a "
        "tracked location -- over locations that are registers and stack slots "
        "(flow-sensitive, per basic block, joined at merges) and program globals "
        "and C++ object fields keyed by (owning class vtable, byte offset) "
        "(flow-insensitive, program-wide, monotone). It is seeded from the one "
        "d3d9 import, `Direct3DCreate9`, whose return value is an IDirect3D9*, and "
        "from typed d3dx9_42 outputs: the six texture creators' last arguments "
        "yield IDirect3DTexture9**, IDirect3DCubeTexture9** or "
        "IDirect3DVolumeTexture9**; D3DXGetShaderConstantTable argument 1 "
        "yields ID3DXConstantTable**. A store of a C++ vtable "
        "constant into [reg] types reg as that class, and a function whose address "
        "appears in a C++ vtable gets that class in ECX at entry, which is what "
        "gives an object field an owner -- without an owner a field is never typed, "
        "so `[esi+4]` in one class and `[eax+0Ch]` in another are never conflated. "
        "`mov vt,[obj]` on a typed obj yields the interface's vtable pointer; "
        "`call [vt+D]` and `mov m,[vt+D]; call m` then name slot D/4 of that "
        "interface, whose name and argument count are read out of runtime/sa2_d3d9.c. "
        "Every creator method in the table above hands a typed pointer back through "
        "a named out-parameter index, and the pushed argument at that index is "
        "resolved to a location address and typed. Types cross functions through "
        "globals, through object fields and through direct calls (both the pushed "
        "argument slots and the live-in registers), and the whole thing iterates to "
        "a fixpoint. A receiver whose type set names several interfaces is "
        "attributed only when every one of them gives the same method name and "
        "argument count at that slot; otherwise the site stays in Layer A.")

    can_be_100 = (doc["layer_a_shape_census"]["unattributed_vtable_call_sites"] == 0)
    doc["denominator_means"] = {
        "counted": (
            "a d3d9 interface method with at least one call site in the image, and a "
            "d3dx9_42 import with at least one call site. %d distinct "
            "(interface, slot) pairs across %d attributed call sites, plus %d of the "
            "%d d3dx9_42 imports."
            % (d3d9_methods, b["call_sites"], len(called), len(dx))),
        "not_counted_a": (
            "a method no code path calls. If the image never contains a call site for "
            "IDirect3DDevice9::DrawTriPatch, it is not in the denominator, however "
            "fully runtime/sa2_d3d9.c implements it. The denominator is a property of "
            "the binary, not of the runtime."),
        "not_counted_b": (
            "a vtable call site whose receiver this dataflow could not type. There are "
            "%d of those out of %d vtable-shaped sites (%.1f%% of the shape census), "
            "and each one may name a method that is not in the denominator. So the "
            "denominator is an UNDER-estimate, and that number is the error bar this "
            "measurement carries. It is a loose bar and it is loose in a knowable "
            "direction: Layer A counts every C++ virtual call in the game, and only "
            "some of those are COM. %d of the %d unattributed sites have a "
            "displacement past the end of the widest d3d9 vtable (%s, "
            "IDirect3DDevice9's 119 slots) and so cannot be d3d9 calls at all, which "
            "leaves at most %d sites that could still be adding to the denominator."
            % (doc["layer_a_shape_census"]["unattributed_vtable_call_sites"],
               a["vtable_shaped_call_sites"],
               100.0 * doc["layer_a_shape_census"]["unattributed_vtable_call_sites"]
               / max(1, a["vtable_shaped_call_sites"]),
               doc["layer_a_shape_census"][
                   "unattributed_displacement_past_every_d3d9_vtable"],
               doc["layer_a_shape_census"]["unattributed_vtable_call_sites"],
               doc["layer_a_shape_census"]["widest_d3d9_vtable_displacement"],
               doc["layer_a_shape_census"]["unattributed_that_could_still_be_d3d9"])),
        "over_estimate": (
            "a call site in code that never executes -- a dead branch, a debug path, "
            "an early-out, a PerfHUD probe, a D3DX failure message -- is counted "
            "exactly like one on the hot path, because a static scan cannot tell them "
            "apart. So the denominator is also an OVER-estimate of what a running game "
            "needs. The two errors point in opposite directions and do not cancel: "
            "they are separate quantities and neither is a correction for the other."),
        "can_M7_read_100_percent": (
            ("No, not meaningfully, not yet. Two things stop it. First, the "
             "denominator is not closed: %d vtable-shaped call sites are still "
             "unattributed and at most %d of them could be d3d9, so implementing "
             "every method in today's list of %d could still leave real call sites "
             "unimplemented and M7 would read 100%% while the game faulted. Second, "
             "the denominator includes methods only dead code calls, so the last few "
             "percent buy nothing a running game needs. M7 should be printed as "
             "'n/%d, error bar +%d unattributed sites', and 100%% should not be the "
             "goal until that error bar is zero."
             % (doc["layer_a_shape_census"]["unattributed_vtable_call_sites"],
                doc["layer_a_shape_census"]["unattributed_that_could_still_be_d3d9"],
                d3d9_methods + len(called), d3d9_methods + len(called),
                doc["layer_a_shape_census"]["unattributed_that_could_still_be_d3d9"]))
            if not can_be_100 else
            "Every vtable-shaped call site is attributed, so the denominator is "
            "closed for the shapes Layer A recognises, and M7 can read 100% once the "
            "runtime implements all of it -- subject to the over-estimate above."),
    }

    doc["limits"] = [
        "Layer A recognises the two MSVC COM call shapes and no others: a tail "
        "`jmp [vt+D]`, a call through a method pointer cached in a global, and a "
        "call whose vtable load is in a different basic block from the call are not "
        "in the census and so are not in the error bar either.",
        "Layer B is a may-analysis: a location that holds two different interfaces on "
        "two paths is typed with both, and the site is attributed only when both "
        "agree on the method at that slot. %d call sites were rejected for "
        "disagreeing." % attr.conflicts,
        "IDirect3DBaseTexture9 has no table in runtime/sa2_d3d9.c because the runtime "
        "never instantiates one; the slots used here are the prefix that "
        "ROWS_TEXTURE, ROWS_CUBETEX and ROWS_VOLTEX already agree on, and a call "
        "above that prefix on a GetTexture result stays unattributed.",
        "IDirect3DVertexShader9 and IDirect3DPixelShader9 share ROWS_SHADER in the "
        "runtime, so a slot number cannot distinguish them; they are distinguished "
        "here only by which creator produced the pointer.",
        "QueryInterface (slot 0 everywhere) is not treated as a creator, so an "
        "interface obtained through it is untyped and its call sites stay in Layer A.",
        "Function extents come from private/generated/function-map.json as "
        "[entry, next entry); a byte outside every extent is not decoded. Coverage "
        "is reported in inputs.decode_coverage.",
        "A call site whose receiver is a join of two interfaces counts once in "
        "layer_b_attributed.call_sites but once under each interface in "
        "distinct_methods, because either interface would have to be implemented for "
        "that site to work. That inflates the denominator by the join count, which "
        "is layer_b_attributed.distinct_methods minus the number of single-interface "
        "(interface, slot) pairs.",
        "The fixpoint runs under a wall-clock budget (--budget, default 240s). "
        "layer_b_attributed.fixpoint_reached says whether the work list emptied; "
        "when it is false the attributed set is a lower bound on what this same "
        "analysis would reach with more time.",
        "No number here is a claim about what executes. It is a claim about what the "
        "image contains a call site for.",
    ]

    # ---------------------------------------------------------------- falsification
    if not args.no_falsification and args.brk == "none":
        doc["falsification"] = {"why": (
            "Seven checks in this project turned out unable to fail. Before this "
            "number is trusted, the two inputs it depends on most are corrupted on "
            "purpose and the anchor checks are re-run. A check that cannot go red is "
            "not a check.")}
        runs = []
        for label, kwargs, broke in (
            ("shift-displacements", {"disp_shift": 4},
             "every vtable displacement is read four bytes high, so slot D/4 becomes "
             "slot (D+4)/4 and every method name shifts by one"),
            ("no-d3d-seed", {"seed_d3d": False},
             "the Direct3DCreate9 return value is no longer typed IDirect3D9*, so the "
             "IDirect3D9 and IDirect3DDevice9 chains have no root; the d3dx9 texture "
             "seeds are left in place so the collapse is visible rather than total"),
        ):
            broken = analyse(code, ifaces, vtinfo, budget=args.budget / 3, **kwargs)
            bch = check_anchors(broken, a["sites"])
            bsum = summarise(broken, a)
            runs.append({
                "broke": label, "what_it_does": broke,
                "anchors_found": bch["anchors_found"],
                "anchors_total": bch["anchors_total"],
                "attributed_call_sites": bsum["call_sites"],
                "distinct_methods": bsum["distinct_methods"],
                "first_anchor_got": bch["anchors"][1]["got"],
                "went_red": bch["anchors_found"] < checks["anchors_found"],
            })
            flush({"falsification": doc["falsification"] | {"runs": runs}})
        doc["falsification"]["runs"] = runs
        doc["falsification"]["verdict"] = (
            "both corruptions drove the anchor count down from %d; the checks can fail"
            % checks["anchors_found"]
            if all(r["went_red"] for r in runs) else
            "AT LEAST ONE CORRUPTION DID NOT GO RED: the anchor check is not a check")

    ok = checks["anchors_found"] == checks["anchors_total"]
    if checks["anchors_found"] == 0:
        doc["verdict"] = ("SCAN FAILED: zero of %d known-good anchors were found. No "
                          "number in this file should be used."
                          % checks["anchors_total"])
    else:
        doc["verdict"] = (
            "%s %d of %d known-good anchors found. The M7 denominator is %d: %d d3d9 "
            "interface methods with a call site in the image plus %d of the %d "
            "d3dx9_42 imports. The error bar is %d vtable-shaped call sites Layer A "
            "found and Layer B could not attribute (%.1f%% of the census); each is a "
            "method that may be missing from the denominator."
            % ("COMPLETE:" if ok else "PARTIAL:", checks["anchors_found"],
               checks["anchors_total"], doc["denominator"]["total"], d3d9_methods,
               len(called), len(dx),
               doc["layer_a_shape_census"]["unattributed_vtable_call_sites"],
               100.0 * doc["layer_a_shape_census"]["unattributed_vtable_call_sites"]
               / max(1, a["vtable_shaped_call_sites"])))
    flush()

    print(doc["verdict"])
    print("anchors: %d/%d" % (checks["anchors_found"], checks["anchors_total"]))
    for row in checks["anchors"]:
        print("  %s %-8s %-28s %-28s %s" % (
            "OK " if row["found"] else "RED", row["va"],
            row["expect_interface"], row["expect_method"] or "-", row["got"]))
    print("layer A: %d vtable-shaped sites, %d unattributed"
          % (a["vtable_shaped_call_sites"],
             doc["layer_a_shape_census"]["unattributed_vtable_call_sites"]))
    print("layer B: %d call sites, %d distinct methods, %d rounds, %d conflicts"
          % (b["call_sites"], b["distinct_methods"], b["fixpoint_rounds"],
             attr.conflicts))
    print("d3dx9_42: %d/%d imports called" % (len(called), len(dx)))
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
