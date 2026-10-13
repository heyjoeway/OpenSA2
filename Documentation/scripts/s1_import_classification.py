#!/usr/bin/env python3
"""S1 receipt 6: the executable's imports classified, with static call sites.

Reads the import table of the unpacked executable with pefile, classifies
every `dll!name` into one of the design 3.5 classes (os-service,
presentation, steam, drm, other) with a finer subclass, and counts the
static references to each import's IAT slot in `.text` with iced-x86:

- `call [slot]` (direct call through the IAT);
- `jmp [slot]` (an import thunk; the linker emits one when a caller lacks
  `__declspec(dllimport)`), plus every direct `call rel32` and tail-call
  `jmp rel32` whose target is such a thunk, so the thunk's callers count
  toward the import;
- any other instruction naming the slot as an absolute memory operand
  (`mov reg,[slot]`, `push [slot]`, `cmp [slot],0`), or naming a thunk's
  address as a 32-bit immediate (`push offset thunk`, a WNDPROC in a
  WNDCLASS): a function-pointer load, not a call.

`static_call_sites` = direct calls + calls and jumps to thunks; it is the static
upper bound the M4 "reachable" work later compares its replay logs
against. An import with zero static call sites and zero pointer loads is
unreferenced from `.text`; one with pointer loads only is reached, if at
all, through a function pointer.

Writes two files:

- `Documentation/receipts/s1-import-classification-v1.json` (schema
  sa2-recomp-receipt/v1): the full record per import.
- `Documentation/receipts/import-classification.json`: the M4 input
  `metrics.py` reads, `imports[{dll, name, class}]` (plus subclass), and
  nothing else that could drift from the receipt: both are written by
  this script from the same list.

With `--function-map` (default `private/generated/function-map.json`,
used when it exists) the direct call sites of each import are also counted
per region; without it the per-region field reads `unpartitioned`.

Limits: the sweep is linear (see s1_opcode_histogram.py): data-in-code can
hide or fabricate a reference, so the counts are a static bound, not a
proof; the second executable section `PSFD00` is not swept; imports by
ordinal (DSOUND, XINPUT1_3) carry a `conventional_name` taken from the
public export ordinals of those DLLs, which are not part of the input.

Usage
-----

    .venv/bin/python Documentation/scripts/s1_import_classification.py \
        [--exe private/exe/sonic2app-unpacked.exe] \
        [--function-map private/generated/function-map.json] \
        [--out Documentation/receipts/s1-import-classification-v1.json] \
        [--metrics-out Documentation/receipts/import-classification.json] \
        [--date YYYY-MM-DD]

Prints summary lines prefixed `S1_IMPORTS`, quoted verbatim by the journal.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib.metadata
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import iced_x86 as ix
import pefile

SCHEMA = "sa2-recomp-receipt/v1"
RECEIPT_ID = "s1-import-classification-v1"
DEFAULT_EXE = "private/exe/sonic2app-unpacked.exe"
DEFAULT_MAP = "private/generated/function-map.json"
DEFAULT_OUT = "Documentation/receipts/s1-import-classification-v1.json"
DEFAULT_METRICS_OUT = "Documentation/receipts/import-classification.json"
PINS = "tools/pins.json"
SECTION = ".text"
DESIGN_IMPORT_COUNT = 251
# Design section 2.3's DLL list (names as the import table spells them, lower-cased).
DESIGN_DLLS = ("d3d9", "d3dx9_42", "steam_api", "steammetricssdk", "kernel32", "user32",
               "gdi32", "advapi32", "shell32", "shlwapi", "dsound", "xinput1_3", "dinput8",
               "ole32", "oleaut32", "winmm")
DESIGN_2_3_DIRECT_CALL_TARGETS = 9297
RESYNC_ALIGNMENT = 16
UNPARTITIONED = "unpartitioned"
UNMAPPED = "unmapped"

CLASSES = ("os-service", "presentation", "steam", "drm", "other")

# --------------------------------------------------------------------------
# Classification tables (design 3.5). Class by DLL, subclass by name where
# a DLL mixes services; every kernel32 name must appear in a group.

DLL_CLASS: dict[str, tuple[str, str]] = {
    "d3d9": ("presentation", "graphics"),
    "d3dx9_42": ("presentation", "graphics-d3dx"),
    "dsound": ("presentation", "audio"),
    "dinput8": ("presentation", "input"),
    "xinput1_3": ("presentation", "input"),
    "winmm": ("presentation", "time"),
    "gdi32": ("presentation", "gdi"),
    "user32": ("presentation", "window"),
    "steam_api": ("steam", "steam-api"),
    "steammetricssdk": ("steam", "steam-metrics"),
    "advapi32": ("os-service", "registry"),
    "shell32": ("os-service", "process"),
    "shlwapi": ("os-service", "files"),
    "ole32": ("os-service", "com"),
    "oleaut32": ("os-service", "memory"),
}

KERNEL32_GROUPS: dict[str, tuple[str, ...]] = {
    "files": ("CloseHandle", "CreateDirectoryA", "CreateFileA", "CreateFileW", "DeleteFileA",
              "FindClose", "FindFirstFileA", "FindNextFileA", "FlushFileBuffers",
              "GetCurrentDirectoryA", "GetFileAttributesA", "GetFileSize", "GetFileType",
              "GetFullPathNameA", "GetStdHandle", "MoveFileA", "ReadFile", "SetEndOfFile",
              "SetFilePointer", "SetFileTime", "SetHandleCount", "SetStdHandle", "WriteFile"),
    "time": ("FileTimeToLocalFileTime", "FileTimeToSystemTime", "GetSystemTimeAsFileTime",
             "GetTickCount", "GetTimeZoneInformation", "QueryPerformanceCounter",
             "QueryPerformanceFrequency", "Sleep"),
    "threads": ("CreateThread", "ExitThread", "GetCurrentThreadId", "GetExitCodeThread",
                "GetThreadPriority", "ResumeThread", "SetThreadAffinityMask",
                "SetThreadExecutionState", "SetThreadIdealProcessor", "SetThreadPriority",
                "SuspendThread", "TlsAlloc", "TlsFree", "TlsGetValue", "TlsSetValue"),
    "sync": ("CreateEventA", "CreateEventW", "CreateMutexW", "DeleteCriticalSection",
             "EnterCriticalSection", "InitializeCriticalSection",
             "InitializeCriticalSectionAndSpinCount", "InterlockedDecrement",
             "InterlockedExchange", "InterlockedIncrement", "LeaveCriticalSection",
             "ResetEvent", "SetEvent", "WaitForSingleObject"),
    "memory": ("GetProcessHeap", "GlobalAlloc", "GlobalLock", "GlobalUnlock", "HeapAlloc",
               "HeapCreate", "HeapFree", "HeapReAlloc", "HeapSize", "LocalAlloc", "LocalFree",
               "LocalLock", "LocalUnlock", "VirtualAlloc", "VirtualFree", "VirtualQuery"),
    "environment": ("FreeEnvironmentStringsA", "FreeEnvironmentStringsW", "GetCommandLineA",
                    "GetEnvironmentStrings", "GetEnvironmentStringsW", "GetStartupInfoA",
                    "GetSystemInfo", "GetVersionExW", "SetEnvironmentVariableA"),
    "process": ("ExitProcess", "FreeLibrary", "GetCurrentProcess", "GetCurrentProcessId",
                "GetLastError", "GetModuleFileNameA", "GetModuleFileNameW", "GetModuleHandleA",
                "GetModuleHandleW", "GetProcAddress", "GetProcessAffinityMask",
                "IsDebuggerPresent", "LoadLibraryA", "OutputDebugStringA", "RaiseException",
                "RtlUnwind", "SetLastError", "SetUnhandledExceptionFilter", "TerminateProcess",
                "UnhandledExceptionFilter"),
    "console": ("GetConsoleCP", "GetConsoleMode", "GetConsoleOutputCP", "WriteConsoleA",
                "WriteConsoleW"),
    "locale": ("CompareStringA", "CompareStringW", "EnumSystemLocalesA", "GetACP", "GetCPInfo",
               "GetLocaleInfoA", "GetLocaleInfoW", "GetOEMCP", "GetStringTypeA", "GetStringTypeW",
               "GetUserDefaultLCID", "IsValidCodePage", "IsValidLocale", "LCMapStringA",
               "LCMapStringW", "MultiByteToWideChar", "WideCharToMultiByte", "lstrcmpA"),
    "arithmetic": ("MulDiv",),
}
KERNEL32_SUBCLASS = {name: group for group, names in KERNEL32_GROUPS.items() for name in names}

USER32_GROUPS: dict[str, tuple[str, ...]] = {
    "window": ("CreateWindowExW", "DefWindowProcW", "DestroyWindow", "EnableMenuItem",
               "EnableWindow", "GetActiveWindow", "GetClassNameW", "GetClientRect",
               "GetLastActivePopup", "GetParent", "GetSystemMenu", "GetWindow", "GetWindowLongW",
               "GetWindowRect", "GetWindowTextW", "IsWindow", "MoveWindow", "RedrawWindow",
               "RegisterClassW", "SetFocus", "SetForegroundWindow", "SetRect", "SetWindowLongW",
               "SetWindowPos", "SetWindowTextW", "ShowWindow"),
    "message": ("DispatchMessageW", "GetMessageW", "IsDialogMessageW", "KillTimer", "PeekMessageW",
                "PostMessageW", "PostQuitMessage", "SendMessageW", "SetTimer", "TranslateMessage",
                "WaitMessage"),
    "cursor": ("GetCursorPos", "LoadCursorW", "ShowCursor"),
    "keyboard": ("GetAsyncKeyState", "GetKeyState"),
    "paint": ("BeginPaint", "DestroyIcon", "DrawIcon", "DrawTextW", "EndPaint", "FillRect",
              "GetDialogBaseUnits", "GetSysColor", "GetSystemMetrics", "LoadIconW",
              "SystemParametersInfoW"),
    "dialog": ("CheckDlgButton", "CreateDialogIndirectParamW", "GetDlgItem", "LoadStringW",
               "MessageBeep", "MessageBoxA", "WinHelpW"),
    "clipboard": ("CloseClipboard", "EmptyClipboard", "OpenClipboard", "SetClipboardData"),
}
USER32_SUBCLASS = {name: group for group, names in USER32_GROUPS.items() for name in names}
# user32 names that are not presentation at all.
NAME_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    ("user32", "wvsprintfA"): ("os-service", "string"),
}

# Public export ordinals of the two DLLs imported by ordinal (not part of the input).
CONVENTIONAL_ORDINALS = {
    ("dsound", 11): "DirectSoundCreate8",
    ("xinput1_3", 2): "XInputGetState",
    ("xinput1_3", 3): "XInputSetState",
    ("xinput1_3", 4): "XInputGetCapabilities",
}


def classify(dll: str, name: str) -> tuple[str, str]:
    """(class, subclass) for one import; `other` names what did not fit."""
    key = dll.lower()
    if key.endswith(".dll"):
        key = key[:-4]
    override = NAME_OVERRIDES.get((key, name))
    if override:
        return override
    if key == "kernel32":
        group = KERNEL32_SUBCLASS.get(name)
        if group is None:
            return ("other", "kernel32 name outside the classification tables")
        return ("os-service", group)
    if key == "user32":
        group = USER32_SUBCLASS.get(name)
        if group is None:
            return ("other", "user32 name outside the classification tables")
        return ("presentation", group)
    entry = DLL_CLASS.get(key)
    if entry is None:
        return ("other", f"unknown dll {dll}")
    return entry


# --------------------------------------------------------------------------
# Sweep (own decode loop; s1_sweep.py is another unit's file)


def padding_run(data: bytes, pos: int) -> int:
    byte = data[pos]
    if byte == 0xCC or (byte == 0x90 and pos + 1 < len(data) and data[pos + 1] == 0x90):
        end = pos
        while end < len(data) and data[end] == byte:
            end += 1
        return end - pos
    return 0


def sweep_references(data: bytes, base: int, slots: set[int]):
    """(slot_refs, direct_calls, direct_jumps, immediates, decode_errors).

    slot_refs: slot VA -> list of (address, kind) with kind in call/jmp/other.
    direct_calls: Counter of near-call targets (every `call rel32`).
    direct_jumps: Counter of near-jump targets (every `jmp rel8/rel32`).
    immediates: Counter of every 32-bit immediate operand value.
    """
    decoder = ix.Decoder(32, data, ip=base)
    refs: dict[int, list[tuple[int, str]]] = defaultdict(list)
    direct_calls: Counter = Counter()
    direct_jumps: Counter = Counter()
    immediates: Counter = Counter()
    errors = 0
    pos = 0
    size = len(data)
    while pos < size:
        run = padding_run(data, pos)
        if run:
            pos += run
            continue
        decoder.position = pos
        decoder.ip = base + pos
        ins = decoder.decode()
        address = base + pos
        if ins.is_invalid:
            errors += 1
            pos += 1
            continue
        pos += ins.len
        flow = ins.flow_control
        if flow == ix.FlowControl.CALL and ins.op0_kind == ix.OpKind.NEAR_BRANCH32:
            direct_calls[ins.near_branch32] += 1
            continue
        if flow == ix.FlowControl.UNCONDITIONAL_BRANCH and ins.op0_kind == ix.OpKind.NEAR_BRANCH32:
            direct_jumps[ins.near_branch32] += 1
            continue
        for index in range(ins.op_count):
            kind = ins.op_kind(index)
            if kind == ix.OpKind.IMMEDIATE32:
                immediates[ins.immediate32] += 1
                continue
            if kind != ix.OpKind.MEMORY:
                continue
            if ins.memory_base != ix.Register.NONE or ins.memory_index != ix.Register.NONE:
                continue
            target = ins.memory_displacement & 0xFFFFFFFF
            if target not in slots:
                continue
            if flow == ix.FlowControl.INDIRECT_CALL:
                kind = "call"
            elif flow == ix.FlowControl.INDIRECT_BRANCH:
                kind = "jmp"
            else:
                kind = "other"
            refs[target].append((address, kind))
            break
    return refs, direct_calls, direct_jumps, immediates, errors


class RegionMap:
    def __init__(self, functions: list[dict]) -> None:
        entries = sorted((int(str(f["address"]), 0), int(f["size"]), str(f["region"]))
                         for f in functions)
        self.starts = [e[0] for e in entries]
        self.entries = entries
        self.regions = sorted({e[2] for e in entries})

    def region_of(self, address: int) -> str:
        index = bisect.bisect_right(self.starts, address) - 1
        if index < 0:
            return UNMAPPED
        start, size, region = self.entries[index]
        return region if start <= address < start + size else UNMAPPED


# --------------------------------------------------------------------------


def text_section(pe: pefile.PE) -> tuple[bytes, int]:
    for section in pe.sections:
        if section.Name.rstrip(b"\0").decode("ascii", "replace") == SECTION:
            return (section.get_data()[: section.Misc_VirtualSize],
                    pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress)
    raise SystemExit(f"no {SECTION} section")


def import_table(pe: pefile.PE) -> list[dict]:
    imports = []
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        dll = entry.dll.decode("ascii", "replace")
        key = dll.lower()[:-4] if dll.lower().endswith(".dll") else dll.lower()
        for imp in entry.imports:
            if imp.name is None:
                name = f"ordinal#{imp.ordinal}"
                conventional = CONVENTIONAL_ORDINALS.get((key, imp.ordinal))
            else:
                name = imp.name.decode("ascii", "replace")
                conventional = None
            record = {"dll": dll, "name": name, "iat_slot": imp.address}
            if conventional:
                record["conventional_name"] = conventional
            imports.append(record)
    return imports


def tool_versions(root: Path) -> dict:
    pins = json.loads((root / PINS).read_text(encoding="utf-8"))["tools"]
    observed = {"python": sys.version.split()[0],
                "pefile": importlib.metadata.version("pefile"),
                "iced-x86": importlib.metadata.version("iced-x86")}
    return {name: {"pinned": pins[name]["version"], "observed": observed[name],
                   "match": pins[name]["version"] == observed[name]}
            for name in observed}


def hexa(value: int) -> str:
    return f"0x{value:08X}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--exe", default=DEFAULT_EXE)
    parser.add_argument("--function-map", default=DEFAULT_MAP)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--metrics-out", default=DEFAULT_METRICS_OUT)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    root: Path = args.root

    exe_bytes = (root / args.exe).read_bytes()
    exe_sha = hashlib.sha256(exe_bytes).hexdigest()
    pe = pefile.PE(data=exe_bytes, fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    imports = import_table(pe)
    data, base = text_section(pe)

    map_path = root / args.function_map
    region_map: RegionMap | None = None
    # The absent path is not written out: the cited-files gate treats every
    # repository path in a receipt as a citation that must exist.
    map_input: dict | str = ("absent; counts are whole-image (pass --function-map "
                             "once the map exists for the per-region breakdown)")
    map_flag = f" --function-map {args.function_map}" if map_path.is_file() else ""
    if map_path.is_file():
        doc = json.loads(map_path.read_text(encoding="utf-8"))
        region_map = RegionMap(doc["functions"])
        map_input = {"path": args.function_map,
                     "sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
                     "functions": len(region_map.entries), "regions": region_map.regions}

    slots = {imp["iat_slot"] for imp in imports}
    refs, direct_calls, direct_jumps, immediates, errors = sweep_references(data, base, slots)

    def region_counts(addresses: list[int]) -> dict | str:
        if region_map is None:
            return UNPARTITIONED
        counts = Counter(region_map.region_of(a) for a in addresses)
        return {k: counts[k] for k in sorted(counts)}

    records = []
    by_class: Counter = Counter()
    by_subclass: Counter = Counter()
    by_dll: Counter = Counter()
    zero_sites = []
    pointer_only = []
    unclassified = []
    for imp in imports:
        cls, sub = classify(imp["dll"], imp["name"])
        sites = sorted(refs.get(imp["iat_slot"], []))
        calls = [a for a, k in sites if k == "call"]
        thunks = [a for a, k in sites if k == "jmp"]
        others = [a for a, k in sites if k == "other"]
        via_thunks = sum(direct_calls.get(t, 0) for t in thunks)
        jumps_to_thunks = sum(direct_jumps.get(t, 0) for t in thunks)
        thunk_pointers = sum(immediates.get(t, 0) for t in thunks)
        total = len(calls) + via_thunks + jumps_to_thunks
        pointer_loads = len(others) + thunk_pointers
        record = {
            "dll": imp["dll"], "name": imp["name"], "class": cls, "subclass": sub,
            "iat_slot": hexa(imp["iat_slot"]),
            "direct_call_sites": len(calls),
            "thunks": [hexa(t) for t in thunks],
            "calls_to_thunks": via_thunks,
            "jumps_to_thunks": jumps_to_thunks,
            "slot_pointer_loads": len(others),
            "thunk_pointer_loads": thunk_pointers,
            "pointer_loads": pointer_loads,
            "static_call_sites": total,
            "direct_call_sites_by_region": region_counts(calls),
        }
        if "conventional_name" in imp:
            record["conventional_name"] = imp["conventional_name"]
        if total == 0:
            (pointer_only if pointer_loads else zero_sites).append(f"{imp['dll']}!{imp['name']}")
        if cls == "other":
            unclassified.append(f"{imp['dll']}!{imp['name']}: {sub}")
        records.append(record)
        by_class[cls] += 1
        by_subclass[f"{cls}/{sub}"] += 1
        by_dll[imp["dll"]] += 1

    records.sort(key=lambda r: (r["dll"].lower(), r["name"]))
    dlls_seen = sorted({r["dll"].lower()[:-4] if r["dll"].lower().endswith(".dll") else r["dll"].lower()
                        for r in records})
    d3dx = sorted(r["name"] for r in records if r["dll"].lower() == "d3dx9_42.dll")
    thunk_count = sum(len(r["thunks"]) for r in records)

    command = (f".venv/bin/python Documentation/scripts/s1_import_classification.py "
               f"--exe {args.exe}{map_flag} --out {args.out} "
               f"--metrics-out {args.metrics_out} --date {args.date}")
    common_inputs = {
        "executable": args.exe,
        "executable_sha256": exe_sha,
        "image_base": hexa(pe.OPTIONAL_HEADER.ImageBase),
        "function_map": map_input,
        "tools": tool_versions(root),
    }
    receipt = {
        "schema": SCHEMA,
        "id": RECEIPT_ID,
        "date": args.date,
        "question": ("Which of the executable's imports are OS services, presentation, Steam or "
                     "DRM (design 3.5), and how many static call sites does each have?"),
        "inputs": common_inputs,
        "command": command,
        "method": (
            "pefile reads the import directory; each dll!name is classified by a table keyed "
            "on DLL, with per-name groups for kernel32 and user32 (subclass) and a name "
            "override for user32!wvsprintfA. A linear iced-x86 decode of .text (padding "
            "skipped, one-byte resync on error) records every absolute memory operand equal "
            "to an IAT slot: call [slot] is a direct call site, jmp [slot] is a thunk, "
            "anything else a pointer load; every call rel32 or jmp rel32 targeting a thunk "
            "counts as a call to the thunk's import, and a 32-bit immediate equal to a "
            "thunk's address as a pointer load. Limits: static and linear, so data-in-code can hide "
            "or fabricate a reference and the counts are an upper bound on distinct sites, "
            "not a reachability proof; register-indirect calls through a loaded pointer are "
            "counted as pointer loads, not calls; PSFD00 is not swept; ordinal imports carry "
            "conventional names from public export tables that are not part of the input."
        ),
        "authority": "statically-translated",
        "outputs": {
            "import_count": len(records),
            "design_2_3_import_count": DESIGN_IMPORT_COUNT,
            "import_count_agrees": len(records) == DESIGN_IMPORT_COUNT,
            "dlls": dict(sorted(by_dll.items(), key=lambda kv: kv[0].lower())),
            "design_2_3_dlls_agree": dlls_seen == sorted(DESIGN_DLLS),
            "by_class": {cls: by_class.get(cls, 0) for cls in CLASSES},
            "by_subclass": dict(sorted(by_subclass.items())),
            "unclassified": unclassified,
            "drm_note": ("no import names a CEG or DRM entry point; the unpacked executable is "
                         "the lifter's input and CEG is never reached (design 3.5 d)"),
            "d3dx9_42_imports": d3dx,
            "sweep": {
                "decode_errors": errors,
                "direct_call_instructions": sum(direct_calls.values()),
                "distinct_direct_call_targets": len(direct_calls),
                "design_2_3_distinct_direct_call_targets": DESIGN_2_3_DIRECT_CALL_TARGETS,
                "import_thunks": thunk_count,
                "static_call_sites_total": sum(r["static_call_sites"] for r in records),
                "pointer_loads_total": sum(r["pointer_loads"] for r in records),
            },
            "zero_static_call_sites_unreferenced": zero_sites,
            "zero_static_call_sites_pointer_loads_only": pointer_only,
            "imports": records,
        },
    }
    metrics_doc = {
        "schema": SCHEMA,
        "id": "import-classification",
        "date": args.date,
        "generated_by": "Documentation/scripts/s1_import_classification.py",
        "source_receipt": args.out,
        "inputs": common_inputs,
        "command": command,
        "authority": "statically-translated",
        "imports": [{"dll": r["dll"], "name": r["name"], "class": r["class"],
                     "subclass": r["subclass"]} for r in records],
    }
    for path, doc in ((root / args.out, receipt), (root / args.metrics_out, metrics_doc)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")

    out = receipt["outputs"]
    print(f"S1_IMPORTS imports={out['import_count']} design_251_agrees={out['import_count_agrees']} "
          f"dlls={len(out['dlls'])} design_dll_list_agrees={out['design_2_3_dlls_agree']} "
          + " ".join(f"{cls}={out['by_class'][cls]}" for cls in CLASSES)
          + f" unclassified={len(unclassified)}")
    sweep = out["sweep"]
    print(f"S1_IMPORTS decode_errors={sweep['decode_errors']} "
          f"direct_call_instructions={sweep['direct_call_instructions']} "
          f"distinct_direct_call_targets={sweep['distinct_direct_call_targets']} "
          f"(design_2_3={DESIGN_2_3_DIRECT_CALL_TARGETS}) import_thunks={sweep['import_thunks']} "
          f"static_call_sites_total={sweep['static_call_sites_total']} "
          f"pointer_loads_total={sweep['pointer_loads_total']}")
    print(f"S1_IMPORTS zero_static_call_sites={len(zero_sites) + len(pointer_only)} "
          f"unreferenced={len(zero_sites)} pointer_loads_only={len(pointer_only)} "
          f"d3dx9_42={len(d3dx)} wrote={args.out} metrics={args.metrics_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
