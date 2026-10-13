#!/usr/bin/env python3
"""Shared iced-x86 linear sweep for the S1 measurement scripts.

One `Image` holds the decoded executable sections of a 32-bit PE, the import
address table, the `jmp [slot]` thunks, and the direct-call graph the three
S1 scripts (`s1_exception_handling.py`, `s1_threads.py`,
`s1_dynamic_code_tls.py`) all need. `FunctionMap` reads the analyzer's
`private/generated/function-map.json` when it exists and partitions any list
of addresses by region; when it is absent every count lands in the single
region "unpartitioned" and the receipt says so.

Linear sweep, not recursive descent: every executable section is decoded
from its first byte to its last in one pass, so data embedded in `.text`
(jump tables, the `int 3` padding) decodes as garbage instructions. The
patterns the S1 scripts count are multi-instruction and anchored on
operands (an FS segment prefix, an IAT slot, an immediate inside a code
section), and a garbage decode that reproduces one of them by accident is
recorded as a known limit in each receipt's method paragraph rather than
filtered by a heuristic that could hide a real hit.

Determinism: every list is sorted by address; the only non-reproducible
field in a receipt is the date.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import hashlib
import importlib.metadata
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pefile
from iced_x86 import Decoder, FlowControl, Instruction, Mnemonic, OpKind, Register

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
DEFAULT_MAP = ROOT / "private" / "generated" / "function-map.json"
PINS = ROOT / "tools" / "pins.json"
SCHEMA = "sa2-recomp-receipt/v1"
REGION_ORDER = ("game", "crt", "cri", "d3dx", "other", "unmapped", "unpartitioned")

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000


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
    def writable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_WRITE)

    @property
    def readable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_READ)

    @property
    def end(self) -> int:
        return self.va + self.vsize

    def contains(self, addr: int) -> bool:
        return self.va <= addr < self.end

    def describe(self) -> dict:
        flags = "".join(f for f, on in (("r", self.readable), ("w", self.writable), ("x", self.executable)) if on)
        return {"name": self.name, "va": hex(self.va), "virtual_size": self.vsize,
                "raw_size": self.raw_size, "characteristics": hex(self.characteristics), "flags": flags}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def abs_displacement(instr: Instruction) -> int | None:
    """The absolute address of a `[imm32]` memory operand (no base, no index), else None."""
    for k in range(instr.op_count):
        if instr.op_kind(k) == OpKind.MEMORY and instr.memory_base == Register.NONE \
                and instr.memory_index == Register.NONE:
            return instr.memory_displacement
    return None


def pushed_imm32(instr: Instruction) -> int | None:
    """The value of a `push imm32` / `push imm8` (sign-extended to 32 bits), else None."""
    if instr.mnemonic != Mnemonic.PUSH or instr.op0_kind not in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
        return None
    return instr.immediate(0) & 0xFFFFFFFF


def is_direct_call(instr: Instruction) -> bool:
    return instr.mnemonic == Mnemonic.CALL and instr.op0_kind == OpKind.NEAR_BRANCH32


class Image:
    """A decoded 32-bit PE: sections, IAT, thunks, direct-call graph, linear sweep."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.sha256 = sha256_of(self.path)
        self.pe = pefile.PE(str(self.path))
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = [Section(s.Name.rstrip(b"\0").decode("ascii", "replace"),
                                 self.base + s.VirtualAddress, s.Misc_VirtualSize, s.SizeOfRawData,
                                 s.Characteristics) for s in self.pe.sections]
        self.exec_sections = [s for s in self.sections if s.executable]
        self.imports: dict[int, str] = {}       # IAT slot VA -> import name
        self.import_dll: dict[str, str] = {}    # import name -> DLL
        for entry in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = entry.dll.decode("ascii", "replace")
            for imp in entry.imports:
                name = imp.name.decode("ascii", "replace") if imp.name else f"{dll}!ordinal{imp.ordinal}"
                self.imports[imp.address] = name
                self.import_dll[name] = dll
        self.instructions: list[Instruction] = []
        for sec in self.exec_sections:
            data = self.pe.get_data(sec.va - self.base, sec.vsize)
            self.instructions.extend(Decoder(32, data, ip=sec.va))
        self.index = {ins.ip: k for k, ins in enumerate(self.instructions)}
        self.thunks: dict[int, str] = {}            # `jmp [slot]` address -> import name
        self.direct_calls: dict[int, list[int]] = defaultdict(list)  # target -> call sites
        self._import_uses: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ins in self.instructions:
            disp = abs_displacement(ins)
            if disp is not None and disp in self.imports:
                name = self.imports[disp]
                if ins.mnemonic == Mnemonic.JMP:
                    self.thunks[ins.ip] = name
                    self._import_uses[name].append((ins.ip, "jmp"))
                elif ins.mnemonic == Mnemonic.CALL:
                    self._import_uses[name].append((ins.ip, "call"))
                else:
                    self._import_uses[name].append((ins.ip, "other"))
            if is_direct_call(ins):
                self.direct_calls[ins.near_branch32].append(ins.ip)
        for ins in self.instructions:
            if is_direct_call(ins) and ins.near_branch32 in self.thunks:
                self._import_uses[self.thunks[ins.near_branch32]].append((ins.ip, "call"))
        self.call_targets = sorted(self.direct_calls)

    # --- addresses -------------------------------------------------------
    def in_code(self, addr: int) -> bool:
        return any(s.contains(addr) for s in self.exec_sections)

    def section_of(self, addr: int) -> Section | None:
        return next((s for s in self.sections if s.contains(addr)), None)

    def read_u32(self, va: int) -> int | None:
        try:
            return int.from_bytes(self.pe.get_data(va - self.base, 4), "little")
        except pefile.PEFormatError:
            return None

    def at(self, addr: int) -> int | None:
        """Sweep position of the instruction decoded at `addr`, or None if no instruction starts there."""
        return self.index.get(addr)

    def nearest_call_target(self, addr: int) -> int | None:
        """The closest direct-call target at or before `addr`: the map-less stand-in for a function entry."""
        k = bisect.bisect_right(self.call_targets, addr) - 1
        return self.call_targets[k] if k >= 0 else None

    # --- imports -----------------------------------------------------------
    def import_uses(self, name: str) -> list[tuple[int, str]]:
        """(address, kind) for every instruction referencing the import: kind is call, jmp (thunk) or other."""
        return sorted(self._import_uses.get(name, []))

    def import_call_sites(self, name: str) -> list[int]:
        """`call [slot]` and `call thunk` sites of one import, sorted."""
        return sorted(a for a, kind in self._import_uses.get(name, []) if kind == "call")

    def imports_matching(self, pattern) -> list[str]:
        return sorted(n for n in self.import_dll if pattern.search(n))

    # --- windows -------------------------------------------------------------
    def pushed_arguments(self, pos: int, limit: int = 10) -> list[int | None]:
        """Immediates pushed before the instruction at sweep position `pos`, in push order
        (last element = the push nearest the call = the first stack argument). A push of a
        register or memory operand contributes None; the walk stops at the previous call, ret
        or jump, or after `limit` instructions."""
        pushes: list[int | None] = []
        for j in range(1, limit + 1):
            if pos - j < 0:
                break
            ins = self.instructions[pos - j]
            if ins.mnemonic in (Mnemonic.CALL, Mnemonic.RET, Mnemonic.JMP) or ins.flow_control == FlowControl.CONDITIONAL_BRANCH:
                break
            if ins.mnemonic == Mnemonic.PUSH:
                pushes.append(pushed_imm32(ins))
        pushes.reverse()
        return pushes


