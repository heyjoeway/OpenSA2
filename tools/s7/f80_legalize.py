#!/usr/bin/env python3
"""f80_legalize: rewrite every x86_fp80 operation in lifted IR into explicit calls, for a host
whose long double is not the x87's.

Subtitle: the arm64 port's IR side. remill computes the guest's x87 arithmetic in LLVM's
`x86_fp80`. An x86 backend lowers that to the host's x87, so the x86-64 build's float results
are the hardware's. The AArch64 backend does not: it aborts on `fpext ... to x86_fp80`, and
the modules it does compile are wrong without an error, because its `fcmp` becomes PowerPC
`__gcc_q*` calls, libm `*l` calls pass x86_fp80 where aarch64's long double is binary128, and
`fesetround` is handed the x86's rounding constants (the S7 port survey; its counts are in
private/generated/s7/wave1/port-survey/image/aarch64-linux-musl.next.summary.json). This
pass, in the style of tools/spike/pc24pass/pc24pass.py (textual, deterministic, idempotent,
refusing what it does not know), rewrites each x86_fp80 operation so that what is left of
the type is data movement: load, store, alloca, phi, select, bitcast, ret, and calls of the
internal helpers the PC24 pass spliced in (integer code on the bit pattern). Then the type itself
goes: every remaining `x86_fp80` becomes `i80` and every `0xK` constant its integer, so that no
x86_fp80 reaches the backend at all. That last step is not tidiness. At -O2 the AArch64
backend lowers a plain `load x86_fp80` / `store x86_fp80` pair to a four-byte copy (measured:
`ldr w8, [x1]; str w8, [x0]`; -O0 copies the ten bytes), and a `select` or `phi` of two
loaded x86_fp80 values stores zero; `i80` loads and stores move exactly the ten bytes on both
targets. Both types have a store size of 10 and an allocation size of 16 on x86-64 and on
aarch64 (x86_fp80 is f80:128 on x86-64; i80 takes i128's alignment on both), so every
alloca, GEP and union over them keeps its layout. A named type whose body holds x86_fp80
(remill's `%union.union_ld = type { x86_fp80 }`) changes with it, so it is held to the same
rule: alloca, GEP, load and store, or an internal function's signature, and refused anywhere
else (an external call returning one would change that call's ABI without an error).
Fast-math flags on a phi, select or call whose value is x86_fp80 are dropped with the type,
since an integer value cannot carry them; they only ever licensed optimisations.

What each operation becomes (the 80-bit value travels as the low 80 bits of an i128, the
value's memory image; see tools/s7/f80_shims.c for the semantics of every callee):

  fadd/fsub/fmul/fdiv         sa2_lf80_add/sub/mul/div            (rounded under the host RC)
  fcmp <pred>                 sa2_lf80_cmp(a, b, mask) != 0       (mask: the predicate's outcomes)
  fpext float|double          sa2_lf80_from_f32/f64               (FLD m32/m64, SNaN quieted)
  fptrunc to float|double     sa2_lf80_to_f32/f64                 (FST m32/m64 under RC)
  sitofp i8/i16/i32/i64       sext to i64, sa2_lf80_from_i64      (FILD; exact)
  uitofp i8/i16/i32           zext to i64, sa2_lf80_from_i64
  fptosi to i16/i32/i64       sa2_lf80_to_i16/i32/i64             (truncation, x87 indefinite)
  fneg, llvm.fabs.f80, fabsl, xor/and/or on the sign bit         (inline integer code)
  llvm.copysign.f80, copysignl
  llvm.sqrt.f80, sqrtl        sa2_lf80_sqrt
  llvm.nearbyint/rint.f80,    sa2_lf80_rint                       (FRNDINT under the host RC)
    nearbyintl, rintl
  llvm.trunc/floor/ceil.f80,  sa2_lf80_trunc/floor/ceil           (musl's x87 frndint forms)
    truncl, floorl, ceill
  fmodl, remainderl           sa2_lf80_fmod/remainder             (FPREM/FPREM1 to completion)
  sinl cosl tanl atanl atan2l sa2_lf80_<name>                     (design 3.4: host libm in
    exp2l log2l expl logl                                         double, a labelled residue)
    log10l powl asinl acosl
  fesetround, fegetround,     sa2_x87_<name>                     (the x86 FE_* constants
    fetestexcept, feraiseexcept, feclearexcept                    mapped to the host's)

Every reference to a host fenv function is a call the pass renames; any other reference (its
address taken, say) is refused, since renaming the declaration alone would leave it undefined.

No compiler-rt routine is used. `selftest` calls each `__*xf*` candidate beside the x87 and
beside the shim, and none survives both targets. On x86-64 only `__floatsixf` and
`__floatdixf` are exact: the arithmetic and the narrowing conversions round to nearest only
(zig's `addf3.zig` reads no rounding mode), their NaN results follow IEEE rather than the
x87's larger-significand rule, `__extendsfxf2` keeps a signalling NaN signalling where FLD
quiets it, and `__fixxfdi` saturates where FISTP stores the integer indefinite. On aarch64
every one of them, the two exact integer conversions included, returns other bytes than on
x86-64 for the same inputs, measured with the candidates' operands loaded and stored as i80
so that the backend's x86_fp80 memory operations are not in the path: zig's compiler-rt and
the AArch64 backend's lowering of an x86_fp80 argument or return value do not agree on where
the 80 bits travel. The shims pass the value as an i128, which both sides lower the same way.

`census` is the backend-side check. It is an allowlist: every undefined symbol of every
legalized object must be a lifted function (`sub_<hex>`), a remill intrinsic (`__remill_*`),
a runtime entry point of this project (`__sa2_*`, `sa2_guest_*`), a shim that the shims
object for the same target defines (read from it with nm, so a misspelt shim fails), or one of
the few host libc functions named in HOST_LIBC. Anything else fails the census; the known
f80 hazards (`__gcc_q*`, the soft-float `xf`/`tf` routines, libm's `*l` functions, the raw
fenv calls) are named in the failure, and any other name fails as "not on the allowlist".

What the proofs cover. The pass is target-independent: its output compiles for any triple,
x86-64 included, which is how it is proved behaviour-neutral on this machine
(tools/s7/cross_build.py builds it for x86_64-linux-musl and the boot and Metal Harbor replays
are run against the default build). Those replays run the guest's arithmetic under round-to-
nearest and reach no rounding tie that the shims' tie rule decides (a review's mutated shims,
round-to-nearest forced under every RC and ties rounded away, left both replays unchanged), so
they prove the round-to-nearest arithmetic and conversions; the directed rounding controls and
the ties rest on the selftest alone. The selftest is the shims' own proof: every exact
operation, the PC24 helpers, select and phi, first over a fixed table of special encodings
crossed for every operand (zeros, denormals, pseudo-denormals, unnormals, pseudo-infinities,
pseudo-NaNs, NaN pairs of equal and unequal payloads, the integer and float formats' limits
and their neighbours, rounding ties) and then over random encodings, all under the four
rounding controls, against this machine's x87; the rounding control's round trip through
fesetround and fegetround; the exception calls' bit mapping, in the masks remill passes; and
the same module built for aarch64 under qemu-user against the x86-64 bytes. Its oracle is this
machine's CPU, which the summary names: the NaN tie rule in f80_shims.c's nan2 was measured
on it, and another x87 that settles equal-significand NaN ties differently would fail it.

Two divergence classes lie outside what the shims make equal, and a cross-target difference
in either is not a shim error:

  - The x86-64 build's optimiser folds x86_fp80 operations under IEEE identities and the
    default FP environment (`fmul x, 1.0` to `x`, for one), and a call here is opaque to it.
    At such a site the two builds differ for a signalling NaN or an unsupported encoding (the
    x87, like the shim, quiets the one and returns the indefinite for the other) and in a
    zero's sign under a directed rounding control; the legalized build is the one that
    matches the native x87. Legalizing the IR after the x86-64 optimiser's run, not before
    it, would remove the class.
  - The transcendentals' residue (design 3.4) is the host libm's, so it differs between
    hosts, and on aarch64 part of it is the toolchain's: zig's compiler_rt supplies sin, cos,
    tan, the logarithms and the exponentials, and musl's asin built by the pinned zig for
    aarch64 at -O2 contracts to fused multiply-adds. One portable double implementation for
    every target is the port survey's question 3, the user's to rule on.

Usage:
  .venv/bin/python tools/s7/f80_legalize.py rewrite IN.ll OUT.ll [--stats stats.json]
  .venv/bin/python tools/s7/f80_legalize.py census --shims SHIMS.o OBJ_OR_DIR... [--json out.json]
  source tools/env.sh
  .venv/bin/python tools/s7/f80_legalize.py selftest --out DIR [--target aarch64-linux-musl]
      [--vectors 20000]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

def _root() -> Path:
    """The repository: the nearest directory above this file holding AGENTS.md and
    tools/pins.json. In the tree that is this file's grandparent; a copy of this file in a
    scratch snapshot under the tree finds the tree, so it reads and names the same paths."""
    for p in Path(__file__).resolve().parents:
        if (p / "AGENTS.md").is_file() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"no repository above {__file__}")


ROOT = _root()
SHIMS_C = Path(__file__).resolve().parent / "f80_shims.c"
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
QEMU_AARCH64 = (ROOT / "private" / "predecessor-tools" / "qemu" / "root" / "usr" / "bin"
                / "qemu-aarch64")

MARKER = ("; sa2-f80-legalize v4: every x86_fp80 operation is a call and the type is i80 "
          "(tools/s7/f80_legalize.py)")
TAG = "   ; sa2-f80"

NAME = r'%(?:"[^"]+"|[-A-Za-z$._][-A-Za-z$._0-9]*|[0-9]+)'
F80C = r"0xK[0-9A-Fa-f]{20}"
OPND = rf"(?:{NAME}|{F80C})"
SCALAR = r"(?:[^,\s()]+)"                           # a float/int operand: a name or a constant
FMF = r"(?: (?:fast|nnan|ninf|nsz|arcp|contract|afn|reassoc))*"
TAIL = r"((?:, ![\w.]+ !\d+)*)(\s*;.*)?"
PREDS = {"false": 0, "oeq": 1, "olt": 2, "ole": 3, "ogt": 4, "oge": 5, "one": 6, "ord": 7,
         "uno": 8, "ueq": 9, "ult": 10, "ule": 11, "ugt": 12, "uge": 13, "une": 14, "true": 15}

RE_BIN = re.compile(rf"^(\s+)({NAME}) = (fadd|fsub|fmul|fdiv|frem){FMF} x86_fp80 ({OPND}), "
                    rf"({OPND}){TAIL}$")
RE_FCMP = re.compile(rf"^(\s+)({NAME}) = fcmp{FMF} (\w+) x86_fp80 ({OPND}), ({OPND}){TAIL}$")
RE_FPEXT = re.compile(rf"^(\s+)({NAME}) = fpext (float|double) ({SCALAR}) to x86_fp80{TAIL}$")
RE_FPTRUNC = re.compile(rf"^(\s+)({NAME}) = fptrunc x86_fp80 ({OPND}) to (float|double){TAIL}$")
RE_ITOF = re.compile(rf"^(\s+)({NAME}) = (sitofp|uitofp) (i\d+) ({SCALAR}) to x86_fp80{TAIL}$")
RE_FTOI = re.compile(rf"^(\s+)({NAME}) = (fptosi|fptoui) x86_fp80 ({OPND}) to (i\d+){TAIL}$")
RE_FNEG = re.compile(rf"^(\s+)({NAME}) = fneg{FMF} x86_fp80 ({OPND}){TAIL}$")
RE_CALL = re.compile(rf"^(\s+)(?:({NAME}) = )?((?:tail |musttail |notail )?call){FMF} "
                     rf"([^@]*?) ?@([-\w.$]+)\(")
RE_DEFINE = re.compile(r"^define\s+([^@]*)@([-\w.$]+)\(")
RE_DECLARE = re.compile(r"^declare\s+[^@]*@([-\w.$]+)\(")

SIGN = 1 << 79
F80_MASK = (1 << 80) - 1

# libm and intrinsic callees: name -> (shim, arity, kind). kind "inline" is integer code here.
EXACT = {"sqrtl": ("sa2_lf80_sqrt", 1), "llvm.sqrt.f80": ("sa2_lf80_sqrt", 1),
         "nearbyintl": ("sa2_lf80_rint", 1), "rintl": ("sa2_lf80_rint", 1),
         "llvm.nearbyint.f80": ("sa2_lf80_rint", 1), "llvm.rint.f80": ("sa2_lf80_rint", 1),
         "truncl": ("sa2_lf80_trunc", 1), "llvm.trunc.f80": ("sa2_lf80_trunc", 1),
         "floorl": ("sa2_lf80_floor", 1), "llvm.floor.f80": ("sa2_lf80_floor", 1),
         "ceill": ("sa2_lf80_ceil", 1), "llvm.ceil.f80": ("sa2_lf80_ceil", 1),
         "fmodl": ("sa2_lf80_fmod", 2), "remainderl": ("sa2_lf80_remainder", 2)}
# design 3.4: "Transcendentals call host libm in double and round; labelled residue."
TRANSCENDENTAL = {"sinl": 1, "cosl": 1, "tanl": 1, "atanl": 1, "atan2l": 2, "exp2l": 1,
                  "log2l": 1, "expl": 1, "logl": 1, "log10l": 1, "powl": 2, "asinl": 1,
                  "acosl": 1}
INLINE = {"llvm.fabs.f80": "fabs", "fabsl": "fabs", "llvm.copysign.f80": "copysign",
          "copysignl": "copysign"}
FENV = {"fesetround": "sa2_x87_fesetround", "fegetround": "sa2_x87_fegetround",
        "fetestexcept": "sa2_x87_fetestexcept", "feraiseexcept": "sa2_x87_feraiseexcept",
        "feclearexcept": "sa2_x87_feclearexcept"}
FENV_REFUSED = ("fegetenv", "fesetenv", "feholdexcept", "feupdateenv", "fegetexceptflag",
                "fesetexceptflag")

# Memory effects of each callee, so the optimiser may move a call past guest memory traffic
# but never past a call that can change the rounding mode (any call without attributes may).
RC_READ = "nounwind willreturn memory(inaccessiblemem: read)"
PURE = "nounwind willreturn memory(none)"
DECLS = {
    "sa2_lf80_add": ("i128", "i128, i128", RC_READ), "sa2_lf80_sub": ("i128", "i128, i128", RC_READ),
    "sa2_lf80_mul": ("i128", "i128, i128", RC_READ), "sa2_lf80_div": ("i128", "i128, i128", RC_READ),
    "sa2_lf80_sqrt": ("i128", "i128", RC_READ), "sa2_lf80_rint": ("i128", "i128", RC_READ),
    "sa2_lf80_trunc": ("i128", "i128", PURE), "sa2_lf80_floor": ("i128", "i128", PURE),
    "sa2_lf80_ceil": ("i128", "i128", PURE),
    "sa2_lf80_fmod": ("i128", "i128, i128", PURE),
    "sa2_lf80_remainder": ("i128", "i128, i128", PURE),
    "sa2_lf80_cmp": ("i32", "i128, i128, i32", PURE),
    "sa2_lf80_from_f32": ("i128", "float", PURE), "sa2_lf80_from_f64": ("i128", "double", PURE),
    "sa2_lf80_to_f32": ("float", "i128", RC_READ), "sa2_lf80_to_f64": ("double", "i128", RC_READ),
    "sa2_lf80_to_i16": ("i16", "i128", PURE), "sa2_lf80_to_i32": ("i32", "i128", PURE),
    "sa2_lf80_to_i64": ("i64", "i128", PURE),
    "sa2_lf80_from_i64": ("i128", "i64", PURE),
}
for _n, _a in TRANSCENDENTAL.items():
    # They call libm and count their calls: no memory attribute.
    DECLS["sa2_lf80_" + _n[:-1]] = ("i128", ", ".join(["i128"] * _a), "nounwind")

# What an x86_fp80 may still appear in after the pass: data movement and type definitions.
ALLOWED = [re.compile(p) for p in (
    rf"^\s+{NAME} = load (?:volatile )?x86_fp80, ptr ",
    r"^\s+store (?:volatile )?x86_fp80 ",
    rf"^\s+{NAME} = alloca x86_fp80\b",
    rf"^\s+{NAME} = phi{FMF} x86_fp80 ",
    rf"^\s+{NAME} = select{FMF} i1 [^,]+, x86_fp80 [^,]+, x86_fp80 ",
    rf"^\s+{NAME} = bitcast x86_fp80 {OPND} to i80\b",
    rf"^\s+{NAME} = bitcast i80 {NAME} to x86_fp80\b",
    rf"^\s+{NAME} = freeze x86_fp80 ",
    r"^\s+ret x86_fp80 ",
    rf"^\s+{NAME} = getelementptr (?:inbounds |nuw |nusw )*x86_fp80, ptr ",
    r"^%[-\w.$\"]+ = type ",
)]


class Refused(Exception):
    """A module the pass does not know how to make legal; it is never half-rewritten."""


def i128_of(opnd: str) -> str | None:
    """The i128 literal of an x86_fp80 constant (`0xK` and 20 hex digits: the 80-bit image)."""
    if opnd.startswith("0xK"):
        return str(int(opnd[3:], 16))
    return None


def split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def call_parts(line: str, start: int) -> tuple[str, str]:
    """(argument text, text after the closing parenthesis) of the call whose `(` is at start-1."""
    depth = 1
    i = start
    while i < len(line) and depth:
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
        i += 1
    if depth:
        raise Refused(f"unbalanced call: {line.strip()[:200]}")
    return line[start:i - 1], line[i:]


def arg_value(arg: str, want: str) -> str:
    toks = arg.split()
    if not toks or toks[0] != want:
        raise Refused(f"expected a {want} argument, got {arg!r}")
    return toks[-1]


class Writer:
    """Emits the replacement lines for one module, with fresh value names."""

    def __init__(self) -> None:
        self.n = 0
        self.used: set[str] = set()

    def fresh(self) -> str:
        self.n += 1
        return f"%sa2f80.{self.n}"

    def wide(self, out: list[str], ind: str, v: str) -> str:
        """An x86_fp80 operand as i128 (its 80-bit image, zero-extended)."""
        lit = i128_of(v)
        if lit is not None:
            return lit
        b = self.fresh()
        out.append(f"{ind}{b}.i = bitcast x86_fp80 {v} to i80")
        out.append(f"{ind}{b}.w = zext i80 {b}.i to i128")
        return f"{b}.w"

    def narrow(self, out: list[str], ind: str, res: str, w: str, what: str) -> None:
        b = self.fresh()
        out.append(f"{ind}{b}.t = trunc i128 {w} to i80")
        out.append(f"{ind}{res} = bitcast i80 {b}.t to x86_fp80{TAG} {what}")

    def bits(self, out: list[str], ind: str, v: str) -> str:
        lit = i128_of(v)
        if lit is not None:
            return lit
        b = self.fresh()
        out.append(f"{ind}{b}.b = bitcast x86_fp80 {v} to i80")
        return f"{b}.b"

    def call(self, out: list[str], ind: str, fn: str, args: list[str], res: str | None = None,
             what: str = "") -> str:
        self.used.add(fn)
        ret, params, _ = DECLS[fn]
        types = [t.strip() for t in params.split(",")]
        name = res or f"{self.fresh()}.r"
        out.append(f"{ind}{name} = call {ret} @{fn}("
                   + ", ".join(f"{t} {a}" for t, a in zip(types, args)) + ")"
                   + (f"{TAG} {what}" if what else ""))
        return name

    def f80_call(self, out: list[str], ind: str, res: str, fn: str, f80_args: list[str],
                 what: str) -> None:
        w = self.call(out, ind, fn, [self.wide(out, ind, a) for a in f80_args])
        self.narrow(out, ind, res, w, what)


def rewrite_call(m: re.Match, line: str, wr: Writer, out: list[str], st: dict,
                 internal: set[str]) -> bool:
    """A call line: True when it was rewritten into `out`, False to keep it as it is."""
    ind, res, callee = m.group(1), m.group(2), m.group(5)
    ret = (m.group(4).split() or [""])[-1]
    if callee in FENV:
        args, after = call_parts(line, m.end())
        st["fenv"][callee] = st["fenv"].get(callee, 0) + 1
        out.append(line[:m.start(5)] + FENV[callee] + line[m.end(5):])
        return True
    if callee in FENV_REFUSED:
        raise Refused(f"{callee}: the x87 environment is not modelled: {line.strip()[:200]}")
    if "x86_fp80" not in line:
        return False
    if callee in internal:
        return False                     # a helper the PC24 pass spliced in: integer code
    args, after = call_parts(line, m.end())
    parts = split_args(args)
    if after.strip() and not re.fullmatch(r"(?:\s*#\d+)?(?:, ![\w.]+ !\d+)*(?:\s*;.*)?\s*",
                                          after):
        raise Refused(f"unexpected text after a call: {line.strip()[:200]}")
    if res is None or ret != "x86_fp80":
        raise Refused(f"a call passing x86_fp80 to {callee}: {line.strip()[:200]}")
    vals = [arg_value(a, "x86_fp80") for a in parts]
    if callee in INLINE:
        kind = INLINE[callee]
        if kind == "fabs":
            if len(vals) != 1:
                raise Refused(f"fabs arity: {line.strip()[:200]}")
            x = wr.bits(out, ind, vals[0])
            b = wr.fresh()
            out.append(f"{ind}{b}.a = and i80 {x}, {F80_MASK ^ SIGN}")
            out.append(f"{ind}{res} = bitcast i80 {b}.a to x86_fp80{TAG} fabs")
        else:
            if len(vals) != 2:
                raise Refused(f"copysign arity: {line.strip()[:200]}")
            x, y = wr.bits(out, ind, vals[0]), wr.bits(out, ind, vals[1])
            b = wr.fresh()
            out.append(f"{ind}{b}.m = and i80 {x}, {F80_MASK ^ SIGN}")
            out.append(f"{ind}{b}.s = and i80 {y}, {SIGN}")
            out.append(f"{ind}{b}.o = or i80 {b}.m, {b}.s")
            out.append(f"{ind}{res} = bitcast i80 {b}.o to x86_fp80{TAG} copysign")
        st["inline"][callee] = st["inline"].get(callee, 0) + 1
        st["replaced_callees"].add(callee)
        return True
    if callee in EXACT:
        fn, arity = EXACT[callee]
        kind = "exact"
    elif callee in TRANSCENDENTAL:
        fn, arity = "sa2_lf80_" + callee[:-1], TRANSCENDENTAL[callee]
        kind = "transcendental"
    elif callee.startswith("llvm.") and callee.endswith(".f80"):
        raise Refused(f"an x86_fp80 intrinsic this pass has no semantics for: {callee}")
    else:
        raise Refused(f"a call passing x86_fp80 to {callee}: {line.strip()[:200]}")
    if len(vals) != arity:
        raise Refused(f"{callee} with {len(vals)} arguments: {line.strip()[:200]}")
    wr.f80_call(out, ind, res, fn, vals, callee)
    st[kind][callee] = st[kind].get(callee, 0) + 1
    st["replaced_callees"].add(callee)
    return True


def legalize(text: str) -> tuple[str, dict]:
    """The module with every x86_fp80 operation a call or integer code and the type i80, and
    what was done. Raises Refused for an x86_fp80 form it has no rule for, or one left after
    the rewrite."""
    st: dict = {"ops": {}, "fcmp": {}, "conversions": {}, "exact": {}, "transcendental": {},
                "inline": {}, "fenv": {}, "replaced_callees": set(), "declarations_dropped": []}
    if text.startswith(MARKER):
        st["replaced_callees"] = []
        st["already_legal"] = True
        return text, st
    lines = text.split("\n")
    internal = {m.group(2) for l in lines if (m := RE_DEFINE.match(l))
                and re.search(r"\b(?:internal|private)\b", m.group(1))}
    wr = Writer()
    out: list[str] = []
    for line in lines:
        if "x86_fp80" not in line and "@fe" not in line:
            out.append(line)
            continue
        if line.startswith("declare"):
            dm = RE_DECLARE.match(line)
            if dm and dm.group(1) in FENV:
                out.append(line.replace(f"@{dm.group(1)}(", f"@{FENV[dm.group(1)]}("))
                continue
            out.append(line)
            continue
        if line.startswith("define") or line.lstrip().startswith(";"):
            out.append(line)
            continue
        if m := RE_BIN.match(line):
            ind, res, op, a, b = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            if op == "frem":
                raise Refused(f"frem x86_fp80 (no x87 instruction lowers to it): {line.strip()}")
            wr.f80_call(out, ind, res, f"sa2_lf80_{op[1:]}", [a, b], op)
            st["ops"][op] = st["ops"].get(op, 0) + 1
            continue
        if m := RE_FCMP.match(line):
            ind, res, pred, a, b = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            if pred not in PREDS:
                raise Refused(f"fcmp predicate {pred}: {line.strip()}")
            c = wr.call(out, ind, "sa2_lf80_cmp",
                        [wr.wide(out, ind, a), wr.wide(out, ind, b), str(PREDS[pred])])
            out.append(f"{ind}{res} = icmp ne i32 {c}, 0{TAG} fcmp {pred}")
            st["fcmp"][pred] = st["fcmp"].get(pred, 0) + 1
            continue
        if m := RE_FPEXT.match(line):
            ind, res, ty, v = m.group(1), m.group(2), m.group(3), m.group(4)
            fn = "sa2_lf80_from_f32" if ty == "float" else "sa2_lf80_from_f64"
            w = wr.call(out, ind, fn, [v])
            wr.narrow(out, ind, res, w, f"fpext {ty}")
            st["conversions"][f"fpext.{ty}"] = st["conversions"].get(f"fpext.{ty}", 0) + 1
            continue
        if m := RE_FPTRUNC.match(line):
            ind, res, v, ty = m.group(1), m.group(2), m.group(3), m.group(4)
            fn = "sa2_lf80_to_f32" if ty == "float" else "sa2_lf80_to_f64"
            wr.call(out, ind, fn, [wr.wide(out, ind, v)], res, f"fptrunc {ty}")
            st["conversions"][f"fptrunc.{ty}"] = st["conversions"].get(f"fptrunc.{ty}", 0) + 1
            continue
        if m := RE_ITOF.match(line):
            ind, res, op, ity, v = (m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
            width = int(ity[1:])
            if width > 64 or (op == "uitofp" and width == 64):
                raise Refused(f"{op} {ity} to x86_fp80: {line.strip()}")
            arg = v
            if width < 64:
                b = wr.fresh()
                out.append(f"{ind}{b}.x = {'sext' if op == 'sitofp' else 'zext'} {ity} {v} to i64")
                arg = f"{b}.x"
            w = wr.call(out, ind, "sa2_lf80_from_i64", [arg])
            wr.narrow(out, ind, res, w, f"{op} {ity}")
            st["conversions"][f"{op}.{ity}"] = st["conversions"].get(f"{op}.{ity}", 0) + 1
            continue
        if m := RE_FTOI.match(line):
            ind, res, op, v, ity = (m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
            if op != "fptosi" or ity not in ("i16", "i32", "i64"):
                raise Refused(f"{op} x86_fp80 to {ity}: {line.strip()}")
            wr.call(out, ind, f"sa2_lf80_to_{ity}", [wr.wide(out, ind, v)], res, f"fptosi {ity}")
            st["conversions"][f"fptosi.{ity}"] = st["conversions"].get(f"fptosi.{ity}", 0) + 1
            continue
        if m := RE_FNEG.match(line):
            ind, res, v = m.group(1), m.group(2), m.group(3)
            x = wr.bits(out, ind, v)
            b = wr.fresh()
            out.append(f"{ind}{b}.n = xor i80 {x}, {SIGN}")
            out.append(f"{ind}{res} = bitcast i80 {b}.n to x86_fp80{TAG} fneg")
            st["inline"]["fneg"] = st["inline"].get("fneg", 0) + 1
            continue
        if m := RE_CALL.match(line):
            if rewrite_call(m, line, wr, out, st, internal):
                continue
        out.append(line)

    # Declarations: an x86_fp80 one nothing references any more goes (a callee rewritten here,
    # or one the PC24 pass already stopped calling, such as llvm.nearbyint.f80 after its FIST
    # rewrite); the shims used come in.
    body = "\n".join(out)
    final: list[str] = []
    for line in out:
        dm = RE_DECLARE.match(line)
        if dm and ("x86_fp80" in line or dm.group(1) in st["replaced_callees"]):
            if len(re.findall(rf"@{re.escape(dm.group(1))}(?![-\w.$])", body)) == 1:
                st["declarations_dropped"].append(dm.group(1))
                continue
        final.append(line)
    declared = {m.group(1) for l in final if (m := RE_DECLARE.match(l))}
    defined = {m.group(2) for l in final if (m := RE_DEFINE.match(l))}
    for fn in sorted(wr.used):
        if fn in declared or fn in defined:
            continue
        ret, params, attrs = DECLS[fn]
        final.append(f"declare {ret} @{fn}({params}) {attrs}")
    result = MARKER + "\n" + "\n".join(final)
    if not result.endswith("\n"):
        result += "\n"
    left = leftovers(result, internal)
    if left:
        raise Refused(f"{len(left)} line(s) the pass left on x86_fp80 or the host fenv: "
                      f"{left[0][:200]}")
    body = to_i80(result[len(MARKER) + 1:])
    if "x86_fp80" in body or RE_F80C.search(body):
        raise Refused("an x86_fp80 type or constant survived the i80 substitution")
    result = MARKER + "\n" + body
    st["replaced_callees"] = sorted(st["replaced_callees"])
    st["shims_used"] = sorted(wr.used)
    st["rewrites"] = (sum(st["ops"].values()) + sum(st["fcmp"].values())
                      + sum(st["conversions"].values()) + sum(st["exact"].values())
                      + sum(st["transcendental"].values()) + sum(st["inline"].values()))
    return result, st


RE_F80C = re.compile(r"\b0xK([0-9A-Fa-f]{20})\b")
RE_TYPEDEF = re.compile(r'^(%(?:"[^"]+"|[-A-Za-z$._][-A-Za-z$._0-9]*)) = type (.*)$')
# Every reference to a host fenv function: after the rename only the shims' names remain.
RE_FENV_REF = re.compile(r"@(?:" + "|".join(list(FENV) + list(FENV_REFUSED)) + r")(?![-\w.$])")
# A phi, select or call carrying fast-math flags: dropped where its value is x86_fp80.
RE_FMF_INSN = re.compile(
    rf"^(\s+{NAME} = (?:phi|select|(?:tail |musttail |notail )?call))"
    r"((?: (?:fast|nnan|ninf|nsz|arcp|contract|afn|reassoc))+)( .*)$", re.M)


def _drop_fmf(m: re.Match) -> str:
    head, rest = m.group(1), m.group(3)
    op = head.split()[-1]
    if op == "phi":
        f80 = rest.startswith(" x86_fp80 ")
    elif op == "select":
        f80 = re.match(r" (?:i1|<\d+ x i1>) [^,]+, x86_fp80 ", rest) is not None
    else:
        ret = rest.split("@", 1)[0].split()
        f80 = bool(ret) and ret[-1] == "x86_fp80"
    return head + rest if f80 else m.group(0)


def to_i80(text: str) -> str:
    """Every x86_fp80 a type i80 and every 0xK constant its 80-bit integer. What is left of the
    type by then is data movement, internal signatures, phis and no-op bitcasts, all of which
    mean the same on i80; `bitcast i80 %v to i80` is a valid no-op. Fast-math flags on a phi,
    select or call of x86_fp80 go first: an integer value cannot carry them."""
    if "x86_fp80" in text:
        text = RE_FMF_INSN.sub(_drop_fmf, text)
    text = RE_F80C.sub(lambda m: str(int(m.group(1), 16)), text)
    return re.sub(r"\bx86_fp80\b", "i80", text)


def f80_named_types(lines: list[str]) -> set[str]:
    """The named types whose body holds x86_fp80, directly or through another such type."""
    defs = {m.group(1): m.group(2) for l in lines if (m := RE_TYPEDEF.match(l))}
    found: set[str] = set()
    while True:
        ref = (re.compile("|".join(re.escape(t) for t in sorted(found)) + r"(?![-\w.$])")
               if found else None)
        new = {n for n, body in defs.items() if n not in found
               and (re.search(r"\bx86_fp80\b", body) or (ref and ref.search(body)))}
        if not new:
            return found
        found |= new


def leftovers(text: str, internal: set[str] | None = None) -> list[str]:
    """Every line that still operates on x86_fp80, or on a named type holding it, outside data
    movement and internal functions, and every reference to a host fenv function."""
    lines = text.split("\n")
    if internal is None:
        internal = {m.group(2) for l in lines if (m := RE_DEFINE.match(l))
                    and re.search(r"\b(?:internal|private)\b", m.group(1))}
    named = f80_named_types(lines)
    t_ref = (re.compile("|".join(re.escape(t) for t in sorted(named)) + r"(?![-\w.$])")
             if named else None)
    t_alt = "(?:" + "|".join(re.escape(t) for t in sorted(named)) + r")(?![-\w.$])"
    t_allowed = [re.compile(p) for p in (
        rf"^\s+{NAME} = alloca {t_alt}",
        rf"^\s+{NAME} = getelementptr (?:inbounds |nuw |nusw )*{t_alt}, ptr ",
        rf"^\s+{NAME} = load (?:volatile )?{t_alt}, ptr ",
        rf"^\s+store (?:volatile )?{t_alt} ",
        r"^%[-\w.$\"]+ = type ",
    )] if named else []
    bad = []
    for line in lines:
        if line.lstrip().startswith(";"):
            continue
        if "@fe" in line and RE_FENV_REF.search(line.split(";", 1)[0]):
            bad.append(line.strip())
            continue
        has_f80 = "x86_fp80" in line
        has_t = bool(t_ref and t_ref.search(line))
        if not (has_f80 or has_t):
            continue
        if line.startswith("define"):
            m = RE_DEFINE.match(line)
            if m and m.group(2) in internal:
                continue
            bad.append(line.strip())
            continue
        if line.startswith("declare"):
            dm = RE_DECLARE.match(line)
            if dm and dm.group(1) in internal:
                continue
            bad.append(line.strip())
            continue
        if ((not has_f80 or any(p.match(line) for p in ALLOWED))
                and (not has_t or any(p.match(line) for p in t_allowed))):
            continue
        m = RE_CALL.match(line)
        if m and m.group(5) in internal:
            continue
        bad.append(line.strip())
    return bad


# ------------------------------------------------------------------ census (nm)

# The census is an allowlist (see the module docstring): these classes of undefined symbol
# are the only ones a legalized object may need.
ALLOW_PATTERNS = (
    ("lifted", re.compile(r"^sub_[0-9a-f]+$")),
    ("remill", re.compile(r"^__remill_[A-Za-z0-9_]+$")),
    ("runtime", re.compile(r"^(?:__sa2_|sa2_guest_)[A-Za-z0-9_]+$")),
)
# Host libc functions lifted code may call, each with why it is the same on every host. The
# two rounding ones are what an x86-64 backend without SSE4.1 makes of llvm.nearbyint.f64 and
# llvm.trunc.f64 (remill's CVTSD2SI and CVTTSD2SI); AArch64 has instructions for them.
HOST_LIBC = {
    "sqrt": "IEEE double square root, correctly rounded on every host (SSE sqrtsd)",
    "nearbyint": "IEEE double rounded to an integer in the current rounding mode, exact on "
                 "every host (SSE cvtsd2si)",
    "trunc": "IEEE double rounded toward zero to an integer, exact on every host (SSE cvttsd2si)",
}
SHIM_PREFIXES = ("sa2_lf80_", "sa2_x87_")

# The known f80 hazards, named when one fails the census.
LIBM_LD = set("""acosl asinl atanl atan2l cosl sinl tanl coshl sinhl tanhl acoshl asinhl atanhl
expl exp2l expm1l logl log10l log1pl log2l logbl powl sqrtl cbrtl hypotl fmodl remainderl remquol
fabsl ceill floorl truncl roundl rintl nearbyintl lrintl llrintl lroundl llroundl frexpl ldexpl
scalbnl scalblnl modfl copysignl fmaxl fminl fdiml fmal nanl nextafterl nexttowardl erfl erfcl
lgammal tgammal ilogbl sincosl""".split())
HOST_FENV = set(FENV) | set(FENV_REFUSED)
RE_SOFT_FP = re.compile(
    r"^__(?:(?:add|sub|mul|div|neg|powi|eq|ne|lt|le|gt|ge|unord|cmp)(?:xf|tf)\d"
    r"|extend(?:hf|sf|df|xf)(?:xf|tf)2|trunc(?:xf|tf)(?:hf|sf|df|xf)2"
    r"|fix(?:uns)?(?:xf|tf)(?:si|di|ti)|float(?:un)?(?:si|di|ti)(?:xf|tf)"
    r"|(?:mul|div)(?:xc|tc)3)$")


def nm_undefined(obj: Path) -> list[str]:
    p = subprocess.run(["nm", "-u", str(obj)], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"nm {obj}: {p.stderr.strip()[:200]}")
    return sorted({l.split()[-1] for l in p.stdout.splitlines() if l.strip()})


def shim_names(obj: Path) -> frozenset[str]:
    """The shims a compiled f80_shims.o defines: its global text symbols with a shim prefix."""
    p = subprocess.run(["nm", "-g", "--defined-only", str(obj)], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"nm {obj}: {p.stderr.strip()[:200]}")
    names = frozenset(f[-1] for l in p.stdout.splitlines() if len(f := l.split()) >= 2
                      and f[-2] == "T" and f[-1].startswith(SHIM_PREFIXES))
    if not names:
        raise RuntimeError(f"{obj} defines no sa2_lf80_/sa2_x87_ function")
    return names


def classify_symbol(sym: str, shims: frozenset[str] | set[str] = frozenset()) -> str:
    """An allowed class ('lifted', 'remill', 'runtime', 'shim', 'host-libc') or
    'disallowed:<why>'. `shims` are the names the shims object defines."""
    for cls, pat in ALLOW_PATTERNS:
        if pat.match(sym):
            return cls
    if sym in shims:
        return "shim"
    if sym in HOST_LIBC:
        return "host-libc"
    if sym.startswith(SHIM_PREFIXES):
        return "disallowed:a shim name f80_shims.o does not define"
    if sym.startswith("__gcc_q"):
        return "disallowed:__gcc_q (PowerPC double-double compare)"
    if RE_SOFT_FP.match(sym):
        return "disallowed:soft-float libcall (x86_fp80 or binary128)"
    if sym in LIBM_LD:
        return "disallowed:libm long double"
    if sym in HOST_FENV:
        return "disallowed:host fenv with x86 constants"
    return "disallowed:not on the census allowlist"


def _census_one(obj: str) -> tuple[str, list[str]]:
    return obj, nm_undefined(Path(obj))


def census(objs: list[Path], shims: frozenset[str] | set[str], jobs: int = 8) -> dict:
    """nm -u over every object, each undefined symbol classed against the allowlist; `ok` when
    none is disallowed. Lifted symbols are counted, every other symbol is listed with how many
    objects need it."""
    per: dict[str, dict] = {}
    lifted: set[str] = set()
    lifted_objects = 0
    with cf.ProcessPoolExecutor(max(1, jobs)) as ex:
        for obj, syms in ex.map(_census_one, [str(o) for o in objs], chunksize=32):
            seen_lifted = False
            for s in syms:
                k = classify_symbol(s, shims)
                if k == "lifted":
                    lifted.add(s)
                    seen_lifted = True
                    continue
                e = per.setdefault(s, {"class": k, "objects": 0, "first": Path(obj).name})
                e["objects"] += 1
            lifted_objects += seen_lifted
    bad = {s: e for s, e in per.items() if e["class"].startswith("disallowed")}
    classes: dict[str, int] = {"lifted": len(lifted)}
    for e in per.values():
        k = e["class"].split(":")[0]
        classes[k] = classes.get(k, 0) + 1
    return {"schema": "sa2-f80-census/v2", "objects": len(objs),
            "allowlist": {"patterns": {c: p.pattern for c, p in ALLOW_PATTERNS},
                          "shims": sorted(shims), "host_libc": HOST_LIBC},
            "lifted": {"symbols": len(lifted), "objects": lifted_objects},
            "classes": dict(sorted(classes.items())),
            "symbols": dict(sorted(per.items())), "disallowed": dict(sorted(bad.items())),
            "ok": not bad and bool(objs)}


# ------------------------------------------------------------------ selftest

# Each entry: (name, oracle IR body, input kinds, output kind). The body computes %r from
# %x (and %y); kinds are f80, f32, f64, i16, i32, i64; output "cmp" is an i1.
def _ops() -> list[tuple[str, str, tuple[str, ...], str]]:
    ops = []
    for op in ("fadd", "fsub", "fmul", "fdiv"):
        ops.append((op, f"%r = {op} x86_fp80 %x, %y", ("f80", "f80"), "f80"))
    for p in PREDS:
        ops.append((f"fcmp_{p}", f"%r = fcmp {p} x86_fp80 %x, %y", ("f80", "f80"), "cmp"))
    ops += [("fpext_f32", "%r = fpext float %x to x86_fp80", ("f32",), "f80"),
            ("fpext_f64", "%r = fpext double %x to x86_fp80", ("f64",), "f80"),
            ("fptrunc_f32", "%r = fptrunc x86_fp80 %x to float", ("f80",), "f32"),
            ("fptrunc_f64", "%r = fptrunc x86_fp80 %x to double", ("f80",), "f64"),
            ("sitofp_i16", "%r = sitofp i16 %x to x86_fp80", ("i16",), "f80"),
            ("sitofp_i32", "%r = sitofp i32 %x to x86_fp80", ("i32",), "f80"),
            ("sitofp_i64", "%r = sitofp i64 %x to x86_fp80", ("i64",), "f80"),
            ("uitofp_i32", "%r = uitofp i32 %x to x86_fp80", ("i32",), "f80"),
            ("fptosi_i16", "%r = fptosi x86_fp80 %x to i16", ("f80",), "i16"),
            ("fptosi_i32", "%r = fptosi x86_fp80 %x to i32", ("f80",), "i32"),
            ("fptosi_i64", "%r = fptosi x86_fp80 %x to i64", ("f80",), "i64"),
            ("fneg", "%r = fneg x86_fp80 %x", ("f80",), "f80"),
            ("select", "%c = fcmp olt x86_fp80 %x, %y\n"
             "  %r = select i1 %c, x86_fp80 %x, x86_fp80 %y", ("f80", "f80"), "f80"),
            ("phi", "%c = fcmp ogt x86_fp80 %x, %y\n  br i1 %c, label %l1, label %l2\n"
             "l1:\n  br label %j\nl2:\n  br label %j\nj:\n"
             "  %r = phi x86_fp80 [ %x, %l1 ], [ %y, %l2 ]", ("f80", "f80"), "f80"),
            ("fabs", "%r = call x86_fp80 @llvm.fabs.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("copysign", "%r = call x86_fp80 @llvm.copysign.f80(x86_fp80 %x, x86_fp80 %y)",
             ("f80", "f80"), "f80"),
            ("sqrt", "%r = call x86_fp80 @llvm.sqrt.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("sqrtl", "%r = call x86_fp80 @sqrtl(x86_fp80 noundef %x)", ("f80",), "f80"),
            ("nearbyint", "%r = call x86_fp80 @llvm.nearbyint.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("rint", "%r = call x86_fp80 @llvm.rint.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("trunc", "%r = call x86_fp80 @llvm.trunc.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("floor", "%r = call x86_fp80 @llvm.floor.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("ceil", "%r = call x86_fp80 @llvm.ceil.f80(x86_fp80 %x)", ("f80",), "f80"),
            ("fmodl", "%r = call x86_fp80 @fmodl(x86_fp80 noundef %x, x86_fp80 noundef %y)",
             ("f80", "f80"), "f80"),
            ("remainderl", "%r = call x86_fp80 @remainderl(x86_fp80 noundef %x, "
             "x86_fp80 noundef %y)", ("f80", "f80"), "f80")]
    for name, ar in TRANSCENDENTAL.items():
        args = "x86_fp80 noundef %x" + (", x86_fp80 noundef %y" if ar == 2 else "")
        ops.append((f"T_{name}", f"%r = call x86_fp80 @{name}({args})",
                    ("f80",) * ar, "f80"))
    return ops


# compiler-rt candidates, each beside the oracle op whose semantics it would have to match.
CRT = [("crt_addxf3", "fadd", "__addxf3", ("f80", "f80")),
       ("crt_subxf3", "fsub", "__subxf3", ("f80", "f80")),
       ("crt_mulxf3", "fmul", "__mulxf3", ("f80", "f80")),
       ("crt_divxf3", "fdiv", "__divxf3", ("f80", "f80")),
       ("crt_extendsfxf2", "fpext_f32", "__extendsfxf2", ("f32",)),
       ("crt_extenddfxf2", "fpext_f64", "__extenddfxf2", ("f64",)),
       ("crt_truncxfsf2", "fptrunc_f32", "__truncxfsf2", ("f80",)),
       ("crt_truncxfdf2", "fptrunc_f64", "__truncxfdf2", ("f80",)),
       ("crt_floatsixf", "sitofp_i32", "__floatsixf", ("i32",)),
       ("crt_floatdixf", "sitofp_i64", "__floatdixf", ("i64",)),
       ("crt_fixxfdi", "fptosi_i64", "__fixxfdi", ("f80",))]
CRT_TYPES = {"__addxf3": ("x86_fp80", "x86_fp80, x86_fp80"),
             "__subxf3": ("x86_fp80", "x86_fp80, x86_fp80"),
             "__mulxf3": ("x86_fp80", "x86_fp80, x86_fp80"),
             "__divxf3": ("x86_fp80", "x86_fp80, x86_fp80"),
             "__extendsfxf2": ("x86_fp80", "float"), "__extenddfxf2": ("x86_fp80", "double"),
             "__truncxfsf2": ("float", "x86_fp80"), "__truncxfdf2": ("double", "x86_fp80"),
             "__floatsixf": ("x86_fp80", "i32"), "__floatdixf": ("x86_fp80", "i64"),
             "__fixxfdi": ("i64", "x86_fp80")}
IRTY = {"f80": "x86_fp80", "f32": "float", "f64": "double", "i16": "i16", "i32": "i32",
        "i64": "i64", "cmp": "i1", "cw": "i16"}
KIND = {"f80": 0, "f32": 1, "f64": 2, "i16": 3, "i32": 4, "i64": 5, "cmp": 6, "cw": 7}

# The helpers the PC24 pass splices into every module (internal, integer code on the bit
# pattern, x86_fp80 only in their signatures, phis and bitcasts): run on the target too, so
# the backend's handling of what the pass leaves of x86_fp80 is measured, not assumed.
HELPER_OPS = [
    ("pc_round", "%r = call x86_fp80 @__sa2_pc_round(x86_fp80 %x, i16 %y)", ("f80", "cw"), "f80"),
    ("round_int", "%r = call x86_fp80 @__sa2_x87_round_int(x86_fp80 %x, i16 %y)", ("f80", "cw"),
     "f80"),
    ("fistp_i16", "%r = call i16 @__sa2_fistp_i16(x86_fp80 %x, i16 %y)", ("f80", "cw"), "i16"),
    ("fistp_i32", "%r = call i32 @__sa2_fistp_i32(x86_fp80 %x, i16 %y)", ("f80", "cw"), "i32"),
    ("fistp_i64", "%r = call i64 @__sa2_fistp_i64(x86_fp80 %x, i16 %y)", ("f80", "cw"), "i64"),
]
HELPER_RETURNS = {"__sa2_pc_round": "x86_fp80", "__sa2_x87_round_int": "x86_fp80",
                  "__sa2_fistp_i16": "i16", "__sa2_fistp_i32": "i32", "__sa2_fistp_i64": "i64"}


def helper_lines() -> list[str]:
    """The PC24 pass's helpers as it splices them (tools/spike/pc24pass, read-only), or []
    when their IR is not on this machine."""
    gen = ROOT / "private" / "generated"
    pc, xi = gen / "spike" / "pc24pass" / "pc_round.ll", gen / "s2" / "corpus_run" / "helper" / "x87_int.ll"
    if not (pc.is_file() and xi.is_file()):
        return []
    sys.path.insert(0, str(ROOT / "tools" / "spike" / "pc24pass"))
    import pc24pass  # noqa: E402
    body, decl = pc24pass.helper_definition(pc.read_text(), ["__sa2_pc_round"], HELPER_RETURNS)
    b2, d2 = pc24pass.helper_definition(xi.read_text(), list(HELPER_RETURNS)[1:], HELPER_RETURNS)
    return body + b2 + decl + d2


def oracle_ir(prefix: str) -> str:
    """One function per operation, `void @<prefix>_<op>(ptr out, ptr a, ptr b)`, in the forms
    the lifted IR uses; plus `<prefix>_setrc(i32 x86 FE constant)` and `<prefix>_getrc()`
    through fesetround and fegetround, and `<prefix>_feclear/feraise/fetest(i32 x86 mask)`."""
    out = ["; f80_legalize selftest module"]
    decls = {"declare x86_fp80 @llvm.fabs.f80(x86_fp80)",
             "declare x86_fp80 @llvm.copysign.f80(x86_fp80, x86_fp80)",
             "declare x86_fp80 @llvm.sqrt.f80(x86_fp80)",
             "declare x86_fp80 @llvm.nearbyint.f80(x86_fp80)",
             "declare x86_fp80 @llvm.rint.f80(x86_fp80)",
             "declare x86_fp80 @llvm.trunc.f80(x86_fp80)",
             "declare x86_fp80 @llvm.floor.f80(x86_fp80)",
             "declare x86_fp80 @llvm.ceil.f80(x86_fp80)",
             "declare x86_fp80 @sqrtl(x86_fp80 noundef)",
             "declare x86_fp80 @fmodl(x86_fp80 noundef, x86_fp80 noundef)",
             "declare x86_fp80 @remainderl(x86_fp80 noundef, x86_fp80 noundef)",
             "declare i32 @fesetround(i32 noundef)", "declare i32 @fegetround()",
             "declare i32 @feclearexcept(i32 noundef)", "declare i32 @feraiseexcept(i32 noundef)",
             "declare i32 @fetestexcept(i32 noundef)"}
    for name, ar in TRANSCENDENTAL.items():
        decls.add(f"declare x86_fp80 @{name}(" + ", ".join(["x86_fp80 noundef"] * ar) + ")")
    entries = [(n, body, ins, outk) for n, body, ins, outk in _ops()]
    helpers = helper_lines()
    if helpers:
        entries += HELPER_OPS
    for n, body, ins, outk in entries:
        out.append(f"define void @{prefix}_{n}(ptr %o, ptr %a, ptr %b) {{")
        for k, v, p in zip(ins, "xy", ("%a", "%b")):
            out.append(f"  %{v} = load {IRTY[k]}, ptr {p}, align 16")
        out.append(f"  {body}")
        if outk == "cmp":
            out.append("  %z = zext i1 %r to i32")
            out.append("  store i32 %z, ptr %o, align 16")
        else:
            out.append(f"  store {IRTY[outk]} %r, ptr %o, align 16")
        out.append("  ret void")
        out.append("}")
    out.append(f"define i32 @{prefix}_setrc(i32 %m) {{")
    out.append("  %r = call i32 @fesetround(i32 noundef %m)")
    out.append("  ret i32 %r")
    out.append("}")
    out.append(f"define i32 @{prefix}_getrc() {{")
    out.append("  %r = call i32 @fegetround()")
    out.append("  ret i32 %r")
    out.append("}")
    for short, fn in (("feclear", "feclearexcept"), ("feraise", "feraiseexcept"),
                      ("fetest", "fetestexcept")):
        out.append(f"define i32 @{prefix}_{short}(i32 %m) {{")
        out.append(f"  %r = call i32 @{fn}(i32 noundef %m)")
        out.append("  ret i32 %r")
        out.append("}")
    out += helpers
    out += sorted(decls)
    return "\n".join(out) + "\n"


def crt_ir(prefix: str = "c") -> str:
    """The compiler-rt candidates, `void @<prefix>_<name>(ptr out, ptr a, ptr b)`, never
    legalized: each loads and stores its 80-bit values as i80 and bitcasts them at the call, so
    that what is measured on aarch64 is the routine and its calling convention, not the
    backend's x86_fp80 loads and stores (which -O2 gets wrong there, see the module
    docstring)."""
    out = ["; f80_legalize selftest: compiler-rt candidates"]
    decls = set()
    for n, _ref, fn, ins in CRT:
        ret, params = CRT_TYPES[fn]
        decls.add(f"declare {ret} @{fn}({params})")
        out.append(f"define void @{prefix}_{n}(ptr %o, ptr %a, ptr %b) {{")
        args = []
        for k, v, p in zip(ins, "xy", ("%a", "%b")):
            if k == "f80":
                out.append(f"  %{v}.i = load i80, ptr {p}, align 16")
                out.append(f"  %{v} = bitcast i80 %{v}.i to x86_fp80")
            else:
                out.append(f"  %{v} = load {IRTY[k]}, ptr {p}, align 16")
            args.append(f"{IRTY[k]} %{v}")
        out.append(f"  %r = call {ret} @{fn}({', '.join(args)})")
        if ret == "x86_fp80":
            out.append("  %r.i = bitcast x86_fp80 %r to i80")
            out.append("  store i80 %r.i, ptr %o, align 16")
        else:
            out.append(f"  store {ret} %r, ptr %o, align 16")
        out.append("  ret void")
        out.append("}")
    return "\n".join(out + sorted(decls)) + "\n"


# The driver's fenv checks, reported beside the ops: the rounding control's round trip as
# remill's FLDCW and FNSTCW use it, and the exception calls in the masks remill passes.
FENV_CHECKS = ("fenv_rc_roundtrip", "fenv_exceptions")


def selftest_entries(with_crt: bool) -> list[dict]:
    ents = []
    for n, _body, ins, outk in _ops() + (HELPER_OPS if helper_lines() else []):
        ents.append({"name": n, "sym": f"l_{n}", "ref": n, "ins": ins, "out": outk,
                     "residue": n.startswith("T_")})
    for n in FENV_CHECKS:
        ents.append({"name": n, "residue": False, "fenv": True})
    if with_crt:
        for n, ref, fn, ins in CRT:
            outk = {"x86_fp80": "f80", "float": "f32", "double": "f64",
                    "i64": "i64"}[CRT_TYPES[fn][0]]
            ents.append({"name": n, "sym": f"c_{n}", "ref": ref, "ins": ins, "out": outk,
                         "residue": False, "candidate": fn})
    return ents


DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef void (*opfn)(void *, const void *, const void *);
%DECLS%
struct ent { const char *name; opfn l; opfn o; int a, b, out; };
static const struct ent ENTS[] = {
%ENTS%
};
extern int l_setrc(int), l_getrc(void), l_feclear(int), l_feraise(int), l_fetest(int);
#ifdef WITH_ORACLE
extern int o_setrc(int), o_getrc(void), o_feclear(int), o_feraise(int), o_fetest(int);
#endif
#define FNV0 1469598103934665603ull
static uint64_t fnv(uint64_t h, const void *p, int n) {
    const uint8_t *b = p;
    for (int j = 0; j < n; j++) h = (h ^ b[j]) * 1099511628211ull;
    return h;
}
static uint64_t sm(uint64_t *s) {
    uint64_t z = (*s += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}
#define TOP 0x8000000000000000ull
#define QB 0x4000000000000000ull
#define B 0x3FFF
/* The fixed table, crossed for every operand of every op before the random vectors: each
 * entry once with each sign. Extended: zeros, the denormal and pseudo-denormal limits, the
 * normal limits, infinity, pseudo-infinity, QNaN and SNaN payload pairs (equal and unequal
 * significands), pseudo-NaNs and unnormals, the halves and the rounding ties of the float and
 * double formats, 2^15, 2^31, 2^63 and 2^64 with their neighbours, and the float and double
 * range limits. */
static const struct { unsigned ef; uint64_t m; } SP80[] = {
    {0, 0}, {0, 1}, {0, 0x7FFFFFFFFFFFFFFFull}, {0, TOP}, {0, ~0ull},
    {1, TOP}, {1, TOP | 1}, {1, 1}, {0x7FFE, ~0ull}, {0x7FFE, TOP},
    {0x7FFF, TOP}, {0x7FFF, 0}, {0x7FFF, TOP | QB}, {0x7FFF, TOP | QB | 1},
    {0x7FFF, TOP | QB | 0x2000000000000000ull}, {0x7FFF, TOP | 1}, {0x7FFF, TOP | 0x2000000000000000ull},
    {0x7FFF, ~0ull}, {0x7FFF, QB}, {0x7FFF, 1}, {B, QB}, {B + 70, 0x0000000100000000ull},
    {B, TOP}, {B, TOP | 1}, {B, TOP | QB}, {B - 1, TOP}, {B - 2, ~0ull}, {B - 1, TOP | 1},
    {B - 1, ~0ull}, {B + 1, TOP | 0x2000000000000000ull}, {B + 1, TOP | QB},
    {B, TOP | (1ull << 10)}, {B, TOP | (3ull << 10)}, {B, TOP | (1ull << 39)}, {B, TOP | (3ull << 39)},
    {B, TOP | (1ull << 10) | 1}, {B + 53, TOP | (1ull << 10)}, {B + 24, TOP | (1ull << 39)},
    {B + 15, TOP}, {B + 14, ~0ull}, {B + 14, 0xFFFE000000000000ull}, {B + 15, TOP | (1ull << 48)},
    {B + 15, TOP | 1}, {B + 31, TOP}, {B + 30, ~0ull}, {B + 30, 0xFFFFFFFE00000000ull},
    {B + 31, TOP | (1ull << 32)}, {B + 63, TOP}, {B + 62, ~0ull}, {B + 63, TOP | 1}, {B + 64, TOP},
    {B + 127, ~0ull}, {B + 127, 0xFFFFFF0000000000ull}, {B + 128, TOP}, {B - 126, TOP},
    {B - 127, ~0ull}, {B - 149, TOP}, {B - 150, TOP}, {B - 150, TOP | 1},
    {B + 1023, ~0ull}, {B + 1023, 0xFFFFFFFFFFFFF800ull}, {B + 1024, TOP}, {B - 1022, TOP},
    {B - 1023, ~0ull}, {B - 1074, TOP}, {B - 1075, TOP}, {B - 1075, TOP | 1},
};
static const uint32_t SP32[] = {0, 1, 0x7FFFFF, 0x800000, 0x7F7FFFFF, 0x7F800000, 0x7FC00000,
    0x7FC00001, 0x7F800001, 0x7FA00000, 0x7FFFFFFF, 0x3F800000, 0x3FC00000, 0x3F000000,
    0x4B800000, 0x4B7FFFFF, 0x46FFFE00, 0x47000000, 0x4EFFFFFF, 0x4F000000, 0x5EFFFFFF,
    0x5F000000, 2};
static const uint64_t SP64[] = {0, 1, 0x000FFFFFFFFFFFFFull, 0x0010000000000000ull,
    0x7FEFFFFFFFFFFFFFull, 0x7FF0000000000000ull, 0x7FF8000000000000ull, 0x7FF8000000000001ull,
    0x7FF0000000000001ull, 0x7FF4000000000000ull, 0x7FFFFFFFFFFFFFFFull, 0x3FF0000000000000ull,
    0x3FF8000000000000ull, 0x3FE0000000000000ull, 0x4340000000000000ull, 0x433FFFFFFFFFFFFFull,
    0x40DFFFC000000000ull, 0x40E0000000000000ull, 0x41DFFFFFFFC00000ull, 0x41E0000000000000ull,
    0x43DFFFFFFFFFFFFFull, 0x43E0000000000000ull, 0x36A0000000000000ull, 2};
static const int16_t SP16[] = {0, 1, -1, 2, 32767, -32768, -32767, 0x5555, -0x5556};
static const int32_t SPI32[] = {0, 1, -1, 2, 0x7FFFFFFF, (int32_t)0x80000000, (int32_t)0x80000001,
    0x01000001, -0x01000001, 32767, -32768, 65535, 0x7FFFFFC0};
static const int64_t SPI64[] = {0, 1, -1, 2, 0x7FFFFFFFFFFFFFFFll, (int64_t)0x8000000000000000ull,
    (int64_t)0x8000000000000001ull, 0x0020000000000001ll, -0x0020000000000001ll,
    0x7FFFFFFFFFFFFC00ll, 0x00000000FFFFFFFFll, 0x7FFFFFFF, -0x80000000ll, 0x0000000100000001ll};
#define N(a) ((int)(sizeof a / sizeof a[0]))
static int nspecial(int kind) {
    switch (kind) {
    case 0: return 2 * N(SP80);
    case 1: return 2 * N(SP32);
    case 2: return 2 * N(SP64);
    case 3: return N(SP16);
    case 4: return N(SPI32);
    case 5: return N(SPI64);
    case 7: return 16;
    default: return 1;
    }
}
static void special(int kind, int i, uint8_t *o) {
    memset(o, 0, 16);
    switch (kind) {
    case 0: {
        uint16_t se = (uint16_t)(((unsigned)(i & 1) << 15) | SP80[i >> 1].ef);
        memcpy(o, &SP80[i >> 1].m, 8); memcpy(o + 8, &se, 2); return; }
    case 1: { uint32_t u = SP32[i >> 1] | ((uint32_t)(i & 1) << 31); memcpy(o, &u, 4); return; }
    case 2: { uint64_t u = SP64[i >> 1] | ((uint64_t)(i & 1) << 63); memcpy(o, &u, 8); return; }
    case 3: memcpy(o, &SP16[i], 2); return;
    case 4: memcpy(o, &SPI32[i], 4); return;
    case 5: memcpy(o, &SPI64[i], 8); return;
    case 7: { uint16_t cw = (uint16_t)(0x007F | ((i & 3) << 8) | ((i >> 2) << 10)); memcpy(o, &cw, 2); return; }
    }
}
static const uint64_t SPM[] = {0, 0x8000000000000000ull, 0xC000000000000000ull,
    0xFFFFFFFFFFFFFFFFull, 0x8000000000000001ull, 0x4000000000000000ull, 0x0000000000000001ull,
    0x7FFFFFFFFFFFFFFFull, 0xA000000000000000ull, 0xE000000000000000ull, 0xC000000000000001ull,
    0x8000000000000000ull, 0xFFFFFFFF00000000ull, 0x8000010000000000ull};
static void gen_f80(uint64_t *s, uint8_t *o, const uint8_t *rel) {
    uint64_t r = sm(s), m = sm(s);
    unsigned k = (unsigned)(r % 100), sign = (unsigned)(r >> 8) & 1u, ef;
    if (k < 10) {
        static const unsigned EFS[] = {0, 0x7FFF, 1, 0x7FFE, 16383, 16382, 16384, 0x3FFF + 62,
                                       0x3FFF + 63, 0x3FFF + 31, 0x3FFF + 15};
        ef = EFS[(r >> 16) % 11];
        m = SPM[(r >> 24) % 14];
        if ((r >> 40) & 1) m ^= (sm(s) & 0xFFull);
    } else if (k < 16) {
        ef = 0;
        m = m >> ((r >> 16) % 64);
        if (((r >> 24) & 7) == 0) m |= 0x8000000000000000ull;    /* pseudo-denormal */
    } else if (k < 20) {
        ef = ((r >> 16) & 1) ? 0x7FFE - (unsigned)((r >> 20) % 70) : 1 + (unsigned)((r >> 20) % 70);
        m |= 0x8000000000000000ull;
    } else if (k < 50 && rel) {
        unsigned rf = (unsigned)(rel[8] | (rel[9] << 8)) & 0x7FFFu;
        int d = (int)((r >> 16) % 141) - 70;
        int e = (int)rf + d;
        if (e < 1) e = 1;
        if (e > 0x7FFE) e = 0x7FFE;
        ef = (unsigned)e;
        uint64_t rm; memcpy(&rm, rel, 8);
        if (((r >> 28) & 3) == 0) { m = rm ^ (1ull << ((r >> 32) % 64)); ef = rf ? rf : 1; }
        if (((r >> 28) & 7) == 1 || ((r >> 28) & 7) == 2) {
            /* the same magnitude, either sign: exact cancellation, NaN ties, equal compares */
            memcpy(o, rel, 16);
            if (((r >> 28) & 7) == 1) o[9] ^= 0x80;
            return;
        }
        m |= 0x8000000000000000ull;
    } else if (k < 62) {
        ef = 16383 - 64 + (unsigned)((r >> 16) % 128);
        unsigned keep = ((r >> 24) & 1) ? 24 : 53;
        m = (m | 0x8000000000000000ull) & ~((1ull << (64 - keep)) - 1);
    } else if (k < 72) {
        ef = 16383 + (unsigned)((r >> 16) % 66);
        int e = (int)ef - 16383;
        if (e < 63) {
            uint64_t frac = 1ull << (62 - e < 0 ? 0 : 62 - e);
            m = ((m | 0x8000000000000000ull) & ~((frac << 1) - 1)) | (((r >> 24) & 1) ? frac : 0);
        }
        m |= 0x8000000000000000ull;
    } else if (k < 75) {
        ef = (unsigned)((r >> 16) % 0x8000);          /* any encoding, unsupported included */
    } else {
        ef = 16383 - 70 + (unsigned)((r >> 16) % 140);
        m |= 0x8000000000000000ull;
    }
    memset(o, 0, 16);
    memcpy(o, &m, 8);
    uint16_t se = (uint16_t)((sign << 15) | (ef & 0x7FFFu));
    memcpy(o + 8, &se, 2);
}
static void gen(uint64_t *s, int kind, uint8_t *o, const uint8_t *rel) {
    uint64_t r = sm(s), v = sm(s);
    memset(o, 0, 16);
    switch (kind) {
    case 0: gen_f80(s, o, rel); return;
    case 1: {
        uint32_t u = (uint32_t)v;
        unsigned k = (unsigned)(r % 10);
        if (k == 0) u = (u & 0x807FFFFFu) | 0x7F800000u;       /* inf / NaN */
        else if (k == 1) u &= 0x807FFFFFu;                     /* denormal / zero */
        else if (k < 5) u = (u & 0x807FFFFFu) | ((uint32_t)(100 + (r >> 8) % 56) << 23);
        memcpy(o, &u, 4); return; }
    case 2: {
        unsigned k = (unsigned)(r % 10);
        if (k == 0) v = (v & 0x800FFFFFFFFFFFFFull) | 0x7FF0000000000000ull;
        else if (k == 1) v &= 0x800FFFFFFFFFFFFFull;
        else if (k < 5) v = (v & 0x800FFFFFFFFFFFFFull) | ((uint64_t)(1000 + (r >> 8) % 48) << 52);
        memcpy(o, &v, 8); return; }
    case 3: { uint16_t u = (uint16_t)(v >> ((r % 3) * 5)); memcpy(o, &u, 2); return; }
    case 7: {
        static const uint16_t CW[] = {0x007F, 0x027F, 0x037F, 0x047F, 0x067F, 0x087F, 0x0A7F,
                                      0x0C7F, 0x0E7F, 0x0F7F, 0x017F, 0x0B7F};
        uint16_t u = (r % 8) ? CW[(r >> 8) % 12] : (uint16_t)v;
        memcpy(o, &u, 2); return; }
    case 4: { uint32_t u = (uint32_t)(v >> ((r % 4) * 8)); if (r % 7 == 0) u = 0x80000000u >> (r % 3); memcpy(o, &u, 4); return; }
    default: { uint64_t u = v >> ((r % 8) * 8); if (r % 7 == 0) u = 0x8000000000000000ull >> (r % 3); memcpy(o, &u, 8); return; }
    }
}
static const int OUTB[] = {10, 4, 8, 2, 4, 8, 4, 2};
static void hx(const uint8_t *p, int n, char *o) {
    for (int i = n - 1; i >= 0; i--) o += sprintf(o, "%02x", p[i]);
}
struct acc { uint64_t hl, ho; long calls, mism, scalls, smism; char ex[4][200]; int nex; };
/* One vector through the shim and, with the oracle, through the x87: 1 when they differ. */
static int run1(const struct ent *t, int rc, const uint8_t *a, const uint8_t *b, struct acc *c) {
    uint8_t rl[16], ro[16];
    int ob = OUTB[t->out], bad = 0;
    memset(rl, 0, 16); memset(ro, 0, 16);
    l_setrc(rc << 10);
    t->l(rl, a, b);
    c->hl = fnv(c->hl, rl, ob);
#ifdef WITH_ORACLE
    o_setrc(rc << 10);
    t->o(ro, a, b);
    c->ho = fnv(c->ho, ro, ob);
    if (memcmp(rl, ro, ob)) {
        if (c->nex < 4) {
            char ha[48] = "", hb[48] = "", hl2[48] = "", ho2[48] = "";
            hx(a, t->a == 0 ? 10 : OUTB[t->a], ha);
            hx(b, t->b == 0 ? 10 : (t->b > 0 ? OUTB[t->b] : 0), hb);
            hx(rl, ob, hl2); hx(ro, ob, ho2);
            snprintf(c->ex[c->nex++], 200, "rc%d a=%s b=%s shim=%s x86=%s", rc, ha, hb, hl2, ho2);
        }
        bad = 1;
    }
#endif
    c->calls++;
    c->mism += bad;
    return bad;
}
static void report(const char *name, const struct acc *c, int *first) {
    printf("%s {\"name\": \"%s\", \"calls\": %ld, \"mismatch\": %ld, \"special_calls\": %ld, "
           "\"special_mismatch\": %ld, \"hash\": \"%016llx\"", *first ? "" : ",", name, c->calls,
           c->mism, c->scalls, c->smism, (unsigned long long)c->hl);
#ifdef WITH_ORACLE
    printf(", \"oracle_hash\": \"%016llx\"", (unsigned long long)c->ho);
#endif
    printf(", \"examples\": [");
    for (int j = 0; j < c->nex; j++) printf("%s\"%s\"", j ? ", " : "", c->ex[j]);
    printf("]}\n");
    *first = 0;
}
static void note(struct acc *c, int bad, const char *what) {
    c->calls++; c->scalls++;
    if (bad) {
        c->mism++; c->smism++;
        if (c->nex < 4) snprintf(c->ex[c->nex++], 200, "%s", what);
    }
}
/* remill's FLDCW calls fesetround with the RC field in place and its FNSTCW reads fegetround
 * back: each RC must round-trip, and a value that is no RC must be refused and change
 * nothing. */
static void fenv_rc(int *first) {
    static const int M[] = {0x000, 0x400, 0x800, 0xC00, 0x400, 0x000, 0xC00, 0x800, 0x200, 0xC01};
    struct acc c = {FNV0, FNV0};
    int prev = l_setrc(0) == 0 ? 0 : -1;
    for (int k = 0; k < N(M); k++) {
        int valid = (M[k] & ~0xC00) == 0;
        int r = l_setrc(M[k]), g = l_getrc();
        char what[200];
        c.hl = fnv(fnv(c.hl, &r, 4), &g, 4);
        snprintf(what, 200, "shim setrc(0x%x)=%d getrc=0x%x", M[k], r, g);
        note(&c, valid ? (r != 0 || g != M[k]) : (r == 0 || g != prev), what);
#ifdef WITH_ORACLE
        o_setrc(prev);
        int ro = o_setrc(M[k]), go = o_getrc();
        c.ho = fnv(fnv(c.ho, &ro, 4), &go, 4);
        snprintf(what, 200, "setrc(0x%x): shim %d/0x%x, x86 %d/0x%x", M[k], r, g, ro, go);
        note(&c, ro != r || go != g, what);
#endif
        if (valid) prev = M[k];
    }
    l_setrc(0);
#ifdef WITH_ORACLE
    o_setrc(0);
#endif
    report("fenv_rc_roundtrip", &c, first);
}
/* The exception calls in the forms remill emits (FNCLEX: feclearexcept(61); the fwait
 * barrier: fetestexcept(61) then feraiseexcept of what it read): 61 is every x86 flag but the
 * denormal one, which no host has. Each subset raised must read back exactly, alone. */
static void fenv_exc(int *first) {
    static const int M[] = {1, 4, 8, 16, 32, 1 | 32, 8 | 32, 16 | 32, 4 | 8, 1 | 4 | 16, 61};
    struct acc c = {FNV0, FNV0};
    for (int k = 0; k < N(M); k++) {
        int v[6];
        l_feclear(61);
        v[0] = l_fetest(61);
        v[1] = l_feraise(M[k]);
        v[2] = l_fetest(61);
        v[3] = l_fetest(M[k]);
        v[4] = l_fetest(61 & ~M[k]);
        l_feclear(M[k]);
        v[5] = l_fetest(61);
        c.hl = fnv(c.hl, v, sizeof v);
        char what[200];
        snprintf(what, 200, "shim mask %d: %d %d %d %d %d %d", M[k], v[0], v[1], v[2], v[3], v[4], v[5]);
        note(&c, v[0] != 0 || v[1] != 0 || v[2] != M[k] || v[3] != M[k] || v[4] != 0 || v[5] != 0, what);
#ifdef WITH_ORACLE
        int w[6];
        o_feclear(61);
        w[0] = o_fetest(61);
        w[1] = o_feraise(M[k]);
        w[2] = o_fetest(61);
        w[3] = o_fetest(M[k]);
        w[4] = o_fetest(61 & ~M[k]);
        o_feclear(M[k]);
        w[5] = o_fetest(61);
        c.ho = fnv(c.ho, w, sizeof w);
        snprintf(what, 200, "mask %d: x86 %d %d %d %d %d %d", M[k], w[0], w[1], w[2], w[3], w[4], w[5]);
        note(&c, memcmp(v, w, sizeof v) != 0, what);
#endif
    }
    l_feclear(61);
    report("fenv_exceptions", &c, first);
}
int main(int argc, char **argv) {
    long n = argc > 1 ? atol(argv[1]) : 20000;
    const char *only = argc > 2 ? argv[2] : 0;
    printf("{\"vectors\": %ld, \"ops\": [\n", n);
    int first = 1;
    for (unsigned e = 0; e < sizeof ENTS / sizeof ENTS[0]; e++) {
        const struct ent *t = &ENTS[e];
        if (only && strcmp(only, t->name)) continue;
        struct acc c = {FNV0, FNV0};
        int na = nspecial(t->a), nb = t->b >= 0 ? nspecial(t->b) : 1;
        for (int rc = 0; rc < 4; rc++) {
            for (int i = 0; i < na; i++)
                for (int j = 0; j < nb; j++) {
                    uint8_t a[16], b[16];
                    special(t->a, i, a);
                    if (t->b >= 0) special(t->b, j, b); else memset(b, 0, 16);
                    c.smism += run1(t, rc, a, b, &c);
                    c.scalls++;
                }
            uint64_t seed = 0x5A2F80ull * (e + 1) + 7;   /* the vectors do not depend on rc */
            for (long i = 0; i < n; i++) {
                uint8_t a[16], b[16];
                gen(&seed, t->a, a, 0);
                gen(&seed, t->b >= 0 ? t->b : 0, b, t->a == 0 ? a : 0);
                run1(t, rc, a, b, &c);
            }
        }
        l_setrc(0);
        report(t->name, &c, &first);
    }
    if (!only || !strcmp(only, "fenv_rc_roundtrip")) fenv_rc(&first);
    if (!only || !strcmp(only, "fenv_exceptions")) fenv_exc(&first);
    printf("]}\n");
    return 0;
}
"""


