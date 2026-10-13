#!/usr/bin/env python3
"""Enumerate the object scope of SA2 (2012 PC) from the shipped SET files.

Counts, per stage and per SET file, the object instances the game places and
the object types it can place, joins them to the per-stage object lists
extracted from the executable, and checks the repository for evidence that any
object type has been ported, traced or observed.

Inputs (all read-only):
  * the shipped SET files under <game>/resource/gd_PC/ (set*.bin);
  * the per-stage object lists split from sonic2app.exe by SA Tools
    (ObjectLists_data.ini, */objList.ini, OBJECT/Master Object List.ini);
  * repository evidence: Assets/Runtime/*.cs, Documentation/*.md,
    agents-journals/**/*.md.

Outputs:
  Documentation/scope/objects.csv  one row per SET file
  Documentation/scope/objects.md   totals, per-kind table, three stage
                                   type lists, method, receipts

Usage:
  python3 Documentation/scripts/enumerate_sa2_object_scope.py
  (override input roots with SA2_GAME_DIR and SA2_OBJECTLISTS_DIR)

Standard library only.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import struct
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = Path(__file__).resolve()
OUT_DIR = PROJECT_ROOT / "Documentation" / "scope"
OUT_CSV = OUT_DIR / "objects.csv"
OUT_MD = OUT_DIR / "objects.md"

GAME_DIR = Path(
    os.environ.get(
        "SA2_GAME_DIR",
        "/home/user/.local/share/Steam/steamapps/common/Sonic Adventure 2",
    )
)
SET_DIR = GAME_DIR / "resource" / "gd_PC"
OBJLIST_DIR = Path(
    os.environ.get(
        "SA2_OBJECTLISTS_DIR",
        str(PROJECT_ROOT / "private" / "generated" / "objectlists"),
    )
)

# ---------------------------------------------------------------------------
# SET file format (SA2 PC, verified in this script against every shipped file)
#
#   header : 32 bytes; u32 BIG-ENDIAN object count at offset 0, then 28 bytes
#            the loader does not read (zero in 239 of 240 files; set0009_s.bin
#            carries non-zero bytes there, which are ignored).
#   record : 32 bytes each, big-endian, SA Tools SETItem layout ">4H6f":
#            u16 id_word, u16 xrot, u16 yrot, u16 zrot,
#            f32 x, f32 y, f32 z, f32 varA, f32 varB, f32 varC.
#   id     : id_word & 0x0FFF indexes the stage's object list. Only bits 0-11
#            and bit 15 are ever set in the shipped files; bit 15 is carried
#            as a flag whose meaning this script does not claim to know.
#
# Verification per file: size == 32 + 32 * count, count read big-endian.
# ---------------------------------------------------------------------------
HEADER_SIZE = 32
RECORD_SIZE = 32
RECORD_STRUCT = struct.Struct(">4H6f")
ID_MASK = 0x0FFF
ID_FLAG_BIT15 = 0x8000

# SET file naming, inferred from the 240 shipped files:
#   set00NN[_hd|_2p]_(s|u).bin   numbered stage NN; hd = hard-mode layout,
#                                 2p = two-player layout; s and u are the two
#                                 SET layers the game loads together
#   set_b_<boss>_(s|u).bin        boss arenas
#   setCart<Track>[_hd].bin       kart tracks (no s/u layer)
#   set_chao_<garden>[_s|_u].bin  Chao gardens (base file plus s/u layers)
SET_NAME = re.compile(
    r"^set(?:"
    r"(?P<stage>\d{4})(?:_(?P<variant>hd|2p))?_(?P<layer>[su])"
    r"|_b_(?P<boss>[A-Za-z0-9]+?(?:_e)?)_(?P<blayer>[su])"
    r"|cart(?P<cart>[A-Za-z0-9]+?)(?:_(?P<cvariant>hd))?"
    r"|_chao_(?P<chao>[a-z]+)(?:_(?P<clayer>[su]))?"
    r")\.bin$",
    re.I,
)

BOSS_LISTS = {
    "bigbogy": "BossBigBogy",
    "bigfoot": "BossGuardRobo/BigFoot",
    "fdog": "BossGuardRobo/FlyingDog",
    "golem": "BossGolem",
    "golem_e": "BossGolemE",
    "hotshot": "BossGuardRobo/BossHotShot",
    "last1": "BossLast1",
}

# Stage kinds. Source: the object-list directory names and the Mod Loader
# stage names in ObjectLists_data.ini; the playable character is the SA2
# stage roster (speed = Sonic/Shadow, mech = Tails/Eggman, hunting =
# Knuckles/Rouge). Stages 1, 2 and 56 have no extracted object list; 1 and 56
# still ship SET files.
STAGE_KINDS = {
    0: ("test", "Basic Test"),
    1: ("test", "Knuckles Test (no object list extracted)"),
    3: ("speed", "Green Forest"),
    4: ("speed", "White Jungle"),
    5: ("hunting", "Pumpkin Hill"),
    6: ("speed", "Sky Rail"),
    7: ("hunting", "Aquatic Mine"),
    8: ("hunting", "Security Hall"),
    9: ("mech", "Prison Lane"),
    10: ("speed", "Metal Harbor"),
    11: ("mech", "Iron Gate"),
    12: ("mech", "Weapons Bed"),
    13: ("speed", "City Escape"),
    14: ("speed", "Radical Highway"),
    15: ("mech", "Weapons Bed 2P"),
    16: ("hunting", "Wild Canyon"),
    17: ("mech", "Mission Street"),
    18: ("hunting", "Dry Lagoon"),
    19: ("boss", "Sonic vs Shadow 1"),
    20: ("boss", "Tails vs Eggman 1"),
    21: ("mech", "Sand Ocean"),
    22: ("speed", "Crazy Gadget"),
    23: ("mech", "Hidden Base"),
    24: ("mech", "Eternal Engine"),
    25: ("hunting", "Death Chamber"),
    26: ("hunting", "Egg Quarters"),
    27: ("mech", "Lost Colony"),
    28: ("speed", "Pyramid Cave"),
    29: ("boss", "Tails vs Eggman 2"),
    30: ("speed", "Final Rush"),
    31: ("speed", "Green Hill"),
    32: ("hunting", "Meteor Herd"),
    33: ("boss", "Knuckles vs Rouge"),
    34: ("speed", "Cannon's Core Sonic"),
    35: ("mech", "Cannon's Core Eggman"),
    36: ("mech", "Cannon's Core Tails"),
    37: ("hunting", "Cannon's Core Rouge"),
    38: ("hunting", "Cannon's Core Knuckles"),
    39: ("mech", "Mission Street 2P"),
    40: ("speed", "Final Chase"),
    41: ("hunting", "Wild Canyon 2P"),
    42: ("boss", "Sonic vs Shadow 2"),
    43: ("mech", "Cosmic Wall"),
    44: ("hunting", "Mad Space"),
    45: ("mech", "Sand Ocean 2P"),
    46: ("hunting", "Dry Lagoon 2P"),
    47: ("speed", "Pyramid Race"),
    48: ("mech", "Hidden Base 2P"),
    49: ("hunting", "Pool Quest (Aquatic Mine 2P)"),
    50: ("hunting", "Planet Quest (Mad Space 2P)"),
    51: ("mech", "Deck Race (Weapons Bed 2P)"),
    52: ("speed", "Downtown Race (City Escape 2P)"),
    53: ("mech", "Cosmic Wall 2P"),
    54: ("speed", "Grind Race (Pyramid Cave 2P)"),
    55: ("mech", "Lost Colony 2P"),
    56: ("mech", "Eternal Engine 2P (no object list extracted)"),
    57: ("speed", "Metal Harbor 2P"),
    58: ("mech", "Iron Gate 2P"),
    59: ("hunting", "Death Chamber 2P"),
}
BOSS_NAMES = {
    "bigbogy": "King Boom Boo",
    "bigfoot": "Big Foot",
    "fdog": "Flying Dog",
    "golem": "Egg Golem (Sonic)",
    "golem_e": "Egg Golem (Eggman)",
    "hotshot": "Hot Shot",
    "last1": "Biolizard",
}
CHAO_NAMES = {"neut": "Neutral Garden", "hero": "Hero Garden", "dark": "Dark Garden"}

SHOWCASE_STAGES = [(10, "speed"), (12, "mech"), (8, "hunting")]

# ---------------------------------------------------------------------------
# Evidence. The grep below finds every repository line that contains an
# object type name (word-bounded, case-insensitive) or an object code address.
# A type counts as having evidence only when a hand-reviewed entry below says
# a kernel, fixture, receipt or journal names it as ported, traced or
# observed. Every other hit is listed in objects.md with its disposition so a
# reader can check the review; a hit with no disposition is reported as
# UNREVIEWED and must be reviewed before the count is trusted.
# ---------------------------------------------------------------------------
EVIDENCE: dict[str, str] = {
    # code address -> citation. Empty on 2026-09-19: no kernel, fixture,
    # receipt or journal names any SET object type as ported, traced or
    # observed. The only object-type work in the repository is the static
    # import of five Neutral Garden plant models at their SET positions
    # (agents-journals/pipeline/2026-09-15-extraction-toolchain.md, line 74;
    # Documentation/garden-prop-placements.json). That is presentation
    # placement, not behaviour, and is recorded separately below.
}
PLACEMENT_ONLY: dict[str, str] = {
    # object list relative dir -> names whose models were imported at SET
    # positions into the Unity garden prototype. Presentation only.
    "Chao/stg_neut": "PLANT A,PLANT B,PLANT C00,PLANT C01,PLANT C02",
}
REVIEWED_NAME_HITS: dict[str, str] = {
    # name -> why the grep hit is not evidence for the object type.
    "STAGE": "English word (stage of the game, stage of a pipeline)",
    "STAKE": "English word (the philosophy's progress-metric rule: 'has a stake in')",
    "WIND": "English word (the scope journal's interrogation addendum: 'Wind Waker')",
    "STEP": "English word / Step() method",
    "GATE": "English word (evidence gate)",
    "STREAM": "MemoryStream / record stream",
    "TEST": "English word / x86 TEST instruction",
    "SHADOW": "the shadow runner / the character Shadow",
    "STORE": "English word (binary32 store)",
    "BLOCK": "English word (code block)",
    "FLAG": "English word (collision flag word)",
    "LINE": "English word (marker line)",
    "SIGN": "English word (sign bit)",
    "BUFFER": "English word (work buffer)",
    "EMERALD": "repository name / Emerald Radar tool name",
    "WALL": "English word (wall contact label)",
    "SPHERE": "English word (query sphere)",
    "UPSTREAM": "English word",
    "MAP": "English word",
    "COVER": "English word",
    "POLE": "VectorToAngles pole case",
    "NET": ".NET SDK",
    "MACHINE": "English word",
    "KEY": "English word (dictionary key)",
    "POOL": "English word (native pool exhaustion)",
    "BRIDGE": "English word (Metal Harbor bridge geometry)",
    "SWITCH": "C# switch statement",
    "OMOCHAO": "Omochao Split tool name",
    "CAPSULE": "Unity CapsuleCollider",
    "BOX": "English word (bounding box)",
    "SIGNS": "English word (float signs)",
    "PIC": "pic-synth-home directory name",
    "LIGHT": "light dash (a player move, not the LIGHT object)",
    "BUG": "English word",
    "ROLL": "Euler roll angle",
    "TERMINAL": "English word",
    "PANEL": "UI panel texture",
    "POISON": "poison synthetic test input",
    "SIGNAL": "English word",
    "MINIMAL": "English word",
    "minimal": "English word",
    "CAM": "Camera.main variable",
    "WARP": "smoke scenario warp",
    "BONE": "SAIO helper bone",
    "JP": "x86 JP instruction",
    "RING": "ring buffer",
    "SHIP": "English word (shipped)",
    "TOWER": "Hero Garden model name in an object-id mapping line, not ported",
    "PALM": "Hero Garden plant model listed in SA Tools config, not ported",
    "KUSA": "Hero Garden model names in an object-id mapping line, not ported",
    "HASIRA": "Hero Garden model names in an object-id mapping line, not ported",
    "HASIRAKAKE": "Hero Garden model names in an object-id mapping line, not ported",
    "HANAAKA": "Hero Garden model names in an object-id mapping line, not ported",
    "HANAMURA": "Hero Garden model names in an object-id mapping line, not ported",
    "LOBBY": "English word",
    "LEAF": "English word (registered leaf)",
    "CORNER": "English word (triangle corner)",
    "NIGHTS": "English word",
    "EYE": "English word",
    "PLANT A": "Neutral Garden model imported at SET positions (placement only)",
    "PLANT B": "Neutral Garden model imported at SET positions (placement only)",
    "PLANT C00": "Neutral Garden model imported at SET positions (placement only)",
    "PLANT C01": "Neutral Garden model imported at SET positions (placement only)",
    "PLANT C02": "Neutral Garden model imported at SET positions (placement only)",
    "FLOWER": "English word",
    "ROCK": "English word",
    "ARROW": "English word",
    "BOARD": "English word",
    "DOOR": "English word",
    "FORKED": "English word",
    "FAN": "fan-out",
    "MAT": "Unity .mat asset extension",
    "BURST": "English word",
    "DUMMY": "English word",
    "GUIDANCE": "English word",
    "TREASURE": "English word",
}


@dataclass
class ObjType:
    index: int
    code: str
    name: str
    flags: str


@dataclass
class ObjList:
    rel_dir: str
    path: Path
    stage_name: str
    types: list[ObjType]

    @property
    def codes(self) -> set[str]:
        return {t.code for t in self.types}


@dataclass
class SetFile:
    name: str
    path: Path
    stage_key: str
    stage_number: int | None
    stage_name: str
    kind: str
    variant: str
    layer: str
    list_dir: str | None
    count: int = 0
    size: int = 0
    verified: bool = False
    verify_note: str = ""
    id_counts: Counter = field(default_factory=Counter)
    bit15_count: int = 0
    over_range: int = 0
    float_violations: int = 0
    content_sha: str = ""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_ini_sections(path: Path) -> list[tuple[str, dict[str, str]]]:
    sections: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="latin-1").splitlines():
        line = raw.rstrip("\r")
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections.append((line[1:-1], current))
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value
    return sections


def load_object_lists(receipts: dict[str, str]) -> dict[str, ObjList]:
    data_ini = OBJLIST_DIR / "ObjectLists_data.ini"
    receipts[str(data_ini)] = sha256_of(data_ini)
    stage_names: dict[str, str] = {}
    for section, kv in read_ini_sections(data_ini):
        if kv.get("type") == "objlist":
            rel_dir = kv["filename"].rsplit("/", 1)[0]
            stage_names[rel_dir] = section.replace(" Object List", "")
    lists: dict[str, ObjList] = {}
    for path in sorted(OBJLIST_DIR.rglob("objList.ini")):
        rel_dir = path.parent.relative_to(OBJLIST_DIR).as_posix()
        receipts[str(path)] = sha256_of(path)
        types: list[ObjType] = []
        for section, kv in read_ini_sections(path):
            index = int(section)
            if index != len(types):
                raise SystemExit(f"{path}: non-contiguous index {index}")
            types.append(ObjType(index, kv["Code"], kv["Name"].strip(), kv.get("Flags", "")))
        lists[rel_dir] = ObjList(rel_dir, path, stage_names.get(rel_dir, rel_dir), types)
    master = OBJLIST_DIR / "OBJECT" / "Master Object List.ini"
    if master.exists():
        receipts[str(master)] = sha256_of(master)
    return lists


def master_list_size() -> int | None:
    master = OBJLIST_DIR / "OBJECT" / "Master Object List.ini"
    if not master.exists():
        return None
    return sum(1 for s, _ in read_ini_sections(master))


def classify_set_file(path: Path, lists: dict[str, ObjList]) -> SetFile:
    m = SET_NAME.match(path.name)
    if not m:
        raise SystemExit(f"unrecognised SET file name: {path.name}")
    if m.group("stage"):
        n = int(m.group("stage"))
        kind, stage_name = STAGE_KINDS.get(n, ("unknown", f"stage {n}"))
        prefix = f"stg{n:02d}_"
        list_dir = next((d for d in lists if d.startswith(prefix)), None)
        return SetFile(path.name, path, f"{n:02d}", n, stage_name, kind,
                       (m.group("variant") or "base").lower(), m.group("layer").lower(), list_dir)
    if m.group("boss"):
        key = m.group("boss").lower()
        return SetFile(path.name, path, f"boss:{key}", None, BOSS_NAMES[key], "boss",
                       "base", m.group("blayer").lower(), BOSS_LISTS[key])
    if m.group("cart"):
        return SetFile(path.name, path, f"cart:{m.group('cart')}", None,
                       f"Kart {m.group('cart')}", "kart",
                       (m.group("cvariant") or "base").lower(), "", "CartCommon")
    key = m.group("chao").lower()
    return SetFile(path.name, path, f"chao:{key}", None, CHAO_NAMES.get(key, key), "chao",
                   "base", (m.group("clayer") or "").lower(), f"Chao/stg_{key}")


def read_set_file(sf: SetFile, lists: dict[str, ObjList]) -> None:
    data = sf.path.read_bytes()
    sf.size = len(data)
    if sf.size < HEADER_SIZE:
        sf.verify_note = "shorter than header"
        return
    sf.count = struct.unpack(">I", data[:4])[0]
    expected = HEADER_SIZE + RECORD_SIZE * sf.count
    if sf.size != expected:
        sf.verify_note = f"size {sf.size} != 32 + 32 * count {sf.count}"
        return
    sf.verified = True
    if data[4:HEADER_SIZE] != b"\0" * (HEADER_SIZE - 4):
        sf.verify_note = "non-zero header padding (ignored)"
    sf.content_sha = hashlib.sha256(data).hexdigest()
    list_size = len(lists[sf.list_dir].types) if sf.list_dir else None
    for i in range(sf.count):
        off = HEADER_SIZE + RECORD_SIZE * i
        fields = RECORD_STRUCT.unpack_from(data, off)
        id_word = fields[0]
        # The record layout (big-endian, SA Tools' SETEntry) is verified per
        # record, not only per file: the position floats must be finite and
        # within the stage's order of magnitude (|coordinate| < 1e5 native
        # units; Metal Harbor's collision spans about 8797 by 2690 by 10477).
        # A little-endian or misaligned read yields denormals or huge values.
        px, py, pz = fields[4], fields[5], fields[6]
        if not all(math.isfinite(v) and abs(v) < 1e5 for v in (px, py, pz)):
            sf.float_violations += 1
        if id_word & ID_FLAG_BIT15:
            sf.bit15_count += 1
        if id_word & ~(ID_MASK | ID_FLAG_BIT15):
            raise SystemExit(f"{sf.name}: record {i} uses id bits outside 0x8FFF: {id_word:#06x}")
        obj_id = id_word & ID_MASK
        sf.id_counts[obj_id] += 1
        if list_size is not None and obj_id >= list_size:
            sf.over_range += 1


def type_for(sf: SetFile, obj_id: int, lists: dict[str, ObjList]) -> ObjType | None:
    if not sf.list_dir:
        return None
    types = lists[sf.list_dir].types
    return types[obj_id] if obj_id < len(types) else None


def evidence_scan(lists: dict[str, ObjList], receipts: dict[str, str]):
    # The same corpus the camera enumeration scans: runtime sources, every
    # document and receipt (json) under Documentation, the checkers and
    # stagers, and every journal.
    files = sorted(
        list((PROJECT_ROOT / "Assets" / "Runtime").glob("*.cs"))
        + list((PROJECT_ROOT / "Documentation").rglob("*.md"))
        + list((PROJECT_ROOT / "Documentation").rglob("*.json"))
        + list((PROJECT_ROOT / "Documentation" / "scripts").glob("*.py"))
        + list((PROJECT_ROOT / "agents-journals").rglob("*.md"))
    )
    # The scope reports, the enumerators themselves and external reviews (text
    # written without repository access, not evidence) are outside the corpus.
    files = [f for f in files if "scope" not in f.relative_to(PROJECT_ROOT).parts
             and "external-reviews" not in f.relative_to(PROJECT_ROOT).parts
             and not f.name.startswith("enumerate_sa2_")]
    names = sorted({t.name for lst in lists.values() for t in lst.types if t.name})
    codes = sorted({t.code for lst in lists.values() for t in lst.types})
    name_re = {n: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])", re.I) for n in names}
    code_needle = {c: c.lstrip("0").lower() for c in codes}
    name_hits: Counter = Counter()
    code_hits: Counter = Counter()
    first: dict[str, str] = {}
    for path in files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for ln, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            for n, rx in name_re.items():
                if n.lower() in low and rx.search(line):
                    name_hits[n] += 1
                    first.setdefault("N:" + n, f"{rel}:{ln}")
            for c, needle in code_needle.items():
                if needle in low:
                    code_hits[c] += 1
                    first.setdefault("C:" + c, f"{rel}:{ln}")
    return len(files), name_hits, code_hits, first


def main() -> int:
    receipts: dict[str, str] = {}
    lists = load_object_lists(receipts)
    master_size = master_list_size()

    set_files = [classify_set_file(p, lists) for p in sorted(SET_DIR.iterdir())
                 if p.is_file() and p.name.lower().startswith("set")]
    set_files.sort(key=lambda s: (s.stage_number is None, s.stage_number or 0, s.stage_key, s.variant != "base", s.variant, s.layer))
    for sf in set_files:
        receipts[str(sf.path)] = sha256_of(sf.path)
        read_set_file(sf, lists)

    n_evidence_files, name_hits, code_hits, first_hit = evidence_scan(lists, receipts)

    all_list_codes = {c for lst in lists.values() for c in lst.codes}
    used_codes: set[str] = set()
    for sf in set_files:
        for obj_id in sf.id_counts:
            t = type_for(sf, obj_id, lists)
            if t:
                used_codes.add(t.code)

    # per-stage aggregation
    stages: dict[str, dict] = {}
    for sf in set_files:
        st = stages.setdefault(sf.stage_key, {
            "name": sf.stage_name, "kind": sf.kind, "number": sf.stage_number,
            "files": [], "instances": 0, "ids": set(), "codes": set(),
            "list_dir": sf.list_dir, "over_range": 0, "type_counts": Counter(),
        })
        st["files"].append(sf)
        st["instances"] += sf.count
        st["over_range"] += sf.over_range
        for obj_id, n in sf.id_counts.items():
            st["ids"].add(obj_id)
            t = type_for(sf, obj_id, lists)
            if t:
                st["codes"].add(t.code)
                st["type_counts"][(t.index, t.name, t.code)] += n

    def evidence_codes(codes: set[str]) -> int:
        return sum(1 for c in codes if c in EVIDENCE)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- CSV -------------------------------------------------------------
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stage", "stage_name", "kind", "set_file", "variant", "layer",
                    "object_instances", "distinct_type_ids_used",
                    "types_in_stage_object_list", "types_with_evidence",
                    "types_placement_only", "ids_over_list_range", "id_bit15_records",
                    "format_verified", "note"])
        for sf in set_files:
            lst = lists.get(sf.list_dir) if sf.list_dir else None
            codes = {type_for(sf, i, lists).code for i in sf.id_counts if type_for(sf, i, lists)}
            placement = 0
            if lst and lst.rel_dir in PLACEMENT_ONLY:
                pnames = set(PLACEMENT_ONLY[lst.rel_dir].split(","))
                placement = sum(1 for i in sf.id_counts if (t := type_for(sf, i, lists)) and t.name in pnames)
            w.writerow([sf.stage_key, sf.stage_name, sf.kind, sf.name, sf.variant, sf.layer,
                        sf.count, len(sf.id_counts), len(lst.types) if lst else "",
                        evidence_codes(codes), placement, sf.over_range, sf.bit15_count,
                        "yes" if sf.verified else "no", sf.verify_note])

    # ---- MD --------------------------------------------------------------
    total_instances = sum(sf.count for sf in set_files)
    unverified = [sf for sf in set_files if not sf.verified]
    kinds = ["speed", "mech", "hunting", "kart", "boss", "chao", "test", "unknown"]
    per_kind: dict[str, dict] = {k: {"stages": set(), "files": 0, "instances": 0, "used": set(), "listed": set(), "lists": set()} for k in kinds}
    for sf in set_files:
        pk = per_kind[sf.kind]
        pk["stages"].add(sf.stage_key)
        pk["files"] += 1
        pk["instances"] += sf.count
        if sf.list_dir:
            pk["lists"].add(sf.list_dir)
            pk["listed"] |= lists[sf.list_dir].codes
            pk["used"] |= {type_for(sf, i, lists).code for i in sf.id_counts if type_for(sf, i, lists)}
    listed_by_any_setfile = set().union(*(pk["listed"] for pk in per_kind.values()))
    lists_without_set = sorted(d for d in lists if d not in {sf.list_dir for sf in set_files})

    md: list[str] = []
    a = md.append
    a("# SA2 object scope, from the shipped SET files")
    a("")
    a("Generated by `Documentation/scripts/enumerate_sa2_object_scope.py`. Every")
    a("number below is computed from the inputs listed under Receipts; nothing is")
    a("typed in by hand except the stage-kind table and the evidence review in the")
    a("script. Per-file rows are in `objects.csv` beside this file.")
    a("")
    a("## Totals")
    a("")
    a(f"- Stage groups with SET files: **{len(stages)}** "
      f"({sum(1 for s in stages.values() if s['number'] is not None)} numbered stages, "
      f"{sum(1 for k in stages if k.startswith('boss:'))} boss arenas, "
      f"{sum(1 for k in stages if k.startswith('cart:'))} kart tracks, "
      f"{sum(1 for k in stages if k.startswith('chao:'))} Chao gardens).")
    a(f"- SET files: **{len(set_files)}**; format verified on {len(set_files) - len(unverified)}, "
      f"failed on {len(unverified)}.")
    a(f"- Object instances placed: **{total_instances}**.")
    distinct_contents = {sf.content_sha for sf in set_files if sf.content_sha}
    duplicate_files = [sf for sf in set_files if sf.content_sha and
                       sum(1 for o in set_files if o.content_sha == sf.content_sha) > 1]
    first_of = {}
    duplicated_instances = 0
    for sf in set_files:
        if sf.content_sha in first_of:
            duplicated_instances += sf.count
        else:
            first_of[sf.content_sha] = sf.name
    a(f"- Distinct SET file contents: **{len(distinct_contents)}** of {len(set_files)} files; "
      f"{len(set_files) - len(distinct_contents)} files are byte copies of another, carrying "
      f"{duplicated_instances} of the instances above a second time.")
    float_bad = sum(sf.float_violations for sf in set_files)
    a(f"- Records whose position floats are not finite or lie outside |coordinate| < 1e5 "
      f"(the per-record layout check): **{float_bad}** of {total_instances}.")
    playable = [st for st in stages.values()
                if st["kind"] in ("speed", "mech", "hunting") and st["list_dir"]]
    placed_counts = sorted(len(st["codes"]) for st in playable)
    if placed_counts:
        median = placed_counts[len(placed_counts) // 2]
        a(f"- Distinct types placed per playable stage group ({len(placed_counts)} groups with an "
          f"extracted list): min {placed_counts[0]}, median {median}, max {placed_counts[-1]}.")
    a(f"- Object lists extracted from the executable: {len(lists)}; lists with no SET file: "
      f"{', '.join(lists_without_set) or 'none'}.")
    a(f"- Distinct object types across all lists (by code address): **{len(all_list_codes)}**"
      + (f"; the Master Object List has {master_size} entries." if master_size is not None else "."))
    a(f"- Distinct object types actually placed by at least one SET file: **{len(used_codes)}**.")
    a(f"- Types with evidence of being ported, traced or observed in this repository: "
      f"**{len(EVIDENCE)}** of {len(all_list_codes)}.")
    placement_total = sum(len(v.split(',')) for v in PLACEMENT_ONLY.values())
    a(f"- Types whose model was imported at SET positions for presentation only, no behaviour: "
      f"{placement_total} (all in the Neutral Garden list).")
    a(f"- Records whose id (masked 0x0FFF) is outside the extracted stage list: "
      f"{sum(sf.over_range for sf in set_files)}, in "
      f"{', '.join(sf.name for sf in set_files if sf.over_range) or 'no file'}.")
    a(f"- Records carrying id bit 15: {sum(sf.bit15_count for sf in set_files)} of {total_instances}.")
    a(f"- SET files with no extracted object list (names unresolved): "
      f"{', '.join(sf.name for sf in set_files if not sf.list_dir)}.")
    a("")
    a("## Per kind")
    a("")
    a("| kind | stage groups | SET files | instances | types placed | types in lists | with evidence |")
    a("|---|---:|---:|---:|---:|---:|---:|")
    for k in kinds:
        pk = per_kind[k]
        if pk["files"] == 0:
            continue
        a(f"| {k} | {len(pk['stages'])} | {pk['files']} | {pk['instances']} | {len(pk['used'])} | "
          f"{len(pk['listed'])} | {evidence_codes(pk['listed'])} |")
    a(f"| **all** | {len(stages)} | {len(set_files)} | {total_instances} | {len(used_codes)} | "
      f"{len(listed_by_any_setfile)} | {len(EVIDENCE)} |")
    a("")
    a("Types are counted by code address, so a type shared by several lists counts")
    a("once per kind and once overall. 'Types in lists' is the union of the lists")
    a(f"of the stages in that kind; the 'all' row covers lists that have a SET file, "
      f"so it is {len(all_list_codes) - len(listed_by_any_setfile)} short of the "
      f"{len(all_list_codes)} across every extracted list. Kinds: speed = Sonic/Shadow, mech = Tails/Eggman,")
    a("hunting = Knuckles/Rouge, kart, boss (including character-versus-character")
    a("fights), chao = Chao gardens, test = the two test stages.")
    a("")
    a("## Per stage")
    a("")
    a("| stage | name | kind | SET files | instances | distinct ids | list size | types placed | over range |")
    a("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for key, st in stages.items():
        lst = lists.get(st["list_dir"]) if st["list_dir"] else None
        a(f"| {key} | {st['name']} | {st['kind']} | {len(st['files'])} | {st['instances']} | "
          f"{len(st['ids'])} | {len(lst.types) if lst else '-'} | {len(st['codes'])} | {st['over_range']} |")
    a("")
    for number, kind in SHOWCASE_STAGES:
        st = stages[f"{number:02d}"]
        lst = lists[st["list_dir"]]
        a(f"## {st['name']} (stage {number:02d}, {kind}): object types placed")
        a("")
        a(f"List `{lst.rel_dir}/objList.ini` has {len(lst.types)} types; the stage's "
          f"{len(st['files'])} SET files place {st['instances']} instances of "
          f"{len(st['codes'])} of them. Counts per file follow the total, in the order "
          + ", ".join(sf.name for sf in st["files"]) + ".")
        a("")
        a("| id | name | code | total | per file |")
        a("|---:|---|---|---:|---|")
        for (index, name, code), total in sorted(st["type_counts"].items(), key=lambda kv: (-kv[1], kv[0][0])):
            per_file = "/".join(str(sf.id_counts.get(index, 0)) for sf in st["files"])
            a(f"| {index} | {name} | {code} | {total} | {per_file} |")
        unused = [t for t in lst.types if (t.index, t.name, t.code) not in st["type_counts"]]
        a("")
        a(f"Listed but never placed by any of the stage's SET files ({len(unused)}): "
          + ", ".join(f"{t.index} {t.name}" for t in unused) + ".")
        a("")
    a("## Evidence search")
    a("")
    a("Searched the repository's working tree (Assets/Runtime/*.cs, Documentation/**/*.md, "
      "Documentation/**/*.json, Documentation/scripts/*.py except the enumerators, "
      "agents-journals/**/*.md) for every object type name (word-bounded, case-insensitive) "
      "and every code address (hex, leading zeros stripped, with or without 0x). The corpus is "
      "the repository itself, which git records; file counts, line counts and first-hit "
      "locations are left out of this report so that it changes only when a new name or "
      "address hits or a disposition changes.")
    a("")
    a(f"- Code-address hits: **{len(code_hits)}** distinct addresses.")
    a(f"- Name hits: {len(name_hits)} distinct names. "
      "Each name is listed below with its review disposition. "
      "A type counts as evidence only through the `EVIDENCE` table in the script, "
      f"which currently holds {len(EVIDENCE)} entries.")
    a("")
    unreviewed = [n for n in name_hits if n not in REVIEWED_NAME_HITS]
    if unreviewed:
        a(f"**UNREVIEWED name hits ({len(unreviewed)}): {', '.join(unreviewed)}. "
          "Review them in the script before trusting the evidence count.**")
        a("")
    a("| name | disposition |")
    a("|---|---|")
    for n in sorted(name_hits):
        a(f"| {n} | {REVIEWED_NAME_HITS.get(n, 'UNREVIEWED')} |")
    for c in sorted(code_hits):
        a(f"| code {c} | UNREVIEWED |")
    a("")
    a("## Method and assumptions")
    a("")
    a("SET file format assumed (SA Tools `SETItem` layout for SA2): a 32-byte header")
    a("whose first four bytes are the object count as a **big-endian** u32 (the PC")
    a("port kept the GameCube byte order; read little-endian, set0010_s.bin would claim")
    a("1761673216 objects), the remaining 28 header bytes unused, then one 32-byte")
    a("big-endian record per object: `u16 id_word, u16 xrot, u16 yrot, u16 zrot,`")
    a("`f32 x, y, z, f32 varA, varB, varC`. Verification, per file: the size must equal")
    a("32 + 32 x count; a file failing that is listed as unverified and its records are")
    a("not counted. The 28 padding bytes are zero in every file except set0009_s.bin,")
    a("where they are ignored.")
    a("")
    a("Object id: `id_word & 0x0FFF` indexes the stage's object list. The script")
    a("aborts if any record sets a bit other than 0-11 or 15; none does. Bit 15 is set")
    a("on the records counted above and is carried as an unexplained flag; the object")
    a("list index does not depend on it. Observed, not explained: bit 15 is set on every")
    a("record of every `_u` file except set_b_fdog_u.bin, on no record of any `_s` file,")
    a("and on part of the records of the layerless Chao base files. A masked id at or beyond the extracted list's")
    a("length is counted as 'over range' and resolves to no type; the over-range records")
    a("are all in 2P layout files whose applicable object list this script does not")
    a("claim to know (a 2P layout may bind a different list from the base stage).")
    a("")
    a("Object lists: SA Tools `split` output from the owned `sonic2app.exe`, one")
    a("`objList.ini` per stage with contiguous `[index]` sections carrying `Code`")
    a("(routine address in the executable) and `Name`. A type's identity is its code")
    a("address, because the same routine is named differently in different lists")
    a("(`RING` / `RING_LINEAR` in one list, `RINGLINE` in another) and because the")
    a("Master Object List keys its entries by that address. SET file to list mapping:")
    a("`set00NN*` to `stgNN_*`, `set_b_<boss>` to the boss list, `setCart*` to")
    a("`CartCommon`, `set_chao_<garden>` to `Chao/stg_<garden>`. Stages 01 and 56 ship")
    a("SET files but have no extracted list; their instances are counted, their types")
    a("are not resolved.")
    a("")
    a("SET file naming inferred from the 240 shipped names: `set00NN[_hd|_2p]_(s|u).bin`")
    a("for numbered stages, where `hd` is the hard-mode layout, `2p` the two-player")
    a("layout, and `s`/`u` the two layers the game loads together for one layout (the")
    a("meaning of the letters is not verified here; both are counted); `set_b_<boss>_(s|u)`")
    a("for boss arenas; `setCart<Track>[_hd]` for kart tracks; `set_chao_<garden>[_s|_u]`")
    a("for Chao gardens. Every one of the 240 names matched this grammar.")
    a("")
    a("Evidence: a type has evidence only when a kernel, fixture, receipt or journal")
    a("names it as ported, traced or observed. The grep is mechanical; the judgement")
    a("is the hand-written review table in the script, reproduced above so it can be")
    a("checked. A name appearing in a list of things not done is not evidence.")
    a("")
    a("## Receipts")
    a("")
    receipts[str(SCRIPT_PATH)] = sha256_of(SCRIPT_PATH)
    a("```")
    for path in sorted(receipts):
        a(f"{receipts[path]}  {path}")
    a("```")
    a("")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    # ---- console summary --------------------------------------------------
    print(f"stages={len(stages)} set_files={len(set_files)} instances={total_instances} "
          f"types_in_lists={len(all_list_codes)} types_placed={len(used_codes)} "
          f"with_evidence={len(EVIDENCE)} unverified={len(unverified)}")
    for k in kinds:
        pk = per_kind[k]
        if pk["files"]:
            print(f"  {k:8s} stages={len(pk['stages']):3d} files={pk['files']:3d} "
                  f"instances={pk['instances']:6d} placed={len(pk['used']):4d} listed={len(pk['listed']):4d}")
    for number, kind in SHOWCASE_STAGES:
        st = stages[f"{number:02d}"]
        print(f"  {st['name']}: {len(st['codes'])} types placed of {len(lists[st['list_dir']].types)} listed, "
              f"{st['instances']} instances")
    for sf in unverified:
        print(f"  UNVERIFIED {sf.name}: {sf.verify_note}")
    if unreviewed:
        print(f"  UNREVIEWED name hits: {', '.join(unreviewed)}")
    print(f"wrote {OUT_CSV} and {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
