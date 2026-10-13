#!/usr/bin/env python3
"""Which module handle does each `GetProcAddress` site ask?

Subtitle: the surface receipt counted the 451 dynamic names against the static
import table and found 3 in both. This asks a different question of the same
sites -- where the *handle* came from -- and does not build anything.

`Documentation/receipts/s4-import-surface-v1.json` establishes that the image
resolves 451 distinct names through 708 `GetProcAddress` sites, that the names
are the game's own data symbols, and that "only 3 of the 451 are also static
imports". All of that reproduces. But "is also a static import" and "is an OS
entry point" are not the same predicate, and the first driven run of the bound
image walked straight through two sites where they differ: the MSVC CRT
resolves `EncodePointer` against `GetModuleHandleW(L"KERNEL32.DLL")` at
0x007ac229 and `CorExitProcess` against `GetModuleHandleW(L"mscoree.dll")` at
0x007ac92d. Neither is a static import, because the CRT looks them up
precisely so it can run on a Windows that lacks them -- and neither is a game
data symbol.

So this classifies every site by the provenance of the handle in its own
function: a function that calls `GetModuleHandle*` or `LoadLibrary*` is asking
an OS module, and a function that touches the game's module-handle global is
asking the data DLL. The granularity is the function, not the instruction, and
a function that does both is reported as `both` rather than guessed at.

Output: `private/generated/s4/procaddr_handles.json`.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/procaddr_handles.py [--handle-global 0x01AF0220]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image import Image, FunctionMap  # noqa: E402

import pefile  # noqa: E402
from iced_x86 import Decoder  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SURFACE = ROOT / "Documentation" / "receipts" / "s4-import-surface-v1.json"
OUT = ROOT / "private" / "generated" / "s4" / "procaddr_handles.json"

OS_HANDLE_IMPORTS = ("GetModuleHandleA", "GetModuleHandleW",
                     "LoadLibraryA", "LoadLibraryW", "LoadLibraryExA", "LoadLibraryExW")
# Win32/CRT-shaped names: an OS entry point starts with a verb and is CamelCase.
# The pattern is a *filter for review*, not a verdict, and every name it selects
# is printed so a reader can reject one.
OS_NAME_RE = re.compile(r"^(Get|Set|Is|Create|Enum|Flush|Fls|Tls|Encode|Decode|Heap|Init|Load|"
                        r"Reg|Cor|Query|Raise|Terminate|Interlocked|Virtual|Message|Output)[A-Z]")


def iat_slots() -> dict[int, str]:
    pe = pefile.PE(str(ROOT / "private/exe/sonic2app-unpacked.exe"), fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    out = {}
    for e in pe.DIRECTORY_ENTRY_IMPORT:
        for imp in e.imports:
            out[imp.address] = imp.name.decode() if imp.name else f"Ordinal#{imp.ordinal}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle-global", default="0x01AF0220",
                    help="the game's module-handle global, per the surface journal")
    args = ap.parse_args()
    handle_global = int(args.handle_global, 0)

    surface = json.loads(SURFACE.read_text())
    by_symbol = surface["symbols"]["by_symbol"]
    img, fmap = Image(), FunctionMap()
    slots = iat_slots()
    os_slots = {a for a, n in slots.items() if n in OS_HANDLE_IMPORTS}

    # Cache the classification per function span, because most sites share one.
    span_cache: dict[int, dict] = {}

    def classify_span(entry: int, size: int) -> dict:
        if entry in span_cache:
            return span_cache[entry]
        data = img.read(entry, size) or b""
        calls_os, touches_global, os_names = set(), False, []
        for ins in Decoder(32, data, ip=entry):
            for i in range(ins.op_count):
                pass
            d = ins.memory_displacement if ins.memory_base == 0 and ins.memory_index == 0 else None
            if d is None:
                continue
            if d in os_slots:
                calls_os.add(slots[d])
            if d == handle_global:
                touches_global = True
        rec = {"calls_os_module_lookup": sorted(calls_os), "touches_handle_global": touches_global}
        rec["class"] = ("both" if calls_os and touches_global else
                        "os-module-handle" if calls_os else
                        "game-data-handle" if touches_global else "unattributed")
        span_cache[entry] = rec
        return rec

    rows, classes = [], Counter()
    for name, info in sorted(by_symbol.items()):
        for at in info["at"]:
            va = int(at, 16)
            fn = fmap.function_at(va) or fmap.preceding(va)
            if not fn:
                rows.append({"name": name, "site": at, "class": "no-function-span"})
                classes["no-function-span"] += 1
                continue
            entry = fn["address"] if isinstance(fn["address"], int) else int(fn["address"], 0)
            end = entry + int(fn.get("size", 0) or 0)
            # The surface receipt's third finding: the map's `size` under-reports,
            # so the span is widened to the next entry, as the adopted lift bound does.
            nxt = fmap.following(entry)
            if nxt:
                nend = nxt["address"] if isinstance(nxt["address"], int) else int(nxt["address"], 0)
                end = max(end, min(nend, va + 0x400))
            end = max(end, va + 16)
            rec = classify_span(entry, end - entry)
            rows.append({"name": name, "site": at, "function": fn.get("name"),
                         "entry": f"0x{entry:08x}", **rec})
            classes[rec["class"]] += 1

    os_shaped = sorted(n for n in by_symbol if OS_NAME_RE.match(n))
    static_imports = set(slots.values())
    doc = {
        "schema": "sa2-recomp-measurement/v1",
        "id": "s4-procaddr-handles",
        "question": "where does each GetProcAddress site's module handle come from, and how "
                    "many of the 451 names are OS entry points rather than game data?",
        "inputs": {"surface_receipt": str(SURFACE.relative_to(ROOT)),
                   "handle_global": args.handle_global},
        "sites": len(rows),
        "distinct_names": len(by_symbol),
        "by_class": dict(classes),
        "os_entry_point_names": {
            "also_static_imports": sorted(set(os_shaped) & static_imports),
            "not_static_imports": sorted(set(os_shaped) - static_imports),
            "total_os_shaped": len(os_shaped),
            "surface_receipt_figure": surface["symbols"][
                "distinct_symbols_that_are_also_static_imports"],
            "note": "the receipt's figure counts names that are ALSO STATIC IMPORTS, which is "
                    "a different predicate from 'is an OS entry point'. The CRT resolves "
                    "EncodePointer, DecodePointer, the four Fls* and CorExitProcess at run "
                    "time exactly because they may be absent, so they are OS entry points "
                    "that are deliberately not static imports.",
        },
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1)[:3000])
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
