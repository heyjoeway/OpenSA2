#!/usr/bin/env python3
"""Camera Ledger (enumerate_sa2_camera_scope.py): count the camera scope of
Sonic Adventure 2 (2012 PC) from the executable and the shipped stage data.

Outputs, both under Documentation/scope/ by default:
  camera.csv  one row per camera mode the executable's dispatch table holds
  camera.md   totals, per-stage record counts, method (every address and
              assumption), receipts (sha256 of every input and of this script)

Inputs (all read-only):
  --exe       the unpacked executable (image base 0x400000)
  --game-dir  the installed resource/gd_PC/ folder holding stgNNcam.prs
  --repo      this repository (camera evidence documents, exporter, port)

Standard library only. Nothing is written outside --out-dir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_sonic_physics import rva_to_raw  # noqa: E402

IMAGE_BASE = 0x400000

# ---- executable addresses (every one is verified against code bytes below) --
MODE_TABLE_VA = 0x8ABD38        # _OBJ_CAMERAMODE[28]: {name*, func*, flag}
ADJUST_TABLE_VA = 0x8ABE88      # _OBJ_CAMERAADJUST[14]: {name*, func*}
MODE_TABLE_COUNT = 28
ADJUST_TABLE_COUNT = 14
MODE_RECORD_SIZE = 12
ADJUST_RECORD_SIZE = 8

# 0x4EBB40: lea ecx,[ebp+ebp*2]; mov edx,[ecx*4+0x8ABD3C]  (func = table[mode]+4)
MODE_LOOKUP_VA = 0x4EBB93
MODE_LOOKUP_BYTES = bytes.fromhex("8d4c6d00" "8b148d3cbd8a00")
# 0x4EBD11: mov eax,[edi*8+0x8ABE8C]  (adjust func = table[adjust]+4)
ADJUST_LOOKUP_VA = 0x4EBD11
ADJUST_LOOKUP_BYTES = bytes.fromhex("8b04fd8cbe8a00")
# 0x4F04B0 runner over camera volume records: skips mode 0, 0x11, 0x1B;
# record stride 0x98; volume-test selector at record+0xC.
RUNNER_VA = 0x4F04B0
RUNNER_SKIP_VA = 0x4F04EC
RUNNER_SKIP_BYTES = bytes.fromhex("85c0" "0f846b010000" "83f811" "0f8462010000" "83f81b" "0f8459010000")
RUNNER_STRIDE_VA = 0x4F0663
RUNNER_STRIDE_BYTES = bytes.fromhex("81c598000000" "81c698000000")
RUNNER_SKIPPED_MODES = (0, 0x11, 0x1B)
# 0x4F04B4: mov ebp,[eax*4+0x15224E8]; list i spans [ptr[i], ptr[i+1]).
LIST_TABLE_VA = 0x15224E8
LIST_TABLE_COUNT = 4
RUNNER_LIST_VA = 0x4F04B4
RUNNER_LIST_BYTES = bytes.fromhex("8b2c85e8245201")
# 0x453A90 LoadStageCameraFile: format string, demo-pair selection, copy target.
LOADER_VA = 0x453A90
LOADER_FORMAT_VA = 0x8B9078
LOADER_FORMAT = b"stg%02dcam.prs"
LOADER_DEMO_SELECT_VA = 0x453C28
LOADER_DEMO_SELECT_BYTES = bytes.fromhex("80f901" "7405" "80f903" "7527" "83f803" "7e22")
LOADER_COPY_VA = 0x453C6D
LOADER_COPY_BYTES = bytes.fromhex("6820fe5a02")
DEMO_STATE_VA = 0x174AFC0
# 0x453870 camset block swapper: entries of 96 bytes (mul 0xAAAAAAAB; shr edx,5).
CAMSET_SWAP_VA = 0x45387D
CAMSET_SWAP_BYTES = bytes.fromhex("b8abaaaaaa" "f7e1" "c1ea05")
CAMSET_ENTRY_SIZE = 96
# 0x4EC490: at stage start, records with mode 0x11 and slot 1 are set to mode 0.
COLLISION_DISABLE_VA = 0x4EC4A4
COLLISION_DISABLE_BYTES = bytes.fromhex("833811" "7511" "395008" "750c" "c70000000000")

CAMERA_RECORD_SIZE = 0x98
CAMERA_FILE_HEADER = 32
MODE_FUNCTION_RANGES = {}  # filled from the table: func -> (start, end)

# Functions whose relationship to the traced mode matters for evidence.
KNUKLES_VA = 0x4F47F0
CARMEL_VA = 0x4F2070
SPACE_VA = 0x4F2B10
INIT_VA = 0x4F16C0
INIT_END_VA = 0x4F1810
INIT_CONSTANTS = (0x905100, 0x905090)   # 60.0 and 30.0 doubles the port uses

# LevelIDs from the X-Hax SA2 Mod Loader SA2Enums.h (assumption; the header's
# sha256 is recorded in the receipts when it is present).
LEVEL_NAMES = {
    0: "BasicTest", 1: "KnucklesTest", 2: "SonicTest", 3: "GreenForest",
    4: "WhiteJungle", 5: "PumpkinHill", 6: "SkyRail", 7: "AquaticMine",
    8: "SecurityHall", 9: "PrisonLane", 10: "MetalHarbor", 11: "IronGate",
    12: "WeaponsBed", 13: "CityEscape", 14: "RadicalHighway", 15: "WeaponsBed2P",
    16: "WildCanyon", 17: "MissionStreet", 18: "DryLagoon", 19: "SonicVsShadow1",
    20: "TailsVsEggman1", 21: "SandOcean", 22: "CrazyGadget", 23: "HiddenBase",
    24: "EternalEngine", 25: "DeathChamber", 26: "EggQuarters", 27: "LostColony",
    28: "PyramidCave", 29: "TailsVsEggman2", 30: "FinalRush", 31: "GreenHill",
    32: "MeteorHerd", 33: "KnucklesVsRouge", 34: "CannonsCoreS", 35: "CannonsCoreE",
    36: "CannonsCoreT", 37: "CannonsCoreR", 38: "CannonsCoreK", 39: "MissionStreet2P",
    40: "FinalChase", 41: "WildCanyon2P", 42: "SonicVsShadow2", 43: "CosmicWall",
    44: "MadSpace", 45: "SandOcean2P", 46: "DryLagoon2P", 47: "PyramidRace",
    48: "HiddenBase2P", 49: "PoolQuest", 50: "PlanetQuest", 51: "DeckRace",
    52: "DowntownRace", 53: "CosmicWall2P", 54: "GrindRace", 55: "LostColony2P",
    56: "EternalEngine2P", 57: "MetalHarbor2P", 58: "IronGate2P", 59: "DeathChamber2P",
    60: "BigFoot", 61: "HotShot", 62: "FlyingDog", 63: "KingBoomBoo", 64: "EggGolemS",
    65: "Biolizard", 66: "FinalHazard", 67: "EggGolemE", 70: "Route101280",
    71: "KartRace", 90: "ChaoWorld",
}

# Evidence citations. A mode has evidence only when a repository file names
# its routine or mode as ported, traced or observed. Each phrase is checked
# for presence in the cited file; a missing phrase voids the citation.
# kind: observed | ported-subset | decompiled-only (decompiled-only is not
# evidence under the rule and yields evidence=no).
EVIDENCE = {
    13: [
        ("observed", "Documentation/CAMERA_EVIDENCE.md",
         "Carmel mode 13 supplies 703 of 717 valid frames"),
        ("observed", "Documentation/scripts/check_sa2_native_camera_trace.py",
         "if modes[13] < 100:"),
    ],
    19: [
        ("observed", "Documentation/CAMERA_EVIDENCE.md",
         "Easyset mode 19 initializes the camera"),
        ("observed", "Documentation/scripts/check_sa2_native_camera_trace.py",
         'row["camera_mode"] == "19"'),
        ("ported-subset", "Documentation/CAMERA_EVIDENCE.md",
         "| Easyset horizontal distance | double at `0x905100` = 60 source units | 6.0 m |"),
        ("ported-subset", "Assets/Runtime/PrototypeFollowCamera.cs",
         "NativeOpeningDistance = 6f;     // Easyset: 60 source units."),
    ],
    3: [
        ("ported-subset", "Documentation/CAMERA_EVIDENCE.md",
         "The five-frame player-position history and velocity lead at `0x4F47F0:81-112` are ported."),
        ("ported-subset", "Documentation/CAMERA_EVIDENCE.md",
         "The Sonic vertical-angle correction at `0x4F47F0:145-166` is also ported."),
        ("ported-subset", "Assets/Runtime/PrototypeFollowCamera.cs",
         "// Decompiled CameraMode_Knuckles: <=50 source units expands by 1.06"),
    ],
    2: [
        ("decompiled-only", "agents-journals/camera/2026-09-16-camera-parity-and-capture.md",
         "`CameraMode_Follow` VA `0x4F5760` targets the player entity"),
    ],
    10: [
        ("decompiled-only", "agents-journals/camera/2026-09-16-camera-parity-and-capture.md",
         "`CameraModeFix` copies Carmel work position offsets"),
    ],
    12: [
        ("decompiled-only", "Documentation/scripts/ExportSA2CameraFunctions.java",
         '{"004F2B10", "SA2_CameraModeCarmel"}'),
    ],
    23: [
        ("decompiled-only", "Documentation/scripts/ExportSA2CameraFunctions.java",
         '{"004F3DB0", "SA2_CameraKnucklesSpecial"}'),
    ],
}
ADJUST_EVIDENCE = {
    0: [("observed", "agents-journals/camera/2026-09-16-camera-parity-and-capture.md",
         "adjustment mode stays zero")],
    2: [("decompiled-only", "Documentation/scripts/ExportSA2CameraFunctions.java",
         '{"004F5620", "SA2_CameraAdjustHalf"}')],
    3: [("decompiled-only", "Documentation/scripts/ExportSA2CameraFunctions.java",
         '{"004F1970", "SA2_CameraAdjustInterpolate"}')],
}
for _i in range(4, 14):
    ADJUST_EVIDENCE[_i] = ADJUST_EVIDENCE[3]

REPO_EVIDENCE_FILES = [
    "Documentation/CAMERA_EVIDENCE.md",
    "Documentation/scripts/ExportSA2CameraFunctions.java",
    "Documentation/scripts/check_sa2_native_camera_trace.py",
    "Assets/Runtime/PrototypeFollowCamera.cs",
    "agents-journals/camera/2026-09-16-camera-parity-and-capture.md",
]
TRACE_CANDIDATES = [
    "native-run/metalharbor-flat-turn-camera-native-trace.csv",
    "native-run/metalharbor-camera-default-native-trace.csv",
]
MODLOADER_HEADERS = ["SA2Enums.h", "SA2Structs.h", "SA2Functions.h", "SA2Variables.h"]


# ---- helpers ---------------------------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prs_decompress(src: bytes) -> bytes:
    """SEGA PRS (LZ77 with a bit-flag stream), as SA Tools' FraGag.Compression.Prs."""
    out = bytearray()
    pos = 0
    flag = 0
    bits = 0

    def bit() -> int:
        nonlocal pos, flag, bits
        if bits == 0:
            flag = src[pos]
            pos += 1
            bits = 8
        value = flag & 1
        flag >>= 1
        bits -= 1
        return value

    while True:
        if bit():
            out.append(src[pos])
            pos += 1
            continue
        if bit():
            word = src[pos] | (src[pos + 1] << 8)
            pos += 2
            if word == 0:
                break
            amount = word & 7
            offset = (word >> 3) | -0x2000
            if amount == 0:
                amount = src[pos] + 1
                pos += 1
            else:
                amount += 2
        else:
            amount = 0
            if bit():
                amount |= 2
            if bit():
                amount |= 1
            amount += 2
            offset = src[pos] | -0x100
            pos += 1
        for _ in range(amount):
            out.append(out[len(out) + offset])
    return bytes(out)


