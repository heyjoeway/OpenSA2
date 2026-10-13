#!/usr/bin/env python3
"""S1 receipt 4: instruction histogram over `.text` (the emitter's subset).

Decodes the `.text` section of the unpacked executable linearly with
iced-x86 in 32-bit mode and writes `Documentation/receipts/
s1-opcode-histogram-v1.json` (schema sa2-recomp-receipt/v1). Design
sections 3.4 (the emitter's required instruction subset) and 5/S1 (the kill
criterion on a large SIMD or dynamic-code surface) read this receipt.

What is counted
---------------

- Padding is skipped before decoding: any run of 0xCC bytes (`int3`; the
  compiler never emits one inside a function) and any run of two or more
  0x90 bytes (`nop` alignment between functions). A single `nop` inside a
  function is decoded and counted as an instruction.
- A decode error is recorded by address; decoding resumes one byte later.
  Every instruction decoded from an error up to the next 16-byte-aligned
  address is *suspect* (data-in-code such as a jump table decodes as
  garbage until the sweep re-synchronises) and is histogrammed separately.
  Suspect instructions are excluded from the clean totals and from the
  coverage numbers.
- The clean stream is histogrammed by mnemonic, by iced `Code` (the exact
  operand form, which distinguishes `fistp m32int` from `fistp m64int`), by
  encoding kind, by CPUID feature set, by flow control and by operand-kind
  signature. Derived views: x87 (CPUID feature FPU/FPU287/FPU387, plus
  `wait`), SIMD (MMX/SSE*/3DNow!/AVX*), non-legacy encodings, `rep`-prefixed
  string ops, segment overrides, `cpuid`/`rdtsc`, and the number of
  distinct mnemonics that cover 99 %, 99.9 % and 100 % of instructions.
- With `--function-map` (default `private/generated/function-map.json`,
  used when it exists) every clean instruction is attributed to the region
  of the function containing it (`unmapped` when none does) and the same
  views are reported per region. Without the map the report carries a
  single region named `unpartitioned`.

Limits
------

A linear sweep has no knowledge of function boundaries or jump tables; a
jump table that happens to decode as valid instructions is counted as
instructions (the suspect mechanism catches only the tables that produce
a decode error). The second executable section `PSFD00` (Steam/CEG stub
area) is not swept; the design's `.text` figure is the section swept here.
64-bit-only encodings cannot be produced by a 32-bit decoder, so that
check is "none by construction" and the encoding-kind histogram is the
evidence for the AVX question.

Usage
-----

    .venv/bin/python Documentation/scripts/s1_opcode_histogram.py \
        [--exe private/exe/sonic2app-unpacked.exe] \
        [--function-map private/generated/function-map.json] \
        [--out Documentation/receipts/s1-opcode-histogram-v1.json] \
        [--date YYYY-MM-DD]

Prints one summary line per region prefixed `S1_OPCODES`, which the
journal quotes verbatim. Output is deterministic for a given input set.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib.metadata
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import iced_x86 as ix
import pefile

SCHEMA = "sa2-recomp-receipt/v1"
RECEIPT_ID = "s1-opcode-histogram-v1"
DEFAULT_EXE = "private/exe/sonic2app-unpacked.exe"
DEFAULT_MAP = "private/generated/function-map.json"
DEFAULT_OUT = "Documentation/receipts/s1-opcode-histogram-v1.json"
PINS = "tools/pins.json"
SECTION = ".text"
RESYNC_ALIGNMENT = 16
ERROR_ADDRESS_LIMIT = 1000
UNPARTITIONED = "unpartitioned"
UNMAPPED = "unmapped"

COVERAGE_THRESHOLDS = (99.0, 99.9, 100.0)

# Design section 2.3, "Float model", measured on the predecessor tree.
DESIGN_2_3_TRANSCENDENTALS = {"fpatan": 5, "fptan": 3, "fsin": 2, "fcos": 2,
                              "fyl2x": 6, "f2xm1": 2}
DESIGN_2_3_FISTP = {"fistps": 4, "fistpl": 423, "fistpll": 272, "total": 699}
FISTP_CODES = {"FISTP_M16INT": "fistps", "FISTP_M32INT": "fistpl",
               "FISTP_M64INT": "fistpll"}

_NAMES = {
    "mnemonic": {v: k.lower() for k, v in vars(ix.Mnemonic).items() if isinstance(v, int)},
    "code": {v: k for k, v in vars(ix.Code).items() if isinstance(v, int)},
    "encoding": {v: k for k, v in vars(ix.EncodingKind).items() if isinstance(v, int)},
    "cpuid": {v: k for k, v in vars(ix.CpuidFeature).items() if isinstance(v, int)},
    "flow": {v: k for k, v in vars(ix.FlowControl).items() if isinstance(v, int)},
    "opkind": {v: k for k, v in vars(ix.OpKind).items() if isinstance(v, int)},
    "register": {v: k for k, v in vars(ix.Register).items() if isinstance(v, int)},
}
X87_FEATURES = frozenset({"FPU", "FPU287", "FPU387", "FPU287XL_ONLY",
                          "FPU387SL_ONLY", "CYRIX_FPU"})
X87_EXTRA_MNEMONICS = frozenset({"wait", "fwait"})
SIMD_FEATURE_PREFIXES = ("MMX", "SSE", "SSSE", "AVX", "FMA", "F16C", "D3NOW", "3DNOW")
TIMESTAMP_MNEMONICS = ("cpuid", "rdtsc", "rdtscp", "rdpmc")
STRING_MNEMONICS = frozenset({"movsb", "movsw", "movsd", "movsq", "stosb", "stosw", "stosd", "stosq",
                              "lodsb", "lodsw", "lodsd", "lodsq", "cmpsb", "cmpsw", "cmpsd", "cmpsq",
                              "scasb", "scasw", "scasd", "scasq", "insb", "insw", "insd",
                              "outsb", "outsw", "outsd"})


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)


def coverage_subset_sizes(histogram: Counter, thresholds=COVERAGE_THRESHOLDS) -> dict[str, int]:
    """How many of the most frequent mnemonics cover each percentage.

    Mnemonics are taken in descending count order (ties broken by name).
    The value for a threshold is the smallest prefix length whose cumulative
    share is at least the threshold; 100 % is the number of distinct
    mnemonics with a non-zero count. An empty histogram gives 0 everywhere.
    """
    total = sum(histogram.values())
    ordered = sorted(histogram.items(), key=lambda item: (-item[1], item[0]))
    result: dict[str, int] = {}
    for threshold in thresholds:
        key = f"{threshold:g}"
        if total == 0:
            result[key] = 0
            continue
        needed = total * threshold / 100.0
        running = 0
        size = 0
        for _, count in ordered:
            if count <= 0:
                break
            running += count
            size += 1
            if running >= needed:
                break
        result[key] = size
    return result


def padding_run(data: bytes, pos: int) -> int:
    """Length of the padding run at `pos`, or 0 when `pos` starts code."""
    byte = data[pos]
    if byte == 0xCC:
        end = pos
        while end < len(data) and data[end] == 0xCC:
            end += 1
        return end - pos
    if byte == 0x90 and pos + 1 < len(data) and data[pos + 1] == 0x90:
        end = pos
        while end < len(data) and data[end] == 0x90:
            end += 1
        return end - pos
    return 0


def next_alignment(address: int, alignment: int = RESYNC_ALIGNMENT) -> int:
    return (address // alignment + 1) * alignment


def decode_linear(data: bytes, base: int):
    """Yield (instruction, suspect) over `data`; padding skipped, errors resynced.

    Also yields ("error", address) tuples for each decode error and
    ("padding", start, length) for each skipped run, so callers can count
    them without a second pass.
    """
    decoder = ix.Decoder(32, data, ip=base)
    pos = 0
    size = len(data)
    suspect_until = base
    while pos < size:
        run = padding_run(data, pos)
        if run:
            yield ("padding", base + pos, run)
            pos += run
            continue
        decoder.position = pos
        decoder.ip = base + pos
        ins = decoder.decode()
        address = base + pos
        if ins.is_invalid:
            yield ("error", address)
            suspect_until = next_alignment(address)
            pos += 1
            continue
        yield (ins, address < suspect_until)
        pos += ins.len


# --------------------------------------------------------------------------
# Function map


class RegionMap:
    def __init__(self, functions: list[dict]) -> None:
        entries = sorted((int(str(f["address"]), 0), int(f["size"]), str(f["region"]))
                         for f in functions)
        self.starts = [e[0] for e in entries]
        self.entries = entries
        self.regions = sorted({e[2] for e in entries})

    def region_of(self, address: int) -> str:
        index = bisect.bisect_right(self.starts, address) - 1
        if index < 0:
            return UNMAPPED
        start, size, region = self.entries[index]
        return region if start <= address < start + size else UNMAPPED


def load_function_map(path: Path) -> RegionMap:
    doc = json.loads(path.read_text(encoding="utf-8"))
    functions = doc["functions"]
    for f in functions:
        for key in ("address", "size", "region"):
            if key not in f:
                raise SystemExit(f"{path}: function entry lacks {key!r}")
    return RegionMap(functions)


# --------------------------------------------------------------------------
# Accumulation


class Tally:
    """Every view over one stream of instructions (whole image or one region)."""

    def __init__(self) -> None:
        self.instructions = 0
        self.bytes = 0
        self.mnemonic: Counter = Counter()
        self.code: Counter = Counter()
        self.encoding: Counter = Counter()
        self.cpuid: Counter = Counter()
        self.flow: Counter = Counter()
        self.operands: Counter = Counter()
        self.x87: Counter = Counter()
        self.simd: Counter = Counter()
        self.simd_feature: Counter = Counter()
        self.non_legacy: Counter = Counter()
        self.rep: Counter = Counter()
        self.rep_non_string: Counter = Counter()
        self.segment: Counter = Counter()
        self.timestamp: Counter = Counter()
        self.lock: Counter = Counter()

    def add(self, ins: ix.Instruction, static: tuple) -> None:
        mnemonic, code, encoding, features, flow = static
        self.instructions += 1
        self.bytes += ins.len
        self.mnemonic[mnemonic] += 1
        self.code[code] += 1
        self.encoding[encoding] += 1
        self.cpuid["+".join(features)] += 1
        self.flow[flow] += 1
        kinds = ",".join(_NAMES["opkind"][ins.op_kind(i)] for i in range(ins.op_count))
        self.operands[f"{mnemonic} {kinds}" if kinds else mnemonic] += 1
        feature_set = set(features)
        if feature_set & X87_FEATURES or mnemonic in X87_EXTRA_MNEMONICS:
            self.x87[mnemonic] += 1
        simd = [f for f in features if f.startswith(SIMD_FEATURE_PREFIXES)]
        if simd:
            self.simd[mnemonic] += 1
            self.simd_feature["+".join(simd)] += 1
        if encoding != "LEGACY":
            self.non_legacy[f"{encoding} {mnemonic}"] += 1
        if ins.has_rep_prefix or ins.has_repe_prefix or ins.has_repne_prefix:
            prefix = "rep" if ins.has_rep_prefix else ("repe" if ins.has_repe_prefix else "repne")
            # movsd/cmpsd are also SSE2 mnemonics; the string form has no XMM operand.
            is_string = mnemonic in STRING_MNEMONICS and "XMM" not in kinds and not simd
            (self.rep if is_string else self.rep_non_string)[f"{prefix} {mnemonic}"] += 1
        if ins.segment_prefix != ix.Register.NONE:
            self.segment[_NAMES["register"][ins.segment_prefix].lower()] += 1
        if mnemonic in TIMESTAMP_MNEMONICS:
            self.timestamp[mnemonic] += 1
        if ins.has_lock_prefix:
            self.lock[mnemonic] += 1

    def report(self) -> dict:
        total = self.instructions
        x87_total = sum(self.x87.values())
        simd_total = sum(self.simd.values())
        transcendentals = {name: self.mnemonic.get(name, 0) for name in DESIGN_2_3_TRANSCENDENTALS}
        fistp = {label: self.code.get(code, 0) for code, label in FISTP_CODES.items()}
        fistp["total"] = sum(fistp.values())
        return {
            "instructions": total,
            "bytes": self.bytes,
            "distinct_mnemonics": len(self.mnemonic),
            "distinct_codes": len(self.code),
            "coverage_mnemonics": coverage_subset_sizes(self.mnemonic),
            "x87": {
                "instructions": x87_total,
                "share_percent": share(x87_total, total),
                "mnemonics": ordered(self.x87),
                "transcendentals": transcendentals,
                "fistp_variants": fistp,
                "design_2_3": {
                    "expected_transcendentals": DESIGN_2_3_TRANSCENDENTALS,
                    "expected_fistp": DESIGN_2_3_FISTP,
                    "transcendentals_agree": transcendentals == DESIGN_2_3_TRANSCENDENTALS,
                    "fistp_agree": fistp == DESIGN_2_3_FISTP,
                },
            },
            "simd": {
                "instructions": simd_total,
                "share_percent": share(simd_total, total),
                "by_feature": ordered(self.simd_feature),
                "mnemonics": ordered(self.simd),
            },
            "non_legacy_encodings": {"instructions": sum(self.non_legacy.values()),
                                     "by_encoding_and_mnemonic": ordered(self.non_legacy)},
            "x64_only_encodings": "none by construction: a 32-bit decoder cannot produce them",
            "rep_string_ops": {"instructions": sum(self.rep.values()), "by_form": ordered(self.rep)},
            "rep_prefix_on_non_string_instruction": {
                "note": "a rep prefix on a non-string instruction is data-in-code that decoded "
                        "without error (jump tables, constants); listed as a limit indicator",
                "instructions": sum(self.rep_non_string.values()),
                "by_form": ordered(self.rep_non_string)},
            "segment_overrides": {"instructions": sum(self.segment.values()),
                                  "by_segment": ordered(self.segment)},
            "lock_prefixed": {"instructions": sum(self.lock.values()), "by_mnemonic": ordered(self.lock)},
            "cpuid_rdtsc": {name: self.timestamp.get(name, 0) for name in TIMESTAMP_MNEMONICS},
            "encoding_histogram": ordered(self.encoding),
            "flow_control_histogram": ordered(self.flow),
            "cpuid_feature_histogram": ordered(self.cpuid),
            "mnemonic_histogram": ordered(self.mnemonic),
            "code_histogram": ordered(self.code),
            "operand_kind_histogram": ordered(self.operands),
        }

    def summary_line(self, region: str) -> str:
        cover = coverage_subset_sizes(self.mnemonic)
        return (f"S1_OPCODES region={region} instructions={self.instructions} "
                f"distinct_mnemonics={len(self.mnemonic)} "
                f"x87={sum(self.x87.values())} simd={sum(self.simd.values())} "
                f"non_legacy={sum(self.non_legacy.values())} "
                f"rep_string={sum(self.rep.values())} rep_non_string={sum(self.rep_non_string.values())} "
                f"seg_override={sum(self.segment.values())} "
                f"cpuid={self.timestamp.get('cpuid', 0)} rdtsc={self.timestamp.get('rdtsc', 0)} "
                f"cover99={cover['99']} cover99.9={cover['99.9']} cover100={cover['100']}")


def share(part: int, total: int) -> float:
    return round(100.0 * part / total, 4) if total else 0.0


def ordered(counter: Counter) -> list[list]:
    return [[key, count] for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def static_info(cache: dict, ins: ix.Instruction) -> tuple:
    code = ins.code
    info = cache.get(code)
    if info is None:
        info = (_NAMES["mnemonic"][ins.mnemonic], _NAMES["code"][code],
                _NAMES["encoding"][ins.encoding],
                tuple(_NAMES["cpuid"][f] for f in ins.cpuid_features()),
                _NAMES["flow"][ins.flow_control])
        cache[code] = info
    return info


# --------------------------------------------------------------------------
# Main


def text_section(pe: pefile.PE) -> tuple[bytes, int, dict]:
    for section in pe.sections:
        if section.Name.rstrip(b"\0").decode("ascii", "replace") == SECTION:
            base = pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress
            data = section.get_data()[: section.Misc_VirtualSize]
            meta = {"name": SECTION, "virtual_address": f"0x{base:08X}",
                    "virtual_size": section.Misc_VirtualSize,
                    "raw_size": section.SizeOfRawData,
                    "characteristics": f"0x{section.Characteristics:08X}"}
            return data, base, meta
    raise SystemExit(f"no {SECTION} section")


def other_executable_sections(pe: pefile.PE) -> list[dict]:
    executable = 0x20000000
    found = []
    for section in pe.sections:
        name = section.Name.rstrip(b"\0").decode("ascii", "replace")
        if name != SECTION and section.Characteristics & executable:
            found.append({"name": name,
                          "virtual_address": f"0x{pe.OPTIONAL_HEADER.ImageBase + section.VirtualAddress:08X}",
                          "virtual_size": section.Misc_VirtualSize})
    return found


def tool_versions(root: Path) -> dict:
    pins = json.loads((root / PINS).read_text(encoding="utf-8"))["tools"]
    observed = {"python": sys.version.split()[0],
                "pefile": importlib.metadata.version("pefile"),
                "iced-x86": importlib.metadata.version("iced-x86")}
    return {name: {"pinned": pins[name]["version"], "observed": observed[name],
                   "match": pins[name]["version"] == observed[name]}
            for name in observed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--exe", default=DEFAULT_EXE)
    parser.add_argument("--function-map", default=DEFAULT_MAP)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    root: Path = args.root

    exe_path = root / args.exe
    exe_bytes = exe_path.read_bytes()
    exe_sha = hashlib.sha256(exe_bytes).hexdigest()
    pe = pefile.PE(data=exe_bytes, fast_load=True)
    data, base, section_meta = text_section(pe)

    map_path = root / args.function_map
    region_map: RegionMap | None = None
    # The absent path is not written out: the cited-files gate treats every
    # repository path in a receipt as a citation that must exist.
    map_input: dict | str = ("absent; counts are whole-image (pass --function-map "
                             "once the map exists for the per-region breakdown)")
    map_flag = f" --function-map {args.function_map}" if map_path.is_file() else ""
    if map_path.is_file():
        region_map = load_function_map(map_path)
        map_input = {"path": args.function_map,
                     "sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
                     "functions": len(region_map.entries),
                     "regions": region_map.regions}

    whole = Tally()
    suspect = Tally()
    per_region: dict[str, Tally] = {}
    padding = {"runs": 0, "bytes": 0, "int3_bytes": 0, "nop_bytes": 0}
    errors: list[int] = []
    cache: dict = {}

    for item in decode_linear(data, base):
        if item[0] == "padding":
            _, start, length = item
            padding["runs"] += 1
            padding["bytes"] += length
            padding["int3_bytes" if data[start - base] == 0xCC else "nop_bytes"] += length
            continue
        if item[0] == "error":
            errors.append(item[1])
            continue
        ins, is_suspect = item
        info = static_info(cache, ins)
        if is_suspect:
            suspect.add(ins, info)
            continue
        whole.add(ins, info)
        if region_map is not None:
            region = region_map.region_of(ins.ip)
            per_region.setdefault(region, Tally()).add(ins, info)

    if region_map is None:
        regions = {UNPARTITIONED: whole.report()}
        region_note = ("function map absent; every count is over the whole .text "
                       "section under the single region 'unpartitioned'")
    else:
        regions = {name: per_region[name].report() for name in sorted(per_region)}
        region_note = ("regions from the function map; 'unmapped' holds instructions "
                       "inside no mapped function")

    command = (f".venv/bin/python Documentation/scripts/s1_opcode_histogram.py "
               f"--exe {args.exe}{map_flag} --out {args.out} "
               f"--date {args.date}")
    receipt = {
        "schema": SCHEMA,
        "id": RECEIPT_ID,
        "date": args.date,
        "question": ("Which x86 instructions does the emitter have to implement, and is "
                     "there a SIMD or dynamic-code surface in .text (design 3.4; S1 kill "
                     "criterion)?"),
        "inputs": {
            "executable": args.exe,
            "executable_sha256": exe_sha,
            "image_base": f"0x{pe.OPTIONAL_HEADER.ImageBase:08X}",
            "section": section_meta,
            "function_map": map_input,
            "tools": tool_versions(root),
        },
        "command": command,
        "method": (
            "Linear decode of .text with iced-x86 (32-bit). Runs of 0xCC and runs of two "
            "or more 0x90 are skipped as inter-function padding. On a decode error the "
            "address is recorded and decoding resumes one byte later; instructions from the "
            "error up to the next 16-byte-aligned address are 'suspect' and kept out of the "
            "clean totals. Every clean instruction is counted by mnemonic, iced Code, "
            "encoding kind, CPUID feature set, flow control and operand-kind signature; "
            "x87 is CPUID feature FPU plus wait; SIMD is MMX/SSE*/3DNow!/AVX*. Coverage is "
            "the number of most-frequent mnemonics whose cumulative share reaches the "
            "threshold. Limits: a linear sweep counts jump tables and other data-in-code "
            "that decode without error as instructions (the rep-on-non-string count and the "
            "es/cs/ss/ds override counts are indicators of that); function boundaries are unknown "
            "without the map; the executable section PSFD00 is not swept; a 32-bit "
            "decoder cannot see 64-bit-only encodings, so that check is by construction."
        ),
        "authority": "statically-translated",
        "outputs": {
            "region_note": region_note,
            "other_executable_sections_not_swept": other_executable_sections(pe),
            "padding_skipped": padding,
            "decode_errors": {
                "count": len(errors),
                "addresses": [f"0x{a:08X}" for a in errors[:ERROR_ADDRESS_LIMIT]],
                "addresses_truncated": len(errors) > ERROR_ADDRESS_LIMIT,
            },
            "suspect": {
                "note": "instructions decoded between an error and the next 16-byte alignment",
                "instructions": suspect.instructions,
                "bytes": suspect.bytes,
                "mnemonic_histogram": ordered(suspect.mnemonic),
                "mnemonics_only_in_suspect_runs": sorted(set(suspect.mnemonic) - set(whole.mnemonic)),
            },
            "whole_image": whole.report(),
            "regions": regions,
        },
    }
    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, indent=1, sort_keys=False) + "\n", encoding="utf-8")

    print(whole.summary_line("whole-image"))
    if region_map is None:
        print(whole.summary_line(UNPARTITIONED))
    else:
        for name in sorted(per_region):
            print(per_region[name].summary_line(name))
    x87 = whole.report()["x87"]
    print(f"S1_OPCODES x87_transcendentals={json.dumps(x87['transcendentals'], sort_keys=True)} "
          f"fistp={json.dumps(x87['fistp_variants'], sort_keys=True)} "
          f"design_2_3_transcendentals_agree={x87['design_2_3']['transcendentals_agree']} "
          f"design_2_3_fistp_agree={x87['design_2_3']['fistp_agree']}")
    print(f"S1_OPCODES decode_errors={len(errors)} suspect_instructions={suspect.instructions} "
          f"padding_runs={padding['runs']} padding_bytes={padding['bytes']} wrote={args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
