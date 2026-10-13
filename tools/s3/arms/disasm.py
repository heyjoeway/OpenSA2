#!/usr/bin/env python3
"""Disassemble a guest address range out of the unpacked image.

Subtitle: read the bytes remill reads, at the addresses the map names.

  .venv/bin/python tools/s3/arms/disasm.py 0x004015f0 0x004016d0
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "whole_image"))
import common as C  # noqa: E402


def main() -> None:
    from iced_x86 import Decoder, Formatter, FormatterSyntax
    start = int(sys.argv[1], 16)
    end = int(sys.argv[2], 16)
    img = C.memory_image()
    data = img[start - C.IMAGE_BASE:end - C.IMAGE_BASE]
    dec = Decoder(32, data, ip=start)
    fmt = Formatter(FormatterSyntax.INTEL)
    for ins in dec:
        raw = data[ins.ip - start:ins.ip - start + ins.len].hex()
        print(f"{ins.ip:08x}  {raw:<20s} {fmt.format(ins)}")


if __name__ == "__main__":
    main()
