#!/usr/bin/env python3
"""runtime/sa2_emulate.cpp against the host CPU: cases, a run, and the comparison.

Subtitle: every emulated site's own bytes and every encoding shape, run once
by the emulator and once natively, compared where remill's model says they
must agree.

    .venv/bin/python tools/s4/emulate_check.py        # build, run, print the summary

`harness/emulate_probe.cpp` does the running; see its header for how the
native side gets the same registers and memory. What is compared, and what is
not, follows from remill's model (runtime/sa2_emulate.h):

* **MMX and SSE forms:** every MMX and XMM register and every byte of guest
  memory. Not the x87 state (remill keeps MMX registers apart from the x87
  stack, where the hardware aliases them) and not MXCSR's sticky flags
  (remill does not model them).
* **x87 environment forms:** every byte the instruction stores except the tag
  word, and afterwards the control word, the status word (TOP included), the
  instruction and operand pointers, all eight registers, and MXCSR -- which
  shares the FXSAVE area and no x87 environment instruction may touch. The tag
  word is not compared: the hardware derives it from the registers' contents
  when it stores it, and remill keeps none.
* **Every mode:** the eight general registers, which none of these change.
* **Refusals and FS/GS forms:** the emulator only -- the bytes are not all
  instructions, an address outside guest memory is a fault natively too, and
  the 64-bit host resolves FS and GS through its own TLS. The emulated side has
  nonzero FS and GS bases, and an FS or GS case must write where that base puts
  it.

The model the emulator is checked against is addressed through byte offsets
read from the lifted IR (`lifted_fx_offsets`), not through the emulator's own
field names, and it carries the stale copies lifted code leaves: an emulator
reading the wrong view of the FPU union, or the stale copy of a flag, fails.

Each case also requires the emulator to report the next instruction's address,
which is where the runtime continues.
"""

from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from iced_x86 import Decoder, OpKind, Register

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

WIN, WIN_SIZE = 0x10000000, 0x00100000
CODE = WIN + 0x000F0000
TARGET = WIN + 0x00040000          # where memory operands point
AUX, AUX_SIZE = 0x00870000, 0x00010000   # the probe's region over image addresses
STACK = WIN + 0x00080000           # esp
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
REMILL_INC = ROOT / "private" / "lifters" / "remill" / "usr" / "local" / "include"
REGS = [Register.EAX, Register.ECX, Register.EDX, Register.EBX,
        Register.ESP, Register.EBP, Register.ESI, Register.EDI]
X87_KINDS = {"fnstenv", "fldenv", "fnsave", "frstor"}
STORES = {"fnstenv": 28, "fnsave": 108}
LOADS = {"fldenv": 28, "frstor": 108}


@dataclass
class Case:
    name: str
    mode: str                      # m: MMX/SSE, x: x87, r: emulator only (a refusal)
    insn: bytes                    # what the emulator runs
    native: bytes | None           # what the host runs, when it has to differ
    gpr: list[int]
    mm: list[int]
    xmm: list[bytes]
    x87: bytes
    mxcsr: int
    pokes: dict[int, bytes] = field(default_factory=dict)
    kind: str = ""
    ea: int | None = None
    expect: int = 0                # emulator status: 0 ok, 1 unsupported, 2 fault
    expect_fault: int | None = None
    expect_write: int | None = None  # an emulator-only case: where its store must land
    expect_why: str | None = None    # a refusal: text its reason must contain


def f80(x: float) -> bytes:
    """A Python float as an x87 80-bit extended value."""
    if x == 0:
        return (b"\0" * 8) + struct.pack("<H", 0x8000 if math.copysign(1, x) < 0 else 0)
    if math.isinf(x):
        return struct.pack("<QH", 1 << 63, (0x8000 if x < 0 else 0) | 0x7FFF)
    if math.isnan(x):
        return struct.pack("<QH", 0xC000000000000000, 0x7FFF)
    m, e = math.frexp(abs(x))                  # x = m * 2**e, 0.5 <= m < 1
    sig = int(m * (1 << 64))                   # the integer bit on top
    return struct.pack("<QH", sig, (0x8000 if x < 0 else 0) | (e - 1 + 16383))


def x87_image(rng: random.Random, cw: int, top: int = 0, regs: list[float] | None = None) -> bytes:
    sw = (rng.getrandbits(1) << 8) | (rng.getrandbits(1) << 9) | (rng.getrandbits(1) << 10) \
        | (rng.getrandbits(1) << 14) | (top << 11) | (rng.getrandbits(6) if cw & 0x3F == 0x3F else 0)
    tw = rng.getrandbits(16)
    env = struct.pack("<HHHHHH", cw, 0xFFFF, sw, 0xFFFF, tw, 0xFFFF)
    env += struct.pack("<IHHIHH", rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(11),
                       rng.getrandbits(32), rng.getrandbits(16), 0xFFFF)
    regs = regs or [rng.uniform(-1e6, 1e6) for _ in range(8)]
    return env + b"".join(f80(v) for v in regs)


