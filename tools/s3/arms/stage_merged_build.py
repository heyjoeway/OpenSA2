#!/usr/bin/env python3
"""Stage the arms-seeded build as a tag the whole-image link tools already take.

Subtitle: one hard-linked object directory and one merged build log, so
`tools/s3/whole_image/link_all.py --tag next+arms` needs no new argument and no
copy of 386 MB of objects.

`tools/s3/arms/table_input.py` ends by *printing* four shell commands that do
this. This is the third time they would have been typed by hand (slice 4's
journal, slice 6's link, and any re-run after a re-lift), which is AGENTS.md
rule 13's threshold, so they are a tool:

  * `link_all.py --tag T` globs `<build>/T/ir_*.o` and `gen_table.py --tag T`
    reads `<build>/T.jsonl`, so the merged arm has to exist as a tag;
  * the objects are **hard links**, not copies: no lifted bytes are duplicated
    and no lifted bytes leave `private/generated` (AGENTS.md rule 9);
  * the merge refuses rather than guessing if an address is in both logs, which
    would silently drop a module -- the map-entry-arm hazard slice 4 found --
    or if any object named by a log record is missing from the staged
    directory, which is how a stale object gets linked.

`--negative-control` stages one arm record whose object does not exist and
shows the refusal firing, because a check nobody has seen fail is not a check.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/stage_merged_build.py --tag next [--force]
  .venv/bin/python tools/s3/arms/stage_merged_build.py --tag next --negative-control
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402

ARMS = C.GEN / "s3" / "arms"


def link_objects(src: Path, dst: Path, names: list[str], force: bool) -> tuple[int, int]:
    made = skipped = 0
    for name in names:
        target = dst / name
        if target.exists():
            if not force:
                skipped += 1
                continue
            target.unlink()
        os.link(src / name, target)
        made += 1
    return made, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--merged-tag", default=None)
    ap.add_argument("--force", action="store_true",
                    help="replace hard links that already exist (use after a re-lift)")
    ap.add_argument("--negative-control", action="store_true")
    args = ap.parse_args()
    merged_tag = args.merged_tag or f"{args.tag}+arms"

    whole_log = C.BUILD / f"{args.tag}.jsonl"
    arms_log = ARMS / f"arms.{args.tag}.jsonl"
    whole = C.read_jsonl(whole_log)
    arms = C.read_jsonl(arms_log)

    both = sorted(set(whole) & set(arms))
    fails: list[str] = []
    if both:
        fails.append(f"{len(both)} addresses appear in both build logs, so the merge "
                     f"would drop a module: {both[:5]}")

    whole_dir = C.BUILD / args.tag
    arms_dir = ARMS / "build" / args.tag
    out_dir = C.BUILD / merged_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    whole_objs = sorted(f"ir_{int(r['va'], 16):08X}.o"
                        for r in whole.values() if r.get("compiled"))
    arm_objs = sorted(f"ir_{int(r['va'], 16):08X}.o"
                      for r in arms.values() if r.get("compiled"))
    if args.negative_control:
        arm_objs.append("ir_DEADBE00.o")

    missing = [n for n in whole_objs if not (whole_dir / n).is_file()]
    missing += [n for n in arm_objs if not (arms_dir / n).is_file()]
    if missing:
        fails.append(f"{len(missing)} objects named by a build log are not on disk: "
                     f"{missing[:5]}")

    doc = {
        "tag": args.tag, "merged_tag": merged_tag,
        "negative_control": args.negative_control,
        "whole_image_modules": len(whole), "arm_modules": len(arms),
        "whole_image_objects": len(whole_objs), "arm_objects": len(arm_objs),
        "objects_expected": len(whole_objs) + len(arm_objs),
        "addresses_in_both_logs": both,
        "objects_named_but_absent": missing[:20],
        "object_dir": str(out_dir), "merged_log": str(C.BUILD / f"{merged_tag}.jsonl"),
    }
    if fails:
        doc["assertion"] = {"failures": fails, "ok": False}
        print(json.dumps(doc, indent=1))
        for f in fails:
            print(f"  FAIL {f}", file=sys.stderr)
        sys.exit(f"REFUSED: the merged build is not stageable ({len(fails)} failures)")

    made_w, skip_w = link_objects(whole_dir, out_dir, whole_objs, args.force)
    made_a, skip_a = link_objects(arms_dir, out_dir, arm_objs, args.force)

    merged_log = C.BUILD / f"{merged_tag}.jsonl"
    with merged_log.open("w") as fh:
        for rec in sorted(list(whole.values()) + list(arms.values()),
                          key=lambda r: r["va"]):
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    staged = sorted(p.name for p in out_dir.glob("ir_*.o"))
    doc.update({
        "hard_links_made": made_w + made_a, "hard_links_already_present": skip_w + skip_a,
        "objects_staged": len(staged),
        "merged_log_records": len(whole) + len(arms),
        "stray_objects_in_the_staged_directory": sorted(
            set(staged) - set(whole_objs) - set(arm_objs))[:20],
        "next": [
            f".venv/bin/python tools/s3/whole_image/link_all.py --tag {merged_tag} "
            f"--arms-expect seeded --arms-lift-tag {args.tag}",
        ],
    })
    extra = set(staged) - set(whole_objs) - set(arm_objs)
    doc["assertion"] = {
        "failures": ([f"{len(staged)} objects staged, expected "
                      f"{len(whole_objs) + len(arm_objs)}"] if len(staged) !=
                     len(whole_objs) + len(arm_objs) else []) +
                    ([f"{len(extra)} objects in the directory belong to no log record"]
                     if extra else []),
    }
    doc["assertion"]["ok"] = not doc["assertion"]["failures"]
    (ARMS / f"stage.{merged_tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    if not doc["assertion"]["ok"]:
        sys.exit("REFUSED: the staged directory does not match the merged log")


if __name__ == "__main__":
    main()