class Executable:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        self.checks: list[tuple[str, bool, str]] = []

    def raw(self, va: int, length: int) -> int:
        offset, _ = rva_to_raw(self.data, va - IMAGE_BASE, length)
        return offset

    def read(self, va: int, length: int) -> bytes:
        offset = self.raw(va, length)
        return self.data[offset:offset + length]

    def u32(self, va: int) -> int:
        return struct.unpack_from("<I", self.data, self.raw(va, 4))[0]

    def cstring(self, va: int) -> str:
        offset = self.raw(va, 1)
        end = self.data.index(b"\0", offset)
        return self.data[offset:end].decode("latin1")

    def expect(self, label: str, va: int, expected: bytes) -> bool:
        actual = self.read(va, len(expected))
        ok = actual == expected
        self.checks.append((label, ok, f"0x{va:X} expected {expected.hex()} got {actual.hex()}"))
        return ok

    def call_targets(self, start: int, end: int) -> list[tuple[int, int]]:
        """Direct near calls (E8 rel32) whose target lies in .text; a linear
        scan, so the caller must only rely on targets it expects."""
        code = self.read(start, end - start)
        found = []
        for i in range(len(code) - 4):
            if code[i] == 0xE8:
                rel = struct.unpack_from("<i", code, i + 1)[0]
                target = start + i + 5 + rel
                if 0x401000 <= target < 0x871000:
                    found.append((start + i, target))
        return found

    def immediates(self, start: int, end: int, values: tuple[int, ...]) -> dict[int, list[int]]:
        code = self.read(start, end - start)
        hits: dict[int, list[int]] = {v: [] for v in values}
        for value in values:
            needle = struct.pack("<I", value)
            i = code.find(needle)
            while i != -1:
                hits[value].append(start + i)
                i = code.find(needle, i + 1)
        return hits


