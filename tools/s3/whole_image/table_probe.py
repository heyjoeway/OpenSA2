#!/usr/bin/env python3
"""What the function table costs, and what its index used to throw away.

`runtime/function_table_impl.c` is open addressing with linear probing and a
load factor held under 1/2. The linked program reports the longest probe an
insertion took; this simulates the same insertions to get the whole
distribution, and -- because a simulation that only reproduces the shipping
number is worth little -- it also runs the alternatives, so "the index is bad"
is a measurement and not an opinion.

**The shipping index changed under this tool and the tool did not notice.**
S3 slice 2 measured that `hash_va(va) & mask` throws the hash away: bit k of a
product depends only on bits 0..k of the operands, so the low bits of
`va * 2654435761` carry no mixing, and masking them is arithmetically close to
indexing on `va` directly. Slice 5 replaced it with the high bits,
`hash_va(va) >> (32 - log2(capacity))`. The variant this file called
`low_bits_as_shipped` was then no longer what ships, and its validation
compared a simulated 15 against a linked 12 and printed `agrees: False` -- a
true comparison between two different tables, which is the shape of failure
this project keeps catching. The variants are named for what they are now:

  low_bits_before_the_fix   what shipped until slice 5, kept as the baseline
                            the fix is measured against
  no_hash_at_all            the control: index on `va`. If the low-bit variant
                            is not meaningfully better than this, it is not
                            hashing.
  high_bits_as_shipped      `runtime/function_table_impl.c`'s `home_slot` today

Validation: the simulated longest *insertion* probe of the shipping variant
must equal the number the linked program prints
(`private/generated/s3/link/link.<tag>.json`, `probe.result.table.max_probe`),
and the tool exits non-zero when it does not. If it disagrees, the simulation
is not simulating the runtime and its other columns mean nothing. The linked
program's worst *lookup* is reported beside it and is deliberately not compared:
an insertion stops at the first empty slot and a lookup does not, so the two
numbers answer different questions (see `runtime/function_table.h`).

`--negative-control` validates against the pre-fix variant instead, which is
what this file did before the rename, and shows the refusal firing.

Usage:
  .venv/bin/python tools/s3/whole_image/table_probe.py [--tag next]
  .venv/bin/python tools/s3/whole_image/table_probe.py --tag next --negative-control
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "link"
ROW = re.compile(r"^\s*\{0x([0-9a-f]+)u,")
GOLDEN = 2654435761


def addresses(tag: str) -> list[int]:
    path = OUT / tag / "function_table_whole.c"
    return [int(m.group(1), 16)
            for m in (ROW.match(line) for line in path.read_text().splitlines()) if m]


def simulate(rows: list[int], capacity: int, index) -> dict:
    mask = capacity - 1
    slots: list[int | None] = [None] * capacity
    depths: list[int] = []
    for va in rows:
        i = index(va, capacity) & mask
        probe = 0
        while slots[i] is not None:
            i = (i + 1) & mask
            probe += 1
        slots[i] = va
        depths.append(probe)
    depths.sort()
    n = len(depths)
    hist: dict[int, int] = {}
    for d in depths:
        hist[d] = hist.get(d, 0) + 1
    return {"rows": n, "capacity": capacity, "load_factor": n / capacity,
            "mean_extra_probes": sum(depths) / n, "max_probe": depths[-1],
            "p99_probe": depths[int(n * 0.99)],
            "histogram": {str(k): v for k, v in sorted(hist.items())}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--negative-control", action="store_true",
                    help="validate the pre-fix variant against the linked program, "
                         "which is what this tool did before the rename: it must fail")
    args = ap.parse_args()
    rows = addresses(args.tag)
    capacity = 16
    while capacity < len(rows) * 2 + 2:       # sa2_ft_init's own rule
        capacity <<= 1
    shift = capacity.bit_length() - 1

    # `high_bits_as_shipped` is `runtime/function_table_impl.c`'s `home_slot`:
    # hash_va(va) >> g_shift, with g_shift = 32 - log2(capacity).
    SHIPPING = "high_bits_as_shipped"
    variants = {
        "low_bits_before_the_fix": lambda va, _c: (va * GOLDEN) & 0xFFFFFFFF,
        "no_hash_at_all": lambda va, _c: va,
        SHIPPING: lambda va, _c: ((va * GOLDEN) & 0xFFFFFFFF) >> (32 - shift),
    }
    out = {"tag": args.tag, "capacity_rule": "sa2_ft_init: the power of two above 2n + 2",
           "shipping_variant": SHIPPING,
           "shipping_index": "runtime/function_table_impl.c home_slot(): "
                             "hash_va(va) >> (32 - log2(capacity))",
           "variants": {k: simulate(rows, capacity, f) for k, f in variants.items()},
           "double_capacity_as_shipped": simulate(rows, capacity * 2,
                                                  variants[SHIPPING])}

    validated = SHIPPING if not args.negative_control else "low_bits_before_the_fix"
    link = OUT / f"link.{args.tag}.json"
    if link.is_file():
        probe = (json.loads(link.read_text()).get("probe") or {}).get("result") or {}
        table = probe.get("table") or {}
        reported = table.get("max_probe")
        lookup = table.get("lookup") or {}
        out["validation"] = {
            "validated_variant": validated,
            "negative_control": args.negative_control,
            "linked_program_max_insertion_probe": reported,
            "simulated_max_insertion_probe": out["variants"][validated]["max_probe"],
            "agrees": reported is not None
                      and reported == out["variants"][validated]["max_probe"],
            "linked_program_max_lookup_probe": lookup.get("max_over_all"),
            "not_compared": "the linked worst lookup is not compared with a simulated "
                            "insertion: a lookup walks to the entry or to an empty slot "
                            "and an insertion stops at the first empty one",
        }
    (OUT / f"table_probe.{args.tag}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True) + "\n")
    for name, v in out["variants"].items():
        print(f"{name:24s} mean extra probes {v['mean_extra_probes']:.3f}  "
              f"max {v['max_probe']:3d}  p99 {v['p99_probe']:3d}"
              f"{'   <- ships' if name == SHIPPING else ''}")
    print(f"validation: {out.get('validation')}")
    print(f"-> {OUT / f'table_probe.{args.tag}.json'}")
    v = out.get("validation")
    if v is None:
        sys.exit(f"REFUSED: no linked-program run to validate against ({link} has no "
                 f"probe.result); the simulation is unchecked")
    if not v["agrees"]:
        sys.exit(f"REFUSED: the simulation of `{validated}` gives max insertion probe "
                 f"{v['simulated_max_insertion_probe']} and the linked program reports "
                 f"{v['linked_program_max_insertion_probe']}. Either the variant named "
                 f"as shipping is not what ships, or the simulation is wrong; its other "
                 f"columns mean nothing until this agrees.")


if __name__ == "__main__":
    main()
