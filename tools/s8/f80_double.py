#!/usr/bin/env python3
"""f80_double: the double mode of the x87 legalisation, the fast mode for the all-platform builds.

Subtitle: `f80_double.py rewrite IN.ll OUT.ll` makes a folded module's x87 arithmetic binary64:
every x86_fp80 value becomes a double, the 80-bit layout stays (as i80, as
tools/s7/f80_legalize.py's exact mode keeps it), and what the pass cannot translate it refuses
by name. `f80_double.py fold-check` runs it over every module the replay link reads and lists
what it refused and rewrote. The shims it calls are tools/s8/f80_shims_double.c.

    .venv/bin/python tools/s8/f80_double.py rewrite IN.ll OUT.ll [--stats stats.json]
    .venv/bin/python tools/s8/f80_double.py fold-check [--jobs 6] [--json out.json]

tools/s8/android_game.py `compile --f80 double` and tools/s8/perf_f80.py `build --mode double`
run it in place of the exact pass.

Why a module of its own: tools/s7/f80_legalize.py and tools/s7/f80_shims.c are pinned by sha256
in the arm64-linux target's receipts (Documentation/receipts/s7-target-arm64-linux-v1.json,
s7-arm64-linux-build-v1.json, s7-arm64-replays-v1.json), and an edit there unmeets that G7
target until every aarch64 module is rebuilt and the qemu replays rerun. So this pass imports
the exact pass's patterns and helpers and leaves both files byte for byte as they are.

What the double mode is. The x87's registers hold binary64 values, and its arithmetic is the
host's double arithmetic: fadd/fsub/fmul/fdiv/fcmp/fneg, phi, select, load and store keep their
form with the type swapped; fpext from float, sitofp and fptrunc to float are the double forms;
fpext from and fptrunc to double are no-ops (freeze); fabs, copysign and sqrt are the f64
intrinsics; the rest are calls into the shims (`sa2_df80_*`). Every alloca, GEP and named type
keeps x86_fp80's 16-byte slot, as i80, and remill's float80_t slots (the `State` registers and
the temporaries remill's semantics copy them through) hold the double's eight bytes and two
zero bytes. So the only 80-bit images are the guest's own m80 operands: FLD m80 and FSTP m80
call `__remill_read_memory_f80_dbl` and `__remill_write_memory_f80_dbl`
(runtime/remill_runtime.cpp), which convert, and the runtime's register accessors convert
whenever the shims' marker `sa2_f80_state_double` is linked.

Why the results match the x87 in nearly every case: the game runs at PC24 (control word 0x007F,
runtime/sa2_d3d9.c CreateDevice) and the PC24 pass rounds every arithmetic result to 24 bits.
For operands of at most 24 significant bits (every float the game loads, every PC24 result) a
double operation followed by that rounding is the correctly rounded 24-bit result, because
53 >= 2 * 24 + 2 (double rounding is innocuous), and so is the x87's 64-then-24 path. The two
differ only for wider operands (a double from memory, a 64-bit constant such as FLDPI, a fild
of more than 24 bits) at a tie of the second rounding, beyond the double exponent range, and in
the status word's exception bits; the replays measure it. The PC24 rounding is inline integer
code on the double's bits under every RC (DBL_HELPERS); only a double denormal goes to
`sa2_df80_pc_round_slow`, which rounds the significand under the control word's RC and keeps
the exponent, as the x87 does.

Integer reads of a register's bytes. remill's semantics read a float80_t's bytes as integers
in two places, both kept exact here:

  * the sign: `load i80` or `bitcast x86_fp80 ... to i80` whose every use is `lshr i80 %x, 79`,
    `icmp slt i80 %x, 0` or `icmp sgt i80 %x, -1` becomes the double's sign moved to bit 79.
    Any other compare of the image is refused by name: `sgt 0`, `sle 0`, `sge -1`, `slt -1`
    and the rest depend on its other 79 bits, which the double's image does not hold;
  * the pieces: SROA splits a float80_t copy into `load i64` (bytes 0-7) and `load i16`
    (bytes 8-9), and remill's FLD m80 and FRNDINT test the pieces for a signalling NaN
    (`and i16 %hi, 32767` / `icmp eq ..., 32767`, `and i64 %lo, 1<<62`, `icmp slt i64 %lo, 0`);
    a product's low bits are tested for the precision flag. A piece stored back into a slot at its
    own offset is a copy and is left alone. Every other use reads the 80-bit image of the double
    the slot holds instead, computed exactly from the double's bits (`__sa2_dimg_lo`,
    `__sa2_dimg_hi`): the integer bit and the fraction, the sign and the biased exponent, a
    double denormal normalised, a NaN's quiet bit kept as it is. So the signalling-NaN test
    reads what it reads in the exact mode, given that the m80 load keeps a signalling NaN
    signalling (runtime/remill_runtime.cpp). An integer store into a slot of anything but a copy,
    zero, or remill's undefined value (`__remill_undefined_f80`, zero in the runtime), and a read
    of a piece whose image cannot be computed (another width or offset, a phi of high words) are
    refused by name.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import hashlib
import json
import math
import re
import struct
import sys
import time
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "s7"))

import f80_legalize as F  # noqa: E402  the exact pass, pinned: read, never edited
from f80_legalize import (  # noqa: E402
    FENV, FENV_REFUSED, FMF, MARKER, NAME, OPND, PURE, RC_READ, RE_BIN, RE_CALL, RE_DECLARE,
    RE_DEFINE, RE_F80C, RE_FCMP, RE_FNEG, RE_FPEXT, RE_FPTRUNC, RE_FTOI, RE_ITOF, RE_TYPEDEF,
    SCALAR, TAIL, TRANSCENDENTAL, Refused, to_i80)

SELF = "tools/s8/f80_double.py"
SHIMS_C = HERE / "f80_shims_double.c"
ZIG = F.ZIG
SHIM_PREFIXES = F.SHIM_PREFIXES + ("sa2_df80_",)

DBL_MARKER = ("; sa2-f80-legalize v4 double: x86_fp80 values are binary64, the 80-bit layout "
              "stays (tools/s8/f80_double.py)")
QUIET80 = 1 << 62
QUIET64 = 1 << 51
PC_HELPER = "__sa2_pc_round"
DBL_INTRINSIC = {"llvm.fabs.f80": "llvm.fabs.f64", "fabsl": "llvm.fabs.f64",
                 "llvm.copysign.f80": "llvm.copysign.f64", "copysignl": "llvm.copysign.f64",
                 "llvm.sqrt.f80": "llvm.sqrt.f64", "sqrtl": "llvm.sqrt.f64"}
DBL_SHIM_CALL = {"nearbyintl": "sa2_df80_rint", "rintl": "sa2_df80_rint",
                 "llvm.nearbyint.f80": "sa2_df80_rint", "llvm.rint.f80": "sa2_df80_rint",
                 "truncl": "sa2_df80_trunc", "llvm.trunc.f80": "sa2_df80_trunc",
                 "floorl": "sa2_df80_floor", "llvm.floor.f80": "sa2_df80_floor",
                 "ceill": "sa2_df80_ceil", "llvm.ceil.f80": "sa2_df80_ceil",
                 "fmodl": "sa2_df80_fmod", "remainderl": "sa2_df80_remainder",
                 **{n: "sa2_df80_" + n[:-1] for n in TRANSCENDENTAL}}
DBL_DECLS = {
    "llvm.fabs.f64": ("double", "double", PURE),
    "llvm.copysign.f64": ("double", "double, double", PURE),
    "llvm.sqrt.f64": ("double", "double", PURE),
    "llvm.ctlz.i64": ("i64", "i64, i1", PURE),
    "sa2_df80_pc_round_slow": ("double", "double, i16", PURE),
    "sa2_df80_round_int": ("double", "double, i16", PURE),
    "sa2_df80_fistp_i16": ("i16", "double, i16", PURE),
    "sa2_df80_fistp_i32": ("i32", "double, i16", PURE),
    "sa2_df80_fistp_i64": ("i64", "double, i16", PURE),
    "sa2_df80_to_i16": ("i16", "double", PURE), "sa2_df80_to_i32": ("i32", "double", PURE),
    "sa2_df80_to_i64": ("i64", "double", PURE),
    "sa2_df80_rint": ("double", "double", RC_READ), "sa2_df80_trunc": ("double", "double", PURE),
    "sa2_df80_floor": ("double", "double", PURE), "sa2_df80_ceil": ("double", "double", PURE),
    "sa2_df80_fmod": ("double", "double, double", PURE),
    "sa2_df80_remainder": ("double", "double, double", PURE),
}
for _n, _a in TRANSCENDENTAL.items():
    DBL_DECLS["sa2_df80_" + _n[:-1]] = ("double", ", ".join(["double"] * _a), "nounwind")

# The helpers the PC24 pass spliced in, as the double mode defines them. The rounding helper is
# inline: PC53 and PC64 are the identity on a double, and PC24 rounds the double's significand
# to 24 bits on its bit pattern under the control word's RC: add an increment and clear the 29
# dropped bits, the increment being half an ulp of the 24-bit result less one plus the kept
# lowest bit at nearest (ties to even), an ulp less one when rounding away from zero (up for a
# positive value, down for a negative one) and nothing toward zero; the carry into the exponent
# is the encoding's own. That is exact for every normal double and zero, under every RC, and
# keeps the exponent, as the x87 does; infinities and NaNs are kept. Only a double denormal is
# `sa2_df80_pc_round_slow`. The game runs most of its arithmetic at PC24 under RC toward zero
# (control word 0x0C7F, measured), where any double rounding is innocuous: truncating to 53 bits
# and then to 24 is truncating to 24. FRNDINT and FIST are calls.
DBL_HELPERS = {
    PC_HELPER: [
        "define internal double @__sa2_pc_round(double %v, i16 %cw) alwaysinline {",
        "entry:",
        "  %pc = and i16 %cw, 768",
        "  %is24 = icmp eq i16 %pc, 0",
        "  br i1 %is24, label %p24, label %keep",
        "keep:",
        "  ret double %v",
        "p24:",
        "  %b = bitcast double %v to i64",
        "  %abs = and i64 %b, 9223372036854775807",
        "  %normal = icmp uge i64 %abs, 4503599627370496",
        "  %zero = icmp eq i64 %abs, 0",
        "  %okexp = or i1 %normal, %zero",
        "  br i1 %okexp, label %r24, label %slow",
        "r24:",
        "  %rcs = lshr i16 %cw, 10",
        "  %rc = and i16 %rcs, 3",
        "  %near = icmp eq i16 %rc, 0",
        "  %down = icmp eq i16 %rc, 1",
        "  %up = icmp eq i16 %rc, 2",
        "  %neg = icmp slt i64 %b, 0",
        "  %away = select i1 %neg, i1 %down, i1 %up",
        "  %adir = select i1 %away, i64 536870911, i64 0",
        "  %lsb = lshr i64 %b, 29",
        "  %l1 = and i64 %lsb, 1",
        "  %anear = add i64 %l1, 268435455",
        "  %add = select i1 %near, i64 %anear, i64 %adir",
        "  %sum = add i64 %b, %add",
        "  %rb = and i64 %sum, -536870912",
        "  %fin = icmp ult i64 %abs, 9218868437227405312",
        "  %r = bitcast i64 %rb to double",
        "  %o = select i1 %fin, double %r, double %v",
        "  ret double %o",
        "slow:",
        "  %s = call double @sa2_df80_pc_round_slow(double %v, i16 %cw)",
        "  ret double %s",
        "}"],
    "__sa2_x87_round_int": [
        "define internal double @__sa2_x87_round_int(double %v, i16 %cw) alwaysinline {",
        "  %r = call double @sa2_df80_round_int(double %v, i16 %cw)",
        "  ret double %r",
        "}"],
    **{f"__sa2_fistp_{w}": [
        f"define internal {w} @__sa2_fistp_{w}(double %v, i16 %cw) alwaysinline {{",
        f"  %r = call {w} @sa2_df80_fistp_{w}(double %v, i16 %cw)",
        f"  ret {w} %r",
        "}"] for w in ("i16", "i32", "i64")},
}

# The 80-bit image of a double, from its bits: what the x87 holds after FLD m64 of it, except
# that a NaN keeps its quiet bit as it is (so a signalling NaN the m80 load kept signalling reads
# as one). `__sa2_dimg_lo` is bytes 0-7 (the integer bit and the fraction), `__sa2_dimg_hi`
# bytes 8-9 (the sign and the biased exponent). A normal double: integer bit, fraction << 11,
# exponent + 15360 (16383 - 1023); a denormal one normalised: its fraction shifted up by its
# leading zeros, exponent 15372 - those zeros (the top bit's power, -1074 + 63 - zeros, biased
# by 16383); zero: zero; infinity and NaN: integer bit, fraction << 11, exponent 0x7FFF.
# runtime/remill_runtime.cpp's sa2_f80_soft_from_f64 is the same function except for a NaN's
# quiet bit, which it sets; Documentation/scripts/tests/test_f80_double.py holds them to that.
IMG_HELPERS = {
    "__sa2_dimg_lo": [
        "define internal i64 @__sa2_dimg_lo(i64 %b) alwaysinline {",
        "  %e = lshr i64 %b, 52",
        "  %e11 = and i64 %e, 2047",
        "  %f = and i64 %b, 4503599627370495",
        "  %den = icmp eq i64 %e11, 0",
        "  %fs = shl i64 %f, 11",
        "  %n = or i64 %fs, -9223372036854775808",
        "  %lz = call i64 @llvm.ctlz.i64(i64 %f, i1 false)",
        "  %zero = icmp eq i64 %f, 0",
        "  %d = shl i64 %f, %lz",
        "  %d0 = select i1 %zero, i64 0, i64 %d",
        "  %r = select i1 %den, i64 %d0, i64 %n",
        "  ret i64 %r",
        "}"],
    "__sa2_dimg_hi": [
        "define internal i16 @__sa2_dimg_hi(i64 %b) alwaysinline {",
        "  %s = lshr i64 %b, 48",
        "  %s16 = trunc i64 %s to i16",
        "  %sign = and i16 %s16, -32768",
        "  %e = lshr i64 %b, 52",
        "  %e11 = and i64 %e, 2047",
        "  %f = and i64 %b, 4503599627370495",
        "  %den = icmp eq i64 %e11, 0",
        "  %max = icmp eq i64 %e11, 2047",
        "  %en = add i64 %e11, 15360",
        "  %lz = call i64 @llvm.ctlz.i64(i64 %f, i1 false)",
        "  %ed = sub i64 15372, %lz",
        "  %zero = icmp eq i64 %f, 0",
        "  %ed0 = select i1 %zero, i64 0, i64 %ed",
        "  %e1 = select i1 %den, i64 %ed0, i64 %en",
        "  %e2 = select i1 %max, i64 32767, i64 %e1",
        "  %e16 = trunc i64 %e2 to i16",
        "  %r = or i16 %sign, %e16",
        "  ret i16 %r",
        "}"],
}

RE_FREEZE_SRC = re.compile(rf"^(\s+)({NAME}) = (?:fpext double ({SCALAR}) to x86_fp80|"
                           rf"fptrunc x86_fp80 ({OPND}) to double){TAIL}$")
RE_I80_LOAD = re.compile(rf"^(\s+)({NAME}) = load i80, ptr ({NAME}), align (\d+)(.*)$")
RE_I80_TOBITS = re.compile(rf"^(\s+)({NAME}) = bitcast x86_fp80 ({OPND}) to i80(.*)$")
RE_I80_QUIET = re.compile(rf"^(\s+)({NAME}) = or i80 ({NAME}), {QUIET80}(.*)$")
RE_I80 = re.compile(r"(?<![.\w])i80\b")
RE_NAMES = re.compile(NAME)
RE_ALLOCA = re.compile(rf"^\s+({NAME}) = alloca ([^,]+?)(?:, align \d+)?\s*$")
RE_GEP = re.compile(rf"^\s+({NAME}) = getelementptr (?:inbounds |nuw |nusw )*([^,]+), "
                    rf"ptr ({NAME})((?:, i\d+ -?\d+)*)\s*$")
RE_INT_LOAD = re.compile(rf"^(\s+)({NAME}) = load (?:volatile )?(i8|i16|i32|i64), ptr ({NAME})"
                         rf"(?:, align (\d+))?")
RE_INT_STORE = re.compile(rf"^\s+store (?:volatile )?(i8|i16|i32|i64|i80) ({NAME}|-?\d+), "
                          rf"ptr ({NAME})")
RE_INT_PHI = re.compile(rf"^\s+({NAME}) = phi (i8|i16|i32|i64) (.*)$")
RE_INT_SELECT = re.compile(rf"^\s+({NAME}) = select i1 [^,]+, (i8|i16|i32|i64) ([^,]+), "
                           rf"(?:i8|i16|i32|i64) (\S+)$")
RE_UNDEF_F80 = re.compile(rf"^\s+({NAME}) = (?:tail |notail )?call \{{ i64, i16 \}} "
                          rf"@__remill_undefined_f80\(\)")
RE_EXTRACT = re.compile(rf"^\s+({NAME}) = extractvalue \{{ i64, i16 \}} ({NAME}), ([01])$")
# The integer operations that consume a piece's value (and so read the 80-bit image).
RE_READ = re.compile(rf"^\s+{NAME} = (?:and|or|xor|lshr|ashr|shl|add|sub|icmp \w+|trunc|zext|"
                     rf"sext) ")
RE_MEM = re.compile(r"call (?:ptr|void) @(?:llvm\.(?:memcpy|memmove|memset)\.\S+|memcpy|memmove|"
                    r"memset)\((.*)\)")
SIGN_READ = (r"^\s+{r} = lshr i80 {n}, 79$", r"^\s+{r} = icmp slt i80 {n}, 0$",
             r"^\s+{r} = icmp sgt i80 {n}, -1$")
# remill's State: the x87 register stack is its field 7, [8 x { [6 x i8], %struct.float80_t }].
STATE_TYPES = {"%struct.State", "%struct.X86State"}
ST_FIELD = [0, 0, 7]
# The piece of a float80_t each integer load is: bytes 0-7 (the double), bytes 8-9.
LO, HI = (64, 0), (16, 8)


def f80_double_hex(digits: str) -> str:
    """The binary64 an 80-bit constant (`0xK` and 20 hex digits) holds, rounded to nearest-even,
    as LLVM's 16-digit hex double: overflow is infinity, underflow zero, a NaN keeps its quiet
    bit and its payload's top 51 bits."""
    w = int(digits, 16)
    sign, e, m = w >> 79, (w >> 64) & 0x7FFF, w & ((1 << 64) - 1)
    if e == 0x7FFF:
        if (m << 1) & ((1 << 64) - 1) == 0:
            bits = 0x7FF0000000000000
        else:
            bits = 0x7FF0000000000000 | ((m >> 11) & ((1 << 52) - 1)) | (QUIET64 if m & QUIET80 else 0)
            if bits & ((1 << 52) - 1) == 0:
                bits |= 1                    # a NaN whose payload sat below bit 11: still a NaN
    else:
        v = Fraction(m) * Fraction(2) ** ((e or 1) - 16383 - 63)
        try:
            d = float(v)
        except OverflowError:
            d = math.inf
        bits = struct.unpack("<Q", struct.pack("<d", d))[0]
    return f"0x{bits | (sign << 63):016X}"