def driver_c(entries: list[dict]) -> str:
    decls, rows = [], []
    for e in entries:
        if e.get("fenv"):
            continue
        decls.append(f"extern void {e['sym']}(void *, const void *, const void *);")
        decls.append("#ifdef WITH_ORACLE")
        decls.append(f"extern void o_{e['ref']}(void *, const void *, const void *);")
        decls.append("#endif")
        a = KIND[e["ins"][0]]
        b = KIND[e["ins"][1]] if len(e["ins"]) > 1 else -1
        rows.append("#ifdef WITH_ORACLE")
        rows.append(f'  {{"{e["name"]}", {e["sym"]}, o_{e["ref"]}, {a}, {b}, {KIND[e["out"]]}}},')
        rows.append("#else")
        rows.append(f'  {{"{e["name"]}", {e["sym"]}, 0, {a}, {b}, {KIND[e["out"]]}}},')
        rows.append("#endif")
    return DRIVER.replace("%DECLS%", "\n".join(dict.fromkeys(decls))).replace(
        "%ENTS%", "\n".join(rows))


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True,
                       timeout=timeout)
    if p.returncode:
        raise SystemExit(f"failed ({p.returncode}): {' '.join(str(c) for c in cmd)}\n"
                         f"{p.stderr[-3000:]}")
    return p


