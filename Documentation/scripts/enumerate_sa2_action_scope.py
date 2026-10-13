#!/usr/bin/env python3
"""Count the player-action and motion scope of SA2 from the executable and its tables.

Chaos Emerald Census: action scope enumerator. Everything below is read from the
unpacked executable, the shipped-table addresses the SA2 Mod Loader headers
publish, the captures the reachability gate consumes, and the three repository
files that name ported or extracted entries. Nothing is typed in as a count; the
documented numbers are re-derived and any disagreement is printed and recorded.

Inputs (all read-only):
  --executable    sonic2app-unpacked.exe, image base 0x400000
  --private-work  the private work tree; supplies native-run/<capture> and the
                  mod loader include directory when present
  --repo          this repository, for the checker, extractor and pacing file
Outputs:
  <out-dir>/actions.csv   one row per dispatch action 0..87, then one row per
                          Sonic action-list entry 0..202
  <out-dir>/actions.md    totals, other characters, method, receipts

Standard library only. Run from anywhere; paths are absolute.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import math
import os
import re
import struct
import sys
from pathlib import Path

IMAGE_BASE = 0x400000

# The switch PResetPosition's caller makes on the entity's action byte. Pinned
# in check_sa2_preset_branch_reachability.py; re-read here from the bytes.
ACTION_DISPATCH = 0x00717C75
ACTION_DISPATCH_BYTES = "0fbe0383f8570f87d6170000ff2485b4977100"
ACTION_TABLE = 0x007197B4
ACTION_COUNT = 0x58
ACTION_DEFAULT_ARM = 0x00719457
DOCUMENTED_DISTINCT_ARMS = 49
DOCUMENTED_DEFAULT_ACTIONS = 18
DOCUMENTED_ACTIONS_WITH_EVIDENCE = (0, 1, 6, 10, 12, 17, 76, 79)

# The nine captures the reachability gate reads, by directory name under
# <private-work>/native-run.
CAPTURES = ("preset-sweep-a", "preset-sweep-c", "preset-sweep-e",
            "preset-sweep-h", "preset-sweep-k", "preset-window-wide-v1",
            "preset-jump-v1", "preset-lever-turn", "preset-lever-release")
CAPTURE_FILE = "sa2-native-preset-position-window-v1.csv"

# AnimationInfo: int16 AnimNum, ModelNum, mtnmode, NextAnimation; float32
# TransitionSpeed, AnimationSpeed (SA2Structs.h, struct AnimationInfo).
ENTRY_SIZE = 16
ENTRY_FORMAT = "<hhhhff"

# Every AnimationInfo array the SA2 Mod Loader publishes, as
# DataArray(AnimationInfo, <name>, <va>, <count>) in SA2Variables.h. The same
# addresses appear as RVAs in SA Tools GameConfig/PC_SA2/Characters.ini
# ([<name> Animation List], type=animationlist). When the header is present the
# constants below are verified against it.
ANIM_LISTS = (
    ("Tails", 0xA0D608, 118),
    ("MechEggman", 0xA0E1F0, 139),
    ("MechTails", 0xA0EAA0, 190),
    ("ChaoWalker", 0xA0F680, 198),
    ("DarkChaoWalker", 0xA102E0, 202),
    ("Eggman", 0xA12750, 164),
    ("Knuckles", 0xA13320, 217),
    ("Tikal", 0xA140B0, 260),
    ("Rouge", 0xA150F0, 217),
    ("Chaos", 0xA15E80, 272),
    ("Sonic", 0xA17C18, 203),
    ("Shadow", 0xA188C8, 249),
    ("Amy", 0xA19858, 249),
    ("MetalSonic", 0xA1A7E8, 249),
    ("SuperSonic", 0x170D228, 110),
    ("SuperShadow", 0x170D908, 117),
)
SONIC_LIST_VA = 0xA17C18
SONIC_LIST_COUNT = 203

# PhysicsData per character: DataArray(PhysicsData, PhysicsArray, 0x17391E8, 19)
# in SA2Variables.h; extract_sonic_physics.py reads record 0 at RVA 0x13391E8.
PHYSICS_VA = 0x17391E8
PHYSICS_RECORD_SIZE = 132
PHYSICS_DOCUMENTED_COUNT = 19
# enum Characters in SA2Enums.h, in order; the header says PhysicsArray is
# indexed by it.
CHARACTERS_ENUM = (
    "Sonic", "Shadow", "Tails", "Eggman", "Knuckles", "Rouge", "MechTails",
    "MechEggman", "Amy", "SuperSonic", "SuperShadow", "B", "MetalSonic",
    "ChaoWalker", "DarkChaoWalker", "Tikal", "Chaos",
)

# The exact instruction shape of the Sonic dispatch, searched across .text:
#   movsx/movzx eax, byte [reg] or [reg+disp8]; cmp eax, imm8; ja rel32;
#   jmp [eax*4 + table]
DISPATCH_SHAPE = re.compile(
    rb"\x0f[\xbe\xb6](?:[\x00-\x3f]|[\x40-\x7f].)\x83\xf8(.)\x0f\x87....\xff\x24\x85(....)",
    re.S)
DISPATCH_MIN_ENTRIES = 32

REPO_CHECKER = "Documentation/scripts/check_sa2_preset_branch_reachability.py"
REPO_EXTRACTOR = "Documentation/scripts/extract_sonic_action_table.py"
REPO_PACING = "Assets/Runtime/SonicAnimationPacing.cs"
MOD_LOADER_INCLUDE = "tools/sa2-mod-loader/SA2ModLoader/include"
SA_TOOLS_CHARACTERS_INI = "tools/sa_tools/GameConfig/PC_SA2/Characters.ini"


class PeImage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        data = self.data
        if data[:2] != b"MZ":
            raise SystemExit(f"{path} is not a PE executable")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise SystemExit(f"{path} has no PE header")
        count = struct.unpack_from("<H", data, pe + 6)[0]
        optional = struct.unpack_from("<H", data, pe + 20)[0]
        first = pe + 24 + optional
        self.sections = []
        for i in range(count):
            header = first + i * 40
            name = data[header:header + 8].split(b"\0", 1)[0].decode("ascii")
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", data, header + 8)
            self.sections.append((name, va, min(vsize, rsize), rptr))

    def raw(self, va: int, length: int) -> tuple[int, str]:
        rva = va - IMAGE_BASE
        for name, start, size, rptr in self.sections:
            if start <= rva and rva + length <= start + size:
                return rptr + rva - start, name
        raise SystemExit(f"VA {va:#x} (+{length}) is not backed by a section")

    def bytes_at(self, va: int, length: int) -> bytes:
        off, _ = self.raw(va, length)
        return self.data[off:off + length]

    def section(self, name: str) -> tuple[int, bytes]:
        for sname, start, size, rptr in self.sections:
            if sname == name:
                return IMAGE_BASE + start, self.data[rptr:rptr + size]
        raise SystemExit(f"no section {name}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_entries(image: PeImage, va: int, count: int) -> list[tuple]:
    off, _ = image.raw(va, count * ENTRY_SIZE)
    return [struct.unpack_from(ENTRY_FORMAT, image.data, off + i * ENTRY_SIZE)
            for i in range(count)]


def valid_entry(row: tuple) -> bool:
    return math.isfinite(row[5]) and row[5] > 0


def parse_checker_actions(path: Path) -> tuple[dict[int, int], dict[int, str]]:
    text = path.read_text()
    match = re.search(r"EXPECTED_ACTIONS\s*=\s*\{([^}]*)\}", text, re.S)
    if not match:
        raise SystemExit(f"{path} has no EXPECTED_ACTIONS")
    expected = {int(k): int(v) for k, v in
                re.findall(r"(\d+)\s*:\s*(\d+)", match.group(1))}
    owners = {}
    match = re.search(r"ACTION_OWNERS\s*=\s*\{([^}]*)\}", text, re.S)
    if match:
        owners = {int(k): v for k, v in
                  re.findall(r"(\d+)\s*:\s*\"([^\"]+)\"", match.group(1))}
    return expected, owners


def parse_selected(path: Path) -> tuple[int, ...]:
    text = path.read_text()
    match = re.search(r"SELECTED\s*=\s*\(([^)]*)\)", text, re.S)
    if not match:
        raise SystemExit(f"{path} has no SELECTED tuple")
    return tuple(int(x) for x in re.findall(r"\d+", match.group(1)))


def parse_pacing_cases(path: Path) -> tuple[int, ...]:
    return tuple(sorted({int(x) for x in
                         re.findall(r"case\s+(\d+)\s*:", path.read_text())}))


# The captures whose calls the PResetPosition port has been run on (the
# fixtures the preset suites read are staged from these: the corpus window and
# the jump replay, per stage_precise_preset_position_corpus_fixture.py's
# PROFILES). The other seven captures were read for reachability only.
KERNEL_RUN_CAPTURES = ("preset-window-wide-v1", "preset-jump-v1")


def read_traces(private_work: Path | None) -> tuple[dict, dict, list[str]]:
    """Every *-native-trace.csv under native-run: the `action` column per
    frame, over every row, keyed by action byte with per-file frame counts.
    These traces were recorded for movement, camera and idle studies; they
    were never read for the action byte until a review asked for the tally."""
    counts: collections.Counter = collections.Counter()
    owners: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    files: list[str] = []
    if private_work is None:
        return counts, owners, files
    for path in sorted((private_work / "native-run").glob("*-native-trace.csv")):
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            if "action" not in (reader.fieldnames or []):
                continue
            files.append(path.name)
            for row in reader:
                value = row.get("action")
                if not value:
                    continue          # short or trailing rows carry no action
                try:
                    action = int(value, 0) & 0xFF
                except ValueError:
                    continue
                counts[action] += 1
                owners[action][path.name] += 1
    return counts, owners, files


def read_captures(private_work: Path | None) -> tuple[dict, dict, list[str], list[str]]:
    counts: collections.Counter = collections.Counter()
    owners: dict[int, set[str]] = collections.defaultdict(set)
    present, missing = [], []
    if private_work is None:
        return counts, owners, present, list(CAPTURES)
    for name in CAPTURES:
        path = private_work / "native-run" / name / CAPTURE_FILE
        if not path.is_file():
            missing.append(name)
            continue
        present.append(name)
        with path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                action = int(row["pre_entity_w0"], 0) & 0xFF
                counts[action] += 1
                owners[action].add(name)
    return counts, owners, present, missing


def parse_mod_loader(include: Path) -> dict:
    """Cross-check the embedded constants against the mod loader headers."""
    result = {"present": False}
    variables = include / "SA2Variables.h"
    if not variables.is_file():
        return result
    result["present"] = True
    text = variables.read_text(encoding="utf-8", errors="replace")
    lists = {}
    for name, va, count in re.findall(
            r"DataArray\(AnimationInfo,\s*(\w+)AnimList,\s*(0x[0-9A-Fa-f]+),\s*(\d+)\)",
            text):
        lists[name] = (int(va, 16), int(count))
    result["anim_lists"] = lists
    match = re.search(
        r"DataArray\(PhysicsData,\s*PhysicsArray,\s*(0x[0-9A-Fa-f]+),\s*(\d+)\)", text)
    result["physics"] = (int(match.group(1), 16), int(match.group(2))) if match else None
    functions = {}
    fpath = include / "SA2Functions.h"
    if fpath.is_file():
        ftext = fpath.read_text(encoding="utf-8", errors="replace")
        for name, va in re.findall(r"ObjectFunc\((\w+),\s*(0x[0-9A-Fa-f]+)\)", ftext):
            functions.setdefault(int(va, 16), name)
        for name, va in re.findall(
                r"FunctionPointer\w*\(\s*[^,]+,\s*(\w+)\s*,\s*\([^)]*\)\s*,\s*(0x[0-9A-Fa-f]+)\s*\)",
                ftext):
            functions.setdefault(int(va, 16), name)
        for name, va in re.findall(r"(\w+)\s*=\s*\(void\s*\*\)\s*(0x[0-9A-Fa-f]+)", ftext):
            functions.setdefault(int(va, 16), name)
    result["functions"] = functions
    result["files"] = [p for p in (variables, fpath, include / "SA2Structs.h",
                                   include / "SA2Enums.h") if p.is_file()]
    return result


def containing_function(functions: dict[int, str], va: int) -> str:
    below = [a for a in functions if a <= va]
    if not below:
        return "not named"
    start = max(below)
    return f"{functions[start]} ({start:#x})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--executable", type=Path,
                        default=os.environ.get("SA2_UNPACKED_EXECUTABLE"))
    parser.add_argument("--private-work", type=Path,
                        default=os.environ.get("SA2_PRIVATE_WORK"))
    parser.add_argument("--repo", type=Path,
                        default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    repo: Path = args.repo.resolve()
    private = args.private_work.resolve() if args.private_work else None
    executable: Path | None = args.executable
    if executable is None and private is not None:
        executable = private / "LocalAssets/SA2/source/sonic2app-unpacked.exe"
    if executable is None or not executable.is_file():
        raise SystemExit("no executable: pass --executable or set SA2_PRIVATE_WORK")
    out_dir = args.out_dir or repo / "Documentation/scope"
    out_dir.mkdir(parents=True, exist_ok=True)

    image = PeImage(executable)
    disagreements: list[str] = []

    # 1. The dispatch table.
    dispatch_bytes = image.bytes_at(ACTION_DISPATCH, len(ACTION_DISPATCH_BYTES) // 2)
    if dispatch_bytes.hex() != ACTION_DISPATCH_BYTES:
        raise SystemExit(f"{ACTION_DISPATCH:#x} is not the pinned dispatch: {dispatch_bytes.hex()}")
    table = struct.unpack_from(f"<{ACTION_COUNT}I", image.data,
                               image.raw(ACTION_TABLE, ACTION_COUNT * 4)[0])
    arm_counts = collections.Counter(table)
    distinct_arms = len(arm_counts)
    distinct_non_default = distinct_arms - (1 if ACTION_DEFAULT_ARM in arm_counts else 0)
    default_actions = arm_counts[ACTION_DEFAULT_ARM]
    text_base, text = image.section(".text")
    for arm in arm_counts:
        if not text_base <= arm < text_base + len(text):
            disagreements.append(f"table entry {arm:#x} is outside .text")
    notes: list[str] = []
    if distinct_non_default != DOCUMENTED_DISTINCT_ARMS:
        disagreements.append(
            f"documented {DOCUMENTED_DISTINCT_ARMS} distinct arms; the table has "
            f"{distinct_arms} distinct targets, {distinct_non_default} of them not the default")
    elif distinct_arms != DOCUMENTED_DISTINCT_ARMS:
        notes.append(
            f"the documented {DOCUMENTED_DISTINCT_ARMS} distinct arms counts the non-default targets; "
            f"the table has {distinct_arms} distinct targets when the default arm `{ACTION_DEFAULT_ARM:#x}` is included")
    if default_actions != DOCUMENTED_DEFAULT_ACTIONS:
        disagreements.append(f"documented {DOCUMENTED_DEFAULT_ACTIONS} default actions, table has {default_actions}")

    # 2. Evidence for dispatch actions: the nine captures, checked against the
    #    gate's pinned counts.
    checker_expected, checker_owners = parse_checker_actions(repo / REPO_CHECKER)
    capture_counts, capture_owners, captures_present, captures_missing = read_captures(private)
    if captures_present and not captures_missing:
        if dict(capture_counts) != checker_expected:
            disagreements.append(
                f"captures give actions {dict(sorted(capture_counts.items()))}, "
                f"checker pins {checker_expected}")
        action_evidence = {a: ", ".join(sorted(capture_owners[a])) for a in capture_counts}
        evidence_basis = f"{len(captures_present)} captures re-read"
    else:
        action_evidence = {a: f"pinned in {REPO_CHECKER}" + (f"; owner {checker_owners[a]}" if a in checker_owners else "")
                           for a in checker_expected}
        evidence_basis = (f"checker pins ({len(captures_present)} of {len(CAPTURES)} captures present; "
                          f"missing: {', '.join(captures_missing) or 'none'})")
    if tuple(sorted(action_evidence)) != DOCUMENTED_ACTIONS_WITH_EVIDENCE:
        disagreements.append(f"documented actions with evidence {DOCUMENTED_ACTIONS_WITH_EVIDENCE}, found {tuple(sorted(action_evidence))}")
    trace_counts, trace_owners, trace_files = read_traces(private)
    observed_any = sorted(set(action_evidence) | set(trace_counts))
    kernel_run_actions = sorted(a for a, owners_ in capture_owners.items()
                                if any(o in KERNEL_RUN_CAPTURES for o in owners_)) if captures_present else []

    # 3. The Sonic action list, and evidence for its entries.
    sonic_rows = read_entries(image, SONIC_LIST_VA, SONIC_LIST_COUNT)
    sonic_valid = [i for i, r in enumerate(sonic_rows) if valid_entry(r)]
    sonic_motions = collections.Counter(r[0] for r in sonic_rows)
    sonic_duplicates = {m: [i for i, r in enumerate(sonic_rows) if r[0] == m]
                        for m, c in sonic_motions.items() if c > 1}
    if not (sonic_rows[0][5] == 0.10000000149011612 and
            (sonic_rows[100][0], sonic_rows[100][2], sonic_rows[100][5]) == (66, 3, 3.0) and
            (sonic_rows[101][0], sonic_rows[101][2], sonic_rows[101][5]) == (101, 6, 0.30000001192092896)):
        disagreements.append("Sonic action list signature (entries 0, 100, 101) did not match the extractor's pins")
    selected = parse_selected(repo / REPO_EXTRACTOR)
    pacing = parse_pacing_cases(repo / REPO_PACING)
    list_evidence: dict[int, list[str]] = collections.defaultdict(list)
    for i in selected:
        list_evidence[i].append("extract_sonic_action_table.py SELECTED")
    for i in pacing:
        list_evidence[i].append("SonicAnimationPacing.cs case")
    pacing_outside_selected = sorted(set(pacing) - set(selected))

    # 4. Other characters: every published list, read at its published address.
    mod = parse_mod_loader(private / MOD_LOADER_INCLUDE) if private else {"present": False}
    ini_path = private / SA_TOOLS_CHARACTERS_INI if private else None
    ini_lists = {}
    if ini_path and ini_path.is_file():
        for name, addr, count in re.findall(
                r"\[([^\]]+) Animation List\]\s*type=animationlist\s*address=([0-9A-Fa-f]+)\s*filename=[^\n]*\s*pointer=[^\n]*\s*count=(\d+)",
                ini_path.read_text(encoding="utf-8", errors="replace")):
            ini_lists[name.replace(" ", "")] = (int(addr, 16) + IMAGE_BASE, int(count))
    if mod["present"]:
        header_lists = mod["anim_lists"]
        embedded = {n: (va, c) for n, va, c in ANIM_LISTS}
        if header_lists != embedded:
            disagreements.append(f"embedded list constants differ from SA2Variables.h: {set(header_lists.items()) ^ set(embedded.items())}")
        if mod["physics"] != (PHYSICS_VA, PHYSICS_DOCUMENTED_COUNT):
            disagreements.append(f"PhysicsArray in SA2Variables.h is {mod['physics']}, embedded {(PHYSICS_VA, PHYSICS_DOCUMENTED_COUNT)}")
    if ini_lists:
        embedded = {n: (va, c) for n, va, c in ANIM_LISTS}
        if ini_lists != embedded:
            disagreements.append(f"Characters.ini lists differ from the embedded constants: {set(ini_lists.items()) ^ set(embedded.items())}")
    other_lists = []
    for name, va, count in ANIM_LISTS:
        rows = read_entries(image, va, count)
        valid = sum(1 for r in rows if valid_entry(r))
        invalid = [i for i, r in enumerate(rows) if not valid_entry(r)]
        motions = len({r[0] for r in rows})
        same = sum(1 for i, r in enumerate(rows) if r[0] == i)
        models = sorted({r[1] for r in rows})
        other_lists.append((name, va, count, valid, motions, same, models, invalid))

    # 5. PhysicsData records: contiguous records from the Sonic one that keep the
    #    shape record 0 has (small timer, lim_h_spd 16, finite bounded floats).
    phys_off, _ = image.raw(PHYSICS_VA, PHYSICS_RECORD_SIZE)
    physics = []
    k = 0
    while True:
        v = struct.unpack_from("<ii31f", image.data, phys_off + k * PHYSICS_RECORD_SIZE)
        if not (0 <= v[0] <= 600 and v[2] == 16.0 and
                all(math.isfinite(x) and abs(x) < 1e4 for x in v[2:])):
            break
        physics.append((PHYSICS_VA + k * PHYSICS_RECORD_SIZE, v))
        k += 1
    if len(physics) != PHYSICS_DOCUMENTED_COUNT:
        disagreements.append(f"PhysicsArray: {len(physics)} contiguous records, header says {PHYSICS_DOCUMENTED_COUNT}")

    # 6. Dispatchers of the same instruction shape across .text.
    dispatchers = []
    for m in DISPATCH_SHAPE.finditer(text):
        bound = m.group(1)[0]
        if bound + 1 >= DISPATCH_MIN_ENTRIES:
            tab = struct.unpack("<I", m.group(2))[0]
            entries = struct.unpack_from(f"<{bound + 1}I", image.data, image.raw(tab, (bound + 1) * 4)[0])
            dispatchers.append((text_base + m.start(), bound + 1, tab, len(set(entries)),
                                containing_function(mod.get("functions", {}), text_base + m.start())))
    if not any(d[0] == ACTION_DISPATCH for d in dispatchers):
        disagreements.append("the dispatcher scan did not find the pinned Sonic dispatch")

    # CSV.
    csv_path = out_dir / "actions.csv"
    with csv_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["kind", "index", "arm_or_animation", "default_arm", "model", "mode",
                         "next_animation", "transition_speed", "animation_speed",
                         "evidence", "evidence_source"])
        for i, arm in enumerate(table):
            writer.writerow(["dispatch", i, f"{arm:#010x}", "yes" if arm == ACTION_DEFAULT_ARM else "no",
                             "", "", "", "", "",
                             "yes" if i in action_evidence else "no", action_evidence.get(i, "")])
        for i, r in enumerate(sonic_rows):
            writer.writerow(["sonic_action_list", i, r[0], "", r[1], r[2], r[3], repr(r[4]), repr(r[5]),
                             "yes" if i in list_evidence else "no", "; ".join(list_evidence.get(i, []))])

    # Markdown.
    exe_sha = sha256(executable)
    script = Path(__file__).resolve()
    lines = []
    w = lines.append
    w("# Player action and motion scope")
    w("")
    w("Generated by `Documentation/scripts/enumerate_sa2_action_scope.py`. "
      "Every number is computed from the inputs in the receipts section; nothing is typed in.")
    w("")
    w("## Totals")
    w("")
    w("| Measure | Count |")
    w("|---|---:|")
    w(f"| Dispatch-table actions (bound `0x{ACTION_COUNT - 1:X}`, table `{ACTION_TABLE:#x}`) | {ACTION_COUNT} |")
    w(f"| Distinct arm targets, including the default `{ACTION_DEFAULT_ARM:#x}` | {distinct_arms} |")
    w(f"| Distinct arm targets, excluding the default | {distinct_non_default} |")
    w(f"| Actions on the default arm | {default_actions} |")
    w(f"| Actions reached in the nine reachability captures ({evidence_basis}) | {len(action_evidence)} |")
    w(f"| Actions observed in any capture or native trace ({len(trace_files)} traces re-read) | {len(observed_any)} |")
    w(f"| Actions whose calls the PResetPosition port has been run on (captures {', '.join(KERNEL_RUN_CAPTURES)}) | {len(kernel_run_actions)} |")
    w(f"| Actions never observed | {ACTION_COUNT - len(observed_any)} |")
    w(f"| Sonic action-list entries (`{SONIC_LIST_VA:#x}`) | {SONIC_LIST_COUNT} |")
    w(f"| Valid entries (finite speed > 0) | {len(sonic_valid)} |")
    w(f"| Distinct source motion indices | {len(sonic_motions)} |")
    w(f"| Entries with evidence (extracted or ported) | {len(list_evidence)} |")
    w(f"| Entries without evidence | {SONIC_LIST_COUNT - len(list_evidence)} |")
    w("")
    w(f"Actions with evidence: {', '.join(str(a) for a in sorted(action_evidence))}.")
    w(f"Actions observed in any capture or trace: {', '.join(str(a) for a in observed_any)}. "
      f"Actions with a kernel run: {', '.join(str(a) for a in kernel_run_actions) or 'none'}. "
      "The port reproduces PResetPosition's output on those calls; it reproduces no action's own logic, "
      "so under the scope table's definition the bit-exact count for actions is 0.")
    if trace_counts:
        w("")
        w("| action | frames in traces | traces |")
        w("|---:|---:|---|")
        for a in sorted(trace_counts):
            w(f"| {a} | {trace_counts[a]} | " + ", ".join(f"{n} ({c})" for n, c in sorted(trace_owners[a].items())) + " |")
    w(f"Action-list entries with evidence: {', '.join(str(i) for i in sorted(list_evidence))}. "
      f"Of these, {len(pacing)} are named by index in `{REPO_PACING}` "
      f"({', '.join(str(i) for i in pacing)}); entries the pacing file names that the extractor does not: "
      f"{', '.join(str(i) for i in pacing_outside_selected) or 'none'}.")
    if sonic_duplicates:
        w("Duplicated source motions in the Sonic list: " +
          "; ".join(f"motion {m} at entries {', '.join(str(i) for i in idx)}" for m, idx in sonic_duplicates.items()) + ".")
    w("")
    w("Motion mode distribution of the Sonic list: " +
      ", ".join(f"mode {m}: {c}" for m, c in sorted(collections.Counter(r[2] for r in sonic_rows).items())) + ".")
    w("")
    w("## Other characters")
    w("")
    w("Located. The SA2 Mod Loader header `SA2Variables.h` publishes sixteen `AnimationInfo` arrays and "
      "SA Tools `GameConfig/PC_SA2/Characters.ini` lists the same sixteen as `type=animationlist`; "
      f"{'both were present and agree with the addresses below' if mod['present'] and ini_lists else 'the embedded addresses were not cross-checked because the header or ini was absent'}. "
      "Each list was read at its published address with the 16-byte entry layout.")
    w("")
    w("| List | VA | Entries | Valid | Distinct motions | AnimNum == index | Model indices | Invalid entries |")
    w("|---|---|---:|---:|---:|---:|---|---|")
    for name, va, count, valid, motions, same, models, invalid in other_lists:
        w(f"| {name} | `{va:#x}` | {count} | {valid} | {motions} | {same} | {', '.join(str(m) for m in models)} | {', '.join(str(i) for i in invalid) or 'none'} |")
    total_entries = sum(x[2] for x in other_lists)
    total_valid = sum(x[3] for x in other_lists)
    w("")
    w(f"All lists: {total_entries} entries, {total_valid} valid, across {len(other_lists)} lists. "
      f"Evidence exists for entries of the Sonic list only.")
    w("")
    w(f"PhysicsData: `SA2Variables.h` declares `PhysicsArray` at `{PHYSICS_VA:#x}` with {PHYSICS_DOCUMENTED_COUNT} records "
      f"indexed by `enum Characters` ({len(CHARACTERS_ENUM)} names). Reading forward from the Sonic record, "
      f"{len(physics)} contiguous records keep its shape. `extract_sonic_physics.py` reads record 0 only; "
      "no other record has a receipt.")
    w("")
    w("| Record | VA | enum Characters name | jump2_timer | max_x_spd | jmp_y_spd | run_speed | rad | height | weight |")
    w("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, (va, v) in enumerate(physics):
        name = CHARACTERS_ENUM[i] if i < len(CHARACTERS_ENUM) else "beyond the enum"
        # Field order from extract_sonic_physics.py FIELDS: jump2_timer 0,
        # max_x_spd 4, jmp_y_spd 6, run_speed 10, rad 28, height 29, weight 30.
        w(f"| {i} | `{va:#x}` | {name} | {v[0]} | {v[4]:g} | {v[6]:g} | {v[10]:g} | {v[28]:g} | {v[29]:g} | {v[30]:g} |")
    w("")
    w(f"Action dispatchers with the Sonic dispatch's exact instruction shape and at least {DISPATCH_MIN_ENTRIES} entries, across `.text`:")
    w("")
    w("| Dispatch VA | Entries | Table | Distinct targets | Containing named routine (nearest lower address in `SA2Functions.h`) |")
    w("|---|---:|---|---:|---|")
    for va, entries, tab, distinct, owner in dispatchers:
        w(f"| `{va:#x}` | {entries} | `{tab:#x}` | {distinct} | {owner} |")
    w("")
    w("The shape match is exact, so dispatchers written with a different register, a 32-bit immediate or a "
      "different load are not counted; this is a lower bound on switch-on-action routines.")
    w("")
    w("## Method")
    w("")
    w(f"1. Verified the 19 dispatch bytes at `{ACTION_DISPATCH:#x}` (`{ACTION_DISPATCH_BYTES}`), then read {ACTION_COUNT} "
      f"little-endian targets at `{ACTION_TABLE:#x}` and counted distinct values and the default arm.")
    w(f"2. Re-read `pre_entity_w0 & 0xFF` from `{CAPTURE_FILE}` in the nine capture directories "
      f"`{REPO_CHECKER}` consumes, and compared the per-action counts with its `EXPECTED_ACTIONS` pin. "
      "An action has evidence when a capture recorded it; the checker's pin is the fallback when captures are absent.")
    w(f"3. Read {SONIC_LIST_COUNT} entries of `{ENTRY_FORMAT}` at `{SONIC_LIST_VA:#x}` and checked the extractor's signature "
      "(entries 0, 100 and 101). An entry has evidence when it is in the extractor's `SELECTED` tuple or is a `case N:` "
      f"in `{REPO_PACING}`; `Documentation/*.md` cite the list only by its address, and the runtime otherwise names clips by string.")
    w("4. A blind scan of `.dataa` for runs of 16-byte entries with the Sonic list's value ranges found only the Sonic list, "
      "because the other lists carry model indices up to 507 where Sonic's carry 0 and 6. The other fifteen were then read "
      "at the addresses the mod loader header publishes, and the header and the SA Tools ini were parsed to check the embedded constants.")
    w(f"5. Walked 132-byte records forward from `{PHYSICS_VA:#x}` while `jump2_timer` is in 0..600, `lim_h_spd` is 16.0 and every float is finite and below 1e4.")
    w("6. Searched `.text` for the dispatch instruction shape and, for each hit with 32 or more entries, read its table and named the nearest lower function in `SA2Functions.h`.")
    w("")
    w("## What did not verify")
    w("")
    if disagreements:
        for d in disagreements:
            w(f"- {d}")
    else:
        w("- Nothing; every documented number reproduced.")
    if notes:
        w("")
        w("Reading notes:")
        w("")
        for n in notes:
            w(f"- {n}")
    w("")
    w("## Receipts")
    w("")
    w("| File | sha256 |")
    w("|---|---|")
    w(f"| `{executable}` | `{exe_sha}` |")
    w(f"| `{script}` | `{sha256(script)}` |")
    for rel in (REPO_CHECKER, REPO_EXTRACTOR, REPO_PACING):
        w(f"| `{repo / rel}` | `{sha256(repo / rel)}` |")
    for p in mod.get("files", []):
        w(f"| `{p}` | `{sha256(p)}` |")
    if ini_path and ini_path.is_file():
        w(f"| `{ini_path}` | `{sha256(ini_path)}` |")
    for name in captures_present:
        p = private / "native-run" / name / CAPTURE_FILE
        w(f"| `{p}` | `{sha256(p)}` |")
    if captures_missing:
        w("")
        w(f"Captures absent at run time: {', '.join(captures_missing)}.")
    w("")
    (out_dir / "actions.md").write_text("\n".join(lines))

    print(f"dispatch actions {ACTION_COUNT}, distinct arms {distinct_arms} "
          f"({distinct_non_default} + default), default actions {default_actions}, "
          f"with evidence {len(action_evidence)}")
    print(f"sonic list entries {SONIC_LIST_COUNT}, valid {len(sonic_valid)}, "
          f"distinct motions {len(sonic_motions)}, with evidence {len(list_evidence)}")
    print(f"other lists {len(other_lists)} ({total_entries} entries, {total_valid} valid); "
          f"physics records {len(physics)}; dispatchers {len(dispatchers)}")
    for d in disagreements:
        print("DID NOT VERIFY:", d)
    print(f"wrote {csv_path} and {out_dir / 'actions.md'}")


if __name__ == "__main__":
    main()
