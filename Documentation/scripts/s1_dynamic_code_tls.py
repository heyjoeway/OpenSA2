#!/usr/bin/env python3
"""S1 receipt 5: dynamic code and TLS surface of the unpacked executable (design S1, expected: none).

    .venv/bin/python Documentation/scripts/s1_dynamic_code_tls.py \
        --out Documentation/receipts/s1-dynamic-code-tls-v1.json

Checks: writable+executable sections; VirtualAlloc / VirtualProtect (and
their Ex forms) call sites and the protection immediate pushed at each
(PAGE_EXECUTE* is any of the 0xF0 bits); FlushInstructionCache and
WriteProcessMemory imports; the TLS directory (callbacks, index address);
FS-relative accesses by displacement (0x18 TEB self, 0x2C TLS array, 0x0
SEH head); `__tls_index`-shaped sequences (an FS:[2Ch] read followed within
four instructions by a `[reg + reg*4]` load); and instructions that write
through an absolute address inside an executable section (self-modifying
code). The verdict is "dynamic code: none found" or the list of hits.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s1_sweep as sw  # noqa: E402
from iced_x86 import InstructionInfoFactory, OpAccess, OpKind, Register  # noqa: E402

PAGE_EXECUTE_MASK = 0xF0  # PAGE_EXECUTE 0x10, _READ 0x20, _READWRITE 0x40, _WRITECOPY 0x80
PROTECT_ARG = {"VirtualAlloc": 4, "VirtualAllocEx": 5, "VirtualProtect": 3, "VirtualProtectEx": 4}
OTHER_DYNAMIC_IMPORTS = ("FlushInstructionCache", "WriteProcessMemory", "VirtualAllocExNuma", "HeapCreate")
TLS_IMPORTS = re.compile(r"^Tls(Alloc|Free|GetValue|SetValue)$")
WRITE_ACCESS = (OpAccess.WRITE, OpAccess.READ_WRITE, OpAccess.COND_WRITE, OpAccess.READ_COND_WRITE)
TLS_INDEX_WINDOW = 4


def sections(image: sw.Image) -> dict:
    rows = [s.describe() for s in image.sections]
    return {"sections": rows, "writable_and_executable": [r["name"] for r in rows if "w" in r["flags"] and "x" in r["flags"]]}


def protection_calls(image: sw.Image, fmap: sw.FunctionMap) -> list[dict]:
    """Every call site of the VirtualAlloc/VirtualProtect family with its protection immediate."""
    rows = []
    for name, arg in PROTECT_ARG.items():
        if name not in image.import_dll:
            continue
        for call in image.import_call_sites(name):
            pos = image.at(call)
            pushes = image.pushed_arguments(pos) if pos is not None else []
            protect = pushes[-arg] if len(pushes) >= arg else None
            rows.append({"import": name, "call": hex(call), "region": fmap.region_of(call),
                         "pushed_immediates": [None if p is None else hex(p) for p in pushes],
                         "protect": None if protect is None else hex(protect),
                         "execute_flag": None if protect is None else bool(protect & PAGE_EXECUTE_MASK)})
    return rows


def fs_accesses(image: sw.Image, fmap: sw.FunctionMap) -> dict:
    by_disp: dict = {}
    other = []
    for ins in image.instructions:
        if ins.segment_prefix != Register.FS:
            continue
        disp = sw.abs_displacement(ins)
        if disp is None:
            other.append(ins.ip)
        else:
            by_disp.setdefault(disp, []).append(ins.ip)
    return {"by_displacement": {hex(d): sw.counted(fmap, a) for d, a in sorted(by_disp.items())},
            "with_base_or_index_register": sw.counted(fmap, other)}


def tls_index_sequences(image: sw.Image) -> list[int]:
    """`mov reg, fs:[2Ch]` followed within TLS_INDEX_WINDOW instructions by a `[base + index*4]` load."""
    hits = []
    for pos, ins in enumerate(image.instructions):
        if ins.segment_prefix != Register.FS or sw.abs_displacement(ins) != 0x2C:
            continue
        for j in range(1, TLS_INDEX_WINDOW + 1):
            if pos + j >= len(image.instructions):
                break
            nxt = image.instructions[pos + j]
            if nxt.memory_base != Register.NONE and nxt.memory_index != Register.NONE and nxt.memory_index_scale == 4:
                hits.append(ins.ip)
                break
    return hits


def code_writes(image: sw.Image) -> list[dict]:
    """Instructions that write memory through an absolute address inside an executable section."""
    factory = InstructionInfoFactory()
    hits = []
    for ins in image.instructions:
        disp = sw.abs_displacement(ins)
        if disp is None or not image.in_code(disp) or ins.op0_kind != OpKind.MEMORY:
            continue
        # match the absolute operand itself: `call [slot]` also lists its stack push as a write.
        if any(m.access in WRITE_ACCESS and m.base == Register.NONE and m.index == Register.NONE
               and m.displacement == disp for m in factory.info(ins).used_memory()):
            hits.append({"address": ins.ip, "target": disp})
    return hits


def tls_directory(image: sw.Image) -> dict:
    tls = getattr(image.pe, "DIRECTORY_ENTRY_TLS", None)
    if tls is None:
        return {"present": False, "callbacks": 0, "index_address": None}
    s = tls.struct
    callbacks = 0
    p = s.AddressOfCallBacks
    while p:
        cb = image.read_u32(p)
        if not cb:
            break
        callbacks += 1
        p += 4
    return {"present": True, "callbacks": callbacks, "index_address": hex(s.AddressOfIndex),
            "raw_data": [hex(s.StartAddressOfRawData), hex(s.EndAddressOfRawData)]}


def analyse(image: sw.Image, fmap: sw.FunctionMap) -> dict:
    sec = sections(image)
    prot = protection_calls(image, fmap)
    writes = code_writes(image)
    seqs = tls_index_sequences(image)
    fs = fs_accesses(image, fmap)
    imports_present = {n: (n in image.import_dll) for n in list(PROTECT_ARG) + list(OTHER_DYNAMIC_IMPORTS)}
    other_sites = {n: sw.counted(fmap, image.import_call_sites(n)) for n in OTHER_DYNAMIC_IMPORTS if n in image.import_dll}
    tls_imports = {n: sw.counted(fmap, image.import_call_sites(n)) for n in image.imports_matching(TLS_IMPORTS)}
    findings = []
    findings += [f"section {n} is writable and executable" for n in sec["writable_and_executable"]]
    findings += [f"{r['import']} at {r['call']} with PAGE_EXECUTE* protection {r['protect']}" for r in prot if r["execute_flag"]]
    findings += [f"{n} imported" for n in OTHER_DYNAMIC_IMPORTS if imports_present[n] and n != "HeapCreate"]
    findings += [f"write into code at {hex(w['address'])} -> {hex(w['target'])}" for w in writes]
    return {
        **sec,
        "dynamic_imports_present": imports_present,
        "protection_call_sites": prot,
        "protection_call_sites_with_execute_flag": sum(1 for r in prot if r["execute_flag"]),
        "protection_call_sites_with_unknown_protect": sum(1 for r in prot if r["protect"] is None),
        "other_dynamic_import_call_sites": other_sites,
        "tls_directory": tls_directory(image),
        "tls_import_call_sites": tls_imports,
        "fs_accesses": fs,
        "fs_0x2c_tls_array": fs["by_displacement"].get("0x2c", {"total": 0, "per_region": {}}),
        "fs_0x18_teb_self": fs["by_displacement"].get("0x18", {"total": 0, "per_region": {}}),
        "tls_index_shaped_sequences": sw.counted(fmap, seqs),
        "writes_into_code": sw.counted(fmap, [w["address"] for w in writes]),
        "writes_into_code_sites": [{"address": hex(w["address"]), "target": hex(w["target"])} for w in writes[:50]],
        "verdict": "dynamic code: none found" if not findings else findings,
    }


METHOD = (
    "pefile reads the section table (IMAGE_SCN_MEM_WRITE 0x80000000 and IMAGE_SCN_MEM_EXECUTE 0x20000000 "
    "together mark a section writable+executable), the import table, the TLS directory (callbacks are "
    "counted by walking AddressOfCallBacks to its null terminator) and the load config. A linear "
    "iced-x86 sweep of every executable section finds call sites of VirtualAlloc / VirtualAllocEx / "
    "VirtualProtect / VirtualProtectEx (`call [slot]` or `call thunk`); at each, the pushes since the "
    "previous call, return or branch are read in stack order and the protection argument (4th, 5th, 3rd, "
    "4th respectively) is flagged when any PAGE_EXECUTE* bit (0xF0) is set; a non-immediate protection is "
    "reported as unknown. FS-relative accesses are grouped by absolute displacement (0x0 SEH head, 0x18 TEB "
    "self, 0x2C TLS array); a `__tls_index`-shaped access is an FS:[2Ch] read followed within four "
    "instructions by a `[base + index*4]` operand. A write into code is an instruction whose first operand "
    "is memory with no base or index register, whose displacement is inside an executable section, and "
    "whose iced-x86 InstructionInfo reports a write access. Regions come from the function map when present."
)

LIMITS = [
    "Protection flags computed in a register (or loaded from memory) are 'unknown', not 'no execute'.",
    "Writes into code through a register pointer (`mov [eax], ...` with eax pointing at .text) or via "
    "rep movs / stos are invisible to an absolute-displacement scan; the section flags and the absence of "
    "VirtualProtect are the guard for that case.",
    "Linear sweep: data inside a code section decodes as instructions and could contribute a false FS or "
    "absolute-write hit; hits are listed by address so a reviewer can check them.",
    "Code generated by a DLL the executable loads (d3dx9_42 shaders, Steam overlay) is out of scope: this "
    "measures the executable image only.",
]


def main(argv=None) -> int:
    args = sw.base_parser(__doc__.splitlines()[0]).parse_args(argv)
    image = sw.Image(args.exe)
    fmap = sw.FunctionMap(args.function_map)
    outputs = analyse(image, fmap)
    doc = sw.receipt(image, fmap, "s1-dynamic-code-tls-v1",
                     "Does the executable generate or modify code at run time, and how does it use TLS? "
                     "(design S1 measurement, expected: none)", METHOD, outputs, LIMITS)
    sw.write_receipt(args.out, doc)
    o = outputs
    print("s1-dynamic-code-tls: sections " + ", ".join(f"{s['name']} {s['flags'] or '-'}" for s in o["sections"])
          + f"; writable+executable {o['writable_and_executable']}")
    print(f"s1-dynamic-code-tls: dynamic imports present {[n for n, p in o['dynamic_imports_present'].items() if p]}; "
          f"absent {[n for n, p in o['dynamic_imports_present'].items() if not p]}")
    print("s1-dynamic-code-tls: protection call sites " + "; ".join(
        f"{r['import']} {r['call']} ({r['region']}) protect {r['protect']} execute {r['execute_flag']}" for r in o["protection_call_sites"])
          + f"; with execute flag {o['protection_call_sites_with_execute_flag']}, unknown protect {o['protection_call_sites_with_unknown_protect']}")
    print(f"s1-dynamic-code-tls: TLS directory {o['tls_directory']}; Tls* import call sites "
          + ", ".join(f"{n} {c['total']}" for n, c in o["tls_import_call_sites"].items()))
    print("s1-dynamic-code-tls: FS accesses by displacement "
          + ", ".join(f"{d} {sw.format_counts('', c)[2:]}" for d, c in o["fs_accesses"]["by_displacement"].items())
          + f"; with base/index register {o['fs_accesses']['with_base_or_index_register']['total']}")
    print(f"s1-dynamic-code-tls: {sw.format_counts('__tls_index-shaped sequences', o['tls_index_shaped_sequences'])}")
    print(f"s1-dynamic-code-tls: {sw.format_counts('writes into code', o['writes_into_code'])}")
    print(f"s1-dynamic-code-tls: verdict {o['verdict']}")
    print(f"s1-dynamic-code-tls: receipt {sw.rel(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
