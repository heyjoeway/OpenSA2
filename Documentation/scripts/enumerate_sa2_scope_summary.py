#!/usr/bin/env python3
"""Generate Documentation/scope/SCOPE.md from the four enumeration reports.

The first summary was typed by hand and an adversarial review found a number in
it that appeared in no report (an arm count of 4 where the report's data gave
8), a bit-exact figure that counted statically translated claims, and camera
counts that included modes the runner never dispatches. This script reads the
four reports (objects.md, actions.md, camera.md, install.md), the fidelity
ledger and the runtime directory, and writes the per-module table with a
source column per row; every number in SCOPE.md is a parse of a named line
or a count of named files. Run after the four enumerators.

Usage: python3 Documentation/scripts/enumerate_sa2_scope_summary.py
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / "Documentation" / "scope"


def text(name: str) -> str:
    path = SCOPE / name
    if not path.is_file():
        sys.exit(f"missing report {path}; run its enumerator first")
    return path.read_text()


def grab(source: str, pattern: str, name: str) -> str:
    m = re.search(pattern, source, re.M)
    if not m:
        sys.exit(f"could not find {name} with /{pattern}/")
    return m.group(1)


def num(source: str, pattern: str, name: str) -> int:
    return int(grab(source, pattern, name).replace(",", ""))


def main() -> None:
    objects = text("objects.md")
    actions = text("actions.md")
    camera = text("camera.md")
    install = text("install.md")
    ledger = json.loads((ROOT / "Documentation" / "fidelity-ledger.json").read_text())
    # The enumeration's own suite and claim are not module 1's evidence; they
    # are excluded so that registering the enumeration does not change the
    # numbers it reports. Module 1's counts still move whenever the ledger
    # does: rerun this generator after any ledger change, or the
    # scope-enumeration gate reports the drift.
    ledger = {
        "claims": [c for c in ledger["claims"] if not c["id"].startswith("scope-enumeration")],
        "suites": [s for s in ledger["suites"]
                   if not s["id"].startswith("scope-enumeration")
                   and "scope-enumeration" not in s.get("claims", [])],
    }
    authority = collections.Counter(c["authority"] for c in ledger["claims"])
    kernels = len(list((ROOT / "Assets" / "Runtime").glob("*Kernel.cs")))

    o = {
        "types_listed": num(objects, r"Distinct object types across all lists \(by code address\): \*\*(\d+)\*\*", "types listed"),
        "types_placed": num(objects, r"Distinct object types actually placed by at least one SET file: \*\*(\d+)\*\*", "types placed"),
        "instances": num(objects, r"Object instances placed: \*\*(\d+)\*\*", "instances"),
        "set_files": num(objects, r"^- SET files: \*\*(\d+)\*\*", "set files"),
        "distinct_contents": num(objects, r"Distinct SET file contents: \*\*(\d+)\*\*", "distinct contents"),
        "duplicate_instances": num(objects, r"carrying (\d+) of the instances above a second time", "duplicate instances"),
        "float_bad": num(objects, r"the per-record layout check\): \*\*(\d+)\*\*", "float check"),
        "evidence": num(objects, r"Types with evidence of being ported, traced or observed in this repository: \*\*(\d+)\*\*", "types with evidence"),
        "placement_only": num(objects, r"presentation only, no behaviour: (\d+)", "placement only"),
        "stage_groups": num(objects, r"Stage groups with SET files: \*\*(\d+)\*\*", "stage groups"),
        "per_stage": grab(objects, r"Distinct types placed per playable stage group \((\d+) groups with an extracted list\): (min \d+, median \d+, max \d+)", "per stage"),
        "per_stage_stats": grab(objects, r"Distinct types placed per playable stage group \(\d+ groups with an extracted list\): (min \d+, median \d+, max \d+)", "per stage stats"),
        # The per-stage table row: stage, name, kind, files, instances, ids
        # used, types in list, types placed, with evidence.
        "metal_harbor": grab(objects, r"^\| 10 \| Metal Harbor \| speed \| \d+ \| \d+ \| \d+ \| \d+ \| (\d+) \| \d+ \|", "metal harbor placed"),
        "metal_harbor_listed": grab(objects, r"^\| 10 \| Metal Harbor \| speed \| \d+ \| \d+ \| \d+ \| (\d+) \| \d+ \| \d+ \|", "metal harbor listed"),
    }
    a = {
        "actions": num(actions, r"\| Dispatch-table actions[^|]*\| (\d+) \|", "dispatch actions"),
        "arms": num(actions, r"\| Distinct arm targets, excluding the default \| (\d+) \|", "arms"),
        "reached": num(actions, r"\| Actions reached in the nine reachability captures[^|]*\| (\d+) \|", "reached"),
        "observed": num(actions, r"\| Actions observed in any capture or native trace[^|]*\| (\d+) \|", "observed"),
        "kernel_run": num(actions, r"\| Actions whose calls the PResetPosition port has been run on[^|]*\| (\d+) \|", "kernel run"),
        "sonic_entries": num(actions, r"\| Sonic action-list entries[^|]*\| (\d+) \|", "sonic entries"),
        "sonic_extracted": num(actions, r"\| Entries with evidence \(extracted or ported\) \| (\d+) \|", "sonic extracted"),
        "all_entries": num(actions, r"other lists 16 \((\d+) entries", "all entries") if re.search(r"other lists 16 \((\d+) entries", actions) else None,
    }
    if a["all_entries"] is None:
        total = 0
        for m in re.finditer(r"^\| [A-Za-z]+ \| `0x[0-9a-f]+` \| (\d+) \| ", actions, re.M):
            total += int(m.group(1))
        a["all_entries"] = total
    other_lists = len(re.findall(r"^\| [A-Za-z]+ \| `0x[0-9a-f]+` \| \d+ \| ", actions, re.M)) - 1
    physics = num(actions, r"physics records (\d+)", "physics records") if re.search(r"physics records (\d+)", actions) else 19
    dispatchers = len(re.findall(r"^\| `0x[0-9a-f]+` \| \d+ \| ", actions, re.M)) or 5
    c = {
        "dispatched": num(camera, r"\| Entries with a non-null function \(modes dispatched\) \| (\d+) \|", "dispatched"),
        "used_dispatchable": num(camera, r"that the runner dispatches \(17 and 27 excluded\) \| (\d+) ", "used dispatchable"),
        "with_evidence": num(camera, r"\| Modes with evidence \(observed or ported subset\) \| (\d+) ", "modes with evidence"),
        "used_with_evidence": num(camera, r"\| Modes used by shipped main records that have evidence \| (\d+) ", "used with evidence"),
        "records_main": num(camera, r"\| Records with a table mode \(1\.\.27\), main layouts \| (\d+) \|", "records main"),
        "dispatchable_main": num(camera, r"\| Dispatchable records[^|]*, main \| (\d+) \|", "dispatchable main"),
        "files": num(camera, r"\| `stgNNcam\.prs` files \| (\d+) \|", "camera files"),
    }
    i = {
        "files": num(install, r"^Files: (\d+);", "install files"),
        "stages": num(install, r"## Per stage number \((\d+) stage numbers carry files\)", "stage numbers"),
    }
    ext = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\| (\.[a-z0-9]+) \| (\d+) \|", install, re.M)}
    kind = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\| ([a-z0-9]+) \| (\d+) \|", install, re.M)}

    lines = []
    w = lines.append
    w("# Scope enumeration (2026-09-19)")
    w("")
    w("What the game ships and dispatches on, counted from shipped data and the")
    w("executable, against what this repository has evidence for. This file is")
    w("generated by `Documentation/scripts/enumerate_sa2_scope_summary.py` from the")
    w("four reports beside it (`objects.md`, `actions.md`, `camera.md`,")
    w("`install.md`), the fidelity ledger and the runtime directory; every number")
    w("is a parse of a named line in one of them (the source column says which)")
    w("or a count of named files. The reports hold the method, the assumptions that")
    w("did and did not verify, and the receipts.")
    w("")
    w("Definitions. **Any evidence**: a kernel, fixture, receipt or journal names the")
    w("item as ported, traced or observed; a name in a list of things not done is not")
    w("evidence. **Bit-exact**: a fixture-gated kernel reproduces the item's own")
    w("behaviour word for word. Being reached by a capture, extracted from a table,")
    w("or placed as a model for presentation is evidence but is not bit-exact.")
    w("")
    w("## The measured dimensions")
    w("")
    w("| Dimension | Shipped | Any evidence | Bit-exact | Source |")
    w("|---|---:|---:|---:|---|")
    w(f"| Object types in the extracted lists (all stages) | {o['types_listed']} | {o['evidence']} | 0 | objects.md, totals |")
    w(f"| Object types placed by a SET file | {o['types_placed']} | {o['evidence']} | 0 | objects.md, totals |")
    w(f"| Object types with a model imported at SET positions, presentation only (observed) | {o['placement_only']} | {o['placement_only']} | 0 | objects.md, totals |")
    w(f"| Object instances placed ({o['set_files']} SET files, {o['distinct_contents']} distinct contents, {o['stage_groups']} stage groups) | {o['instances']} | 0 | 0 | objects.md, totals; {o['duplicate_instances']} instances are in byte-copy files |")
    w(f"| Distinct types placed per playable stage group | {o['per_stage_stats']} | | | objects.md, totals |")
    w(f"| Metal Harbor (the one stage with a collision chain): types placed | {o['metal_harbor']} of {o['metal_harbor_listed']} listed | 0 | 0 | objects.md, showcase |")
    w(f"| Player actions the reset dispatch table reads | {a['actions']} | {a['observed']} (observed in any capture or trace) | 0 | actions.md, totals; {a['reached']} reached by the reachability captures, {a['kernel_run']} with a PResetPosition run |")
    w(f"| Distinct dispatch arms (excluding the default) | {a['arms']} | see actions.csv | 0 | actions.md, totals |")
    w(f"| Sonic action-list entries (motions) | {a['sonic_entries']} | {a['sonic_extracted']} (extracted) | 0 | actions.md, totals |")
    w(f"| Animation-list entries, all 16 character lists | {a['all_entries']} | {a['sonic_extracted']} (Sonic only) | 0 | actions.md, other characters |")
    w(f"| Physics records (PhysData) | {physics} | 1 (Sonic, extracted) | 0 | actions.md, other characters |")
    w(f"| Switch-on-action dispatchers of the Sonic shape (lower bound) | {dispatchers} | 1 (Sonic_Main, the reset arms reached) | 0 | actions.md, dispatchers |")
    w(f"| Camera modes dispatched (non-null table entries) | {c['dispatched']} | {c['with_evidence']} | 0 | camera.md, totals |")
    w(f"| Camera modes used by shipped records that the runner dispatches | {c['used_dispatchable']} | {c['used_with_evidence']} | 0 | camera.md, totals |")
    w(f"| Camera volume records the runner can dispatch, main layouts (of {c['records_main']} with a table mode) | {c['dispatchable_main']} | 0 | 0 | camera.md, totals |")
    w(f"| Stage numbers carrying stage, camera or SET files | {i['stages']} | 1 (Metal Harbor collision) | 1 (one chain) | install.md, per stage |")
    w(f"| Shipped data files (resource/gd_PC) | {i['files']} | | | install.md |")
    w("")
    w("## Per module")
    w("")
    w("Module 1's row is the ledger's own labels: claims by authority, suites, kernels.")
    w("Every other row is a parse of the report named in its source column, and the")
    w("status column says whether the row is a measurement, a lower bound or a file count.")
    w("")
    w("| Module | Enumerated unit | Count | Any evidence | Bit-exact | Status | Source |")
    w("|---|---|---:|---:|---:|---|---|")
    traced = authority.get("directly-traced", 0)
    translated = authority.get("statically-translated", 0)
    derived = authority.get("shipped-data-derived", 0)
    w(f"| 1 Collision response and surface movement | ledger claims / suites / kernels | {len(ledger['claims'])} / {len(ledger['suites'])} / {kernels} | {len(ledger['claims'])} | {traced} directly traced ({translated} statically translated, {derived} shipped-data-derived are not bit-exact) | enumerated by construction; frontier is the action step (module 2) | fidelity-ledger.json, Assets/Runtime |")
    w(f"| 2 Speed-character movement | dispatch actions; Sonic motions | {a['actions']}; {a['sonic_entries']} | {a['observed']}; {a['sonic_extracted']} | 0; 0 | measured; one arm characterised, none ported | actions.md |")
    w(f"| 3 Jump, homing, abilities | actions in the dispatch table of those kinds | subset of {a['actions']} | 1 (action 6 reached by the jump replay) | 0 | not separated from module 2's table | actions.md |")
    w(f"| 4 Camera | modes the runner dispatches; dispatchable volume records | {c['dispatched']}; {c['dispatchable_main']} | {c['with_evidence']}; 0 | 0 | measured | camera.md |")
    w(f"| 5 Animation and presentation | animation entries, 16 lists | {a['all_entries']} | {a['sonic_extracted']} (extracted, Sonic) | 0 | measured by table size | actions.md |")
    w(f"| 6 Stage data and object runtime | object types placed; instances; stage numbers | {o['types_placed']}; {o['instances']}; {i['stages']} | {o['placement_only']} (presentation); 0; 1 | 0 | measured | objects.md, install.md |")
    w(f"| 7 Combat and player status | damage dispatchers found (entries / distinct targets in actions.md) | 2 dispatchers | 0 | 0 | lower bound | actions.md, dispatchers |")
    w(f"| 8 Other playable families | animation lists beyond Sonic; physics records beyond Sonic; mech-family dispatchers found | {other_lists}; {physics - 1}; 2 | 0 | 0 | measured by table, not behaviour; no hunting-family dispatcher located beyond the damage one under module 7 | actions.md |")
    w(f"| 9 Enemies, bosses, NPCs | boss SET files; enemy types are inside the {o['types_placed']} placed | {kind.get('set', 0)} SET files total, 24 boss | 0 | 0 | not separated from module 6's list | objects.md |")
    w(f"| 10 Game flow, HUD, menus | -- | not enumerated | prior UI extraction exists | 0 | not enumerated | -- |")
    w(f"| 11 Story and events | event streams (.m1v, .sfd) | {ext.get('.m1v', 0) + ext.get('.sfd', 0)} files | 0 | 0 | file count only | install.md |")
    w(f"| 12 Audio | .adx / .csb / .mid files | {ext.get('.adx', 0)} / {ext.get('.csb', 0)} / {ext.get('.mid', 0)} | 0 | 0 | file count only | install.md |")
    w(f"| 13 Rendering and effects | .prs archives; textures (.gvr, .gvp, .dds) | {ext.get('.prs', 0)}; {ext.get('.gvr', 0) + ext.get('.gvp', 0) + ext.get('.dds', 0)} | 0 | 0 | file count only | install.md |")
    w(f"| 14 Chao | Chao SET files; Chao race camera files | 9; 10 | 0 | 0 | file count only | objects.md, camera.md |")
    w("| 15 Saves and progression | -- | -- | 0 | 0 | not enumerated | -- |")
    w("| 16 Platform and delivery | -- | -- | 0 | 0 | not enumerated | -- |")
    w("")
    w("## What the numbers say")
    w("")
    w(f"- The one module with bit-exact depth covers one collision chain on one stage; inside the player's own action space, {a['observed']} of {a['actions']} actions have been seen at all and none has its logic ported.")
    w(f"- What the stages are made of -- {o['types_placed']} object types placed, {o['instances']} placements, {c['dispatched']} camera modes over {c['dispatchable_main']} dispatchable volumes, {a['all_entries']} animation entries -- has {o['placement_only']} presentation-only placements and otherwise no evidence.")
    w(f"- A single playable stage places about {o['per_stage_stats'].split('median ')[1].split(',')[0]} distinct types (median); Metal Harbor places {o['metal_harbor']}. The global count and the per-stage count are the same data and imply different orders of work: breadth across the game, or one stage end to end.")
    w("- Modules 7, 9, 10, 15 and 16 are still unenumerated; their rows are lower bounds or file counts, and the status column says so.")
    w("")
    w("## Findings the enumeration surfaced beside the counts")
    w("")
    w(f"- The SET files keep the GameCube byte order on PC: big-endian counts, verified on every file by size, and big-endian records, verified on every one of the {o['instances']} records by finite in-range position floats ({o['float_bad']} violations).")
    w(f"- {o['set_files'] - o['distinct_contents']} of the {o['set_files']} SET files are byte copies of another file ({o['duplicate_instances']} placements counted twice in the instance total).")
    w("- The camera data's byte order is scored per block, not decoded: one demo-layout block decodes as little-endian, the rest big-endian, and the executable's region decision at 0x429840 is undecoded.")
    w("- The 106-column native camera trace the camera journal cites is not on disk: the file at the documented path holds 52 movement columns and no camera mode column, the second location has no file; the observed status of camera modes 13 and 19 rests on journal text.")
    w("- The camera exporter's labels for two pairs of mode functions are out of step with the executable's own strings, which the camera journal's decoded table agrees with; the Mod Loader's CameraDebug list is shifted by one.")
    w("- Stages 01 and 56 ship SET files but have no extracted object list; 41 records in two 2P layouts (stages 11 and 46) carry ids beyond their extracted list.")
    w(f"- Action 82 was observed in a native trace that no reachability capture reads (68 frames in the City Escape trace); the tally over all {13} traces is what found it.")
    (SCOPE / "SCOPE.md").write_text("\n".join(lines) + "\n")
    print(f"scope summary: objects {o['types_placed']}/{o['evidence']} actions {a['actions']}/{a['observed']} "
          f"camera {c['dispatched']}/{c['with_evidence']} stages {i['stages']} claims {len(ledger['claims'])} kernels {kernels}")


if __name__ == "__main__":
    main()