def build_selftest(out: Path, target: str, oracle: bool, shims_defs: list[str] | None = None,
                   with_crt: bool = True) -> Path:
    """Compile the legalized selftest module (and, on x86, the oracle) with the shims and the
    driver into one static binary for `target`."""
    out.mkdir(parents=True, exist_ok=True)
    legal_ir, _st = legalize(oracle_ir("l"))
    (out / "legal.ll").write_text(legal_ir)
    (out / "oracle.ll").write_text(oracle_ir("o"))
    (out / "crt.ll").write_text(crt_ir("c"))
    (out / "driver.c").write_text(driver_c(selftest_entries(with_crt)))
    zig = [str(ZIG), "cc", "-target", target]
    objs = []
    srcs = [("legal.ll", []), ("driver.c", ["-DWITH_ORACLE"] if oracle else []),
            (str(SHIMS_C), list(shims_defs or []))] + ([("crt.ll", [])] if with_crt else [])
    for src, extra in srcs:
        o = out / (Path(src).stem + ".o")
        _run(zig + ["-O2", "-w", "-c", *extra, src, "-o", str(o)], out)
        objs.append(o)
    if oracle:
        o = out / "oracle.o"
        _run(zig + ["-O2", "-w", "-c", "oracle.ll", "-o", str(o)], out)
        objs.append(o)
    exe = out / "selftest"
    _run(zig + ["-static", "-O2", *[str(o) for o in objs], "-lm", "-o", str(exe)], out)
    return exe


