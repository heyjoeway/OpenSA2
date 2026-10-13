#!/usr/bin/env python3
"""Write M1, M2 and Traps from S3's whole-image lift.

The S1 version of this script recorded the absence of an emitter. S3 has
one: `tools/s3/whole_image` lifts, passes, folds and compiles every
function in the analyzer's map, and writes a JSONL record per function.
This reads those records, never a literal:

  private/generated/s3/whole_image/lift/<tag>.jsonl   -> emitted
  private/generated/s3/whole_image/build/<tag>.jsonl  -> compiled, failed
  private/generated/s3/whole_image/census.<tag>.json  -> traps

`emitted` is a function the pipeline produced a module for; `compiled` is
one whose object built. Traps are the missing-block sites design 5 (S3)
asks be counted with their opcodes: a control-flow edge leaving the lifted
byte range, which is the adopted pipeline's analogue of an unsupported
instruction, recorded with the guest instruction that reaches it.

They come from the **census**, not from the lift log. The lift log's
`missing_block_calls` counts calls in the raw module, before
`internalize.py` folds the direct-call trampolines away, and in the game
region that reads about thirty times high: on the published arm, 30751
against a true 611. The
census walks each module's control-flow edges and keeps only those that
leave its lifted byte range, which is what a trap is. Using the raw count
would have made the metric fire design 5 (S3)'s 2 % kill criterion on an
artefact.

The tag selects which arm of the lift to publish. `map` is the lift bounded
by the analyzer's recorded function sizes; `next` is the same lift with
`--to-next-entry`, adopted for the whole image in S3 slice 2.

**`--tag` is required and has no default.** It used to default to `map`,
and after the adoption that default was a loaded gun: re-running the publish
step as the journals documented it would have rewritten the emit log with
the abandoned arm, moving Traps from 611 back to 1080, while all 17 gates
stayed green -- `metrics.py` regenerates from the same log it would have
rewritten, and METRICS.md carries no arm field to disagree with. The S3
interrogation found it aimed. A required flag makes the arm an act rather
than an omission, and `check_published_arm.py` makes the choice checkable.

Usage: .venv/bin/python Documentation/scripts/s3_metrics_inputs.py --tag next
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gitlog import project_root

RECEIPT = "Documentation/receipts/s3-whole-image-lift-v1.json"


def records(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"missing {path}; run tools/s3/whole_image/lift_all.py first")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, choices=["map", "next"],
                        help="which arm of the lift to publish; required, because a "
                             "default here silently republishes the wrong arm")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    root = project_root(args.root)
    base = root / "private" / "generated" / "s3" / "whole_image"

    lifted = records(base / "lift" / f"{args.tag}.jsonl")
    built = records(base / "build" / f"{args.tag}.jsonl")
    census_path = base / f"census.{args.tag}.json"
    if not census_path.is_file():
        raise SystemExit(f"missing {census_path}; run tools/s3/whole_image/census.py --tag "
                         f"{args.tag} first")
    census = json.loads(census_path.read_text())["traps"]
    trap_rows = census.get("functions")
    # Design 5's S3 exit asks for the trap count "with every trap opcode
    # listed". The census classifies each trap site by the opcode of the
    # instruction that reaches it, so the histogram is a real measurement and
    # belongs in the published log rather than only in the receipt: a gate
    # cannot check a number that is not published.
    # M1 counts functions for which a `define` exists. That is the design's
    # definition and it is honest, but on its own "12683/12683" reads as "the
    # game's code is present", and 13 % of the game region's instructions are
    # behind jump-table arms remill never decoded. The S3 interrogation called
    # 12683/12683 alone the most misleading true statement in the record, so
    # the instruction-level companion is published here and the M1 row carries
    # it. It is absent when the indirect measurement has not been run, and the
    # row simply omits the clause.
    # The dispatch surface: how many addresses something in the image can jump
    # to that the function table cannot resolve. S3 slice 6 found 343 lifted
    # jump-table arms with no entry symbol -- a whole population of guaranteed
    # traps -- and had to go looking, because no metric would have shown them.
    # A number nothing publishes is a number nobody watches, so it is
    # published here and the row names its populations rather than a total.
    # G3's second interrogation then found a second such population that this
    # row still could not have shown, because the query had never been asked
    # about it: the 755 code pointers the image stores in `.rdata` that the
    # analyzer's map never named. They arrive here as the group
    # `rdata_vtable_entry_unmapped`, with no threshold on them. Nothing below
    # enumerates groups by name, so a sixth one publishes itself -- which is
    # the property that matters, since the next population will not be one
    # anybody thought to list either.
    surface = None
    for candidate in (f"dispatch_surface.{args.tag}+allarms.json",
                      f"dispatch_surface.{args.tag}+arms.json",
                      f"dispatch_surface.{args.tag}.json"):
        path = base.parent / "link" / candidate
        if path.is_file():
            found = json.loads(path.read_text())
            groups = (found.get("result") or {}).get("groups")
            if isinstance(groups, list) and groups:
                surface = {"measured_on": candidate,
                           "binary_tag": found.get("tag"),
                           "groups": {g["group"]: {"distinct": g["distinct"],
                                                   "unresolved": g["distinct_misses"]}
                                      for g in groups},
                           # Summing the groups double-counts an address that
                           # is in two of them, and 511 are: 503 map entries
                           # that are also missing-block targets, 4 that are
                           # also jump-table arms, and 4 import-address-table
                           # values that happen to equal a `.rdata` code
                           # pointer, because an IAT slot
                           # holds an RVA and an RVA near 0x86_0000 lands
                           # inside `.text`'s own address range. The query
                           # reports the deduplicated figure; use it, never a
                           # sum of the groups.
                           "distinct_over_all_groups":
                               (found.get("result") or {}).get("distinct_over_all_groups"),
                           "controls": (found.get("controls") or {})}
            break

    #
    # Two measurements of it exist and they are of two different builds.
    # `indirect.py` measures the arms-free lift; the arm seeding recovered
    # 130 399 instructions, so that figure understates the build that ships
    # by about thirteen points in the game region. METRICS.md carried it
    # anyway until the G3 report made the staleness visible. The seeded
    # measurement wins where it exists, and the published block says which
    # build it measured so the two can never be quoted for each other.
    coverage = None
    recovery_path = base.parent / "arms" / f"recovery.{args.tag}.json"
    if recovery_path.is_file():
        found = (json.loads(recovery_path.read_text()).get("recovery") or {})
        if isinstance(found.get("by_region"), dict):
            coverage = {
                "build": "whole-image modules plus arm traces (the build that ships)",
                "source": f"private/generated/s3/arms/recovery.{args.tag}.json",
                "mapped_instructions": found.get("mapped_instructions"),
                "decoded_into_a_module": found.get("guest_instructions_covered_after"),
                "percent_decoded": found.get("percent_decoded_after"),
                "by_region": {r: {"mapped_instructions": v["mapped_instructions"],
                                  "decoded_by_remill_lower_bound": v["decoded_into_a_module"],
                                  "percent_decoded": v["percent_decoded"]}
                              for r, v in found["by_region"].items()},
            }

    indirect_path = base.parent / "link" / f"indirect.{args.tag}.json"
    if coverage is None and indirect_path.is_file():
        found = json.loads(indirect_path.read_text()).get("decoded_coverage")
        if isinstance(found, dict) and isinstance(found.get("by_region"), dict):
            coverage = dict(found)
            coverage["build"] = "the arms-free lift; no arm traces are in this build"
            coverage["source"] = f"private/generated/s3/link/indirect.{args.tag}.json"

    opcodes = census.get("opcode_histogram")
    if not isinstance(opcodes, dict) or not opcodes:
        raise SystemExit(f"{census_path}: traps.opcode_histogram is absent or empty; "
                         f"design 5 asks for every trap opcode, so the census must "
                         f"classify the reaching instruction")
    if not isinstance(trap_rows, list):
        raise SystemExit(f"{census_path}: traps.functions is absent; the census must persist "
                         f"every trap row, not only its worst 25")

    emitted = [r for r in lifted if r.get("lifted")]
    traps = sum(int(r.get("sites") or 0) for r in trap_rows)
    compiled = [r for r in built if r.get("compiled")]
    failed = [{"address": r["va"], "error": (r.get("error_head") or "")[:200]}
              for r in built if not r.get("compiled")]

    out = root / "private" / "generated"
    (out / "emit-log.json").write_text(json.dumps({
        "note": f"S3 whole-image lift, arm {args.tag!r}: every function in the analyzer's map "
                f"lifted, passed, folded and compiled by tools/s3/whole_image. Cited to "
                f"{RECEIPT}. Traps are missing-block sites, the adopted pipeline's analogue of "
                f"an unsupported instruction (design 5, S3); the receipt carries their opcode "
                f"histogram and target classification.",
        "source": RECEIPT,
        "arm": args.tag,
        # Per region, keyed by the reaching instruction's mnemonic. The metric
        # lists the game region's; the others are here so nothing has to go
        # back to the census to see them.
        "missing_block_opcodes": opcodes,
        "decoded_coverage": coverage,
        "dispatch_surface": surface,
        "emitted": [{"address": r["va"], "region": r["region"]} for r in emitted],
        # The metric reads this field name and counts `count` per address,
        # classing an address into the game region by the analyzer's map.
        "missing_blocks": [{"address": r["va"], "function": r.get("name", r["va"]),
                            "count": int(r["sites"]), "targets": r.get("targets", [])}
                           for r in trap_rows],
    }, indent=1, sort_keys=True) + "\n")
    (out / "build-log.json").write_text(json.dumps({
        "note": f"S3 whole-image compile, arm {args.tag!r}; cited to {RECEIPT}.",
        "source": RECEIPT,
        "arm": args.tag,
        "compiled": [{"address": r["va"]} for r in compiled],
        "failed": failed,
    }, indent=1, sort_keys=True) + "\n")

    by_region: dict[str, int] = {}
    for r in emitted:
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
    print(f"S3_METRICS_INPUTS arm={args.tag} emitted={len(emitted)} compiled={len(compiled)} "
          f"failed={len(failed)} trap_sites={traps} by_region={by_region}")


if __name__ == "__main__":
    main()
