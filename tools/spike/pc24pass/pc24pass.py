#!/usr/bin/env python3
"""pc24pass: add x87 precision-control rounding to remill's lifted LLVM IR, textually.

remill (v5.0.7, tools/spike/lift_remill.py) computes every x87 arithmetic op as
an LLVM `fadd`/`fsub`/`fmul`/`fdiv` on `x86_fp80` (64-bit significand) and never
reads the FPU control word it carries in `%struct.State`. Design 3.4 wants the
result of every basic op rounded to 24 significand bits under PC24 (control
word bits 8-9 == 00), 53 under PC53, unrounded under PC64, read from the live
control word. This pass rewrites each

    %r = fmul x86_fp80 %a, %b

into

    %sa2pre.r = fmul x86_fp80 %a, %b
    %sa2cwp.r = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 <FPU>, i32 0, i32 0, i32 0, i32 0
    %sa2cw.r = load i16, ptr %sa2cwp.r, align 2
    %r = call x86_fp80 @__sa2_pc_round(x86_fp80 %sa2pre.r, i16 %sa2cw.r)   ; sa2-pc24pass

so every original user of %r sees the rounded value without being touched.
`<FPU>` is the index of `%union.FPU` in `%struct.X86State` (11 in v5.0.7),
found by parsing the type line, and the trailing indices walk
FPU -> anon{FpuFXSAVE,...} -> FpuFXSAVE -> cwd (FPUControlWord union) -> flat
i16, the same path remill's own IR uses for `fop`, `ip` and `dp` next to it
(`... i32 11, i32 0, i32 0, i32 4` is `fop`). The state pointer is the first
parameter of the enclosing `define` (remill: `ptr noalias %state`); a define
whose first parameter is not a pointer is not lifted code (the spliced helper
itself, or anything clang added) and is skipped and counted.

Which ops are instrumented is decided by the source instruction, not by the IR
opcode. remill inlines one C++ semantic per instruction and names the block
that holds its arithmetic after the mangled semantic
(`_ZN12_GLOBAL__N_17FMULmemI...exit`, `_ZN12_GLOBAL__N_15FADDPI...exit`), so
the pass reads the Itanium length-prefixed name out of the nearest preceding
label and instruments an `x86_fp80` op only under FADD/FSUB/FMUL/FDIV/FSQRT
and their reverse (R), pop (P), integer-operand (FI...) and memory (...mem)
forms: the ops the precision-control field applies to (Intel SDM vol. 1,
8.1.5.2). An op under any other label (FSIN, FCOS, FPTAN, FPATAN, FYL2X,
F2XM1, FSCALE, FRNDINT, FPREM, FPREM1, FXTRACT, FLD/FST conversions, a block
with no remill label at all) is left untouched and counted in
`skipped_by_semantic`: remill decomposes those into fp80 arithmetic
internally, and x87 does not round their internals to PC. `frem` is never
matched (no x87 instruction lowers to it).

Not every semantic puts its arithmetic in its own `.exit` block. FSQRT and the
FIST family reach theirs through a static helper of remill's semantics library
(`_ZL19CheckedFloatUnaryOp…exit.i`), whose label carries no semantic name, so
the nearest-preceding rule reads `?` there and the S2 measurement found both
classes silently skipped. remill emits a semantic's body *before* its `.exit`
label, so when the nearest preceding label carries no semantic the pass looks
forward to the next semantic label in the same define and uses that: an op
inside a semantic's own exit block keeps the nearest-preceding answer, an op in
a nested helper block gets the semantic whose body it is in, and an op with no
semantic label after it at all is still `?`. Sites attributed this way are
counted separately in `by_semantic_enclosing`.

remill does not lower `fsqrt` to `llvm.sqrt.f80` (measured, tools/s2/patch/
probes.py): its FSQRT semantic calls the host's `sqrtl`, under the label
`DoFSQRT`. Both forms are matched, so the pass rounds the square root's result
to the guest's precision whichever one a remill build emits.

Two further families are rewritten, not rounded, because remill borrows the
host for them (S2 patch work, Documentation/receipts/s2-patch-v1.json):

  FIST/FISTP  remill emits `llvm.nearbyint.f80` (which follows the *host*
              rounding mode) and `fptosi x86_fp80 to iN` (which is poison, not a
              value, when the operand is out of the destination's range). With
              `--int-helper-ll`, the rounding becomes `__sa2_x87_round_int` and
              the conversion `__sa2_fistp_i16/i32/i64` (tools/s2/patch/x87_int.c),
              both taking the live control word: x87's rounding control and
              x87's integer indefinite value (0x80000000 and its siblings), with
              nothing left to the host. 699 sites in the image.
  FLD/FSTP m80  remill declares `__remill_read_memory_f80` and
              `__remill_write_memory_f80` with LLVM's `readnone` even though the
              80-bit value travels through an *out-pointer* rather than a return
              value: `%v = alloca x86_fp80; call @__remill_read_memory_f80(mem,
              addr, ptr %v); load x86_fp80, ptr %v`. `readnone` promises the
              callee touches no memory, so at `-O2` LLVM knows nothing ever wrote
              `%v` and folds the load to zero -- every `fld tbyte` in the image
              loads 0.0, silently, with the value present and correct in guest
              memory. Measured at 0x0085A39C (`fld tbyte [0x00922EEA]`, the CRT's
              pi/2 constant), which is why `atan2(x, 0)` returned 0 instead of
              +-pi/2. With `--fix-f80-accessors` (on by default) each such
              declaration is moved to a fresh attribute group that is its own
              minus `readnone`/`readonly`/`memory(none)`/`memory(read)`; the
              groups the other intrinsics share are not touched, so a module with
              no 80-bit memory operand is byte-identical. Every site is recorded
              in `f80_accessor_attrs_fixed`.
  FPATAN      remill's FPATAN semantic divides st(1) by st(0) and calls the
              host's one-argument `atanl`, which only spans (-pi/2, pi/2): the
              quadrant x87's two-argument FPATAN gets from the sign of st(0) is
              gone, so every lifted `fpatan` with st(0) < 0 is off by pi, and
              st(0) == 0 divides by zero. Measured through the CRT's `acos`
              (0x0086BF91: `fsqrt; fxch; fpatan` over `atan2(sqrt(1-x*x), x)`),
              which returned `acos(x) - pi` for every negative x -- a half-turn
              error in every angle the game takes from an arc cosine. With
              `--fix-fpatan` (on by default) the call becomes `atan2l(st(1),
              st(0))` on the division's own operands, and the quotient, now
              unused, is dead. The result stays a design 7.4 transcendental
              residue; the quadrant is no longer wrong. Sites are recorded in
              `fpatan_rewrites`.
              Which division feeds the call is not guessed: the call's *own* argument is
              resolved backwards through remill's store/memcpy/load chain
              (`fpatan_divisor`), and a site whose argument does not resolve to an
              `fdiv x86_fp80` is left exactly as remill emitted it and counted in
              `fpatan_refused`. Until the G2 fold the rule was "the last `fdiv` before
              the call" with no check that its quotient reached the call at all; how
              often that guess agreed is `fpatan_nearest_fdiv_agreed`.
  FLDCW/FNSTCW  remill's control-word store masks the result with `-769`
              (0xFCFF), which clears the precision-control field, and FNSTCW's
              read-back masks with `-3841` (0xF0FF) before reconstructing the
              rounding control from `fegetround`. A guest `fldcw 0x27F` therefore
              leaves PC = 00 in State, so this pass's live read sees PC24 at
              every one of the game region's 1025 `fldcw` sites. With
              `--fix-control-word` (on by default) the masks become `-1` and
              `-3073` (0xF3FF), which keeps the precision-control bits and still
              lets FNSTCW rebuild the rounding control. The rewrite applies only
              inside a lifted define that contains the control-word GEP, and
              every site is recorded in `control_word_masks_fixed`.
              FNSTCW's read-back also sets bit 6, because the hardware does:
              bit 6 is reserved and reads as 1 whatever was loaded (`fldcw
              0x023F` reads back 0x027F, measured on the build machine,
              2026-09-23). remill returns the stored word, so the CRT's math
              dispatchers, which take their SSE2 implementations only when
              `fnstcw & 0x7F` is 0x7F, took the x87 ones after the CRT's own
              `fldcw 0x023F`. Recorded in `control_word_bit6_set`.
  FXAM        remill's FXAM semantic reads the sign of st(0) once, before it
              branches on the class, and stores it into C1 only on the normal and
              denormal arms; its zero, NaN and infinity arms store C1 = 0. The
              x87 sets C1 to the sign on every class: Intel SDM vol. 2A, FXAM,
              whose Operation section reads "C1 <- sign bit of ST" ahead of the
              class table, and whose flags section repeats it. The CRT's
              transcendental dispatchers keep that bit: __trandisp2 (0x0082C037)
              saves each operand's C1 (`and ecx,404h`) and atan2's zero-operand
              tail (0x0085A3A5) takes pi's sign from it, so the lifted
              atan2(-0, x<0) returned +pi where the game natively gets -pi
              (22 contact-angle words in Metal Harbor, 0x8000 native against
              0xffff8000 lifted, from tick 338). With `--fix-fxam` (on by
              default) each of those constant stores becomes a store of the sign
              the semantic already computed. The value is checked, not assumed:
              it must be the one non-constant value the FXAM stores into C1, it
              must be `trunc(lshr(bitcast V to i80), 79)` of the V the
              semantic's zero test classifies, its block must dominate every
              store it replaces, and, with those stores rewritten, every path
              from the sign to the FXAM's exit must store the sign into C1 last
              (`fxam_c1_fix`, `c1_last_store_is_sign`), so an arm the plan did
              not find refuses the FXAM instead of keeping its 0 unseen. An FXAM
              that fails any of these is left as remill emitted it and recorded
              in `fxam_refused_sites`; the stores rewritten are
              `fxam_c1_stores_fixed`. The class bits C3, C2 and C0 are remill's,
              left as emitted: right on the canonical classes, but remill models
              no "empty" or "unsupported" class (an unsupported encoding is
              classed NaN, C0 = 1) and classes a pseudo-denormal as normal
              (C3 = 0, the x87 reports denormal); C1 is the sign on all of them
              after the rewrite. test_pc24pass.py holds all four bits to this
              host's own `fxam` on each of those encodings.
  FLD m32/m64/m80  remill's FLDmem sets the sticky invalid-operation flag from the
              value it loads (`ie |= IsSignalingNaN(v)`) and then quiets the value
              (sets significand bit 62) whenever `ie` is set: it tests the sticky
              flag, not the value. Once any x87 operation has set IE, every later
              FLD m32/m64 gains the top fraction bit of what it loads (1.0 loads as
              1.5, bf09fe7f as bf49fe7f) until an operation remill models as
              clearing IE runs. The x87 quiets only the signalling NaN itself
              (Intel SDM vol. 2A, FLD, "#IA: Source operand is an SNaN"; measured on
              the build machine, 2026-10-12: with IE set, `fld dword` of bf09fe7f
              stores back bf09fe7f and of 7f800001 stores 7fc00001), and FLD m80
              neither quiets nor flags ("Does not occur if the source operand is in
              double extended-precision floating-point format"). The game's event
              and mini-event shape builder (0x00783A30) moves big-endian key words
              through `fld dword`/`fstp` before swapping them; a word that reads as a
              signalling NaN in the wrong byte order set IE, and every key component
              loaded after it in the same mesh gained that bit: the misplaced faces and
              spiky hands of the opening event and the mini-events. With
              `--fix-fld-quiet` (on by default) the quieting branch of each FLDmem
              tests the load's own signalling-NaN result (m32, m64) or nothing at all
              (m80, never quieted); the IE update is left as remill wrote it, so the
              flag is still sticky. The rewrite is checked, not assumed: the branch
              into the semantic's one `or i80 v, 2^62` must test `icmp eq/ne i8 R, 0`
              with R the IE flag read after the semantic's own IE store (or that
              store's value), the store's value must be `or i8 (load IE), S`, S must
              be `zext` of `exponent all ones, quiet bit clear, fraction nonzero` on
              the bits of the value the semantic reads with `__remill_read_memory_f32`
              or `_f64`, S's block must dominate the test, and the test must have no
              other use. Anything else refuses that FLD (`fld_quiet_refused_sites`);
              the tests rewritten are `fld_quiet_fixed`. remill's IE remains wrong in
              two ways no value reads after this rewrite: FLD m80 of a signalling NaN
              sets it, and FLD ST(i) and the FCOM family clear it.

The helper `__sa2_pc_round` (pc_round.c) is compiled to IR by the driver and
its definition is spliced in as an `internal` function, so the fixture harness
links nothing new and `-O1` inlines it; the integer helpers are spliced the
same way from the IR `--int-helper-ll` names.

Deterministic (pure text, no timestamps) and idempotent: the output starts
with a marker line and a second run over instrumented IR returns it byte for
byte. The marker is a short-circuit, not the guard: with the marker line
stripped, a re-run still changes nothing, because an op whose result is named
`sa2pre.*` is never wrapped twice, the helper's own define is skipped as
non-lifted, and the helper is not spliced a second time when it is already
defined (tools/spike/pc24pass/test_pc24pass.py checks all three).

Usage:
  pc24pass.py --helper-ll pc_round.ll IN.ll OUT.ll [--stats stats.json]
              [--int-helper-ll x87_int.ll] [--no-fix-control-word]
              [--no-fix-f80-accessors] [--no-fix-fpatan] [--no-fix-fxam]
              [--no-fix-fld-quiet]
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sys
from pathlib import Path

MARKER = "; sa2-pc24pass v1"
HELPER = "__sa2_pc_round"
ROUND_INT_HELPER = "__sa2_x87_round_int"
FISTP_HELPERS = {"i16": "__sa2_fistp_i16", "i32": "__sa2_fistp_i32", "i64": "__sa2_fistp_i64"}
INT_HELPERS = [ROUND_INT_HELPER] + list(FISTP_HELPERS.values())
NAME = r'%(?:"[^"]+"|[-A-Za-z$._][-A-Za-z$._0-9]*|[0-9]+)'
RE_OP = re.compile(rf"^(\s+)({NAME}) = (fadd|fsub|fmul|fdiv)((?: [a-z]+)*) x86_fp80 (.+)$")
RE_SQRT = re.compile(rf"^(\s+)({NAME}) = (?:tail )?call x86_fp80 @llvm\.sqrt\.f80\((.*)\)(.*)$")
# remill v5.0.7 lowers FSQRT to the host's sqrtl, not to the intrinsic (measured).
RE_SQRTL = re.compile(rf"^(\s+)({NAME}) = (?:tail )?call x86_fp80 @sqrtl\(x86_fp80 (?:noundef )?({NAME}|0xK[0-9A-Fa-f]+)\)(.*)$")
RE_NEARBYINT = re.compile(rf"^(\s+)({NAME}) = (?:tail )?call x86_fp80 @llvm\.nearbyint\.f80\(x86_fp80 ({NAME}|0xK[0-9A-Fa-f]+)\)(.*)$")
RE_FPTOSI = re.compile(rf"^(\s+)({NAME}) = fptosi x86_fp80 ({NAME}|0xK[0-9A-Fa-f]+) to (i16|i32|i64)$")
RE_CW_MASK = re.compile(rf"^(\s+)({NAME}) = and i16 ({NAME}), (-769|-3841)$")
RE_DEFINE = re.compile(r"^define .*@([^(\s]+)\(([^)]*)\)")
RE_TYPE = re.compile(r"^(%[\w.]+) = type \{ (.*) \}$")
RE_LABEL = re.compile(r"^([\w.$]+):")
RE_SEMANTIC = re.compile(r"_ZN12_GLOBAL__N_1(\d+)([A-Za-z0-9_]+)")
# The x87 instructions whose result the precision-control field rounds (Intel SDM vol. 1, 8.1.5.2), as remill
# names their semantics: F[I](ADD|SUB|MUL|DIV)[R][P][mem] and FSQRT (which remill v5.0.7 calls DoFSQRT).
# Everything else is skipped and counted.
RE_PC_SEMANTIC = re.compile(r"^(?:FI?(?:ADD|SUB|MUL|DIV)R?P?(?:mem)?|(?:Do)?FSQRT(?:mem)?)$")
# The float-to-integer stores: FIST / FISTP / FISTTP in every destination width.
RE_FIST_SEMANTIC = re.compile(r"^FIST(?:T)?P?(?:m16|m32|m64)?$")
# What FNSTCW clears before rebuilding the rounding control from fegetround: -3841 = 0xF0FF wipes
# precision control (bits 8-9) as well as rounding control (10-11); -3073 = 0xF3FF wipes only the latter.
CW_MASK_FIX = {"-769": "-1", "-3841": "-3073"}
NO_SEMANTIC = "?"

# The remill memory accessors that hand their value back through an out-pointer instead of a
# return value. LLVM's `readnone` on such a declaration is a lie the optimiser acts on: the
# store into the caller's alloca never happens, so the load after the call is undef (zero at
# -O2). Only these are repaired; the return-value accessors are correctly `readnone`.
# remill's FPATAN semantic divides st(1) by st(0) and calls the host's one-argument `atanl`,
# which cannot tell the second and third quadrants from the fourth and first. x87's FPATAN is
# a two-argument arc tangent over the whole circle, so the lifted result is off by pi wherever
# st(0) < 0 (and undefined where st(0) == 0). `atan2l` is that function.
RE_FPATAN_SEMANTIC = re.compile(r"^(?:Do)?FPATAN$")
RE_ATANL = re.compile(rf"^(\s+)({NAME}) = (?:tail )?call x86_fp80 @atanl\(x86_fp80 (?:noundef )?"
                      rf"({NAME}|0xK[0-9A-Fa-f]+)\)(.*)$")
ATAN2 = "atan2l"
ATAN2_DECLARE = "declare dso_local x86_fp80 @atan2l(x86_fp80 noundef, x86_fp80 noundef) local_unnamed_addr"

# FXAM: remill's semantic stores C1 = the sign of st(0) only on its normal and denormal arms,
# and a constant 0 on its zero, NaN and infinity arms. The x87 sets C1 to the sign bit on every
# class (see the module docstring). `FXAM_C1_FIELD` is `c1`'s index in remill's
# `struct FPUStatusFlags { _0, c0, _1, c1, _2, c2, _3, c3, ... }` (remill/Arch/X86/Runtime/State.h),
# which `%struct.X86State` holds as its `sw` member; `fxam_c1_gep` finds that member by type.
RE_FXAM_SEMANTIC = re.compile(r"^(?:Do)?FXAM$")
FXAM_C1_FIELD = 3
RE_STORE_I8 = re.compile(rf"^(\s+)store i8 ({NAME}|-?\d+), ptr ({NAME})(, align \d+)?(\s*;.*)?$")
# Any store, its pointer the last `ptr` operand; a store with metadata or an address space does
# not match the pointer group, so the postcondition cannot place it and refuses.
RE_STORE_ANY = re.compile(rf"^\s+store\b.*, ptr ({NAME})(?:, align \d+)?(?:\s*;.*)?$|^\s+store\b")
RE_ASSIGN = re.compile(rf"^\s+({NAME}) = (.*)$")
RE_LABEL_REF = re.compile(rf"\blabel ({NAME})")
FXAM_TAG = "   ; sa2-pc24pass fxam c1"

# FLD m32/m64/m80: remill's FLDmem quiets the loaded value whenever the sticky IE flag is set; the
# x87 quiets an m32/m64 signalling NaN only, and an m80 never (see the module docstring).
# `FLD_IE_FIELD` is `ie`'s index in remill's `struct FPUStatusFlags { _0, c0, _1, c1, _2, c2, _3,
# c3, _4, pe, _5, ue, _6, oe, _7, ze, _8, de, _9, ie, _padding[4] }` (State.h).
RE_FLD_SEMANTIC = re.compile(r"^FLDmem$")
# The width is the semantic's template argument: FLDmem<Mn<float>>, <Mn<double>>, <Mn<float80_t>>.
RE_FLD_WIDTH = re.compile(r"_ZN12_GLOBAL__N_16FLDmemI2MnI(f|d|9float80_t)E")
FLD_WIDTHS = {"f": 32, "d": 64, "9float80_t": 80}
FLD_IE_FIELD = 19
FLD_QUIET_BIT = str(1 << 62)             # the x87 quiet bit, bit 62 of the 80-bit image
# IsSignalingNaN on the loaded value's bits, per width: (int type, exponent|quiet mask, exponent
# all ones, fraction below the quiet bit), as remill's semantic computes it.
FLD_SNAN_TEST = {32: ("i32", 0x7FC00000, 0x7F800000, 0x003FFFFF),
                 64: ("i64", 0x7FF8000000000000, 0x7FF0000000000000, 0x0007FFFFFFFFFFFF)}
FLD_READS = {32: ("float", "__remill_read_memory_f32"), 64: ("double", "__remill_read_memory_f64")}
# A semantic's own exit label (`..._ZN12_GLOBAL__N_1<len><name>...E.exit[N]`), not that of a helper
# inlined into it (`....exit.i.i`): the FLD's lines lie after the previous one.
RE_SEMANTIC_EXIT = re.compile(r"\.exit\d*$")
RE_ICMP_I8_ZERO = re.compile(rf"^({NAME}) = icmp (eq|ne) i8 ({NAME}|0), 0$")
RE_BR_COND = re.compile(rf"^\s+br i1 ({NAME}), label ({NAME}), label ({NAME})$")
FLD_TAG = "   ; sa2-pc24pass fld quiet"

RE_ATTR_GROUP = re.compile(r"^attributes #(\d+) = \{ (.*) \}$")
RE_DECL_OUT_PTR = re.compile(
    r"^(declare\s+(?:dso_local\s+)?[^@]*@(__remill_(?:read|write)_memory_\w+)"
    r"\([^)]*\bdereferenceable\(\d+\)[^)]*\))\s+#(\d+)$")
NO_MEMORY_ATTRS = ("readnone", "readonly")
RE_NO_MEMORY_ATTR = re.compile(r"^memory\((?:none|read)\)$")


def semantic_of(label: str | None) -> str:
    """remill's semantic name from a block label (`_ZN12_GLOBAL__N_1<len><name>...`), or NO_SEMANTIC."""
    m = RE_SEMANTIC.search(label) if label else None
    if not m:
        return NO_SEMANTIC
    n = int(m.group(1))
    name = m.group(2)[:n]
    return name if len(name) == n else NO_SEMANTIC