def run_selftest(exe: Path, target: str, vectors: int) -> dict:
    cmd = [str(exe), str(vectors)]
    if target.startswith("aarch64"):
        cmd = [str(QEMU_AARCH64)] + cmd
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if p.returncode:
        raise SystemExit(f"selftest failed ({p.returncode}): {p.stderr[-2000:]}")
    return json.loads(p.stdout)


def host_cpu() -> dict:
    """The machine whose x87 is the selftest's oracle (the NaN tie rule was measured on it)."""
    import platform
    model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"machine": platform.machine(), "model": model}


def summarize(native: dict, cross: dict | None, entries: list[dict]) -> dict:
    """Each op: exact against the x87 (mismatch 0) or not, how much of that came from the fixed
    table of special encodings, and the cross-target hash check."""
    meta = {e["name"]: e for e in entries}
    rows = {}
    for r in native["ops"]:
        e = meta[r["name"]]
        row = {"calls": r["calls"], "mismatch_vs_x86": r["mismatch"],
               "special_calls": r.get("special_calls", 0),
               "special_mismatch": r.get("special_mismatch", 0),
               "examples": r.get("examples", [])}
        if e.get("candidate"):
            row["candidate"] = e["candidate"]
        if e["residue"]:
            row["residue"] = "design 3.4 transcendental (host libm in double)"
        rows[r["name"]] = row
    if cross:
        for r in cross["ops"]:
            nat = next(x for x in native["ops"] if x["name"] == r["name"])
            rows[r["name"]]["cross_target_hash_equal"] = r["hash"] == nat["hash"]
    exact_ops = [n for n, e in meta.items() if not e["residue"] and not e.get("candidate")]
    return {
        "vectors_per_rc": native["vectors"],
        "oracle_host": host_cpu(),
        "ops": rows,
        "exact_ops": len(exact_ops),
        "exact_ops_mismatching": sorted(n for n in exact_ops if rows[n]["mismatch_vs_x86"]),
        "cross_target_differs": sorted(n for n in rows if cross and n in exact_ops
                                       and not rows[n].get("cross_target_hash_equal")),
        # a compiler-rt candidate is usable only if it is exact here and, built for the other
        # target, gives the same bytes there
        "candidates_exact": sorted(n for n, e in meta.items() if e.get("candidate")
                                   and rows[n]["mismatch_vs_x86"] == 0
                                   and rows[n].get("cross_target_hash_equal", True)),
        "candidates_exact_on_x86_only": sorted(
            n for n, e in meta.items() if e.get("candidate") and rows[n]["mismatch_vs_x86"] == 0
            and rows[n].get("cross_target_hash_equal") is False),
    }


