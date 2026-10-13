#!/usr/bin/env python3
"""Behaviour tests for the S1 opcode-histogram and import-classification scripts.

The classifier is exercised on a table of dll!name examples (one per class
and the edge cases: ordinal imports, the user32 override, unknown names in a
known DLL, an unknown DLL). The coverage computation is exercised on a
synthetic Counter with a literal expected answer. The decode loop is
exercised on a handful of hand-assembled bytes so padding skipping, error
resync and the suspect window are checked without the executable.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import s1_import_classification as imports  # noqa: E402
import s1_opcode_histogram as opcodes  # noqa: E402


@pytest.mark.parametrize("dll, name, expected", [
    ("KERNEL32.dll", "CreateFileA", ("os-service", "files")),
    ("KERNEL32.dll", "QueryPerformanceCounter", ("os-service", "time")),
    ("KERNEL32.dll", "CreateThread", ("os-service", "threads")),
    ("KERNEL32.dll", "EnterCriticalSection", ("os-service", "sync")),
    ("KERNEL32.dll", "HeapAlloc", ("os-service", "memory")),
    ("ADVAPI32.dll", "RegCreateKeyExW", ("os-service", "registry")),
    ("KERNEL32.dll", "GetCommandLineA", ("os-service", "environment")),
    ("KERNEL32.dll", "ExitProcess", ("os-service", "process")),
    ("KERNEL32.dll", "WriteConsoleW", ("os-service", "console")),
    ("KERNEL32.dll", "MultiByteToWideChar", ("os-service", "locale")),
    ("d3d9.dll", "Direct3DCreate9", ("presentation", "graphics")),
    ("d3dx9_42.dll", "D3DXCreateTexture", ("presentation", "graphics-d3dx")),
    ("DSOUND.dll", "ordinal#11", ("presentation", "audio")),
    ("XINPUT1_3.dll", "ordinal#2", ("presentation", "input")),
    ("DINPUT8.dll", "DirectInput8Create", ("presentation", "input")),
    ("WINMM.dll", "timeGetTime", ("presentation", "time")),
    ("GDI32.dll", "SelectObject", ("presentation", "gdi")),
    ("USER32.dll", "CreateWindowExW", ("presentation", "window")),
    ("USER32.dll", "PeekMessageW", ("presentation", "message")),
    ("USER32.dll", "ShowCursor", ("presentation", "cursor")),
    ("USER32.dll", "GetAsyncKeyState", ("presentation", "keyboard")),
    ("USER32.dll", "wvsprintfA", ("os-service", "string")),
    ("steam_api.dll", "SteamAPI_Init", ("steam", "steam-api")),
    ("SteamMetricsSDK.dll", "?LogEvent@MetricsAgent@SteamMetricsSDK@@QAEXPBD00@Z",
     ("steam", "steam-metrics")),
    ("ole32.dll", "CoCreateInstance", ("os-service", "com")),
    ("OLEAUT32.dll", "SysAllocString", ("os-service", "memory")),
    ("KERNEL32.dll", "NoSuchFunction", ("other", "kernel32 name outside the classification tables")),
    ("USER32.dll", "NoSuchFunction", ("other", "user32 name outside the classification tables")),
    ("ceg.dll", "Anything", ("other", "unknown dll ceg.dll")),
])
def test_classify(dll: str, name: str, expected: tuple[str, str]) -> None:
    assert imports.classify(dll, name) == expected


def test_classify_is_case_insensitive_on_the_dll_and_tolerates_a_missing_suffix() -> None:
    assert imports.classify("kernel32", "Sleep") == ("os-service", "time")
    assert imports.classify("Kernel32.DLL", "Sleep") == ("os-service", "time")


def test_every_class_named_in_the_receipt_is_a_design_class() -> None:
    assert imports.CLASSES == ("os-service", "presentation", "steam", "drm", "other")
    for names in list(imports.KERNEL32_GROUPS.values()) + list(imports.USER32_GROUPS.values()):
        assert len(set(names)) == len(names)


def test_coverage_subset_sizes_on_a_synthetic_histogram() -> None:
    # 1000 instructions: a covers 90 %, b 9 %, c 0.9 %, d 0.09 %, e the last 0.01 %.
    histogram = Counter({"a": 900, "b": 90, "c": 9, "d": 0, "e": 1})
    assert opcodes.coverage_subset_sizes(histogram) == {"99": 2, "99.9": 3, "100": 4}
    assert opcodes.coverage_subset_sizes(Counter()) == {"99": 0, "99.9": 0, "100": 0}
    assert opcodes.coverage_subset_sizes(Counter({"only": 5})) == {"99": 1, "99.9": 1, "100": 1}


def test_coverage_breaks_ties_by_name_so_the_answer_is_deterministic() -> None:
    assert opcodes.coverage_subset_sizes(Counter({"z": 50, "a": 50}), (50.0,)) == {"50": 1}


def test_padding_run_skips_int3_runs_and_double_nops_only() -> None:
    assert opcodes.padding_run(b"\xcc\xcc\xcc\x90", 0) == 3
    assert opcodes.padding_run(b"\x90\x90\x90\xcc", 0) == 3
    assert opcodes.padding_run(b"\x90\xc3", 0) == 0     # a single nop is code
    assert opcodes.padding_run(b"\xc3\xcc", 0) == 0


def test_decode_linear_resyncs_after_an_error_and_marks_the_window_suspect() -> None:
    # ret; int3 x3; then at 0x401004 an invalid opcode (0x0F 0x0B is ud2, valid;
    # 0xFF 0xFF is invalid), then nop, nop, nop (a padding run), then ret at
    # 0x401010 which is past the next 16-byte alignment.
    code = b"\xc3" + b"\xcc" * 3 + b"\xff\xff" + b"\xc3" + b"\x90\x90\x90" + b"\xc3" * 6 + b"\xc3"
    base = 0x401000
    events = list(opcodes.decode_linear(code, base))
    kinds = [(e[0] if isinstance(e[0], str) else "ins") for e in events]
    assert kinds[:3] == ["ins", "padding", "error"]
    assert events[1] == ("padding", 0x401001, 3)
    assert events[2] == ("error", 0x401004)
    # After the error at 0x401004 the sweep resumes at 0x401005 (0xFF 0xC3 = inc ebx),
    # then the nop run is skipped as padding; everything before 0x401010 is suspect.
    decoded = [(ins.ip, suspect) for ins, suspect in
               (e for e in events if not isinstance(e[0], str))]
    assert decoded[0] == (0x401000, False)
    assert all(suspect for ip, suspect in decoded if 0x401004 <= ip < 0x401010)
    assert (0x401010, False) in decoded
    assert ("padding", 0x401007, 3) in events


def test_region_map_attributes_addresses_and_reports_unmapped() -> None:
    region_map = opcodes.RegionMap([
        {"address": "0x401000", "size": 16, "name": "a", "region": "game"},
        {"address": 0x401020, "size": 8, "name": "b", "region": "crt"},
    ])
    assert region_map.region_of(0x401000) == "game"
    assert region_map.region_of(0x40100F) == "game"
    assert region_map.region_of(0x401010) == "unmapped"
    assert region_map.region_of(0x401027) == "crt"
    assert region_map.region_of(0x400FFF) == "unmapped"
    assert region_map.regions == ["crt", "game"]