def _dbl(line: str) -> str:
    """A line's x86_fp80 values as doubles: the type, and each 0xK constant's binary64."""
    line = RE_F80C.sub(lambda m: f80_double_hex(m.group(1)), line)
    return re.sub(r"\bx86_fp80\b", "double", line)


def _name_re(name: str) -> re.Pattern:
    return re.compile(re.escape(name) + r"(?![-\w.$\"])")


def _helper_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Each PC24-pass helper's definition: name -> (first line, closing brace line)."""
    spans = {}
    for i, l in enumerate(lines):
        m = RE_DEFINE.match(l)
        if m and m.group(2) in DBL_HELPERS and re.search(r"\b(?:internal|private)\b", m.group(1)):
            j = i
            while j < len(lines) and lines[j] != "}":
                j += 1
            if j == len(lines):
                raise Refused(f"{m.group(2)}: no closing brace")
            spans[m.group(2)] = (i, j)
    return spans


def slot_types(lines: list[str]) -> set[str]:
    """The types whose alloca is an x87 slot holding a value at offset 0: float80_t, x86_fp80,
    the [10 x i8] SROA leaves of them, and every named type that only wraps one of these
    (remill's `%union.anon.11 = type { %struct.float80_t }`, `%union.union_ld = type
    { x86_fp80 }`)."""
    defs = {m.group(1): m.group(2).strip() for l in lines if (m := RE_TYPEDEF.match(l))}
    out = {"%struct.float80_t", "x86_fp80", "[10 x i8]"}
    changed = True
    while changed:
        changed = False
        for n, body in defs.items():
            inner = re.fullmatch(r"\{\s*(.+?)\s*\}", body)
            if n not in out and inner and inner.group(1) in out:
                out.add(n)
                changed = True
    return out