def cmd_selftest(args) -> None:
    out = Path(args.out).resolve()
    entries = selftest_entries(True)
    nat_exe = build_selftest(out / "x86_64-linux-musl", "x86_64-linux-musl", oracle=True)
    native = run_selftest(nat_exe, "x86_64-linux-musl", args.vectors)
    cross = None
    if args.target and args.target != "x86_64-linux-musl":
        x_exe = build_selftest(out / args.target, args.target, oracle=False)
        cross = run_selftest(x_exe, args.target, args.vectors)
    summary = summarize(native, cross, entries)
    summary.update(target=args.target, shims_sha256=hashlib.sha256(SHIMS_C.read_bytes()).hexdigest(),
                   pass_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out / "selftest.summary.txt").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "ops"}, indent=1))
    for n, r in summary["ops"].items():
        flag = "" if not r["mismatch_vs_x86"] else f"  mismatch {r['mismatch_vs_x86']}"
        x = r.get("cross_target_hash_equal")
        print(f"  {n:22s} calls={r['calls']}{flag}"
              + ("" if x is None else f"  cross={'=' if x else 'DIFF'}")
              + (f"  e.g. {r['examples'][0]}" if r["examples"] else ""))
    if summary["exact_ops_mismatching"] or summary["cross_target_differs"]:
        raise SystemExit(2)


