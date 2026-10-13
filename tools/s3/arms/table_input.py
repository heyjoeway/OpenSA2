#!/usr/bin/env python3
"""The function-table input for the seeded arms, in the form gen_table.py eats.

Subtitle: one row per arm, produced by running the whole-image generator over
a build log that is the whole-image log plus this slice's, and refusing if the
merge loses a row or doubles a symbol.

`tools/s3/whole_image/gen_table.py` builds the table from
`build/<tag>.jsonl` -- each record's `va` and its externally defined `defines`.
`lift_arms_image.py` writes records of exactly that shape, so the arm input is
not a new format: it is more rows of the same log. This tool

  1. writes the arms-only input, `function_table_input.<tag>.jsonl`, which is
     the answer to "the list of arm addresses with their symbols";
  2. writes the merged log and **runs the real generator over it**, with
     `gen_table`'s `C.BUILD` pointed at this slice's directory, so the claim
     "gen_table consumes this" is executed rather than asserted;
  3. refuses unless every arm got exactly one row, no symbol is defined twice,
     and the merge lost nothing -- `read_jsonl` keys by `va`, so a single
     address appearing in both logs would silently drop a module, which is the
     shape of the map-entry-arm hazard one layer up.

The link is **not** run: the runtime is being changed concurrently. The exact
two commands that would run it later are printed.

`--negative-control` injects one arm record claiming a symbol the whole-image
build already defines and shows the refusal firing, because a check nobody has
seen fail is not a check.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/table_input.py --tag next [--negative-control]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import gen_table as GT  # noqa: E402

OUT = C.GEN / "s3" / "arms"
# captured before `GT.C.BUILD` is repointed below: `gen_table` imports the same
# `common` module object, so patching its `C.BUILD` patches this one's too, and
# the reproduction commands would otherwise print the patched path.
WHOLE_BUILD = C.BUILD


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--negative-control", action="store_true")
    args = ap.parse_args()

    arms_log_path = OUT / f"arms.{args.tag}.jsonl"
    whole_log_path = WHOLE_BUILD / f"{args.tag}.jsonl"
    arms = C.read_jsonl(arms_log_path)
    whole = C.read_jsonl(whole_log_path)

    arm_rows = [{"va": r["va"], "name": r["name"], "region": r["region"],
                 "owner": r["owner"], "anchor": r["anchor"],
                 "compiled": bool(r.get("compiled")), "defines": r.get("defines", []),
                 "object": str(OUT / "build" / args.tag /
                               f"ir_{int(r['va'], 16):08X}.o"),
                 "object_bytes": r.get("object_bytes", 0)}
                for r in sorted(arms.values(), key=lambda x: x["va"])]
    if args.negative_control:
        victim = sorted(whole.values(), key=lambda x: x["va"])[0]
        arm_rows.append({"va": "0xdeadbe00", "name": "negative_control",
                         "region": "game", "owner": victim["va"],
                         "anchor": [victim["va"], victim["va"]],
                         "compiled": True, "defines": list(victim["defines"]),
                         "object": "(none)", "object_bytes": 0})

    inp = OUT / f"function_table_input.{args.tag}.jsonl"
    with inp.open("w") as fh:
        for r in arm_rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    # the merged log, in the layout gen_table expects: <dir>/<tag>.jsonl
    merged_dir = OUT / "table"
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged_tag = f"{args.tag}+arms" + ("+negcontrol" if args.negative_control else "")
    merged = merged_dir / f"{merged_tag}.jsonl"
    collisions = sorted(set(whole) & {r["va"] for r in arm_rows})
    with merged.open("w") as fh:
        for r in sorted(whole.values(), key=lambda x: x["va"]):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
        for r in arm_rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    GT.C.BUILD = merged_dir                      # run the real generator, on disk
    out_c = OUT / f"function_table_whole_with_arms.{merged_tag}.c"
    rows, census = GT.rows(merged_tag)
    GT.emit(rows, out_c, merged_tag)

    compiled_arms = sum(1 for r in arm_rows if r["compiled"])
    expected = len(whole) + compiled_arms
    arm_addrs = {r["va"] for r in arm_rows if r["compiled"]}
    row_addrs = {f"0x{va:08x}" for va, _s, _n, _r in rows}
    fails = []
    if collisions:
        fails.append(f"{len(collisions)} addresses appear in both logs, so the merge "
                     f"drops a module: {collisions[:5]}")
    if census["duplicate_symbols"]:
        fails.append(f"{len(census['duplicate_symbols'])} symbols defined by two "
                     f"modules: {census['duplicate_symbols'][:3]}")
    if len(rows) != expected:
        fails.append(f"{len(rows)} rows, expected {expected} "
                     f"({len(whole)} whole-image + {compiled_arms} arms)")
    missing = sorted(arm_addrs - row_addrs)
    if missing:
        fails.append(f"{len(missing)} compiled arms have no row: {missing[:5]}")

    doc = {
        "tag": args.tag, "merged_tag": merged_tag,
        "negative_control": args.negative_control,
        "arms_only_input": str(inp),
        "merged_build_log": str(merged),
        "generated_table": str(out_c),
        "generated_table_bytes": out_c.stat().st_size,
        "whole_image_modules": len(whole),
        "arm_modules_compiled": compiled_arms,
        "rows": len(rows),
        "rows_expected": expected,
        "duplicate_symbols": census["duplicate_symbols"][:10],
        "non_entry_defines_skipped": census["non_entry_defines_skipped"],
        "rows_by_region": census["by_region"],
        "addresses_in_both_logs": collisions,
        "assertion": {"failures": fails, "ok": not fails},
        "the_link_is_not_run_here": {
            "why": "the runtime and harness are being changed concurrently; this slice "
                   "produces objects and a table input only",
            # `link_all.py --tag T` globs `build/T/ir_*.o` and `gen_table.py --tag T`
            # reads `build/T.jsonl`, so the merged arm has to exist as a tag: one
            # object directory of hard links (no bytes copied) and one log.
            "commands_when_it_is": [
                f"cp {merged} {WHOLE_BUILD / f'{merged_tag}.jsonl'}",
                f"mkdir -p {WHOLE_BUILD / merged_tag}",
                f"cp -al {WHOLE_BUILD / args.tag}/ir_*.o {WHOLE_BUILD / merged_tag}/",
                f"cp -al {OUT / 'build' / args.tag}/ir_*.o {WHOLE_BUILD / merged_tag}/",
                f".venv/bin/python tools/s3/whole_image/gen_table.py --tag {merged_tag} "
                f"--out private/generated/s3/link/{merged_tag}/function_table_whole.c",
                f".venv/bin/python tools/s3/whole_image/link_all.py --tag {merged_tag}",
            ],
        },
    }
    (OUT / f"table_input.{merged_tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    if fails:
        for f in fails:
            print(f"  FAIL {f}", file=sys.stderr)
        sys.exit(f"REFUSED: the table input is not linkable ({len(fails)} failures)")


if __name__ == "__main__":
    main()
