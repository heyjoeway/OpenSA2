#!/usr/bin/env python3
"""Disassemble a range of the shipped image, with imports and strings named.

Subtitle: `tools/s4/image.py`'s anchored decoder with a command line, so
reading the CRT startup path does not mean writing a throwaway script each
time. It prints text; it never writes image bytes anywhere.

Usage:
  .venv/bin/python tools/s4/disas.py 0x007a801c --count 90
  .venv/bin/python tools/s4/disas.py 0x007a801c --until 0x007a80c0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import image as IM  # noqa: E402
from iced_x86 import Decoder  # noqa: E402


def annotate(img: "IM.Image", ins) -> str:
    out = ""
    disp = IM.abs_displacement(ins)
    if disp is not None:
        if disp in img.imports:
            out += "   ; " + "!".join(img.imports[disp])
        else:
            s = img.cstring(disp, 64)
            if s:
                out += '   ; "%s"' % s
            else:
                out += "   ; [%#010x]" % disp
    for imm in IM.immediates(ins):
        s = img.cstring(imm, 64)
        if s:
            out += '   ; "%s"' % s
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--until", default=None)
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    args = ap.parse_args()

    img = IM.Image(args.exe)
    start = int(args.start, 0)
    until = int(args.until, 0) if args.until else None
    sec = img.section_of(start)
    if sec is None or not sec.executable:
        raise SystemExit(f"{start:#010x} is not in an executable section")
    data = img._section_bytes[sec.name]
    for i, ins in enumerate(Decoder(32, data[start - sec.va:], ip=start)):
        if until is None and i >= args.count:
            break
        if until is not None and ins.ip > until:
            break
        print("%08x  %-40s%s" % (ins.ip, img.text(ins), annotate(img, ins)))


if __name__ == "__main__":
    main()