# ------------------------------------------------------------------ CLI

def stats_json(st: dict) -> dict:
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in st.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rewrite")
    r.add_argument("src")
    r.add_argument("dst")
    r.add_argument("--stats")
    c = sub.add_parser("census")
    c.add_argument("objs", nargs="+", help="objects, or directories of *.o")
    c.add_argument("--shims", required=True, help="the f80_shims.o built for the same target")
    c.add_argument("--json")
    c.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    s = sub.add_parser("selftest")
    s.add_argument("--out", required=True)
    s.add_argument("--target", default="aarch64-linux-musl")
    s.add_argument("--vectors", type=int, default=20000)
    args = ap.parse_args()
    if args.cmd == "rewrite":
        text = Path(args.src).read_text(errors="replace")
        try:
            out, st = legalize(text)
        except Refused as e:
            raise SystemExit(f"refused: {e}")
        Path(args.dst).write_text(out)
        if args.stats:
            Path(args.stats).write_text(json.dumps(stats_json(st), indent=1) + "\n")
        print(json.dumps({"rewrites": st.get("rewrites", 0),
                          "shims_used": st.get("shims_used", [])}))
    elif args.cmd == "census":
        objs = sorted(q for d in map(Path, args.objs)
                      for q in (d.glob("*.o") if d.is_dir() else [d]))
        res = census(objs, shim_names(Path(args.shims)), args.jobs)
        if args.json:
            Path(args.json).write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps({k: res[k] for k in ("objects", "ok", "disallowed")}, indent=1))
        if not res["ok"]:
            raise SystemExit(1)
    elif args.cmd == "selftest":
        cmd_selftest(args)


if __name__ == "__main__":
    main()