class Fn:
    """One function's integer accesses to x87 slots, analysed before the rewrite: which lines
    are the image's sign or NaN-quieting reads, which piece reads get the 80-bit image of the
    double, and what is refused."""

    def __init__(self, body: list[str], first: int, stypes: set[str], fresh: list[int],
                 st: dict) -> None:
        self.body, self.first, self.stypes, self.fresh, self.st = body, first, stypes, fresh, st
        self.i80: dict[int, str] = {}           # absolute line -> its i80 rule
        self.subst: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
        self.before: dict[int, list[str]] = collections.defaultdict(list)
        self.after: dict[int, list[str]] = collections.defaultdict(list)
        self.helpers: set[str] = set()
        self.index: dict[str, list[int]] = collections.defaultdict(list)
        for k, l in enumerate(body):
            for nm in set(RE_NAMES.findall(l)):
                self.index[nm].append(k)
        self.slots = self._slots()
        self._i80()
        self._pieces()

    def name(self, tag: str) -> str:
        self.fresh[0] += 1
        return f"%sa2d.{self.fresh[0]}.{tag}"

    def uses(self, name: str, k: int) -> list[int]:
        """The lines other than `k` that name the value."""
        pat = _name_re(name)
        return [u for u in self.index.get(name, ()) if u != k and pat.search(self.body[u])]

    def where(self, k: int) -> str:
        return self.body[k].strip()[:200]

    # -- slots
    def _slots(self) -> dict[str, tuple[str, int | None]]:
        """Pointer -> (root, byte offset in its float80_t, None when not known)."""
        slots: dict[str, tuple[str, int | None]] = {}
        for l in self.body:
            if m := RE_ALLOCA.match(l):
                if m.group(2).strip() in self.stypes:
                    slots[m.group(1)] = (m.group(2).strip(), 0)
                continue
            m = RE_GEP.match(l)
            if not m:
                continue
            res, ty, base, idx = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
            ids = [int(x.split()[1]) for x in idx.split(",")[1:]] if idx else []
            if ty in STATE_TYPES and ids[:3] == ST_FIELD:
                rest = ids[3:]
                off = (0 if len(rest) in (3, 4) and rest[0] == 0 and rest[2] == 1
                       and rest[3:] in ([], [0]) else
                       rest[4] if len(rest) == 5 and rest[0] == 0 and rest[2] == 1
                       and rest[3] == 0 else None)
                slots[res] = ("st", off)
                continue
            if base not in slots:
                continue
            root, off = slots[base]
            if off is None:
                pass
            elif ty in ("%struct.float80_t", "[10 x i8]") and len(ids) == 3 and ids[:2] == [0, 0]:
                off += ids[2]
            elif ty == "[10 x i8]" and len(ids) == 2 and ids[0] == 0:
                off += ids[1]
            elif ty == "i8" and len(ids) == 1:
                off += ids[0]
            elif not all(x == 0 for x in ids) or not (ty in self.stypes or ty == "i8"):
                off = None
            slots[res] = (root, off)
        return slots

    # -- i80: the sign and the NaN-quieting reads
    def _sign_uses(self, name: str, k: int) -> list[int]:
        """The sign reads of an 80-bit image `name` defined at line k; anything else refused."""
        uses = self.uses(name, k)
        if not uses:
            raise Refused(f"an 80-bit image with no use: {self.where(k)}")
        n = re.escape(name)
        for u in uses:
            line = self.body[u]
            if not any(re.match(p.format(r=NAME, n=n), line) for p in SIGN_READ):
                raise Refused(f"`{line.strip()[:160]}` reads the 80-bit image of a value beyond "
                              f"its sign (the sign reads are `icmp slt i80 %x, 0`, `icmp sgt "
                              f"i80 %x, -1` and `lshr i80 %x, 79`)")
        return uses

    def _i80(self) -> None:
        for k, line in enumerate(self.body):
            if "i80" not in line:
                continue
            a = self.first + k
            if m := RE_I80_LOAD.match(line):
                res = m.group(2)
                uses = self.uses(res, k)
                if len(uses) == 1 and (q := RE_I80_QUIET.match(self.body[uses[0]])) \
                        and q.group(3) == res:
                    self.i80[a] = "quiet-load"
                    self.i80[self.first + uses[0]] = "quiet-or"
                    for s in self.uses(q.group(2), uses[0]):
                        sm = RE_INT_STORE.match(self.body[s])
                        if not (sm and sm.group(1) == "i80" and sm.group(2) == q.group(2)
                                and self.slots.get(sm.group(3), ("", None))[1] == 0):
                            raise Refused(f"the NaN-quieting `or` of an 80-bit image is used "
                                          f"beyond a store into an x87 slot: {self.where(s)}")
                        self.i80[self.first + s] = "quiet-store"
                    continue
                self.i80[a] = "sign-load"
                self._sign_marks(res, k)
            elif m := RE_I80_TOBITS.match(line):
                self.i80[a] = "sign-bitcast"
                self._sign_marks(m.group(2), k)

    def _sign_marks(self, name: str, k: int) -> None:
        for u in self._sign_uses(name, k):
            line = self.body[u]
            self.i80[self.first + u] = "sign-read"
            if " = lshr i80 " in line:
                bit = line.split("=")[0].strip()
                for t in self.uses(bit, u):
                    if not re.match(rf"^\s+{NAME} = trunc i80 {re.escape(bit)} to i(?:1|8|16|32|64)$",
                                    self.body[t]):
                        raise Refused(f"the sign bit of an 80-bit image is used beyond a trunc: "
                                      f"{self.where(t)}")
                    self.i80[self.first + t] = "sign-read"

    # -- the pieces: integer loads of a float80_t's bytes
    def _pieces(self) -> None:
        body = self.body
        piece: dict[str, tuple[tuple[int, int | None], int, str | None]] = {}
        # name -> ((width, offset), defining line, pointer of a load or None for a phi/select)
        for k, l in enumerate(body):
            m = RE_INT_LOAD.match(l)
            if m and m.group(4) in self.slots:
                piece[m.group(2)] = ((int(m.group(3)[1:]), self.slots[m.group(4)][1]), k,
                                     m.group(4))
        merged: dict[str, tuple[int, list[str]]] = {}
        for k, l in enumerate(body):
            if m := RE_INT_PHI.match(l):
                vals = re.findall(rf"\[\s*({NAME}|-?\d+|undef|poison)\s*,", m.group(3))
                merged[m.group(1)] = (k, vals)
            elif m := RE_INT_SELECT.match(l):
                merged[m.group(1)] = (k, [m.group(3).strip(), m.group(4).strip()])
        changed = True
        while changed:
            changed = False
            for res, (k, vals) in merged.items():
                if res in piece:
                    continue
                kinds = {piece[v][0] for v in vals if v in piece}
                if kinds and all(v in piece for v in vals) and len(kinds) == 1:
                    piece[res] = (kinds.pop(), k, None)
                    changed = True
        for res, (k, vals) in merged.items():
            if res not in piece and any(v in piece for v in vals):
                raise Refused(f"a phi or select mixes an x87 slot's bytes with other values: "
                              f"{self.where(k)}")
        zero = {m.group(1) for l in body if (m := RE_UNDEF_F80.match(l))}
        undef = {m.group(1) for l in body if (m := RE_EXTRACT.match(l)) and m.group(2) in zero}
        # every integer store into a slot: a copy of a piece at its own place, zero, or undef
        for k, l in enumerate(body):
            m = RE_INT_STORE.match(l)
            if not m or m.group(3) not in self.slots:
                continue
            if m.group(1) == "i80":
                if self.i80.get(self.first + k) != "quiet-store":
                    raise Refused(f"an i80 store into an x87 slot of a value that is not its "
                                  f"quieted copy: {self.where(k)}")
                continue
            want = (int(m.group(1)[1:]), self.slots[m.group(3)][1])
            v = m.group(2)
            if v == "0" or v in undef:
                continue
            if v in piece and piece[v][0] == want and want[1] is not None:
                continue
            raise Refused(f"an integer store into an x87 register or temporary of a value that "
                          f"is not a copy of its own bytes: {self.where(k)}")
        for k, l in enumerate(body):
            if (m := RE_MEM.search(l)) and any(p in self.slots for p in re.findall(NAME, m.group(1))):
                size = re.search(r", i64 (?:noundef )?(\d+)", m.group(1))
                fill = re.search(r"\bi(?:8|32) (?:noundef )?(-?\d+)", m.group(1))
                ptrs = re.findall(rf"ptr [^%,]*({NAME})", m.group(1))
                if not size or size.group(1) not in ("10", "16") or \
                        ("memset" in l and (not fill or fill.group(1) != "0")) or \
                        ("memset" not in l and not all(p in self.slots for p in ptrs[:2])):
                    raise Refused(f"a memory call over part of an x87 slot, between one and other "
                                  f"memory, or filling one with other than zero: {self.where(k)}")
        # the reads: every use of a piece that is not a copy or a merge
        for v, (kind, k, ptr) in piece.items():
            reads = []
            for u in self.uses(v, k):
                line = body[u]
                sm = RE_INT_STORE.match(line)
                if sm and sm.group(2) == v:
                    if sm.group(3) not in self.slots:
                        raise Refused(f"an x87 slot's bytes are stored outside the x87 slots: "
                                      f"{self.where(u)}")
                    continue                     # checked above: a copy into a slot
                if (mm := RE_INT_PHI.match(line) or RE_INT_SELECT.match(line)) \
                        and mm.group(1) in piece:
                    continue                     # a merge of pieces: its own uses are checked
                if not RE_READ.match(line):
                    raise Refused(f"`{line.strip()[:160]}` uses an x87 slot's bytes in a form "
                                  f"the double mode has no rule for: {self.where(k)}")
                reads.append(u)
            if not reads:
                continue
            if kind == LO and ptr is not None:
                img = self.name("lo")
                self.after[self.first + k].append(f"  {img} = call i64 @__sa2_dimg_lo(i64 {v})")
                for r in reads:
                    self.subst[self.first + r].append((v, img))
            elif kind == LO:                     # a merge of bytes 0-7: the image at each read
                for r in reads:
                    img = self.name("lo")
                    self.before[self.first + r].append(
                        f"  {img} = call i64 @__sa2_dimg_lo(i64 {v})")
                    self.subst[self.first + r].append((v, img))
            elif kind == HI and ptr is not None:
                # bytes 8-9 hold nothing of the double: its image is computed from bytes 0-7,
                # read beside them while the slot holds what they were loaded from
                p, d, img = self.name("p"), self.name("d"), self.name("hi")
                self.after[self.first + k] += [
                    f"  {p} = getelementptr i8, ptr {ptr}, i64 -8",
                    f"  {d} = load i64, ptr {p}, align 1",
                    f"  {img} = call i16 @__sa2_dimg_hi(i64 {d})"]
                for r in reads:
                    self.subst[self.first + r].append((v, img))
            else:
                raise Refused(f"`{body[reads[0]].strip()[:160]}` reads bytes {kind[1]} (i{kind[0]}) "
                              f"of an x87 slot, whose image this mode cannot give there: "
                              f"{self.where(k)}")
            self.helpers.add("__sa2_dimg_lo" if kind == LO else "__sa2_dimg_hi")
            self.st["image_reads"]["lo" if kind == LO else "hi"] += len(reads)


