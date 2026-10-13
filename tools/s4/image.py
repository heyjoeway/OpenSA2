#!/usr/bin/env python3
"""A self-contained 32-bit PE view for the S4 import-surface work.

This deliberately does not import `Documentation/scripts/s1_sweep.py`. The
question S4 asks of the `GetProcAddress` surface is whether the S1 figures the
G1 decision quotes -- 154 game-region direct call sites, 209 in all, 134
pointer loads -- are right, and re-running the code that produced them can only
reproduce them. An independent decode is the only reconciliation worth the
name, and it also keeps the receipt's `inputs.pipeline` pins inside `tools/s4/`,
which this agent owns, instead of pinning files another thread is editing.

What it provides beyond a linear sweep:

  * **Anchored decode.** A linear sweep from the first byte of `.text`
    decodes jump tables, `int3` padding and relocated pointer arrays as
    instructions, and any of that garbage can carry an IAT slot address in a
    displacement field by accident. So every site this module reports is also
    re-decoded from an *independent anchor* -- the entry of the analyzer
    function that contains it -- and a site that is not an instruction boundary
    on that second decode is reported as such rather than silently counted.
  * **Relocation coverage.** `.reloc` names every dword in the image that
    holds an absolute address. A real `call [0x008730D4]` has its displacement
    field relocated; so does a data dword that happens to be `0x008730D4`.
    Coverage alone proves nothing, but its *absence* at a claimed site proves
    the site is not a real absolute reference, so it is checked and reported.

Everything is sorted by address; nothing here is random.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pefile
from iced_x86 import Decoder, FlowControl, Formatter, FormatterSyntax, Instruction, Mnemonic, OpKind, Register

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
DEFAULT_MAP = ROOT / "private" / "generated" / "function-map.json"

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000

REGION_ORDER = ("game", "crt", "cri", "d3dx", "other", "unmapped", "unpartitioned")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path: Path | str) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def region_sort_key(region: str):
    return (REGION_ORDER.index(region) if region in REGION_ORDER else len(REGION_ORDER), region)


@dataclass(frozen=True)
class Section:
    name: str
    va: int
    vsize: int
    raw_size: int
    characteristics: int

    @property
    def executable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_EXECUTE)

    @property
    def readable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_READ)

    @property
    def writable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_WRITE)

    @property
    def end(self) -> int:
        return self.va + self.vsize

    def contains(self, addr: int) -> bool:
        return self.va <= addr < self.end

    def describe(self) -> dict:
        flags = "".join(f for f, on in (("r", self.readable), ("w", self.writable),
                                        ("x", self.executable)) if on)
        return {"name": self.name, "va": hex(self.va), "virtual_size": self.vsize,
                "raw_size": self.raw_size, "flags": flags}


_REGISTER_NAME = {value: name for name, value in vars(Register).items()
                  if isinstance(value, int) and not name.startswith("_")}


def register_name(reg: int) -> str | None:
    """`Register` is a module of int constants in the Python bindings, with no
    name lookup of its own; this is that lookup."""
    return _REGISTER_NAME.get(reg)


def abs_displacement(instr: Instruction) -> int | None:
    """The absolute address of an `[imm32]` memory operand (no base, no index), else None."""
    for k in range(instr.op_count):
        if (instr.op_kind(k) == OpKind.MEMORY
                and instr.memory_base == Register.NONE
                and instr.memory_index == Register.NONE):
            return instr.memory_displacement
    return None


def immediates(instr: Instruction) -> list[int]:
    """Every 32-bit immediate operand value of the instruction."""
    out = []
    for k in range(instr.op_count):
        kind = instr.op_kind(k)
        if kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE32TO64, OpKind.IMMEDIATE8TO32):
            out.append(instr.immediate(k) & 0xFFFFFFFF)
    return out


class Image:
    """Sections, imports, relocations, a linear sweep and an anchored re-decode."""

    def __init__(self, path: Path = DEFAULT_EXE) -> None:
        self.path = Path(path)
        self.sha256 = sha256_of(self.path)
        self.pe = pefile.PE(str(self.path))
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = [
            Section(s.Name.rstrip(b"\0").decode("ascii", "replace"),
                    self.base + s.VirtualAddress, s.Misc_VirtualSize,
                    s.SizeOfRawData, s.Characteristics)
            for s in self.pe.sections]
        self.exec_sections = [s for s in self.sections if s.executable]

        # imports: IAT slot VA -> (dll, name)
        self.imports: dict[int, tuple[str, str]] = {}
        for entry in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = entry.dll.decode("ascii", "replace")
            for imp in entry.imports:
                name = (imp.name.decode("ascii", "replace") if imp.name
                        else f"{dll}!ordinal{imp.ordinal}")
                self.imports[imp.address] = (dll, name)

        # relocated dwords
        self.relocated: set[int] = set()
        for block in getattr(self.pe, "DIRECTORY_ENTRY_BASERELOC", []):
            for entry in block.entries:
                if entry.type in (3, 10):       # HIGHLOW, DIR64
                    self.relocated.add(self.base + entry.rva)

        # linear sweep of every executable section
        self.instructions: list[Instruction] = []
        self._section_bytes: dict[str, bytes] = {}
        for sec in self.exec_sections:
            data = self.pe.get_data(sec.va - self.base, sec.vsize)
            self._section_bytes[sec.name] = data
            self.instructions.extend(Decoder(32, data, ip=sec.va))
        self.index = {ins.ip: k for k, ins in enumerate(self.instructions)}

        # `jmp [slot]` thunks
        self.thunks: dict[int, int] = {}    # thunk VA -> IAT slot VA
        for ins in self.instructions:
            if ins.mnemonic == Mnemonic.JMP:
                disp = abs_displacement(ins)
                if disp is not None and disp in self.imports:
                    self.thunks[ins.ip] = disp

        self._formatter = Formatter(FormatterSyntax.NASM)

    # --- addresses -------------------------------------------------------
    def section_of(self, addr: int) -> Section | None:
        return next((s for s in self.sections if s.contains(addr)), None)

    def in_code(self, addr: int) -> bool:
        return any(s.contains(addr) for s in self.exec_sections)

    def read(self, va: int, n: int) -> bytes | None:
        try:
            return self.pe.get_data(va - self.base, n)
        except (pefile.PEFormatError, ValueError):
            return None

    def read_u32(self, va: int) -> int | None:
        data = self.read(va, 4)
        return None if data is None or len(data) < 4 else int.from_bytes(data, "little")

    def cstring(self, va: int, limit: int = 128) -> str | None:
        """A NUL-terminated printable-ASCII string at `va` in a readable, non-executable
        section, else None. Symbol names are the only strings this project reads out of
        the image, and it reads them as text, never as bytes to be carried anywhere."""
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

    def text(self, ins: Instruction) -> str:
        return self._formatter.format(ins)

    # --- anchored re-decode -----------------------------------------------
    def decode_from(self, anchor: int, upto: int) -> list[int]:
        """Instruction start addresses produced by decoding forward from `anchor`
        until `upto` is reached or passed. An empty list when `anchor` is not in a
        decodable executable section."""
        sec = self.section_of(anchor)
        if sec is None or not sec.executable or not (sec.va <= upto < sec.end):
            return []
        data = self._section_bytes.get(sec.name)
        if data is None:
            return []
        decoder = Decoder(32, data[anchor - sec.va:], ip=anchor)
        starts = []
        for ins in decoder:
            starts.append(ins.ip)
            if ins.ip >= upto:
                break
            if ins.ip > upto:
                break
        return starts

    def boundary_from(self, anchor: int, addr: int) -> bool:
        """True when a decode started at `anchor` lands exactly on `addr`."""
        return addr in set(self.decode_from(anchor, addr))

    def at(self, addr: int) -> int | None:
        return self.index.get(addr)


class FunctionMap:
    """`private/generated/function-map.json`, by address and by region."""

    def __init__(self, path: Path = DEFAULT_MAP) -> None:
        self.path = Path(path)
        self.present = self.path.is_file()
        self.sha256 = sha256_of(self.path) if self.present else None
        self.functions: list[dict] = []
        self.doc: dict = {}
        if self.present:
            self.doc = json.loads(self.path.read_text(encoding="utf-8"))
            for f in self.doc["functions"]:
                addr = f["address"]
                addr = int(addr, 0) if isinstance(addr, str) else int(addr)
                self.functions.append({
                    "address": addr,
                    "size": int(f.get("size", 0) or 0),
                    "name": f.get("name", f"sub_{addr:08X}"),
                    "region": str(f.get("region", "other")),
                    "import_calls": list(f.get("import_calls") or []),
                })
            self.functions.sort(key=lambda f: f["address"])
        self._starts = [f["address"] for f in self.functions]

    def function_at(self, addr: int) -> dict | None:
        k = bisect.bisect_right(self._starts, addr) - 1
        if k < 0:
            return None
        f = self.functions[k]
        if f["size"] and addr >= f["address"] + f["size"]:
            return None
        return f

    def preceding(self, addr: int) -> dict | None:
        k = bisect.bisect_right(self._starts, addr) - 1
        return self.functions[k] if k >= 0 else None

    def following(self, addr: int) -> dict | None:
        k = bisect.bisect_right(self._starts, addr)
        return self.functions[k] if k < len(self.functions) else None

    def region_of(self, addr: int) -> str:
        if not self.present:
            return "unpartitioned"
        f = self.function_at(addr)
        return f["region"] if f else "unmapped"

    def by_region(self, addrs) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for a in addrs:
            counts[self.region_of(a)] += 1
        return {r: counts[r] for r in sorted(counts, key=region_sort_key)}

    def functions_calling(self, symbol_suffix: str) -> list[dict]:
        """Functions whose analyzer `import_calls` name this import: the analyzer's
        own recursive-descent view, independent of any sweep done here."""
        suffix = "!" + symbol_suffix.lower()
        return [f for f in self.functions
                if any(str(c).lower().endswith(suffix) for c in f["import_calls"])]

    def describe(self) -> dict:
        if not self.present:
            return {"path": rel(self.path), "present": False}
        return {"path": rel(self.path), "present": True, "sha256": self.sha256,
                "functions": len(self.functions),
                "regions": self.by_region(f["address"] for f in self.functions)}