class FunctionMap:
    """`private/generated/function-map.json`: functions[{address,size,name,region}]."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path else None
        self.present = bool(self.path and self.path.is_file())
        self.sha256 = sha256_of(self.path) if self.present else None
        self.functions: list[dict] = []
        if self.present:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            raw = doc["functions"] if isinstance(doc, dict) else doc
            for f in raw:
                addr = f["address"]
                addr = int(addr, 0) if isinstance(addr, str) else int(addr)
                self.functions.append({"address": addr, "size": int(f.get("size", 0) or 0),
                                       "name": f.get("name", f"sub_{addr:08X}"),
                                       "region": str(f.get("region", "other"))})
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

    def region_of(self, addr: int) -> str:
        if not self.present:
            return "unpartitioned"
        f = self.function_at(addr)
        return f["region"] if f else "unmapped"

    def neighbour_region(self, addr: int) -> str | None:
        """Region of the nearest mapped function at or before `addr`, for an address the map does
        not cover (a thread start routine only reached through a pointer, say)."""
        if not self.present or not self._starts:
            return None
        k = bisect.bisect_right(self._starts, addr) - 1
        return self.functions[k]["region"] if k >= 0 else None

    def enclosing(self, image: Image, addr: int) -> dict:
        """The function holding `addr`: from the map when present, else the nearest preceding
        direct-call target (the 9297-entry lower bound the S1 protocol names)."""
        if self.present:
            f = self.function_at(addr)
            if f:
                return {"entry": f["address"], "name": f["name"], "region": f["region"], "source": "function-map"}
        entry = image.nearest_call_target(addr)
        return {"entry": entry, "name": None if entry is None else f"sub_{entry:08X}",
                "region": self.region_of(addr), "source": "nearest-direct-call-target"}

    def by_region(self, addrs) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for a in addrs:
            counts[self.region_of(a)] += 1
        return {r: counts[r] for r in sorted(counts, key=region_sort_key)}

    def describe(self) -> dict:
        if not self.present:
            return {"path": rel(self.path) if self.path else None, "present": False, "region": "unpartitioned",
                    "note": "function map absent: every count is whole-image; re-run with --function-map once it lands"}
        return {"path": rel(self.path), "present": True, "sha256": self.sha256, "functions": len(self.functions),
                "regions": self.by_region(f["address"] for f in self.functions)}


def region_sort_key(region: str):
    return (REGION_ORDER.index(region) if region in REGION_ORDER else len(REGION_ORDER), region)


def counted(fmap: FunctionMap, addrs) -> dict:
    addrs = sorted(addrs)
    return {"total": len(addrs), "per_region": fmap.by_region(addrs)}


def format_counts(label: str, c: dict) -> str:
    parts = ", ".join(f"{r} {n}" for r, n in c["per_region"].items())
    return f"{label}: {c['total']} ({parts})"


def rel(path: Path | str) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def command_line() -> str:
    argv = [rel(a) if Path(a).exists() or a.endswith(".json") else a for a in sys.argv[1:]]
    return " ".join([".venv/bin/python", rel(sys.argv[0])] + argv)


def tool_versions() -> dict:
    pins = json.loads(PINS.read_text(encoding="utf-8"))["tools"] if PINS.is_file() else {}
    return {
        "python": {"pinned": pins.get("python", {}).get("version"), "observed": sys.version.split()[0]},
        "pefile": {"pinned": pins.get("pefile", {}).get("version"), "observed": importlib.metadata.version("pefile")},
        "iced-x86": {"pinned": pins.get("iced-x86", {}).get("version"), "observed": importlib.metadata.version("iced-x86")},
    }


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--exe", type=Path, default=DEFAULT_EXE, help="32-bit PE to sweep (default: the unpacked executable)")
    p.add_argument("--function-map", type=Path, default=DEFAULT_MAP,
                   help="analyzer function map; counts are per region when it exists, whole-image otherwise")
    p.add_argument("--out", type=Path, required=True, help="receipt path to write")
    return p


def receipt(image: Image, fmap: FunctionMap, ident: str, question: str, method: str, outputs: dict,
            limits: list[str]) -> dict:
    return {
        "schema": SCHEMA,
        "id": ident,
        "date": _dt.date.today().isoformat(),
        "question": question,
        "inputs": {
            "exe": rel(image.path),
            "exe_sha256": image.sha256,
            "image_base": hex(image.base),
            "executable_sections": [s.describe() for s in image.exec_sections],
            "instructions_decoded": len(image.instructions),
            "function_map": fmap.describe(),
            "tools": tool_versions(),
        },
        "command": command_line(),
        "method": method,
        "outputs": outputs,
        "limits": limits,
        "authority": "statically-translated",
    }


def write_receipt(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