def legalize(text: str) -> tuple[str, dict]:
    """The double mode's module (see the docstring): every x86_fp80 value a double, the 80-bit
    layout kept as i80, and what was done. Raises Refused for a form it has no rule for."""
    st: dict = {"mode": "double", "retyped": 0, "freeze": 0, "calls": {}, "fenv": {},
                "helpers": [], "quiet_nan": 0, "sign_reads": 0, "m80": 0,
                "image_reads": {"lo": 0, "hi": 0},
                "declarations_dropped": [], "replaced_callees": set()}
    if text.startswith(DBL_MARKER):
        st["already_legal"] = True
        st["replaced_callees"] = []
        return text, st
    if text.startswith(MARKER):
        raise Refused("the module was legalized in the exact mode")
    lines = text.split("\n")
    internal = {m.group(2) for l in lines if (m := RE_DEFINE.match(l))
                and re.search(r"\b(?:internal|private)\b", m.group(1))}
    spans = _helper_spans(lines)
    skip: dict[int, list[str] | None] = {}
    for name, (i, j) in spans.items():
        skip[i] = DBL_HELPERS[name]
        for k in range(i + 1, j + 1):
            skip[k] = None
        st["helpers"].append(name)
    stypes = slot_types(lines)
    fresh = [0]
    i80: dict[int, str] = {}
    subst: dict[int, list[tuple[str, str]]] = {}
    before: dict[int, list[str]] = {}
    after: dict[int, list[str]] = {}
    img_used: set[str] = set()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("define") or i in skip:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j] != "}":
            j += 1
        fn = Fn(lines[i + 1:j], i + 1, stypes, fresh, st)
        i80.update(fn.i80)
        subst.update(fn.subst)
        before.update(fn.before)
        after.update(fn.after)
        img_used |= fn.helpers
        i = j + 1

    used: set[str] = set()
    out: list[str] = []

    def call(ind: str, res: str | None, fn: str, args: list[str], tail: str = "") -> None:
        used.add(fn)
        ret, params, _ = DBL_DECLS[fn]
        types = [t.strip() for t in params.split(",")]
        head = f"{ind}{res} = " if res else ind
        out.append(f"{head}call {ret} @{fn}(" + ", ".join(f"{t} {a}" for t, a in zip(types, args))
                   + f"){tail}")

    def sign_image(ind: str, res: str, src: list[str]) -> None:
        b = f"%sa2d.s{len(out)}."
        out.extend(f"{ind}{l.format(b=b)}" for l in src)
        out.extend([f"{ind}{b}s = lshr i64 {b}b, 63", f"{ind}{b}z = zext i64 {b}s to i80",
                    f"{ind}{res} = shl i80 {b}z, 79"])
        st["sign_reads"] += 1

    def one(idx: int, line: str) -> None:
        has = "x86_fp80" in line
        if idx in i80:
            rule = i80[idx]
            if rule == "quiet-or":
                m = RE_I80_QUIET.match(line)
                out.append(f"{m.group(1)}{m.group(2)} = or i80 {m.group(3)}, {QUIET64}{m.group(4)}")
                st["quiet_nan"] += 1
            elif rule == "sign-load":
                m = RE_I80_LOAD.match(line)
                sign_image(m.group(1), m.group(2),
                           [f"{{b}}b = load i64, ptr {m.group(3)}, align {min(8, int(m.group(4)))}"])
            elif rule == "sign-bitcast":
                m = RE_I80_TOBITS.match(line)
                sign_image(m.group(1), m.group(2), [f"{{b}}b = bitcast double {_dbl(m.group(3))} to i64"])
            else:                                # quiet-load, quiet-store, sign-read: as they are
                out.append(line)
            return
        if not has and "i80" not in line and "@fe" not in line and "memory_f80" not in line:
            out.append(line)
            return
        if line.lstrip().startswith(";"):
            out.append(line)
            return
        if line.startswith("declare"):
            dm = RE_DECLARE.match(line)
            if dm and dm.group(1) in FENV:
                out.append(line.replace(f"@{dm.group(1)}(", f"@{FENV[dm.group(1)]}("))
            elif dm and dm.group(1) in ("__remill_read_memory_f80", "__remill_write_memory_f80"):
                out.append(line.replace(f"@{dm.group(1)}(", f"@{dm.group(1)}_dbl("))
            else:
                out.append(line)
            return
        if line.startswith("define"):
            m = RE_DEFINE.match(line)
            if has and not (m and m.group(2) in internal):
                raise Refused(f"a non-internal function with x86_fp80 in its signature: "
                              f"{line.strip()[:200]}")
            out.append(_dbl(line) if has else line)
            return
        if line.startswith("%") and " = type " in line:
            out.append(line)                     # layout: x86_fp80 -> i80 below
            return
        if has and (re.match(rf"^\s+{NAME} = alloca x86_fp80\b", line)
                    or re.match(rf"^\s+{NAME} = getelementptr (?:inbounds |nuw |nusw )*x86_fp80, ",
                                line)):
            out.append(line)
            return
        if m := RE_FREEZE_SRC.match(line):
            ind, res, v = m.group(1), m.group(2), m.group(3) or m.group(4)
            out.append(f"{ind}{res} = freeze double {_dbl(v)}" + (m.group(5) or "")
                       + (m.group(6) or ""))
            st["freeze"] += 1
            return
        if m := RE_FTOI.match(line):
            ind, res, op, v, ity = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            if op != "fptosi" or ity not in ("i16", "i32", "i64"):
                raise Refused(f"{op} x86_fp80 to {ity}: {line.strip()}")
            call(ind, res, f"sa2_df80_to_{ity}", [_dbl(v)])
            st["calls"][f"fptosi.{ity}"] = st["calls"].get(f"fptosi.{ity}", 0) + 1
            return
        if RE_I80.search(line) and not has:
            raise Refused(f"an i80 operation the double mode has no rule for: {line.strip()[:200]}")
        if m := RE_CALL.match(line):
            ind, res, callee = m.group(1), m.group(2), m.group(5)
            if callee in FENV:
                st["fenv"][callee] = st["fenv"].get(callee, 0) + 1
                out.append(line[:m.start(5)] + FENV[callee] + line[m.end(5):])
                return
            if callee in FENV_REFUSED:
                raise Refused(f"{callee}: the x87 environment is not modelled: {line.strip()[:200]}")
            if callee in ("__remill_read_memory_f80", "__remill_write_memory_f80"):
                out.append(line[:m.start(5)] + callee + "_dbl" + line[m.end(5):])
                st["m80"] += 1
                return
            if not has:
                out.append(line)
                return
            if callee in internal:
                out.append(_dbl(line))           # a helper of the PC24 pass, now on doubles
                st["retyped"] += 1
                return
            new = DBL_INTRINSIC.get(callee) or DBL_SHIM_CALL.get(callee)
            if new is None:
                raise Refused(f"a call passing x86_fp80 to {callee}: {line.strip()[:200]}")
            used.add(new)
            out.append(_dbl(line[:m.start(5)] + new + line[m.end(5):]))
            st["calls"][callee] = st["calls"].get(callee, 0) + 1
            st["replaced_callees"].add(callee)
            return
        if not has:
            out.append(line)
            return
        if (RE_BIN.match(line) or RE_FCMP.match(line) or RE_FPEXT.match(line)
                or RE_FPTRUNC.match(line) or RE_ITOF.match(line) or RE_FNEG.match(line)
                or re.match(rf"^\s+{NAME} = load (?:volatile )?x86_fp80, ptr ", line)
                or re.match(r"^\s+store (?:volatile )?x86_fp80 ", line)
                or re.match(rf"^\s+{NAME} = phi{FMF} x86_fp80 ", line)
                or re.match(rf"^\s+{NAME} = select{FMF} i1 [^,]+, x86_fp80 [^,]+, x86_fp80 ", line)
                or re.match(rf"^\s+{NAME} = freeze x86_fp80 ", line)
                or re.match(r"^\s+ret x86_fp80 ", line)):
            if m := RE_BIN.match(line):
                if m.group(3) == "frem":
                    raise Refused(f"frem x86_fp80: {line.strip()}")
            if m := RE_ITOF.match(line):
                if int(m.group(4)[1:]) > 64 or (m.group(3) == "uitofp" and m.group(4) == "i64"):
                    raise Refused(f"{m.group(3)} {m.group(4)} to x86_fp80: {line.strip()}")
            out.append(_dbl(line))
            st["retyped"] += 1
            return
        raise Refused(f"an x86_fp80 form the double mode has no rule for: {line.strip()[:200]}")

    for idx, line in enumerate(lines):
        if idx in skip:
            if skip[idx] is not None:
                out.extend(skip[idx])
            continue
        out.extend(before.get(idx, ()))
        for old, new in subst.get(idx, ()):
            line = _name_re(old).sub(new, line)
        one(idx, line)
        out.extend(after.get(idx, ()))

    # Declarations: an x86_fp80 one nothing references any more goes; the callees used come in.
    body = "\n".join(out)
    final: list[str] = []
    for line in out:
        dm = RE_DECLARE.match(line)
        if dm and ("x86_fp80" in line or dm.group(1) in st["replaced_callees"]):
            if len(re.findall(rf"@{re.escape(dm.group(1))}(?![-\w.$])", body)) == 1:
                st["declarations_dropped"].append(dm.group(1))
                continue
            raise Refused(f"an x86_fp80 declaration still referenced: {line.strip()[:200]}")
        final.append(line)
    defined = {m.group(2) for l in final if (m := RE_DEFINE.match(l))}
    while final and final[-1] == "":
        final.pop()
    for h in sorted(img_used):
        if h in defined:
            raise Refused(f"the module already defines {h}")
        final += [""] + IMG_HELPERS[h]
    for h in st["helpers"]:
        for l in DBL_HELPERS[h]:
            used.update(re.findall(r"@((?:sa2_df80_|llvm\.)[\w.]+)\(", l))
    for h in img_used:
        for l in IMG_HELPERS[h]:
            used.update(re.findall(r"@((?:sa2_df80_|llvm\.)[\w.]+)\(", l))
    declared = {m.group(1) for l in final if (m := RE_DECLARE.match(l))}
    defined = {m.group(2) for l in final if (m := RE_DEFINE.match(l))}
    for fn in sorted(used):
        if fn in declared or fn in defined:
            continue
        ret, params, attrs = DBL_DECLS[fn]
        final.append(f"declare {ret} @{fn}({params}) {attrs}")
    result = "\n".join(final)
    if not result.endswith("\n"):
        result += "\n"
    for line in result.split("\n"):
        if "x86_fp80" not in line or line.lstrip().startswith(";"):
            continue
        if (line.startswith("%") and " = type " in line) or re.match(
                rf"^\s+{NAME} = (?:alloca x86_fp80\b|getelementptr (?:inbounds |nuw |nusw )*x86_fp80, )",
                line):
            continue
        raise Refused(f"an x86_fp80 line the double mode left: {line.strip()[:200]}")
    body = to_i80(result)
    if "x86_fp80" in body or RE_F80C.search(body):
        raise Refused("an x86_fp80 type or constant survived the i80 substitution")
    st["replaced_callees"] = sorted(st["replaced_callees"])
    st["shims_used"] = sorted(u for u in used if u.startswith("sa2_df80_"))
    st["image_helpers"] = sorted(img_used)
    st["rewrites"] = (st["retyped"] + st["freeze"] + sum(st["calls"].values())
                      + st["quiet_nan"] + st["sign_reads"] + st["m80"]
                      + sum(st["image_reads"].values()))
    return DBL_MARKER + "\n" + body, st


