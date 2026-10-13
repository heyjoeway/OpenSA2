#!/usr/bin/env python3
"""A small Shader Model 3.0 assembler for host 2's synthetic test shaders (R1 U13).

Subtitle: the translator's tests need Direct3D 9 shader bytecode, and the game's shaders are
shipped bytes that no tracked file may hold (AGENTS.md rule 9). This writes the token stream of
the D3D9 shader format (d3d9types.h's D3DSIO_*, D3DSPR_*, D3DDECLUSAGE_* encodings) from a
readable text, for the instruction classes the game's shaders use (RENDERER.md section 1.1):
arithmetic, texld, dcl, def/defi, if/if_cmp/else, loop aL with relative constant addressing,
partial precision and saturate, and vertex outputs including psize.

  sm3_asm.py IN.asm OUT.bin      assemble one shader
  import sm3_asm; sm3_asm.assemble(text) -> bytes

Syntax, one instruction per line, ';' starts a comment:
  vs_3_0 | ps_3_0
  dcl_<usage>[N] v#|o#[.mask]       dcl_2d s#         dcl_texcoord0 v1      dcl_psize o2.x
  def c#, f, f, f, f                defi i#, n, n, n, n
  <op>[_pp][_sat] dst[.mask], src[.swz] ...   with -src for negation and c#[aL] for relative
  if_<gt|eq|ge|lt|ne|le> a, b  |  if b#  |  else  |  endif  |  loop aL, i#  |  endloop
"""
import re
import struct
import sys

OPCODES = {
    "nop": (0, 0), "mov": (1, 1), "add": (2, 2), "sub": (3, 2), "mad": (4, 3), "mul": (5, 2), "rcp": (6, 1),
    "rsq": (7, 1), "dp3": (8, 2), "dp4": (9, 2), "min": (10, 2), "max": (11, 2), "slt": (12, 2), "sge": (13, 2),
    "exp": (14, 1), "log": (15, 1), "lrp": (18, 3), "frc": (19, 1), "pow": (32, 2), "abs": (35, 1), "nrm": (36, 1),
    "cmp": (88, 3), "texld": (66, 2), "dsx": (91, 1), "dsy": (92, 1),
}
COMPARE = {"gt": 1, "eq": 2, "ge": 3, "lt": 4, "ne": 5, "le": 6}
USAGE = {"position": 0, "blendweight": 1, "blendindices": 2, "normal": 3, "psize": 4, "texcoord": 5, "tangent": 6,
         "binormal": 7, "tessfactor": 8, "positiont": 9, "color": 10, "fog": 11, "depth": 12, "sample": 13}
TEXTURE = {"2d": 2, "cube": 3, "volume": 4}
REGTYPE = {"r": 0, "v": 1, "c": 2, "a": 3, "o": 6, "i": 7, "oC": 8, "oDepth": 9, "s": 10, "aL": 15, "b": 14}
SWZ = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}


class AsmError(Exception):
    pass


def reg_token(rtype, num):
    return 0x80000000 | ((rtype & 7) << 28) | (((rtype >> 3) & 3) << 11) | (num & 0x7FF)


def parse_reg(text):
    m = re.fullmatch(r"(oC|oDepth|aL|[rvcaoisb])(\d*)", text)
    if not m:
        raise AsmError(f"bad register {text!r}")
    name, num = m.group(1), int(m.group(2) or 0)
    return REGTYPE[name], num


def dst(text, pp=False, sat=False):
    reg, _, mask = text.partition(".")
    rtype, num = parse_reg(reg)
    wm = 0xF if not mask else sum(1 << SWZ[ch] for ch in mask)
    mod = (1 if sat else 0) | (2 if pp else 0)
    return [reg_token(rtype, num) | (wm << 16) | (mod << 20)]


def src(text):
    neg = text.startswith("-")
    text = text.lstrip("-")
    m = re.fullmatch(r"([A-Za-z]+\d*)(?:\[(aL|a0\.[xyzw])\])?(?:\.([xyzwrgba]{1,4}))?", text)
    if not m:
        raise AsmError(f"bad source {text!r}")
    rtype, num = parse_reg(m.group(1))
    swz = m.group(3) or "xyzw"
    swz = (swz + swz[-1] * 4)[:4]
    s = sum(SWZ[ch] << (2 * i) for i, ch in enumerate(swz))
    tok = reg_token(rtype, num) | (s << 16) | ((1 if neg else 0) << 24)
    out = [tok]
    if m.group(2):
        tok |= 1 << 13  # D3DSHADER_ADDRMODE_RELATIVE
        out = [tok]
        rel = m.group(2)
        if rel == "aL":
            out.append(reg_token(15, 0) | (0x00 << 16))       # aL.x replicated
        else:
            out.append(reg_token(3, 0) | ((SWZ[rel[-1]] * 0x55) << 16))
    return out


def instr(opcode, params, controls=0):
    return [opcode | (controls << 16) | (len(params) << 24)] + params


def floats(values):
    return [struct.unpack("<I", struct.pack("<f", float(v)))[0] for v in values]


def assemble(text):
    tokens = []
    lines = [l.split(";")[0].strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    if not lines or lines[0] not in ("vs_3_0", "ps_3_0"):
        raise AsmError("the first line must be vs_3_0 or ps_3_0")
    tokens.append(0xFFFE0300 if lines[0] == "vs_3_0" else 0xFFFF0300)
    for line in lines[1:]:
        head, _, rest = line.partition(" ")
        args = [a.strip() for a in rest.split(",")] if rest.strip() else []
        if head.startswith("dcl_"):
            kind = head[4:]
            if kind in TEXTURE:
                rtype, num = parse_reg(args[0])
                tokens += instr(31, [0x80000000 | (TEXTURE[kind] << 27), reg_token(rtype, num) | (0xF << 16)])
                continue
            m = re.fullmatch(r"([a-z]+?)(\d*)", kind)
            if not m or m.group(1) not in USAGE:
                raise AsmError(f"bad dcl {head}")
            usage = 0x80000000 | USAGE[m.group(1)] | (int(m.group(2) or 0) << 16)
            tokens += instr(31, [usage] + dst(args[0]))
        elif head == "def":
            tokens += instr(81, dst(args[0]) + floats(args[1:5]))
        elif head == "defi":
            tokens += instr(48, dst(args[0]) + [int(a) & 0xFFFFFFFF for a in args[1:5]])
        elif head.startswith("if_"):
            tokens += instr(41, src(args[0]) + src(args[1]), COMPARE[head[3:]])
        elif head == "if":
            tokens += instr(40, src(args[0]))
        elif head == "else":
            tokens += instr(42, [])
        elif head == "endif":
            tokens += instr(43, [])
        elif head == "loop":
            tokens += instr(27, src(args[0]) + src(args[1]))
        elif head == "endloop":
            tokens += instr(29, [])
        else:
            parts = head.split("_")
            op = parts[0]
            if op not in OPCODES:
                raise AsmError(f"unknown instruction {head}")
            code, nsrc = OPCODES[op]
            pp, sat = "pp" in parts[1:], "sat" in parts[1:]
            if len(args) != nsrc + 1:
                raise AsmError(f"{head} takes {nsrc + 1} operands")
            params = dst(args[0], pp, sat)
            for a in args[1:]:
                params += src(a)
            tokens += instr(code, params)
    tokens.append(0x0000FFFF)
    return struct.pack("<%dI" % len(tokens), *tokens)


def main(argv):
    if len(argv) != 3:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    open(argv[2], "wb").write(assemble(open(argv[1]).read()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