def rc(cw: int) -> int:
    return (cw >> 10) & 3


def mxcsr_for(cw: int) -> int:
    return 0x1F80 | (rc(cw) << 13)


def pack_ctx(c: Case) -> bytes:
    out = struct.pack("<8I", *[g & 0xFFFFFFFF for g in c.gpr])
    out += struct.pack("<8Q", *[m & 0xFFFFFFFFFFFFFFFF for m in c.mm])
    out += b"".join(x.ljust(16, b"\0")[:16] for x in c.xmm)
    out += c.x87
    out += struct.pack("<I", c.mxcsr)
    assert len(out) == 336
    return out


def unpack_ctx(b: bytes) -> dict:
    gpr = struct.unpack_from("<8I", b, 0)
    mm = struct.unpack_from("<8Q", b, 32)
    xmm = [b[96 + 16 * k:112 + 16 * k] for k in range(8)]
    return {"gpr": gpr, "mm": mm, "xmm": xmm, "x87": b[224:332],
            "mxcsr": struct.unpack_from("<I", b, 332)[0]}


def floats(*vals: float) -> bytes:
    return b"".join(struct.pack("<f", v) for v in vals)


SPECIAL = [0.0, -0.0, 0.5, 1.5, 2.5, -2.5, -0.5, 3.5, 1e10, -1e10, 2147483647.0, 2147483648.0,
           -2147483648.0, -2147483904.0, float("inf"), float("-inf"), float("nan"), 1e-40,
           123.456, -99.99, 16777217.0, 0.49999997]


def base_case(rng: random.Random, name: str, insn: bytes, kind: str) -> Case:
    cw = rng.choice([0x037F, 0x027F, 0x007F, 0x077F, 0x0B7F, 0x0F7F, 0x0360, 0x0B72])
    return Case(name=name, mode="x" if kind in X87_KINDS else "m", insn=insn, native=None,
                gpr=[rng.getrandbits(32) for _ in range(8)],
                mm=[rng.getrandbits(64) for _ in range(8)],
                xmm=[bytes(rng.getrandbits(8) for _ in range(16)) for _ in range(8)],
                x87=x87_image(rng, cw, top=rng.randrange(8)), mxcsr=mxcsr_for(cw), kind=kind)