def shim_names(obj: Path) -> frozenset[str]:
    """The shims a compiled f80_shims_double.o defines: its global text symbols with an exact
    or a double shim prefix (tools/s7/f80_legalize.py's census classes them)."""
    import subprocess  # noqa: PLC0415
    p = subprocess.run(["nm", "-g", "--defined-only", str(obj)], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"nm {obj}: {p.stderr.strip()[:200]}")
    names = frozenset(f[-1] for l in p.stdout.splitlines() if len(f := l.split()) >= 2
                      and f[-2] == "T" and f[-1].startswith(SHIM_PREFIXES))
    if not any(n.startswith("sa2_df80_") for n in names):
        raise RuntimeError(f"{obj} defines no sa2_df80_ function: not the double mode's shims")
    return names


def census(objs: list[Path], shims: frozenset[str], jobs: int = 8) -> dict:
    """tools/s7/f80_legalize.py's census (nm -u against its allowlist) with the double mode's
    shim names."""
    return F.census(objs, shims, jobs)


# ------------------------------------------------------------------ the fold check

def _check_one(path: str) -> dict:
    text = Path(path).read_text(errors="replace")
    try:
        _, st = legalize(text)
    except Refused as e:
        return {"ir": path, "refused": str(e)[:600]}
    return {"ir": path, "image_reads": st["image_reads"], "sign_reads": st["sign_reads"],
            "quiet_nan": st["quiet_nan"], "m80": st["m80"]}


