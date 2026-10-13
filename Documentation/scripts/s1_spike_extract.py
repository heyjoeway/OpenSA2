#!/usr/bin/env python3
"""Extract the ten S1 spike functions (design appendix B) from the unpacked exe.

For each function address it writes, under private/generated/spike/functions/
(ignored by git; nothing derived from the executable is ever committed):

  sub_<VA>.bin      the function's bytes from its start to its first `ret`
                    inclusive (appendix B counts "objdump instruction lines to
                    first ret"), so a lifter fed raw bytes with the base
                    address set sees exactly the sized function;
  sub_<VA>.objdump  `objdump -D -b binary -m i386 --adjust-vma=<VA>` over those
                    bytes, the listing appendix B's sizes were taken from;

and one manifest, private/generated/spike/functions.json, with per function
the VA, file offset, byte length, the iced-x86 instruction count, the objdump
instruction-line count, the appendix B count, and whether they agree. The
receipt writer (tools/spike/write_receipt.py) copies the manifest's numbers;
nobody types them.

The first `ret` is found with iced-x86 decoding linearly from the start; a
function with an early return is cut short by this rule, which is what
appendix B's sizing rule does too, so the two counts stay comparable.

Usage: .venv/bin/python Documentation/scripts/s1_spike_extract.py
       [--exe private/exe/sonic2app-unpacked.exe] [--out private/generated/spike]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pefile
from iced_x86 import Decoder, Formatter, FormatterSyntax, Mnemonic

EXE_SHA256 = "c97b116fbd1d64108cbc8ec403b4a59ba6f815159b9a0ad896668d6c19c1bfe9"

# Design appendix B: address, instruction count, name where the design gives one.
APPENDIX_B = [
    (0x004273B0, 34, ""),
    (0x00426CC0, 47, ""),
    (0x0048B9F0, 78, ""),
    (0x00446960, 25, ""),
    (0x004469E0, 29, ""),
    (0x0077F3A0, 50, "njUnitVector"),
    (0x0077FC30, 28, "length"),
    (0x00468E70, 39, "rotate by entity"),
    (0x00468FA0, 57, ""),
    (0x0044B210, 26, ""),
]

RET_MNEMONICS = {Mnemonic.RET, Mnemonic.RETF}
MAX_BYTES = 4096


def objdump_lines(bin_path: Path, va: int) -> list[str]:
    out = subprocess.run(
        ("objdump", "-D", "-b", "binary", "-m", "i386", f"--adjust-vma={va:#x}", str(bin_path)),
        capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def count_objdump_instructions(lines: list[str]) -> int:
    """Instruction lines (address-colon-bytes-mnemonic) up to and including the first ret."""
    count = 0
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].strip().endswith(":"):
            continue           # header, blank, section line, or a bytes-only continuation
        count += 1
        if parts[2].strip().split()[0] in ("ret", "retn", "retf", "ret ", "lret"):
            break
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=Path("private/exe/sonic2app-unpacked.exe"))
    parser.add_argument("--out", type=Path, default=Path("private/generated/spike"))
    args = parser.parse_args()

    data = args.exe.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXE_SHA256:
        raise SystemExit(f"{args.exe}: sha256 {digest} is not the pinned unpacked exe {EXE_SHA256}")
    pe = pefile.PE(data=data, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    functions_dir = args.out / "functions"
    functions_dir.mkdir(parents=True, exist_ok=True)
    formatter = Formatter(FormatterSyntax.INTEL)

    entries = []
    for va, expected, name in APPENDIX_B:
        offset = pe.get_offset_from_rva(va - base)
        window = data[offset:offset + MAX_BYTES]
        decoder = Decoder(32, window, ip=va)
        length = 0
        iced_count = 0
        listing = []
        for insn in decoder:
            iced_count += 1
            length = insn.ip + insn.len - va
            listing.append(f"{insn.ip:08x}  {formatter.format(insn)}")
            if insn.mnemonic in RET_MNEMONICS:
                break
        else:
            raise SystemExit(f"{va:#010x}: no ret within {MAX_BYTES} bytes")
        body = window[:length]
        bin_path = functions_dir / f"sub_{va:08X}.bin"
        bin_path.write_bytes(body)
        lines = objdump_lines(bin_path, va)
        (functions_dir / f"sub_{va:08X}.objdump").write_text("\n".join(lines) + "\n")
        (functions_dir / f"sub_{va:08X}.iced").write_text("\n".join(listing) + "\n")
        objdump_count = count_objdump_instructions(lines)
        entries.append({
            "va": f"{va:#010x}",
            "name": name,
            "file_offset": offset,
            "bytes": length,
            "bytes_sha256": hashlib.sha256(body).hexdigest(),
            "iced_instructions_to_first_ret": iced_count,
            "objdump_instructions_to_first_ret": objdump_count,
            "appendix_b_instructions": expected,
            "matches_appendix_b": objdump_count == expected,
            "bin": str(bin_path),
            "objdump": str(functions_dir / f"sub_{va:08X}.objdump"),
        })
        print(f"{va:#010x} {name or '-':18} bytes={length:4} iced={iced_count:3} "
              f"objdump={objdump_count:3} appendixB={expected:3} "
              f"{'ok' if objdump_count == expected else 'MISMATCH'}")

    manifest = {
        "schema": "sa2-recomp-spike-functions/v1",
        "exe": str(args.exe),
        "exe_sha256": digest,
        "image_base": f"{base:#x}",
        "sizing_rule": "objdump -D -b binary -m i386 instruction lines from the function start to its first ret inclusive",
        "functions": entries,
    }
    (args.out / "functions.json").write_text(json.dumps(manifest, indent=2) + "\n")
    mismatches = [e["va"] for e in entries if not e["matches_appendix_b"]]
    print(f"wrote {args.out / 'functions.json'}; {len(entries)} functions, "
          f"{len(mismatches)} appendix B mismatch(es){': ' + ', '.join(mismatches) if mismatches else ''}")


if __name__ == "__main__":
    main()