def aim(c: Case, rng: random.Random, image=None, align: int = 4) -> bool:
    """Point the instruction's memory operand at TARGET; re-encode for the host
    where 64-bit mode reads the guest's bytes differently. False when the
    operand cannot be aimed (an absolute address outside the window), or when
    it cannot be put on an `align`-byte boundary: psubsw's xmm form must be
    16-byte aligned or the hardware raises #GP, so its cases are aimed at 16."""
    ins = next(iter(Decoder(32, c.insn, ip=CODE)))
    c.gpr[4] = STACK
    has_mem = any(ins.op_kind(k) == OpKind.MEMORY for k in range(ins.op_count))
    if not has_mem:
        return True
    disp = ins.memory_displacement & 0xFFFFFFFF
    base, index, scale = ins.memory_base, ins.memory_index, ins.memory_index_scale
    if base == Register.NONE and index == Register.NONE:
        if AUX <= disp < AUX + AUX_SIZE - 128 and image is not None:
            c.pokes[disp] = image.read(disp, 16)       # the value the game reads there
        elif not (WIN <= disp < WIN + WIN_SIZE - 128):
            return False
        if disp % align:
            return False
        c.ea = disp
        # mod=00 rm=101 is RIP-relative in 64-bit mode: the host gets the same
        # instruction through a SIB byte with no base and no index. Only that
        # ModRM form: an operand that already has a SIB byte is left alone.
        raw = bytearray(c.insn)
        i = 0
        while raw[i] in (0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66):
            i += 1
        i += 2 if raw[i] == 0x0F else 1          # the ModRM byte
        if raw[i] & 0xC7 == 0x05:
            c.native = bytes(raw[:i]) + bytes([(raw[i] & 0x38) | 0x04, 0x25]) + bytes(raw[i + 1:])
        return True
    iv = 0
    if index != Register.NONE:
        iv = rng.randrange(0, 16)
        if align != 4 and base == Register.NONE:
            iv -= iv % max(1, align // scale)     # disp + iv*scale must land aligned
        if index != base:
            c.gpr[REGS.index(index)] = iv
    if base != Register.NONE:
        if base == index:
            return False
        want = (TARGET + rng.randrange(0, 64) * align - disp - iv * scale) & 0xFFFFFFFF
        if base == Register.ESP:
            c.gpr[4] = want
        else:
            c.gpr[REGS.index(base)] = want
    ea = (disp + iv * scale + (c.gpr[REGS.index(base)] if base != Register.NONE else 0)) & 0xFFFFFFFF
    if not (WIN <= ea < WIN + WIN_SIZE - 128) or WIN + 0xF0000 <= ea + 128 and ea < WIN + 0xF0000 + 16:
        return False
    if ea % align:
        return False
    c.ea = ea
    return True


def load_pokes(c: Case, rng: random.Random) -> None:
    """A valid environment or image where fldenv or frstor will read it."""
    if c.kind in LOADS and c.ea is not None:
        cw = rng.choice([0x037F, 0x027F, 0x007F, 0x0B7F, 0x0360, 0x0772])
        img = x87_image(rng, cw, top=rng.randrange(8))
        c.pokes[c.ea] = img[:LOADS[c.kind]]
        c.mxcsr = mxcsr_for(struct.unpack_from("<H", c.x87, 0)[0])


def site_cases(rng: random.Random) -> tuple[list[Case], list[str]]:
    """Every site lifted code reaches, with its own bytes, three times over."""
    import image as IM
    import emulated as EM
    sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
    import common as C
    import json
    img = IM.Image()
    extras = json.loads((ROOT / "Documentation" / "lifted-extras.json").read_text())["rows"]
    found = EM.census(img, C, extras)
    out, skipped = [], []
    for site in found.values():
        raw = img.read(site.ip, site.next_ip - site.ip)
        for k in range(3):
            c = base_case(rng, f"site {site.ip:#010x} {site.kind} #{k}", raw, site.kind)
            if not aim(c, rng, img, align=16 if site.kind == "psubsw xmm" else 4):
                skipped.append(f"{site.ip:#010x} {site.kind}: its operand cannot be aimed at guest memory")
                break
            if site.kind == "cvtps2pi":
                for r in range(8):
                    c.xmm[r] = floats(*(rng.choice(SPECIAL) for _ in range(4)))
            load_pokes(c, rng)
            out.append(c)
    return out, skipped


def modrm_forms(reg: int) -> list[tuple[str, bytes]]:
    """ModRM (and SIB and displacement) bytes for a spread of addressing shapes,
    each with `reg` in ModRM.reg."""
    r = reg << 3
    return [
        ("[eax]", bytes([0x00 | r | 0])),
        ("[esi+disp8]", bytes([0x40 | r | 6, 0x10])),
        ("[esi-disp8]", bytes([0x40 | r | 6, 0xE0])),
        ("[esp-disp8]", bytes([0x40 | r | 4, 0x24, 0xF0])),
        ("[ebp+disp32]", bytes([0x80 | r | 5]) + struct.pack("<i", -0x20)),
        ("[esp]", bytes([0x00 | r | 4, 0x24])),
        ("[esp+disp8]", bytes([0x40 | r | 4, 0x24, 0x14])),
        ("[ebx+ecx*8]", bytes([0x00 | r | 4, 0xCB])),
        ("[edi+eax*4+disp8]", bytes([0x40 | r | 4, 0x87, 0x08])),
        ("[edx*2+disp32]", bytes([0x00 | r | 4, 0x55]) + struct.pack("<I", TARGET)),
        ("[disp32]", bytes([0x00 | r | 5]) + struct.pack("<I", TARGET + 0x100)),
    ]


def synthetic_cases(rng: random.Random) -> list[Case]:
    out = []
    edges = [0, 0xFF, 0x7F, 0x80, 0x0101010101010101, 0xFFFFFFFFFFFFFFFF, 0x00FF00FF00FF00FF,
             0x8000800080008000, 0x7FFF7FFF7FFF7FFF]
    for op, kind in ((0xDC, "paddusb"), (0xDD, "paddusw")):
        for d in range(8):
            for s in range(8):
                c = base_case(rng, f"{kind} mm{d},mm{s}", bytes([0x0F, op, 0xC0 | d << 3 | s]), kind)
                c.gpr[4] = STACK
                c.mm[d], c.mm[s] = rng.choice(edges + [rng.getrandbits(64)]), rng.getrandbits(64)
                out.append(c)
        for label, mrm in modrm_forms(rng.randrange(8)):
            for prefix in (b"", b"\x3e", b"\x26"):
                c = base_case(rng, f"{kind} mm,{label} {prefix.hex()}", prefix + bytes([0x0F, op]) + mrm, kind)
                if aim(c, rng):
                    out.append(c)
    # psubsw, both forms: signed saturation at both ends, and random words.
    sat = [0x8000800080008000, 0x7FFF7FFF7FFF7FFF, 0x0001FFFF80017FFE, 0, 0xFFFFFFFFFFFFFFFF]
    for d in range(8):
        for s in range(8):
            c = base_case(rng, f"psubsw mm{d},mm{s}", bytes([0x0F, 0xE9, 0xC0 | d << 3 | s]), "psubsw")
            c.gpr[4] = STACK
            c.mm[d], c.mm[s] = rng.choice(sat + [rng.getrandbits(64)]), rng.choice(sat + [rng.getrandbits(64)])
            out.append(c)
            c = base_case(rng, f"psubsw xmm{d},xmm{s}", bytes([0x66, 0x0F, 0xE9, 0xC0 | d << 3 | s]),
                          "psubsw xmm")
            c.gpr[4] = STACK
            c.xmm[d] = struct.pack("<QQ", rng.choice(sat), rng.getrandbits(64))
            c.xmm[s] = struct.pack("<QQ", rng.getrandbits(64), rng.choice(sat))
            out.append(c)
    for label, mrm in modrm_forms(rng.randrange(8)):
        for prefix in (b"", b"\x3e", b"\x26"):
            c = base_case(rng, f"psubsw mm,{label} {prefix.hex()}", prefix + bytes([0x0F, 0xE9]) + mrm,
                          "psubsw")
            if aim(c, rng):
                out.append(c)
            c = base_case(rng, f"psubsw xmm,{label} {prefix.hex()}",
                          prefix + bytes([0x66, 0x0F, 0xE9]) + mrm, "psubsw xmm")
            if aim(c, rng, align=16):
                out.append(c)
    # And fixed pairs, so every lane saturates in both directions whatever the seed.
    fixed = [(0x7FFF7FFF7FFF7FFF, 0x8000800080008000), (0x8000800080008000, 0x7FFF7FFF7FFF7FFF),
             (0x0001FFFF80017FFE, 0x8000800080008000), (0x8000800080008000, 0x0001FFFF80017FFE)]
    for k, (a, b) in enumerate(fixed):
        c = base_case(rng, f"psubsw mm1,mm2 saturating #{k}", b"\x0f\xe9\xca", "psubsw")
        c.gpr[4] = STACK
        c.mm[1], c.mm[2] = a, b
        out.append(c)
        c = base_case(rng, f"psubsw xmm1,xmm2 saturating #{k}", b"\x66\x0f\xe9\xca", "psubsw xmm")
        c.gpr[4] = STACK
        c.xmm[1], c.xmm[2] = struct.pack("<QQ", a, b), struct.pack("<QQ", b, a)
        out.append(c)
    for mode_cw in (0x037F, 0x077F, 0x0B7F, 0x0F7F):
        for d in range(8):
            for s in range(8):
                c = base_case(rng, f"cvtps2pi mm{d},xmm{s} rc{rc(mode_cw)}",
                              bytes([0x0F, 0x2D, 0xC0 | d << 3 | s]), "cvtps2pi")
                c.gpr[4] = STACK
                c.x87 = x87_image(rng, mode_cw)
                c.mxcsr = mxcsr_for(mode_cw)
                for r in range(8):
                    c.xmm[r] = floats(*(rng.choice(SPECIAL) for _ in range(4)))
                out.append(c)
        for label, mrm in modrm_forms(rng.randrange(8))[:5]:
            c = base_case(rng, f"cvtps2pi mm,{label} rc{rc(mode_cw)}", bytes([0x0F, 0x2D]) + mrm, "cvtps2pi")
            if aim(c, rng):
                c.x87 = x87_image(rng, mode_cw)
                c.mxcsr = mxcsr_for(mode_cw)
                c.pokes[c.ea] = floats(rng.choice(SPECIAL), rng.choice(SPECIAL))
                out.append(c)
    for kind, op, reg in (("fnstenv", 0xD9, 6), ("fldenv", 0xD9, 4), ("fnsave", 0xDD, 6),
                          ("frstor", 0xDD, 4)):
        for label, mrm in modrm_forms(reg):
            for k in range(4):
                c = base_case(rng, f"{kind} {label} #{k}", bytes([op]) + mrm, kind)
                if aim(c, rng):
                    load_pokes(c, rng)
                    out.append(c)
    return out


def refusal_cases(rng: random.Random) -> list[Case]:
    out = []
    for label, raw in (("cvtpi2ps 0F 2A", b"\x0f\x2a\xc1"), ("cvtss2si F3 0F 2D", b"\xf3\x0f\x2d\xc1"),
                       ("cvtpd2pi 66 0F 2D", b"\x66\x0f\x2d\xc1"), ("fnstenv 16-bit", b"\x66\xd9\x30"),
                       ("fchs D9 E0", b"\xd9\xe0"), ("addr-size 67", b"\x67\x0f\xdc\x00"),
                       ("lock paddusb", b"\xf0\x0f\xdc\xc1"), ("rep paddusw", b"\xf3\x0f\xdd\xc1"),
                       ("paddusb xmm 66 0F DC", b"\x66\x0f\xdc\xc1"),
                       ("paddusw xmm 66 0F DD", b"\x66\x0f\xdd\x00"),
                       ("nop", b"\x90")):
        c = base_case(rng, f"refuse {label}", raw, "")
        c.mode, c.expect = "r", 1
        out.append(c)
    # psubsw's xmm form with an m128 off a 16-byte boundary: the hardware raises
    # #GP, and the emulator refuses by name rather than executing it.
    c = base_case(rng, "refuse psubsw xmm unaligned m128", b"\x66\x0f\xe9\x01", "")
    c.mode, c.expect = "r", 1
    c.gpr[1] = TARGET + 4
    c.expect_why = "16-byte_aligned"   # refused for its alignment, not as an unknown opcode
    out.append(c)
    # memory the guest does not have: a fault, at the address the hardware would name
    for kind, raw in (("paddusw", b"\x0f\xdd\x01"), ("fnstenv", b"\xd9\x31"), ("frstor", b"\xdd\x21"),
                      ("cvtps2pi", b"\x0f\x2d\x01"), ("psubsw", b"\x0f\xe9\x01"),
                      ("psubsw xmm", b"\x66\x0f\xe9\x01")):
        c = base_case(rng, f"fault {kind} [ecx]", raw, kind)
        c.mode, c.expect = "r", 2
        c.gpr[1] = 0x00001000
        c.expect_fault = 0x00001000
        out.append(c)
    # FS and GS: the emulated side's bases are WIN + 0x20000 and WIN + 0x30000
    for label, prefix, base in (("fs", b"\x64", WIN + 0x20000), ("gs", b"\x65", WIN + 0x30000)):
        for kind, raw in (("fnstenv", b"\xd9\x31"), ("fnsave", b"\xdd\x31")):
            c = base_case(rng, f"{kind} {label}:[ecx]", prefix + raw, kind)
            c.mode, c.expect = "e", 0
            c.gpr[1] = 0x100
            c.expect_write = base + 0x100
            out.append(c)
    # a store that crosses out of guest memory faults at the first byte it cannot write
    c = base_case(rng, "fault fnstenv across the end", b"\xd9\x31", "fnstenv")
    c.mode, c.expect = "r", 2
    c.gpr[1] = WIN + WIN_SIZE - 10
    c.expect_fault = WIN + WIN_SIZE
    out.append(c)
    return out


LIFTED_LL = ROOT / "private" / "generated" / "s3" / "whole_image" / "lift" / "next" / "sub_0082DF16.pc24.ll"
FX_FIELDS = {"cwd": 0, "swd": 1, "ftw": 2, "fop": 4, "ip": 5, "cs": 6, "dp": 8, "ds": 9, "mxcsr": 11}


def lifted_fx_offsets(ll: Path = LIFTED_LL) -> dict[str, int]:
    """Where lifted code keeps the x87 environment, read from its own IR.

    The probe addresses the model through these byte offsets rather than
    through the emulator's C++ field names. The first version of the emulator
    and of this harness both used remill's FSAVE view of the FPU union, agreed
    with each other and passed 1296 cases, and were both wrong: the IR's union
    is FXSAVE-shaped. So the offsets come from two things the IR says:

    * the union's storage type, `%union.FPU = { %struct.anon.N }` with
      `anon.N = { %struct.FpuFXSAVE, ... }`, whose field list gives every
      field's offset under LLVM's natural alignment;
    * behaviour: the field index every lifted FLD updates TOP through (a load
      of i16, `add ... 14336`, a store), which must be the status word's.
    """
    import re
    text = ll.read_text(errors="replace")
    types = dict(re.findall(r"^(%[\w.]+) = type (\{.*\})$", text, re.M))
    fpu = types.get("%union.FPU", "")
    inner = re.fullmatch(r"\{ (%struct\.anon\.\d+) \}", fpu)
    if not inner or not types.get(inner.group(1), "").startswith("{ %struct.FpuFXSAVE,"):
        raise SystemExit(f"{ll.name}: the FPU union's storage is not FpuFXSAVE ({fpu!r})")
    x86 = [f.strip() for f in types["%struct.X86State"][1:-1].split(",")]
    fpu_index = x86.index("%union.FPU")

    def fields(body: str) -> list[str]:
        out, depth, cur = [], 0, ""
        for ch in body[1:-1]:
            if ch in "{[<":
                depth += 1
            elif ch in "}]>":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        return out + ([cur.strip()] if cur.strip() else [])

    def size_align(ty: str) -> tuple[int, int]:
        prim = {"i8": (1, 1), "i16": (2, 2), "i32": (4, 4), "i64": (8, 8), "i128": (16, 16),
                "x86_fp80": (16, 16), "float": (4, 4), "double": (8, 8), "ptr": (8, 8)}
        if ty in prim:
            return prim[ty]
        m = re.fullmatch(r"\[(\d+) x (.+)\]", ty)
        if m:
            s, a = size_align(m.group(2))
            return int(m.group(1)) * s, a
        body = types[ty]
        off, al = 0, 1
        for f in fields(body):
            s, a = size_align(f)
            off = (off + a - 1) // a * a + s
            al = max(al, a)
        return (off + al - 1) // al * al, al

    offs, off = [], 0
    for f in fields(types["%struct.FpuFXSAVE"]):
        s, a = size_align(f)
        off = (off + a - 1) // a * a
        offs.append(off)
        off += s
    top = re.search(r"getelementptr inbounds %struct\.State, ptr %state, i64 0, i32 0, "
                    rf"i32 {fpu_index}, i32 0, i32 0, i32 (\d+), i32 0\n\s+(%[\w.]+) = load i16, "
                    r"ptr %[\w.]+, align \d+\n\s+%[\w.]+ = add i16 \2, 14336", text)
    if not top:
        raise SystemExit(f"{ll.name}: no lifted FLD updating TOP was found to check the status word by")
    if int(top.group(1)) != FX_FIELDS["swd"]:
        raise SystemExit(f"{ll.name}: lifted FLD updates TOP through field {top.group(1)}, "
                         f"not the status word's {FX_FIELDS['swd']}")
    return {name: offs[k] for name, k in FX_FIELDS.items()}


# Emulators broken on purpose, each the shape of a real or plausible defect, and
# each of which the comparison must catch. The first is the one the review of
# the first version found, and the harness of that version passed.
MUTATIONS = {
    "the FSAVE view of the FPU union": (
        "decltype(State::x87.fxsave32) &fx(State *s) { return s->x87.fxsave32; }\n"
        "const decltype(State::x87.fxsave32) &fx(const State *s) { return s->x87.fxsave32; }",
        "decltype(State::x87.fsave) &fx(State *s) { return s->x87.fsave; }\n"
        "const decltype(State::x87.fsave) &fx(const State *s) { return s->x87.fsave; }"),
    "fldenv not turning the stack for a new TOP": (
        "    if (turn) retop(s, (get16(4) >> 11) & 7u);", "    (void)turn;"),
    "the rounding read from the control word's stale copy": (
        "return static_cast<uint16_t>((fx(s).cwd.flat & 0xF3FFu) | 0x0040u\n"
        "                                 | rc_bits(std::fegetround()));",
        "return static_cast<uint16_t>(fx(s).cwd.flat | 0x0040u);"),
    "the control word's reserved bit 6 not read as 1": (
        "return static_cast<uint16_t>((fx(s).cwd.flat & 0xF3FFu) | 0x0040u\n"
        "                                 | rc_bits(std::fegetround()));",
        "return static_cast<uint16_t>((fx(s).cwd.flat & 0xF3BFu)\n"
        "                                 | rc_bits(std::fegetround()));"),
    "fnstenv not masking the exceptions after it stores": (
        "        fx(s).cwd.flat |= 0x003Fu;           /* FNSTENV then masks every exception */", ""),
    "psubsw's xmm form writing only its low half": (
        "    const uint32_t n = x ? 16u : 8u;", "    const uint32_t n = 8u;"),
    "psubsw's xmm form not checking its alignment": (
        "        if (x && (d.ea & 15u)) {", "        if (false) {"),
    "the condition codes read from the status word's stale copy": (
        "    return static_cast<uint16_t>((fx(s).swd.flat & 0xB8C0u)\n"
        "        | ((f.c0 & 1u) << 8)",
        "    return static_cast<uint16_t>((fx(s).swd.flat & 0xFFFFu)\n"
        "        | ((f.c0 & 1u) << 8)"),
}


def mutated(name: str, out_dir: Path) -> Path:
    src = (ROOT / "runtime" / "sa2_emulate.cpp").read_text()
    old, new = MUTATIONS[name]
    if src.count(old) != 1:
        raise SystemExit(f"mutation {name!r} no longer applies: the source it edits moved")
    p = out_dir / "sa2_emulate_mutated.cpp"
    p.write_text(src.replace(old, new, 1).replace('#include "sa2_emulate.h"',
                                                   f'#include "{ROOT / "runtime" / "sa2_emulate.h"}"'))
    return p


def all_cases(seed: int = 20260922) -> tuple[list[Case], list[str]]:
    rng = random.Random(seed)
    sites, skipped = site_cases(rng)
    return sites + synthetic_cases(rng) + refusal_cases(rng), skipped


def build_probe(out_dir: Path, emulator: Path | None = None) -> Path:
    """The probe over runtime/sa2_emulate.cpp, or over `emulator`, a mutated
    copy the tests use to show the comparison catching a broken emulator."""
    exe = out_dir / "emulate_probe"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(out_dir / "zc"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    strict = [] if emulator else ["-Werror"]
    done = subprocess.run([str(ZIG), "c++", "-target", "x86_64-linux-musl", "-O1", "-std=c++17",
                           "-mno-red-zone", "-Wall", "-Wextra", *strict,
                           "-I", str(ROOT / "runtime"), "-isystem", str(REMILL_INC),
                           str(ROOT / "harness" / "emulate_probe.cpp"),
                           str(emulator or ROOT / "runtime" / "sa2_emulate.cpp"), "-o", str(exe)],
                          capture_output=True, text=True, env=env)
    if done.returncode:
        raise SystemExit(f"the probe did not build:\n{done.stderr}")
    return exe


def build_continuation_probe(out_dir: Path, emulator: bool = True) -> Path:
    """harness/continuation_probe.cpp over the real runtime/remill_runtime.cpp,
    with sa2_emulate.cpp or, for the no-emulator control, without it."""
    exe = out_dir / ("continuation_probe" if emulator else "continuation_probe_noemu")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(out_dir / "zc"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    objs = []
    for c in ("sa2_trap.c", "function_table_impl.c"):
        o = out_dir / (c[:-2] + ".o")
        done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-w", "-c",
                               "-I", str(ROOT / "runtime"), str(ROOT / "runtime" / c), "-o", str(o)],
                              capture_output=True, text=True, env=env)
        if done.returncode:
            raise SystemExit(f"{c} did not build:\n{done.stderr}")
        objs.append(str(o))
    srcs = [str(ROOT / "harness" / "continuation_probe.cpp"), str(ROOT / "runtime" / "remill_runtime.cpp")]
    if emulator:
        srcs.append(str(ROOT / "runtime" / "sa2_emulate.cpp"))
    done = subprocess.run([str(ZIG), "c++", "-target", "x86_64-linux-musl", "-O2", "-std=c++17", "-w",
                           *([] if emulator else ["-DNO_EMULATOR"]),
                           "-I", str(ROOT / "runtime"), "-isystem", str(REMILL_INC), *srcs, *objs,
                           "-o", str(exe)], capture_output=True, text=True, env=env)
    if done.returncode:
        raise SystemExit(f"the continuation probe did not build:\n{done.stderr[-2000:]}")
    return exe


def run(probe: Path, cases: list[Case]) -> list[str]:
    lines = []
    for c in cases:
        pk = ",".join(f"{a:08x}:{b.hex()}" for a, b in c.pokes.items()) or "-"
        lines.append(f"{c.mode} {c.insn.hex()} {c.native.hex() if c.native else '-'} "
                     f"{pack_ctx(c).hex()} {pk}")
    fx = ",".join(f"{k}={v}" for k, v in lifted_fx_offsets().items())
    done = subprocess.run([str(probe)], input="\n".join(lines) + "\n", capture_output=True, text=True,
                          env=dict(os.environ, SA2_FX_OFFSETS=fx))
    if done.returncode:
        raise SystemExit(f"the probe exited {done.returncode}:\n{done.stderr[-2000:]}")
    out = done.stdout.splitlines()
    if len(out) != len(cases):
        raise SystemExit(f"{len(cases)} cases and {len(out)} results")
    return out


def compare(c: Case, line: str) -> list[str]:
    """Every way one case's two results disagree where the model says they must not."""
    nat, emu = (x.strip() for x in line.split("|", 1))
    e = emu.split()
    if e[1] == "setup-failed":
        return [f"{c.name}: the emulated side could not be set up: {emu}"]
    status, where = int(e[1]), int(e[2], 16)
    problems = []
    if status != c.expect:
        return [f"{c.name}: emulator status {status}, expected {c.expect} ({emu[-160:]})"]
    if c.expect == 2:
        if c.expect_fault is not None and where != c.expect_fault:
            problems.append(f"{c.name}: faulted at {where:#010x}, expected {c.expect_fault:#010x}")
        if len(e) > 4 and e[4] != "-" and not e[4].startswith("why="):
            problems.append(f"{c.name}: a faulting instruction wrote {e[4][:80]}")
        return problems
    if c.expect == 1:
        if c.expect_why and c.expect_why not in emu:
            problems.append(f"{c.name}: refused, but not for {c.expect_why!r}: {emu[-160:]}")
        return problems
    if where != CODE + len(c.insn):
        problems.append(f"{c.name}: continues at {where:#010x}, expected {CODE + len(c.insn):#010x}")
    ewin = e[4] if len(e) > 4 else "-"
    if c.mode == "e":
        first = min(_changes(ewin)) if ewin != "-" else None
        if first != c.expect_write:
            problems.append(f"{c.name}: stored at {first if first is None else hex(first)}, "
                            f"expected {c.expect_write:#010x}")
        return problems
    n = nat.split()
    got, want = unpack_ctx(bytes.fromhex(e[3])), unpack_ctx(bytes.fromhex(n[1]))
    nwin = n[2] if len(n) > 2 else "-"
    for k in range(8):
        if got["gpr"][k] != want["gpr"][k]:
            problems.append(f"{c.name}: gpr{k} {got['gpr'][k]:#010x}, native {want['gpr'][k]:#010x}")
    if c.mode == "m":
        for k in range(8):
            if got["mm"][k] != want["mm"][k]:
                problems.append(f"{c.name}: mm{k} {got['mm'][k]:016x}, native {want['mm'][k]:016x}")
            if got["xmm"][k] != want["xmm"][k]:
                problems.append(f"{c.name}: xmm{k} {got['xmm'][k].hex()}, native {want['xmm'][k].hex()}")
        if ewin != nwin:
            problems.append(f"{c.name}: memory {ewin[:120]}, native {nwin[:120]}")
        return problems
    # x87: the stored bytes but the tag word, then the state after
    if ewin != nwin:
        ew, nw = _changes(ewin), _changes(nwin)
        for a in sorted(set(ew) | set(nw)):
            if ew.get(a) == nw.get(a):
                continue
            off = a - (c.ea or 0)
            if c.kind in STORES and off in (8, 9):
                continue                       # the tag word, which remill does not keep
            problems.append(f"{c.name}: byte {a:#010x} (+{off}) {ew.get(a)}, native {nw.get(a)}")
    gi, wi = got["x87"], want["x87"]
    if gi[0:2] != wi[0:2]:
        problems.append(f"{c.name}: control word {gi[0:2].hex()}, native {wi[0:2].hex()}")
    if gi[4:6] != wi[4:6]:
        problems.append(f"{c.name}: status word {gi[4:6].hex()}, native {wi[4:6].hex()}")
    if gi[12:28] != wi[12:28]:
        problems.append(f"{c.name}: pointers {gi[12:28].hex()}, native {wi[12:28].hex()}")
    if got["mxcsr"] != want["mxcsr"]:
        problems.append(f"{c.name}: MXCSR {got['mxcsr']:#010x}, native {want['mxcsr']:#010x}")
    for k in range(8):
        if gi[28 + 10 * k:38 + 10 * k] != wi[28 + 10 * k:38 + 10 * k]:
            problems.append(f"{c.name}: ST({k}) {gi[28+10*k:38+10*k].hex()}, native {wi[28+10*k:38+10*k].hex()}")
    return problems


def _changes(s: str) -> dict[int, int]:
    out = {}
    if s == "-":
        return out
    for item in s.split(","):
        a, h = item.split(":")
        for k, byte in enumerate(bytes.fromhex(h)):
            out[int(a, 16) + k] = byte
    return out


def main() -> int:
    import tempfile
    cases, skipped = all_cases()
    with tempfile.TemporaryDirectory() as td:
        probe = build_probe(Path(td))
        results = run(probe, cases)
    problems = [p for c, r in zip(cases, results) for p in compare(c, r)]
    kinds = {}
    for c in cases:
        kinds[c.kind or "refusal"] = kinds.get(c.kind or "refusal", 0) + 1
    print(f"{len(cases)} cases {kinds}; {len(skipped)} site(s) skipped; {len(problems)} problem(s)")
    for s in skipped[:10]:
        print("  skipped", s)
    for p in problems[:40]:
        print("  PROBLEM", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