def fold_check(args: argparse.Namespace) -> int:
    import cross_build as XB  # noqa: PLC0415  tools/s7/cross_build.py: the link's populations
    pops = XB.populations()
    files = [str(p) for pop in pops.values() for _, p in pop]
    t0 = time.time()
    with cf.ProcessPoolExecutor(args.jobs) as ex:
        recs = list(ex.map(_check_one, files, chunksize=32))
    refused = [r for r in recs if "refused" in r]
    doc = {"schema": "sa2-s8-f80-double-fold-check/v1", "generated_by": SELF,
           "pass_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "modules": len(recs), "refused": len(refused),
           "refusals": collections.Counter(r["refused"].split(": ")[0][:160] for r in refused),
           "first_refusals": refused[:20],
           "image_reads": {k: sum(r.get("image_reads", {}).get(k, 0) for r in recs)
                           for k in ("lo", "hi")},
           "modules_with_image_reads": sum(1 for r in recs if sum(r.get("image_reads", {}).values())),
           "sign_reads": sum(r.get("sign_reads", 0) for r in recs),
           "quiet_nan": sum(r.get("quiet_nan", 0) for r in recs),
           "m80": sum(r.get("m80", 0) for r in recs),
           "seconds": round(time.time() - t0, 1)}
    text = json.dumps(doc, indent=1, default=list)
    if args.json:
        Path(args.json).write_text(text + "\n")
    print(text)
    return 0 if not refused else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rewrite", help="one module in the double mode")
    r.add_argument("src")
    r.add_argument("dst")
    r.add_argument("--stats")
    c = sub.add_parser("fold-check", help="the pass over every module the replay link reads")
    c.add_argument("--jobs", type=int, default=6)
    c.add_argument("--json")
    a = ap.parse_args(argv)
    if a.cmd == "fold-check":
        return fold_check(a)
    text = Path(a.src).read_text(errors="replace")
    try:
        out, st = legalize(text)
    except Refused as e:
        raise SystemExit(f"refused: {e}")
    Path(a.dst).write_text(out)
    if a.stats:
        Path(a.stats).write_text(json.dumps(F.stats_json(st), indent=1) + "\n")
    print(json.dumps({k: st[k] for k in ("rewrites", "image_reads", "sign_reads", "quiet_nan",
                                         "m80")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