# ---- executable side -------------------------------------------------------
def read_dispatch(exe: Executable) -> tuple[list[dict], list[dict]]:
    exe.expect("mode lookup: func = _OBJ_CAMERAMODE[mode].fn (stride 12, +4)",
               MODE_LOOKUP_VA, MODE_LOOKUP_BYTES)
    exe.expect("adjust lookup: func = _OBJ_CAMERAADJUST[adjust].fn (stride 8, +4)",
               ADJUST_LOOKUP_VA, ADJUST_LOOKUP_BYTES)
    exe.expect("runner skips record modes 0, 0x11, 0x1B", RUNNER_SKIP_VA, RUNNER_SKIP_BYTES)
    exe.expect("runner record stride 0x98", RUNNER_STRIDE_VA, RUNNER_STRIDE_BYTES)
    exe.expect("runner reads list pointers at 0x15224E8", RUNNER_LIST_VA, RUNNER_LIST_BYTES)
    exe.expect("loader format string stg%02dcam.prs", LOADER_FORMAT_VA, LOADER_FORMAT + b"\0")
    exe.expect("loader selects blocks 2/3 when byte 0x174AFC0 is 1 or 3 and count > 3",
               LOADER_DEMO_SELECT_VA, LOADER_DEMO_SELECT_BYTES)
    exe.expect("loader copies the record block to 0x25AFE20", LOADER_COPY_VA, LOADER_COPY_BYTES)
    exe.expect("camset swapper divides the block by 96", CAMSET_SWAP_VA, CAMSET_SWAP_BYTES)
    exe.expect("stage start clears mode 0x11 records with slot 1", COLLISION_DISABLE_VA,
               COLLISION_DISABLE_BYTES)
    assert MODE_TABLE_VA + MODE_TABLE_COUNT * MODE_RECORD_SIZE == ADJUST_TABLE_VA

    modes = []
    for index in range(MODE_TABLE_COUNT):
        va = MODE_TABLE_VA + index * MODE_RECORD_SIZE
        name_ptr, func, flag = struct.unpack_from("<3I", exe.data, exe.raw(va, 12))
        modes.append({"id": index, "name": exe.cstring(name_ptr), "func": func, "flag": flag,
                      "name_ptr": name_ptr})
    adjusts = []
    for index in range(ADJUST_TABLE_COUNT):
        va = ADJUST_TABLE_VA + index * ADJUST_RECORD_SIZE
        name_ptr, func = struct.unpack_from("<2I", exe.data, exe.raw(va, 8))
        adjusts.append({"id": index, "name": exe.cstring(name_ptr), "func": func})
    list_ptrs = [exe.u32(LIST_TABLE_VA + 4 * i) for i in range(LIST_TABLE_COUNT)]
    exe.checks.append(("list pointer table 0x15224E8 = 0x25AFE20, 0x25BFE20, 0x25C3E20, 0x25C7E20",
                       list_ptrs == [0x25AFE20, 0x25BFE20, 0x25C3E20, 0x25C7E20],
                       ", ".join(f"0x{p:X}" for p in list_ptrs)))
    return modes, adjusts


def function_ranges(modes: list[dict]) -> dict[int, tuple[int, int]]:
    """Approximate each mode function's extent as [entry, next table entry)."""
    entries = sorted({m["func"] for m in modes if m["func"]})
    ranges = {}
    for i, entry in enumerate(entries):
        nxt = entries[i + 1] if i + 1 < len(entries) else entry + 0x1000
        ranges[entry] = (entry, min(nxt, entry + 0x1000))
    return ranges


def parse_exporter_labels(repo: Path) -> dict[int, str]:
    text = (repo / "Documentation/scripts/ExportSA2CameraFunctions.java").read_text()
    return {int(a, 16): n for a, n in re.findall(r'\{"([0-9A-Fa-f]{8})", "([^"]+)"\}', text)}


def parse_modloader_labels(headers: dict[str, Path]) -> dict[int, str]:
    path = headers.get("SA2Functions.h")
    if not path:
        return {}
    text = path.read_text(errors="replace")
    return {int(a, 16): n for n, a in
            re.findall(r"CameraDebug_(\w+),[^;]*0x([0-9A-Fa-f]+)\)", text)}