def split_fields(body: str) -> list[str]:
    """Top-level comma split of a struct body (nested [..] and {..} kept whole)."""
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def control_word_gep(types: dict[str, list[str]]) -> str:
    """The getelementptr index list from %struct.State to the i16 control word, checked against the types."""
    state = types.get("%struct.State")
    if state != ["%struct.X86State"]:
        raise SystemExit(f"unexpected %struct.State layout: {state}")
    x86 = types["%struct.X86State"]
    fpu_ix = x86.index("%union.FPU")
    fpu = types["%union.FPU"]
    anon = types[fpu[0]]
    if anon[0] != "%struct.FpuFXSAVE":
        raise SystemExit(f"expected FpuFXSAVE first in the FPU union, got {anon}")
    fx = types["%struct.FpuFXSAVE"]
    cwd = types[fx[0]]
    if cwd != ["i16"]:
        raise SystemExit(f"expected the control word as {{ i16 }}, got {fx[0]} = {cwd}")
    return f"i64 0, i32 0, i32 {fpu_ix}, i32 0, i32 0, i32 0, i32 0"


def one_definition(lines: list[str], name: str, want_return: str | None = None) -> list[str]:
    """One helper's `define` rewritten as an `internal` function with no attribute groups or metadata."""
    start = next((i for i, l in enumerate(lines) if l.startswith("define") and f"@{name}(" in l), None)
    if start is None:
        raise SystemExit(f"{name} not defined in the helper IR")
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    m = re.match(rf"^define\s+(.*?)@{re.escape(name)}\((.*)\)(.*)\{{$", lines[start])
    if not m:
        raise SystemExit(f"cannot parse the define of {name}: {lines[start]}")
    ret = m.group(1).split()[-1]          # `dso_local signext i16 ` -> `i16` (attributes dropped)
    if want_return and ret != want_return:
        raise SystemExit(f"{name} returns {ret}, expected {want_return}")
    params = []
    for p in split_fields(m.group(2)):
        toks = p.split()
        params.append(f"{toks[0]} {toks[-1]}")
    if [p.split()[0] for p in params] != ["x86_fp80", "i16"]:
        raise SystemExit(f"{name} signature changed: {params}")
    body = [f"define internal {ret} @{name}({', '.join(params)}) {{"]
    for l in lines[start + 1:end]:
        l = re.sub(r" #\d+\b", "", l)
        l = re.sub(r"(, ![\w.]+ !\d+)+\s*$", "", l)
        l = re.sub(r", !dbg !\d+", "", l)
        body.append(l)
    body.append("}")
    for l in body:
        if re.search(r"= (fadd|fsub|fmul|fdiv|frem) ", l):
            raise SystemExit(f"{name} must not contain floating-point arithmetic (it would be instrumented)")
    return body


