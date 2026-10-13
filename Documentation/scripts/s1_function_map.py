#!/usr/bin/env python3
"""S1 receipt 1: the analyzer's function map with region partition.

Runs Ghidra headless on the unpacked executable with
`ExportSA2FunctionMap.java`, then partitions every function into a region
(game / crt / cri / d3dx / other), applies the Mod Loader names, audits the
list against a linear direct-call sweep, and writes

  private/generated/function-map.json          the map metrics.py reads (ignored)
  Documentation/receipts/s1-function-map-v1.json  counts only (tracked)

Ghidra state lives under private/ghidra/ (project, headless.log, script.log,
seeds.txt, the raw exporter JSON, run.json with the command and wall time).
The project is deleted and recreated on every run; `--skip-ghidra` reuses the
raw JSON and run.json from the last run and repeats only the post-processing.

Seeding. Ghidra's default analysis alone left 743 Mod Loader function
addresses and 248 direct-call targets of a linear sweep with no function and
no instruction (first run, 2026-09-19): code reached only through data tables
Ghidra did not recognise as pointer tables (the object task lists) and the
code those functions call. The driver therefore writes every Mod Loader
function address and every sweep target to private/ghidra/seeds.txt; the
exporter records the entry list Ghidra found alone, then creates a function at
each seed that is executable and holds no instruction, runs the analyzer over
the changes, and exports. The receipt reports both counts and every seed's
result. A seed at a false decode of the linear sweep (a call opcode inside a
jump table or padding) becomes a bogus function; each seeded function carries
`seeded: true` and its source so a later stage can treat it with suspicion.

Region rule (design section 3.2), applied in this order
-------------------------------------------------------

1. String-evidence seeds.
   cri: a function whose data references hit a CRI string cluster or a CRI
        error-code string. A cluster is the .rdata interval spanned by one
        family of CRI build stamps ("Append: MSC1400,MT" x17,
        "Append: MSC1500,MT" x32; every CRI object carries one in its version
        string) plus the standalone " CRI MIDDLEWARE CO., LTD. " string. An
        error-code string is a C string of the CRI Atom library's form
        "E06092607:Failed to create an AISAC group" (E or W, 8 to 11 digits,
        an optional letter, a colon); the Atom objects sit past the stamp
        clusters and would otherwise be reached from the game and taken.
   crt: a function whose data references hit the CRT string cluster (the
        .rdata interval spanned by the "R60nn" runtime-error strings and the
        "Microsoft Visual C++ Runtime Library" caption), or that Ghidra's
        Function ID database matched (a bookmark on the entry; the database
        covers Microsoft's static libraries), or that is reachable from the PE
        entry point over direct calls without passing through WinMain.
   d3dx: a function that calls a d3dx9_42 import and, transitively over its
        direct calls, calls nothing but d3dx9_42 / d3d9 imports and other such
        functions: the out-of-line wrappers of the static part of d3dx9.lib.
        d3dx9_42 is a DLL import, so this region is expected to be small.
2. Mod Loader names: a named function is a game seed, except the header's
   IDA placeholders (nullsub_N), which name a function without saying whose
   it is. A string-evidence seed keeps its region even when the Mod Loader
   names it; those conflicts are counted in the receipt.
3. Contiguous fill of cri and crt: functions are sorted by address. Between
   two consecutive seeds of the same region with no seed of another region
   in between, every unassigned function takes that region. MSVC emits each
   object's functions contiguously and links objects in order, so a
   compilation unit's functions that reference no string sit between ones
   that do. This runs before reachability because CRI's API entry functions
   reference no string, and reachability from the game would otherwise walk
   through them into CRI's internals. d3dx is not filled.
4. Reachability: every still-unassigned function reachable from WinMain over
   direct calls, function-pointer operands and function-pointer tables in
   data (a table is a run of pointers to function entries at most TABLE_GAP
   bytes apart; a reachable function that references any address inside the
   run reaches every function the run points to) is a game seed. Assigned
   non-game functions are sinks. WinMain is the callee within two direct
   calls of the entry point with the largest direct-call closure, each
   candidate measured with the others as sinks.
5. Contiguous fill of game between game seeds, as in step 3.
6. Everything else is "other": neither string evidence, nor a Mod Loader
   name, nor reachable from WinMain, nor between two seeds of one region.

Known limits: a CRI object without a version string and without other
string references is found only by the fill; a game object placed between
two CRI objects with no name and no reachability would be filled as cri (the
receipt reports how many functions each region got by fill versus seed);
FID can match a library function the game links from a Microsoft library
other than the CRT; reachability through indirect calls that do not go via a
data table (computed from a register with no table) is missed, so "other" is
an upper bound on dead code.

Usage:
    .venv/bin/python Documentation/scripts/s1_function_map.py
    .venv/bin/python Documentation/scripts/s1_function_map.py --skip-ghidra
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import pefile
from iced_x86 import Code, Decoder, Mnemonic, OpKind

from gitlog import project_root

EXE = "private/exe/sonic2app-unpacked.exe"
PINS = "tools/pins.json"
HEADER = "private/predecessor-tools/sa2-mod-loader/SA2ModLoader/include/SA2Functions.h"
JAVA_SCRIPT = "Documentation/scripts/ExportSA2FunctionMap.java"
GHIDRA_DIR = "private/ghidra"
RAW_JSON = "private/ghidra/ghidra-function-map.json"
RUN_JSON = "private/ghidra/run.json"
SEEDS_TXT = "private/ghidra/seeds.txt"
FUNCTION_MAP = "private/generated/function-map.json"
RECEIPT = "Documentation/receipts/s1-function-map-v1.json"
PROJECT_NAME = "SA2FunctionMap"
DESIGN_LOWER_BOUND = 9297  # design section 2.3: distinct direct call targets
DESIGN_MODLOADER_FUNCTIONS = 725  # design section 2.3
TABLE_GAP = 64  # bytes between function pointers that still form one table
REGIONS = ("game", "crt", "cri", "d3dx", "other")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- Ghidra run

def run_ghidra(root: Path, exe: Path, seeds: Path) -> dict:
    pins = json.loads((root / PINS).read_text())
    ghidra_dir = root / pins["tools"]["ghidra"]["path"]
    java_home = root / pins["tools"]["java"]["path"]
    headless = ghidra_dir / "support" / "analyzeHeadless"
    if not headless.exists():
        sys.exit(f"analyzeHeadless not found: {headless}")
    work = root / GHIDRA_DIR
    project = work / "project"
    shutil.rmtree(project, ignore_errors=True)
    project.mkdir(parents=True)  # Ghidra needs the location directory to exist
    xdg = work / "xdg-config"
    xdg.mkdir(exist_ok=True)
    raw = root / RAW_JSON
    raw.unlink(missing_ok=True)
    for name in ("headless.log", "script.log", "stdout.log"):
        (work / name).unlink(missing_ok=True)
    command = [
        str(headless), str(project), PROJECT_NAME,
        "-import", str(exe),
        "-scriptPath", str(root / "Documentation/scripts"),
        "-postScript", "ExportSA2FunctionMap.java", str(raw), str(seeds),
        "-scriptlog", str(work / "script.log"),
        "-log", str(work / "headless.log"),
    ]
    env = dict(os.environ)
    env["JAVA_HOME"] = str(java_home)
    env["PATH"] = f"{java_home / 'bin'}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(xdg)
    started = time.time()
    with (work / "stdout.log").open("wb") as out:
        result = subprocess.run(command, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT)
    wall = time.time() - started
    log = (work / "headless.log").read_text(errors="replace")
    if result.returncode != 0 or "SA2_FUNCTION_MAP_WRITTEN" not in log or not raw.exists():
        sys.exit(f"Ghidra run failed (exit {result.returncode}); see {work / 'headless.log'}")
    match = re.search(r"Total Time\s+(\d+) secs", log)
    run = {
        "command": " ".join(command),
        "env": {"JAVA_HOME": env["JAVA_HOME"], "XDG_CONFIG_HOME": env["XDG_CONFIG_HOME"]},
        "wall_seconds": round(wall, 1),
        "analysis_seconds": int(match.group(1)) if match else None,
        "exit_code": result.returncode,
        "ghidra_version": pins["tools"]["ghidra"]["version"],
        "java_version": pins["tools"]["java"]["version"],
    }
    (root / RUN_JSON).write_text(json.dumps(run, indent=2) + "\n")
    return run


# --------------------------------------------------------------- PE evidence

class Image:
    def __init__(self, path: Path):
        self.pe = pefile.PE(str(path))
        self.data = self.pe.__data__
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.sections = []
        for s in self.pe.sections:
            start = self.base + s.VirtualAddress
            self.sections.append({
                "name": s.Name.decode("latin-1").rstrip("\0"),
                "start": start, "end": start + max(s.Misc_VirtualSize, s.SizeOfRawData),
                "raw": s.PointerToRawData, "raw_size": s.SizeOfRawData,
            })

    def va(self, offset: int) -> int:
        return self.base + self.pe.get_rva_from_offset(offset)

    def find_all(self, pattern: bytes) -> list[int]:
        return [self.va(m.start()) for m in re.finditer(pattern, self.data)]

    def cstring_bounds(self, va: int) -> tuple[int, int]:
        off = self.pe.get_offset_from_rva(va - self.base)
        s = off
        while s > 0 and 0x20 <= self.data[s - 1] < 0x7F:
            s -= 1
        e = off
        while e < len(self.data) and 0x20 <= self.data[e] < 0x7F:
            e += 1
        return self.va(s), self.va(e) + 1

    def section(self, name: str) -> dict:
        for s in self.sections:
            if s["name"] == name:
                return s
        raise KeyError(name)


def string_clusters(image: Image) -> dict:
    """Address intervals of the CRI and CRT string clusters (see module doc)."""
    clusters = {}
    stamps = {}
    for family in ("MSC1400", "MSC1500"):
        hits = image.find_all(re.escape(f"Append: {family},MT".encode()))
        bounds = [image.cstring_bounds(h) for h in hits]
        stamps[family] = {"count": len(hits), "first": min(b[0] for b in bounds),
                          "last": max(b[1] for b in bounds)}
        clusters[f"cri-{family}"] = (stamps[family]["first"], stamps[family]["last"])
    cri_mark = image.find_all(rb" CRI MIDDLEWARE CO\., LTD\. ")
    for i, va in enumerate(cri_mark):
        clusters[f"cri-middleware-{i}"] = image.cstring_bounds(va)
    crt_hits = image.find_all(rb"R60\d\d") + image.find_all(rb"Microsoft Visual C\+\+ Runtime Library")
    bounds = [image.cstring_bounds(h) for h in crt_hits]
    clusters["crt-runtime-errors"] = (min(b[0] for b in bounds), max(b[1] for b in bounds))
    rdata = image.section(".rdata")
    errors = []
    for m in re.finditer(rb"(?<=\x00)[EW]\d{8,11}[A-Z]?:[ -~]*\x00", image.data[rdata["raw"]:rdata["raw"] + rdata["raw_size"]]):
        start = rdata["start"] + m.start()
        errors.append((start, start + len(m.group(0))))
    return {"clusters": clusters, "stamps": stamps, "crt_string_hits": len(crt_hits),
            "cri_error_strings": sorted(errors)}


def modloader_functions(root: Path) -> tuple[dict[int, str], dict[str, int]]:
    text = (root / HEADER).read_text(encoding="utf-8", errors="replace")
    names: dict[int, str] = {}
    by_macro: dict[str, int] = {}
    patterns = {
        "FunctionPointer-family": r"^\s*((?:Thiscall|Fastcall|Stdcall)?FunctionPointer)\(\s*[^,]+,\s*(\w+)\s*,\s*\(.*?\)\s*,\s*(0x[0-9A-Fa-f]+)\s*\)",
        "VoidFunc/ObjectFunc": r"^\s*(VoidFunc|ObjectFunc)\(\s*(\w+)\s*,\s*(0x[0-9A-Fa-f]+)\s*\)",
        "usercall Ptr wrapper": r"^\s*static const void\* const (\w+)Ptr = \(void\*\)(0x[0-9A-Fa-f]+);()",
    }
    for macro, pattern in patterns.items():
        for m in re.finditer(pattern, text, re.M):
            if macro == "usercall Ptr wrapper":
                name, address = m.group(1), int(m.group(2), 16)
                key = macro
            else:
                key, name, address = m.group(1), m.group(2), int(m.group(3), 16)
            by_macro[key] = by_macro.get(key, 0) + 1
            names.setdefault(address, name)
    return names, by_macro


# ------------------------------------------------------------ direct-call sweep

def direct_call_sweep(image: Image) -> dict[int, list[int]]:
    """Linear decode of .text: direct call target -> call-site addresses."""
    text = image.section(".text")
    code = image.data[text["raw"]:text["raw"] + text["raw_size"]]
    decoder = Decoder(32, code, ip=text["start"])
    targets: dict[int, list[int]] = {}
    image_end = max(s["end"] for s in image.sections)
    for insn in decoder:
        if insn.mnemonic == Mnemonic.CALL and insn.op0_kind == OpKind.NEAR_BRANCH32:
            t = insn.near_branch32
            if image.base <= t < image_end:
                targets.setdefault(t, []).append(insn.ip)
    return targets


# ------------------------------------------------------------------ partition

def partition(raw: dict, image: Image, names: dict[int, str]) -> tuple[list[dict], dict]:
    functions = sorted(raw["functions"], key=lambda f: int(f["address"], 16))
    for f in functions:
        f["addr"] = int(f["address"], 16)
        f["data_ref_addrs"] = [int(a, 16) for a in f["data_refs"]]
        f["call_addrs"] = [int(a, 16) for a in f["direct_calls"]]
    by_addr = {f["addr"]: f for f in functions}
    entries = set(by_addr)
    order = {f["addr"]: i for i, f in enumerate(functions)}

    evidence = string_clusters(image)
    clusters = evidence["clusters"]
    error_strings = evidence["cri_error_strings"]
    error_starts = [lo for lo, _ in error_strings]

    def hits(f: dict, prefix: str) -> bool:
        for lo, hi in (v for k, v in clusters.items() if k.startswith(prefix)):
            if any(lo <= a < hi for a in f["data_ref_addrs"]):
                return True
        return False

    def hits_error_string(f: dict) -> bool:
        for a in f["data_ref_addrs"]:
            i = bisect.bisect_right(error_starts, a) - 1
            if i >= 0 and error_strings[i][0] <= a < error_strings[i][1]:
                return True
        return False

    seed: dict[int, tuple[str, str]] = {}  # addr -> (region, basis)

    # 1. string evidence
    for f in functions:
        if hits(f, "cri-"):
            seed[f["addr"]] = ("cri", "cri-string-cluster")
        elif hits_error_string(f):
            seed[f["addr"]] = ("cri", "cri-error-string")
    for f in functions:
        if f["addr"] in seed:
            continue
        if hits(f, "crt-"):
            seed[f["addr"]] = ("crt", "crt-string-cluster")
        elif f.get("fid"):
            seed[f["addr"]] = ("crt", "ghidra-fid")

    # entry point, WinMain, CRT start chain
    # The PE header's entry point; Ghidra's external entry list also holds
    # other externally visible addresses (a data export at 0x164A350 here).
    entry = image.base + image.pe.OPTIONAL_HEADER.AddressOfEntryPoint
    if entry not in by_addr:
        sys.exit(f"entry point {entry:#x} is not a function")

    def closure(start: int, sink: set[int]) -> set[int]:
        seen = {start}
        queue = deque([start])
        while queue:
            a = queue.popleft()
            for c in by_addr[a]["call_addrs"]:
                if c in by_addr and c not in seen and c not in sink:
                    seen.add(c)
                    queue.append(c)
        return seen

    candidates = set(by_addr[entry]["call_addrs"])
    for c in list(candidates):
        if c in by_addr:
            candidates.update(by_addr[c]["call_addrs"])
    candidates = {c for c in candidates if c in by_addr}
    # Each candidate's closure is measured with the other candidates as sinks,
    # so the CRT startup routine that calls WinMain does not inherit WinMain's
    # closure and win by one function.
    sizes = {c: len(closure(c, candidates - {c})) for c in candidates}
    winmain = max(sizes, key=sizes.get)
    for a in closure(entry, {winmain}):
        seed.setdefault(a, ("crt", "crt-entry-chain"))

    # d3dx: pure wrappers over d3dx9_42 / d3d9 imports
    d3d_dlls = ("D3DX9_42.DLL!", "D3D9.DLL!")
    d3dx = {f["addr"] for f in functions
            if f["import_calls"] and all(i.upper().startswith(d3d_dlls) for i in f["import_calls"])
            and any(i.upper().startswith("D3DX9_42.DLL!") for i in f["import_calls"])
            and f["addr"] not in seed}
    while True:
        keep = {a for a in d3dx if all(c in d3dx for c in by_addr[a]["call_addrs"] if c in by_addr)}
        if keep == d3dx:
            break
        d3dx = keep
    for a in d3dx:
        seed[a] = ("d3dx", "d3dx-import-wrapper")

    # 2. Mod Loader names
    conflicts = []
    for a, name in names.items():
        if re.fullmatch(r"nullsub_\d+", name):
            continue
        if a in seed:
            conflicts.append({"address": f"0x{a:08X}", "modloader_name": name, "region": seed[a][0]})
        elif a in by_addr:
            seed[a] = ("game", "modloader-name")

    region_of = {a: r for a, (r, _) in seed.items()}
    basis_of = {a: b for a, (_, b) in seed.items()}

    def fill(regions: set[str]) -> None:
        positions = sorted(order[a] for a in seed)
        for lo, hi in zip(positions, positions[1:]):
            r = region_of[functions[lo]["addr"]]
            if r not in regions or r != region_of[functions[hi]["addr"]]:
                continue
            for k in range(lo + 1, hi):
                a = functions[k]["addr"]
                if a not in region_of:
                    region_of[a] = r
                    basis_of[a] = f"{r}-fill"

    # 3. contiguous fill of the string-evidence regions
    fill({"cri", "crt"})
    sinks = {a for a, r in region_of.items() if r != "game"}

    # function-pointer tables in data: pointers Ghidra found from data to entries
    # are not exported per se; approximate with the function-pointer operands of
    # code (push offset f) plus the data reference targets that are entries.
    pointer_runs: list[tuple[int, int, list[int]]] = []  # (lo, hi, [targets])
    data_pointers = raw.get("data_function_pointers", [])
    if data_pointers:
        items = sorted((int(p["from"], 16), int(p["to"], 16)) for p in data_pointers)
        run_lo, run_hi, run_targets = items[0][0], items[0][0] + 4, [items[0][1]]
        for src, dst in items[1:]:
            if src - run_hi <= TABLE_GAP:
                run_hi = src + 4
                run_targets.append(dst)
            else:
                pointer_runs.append((run_lo, run_hi, run_targets))
                run_lo, run_hi, run_targets = src, src + 4, [dst]
        pointer_runs.append((run_lo, run_hi, run_targets))
    run_starts = [r[0] for r in pointer_runs]

    def edges(a: int) -> set[int]:
        out = set(by_addr[a]["call_addrs"])
        for d in by_addr[a]["data_ref_addrs"]:
            if d in entries:
                out.add(d)
            elif pointer_runs:
                i = bisect.bisect_right(run_starts, d) - 1
                if i >= 0 and pointer_runs[i][0] <= d < pointer_runs[i][1]:
                    out.update(pointer_runs[i][2])
        return {c for c in out if c in by_addr}

    reached = {winmain}
    queue = deque([winmain])
    while queue:
        a = queue.popleft()
        if a in sinks:
            continue
        for c in edges(a):
            if c not in reached:
                reached.add(c)
                queue.append(c)
    # 4. reachability claims what is still unassigned
    for a in reached:
        if a not in region_of:
            seed[a] = ("game", "winmain-reachable")
            region_of[a] = "game"
            basis_of[a] = "winmain-reachable"

    # 5. contiguous fill of game
    fill({"game"})

    # 6. other
    out = []
    for f in functions:
        a = f["addr"]
        region = region_of.get(a, "other")
        basis = basis_of.get(a, "unclassified")
        record = {
            "address": f"0x{a:08X}",
            "size": f["size"],
            "name": names.get(a, f["name"]),
            "name_source": "modloader" if a in names else "ghidra",
            "region": region,
            "region_basis": basis,
            "ghidra_name": f["name"],
            "ghidra_name_source": f["name_source"],
        }
        for key in ("fid", "thunk", "thunk_of", "calling_convention", "signature_source",
                    "instructions", "fs0_sites", "indirect_call_sites",
                    "unresolved_indirect_jumps", "jump_tables", "direct_calls",
                    "import_calls", "data_refs"):
            if key in f:
                record[key] = f[key]
        out.append(record)

    info = {
        "entry_point": f"0x{entry:08X}",
        "winmain": f"0x{winmain:08X}",
        "winmain_candidates": {f"0x{c:08X}": n for c, n in sorted(sizes.items())},
        "string_clusters": {k: [f"0x{lo:08X}", f"0x{hi:08X}"] for k, (lo, hi) in clusters.items()},
        "stamps": {k: {"count": v["count"], "first": f"0x{v['first']:08X}", "last": f"0x{v['last']:08X}"}
                   for k, v in evidence["stamps"].items()},
        "crt_string_hits": evidence["crt_string_hits"],
        "cri_error_strings": len(error_strings),
        "modloader_conflicts": conflicts,
        "pointer_runs": len(pointer_runs),
    }
    return out, info


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--skip-ghidra", action="store_true",
                        help="reuse private/ghidra/ghidra-function-map.json and run.json")
    args = parser.parse_args()
    root = args.root or project_root()
    exe = root / EXE
    pins = json.loads((root / PINS).read_text())
    exe_sha = sha256(exe)
    pinned = pins["executables"]["sonic2app-unpacked.exe"]["sha256"]
    if exe_sha != pinned:
        sys.exit(f"{EXE} sha256 {exe_sha} != pinned {pinned}")

    image = Image(exe)
    names, by_macro = modloader_functions(root)
    targets = direct_call_sweep(image)
    text = image.section(".text")
    seeds_path = root / SEEDS_TXT
    seeds_path.parent.mkdir(parents=True, exist_ok=True)
    seed_lines = {a: "modloader" for a in names if text["start"] <= a < text["end"]}
    for t in targets:
        if text["start"] <= t < text["end"]:
            seed_lines.setdefault(t, "direct-call-sweep")
    seeds_path.write_text("".join(f"0x{a:08X} {src}\n" for a, src in sorted(seed_lines.items())))

    if args.skip_ghidra:
        run = json.loads((root / RUN_JSON).read_text())
    else:
        run = run_ghidra(root, exe, seeds_path)
    raw = json.loads((root / RAW_JSON).read_text())
    if raw["executable_sha256"].lower() != exe_sha:
        sys.exit("Ghidra analysed a different executable than the pinned one")

    functions, info = partition(raw, image, names)
    seed_result = {int(r["address"], 16): r for r in raw.get("seeds", [])}
    for f in functions:
        r = seed_result.get(int(f["address"], 16))
        f["seeded"] = bool(r and r["result"] == "created")
        if f["seeded"]:
            f["seed_source"] = r["source"]
    seed_summary: dict[str, dict[str, int]] = {}
    for r in seed_result.values():
        bucket = seed_summary.setdefault(r["source"], {})
        bucket[r["result"]] = bucket.get(r["result"], 0) + 1

    # audit against the linear direct-call sweep: Ghidra alone, then seeded
    entries = {int(f["address"], 16) for f in functions}
    unseeded = {int(a, 16) for a in raw.get("unseeded_entries", [])}
    starts = sorted(entries)
    bodies = {int(f["address"], 16): f["size"] for f in functions}

    def container(t: int) -> str | None:
        i = bisect.bisect_right(starts, t) - 1
        if i >= 0 and starts[i] <= t < starts[i] + bodies[starts[i]]:
            return f"0x{starts[i]:08X}"
        return None

    misses_alone = sorted(t for t in targets if t not in unseeded)
    sites_in_unseeded = sum(
        1 for t in misses_alone for site in targets[t]
        if container(site) is not None and int(container(site), 16) in unseeded)
    misses = sorted(t for t in targets if t not in entries)
    first_misses = []
    inside = 0
    for t in misses:
        c = container(t)
        inside += c is not None
        if len(first_misses) < 20:
            first_misses.append({"target": f"0x{t:08X}", "inside_function": c,
                                 "call_sites": len(targets[t])})
    first_misses_alone = [{"target": f"0x{t:08X}", "call_sites": len(targets[t])}
                          for t in misses_alone[:20]]

    # imports: pefile's table against what Ghidra resolved
    pe_imports = []
    for entry_ in image.pe.DIRECTORY_ENTRY_IMPORT:
        dll = entry_.dll.decode("latin-1").upper()
        for imp in entry_.imports:
            pe_imports.append(f"{dll}!{imp.name.decode('latin-1') if imp.name else f'Ordinal_{imp.ordinal}'}")
    ghidra_imports = {i["symbol"].upper() for i in raw["imports"]}
    imports_missing = sorted(i for i in pe_imports if i.upper() not in ghidra_imports)

    def counts(region: str) -> dict:
        fs = [f for f in functions if f["region"] == region]
        basis = {}
        for f in fs:
            basis[f["region_basis"]] = basis.get(f["region_basis"], 0) + 1
        return {"functions": len(fs), "bytes": sum(f["size"] for f in fs),
                "named_by_modloader": sum(f["name_source"] == "modloader" for f in fs),
                "basis": dict(sorted(basis.items()))}

    per_region = {r: counts(r) for r in REGIONS}
    jump_tables = sum(len(f["jump_tables"]) for f in functions)
    jump_table_targets = sum(len(t["targets"]) for f in functions for t in f["jump_tables"])
    outputs = {
        "functions_total": len(functions),
        "functions_ghidra_alone": len(unseeded),
        "functions_seeded": sum(f["seeded"] for f in functions),
        "seeds": {"written": len(seed_lines), "results_by_source": seed_summary},
        "bytes_total": sum(f["size"] for f in functions),
        "per_region": per_region,
        "named_by_modloader": sum(f["name_source"] == "modloader" for f in functions),
        "modloader_function_addresses": len(names),
        "modloader_addresses_by_macro": dict(sorted(by_macro.items())),
        "modloader_addresses_not_function_entries": sum(a not in entries for a in names),
        "modloader_addresses_expected_by_design": DESIGN_MODLOADER_FUNCTIONS,
        "modloader_conflicts_with_string_evidence": len(info["modloader_conflicts"]),
        "jump_tables": jump_tables,
        "jump_table_targets": jump_table_targets,
        "functions_with_unresolved_indirect_jumps": sum(f["unresolved_indirect_jumps"] > 0 for f in functions),
        "functions_with_fs0": sum(f["fs0_sites"] > 0 for f in functions),
        "fs0_sites": sum(f["fs0_sites"] for f in functions),
        "thunks": sum(bool(f.get("thunk")) for f in functions),
        "fid_matched": sum(bool(f.get("fid")) for f in functions),
        "imports_in_iat": len(raw["imports"]),
        "imports_pefile": len(pe_imports),
        "imports_pefile_not_resolved_by_ghidra": imports_missing,
        "entry_point": info["entry_point"],
        "winmain": info["winmain"],
        "winmain_candidates_closure_sizes": info["winmain_candidates"],
        "stamps": info["stamps"],
        "cri_error_strings": info["cri_error_strings"],
        "string_clusters": info["string_clusters"],
        "direct_call_audit": {
            "design_lower_bound": DESIGN_LOWER_BOUND,
            "sweep_distinct_targets": len(targets),
            "sweep_call_sites": sum(len(v) for v in targets.values()),
            "ghidra_alone": {
                "targets_that_are_function_entries": len(targets) - len(misses_alone),
                "targets_missing_from_function_list": len(misses_alone),
                "miss_call_sites_inside_ghidra_functions": sites_in_unseeded,
                "first_20_misses": first_misses_alone,
            },
            "seeded": {
                "targets_that_are_function_entries": len(targets) - len(misses),
                "targets_missing_from_function_list": len(misses),
                "misses_inside_another_function_body": inside,
                "misses_in_no_function": len(misses) - inside,
                "first_20_misses": first_misses,
            },
        },
        "analysis_wall_seconds": run["wall_seconds"],
        "ghidra_analysis_seconds": run["analysis_seconds"],
    }

    generated = root / FUNCTION_MAP
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text(json.dumps({
        "schema": "sa2-recomp-function-map/v1",
        "executable": EXE,
        "executable_sha256": exe_sha,
        "ghidra_version": raw["ghidra_version"],
        "image_base": raw["image_base"],
        "entry_points": raw["entry_points"],
        "winmain": info["winmain"],
        "memory_map": raw["memory_map"],
        "imports": raw["imports"],
        "region_rule": "see Documentation/scripts/s1_function_map.py module docstring",
        "string_clusters": info["string_clusters"],
        "modloader_conflicts": info["modloader_conflicts"],
        "functions": functions,
    }, indent=1) + "\n")

    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s1-function-map-v1",
        "date": time.strftime("%Y-%m-%d"),
        "question": "What is the analyzer's function list, how does it partition into regions, and does it cover the direct-call lower bound (design sections 2.3, 3.2, S1)?",
        "inputs": {
            "executable": EXE,
            "executable_sha256": exe_sha,
            "ghidra_version": run["ghidra_version"],
            "java_version": run["java_version"],
            "ghidra_script": JAVA_SCRIPT,
            "ghidra_script_sha256": sha256(root / JAVA_SCRIPT),
            "driver": "Documentation/scripts/s1_function_map.py",
            "driver_sha256": sha256(Path(__file__)),
            "modloader_header": HEADER,
            "modloader_header_sha256": sha256(root / HEADER),
            "ghidra_analysis": "default auto-analysis options of analyzeHeadless -import",
        },
        "command": {
            "driver": ".venv/bin/python Documentation/scripts/s1_function_map.py",
            "analyzeHeadless": run["command"],
            "env": run["env"],
        },
        "outputs": outputs,
        "artifacts": {
            "function_map": FUNCTION_MAP,
            "ghidra_raw_export": RAW_JSON,
            "seeds": SEEDS_TXT,
            "headless_log": "private/ghidra/headless.log",
        },
        "authority": "statically-translated",
        "note": "Counts only; names and addresses derived from the executable and the Mod Loader header stay in the ignored function map.",
    }
    (root / RECEIPT).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(outputs, indent=2))
    print(f"wrote {generated} and {root / RECEIPT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