def label_agrees(table_name: str, label: str | None) -> str:
    if label is None:
        return ""
    canon = table_name.lower().replace("knukles", "knuckles").replace("_", "")
    other = label
    for prefix in ("SA2_CameraMode", "SA2_Camera", "CameraDebug_"):
        if other.startswith(prefix):
            other = other[len(prefix):]
    return "agrees" if canon == other.lower().replace("_", "") else "disagrees"


# ---- shipped data side -----------------------------------------------------
def record_class(rec: tuple[int, int, int, int]) -> str:
    """Classify a record by the runner's own gates at 0x4F04B0."""
    mode, adjust, slot, vtype = rec
    if mode == 0:
        return "zero"
    if mode >= MODE_TABLE_COUNT:
        return "outside"            # first word is not a table index
    if mode in RUNNER_SKIPPED_MODES:
        return "skipped-mode"       # 0x11 or 0x1B: runner never tests it
    if vtype not in (1, 2, 3):
        return "no-volume-test"     # +0xC selects no test: never dispatched
    if adjust >= ADJUST_TABLE_COUNT or slot >= 16:
        return "field-out-of-range"
    return "dispatchable"


def parse_camera_file(raw: bytes) -> dict:
    count = struct.unpack_from(">I", raw, 0)[0]
    sizes = list(struct.unpack_from(f">{count - 1}I", raw, 4)) if count > 1 else []
    result = {"header_count": count, "sizes": sizes, "blocks": [], "verified": True, "notes": []}
    if CAMERA_FILE_HEADER + sum(sizes) != len(raw):
        result["verified"] = False
        result["notes"].append(f"block sizes sum to {sum(sizes)} but body is {len(raw) - 32}")
    offset = CAMERA_FILE_HEADER
    for index, size in enumerate(sizes):
        block = raw[offset:offset + size]
        offset += size
        role = "records" if index % 2 == 0 else "camset"
        entry = {"index": index, "size": size, "role": role}
        if role == "records":
            entry["slots"] = size // CAMERA_RECORD_SIZE
            entry["remainder"] = size % CAMERA_RECORD_SIZE
            if entry["remainder"] and size != 0x10000:
                result["verified"] = False
                result["notes"].append(f"block {index}: {size} is not a multiple of 0x98")
            tail = block[entry["slots"] * CAMERA_RECORD_SIZE:]
            entry["tail_mode"] = struct.unpack_from(">I", tail, 0)[0] if len(tail) >= 4 else None
            # Byte order per block: the executable decides by memory region
            # (0x429840 via the table at 0x1A55800), which is not decoded
            # here. The order that yields in-range fields for more non-zero
            # records wins; ties keep big-endian.
            decoded = {}
            for order in (">", "<"):
                records = []
                for i in range(entry["slots"]):
                    rec = block[i * CAMERA_RECORD_SIZE:(i + 1) * CAMERA_RECORD_SIZE]
                    records.append(struct.unpack_from(order + "4I", rec, 0))
                decoded[order] = records
            score = {o: sum(1 for r in decoded[o] if r[0] and record_class(r) != "outside")
                     for o in decoded}
            order = "<" if score["<"] > score[">"] else ">"
            entry["byte_order"] = "little" if order == "<" else "big"
            entry["records"] = decoded[order]
            entry["classes"] = Counter(record_class(r) for r in entry["records"])
            entry["outside_with_valid_type"] = sum(
                1 for r in entry["records"] if r[0] >= MODE_TABLE_COUNT and r[3] in (1, 2, 3))
        else:
            entry["entries"] = size // CAMSET_ENTRY_SIZE
            if size not in (0, 0x4000, 0x4020):
                result["notes"].append(f"block {index}: camset size 0x{size:X} not seen elsewhere")
        result["blocks"].append(entry)
    return result


def enumerate_game_data(game_dir: Path, mode_count: int) -> tuple[list[dict], list[str]]:
    stages = []
    field_violations = []
    for path in sorted(game_dir.glob("stg*cam.prs")):
        stage = int(re.match(r"stg(\d+)cam\.prs", path.name).group(1))
        compressed = path.read_bytes()
        raw = prs_decompress(compressed)
        parsed = parse_camera_file(raw)
        parsed.update({"stage": stage, "file": path.name, "compressed": len(compressed),
                       "decompressed": len(raw), "sha256": hashlib.sha256(compressed).hexdigest(),
                       "level": LEVEL_NAMES.get(stage, "")})
        for block in parsed["blocks"]:
            if block["role"] != "records":
                continue
            for i, rec in enumerate(block["records"]):
                if record_class(rec) == "field-out-of-range":
                    field_violations.append(f"{path.name} block {block['index']} record {i}: "
                                            f"mode={rec[0]} adjust={rec[1]} slot={rec[2]} type={rec[3]}")
        stages.append(parsed)
    return stages, field_violations


# ---- evidence side ---------------------------------------------------------
def check_citations(repo: Path, table: dict[int, list]) -> dict[int, list[dict]]:
    result = {}
    cache = {}
    for key, citations in table.items():
        rows = []
        for kind, rel, phrase in citations:
            path = repo / rel
            if rel not in cache:
                cache[rel] = path.read_text(errors="replace") if path.exists() else None
            text = cache[rel]
            present = text is not None and phrase in text
            rows.append({"kind": kind, "file": rel, "phrase": phrase, "present": present})
        result[key] = rows
    return result


def evidence_verdict(rows: list[dict]) -> tuple[str, str, str]:
    kinds = sorted({r["kind"] for r in rows if r["present"] and r["kind"] != "decompiled-only"})
    if kinds:
        sources = sorted({r["file"] for r in rows if r["present"] and r["kind"] != "decompiled-only"})
        return "yes", "+".join(kinds), "; ".join(sources)
    if any(r["present"] and r["kind"] == "decompiled-only" for r in rows):
        sources = sorted({r["file"] for r in rows if r["present"]})
        return "no", "decompiled-only", "; ".join(sources)
    if rows:
        return "no", "citation-not-found", ""
    return "no", "", ""


