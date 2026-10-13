#!/usr/bin/env python3
"""What the linked image still cannot dispatch to, measured against the binary.

Subtitle: every dispatch target the analyzer can name, asked of the linked
program's own function table, grouped by where the address came from.

S3 has counted dispatch *sites* -- 917 `__remill_jump` in the whole-image IR,
1 170 more in the arm traces, 2 087 once the arms link -- and called that the
trap surface. It is not: a site is a place that consults the table, and only a
*target* can be resolved or not. Two thousand sites can share a few hundred
targets. Nobody had asked a binary.

So this asks one. `harness/table_query.c` looks every address up in the real
table of a real linked program; this tool assembles the address list from
sources that are not the function table:

  map_entry              every function the analyzer's map lists. The positive
                         control: the generated table holds one row per lifted
                         define, so a miss here means the query is broken, not
                         that the image is.
  jump_table_arm         every target of every resolved jump table (5 817
                         occurrences over 5 715 addresses)
  import_iat_value       the dword each import-address-table slot holds *as
                         loaded from the file*. Nothing binds the IAT at S3, so
                         a `call dword ptr [slot]` dispatches on the file's own
                         name-table pointer. These are the addresses the guest
                         actually reaches an import through, not the names.
  missing_block_target   every target of a control-flow edge that leaves its
                         module's lifted byte range, from the trap census
  rdata_vtable_entry_unmapped
                         code pointers the image *stores*, that the map never
                         named. See `rdata_code_pointers` for the four criteria
                         and what each one removes. Reported, never asserted.
  far_outside_the_image  addresses in no section at all. The negative control,
                         and a weak one on its own -- with no deletions, linear
                         probing cannot produce a false hit -- so it is reported
                         as a self-check on the query and not as a finding.

What it cannot measure is named rather than left out: an indirect call or jump
whose target the analyzer could not resolve has no address to look up, so its
*sites* are counted and its targets are unknown until a run produces one.

The fifth group has no `--max` flag and never will until somebody reviews its
boundary. The arms are a closed population -- the analyzer either resolved a
jump table's target or it did not -- so a threshold over them freezes nothing.
`rdata_vtable_entry_unmapped` is a *discovery*, and its edge is a judgement
call: the four criteria below are one defensible cut through a continuum of
dwords that happen to hold values in `.text`, and 37 of the 755 they admit
decode as `int3` and are almost certainly coincidence. A threshold would turn
that judgement into a gate before anyone had argued with it, and the first
person who wanted the number to move would move the criteria instead. So it is
reported: the row carries it, `METRICS.md` names it, nothing refuses on it.

`--arms-unresolved-max N` turns the arm row into an assertion. It is optional
because the arms-free and partly-seeded builds are legitimate measurements with
non-zero rows; it is passed with 0 for a build that claims to have closed the
lifter-side surface, and it refuses on `--tag next+arms` (343) and on
`--tag next` (5 711), which is how it is seen to be able to fail.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/whole_image/dispatch_surface.py --tag next+allarms \
      --arms-unresolved-max 0
  .venv/bin/python tools/s3/whole_image/dispatch_surface.py --tag next        # before
  .venv/bin/python tools/s3/whole_image/dispatch_surface.py --tag next+allarms \
      --sieve                                  # the .rdata criteria, with and without
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "link"
FAR_OUTSIDE = [0xDEAD0000, 0xDEAD0004, 0x00000FFC, 0x03FFFFF0, 0x0BADF00D]


def iat_values() -> list[tuple[int, int]]:
    """(slot, value) for every import-address-table slot, as the file holds it.

    Read out of the memory-mapped image rather than out of pefile's parsed
    import descriptors, because what dispatch sees is the dword in the slot and
    nothing else."""
    import pefile
    pe = pefile.PE(str(C.EXE), fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    img = pe.get_memory_mapped_image()
    out = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        for imp in entry.imports:
            slot = imp.address                    # already a VA
            off = slot - C.IMAGE_BASE
            if 0 <= off <= len(img) - 4:
                out.append((slot, struct.unpack_from("<I", img, off)[0]))
    return out


def rdata_code_pointers(require_int3_pad: bool = True) -> tuple[list[int], dict]:
    """The code pointers the image stores in `.rdata` that the map never named.

    G3's second interrogation found that this tool assembled its address list
    from five sources and that not one of them was *the function pointers the
    image itself holds*. A vtable slot is a dispatch target in the most literal
    sense available: the program loads that dword and calls through it. If the
    analyzer never named the function it points at, the table cannot resolve it
    and the call traps, in the build that ships, today.

    Four criteria, in the order they are applied, each stated so the next
    reader can argue with it rather than guess at it:

    1. **An aligned dword in `.rdata`.** Every 4-aligned dword in the section,
       read from the memory-mapped image. `.rdata` and not `.data`, because
       `.data` is mutable bulk -- floats, structures, save blocks -- and a
       32-bit value there that happens to fall inside `.text`'s four-and-a-half
       megabytes is usually arithmetic, not a pointer. `.rdata` is where MSVC
       puts vtables, and it is read-only, so a pointer there is a pointer the
       linker wrote.
    2. **The value lies inside `.text`.** `[section start, start + min(virtual
       size, raw size))`. The section is chosen by name, and that is a choice
       rather than a fact: the unpacked executable has a *second* code-flagged
       section, `PSFD00`, which holds 17 of the map's own functions, so "the
       code is `.text`" is not true of this binary. Admitting `PSFD00` would
       add exactly one address, `0x00872bd0`, from the slot at `0x0087e418`.
       It is named here rather than argued about: one address either way, and
       keeping to `.text` is what makes this count the same population G3
       measured.
    3. **The value is not a function the map names.** This is what makes the
       group a *finding* and not a restatement of `map_entry`: 512 of the
       values that survive (2) are mapped functions, which is the criteria
       recognising something real. Those are dropped, since `map_entry`
       already carries them and the question is what is missing.
    4. **The byte before the value is `int3` (0xCC).** MSVC pads between
       functions with `int3`, so a real function entry that follows another
       function is preceded by one. This is the criterion that separates a
       pointer from a coincidence, and it does nearly all the work: without it
       2 970 addresses survive, with it 755.

    None of the four is free of judgement, and (4) least of all -- it admits
    37 addresses whose first instruction is itself `int3`, which are pointers
    into padding rather than into code, and it would reject a genuine entry
    that begins a section or follows data. That is exactly why the group is
    reported and not asserted. The returned meta carries the count after each
    criterion so a reader can see what each one removed without rerunning
    anything, and `require_int3_pad=False` reproduces the 2 970 for comparison.

    What the group is *not*: an assertion that all 755 are reached at run time.
    Some vtable slots belong to classes the game never instantiates. It is the
    set of addresses the image stores as code pointers and the table cannot
    answer for, which is a count of guaranteed misses *if* dispatched, not a
    prediction of dispatches.
    """
    import pefile
    pe = pefile.PE(str(C.EXE), fast_load=True)
    img = pe.get_memory_mapped_image()
    sec = {}
    for s in pe.sections:
        name = s.Name.rstrip(b"\x00").decode("ascii", "replace")
        lo = C.IMAGE_BASE + s.VirtualAddress
        sec[name] = (lo, lo + min(s.Misc_VirtualSize, s.SizeOfRawData))
    if ".rdata" not in sec or ".text" not in sec:
        raise SystemExit(f"{C.EXE}: expected .text and .rdata, found {sorted(sec)}")
    rd_lo, rd_hi = sec[".rdata"]
    tx_lo, tx_hi = sec[".text"]
    mapped = set(C.by_address())

    slots = 0
    into_text: set[int] = set()
    occurrences = 0
    for va in range(rd_lo, rd_hi - 3, 4):
        slots += 1
        v = struct.unpack_from("<I", img, va - C.IMAGE_BASE)[0]
        if tx_lo <= v < tx_hi:
            into_text.add(v)
    unmapped = {v for v in into_text if v not in mapped}
    if require_int3_pad:
        kept = {v for v in unmapped if img[v - C.IMAGE_BASE - 1] == 0xCC}
    else:
        kept = set(unmapped)
    for va in range(rd_lo, rd_hi - 3, 4):
        if struct.unpack_from("<I", img, va - C.IMAGE_BASE)[0] in kept:
            occurrences += 1

    meta = {
        "criteria": [
            "an aligned dword in .rdata",
            "whose value lies inside .text",
            "and is not a function the analyzer's map names",
            "and is preceded by an int3 pad byte"
            + ("" if require_int3_pad else " [DISABLED for this run]"),
        ],
        "rdata_range": [f"{rd_lo:#010x}", f"{rd_hi:#010x}"],
        "text_range": [f"{tx_lo:#010x}", f"{tx_hi:#010x}"],
        "sieve": {
            "1_aligned_dwords_in_rdata": slots,
            "2_distinct_values_inside_text": len(into_text),
            "3_and_not_a_map_entry": len(unmapped),
            "4_and_int3_padded": len(kept),
        },
        "int3_pad_criterion_applied": require_int3_pad,
        "rdata_slots_holding_one": occurrences,
    }
    return sorted(kept), meta


def build(tag: str) -> tuple[list[tuple[int, str]], dict]:
    fmap = C.by_address()
    rows: list[tuple[int, str]] = [(va, "map_entry") for va in sorted(fmap)]

    arms = 0
    for va in sorted(fmap):
        for jt in fmap[va].get("jump_tables") or []:
            for t in jt.get("targets", []):
                rows.append((int(t, 16), "jump_table_arm"))
                arms += 1

    iat = iat_values()
    rows += [(v, "import_iat_value") for _s, v in iat]

    census = json.loads((C.OUT / f"census.{tag.split('+')[0]}.json").read_text())
    mb = set()
    for fn in census["traps"]["functions"]:
        for t in fn["targets"]:
            mb.add(int(t, 16))
    rows += [(t, "missing_block_target") for t in sorted(mb)]

    rdata, rdata_meta = rdata_code_pointers()
    rows += [(v, "rdata_vtable_entry_unmapped") for v in rdata]

    rows += [(a, "far_outside_the_image") for a in FAR_OUTSIDE]

    indirect = json.loads((OUT / f"indirect.{tag.split('+')[0]}.json").read_text())
    surface = indirect["map_surface_total"]
    meta = {
        "map_entries": len(fmap),
        "jump_table_arm_occurrences": arms,
        "import_slots": len(iat),
        "missing_block_targets": len(mb),
        "rdata_code_pointers": rdata_meta,
        "not_lookupable": {
            "indirect_call_sites": surface["indirect_call_sites"],
            "unresolved_indirect_jump_sites": surface["unresolved_indirect_jumps"],
            "remill_jump_sites_whole_image": indirect["lifted_ir"]["total_remill_jump_calls"],
            "why": "the analyzer resolved no target for these, so there is no address "
                   "to look up; the target only exists once a run computes it",
        },
    }
    return rows, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--arms-unresolved-max", type=int, default=None,
                    help="refuse if more than this many jump-table arm addresses do "
                         "not resolve. No default: an arms-free build legitimately "
                         "has 5 711 and is still worth measuring.")
    ap.add_argument("--binary", type=Path, default=None,
                    help="default: the table_query main linked for this tag")
    ap.add_argument("--sieve", action="store_true",
                    help="derive rdata_vtable_entry_unmapped twice, with the int3-pad "
                         "criterion and without it, print both sieves and exit without "
                         "querying a binary. This is how the criteria are shown to be "
                         "load-bearing: drop the last one and the count is a different "
                         "number, so the 755 is a consequence of a stated rule and not "
                         "a figure somebody chose.")
    args = ap.parse_args()

    if args.sieve:
        for pad in (True, False):
            addrs, m = rdata_code_pointers(require_int3_pad=pad)
            print(f"int3-pad criterion {'applied' if pad else 'DROPPED'}: "
                  f"{len(addrs)} addresses")
            for k, v in m["sieve"].items():
                print(f"    {k:34s} {v}")
        return

    rows, meta = build(args.tag)
    inp = OUT / f"dispatch_surface.{args.tag}.txt"
    inp.write_text("# <hex address> <group>; see tools/s3/whole_image/dispatch_surface.py\n"
                   + "".join(f"{va:08x} {g}\n" for va, g in rows))

    binary = args.binary or (OUT / "mains" / f"tablequery.{args.tag}" / "sa2_main")
    if not binary.is_file():
        sys.exit(f"no binary {binary}; link one first:\n"
                 f"  .venv/bin/python tools/s3/whole_image/link_main.py --tag {args.tag} "
                 f"--main harness/table_query.c --name tablequery.{args.tag}")
    p = subprocess.run([str(binary), str(C.EXE), str(inp)],
                       capture_output=True, text=True, timeout=1800)
    try:
        result = json.loads(p.stdout)
    except json.JSONDecodeError:
        sys.exit(f"table_query produced no JSON (rc={p.returncode}):\n{p.stdout[-2000:]}\n"
                 f"{p.stderr[-2000:]}")

    by = {g["group"]: g for g in result["groups"]}
    fails = []
    ctl = by.get("map_entry", {})
    if ctl.get("distinct_misses"):
        fails.append(f"{ctl['distinct_misses']} map entries do not resolve; the positive "
                     f"control failed, so nothing else here means anything")
    neg = by.get("far_outside_the_image", {})
    if neg.get("distinct_hits"):
        fails.append(f"{neg['distinct_hits']} addresses outside the image resolve")
    arms = by.get("jump_table_arm", {})
    if args.arms_unresolved_max is not None and \
            arms.get("distinct_misses", 0) > args.arms_unresolved_max:
        fails.append(f"{arms['distinct_misses']} jump-table arms do not resolve, over "
                     f"the {args.arms_unresolved_max} this run asserts. Every one is an "
                     f"indirect jump that traps SA2_TRAP_CONTROL_LEFT: "
                     f"{arms.get('miss_sample', [])[:5]}")

    doc = {
        "tag": args.tag, "binary": str(binary), "input": str(inp),
        "inputs": meta, "result": result,
        "unresolved_dispatch_targets": result["distinct_over_all_groups"]["unresolved"],
        "arms_unresolved_max_asserted": args.arms_unresolved_max,
        "rdata_vtable_entry_unmapped_asserted": False,
        "rdata_vtable_entry_unmapped_why_not_asserted":
            "a discovery whose boundary is a judgement call; a threshold would freeze "
            "the four criteria before anyone has reviewed them, and the next person who "
            "wanted the number to move would move the criteria. Reported, watched, and "
            "left arguable. See rdata_code_pointers' docstring and --sieve.",
        "controls": {
            "positive_map_entry_distinct_misses": ctl.get("distinct_misses"),
            "negative_far_outside_distinct_hits": neg.get("distinct_hits"),
            "note": "the negative control is weak on its own -- a table with no deletions "
                    "and linear probing cannot produce a false hit -- and is here as a "
                    "self-check on the query, not as a finding",
        },
        "assertion": {"failures": fails, "ok": not fails},
    }
    (OUT / f"dispatch_surface.{args.tag}.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "result"}, indent=1))
    for g in result["groups"]:
        print(f"  {g['group']:24s} distinct {g['distinct']:6d}  resolved "
              f"{g['distinct_hits']:6d}  unresolved {g['distinct_misses']:6d}")
    print(f"  {'ALL (distinct addresses)':24s} distinct "
          f"{result['distinct_over_all_groups']['addresses']:6d}  resolved "
          f"{result['distinct_over_all_groups']['resolved']:6d}  unresolved "
          f"{result['distinct_over_all_groups']['unresolved']:6d}")
    print(f"-> {OUT / f'dispatch_surface.{args.tag}.json'}")
    if fails:
        for f in fails:
            print(f"  FAIL {f}", file=sys.stderr)
        sys.exit("REFUSED: a control failed")


if __name__ == "__main__":
    main()
