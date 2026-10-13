#!/usr/bin/env python3
"""Would the seeded objects link? Asked of the objects, not of the IR.

Subtitle: `nm -g --defined-only` over every whole-image object and every arm
object, refusing if one global symbol is defined twice.

`link_surface.py` answers this from the LLVM IR, and `table_input.py` answers
it from the build logs. Both are upstream of the compiler, and the failure this
slice exists to avoid -- two modules defining `sub_<arm>` -- is a property of
the objects the linker is handed. So it is asked of them. This is not the
whole-image link: no runtime is involved, nothing is executed, and the runtime
and harness are being edited concurrently by another unit.

`--include-dup-probe` adds the object `dup_probe.py` built for a map-entry arm,
which really does duplicate `sub_6d09b0`, and the refusal fires. A check that
has not been seen to fail is not a check.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/symbol_check.py --tag next [--include-dup-probe]
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "arms"
# nm's type letters for a *global definition* that a second definition would
# collide with. `W`/`V` are weak and `U` is undefined, so neither collides.
STRONG = set("TDBRGS")


def defined_globals(paths: list[Path], batch: int = 400,
                    undefined: bool = False) -> dict[str, list[str]]:
    out: dict[str, list[str]] = collections.defaultdict(list)
    for i in range(0, len(paths), batch):
        chunk = paths[i:i + batch]
        flag = "--undefined-only" if undefined else "--defined-only"
        p = subprocess.run(["nm", "-g", flag, "-A", *[str(x) for x in chunk]],
                           capture_output=True, text=True)
        for line in p.stdout.splitlines():
            # binutils nm -A prints "<file>:<addr> <type> <name>" with no space
            # after the colon, and the paths here contain no colon of their own.
            fields = line.split()
            if len(fields) < 3 or ":" not in fields[0]:
                continue
            path = fields[0].rsplit(":", 1)[0]
            typ, name = fields[-2], fields[-1]
            if undefined:
                if typ == "U":
                    out[name].append(Path(path).name)
            elif typ in STRONG:
                out[name].append(Path(path).name)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--include-dup-probe", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    whole = sorted((C.BUILD / args.tag).glob("ir_*.o"))
    arms = sorted((OUT / "build" / args.tag).glob("ir_*.o"))
    extra = []
    if args.include_dup_probe:
        extra = sorted((OUT / "dup_probe" / "build").glob("ir_*.o"))
    syms = defined_globals(whole + arms + extra)
    dups = {k: v for k, v in syms.items() if len(v) > 1}
    # The other way an object set fails to link: a lifted `call sub_X` whose
    # target nothing defines. `link_surface.py` measured this at zero for the
    # whole image; the arm traces decode code nothing had decoded before, so
    # their call targets are new and are asked about rather than assumed.
    undef = defined_globals(whole + arms + extra, undefined=True)
    dangling = {k: v for k, v in undef.items()
                if k.startswith("sub_") and k not in syms}

    doc = {
        "tag": args.tag,
        "include_dup_probe": args.include_dup_probe,
        "whole_image_objects": len(whole),
        "arm_objects": len(arms),
        "extra_objects": [p.name for p in extra],
        "objects_total": len(whole) + len(arms) + len(extra),
        "distinct_global_definitions": len(syms),
        "symbols_defined_more_than_once": len(dups),
        "examples": [{"symbol": k, "objects": v[:4]} for k, v in
                     sorted(dups.items())[:10]],
        "sub_symbols": sum(1 for k in syms if k.startswith("sub_")),
        "sub_symbols_called_but_never_defined": len(dangling),
        "sub_symbols_called_but_never_defined_first": [
            {"symbol": k, "called_from": v[:3]} for k, v in sorted(dangling.items())[:10]],
        "distinct_undefined_symbols": len(undef),
        "method": "nm -g --defined-only over every object; a symbol whose nm type is "
                  f"one of {sorted(STRONG)} in two objects is a link failure",
        "seconds": round(time.time() - t0, 2),
    }
    (OUT / f"symbol_check.{args.tag}"
     f"{'.dupprobe' if args.include_dup_probe else ''}.json").write_text(
        json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1))
    if dups or dangling:
        sys.exit(f"REFUSED: {len(dups)} symbols defined by more than one object, "
                 f"{len(dangling)} sub_ symbols called but never defined")


if __name__ == "__main__":
    main()