def helper_definition(helper_ll: str, names: list[str] | None = None,
                      returns: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """(definition lines as internal functions, declare lines they need) from clang's IR for a helper file."""
    lines = helper_ll.splitlines()
    names = names or [HELPER]
    returns = returns or {HELPER: "x86_fp80"}
    body: list[str] = []
    for n in names:
        body.extend(one_definition(lines, n, returns.get(n)))
    called = set(re.findall(r"call [^@]*@([\w.]+)\(", "\n".join(body)))
    declares = []
    for l in lines:
        if l.startswith("declare"):
            mm = re.search(r"@([\w.]+)\(", l)
            if mm and mm.group(1) in called and mm.group(1) not in names:
                declares.append(re.sub(r" #\d+\b", "", l))
    return body, declares


def enclosing_semantics(lines: list[str]) -> list[str]:
    """Per line, the semantic whose inlined body that line is in, looking forward within the define.

    remill emits a semantic's blocks before its `.exit` label, so a line that sits in a block with no
    semantic name belongs to the next semantic label that follows it inside the same `define`. Lines
    after the last semantic label of a define, and lines in a define with none, get NO_SEMANTIC.
    """
    out = [NO_SEMANTIC] * len(lines)
    pending: list[int] = []
    for i, line in enumerate(lines):
        if line.startswith("define ") or line == "}":
            pending.clear()
            continue
        m = RE_LABEL.match(line)
        if m:
            sem = semantic_of(m.group(1))
            if sem != NO_SEMANTIC:
                for j in pending:
                    out[j] = sem
                pending.clear()
                continue
        pending.append(i)
    return out


def f80_accessor_fix(lines: list[str]) -> tuple[dict[int, str], list[str], list[dict]]:
    """Plan the repair of remill's out-pointer memory accessors.

    Returns ({line index: replacement declare}, [new attribute-group lines], [sites]).
    A declaration whose attribute group makes no memory claim is left alone, so the
    pass is idempotent and a module with no 80-bit memory operand is untouched.
    """
    groups = {int(m.group(1)): m.group(2) for l in lines if (m := RE_ATTR_GROUP.match(l))}
    if not groups:
        return {}, [], []
    next_id = max(groups) + 1
    remap: dict[int, int] = {}          # old group -> new group, shared between declares
    new_groups: list[str] = []
    rewrites: dict[int, str] = {}
    sites: list[dict] = []
    for i, line in enumerate(lines):
        m = RE_DECL_OUT_PTR.match(line)
        if not m:
            continue
        head, name, gid = m.group(1), m.group(2), int(m.group(3))
        body = groups.get(gid)
        if body is None:
            continue
        toks = body.split()
        kept = [t for t in toks if t not in NO_MEMORY_ATTRS and not RE_NO_MEMORY_ATTR.match(t)]
        if len(kept) == len(toks):
            continue
        if gid not in remap:
            remap[gid] = next_id
            new_groups.append(f"attributes #{next_id} = {{ {' '.join(kept)} }}"
                              f"   ; sa2-pc24pass f80 (was #{gid})")
            next_id += 1
        rewrites[i] = f"{head} #{remap[gid]}   ; sa2-pc24pass f80"
        sites.append({"intrinsic": name, "was_group": gid, "now_group": remap[gid],
                      "removed": [t for t in toks if t not in kept]})
    return rewrites, new_groups, sites


def fxam_c1_gep(types: dict[str, list[str]]) -> str | None:
    """The index list from %struct.State to remill's C1 byte, or None if the module lacks the types."""
    x86 = types.get("%struct.X86State") or []
    sw = types.get("%struct.FPUStatusFlags")
    if "%struct.FPUStatusFlags" not in x86 or not sw:
        return None
    if len(sw) <= FXAM_C1_FIELD or any(t != "i8" for t in sw[:FXAM_C1_FIELD + 1]):
        return None
    return f"i64 0, i32 0, i32 {x86.index('%struct.FPUStatusFlags')}, i32 {FXAM_C1_FIELD}"


def define_cfg(lines: list[str], start: int, end: int) -> tuple[dict[int, str], dict[str, set[str]], str, set[str]]:
    """The blocks of the define whose `define` line is `start` and whose `}` is `end`.

    Returns ({line index: its block}, {block: successors}, entry block, label references
    that name no block of the define). A block's successors are every `label %x` its lines
    name, a superset of its CFG edges whatever the terminator (br, switch across several
    lines, indirectbr, invoke), so a dominance answer built on them errs towards "not
    dominated", never the other way.
    """
    block_of: dict[int, str] = {}
    succ: dict[str, set[str]] = {}
    entry = cur = "%<entry>"
    succ[cur] = set()
    refs: dict[str, set[str]] = {cur: set()}
    for j in range(start + 1, end):
        m = RE_LABEL.match(lines[j])
        if m:
            cur = m.group(1)
            if j == start + 1:
                entry = cur
                succ.pop("%<entry>", None)
                refs.pop("%<entry>", None)
            succ.setdefault(cur, set())
            refs.setdefault(cur, set())
        block_of[j] = cur
        for r in RE_LABEL_REF.findall(lines[j].split(";", 1)[0]):
            refs[cur].add(r[1:].strip('"'))
    unknown: set[str] = set()
    for b, rs in refs.items():
        for r in rs:
            if r in succ:
                succ[b].add(r)
            else:
                unknown.add(r)
    return block_of, succ, entry, unknown


def dominates(succ: dict[str, set[str]], entry: str, dom: str, target: str) -> bool:
    """Every path from the entry to `target` passes through `dom` (target == dom counts)."""
    if target == dom or entry == dom:
        return True
    seen, stack = {entry}, [entry]
    while stack:
        for s in succ.get(stack.pop(), ()):
            if s == dom or s in seen:
                continue
            if s == target:
                return False
            seen.add(s)
            stack.append(s)
    return True


def c1_last_store_is_sign(lines: list[str], block_of: dict[int, str], succ: dict[str, set[str]],
                          sblock: str, sdef: int, exit_label: str, c1_ptrs: set[str], sign: str,
                          planned: set[int], field_ptrs: set[str]) -> bool:
    """Every path from the sign's definition reaches the FXAM's exit label, and stores the sign
    into C1 last, counting the planned rewrites as done. An arm the plan missed fails it: one
    whose C1 store is laid out before the sign's line (and so was taken for an earlier
    instruction's), one that leaves the define without passing the exit label, or one with a
    store the pass cannot place -- through a pointer that is not one of %struct.FPUStatusFlags'
    fields (`field_ptrs`), or to C1 in a form other than a plain `store i8` -- since such a
    store may be C1's."""
    body: dict[str, list[int]] = {}
    for j in sorted(block_of):
        body.setdefault(block_of[j], []).append(j)

    def through(block: str, signed: bool, after: int = -1) -> bool | None:
        for j in body.get(block, ()):
            if j <= after or not RE_STORE_ANY.match(lines[j]):
                continue
            ms = RE_STORE_I8.match(lines[j])
            if ms and ms.group(3) in c1_ptrs:
                signed = j in planned or ms.group(2) == sign
            elif not ((mp := RE_STORE_ANY.match(lines[j])).group(1) in field_ptrs
                      and mp.group(1) not in c1_ptrs):
                return None
        return signed

    def leaves(block: str) -> bool:
        code = [c for j in body.get(block, ()) if (c := lines[j].split(";", 1)[0].strip())]
        return not succ.get(block) and not (code and code[-1] == "unreachable")

    first = through(sblock, False, sdef)
    if first is None:
        return False
    start = (sblock, first)
    seen, stack = {start}, [start]
    while stack:
        block, signed = stack.pop()
        if leaves(block):
            return False
        for s in succ.get(block, ()):
            if s == exit_label:
                if not signed:
                    return False
                continue
            after = through(s, signed)
            if after is None:
                return False
            nxt = (s, after)
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return True


def fxam_c1_fix(lines: list[str], types: dict[str, list[str]]) -> tuple[dict[int, str], list[dict], list[dict], int]:
    """Plan the repair of remill's FXAM C1 stores.

    remill's DoFXAM computes the sign of st(0) once, before it branches on the class, and
    stores it into C1 on the normal and denormal arms only; the zero, NaN and infinity arms
    store a constant 0. Each such constant store becomes a store of the sign the semantic
    already computed. Per FXAM (one per `DoFXAM...exit` label), the rewrite requires:

      * exactly one non-constant value `S` stored into C1 between the previous semantic
        label and the exit label, and `S` is `trunc (lshr (bitcast V to i80), 79) to i8`
        where `V` is the x86_fp80 the semantic's zero test (`fcmp oeq x86_fp80 V, 0`)
        classifies -- the sign bit of the examined value, not a guess;
      * every constant store into C1 after `S`'s definition is the constant 0, and lies in
        a block `S`'s block dominates (or after `S` in `S`'s own block), so the rewritten
        store reads a value defined on every path to it;
      * with those stores rewritten, every path from `S`'s definition to the exit label
        stores `S` into C1 last (`c1_last_store_is_sign`). The two rules above find the
        arms by line order and by the exact getelementptr text; this one checks the
        result on the CFG, so an arm they miss refuses the FXAM rather than leaving it
        at 0 while the counters read healthy.

    Anything else refuses the whole FXAM and records why; nothing is rewritten on a guess.
    Returns ({line index: replacement}, [sites], [refused], FXAMs already carrying the sign).
    """
    exits = [i for i, l in enumerate(lines)
             if (m := RE_LABEL.match(l)) and RE_FXAM_SEMANTIC.match(semantic_of(m.group(1)))]
    if not exits:
        return {}, [], [], 0
    tail = fxam_c1_gep(types)
    rewrites: dict[int, str] = {}
    sites: list[dict] = []
    refused: list[dict] = []
    already = 0
    cfg_cache: dict[int, tuple] = {}
    for e in exits:
        exit_label = RE_LABEL.match(lines[e]).group(1)
        ds = max(i for i in range(e) if lines[i].startswith("define "))
        de = next(i for i in range(e, len(lines)) if lines[i] == "}")
        dm = RE_DEFINE.match(lines[ds])
        func = dm.group(1) if dm else "?"
        params = split_fields(dm.group(2)) if dm else []
        first = params[0].split() if params else []

        def refuse(why: str) -> None:
            refused.append({"function": func, "exit_label": exit_label, "reason": why})

        if not first or first[0] != "ptr":
            refuse("the FXAM is not in a lifted define (its first parameter is not the State pointer)")
            continue
        if tail is None:
            refuse("C1's getelementptr cannot be derived: %struct.FPUStatusFlags is not in %struct.X86State "
                   "with i8 fields up to c1")
            continue
        state = first[-1]
        c1_gep = f"getelementptr inbounds %struct.State, ptr {state}, {tail}"
        # The FXAM's lines: after the previous semantic label of this define, up to its exit label.
        p = max((i for i in range(ds + 1, e)
                 if (m := RE_LABEL.match(lines[i])) and semantic_of(m.group(1)) != NO_SEMANTIC),
                default=ds)
        c1_ptrs = {m.group(1) for j in range(ds + 1, de)
                   if (m := RE_ASSIGN.match(lines[j])) and m.group(2).strip() == c1_gep}
        # every %struct.FPUStatusFlags field's pointer: a store through one that is not C1's is
        # not C1's (the fields are distinct bytes); any other store on an arm is not placeable
        sw_head = c1_gep[:c1_gep.rindex(", i32 ")] + ", i32 "
        field_ptrs = {m.group(1) for j in range(ds + 1, de)
                      if (m := RE_ASSIGN.match(lines[j])) and m.group(2).strip().startswith(sw_head)
                      and m.group(2).strip()[len(sw_head):].isdigit()}
        defs: dict[str, tuple[int, str]] = {}
        stores: list[tuple[int, re.Match]] = []
        zero_tested: set[str] = set()
        for j in range(p + 1, e):
            if (m := RE_ASSIGN.match(lines[j])):
                defs[m.group(1)] = (j, m.group(2))
                zt = re.match(rf"^fcmp oeq x86_fp80 ({NAME}), 0xK0{{20}}$", m.group(2))
                if zt:
                    zero_tested.add(zt.group(1))
            if (ms := RE_STORE_I8.match(lines[j])) and ms.group(3) in c1_ptrs:
                stores.append((j, ms))
        signs = {ms.group(2) for _, ms in stores if ms.group(2).startswith("%")}
        if len(signs) != 1:
            refuse(f"expected one non-constant value stored into C1, found {sorted(signs)}")
            continue
        sign = signs.pop()
        # S = trunc (lshr (bitcast V to i80), 79) to i8, V the value the zero test classifies
        chain_ok, operand = False, None
        if sign in defs:
            mt = re.match(rf"^trunc i80 ({NAME}) to i8$", defs[sign][1])
            ml = mt and defs.get(mt.group(1)) and re.match(rf"^lshr i80 ({NAME}), 79$", defs[mt.group(1)][1])
            mb = ml and defs.get(ml.group(1)) and re.match(rf"^bitcast x86_fp80 ({NAME}) to i80$",
                                                           defs[ml.group(1)][1])
            if mb and mb.group(1) in zero_tested:
                chain_ok, operand = True, mb.group(1)
        if not chain_ok:
            refuse(f"the value stored into C1 ({sign}) is not the sign bit of the value the FXAM's zero "
                   "test classifies")
            continue
        sdef = defs[sign][0]
        if ds not in cfg_cache:
            cfg_cache[ds] = define_cfg(lines, ds, de)
        block_of, succ, entry, unknown = cfg_cache[ds]
        # A store into C1 before the sign exists belongs to an earlier instruction's
        # semantic (one inlined with no label of its own), not to one of FXAM's arms: it
        # is left as it is and counted.
        consts = [(j, ms) for j, ms in stores if not ms.group(2).startswith("%") and j > sdef]
        earlier = sum(1 for j, ms in stores if not ms.group(2).startswith("%") and j < sdef)
        if any(ms.group(2) != "0" for _, ms in consts):
            refuse(f"a constant other than 0 is stored into C1: {sorted({ms.group(2) for _, ms in consts})}")
            continue
        if unknown:
            refuse(f"the define names blocks it does not hold, so dominance cannot be shown: {sorted(unknown)[:5]}")
            continue
        sblock = block_of[sdef]
        bad = [block_of[j] for j, _ in consts
               if not (block_of[j] == sblock and j > sdef) and not dominates(succ, entry, sblock, block_of[j])]
        if bad:
            refuse(f"the sign's block {sblock} does not dominate the C1 store(s) in {bad}")
            continue
        # The postcondition runs before an FXAM with nothing to rewrite is counted as already
        # signed: one whose every arm is laid out before the sign's line has no store in
        # `consts` either, and would otherwise read as already signed with C1 still 0.
        if not c1_last_store_is_sign(lines, block_of, succ, sblock, sdef, exit_label, c1_ptrs, sign,
                                     {j for j, _ in consts}, field_ptrs):
            refuse(f"a path from the sign reaches {exit_label} with C1 not the sign, leaves the define "
                   "without reaching it, or stores where the pass cannot place: an arm the pass did "
                   "not find")
            continue
        if not consts:
            already += 1
            continue
        for j, ms in consts:
            rewrites[j] = f"{ms.group(1)}store i8 {sign}, ptr {ms.group(3)}{ms.group(4) or ''}{FXAM_TAG}"
        sites.append({"function": func, "exit_label": exit_label, "sign": sign, "operand": operand,
                      "c1_stores_fixed": len(consts),
                      "arms": sorted({block_of[j] for j, _ in consts}),
                      "earlier_c1_stores_left": earlier})
    return rewrites, sites, refused, already


def fld_ie_gep(types: dict[str, list[str]]) -> str | None:
    """The index list from %struct.State to remill's IE byte, or None if the module lacks the types."""
    x86 = types.get("%struct.X86State") or []
    sw = types.get("%struct.FPUStatusFlags")
    if "%struct.FPUStatusFlags" not in x86 or not sw:
        return None
    if len(sw) <= FLD_IE_FIELD or any(t != "i8" for t in sw[:FLD_IE_FIELD + 1]):
        return None
    return f"i64 0, i32 0, i32 {x86.index('%struct.FPUStatusFlags')}, i32 {FLD_IE_FIELD}"


def fld_snan_read(defs: dict[str, tuple[int, str]], s: str, width: int) -> str | None:
    """The value whose signalling-NaN test `s` is, when `s` is remill's IsSignalingNaN of what an
    FLD m32/m64 read: `zext i1 X to i8`, X `and i1 A, B` (or `select i1 A, i1 B, i1 false`), A
    `icmp eq (and BITS, exponent|quiet), exponent`, B `icmp ne (and BITS, fraction), 0`, BITS
    `bitcast V`, V a call of `__remill_read_memory_f32` (`_f64`). None for anything else."""
    ity, qmask, exp, frac = FLD_SNAN_TEST[width]
    fty, reader = FLD_READS[width]

    def rhs(n: str) -> str:
        return defs.get(n, (0, ""))[1]

    mz = re.match(rf"^zext i1 ({NAME}) to i8$", rhs(s))
    x = rhs(mz.group(1)) if mz else ""
    ma = re.match(rf"^and i1 ({NAME}), ({NAME})$", x) or re.match(
        rf"^select i1 ({NAME}), i1 ({NAME}), i1 false$", x)
    if not ma:
        return None

    def bits_of(n: str, test: str, mask: int) -> str | None:
        mt = re.match(rf"^icmp {test} {ity} ({NAME}), {exp if test == 'eq' else 0}$", rhs(n))
        mm = mt and re.match(rf"^and {ity} ({NAME}), {mask}$", rhs(mt.group(1)))
        return mm.group(1) if mm else None

    a, b = ma.groups()
    bits = next((e for e, f in ((bits_of(a, "eq", qmask), bits_of(b, "ne", frac)),
                                (bits_of(b, "eq", qmask), bits_of(a, "ne", frac))) if e and e == f), None)
    mb = bits and re.match(rf"^bitcast {fty} ({NAME}) to {ity}$", rhs(bits))
    if not (mb and re.match(rf"^(?:tail )?call {fty} @{reader}\(", rhs(mb.group(1)))):
        return None
    return mb.group(1)


def fld_quiet_fix(lines: list[str], types: dict[str, list[str]]) -> tuple[dict[int, str], list[dict], list[dict], int]:
    """Plan the repair of remill's FLD m32/m64/m80 quieting test.

    remill's FLDmem stores `ie |= IsSignalingNaN(v)` and then quiets v (`or i80 v, 2^62`) when
    IE, the sticky flag, is set. Per FLDmem (one per `FLDmem...exit` label; its lines are those
    after the previous semantic exit label of the define), the rewrite requires:

      * exactly one quieting `or i80 _, 2^62` among its lines, and exactly one conditional
        branch into the quieting block, on `icmp eq i8 R, 0` (the quiet arm false) or
        `icmp ne i8 R, 0` (the quiet arm true), a test with no use but that branch;
      * R is IE: a load through IE's getelementptr after the FLD's own IE store (the nearest
        IE store before it), or that store's value itself;
      * the store's value is `or i8 L, S` with L a load of IE and S, for m32 and m64, remill's
        IsSignalingNaN of the value the FLD reads (`fld_snan_read`), its block dominating the
        test's.

    The test becomes `icmp eq/ne i8 S, 0` for m32/m64 (quiet the signalling NaN, nothing
    else) and `icmp eq/ne i8 0, 0` for m80 (never quiet). Anything else refuses that FLD and
    records why; nothing is rewritten on a guess. Returns ({line index: replacement}, [sites],
    [refused], FLDs whose test already is S, or 0 for m80).
    """
    exits = [i for i, l in enumerate(lines)
             if (m := RE_LABEL.match(l)) and RE_FLD_SEMANTIC.match(semantic_of(m.group(1)))]
    if not exits:
        return {}, [], [], 0
    tail = fld_ie_gep(types)
    starts = [i for i, l in enumerate(lines) if l.startswith("define ")]
    bounds = [i for i, l in enumerate(lines) if (m := RE_LABEL.match(l))
              and semantic_of(m.group(1)) != NO_SEMANTIC and RE_SEMANTIC_EXIT.search(m.group(1))]
    rewrites: dict[int, str] = {}
    sites: list[dict] = []
    refused: list[dict] = []
    already = 0
    per_define: dict[int, tuple] = {}
    for e in exits:
        exit_label = RE_LABEL.match(lines[e]).group(1)
        ds = starts[bisect.bisect_right(starts, e) - 1] if starts and starts[0] < e else None
        dm = RE_DEFINE.match(lines[ds]) if ds is not None else None
        func = dm.group(1) if dm else "?"
        params = split_fields(dm.group(2)) if dm else []
        first = params[0].split() if params else []
        mw = RE_FLD_WIDTH.search(exit_label)
        width = FLD_WIDTHS[mw.group(1)] if mw else None

        def refuse(why: str) -> None:
            refused.append({"function": func, "exit_label": exit_label, "width": width, "reason": why})

        if not first or first[0] != "ptr":
            refuse("the FLD is not in a lifted define (its first parameter is not the State pointer)")
            continue
        if width is None:
            refuse("the FLD's operand width is not FLDmem's float, double or float80_t template argument")
            continue
        if tail is None:
            refuse("IE's getelementptr cannot be derived: %struct.FPUStatusFlags is not in %struct.X86State "
                   "with i8 fields up to ie")
            continue
        if ds not in per_define:
            de = next(i for i in range(e, len(lines)) if lines[i] == "}")
            ie_gep = f"getelementptr inbounds %struct.State, ptr {first[-1]}, {tail}"
            defs: dict[str, tuple[int, str]] = {}
            uses: dict[str, int] = {}
            for j in range(ds + 1, de):
                code = lines[j].split(";", 1)[0]
                if (m := RE_ASSIGN.match(code)):
                    defs[m.group(1)] = (j, m.group(2).strip())
                for n in re.findall(NAME, code):
                    uses[n] = uses.get(n, 0) + 1
            ie_ptrs = {n for n, (_, r) in defs.items() if r == ie_gep}
            per_define[ds] = (de, defs, uses, ie_ptrs, define_cfg(lines, ds, de))
        de, defs, uses, ie_ptrs, (block_of, succ, entry, unknown) = per_define[ds]
        k = bisect.bisect_left(bounds, e) - 1
        p = bounds[k] if k >= 0 and bounds[k] > ds else ds
        quiets, branches, stores = [], [], []
        for j in range(p + 1, e):
            code = lines[j].split(";", 1)[0].rstrip()
            if (m := RE_ASSIGN.match(code)) and re.match(rf"^or i80 {NAME}, {FLD_QUIET_BIT}$", m.group(2).strip()):
                quiets.append(j)
            elif (mb := RE_BR_COND.match(code)):
                branches.append((j, mb))
            elif (ms := RE_STORE_I8.match(lines[j])) and ms.group(3) in ie_ptrs:
                stores.append((j, ms.group(2)))

        def ie_load(n: str) -> bool:
            ml = re.match(rf"^load i8, ptr ({NAME})(?:, align \d+)?$", defs.get(n, (0, ""))[1])
            return bool(ml and ml.group(1) in ie_ptrs)

        if len(quiets) != 1:
            refuse(f"expected one quieting `or i80 _, {FLD_QUIET_BIT}` in the FLD, found {len(quiets)}")
            continue
        qblock = block_of[quiets[0]]
        into = [(j, mb) for j, mb in branches
                if qblock in (mb.group(2)[1:].strip('"'), mb.group(3)[1:].strip('"'))]
        if len(into) != 1 or into[0][1].group(2) == into[0][1].group(3):
            refuse(f"expected one conditional branch into the quieting block {qblock}, found {len(into)}")
            continue
        bj, mb = into[0]
        test = mb.group(1)
        tdef = defs.get(test)
        mt = RE_ICMP_I8_ZERO.match(f"{test} = {tdef[1]}") if tdef else None
        if not mt:
            refuse(f"the quieting branch tests {tdef[1] if tdef else test}, not `icmp eq/ne i8 _, 0`")
            continue
        tj, pred, r = tdef[0], mt.group(2), mt.group(3)
        if (pred == "ne") != (mb.group(2)[1:].strip('"') == qblock):
            refuse(f"`icmp {pred}` with the quieting block on the other arm: the branch does not quiet when IE is set")
            continue
        if uses.get(test, 0) != 2:
            refuse(f"the test {test} has uses beyond the quieting branch ({uses.get(test, 0) - 1})")
            continue
        if r == "0":
            if width == 80:
                already += 1
            else:
                refuse(f"the m{width} FLD's quieting test is a constant")
            continue
        if ie_load(r) and p < defs[r][0] < tj:
            before = [(j, v) for j, v in stores if j < defs[r][0]]
            if not before:
                refuse(f"IE ({r}) is read with no IE store of the FLD before it")
                continue
            store_value = before[-1][1]
        elif any(v == r for _, v in stores):
            store_value = r
        else:
            store_value = None
        sem_s = None
        if store_value is not None:
            mo = re.match(rf"^or i8 ({NAME}), ({NAME})$", defs.get(store_value, (0, ""))[1])
            if not mo or ie_load(mo.group(1)) == ie_load(mo.group(2)):
                refuse(f"the FLD's IE store is not `or i8 (load IE), S`: {defs.get(store_value, (0, store_value))[1]}")
                continue
            sem_s = mo.group(2) if ie_load(mo.group(1)) else mo.group(1)
        elif width != 80 and fld_snan_read(defs, r, width) and any(
                (mo := re.match(rf"^or i8 ({NAME}), ({NAME})$", defs.get(v, (0, ""))[1])) and r in mo.groups()
                for _, v in stores):
            already += 1                     # the test already reads the load's own NaN test
            continue
        else:
            refuse(f"the quieting branch tests {r}, which is neither IE read after the FLD's IE store nor "
                   f"that store's value")
            continue
        if width != 80:
            if not fld_snan_read(defs, sem_s, width):
                refuse(f"the value or-ed into IE ({sem_s}) is not remill's signalling-NaN test of the m{width} "
                       f"value the FLD reads")
                continue
            if unknown:
                refuse(f"the define names blocks it does not hold, so dominance cannot be shown: {sorted(unknown)[:5]}")
                continue
            sj, sblock, tblock = defs[sem_s][0], block_of[defs[sem_s][0]], block_of[tj]
            if not ((sblock == tblock and sj < tj) or (sblock != tblock and dominates(succ, entry, sblock, tblock))):
                refuse(f"the signalling-NaN test's block {sblock} does not dominate the quieting test's {tblock}")
                continue
        now = sem_s if width != 80 else "0"
        indent = lines[tj][:len(lines[tj]) - len(lines[tj].lstrip())]
        rewrites[tj] = f"{indent}{test} = icmp {pred} i8 {now}, 0{FLD_TAG}"
        sites.append({"function": func, "exit_label": exit_label, "width": width, "test": test,
                      "was": r, "now": now})
    return rewrites, sites, refused, already


def nearest_fdiv(lines: list[str], start: int, end: int) -> tuple[str, str] | None:
    """The operands of the last `fdiv x86_fp80` before `end` in the enclosing define.

    Kept only as a cross-check: `fpatan_divisor` below decides. Until the G2 fold this
    was the rule, and it never checked that the call's argument was that quotient.
    """
    for j in range(end - 1, start, -1):
        m = RE_OP.match(lines[j])
        if m and m.group(3) == "fdiv":
            args = [a.strip() for a in m.group(5).split(",")]
            return (args[0], args[1]) if len(args) == 2 else None
    return None


# The 80-bit value remill hands to `atanl` does not reach the call as an SSA value: the
# quotient is stored into a `union_ld`, memcpy'd ten bytes at a time into the semantic's
# `float80_t` return slot, reloaded as an i64 and an i16, stored into the argument slot,
# memcpy'd again and finally loaded back as an `x86_fp80`. These match the pieces.
RE_GEP = re.compile(rf"^\s+({NAME}) = getelementptr inbounds \S+, ptr ({NAME}), (.+)$")
RE_MEMCPY = re.compile(rf"^\s+(?:{NAME} = )?(?:tail )?call ptr @memcpy\(ptr [^,]*?({NAME}), "
                       rf"ptr [^,]*?({NAME}), i64 noundef (\d+)\)")
RE_STORE_FP80 = re.compile(rf"^\s+store x86_fp80 ({NAME}|0xK[0-9A-Fa-f]+), ptr ({NAME}),")
RE_STORE_INT = re.compile(rf"^\s+store (i64|i16) ({NAME}), ptr ({NAME}),")
RE_LOAD_FP80 = re.compile(rf"^\s+({NAME}) = load x86_fp80, ptr ({NAME}),")
RE_LOAD_INT = re.compile(rf"^\s+({NAME}) = load (i64|i16), ptr ({NAME}),")


def fpatan_divisor(lines: list[str], start: int, end: int, arg: str) -> tuple[str, str] | None:
    """The operands of the `fdiv x86_fp80` the value in `arg` actually came from, or None.

    A straight-line forward walk of the enclosing define up to the call, tracking which
    80-bit *slot* (alloca base) holds which SSA value through remill's store/memcpy/load
    chain. `arg` is resolved to the instruction that produced it; only when that is an
    `fdiv x86_fp80` is the rewrite safe, and then its own two operands are what x87's
    two-argument FPATAN takes. Anything the walk cannot follow -- a slot written in
    another block, a memcpy of a length other than ten, a value that is not a quotient at
    all -- returns None, and the caller refuses to rewrite and counts the site.

    Before the G2 fold the pass took the *nearest preceding* `fdiv` in the define and
    never checked it reached the call; this resolves it instead.
    """
    base: dict[str, str] = {}          # gep result -> the pointer it is an offset from
    slot_fp: dict[str, str] = {}       # slot base -> the fp80 SSA value it holds
    slot_int: dict[str, dict[str, str]] = {}   # slot base -> {"i64": name, "i16": name}
    int_from: dict[str, tuple[str, str]] = {}  # loaded integer half -> (slot base, width)
    origin: dict[str, str] = {}        # fp80 SSA value -> the fp80 SSA value it copies

    def root(p: str) -> str:
        seen = set()
        while p in base and p not in seen:
            seen.add(p)
            p = base[p]
        return p

    def carry(dst: str, src: str) -> None:
        """Propagate a slot's 80-bit contents to another slot."""
        if src in slot_fp:
            slot_fp[dst] = slot_fp[src]
        else:
            slot_fp.pop(dst, None)
        if src in slot_int:
            slot_int[dst] = dict(slot_int[src])
        else:
            slot_int.pop(dst, None)

    for j in range(start, end):
        line = lines[j]
        if (m := RE_GEP.match(line)):
            base[m.group(1)] = m.group(2)
            continue
        if (m := RE_MEMCPY.match(line)):
            dst, src, n = root(m.group(1)), root(m.group(2)), int(m.group(3))
            if n == 10:
                carry(dst, src)
            else:
                slot_fp.pop(dst, None)
                slot_int.pop(dst, None)
            continue
        if (m := RE_STORE_FP80.match(line)):
            slot_fp[root(m.group(2))] = m.group(1)
            continue
        if (m := RE_STORE_INT.match(line)):
            width, value, slot = m.group(1), m.group(2), root(m.group(3))
            src = int_from.get(value)
            if src and src[1] == width:
                # The two halves of one 80-bit value, split and reassembled: the slot
                # keeps its contents only while both halves come from the same source.
                held = slot_int.setdefault(slot, {})
                held[width] = src[0]
                if held.get("i64") == held.get("i16") == src[0]:
                    carry(slot, src[0])
                    slot_int[slot] = held
            else:
                slot_fp.pop(slot, None)
                slot_int.pop(slot, None)
            continue
        if (m := RE_LOAD_INT.match(line)):
            int_from[m.group(1)] = (root(m.group(3)), m.group(2))
            continue
        if (m := RE_LOAD_FP80.match(line)):
            held = slot_fp.get(root(m.group(2)))
            if held is not None:
                origin[m.group(1)] = held
            continue

    # Walk `arg` back to the value that defined it, then demand an `fdiv x86_fp80`.
    seen: set[str] = set()
    name = arg
    while name in origin and name not in seen:
        seen.add(name)
        name = origin[name]
    for j in range(start, end):
        m = RE_OP.match(lines[j])
        if m and m.group(2) == name and m.group(3) == "fdiv":
            args = [a.strip() for a in m.group(5).split(",")]
            return (args[0], args[1]) if len(args) == 2 else None
    return None


def instrument(text: str, helper_ll: str, int_helper_ll: str | None = None,
               fix_control_word: bool = True, fix_f80_accessors: bool = True,
               fix_fpatan: bool = True, fix_fxam: bool = True,
               fix_fld_quiet: bool = True) -> tuple[str, dict]:
    if text.startswith(MARKER):
        return text, {"already_instrumented": True, "ops_instrumented": 0}
    lines = text.splitlines()
    types = {m.group(1): split_fields(m.group(2)) for l in lines if (m := RE_TYPE.match(l))}
    gep = control_word_gep(types)
    helper_body, helper_declares = helper_definition(helper_ll)
    helper_defined = any(l.startswith("define") and f"@{HELPER}(" in l for l in lines)
    int_body: list[str] = []
    int_declares: list[str] = []
    int_defined = any(l.startswith("define") and f"@{ROUND_INT_HELPER}(" in l for l in lines)
    if int_helper_ll is not None:
        int_body, int_declares = helper_definition(
            int_helper_ll, INT_HELPERS,
            {ROUND_INT_HELPER: "x86_fp80", **{n: w for w, n in FISTP_HELPERS.items()}})
    declared = {m.group(1) for l in lines if l.startswith(("declare", "define")) and (m := re.search(r"@([\w.]+)\(", l))}
    enclosing = enclosing_semantics(lines)

    out: list[str] = [f"{MARKER}: precision-control rounding inserted after every x86_fp80 arithmetic op; helper {HELPER} sha256 {hashlib.sha256(helper_ll.encode()).hexdigest()[:16]}"]
    stats: dict = {"already_instrumented": False, "ops_instrumented": 0, "by_op": {}, "by_semantic": {}, "by_function": {}, "sites": [],
                   "skipped_by_semantic": {}, "skipped_sites": [], "skipped_non_lifted": 0, "skipped_non_lifted_defines": [],
                   "skipped_in_non_lifted": 0, "skipped_already_wrapped": 0, "by_semantic_enclosing": {},
                   "int_helper": bool(int_helper_ll), "fist_rewrites": 0, "by_fist_semantic": {}, "fist_sites": [],
                   "fix_control_word": bool(fix_control_word), "control_word_masks_fixed": 0, "control_word_bit6_set": 0, "control_word_bit6_refused": 0, "control_word_mask_sites": [],
                   "fix_f80_accessors": bool(fix_f80_accessors), "f80_accessor_attrs_fixed": 0, "f80_accessor_sites": [],
                   "fix_fpatan": bool(fix_fpatan), "fpatan_rewrites": 0, "fpatan_sites": [],
                   "fpatan_refused": 0, "fpatan_refused_sites": [],
                   "fpatan_nearest_fdiv_agreed": 0,
                   "fix_fxam": bool(fix_fxam), "fxam_c1_stores_fixed": 0, "fxam_sites": [],
                   "fxam_refused": 0, "fxam_refused_sites": [], "fxam_already_signed": 0,
                   "fix_fld_quiet": bool(fix_fld_quiet), "fld_quiet_fixed": 0, "fld_quiet_sites": [],
                   "fld_quiet_refused": 0, "fld_quiet_refused_sites": [], "fld_quiet_already": 0}
    f80_rewrites, f80_groups, f80_sites = f80_accessor_fix(lines) if fix_f80_accessors else ({}, [], [])
    stats["f80_accessor_attrs_fixed"] = len(f80_sites)
    stats["f80_accessor_sites"] = f80_sites
    fxam_rewrites, fxam_sites, fxam_refused, fxam_already = (
        fxam_c1_fix(lines, types) if fix_fxam else ({}, [], [], 0))
    stats["fxam_c1_stores_fixed"] = len(fxam_rewrites)
    stats["fxam_sites"] = fxam_sites
    stats["fxam_refused"] = len(fxam_refused)
    stats["fxam_refused_sites"] = fxam_refused
    stats["fxam_already_signed"] = fxam_already
    fld_rewrites, fld_sites, fld_refused, fld_already = (
        fld_quiet_fix(lines, types) if fix_fld_quiet else ({}, [], [], 0))
    stats["fld_quiet_fixed"] = len(fld_rewrites)
    stats["fld_quiet_sites"] = fld_sites
    stats["fld_quiet_refused"] = len(fld_refused)
    stats["fld_quiet_refused_sites"] = fld_refused
    stats["fld_quiet_already"] = fld_already
    state_var = None      # the State pointer of the current lifted define, None outside one
    func = None           # the current define's name, lifted or not
    lifted = False
    label = None
    define_has_cw_gep = False
    define_start = 0

    def cw_lines(indent: str, bare: str) -> tuple[str, list[str]]:
        """The two lines that load the live control word beside a site, and the SSA name holding it."""
        cwp, cw = f"%sa2cwp.{bare}", f"%sa2cw.{bare}"
        return cw, [f"{indent}{cwp} = getelementptr inbounds %struct.State, ptr {state_var}, {gep}",
                    f"{indent}{cw} = load i16, ptr {cwp}, align 2"]

    for ix, line in enumerate(lines):
        if ix in f80_rewrites:
            out.append(f80_rewrites[ix])
            continue
        if ix in fxam_rewrites:
            out.append(fxam_rewrites[ix])
            continue
        if ix in fld_rewrites:
            out.append(fld_rewrites[ix])
            continue
        if line.startswith("define "):
            m = RE_DEFINE.match(line)
            func = m.group(1)
            params = split_fields(m.group(2))
            first = params[0].split() if params else []
            lifted = bool(first) and first[0] == "ptr"
            state_var = first[-1] if lifted else None
            label = None
            define_has_cw_gep = False
            define_start = ix
            if not lifted:
                stats["skipped_non_lifted"] += 1
                stats["skipped_non_lifted_defines"].append(func)
            else:
                # Does this define ever touch the control word? (the mask rewrite is gated on it)
                for j in range(ix + 1, len(lines)):
                    if lines[j] == "}":
                        break
                    if f"getelementptr inbounds %struct.State, ptr {state_var}, {gep}" in lines[j]:
                        define_has_cw_gep = True
                        break
        elif line == "}":
            func = state_var = label = None
            lifted = False
            define_has_cw_gep = False
            define_start = ix
        elif (m := RE_LABEL.match(line)):
            label = m.group(1)

        def semantic_here() -> tuple[str, bool]:
            """(semantic, attributed by the enclosing rule): nearest preceding label first, then forward."""
            s = semantic_of(label)
            if s != NO_SEMANTIC:
                return s, False
            return enclosing[ix], enclosing[ix] != NO_SEMANTIC

        m = RE_OP.match(line) or RE_SQRT.match(line) or RE_SQRTL.match(line)
        if m and func:
            indent, name = m.group(1), m.group(2)
            sem, by_enclosing = semantic_here()
            is_arith = bool(RE_OP.match(line))
            is_intrinsic_sqrt = bool(RE_SQRT.match(line))
            if not lifted:
                stats["skipped_in_non_lifted"] += 1
            elif name.startswith("%sa2pre."):
                stats["skipped_already_wrapped"] += 1
            elif not RE_PC_SEMANTIC.match(sem):
                op = m.group(3) if is_arith else "sqrt"
                stats["skipped_by_semantic"][sem] = stats["skipped_by_semantic"].get(sem, 0) + 1
                stats["skipped_sites"].append({"function": func, "result": name, "op": op, "remill_semantic": sem, "label": label})
                # the division whose quotient remill hands to `atanl`: keep both operands
            else:
                bare = name[1:].strip('"')
                pre = f"%sa2pre.{bare}"
                if is_arith:
                    op = m.group(3)
                    out.append(f"{indent}{pre} = {op}{m.group(4)} x86_fp80 {m.group(5)}")
                elif is_intrinsic_sqrt:
                    op = "sqrt"
                    out.append(f"{indent}{pre} = call x86_fp80 @llvm.sqrt.f80({m.group(3)}){m.group(4)}")
                else:
                    op = "sqrtl"
                    out.append(f"{indent}{pre} = call x86_fp80 @sqrtl(x86_fp80 noundef {m.group(3)}){m.group(4)}")
                cw, loads = cw_lines(indent, bare)
                out.extend(loads)
                out.append(f"{indent}{name} = call x86_fp80 @{HELPER}(x86_fp80 {pre}, i16 {cw})   ; sa2-pc24pass")
                stats["ops_instrumented"] += 1
                stats["by_op"][op] = stats["by_op"].get(op, 0) + 1
                stats["by_semantic"][sem] = stats["by_semantic"].get(sem, 0) + 1
                stats["by_function"][func] = stats["by_function"].get(func, 0) + 1
                if by_enclosing:
                    stats["by_semantic_enclosing"][sem] = stats["by_semantic_enclosing"].get(sem, 0) + 1
                stats["sites"].append({"function": func, "result": name, "op": op, "remill_semantic": sem, "label": label,
                                       "attributed_by_enclosing_rule": by_enclosing})
                continue

        # ---- FIST / FISTP: the host rounding mode and the poison conversion ----
        if int_helper_ll is not None and lifted and func:
            mi = RE_NEARBYINT.match(line)
            mf = RE_FPTOSI.match(line) if not mi else None
            if mi or mf:
                sem, by_enclosing = semantic_here()
                if RE_FIST_SEMANTIC.match(sem):
                    indent = (mi or mf).group(1)
                    name = (mi or mf).group(2)
                    src = (mi or mf).group(3)
                    bare = name[1:].strip('"')
                    cw, loads = cw_lines(indent, bare)
                    out.extend(loads)
                    if mi:
                        out.append(f"{indent}{name} = call x86_fp80 @{ROUND_INT_HELPER}(x86_fp80 {src}, i16 {cw})   ; sa2-pc24pass fist")
                        kind = "nearbyint"
                    else:
                        width = mf.group(4)
                        out.append(f"{indent}{name} = call {width} @{FISTP_HELPERS[width]}(x86_fp80 {src}, i16 {cw})   ; sa2-pc24pass fist")
                        kind = f"fptosi.{width}"
                    stats["fist_rewrites"] += 1
                    stats["by_fist_semantic"][sem] = stats["by_fist_semantic"].get(sem, 0) + 1
                    stats["fist_sites"].append({"function": func, "result": name, "kind": kind, "remill_semantic": sem,
                                                "label": label, "attributed_by_enclosing_rule": by_enclosing})
                    continue

        # ---- FPATAN: remill's one-argument `atanl` loses the quadrant ----
        if fix_fpatan and lifted and (ma := RE_ATANL.match(line)):
            sem, by_enclosing = semantic_here()
            indent, name, quotient, rest = ma.groups()
            # The call's own argument, resolved backwards through remill's store/memcpy/
            # load chain. `nearest_fdiv` is kept only to record how often the old
            # unverified rule agreed with it.
            div = fpatan_divisor(lines, define_start, ix, quotient)
            guess = nearest_fdiv(lines, define_start, ix)
            if RE_FPATAN_SEMANTIC.match(sem) and div:
                num, den = div
                out.append(f"{indent}{name} = call x86_fp80 @{ATAN2}(x86_fp80 noundef {num}, "
                           f"x86_fp80 noundef {den}){rest}   ; sa2-pc24pass fpatan")
                stats["fpatan_rewrites"] += 1
                if guess == div:
                    stats["fpatan_nearest_fdiv_agreed"] += 1
                stats["fpatan_sites"].append({"function": func, "result": name, "quotient": quotient,
                                              "st1": num, "st0": den, "remill_semantic": sem,
                                              "label": label, "attributed_by_enclosing_rule": by_enclosing,
                                              "nearest_fdiv_agreed": guess == div})
                continue
            if RE_FPATAN_SEMANTIC.match(sem):
                # Under DoFPATAN, but the argument could not be shown to be a quotient:
                # rewriting would change the meaning on an unverified guess, so it is
                # left as remill emitted it and the site is counted.
                stats["fpatan_refused"] += 1
                stats["fpatan_refused_sites"].append(
                    {"function": func, "result": name, "argument": quotient,
                     "reason": "the call's argument does not resolve to an `fdiv x86_fp80`",
                     "nearest_fdiv": list(guess) if guess else None,
                     "remill_semantic": sem, "label": label})

        # ---- FLDCW / FNSTCW: remill's masks drop the precision-control field ----
        if fix_control_word and lifted and define_has_cw_gep and (mc := RE_CW_MASK.match(line)):
            indent, name, src, mask = mc.groups()
            # remill's FNSTCW builds its result in four arms, each ending in a
            # branch to the semantic's exit block; the bit is set only there, so
            # a guest `and ax,0F0FFh` that happens to share the constant is left
            # alone (2026-09-23 review).
            in_fnstcw = mask == "-3841" and any(
                "FNSTCW" in nxt or "FSTCW" in nxt for nxt in lines[ix + 1:ix + 4]
                if nxt.lstrip().startswith("br "))
            if mask == "-3841" and not in_fnstcw:
                stats["control_word_bit6_refused"] += 1
            if in_fnstcw:
                # FNSTCW's read-back: keep PC, clear RC for the rebuild, set bit 6.
                out.append(f"{indent}{name}.sa2cw = and i16 {src}, {CW_MASK_FIX[mask]}"
                           f"   ; sa2-pc24pass cw ({mask})")
                out.append(f"{indent}{name} = or i16 {name}.sa2cw, 64   ; sa2-pc24pass cw bit 6")
                stats["control_word_bit6_set"] += 1
            else:
                out.append(f"{indent}{name} = and i16 {src}, {CW_MASK_FIX[mask]}"
                           f"   ; sa2-pc24pass cw ({mask})")
            stats["control_word_masks_fixed"] += 1
            stats["control_word_mask_sites"].append({"function": func, "result": name, "was": mask,
                                                     "now": CW_MASK_FIX[mask], "label": label})
            continue

        out.append(line)

    if stats["ops_instrumented"] and not helper_defined:
        out.append("")
        out.extend(helper_body)
        for d in helper_declares:
            if re.search(r"@([\w.]+)\(", d).group(1) not in declared:
                out.append(d)
    if stats["fist_rewrites"] and not int_defined:
        out.append("")
        out.extend(int_body)
        for d in int_declares:
            if re.search(r"@([\w.]+)\(", d).group(1) not in declared:
                out.append(d)
    if stats["fpatan_rewrites"] and ATAN2 not in declared:
        out.append(ATAN2_DECLARE)
    out.extend(f80_groups)
    return "\n".join(out) + "\n", stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--helper-ll", required=True, type=Path, help="pc_round.c compiled with clang -S -emit-llvm")
    ap.add_argument("--int-helper-ll", type=Path, help="tools/s2/patch/x87_int.c compiled the same way; enables the FIST/FISTP rewrite")
    ap.add_argument("--no-fix-control-word", action="store_true",
                    help="leave remill's FLDCW/FNSTCW masks alone (they clear the precision-control field)")
    ap.add_argument("--no-fix-fpatan", action="store_true",
                    help="leave remill's one-argument `atanl` alone (it loses FPATAN's quadrant)")
    ap.add_argument("--no-fix-fxam", action="store_true",
                    help="leave remill's FXAM alone (it stores C1 = 0 for a zero, NaN or infinity, "
                         "losing the sign the x87 reports on every class)")
    ap.add_argument("--no-fix-fld-quiet", action="store_true",
                    help="leave remill's FLD m32/m64/m80 quieting test alone (it quiets every value it "
                         "loads while the sticky IE flag is set, not only a signalling NaN)")
    ap.add_argument("--no-fix-f80-accessors", action="store_true",
                    help="leave remill's `readnone` on __remill_read/write_memory_f80 alone "
                         "(it makes every `fld tbyte` load 0.0 at -O2)")
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--stats", type=Path)
    a = ap.parse_args()
    text = a.input.read_text()
    out, stats = instrument(text, a.helper_ll.read_text(),
                            a.int_helper_ll.read_text() if a.int_helper_ll else None,
                            not a.no_fix_control_word, not a.no_fix_f80_accessors,
                            not a.no_fix_fpatan, not a.no_fix_fxam, not a.no_fix_fld_quiet)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(out)
    stats.update({"input": str(a.input), "output": str(a.output), "input_lines": text.count("\n"), "output_lines": out.count("\n"),
                  "input_sha256": hashlib.sha256(text.encode()).hexdigest(), "output_sha256": hashlib.sha256(out.encode()).hexdigest()})
    if a.stats:
        a.stats.write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps({k: v for k, v in stats.items() if k not in ("sites", "skipped_sites")}), file=sys.stderr)


if __name__ == "__main__":
    main()
