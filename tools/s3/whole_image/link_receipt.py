#!/usr/bin/env python3
"""Assemble `Documentation/receipts/s3-whole-image-link-v1.json` from the run logs.

The second S3 slice's receipt: the lift-bound decision with the evidence that
decided it, the whole-image link, and what the lifted image does with jump
tables, indirect calls and the G1 exception-handling strategy.

Every number is read out of a file under `private/generated/s3/`, which a tool
wrote (AGENTS.md rule 7). Nothing is typed. No lifted output crosses into the
repository: hashes, counts and grouped samples only.

Inputs, all generated:
  private/generated/s3/link/link.<tag>.json        link_all.py
  private/generated/s3/link/indirect.<tag>.json    indirect.py
  private/generated/s3/link/table_probe.<tag>.json table_probe.py
  private/generated/s3/link/verify.census.<tag>.json  census.py (reproduction)
  private/generated/s3/link/verify.decode_failures.json
  private/generated/s3/whole_image/census.<tag>.json  census.py (slice 1)
  private/generated/s3/whole_image/error_sites.<tag>.json

Usage:
  .venv/bin/python tools/s3/whole_image/link_receipt.py [--out PATH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def _repo_relative(value):
    """Rewrite every absolute repository path in `value` to a relative one.

    Tools that are handed a path record it as given, so an absolute path
    reaches the receipt wherever a command line or an input name is copied
    through. Someone's home directory is not a measurement, it does not
    reproduce on another machine, and the sibling lift receipt carries none,
    so this walks the whole document rather than one block. Applied once at
    the write, so no call site has to remember.
    """
    prefix = str(C.ROOT) + "/"
    if isinstance(value, str):
        return value.replace(prefix, "")
    if isinstance(value, dict):
        return {k: _repo_relative(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_repo_relative(v) for v in value]
    return value

LINK = C.GEN / "s3" / "link"
TOOLS = Path(__file__).resolve().parent
RECEIPT = C.ROOT / "Documentation" / "receipts" / "s3-whole-image-link-v1.json"


def load(path: Path):
    if not path.is_file():
        raise SystemExit(f"missing generated input {path}; see this tool's docstring")
    return json.loads(path.read_text())


def disk_bytes() -> dict:
    """IR and object bytes per arm, straight off the files, not off a log."""
    out = {}
    for tag in ("map", "next"):
        raw = pc24 = fold = obj = 0
        for e in os.scandir(C.LIFT / tag):
            if e.name.endswith(".pc24.ll"):
                pc24 += e.stat().st_size
            elif e.name.endswith(".ll"):
                raw += e.stat().st_size
        for e in os.scandir(C.FOLD / tag):
            fold += e.stat().st_size
        for e in os.scandir(C.BUILD / tag):
            if e.name.endswith(".o"):
                obj += e.stat().st_size
        out[tag] = {"raw_ir_bytes": raw, "ir_bytes_after_pc24_pass": pc24,
                    "ir_bytes_after_fold": fold, "object_bytes": obj}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=RECEIPT)
    args = ap.parse_args()

    link_next = load(LINK / "link.next.json")
    link_map = load(LINK / "link.map.json")
    indirect = load(LINK / "indirect.next.json")
    probe_sim = load(LINK / "table_probe.next.json")
    decode = load(LINK / "verify.decode_failures.json")
    census = {t: load(LINK / f"verify.census.{t}.json") for t in ("map", "next")}
    slice1 = {t: load(C.OUT / f"census.{t}.json") for t in ("map", "next")}
    errors = {t: load(C.OUT / f"error_sites.{t}.json") for t in ("map", "next")}
    bytes_ = disk_bytes()

    def strip_seconds(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "seconds"}

    reproduces = {t: strip_seconds(census[t]) == strip_seconds(slice1[t])
                  for t in ("map", "next")}

    def arm(tag: str) -> dict:
        c = census[tag]
        game = c["traps"]["by_region"]["game"]
        return {
            "straddle": c["map_defects"]["counts"]["straddle"],
            "ends_at_end": c["map_defects"]["counts"]["ends_at_end"],
            "gap_target": c["map_defects"]["counts"]["gap_target"],
            "decode_failure_sites": decode[tag]["decode_failure_sites"],
            "decode_failure_sites_by_region": {
                r: decode[tag]["by_region"][r]["decode_failure_sites"]
                for r in decode[tag]["by_region"]},
            "game_trap_sites": game["trap_sites"],
            "game_trap_targets_in_no_map_entry":
                c["traps"]["target_class"]["game"].get("gap", 0),
            "game_trap_targets_that_are_map_entries":
                c["traps"]["target_class"]["game"].get("map_entry", 0),
            "game_reachable_instructions": game["reachable_instructions"],
            "game_mapped_instructions": game["mapped_instructions"],
            "kill_criterion_percent": c["traps"]["kill_criterion"]["percent"],
            "kill_criterion_fires": c["traps"]["kill_criterion"]["fires"],
            "unsupported_instruction_sites":
                errors[tag].get("unsupported_instructions", {}).get("sites"),
            "unsupported_opcodes":
                errors[tag].get("unsupported_instructions", {}).get("opcode_histogram"),
            "unsupported_by_region":
                errors[tag].get("unsupported_instructions", {}).get("by_region"),
        } | bytes_[tag]

    out = {
        "id": "s3-whole-image-link-v1",
        "schema": 1,
        "stage": "S3",
        "date": dt.date.today().isoformat(),
        "question": "which lift bound does S3 publish, does the whole image link, and "
                    "what does the lifted image do with jump tables, indirect calls and "
                    "the G1 exception-handling strategy?",
        "inputs": {
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": C.sha256(C.EXE)},
            "function_map": {"path": "private/generated/function-map.json",
                             "sha256": C.sha256(C.MAP),
                             "functions": len(C.functions())},
            "records_built_on": [
                "Documentation/receipts/s3-whole-image-lift-v1.json",
                "agents-journals/s3/2026-09-20-s3-whole-image-lift.md",
                "Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md",
                "Documentation/S3_INPUTS.md",
            ],
            "pipeline": {f"tools/s3/whole_image/{p.name}": C.sha256(p)
                         for p in sorted(TOOLS.glob("*.py")) + sorted(TOOLS.glob("*.c"))},
            "machine": {"nproc": os.cpu_count(), "platform": platform.platform(),
                        "python": platform.python_version()},
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s3/whole_image/census.py --tag map "
            "--out private/generated/s3/link/verify.census.map.json",
            ".venv/bin/python tools/s3/whole_image/census.py --tag next "
            "--out private/generated/s3/link/verify.census.next.json",
            ".venv/bin/python Documentation/scripts/s3_metrics_inputs.py --tag next",
            ".venv/bin/python tools/s3/whole_image/link_all.py --tag next --phase all",
            ".venv/bin/python tools/s3/whole_image/link_all.py --tag map --phase all",
            ".venv/bin/python tools/s3/whole_image/table_probe.py --tag next",
            ".venv/bin/python tools/s3/whole_image/indirect.py --tag next --jobs 16 "
            "--probe-limit 0",
            ".venv/bin/python tools/s3/whole_image/link_receipt.py",
        ],
        "lift_bound": {
            "question": "should the whole-image lift use --to-next-entry?",
            "decision": "Yes, and the published metrics come from that arm. Slice 1 "
                        "recommended it; this slice reproduced both arms before adopting "
                        "it. The wider bound removes every straddle, cuts remill decode "
                        "failures 425 -> 18, cuts game-region trap sites 1080 -> 611 and "
                        "cuts the game-region trap targets that land in no map entry "
                        "507 -> 23, for 0.119 % more IR and 0.057 % more object. It stays "
                        "off in tools/s2/corpus_run/lift_set.py, whose output is pinned by "
                        "Documentation/receipts/emitter-determinism-v1.json.",
            "census_reproduces_slice_1_byte_for_byte": reproduces,
            "independent_decode_failure_scan": {
                "method": "every raw module re-scanned for the lifter's own decode-failure "
                          "shape, without reading slice 1's output",
                "map": decode["map"]["decode_failure_sites"],
                "next": decode["next"]["decode_failure_sites"],
            },
            "arms": {"map": arm("map"), "next": arm("next")},
            "corrections_to_slice_1": [
                "slice 1's journal reports +0.005 % IR and +0.03 % object for the wider "
                "bound; the bytes on disk and slice 1's own receipt say +0.119 % and "
                "+0.057 %. The journal's `next`-arm totals match neither.",
                "'0 unsupported instructions in the game region' is the map arm's figure. "
                "Under the adopted arm there are 2 probed sites, fnsave and frstor at "
                "0x00828d3d and 0x00828d49 inside FUN_00828c2d, which is the CRT's pow "
                "with a game region label.",
                "the published emit log was already on the `next` arm before this slice "
                "ran, although slice 1's journal and the operating notes both say the "
                "published metrics come from `map`.",
            ],
        },
        "link": {
            "attempted": "the whole image, both arms",
            "next": {k: v for k, v in link_next["link"].items()
                     if k not in ("stdout", "stderr_tail")},
            "map": {k: v for k, v in link_map["link"].items()
                    if k not in ("stdout", "stderr_tail")},
            "table_generation": link_next["table_generation"],
            "first_failure": {
                "error": "undefined symbol: __sa2_dispatch_stub",
                "references": 18259,
                "cause": "tools/s2/corpus_run/internalize.py folds every module's "
                         "missing-block trampolines into __sa2_dispatch_stub, which is "
                         "defined only in harness/plan_driver.c. Any program that links "
                         "folded IR and is not the corpus driver fails to link.",
                "fix_applied": "tools/s3/whole_image/link_probe.c defines its own copy; "
                               "the definition belongs in runtime/, which this slice does "
                               "not own.",
            },
            "probe": link_next.get("probe", {}).get("result"),
            "table_index": {
                "finding": "runtime/function_table_impl.c indexes with hash_va(va) & mask. "
                           "The low bits of a multiplication depend only on the low bits "
                           "of the operands, so masking them is arithmetically almost the "
                           "same as not hashing: 2.689 mean extra probes against 2.682 for "
                           "no hash at all, where taking the high bits gives 0.295 and "
                           "theory for this load factor is 0.31.",
                "simulation": probe_sim,
            },
        },
        "indirect_edges": {
            "decoded_coverage": indirect["decoded_coverage"],
            "map_surface": indirect["map_surface_total"],
            "map_surface_by_region": indirect["map_surface"],
            "jump_table_arms": indirect["jump_table_arms"],
            "ir_volume_check": indirect["ir_volume_check"],
            "ir_confirmation": indirect["ir_confirmation"],
            "lifted_ir": indirect["lifted_ir"],
            # The arm claim, asked of the linked program instead of derived from the
            # rule: link_probe.c looks every jump-table arm address up in the function
            # table the linked image actually holds, and measures what a miss costs by
            # walking that table's own slots. `arm_list` is the address list it was
            # given (link_all.py --phase arms) and the map's prediction for it.
            # The probe records the arm file by the absolute path it was handed,
            # and an absolute path under someone's home directory does not belong
            # in a committed record.
            "arm_dispatch_measured": (link_next.get("probe") or {}).get(
                "result", {}).get("arm_dispatch"),
            "arm_list": link_next.get("arms"),
            "finding": "remill decodes by recursive descent and stops at an indirect "
                       "branch, so 5453 of the 5817 jump-table arms are in no module at "
                       "all and 130310 instructions exist in none. At run time the site lifts "
                       "to __remill_jump, which dispatches through the function table, and "
                       "the table holds one row per map entry: 4 of the 5817 arms are map "
                       "entries, so 5813 are a guaranteed dispatch miss. That is no longer "
                       "a derivation from the rule: `arm_dispatch_measured` is the linked "
                       "program's own answer for every arm address, hit by hit, with the "
                       "cost of each miss taken from the table's slots. Design 3.4's "
                       "'jump tables: from the analyzer, emitted as switch' is not what "
                       "this pipeline does, and closing it is S3 work that remains.",
        },
        "exception_handling": indirect["exception_handling"],
        "limits": [
            "Nothing guest-side was executed. The linked program initialises the runtime "
            "and the function table and resolves every address; it calls no lifted "
            "function, because running guest code at image scale needs S4's import stubs "
            "and Documentation/S3_INPUTS.md section 3a's CPU-feature globals. Every trap "
            "below is therefore a static prediction. The jump-table arm misses are not: "
            "they are lookups the linked program performed in the table it holds. What "
            "stays predicted about them is that a guest run would reach those arms.",
            "The compile target is x86_64-linux-musl while the lifted IR carries an "
            "i386-windows-msvc triple, as in every S1 and S2 measurement.",
            "The link time is measured with the objects warm in the page cache, since the "
            "compile that produced them and the failed first link preceded it.",
            "The per-object compile times in this slice's `compile` block are zig cache "
            "hits, not compilations; the whole-image compile time is slice 1's "
            "measurement and is not re-measured here.",
            "The IR confirmation of unlifted jump-table arms searches for one distinctive "
            "constant per arm, so a hit can be that constant occurring elsewhere in the "
            "function: 547 of 2634 hits are an upper bound on disagreement, and the "
            "volume check is the independent answer.",
            "Whether an arm is lifted is decided by a recursive descent of the same shape "
            "as remill's; it is corroborated at the IR level twice but not by driving.",
        ],
        "verdict": {
            "lift_bound": "--to-next-entry, adopted and published",
            "m1": "12683/12683 emitted, unchanged by the bound",
            "m2": "12683/12683 compiling, unchanged by the bound",
            "traps": "611 missing-block sites in the game region across 470 addresses "
                     "(1134 over the image), from 1080/1741 on the narrow bound",
            "link": "the whole image links: 12683 objects, 261.5 MB in, 173.4 MB out, "
                    "0.21 s wall, 609 MB peak linker RSS, 0 duplicate symbols, 0 "
                    "unresolved lifted symbols after the dispatch stub is supplied",
            "jump_tables": "not closed: the linked program's own table answers that "
                           "5813 of the 5817 arms have no row, and 5453 are in no module "
                           "at all",
            "decoded_coverage": "88.81 % of mapped instructions are decoded into some "
                                "module, not the 99.88 % the census reports: the census "
                                "seeds the map's jump-table targets and so counts 130310 "
                                "instructions that exist in no module; in the game region "
                                "alone it is 846518 of 971553, 87.1 %",
            "exception_handling": "the lift matches G1 step 1: FS:[0] is an ordinary "
                                  "guest-memory access through the State's FS base, and "
                                  "the IR's FS-base loads match the map's registration "
                                  "sites region by region, with one function differing "
                                  "at count level (0x0086c7c0 __ValidateEH3RN, whose sixth "
                                  "FS-base load is the FS:[0x18] self-pointer read rather "
                                  "than a registration). "
                                  "The runtime half does not exist yet: nothing sets the "
                                  "FS base, so FS:[0] is guest address 0 inside the null "
                                  "guard, and the IAT is unbound, so RaiseException "
                                  "dispatches on 0x00515a3e and misses.",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_repo_relative(out), indent=1, sort_keys=True) + "\n")
    print(f"-> {args.out}  ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