def find_trace(private_work: Path | None) -> tuple[Path | None, dict]:
    if private_work is None:
        return None, {"status": "SA2_PRIVATE_WORK not set"}
    # Every candidate is examined; a present file without the camera mode
    # column does not stop the search (review finding: the first version
    # returned on the first existing path, so a defective first file would
    # have hidden a usable second one).
    examined = []
    for rel in TRACE_CANDIDATES:
        path = private_work / rel
        if not path.exists():
            examined.append(f"{rel}: absent")
            continue
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames or []
            if "camera_mode" not in columns:
                examined.append(f"{rel}: present, no camera_mode column ({len(columns)} columns), "
                                f"sha256 {sha256_file(path)}")
                continue
            modes = Counter(int(r["camera_mode"]) for r in reader if r.get("player") == "1")
        return path, {"status": "camera_mode histogram read", "modes": dict(modes),
                      "sha256": sha256_file(path), "examined": examined}
    return None, {"status": "no candidate trace holds a camera_mode column", "examined": examined}


# ---- output ----------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    private = os.environ.get("SA2_PRIVATE_WORK")
    parser.add_argument("--exe", type=Path,
                        default=Path(private) / "LocalAssets/SA2/source/sonic2app-unpacked.exe" if private else None)
    parser.add_argument("--game-dir", type=Path,
                        default=Path.home() / ".local/share/Steam/steamapps/common/Sonic Adventure 2/resource/gd_PC")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--modloader-include", type=Path,
                        default=Path(private) / "tools/sa2-mod-loader/SA2ModLoader/include" if private else None)
    args = parser.parse_args()
    if args.exe is None or not args.exe.exists():
        print("FAIL: --exe missing (set SA2_PRIVATE_WORK or pass --exe)", file=sys.stderr)
        return 2
    out_dir = args.out_dir or (args.repo / "Documentation/scope")
    out_dir.mkdir(parents=True, exist_ok=True)

    exe = Executable(args.exe)
    modes, adjusts = read_dispatch(exe)
    ranges = function_ranges(modes)
    exporter = parse_exporter_labels(args.repo)
    headers = {}
    if args.modloader_include and args.modloader_include.exists():
        headers = {n: args.modloader_include / n for n in MODLOADER_HEADERS
                   if (args.modloader_include / n).exists()}
    modloader = parse_modloader_labels(headers)

    # Which mode functions reach the ported routine 0x4F47F0 directly.
    reach_knukles = {}
    for mode in modes:
        if not mode["func"] or mode["func"] not in ranges:
            continue
        start, end = ranges[mode["func"]]
        sites = [f"0x{s:X}" for s, t in exe.call_targets(start, end) if t == KNUKLES_VA]
        if sites:
            reach_knukles[mode["id"]] = sites
    init_hits = exe.immediates(INIT_VA, INIT_END_VA, INIT_CONSTANTS)
    exe.checks.append(("0x4F16C0 (table INIT, mode 19) references 0x905100 and 0x905090",
                       all(init_hits[v] for v in INIT_CONSTANTS),
                       "; ".join(f"0x{v:X} at {[hex(a) for a in init_hits[v]]}" for v in INIT_CONSTANTS)))
    exe.checks.append(("0x4F2070 (table CARMEL, mode 13) calls 0x4F47F0 directly",
                       13 in reach_knukles, ", ".join(reach_knukles.get(13, []))))

    stages, violations = enumerate_game_data(args.game_dir, MODE_TABLE_COUNT)

    # Aggregate records per mode.
    per_mode_main = Counter()
    per_mode_demo = Counter()
    stages_per_mode = defaultdict(set)
    per_adjust = Counter()
    per_vtype = Counter()
    per_slot = Counter()
    class_totals = {"main": Counter(), "demo": Counter()}
    mode17_slot1 = 0
    little_endian_blocks = []
    outside_dispatch = []
    for stage in stages:
        for block in stage["blocks"]:
            if block["role"] != "records":
                continue
            demo = block["index"] >= 2
            class_totals["demo" if demo else "main"].update(block["classes"])
            if block["byte_order"] == "little":
                little_endian_blocks.append(f"{stage['file']} block {block['index']}")
            if block["outside_with_valid_type"]:
                outside_dispatch.append(f"{stage['file']} block {block['index']}: {block['outside_with_valid_type']}")
            for rec in block["records"]:
                mode, adjust, slot, vtype = rec
                if mode == 0 or mode >= MODE_TABLE_COUNT:
                    continue
                (per_mode_demo if demo else per_mode_main)[mode] += 1
                stages_per_mode[mode].add(stage["stage"])
                if not demo:
                    per_adjust[adjust] += 1
                    per_vtype[vtype] += 1
                    per_slot[slot] += 1
                if mode == 0x11 and slot == 1:
                    mode17_slot1 += 1

    citations = check_citations(args.repo, EVIDENCE)
    adjust_citations = check_citations(args.repo, ADJUST_EVIDENCE)
    trace_path, trace_info = find_trace(Path(private) if private else None)

    # ---- CSV
    csv_path = out_dir / "camera.csv"
    with csv_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["mode_id", "name_in_executable", "function_va", "table_flag",
                         "runner_skips", "records_main", "records_demo", "stages_using",
                         "evidence", "evidence_kind", "evidence_source",
                         "exporter_label", "exporter_agrees", "modloader_label", "modloader_agrees",
                         "calls_0x4F47F0_at"])
        for mode in modes:
            verdict, kind, source = evidence_verdict(citations.get(mode["id"], []))
            exp = exporter.get(mode["func"]) if mode["func"] else None
            mlb = modloader.get(mode["func"]) if mode["func"] else None
            writer.writerow([
                mode["id"], mode["name"], f"0x{mode['func']:X}" if mode["func"] else "null",
                mode["flag"], "yes" if mode["id"] in RUNNER_SKIPPED_MODES else "no",
                per_mode_main.get(mode["id"], 0), per_mode_demo.get(mode["id"], 0),
                len(stages_per_mode.get(mode["id"], ())),
                verdict, kind, source,
                exp or "", label_agrees(mode["name"], exp),
                mlb or "", label_agrees(mode["name"], mlb),
                " ".join(reach_knukles.get(mode["id"], [])),
            ])

    # ---- totals
    dispatched = [m for m in modes if m["func"]]
    used_main = sorted(per_mode_main)
    used_any = sorted(set(per_mode_main) | set(per_mode_demo))
    used_dispatchable = [m for m in used_main if m not in RUNNER_SKIPPED_MODES]
    with_evidence = [m["id"] for m in modes if evidence_verdict(citations.get(m["id"], []))[0] == "yes"]
    used_with_evidence = [m for m in used_main if m in with_evidence]
    main_records = sum(per_mode_main.values())
    demo_records = sum(per_mode_demo.values())
    stages_with_records = [s for s in stages if any(b["role"] == "records" and b["classes"]["dispatchable"]
                                                    for b in s["blocks"])]
    dispatchable_main = class_totals["main"]["dispatchable"]
    dispatchable_demo = class_totals["demo"]["dispatchable"]
    adjust_with_evidence = [a["id"] for a in adjusts if evidence_verdict(adjust_citations.get(a["id"], []))[0] == "yes"]

    # ---- receipts
    receipts = [("executable", args.exe, sha256_file(args.exe)),
                ("script", Path(__file__).resolve(), sha256_file(Path(__file__).resolve()))]
    for rel in REPO_EVIDENCE_FILES:
        path = args.repo / rel
        if path.exists():
            receipts.append((rel, path, sha256_file(path)))
    for name, path in sorted(headers.items()):
        receipts.append((f"modloader {name}", path, sha256_file(path)))
    if trace_path:
        receipts.append(("native trace (searched for camera_mode)", trace_path, trace_info["sha256"]))
    extra_files = sorted(list(args.game_dir.glob("camset*.bin")) + list(args.game_dir.glob("ChaoRaceCam*.bin")))

    # ---- Markdown
    lines = []
    w = lines.append
    w("# SA2 camera scope")
    w("")
    w("Generated by `Documentation/scripts/enumerate_sa2_camera_scope.py` from the")
    w("executable and the shipped `stgNNcam.prs` files. Counts, addresses and hashes;")
    w("no assessment. Companion table: `camera.csv` (one row per dispatch-table mode).")
    w("")
    w("## Totals")
    w("")
    w(f"| Quantity | Count |")
    w(f"| --- | --- |")
    w(f"| Camera mode table entries (`_OBJ_CAMERAMODE` at 0x{MODE_TABLE_VA:X}) | {len(modes)} |")
    w(f"| Entries with a non-null function (modes dispatched) | {len(dispatched)} |")
    w(f"| Distinct mode functions | {len({m['func'] for m in dispatched})} |")
    w(f"| Entries with a null function | {len(modes) - len(dispatched)} ({', '.join(f'{m['id']} {m['name']}' for m in modes if not m['func'])}) |")
    w(f"| Record modes the runner at 0x{RUNNER_VA:X} skips | {len(RUNNER_SKIPPED_MODES)} ({', '.join(str(m) for m in RUNNER_SKIPPED_MODES)}) |")
    w(f"| Camera adjust table entries (`_OBJ_CAMERAADJUST` at 0x{ADJUST_TABLE_VA:X}) | {len(adjusts)} ({len([a for a in adjusts if a['func']])} non-null, {len({a['func'] for a in adjusts if a['func']})} distinct functions) |")
    w(f"| Modes used by shipped main-layout records | {len(used_main)} ({', '.join(str(m) for m in used_main)}) |")
    w(f"| Modes used by shipped main-layout records that the runner dispatches (17 and 27 excluded) | {len(used_dispatchable)} ({', '.join(str(m) for m in used_dispatchable)}) |")
    w(f"| Modes used by shipped records including demo layouts | {len(used_any)} ({', '.join(str(m) for m in used_any)}) |")
    w(f"| Adjust ids used by shipped main-layout records | {len(per_adjust)} ({', '.join(str(a) for a in sorted(per_adjust))}) |")
    w(f"| Modes with evidence (observed or ported subset) | {len(with_evidence)} ({', '.join(str(m) for m in with_evidence)}) |")
    w(f"| Modes used by shipped main records that have evidence | {len(used_with_evidence)} ({', '.join(str(m) for m in used_with_evidence)}) |")
    w(f"| Adjust ids with evidence | {len(adjust_with_evidence)} ({', '.join(str(a) for a in adjust_with_evidence)}) |")
    w(f"| `stgNNcam.prs` files | {len(stages)} |")
    w(f"| Files with at least one dispatchable record | {len(stages_with_records)} |")
    w(f"| Records with a table mode (1..27), main layouts | {main_records} |")
    w(f"| Records with a table mode (1..27), demo layouts (blocks 2/3) | {demo_records} |")
    w(f"| Dispatchable records (mode 1..27 not 17/27, +0xC in 1..3, adjust<14, slot<16), main | {dispatchable_main} |")
    w(f"| Dispatchable records, demo | {dispatchable_demo} |")
    w(f"| Record classes, main | {', '.join(f'{k}: {v}' for k, v in sorted(class_totals['main'].items()))} |")
    w(f"| Record classes, demo | {', '.join(f'{k}: {v}' for k, v in sorted(class_totals['demo'].items()))} |")
    w(f"| Records of mode 17 with slot 1 (cleared at stage start by 0x4EC490) | {mode17_slot1} |")
    w(f"| Volume-test selector (record +0xC) counts, main | {', '.join(f'{k}: {v}' for k, v in sorted(per_vtype.items()))} |")
    w(f"| Slot (record +8) counts, main | {', '.join(f'{k}: {v}' for k, v in sorted(per_slot.items()))} |")
    w("")
    w("## Modes")
    w("")
    w("| id | name (executable string) | function | flag | main records | demo records | stages | evidence | kind | exporter label | Mod Loader label |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for mode in modes:
        verdict, kind, _ = evidence_verdict(citations.get(mode["id"], []))
        exp = exporter.get(mode["func"]) if mode["func"] else None
        mlb = modloader.get(mode["func"]) if mode["func"] else None
        exp_s = f"{exp} ({label_agrees(mode['name'], exp)})" if exp else ""
        mlb_s = f"CameraDebug_{mlb} ({label_agrees(mode['name'], mlb)})" if mlb else ""
        w(f"| {mode['id']} | {mode['name']} | {'0x%X' % mode['func'] if mode['func'] else 'null'} | {mode['flag']} | "
          f"{per_mode_main.get(mode['id'], 0)} | {per_mode_demo.get(mode['id'], 0)} | {len(stages_per_mode.get(mode['id'], ()))} | "
          f"{verdict} | {kind} | {exp_s} | {mlb_s} |")
    w("")
    w("Adjust table:")
    w("")
    w("| id | name | function | main records | evidence | kind |")
    w("| --- | --- | --- | --- | --- | --- |")
    for adjust in adjusts:
        verdict, kind, _ = evidence_verdict(adjust_citations.get(adjust["id"], []))
        w(f"| {adjust['id']} | {adjust['name']} | {'0x%X' % adjust['func'] if adjust['func'] else 'null'} | "
          f"{per_adjust.get(adjust['id'], 0)} | {verdict} | {kind} |")
    w("")
    w("## Camera records per stage")
    w("")
    w("Main = blocks 0/1; demo = blocks 2/3, which the loader selects when the byte at")
    w(f"0x{DEMO_STATE_VA:X} is 1 or 3 (Mod Loader name `CurrentDemoState`) and the header count exceeds 3.")
    w("`slots` is block size / 0x98; `disp` counts dispatchable records (the runner's gates);")
    w("`other` counts non-zero records the runner never dispatches (skipped mode 17/27, no volume")
    w("test, first word outside the table). `modes` lists table modes present. Camset entries are block size / 96.")
    w("")
    w("| stage | level (Mod Loader enum) | file | prs bytes | decompressed | header count | main order | main slots | main disp | main other | main modes | camset main | demo order | demo slots | demo disp | demo other | demo modes | camset demo | verified |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for stage in stages:
        blocks = stage["blocks"]

        def rec(i):
            if i < len(blocks) and blocks[i]["role"] == "records" and blocks[i]["size"]:
                b = blocks[i]
                c = b["classes"]
                other = sum(v for k, v in c.items() if k not in ("zero", "dispatchable"))
                modes_used = sorted({r[0] for r in b["records"] if 0 < r[0] < MODE_TABLE_COUNT})
                return b["byte_order"], b["slots"], c["dispatchable"], other, ",".join(map(str, modes_used))
            return "", 0, 0, 0, ""

        def cam(i):
            return blocks[i]["entries"] if i < len(blocks) and blocks[i]["role"] == "camset" else 0

        m_order, m_slots, m_disp, m_other, m_modes = rec(0)
        d_order, d_slots, d_disp, d_other, d_modes = rec(2)
        verified = "yes" if stage["verified"] else "no: " + "; ".join(stage["notes"])
        w(f"| {stage['stage']} | {stage['level']} | {stage['file']} | {stage['compressed']} | {stage['decompressed']} | "
          f"{stage['header_count']} | {m_order} | {m_slots} | {m_disp} | {m_other} | {m_modes} | {cam(1)} | "
          f"{d_order} | {d_slots} | {d_disp} | {d_other} | {d_modes} | {cam(3)} | {verified} |")
    w("")
    w("Other camera files in the same folder, listed and hashed but not decoded:")
    w("")
    for path in extra_files:
        w(f"- `{path.name}` {path.stat().st_size} bytes sha256 `{sha256_file(path)}`")
    w("")
    w("## Method")
    w("")
    w("Every address is a virtual address in the unpacked executable (image base 0x400000).")
    w("Each code-byte check below was run by this script; a failed check is printed as such.")
    w("")
    for label, ok, detail in exe.checks:
        w(f"- {'PASS' if ok else 'FAIL'}: {label} -- {detail}")
    w("")
    w("Dispatch: `_OBJ_CAMERAMODE` is 28 records of {name*, function*, flag} at")
    w(f"0x{MODE_TABLE_VA:X}..0x{ADJUST_TABLE_VA:X}; the setter 0x4EBB40 stores table[mode].function into")
    w("`CameraParam.pObjCameraMode` (CameraInfo+0x258 per slot, 0x114 bytes per slot). The 14-record")
    w(f"adjust table follows at 0x{ADJUST_TABLE_VA:X}; 0x4EBCD0 reads table[adjust].function. Names are the")
    w("executable's own strings at the record's first pointer; where the repository's Ghidra exporter")
    w("or the Mod Loader `CameraDebug_*` list attaches a different name to the same address, the table")
    w("above says so mechanically (name substring test). Two sources agree with the executable's")
    w("strings against the exporter: the camera journal (`agents-journals/camera/2026-09-16-camera-parity-and-capture.md`,")
    w("the decoded mode table line) names mode 12 SPACE `0x4F2B10` and mode 13 CARMEL `0x4F2070`, as the")
    w("table strings do; the exporter's labels for those two functions (and for `0x4F16C0` INIT versus")
    w("EASYSET) are the ones out of step, and the Mod Loader's `CameraDebug_*` list is shifted by one.")
    w("")
    w("Camera volume records: `LoadStageCameraFile` 0x453A90 formats `stg%02dcam.prs`, PRS-decompresses")
    w("it, reads a 32-byte header (big-endian u32 count, then count-1 big-endian u32 block sizes;")
    w("verified here by the sizes summing to the body length for every file), and copies block 0 to")
    w("0x25AFE20 and block 1 to 0x25C7E20 and 0x25CBE20 (one copy per player, swapped by 0x453870 in")
    w("96-byte entries). When the byte at 0x174AFC0 is 1 or 3 and the count exceeds 3 it uses blocks")
    w("2 and 3 instead. The record list pointers at 0x15224E8 are static: list 0 spans 0x25AFE20..0x25BFE20")
    w("(0x10000 bytes, 431 whole records), lists 1 and 2 are 0x4000 each and are only filled when a")
    w("block exceeds 0x10000, which no shipped block does. The runner 0x4F04B0 walks list 0 for every")
    w("player, skips records whose mode is 0, 0x11 or 0x1B, dispatches the volume test on record+0xC")
    w("(1: 0x4F0000, 2: 0x4F0090, 3: 0x4F02F0), and on a hit sets the slot at record+8 to the mode at")
    w("record+0 with the adjust at record+4. Record fields are big-endian on disk and swapped by 0x453350.")
    w("")
    w("Assumptions:")
    w("")
    w("- Record fields +0 mode, +4 adjust, +8 slot, +0xC volume-test selector are read from the runner's")
    w("  use of them; the remaining 0x88 bytes (positions, extents, angles) are not decoded here.")
    w("- `0x10000` record blocks are padded: their 431 whole records are counted and the 24-byte")
    w("  remainder's mode word is checked to be 0 (it is the first word of an inert record). Their")
    w("  padding can hold stale non-record data (first word outside the table); such records are")
    w("  counted under `outside` and are inert unless +0xC is 1..3, which is reported separately.")
    w("- Byte order is *scored* per record block, not decoded: the order that yields in-range fields")
    w("  for more non-zero records wins and ties keep big-endian; the executable's own region decision")
    w("  at 0x429840 is undecoded (see the runner above). The blocks that")
    w("  decoded as little-endian are listed under findings.")
    w("- Level names come from the Mod Loader `LevelIDs` enum (SA2Enums.h, hashed in the receipts).")
    w("- Camset blocks and `camset0013S.bin` (0x4000/0x4020 bytes) hold 96-byte entries per the swapper;")
    w("  their contents are not decoded. `ChaoRaceCam*.bin` belong to the Chao race module")
    w("  (`alr_camera_data.c` strings in the executable) and are not decoded.")
    w("- Evidence: a mode counts as having evidence only when a cited repository file contains the")
    w("  cited phrase naming the mode or its routine as observed or ported. Decompiled-only citations")
    w("  are listed but yield `evidence=no`.")
    w("")
    w("Findings that did not verify or need stating:")
    w("")
    tail_bad = [f"{s['file']} block {b['index']}" for s in stages for b in s["blocks"]
                if b["role"] == "records" and b["remainder"] and b.get("tail_mode") not in (0, None)]
    w(f"- Records with a table mode and a volume test but adjust>=14 or slot>=16: {len(violations)}"
      + ("" if not violations else " -- " + "; ".join(violations[:10])))
    w(f"- Record blocks that decoded as little-endian: {len(little_endian_blocks)}"
      + (" -- " + ", ".join(little_endian_blocks) if little_endian_blocks else ""))
    w(f"- Records with first word outside the table but +0xC in 1..3 (would index past the table): "
      f"{len(outside_dispatch)}" + (" -- " + ", ".join(outside_dispatch) if outside_dispatch else ""))
    w(f"- Padded blocks whose remainder mode word is non-zero: {len(tail_bad)}" + (" -- " + ", ".join(tail_bad) if tail_bad else ""))
    unverified = [s["file"] for s in stages if not s["verified"]]
    w(f"- Files whose header did not verify: {len(unverified)}" + (" -- " + ", ".join(unverified) if unverified else ""))
    w(f"- Native trace search: {trace_info.get('status')}"
      + (f"; modes {trace_info['modes']}" if "modes" in trace_info else "")
      + (f" (`{trace_path}`)" if trace_path else ""))
    for entry in trace_info.get("examined", []):
        w(f"  - {entry}")
    w("  The observed-mode citations therefore rest on the cited documents, not on a re-read trace.")
    for mode_id, rows in sorted(citations.items()):
        missing = [r for r in rows if not r["present"]]
        for r in missing:
            w(f"- Citation not found for mode {mode_id}: `{r['file']}` lacks `{r['phrase']}`")
    for mode_id in sorted(reach_knukles):
        w(f"- Mode {mode_id} function calls 0x4F47F0 (the ported KNUKLES routine) at {', '.join(reach_knukles[mode_id])}")
    for mode in modes:
        exp = exporter.get(mode["func"]) if mode["func"] else None
        if exp and label_agrees(mode["name"], exp) == "disagrees":
            w(f"- Exporter label `{exp}` is attached to 0x{mode['func']:X}, which the table names {mode['name']} (mode {mode['id']})")
    w("")
    w("## Evidence citations")
    w("")
    w("| mode | kind | file | phrase | present |")
    w("| --- | --- | --- | --- | --- |")
    for mode_id, rows in sorted(citations.items()):
        for r in rows:
            w(f"| {mode_id} | {r['kind']} | `{r['file']}` | `{r['phrase'].replace('|', '\\|')}` | {'yes' if r['present'] else 'no'} |")
    for adj_id, rows in sorted(adjust_citations.items()):
        if adj_id > 3:
            continue
        for r in rows:
            w(f"| adjust {adj_id}{' (3..13 share)' if adj_id == 3 else ''} | {r['kind']} | `{r['file']}` | `{r['phrase']}` | {'yes' if r['present'] else 'no'} |")
    w("")
    w("## Receipts")
    w("")
    w("| input | path | sha256 |")
    w("| --- | --- | --- |")
    for label, path, digest in receipts:
        w(f"| {label} | `{path}` | `{digest}` |")
    for stage in stages:
        w(f"| {stage['file']} | `{args.game_dir / stage['file']}` | `{stage['sha256']}` |")
    w("")
    (out_dir / "camera.md").write_text("\n".join(lines))

    failed = [c for c in exe.checks if not c[1]]
    print(f"modes_table={len(modes)} dispatched={len(dispatched)} used_main={len(used_main)} "
          f"used_any={len(used_any)} with_evidence={len(with_evidence)} used_with_evidence={len(used_with_evidence)} "
          f"files={len(stages)} main_records={main_records} demo_records={demo_records} "
          f"checks_failed={len(failed)} field_violations={len(violations)} unverified_files={len(unverified)}")
    for label, ok, detail in failed:
        print(f"FAIL: {label} -- {detail}")
    print(f"wrote {csv_path} and {out_dir / 'camera.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
